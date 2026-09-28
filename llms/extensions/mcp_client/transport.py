"""Operation-scoped Streamable HTTP. No automatic tool retries or ambient credentials."""

import asyncio
import contextlib

from .headers import encode_header
import ipaddress
import json
import socket
from urllib.parse import urlsplit

import aiohttp
from aiohttp.abc import AbstractResolver

from .common import McpError, bounded, dumps, http_error

VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")


class NetworkPolicy:
    def __init__(self, config=None):
        self.config = config or {}

    @staticmethod
    def validate_uri(url):
        try:
            p = urlsplit(url)
            port = p.port
        except (ValueError, TypeError):
            raise McpError("network_policy", "Invalid destination URL") from None
        if (
            p.scheme not in ("https", "http")
            or not p.hostname
            or p.username is not None
            or p.password is not None
            or p.fragment
            or any(ord(c) < 33 or c == "\\" for c in url)
            or (port is not None and not 1 <= port <= 65535)
        ):
            raise McpError("network_policy", "Destination is not permitted by the host")
        return p

    def uri(self, url):
        p = self.validate_uri(url)
        port = p.port or (443 if p.scheme == "https" else 80)
        if (p.scheme == "http" and not self.config.get("allowHttp")) or port not in self.config.get("allowedPorts", [443]):
            raise McpError("network_policy", "Destination is not permitted by the host")
        # Numeric hosts bypass aiohttp's resolver, so check them here too.
        with contextlib.suppress(ValueError):
            self.address(p.hostname)
        return p

    def address(self, host):
        address = ipaddress.ip_address(host.split("%")[0])
        mapped = getattr(address, "ipv4_mapped", None)
        address = mapped or address
        if not address.is_global:
            networks = self.config.get("allowedPrivateNetworks", [])
            if not any(address in ipaddress.ip_network(n) for n in networks):
                raise McpError("network_policy", "Private destination is not permitted by the host")

    def client(self, timeout):
        return aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(resolver=CheckedResolver(self), use_dns_cache=False),
            cookie_jar=aiohttp.DummyCookieJar(),
            trust_env=False,
            timeout=aiohttp.ClientTimeout(total=timeout),
            auto_decompress=True,
        )


class CheckedResolver(AbstractResolver):
    def __init__(self, policy):
        self.policy = policy
        self.resolver = aiohttp.resolver.ThreadedResolver()

    async def resolve(self, host, port=0, family=socket.AF_INET):
        rows = await self.resolver.resolve(host, port, family)
        for row in rows:
            self.policy.address(row["host"])
        return rows

    async def close(self):
        await self.resolver.close()


async def read_bounded(response, limit):
    data = bytearray()
    async for chunk in response.content.iter_chunked(65536):
        data.extend(chunk)
        if len(data) > limit:
            raise McpError("response_limit")
    return bytes(data)


def parse_json(data):
    try:
        result = json.loads(data, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        return bounded(result, max(len(data) * 4, 1024))
    except (ValueError, UnicodeError, RecursionError):
        raise McpError("invalid_response", "Invalid JSON response") from None


async def sse_messages(response, limit):
    """Yield complete SSE data events without waiting for the stream to close."""
    pending = bytearray()
    event = []
    total = 0
    async for chunk in response.content.iter_chunked(65536):
        total += len(chunk)
        if total > limit:
            raise McpError("response_limit")
        pending.extend(chunk)
        while (end := pending.find(b"\n")) >= 0:
            line = bytes(pending[:end]).removesuffix(b"\r")
            del pending[: end + 1]
            if line.startswith(b"data:"):
                event.append(line[5:].removeprefix(b" "))
            elif not line and event:
                yield parse_json(b"\n".join(event).decode("utf-8-sig"))
                event = []
    if pending.startswith(b"data:"):
        event.append(bytes(pending[5:]).removeprefix(b" "))
    if event:
        yield parse_json(b"\n".join(event).decode("utf-8-sig"))


def rpc_result(message, ident):
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        raise McpError("invalid_response")
    if "method" in message:
        if "id" in message:
            raise McpError("unsupported_operation", "Server requested client interaction")
        return None
    if message.get("id") != ident:
        raise McpError("invalid_response", "Mismatched RPC response")
    if "error" in message:
        error = message["error"]
        code = "method_not_found" if isinstance(error, dict) and error.get("code") == -32601 else "remote_error"
        failure = McpError(code, "Remote MCP request failed")
        failure.remote_response = True
        raise failure
    result = message.get("result")
    if not isinstance(result, dict):
        raise McpError("invalid_response", "Missing RPC result")
    if "inputRequests" in result or "requestState" in result or result.get("resultType") == "input_required":
        raise McpError("unsupported_operation", "Server continuation is not supported; call will not be replayed")
    return result


class HttpSession:
    def __init__(self, server, policy, limits, credential=None):
        self.server, self.policy, self.limits = server, policy, limits
        self.credential = credential
        self.http = None
        self.session_id = None
        self.protocol = None
        self.counter = 0
        self.called = False

    async def __aenter__(self):
        self.policy.uri(self.server["endpoint"])
        self.http = self.policy.client(self.limits["callTimeout"])
        try:
            preferred = self.server.get("protocolVersion", "auto")
            if preferred in ("auto", "2026-07-28"):
                self.protocol = "2026-07-28"
                try:
                    discovered = await self.rpc("server/discover")
                    if self.protocol not in discovered.get("supportedVersions", []):
                        raise McpError("unsupported_protocol")
                except McpError as exc:
                    if preferred != "auto" or exc.code not in (
                        "method_not_found",
                        "unsupported_discovery",
                        "unsupported_protocol",
                    ):
                        raise
                    self.protocol = None
            if self.protocol is None:
                result = await self.rpc(
                    "initialize",
                    {
                        "protocolVersion": "2025-11-25" if preferred == "auto" else preferred,
                        "capabilities": {},
                        "clientInfo": {"name": "llms-py", "version": "1.0"},
                    },
                )
                self.protocol = result.get("protocolVersion")
                if self.protocol not in VERSIONS:
                    raise McpError("unsupported_protocol")
                await self.rpc("notifications/initialized", {}, notification=True)
            return self
        except BaseException:
            await self.http.close()
            raise

    async def __aexit__(self, *exc):
        if self.session_id:
            with contextlib.suppress(Exception):
                async with asyncio.timeout(2):
                    async with self.http.delete(self.server["endpoint"], headers=self.headers(), allow_redirects=False):
                        pass
        await self.http.close()

    def headers(self):
        headers = {"Accept": "application/json, text/event-stream"}
        if self.credential:
            token = self.credential["accessToken"]
            if not isinstance(token, str) or not token or any(ord(c) < 33 or ord(c) > 126 for c in token):
                raise McpError("auth_required")
            headers["Authorization"] = "Bearer " + token
        if self.session_id:
            headers["MCP-Session-Id"] = self.session_id
        if self.protocol:
            headers["MCP-Protocol-Version"] = self.protocol
        return headers

    async def rpc(self, method, params=None, notification=False, parameter_headers=None):
        self.counter += 1
        ident = self.counter
        params = dict(params or {})
        if method == "tools/call":
            if self.called or "inputResponses" in params or "requestState" in params:
                raise McpError("replay_blocked")
            self.called = True
        headers = self.headers()
        if self.protocol == "2026-07-28":
            params["_meta"] = {
                "io.modelcontextprotocol/protocolVersion": self.protocol,
                "io.modelcontextprotocol/clientInfo": {"name": "llms-py", "version": "1.0"},
                "io.modelcontextprotocol/clientCapabilities": {},
            }
            headers["Mcp-Method"] = method
            if "name" in params:
                headers["Mcp-Name"] = encode_header(params["name"])
        if parameter_headers:
            headers.update(parameter_headers)
        body = {"jsonrpc": "2.0", "method": method, "params": params}
        if not notification:
            body["id"] = ident
        async with self.http.post(
            self.server["endpoint"],
            data=dumps(body).encode(),
            headers={**headers, "Content-Type": "application/json"},
            allow_redirects=False,
        ) as response:
            if response.status >= 300:
                if method == "server/discover" and response.status in (400, 404, 405):
                    raise McpError("unsupported_discovery", f"MCP HTTP status {response.status}")
                raise http_error(response.status)
            if method == "initialize" and "MCP-Session-Id" in response.headers:
                session_id = response.headers["MCP-Session-Id"]
                if not session_id or len(session_id) > 1024 or any(not 33 <= ord(c) <= 126 for c in session_id):
                    raise McpError("invalid_response")
                self.session_id = session_id
            if notification:
                if response.status != 202:
                    raise McpError("invalid_response", "Notification was not accepted")
                return {}
            content_type = response.headers.get("Content-Type", "").split(";")[0].strip()
            if content_type == "text/event-stream":
                try:
                    async for message in sse_messages(response, self.limits["maxResponseBytes"]):
                        result = rpc_result(message, ident)
                        if result is not None:
                            return result
                except UnicodeError:
                    raise McpError("invalid_response") from None
            elif content_type == "application/json":
                data = await read_bounded(response, self.limits["maxResponseBytes"])
                result = rpc_result(parse_json(data), ident)
                if result is not None:
                    return result
            else:
                raise McpError("invalid_response", "Expected JSON or SSE")
            raise McpError("invalid_response", "Missing RPC result")

    async def list_tools(self, cursor=None):
        return await self.rpc("tools/list", {"cursor": cursor} if cursor else {})

    async def call(self, name, arguments, parameter_headers=None):
        return await self.rpc("tools/call", {"name": name, "arguments": arguments}, parameter_headers=parameter_headers)
