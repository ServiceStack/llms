"""User-scoped MCP tool catalogs and dispatch, following the C# client contracts."""

import asyncio
import contextlib
import copy
import os
import re
import time
import uuid
from contextlib import asynccontextmanager
from urllib.parse import urlunsplit

from . import schema
from .common import McpError, alias, allows, bounded, config_hash, digest, dumps, limits, maybe_await, needs_approval
from .configuration import Configuration
from .headers import header_mappings, parameter_headers
from .oauth import OAuth
from .results import map_result
from .store import Store, now
from .transport import VERSIONS, HttpSession, NetworkPolicy


CALLBACK_PATH = "/ext/mcp_client/oauth/callback"
LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


class Client(Configuration):
    def __init__(self, ctx, config, hooks=None):
        self.ctx, self.app, self.config, self.hooks = ctx, ctx.app, copy.deepcopy(config), hooks or {}
        if bool(self.hooks.get("protect")) != bool(self.hooks.get("unprotect")):
            raise McpError("invalid_configuration", "MCP protect and unprotect hooks must be configured together")
        self.limits = limits(config.get("limits", {}))
        self.policy = NetworkPolicy(config.get("networkPolicy"))
        self.servers = self.config.get("servers", [])
        if not ctx.is_auth_enabled() and not self.config.get("singleUserMode"):
            raise McpError("invalid_configuration", "MCP without authentication requires explicit single-user mode")
        if self.config.get("oauthRedirectUri"):
            redirect = NetworkPolicy.validate_uri(self.config["oauthRedirectUri"])
            if redirect.query or redirect.scheme != "https" and not (
                redirect.scheme == "http" and redirect.hostname in LOOPBACK_HOSTS
            ):
                raise McpError("invalid_configuration", "OAuth callback requires HTTPS or a local loopback URL")
        self.validate_servers(self.servers)
        self.store = Store(os.path.join(ctx.get_user_path(), "mcp_client", "mcp.sqlite"))
        self.oauth = OAuth(self)
        self.entries, self.gates, self.active = {}, {}, set()
        self.closed = False
        self.session_factory = HttpSession

    def validate_servers(self, servers):
        seen = set()
        for server in servers:
            ident = server.get("id", "")
            if not re.fullmatch("[a-z][a-z0-9_]{0,31}", ident) or ident in seen:
                raise McpError("invalid_configuration", "Invalid or duplicate MCP server id")
            seen.add(ident)
            self.policy.uri(server.get("endpoint", ""))
            auth = server.setdefault("auth", {"mode": "anonymous"})
            if auth.get("mode") not in ("anonymous", "host_secret", "user_oauth", "bearer"):
                raise McpError("invalid_configuration", "Invalid authentication mode")
            if auth["mode"] == "host_secret" and (
                not auth.get("secretReference", "").strip() or not (self.hooks.get("authorize") or server.get("allowedUsers"))
            ):
                raise McpError("invalid_configuration", "Host credentials require an explicit audience")
            if server.get("protocolVersion", "auto") not in (*VERSIONS, "2026-07-28", "auto"):
                raise McpError("invalid_configuration", "Unsupported protocol version")
            for key in (
                "allowedTools",
                "deniedTools",
                "toolsWithoutApproval",
                "requiredRoles",
                "allowedUsers",
                "oauthScopes",
            ):
                if not isinstance(server.get(key, []), list) or any(
                    not isinstance(x, str) for x in server.get(key, [])
                ):
                    raise McpError("invalid_configuration", f"Invalid {key}")
            if server.get("approval", "always").lower() not in ("always", "never"):
                raise McpError("invalid_configuration", "Invalid approval policy")
            if "oauthIssuer" in server:
                self.policy.uri(server["oauthIssuer"])
            if auth["mode"] == "user_oauth":
                if not (self.ctx.is_auth_enabled() or self.config.get("singleUserMode")):
                    raise McpError("invalid_configuration", "OAuth requires authentication or single-user mode")
                self.policy.uri(server.get("oauthIssuer", ""))
                if not server.get("oauthClientId"):
                    raise McpError("invalid_configuration", "OAuth requires a registered client")

    def oauth_redirect_uri(self, request=None):
        configured = self.config.get("oauthRedirectUri")
        if configured:
            return configured

        # GitHub sign-in already has a trusted public callback URL. Reuse its
        # origin, not proxy headers or an untrusted Host header.
        auth_redirect = getattr(getattr(self.app, "auth_provider", None), "redirect_uri", None)
        if auth_redirect:
            try:
                source = NetworkPolicy.validate_uri(auth_redirect)
                if source.scheme == "https" or source.scheme == "http" and source.hostname in LOOPBACK_HOSTS:
                    return urlunsplit((source.scheme, source.netloc, CALLBACK_PATH, "", ""))
            except McpError:
                pass

        # The built-in server has no TLS listener. A local browser may use its
        # loopback address directly; remote deployments need a configured URL.
        if request is not None:
            try:
                source = NetworkPolicy.validate_uri(f"{request.scheme}://{request.host}")
                if source.hostname in LOOPBACK_HOSTS and source.path in ("", "/"):
                    return urlunsplit((source.scheme, source.netloc, CALLBACK_PATH, "", ""))
            except (McpError, AttributeError):
                pass
        return None

    def server(self, ident, owner=None):
        for server in self.get_servers(owner):
            if server["id"] == ident:
                return server
        raise McpError("not_found", "Connection not found")

    def request_context(self, request):
        user = self.ctx.get_username(request)
        return {"request": request, "user": user, "owner": user or "default"}

    async def access(self, server, context, operation):
        if self.closed:
            raise McpError("access_denied")
        request = context.get("request")
        if context.get("runId") and self.hooks.get("reauthorizeBackgroundRequest"):
            request = await maybe_await(self.hooks["reauthorizeBackgroundRequest"](context.get("user")))
            context["request"] = request
        user = context.get("user")
        owner = user or "default"
        context["owner"] = owner
        if owner in ("all", "*"):
            raise McpError("access_denied")
        if self.ctx.is_auth_enabled():
            if (
                request is None
                or not self.ctx.check_auth(request)[0]
                or self.ctx.get_username(request) != user
                or not user
            ):
                raise McpError("access_denied", "Current host authorization is required")
        elif not self.config.get("singleUserMode") or owner != "default":
            raise McpError("access_denied", "Anonymous MCP requires explicit single-user mode")
        if not any(s["id"] == server["id"] and config_hash(s) == config_hash(server) for s in self.get_servers(owner)):
            raise McpError("stale_tool", "Connection changed")
        if server["auth"]["mode"] == "user_oauth" and not (self.ctx.is_auth_enabled() or self.config.get("singleUserMode")):
            raise McpError("access_denied", "OAuth requires authentication or single-user mode")
        roles = (self.ctx.get_session(request) or {}).get("roles", []) if request is not None else []
        if any(role not in roles for role in server.get("requiredRoles", [])):
            raise McpError("access_denied")
        users = server.get("allowedUsers")
        if users and owner not in users:
            raise McpError("access_denied")
        authorize = self.hooks.get("authorize")
        if authorize and not await maybe_await(authorize(server, context, operation)):
            raise McpError("access_denied")

    def mutation(self, request):
        if self.ctx.is_auth_enabled() and not self.ctx.check_auth(request)[0]:
            raise McpError("access_denied", "Connection mutation requires authentication")
        if not self.ctx.is_auth_enabled() and not self.config.get("singleUserMode"):
            raise McpError("access_denied", "Connection mutation requires authentication")
        if request.headers.get("X-Mcp-Client") != "1" or request.headers.get("Sec-Fetch-Site") == "cross-site":
            raise McpError("access_denied", "MCP request verification failed")

    async def host_credential(self, server):
        if server["auth"]["mode"] != "host_secret":
            return None
        reference = server["auth"]["secretReference"]
        resolver = self.hooks.get("credentialStore")
        if resolver:
            value = await maybe_await(resolver(reference))
        else:
            # An environment variable reference is host configuration, never browser input.
            token = os.environ.get(reference)
            value = {"accessToken": token, "revision": digest(token)} if token else None
        if not value or not value.get("accessToken") or not value.get("revision"):
            raise McpError("auth_required", "Host credential is unavailable")
        return value

    def save_bearer(self, server, owner, token):
        if server["auth"]["mode"] != "bearer" or not isinstance(token, str) or not token or len(token) > 8192 or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in token):
            raise McpError("invalid_arguments", "Enter the token only, without the Bearer prefix")
        payload = self.oauth.seal({"accessToken": token, "configurationHash": config_hash(server)}, owner, server["id"])
        self.store.connect(owner, server["id"], disconnected=False, delete=True)
        binding = self.store.binding(owner, server["id"])
        self.store.credential(owner, server["id"], binding["revision"], payload)
        self.invalidate(owner, server["id"])

    def save_oauth_client_secret(self, server, owner, secret):
        if (server["auth"]["mode"] != "user_oauth" or not isinstance(secret, str)
                or not secret.strip() or len(secret) > 8192 or any(ord(c) < 32 or ord(c) == 127 for c in secret)):
            raise McpError("invalid_arguments", "Enter a valid OAuth client secret")
        binding = self.store.ensure_binding(owner, server["id"])
        self.store.client_secret(owner, server["id"], binding["revision"], self.oauth.seal(
            {"secret": secret, "configurationHash": config_hash(server)}, owner, server["id"]
        ))
        self.store.cancel_authorizations(owner, server["id"])
        self.invalidate(owner, server["id"])

    def oauth_client_secret(self, server, owner):
        binding = self.store.binding(owner, server["id"])
        if not binding["clientSecret"]:
            return None
        data = self.oauth.open(binding["clientSecret"], owner, server["id"])
        return data["secret"] if data.get("configurationHash") == config_hash(server) else None

    def bearer_credential(self, server, owner):
        binding = self.store.binding(owner, server["id"])
        if binding["disconnected"] or not binding["credential"]:
            raise McpError("auth_required", "Add a Bearer token in connection settings")
        data = self.oauth.open(binding["credential"], owner, server["id"])
        if data["configurationHash"] != config_hash(server):
            raise McpError("auth_required", "Connection changed; enter the Bearer token again")
        return {"accessToken": data["accessToken"], "revision": str(binding["revision"])}

    def entry(self, owner, server):
        stamp = time.monotonic()
        for key, value in list(self.entries.items()):
            if not value["users"] and stamp - value["lastUsed"] > self.limits["idleTimeout"]:
                del self.entries[key]
        key = (owner, server["id"])
        if key not in self.entries:
            if len(self.entries) >= self.limits["maxClients"]:
                raise McpError("busy", "Connection capacity reached")
            self.entries[key] = {
                "users": 0,
                "queued": 0,
                "lastUsed": stamp,
                "retryAfter": 0,
                "state": "disconnected",
                "reason": None,
                "protocol": None,
                "catalog": None,
                "discovered": 0,
                "revision": None,
                "calls": asyncio.Semaphore(self.limits["callsPerPrincipal"]),
                "discovery": asyncio.Lock(),
            }
        entry = self.entries[key]
        entry["lastUsed"] = stamp
        return entry

    def invalidate(self, owner, ident):
        entry = self.entries.get((owner, ident))
        if entry:
            entry.update(catalog=None, state="disconnected", reason=None, retryAfter=0)

    def status(self, owner, server):
        entry = self.entries.get((owner, server["id"]), {})
        binding = self.store.binding(owner, server["id"])
        mode = server["auth"]["mode"]
        return {
            "id": server["id"],
            "name": server.get("displayName") or server["id"],
            "accountMode": "application_account" if mode == "host_secret" else mode,
            "disabled": bool(binding["disconnected"]),
            "state": "disconnected" if binding["disconnected"] else entry.get("state", "disconnected"),
            "reason": entry.get("reason"),
            "protocol": entry.get("protocol"),
            "lastDiscovery": entry.get("lastDiscovery"),
            "group": "mcp_" + server["id"],
        }

    @asynccontextmanager
    async def session(self, server, context, discovery=False):
        await self.access(server, context, "discover" if discovery else "invoke")
        owner = context["owner"]
        entry = self.entry(owner, server)
        if entry["retryAfter"] > time.monotonic():
            raise McpError("backoff")
        if entry["queued"] >= self.limits["queueLength"] + self.limits["callsPerPrincipal"]:
            raise McpError("busy", "Connection queue is full")
        entry["users"] += 1
        entry["queued"] += 1
        task = asyncio.current_task()
        self.active.add(task)
        gate = self.gates.setdefault(server["id"], asyncio.Semaphore(self.limits["callsPerServer"]))
        timeout = self.limits["discoveryTimeout" if discovery else "callTimeout"]
        try:
            async with asyncio.timeout(timeout), entry["calls"], gate:
                await self.access(server, context, "discover" if discovery else "invoke")
                binding = self.store.binding(owner, server["id"])
                if binding["disconnected"]:
                    raise McpError("disconnected")
                credential = await self.host_credential(server)
                entry["state"] = "connecting"
                with (
                    self.store.credential_lease(owner, server["id"])
                    if server["auth"]["mode"] == "user_oauth"
                    else contextlib.nullcontext()
                ):
                    if server["auth"]["mode"] == "bearer":
                        credential = self.bearer_credential(server, owner)
                    if server["auth"]["mode"] == "user_oauth":
                        credential = await self.oauth.credential(server, owner)
                    revision = (
                        config_hash(server) + ":" + str(credential["revision"] if credential else binding["revision"])
                    )
                    async with self.session_factory(server, self.policy, self.limits, credential) as session:
                        entry["protocol"] = session.protocol
                        yield session, entry, revision
                    entry.update(state="ready", reason=None, retryAfter=0)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            failure = exc if isinstance(exc, McpError) else (
                McpError("timeout", "The MCP server did not respond before the connection timed out. Try again.")
                if isinstance(exc, TimeoutError) else
                McpError("connection_error", "Could not establish an MCP connection. Check the server URL and authentication settings.")
            )
            code = failure.code
            if getattr(failure, "remote_response", False) and code in ("remote_error", "http_400", "http_422"):
                # A complete request rejection is a tool failure, not a broken connection.
                entry.update(state="ready", reason=None, retryAfter=0)
            else:
                entry.update(
                    state=code if code in ("auth_required", "disconnected") else "degraded",
                    reason=code,
                    retryAfter=time.monotonic() + 3,
                )
            raise failure from None
        finally:
            self.active.discard(task)
            entry["users"] -= 1
            entry["queued"] -= 1
            entry["lastUsed"] = time.monotonic()

    async def discover(self, session, server):
        tools, names, cursors = [], set(), set()
        cursor, size = None, 0
        for _ in range(self.limits["maxPages"]):
            result = await session.list_tools(cursor)
            size += len(dumps(result).encode())
            if size > self.limits["maxCatalogBytes"]:
                raise McpError("catalog_limit")
            if not isinstance(result.get("tools"), list):
                raise McpError("invalid_catalog")
            for item in result["tools"]:
                name = item.get("name") if isinstance(item, dict) else None
                if not isinstance(name, str) or not 0 < len(name) <= 512 or name in names:
                    raise McpError("invalid_catalog")
                names.add(name)
                if len(names) > self.limits["maxTools"]:
                    raise McpError("catalog_limit")
                if not allows(server, name):
                    continue
                params, output = item.get("inputSchema"), item.get("outputSchema")
                schema.check(params, self.limits["maxSchemaBytes"])
                try:
                    header_mappings(params)
                except McpError:
                    # A malformed annotation invalidates this tool, not the full catalog.
                    continue
                if output is not None:
                    schema.check(output, self.limits["maxSchemaBytes"])
                description = item.get("description", name)
                if not isinstance(description, str):
                    raise McpError("invalid_catalog")
                tools.append(
                    {
                        "name": alias(server["id"], name),
                        "remoteName": name,
                        "description": description[:4096],
                        "schema": params,
                        "outputSchema": output,
                        "schemaHash": digest(dumps(params) + "\n" + (dumps(output) if output is not None else "")),
                        "requiresApproval": needs_approval(server, name),
                        "group": "mcp_" + server["id"],
                    }
                )
            cursor = result.get("nextCursor")
            if not cursor:
                return sorted(tools, key=lambda x: x["name"])
            if not isinstance(cursor, str) or cursor in cursors:
                raise McpError("invalid_catalog")
            cursors.add(cursor)
        raise McpError("catalog_limit")

    async def catalog(self, server, context, refresh=False):
        await self.access(server, context, "discover")
        owner = context["owner"]
        binding = self.store.binding(owner, server["id"])
        if binding["disconnected"]:
            raise McpError("disconnected")
        credential = await self.host_credential(server)
        revision = config_hash(server) + ":" + str(credential["revision"] if credential else binding["revision"])
        entry = self.entry(owner, server)
        # Count discovery-lock waiters too, so eviction cannot detach an active partition.
        entry["users"] += 1
        try:
            async with asyncio.timeout(self.limits["discoveryTimeout"]), entry["discovery"]:
                if (
                    not refresh
                    and entry["catalog"] is not None
                    and entry["revision"] == revision
                    and time.monotonic() - entry["discovered"] < self.limits["catalogFreshness"]
                ):
                    return copy.deepcopy(entry["catalog"])
                async with self.session(server, context, discovery=True) as (session, entry, revision):
                    tools = await self.discover(session, server)
                    entry.update(catalog=tools, revision=revision, discovered=time.monotonic(), lastDiscovery=now())
                    return copy.deepcopy(tools)
        finally:
            entry["users"] -= 1

    async def resolve(self, context, selector):
        handles = {}
        if (
            selector == "none"
            or context.get("mcpTransport")
            or (context.get("modelInfo") or {}).get("tool_call") is False
        ):
            return handles
        selected = {s.strip() for s in str(selector).split(",")}
        for server in self.get_servers(context.get("user") or "default"):
            group = "mcp_" + server["id"]
            if (
                selector != "__list"
                and not (selector == "all" and server.get("includeInAll"))
                and group not in selected
                and not any(s.startswith(group + "_") for s in selected)
            ):
                continue
            try:
                tools = await self.catalog(server, context)
            except McpError:
                continue  # A failed remote server must not hide local or other servers' tools.
            for tool in tools:
                if (
                    selector != "__list"
                    and not (selector == "all" and server.get("includeInAll"))
                    and group not in selected
                    and tool["name"] not in selected
                ):
                    continue
                if tool["name"] in self.app.tools:
                    raise McpError("alias_collision")
                binding = self.store.binding(context["owner"], server["id"])
                credential = await self.host_credential(server)
                handles[tool["name"]] = {
                    "provider": self,
                    "server": server,
                    "tool": tool,
                    "owner": context["owner"],
                    "configurationHash": config_hash(server),
                    "bindingRevision": binding["revision"],
                    "credentialRevision": credential["revision"] if credential else "",
                    "definition": {
                        "type": "function",
                        "function": {
                            "name": tool["name"],
                            "description": tool["description"],
                            "parameters": copy.deepcopy(tool["schema"]),
                        },
                    },
                }
        bounded([v["definition"] for v in handles.values()], self.limits["maxDefinitionBytes"], "definition_limit")
        return handles

    def validate_model(self, context, provider):
        if getattr(provider, "sdk", "") != "@ai-sdk/google":
            return

        def visit(node):
            if isinstance(node, dict):
                # Google's existing adapter removes additionalProperties. Reject
                # selected tools when that would weaken an assertion.
                if "additionalProperties" in node and node["additionalProperties"] is not True:
                    raise McpError("unsupported_model_schema", "Select another model or fewer MCP tools")
                for value in node.values():
                    visit(value)
            elif isinstance(node, list):
                for value in node:
                    visit(value)

        for handle in context.get("contextualTools", {}).values():
            visit(handle["tool"]["schema"])

    async def verify(self, handle, context, arguments):
        server, tool = handle["server"], handle["tool"]
        await self.access(server, context, "invoke")
        binding = self.store.binding(context["owner"], server["id"])
        if (
            context["owner"] != handle["owner"]
            or binding["disconnected"]
            or binding["revision"] != handle["bindingRevision"]
            or config_hash(server) != handle["configurationHash"]
            or not allows(server, tool["remoteName"])
        ):
            raise McpError("stale_tool")
        credential = await self.host_credential(server)
        if (credential["revision"] if credential else "") != handle["credentialRevision"]:
            raise McpError("stale_tool")
        if not isinstance(arguments, dict):
            raise McpError("schema_validation", "Tool arguments must be an object")
        schema.validate(tool["schema"], arguments, self.limits["maxSchemaBytes"])
        if self.app.should_cancel_thread(context):
            raise McpError("canceled")
        if context.get("threadId"):
            db = getattr(self.app, "agent_db", None)
            thread = db.get_thread(context["threadId"], user=context.get("user")) if db else None
            if not thread or thread.get("completedAt") or thread.get("error"):
                raise McpError("canceled", "Conversation is no longer active")

    def needs_approval(self, server, tool, owner):
        return needs_approval(server, tool["remoteName"]) and not self.store.has_approval_grant(
            owner, server["id"], tool["remoteName"], config_hash(server), tool["schemaHash"]
        )

    async def invoke(self, handle, arguments, context, approved=False, invocation_id=None):
        await self.verify(handle, context, arguments)
        server, tool, owner = handle["server"], handle["tool"], handle["owner"]
        if self.needs_approval(server, tool, owner) and not approved:
            raise McpError("approval_required", "Interactive approval is required")
        ident = invocation_id or uuid.uuid4().hex
        prior = self.store.invocation_claim(ident, owner)
        if prior is not None:
            return prior
        dispatched = received = False
        try:
            async with self.session(server, context) as (session, _, revision):
                current = next(
                    (t for t in await self.discover(session, server) if t["remoteName"] == tool["remoteName"]), None
                )
                if not current or current["schemaHash"] != tool["schemaHash"]:
                    raise McpError("stale_tool")
                await self.verify(handle, context, arguments)
                if (
                    server["auth"]["mode"] == "host_secret"
                    and revision != handle["configurationHash"] + ":" + handle["credentialRevision"]
                ):
                    raise McpError("stale_tool")
                if not approved and self.needs_approval(server, tool, owner):
                    raise McpError("approval_required", "Interactive approval is required")
                headers = parameter_headers(current["schema"], arguments)
                self.store.invocation_state(ident, owner, "dispatched")
                dispatched = True
                # Mark the enclosing model turn before network dispatch: even a
                # subsequent SQLite/checkpoint failure must not restart that turn.
                context["remoteToolsDispatched"] = True
                response = await session.call(tool["remoteName"], arguments, headers)
                received = True
                result = map_result(response, tool, server, self.limits)
            self.store.invocation_state(ident, owner, "failed" if result["isError"] else "completed", result)
            return result
        except BaseException as exc:
            code = (
                "outcome_unknown"
                if dispatched and not received and not getattr(exc, "remote_response", False)
                else (exc.code if isinstance(exc, McpError) else "connection_error")
            )
            self.store.invocation_state(ident, owner, code if code == "outcome_unknown" else "failed")
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise McpError(
                code,
                "The remote outcome is uncertain; reconcile before continuing."
                if code == "outcome_unknown"
                else "MCP invocation failed: " + code,
            ) from None

    async def close(self):
        self.closed = True
        tasks = list(self.active)
        for task in tasks:
            task.cancel()
        if tasks:
            # Match the host's bounded shutdown drain; a stuck remote cleanup
            # must not hold server shutdown indefinitely.
            done, _ = await asyncio.wait(tasks, timeout=10)
            for task in done:
                if not task.cancelled():
                    task.exception()
        self.entries.clear()

    def delete_user_credentials(self, owner):
        """Host user-deletion hook; remove MCP-owned credentials and claims."""
        for user, server in tuple(self.entries):
            if user == owner:
                self.invalidate(user, server)
        self.store.delete_user(owner)
