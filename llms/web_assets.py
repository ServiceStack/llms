"""Compression and revalidation for the unbundled browser UI."""

import asyncio
import hashlib
import mimetypes

from aiohttp import web


def is_text_asset(content_type):
    return content_type.startswith("text/") or content_type in {
        "application/javascript", "application/json", "application/xml", "image/svg+xml",
    }


@web.middleware
async def compress_responses(request, handler):
    response = await handler(request)
    # Leave streamed completions, SSE, downloads and already compressed responses alone.
    if (
        isinstance(response, web.Response)
        and not response.prepared
        and isinstance(response.body, bytes)
        and len(response.body) >= 1024
        and is_text_asset(response.content_type)
        and "Content-Encoding" not in response.headers
    ):
        response.enable_compression()
        vary = response.headers.get("Vary", "")
        if "accept-encoding" not in vary.lower():
            response.headers["Vary"] = ", ".join(filter(None, [vary, "Accept-Encoding"]))
    return response


async def asset_response(request, resource):
    """Serve filesystem or packaged text assets with an ETag, without stale upgrade caches."""
    content_type = mimetypes.guess_type(str(resource))[0] or "application/octet-stream"
    content = await asyncio.to_thread(resource.read_bytes)
    etag = 'W/"' + hashlib.sha256(content).hexdigest() + '"'
    headers = {"ETag": etag, "Cache-Control": "no-cache", "Vary": "Accept-Encoding"}
    matches = request.headers.get("If-None-Match", "").split(",")
    if any(value.strip() == "*" or value.strip().removeprefix("W/") == etag[2:] for value in matches):
        return web.Response(status=304, headers=headers)
    return web.Response(body=content, content_type=content_type, headers=headers, zlib_executor_size=64 * 1024)
