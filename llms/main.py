#!/usr/bin/env python

# Copyright (c) Demis Bellot, ServiceStack <https://servicestack.net>
# License: https://github.com/ServiceStack/llms/blob/main/LICENSE

# A lightweight CLI tool and OpenAI-compatible server for querying multiple Large Language Model (LLM) providers.
# Docs: https://github.com/ServiceStack/llms

import argparse
import asyncio
import base64
import contextlib
import hashlib
import importlib.util
import inspect
import json
import mimetypes
import os
import re
import shlex
import shutil
import site
import subprocess
import sys
import tempfile
import time
import traceback
from datetime import datetime
from enum import Enum, IntEnum
from importlib import resources  # Py≥3.9  (pip install importlib_resources for 3.7/3.8)
from io import BytesIO
from pathlib import Path
from typing import (
    Annotated,
    Any,
    Callable,
    Dict,
    List,
    Literal,
    Optional,
    Tuple,
    Union,
    get_args,
    get_origin,
    get_type_hints,
)
from urllib.parse import parse_qs, urljoin

import aiohttp
from aiohttp import web

try:
    from PIL import Image

    HAS_PIL = True
except ImportError:
    HAS_PIL = False

_ROOT = None
VERSION = "3.0.25"
DEBUG = os.getenv("DEBUG") == "1"
MOCK = os.getenv("MOCK") == "1"
MOCK_DIR = os.getenv("MOCK_DIR")
DISABLE_EXTENSIONS = (os.getenv("LLMS_DISABLE") or "").split(",")
g_config_path = None
g_config = None
g_providers = None
g_handlers = {}
g_verbose = False
g_logprefix = ""
g_default_model = ""
g_app = None  # ExtensionsContext Singleton


class ExitCode(IntEnum):
    SUCCESS = 0
    FAILED = 1
    UNHANDLED = 9


def _log(message):
    if g_verbose:
        print(f"{g_logprefix}{message}", flush=True)


def _dbg(message):
    if DEBUG:
        print(f"DEBUG: {message}", flush=True)


def _err(message, e):
    print(f"ERROR: {message}: {e}", flush=True)
    if g_verbose:
        print(traceback.format_exc(), flush=True)


def printdump(obj):
    args = obj.__dict__ if hasattr(obj, "__dict__") else obj
    print(json.dumps(args, indent=2))


def print_chat(chat):
    _log(f"Chat: {chat_summary(chat)}")


def chat_summary(chat):
    """Summarize chat completion request for logging."""
    # replace image_url.url with <image>
    clone = json.loads(json.dumps(chat))
    for message in clone["messages"]:
        if "content" in message and isinstance(message["content"], list):
            for item in message["content"]:
                if "image_url" in item:
                    if "url" in item["image_url"]:
                        url = item["image_url"]["url"]
                        prefix = url.split(",", 1)[0]
                        item["image_url"]["url"] = prefix + f",({len(url) - len(prefix)})"
                elif "input_audio" in item:
                    if "data" in item["input_audio"]:
                        data = item["input_audio"]["data"]
                        item["input_audio"]["data"] = f"({len(data)})"
                elif "file" in item and "file_data" in item["file"]:
                    data = item["file"]["file_data"]
                    prefix = data.split(",", 1)[0]
                    item["file"]["file_data"] = prefix + f",({len(data) - len(prefix)})"
    return json.dumps(clone, indent=2)


image_exts = ["png", "webp", "jpg", "jpeg", "gif", "bmp", "svg", "tiff", "ico"]
audio_exts = ["mp3", "wav", "ogg", "flac", "m4a", "opus", "webm"]


def is_file_path(path):
    # macOs max path is 1023
    return path and len(path) < 1024 and os.path.exists(path)


def is_url(url):
    return url and (url.startswith("http://") or url.startswith("https://"))


def get_filename(file):
    return file.rsplit("/", 1)[1] if "/" in file else "file"


def parse_args_params(args_str):
    """Parse URL-encoded parameters and return a dictionary."""
    if not args_str:
        return {}

    # Parse the URL-encoded string
    parsed = parse_qs(args_str, keep_blank_values=True)

    # Convert to simple dict with single values (not lists)
    result = {}
    for key, values in parsed.items():
        if len(values) == 1:
            value = values[0]
            # Try to convert to appropriate types
            if value.lower() == "true":
                result[key] = True
            elif value.lower() == "false":
                result[key] = False
            elif value.isdigit():
                result[key] = int(value)
            else:
                try:
                    # Try to parse as float
                    result[key] = float(value)
                except ValueError:
                    # Keep as string
                    result[key] = value
        else:
            # Multiple values, keep as list
            result[key] = values

    return result


def apply_args_to_chat(chat, args_params):
    """Apply parsed arguments to the chat request."""
    if not args_params:
        return chat

    # Apply each parameter to the chat request
    for key, value in args_params.items():
        if isinstance(value, str):
            if key == "stop":
                if "," in value:
                    value = value.split(",")
            elif (
                key == "max_completion_tokens"
                or key == "max_tokens"
                or key == "n"
                or key == "seed"
                or key == "top_logprobs"
            ):
                value = int(value)
            elif key == "temperature" or key == "top_p" or key == "frequency_penalty" or key == "presence_penalty":
                value = float(value)
            elif (
                key == "store"
                or key == "logprobs"
                or key == "enable_thinking"
                or key == "parallel_tool_calls"
                or key == "stream"
            ):
                value = bool(value)
        chat[key] = value

    return chat


def is_base_64(data):
    try:
        base64.b64decode(data)
        return True
    except Exception:
        return False


def id_to_name(id):
    return id.replace("-", " ").title()


def pluralize(word, count):
    if count == 1:
        return word
    return word + "s"


def get_file_mime_type(filename):
    mimetype, _ = mimetypes.guess_type(filename)
    return mimetype or "application/octet-stream"


def price_to_string(price: float | int | str | None) -> str | None:
    """Convert numeric price to string without scientific notation.

    Detects and rounds up numbers with recurring 9s (e.g., 0.00014999999999999999)
    to avoid floating-point precision artifacts.
    """
    if price is None or price == 0 or price == "0":
        return "0"
    try:
        price_float = float(price)
        # Format with enough decimal places to avoid scientific notation
        formatted = format(price_float, ".20f")

        # Detect recurring 9s pattern (e.g., "...9999999")
        # If we have 4 or more consecutive 9s, round up
        if "9999" in formatted:
            # Round up by adding a small amount and reformatting
            # Find the position of the 9s to determine precision
            import decimal

            decimal.getcontext().prec = 28
            d = decimal.Decimal(str(price_float))
            # Round to one less decimal place than where the 9s start
            nines_pos = formatted.find("9999")
            if nines_pos > 0:
                # Round up at the position before the 9s
                decimal_places = nines_pos - formatted.find(".") - 1
                if decimal_places > 0:
                    quantize_str = "0." + "0" * (decimal_places - 1) + "1"
                    d = d.quantize(decimal.Decimal(quantize_str), rounding=decimal.ROUND_UP)
                    result = str(d)
                    # Remove trailing zeros
                    if "." in result:
                        result = result.rstrip("0").rstrip(".")
                    return result

        # Normal case: strip trailing zeros
        return formatted.rstrip("0").rstrip(".")
    except (ValueError, TypeError):
        return None


def convert_image_if_needed(image_bytes, mimetype="image/png"):
    """
    Convert and resize image to WebP if it exceeds configured limits.

    Args:
        image_bytes: Raw image bytes
        mimetype: Original image MIME type

    Returns:
        tuple: (converted_bytes, new_mimetype) or (original_bytes, original_mimetype) if no conversion needed
    """
    if not HAS_PIL:
        return image_bytes, mimetype

    # Get conversion config
    convert_config = g_config.get("convert", {}).get("image", {}) if g_config else {}
    if not convert_config:
        return image_bytes, mimetype

    max_size_str = convert_config.get("max_size", "1536x1024")
    max_length = convert_config.get("max_length", 1.5 * 1024 * 1024)  # 1.5MB

    try:
        # Parse max_size (e.g., "1536x1024")
        max_width, max_height = map(int, max_size_str.split("x"))

        # Open image
        with Image.open(BytesIO(image_bytes)) as img:
            original_width, original_height = img.size

            # Check if image exceeds limits
            needs_resize = original_width > max_width or original_height > max_height

            # Check if base64 length would exceed max_length (in KB)
            # Base64 encoding increases size by ~33%, so check raw bytes * 1.33 / 1024
            estimated_kb = (len(image_bytes) * 1.33) / 1024
            needs_conversion = estimated_kb > max_length

            if not needs_resize and not needs_conversion:
                return image_bytes, mimetype

            # Convert RGBA to RGB if necessary (WebP doesn't support transparency in RGB mode)
            if img.mode in ("RGBA", "LA", "P"):
                # Create a white background
                background = Image.new("RGB", img.size, (255, 255, 255))
                if img.mode == "P":
                    img = img.convert("RGBA")
                background.paste(img, mask=img.split()[-1] if img.mode in ("RGBA", "LA") else None)
                img = background
            elif img.mode != "RGB":
                img = img.convert("RGB")

            # Resize if needed (preserve aspect ratio)
            if needs_resize:
                img.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)
                _log(f"Resized image from {original_width}x{original_height} to {img.size[0]}x{img.size[1]}")

            # Convert to WebP
            output = BytesIO()
            img.save(output, format="WEBP", quality=85, method=6)
            converted_bytes = output.getvalue()

            _log(
                f"Converted image to WebP: {len(image_bytes)} bytes -> {len(converted_bytes)} bytes ({len(converted_bytes) * 100 // len(image_bytes)}%)"
            )

            return converted_bytes, "image/webp"

    except Exception as e:
        _log(f"Error converting image: {e}")
        # Return original if conversion fails
        return image_bytes, mimetype


def to_content(result):
    if isinstance(result, (str, int, float, bool)):
        return str(result)
    elif isinstance(result, (list, set, tuple, dict)):
        return json.dumps(result)
    else:
        return str(result)


def get_literal_values(typ):
    """Recursively extract values from Literal and Union types."""
    origin = get_origin(typ)
    if origin is Literal:
        return list(get_args(typ))
    elif origin is Union:
        values = []
        for arg in get_args(typ):
            # Recurse for nested Unions or Literals
            nested_values = get_literal_values(arg)
            if nested_values:
                for v in nested_values:
                    if v not in values:
                        values.append(v)
        return values
    return None


def _py_type_to_json_type(param_type):
    param_type_name = "string"
    enum_values = None
    items = None

    # Check for Enum
    if inspect.isclass(param_type) and issubclass(param_type, Enum):
        enum_values = [e.value for e in param_type]
    elif get_origin(param_type) is list or get_origin(param_type) is list:
        param_type_name = "array"
        args = get_args(param_type)
        if args:
            items_type, _, _ = _py_type_to_json_type(args[0])
            items = {"type": items_type}
    elif get_origin(param_type) is dict:
        param_type_name = "object"
    else:
        # Check for Literal / Union[Literal]
        enum_values = get_literal_values(param_type)

    if enum_values:
        # Infer type from the first value
        value_type = type(enum_values[0])
        if value_type is int:
            param_type_name = "integer"
        elif value_type is float:
            param_type_name = "number"
        elif value_type is bool:
            param_type_name = "boolean"

    elif param_type is int:
        param_type_name = "integer"
    elif param_type is float:
        param_type_name = "number"
    elif param_type is bool:
        param_type_name = "boolean"

    return param_type_name, enum_values, items


def function_to_tool_definition(func):
    type_hints = get_type_hints(func, include_extras=True)
    signature = inspect.signature(func)
    parameters = {"type": "object", "properties": {}, "required": []}

    for name, param in signature.parameters.items():
        param_type = type_hints.get(name, str)
        description = None

        # Check for Annotated (for description)
        if get_origin(param_type) is Annotated:
            args = get_args(param_type)
            param_type = args[0]
            for arg in args[1:]:
                if isinstance(arg, str):
                    description = arg
                    break

        # Unwrap Optional / Union[T, None]
        origin = get_origin(param_type)
        if origin is Union:
            args = get_args(param_type)
            # Filter out NoneType
            non_none_args = [arg for arg in args if arg is not type(None)]
            if len(non_none_args) == 1:
                param_type = non_none_args[0]

        param_type_name, enum_values, items = _py_type_to_json_type(param_type)

        prop = {"type": param_type_name}
        if description:
            prop["description"] = description
        if enum_values:
            prop["enum"] = enum_values
        if items:
            prop["items"] = items
        parameters["properties"][name] = prop

        if param.default == inspect.Parameter.empty:
            parameters["required"].append(name)

    return {
        "type": "function",
        "function": {
            "name": func.__name__,
            "description": (func.__doc__ or "").strip(),
            "parameters": parameters,
        },
    }


async def download_file(url):
    async with aiohttp.ClientSession() as session:
        return await session_download_file(session, url)


async def session_download_file(session, url, default_mimetype="application/octet-stream"):
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=120)) as response:
            response.raise_for_status()
            content = await response.read()
            mimetype = response.headers.get("Content-Type")
            disposition = response.headers.get("Content-Disposition")
            if mimetype and ";" in mimetype:
                mimetype = mimetype.split(";")[0]
            ext = None
            if disposition:
                start = disposition.index('filename="') + len('filename="')
                end = disposition.index('"', start)
                filename = disposition[start:end]
                if not mimetype:
                    mimetype = mimetypes.guess_type(filename)[0] or default_mimetype
            else:
                filename = url.split("/")[-1]
                if "." not in filename:
                    if mimetype is None:
                        mimetype = default_mimetype
                    ext = mimetypes.guess_extension(mimetype) or mimetype.split("/")[1]
                    filename = f"{filename}.{ext}"

            if not ext:
                ext = Path(filename).suffix.lstrip(".")

            info = {
                "url": url,
                "type": mimetype,
                "name": filename,
                "ext": ext,
            }
            return content, info
    except Exception as e:
        _err(f"Error downloading file: {url}", e)
        raise e


def read_binary_file(url):
    try:
        path = Path(url)
        with open(url, "rb") as f:
            content = f.read()
            info_path = path.stem + ".info.json"
            if os.path.exists(info_path):
                with open(info_path) as f_info:
                    info = json.load(f_info)
                    return content, info

            stat = path.stat()
            info = {
                "date": int(stat.st_mtime),
                "name": path.name,
                "ext": path.suffix.lstrip("."),
                "type": mimetypes.guess_type(path.name)[0],
                "url": f"/~cache/{path.name[:2]}/{path.name}",
            }
            return content, info
    except Exception as e:
        _err(f"Error reading file: {url}", e)
        raise e


async def process_chat(chat, provider_id=None):
    if not chat:
        raise Exception("No chat provided")
    if "stream" not in chat:
        chat["stream"] = False
    # Some providers don't support empty tools
    if "tools" in chat and (chat["tools"] is None or len(chat["tools"]) == 0):
        del chat["tools"]
    if "messages" not in chat:
        return chat

    async with aiohttp.ClientSession() as session:
        for message in chat["messages"]:
            if "content" not in message:
                continue

            if isinstance(message["content"], list):
                for item in message["content"]:
                    if "type" not in item:
                        continue
                    if item["type"] == "image_url" and "image_url" in item:
                        image_url = item["image_url"]
                        if "url" in image_url:
                            url = image_url["url"]
                            if url.startswith("/~cache/"):
                                url = get_cache_path(url[8:])
                            if is_url(url):
                                _log(f"Downloading image: {url}")
                                content, info = await session_download_file(session, url, default_mimetype="image/png")
                                mimetype = info["type"]
                                # convert/resize image if needed
                                content, mimetype = convert_image_if_needed(content, mimetype)
                                # convert to data uri
                                image_url["url"] = f"data:{mimetype};base64,{base64.b64encode(content).decode('utf-8')}"
                            elif is_file_path(url):
                                _log(f"Reading image: {url}")
                                content, info = read_binary_file(url)
                                mimetype = info["type"]
                                # convert/resize image if needed
                                content, mimetype = convert_image_if_needed(content, mimetype)
                                # convert to data uri
                                image_url["url"] = f"data:{mimetype};base64,{base64.b64encode(content).decode('utf-8')}"
                            elif url.startswith("data:"):
                                # Extract existing data URI and process it
                                if ";base64," in url:
                                    prefix = url.split(";base64,")[0]
                                    mimetype = prefix.split(":")[1] if ":" in prefix else "image/png"
                                    base64_data = url.split(";base64,")[1]
                                    content = base64.b64decode(base64_data)
                                    # convert/resize image if needed
                                    content, mimetype = convert_image_if_needed(content, mimetype)
                                    # update data uri with potentially converted image
                                    image_url["url"] = (
                                        f"data:{mimetype};base64,{base64.b64encode(content).decode('utf-8')}"
                                    )
                            else:
                                raise Exception(f"Invalid image: {url}")
                    elif item["type"] == "input_audio" and "input_audio" in item:
                        input_audio = item["input_audio"]
                        if "data" in input_audio:
                            url = input_audio["data"]
                            if url.startswith("/~cache/"):
                                url = get_cache_path(url[8:])
                            if is_url(url):
                                _log(f"Downloading audio: {url}")
                                content, info = await session_download_file(session, url, default_mimetype="audio/mp3")
                                mimetype = info["type"]
                                # convert to base64
                                input_audio["data"] = base64.b64encode(content).decode("utf-8")
                                if provider_id == "alibaba":
                                    input_audio["data"] = f"data:{mimetype};base64,{input_audio['data']}"
                                input_audio["format"] = mimetype.rsplit("/", 1)[1]
                            elif is_file_path(url):
                                _log(f"Reading audio: {url}")
                                content, info = read_binary_file(url)
                                mimetype = info["type"]
                                # convert to base64
                                input_audio["data"] = base64.b64encode(content).decode("utf-8")
                                if provider_id == "alibaba":
                                    input_audio["data"] = f"data:{mimetype};base64,{input_audio['data']}"
                                input_audio["format"] = mimetype.rsplit("/", 1)[1]
                            elif is_base_64(url):
                                pass  # use base64 data as-is
                            else:
                                raise Exception(f"Invalid audio: {url}")
                    elif item["type"] == "file" and "file" in item:
                        file = item["file"]
                        if "file_data" in file:
                            url = file["file_data"]
                            if url.startswith("/~cache/"):
                                url = get_cache_path(url[8:])
                            if is_url(url):
                                _log(f"Downloading file: {url}")
                                content, info = await session_download_file(
                                    session, url, default_mimetype="application/pdf"
                                )
                                mimetype = info["type"]
                                file["filename"] = info["name"]
                                file["file_data"] = (
                                    f"data:{mimetype};base64,{base64.b64encode(content).decode('utf-8')}"
                                )
                            elif is_file_path(url):
                                _log(f"Reading file: {url}")
                                content, info = read_binary_file(url)
                                mimetype = info["type"]
                                file["filename"] = info["name"]
                                file["file_data"] = (
                                    f"data:{mimetype};base64,{base64.b64encode(content).decode('utf-8')}"
                                )
                            elif url.startswith("data:"):
                                if "filename" not in file:
                                    file["filename"] = "file"
                                pass  # use base64 data as-is
                            else:
                                raise Exception(f"Invalid file: {url}")
    return chat


def image_ext_from_mimetype(mimetype, default="png"):
    if "/" in mimetype:
        _ext = mimetypes.guess_extension(mimetype)
        if _ext:
            return _ext.lstrip(".")
    return default


def audio_ext_from_format(format, default="mp3"):
    if format == "mpeg":
        return "mp3"
    return format or default


def file_ext_from_mimetype(mimetype, default="pdf"):
    if "/" in mimetype:
        _ext = mimetypes.guess_extension(mimetype)
        if _ext:
            return _ext.lstrip(".")
    return default


def cache_message_inline_data(m):
    """
    Replaces and caches any inline data URIs in the message content.
    """
    if "content" not in m:
        return

    content = m["content"]
    if isinstance(content, list):
        for item in content:
            if item.get("type") == "image_url":
                image_url = item.get("image_url", {})
                url = image_url.get("url")
                if url and url.startswith("data:"):
                    # Extract base64 and mimetype
                    try:
                        header, base64_data = url.split(";base64,")
                        # header is like "data:image/png"
                        ext = image_ext_from_mimetype(header.split(":")[1])
                        filename = f"image.{ext}"  # Hash will handle uniqueness

                        cache_url, _ = save_image_to_cache(base64_data, filename, {}, ignore_info=True)
                        image_url["url"] = cache_url
                    except Exception as e:
                        _log(f"Error caching inline image: {e}")

            elif item.get("type") == "input_audio":
                input_audio = item.get("input_audio", {})
                data = input_audio.get("data")
                if data:
                    # Handle data URI or raw base64
                    base64_data = data
                    if data.startswith("data:"):
                        with contextlib.suppress(ValueError):
                            header, base64_data = data.split(";base64,")

                    fmt = audio_ext_from_format(input_audio.get("format"))
                    filename = f"audio.{fmt}"

                    try:
                        cache_url, _ = save_bytes_to_cache(base64_data, filename, {}, ignore_info=True)
                        input_audio["data"] = cache_url
                    except Exception as e:
                        _log(f"Error caching inline audio: {e}")

            elif item.get("type") == "file":
                file_info = item.get("file", {})
                file_data = file_info.get("file_data")
                if file_data and file_data.startswith("data:"):
                    try:
                        header, base64_data = file_data.split(";base64,")
                        mimetype = header.split(":")[1]
                        # Try to get extension from filename if available, else mimetype
                        filename = file_info.get("filename", "file")
                        if "." not in filename:
                            ext = file_ext_from_mimetype(mimetype)
                            filename = f"{filename}.{ext}"

                        cache_url, info = save_bytes_to_cache(base64_data, filename)
                        file_info["file_data"] = cache_url
                        file_info["filename"] = info["name"]
                    except Exception as e:
                        _log(f"Error caching inline file: {e}")


class HTTPError(Exception):
    def __init__(self, status, reason, body, headers=None):
        self.status = status
        self.reason = reason
        self.body = body
        self.headers = headers
        super().__init__(f"HTTP {status} {reason}")


def save_bytes_to_cache(base64_data, filename, file_info=None, ignore_info=False):
    ext = filename.split(".")[-1]
    mimetype = get_file_mime_type(filename)
    content = base64.b64decode(base64_data) if isinstance(base64_data, str) else base64_data
    sha256_hash = hashlib.sha256(content).hexdigest()

    save_filename = f"{sha256_hash}.{ext}" if ext else sha256_hash

    # Use first 2 chars for subdir to avoid too many files in one dir
    subdir = sha256_hash[:2]
    relative_path = f"{subdir}/{save_filename}"
    full_path = get_cache_path(relative_path)
    url = f"/~cache/{relative_path}"

    # if file and its .info.json already exists, return it
    info_path = os.path.splitext(full_path)[0] + ".info.json"
    if os.path.exists(full_path) and os.path.exists(info_path):
        _dbg(f"Cached bytes exists: {relative_path}")
        if ignore_info:
            return url, None
        return url, json_from_file(info_path)

    os.makedirs(os.path.dirname(full_path), exist_ok=True)

    with open(full_path, "wb") as f:
        f.write(content)
    info = {
        "date": int(time.time()),
        "url": url,
        "size": len(content),
        "type": mimetype,
        "name": filename,
    }
    if file_info:
        info.update(file_info)

    # Save metadata
    info_path = os.path.splitext(full_path)[0] + ".info.json"
    with open(info_path, "w") as f:
        json.dump(info, f)

    _dbg(f"Saved cached bytes and info: {relative_path}")

    g_app.on_cache_saved_filters({"url": url, "info": info})

    return url, info


def save_audio_to_cache(base64_data, filename, audio_info, ignore_info=False):
    return save_bytes_to_cache(base64_data, filename, audio_info, ignore_info)


def save_video_to_cache(base64_data, filename, file_info, ignore_info=False):
    return save_bytes_to_cache(base64_data, filename, file_info, ignore_info)


def save_image_to_cache(base64_data, filename, image_info, ignore_info=False):
    ext = filename.split(".")[-1]
    mimetype = get_file_mime_type(filename)
    content = base64.b64decode(base64_data) if isinstance(base64_data, str) else base64_data
    sha256_hash = hashlib.sha256(content).hexdigest()

    save_filename = f"{sha256_hash}.{ext}" if ext else sha256_hash

    # Use first 2 chars for subdir to avoid too many files in one dir
    subdir = sha256_hash[:2]
    relative_path = f"{subdir}/{save_filename}"
    full_path = get_cache_path(relative_path)
    url = f"/~cache/{relative_path}"

    # if file and its .info.json already exists, return it
    info_path = os.path.splitext(full_path)[0] + ".info.json"
    if os.path.exists(full_path) and os.path.exists(info_path):
        _dbg(f"Saved image exists: {relative_path}")
        if ignore_info:
            return url, None
        return url, json_from_file(info_path)

    os.makedirs(os.path.dirname(full_path), exist_ok=True)

    with open(full_path, "wb") as f:
        f.write(content)
    info = {
        "date": int(time.time()),
        "url": url,
        "size": len(content),
        "type": mimetype,
        "name": filename,
    }
    info.update(image_info)

    # If image, get dimensions
    if HAS_PIL and mimetype.startswith("image/"):
        try:
            with Image.open(BytesIO(content)) as img:
                info["width"] = img.width
                info["height"] = img.height
        except Exception:
            pass

    if "width" in info and "height" in info:
        _log(f"Saved image to cache: {full_path} ({len(content)} bytes) {info['width']}x{info['height']}")
    else:
        _log(f"Saved image to cache: {full_path} ({len(content)} bytes)")

    # Save metadata
    info_path = os.path.splitext(full_path)[0] + ".info.json"
    with open(info_path, "w") as f:
        json.dump(info, f)

    _dbg(f"Saved image and info: {relative_path}")

    g_app.on_cache_saved_filters({"url": url, "info": info})

    return url, info


def http_error_to_message(response, text):
    if response.status >= 400:
        message = "HTTP " + str(response.status) + " " + response.reason
        _dbg(f"HTTP {response.status} {response.reason}\n{dict(response.headers)}\n{text}")
        try:
            body = json.loads(text)
            if "message" in body:
                message = body["message"]
            elif "error" in body:
                message = body["error"]
        except Exception:
            if text:
                message += ": " + text[:100]
        return message


async def response_json(response):
    text = await response.text()
    if response.status >= 400:
        message = http_error_to_message(response, text)
        raise Exception(message)
    response.raise_for_status()
    body = json.loads(text)
    return body


def chat_to_prompt(chat):
    prompt = ""
    if "messages" in chat:
        for message in chat["messages"]:
            if message.get("role") == "user":
                # if content is string
                if isinstance(message["content"], str):
                    if prompt:
                        prompt += "\n"
                    prompt += message["content"]
                elif isinstance(message["content"], list):
                    # if content is array of objects
                    for part in message["content"]:
                        if part["type"] == "text":
                            if prompt:
                                prompt += "\n"
                            prompt += part["text"]
    return prompt


def chat_to_system_prompt(chat):
    if "messages" in chat:
        for message in chat["messages"]:
            if message.get("role") == "system":
                # if content is string
                if isinstance(message["content"], str):
                    return message["content"]
                elif isinstance(message["content"], list):
                    # if content is array of objects
                    for part in message["content"]:
                        if part["type"] == "text":
                            return part["text"]
    return None


def chat_to_username(chat):
    if "metadata" in chat and "user" in chat["metadata"]:
        return chat["metadata"]["user"]
    return None


def chat_to_aspect_ratio(chat):
    if "image_config" in chat and "aspect_ratio" in chat["image_config"]:
        return chat["image_config"]["aspect_ratio"]
    return None


def last_user_prompt(chat):
    prompt = ""
    if "messages" in chat:
        for message in chat["messages"]:
            if message.get("role") == "user":
                # if content is string
                if isinstance(message["content"], str):
                    prompt = message["content"]
                elif isinstance(message["content"], list):
                    # if content is array of objects
                    for part in message["content"]:
                        if part["type"] == "text":
                            prompt = part["text"]
    return prompt


def chat_response_to_message(openai_response):
    """
    Returns an assistant message from the OpenAI Response.
    Handles normalizing text, image, and audio responses into the message content.
    """
    timestamp = int(time.time() * 1000)  # openai_response.get("created")
    choices = openai_response
    if isinstance(openai_response, dict) and "choices" in openai_response:
        choices = openai_response["choices"]

    choice = choices[0] if isinstance(choices, list) and choices else choices

    if isinstance(choice, str):
        return {"role": "assistant", "content": choice, "timestamp": timestamp}

    if isinstance(choice, dict):
        message = choice.get("message", choice)
    else:
        return {"role": "assistant", "content": str(choice), "timestamp": timestamp}

    # Ensure message is a dict
    if not isinstance(message, dict):
        return {"role": "assistant", "content": message, "timestamp": timestamp}

    message.update({"timestamp": timestamp})
    return message


def to_file_info(chat, info=None, response=None):
    prompt = last_user_prompt(chat)
    ret = info or {}
    if chat["model"] and "model" not in ret:
        ret["model"] = chat["model"]
    if prompt and "prompt" not in ret:
        ret["prompt"] = prompt
    if "image_config" in chat:
        ret.update(chat["image_config"])
    user = chat_to_username(chat)
    if user:
        ret["user"] = user
    return ret


# Image Generator Providers
class GeneratorBase:
    def __init__(self, **kwargs):
        self.id = kwargs.get("id")
        self.api = kwargs.get("api")
        self.api_key = kwargs.get("api_key")
        self.headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        self.chat_url = f"{self.api}/chat/completions"
        self.default_content = "I've generated the image for you."

    def validate(self, **kwargs):
        if not self.api_key:
            api_keys = ", ".join(self.env)
            return f"Provider '{self.name}' requires API Key {api_keys}"
        return None

    def test(self, **kwargs):
        error_msg = self.validate(**kwargs)
        if error_msg:
            _log(error_msg)
            return False
        return True

    async def load(self):
        pass

    def gen_summary(self, gen):
        """Summarize gen response for logging."""
        clone = json.loads(json.dumps(gen))
        return json.dumps(clone, indent=2)

    def chat_summary(self, chat):
        return chat_summary(chat)

    def process_chat(self, chat, provider_id=None):
        return process_chat(chat, provider_id)

    async def response_json(self, response):
        return await response_json(response)

    def get_headers(self, provider, chat):
        headers = self.headers.copy()
        if provider is not None:
            headers["Authorization"] = f"Bearer {provider.api_key}"
        elif self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def to_response(self, response, chat, started_at):
        raise NotImplementedError

    async def chat(self, chat, provider=None, context=None):
        return {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "Not Implemented",
                        "images": [
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": "data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIyNCIgaGVpZ2h0PSIyNCIgdmlld0JveD0iMCAwIDI0IDI0Ij48cGF0aCBmaWxsPSJjdXJyZW50Q29sb3IiIGQ9Ik0xMiAyMGE4IDggMCAxIDAgMC0xNmE4IDggMCAwIDAgMCAxNm0wIDJDNi40NzcgMjIgMiAxNy41MjMgMiAxMlM2LjQ3NyAyIDEyIDJzMTAgNC40NzcgMTAgMTBzLTQuNDc3IDEwLTEwIDEwbS0xLTZoMnYyaC0yem0wLTEwaDJ2OGgtMnoiLz48L3N2Zz4=",
                                },
                            }
                        ],
                    }
                }
            ]
        }


# OpenAI Providers


class OpenAiCompatible:
    sdk = "@ai-sdk/openai-compatible"

    def __init__(self, **kwargs):
        required_args = ["id", "api"]
        for arg in required_args:
            if arg not in kwargs:
                raise ValueError(f"Missing required argument: {arg}")

        self.id = kwargs.get("id")
        self.api = kwargs.get("api").strip("/")
        self.env = kwargs.get("env", [])
        self.api_key = kwargs.get("api_key")
        self.name = kwargs.get("name", id_to_name(self.id))
        self.set_models(**kwargs)

        self.chat_url = f"{self.api}/chat/completions"

        self.headers = kwargs.get("headers", {"Content-Type": "application/json"})
        if self.api_key is not None:
            self.headers["Authorization"] = f"Bearer {self.api_key}"

        self.frequency_penalty = float(kwargs["frequency_penalty"]) if "frequency_penalty" in kwargs else None
        self.max_completion_tokens = int(kwargs["max_completion_tokens"]) if "max_completion_tokens" in kwargs else None
        self.n = int(kwargs["n"]) if "n" in kwargs else None
        self.parallel_tool_calls = bool(kwargs["parallel_tool_calls"]) if "parallel_tool_calls" in kwargs else None
        self.presence_penalty = float(kwargs["presence_penalty"]) if "presence_penalty" in kwargs else None
        self.prompt_cache_key = kwargs.get("prompt_cache_key")
        self.reasoning_effort = kwargs.get("reasoning_effort")
        self.safety_identifier = kwargs.get("safety_identifier")
        self.seed = int(kwargs["seed"]) if "seed" in kwargs else None
        self.service_tier = kwargs.get("service_tier")
        self.stop = kwargs.get("stop")
        self.store = bool(kwargs["store"]) if "store" in kwargs else None
        self.temperature = float(kwargs["temperature"]) if "temperature" in kwargs else None
        self.top_logprobs = int(kwargs["top_logprobs"]) if "top_logprobs" in kwargs else None
        self.top_p = float(kwargs["top_p"]) if "top_p" in kwargs else None
        self.verbosity = kwargs.get("verbosity")
        self.stream = bool(kwargs["stream"]) if "stream" in kwargs else None
        self.enable_thinking = bool(kwargs["enable_thinking"]) if "enable_thinking" in kwargs else None
        self.check = kwargs.get("check")
        self.modalities = kwargs.get("modalities", {})

    def set_models(self, **kwargs):
        models = kwargs.get("models", {})
        self.map_models = kwargs.get("map_models", {})
        # if 'map_models' is provided, only include models in `map_models[model_id] = provider_model_id`
        if self.map_models:
            self.models = {}
            for provider_model_id in self.map_models.values():
                if provider_model_id in models:
                    self.models[provider_model_id] = models[provider_model_id]
        else:
            self.models = models

        include_models = kwargs.get("include_models")  # string regex pattern
        # only include models that match the regex pattern
        if include_models:
            _log(f"Filtering {len(self.models)} models, only including models that match regex: {include_models}")
            self.models = {k: v for k, v in self.models.items() if re.search(include_models, k)}

        exclude_models = kwargs.get("exclude_models")  # string regex pattern
        # exclude models that match the regex pattern
        if exclude_models:
            _log(f"Filtering {len(self.models)} models, excluding models that match regex: {exclude_models}")
            self.models = {k: v for k, v in self.models.items() if not re.search(exclude_models, k)}

    def validate(self, **kwargs):
        if not self.api_key:
            api_keys = ", ".join(self.env)
            return f"Provider '{self.name}' requires API Key {api_keys}"
        return None

    def test(self, **kwargs):
        error_msg = self.validate(**kwargs)
        if error_msg:
            _log(error_msg)
            return False
        return True

    async def load(self):
        if not self.models:
            await self.load_models()

    def model_info(self, model):
        provider_model = self.provider_model(model) or model
        for model_id, model_info in self.models.items():
            if model_id.lower() == provider_model.lower():
                return model_info
        return None

    def model_cost(self, model):
        model_info = self.model_info(model)
        return model_info.get("cost") if model_info else None

    def provider_model(self, model):
        # convert model to lowercase for case-insensitive comparison
        model_lower = model.lower()

        # if model is a map model id, return the provider model id
        for model_id, provider_model in self.map_models.items():
            if model_id.lower() == model_lower:
                return provider_model

        # if model is a provider model id, try again with just the model name
        for provider_model in self.map_models.values():
            if provider_model.lower() == model_lower:
                return provider_model

        # if model is a model id, try again with just the model id or name
        for model_id, provider_model_info in self.models.items():
            id = provider_model_info.get("id") or model_id
            if model_id.lower() == model_lower or id.lower() == model_lower:
                return id
            name = provider_model_info.get("name")
            if name and name.lower() == model_lower:
                return id

        # fallback to trying again with just the model short name
        for model_id, provider_model_info in self.models.items():
            id = provider_model_info.get("id") or model_id
            if "/" in id:
                model_name = id.split("/")[-1]
                if model_name.lower() == model_lower:
                    return id

        # if model is a full provider model id, try again with just the model name
        if "/" in model:
            last_part = model.split("/")[-1]
            return self.provider_model(last_part)

        return None

    def response_json(self, response):
        return response_json(response)

    def to_response(self, response, chat, started_at, context=None):
        if "metadata" not in response:
            response["metadata"] = {}
        response["metadata"]["duration"] = int((time.time() - started_at) * 1000)
        if chat is not None and "model" in chat:
            pricing = self.model_cost(chat["model"])
            if pricing and "input" in pricing and "output" in pricing:
                response["metadata"]["pricing"] = f"{pricing['input']}/{pricing['output']}"
        if context is not None:
            context["providerResponse"] = response
        return response

    def chat_summary(self, chat):
        return chat_summary(chat)

    async def process_chat(self, chat, provider_id=None):
        return await process_chat(chat, provider_id)

    async def chat(self, chat, context=None):
        chat["model"] = self.provider_model(chat["model"]) or chat["model"]

        modalities = chat.get("modalities") or []
        if len(modalities) > 0:
            for modality in modalities:
                # use default implementation for text modalities
                if modality == "text":
                    continue
                modality_provider = self.modalities.get(modality)
                if modality_provider:
                    return await modality_provider.chat(chat, self, context=context)
                else:
                    raise Exception(f"Provider {self.name} does not support '{modality}' modality")

        # with open(os.path.join(os.path.dirname(__file__), 'chat.wip.json'), "w") as f:
        #     f.write(json.dumps(chat, indent=2))

        if self.frequency_penalty is not None:
            chat["frequency_penalty"] = self.frequency_penalty
        if self.max_completion_tokens is not None:
            chat["max_completion_tokens"] = self.max_completion_tokens
        if self.n is not None:
            chat["n"] = self.n
        if self.parallel_tool_calls is not None:
            chat["parallel_tool_calls"] = self.parallel_tool_calls
        if self.presence_penalty is not None:
            chat["presence_penalty"] = self.presence_penalty
        if self.prompt_cache_key is not None:
            chat["prompt_cache_key"] = self.prompt_cache_key
        if self.reasoning_effort is not None:
            chat["reasoning_effort"] = self.reasoning_effort
        if self.safety_identifier is not None:
            chat["safety_identifier"] = self.safety_identifier
        if self.seed is not None:
            chat["seed"] = self.seed
        if self.service_tier is not None:
            chat["service_tier"] = self.service_tier
        if self.stop is not None:
            chat["stop"] = self.stop
        if self.store is not None:
            chat["store"] = self.store
        if self.temperature is not None:
            chat["temperature"] = self.temperature
        if self.top_logprobs is not None:
            chat["top_logprobs"] = self.top_logprobs
        if self.top_p is not None:
            chat["top_p"] = self.top_p
        if self.verbosity is not None:
            chat["verbosity"] = self.verbosity
        if self.enable_thinking is not None:
            chat["enable_thinking"] = self.enable_thinking

        chat = await self.process_chat(chat, provider_id=self.id)
        _log(f"POST {self.chat_url}")
        _log(chat_summary(chat))
        # remove metadata if any (conflicts with some providers, e.g. Z.ai)
        metadata = chat.pop("metadata", None)

        async with aiohttp.ClientSession() as session:
            started_at = time.time()
            async with session.post(
                self.chat_url, headers=self.headers, data=json.dumps(chat), timeout=aiohttp.ClientTimeout(total=120)
            ) as response:
                chat["metadata"] = metadata
                return self.to_response(await response_json(response), chat, started_at, context=context)


class MistralProvider(OpenAiCompatible):
    sdk = "@ai-sdk/mistral"

    def __init__(self, **kwargs):
        if "api" not in kwargs:
            kwargs["api"] = "https://api.mistral.ai/v1"
        super().__init__(**kwargs)


class GroqProvider(OpenAiCompatible):
    sdk = "@ai-sdk/groq"

    def __init__(self, **kwargs):
        if "api" not in kwargs:
            kwargs["api"] = "https://api.groq.com/openai/v1"
        super().__init__(**kwargs)

    async def process_chat(self, chat, provider_id=None):
        ret = await super().process_chat(chat, provider_id)
        chat.pop("modalities", None)  # groq doesn't support modalities
        messages = chat.get("messages", []).copy()
        for message in messages:
            message.pop("timestamp", None)  # groq doesn't support timestamp
        ret["messages"] = messages
        return ret


class XaiProvider(OpenAiCompatible):
    sdk = "@ai-sdk/xai"

    def __init__(self, **kwargs):
        if "api" not in kwargs:
            kwargs["api"] = "https://api.x.ai/v1"
        super().__init__(**kwargs)


class CodestralProvider(OpenAiCompatible):
    sdk = "codestral"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)


class OllamaProvider(OpenAiCompatible):
    sdk = "ollama"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # Ollama's OpenAI-compatible endpoint is at /v1/chat/completions
        self.chat_url = f"{self.api}/v1/chat/completions"

    async def load(self):
        if not self.models:
            await self.load_models()

    async def get_models(self):
        ret = {}
        try:
            async with aiohttp.ClientSession() as session:
                _log(f"GET {self.api}/api/tags")
                async with session.get(
                    f"{self.api}/api/tags", headers=self.headers, timeout=aiohttp.ClientTimeout(total=120)
                ) as response:
                    data = await response_json(response)
                    for model in data.get("models", []):
                        model_id = model["model"]
                        if model_id.endswith(":latest"):
                            model_id = model_id[:-7]
                        ret[model_id] = model_id
                    _log(f"Loaded Ollama models: {ret}")
        except Exception as e:
            _log(f"Error getting Ollama models: {e}")
            # return empty dict if ollama is not available
        return ret

    async def load_models(self):
        """Load models if all_models was requested"""

        # Map models to provider models {model_id:model_id}
        model_map = await self.get_models()
        if self.map_models:
            map_model_values = set(self.map_models.values())
            to = {}
            for k, v in model_map.items():
                if k in self.map_models:
                    to[k] = v
                if v in map_model_values:
                    to[k] = v
            model_map = to
        else:
            self.map_models = model_map
        models = {}
        for k, v in model_map.items():
            models[k] = {
                "id": k,
                "name": v.replace(":", " "),
                "modalities": {"input": ["text"], "output": ["text"]},
                "tool_call": True,
                "cost": {
                    "input": 0,
                    "output": 0,
                },
            }
        self.models = models

    def validate(self, **kwargs):
        return None


class LMStudioProvider(OllamaProvider):
    sdk = "lmstudio"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.chat_url = f"{self.api}/chat/completions"

    async def get_models(self):
        ret = {}
        try:
            async with aiohttp.ClientSession() as session:
                _log(f"GET {self.api}/models")
                async with session.get(
                    f"{self.api}/models", headers=self.headers, timeout=aiohttp.ClientTimeout(total=120)
                ) as response:
                    data = await response_json(response)
                    for model in data.get("data", []):
                        id = model["id"]
                        ret[id] = id
                    _log(f"Loaded LMStudio models: {ret}")
        except Exception as e:
            _log(f"Error getting LMStudio models: {e}")
            # return empty dict if ollama is not available
        return ret


class OpenAiLocalProvider(LMStudioProvider):
    sdk = "openai-local"


def get_provider_model(model_name):
    for provider in g_handlers.values():
        provider_model = provider.provider_model(model_name)
        if provider_model:
            return provider_model
    return None


def get_models():
    ret = []
    for provider in g_handlers.values():
        for model in provider.models:
            if model not in ret:
                ret.append(model)
    ret.sort()
    return ret


def get_active_models():
    ret = []
    existing_models = set()
    for provider_id, provider in g_handlers.items():
        for model in provider.models.values():
            name = model.get("name")
            if not name:
                _log(f"Provider {provider_id} model {model} has no name")
                continue
            if name not in existing_models:
                existing_models.add(name)
                item = model.copy()
                item.update({"provider": provider_id})
                ret.append(item)
    ret.sort(key=lambda x: x["id"])
    return ret


def api_providers():
    ret = []
    for id, provider in g_handlers.items():
        ret.append({"id": id, "name": provider.name, "models": provider.models})
    return ret


def to_error_message(e):
    # check if has 'message' attribute
    if hasattr(e, "message"):
        return e.message
    if hasattr(e, "status"):
        return str(e.status)
    return str(e)


def to_error_response(e, stacktrace=False):
    status = {"errorCode": "Error", "message": to_error_message(e)}
    if stacktrace:
        status["stackTrace"] = traceback.format_exc()
    return {"responseStatus": status}


def create_error_response(message, error_code="Error", stack_trace=None):
    ret = {"responseStatus": {"errorCode": error_code, "message": message}}
    if stack_trace:
        ret["responseStatus"]["stackTrace"] = stack_trace
    return ret


def should_cancel_thread(context):
    ret = context.get("cancelled", False)
    if ret:
        thread_id = context.get("threadId")
        _dbg(f"Thread cancelled {thread_id}")
    return ret


def g_chat_request(template=None, text=None, model=None, system_prompt=None):
    chat_template = g_config["defaults"].get(template or "text")
    if not chat_template:
        raise Exception(f"Chat template '{template}' not found")

    chat = chat_template.copy()
    if model:
        chat["model"] = model
    if system_prompt is not None:
        chat["messages"].insert(0, {"role": "system", "content": system_prompt})
    if text is not None:
        if not chat["messages"] or len(chat["messages"]) == 0:
            chat["messages"] = [{"role": "user", "content": [{"type": "text", "text": ""}]}]

        # replace content of last message if exists, else add
        last_msg = chat["messages"][-1] if "messages" in chat else None
        if last_msg and last_msg["role"] == "user":
            if isinstance(last_msg["content"], list):
                last_msg["content"][-1]["text"] = text
            else:
                last_msg["content"] = text
        else:
            chat["messages"].append({"role": "user", "content": text})

    return chat


def tool_result_part(result: dict, function_name: Optional[str] = None, function_args: Optional[dict] = None):
    args = function_args or {}
    type = result.get("type")
    prompt = args.get("prompt") or args.get("text") or args.get("message")
    if type == "text":
        return result.get("text"), None
    elif type == "image":
        format = result.get("format") or args.get("format") or "png"
        filename = result.get("filename") or args.get("filename") or f"{function_name}-{int(time.time())}.{format}"
        mime_type = get_file_mime_type(filename)
        image_info = {"type": mime_type}
        if prompt:
            image_info["prompt"] = prompt
        if "model" in args:
            image_info["model"] = args["model"]
        if "aspect_ratio" in args:
            image_info["aspect_ratio"] = args["aspect_ratio"]
        base64_data = result.get("data")
        if not base64_data:
            _dbg(f"Image data not found for {function_name}")
            return None, None
        url, _ = save_image_to_cache(base64_data, filename, image_info=image_info, ignore_info=True)
        resource = {
            "type": "image_url",
            "image_url": {
                "url": url,
            },
        }
        text = f"![{args.get('prompt') or filename}]({url})\n"
        return text, resource
    elif type == "audio":
        format = result.get("format") or args.get("format") or "mp3"
        filename = result.get("filename") or args.get("filename") or f"{function_name}-{int(time.time())}.{format}"
        mime_type = get_file_mime_type(filename)
        audio_info = {"type": mime_type}
        if prompt:
            audio_info["prompt"] = prompt
        if "model" in args:
            audio_info["model"] = args["model"]
        base64_data = result.get("data")
        if not base64_data:
            _dbg(f"Audio data not found for {function_name}")
            return None, None
        url, _ = save_audio_to_cache(base64_data, filename, audio_info=audio_info, ignore_info=True)
        resource = {
            "type": "audio_url",
            "audio_url": {
                "url": url,
            },
        }
        text = f"[{args.get('prompt') or filename}]({url})\n"
        return text, resource
    elif type == "file":
        filename = result.get("filename") or args.get("filename") or result.get("name") or args.get("name")
        format = result.get("format") or args.get("format") or (get_filename(filename) if filename else "txt")
        if not filename:
            filename = f"{function_name}-{int(time.time())}.{format}"

        mime_type = get_file_mime_type(filename)
        file_info = {"type": mime_type}
        if prompt:
            file_info["prompt"] = prompt
        if "model" in args:
            file_info["model"] = args["model"]
        base64_data = result.get("data")
        if not base64_data:
            _dbg(f"File data not found for {function_name}")
            return None, None
        url, info = save_bytes_to_cache(base64_data, filename, file_info=file_info)
        resource = {
            "type": "file",
            "file": {
                "file_data": url,
                "filename": info["name"],
            },
        }
        text = f"[{args.get('prompt') or filename}]({url})\n"
        return text, resource
    else:
        try:
            return json.dumps(result), None
        except Exception as e:
            _dbg(f"Error converting result to JSON: {e}")
            try:
                return str(result), None
            except Exception as e:
                _dbg(f"Error converting result to string: {e}")
                return None, None


def g_tool_result(result, function_name: Optional[str] = None, function_args: Optional[dict] = None):
    content = []
    resources = []
    args = function_args or {}
    _dbg(f"{function_name} tool result type: {type(result)}")
    if isinstance(result, dict):
        text, res = tool_result_part(result, function_name, args)
        if text:
            content.append(text)
        if res:
            resources.append(res)
    elif isinstance(result, list):
        for item in result:
            text, res = tool_result_part(item, function_name, args)
            if text:
                content.append(text)
            if res:
                resources.append(res)
    else:
        content = [str(result)]

    text = "\n".join(content)
    return text, resources


def convert_tool_args(function_name, function_args):
    """
    Convert tool arg values to their specified types.
    types: string, number, integer, boolean, object, array, null
    example prop_def = [
        {
            "type": "string"
        },
        {
            "default": "name",
            "type": "string",
            "enum": ["name", "size"]
        },
        {
            "default": [],
            "type": "array",
            "items": {
                "type": "string"
            }
        },
        {
            "anyOf": [
                {
                    "type": "string"
                },
                {
                    "type": "null"
                }
            ],
            "default": null,
        },
    ]
    """
    tool_def = g_app.get_tool_definition(function_name)
    if not tool_def:
        return function_args

    if "function" in tool_def and "parameters" in tool_def["function"]:
        parameters = tool_def.get("function", {}).get("parameters")
        properties = parameters.get("properties", {})
        required = parameters.get("required", [])
        new_args = function_args.copy()

        for key, value in function_args.items():
            if key in properties and isinstance(value, str):
                prop_type = properties[key].get("type")
                str_val = value.strip()

                if str_val == "":
                    if prop_type in ("integer", "number"):
                        new_args[key] = None
                    else:
                        new_args.pop(key)
                    continue

                if prop_type == "integer":
                    with contextlib.suppress(ValueError, TypeError):
                        new_args[key] = int(str_val)

                elif prop_type == "number":
                    with contextlib.suppress(ValueError, TypeError):
                        new_args[key] = float(str_val)

                elif prop_type == "boolean":
                    lower_val = str_val.lower()
                    if lower_val in ("true", "1", "yes"):
                        new_args[key] = True
                    elif lower_val in ("false", "0", "no"):
                        new_args[key] = False

                elif prop_type == "object":
                    if str_val == "":
                        new_args[key] = None
                    else:
                        with contextlib.suppress(json.JSONDecodeError, TypeError):
                            new_args[key] = json.loads(str_val)

                elif prop_type == "array":
                    if str_val == "":
                        new_args[key] = []
                    else:
                        # Simple CSV split for arrays; could be more robust with JSON parsing if wrapped in brackets
                        # Check if it looks like a JSON array
                        if str_val.startswith("[") and str_val.endswith("]"):
                            with contextlib.suppress(json.JSONDecodeError):
                                items = json.loads(str_val)
                        else:
                            items = [s.strip() for s in str_val.split(",")]
                        item_type = properties[key].get("items", {}).get("type")
                        if item_type == "integer":
                            items = [int(i) for i in items]
                        elif item_type == "number":
                            items = [float(i) for i in items]
                        new_args[key] = items

        # Validate required parameters
        missing = [key for key in required if key not in new_args]
        if missing:
            raise ValueError(f"Missing required arguments: {', '.join(missing)}")

        return new_args

    return function_args


async def g_exec_tool(function_name, function_args):
    _log(f"g_exec_tool: {function_name}")
    if function_name in g_app.tools:
        try:
            # Type conversion based on tool definition
            function_args = convert_tool_args(function_name, function_args)

            func = g_app.tools[function_name]
            is_async = inspect.iscoroutinefunction(func)
            _dbg(f"Executing {'async' if is_async else 'sync'} tool '{function_name}' with args: {function_args}")
            if is_async:
                return g_tool_result(await func(**function_args), function_name, function_args)
            else:
                return g_tool_result(func(**function_args), function_name, function_args)
        except Exception as e:
            return f"Error executing tool '{function_name}':\n{to_error_message(e)}", None
    return f"Error: Tool '{function_name}' not found", None


def group_resources(resources: list):
    """
    converts list of parts into a grouped dictionary, e.g:
    [{"type: "image_url", "image_url": {"url": "/image.jpg"}}] =>
    {"images": [{"type": "image_url", "image_url": {"url": "/image.jpg"}}] }
    """
    grouped = {}
    for res in resources or []:
        type = res.get("type")
        if not type:
            continue
        if type == "image_url":
            group = "images"
        elif type == "audio_url":
            group = "audios"
        elif type == "file_urls" or type == "file":
            group = "files"
        elif type == "text":
            group = "texts"
        else:
            group = "others"
        if group not in grouped:
            grouped[group] = []
        grouped[group].append(res)
    return grouped


async def g_chat_completion(chat, context=None):
    try:
        model = chat.get("model")
        if not model:
            raise Exception("Model not specified")

        if context is None:
            context = {"chat": chat, "tools": "all"}

        if "request_id" not in context:
            context["request_id"] = str(int(time.time() * 1000))

        # get first provider that has the model
        candidate_providers = [name for name, provider in g_handlers.items() if provider.provider_model(model)]
        if len(candidate_providers) == 0:
            raise (Exception(f"Model {model} not found"))
    except Exception as e:
        await g_app.on_chat_error(e, context or {"chat": chat})
        raise e

    started_at = time.time()
    first_exception = None
    provider_name = "Unknown"
    for name in candidate_providers:
        try:
            provider_name = name
            provider = g_handlers[name]
            _log(f"provider: {name} {type(provider).__name__}")
            started_at = time.time()
            context["startedAt"] = datetime.now()
            context["provider"] = name
            model_info = provider.model_info(model)
            context["modelCost"] = model_info.get("cost", provider.model_cost(model)) or {"input": 0, "output": 0}
            context["modelInfo"] = model_info

            # Accumulate usage across tool calls
            total_usage = {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
            }
            accumulated_cost = 0.0

            # Inject global tools if present
            current_chat = g_app.create_chat_with_tools(chat, use_tools=context.get("tools", "all"))

            # Apply pre-chat filters ONCE
            context["chat"] = current_chat
            for filter_func in g_app.chat_request_filters:
                await filter_func(current_chat, context)

            # Tool execution loop
            max_iterations = 10
            tool_history = []
            final_response = None

            for request_count in range(max_iterations):
                if should_cancel_thread(context):
                    return

                if DEBUG:
                    messages = current_chat.get("messages", [])
                    last_message = messages[-1] if messages else None
                    _dbg(f"Provider {provider_name}, request {request_count}:\n{json.dumps(last_message, indent=2)}")

                response = await provider.chat(current_chat, context=context)

                if should_cancel_thread(context):
                    return

                # Aggregate usage
                if "usage" in response:
                    usage = response["usage"]
                    total_usage["prompt_tokens"] += usage.get("prompt_tokens", 0)
                    total_usage["completion_tokens"] += usage.get("completion_tokens", 0)
                    total_usage["total_tokens"] += usage.get("total_tokens", 0)

                    # Calculate cost for this step if available
                    if "cost" in response and isinstance(response["cost"], (int, float)):
                        accumulated_cost += response["cost"]
                    elif "cost" in usage and isinstance(usage["cost"], (int, float)):
                        accumulated_cost += usage["cost"]

                # Check for tool_calls in the response
                choice = response.get("choices", [])[0] if response.get("choices") else {}
                message = choice.get("message", {})
                tool_calls = message.get("tool_calls")
                supports_tool_calls = model_info.get("tool_call", False)

                if tool_calls and supports_tool_calls:
                    # Append the assistant's message with tool calls to history
                    if "messages" not in current_chat:
                        current_chat["messages"] = []
                    if "timestamp" not in message:
                        message["timestamp"] = int(time.time() * 1000)
                    current_chat["messages"].append(message)
                    tool_history.append(message)

                    await g_app.on_chat_tool(current_chat, context)

                    for tool_call in tool_calls:
                        function_name = tool_call["function"]["name"]
                        try:
                            function_args = json.loads(tool_call["function"]["arguments"])
                        except Exception as e:
                            tool_result = f"Error: Failed to parse JSON arguments for tool '{function_name}': {to_error_message(e)}"
                        else:
                            tool_result, resources = await g_exec_tool(function_name, function_args)

                        # Append tool result to history
                        tool_msg = {"role": "tool", "tool_call_id": tool_call["id"], "content": to_content(tool_result)}

                        tool_msg.update(group_resources(resources))

                        current_chat["messages"].append(tool_msg)
                        tool_history.append(tool_msg)

                    await g_app.on_chat_tool(current_chat, context)

                    if should_cancel_thread(context):
                        return

                    # Continue loop to send tool results back to LLM
                    continue

                # If no tool calls, this is the final response
                if tool_history:
                    response["tool_history"] = tool_history

                # Update final response with aggregated usage
                if "usage" not in response:
                    response["usage"] = {}
                # convert to int seconds
                context["duration"] = duration = int(time.time() - started_at)
                total_usage.update({"duration": duration})
                response["usage"].update(total_usage)
                # If we accumulated cost, set it on the response
                if accumulated_cost > 0:
                    response["cost"] = accumulated_cost

                final_response = response
                break  # Exit tool loop

            if final_response:
                # Apply post-chat filters ONCE on final response
                for filter_func in g_app.chat_response_filters:
                    await filter_func(final_response, context)

                if DEBUG:
                    _dbg(json.dumps(final_response, indent=2))

                return final_response

        except Exception as e:
            if first_exception is None:
                first_exception = e
                context["stackTrace"] = traceback.format_exc()
            _err(f"Provider {provider_name} failed", first_exception)
            continue

    # If we get here, all providers failed
    if first_exception:
        await g_app.on_chat_error(first_exception, context or {"chat": chat})
        raise first_exception

    e = Exception("All providers failed")
    await g_app.on_chat_error(e, context or {"chat": chat})
    raise e


async def cli_chat(chat, tools=None, image=None, audio=None, file=None, args=None, raw=False):
    if g_default_model:
        chat["model"] = g_default_model

    # Apply args parameters to chat request
    if args:
        chat = apply_args_to_chat(chat, args)

    # process_chat downloads the image, just adding the reference here
    if image is not None:
        first_message = None
        for message in chat["messages"]:
            if message["role"] == "user":
                first_message = message
                break
        image_content = {"type": "image_url", "image_url": {"url": image}}
        if "content" in first_message:
            if isinstance(first_message["content"], list):
                image_url = None
                for item in first_message["content"]:
                    if "image_url" in item:
                        image_url = item["image_url"]
                # If no image_url, add one
                if image_url is None:
                    first_message["content"].insert(0, image_content)
                else:
                    image_url["url"] = image
            else:
                first_message["content"] = [image_content, {"type": "text", "text": first_message["content"]}]
    if audio is not None:
        first_message = None
        for message in chat["messages"]:
            if message["role"] == "user":
                first_message = message
                break
        audio_content = {"type": "input_audio", "input_audio": {"data": audio, "format": "mp3"}}
        if "content" in first_message:
            if isinstance(first_message["content"], list):
                input_audio = None
                for item in first_message["content"]:
                    if "input_audio" in item:
                        input_audio = item["input_audio"]
                # If no input_audio, add one
                if input_audio is None:
                    first_message["content"].insert(0, audio_content)
                else:
                    input_audio["data"] = audio
            else:
                first_message["content"] = [audio_content, {"type": "text", "text": first_message["content"]}]
    if file is not None:
        first_message = None
        for message in chat["messages"]:
            if message["role"] == "user":
                first_message = message
                break
        file_content = {"type": "file", "file": {"filename": get_filename(file), "file_data": file}}
        if "content" in first_message:
            if isinstance(first_message["content"], list):
                file_data = None
                for item in first_message["content"]:
                    if "file" in item:
                        file_data = item["file"]
                # If no file_data, add one
                if file_data is None:
                    first_message["content"].insert(0, file_content)
                else:
                    file_data["filename"] = get_filename(file)
                    file_data["file_data"] = file
            else:
                first_message["content"] = [file_content, {"type": "text", "text": first_message["content"]}]

    if g_verbose:
        printdump(chat)

    try:
        context = {
            "tools": tools or "all",
        }
        response = await g_app.chat_completion(chat, context=context)

        if raw:
            print(json.dumps(response, indent=2))
            exit(0)
        else:
            msg = response["choices"][0]["message"]
            if "content" in msg or "answer" in msg:
                print(msg["content"])

            generated_files = []
            for choice in response["choices"]:
                if "message" in choice:
                    msg = choice["message"]
                    if "images" in msg:
                        for image in msg["images"]:
                            image_url = image["image_url"]["url"]
                            generated_files.append(image_url)
                    if "audios" in msg:
                        for audio in msg["audios"]:
                            audio_url = audio["audio_url"]["url"]
                            generated_files.append(audio_url)

            if len(generated_files) > 0:
                print("\nSaved files:")
                for file in generated_files:
                    if file.startswith("/~cache"):
                        print(get_cache_path(file[8:]))
                        print(urljoin("http://localhost:8000", file))
                    else:
                        print(file)

    except HTTPError as e:
        # HTTP error (4xx, 5xx)
        print(f"{e}:\n{e.body}")
        g_app.exit(1)
    except aiohttp.ClientConnectionError as e:
        # Connection issues
        print(f"Connection error: {e}")
        g_app.exit(1)
    except asyncio.TimeoutError as e:
        # Timeout
        print(f"Timeout error: {e}")
        g_app.exit(1)


def config_str(key):
    return key in g_config and g_config[key] or None


def load_config(config, providers, verbose=None, debug=None, disable_extensions: List[str] = None):
    global g_config, g_providers, g_verbose
    g_config = config
    g_providers = providers
    if verbose is not None:
        g_verbose = verbose
    if debug is not None:
        global DEBUG
        DEBUG = debug
    if disable_extensions:
        global DISABLE_EXTENSIONS
        DISABLE_EXTENSIONS = disable_extensions


def init_llms(config, providers):
    global g_config, g_handlers

    load_config(config, providers)
    g_handlers = {}
    # iterate over config and replace $ENV with env value
    for key, value in g_config.items():
        if isinstance(value, str) and value.startswith("$"):
            g_config[key] = os.getenv(value[1:], "")

    # if g_verbose:
    #     printdump(g_config)
    providers = g_config["providers"]

    for id, orig in providers.items():
        if "enabled" in orig and not orig["enabled"]:
            continue

        provider, constructor_kwargs = create_provider_from_definition(id, orig)
        if provider and provider.test(**constructor_kwargs):
            g_handlers[id] = provider
    return g_handlers


def create_provider_from_definition(id, orig):
    definition = orig.copy()
    provider_id = definition.get("id", id)
    if "id" not in definition:
        definition["id"] = provider_id
    provider = g_providers.get(provider_id)
    constructor_kwargs = create_provider_kwargs(definition, provider)
    provider = create_provider(constructor_kwargs)
    return provider, constructor_kwargs


def create_provider_kwargs(definition, provider=None):
    if provider:
        provider = provider.copy()
        provider.update(definition)
    else:
        provider = definition.copy()

    # Replace API keys with environment variables if they start with $
    if "api_key" in provider:
        value = provider["api_key"]
        if isinstance(value, str) and value.startswith("$"):
            provider["api_key"] = os.getenv(value[1:], "")

    if "api_key" not in provider and "env" in provider:
        for env_var in provider["env"]:
            val = os.getenv(env_var)
            if val:
                provider["api_key"] = val
                break

    # Create a copy of provider
    constructor_kwargs = dict(provider.items())
    # Create a copy of all list and dict values
    for key, value in constructor_kwargs.items():
        if isinstance(value, (list, dict)):
            constructor_kwargs[key] = value.copy()
    constructor_kwargs["headers"] = g_config["defaults"]["headers"].copy()

    if "modalities" in definition:
        constructor_kwargs["modalities"] = {}
        for modality, modality_definition in definition["modalities"].items():
            modality_provider = create_provider(modality_definition)
            if not modality_provider:
                return None
            constructor_kwargs["modalities"][modality] = modality_provider

    return constructor_kwargs


def create_provider(provider):
    if not isinstance(provider, dict):
        return None
    provider_label = provider.get("id", provider.get("name", "unknown"))
    npm_sdk = provider.get("npm")
    if not npm_sdk:
        _log(f"Provider {provider_label} is missing 'npm' sdk")
        return None

    for provider_type in g_app.all_providers:
        if provider_type.sdk == npm_sdk:
            kwargs = create_provider_kwargs(provider)
            if kwargs is None:
                kwargs = provider
            return provider_type(**kwargs)

    _log(f"Could not find provider {provider_label} with npm sdk {npm_sdk}")
    return None


async def load_llms():
    global g_handlers
    _log("Loading providers...")
    for _name, provider in g_handlers.items():
        await provider.load()


def save_config(config):
    global g_config, g_config_path
    g_config = config
    with open(g_config_path, "w", encoding="utf-8") as f:
        json.dump(g_config, f, indent=4)
        _log(f"Saved config to {g_config_path}")


def github_url(filename):
    return f"https://raw.githubusercontent.com/ServiceStack/llms/refs/heads/main/llms/{filename}"


async def get_text(url):
    async with aiohttp.ClientSession() as session:
        _log(f"GET {url}")
        async with session.get(url) as resp:
            text = await resp.text()
            if resp.status >= 400:
                raise Exception(http_error_to_message(resp, text))
            return text


async def save_text_url(url, save_path):
    text = await get_text(url)
    os.makedirs(os.path.dirname(save_path), exist_ok=True)
    with open(save_path, "w", encoding="utf-8") as f:
        f.write(text)
    return text


async def save_default_config(config_path):
    global g_config
    config_json = await save_text_url(github_url("llms.json"), config_path)
    g_config = json.loads(config_json)


async def update_providers(home_providers_path):
    global g_providers
    text = await get_text("https://models.dev/api.json")
    all_providers = json.loads(text)
    extra_providers = {}
    extra_providers_path = home_providers_path.replace("providers.json", "providers-extra.json")
    if os.path.exists(extra_providers_path):
        with open(extra_providers_path) as f:
            extra_providers = json.load(f)

    filtered_providers = {}
    for id, provider in all_providers.items():
        if id in g_config["providers"]:
            filtered_providers[id] = provider
            if id in extra_providers and "models" in extra_providers[id]:
                for model_id, model in extra_providers[id]["models"].items():
                    if "id" not in model:
                        model["id"] = model_id
                    if "name" not in model:
                        model["name"] = id_to_name(model["id"])
                    filtered_providers[id]["models"][model_id] = model

    os.makedirs(os.path.dirname(home_providers_path), exist_ok=True)
    with open(home_providers_path, "w", encoding="utf-8") as f:
        json.dump(filtered_providers, f)

    g_providers = filtered_providers


def provider_status():
    enabled = list(g_handlers.keys())
    disabled = [provider for provider in g_config["providers"] if provider not in enabled]
    enabled.sort()
    disabled.sort()
    return enabled, disabled


def print_status():
    enabled, disabled = provider_status()
    if len(enabled) > 0:
        print(f"\nEnabled: {', '.join(enabled)}")
    else:
        print("\nEnabled: None")
    if len(disabled) > 0:
        print(f"Disabled: {', '.join(disabled)}")
    else:
        print("Disabled: None")


def home_llms_path(filename):
    home_dir = os.getenv("LLMS_HOME", os.path.join(os.getenv("HOME"), ".llms"))
    relative_path = os.path.join(home_dir, filename)
    # return resolved full absolute path
    return os.path.abspath(os.path.normpath(relative_path))


def get_cache_path(path=""):
    return home_llms_path(f"cache/{path}") if path else home_llms_path("cache")


def get_config_path():
    home_config_path = home_llms_path("llms.json")
    check_paths = [
        "./llms.json",
        home_config_path,
    ]
    if os.getenv("LLMS_CONFIG_PATH"):
        check_paths.insert(0, os.getenv("LLMS_CONFIG_PATH"))

    for check_path in check_paths:
        g_config_path = os.path.normpath(os.path.join(os.path.dirname(__file__), check_path))
        if os.path.exists(g_config_path):
            return g_config_path
    return None


def enable_provider(provider):
    msg = None
    provider_config = g_config["providers"][provider]
    if not provider_config:
        return None, f"Provider {provider} not found"

    provider, constructor_kwargs = create_provider_from_definition(provider, provider_config)
    msg = provider.validate(**constructor_kwargs)
    if msg:
        return None, msg

    provider_config["enabled"] = True
    save_config(g_config)
    init_llms(g_config, g_providers)
    return provider_config, msg


def disable_provider(provider):
    provider_config = g_config["providers"][provider]
    provider_config["enabled"] = False
    save_config(g_config)
    init_llms(g_config, g_providers)


def resolve_root():
    # Try to find the resource root directory
    # When installed as a package, static files may be in different locations

    # Method 1: Try importlib.resources for package data (Python 3.9+)
    try:
        try:
            # Try to access the package resources
            pkg_files = resources.files("llms")
            # Check if ui directory exists in package resources
            if hasattr(pkg_files, "is_dir") and (pkg_files / "ui").is_dir():
                _log(f"RESOURCE ROOT (package): {pkg_files}")
                return pkg_files
        except (FileNotFoundError, AttributeError, TypeError):
            # Package doesn't have the resources, try other methods
            pass
    except ImportError:
        # importlib.resources not available (Python < 3.9)
        pass

    # Method 1b: Look for the installed package and check for UI files
    try:
        import llms

        # If llms is a package, check its directory
        if hasattr(llms, "__path__"):
            # It's a package
            package_path = Path(llms.__path__[0])

            # Check if UI files are in the package directory
            if (package_path / "index.html").exists() and (package_path / "ui").is_dir():
                _log(f"RESOURCE ROOT (package directory): {package_path}")
                return package_path
        else:
            # It's a module
            module_path = Path(llms.__file__).resolve().parent

            # Check if UI files are in the same directory as the module
            if (module_path / "index.html").exists() and (module_path / "ui").is_dir():
                _log(f"RESOURCE ROOT (module directory): {module_path}")
                return module_path

            # Check parent directory (sometimes data files are installed one level up)
            parent_path = module_path.parent
            if (parent_path / "index.html").exists() and (parent_path / "ui").is_dir():
                _log(f"RESOURCE ROOT (module parent): {parent_path}")
                return parent_path

    except (ImportError, AttributeError):
        pass

    # Method 2: Try to find data files in sys.prefix (where data_files are installed)
    # Get all possible installation directories
    possible_roots = [
        Path(sys.prefix),  # Standard installation
        Path(sys.prefix) / "share",  # Some distributions
        Path(sys.base_prefix),  # Virtual environments
        Path(sys.base_prefix) / "share",
    ]

    # Add site-packages directories
    for site_dir in site.getsitepackages():
        possible_roots.extend(
            [
                Path(site_dir),
                Path(site_dir).parent,
                Path(site_dir).parent / "share",
            ]
        )

    # Add user site directory
    try:
        user_site = site.getusersitepackages()
        if user_site:
            possible_roots.extend(
                [
                    Path(user_site),
                    Path(user_site).parent,
                    Path(user_site).parent / "share",
                ]
            )
    except AttributeError:
        pass

    # Method 2b: Look for data files in common macOS Homebrew locations
    # Homebrew often installs data files in different locations
    homebrew_roots = []
    if sys.platform == "darwin":  # macOS
        homebrew_prefixes = ["/opt/homebrew", "/usr/local"]  # Apple Silicon and Intel
        for prefix in homebrew_prefixes:
            if Path(prefix).exists():
                homebrew_roots.extend(
                    [
                        Path(prefix),
                        Path(prefix) / "share",
                        Path(prefix) / "lib" / "python3.11" / "site-packages",
                        Path(prefix)
                        / "lib"
                        / f"python{sys.version_info.major}.{sys.version_info.minor}"
                        / "site-packages",
                    ]
                )

    possible_roots.extend(homebrew_roots)

    for root in possible_roots:
        try:
            if root.exists() and (root / "index.html").exists() and (root / "ui").is_dir():
                _log(f"RESOURCE ROOT (data files): {root}")
                return root
        except (OSError, PermissionError):
            continue

    # Method 3: Development mode - look relative to this file
    # __file__ is *this* module; look in same directory first, then parent
    dev_roots = [
        Path(__file__).resolve().parent,  # Same directory as llms.py
        Path(__file__).resolve().parent.parent,  # Parent directory (repo root)
    ]

    for root in dev_roots:
        try:
            if (root / "index.html").exists() and (root / "ui").is_dir():
                _log(f"RESOURCE ROOT (development): {root}")
                return root
        except (OSError, PermissionError):
            continue

    # Fallback: use the directory containing this file
    from_file = Path(__file__).resolve().parent
    _log(f"RESOURCE ROOT (fallback): {from_file}")
    return from_file


def resource_exists(resource_path):
    # Check if resource files exist (handle both Path and Traversable objects)
    try:
        if hasattr(resource_path, "is_file"):
            return resource_path.is_file()
        else:
            return os.path.exists(resource_path)
    except (OSError, AttributeError):
        pass


def read_resource_text(resource_path):
    if hasattr(resource_path, "read_text"):
        return resource_path.read_text()
    else:
        with open(resource_path, encoding="utf-8") as f:
            return f.read()


def read_resource_file_bytes(resource_file):
    try:
        if hasattr(_ROOT, "joinpath"):
            # importlib.resources Traversable
            index_resource = _ROOT.joinpath(resource_file)
            if index_resource.is_file():
                return index_resource.read_bytes()
        else:
            # Regular Path object
            index_path = _ROOT / resource_file
            if index_path.exists():
                return index_path.read_bytes()
    except (OSError, PermissionError, AttributeError) as e:
        _log(f"Error reading resource bytes: {e}")


async def check_models(provider_name, model_names=None):
    """
    Check validity of models for a specific provider by sending a ping message.

    Args:
        provider_name: Name of the provider to check
        model_names: List of specific model names to check, or None to check all models
    """
    if provider_name not in g_handlers:
        print(f"Provider '{provider_name}' not found or not enabled")
        print(f"Available providers: {', '.join(g_handlers.keys())}")
        return

    provider = g_handlers[provider_name]
    models_to_check = []

    # Determine which models to check
    if model_names is None or (len(model_names) == 1 and model_names[0] == "all"):
        # Check all models for this provider
        models_to_check = list(provider.models.keys())
    else:
        # Check only specified models
        for model_name in model_names:
            provider_model = provider.provider_model(model_name)
            if provider_model:
                models_to_check.append(model_name)
            else:
                print(f"Model '{model_name}' not found in provider '{provider_name}'")

    if not models_to_check:
        print(f"No models to check for provider '{provider_name}'")
        return

    print(
        f"\nChecking {len(models_to_check)} model{'' if len(models_to_check) == 1 else 's'} for provider '{provider_name}':\n"
    )

    # Test each model
    for model in models_to_check:
        await check_provider_model(provider, model)

    print()


async def check_provider_model(provider, model):
    # Create a simple ping chat request
    chat = (provider.check or g_config["defaults"]["check"]).copy()
    chat["model"] = model

    success = False
    started_at = time.time()
    try:
        # Try to get a response from the model
        response = await provider.chat(chat)
        duration_ms = int((time.time() - started_at) * 1000)

        # Check if we got a valid response
        if response and "choices" in response and len(response["choices"]) > 0:
            success = True
            print(f"  ✓ {model:<40} ({duration_ms}ms)")
        else:
            print(f"  ✗ {model:<40} Invalid response format")
    except HTTPError as e:
        duration_ms = int((time.time() - started_at) * 1000)
        error_msg = f"HTTP {e.status}"
        try:
            # Try to parse error body for more details
            error_body = json.loads(e.body) if e.body else {}
            if "error" in error_body:
                error = error_body["error"]
                if isinstance(error, dict):
                    if "message" in error and isinstance(error["message"], str):
                        # OpenRouter
                        error_msg = error["message"]
                        if "code" in error:
                            error_msg = f"{error['code']} {error_msg}"
                        if "metadata" in error and "raw" in error["metadata"]:
                            error_msg += f" - {error['metadata']['raw']}"
                        if "provider" in error:
                            error_msg += f" ({error['provider']})"
                elif isinstance(error, str):
                    error_msg = error
            elif "message" in error_body:
                if isinstance(error_body["message"], str):
                    error_msg = error_body["message"]
                elif (
                    isinstance(error_body["message"], dict)
                    and "detail" in error_body["message"]
                    and isinstance(error_body["message"]["detail"], list)
                ):
                    # codestral error format
                    error_msg = error_body["message"]["detail"][0]["msg"]
                    if (
                        "loc" in error_body["message"]["detail"][0]
                        and len(error_body["message"]["detail"][0]["loc"]) > 0
                    ):
                        error_msg += f" (in {' '.join(error_body['message']['detail'][0]['loc'])})"
        except Exception as parse_error:
            _log(f"Error parsing error body: {parse_error}")
            error_msg = e.body[:100] if e.body else f"HTTP {e.status}"
        print(f"  ✗ {model:<40} {error_msg}")
    except asyncio.TimeoutError:
        duration_ms = int((time.time() - started_at) * 1000)
        print(f"  ✗ {model:<40} Timeout after {duration_ms}ms")
    except Exception as e:
        duration_ms = int((time.time() - started_at) * 1000)
        error_msg = str(e)[:100]
        print(f"  ✗ {model:<40} {error_msg}")
    return success


def text_from_resource(filename):
    global _ROOT
    resource_path = _ROOT / filename
    if resource_exists(resource_path):
        try:
            return read_resource_text(resource_path)
        except (OSError, AttributeError) as e:
            _log(f"Error reading resource config {filename}: {e}")
    return None


def text_from_file(filename):
    if os.path.exists(filename):
        with open(filename, encoding="utf-8") as f:
            return f.read()
    return None


def json_from_file(filename):
    if os.path.exists(filename):
        with open(filename, encoding="utf-8") as f:
            return json.load(f)
    return None


async def text_from_resource_or_url(filename):
    text = text_from_resource(filename)
    if not text:
        try:
            resource_url = github_url(filename)
            text = await get_text(resource_url)
        except Exception as e:
            _log(f"Error downloading JSON from {resource_url}: {e}")
            raise e
    return text


async def save_home_configs():
    home_config_path = home_llms_path("llms.json")
    home_providers_path = home_llms_path("providers.json")
    home_providers_extra_path = home_llms_path("providers-extra.json")

    if (
        os.path.exists(home_config_path)
        and os.path.exists(home_providers_path)
        and os.path.exists(home_providers_extra_path)
    ):
        return

    llms_home = os.path.dirname(home_config_path)
    os.makedirs(llms_home, exist_ok=True)
    try:
        if not os.path.exists(home_config_path):
            config_json = await text_from_resource_or_url("llms.json")
            with open(home_config_path, "w", encoding="utf-8") as f:
                f.write(config_json)
            _log(f"Created default config at {home_config_path}")

        if not os.path.exists(home_providers_path):
            providers_json = await text_from_resource_or_url("providers.json")
            with open(home_providers_path, "w", encoding="utf-8") as f:
                f.write(providers_json)
            _log(f"Created default providers config at {home_providers_path}")

        if not os.path.exists(home_providers_extra_path):
            extra_json = await text_from_resource_or_url("providers-extra.json")
            with open(home_providers_extra_path, "w", encoding="utf-8") as f:
                f.write(extra_json)
            _log(f"Created default extra providers config at {home_providers_extra_path}")
    except Exception:
        print("Could not create llms.json. Create one with --init or use --config <path>")
        exit(1)


def load_config_json(config_json):
    if config_json is None:
        return None
    config = json.loads(config_json)
    if not config or "version" not in config or config["version"] < 3:
        preserve_keys = ["auth", "defaults", "limits", "convert"]
        new_config = json.loads(text_from_resource("llms.json"))
        if config:
            for key in preserve_keys:
                if key in config:
                    new_config[key] = config[key]
        config = new_config
        # move old config to YYYY-MM-DD.bak
        new_path = f"{g_config_path}.{datetime.now().strftime('%Y-%m-%d')}.bak"
        if os.path.exists(new_path):
            os.remove(new_path)
        os.rename(g_config_path, new_path)
        print(f"llms.json migrated. old config moved to {new_path}")
        # save new config
        save_config(g_config)
    return config


async def reload_providers():
    global g_config, g_handlers
    g_handlers = init_llms(g_config, g_providers)
    await load_llms()
    _log(f"{len(g_handlers)} providers loaded")
    return g_handlers


async def watch_config_files(config_path, providers_path, interval=1):
    """Watch config files and reload providers when they change"""
    global g_config

    config_path = Path(config_path)
    providers_path = Path(providers_path)

    _log(f"Watching config file: {config_path}")
    _log(f"Watching providers file: {providers_path}")

    def get_latest_mtime():
        ret = 0
        name = "llms.json"
        if config_path.is_file():
            ret = config_path.stat().st_mtime
            name = config_path.name
        if providers_path.is_file() and providers_path.stat().st_mtime > ret:
            ret = providers_path.stat().st_mtime
            name = providers_path.name
        return ret, name

    latest_mtime, name = get_latest_mtime()

    while True:
        await asyncio.sleep(interval)

        # Check llms.json
        try:
            new_mtime, name = get_latest_mtime()
            if new_mtime > latest_mtime:
                _log(f"Config file changed: {name}")
                latest_mtime = new_mtime

                try:
                    # Reload llms.json
                    with open(config_path) as f:
                        g_config = json.load(f)

                    # Reload providers
                    await reload_providers()
                    _log("Providers reloaded successfully")
                except Exception as e:
                    _log(f"Error reloading config: {e}")
        except FileNotFoundError:
            pass


class AuthProvider:
    def __init__(self, app):
        self.app = app

    def get_session_token(self, request: web.Request):
        return (
            request.query.get("session") or request.headers.get("X-Session-Token") or request.cookies.get("llms-token")
        )

    def get_session(self, request: web.Request) -> Optional[Dict[str, Any]]:
        session_token = self.get_session_token(request)

        if not session_token or session_token not in self.app.sessions:
            return None

        session_data = self.app.sessions[session_token]
        return session_data

    def get_username(self, request: web.Request) -> Optional[str]:
        session = self.get_session(request)
        if session:
            return session.get("userName")
        return None

    def check_auth(self, request: web.Request) -> Tuple[bool, Optional[Dict[str, Any]]]:
        """Check if request is authenticated. Returns (is_authenticated, user_data)"""
        session = self.get_session(request)
        if session:
            return True, session
        return False, None


class AppExtensions:
    """
    APIs extensions can use to extend the app
    """

    def __init__(self, cli_args: argparse.Namespace, extra_args: Dict[str, Any]):
        self.cli_args = cli_args
        self.extra_args = extra_args
        self.config = None
        self.error_auth_required = create_error_response("Authentication required", "Unauthorized")
        self.ui_extensions = []
        self.chat_request_filters = []
        self.extensions = []
        self.loaded = False
        self.chat_tool_filters = []
        self.chat_response_filters = []
        self.chat_error_filters = []
        self.server_add_get = []
        self.server_add_post = []
        self.server_add_put = []
        self.server_add_delete = []
        self.server_add_patch = []
        self.cache_saved_filters = []
        self.shutdown_handlers = []
        self.tools = {}
        self.tool_definitions = []
        self.tool_groups = {}
        self.index_headers = []
        self.index_footers = []
        self.allowed_directories = []
        self.auth_providers = []
        self.sessions = {}  # OAuth session storage: {session_token: {userId, userName, displayName, profileUrl, email, created}}
        self.oauth_states = {}  # CSRF protection: {state: {created, redirect_uri}}
        self.request_args = {
            "image_config": dict,  # e.g. { "aspect_ratio": "1:1" }
            "temperature": float,  # e.g: 0.7
            "max_completion_tokens": int,  # e.g: 2048
            "seed": int,  # e.g: 42
            "top_p": float,  # e.g: 0.9
            "frequency_penalty": float,  # e.g: 0.5
            "presence_penalty": float,  # e.g: 0.5
            "stop": list,  # e.g: ["Stop"]
            "reasoning_effort": str,  # e.g: minimal, low, medium, high
            "verbosity": str,  # e.g: low, medium, high
            "service_tier": str,  # e.g: auto, default
            "top_logprobs": int,
            "safety_identifier": str,
            "store": bool,
            "enable_thinking": bool,
        }
        self.all_providers = [
            OpenAiCompatible,
            MistralProvider,
            GroqProvider,
            XaiProvider,
            CodestralProvider,
            OllamaProvider,
            LMStudioProvider,
            OpenAiLocalProvider,
        ]
        self.aspect_ratios = {
            "1:1": "1024×1024",
            "2:3": "832×1248",
            "3:2": "1248×832",
            "3:4": "864×1184",
            "4:3": "1184×864",
            "4:5": "896×1152",
            "5:4": "1152×896",
            "9:16": "768×1344",
            "16:9": "1344×768",
            "21:9": "1536×672",
        }
        self.import_maps = {
            "vue-prod": "/ui/lib/vue.min.mjs",
            "vue": "/ui/lib/vue.mjs",
            "vue-router": "/ui/lib/vue-router.min.mjs",
            "@servicestack/client": "/ui/lib/servicestack-client.mjs",
            "@servicestack/vue": "/ui/lib/servicestack-vue.mjs",
            "idb": "/ui/lib/idb.min.mjs",
            "marked": "/ui/lib/marked.min.mjs",
            "highlight.js": "/ui/lib/highlight.min.mjs",
            "chart.js": "/ui/lib/chart.js",
            "color.js": "/ui/lib/color.js",
            "ctx.mjs": "/ui/ctx.mjs",
        }

    def set_config(self, config: Dict[str, Any]):
        self.config = config

    def set_allowed_directories(
        self, directories: List[Annotated[str, "List of absolute paths that are allowed to be accessed."]]
    ) -> None:
        """Set the list of allowed directories."""
        self.allowed_directories = [os.path.abspath(d) for d in directories]

    def add_allowed_directory(self, path: str) -> None:
        """Add an allowed directory."""
        abs_path = os.path.abspath(path)
        if abs_path not in self.allowed_directories:
            self.allowed_directories.append(abs_path)

    def get_allowed_directories(self) -> List[str]:
        """Get the list of allowed directories."""
        return self.allowed_directories

    def add_auth_provider(self, auth_provider: AuthProvider) -> None:
        """Add an authentication provider."""
        self.auth_providers.append(auth_provider)

    def is_auth_enabled(self) -> bool:
        return len(self.auth_providers) > 0

    def get_session(self, request: web.Request) -> Optional[Dict[str, Any]]:
        for auth_provider in self.auth_providers:
            session = auth_provider.get_session(request)
            if session:
                return session
            return None

    def get_username(self, request: web.Request) -> Optional[str]:
        for auth_provider in self.auth_providers:
            username = auth_provider.get_username(request)
            if username:
                return username
            return None

    def check_auth(self, request: web.Request) -> Tuple[bool, Optional[Dict[str, Any]]]:
        """Check if request is authenticated. Returns (is_authenticated, user_data)"""
        if len(self.auth_providers) == 0:
            return True, None

        for auth_provider in self.auth_providers:
            is_authenticated, user_data = auth_provider.check_auth(request)
            if is_authenticated:
                return True, user_data

        return False, None

    def get_user_path(self, username: Optional[str] = None) -> str:
        if username:
            return home_llms_path(os.path.join("user", username))
        return home_llms_path(os.path.join("user", "default"))

    def get_providers(self) -> Dict[str, Any]:
        return g_handlers

    def chat_request(
        self,
        template: Optional[str] = None,
        text: Optional[str] = None,
        model: Optional[str] = None,
        system_prompt: Optional[str] = None,
    ) -> Dict[str, Any]:
        return g_chat_request(template=template, text=text, model=model, system_prompt=system_prompt)

    async def chat_completion(self, chat: Dict[str, Any], context: Optional[Dict[str, Any]] = None) -> Any:
        response = await g_chat_completion(chat, context)
        return response

    def on_cache_saved_filters(self, context: Dict[str, Any]):
        # _log(f"on_cache_saved_filters {len(self.cache_saved_filters)}: {context['url']}")
        for filter_func in self.cache_saved_filters:
            filter_func(context)

    async def on_chat_error(self, e: Exception, context: Dict[str, Any]):
        # Apply chat error filters
        if "stackTrace" not in context:
            context["stackTrace"] = traceback.format_exc()
        for filter_func in self.chat_error_filters:
            try:
                task = filter_func(e, context)
                if inspect.iscoroutine(task):
                    await task
            except Exception as e:
                _err("chat error filter failed", e)

    async def on_chat_tool(self, chat: Dict[str, Any], context: Dict[str, Any]):
        m_len = len(chat.get("messages", []))
        t_len = len(self.chat_tool_filters)
        _dbg(
            f"on_tool_call for thread {context.get('threadId')} with {m_len} {pluralize('message', m_len)}, invoking {t_len} {pluralize('filter', t_len)}:"
        )
        for filter_func in self.chat_tool_filters:
            await filter_func(chat, context)

    def shutdown(self):
        if len(self.shutdown_handlers) > 0:
            _dbg(f"running {len(self.shutdown_handlers)} shutdown handlers...")
            for handler in self.shutdown_handlers:
                handler()

    def exit(self, exit_code: int = 0):
        self.shutdown()
        _dbg(f"exit({exit_code})")
        sys.exit(exit_code)

    def create_chat_with_tools(self, chat: Dict[str, Any], use_tools: str = "all") -> Dict[str, Any]:
        # Inject global tools if present
        current_chat = chat.copy()
        tools = current_chat.get("tools")
        if tools is None:
            tools = current_chat["tools"] = []
        if self.tool_definitions and len(tools) == 0:
            include_all_tools = use_tools == "all"
            only_tools_list = use_tools.split(",")

            if include_all_tools or len(only_tools_list) > 0:
                if "tools" not in current_chat:
                    current_chat["tools"] = []

                _dbg(
                    f"create_chat_with_tools: all_tools:{include_all_tools}, only_tools:{only_tools_list}, chat tools: "
                    + str(len(current_chat["tools"]))
                )

                existing_tools = {t["function"]["name"] for t in current_chat["tools"]}
                for tool_def in self.tool_definitions:
                    name = tool_def["function"]["name"]
                    if name not in existing_tools and (include_all_tools or name in only_tools_list):
                        current_chat["tools"].append(tool_def)
        return current_chat

    def get_tool_definition(self, name: str) -> Optional[Dict[str, Any]]:
        for tool_def in self.tool_definitions:
            if tool_def["function"]["name"] == name:
                return tool_def
        return None


def handler_name(handler):
    if hasattr(handler, "__name__"):
        return handler.__name__
    return "unknown"


class ExtensionContext:
    def __init__(self, app: AppExtensions, path: str):
        self.app = app
        self.config = app.config
        self.cli_args = app.cli_args
        self.extra_args = app.extra_args
        self.error_auth_required = app.error_auth_required
        self.path = path
        self.name = os.path.basename(path)
        if self.name.endswith(".py"):
            self.name = self.name[:-3]
        self.ext_prefix = f"/ext/{self.name}"
        self.MOCK = MOCK
        self.MOCK_DIR = MOCK_DIR
        self.debug = DEBUG
        self.verbose = g_verbose
        self.aspect_ratios = app.aspect_ratios
        self.request_args = app.request_args
        self.sessions = app.sessions
        self.oauth_states = app.oauth_states
        self.disabled = False

    def add_auth_provider(self, auth_provider: AuthProvider) -> None:
        """Add an authentication provider."""
        self.app.add_auth_provider(auth_provider)
        self.log(f"Added Auth Provider: {auth_provider.__class__.__name__}, Authentication is now enabled")

    def get_session(self, request: web.Request) -> Optional[Dict[str, Any]]:
        return self.app.get_session(request)

    def get_username(self, request: web.Request) -> Optional[str]:
        return self.app.get_username(request)

    def check_auth(self, request: web.Request) -> Tuple[bool, Optional[Dict[str, Any]]]:
        return self.app.check_auth(request)

    def set_allowed_directories(
        self, directories: List[Annotated[str, "List of absolute paths that are allowed to be accessed."]]
    ) -> None:
        """Set the list of allowed directories."""
        self.app.set_allowed_directories(directories)

    def add_allowed_directory(self, path: str) -> None:
        """Add an allowed directory."""
        self.app.add_allowed_directory(path)

    def get_allowed_directories(self) -> List[str]:
        """Get the list of allowed directories."""
        return self.app.get_allowed_directories()

    def chat_to_prompt(self, chat: Dict[str, Any]) -> str:
        return chat_to_prompt(chat)

    def chat_to_system_prompt(self, chat: Dict[str, Any]) -> str:
        return chat_to_system_prompt(chat)

    def chat_response_to_message(self, response: Dict[str, Any]) -> Dict[str, Any]:
        return chat_response_to_message(response)

    def last_user_prompt(self, chat: Dict[str, Any]) -> str:
        return last_user_prompt(chat)

    def to_file_info(
        self, chat: Dict[str, Any], info: Optional[Dict[str, Any]] = None, response: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        return to_file_info(chat, info=info, response=response)

    def save_image_to_cache(
        self, base64_data: Union[str, bytes], filename: str, image_info: Dict[str, Any], ignore_info: bool = False
    ) -> Tuple[str, Optional[Dict[str, Any]]]:
        return save_image_to_cache(base64_data, filename, image_info, ignore_info=ignore_info)

    def save_bytes_to_cache(
        self, bytes_data: Union[str, bytes], filename: str, file_info: Optional[Dict[str, Any]]
    ) -> Tuple[str, Optional[Dict[str, Any]]]:
        return save_bytes_to_cache(bytes_data, filename, file_info)

    def text_from_file(self, path: str) -> str:
        return text_from_file(path)

    def json_from_file(self, path: str) -> Any:
        return json_from_file(path)

    def download_file(self, url: str) -> Tuple[bytes, Dict[str, Any]]:
        return download_file(url)

    def session_download_file(self, session: aiohttp.ClientSession, url: str) -> Tuple[bytes, Dict[str, Any]]:
        return session_download_file(session, url)

    def read_binary_file(self, url: str) -> Tuple[bytes, Dict[str, Any]]:
        return read_binary_file(url)

    def log(self, message: Any):
        if self.verbose:
            print(f"[{self.name}] {message}", flush=True)
        return message

    def log_json(self, obj: Any):
        if self.verbose:
            print(f"[{self.name}] {json.dumps(obj, indent=2)}", flush=True)
        return obj

    def dbg(self, message: Any):
        if self.debug:
            print(f"DEBUG [{self.name}]: {message}", flush=True)

    def err(self, message: str, e: Exception):
        print(f"ERROR [{self.name}]: {message}", e)
        if self.verbose:
            print(traceback.format_exc(), flush=True)

    def error_message(self, e: Exception) -> str:
        return to_error_message(e)

    def error_response(self, e: Exception, stacktrace: bool = False) -> Dict[str, Any]:
        return to_error_response(e, stacktrace=stacktrace)

    def create_error_response(self, message, error_code="Error", stack_trace=None):
        return create_error_response(message, error_code, stack_trace)

    def add_provider(self, provider: Any):
        self.log(f"Registered provider: {provider.__name__}")
        self.app.all_providers.append(provider)

    def register_ui_extension(self, index: str):
        path = os.path.join(self.ext_prefix, index)
        self.log(f"Registered UI extension: {path}")
        self.app.ui_extensions.append({"id": self.name, "path": path})

    def register_chat_request_filter(self, handler: Callable):
        self.log(f"Registered chat request filter: {handler_name(handler)}")
        self.app.chat_request_filters.append(handler)

    def register_chat_tool_filter(self, handler: Callable):
        self.log(f"Registered chat tool filter: {handler_name(handler)}")
        self.app.chat_tool_filters.append(handler)

    def register_chat_response_filter(self, handler: Callable):
        self.log(f"Registered chat response filter: {handler_name(handler)}")
        self.app.chat_response_filters.append(handler)

    def register_chat_error_filter(self, handler: Callable):
        self.log(f"Registered chat error filter: {handler_name(handler)}")
        self.app.chat_error_filters.append(handler)

    def register_cache_saved_filter(self, handler: Callable):
        self.log(f"Registered cache saved filter: {handler_name(handler)}")
        self.app.cache_saved_filters.append(handler)

    def register_shutdown_handler(self, handler: Callable):
        self.log(f"Registered shutdown handler: {handler_name(handler)}")
        self.app.shutdown_handlers.append(handler)

    def add_static_files(self, ext_dir: str):
        self.log(f"Registered static files: {ext_dir}")

        async def serve_static(request):
            path = request.match_info["path"]
            file_path = os.path.join(ext_dir, path)
            if os.path.exists(file_path):
                return web.FileResponse(file_path)
            return web.Response(status=404)

        self.app.server_add_get.append((os.path.join(self.ext_prefix, "{path:.*}"), serve_static, {}))

    def web_path(self, method: str, path: str) -> str:
        full_path = os.path.join(self.ext_prefix, path) if path else self.ext_prefix
        self.dbg(f"Registered {method:<6} {full_path}")
        return full_path

    def add_get(self, path: str, handler: Callable, **kwargs: Any):
        self.app.server_add_get.append((self.web_path("GET", path), handler, kwargs))

    def add_post(self, path: str, handler: Callable, **kwargs: Any):
        self.app.server_add_post.append((self.web_path("POST", path), handler, kwargs))

    def add_put(self, path: str, handler: Callable, **kwargs: Any):
        self.app.server_add_put.append((self.web_path("PUT", path), handler, kwargs))

    def add_delete(self, path: str, handler: Callable, **kwargs: Any):
        self.app.server_add_delete.append((self.web_path("DELETE", path), handler, kwargs))

    def add_patch(self, path: str, handler: Callable, **kwargs: Any):
        self.app.server_add_patch.append((self.web_path("PATCH", path), handler, kwargs))

    def add_importmaps(self, dict: Dict[str, str]):
        self.app.import_maps.update(dict)

    def add_index_header(self, html: str):
        self.app.index_headers.append(html)

    def add_index_footer(self, html: str):
        self.app.index_footers.append(html)

    def get_home_path(self, name: str = "") -> str:
        return home_llms_path(name)

    def get_config(self) -> Optional[Dict[str, Any]]:
        return g_config

    def get_cache_path(self, path: str = "") -> str:
        return get_cache_path(path)

    def get_file_mime_type(self, filename: str) -> str:
        return get_file_mime_type(filename)

    def chat_request(
        self,
        template: Optional[str] = None,
        text: Optional[str] = None,
        model: Optional[str] = None,
        system_prompt: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self.app.chat_request(template=template, text=text, model=model, system_prompt=system_prompt)

    async def chat_completion(self, chat: Dict[str, Any], context: Optional[Dict[str, Any]] = None) -> Any:
        return await self.app.chat_completion(chat, context=context)

    def get_providers(self) -> Dict[str, Any]:
        return g_handlers

    def get_provider(self, name: str) -> Optional[Any]:
        return g_handlers.get(name)

    def sanitize_tool_def(self, tool_def: Dict[str, Any]) -> Dict[str, Any]:
        """
        Merge $defs parameter into tool_def property to reduce client/server complexity
        """
        # parameters = {
        #     "$defs": {
        #         "AspectRatio": {
        #             "description": "Supported aspect ratios for image generation.",
        #             "enum": [
        #                 "1:1",
        #                 "2:3",
        #                 "16:9"
        #             ],
        #             "type": "string"
        #         }
        #     },
        #     "properties": {
        #         "prompt": {
        #             "type": "string"
        #         },
        #         "model": {
        #             "default": "gemini-2.5-flash-image",
        #             "type": "string"
        #         },
        #         "aspect_ratio": {
        #             "$ref": "#/$defs/AspectRatio",
        #             "default": "1:1"
        #         }
        #     },
        #     "required": [
        #         "prompt"
        #     ],
        #     "type": "object"
        # }
        type = tool_def.get("type")
        if type == "function":
            func_def = tool_def.get("function", {})
            parameters = func_def.get("parameters", {})
            defs = parameters.get("$defs", {})
            properties = parameters.get("properties", {})
            for _, prop_def in properties.items():
                if "$ref" in prop_def:
                    ref = prop_def["$ref"]
                    if ref.startswith("#/$defs/"):
                        def_name = ref.replace("#/$defs/", "")
                        if def_name in defs:
                            prop_def.update(defs[def_name])
                            del prop_def["$ref"]
            if "$defs" in parameters:
                del parameters["$defs"]
        return tool_def

    def register_tool(self, func: Callable, tool_def: Optional[Dict[str, Any]] = None, group: Optional[str] = None):
        if tool_def is None:
            tool_def = function_to_tool_definition(func)

        name = tool_def["function"]["name"]
        if name in self.app.tools:
            self.log(f"Overriding existing tool: {name}")
            self.app.tool_definitions = [t for t in self.app.tool_definitions if t["function"]["name"] != name]
            for g_tools in self.app.tool_groups.values():
                if name in g_tools:
                    g_tools.remove(name)
        else:
            self.log(f"Registered tool: {name}")

        self.app.tools[name] = func
        self.app.tool_definitions.append(self.sanitize_tool_def(tool_def))
        if not group:
            group = "custom"
        if group not in self.app.tool_groups:
            self.app.tool_groups[group] = []
        self.app.tool_groups[group].append(name)

    def get_tool_definition(self, name: str) -> Optional[Dict[str, Any]]:
        return self.app.get_tool_definition(name)

    def group_resources(self, resources: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
        return group_resources(resources)

    def check_auth(self, request: web.Request) -> Tuple[bool, Optional[Dict[str, Any]]]:
        return self.app.check_auth(request)

    def get_session(self, request: web.Request) -> Optional[Dict[str, Any]]:
        return self.app.get_session(request)

    def get_username(self, request: web.Request) -> Optional[str]:
        return self.app.get_username(request)

    def get_user_path(self, username: Optional[str] = None) -> str:
        return self.app.get_user_path(username)

    def context_to_username(self, context: Optional[Dict[str, Any]]) -> Optional[str]:
        if context and "request" in context:
            return self.get_username(context["request"])
        return None

    def should_cancel_thread(self, context: Dict[str, Any]) -> bool:
        return should_cancel_thread(context)

    def cache_message_inline_data(self, message: Dict[str, Any]):
        return cache_message_inline_data(message)

    async def exec_tool(self, name: str, args: Dict[str, Any]) -> Tuple[Optional[str], List[Dict[str, Any]]]:
        return await g_exec_tool(name, args)

    def tool_result(
        self, result: Any, function_name: Optional[str] = None, function_args: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        return g_tool_result(result, function_name, function_args)

    def tool_result_part(
        self,
        result: Dict[str, Any],
        function_name: Optional[str] = None,
        function_args: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        return tool_result_part(result, function_name, function_args)

    def to_content(self, result: Any) -> str:
        return to_content(result)

    def create_chat_with_tools(self, chat: Dict[str, Any], use_tools: str = "all") -> Dict[str, Any]:
        return self.app.create_chat_with_tools(chat, use_tools)

    def chat_to_aspect_ratio(self, chat: Dict[str, Any]) -> str:
        return chat_to_aspect_ratio(chat)


def get_extensions_path():
    return os.getenv("LLMS_EXTENSIONS_DIR", home_llms_path("extensions"))


def get_disabled_extensions():
    ret = DISABLE_EXTENSIONS.copy()
    if g_config:
        for ext in g_config.get("disable_extensions", []):
            if ext not in ret:
                ret.append(ext)
    return ret


def get_extensions_dirs():
    """
    Returns a list of extension directories.
    """
    extensions_path = get_extensions_path()
    os.makedirs(extensions_path, exist_ok=True)

    # allow overriding builtin extensions
    override_extensions = []
    if os.path.exists(extensions_path):
        override_extensions = os.listdir(extensions_path)

    ret = []
    disabled_extensions = get_disabled_extensions()

    builtin_extensions_dir = _ROOT / "extensions"
    if not os.path.exists(builtin_extensions_dir):
        # look for local ./extensions dir from script
        builtin_extensions_dir = os.path.join(os.path.dirname(__file__), "extensions")

    _dbg(f"Loading extensions from {builtin_extensions_dir}")
    if os.path.exists(builtin_extensions_dir):
        for item in os.listdir(builtin_extensions_dir):
            if os.path.isdir(os.path.join(builtin_extensions_dir, item)):
                if item in override_extensions:
                    continue
                if item in disabled_extensions:
                    continue
                ret.append(os.path.join(builtin_extensions_dir, item))

    if os.path.exists(extensions_path):
        for item in os.listdir(extensions_path):
            if os.path.isdir(os.path.join(extensions_path, item)):
                if item in disabled_extensions:
                    continue
                ret.append(os.path.join(extensions_path, item))

    return ret


def verify_root_path():
    global _ROOT
    _ROOT = os.getenv("LLMS_ROOT", resolve_root())
    if not _ROOT:
        print("Resource root not found")
        exit(1)


def init_extensions(parser):
    """
    Programmatic entry point for the CLI.
    Example: cli("ls minimax")
    """
    verify_root_path()

    """
    Initializes extensions by loading their __init__.py files and calling the __parser__ function if it exists.
    """
    for item_path in get_extensions_dirs():
        item = os.path.basename(item_path)

        if os.path.isdir(item_path):
            try:
                # check for __parser__ function if exists in __init.__.py and call it with parser
                init_file = os.path.join(item_path, "__init__.py")
                if os.path.exists(init_file):
                    spec = importlib.util.spec_from_file_location(item, init_file)
                    if spec and spec.loader:
                        module = importlib.util.module_from_spec(spec)
                        sys.modules[item] = module
                        spec.loader.exec_module(module)

                        parser_func = getattr(module, "__parser__", None)
                        if callable(parser_func):
                            parser_func(parser)
                            _log(f"Extension {item} parser loaded")
            except Exception as e:
                _err(f"Failed to load extension {item} parser", e)


def install_extensions():
    """
    Scans ensure ~/.llms/extensions/ for directories with __init__.py and loads them as extensions.
    Calls the `__install__(ctx)` function in the extension module.
    """

    extension_dirs = get_extensions_dirs()
    ext_count = len(list(extension_dirs))
    if ext_count == 0:
        _log("No extensions found")
        return

    disabled_extensions = get_disabled_extensions()
    if len(disabled_extensions) > 0:
        _log(f"Disabled extensions: {', '.join(disabled_extensions)}")

    _log(f"Installing {ext_count} extension{'' if ext_count == 1 else 's'}...")

    extensions = []

    for item_path in extension_dirs:
        item = os.path.basename(item_path)

        if os.path.isdir(item_path):
            sys.path.append(item_path)
            try:
                ctx = ExtensionContext(g_app, item_path)
                module = None
                init_file = os.path.join(item_path, "__init__.py")
                if os.path.exists(init_file):
                    spec = importlib.util.spec_from_file_location(item, init_file)
                    if spec and spec.loader:
                        module = importlib.util.module_from_spec(spec)
                        sys.modules[item] = module
                        spec.loader.exec_module(module)

                        install_func = getattr(module, "__install__", None)
                        if callable(install_func):
                            install_func(ctx)
                            _log(f"Extension {item} installed")
                        else:
                            _dbg(f"Extension {item} has no __install__ function")
                    else:
                        _dbg(f"Extension {item} has no __init__.py")
                else:
                    _dbg(f"Extension {init_file} not found")

                if ctx.disabled:
                    _log(f"Extension {item} was disabled")
                    continue

                # if ui folder exists, serve as static files at /ext/{item}/
                ui_path = os.path.join(item_path, "ui")
                if os.path.exists(ui_path):
                    ctx.add_static_files(ui_path)

                # Register UI extension if index.mjs exists (/ext/{item}/index.mjs)
                if os.path.exists(os.path.join(ui_path, "index.mjs")):
                    ctx.register_ui_extension("index.mjs")

                # include __load__ and __run__ hooks if they exist
                load_func = getattr(module, "__load__", None) if module else None
                if callable(load_func) and not inspect.iscoroutinefunction(load_func):
                    _log(f"Warning: Extension {item} __load__ must be async")
                    load_func = None

                run_func = getattr(module, "__run__", None) if module else None
                if callable(run_func) and inspect.iscoroutinefunction(run_func):
                    _log(f"Warning: Extension {item} __run__ must be sync")
                    run_func = None

                extensions.append({"name": item, "module": module, "ctx": ctx, "load": load_func, "run": run_func})
            except Exception as e:
                _err(f"Failed to install extension {item}", e)
        else:
            _dbg(f"Extension {item} not found: {item_path} is not a directory {os.path.exists(item_path)}")

    return extensions


async def load_extensions():
    """
    Calls the `__load__(ctx)` async function in all installed extensions concurrently.
    """
    tasks = []
    for ext in g_app.extensions:
        if ext.get("load"):
            task = ext["load"](ext["ctx"])
            tasks.append({"name": ext["name"], "task": task})

    if len(tasks) > 0:
        _log(f"Loading {len(tasks)} extensions...")
        results = await asyncio.gather(*[t["task"] for t in tasks], return_exceptions=True)
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                # Gather returns results in order corresponding to tasks
                extension = tasks[i]
                _err(f"Failed to load extension {extension['name']}", result)


def run_extension_cli():
    """
    Run the CLI for an extension.
    """
    for item_path in get_extensions_dirs():
        item = os.path.basename(item_path)

        if os.path.isdir(item_path):
            init_file = os.path.join(item_path, "__init__.py")
            if os.path.exists(init_file):
                ctx = ExtensionContext(g_app, item_path)
                try:
                    spec = importlib.util.spec_from_file_location(item, init_file)
                    if spec and spec.loader:
                        module = importlib.util.module_from_spec(spec)
                        sys.modules[item] = module
                        spec.loader.exec_module(module)

                    # Check for __run__ function if exists in __init__.py and call it with ctx
                    run_func = getattr(module, "__run__", None)
                    if callable(run_func):
                        _log(f"Running extension {item}...")
                        handled = run_func(ctx)
                        return handled

                except Exception as e:
                    _err(f"Failed to run extension {item}", e)
                    return False


def create_arg_parser():
    parser = argparse.ArgumentParser(description=f"llms v{VERSION}")
    parser.add_argument("--config", default=None, help="Path to config file", metavar="FILE")
    parser.add_argument("--providers", default=None, help="Path to models.dev providers file", metavar="FILE")
    parser.add_argument("-m", "--model", default=None, help="Model to use")

    parser.add_argument("--chat", default=None, help="OpenAI Chat Completion Request to send", metavar="REQUEST")
    parser.add_argument(
        "-s", "--system", default=None, help="System prompt to use for chat completion", metavar="PROMPT"
    )
    parser.add_argument(
        "--tools", default=None, help="Tools to use for chat completion (all|none|<tool>,<tool>...)", metavar="TOOLS"
    )
    parser.add_argument("--image", default=None, help="Image input to use in chat completion")
    parser.add_argument("--audio", default=None, help="Audio input to use in chat completion")
    parser.add_argument("--file", default=None, help="File input to use in chat completion")
    parser.add_argument("--out", default=None, help="Image or Video Generation Request", metavar="MODALITY")
    parser.add_argument(
        "--args",
        default=None,
        help='URL-encoded parameters to add to chat request (e.g. "temperature=0.7&seed=111")',
        metavar="PARAMS",
    )
    parser.add_argument("--raw", action="store_true", help="Return raw AI JSON response")

    parser.add_argument(
        "--list", action="store_true", help="Show list of enabled providers and their models (alias ls provider?)"
    )
    parser.add_argument("--check", default=None, help="Check validity of models for a provider", metavar="PROVIDER")

    parser.add_argument(
        "--serve", default=None, help="Port to start an OpenAI Chat compatible server on", metavar="PORT"
    )

    parser.add_argument("--enable", default=None, help="Enable a provider", metavar="PROVIDER")
    parser.add_argument("--disable", default=None, help="Disable a provider", metavar="PROVIDER")
    parser.add_argument("--default", default=None, help="Configure the default model to use", metavar="MODEL")

    parser.add_argument("--init", action="store_true", help="Create a default llms.json")
    parser.add_argument("--update-providers", action="store_true", help="Update local models.dev providers.json")

    parser.add_argument("--logprefix", default="", help="Prefix used in log messages", metavar="PREFIX")
    parser.add_argument("--verbose", action="store_true", help="Verbose output")

    parser.add_argument(
        "--add",
        nargs="?",
        const="ls",
        default=None,
        help="Install an extension (lists available extensions if no name provided)",
        metavar="EXTENSION",
    )
    parser.add_argument(
        "--remove",
        nargs="?",
        const="ls",
        default=None,
        help="Remove an extension (lists installed extensions if no name provided)",
        metavar="EXTENSION",
    )

    parser.add_argument(
        "--update",
        nargs="?",
        const="ls",
        default=None,
        help="Update an extension (use 'all' to update all extensions)",
        metavar="EXTENSION",
    )
    return parser


def cli_exec(cli_args, extra_args):
    global _ROOT, g_verbose, g_default_model, g_logprefix, g_providers, g_config, g_config_path, g_app

    verify_root_path()

    g_app = AppExtensions(cli_args, extra_args)

    # Check for verbose mode from CLI argument or environment variables
    verbose_env = os.getenv("VERBOSE", "").lower()
    if cli_args.verbose or verbose_env in ("1", "true"):
        g_verbose = True
        # printdump(cli_args)
    if cli_args.model:
        g_default_model = cli_args.model
    if cli_args.logprefix:
        g_logprefix = cli_args.logprefix

    home_config_path = home_llms_path("llms.json")
    home_providers_path = home_llms_path("providers.json")
    home_providers_extra_path = home_llms_path("providers-extra.json")

    if cli_args.init:
        if os.path.exists(home_config_path):
            print(f"llms.json already exists at {home_config_path}")
        else:
            asyncio.run(save_default_config(home_config_path))
            print(f"Created default config at {home_config_path}")

        if os.path.exists(home_providers_path):
            print(f"providers.json already exists at {home_providers_path}")
        else:
            asyncio.run(save_text_url(github_url("providers.json"), home_providers_path))
            print(f"Created default providers config at {home_providers_path}")

        if os.path.exists(home_providers_extra_path):
            print(f"providers-extra.json already exists at {home_providers_extra_path}")
        else:
            asyncio.run(save_text_url(github_url("providers-extra.json"), home_providers_extra_path))
            print(f"Created default extra providers config at {home_providers_extra_path}")
        return ExitCode.SUCCESS

    if cli_args.providers:
        if not os.path.exists(cli_args.providers):
            print(f"providers.json not found at {cli_args.providers}")
            return ExitCode.FAILED
        g_providers = json.loads(text_from_file(cli_args.providers))

    if cli_args.config:
        # read contents
        g_config_path = cli_args.config
        with open(g_config_path, encoding="utf-8") as f:
            config_json = f.read()
            g_config = load_config_json(config_json)

        config_dir = os.path.dirname(g_config_path)

        if not g_providers and os.path.exists(os.path.join(config_dir, "providers.json")):
            g_providers = json.loads(text_from_file(os.path.join(config_dir, "providers.json")))

    else:
        # ensure llms.json and providers.json exist in home directory
        asyncio.run(save_home_configs())
        g_config_path = home_config_path
        g_config = load_config_json(text_from_file(g_config_path))

    g_app.set_config(g_config)

    if not g_providers:
        g_providers = json.loads(text_from_file(home_providers_path))

    if cli_args.update_providers:
        asyncio.run(update_providers(home_providers_path))
        print(f"Updated {home_providers_path}")
        return ExitCode.SUCCESS

    # if home_providers_path is older than 1 day, update providers list
    if (
        os.path.exists(home_providers_path)
        and (time.time() - os.path.getmtime(home_providers_path)) > 86400
        and os.getenv("LLMS_DISABLE_UPDATE", "") != "1"
    ):
        try:
            asyncio.run(update_providers(home_providers_path))
            _log(f"Updated {home_providers_path}")
        except Exception as e:
            _err("Failed to update providers", e)

    if cli_args.add is not None:
        if cli_args.add == "ls":

            async def list_extensions():
                print("\nAvailable extensions:")
                text = await get_text("https://api.github.com/orgs/llmspy/repos?per_page=100&sort=updated")
                repos = json.loads(text)
                max_name_length = 0
                for repo in repos:
                    max_name_length = max(max_name_length, len(repo["name"]))

                for repo in repos:
                    print(f"  {repo['name']:<{max_name_length + 2}} {repo['description']}")

                print("\nUsage:")
                print("  llms --add <extension>")
                print("  llms --add <github-user>/<repo>")

            asyncio.run(list_extensions())
            return ExitCode.SUCCESS

        async def install_extension(name):
            # Determine git URL and target directory name
            if "/" in name:
                git_url = f"https://github.com/{name}"
                target_name = name.split("/")[-1]
            else:
                git_url = f"https://github.com/llmspy/{name}"
                target_name = name

            # check extension is not already installed
            extensions_path = get_extensions_path()
            target_path = os.path.join(extensions_path, target_name)

            if os.path.exists(target_path):
                print(f"Extension {target_name} is already installed at {target_path}")
                return

            print(f"Installing extension: {name}")
            print(f"Cloning from {git_url} to {target_path}...")

            try:
                subprocess.run(["git", "clone", git_url, target_path], check=True)

                # Check for requirements.txt
                requirements_path = os.path.join(target_path, "requirements.txt")
                if os.path.exists(requirements_path):
                    print(f"Installing dependencies from {requirements_path}...")

                    # Check if uv is installed
                    has_uv = False
                    try:
                        subprocess.run(
                            ["uv", "--version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True
                        )
                        has_uv = True
                    except (subprocess.CalledProcessError, FileNotFoundError):
                        pass

                    if has_uv:
                        subprocess.run(
                            ["uv", "pip", "install", "-p", sys.executable, "-r", "requirements.txt"],
                            cwd=target_path,
                            check=True,
                        )
                    else:
                        subprocess.run(
                            [sys.executable, "-m", "pip", "install", "-r", "requirements.txt"],
                            cwd=target_path,
                            check=True,
                        )
                    print("Dependencies installed successfully.")

                print(f"Extension {target_name} installed successfully.")

            except subprocess.CalledProcessError as e:
                print(f"Failed to install extension: {e}")
                # cleanup if clone failed but directory was created (unlikely with simple git clone but good practice)
                if os.path.exists(target_path) and not os.listdir(target_path):
                    os.rmdir(target_path)

        asyncio.run(install_extension(cli_args.add))
        return ExitCode.SUCCESS

    if cli_args.remove is not None:
        if cli_args.remove == "ls":
            # List installed extensions
            extensions_path = get_extensions_path()
            extensions = os.listdir(extensions_path)
            if len(extensions) == 0:
                print("No extensions installed.")
                return ExitCode.SUCCESS
            print("Installed extensions:")
            for extension in extensions:
                print(f"  {extension}")
            return ExitCode.SUCCESS
        # Remove an extension
        extension_name = cli_args.remove
        extensions_path = get_extensions_path()
        target_path = os.path.join(extensions_path, extension_name)

        if not os.path.exists(target_path):
            print(f"Extension {extension_name} not found at {target_path}")
            return ExitCode.FAILED

        print(f"Removing extension: {extension_name}...")
        try:
            shutil.rmtree(target_path)
            print(f"Extension {extension_name} removed successfully.")
        except Exception as e:
            print(f"Failed to remove extension: {e}")
            return ExitCode.FAILED

        return ExitCode.SUCCESS

    if cli_args.update:
        if cli_args.update == "ls":
            # List installed extensions
            extensions_path = get_extensions_path()
            extensions = os.listdir(extensions_path)
            if len(extensions) == 0:
                print("No extensions installed.")
                return ExitCode.SUCCESS
            print("Installed extensions:")
            for extension in extensions:
                print(f"  {extension}")

            print("\nUsage:")
            print("  llms --update <extension>")
            print("  llms --update all")
            return ExitCode.SUCCESS

        async def update_extensions(extension_name):
            extensions_path = get_extensions_path()
            for extension in os.listdir(extensions_path):
                extension_path = os.path.join(extensions_path, extension)
                if os.path.isdir(extension_path):
                    if extension_name != "all" and extension != extension_name:
                        continue
                    result = subprocess.run(["git", "pull"], cwd=extension_path, capture_output=True)
                    if result.returncode != 0:
                        print(f"Failed to update extension {extension}: {result.stderr.decode('utf-8')}")
                        continue
                    print(f"Updated extension {extension}")
                    _log(result.stdout.decode("utf-8"))

        asyncio.run(update_extensions(cli_args.update))
        return ExitCode.SUCCESS

    g_app.add_allowed_directory(os.getcwd())  # add current directory
    g_app.add_allowed_directory(home_llms_path(".agent"))  # info for agents, e.g: skills
    g_app.add_allowed_directory(tempfile.gettempdir())  # add temp directory

    g_app.extensions = install_extensions()

    # Use a persistent event loop to ensure async connections (like MCP)
    # established in load_extensions() remain active during cli_chat()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    loop.run_until_complete(reload_providers())
    loop.run_until_complete(load_extensions())
    g_app.loaded = True

    # print names
    _log(f"enabled providers: {', '.join(g_handlers.keys())}")

    filter_list = []
    if len(extra_args) > 0:
        arg = extra_args[0]
        if arg == "ls":
            cli_args.list = True
            if len(extra_args) > 1:
                filter_list = extra_args[1:]

    if cli_args.list:
        # Show list of enabled providers and their models
        enabled = []
        provider_count = 0
        model_count = 0

        max_model_length = 0
        for name, provider in g_handlers.items():
            if len(filter_list) > 0 and name not in filter_list:
                continue
            for model in provider.models:
                max_model_length = max(max_model_length, len(model))

        for name, provider in g_handlers.items():
            if len(filter_list) > 0 and name not in filter_list:
                continue
            provider_count += 1
            print(f"{name}:")
            enabled.append(name)
            for model in provider.models:
                model_count += 1
                model_cost_info = None
                if "cost" in provider.models[model]:
                    model_cost = provider.models[model]["cost"]
                    if "input" in model_cost and "output" in model_cost:
                        if model_cost["input"] == 0 and model_cost["output"] == 0:
                            model_cost_info = "      0"
                        else:
                            model_cost_info = f"{model_cost['input']:5} / {model_cost['output']}"
                print(f"  {model:{max_model_length}} {model_cost_info or ''}")

        print(f"\n{model_count} models available from {provider_count} providers")

        print_status()
        return ExitCode.SUCCESS

    if cli_args.check is not None:
        # Check validity of models for a provider
        provider_name = cli_args.check
        model_names = extra_args if len(extra_args) > 0 else None
        provider_name = cli_args.check
        model_names = extra_args if len(extra_args) > 0 else None
        loop.run_until_complete(check_models(provider_name, model_names))
        return ExitCode.SUCCESS

    if cli_args.serve is not None:
        # Disable inactive providers and save to config before starting server
        all_providers = g_config["providers"].keys()
        enabled_providers = list(g_handlers.keys())
        disable_providers = []
        for provider in all_providers:
            provider_config = g_config["providers"][provider]
            if provider not in enabled_providers and "enabled" in provider_config and provider_config["enabled"]:
                provider_config["enabled"] = False
                disable_providers.append(provider)

        if len(disable_providers) > 0:
            _log(f"Disabled unavailable providers: {', '.join(disable_providers)}")
            save_config(g_config)

        # Start server
        port = int(cli_args.serve)

        # Validate auth configuration if enabled
        if g_config.get("auth", {}).get("enabled", False):
            print("ERROR: GitHub Authentication has moved to the github_auth extension.")
            print("Please remove the auth configuration from llms.json.")
            print("Learn more: https://llmspy.org/docs/deployment/github-oauth")
            return ExitCode.FAILED

        client_max_size = g_config.get("limits", {}).get(
            "client_max_size", 20 * 1024 * 1024
        )  # 20MB max request size (to handle base64 encoding overhead)
        _log(f"client_max_size set to {client_max_size} bytes ({client_max_size / 1024 / 1024:.1f}MB)")
        app = web.Application(client_max_size=client_max_size)

        async def chat_handler(request):
            # Check authentication if enabled
            is_authenticated, user_data = g_app.check_auth(request)
            if not is_authenticated:
                return web.json_response(g_app.error_auth_required, status=401)

            try:
                chat = await request.json()
                context = {"chat": chat, "request": request, "user": g_app.get_username(request)}
                metadata = chat.get("metadata", {})
                context["threadId"] = metadata.get("threadId", None)
                context["tools"] = metadata.get("tools", "all")
                response = await g_app.chat_completion(chat, context)
                return web.json_response(response)
            except Exception as e:
                return web.json_response(to_error_response(e), status=500)

        app.router.add_post("/v1/chat/completions", chat_handler)

        async def active_models_handler(request):
            return web.json_response(get_active_models())

        app.router.add_get("/models", active_models_handler)

        async def active_providers_handler(request):
            return web.json_response(api_providers())

        app.router.add_get("/providers", active_providers_handler)

        async def status_handler(request):
            enabled, disabled = provider_status()
            return web.json_response(
                {
                    "all": list(g_config["providers"].keys()),
                    "enabled": enabled,
                    "disabled": disabled,
                }
            )

        app.router.add_get("/status", status_handler)

        async def provider_handler(request):
            provider = request.match_info.get("provider", "")
            data = await request.json()
            msg = None
            if provider:
                if data.get("enable", False):
                    provider_config, msg = enable_provider(provider)
                    _log(f"Enabled provider {provider} {msg}")
                    if not msg:
                        await load_llms()
                elif data.get("disable", False):
                    disable_provider(provider)
                    _log(f"Disabled provider {provider}")
            enabled, disabled = provider_status()
            return web.json_response(
                {
                    "enabled": enabled,
                    "disabled": disabled,
                    "feedback": msg or "",
                }
            )

        app.router.add_post("/providers/{provider}", provider_handler)

        async def upload_handler(request):
            # Check authentication if enabled
            is_authenticated, user_data = g_app.check_auth(request)
            if not is_authenticated:
                return web.json_response(g_app.error_auth_required, status=401)

            reader = await request.multipart()

            # Read first file field
            field = await reader.next()
            while field and field.name != "file":
                field = await reader.next()

            if not field:
                return web.json_response(create_error_response("No file provided"), status=400)

            filename = field.filename or "file"
            content = await field.read()
            mimetype = get_file_mime_type(filename)

            # If image, resize if needed
            if mimetype.startswith("image/"):
                content, mimetype = convert_image_if_needed(content, mimetype)

            # Calculate SHA256
            sha256_hash = hashlib.sha256(content).hexdigest()
            ext = filename.rsplit(".", 1)[1] if "." in filename else ""
            if not ext:
                ext = mimetypes.guess_extension(mimetype) or ""
                if ext.startswith("."):
                    ext = ext[1:]

            if not ext:
                ext = "bin"

            save_filename = f"{sha256_hash}.{ext}" if ext else sha256_hash

            # Use first 2 chars for subdir to avoid too many files in one dir
            subdir = sha256_hash[:2]
            relative_path = f"{subdir}/{save_filename}"
            full_path = get_cache_path(relative_path)

            # if file and its .info.json already exists, return it
            info_path = os.path.splitext(full_path)[0] + ".info.json"
            if os.path.exists(full_path) and os.path.exists(info_path):
                return web.json_response(json_from_file(info_path))

            os.makedirs(os.path.dirname(full_path), exist_ok=True)

            with open(full_path, "wb") as f:
                f.write(content)

            url = f"/~cache/{relative_path}"
            response_data = {
                "date": int(time.time()),
                "url": url,
                "size": len(content),
                "type": mimetype,
                "name": filename,
            }

            # If image, get dimensions
            if HAS_PIL and mimetype.startswith("image/"):
                try:
                    with Image.open(BytesIO(content)) as img:
                        response_data["width"] = img.width
                        response_data["height"] = img.height
                except Exception:
                    pass

            # Save metadata
            info_path = os.path.splitext(full_path)[0] + ".info.json"
            with open(info_path, "w") as f:
                json.dump(response_data, f)

            g_app.on_cache_saved_filters({"url": url, "info": response_data})

            return web.json_response(response_data)

        app.router.add_post("/upload", upload_handler)

        async def extensions_handler(request):
            return web.json_response(g_app.ui_extensions)

        app.router.add_get("/ext", extensions_handler)

        async def cache_handler(request):
            path = request.match_info["tail"]
            full_path = get_cache_path(path)
            info_path = os.path.splitext(full_path)[0] + ".info.json"

            if "info" in request.query:
                if not os.path.exists(info_path):
                    return web.Response(text="404: Not Found", status=404)

                # Check for directory traversal for info path
                try:
                    cache_root = Path(get_cache_path())
                    requested_path = Path(info_path).resolve()
                    if not str(requested_path).startswith(str(cache_root)):
                        _dbg(f"Forbidden: {requested_path} is not in {cache_root}")
                        return web.Response(text="403: Forbidden", status=403)
                except Exception as e:
                    _err(f"Forbidden: {requested_path} is not in {cache_root}", e)
                    return web.Response(text="403: Forbidden", status=403)

                with open(info_path) as f:
                    content = f.read()
                return web.Response(text=content, content_type="application/json")

            if not os.path.exists(full_path):
                return web.Response(text="404: Not Found", status=404)

            # Check for directory traversal
            try:
                cache_root = Path(get_cache_path())
                requested_path = Path(full_path).resolve()
                if not str(requested_path).startswith(str(cache_root)):
                    _dbg(f"Forbidden: {requested_path} is not in {cache_root}")
                    return web.Response(text="403: Forbidden", status=403)
            except Exception as e:
                _err(f"Forbidden: {requested_path} is not in {cache_root}", e)
                return web.Response(text="403: Forbidden", status=403)

            mimetype = get_file_mime_type(full_path)
            if "download" in request.query:
                # download file as an attachment
                info = json_from_file(info_path) or {}
                mimetype = info.get("type", mimetype)
                filename = info.get("name") or os.path.basename(full_path)
                mtime = info.get("date", os.path.getmtime(full_path))
                mdate = datetime.fromtimestamp(mtime).isoformat()
                return web.FileResponse(
                    full_path,
                    headers={
                        "Content-Disposition": f'attachment; filename="{filename}"; modification-date="{mdate}"',
                        "Content-Type": mimetype,
                    },
                )
            else:
                return web.FileResponse(full_path, headers={"Content-Type": mimetype})

        app.router.add_get("/~cache/{tail:.*}", cache_handler)

        async def ui_static(request: web.Request) -> web.Response:
            path = Path(request.match_info["path"])

            try:
                # Handle both Path objects and importlib.resources Traversable objects
                if hasattr(_ROOT, "joinpath"):
                    # importlib.resources Traversable
                    resource = _ROOT.joinpath("ui").joinpath(str(path))
                    if not resource.is_file():
                        raise web.HTTPNotFound
                    content = resource.read_bytes()
                else:
                    # Regular Path object
                    resource = _ROOT / "ui" / path
                    if not resource.is_file():
                        raise web.HTTPNotFound
                    try:
                        resource.relative_to(Path(_ROOT))  # basic directory-traversal guard
                    except ValueError as e:
                        raise web.HTTPBadRequest(text="Invalid path") from e
                    content = resource.read_bytes()

                content_type, _ = mimetypes.guess_type(str(path))
                if content_type is None:
                    content_type = "application/octet-stream"
                return web.Response(body=content, content_type=content_type)
            except (OSError, PermissionError, AttributeError) as e:
                raise web.HTTPNotFound from e

        app.router.add_get("/ui/{path:.*}", ui_static, name="ui_static")

        async def config_handler(request):
            ret = {}
            if "defaults" not in ret:
                ret["defaults"] = g_config["defaults"]
            enabled, disabled = provider_status()
            ret["status"] = {"all": list(g_config["providers"].keys()), "enabled": enabled, "disabled": disabled}
            # Add auth configuration
            ret["requiresAuth"] = g_app.is_auth_enabled()
            ret["authTypes"] = [provider.__class__.__name__ for provider in g_app.auth_providers]
            return web.json_response(ret)

        app.router.add_get("/config", config_handler)

        async def not_found_handler(request):
            return web.Response(text="404: Not Found", status=404)

        app.router.add_get("/favicon.ico", not_found_handler)

        # go through and register all g_app extensions
        for handler in g_app.server_add_get:
            handler_fn = handler[1]

            async def managed_handler(request, handler_fn=handler_fn):
                try:
                    return await handler_fn(request)
                except Exception as e:
                    return web.json_response(to_error_response(e, stacktrace=g_verbose), status=500)

            app.router.add_get(handler[0], managed_handler, **handler[2])
        for handler in g_app.server_add_post:
            handler_fn = handler[1]

            async def managed_handler(request, handler_fn=handler_fn):
                try:
                    return await handler_fn(request)
                except Exception as e:
                    return web.json_response(to_error_response(e, stacktrace=g_verbose), status=500)

            app.router.add_post(handler[0], managed_handler, **handler[2])
        for handler in g_app.server_add_put:
            handler_fn = handler[1]

            async def managed_handler(request, handler_fn=handler_fn):
                try:
                    return await handler_fn(request)
                except Exception as e:
                    return web.json_response(to_error_response(e, stacktrace=g_verbose), status=500)

            app.router.add_put(handler[0], managed_handler, **handler[2])
        for handler in g_app.server_add_delete:
            handler_fn = handler[1]

            async def managed_handler(request, handler_fn=handler_fn):
                try:
                    return await handler_fn(request)
                except Exception as e:
                    return web.json_response(to_error_response(e, stacktrace=g_verbose), status=500)

            app.router.add_delete(handler[0], managed_handler, **handler[2])
        for handler in g_app.server_add_patch:
            handler_fn = handler[1]

            async def managed_handler(request, handler_fn=handler_fn):
                try:
                    return await handler_fn(request)
                except Exception as e:
                    return web.json_response(to_error_response(e, stacktrace=g_verbose), status=500)

            app.router.add_patch(handler[0], managed_handler, **handler[2])

        # Serve index.html from root
        async def index_handler(request):
            index_content = read_resource_file_bytes("index.html")

            importmaps = {"imports": g_app.import_maps}
            importmaps_script = '<script type="importmap">\n' + json.dumps(importmaps, indent=4) + "\n</script>"
            index_content = index_content.replace(
                b'<script type="importmap"></script>',
                importmaps_script.encode("utf-8"),
            )

            if len(g_app.index_headers) > 0:
                html_header = ""
                for header in g_app.index_headers:
                    html_header += header
                # replace </head> with html_header
                index_content = index_content.replace(b"</head>", html_header.encode("utf-8") + b"\n</head>")

            if len(g_app.index_footers) > 0:
                html_footer = ""
                for footer in g_app.index_footers:
                    html_footer += footer
                # replace </body> with html_footer
                index_content = index_content.replace(b"</body>", html_footer.encode("utf-8") + b"\n</body>")

            return web.Response(body=index_content, content_type="text/html")

        app.router.add_get("/", index_handler)

        # Serve index.html as fallback route (SPA routing)
        app.router.add_route("*", "/{tail:.*}", index_handler)

        # Setup file watcher for config files
        async def start_background_tasks(app):
            """Start background tasks when the app starts"""
            # Start watching config files in the background
            asyncio.create_task(watch_config_files(g_config_path, home_providers_path))

        app.on_startup.append(start_background_tasks)

        # go through and register all g_app extensions

        print(f"Starting server on port {port}...")
        web.run_app(app, host="0.0.0.0", port=port, print=_log)
        return ExitCode.SUCCESS

    if cli_args.enable is not None:
        if cli_args.enable.endswith(","):
            cli_args.enable = cli_args.enable[:-1].strip()
        enable_providers = [cli_args.enable]
        all_providers = g_config["providers"].keys()
        msgs = []
        if len(extra_args) > 0:
            for arg in extra_args:
                if arg.endswith(","):
                    arg = arg[:-1].strip()
                if arg in all_providers:
                    enable_providers.append(arg)

        for provider in enable_providers:
            if provider not in g_config["providers"]:
                print(f"Provider '{provider}' not found")
                print(f"Available providers: {', '.join(g_config['providers'].keys())}")
                return ExitCode.FAILED
            if provider in g_config["providers"]:
                provider_config, msg = enable_provider(provider)
                print(f"\nEnabled provider {provider}:")
                printdump(provider_config)
                if msg:
                    msgs.append(msg)

        print_status()
        if len(msgs) > 0:
            print("\n" + "\n".join(msgs))
        return ExitCode.SUCCESS

    if cli_args.disable is not None:
        if cli_args.disable.endswith(","):
            cli_args.disable = cli_args.disable[:-1].strip()
        disable_providers = [cli_args.disable]
        all_providers = g_config["providers"].keys()
        if len(extra_args) > 0:
            for arg in extra_args:
                if arg.endswith(","):
                    arg = arg[:-1].strip()
                if arg in all_providers:
                    disable_providers.append(arg)

        for provider in disable_providers:
            if provider not in g_config["providers"]:
                print(f"Provider {provider} not found")
                print(f"Available providers: {', '.join(g_config['providers'].keys())}")
                return ExitCode.FAILED
            disable_provider(provider)
            print(f"\nDisabled provider {provider}")

        print_status()
        return ExitCode.SUCCESS

    if cli_args.default is not None:
        default_model = cli_args.default
        provider_model = get_provider_model(default_model)
        if provider_model is None:
            print(f"Model {default_model} not found")
            return ExitCode.FAILED
        default_text = g_config["defaults"]["text"]
        default_text["model"] = default_model
        save_config(g_config)
        print(f"\nDefault model set to: {default_model}")
        return ExitCode.SUCCESS

    if (
        cli_args.chat is not None
        or cli_args.image is not None
        or cli_args.audio is not None
        or cli_args.file is not None
        or cli_args.out is not None
        or len(extra_args) > 0
    ):
        try:
            chat = g_config["defaults"]["text"]
            if cli_args.image is not None:
                chat = g_config["defaults"]["image"]
            elif cli_args.audio is not None:
                chat = g_config["defaults"]["audio"]
            elif cli_args.file is not None:
                chat = g_config["defaults"]["file"]
            elif cli_args.out is not None:
                template = f"out:{cli_args.out}"
                if template not in g_config["defaults"]:
                    print(f"Template for output modality '{cli_args.out}' not found")
                    return ExitCode.FAILED
                chat = g_config["defaults"][template]
            if cli_args.chat is not None:
                chat_path = os.path.join(os.path.dirname(__file__), cli_args.chat)
                if not os.path.exists(chat_path):
                    print(f"Chat request template not found: {chat_path}")
                    return ExitCode.FAILED
                _log(f"Using chat: {chat_path}")

                with open(chat_path) as f:
                    chat_json = f.read()
                    chat = json.loads(chat_json)

            if cli_args.system is not None:
                chat["messages"].insert(0, {"role": "system", "content": cli_args.system})

            if len(extra_args) > 0:
                prompt = " ".join(extra_args)
                if not chat["messages"] or len(chat["messages"]) == 0:
                    chat["messages"] = [{"role": "user", "content": [{"type": "text", "text": ""}]}]

                # replace content of last message if exists, else add
                last_msg = chat["messages"][-1] if "messages" in chat else None
                if last_msg and last_msg["role"] == "user":
                    if isinstance(last_msg["content"], list):
                        last_msg["content"][-1]["text"] = prompt
                    else:
                        last_msg["content"] = prompt
                else:
                    chat["messages"].append({"role": "user", "content": prompt})

            # Parse args parameters if provided
            args = None
            if cli_args.args is not None:
                args = parse_args_params(cli_args.args)

            loop.run_until_complete(
                cli_chat(
                    chat,
                    tools=cli_args.tools,
                    image=cli_args.image,
                    audio=cli_args.audio,
                    file=cli_args.file,
                    args=args,
                    raw=cli_args.raw,
                )
            )
            return ExitCode.SUCCESS
        except Exception as e:
            print(f"{cli_args.logprefix}Error: {e}")
            if cli_args.verbose:
                traceback.print_exc()
            return ExitCode.FAILED

    handled = run_extension_cli()
    return ExitCode.SUCCESS if handled else ExitCode.UNHANDLED


def get_app():
    return g_app


def cli(command_line: str):
    parser = create_arg_parser()

    # Load parser extensions, go through all extensions and load their parser arguments
    if load_extensions:
        init_extensions(parser)

    args = shlex.split(command_line)
    cli_args, extra_args = parser.parse_known_args(args)
    return cli_exec(cli_args, extra_args)


def main():
    parser = create_arg_parser()

    # Load parser extensions, go through all extensions and load their parser arguments
    init_extensions(parser)

    cli_args, extra_args = parser.parse_known_args()
    exit_code = cli_exec(cli_args, extra_args)

    if exit_code == ExitCode.UNHANDLED:
        # show usage from ArgumentParser
        parser.print_help()
        g_app.exit(0) if g_app else exit(0)

    g_app.exit(exit_code) if g_app else exit(exit_code)


if __name__ == "__main__":
    if MOCK or DEBUG:
        print(f"MOCK={MOCK} or DEBUG={DEBUG}")
    main()
