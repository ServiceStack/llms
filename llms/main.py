#!/usr/bin/env python

# Copyright (c) Demis Bellot, ServiceStack <https://servicestack.net>
# License: https://github.com/ServiceStack/llms/blob/main/LICENSE

# A lightweight CLI tool and OpenAI-compatible server for querying multiple Large Language Model (LLM) providers.
# Docs: https://github.com/ServiceStack/llms

import argparse
import asyncio
import base64
import json
import mimetypes
import os
import re
import secrets
import site
import subprocess
import sys
import time
import traceback
from importlib import resources  # Py≥3.9  (pip install importlib_resources for 3.7/3.8)
from io import BytesIO
from pathlib import Path
from urllib.parse import parse_qs, urlencode

import aiohttp
from aiohttp import web

try:
    from PIL import Image

    HAS_PIL = True
except ImportError:
    HAS_PIL = False

VERSION = "2.0.35"
_ROOT = None
g_config_path = None
g_ui_path = None
g_config = None
g_handlers = {}
g_verbose = False
g_logprefix = ""
g_default_model = ""
g_sessions = {}  # OAuth session storage: {session_token: {userId, userName, displayName, profileUrl, email, created}}
g_oauth_states = {}  # CSRF protection: {state: {created, redirect_uri}}


def _log(message):
    """Helper method for logging from the global polling task."""
    if g_verbose:
        print(f"{g_logprefix}{message}", flush=True)


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


def gemini_chat_summary(gemini_chat):
    """Summarize Gemini chat completion request for logging. Replace inline_data with size of content only"""
    clone = json.loads(json.dumps(gemini_chat))
    for content in clone["contents"]:
        for part in content["parts"]:
            if "inline_data" in part:
                data = part["inline_data"]["data"]
                part["inline_data"]["data"] = f"({len(data)})"
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


def get_file_mime_type(filename):
    mime_type, _ = mimetypes.guess_type(filename)
    return mime_type or "application/octet-stream"


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


async def process_chat(chat):
    if not chat:
        raise Exception("No chat provided")
    if "stream" not in chat:
        chat["stream"] = False
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
                            if is_url(url):
                                _log(f"Downloading image: {url}")
                                async with session.get(url, timeout=aiohttp.ClientTimeout(total=120)) as response:
                                    response.raise_for_status()
                                    content = await response.read()
                                    # get mimetype from response headers
                                    mimetype = get_file_mime_type(get_filename(url))
                                    if "Content-Type" in response.headers:
                                        mimetype = response.headers["Content-Type"]
                                    # convert/resize image if needed
                                    content, mimetype = convert_image_if_needed(content, mimetype)
                                    # convert to data uri
                                    image_url["url"] = (
                                        f"data:{mimetype};base64,{base64.b64encode(content).decode('utf-8')}"
                                    )
                            elif is_file_path(url):
                                _log(f"Reading image: {url}")
                                with open(url, "rb") as f:
                                    content = f.read()
                                    # get mimetype from file extension
                                    mimetype = get_file_mime_type(get_filename(url))
                                    # convert/resize image if needed
                                    content, mimetype = convert_image_if_needed(content, mimetype)
                                    # convert to data uri
                                    image_url["url"] = (
                                        f"data:{mimetype};base64,{base64.b64encode(content).decode('utf-8')}"
                                    )
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
                            mimetype = get_file_mime_type(get_filename(url))
                            if is_url(url):
                                _log(f"Downloading audio: {url}")
                                async with session.get(url, timeout=aiohttp.ClientTimeout(total=120)) as response:
                                    response.raise_for_status()
                                    content = await response.read()
                                    # get mimetype from response headers
                                    if "Content-Type" in response.headers:
                                        mimetype = response.headers["Content-Type"]
                                    # convert to base64
                                    input_audio["data"] = base64.b64encode(content).decode("utf-8")
                                    input_audio["format"] = mimetype.rsplit("/", 1)[1]
                            elif is_file_path(url):
                                _log(f"Reading audio: {url}")
                                with open(url, "rb") as f:
                                    content = f.read()
                                    # convert to base64
                                    input_audio["data"] = base64.b64encode(content).decode("utf-8")
                                    input_audio["format"] = mimetype.rsplit("/", 1)[1]
                            elif is_base_64(url):
                                pass  # use base64 data as-is
                            else:
                                raise Exception(f"Invalid audio: {url}")
                    elif item["type"] == "file" and "file" in item:
                        file = item["file"]
                        if "file_data" in file:
                            url = file["file_data"]
                            mimetype = get_file_mime_type(get_filename(url))
                            if is_url(url):
                                _log(f"Downloading file: {url}")
                                async with session.get(url, timeout=aiohttp.ClientTimeout(total=120)) as response:
                                    response.raise_for_status()
                                    content = await response.read()
                                    file["filename"] = get_filename(url)
                                    file["file_data"] = (
                                        f"data:{mimetype};base64,{base64.b64encode(content).decode('utf-8')}"
                                    )
                            elif is_file_path(url):
                                _log(f"Reading file: {url}")
                                with open(url, "rb") as f:
                                    content = f.read()
                                    file["filename"] = get_filename(url)
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


class HTTPError(Exception):
    def __init__(self, status, reason, body, headers=None):
        self.status = status
        self.reason = reason
        self.body = body
        self.headers = headers
        super().__init__(f"HTTP {status} {reason}")


async def response_json(response):
    text = await response.text()
    if response.status >= 400:
        raise HTTPError(response.status, reason=response.reason, body=text, headers=dict(response.headers))
    response.raise_for_status()
    body = json.loads(text)
    return body


class OpenAiProvider:
    def __init__(self, base_url, api_key=None, models=None, **kwargs):
        if models is None:
            models = {}
        self.base_url = base_url.strip("/")
        self.api_key = api_key
        self.models = models

        # check if base_url ends with /v{\d} to handle providers with different versions (e.g. z.ai uses /v4)
        last_segment = base_url.rsplit("/", 1)[1]
        if last_segment.startswith("v") and last_segment[1:].isdigit():
            self.chat_url = f"{base_url}/chat/completions"
        else:
            self.chat_url = f"{base_url}/v1/chat/completions"

        self.headers = kwargs.get("headers", {"Content-Type": "application/json"})
        if api_key is not None:
            self.headers["Authorization"] = f"Bearer {api_key}"

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
        self.pricing = kwargs.get("pricing")
        self.default_pricing = kwargs.get("default_pricing")
        self.check = kwargs.get("check")

    @classmethod
    def test(cls, base_url=None, api_key=None, models=None, **kwargs):
        if models is None:
            models = {}
        return base_url and api_key and len(models) > 0

    async def load(self):
        pass

    def model_pricing(self, model):
        provider_model = self.provider_model(model) or model
        if self.pricing and provider_model in self.pricing:
            return self.pricing[provider_model]
        return self.default_pricing or None

    def provider_model(self, model):
        if model in self.models:
            return self.models[model]
        return None

    def to_response(self, response, chat, started_at):
        if "metadata" not in response:
            response["metadata"] = {}
        response["metadata"]["duration"] = int((time.time() - started_at) * 1000)
        if chat is not None and "model" in chat:
            pricing = self.model_pricing(chat["model"])
            if pricing and "input" in pricing and "output" in pricing:
                response["metadata"]["pricing"] = f"{pricing['input']}/{pricing['output']}"
        _log(json.dumps(response, indent=2))
        return response

    async def chat(self, chat):
        chat["model"] = self.provider_model(chat["model"]) or chat["model"]

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

        chat = await process_chat(chat)
        _log(f"POST {self.chat_url}")
        _log(chat_summary(chat))
        # remove metadata if any (conflicts with some providers, e.g. Z.ai)
        chat.pop("metadata", None)

        async with aiohttp.ClientSession() as session:
            started_at = time.time()
            async with session.post(
                self.chat_url, headers=self.headers, data=json.dumps(chat), timeout=aiohttp.ClientTimeout(total=120)
            ) as response:
                return self.to_response(await response_json(response), chat, started_at)


class OllamaProvider(OpenAiProvider):
    def __init__(self, base_url, models, all_models=False, **kwargs):
        super().__init__(base_url=base_url, models=models, **kwargs)
        self.all_models = all_models

    async def load(self):
        if self.all_models:
            await self.load_models(default_models=self.models)

    async def get_models(self):
        ret = {}
        try:
            async with aiohttp.ClientSession() as session:
                _log(f"GET {self.base_url}/api/tags")
                async with session.get(
                    f"{self.base_url}/api/tags", headers=self.headers, timeout=aiohttp.ClientTimeout(total=120)
                ) as response:
                    data = await response_json(response)
                    for model in data.get("models", []):
                        name = model["model"]
                        if name.endswith(":latest"):
                            name = name[:-7]
                        ret[name] = name
                    _log(f"Loaded Ollama models: {ret}")
        except Exception as e:
            _log(f"Error getting Ollama models: {e}")
            # return empty dict if ollama is not available
        return ret

    async def load_models(self, default_models):
        """Load models if all_models was requested"""
        if self.all_models:
            self.models = await self.get_models()
        if default_models:
            self.models = {**default_models, **self.models}

    @classmethod
    def test(cls, base_url=None, models=None, all_models=False, **kwargs):
        if models is None:
            models = {}
        return base_url and (len(models) > 0 or all_models)


class GoogleOpenAiProvider(OpenAiProvider):
    def __init__(self, api_key, models, **kwargs):
        super().__init__(base_url="https://generativelanguage.googleapis.com", api_key=api_key, models=models, **kwargs)
        self.chat_url = "https://generativelanguage.googleapis.com/v1beta/chat/completions"

    @classmethod
    def test(cls, api_key=None, models=None, **kwargs):
        if models is None:
            models = {}
        return api_key and len(models) > 0


class GoogleProvider(OpenAiProvider):
    def __init__(self, models, api_key, safety_settings=None, thinking_config=None, curl=False, **kwargs):
        super().__init__(base_url="https://generativelanguage.googleapis.com", api_key=api_key, models=models, **kwargs)
        self.safety_settings = safety_settings
        self.thinking_config = thinking_config
        self.curl = curl
        self.headers = kwargs.get("headers", {"Content-Type": "application/json"})
        # Google fails when using Authorization header, use query string param instead
        if "Authorization" in self.headers:
            del self.headers["Authorization"]

    @classmethod
    def test(cls, api_key=None, models=None, **kwargs):
        if models is None:
            models = {}
        return api_key is not None and len(models) > 0

    async def chat(self, chat):
        chat["model"] = self.provider_model(chat["model"]) or chat["model"]

        chat = await process_chat(chat)
        generation_config = {}

        # Filter out system messages and convert to proper Gemini format
        contents = []
        system_prompt = None

        async with aiohttp.ClientSession() as session:
            for message in chat["messages"]:
                if message["role"] == "system":
                    content = message["content"]
                    if isinstance(content, list):
                        for item in content:
                            if "text" in item:
                                system_prompt = item["text"]
                                break
                    elif isinstance(content, str):
                        system_prompt = content
                elif "content" in message:
                    if isinstance(message["content"], list):
                        parts = []
                        for item in message["content"]:
                            if "type" in item:
                                if item["type"] == "image_url" and "image_url" in item:
                                    image_url = item["image_url"]
                                    if "url" not in image_url:
                                        continue
                                    url = image_url["url"]
                                    if not url.startswith("data:"):
                                        raise (Exception("Image was not downloaded: " + url))
                                    # Extract mime type from data uri
                                    mimetype = url.split(";", 1)[0].split(":", 1)[1] if ";" in url else "image/png"
                                    base64_data = url.split(",", 1)[1]
                                    parts.append({"inline_data": {"mime_type": mimetype, "data": base64_data}})
                                elif item["type"] == "input_audio" and "input_audio" in item:
                                    input_audio = item["input_audio"]
                                    if "data" not in input_audio:
                                        continue
                                    data = input_audio["data"]
                                    format = input_audio["format"]
                                    mimetype = f"audio/{format}"
                                    parts.append({"inline_data": {"mime_type": mimetype, "data": data}})
                                elif item["type"] == "file" and "file" in item:
                                    file = item["file"]
                                    if "file_data" not in file:
                                        continue
                                    data = file["file_data"]
                                    if not data.startswith("data:"):
                                        raise (Exception("File was not downloaded: " + data))
                                    # Extract mime type from data uri
                                    mimetype = (
                                        data.split(";", 1)[0].split(":", 1)[1]
                                        if ";" in data
                                        else "application/octet-stream"
                                    )
                                    base64_data = data.split(",", 1)[1]
                                    parts.append({"inline_data": {"mime_type": mimetype, "data": base64_data}})
                            if "text" in item:
                                text = item["text"]
                                parts.append({"text": text})
                        if len(parts) > 0:
                            contents.append(
                                {
                                    "role": message["role"]
                                    if "role" in message and message["role"] == "user"
                                    else "model",
                                    "parts": parts,
                                }
                            )
                    else:
                        content = message["content"]
                        contents.append(
                            {
                                "role": message["role"] if "role" in message and message["role"] == "user" else "model",
                                "parts": [{"text": content}],
                            }
                        )

            gemini_chat = {
                "contents": contents,
            }

            if self.safety_settings:
                gemini_chat["safetySettings"] = self.safety_settings

            # Add system instruction if present
            if system_prompt is not None:
                gemini_chat["systemInstruction"] = {"parts": [{"text": system_prompt}]}

            if "max_completion_tokens" in chat:
                generation_config["maxOutputTokens"] = chat["max_completion_tokens"]
            if "stop" in chat:
                generation_config["stopSequences"] = [chat["stop"]]
            if "temperature" in chat:
                generation_config["temperature"] = chat["temperature"]
            if "top_p" in chat:
                generation_config["topP"] = chat["top_p"]
            if "top_logprobs" in chat:
                generation_config["topK"] = chat["top_logprobs"]

            if "thinkingConfig" in chat:
                generation_config["thinkingConfig"] = chat["thinkingConfig"]
            elif self.thinking_config:
                generation_config["thinkingConfig"] = self.thinking_config

            if len(generation_config) > 0:
                gemini_chat["generationConfig"] = generation_config

            started_at = int(time.time() * 1000)
            gemini_chat_url = f"https://generativelanguage.googleapis.com/v1beta/models/{chat['model']}:generateContent?key={self.api_key}"

            _log(f"POST {gemini_chat_url}")
            _log(gemini_chat_summary(gemini_chat))
            started_at = time.time()

            if self.curl:
                curl_args = [
                    "curl",
                    "-X",
                    "POST",
                    "-H",
                    "Content-Type: application/json",
                    "-d",
                    json.dumps(gemini_chat),
                    gemini_chat_url,
                ]
                try:
                    o = subprocess.run(curl_args, check=True, capture_output=True, text=True, timeout=120)
                    obj = json.loads(o.stdout)
                except Exception as e:
                    raise Exception(f"Error executing curl: {e}") from e
            else:
                async with session.post(
                    gemini_chat_url,
                    headers=self.headers,
                    data=json.dumps(gemini_chat),
                    timeout=aiohttp.ClientTimeout(total=120),
                ) as res:
                    obj = await response_json(res)
                    _log(f"google response:\n{json.dumps(obj, indent=2)}")

            response = {
                "id": f"chatcmpl-{started_at}",
                "created": started_at,
                "model": obj.get("modelVersion", chat["model"]),
            }
            choices = []
            if "error" in obj:
                _log(f"Error: {obj['error']}")
                raise Exception(obj["error"]["message"])
            for i, candidate in enumerate(obj["candidates"]):
                role = "assistant"
                if "content" in candidate and "role" in candidate["content"]:
                    role = "assistant" if candidate["content"]["role"] == "model" else candidate["content"]["role"]

                # Safely extract content from all text parts
                content = ""
                reasoning = ""
                if "content" in candidate and "parts" in candidate["content"]:
                    text_parts = []
                    reasoning_parts = []
                    for part in candidate["content"]["parts"]:
                        if "text" in part:
                            if "thought" in part and part["thought"]:
                                reasoning_parts.append(part["text"])
                            else:
                                text_parts.append(part["text"])
                    content = " ".join(text_parts)
                    reasoning = " ".join(reasoning_parts)

                choice = {
                    "index": i,
                    "finish_reason": candidate.get("finishReason", "stop"),
                    "message": {
                        "role": role,
                        "content": content,
                    },
                }
                if reasoning:
                    choice["message"]["reasoning"] = reasoning
                choices.append(choice)
            response["choices"] = choices
            if "usageMetadata" in obj:
                usage = obj["usageMetadata"]
                response["usage"] = {
                    "completion_tokens": usage["candidatesTokenCount"],
                    "total_tokens": usage["totalTokenCount"],
                    "prompt_tokens": usage["promptTokenCount"],
                }
            return self.to_response(response, chat, started_at)


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
    for id, provider in g_handlers.items():
        for model in provider.models:
            if model not in existing_models:
                existing_models.add(model)
                provider_model = provider.models[model]
                pricing = provider.model_pricing(model)
                ret.append({"id": model, "provider": id, "provider_model": provider_model, "pricing": pricing})
    ret.sort(key=lambda x: x["id"])
    return ret


async def chat_completion(chat):
    model = chat["model"]
    # get first provider that has the model
    candidate_providers = [name for name, provider in g_handlers.items() if model in provider.models]
    if len(candidate_providers) == 0:
        raise (Exception(f"Model {model} not found"))

    first_exception = None
    for name in candidate_providers:
        provider = g_handlers[name]
        _log(f"provider: {name} {type(provider).__name__}")
        try:
            response = await provider.chat(chat.copy())
            return response
        except Exception as e:
            if first_exception is None:
                first_exception = e
            _log(f"Provider {name} failed: {e}")
            continue

    # If we get here, all providers failed
    raise first_exception


async def cli_chat(chat, image=None, audio=None, file=None, args=None, raw=False):
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
        response = await chat_completion(chat)
        if raw:
            print(json.dumps(response, indent=2))
            exit(0)
        else:
            answer = response["choices"][0]["message"]["content"]
            print(answer)
    except HTTPError as e:
        # HTTP error (4xx, 5xx)
        print(f"{e}:\n{e.body}")
        exit(1)
    except aiohttp.ClientConnectionError as e:
        # Connection issues
        print(f"Connection error: {e}")
        exit(1)
    except asyncio.TimeoutError as e:
        # Timeout
        print(f"Timeout error: {e}")
        exit(1)


def config_str(key):
    return key in g_config and g_config[key] or None


def init_llms(config):
    global g_config, g_handlers

    g_config = config
    g_handlers = {}
    # iterate over config and replace $ENV with env value
    for key, value in g_config.items():
        if isinstance(value, str) and value.startswith("$"):
            g_config[key] = os.environ.get(value[1:], "")

    # if g_verbose:
    #     printdump(g_config)
    providers = g_config["providers"]

    for name, orig in providers.items():
        definition = orig.copy()
        provider_type = definition["type"]
        if "enabled" in definition and not definition["enabled"]:
            continue

        # Replace API keys with environment variables if they start with $
        if "api_key" in definition:
            value = definition["api_key"]
            if isinstance(value, str) and value.startswith("$"):
                definition["api_key"] = os.environ.get(value[1:], "")

        # Create a copy of definition without the 'type' key for constructor kwargs
        constructor_kwargs = {k: v for k, v in definition.items() if k != "type" and k != "enabled"}
        constructor_kwargs["headers"] = g_config["defaults"]["headers"].copy()

        if provider_type == "OpenAiProvider" and OpenAiProvider.test(**constructor_kwargs):
            g_handlers[name] = OpenAiProvider(**constructor_kwargs)
        elif provider_type == "OllamaProvider" and OllamaProvider.test(**constructor_kwargs):
            g_handlers[name] = OllamaProvider(**constructor_kwargs)
        elif provider_type == "GoogleProvider" and GoogleProvider.test(**constructor_kwargs):
            g_handlers[name] = GoogleProvider(**constructor_kwargs)
        elif provider_type == "GoogleOpenAiProvider" and GoogleOpenAiProvider.test(**constructor_kwargs):
            g_handlers[name] = GoogleOpenAiProvider(**constructor_kwargs)

    return g_handlers


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
                raise HTTPError(resp.status, reason=resp.reason, body=text, headers=dict(resp.headers))
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
    return f"{os.environ.get('HOME')}/.llms/{filename}"


def get_config_path():
    home_config_path = home_llms_path("llms.json")
    check_paths = [
        "./llms.json",
        home_config_path,
    ]
    if os.environ.get("LLMS_CONFIG_PATH"):
        check_paths.insert(0, os.environ.get("LLMS_CONFIG_PATH"))

    for check_path in check_paths:
        g_config_path = os.path.normpath(os.path.join(os.path.dirname(__file__), check_path))
        if os.path.exists(g_config_path):
            return g_config_path
    return None


def get_ui_path():
    ui_paths = [home_llms_path("ui.json"), "ui.json"]
    for ui_path in ui_paths:
        if os.path.exists(ui_path):
            return ui_path
    return None


def enable_provider(provider):
    msg = None
    provider_config = g_config["providers"][provider]
    provider_config["enabled"] = True
    if "api_key" in provider_config:
        api_key = provider_config["api_key"]
        if isinstance(api_key, str):
            if api_key.startswith("$"):
                if not os.environ.get(api_key[1:], ""):
                    msg = f"WARNING: {provider} requires missing API Key in Environment Variable {api_key}"
            else:
                msg = f"WARNING: {provider} is not configured with an API Key"
    save_config(g_config)
    init_llms(g_config)
    return provider_config, msg


def disable_provider(provider):
    provider_config = g_config["providers"][provider]
    provider_config["enabled"] = False
    save_config(g_config)
    init_llms(g_config)


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
            if model_name in provider.models:
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
        # Create a simple ping chat request
        chat = (provider.check or g_config["defaults"]["check"]).copy()
        chat["model"] = model

        started_at = time.time()
        try:
            # Try to get a response from the model
            response = await provider.chat(chat)
            duration_ms = int((time.time() - started_at) * 1000)

            # Check if we got a valid response
            if response and "choices" in response and len(response["choices"]) > 0:
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

    print()


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
    home_ui_path = home_llms_path("ui.json")
    if os.path.exists(home_config_path) and os.path.exists(home_ui_path):
        return

    llms_home = os.path.dirname(home_config_path)
    os.makedirs(llms_home, exist_ok=True)
    try:
        if not os.path.exists(home_config_path):
            config_json = await text_from_resource_or_url("llms.json")
            with open(home_config_path, "w", encoding="utf-8") as f:
                f.write(config_json)
            _log(f"Created default config at {home_config_path}")

        if not os.path.exists(home_ui_path):
            ui_json = await text_from_resource_or_url("ui.json")
            with open(home_ui_path, "w", encoding="utf-8") as f:
                f.write(ui_json)
            _log(f"Created default ui config at {home_ui_path}")
    except Exception:
        print("Could not create llms.json. Create one with --init or use --config <path>")
        exit(1)


async def reload_providers():
    global g_config, g_handlers
    g_handlers = init_llms(g_config)
    await load_llms()
    _log(f"{len(g_handlers)} providers loaded")
    return g_handlers


async def watch_config_files(config_path, ui_path, interval=1):
    """Watch config files and reload providers when they change"""
    global g_config

    config_path = Path(config_path)
    ui_path = Path(ui_path) if ui_path else None

    file_mtimes = {}

    _log(f"Watching config files: {config_path}" + (f", {ui_path}" if ui_path else ""))

    while True:
        await asyncio.sleep(interval)

        # Check llms.json
        try:
            if config_path.is_file():
                mtime = config_path.stat().st_mtime

                if str(config_path) not in file_mtimes:
                    file_mtimes[str(config_path)] = mtime
                elif file_mtimes[str(config_path)] != mtime:
                    _log(f"Config file changed: {config_path.name}")
                    file_mtimes[str(config_path)] = mtime

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

        # Check ui.json
        if ui_path:
            try:
                if ui_path.is_file():
                    mtime = ui_path.stat().st_mtime

                    if str(ui_path) not in file_mtimes:
                        file_mtimes[str(ui_path)] = mtime
                    elif file_mtimes[str(ui_path)] != mtime:
                        _log(f"Config file changed: {ui_path.name}")
                        file_mtimes[str(ui_path)] = mtime
                        _log("ui.json reloaded - reload page to update")
            except FileNotFoundError:
                pass


def main():
    global _ROOT, g_verbose, g_default_model, g_logprefix, g_config, g_config_path, g_ui_path

    parser = argparse.ArgumentParser(description=f"llms v{VERSION}")
    parser.add_argument("--config", default=None, help="Path to config file", metavar="FILE")
    parser.add_argument("-m", "--model", default=None, help="Model to use")

    parser.add_argument("--chat", default=None, help="OpenAI Chat Completion Request to send", metavar="REQUEST")
    parser.add_argument(
        "-s", "--system", default=None, help="System prompt to use for chat completion", metavar="PROMPT"
    )
    parser.add_argument("--image", default=None, help="Image input to use in chat completion")
    parser.add_argument("--audio", default=None, help="Audio input to use in chat completion")
    parser.add_argument("--file", default=None, help="File input to use in chat completion")
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

    parser.add_argument("--root", default=None, help="Change root directory for UI files", metavar="PATH")
    parser.add_argument("--logprefix", default="", help="Prefix used in log messages", metavar="PREFIX")
    parser.add_argument("--verbose", action="store_true", help="Verbose output")

    cli_args, extra_args = parser.parse_known_args()

    # Check for verbose mode from CLI argument or environment variables
    verbose_env = os.environ.get("VERBOSE", "").lower()
    if cli_args.verbose or verbose_env in ("1", "true"):
        g_verbose = True
        # printdump(cli_args)
    if cli_args.model:
        g_default_model = cli_args.model
    if cli_args.logprefix:
        g_logprefix = cli_args.logprefix

    _ROOT = Path(cli_args.root) if cli_args.root else resolve_root()
    if not _ROOT:
        print("Resource root not found")
        exit(1)

    home_config_path = home_llms_path("llms.json")
    home_ui_path = home_llms_path("ui.json")

    if cli_args.init:
        if os.path.exists(home_config_path):
            print(f"llms.json already exists at {home_config_path}")
        else:
            asyncio.run(save_default_config(home_config_path))
            print(f"Created default config at {home_config_path}")

        if os.path.exists(home_ui_path):
            print(f"ui.json already exists at {home_ui_path}")
        else:
            asyncio.run(save_text_url(github_url("ui.json"), home_ui_path))
            print(f"Created default ui config at {home_ui_path}")
        exit(0)

    if cli_args.config:
        # read contents
        g_config_path = cli_args.config
        with open(g_config_path, encoding="utf-8") as f:
            config_json = f.read()
            g_config = json.loads(config_json)

        config_dir = os.path.dirname(g_config_path)
        # look for ui.json in same directory as config
        ui_path = os.path.join(config_dir, "ui.json")
        if os.path.exists(ui_path):
            g_ui_path = ui_path
        else:
            if not os.path.exists(home_ui_path):
                ui_json = text_from_resource("ui.json")
                with open(home_ui_path, "w", encoding="utf-8") as f:
                    f.write(ui_json)
                _log(f"Created default ui config at {home_ui_path}")
            g_ui_path = home_ui_path
    else:
        # ensure llms.json and ui.json exist in home directory
        asyncio.run(save_home_configs())
        g_config_path = home_config_path
        g_ui_path = home_ui_path
        g_config = json.loads(text_from_file(g_config_path))

    asyncio.run(reload_providers())

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
        for name, provider in g_handlers.items():
            if len(filter_list) > 0 and name not in filter_list:
                continue
            print(f"{name}:")
            enabled.append(name)
            for model in provider.models:
                print(f"  {model}")

        print_status()
        exit(0)

    if cli_args.check is not None:
        # Check validity of models for a provider
        provider_name = cli_args.check
        model_names = extra_args if len(extra_args) > 0 else None
        asyncio.run(check_models(provider_name, model_names))
        exit(0)

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

        if not os.path.exists(g_ui_path):
            print(f"UI not found at {g_ui_path}")
            exit(1)

        # Validate auth configuration if enabled
        auth_enabled = g_config.get("auth", {}).get("enabled", False)
        if auth_enabled:
            github_config = g_config.get("auth", {}).get("github", {})
            client_id = github_config.get("client_id", "")
            client_secret = github_config.get("client_secret", "")

            # Expand environment variables
            if client_id.startswith("$"):
                client_id = os.environ.get(client_id[1:], "")
            if client_secret.startswith("$"):
                client_secret = os.environ.get(client_secret[1:], "")

            if not client_id or not client_secret:
                print("ERROR: Authentication is enabled but GitHub OAuth is not properly configured.")
                print("Please set GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET environment variables,")
                print("or disable authentication by setting 'auth.enabled' to false in llms.json")
                exit(1)

            _log("Authentication enabled - GitHub OAuth configured")

        client_max_size = g_config.get("limits", {}).get(
            "client_max_size", 20 * 1024 * 1024
        )  # 20MB max request size (to handle base64 encoding overhead)
        _log(f"client_max_size set to {client_max_size} bytes ({client_max_size / 1024 / 1024:.1f}MB)")
        app = web.Application(client_max_size=client_max_size)

        # Authentication middleware helper
        def check_auth(request):
            """Check if request is authenticated. Returns (is_authenticated, user_data)"""
            if not auth_enabled:
                return True, None

            # Check for OAuth session token
            session_token = request.query.get("session") or request.headers.get("X-Session-Token")
            if session_token and session_token in g_sessions:
                return True, g_sessions[session_token]

            # Check for API key
            auth_header = request.headers.get("Authorization", "")
            if auth_header.startswith("Bearer "):
                api_key = auth_header[7:]
                if api_key:
                    return True, {"authProvider": "apikey"}

            return False, None

        async def chat_handler(request):
            # Check authentication if enabled
            is_authenticated, user_data = check_auth(request)
            if not is_authenticated:
                return web.json_response(
                    {
                        "error": {
                            "message": "Authentication required",
                            "type": "authentication_error",
                            "code": "unauthorized",
                        }
                    },
                    status=401,
                )

            try:
                chat = await request.json()
                response = await chat_completion(chat)
                return web.json_response(response)
            except Exception as e:
                return web.json_response({"error": str(e)}, status=500)

        app.router.add_post("/v1/chat/completions", chat_handler)

        async def models_handler(request):
            return web.json_response(get_models())

        app.router.add_get("/models/list", models_handler)

        async def active_models_handler(request):
            return web.json_response(get_active_models())

        app.router.add_get("/models", active_models_handler)

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
                    _log(f"Enabled provider {provider}")
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

        # OAuth handlers
        async def github_auth_handler(request):
            """Initiate GitHub OAuth flow"""
            if "auth" not in g_config or "github" not in g_config["auth"]:
                return web.json_response({"error": "GitHub OAuth not configured"}, status=500)

            auth_config = g_config["auth"]["github"]
            client_id = auth_config.get("client_id", "")
            redirect_uri = auth_config.get("redirect_uri", "")

            # Expand environment variables
            if client_id.startswith("$"):
                client_id = os.environ.get(client_id[1:], "")
            if redirect_uri.startswith("$"):
                redirect_uri = os.environ.get(redirect_uri[1:], "")

            if not client_id:
                return web.json_response({"error": "GitHub client_id not configured"}, status=500)

            # Generate CSRF state token
            state = secrets.token_urlsafe(32)
            g_oauth_states[state] = {"created": time.time(), "redirect_uri": redirect_uri}

            # Clean up old states (older than 10 minutes)
            current_time = time.time()
            expired_states = [s for s, data in g_oauth_states.items() if current_time - data["created"] > 600]
            for s in expired_states:
                del g_oauth_states[s]

            # Build GitHub authorization URL
            params = {
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "state": state,
                "scope": "read:user user:email",
            }
            auth_url = f"https://github.com/login/oauth/authorize?{urlencode(params)}"

            return web.HTTPFound(auth_url)

        def validate_user(github_username):
            auth_config = g_config["auth"]["github"]
            # Check if user is restricted
            restrict_to = auth_config.get("restrict_to", "")

            # Expand environment variables
            if restrict_to.startswith("$"):
                restrict_to = os.environ.get(restrict_to[1:], "")

            # If restrict_to is configured, validate the user
            if restrict_to:
                # Parse allowed users (comma or space delimited)
                allowed_users = [u.strip() for u in re.split(r"[,\s]+", restrict_to) if u.strip()]

                # Check if user is in the allowed list
                if not github_username or github_username not in allowed_users:
                    _log(f"Access denied for user: {github_username}. Not in allowed list: {allowed_users}")
                    return web.Response(
                        text=f"Access denied. User '{github_username}' is not authorized to access this application.",
                        status=403,
                    )
            return None

        async def github_callback_handler(request):
            """Handle GitHub OAuth callback"""
            code = request.query.get("code")
            state = request.query.get("state")

            if not code or not state:
                return web.Response(text="Missing code or state parameter", status=400)

            # Verify state token (CSRF protection)
            if state not in g_oauth_states:
                return web.Response(text="Invalid state parameter", status=400)

            g_oauth_states.pop(state)

            if "auth" not in g_config or "github" not in g_config["auth"]:
                return web.json_response({"error": "GitHub OAuth not configured"}, status=500)

            auth_config = g_config["auth"]["github"]
            client_id = auth_config.get("client_id", "")
            client_secret = auth_config.get("client_secret", "")
            redirect_uri = auth_config.get("redirect_uri", "")

            # Expand environment variables
            if client_id.startswith("$"):
                client_id = os.environ.get(client_id[1:], "")
            if client_secret.startswith("$"):
                client_secret = os.environ.get(client_secret[1:], "")
            if redirect_uri.startswith("$"):
                redirect_uri = os.environ.get(redirect_uri[1:], "")

            if not client_id or not client_secret:
                return web.json_response({"error": "GitHub OAuth credentials not configured"}, status=500)

            # Exchange code for access token
            async with aiohttp.ClientSession() as session:
                token_url = "https://github.com/login/oauth/access_token"
                token_data = {
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "code": code,
                    "redirect_uri": redirect_uri,
                }
                headers = {"Accept": "application/json"}

                async with session.post(token_url, data=token_data, headers=headers) as resp:
                    token_response = await resp.json()
                    access_token = token_response.get("access_token")

                    if not access_token:
                        error = token_response.get("error_description", "Failed to get access token")
                        return web.Response(text=f"OAuth error: {error}", status=400)

                # Fetch user info
                user_url = "https://api.github.com/user"
                headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}

                async with session.get(user_url, headers=headers) as resp:
                    user_data = await resp.json()

                # Validate user
                error_response = validate_user(user_data.get("login", ""))
                if error_response:
                    return error_response

            # Create session
            session_token = secrets.token_urlsafe(32)
            g_sessions[session_token] = {
                "userId": str(user_data.get("id", "")),
                "userName": user_data.get("login", ""),
                "displayName": user_data.get("name", ""),
                "profileUrl": user_data.get("avatar_url", ""),
                "email": user_data.get("email", ""),
                "created": time.time(),
            }

            # Redirect to UI with session token
            return web.HTTPFound(f"/?session={session_token}")

        async def session_handler(request):
            """Validate and return session info"""
            session_token = request.query.get("session") or request.headers.get("X-Session-Token")

            if not session_token or session_token not in g_sessions:
                return web.json_response({"error": "Invalid or expired session"}, status=401)

            session_data = g_sessions[session_token]

            # Clean up old sessions (older than 24 hours)
            current_time = time.time()
            expired_sessions = [token for token, data in g_sessions.items() if current_time - data["created"] > 86400]
            for token in expired_sessions:
                del g_sessions[token]

            return web.json_response({**session_data, "sessionToken": session_token})

        async def logout_handler(request):
            """End OAuth session"""
            session_token = request.query.get("session") or request.headers.get("X-Session-Token")

            if session_token and session_token in g_sessions:
                del g_sessions[session_token]

            return web.json_response({"success": True})

        async def auth_handler(request):
            """Check authentication status and return user info"""
            # Check for OAuth session token
            session_token = request.query.get("session") or request.headers.get("X-Session-Token")

            if session_token and session_token in g_sessions:
                session_data = g_sessions[session_token]
                return web.json_response(
                    {
                        "userId": session_data.get("userId", ""),
                        "userName": session_data.get("userName", ""),
                        "displayName": session_data.get("displayName", ""),
                        "profileUrl": session_data.get("profileUrl", ""),
                        "authProvider": "github",
                    }
                )

            # Check for API key in Authorization header
            # auth_header = request.headers.get('Authorization', '')
            # if auth_header.startswith('Bearer '):
            #     # For API key auth, return a basic response
            #     # You can customize this based on your API key validation logic
            #     api_key = auth_header[7:]
            #     if api_key:  # Add your API key validation logic here
            #         return web.json_response({
            #             "userId": "1",
            #             "userName": "apiuser",
            #             "displayName": "API User",
            #             "profileUrl": "",
            #             "authProvider": "apikey"
            #         })

            # Not authenticated - return error in expected format
            return web.json_response(
                {"responseStatus": {"errorCode": "Unauthorized", "message": "Not authenticated"}}, status=401
            )

        app.router.add_get("/auth", auth_handler)
        app.router.add_get("/auth/github", github_auth_handler)
        app.router.add_get("/auth/github/callback", github_callback_handler)
        app.router.add_get("/auth/session", session_handler)
        app.router.add_post("/auth/logout", logout_handler)

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

        async def ui_config_handler(request):
            with open(g_ui_path, encoding="utf-8") as f:
                ui = json.load(f)
                if "defaults" not in ui:
                    ui["defaults"] = g_config["defaults"]
                enabled, disabled = provider_status()
                ui["status"] = {"all": list(g_config["providers"].keys()), "enabled": enabled, "disabled": disabled}
                # Add auth configuration
                ui["requiresAuth"] = auth_enabled
                ui["authType"] = "oauth" if auth_enabled else "apikey"
                return web.json_response(ui)

        app.router.add_get("/config", ui_config_handler)

        async def not_found_handler(request):
            return web.Response(text="404: Not Found", status=404)

        app.router.add_get("/favicon.ico", not_found_handler)

        # Serve index.html from root
        async def index_handler(request):
            index_content = read_resource_file_bytes("index.html")
            if index_content is None:
                raise web.HTTPNotFound
            return web.Response(body=index_content, content_type="text/html")

        app.router.add_get("/", index_handler)

        # Serve index.html as fallback route (SPA routing)
        app.router.add_route("*", "/{tail:.*}", index_handler)

        # Setup file watcher for config files
        async def start_background_tasks(app):
            """Start background tasks when the app starts"""
            # Start watching config files in the background
            asyncio.create_task(watch_config_files(g_config_path, g_ui_path))

        app.on_startup.append(start_background_tasks)

        print(f"Starting server on port {port}...")
        web.run_app(app, host="0.0.0.0", port=port, print=_log)
        exit(0)

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
                print(f"Provider {provider} not found")
                print(f"Available providers: {', '.join(g_config['providers'].keys())}")
                exit(1)
            if provider in g_config["providers"]:
                provider_config, msg = enable_provider(provider)
                print(f"\nEnabled provider {provider}:")
                printdump(provider_config)
                if msg:
                    msgs.append(msg)

        print_status()
        if len(msgs) > 0:
            print("\n" + "\n".join(msgs))
        exit(0)

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
                exit(1)
            disable_provider(provider)
            print(f"\nDisabled provider {provider}")

        print_status()
        exit(0)

    if cli_args.default is not None:
        default_model = cli_args.default
        all_models = get_models()
        if default_model not in all_models:
            print(f"Model {default_model} not found")
            print(f"Available models: {', '.join(all_models)}")
            exit(1)
        default_text = g_config["defaults"]["text"]
        default_text["model"] = default_model
        save_config(g_config)
        print(f"\nDefault model set to: {default_model}")
        exit(0)

    if (
        cli_args.chat is not None
        or cli_args.image is not None
        or cli_args.audio is not None
        or cli_args.file is not None
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
            if cli_args.chat is not None:
                chat_path = os.path.join(os.path.dirname(__file__), cli_args.chat)
                if not os.path.exists(chat_path):
                    print(f"Chat request template not found: {chat_path}")
                    exit(1)
                _log(f"Using chat: {chat_path}")

                with open(chat_path) as f:
                    chat_json = f.read()
                    chat = json.loads(chat_json)

            if cli_args.system is not None:
                chat["messages"].insert(0, {"role": "system", "content": cli_args.system})

            if len(extra_args) > 0:
                prompt = " ".join(extra_args)
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

            asyncio.run(
                cli_chat(
                    chat, image=cli_args.image, audio=cli_args.audio, file=cli_args.file, args=args, raw=cli_args.raw
                )
            )
            exit(0)
        except Exception as e:
            print(f"{cli_args.logprefix}Error: {e}")
            if cli_args.verbose:
                traceback.print_exc()
            exit(1)

    # show usage from ArgumentParser
    parser.print_help()


if __name__ == "__main__":
    main()
