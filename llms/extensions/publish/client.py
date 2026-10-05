"""Account-isolated publisher configuration and bounded same-origin requests."""

import json
import re
from pathlib import Path
from urllib.parse import urlsplit

import aiohttp
from aiohttp import web

DEFAULT_BASE_URL = "https://ai.llmspy.org"
MAX_PUBLICATION_BYTES = 3 * 1024 * 1024


def get_publish_config(ctx, user=None, obscure=True):
    # A named account never inherits the default account's credentials.
    path = Path(ctx.get_user_path(user)) / "publish/config.json"
    obj = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    public_defaults = {}
    default_path = Path(ctx.get_user_path()) / "publish/config.json"
    if user and default_path != path and default_path.exists():
        defaults = json.loads(default_path.read_text(encoding="utf-8"))
        public_defaults = {key: defaults[key] for key in ("baseUrl", "allowHttp") if key in defaults}
    obj = {**public_defaults, "apiKey": None, "userName": None, "userId": None, **obj}
    obj.setdefault("baseUrl", DEFAULT_BASE_URL)
    obj.setdefault("registerUrl", obj["baseUrl"].rstrip("/") + "/embed/register.html?domain=llmspy.org")
    if obscure and obj.get("apiKey"):
        obj["apiKey"] = obj["apiKey"][:3] + "******" + obj["apiKey"][-4:]
    return obj


def publisher_origin(config):
    base = config.get("baseUrl", DEFAULT_BASE_URL).rstrip("/")
    u = urlsplit(base)
    if (
        u.scheme not in ("https", "http")
        or not u.hostname
        or u.username
        or u.password
        or u.path
        or u.query
        or u.fragment
        or (u.scheme != "https" and not config.get("allowHttp"))
    ):
        raise web.HTTPBadRequest(
            text="Configure a valid HTTPS publisher origin (allowHttp is required for local tests)."
        )
    return base


def public_reference(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", value):
        raise web.HTTPBadRequest(text="Invalid public recipe reference.")
    return value


def reference_from_url(config, value):
    base = urlsplit(publisher_origin(config))
    try:
        u = urlsplit(value)
        same = (u.scheme, u.hostname, u.port) == (base.scheme, base.hostname, base.port)
    except (ValueError, TypeError):
        same = False
    if not same or u.username or u.password or u.query or u.fragment:
        raise web.HTTPBadRequest(text="Paste a recipe link from the configured publisher host.")
    match = re.fullmatch(r"/d/([A-Za-z0-9_-]{1,100})(?:\.json|/recipe\.json)?", u.path)
    if not match:
        raise web.HTTPBadRequest(text="Paste a /d/ recipe page or JSON download link.")
    return match[1]


class PublisherClient:
    def __init__(self, config):
        self.config = config
        self.base = publisher_origin(config)

    async def request(self, method, path, payload=None, authenticated=False):
        headers = {"Accept": "application/json"}
        if authenticated:
            if not self.config.get("apiKey"):
                raise web.HTTPUnauthorized(text="Connect publisher account first.")
            headers["Authorization"] = "Bearer " + self.config["apiKey"]
        data = None
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode()
            if len(data) > MAX_PUBLICATION_BYTES:
                raise web.HTTPRequestEntityTooLarge(max_size=MAX_PUBLICATION_BYTES, actual_size=len(data))
            headers["Content-Type"] = "application/json"
        try:
            async with (
                aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30, connect=5, sock_read=15)) as session,
                session.request(method, self.base + path, data=data, headers=headers, allow_redirects=False) as res,
            ):
                raw = bytearray()
                async for chunk in res.content.iter_chunked(65536):
                    raw.extend(chunk)
                    if len(raw) > MAX_PUBLICATION_BYTES:
                        raise web.HTTPBadGateway(text="Publisher response exceeds the size limit.")
                if res.status in (401, 403):
                    raise web.HTTPUnauthorized(text="Reconnect publisher account.")
                if res.status == 404:
                    raise web.HTTPNotFound(text="Sharing is unavailable on this publisher, or the recipe was removed.")
                if res.status == 429:
                    raise web.HTTPTooManyRequests(text="Publisher is rate limiting requests. Wait a moment and retry.")
                if res.status == 413:
                    raise web.HTTPRequestEntityTooLarge(
                        max_size=MAX_PUBLICATION_BYTES,
                        actual_size=len(data or b""),
                        text="Run a smaller worked example before sharing.",
                    )
                if res.status == 409:
                    raise web.HTTPConflict(text="The public recipe changed. Refresh and review before trying again.")
                if not 200 <= res.status < 300:
                    raise web.HTTPBadGateway(text=f"Publisher request failed (HTTP {res.status}). Try again later.")
                if res.content_type != "application/json":
                    raise web.HTTPBadGateway(text="Publisher returned a non-JSON response.")
                try:
                    result = json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
                    json.dumps(result, allow_nan=False)
                    return result
                except (ValueError, RecursionError, UnicodeError):
                    raise web.HTTPBadGateway(text="Publisher returned invalid JSON.") from None
        except (aiohttp.ClientError, TimeoutError):
            raise web.HTTPBadGateway(text="Could not reach publisher. Retry to recover the same publication.") from None
