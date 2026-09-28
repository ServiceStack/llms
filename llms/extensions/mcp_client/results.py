import base64
import binascii
import json

from . import schema
from .common import McpError, bounded


def map_result(response, tool, server, limits):
    bounded(response, limits["maxResponseBytes"])
    if "inputRequests" in response or "requestState" in response:
        raise McpError("unsupported_operation")
    structured = response.get("structuredContent")
    if tool.get("outputSchema") is not None and not response.get("isError"):
        schema.validate(tool["outputSchema"], structured, limits["maxResponseBytes"])
    content, resources = [], []
    blocks = response.get("content", [])
    if not isinstance(blocks, list) or type(response.get("isError", False)) is not bool:
        raise McpError("invalid_response")
    for block in blocks:
        if not isinstance(block, dict):
            raise McpError("invalid_response")
        kind = block.get("type")
        if kind == "text":
            text = block.get("text", "")
            if not isinstance(text, str):
                raise McpError("invalid_response")
            if structured is not None:
                try:
                    if schema.equal(json.loads(text), structured):
                        continue
                except (ValueError, RecursionError):
                    pass
            content.append({"type": "text", "text": text})
        elif kind == "resource_link":
            content.append({"type": "reference", "name": block.get("name"), "uri": block.get("uri")})
        elif kind == "resource" and isinstance(block.get("resource"), dict) and "text" in block["resource"]:
            content.append({"type": "text", "text": block["resource"]["text"], "uri": block["resource"].get("uri")})
        elif kind in ("image", "audio") or (kind == "resource" and "blob" in block.get("resource", {})):
            data = block if kind != "resource" else {**block["resource"], "data": block["resource"]["blob"]}
            add_media(data, content, resources)
        else:
            content.append({"type": "unsupported", "contentType": kind, "message": "Unsupported remote content type"})
    return {
        "source": "mcp_client",
        "serverId": server["id"],
        "tool": tool["remoteName"],
        "isError": response.get("isError", False),
        "structuredContent": structured,
        "content": content,
        "resources": resources,
    }


def add_media(block, content, resources):
    mime = block.get("mimeType", "")
    if mime not in ("image/png", "image/jpeg", "image/gif", "image/webp", "audio/mpeg", "audio/wav", "audio/ogg"):
        content.append({"type": "unsupported", "message": "Unsupported media MIME type"})
        return
    try:
        data = base64.b64decode(block.get("data", ""), validate=True)
    except (ValueError, TypeError, binascii.Error):
        raise McpError("invalid_media") from None
    valid = {
        "image/png": data.startswith(b"\x89PNG\r\n\x1a\n"),
        "image/jpeg": data.startswith(b"\xff\xd8\xff"),
        "image/gif": data.startswith(b"GIF8"),
        "image/webp": data.startswith(b"RIFF") and data[8:12] == b"WEBP",
        "audio/wav": data.startswith(b"RIFF") and data[8:12] == b"WAVE",
        "audio/ogg": data.startswith(b"OggS"),
        "audio/mpeg": data.startswith(b"ID3") or (len(data) >= 2 and data[0] == 255 and data[1] & 0xE0 == 0xE0),
    }[mime]
    if len(data) < 4 or not valid:
        raise McpError("invalid_media", "Media signature does not match MIME type")
    kind = "image_url" if mime.startswith("image/") else "audio_url"
    resources.append({"type": kind, kind: {"url": f"data:{mime};base64," + base64.b64encode(data).decode()}})
    content.append({"type": "media", "mimeType": mime, "resourceIndex": len(resources) - 1})
