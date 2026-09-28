"""Public-client OAuth PKCE with explicit issuer/resource binding.

An embedding host may supply protect/unprotect hooks; standalone llms-py
stores the JSON payload directly in its owner-scoped SQLite database.
"""

import base64
import hashlib
import re
import secrets
import time
from urllib.parse import urlencode, urlsplit, urlunsplit

from .common import McpError, config_hash, digest, dumps
from .transport import parse_json, read_bounded


class OAuth:
    def __init__(self, client):
        self.client = client

    def seal(self, data, owner, server):
        payload = dumps(data)
        protect = self.client.hooks.get("protect")
        return protect(payload, f"mcp_client:{owner}:{server}") if protect else payload

    def open(self, data, owner, server):
        unprotect = self.client.hooks.get("unprotect")
        payload = unprotect(data, f"mcp_client:{owner}:{server}") if unprotect else data
        return parse_json(payload)

    async def request(self, url, method="GET", data=None, timeout=None, token_exchange=False):
        c = self.client
        c.policy.uri(url)
        async with (
            c.policy.client(timeout or c.limits["discoveryTimeout"]) as http,
            http.request(method, url, data=data, headers={"Accept": "application/json"}, allow_redirects=False) as response,
        ):
            if response.status != 200:
                if token_exchange:
                    raise McpError("oauth_token_rejected", f"OAuth provider rejected the token request (HTTP {response.status})")
                raise McpError(
                    "oauth_not_found" if response.status == 404 else "auth_required",
                    "OAuth endpoint rejected the request",
                )
            result = parse_json(await read_bounded(response, c.limits["maxResponseBytes"]))
            if not isinstance(result, dict):
                raise McpError("invalid_oauth_metadata")
            return result

    @staticmethod
    def resource(server):
        p = urlsplit(server["endpoint"])
        return urlunsplit((p.scheme, p.netloc, p.path or "/", "", ""))

    async def challenge_metadata(self, server):
        """Read advertised metadata without forwarding host cookies or any token."""
        c = self.client
        c.policy.uri(server["endpoint"])
        body = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "server/discover",
            "params": {
                "_meta": {
                    "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                    "io.modelcontextprotocol/clientInfo": {"name": "llms-py", "version": "1.0"},
                    "io.modelcontextprotocol/clientCapabilities": {},
                }
            },
        }
        async with (
            c.policy.client(c.limits["discoveryTimeout"]) as http,
            http.post(
                server["endpoint"],
                json=body,
                headers={
                    "Accept": "application/json, text/event-stream",
                    "MCP-Protocol-Version": "2026-07-28",
                    "Mcp-Method": "server/discover",
                },
                allow_redirects=False,
            ) as response,
        ):
            await read_bounded(response, c.limits["maxResponseBytes"])
            if response.status == 401:
                header = response.headers.get("WWW-Authenticate", "")
                advertised = re.search(r'(?:^|[,\s])resource_metadata="([^"\r\n]+)"', header, flags=re.IGNORECASE)
                challenge_scopes = re.search(r'(?:^|[,\s])scope="([^"\r\n]+)"', header, flags=re.IGNORECASE)
                if advertised or challenge_scopes:
                    return (advertised.group(1) if advertised else None,
                            challenge_scopes.group(1).split() if challenge_scopes else [])
        return None, []

    async def protected_resource(self, endpoint):
        """Read protected-resource metadata without forwarding credentials."""
        server = {"endpoint": endpoint}
        resource = self.resource(server)
        p = urlsplit(resource)
        advertised, challenge_scopes = await self.challenge_metadata(server)
        location = advertised or urlunsplit(
            (p.scheme, p.netloc, "/.well-known/oauth-protected-resource" + p.path.rstrip("/"), "", "")
        )
        try:
            protected = await self.request(location)
        except McpError as exc:
            if advertised or exc.code != "oauth_not_found":
                raise
            protected = await self.request(
                urlunsplit((p.scheme, p.netloc, "/.well-known/oauth-protected-resource", "", ""))
            )
        if protected.get("resource") != resource:
            raise McpError("invalid_oauth_metadata", "Unexpected protected resource")
        return protected, challenge_scopes

    async def discover_issuers(self, endpoint):
        """Resolve sign-in providers for an unsaved MCP server."""
        protected, _ = await self.protected_resource(endpoint)
        issuers = protected.get("authorization_servers")
        if not isinstance(issuers, list) or not 1 <= len(issuers) <= 16:
            raise McpError("invalid_oauth_metadata", "This MCP server did not advertise a usable sign-in provider")
        for issuer in issuers:
            if not isinstance(issuer, str):
                raise McpError("invalid_oauth_metadata", "The MCP server advertised an invalid sign-in provider")
            self.client.policy.uri(issuer)
        return list(dict.fromkeys(issuers))

    @staticmethod
    def valid_scopes(values):
        if not isinstance(values, list) or len(values) > 128 or any(
            not isinstance(value, str) or not 0 < len(value) <= 256
            or any(ord(char) < 33 or ord(char) > 126 or char in '\\"' for char in value)
            for value in values
        ):
            raise McpError("invalid_oauth_metadata", "This MCP server advertised invalid OAuth scopes")
        return list(dict.fromkeys(values))

    async def metadata(self, server):
        protected, challenge_scopes = await self.protected_resource(server["endpoint"])
        issuers = protected.get("authorization_servers")
        if not isinstance(issuers, list) or not 1 <= len(issuers) <= 16:
            raise McpError("invalid_oauth_metadata", "This MCP server did not advertise a usable sign-in provider")
        for advertised_issuer in issuers:
            if not isinstance(advertised_issuer, str):
                raise McpError("invalid_oauth_metadata", "The MCP server advertised an invalid sign-in provider")
            self.client.policy.uri(advertised_issuer)
        if server["oauthIssuer"] not in issuers:
            raise McpError("invalid_oauth_metadata", "Unexpected authorization server")
        issuer = urlsplit(server["oauthIssuer"])
        location = urlunsplit(
            (issuer.scheme, issuer.netloc, "/.well-known/oauth-authorization-server" + issuer.path.rstrip("/"), "", "")
        )
        try:
            metadata = await self.request(location)
        except McpError as exc:
            if exc.code != "oauth_not_found":
                raise
            metadata = await self.request(server["oauthIssuer"].rstrip("/") + "/.well-known/openid-configuration")
        if metadata.get("issuer") != server["oauthIssuer"] or "S256" not in metadata.get(
            "code_challenge_methods_supported", []
        ):
            raise McpError("invalid_oauth_metadata", "Issuer or PKCE support does not match")
        for key in ("authorization_endpoint", "token_endpoint"):
            self.client.policy.uri(metadata.get(key, ""))
        # Match the C# MCP SDK: challenge scopes, then protected-resource scopes,
        # then configured scopes as a fallback when the server advertises none.
        scopes = self.valid_scopes(challenge_scopes) or self.valid_scopes(
            protected.get("scopes_supported", [])
        ) or server.get("oauthScopes", [])
        # The C# MCP SDK also asks for a refresh token when the authorization
        # server advertises offline_access (SEP-2207).
        authorization_scopes = metadata.get("scopes_supported")
        if isinstance(authorization_scopes, list) and "offline_access" in authorization_scopes:
            scopes = list(dict.fromkeys([*scopes, "offline_access"]))
        return metadata, scopes

    async def start(self, server, context):
        c, owner = self.client, context["owner"]
        if urlsplit(server["endpoint"]).hostname == "api.githubcopilot.com" and c.oauth_client_secret(server, owner) is None:
            raise McpError("oauth_client_secret_required", "Add the GitHub OAuth App client secret in connection settings before signing in")
        redirect_uri = c.oauth_redirect_uri(context.get("request"))
        if not redirect_uri:
            raise McpError("invalid_configuration", "Configure an HTTPS MCP OAuth callback URL for this host")
        meta, scopes = await self.metadata(server)
        state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        binding = c.store.binding(owner, server["id"])
        c.store.oauth_start(
            digest(state),
            owner,
            server["id"],
            binding["revision"],
            self.seal(
                {"verifier": verifier, "metadata": meta, "configurationHash": config_hash(server),
                 "redirectUri": redirect_uri, "issuer": server["oauthIssuer"]}, owner, server["id"]
            ),
        )
        args = {
            "response_type": "code",
            "client_id": server["oauthClientId"],
            "redirect_uri": redirect_uri,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
            "resource": self.resource(server),
        }
        if scopes:
            args["scope"] = " ".join(scopes)
        return (
            meta["authorization_endpoint"] + ("&" if "?" in meta["authorization_endpoint"] else "?") + urlencode(args)
        )

    async def complete(self, request, context):
        c, owner = self.client, context["owner"]
        state, code = request.query.get("state", ""), request.query.get("code", "")
        if not isinstance(state, str) or not 0 < len(state) <= 1024:
            raise McpError("invalid_oauth_state", "Missing or invalid authorization state")
        if not isinstance(code, str) or not 0 < len(code) <= 8192:
            raise McpError("invalid_oauth_response", "Missing or invalid authorization code")
        row = c.store.oauth_claim(digest(state), owner)
        server = c.server(row["server"], owner)
        await c.access(server, context, "connect")
        data = self.open(row["payload"], owner, server["id"])
        if (data["configurationHash"] != config_hash(server)
                or data.get("redirectUri") != c.oauth_redirect_uri(request)
                or data.get("issuer") != server["oauthIssuer"]
                or c.store.binding(owner, server["id"])["revision"] != row["revision"]):
            raise McpError("stale_tool")
        if (
            request.query.get("iss", server["oauthIssuer"]) != server["oauthIssuer"]
            or request.query.get("error")
        ):
            raise McpError("invalid_oauth_response")
        if (
            data["metadata"].get("authorization_response_iss_parameter_supported")
            and request.query.get("iss") != server["oauthIssuer"]
        ):
            raise McpError("invalid_oauth_response")
        with c.store.credential_lease(owner, server["id"]):
            secret = c.oauth_client_secret(server, owner)
            form = {
                "grant_type": "authorization_code",
                "code": code,
                "code_verifier": data["verifier"],
                "client_id": server["oauthClientId"],
                "redirect_uri": data["redirectUri"],
                "resource": self.resource(server),
            }
            if secret is not None:
                form["client_secret"] = secret
            token = await self.request(
                data["metadata"]["token_endpoint"],
                "POST",
                form,
                timeout=60,
                token_exchange=True,
            )
            saved = self.normalize(token, data["metadata"], server)
            c.store.credential(owner, server["id"], row["revision"], self.seal(saved, owner, server["id"]))
        c.invalidate(owner, server["id"])
        return server["id"]

    def normalize(self, token, metadata, server, previous=None):
        if isinstance(token.get("error"), str):
            # Only report known OAuth error identifiers; provider descriptions may
            # contain account details and must never reach the callback page.
            known_errors = {
                "access_denied", "bad_verification_code", "expired_token", "incorrect_client_credentials",
                "invalid_client", "invalid_grant", "invalid_scope", "redirect_uri_mismatch",
            }
            error = token["error"] if token["error"] in known_errors else "rejected"
            raise McpError("oauth_" + error, "OAuth provider rejected the token request")
        if (
            not isinstance(token.get("access_token"), str)
            or not token["access_token"]
            or token.get("token_type", "").lower() != "bearer"
        ):
            raise McpError("invalid_oauth_response")
        expires = token.get("expires_in", 3600)
        if type(expires) not in (float, int) or not 0 < expires <= 31536000:
            raise McpError("invalid_oauth_response")
        return {
            "accessToken": token["access_token"],
            "refreshToken": token.get("refresh_token", (previous or {}).get("refreshToken")),
            "expiresAt": time.time() + expires,
            "metadata": metadata,
            "configurationHash": config_hash(server),
        }

    async def credential(self, server, owner):
        c = self.client
        binding = c.store.binding(owner, server["id"])
        if not binding["credential"] or binding["disconnected"]:
            raise McpError("auth_required")
        token = self.open(binding["credential"], owner, server["id"])
        if token["configurationHash"] != config_hash(server):
            raise McpError("auth_required")
        if token["expiresAt"] <= time.time() + 30:
            if not token.get("refreshToken"):
                raise McpError("auth_required")
            form = {
                "grant_type": "refresh_token",
                "refresh_token": token["refreshToken"],
                "client_id": server["oauthClientId"],
                "resource": self.resource(server),
            }
            secret = c.oauth_client_secret(server, owner)
            if secret is not None:
                form["client_secret"] = secret
            response = await self.request(
                token["metadata"]["token_endpoint"],
                "POST",
                form,
                timeout=c.limits["callTimeout"],
                token_exchange=True,
            )
            token = self.normalize(response, token["metadata"], server, token)
            c.store.credential(owner, server["id"], binding["revision"], self.seal(token, owner, server["id"]))
        return {"accessToken": token["accessToken"], "revision": str(binding["revision"])}
