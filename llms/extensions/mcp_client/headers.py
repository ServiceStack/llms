"""MCP parameter-header annotations for Streamable HTTP tool calls."""

import base64
import re

from .common import McpError

TOKEN = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+\Z")
SAFE_INTEGER = 2**53 - 1


def encode_header(value):
    if any(ord(c) < 32 or ord(c) > 126 for c in value) or value != value.strip() or (
        value.startswith("=?base64?") and value.endswith("?=")
    ):
        return "=?base64?" + base64.b64encode(value.encode("utf-8")).decode("ascii") + "?="
    return value


def header_mappings(schema):
    """Validate annotations and return (argument path, header name) pairs."""
    mappings, seen = [], set()

    def walk(node, path=(), static=True, property_node=False):
        if isinstance(node, list):
            for item in node:
                walk(item, path, False)
            return
        if not isinstance(node, dict):
            return
        if "x-mcp-header" in node:
            name = node["x-mcp-header"]
            types = node.get("type")
            types = [types] if isinstance(types, str) else types
            if (
                not static or not property_node or not isinstance(name, str) or not TOKEN.fullmatch(name)
                or name.lower() in seen or not isinstance(types, list) or not types
                or any(t not in ("string", "integer", "boolean", "null") for t in types)
                or not any(t in ("string", "integer", "boolean") for t in types)
            ):
                raise McpError("invalid_catalog", "Invalid MCP parameter-header annotation")
            seen.add(name.lower())
            mappings.append((path, name))
        for key, child in node.items():
            if key == "properties" and isinstance(child, dict):
                for property_name, property_schema in child.items():
                    walk(property_schema, path + (property_name,), static, static)
            elif isinstance(child, (dict, list)):
                walk(child, path, False)

    walk(schema)
    return mappings


def parameter_headers(schema, arguments):
    headers = {}
    for path, name in header_mappings(schema):
        value = arguments
        for property_name in path:
            if not isinstance(value, dict) or property_name not in value:
                value = None
                break
            value = value[property_name]
        if value is None:
            continue
        if isinstance(value, bool):
            value = "true" if value else "false"
        elif isinstance(value, int) and not isinstance(value, bool) and abs(value) <= SAFE_INTEGER:
            value = str(value)
        elif isinstance(value, float) and value.is_integer() and abs(value) <= SAFE_INTEGER:
            value = str(int(value))
        elif not isinstance(value, str):
            raise McpError("invalid_arguments", "MCP parameter cannot be mirrored into a header")
        headers["Mcp-Param-" + name] = encode_header(value)
    return headers
