"""Shared MCP contracts; wire names match ServiceStack.AI.Chat's McpClientExtension."""

import hashlib
import inspect
import json
import math
import re

from llms.main import ContextualToolError as McpError


def dumps(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def alias(server, name):
    # .NET Regex operates on UTF-16 units; retain identical aliases even for names
    # containing non-BMP characters (each surrogate becomes an underscore).
    encoded = f"mcp_{server}_{name}".encode("utf-16-le")
    prefix = "".join(chr(int.from_bytes(encoded[i : i + 2], "little")) for i in range(0, len(encoded), 2))
    return re.sub("[^a-zA-Z0-9_]", "_", prefix)[:47] + "_" + digest(server + "\0" + name)[:16]


async def maybe_await(value):
    return await value if inspect.isawaitable(value) else value


def bounded(value, limit, code="response_limit"):
    def visit(node, depth=0):
        if depth > 32:
            raise McpError(code, "JSON nesting exceeds 32 levels")
        if isinstance(node, dict):
            for x in node.values():
                visit(x, depth + 1)
        elif isinstance(node, list):
            for x in node:
                visit(x, depth + 1)
        elif isinstance(node, float) and not math.isfinite(node):
            raise McpError(code, "Non-finite JSON number")

    visit(value)
    if len(dumps(value).encode()) > limit:
        raise McpError(code)
    return value


LIMITS = {
    "discoveryTimeout": 15,
    "callTimeout": 60,
    "catalogFreshness": 300,
    "maxTools": 256,
    "maxPages": 100,
    "maxSchemaBytes": 65536,
    "maxCatalogBytes": 1048576,
    "maxResponseBytes": 4194304,
    "maxDefinitionBytes": 1048576,
    "maxClients": 128,
    "callsPerPrincipal": 4,
    "callsPerServer": 16,
    "queueLength": 32,
    "idleTimeout": 900,
}
MAX_LIMITS = {
    "discoveryTimeout": 60,
    "callTimeout": 300,
    "maxTools": 1024,
    "maxPages": 100,
    "maxSchemaBytes": 262144,
    "maxCatalogBytes": 4194304,
    "maxResponseBytes": 16777216,
    "maxDefinitionBytes": 4194304,
    "maxClients": 1024,
    "callsPerPrincipal": 16,
    "callsPerServer": 64,
    "queueLength": 256,
    "catalogFreshness": 86400,
    "idleTimeout": 86400,
}


def limits(config):
    result = {**LIMITS, **config}
    for key, value in result.items():
        if (
            key not in MAX_LIMITS
            or type(value) not in (int, float)
            or not (0 if key == "queueLength" else 0.001) <= value <= MAX_LIMITS[key]
        ):
            raise McpError("invalid_configuration", f"Invalid MCP limit: {key}")
        if (key.startswith(("max", "calls")) or key == "queueLength") and type(value) is not int:
            raise McpError("invalid_configuration", f"{key} must be an integer")
    return result


def allows(server, name):
    denied, allowed = server.get("deniedTools", []), server.get("allowedTools", [])
    return "*" not in denied and name not in denied and ("*" in allowed or name in allowed)


def needs_approval(server, name):
    return server.get("approval", "always").lower() != "never" and name not in server.get("toolsWithoutApproval", [])


def config_hash(server):
    return digest(dumps({k: v for k, v in sorted(server.items()) if k != "authorize"}))


def http_error(status):
    # Never expose remote response bodies: they can echo credentials.
    errors = {
        400: ("http_400", "The MCP server rejected the request (HTTP 400). Check the server URL and credentials; for Bearer authentication, enter the token only."),
        401: ("auth_required", "The MCP server rejected authentication (HTTP 401). Enter a valid token or reconnect your account."),
        403: ("access_denied", "The MCP server denied access (HTTP 403). Check the token permissions and your account's access to this server."),
        404: ("http_404", "The MCP endpoint was not found (HTTP 404). Check the server URL."),
        429: ("rate_limited", "The MCP server rate limit was reached (HTTP 429). Try again later."),
    }
    failure = McpError(*errors.get(status, (f"http_{status}", f"The MCP server returned HTTP {status}. Check the server configuration or try again later.")))
    if status in (400, 401, 403, 404, 405, 422, 429):
        failure.remote_response = True
    return failure
