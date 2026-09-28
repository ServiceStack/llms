"""Bounded JSON Schema subset. Unsupported assertions fail closed, never get dropped.

No remote references, regex execution, recursive schemas, or runtime dependencies.
"""

from decimal import Decimal

from .common import McpError, bounded, dumps

ANNOTATIONS = {
    "x-mcp-header",  # MCP transport metadata, not an argument validation assertion.
    "$schema",
    "$comment",
    "title",
    "description",
    "default",
    "examples",
    "deprecated",
    "readOnly",
    "writeOnly",
    "format",
}
KEYWORDS = ANNOTATIONS | {
    "$ref",
    "$defs",
    "definitions",
    "type",
    "properties",
    "required",
    "additionalProperties",
    "minProperties",
    "maxProperties",
    "items",
    "prefixItems",
    "minItems",
    "maxItems",
    "uniqueItems",
    "contains",
    "minContains",
    "maxContains",
    "minLength",
    "maxLength",
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "multipleOf",
    "enum",
    "const",
    "allOf",
    "anyOf",
    "oneOf",
    "not",
    "if",
    "then",
    "else",
    "dependentRequired",
    "dependentSchemas",
    "propertyNames",
}
TYPES = {"object", "array", "string", "number", "integer", "boolean", "null"}


def pointer(root, ref):
    if not isinstance(ref, str) or not ref.startswith("#/"):
        raise McpError("unsupported_schema", "Only local JSON Pointer references are supported")
    node = root
    try:
        for segment in ref[2:].split("/"):
            segment = segment.replace("~1", "/").replace("~0", "~")
            node = node[int(segment)] if isinstance(node, list) else node[segment]
    except (KeyError, ValueError, IndexError, TypeError):
        raise McpError("unsupported_schema", "Unresolved schema reference") from None
    return node


def check(schema, limit=65536):
    bounded(schema, limit, "schema_limit")
    if not isinstance(schema, dict):
        raise McpError("invalid_schema")
    budget = [8192]

    def visit(s, ancestors=()):
        budget[0] -= 1
        if budget[0] < 0 or id(s) in ancestors or len(ancestors) > 64:
            raise McpError("unsupported_schema", "Recursive or excessive schema references")
        if isinstance(s, bool):
            return
        if not isinstance(s, dict) or set(s) - KEYWORDS:
            raise McpError("unsupported_schema", "Schema contains unsupported keywords")
        path = (*ancestors, id(s))
        for k, v in s.items():
            if k == "$ref":
                visit(pointer(schema, v), path)
            elif k in ("properties", "$defs", "definitions", "dependentSchemas"):
                if not isinstance(v, dict):
                    raise McpError("invalid_schema")
                for child in v.values():
                    visit(child, path)
            elif k in ("items", "additionalProperties", "contains", "not", "if", "then", "else", "propertyNames"):
                visit(v, path)
            elif k in ("allOf", "anyOf", "oneOf", "prefixItems"):
                if not isinstance(v, list) or not v:
                    raise McpError("invalid_schema")
                for child in v:
                    visit(child, path)
            elif k == "type":
                values = v if isinstance(v, list) else [v]
                if not values or any(not isinstance(t, str) or t not in TYPES for t in values):
                    raise McpError("invalid_schema")
            elif k == "required":
                if not isinstance(v, list) or any(not isinstance(x, str) for x in v) or len(set(v)) != len(v):
                    raise McpError("invalid_schema")
            elif k == "dependentRequired":
                if not isinstance(v, dict) or any(
                    not isinstance(x, list) or any(not isinstance(y, str) for y in x) for x in v.values()
                ):
                    raise McpError("invalid_schema")
            elif k.startswith(("min", "max")) and k not in ("minimum", "maximum"):
                if type(v) is not int or v < 0:
                    raise McpError("invalid_schema")
            elif k in ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf"):
                if type(v) not in (int, float) or (k == "multipleOf" and v <= 0):
                    raise McpError("invalid_schema")
            elif k == "uniqueItems" and not isinstance(v, bool) or k == "enum" and (not isinstance(v, list) or not v):
                raise McpError("invalid_schema")

    visit(schema)


def equal(a, b):
    # JSON booleans are not numbers, and object member order is irrelevant.
    if isinstance(a, bool) != isinstance(b, bool):
        return False
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(equal(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(equal(x, y) for x, y in zip(a, b, strict=False))
    return a == b


def validate(schema, value, limit=65536):
    bounded(value, limit, "argument_limit")
    budget = [100000]

    def valid(s, x, depth=0):
        budget[0] -= 1
        if budget[0] < 0 or depth > 64:
            raise McpError("schema_limit")
        if isinstance(s, bool):
            return s

        def test(sub, val=x):
            return valid(sub, val, depth + 1)

        if "$ref" in s and not test(pointer(schema, s["$ref"])):
            return False
        ts = s.get("type", [])
        ts = [ts] if isinstance(ts, str) else ts
        kinds = {
            "null": x is None,
            "boolean": type(x) is bool,
            "object": isinstance(x, dict),
            "array": isinstance(x, list),
            "string": isinstance(x, str),
            "number": type(x) in (int, float),
            "integer": type(x) in (int, float) and x == int(x),
        }
        if ts and not any(kinds[t] for t in ts):
            return False
        if "const" in s and not equal(x, s["const"]):
            return False
        if "enum" in s and not any(equal(x, y) for y in s["enum"]):
            return False
        if "allOf" in s and not all(test(t) for t in s["allOf"]):
            return False
        if "anyOf" in s and not any(test(t) for t in s["anyOf"]):
            return False
        if "oneOf" in s and sum(test(t) for t in s["oneOf"]) != 1:
            return False
        if "not" in s and test(s["not"]):
            return False
        if "if" in s and not test(s.get("then" if test(s["if"]) else "else", True)):
            return False
        if isinstance(x, dict):
            if any(k not in x for k in s.get("required", [])):
                return False
            if not s.get("minProperties", 0) <= len(x) <= s.get("maxProperties", len(x)):
                return False
            for k, val in x.items():
                if not test(s.get("properties", {}).get(k, s.get("additionalProperties", True)), val):
                    return False
                if not test(s.get("propertyNames", True), k):
                    return False
            for k, required in s.get("dependentRequired", {}).items():
                if k in x and any(name not in x for name in required):
                    return False
            for k, sub in s.get("dependentSchemas", {}).items():
                if k in x and not test(sub):
                    return False
        elif isinstance(x, list):
            if not s.get("minItems", 0) <= len(x) <= s.get("maxItems", len(x)):
                return False
            if s.get("uniqueItems"):
                keys = [dumps(canonical(i)) for i in x]
                if len(set(keys)) != len(keys):
                    return False
            prefix = s.get("prefixItems", [])
            for i, val in enumerate(x):
                if not test(prefix[i] if i < len(prefix) else s.get("items", True), val):
                    return False
            if "contains" in s:
                n = sum(test(s["contains"], val) for val in x)
                if not s.get("minContains", 1) <= n <= s.get("maxContains", len(x)):
                    return False
        elif isinstance(x, str):
            if not s.get("minLength", 0) <= len(x) <= s.get("maxLength", len(x)):
                return False
        elif type(x) in (int, float):
            if x < s.get("minimum", x) or x > s.get("maximum", x):
                return False
            if "exclusiveMinimum" in s and x <= s["exclusiveMinimum"]:
                return False
            if "exclusiveMaximum" in s and x >= s["exclusiveMaximum"]:
                return False
            if "multipleOf" in s and Decimal(str(x)) % Decimal(str(s["multipleOf"])):
                return False
        return True

    if not valid(schema, value):
        raise McpError("schema_validation", "Payload does not match the tool schema")


def canonical(x):
    if isinstance(x, dict):
        return {k: canonical(v) for k, v in sorted(x.items())}
    if isinstance(x, list):
        return [canonical(v) for v in x]
    if type(x) is float and x.is_integer():
        return int(x)
    return x
