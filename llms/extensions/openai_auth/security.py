"""Public SIWC protocol and protected, per-user credential storage.

Only aiohttp and the standard library are used. RS256 verification implements the
strict public-key verification operation from RFC 8017, section 8.2.2; no signing
or private-key operations are implemented here.
"""

import asyncio
import base64
import copy
import hashlib
import ipaddress
import json
import math
import os
import secrets
import tempfile
import threading
import time
import urllib.parse
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import aiohttp


class SubscriptionError(Exception):
    """A safe user-facing error which must not replay an inference request."""

    retryable = False


def parse_object(raw):
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8")

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON property")
            result[key] = value
        return result

    def finite(value):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("Non-finite JSON number")
        return number

    value = json.loads(
        raw,
        object_pairs_hook=pairs,
        parse_float=finite,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Non-finite JSON number")),
    )
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object")
    return value


def endpoint(url):
    parsed = urllib.parse.urlsplit(url)
    try:
        loopback = ipaddress.ip_address(parsed.hostname or "").is_loopback
    except ValueError:
        loopback = False
    if (
        not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or parsed.scheme != "https"
        and not (parsed.scheme == "http" and loopback)
    ):
        raise SubscriptionError("Configure an HTTPS subscription endpoint.")
    return url


@dataclass
class Options:
    issuer: str = "https://auth.openai.com"
    authorization_url: str = "https://auth.openai.com/api/accounts/authorize"
    token_url: str = "https://auth.openai.com/api/accounts/oauth/token"
    jwks_url: str = "https://auth.openai.com/.well-known/jwks.json"
    responses_url: str = "https://api.openai.com/v1/responses"
    models_url: str = "https://api.openai.com/v1/models"
    resource: str = "https://api.openai.com/v1"
    client_id: str = "dynamic_agent_client"
    scope: str = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"
    agent_name: str = "llms-py"
    redirect_uri: str = "http://127.0.0.1:1455/auth/callback"
    automatic_callback: bool = True
    flow_lifetime: int = 600
    timeout: int = 20
    # Both are explicit host callbacks. Never probe or import the operator's file.
    local_credentials_path: Callable | None = None
    can_import_local_credentials: Callable | None = None


def check_path(path):
    for part in (Path(path), *Path(path).parents):
        if part.is_symlink():
            raise SubscriptionError("Subscription credential paths must not contain links.")


def read_file(path):
    check_path(path)
    try:
        with open(path, "rb") as source:
            raw = source.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise ValueError("File too large")
        return parse_object(raw)
    except FileNotFoundError:
        return None
    except (ValueError, OSError):
        raise SubscriptionError("Subscription credentials are unreadable. Sign in again.") from None


def write_file(path, value):
    check_path(path)
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    check_path(path)
    fd, temporary = tempfile.mkstemp(prefix=".openai-", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(value, output, allow_nan=False)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)


class UserState:
    def __init__(self):
        self.gate = threading.RLock()
        self.refresh = asyncio.Lock()
        self.catalog = asyncio.Lock()
        self.generation = 0


_states = {}
_states_gate = threading.Lock()


class Store:
    """One server process owns its data root; gates span extension instances."""

    def __init__(self, ctx):
        self.ctx = ctx

    def path(self, user=None):
        user = user or "default"
        if not isinstance(user, str) or user in (".", "..") or any(c in user for c in "/\\\x00"):
            raise SubscriptionError("Invalid subscription user.")
        return os.path.abspath(os.path.join(self.ctx.get_user_path(user), "credentials", "openai_subscription.json"))

    def state(self, user=None):
        with _states_gate:
            return _states.setdefault(os.path.normcase(self.path(user)), UserState())

    def load(self, user=None):
        with self.state(user).gate:
            return read_file(self.path(user))

    @staticmethod
    def fingerprint(value):
        return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()

    def snapshot(self, user=None):
        state = self.state(user)
        with state.gate:
            value = self.load(user)
            return value, state.generation, self.fingerprint(value)

    def save(self, user, value, expected=None):
        state = self.state(user)
        with state.gate:
            if expected is not None and (state.generation, self.fingerprint(self.load(user))) != expected:
                raise SubscriptionError("The subscription changed during authentication. Sign in again.")
            write_file(self.path(user), value)
            state.generation += 1

    def disconnect(self, user=None):
        state = self.state(user)
        with state.gate:
            state.generation += 1
            path = self.path(user)
            check_path(path)
            if os.path.exists(path):
                grant = self.load(user) or {}
                if grant.get("subject") and grant.get("issuer") and grant.get("client_id") != "dynamic_agent_client":
                    # Retain only the verified registration, never any tokens.
                    write_file(
                        os.path.join(os.path.dirname(path), "openai_registration.json"),
                        {k: grant[k] for k in ("issuer", "subject", "client_id", "ext_agent_host_id") if k in grant},
                    )
                os.remove(path)

    def registration(self, user):
        with self.state(user).gate:
            return read_file(os.path.join(os.path.dirname(self.path(user)), "openai_registration.json")) or {}

    def host_id(self):
        # A stable installation identity, independent of any user's tokens.
        path = str(Path(self.path("default")).parents[3] / "openai-agent-host.json")
        with _states_gate:
            value = read_file(path)
            if value is None:
                value = {"id": "urn:uuid:" + str(uuid.uuid4())}
                write_file(path, value)
            if not isinstance(value.get("id"), str) or not value["id"]:
                raise SubscriptionError("Invalid subscription host identity.")
            return value["id"]


class Transport:
    def __init__(self, options):
        self.options = options

    async def json(self, method, url, **kwargs):
        endpoint(url)
        try:
            async with (
                aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar(), trust_env=False) as session,
                session.request(
                    method,
                    url,
                    allow_redirects=False,
                    timeout=aiohttp.ClientTimeout(total=self.options.timeout),
                    **kwargs,
                ) as response,
            ):
                if response.status != 200:
                    raise SubscriptionError(
                        f"OpenAI credential service returned HTTP {response.status}. Sign in again or try later."
                    )
                raw = bytearray()
                async for chunk in response.content.iter_chunked(16384):
                    raw.extend(chunk)
                    if len(raw) > 1024 * 1024:
                        raise SubscriptionError("OpenAI credential response exceeds the size limit.")
                return parse_object(bytes(raw))
        except (aiohttp.ClientError, TimeoutError, ValueError):
            raise SubscriptionError(
                "Could not read the OpenAI credential response. Sign in again or try later."
            ) from None

    async def token(self, form):
        return await self.json("POST", self.options.token_url, data=form)

    async def revoke(self, grant):
        discovery = await self.json("GET", self.options.issuer.rstrip("/") + "/.well-known/openid-configuration")
        if discovery.get("issuer") != self.options.issuer:
            raise SubscriptionError("OpenAI revocation discovery has an unexpected issuer.")
        url = endpoint(discovery.get("revocation_endpoint", ""))
        try:
            async with (
                aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar(), trust_env=False) as session,
                session.post(
                    url,
                    data={
                        "token": grant["refresh_token"],
                        "token_type_hint": "refresh_token",
                        "client_id": grant["client_id"],
                    },
                    allow_redirects=False,
                    timeout=aiohttp.ClientTimeout(total=self.options.timeout),
                ) as response,
            ):
                return response.status == 200
        except (aiohttp.ClientError, TimeoutError):
            return False


def decode_base64(value):
    if (
        not isinstance(value, str)
        or not value
        or any(c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_" for c in value)
    ):
        raise ValueError("Invalid base64url")
    return base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)


class Identity:
    def __init__(self, options, http):
        self.options, self.http = options, http
        self.keys, self.loaded_at = [], 0
        self.gate = asyncio.Lock()

    async def verify(self, jwt, client_id, nonce=None):
        try:
            if not isinstance(jwt, str) or len(jwt) > 128 * 1024:
                raise ValueError()
            header64, claims64, signature64 = jwt.split(".")
            header = parse_object(decode_base64(header64))
            if (
                header.get("alg") != "RS256"
                or not isinstance(header.get("kid"), str)
                or not header["kid"]
                or header.get("crit")
            ):
                raise ValueError()
            async with self.gate:
                matches = [key for key in self.keys if key.get("kid") == header["kid"]]
                if not matches or time.time() - self.loaded_at > 3600:
                    jwks = await self.http.json("GET", self.options.jwks_url)
                    self.keys = jwks.get("keys", [])
                    if not isinstance(self.keys, list) or any(not isinstance(k, dict) for k in self.keys):
                        raise ValueError()
                    self.loaded_at = time.time()
                    matches = [key for key in self.keys if key.get("kid") == header["kid"]]
                if len(matches) != 1:
                    raise ValueError()
                key = matches[0]
            if (
                key.get("kty") != "RSA"
                or key.get("alg", "RS256") != "RS256"
                or key.get("use", "sig") != "sig"
                or "key_ops" in key
                and "verify" not in key["key_ops"]
            ):
                raise ValueError()
            n = int.from_bytes(decode_base64(key["n"]), "big")
            e = int.from_bytes(decode_base64(key["e"]), "big")
            if not 2048 <= n.bit_length() <= 8192 or not 3 <= e <= 0xFFFFFFFF or e % 2 != 1:
                raise ValueError()
            signature = decode_base64(signature64)
            width = (n.bit_length() + 7) // 8
            s = int.from_bytes(signature, "big")
            if len(signature) != width or s >= n:
                raise ValueError()
            digest_info = (
                bytes.fromhex("3031300d060960864801650304020105000420")
                + hashlib.sha256((header64 + "." + claims64).encode("ascii")).digest()
            )
            expected = b"\x00\x01" + b"\xff" * (width - len(digest_info) - 3) + b"\x00" + digest_info
            if not secrets.compare_digest(pow(s, e, n).to_bytes(width, "big"), expected):
                raise ValueError()
            claims = parse_object(decode_base64(claims64))
            now = time.time()
            audience = claims.get("aud")
            if (
                claims.get("iss") != self.options.issuer
                or not isinstance(claims.get("sub"), str)
                or not claims["sub"]
                or not (audience == client_id or isinstance(audience, list) and client_id in audience)
                or isinstance(audience, list)
                and len(audience) > 1
                and claims.get("azp") != client_id
                or claims.get("azp", client_id) != client_id
                or type(claims.get("exp")) is not int
                or claims["exp"] <= now
                or "nbf" in claims
                and (type(claims["nbf"]) is not int or claims["nbf"] > now + 30)
                or "iat" in claims
                and (type(claims["iat"]) is not int or claims["iat"] > now + 30)
                or nonce is not None
                and claims.get("nonce") != nonce
            ):
                raise ValueError()
            return claims
        except (ValueError, KeyError, TypeError, OverflowError):
            raise SubscriptionError("OpenAI identity verification failed. Start a new sign-in.") from None


def account_info(claims):
    auth = claims.get("https://api.openai.com/auth") or {}
    profile = claims.get("https://api.openai.com/profile") or {}
    auth = auth if isinstance(auth, dict) else {}
    profile = profile if isinstance(profile, dict) else {}
    account_id = (
        auth.get("chatgpt_account_id")
        or auth.get("account_id")
        or claims.get("chatgpt_account_id")
        or claims.get("account_id")
        or ""
    )
    email = claims.get("email") or profile.get("email") or ""
    plan = auth.get("chatgpt_plan_type") or auth.get("plan_type") or claims.get("plan")
    return {
        "email": email,
        "name": claims.get("name")
        or claims.get("preferred_username")
        or (email.split("@")[0] if email else "ChatGPT User"),
        "plan": "ChatGPT Subscription"
        if not plan
        else str(plan)
        if str(plan).lower().startswith("chatgpt")
        else "ChatGPT " + str(plan).title(),
        "account_id": account_id,
    }


def token_string(value):
    return isinstance(value, str) and 0 < len(value) <= 128 * 1024 and all(33 <= ord(c) <= 126 for c in value)


class Auth:
    def __init__(self, ctx, options=None):
        self.options = options or Options()
        self.store = Store(ctx)
        self.http = Transport(self.options)
        self.identity = Identity(self.options, self.http)
        self.pending, self.catalogs = {}, {}
        self.callback_errors = {}
        self.callback_receiver = None
        self.closed = False

    def close(self):
        self.closed = True
        self.pending.clear()
        self.catalogs.clear()
        self.callback_errors.clear()

    async def cleanup(self):
        self.close()
        if self.callback_receiver:
            await self.callback_receiver.close()

    def has_pending(self, user):
        self.pending = {
            u: flow for u, flow in self.pending.items() if time.time() - flow["created_at"] < self.options.flow_lifetime
        }
        return user in self.pending

    def callback_error(self, user):
        self.callback_errors = {
            u: error for u, error in self.callback_errors.items() if time.time() - error[1] < self.options.flow_lifetime
        }
        return self.callback_errors.get(user, (None, 0))[0]

    def connect(self, user, *, redirect_uri=None, automatic=False, return_url=None):
        if self.closed:
            raise SubscriptionError("Subscription service is shutting down.")
        endpoint(self.options.authorization_url)
        redirect_uri = redirect_uri or self.options.redirect_uri
        redirect = urllib.parse.urlsplit(redirect_uri)
        if (
            redirect.scheme != "http"
            or redirect.hostname != "127.0.0.1"
            or redirect.path != "/auth/callback"
            or redirect.username is not None
            or redirect.fragment
            or redirect.query
        ):
            raise SubscriptionError("Configure the registered HTTP 127.0.0.1 /auth/callback URI.")
        self.has_pending(user)
        if len(self.pending) >= 1024 and user not in self.pending:
            raise SubscriptionError("Too many pending sign-ins. Try later.")
        previous, _, fingerprint = self.store.snapshot(user)
        previous = previous or self.store.registration(user)
        # Legacy Codex grants are deliberately not reused as public registrations.
        client = (
            previous.get("client_id")
            if previous.get("issuer") == self.options.issuer and previous.get("subject")
            else self.options.client_id
        )
        state = self.store.state(user)
        with state.gate:
            state.generation += 1
            generation = state.generation
        verifier = secrets.token_urlsafe(64)
        flow = {
            "state": secrets.token_urlsafe(32),
            "nonce": secrets.token_urlsafe(32),
            "verifier": verifier,
            "client_id": client,
            "redirect": redirect_uri,
            "automatic": automatic,
            "return_url": return_url,
            "created_at": time.time(),
            "generation": generation,
            "previous": fingerprint,
            "subject": previous.get("subject") if previous.get("issuer") == self.options.issuer else None,
        }
        self.pending[user] = flow
        self.callback_errors.pop(user, None)
        query = {
            "response_type": "code",
            "client_id": client,
            "redirect_uri": flow["redirect"],
            "scope": self.options.scope,
            "resource": self.options.resource,
            "state": flow["state"],
            "nonce": flow["nonce"],
            "ext_agent_host_id": self.store.host_id(),
            "code_challenge_method": "S256",
            "code_challenge": base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("="),
        }
        if client == "dynamic_agent_client":
            query["agent_name_hint"] = self.options.agent_name
        # Retained ID tokens stay on the server, rather than exposing hints through UI JSON.
        return {
            "auth_url": self.options.authorization_url + "?" + urllib.parse.urlencode(query),
            "redirect_uri": flow["redirect"],
            "port": redirect.port or 80,
            "manual_callback": not automatic,
            "automatic_callback": automatic,
        }

    async def automatic_callback(self, callback):
        if not isinstance(callback, str) or len(callback) > 16384:
            raise SubscriptionError("Invalid OpenAI callback URL.")
        try:
            pairs = urllib.parse.parse_qsl(
                urllib.parse.urlsplit(callback).query, keep_blank_values=True, max_num_fields=32
            )
            query = dict(pairs)
            if len(pairs) != len(query) or not query.get("state"):
                raise ValueError()
        except ValueError:
            raise SubscriptionError("Invalid OpenAI callback parameters.") from None
        self.has_pending("")  # Expire old attempts before looking up their initiating identity.
        match = next(
            (
                (user, flow)
                for user, flow in self.pending.items()
                if flow.get("automatic") and secrets.compare_digest(query["state"].encode(), flow["state"].encode())
            ),
            None,
        )
        if match is None:
            raise SubscriptionError("No matching active sign-in. Start a new sign-in.")
        user, flow = match
        try:
            await self.callback(user, callback)
        except SubscriptionError as error:
            if self.store.state(user).generation == flow["generation"]:
                self.callback_errors[user] = (str(error), time.time())
            raise
        return user, flow.get("return_url")

    def credentials(self, response, client, claims, previous=None):
        previous = previous or {}
        expiry = response.get("expires_in", 3600)
        scope = response.get("scope", previous.get("scope", ""))
        token_type = response.get("token_type", "Bearer")
        if (
            not token_string(response.get("access_token"))
            or type(expiry) is not int
            or not 0 < expiry <= 86400
            or not isinstance(token_type, str)
            or token_type.lower() != "bearer"
            or not isinstance(scope, str)
        ):
            raise SubscriptionError("OpenAI returned invalid subscription credentials.")
        for name in ("refresh_token", "id_token"):
            if response.get(name) is not None and not token_string(response[name]):
                raise SubscriptionError("OpenAI returned invalid subscription credentials.")
        earliest = response.get("earliest_refresh_at")
        if earliest is not None and (type(earliest) is not int or earliest < 0):
            raise SubscriptionError("OpenAI returned invalid refresh timing.")
        return {
            "client_id": client,
            "issuer": self.options.issuer,
            "subject": claims["sub"],
            "access_token": response["access_token"],
            "refresh_token": response.get("refresh_token", previous.get("refresh_token")),
            "id_token": response.get("id_token", previous.get("id_token")),
            "expires_at": time.time() + expiry,
            "earliest_refresh_at": earliest,
            "scope": scope,
            "plan_enabled": {"resource.invoke", "chatgpt.tokens.use.direct"}.issubset(scope.split()),
            "account": account_info(claims),
            "account_id": account_info(claims)["account_id"],
            "ext_agent_host_id": self.store.host_id(),
        }

    async def callback(self, user, callback):
        if self.closed or not self.has_pending(user):
            raise SubscriptionError("No active login for this user. Start a new sign-in.")
        if not isinstance(callback, str) or len(callback) > 16384 or any(ord(c) < 32 for c in callback):
            raise SubscriptionError("Paste the complete callback URL, including state.")
        flow = self.pending[user]
        try:
            uri = urllib.parse.urlsplit(callback)
            expected = urllib.parse.urlsplit(flow["redirect"])
            pairs = urllib.parse.parse_qsl(uri.query, keep_blank_values=True, max_num_fields=32)
            query = dict(pairs)
            if (
                len(pairs) != len(query)
                or (uri.scheme, uri.hostname, uri.port, uri.path)
                != (expected.scheme, expected.hostname, expected.port, expected.path)
                or uri.username is not None
                or uri.fragment
                or not secrets.compare_digest(query.get("state", "").encode("utf-8"), flow["state"].encode("utf-8"))
            ):
                raise ValueError()
        except ValueError:
            raise SubscriptionError(
                "Callback state or redirect does not match this sign-in. Paste the complete callback URL."
            ) from None
        if "error" in query:
            del self.pending[user]
            raise SubscriptionError("OpenAI sign-in was not authorized. Start a new sign-in.")
        if not query.get("code"):
            raise SubscriptionError("The callback URL is missing an authorization code.")
        client = query.get("client_id", flow["client_id"])
        if (
            not token_string(client)
            or len(client) > 512
            or client == "dynamic_agent_client"
            or flow["client_id"] != "dynamic_agent_client"
            and client != flow["client_id"]
        ):
            raise SubscriptionError("Callback client registration does not match this sign-in.")
        del self.pending[user]  # Exactly one exchange; never replay an uncertain code.
        response = await self.http.token(
            {
                "grant_type": "authorization_code",
                "client_id": client,
                "code": query["code"],
                "code_verifier": flow["verifier"],
                "redirect_uri": flow["redirect"],
                "resource": self.options.resource,
            }
        )
        claims = await self.identity.verify(response.get("id_token"), client, flow["nonce"])
        if flow["subject"] and claims["sub"] != flow["subject"]:
            raise SubscriptionError("The returning ChatGPT account does not match this registration.")
        credentials = self.credentials(response, client, claims)
        if self.closed:
            raise SubscriptionError("Subscription service is shutting down.")
        self.store.save(user, credentials, (flow["generation"], flow["previous"]))
        self.catalogs.pop(user, None)

    async def valid_credentials(self, user=None, rejected_token=None):
        user = user or "default"
        async with self.store.state(user).refresh:
            credentials, generation, fingerprint = self.store.snapshot(user)
            if self.closed:
                raise SubscriptionError("Subscription service is shutting down.")
            if credentials is None:
                return None
            if (
                credentials.get("issuer") != self.options.issuer
                or not token_string(credentials.get("client_id"))
                or credentials["client_id"] == "dynamic_agent_client"
                or not credentials.get("subject")
                or not token_string(credentials.get("access_token"))
            ):
                raise SubscriptionError("Subscription credentials require a new public ChatGPT sign-in in Settings.")
            now = time.time()
            expires = credentials.get("expires_at", 0)
            if not isinstance(expires, (int, float)) or not math.isfinite(expires):
                raise SubscriptionError("Invalid subscription expiry. Sign in again.")
            if (
                rejected_token is None
                and now < expires - 300
                or rejected_token is not None
                and credentials["access_token"] != rejected_token
            ):
                return credentials
            earliest = credentials.get("earliest_refresh_at")
            if (
                not credentials.get("refresh_token")
                or rejected_token is None
                and isinstance(earliest, int)
                and now < earliest
            ):
                if rejected_token is None and now < expires:
                    return credentials
                raise SubscriptionError("The ChatGPT subscription expired. Sign in again.")
            try:
                response = await self.http.token(
                    {
                        "grant_type": "refresh_token",
                        "client_id": credentials["client_id"],
                        "refresh_token": credentials["refresh_token"],
                        "resource": self.options.resource,
                    }
                )
                claims = (
                    await self.identity.verify(response["id_token"], credentials["client_id"])
                    if response.get("id_token")
                    else {"sub": credentials["subject"]}
                )
                if claims["sub"] != credentials["subject"]:
                    raise SubscriptionError("Subscription account changed. Sign in again.")
                updated = self.credentials(response, credentials["client_id"], claims, credentials)
                if not response.get("id_token"):
                    updated["account"], updated["account_id"] = (
                        credentials.get("account", {}),
                        credentials.get("account_id", ""),
                    )
            except SubscriptionError:
                current, current_generation, current_fingerprint = self.store.snapshot(user)
                if (
                    rejected_token is None
                    and time.time() < expires
                    and current is not None
                    and (current_generation, current_fingerprint) == (generation, fingerprint)
                    and not self.closed
                ):
                    return credentials
                raise
            if self.closed:
                raise SubscriptionError("Subscription service is shutting down.")
            self.store.save(user, updated, (generation, fingerprint))
            return updated

    def disconnect(self, user):
        self.store.disconnect(user)
        self.pending.pop(user, None)
        self.catalogs.pop(user, None)
        self.callback_errors.pop(user, None)

    async def models(self, user):
        async with self.store.state(user).catalog:
            grant = await self.valid_credentials(user)
            if not grant:
                raise SubscriptionError("Connect a ChatGPT subscription before requesting models.")
            if not grant.get("plan_enabled"):
                return []
            fingerprint = self.store.fingerprint(grant)
            cached = self.catalogs.get(user)
            if cached and cached[0] == fingerprint and time.time() - cached[1] < 300:
                return copy.deepcopy(cached[2])
            data = await self.http.json(
                "GET", self.options.models_url, headers={"Authorization": "Bearer " + grant["access_token"]}
            )
            if not isinstance(data.get("models"), list):
                raise SubscriptionError("ChatGPT returned an invalid model catalog.")
            rows = []
            for row in data["models"]:
                if (
                    not isinstance(row, dict)
                    or row.get("visibility") != "list"
                    or not token_string(row.get("slug"))
                    or len(row["slug"]) > 512
                ):
                    continue
                rows.append(
                    {
                        "id": row["slug"],
                        "name": row.get("display_name") or row["slug"],
                        "provider": "openai",
                        "tool_call": True,
                        "cost": {"input": 0, "output": 0},
                        "modalities": {"input": ["text", "image"], "output": ["text"]},
                    }
                )
            if self.closed or self.store.fingerprint(self.store.load(user)) != fingerprint:
                raise SubscriptionError("The subscription changed while listing models. Reload the model picker.")
            self.catalogs[user] = (fingerprint, time.time(), rows)
            return copy.deepcopy(rows)

    async def filter_models(self, models, user, api_available=False):
        other = [m for m in models if m.get("provider") != "openai"]
        try:
            if self.store.load(user) is None:
                return models if api_available else other
            return other + await self.models(user)
        except SubscriptionError:
            # Expired/legacy grants must not break SPA bootstrap or expose the
            # API-key catalog as a billing fallback. Settings remains reachable.
            return other
