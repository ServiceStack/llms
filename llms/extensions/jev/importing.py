"""Bounded, unauthenticated downloads of portable recipe JSON."""

import json
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit

import aiohttp
from aiohttp import web

from llms.extensions.publish.client import DEFAULT_BASE_URL, reference_from_url

from .schema import MAX_BYTES, ValidationError, require, validate_recipe
from .storage import InvalidIdentityError, default_filename, recipe_filename


def import_url(config, value):
    require(isinstance(value, str) and len(value) <= 4096, "url", "Enter a recipe JSON URL.")
    value = value.strip()
    try:
        parsed = urlsplit(value)
        require(
            parsed.scheme in ("https", "http") and parsed.hostname and not parsed.username and not parsed.password,
            "url",
            "Enter an HTTP or HTTPS URL without credentials.",
        )
        parsed.port
    except ValueError:
        raise ValidationError("url", "Enter a valid HTTP or HTTPS URL.") from None
    for origin in (config, {"baseUrl": DEFAULT_BASE_URL}):
        try:
            reference = reference_from_url(origin, urlunsplit(parsed._replace(query="", fragment="")))
            return origin.get("baseUrl", DEFAULT_BASE_URL).rstrip("/") + "/d/" + reference + ".json"
        except web.HTTPBadRequest:
            pass
    return value


def download_filename(value, document):
    try:
        return recipe_filename(value)
    except InvalidIdentityError:
        return default_filename(document["name"])


async def download_recipe(url):
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30, connect=5, sock_read=15)) as session:
            for _ in range(6):
                async with session.get(url, headers={"Accept": "application/json"}, allow_redirects=False) as response:
                    if response.status in (301, 302, 303, 307, 308):
                        require(response.headers.get("Location"), "url", "The download redirect has no destination.")
                        url = import_url({}, urljoin(url, response.headers["Location"]))
                        continue
                    require(200 <= response.status < 300, "url", f"Recipe download failed (HTTP {response.status}).")
                    raw = bytearray()
                    async for chunk in response.content.iter_chunked(65536):
                        raw.extend(chunk)
                        require(len(raw) <= MAX_BYTES, "url", "Keep the recipe JSON under 512 KB.")
                    try:
                        value = json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
                    except (ValueError, UnicodeError, RecursionError):
                        raise ValidationError("url", "The URL must return a recipe JSON export.") from None
                    document = validate_recipe(value)
                    disposition = response.content_disposition
                    filename = disposition.filename if disposition else None
                    filename = filename or unquote(urlsplit(url).path.rsplit("/", 1)[-1])
                    return {"document": document, "filename": download_filename(filename, document), "downloadUrl": url}
            raise ValidationError("url", "Too many recipe download redirects.")
    except (aiohttp.ClientError, TimeoutError):
        raise ValidationError("url", "Could not download recipe JSON. Check the URL and try again.") from None
