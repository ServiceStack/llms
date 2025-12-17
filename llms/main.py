#!/usr/bin/env python

# Copyright (c) Demis Bellot, ServiceStack <https://servicestack.net>
# License: https://github.com/ServiceStack/llms/blob/main/LICENSE

# A lightweight CLI tool and OpenAI-compatible server for querying multiple Large Language Model (LLM) providers.
# Docs: https://github.com/ServiceStack/llms

import argparse
import asyncio
import base64
import hashlib
import importlib.util
import json
import mimetypes
import os
import re
import secrets
import shutil
import site
import subprocess
import sys
import time
import traceback
from datetime import datetime
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

VERSION = "3.0.0b2"
_ROOT = None
DEBUG = True  # os.getenv("PYPI_SERVICESTACK") is not None
g_config_path = None
g_ui_path = None
g_config = None
g_providers = None
g_handlers = {}
g_verbose = False
g_logprefix = ""
g_default_model = ""
g_sessions = {}  # OAuth session storage: {session_token: {userId, userName, displayName, profileUrl, email, created}}
g_oauth_states = {}  # CSRF protection: {state: {created, redirect_uri}}
g_app = None  # ExtensionsContext Singleton


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


async def process_chat(chat, provider_id=None):
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
                            if url.startswith("/~cache/"):
                                url = get_cache_path(url[8:])
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
                            if url.startswith("/~cache/"):
                                url = get_cache_path(url[8:])
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
                                    if provider_id == "alibaba":
                                        input_audio["data"] = f"data:{mimetype};base64,{input_audio['data']}"
                                    input_audio["format"] = mimetype.rsplit("/", 1)[1]
                            elif is_file_path(url):
                                _log(f"Reading audio: {url}")
                                with open(url, "rb") as f:
                                    content = f.read()
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


class OpenAiCompatible:
    sdk = "@ai-sdk/openai-compatible"

    def __init__(self, **kwargs):
        required_args = ["id", "api"]
        for arg in required_args:
            if arg not in kwargs:
                raise ValueError(f"Missing required argument: {arg}")

        self.id = kwargs.get("id")
        self.api = kwargs.get("api").strip("/")
        self.api_key = kwargs.get("api_key")
        self.name = kwargs.get("name", self.id.replace("-", " ").title().replace(" ", ""))
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

    def test(self, **kwargs):
        ret = self.api and self.api_key and (len(self.models) > 0)
        if not ret:
            _log(f"Provider {self.name} Missing: {self.api}, {self.api_key}, {len(self.models)}")
        return ret

    async def load(self):
        if not self.models:
            await self.load_models()

    def model_cost(self, model):
        provider_model = self.provider_model(model) or model
        for model_id, model_info in self.models.items():
            if model_id.lower() == provider_model.lower():
                return model_info.get("cost")
        return None

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

    def to_response(self, response, chat, started_at):
        if "metadata" not in response:
            response["metadata"] = {}
        response["metadata"]["duration"] = int((time.time() - started_at) * 1000)
        if chat is not None and "model" in chat:
            pricing = self.model_cost(chat["model"])
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

        chat = await process_chat(chat, provider_id=self.id)
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


class OpenAiProvider(OpenAiCompatible):
    sdk = "@ai-sdk/openai"

    def __init__(self, **kwargs):
        if "api" not in kwargs:
            kwargs["api"] = "https://api.openai.com/v1"
        super().__init__(**kwargs)


class AnthropicProvider(OpenAiCompatible):
    sdk = "@ai-sdk/anthropic"

    def __init__(self, **kwargs):
        if "api" not in kwargs:
            kwargs["api"] = "https://api.anthropic.com/v1"
        super().__init__(**kwargs)

        # Anthropic uses x-api-key header instead of Authorization
        if self.api_key:
            self.headers = self.headers.copy()
            if "Authorization" in self.headers:
                del self.headers["Authorization"]
            self.headers["x-api-key"] = self.api_key

        if "anthropic-version" not in self.headers:
            self.headers = self.headers.copy()
            self.headers["anthropic-version"] = "2023-06-01"
        self.chat_url = f"{self.api}/messages"

    async def chat(self, chat):
        chat["model"] = self.provider_model(chat["model"]) or chat["model"]

        chat = await process_chat(chat, provider_id=self.id)

        # Transform OpenAI format to Anthropic format
        anthropic_request = {
            "model": chat["model"],
            "messages": [],
        }

        # Extract system message (Anthropic uses top-level 'system' parameter)
        system_messages = []
        for message in chat.get("messages", []):
            if message.get("role") == "system":
                content = message.get("content", "")
                if isinstance(content, str):
                    system_messages.append(content)
                elif isinstance(content, list):
                    for item in content:
                        if item.get("type") == "text":
                            system_messages.append(item.get("text", ""))

        if system_messages:
            anthropic_request["system"] = "\n".join(system_messages)

        # Transform messages (exclude system messages)
        for message in chat.get("messages", []):
            if message.get("role") == "system":
                continue

            anthropic_message = {"role": message.get("role"), "content": []}

            content = message.get("content", "")
            if isinstance(content, str):
                anthropic_message["content"] = content
            elif isinstance(content, list):
                for item in content:
                    if item.get("type") == "text":
                        anthropic_message["content"].append({"type": "text", "text": item.get("text", "")})
                    elif item.get("type") == "image_url" and "image_url" in item:
                        # Transform OpenAI image_url format to Anthropic format
                        image_url = item["image_url"].get("url", "")
                        if image_url.startswith("data:"):
                            # Extract media type and base64 data
                            parts = image_url.split(";base64,", 1)
                            if len(parts) == 2:
                                media_type = parts[0].replace("data:", "")
                                base64_data = parts[1]
                                anthropic_message["content"].append(
                                    {
                                        "type": "image",
                                        "source": {"type": "base64", "media_type": media_type, "data": base64_data},
                                    }
                                )

            anthropic_request["messages"].append(anthropic_message)

        # Handle max_tokens (required by Anthropic, uses max_tokens not max_completion_tokens)
        if "max_completion_tokens" in chat:
            anthropic_request["max_tokens"] = chat["max_completion_tokens"]
        elif "max_tokens" in chat:
            anthropic_request["max_tokens"] = chat["max_tokens"]
        else:
            # Anthropic requires max_tokens, set a default
            anthropic_request["max_tokens"] = 4096

        # Copy other supported parameters
        if "temperature" in chat:
            anthropic_request["temperature"] = chat["temperature"]
        if "top_p" in chat:
            anthropic_request["top_p"] = chat["top_p"]
        if "top_k" in chat:
            anthropic_request["top_k"] = chat["top_k"]
        if "stop" in chat:
            anthropic_request["stop_sequences"] = chat["stop"] if isinstance(chat["stop"], list) else [chat["stop"]]
        if "stream" in chat:
            anthropic_request["stream"] = chat["stream"]
        if "tools" in chat:
            anthropic_request["tools"] = chat["tools"]
        if "tool_choice" in chat:
            anthropic_request["tool_choice"] = chat["tool_choice"]

        _log(f"POST {self.chat_url}")
        _log(f"Anthropic Request: {json.dumps(anthropic_request, indent=2)}")

        async with aiohttp.ClientSession() as session:
            started_at = time.time()
            async with session.post(
                self.chat_url,
                headers=self.headers,
                data=json.dumps(anthropic_request),
                timeout=aiohttp.ClientTimeout(total=120),
            ) as response:
                return self.to_response(await response_json(response), chat, started_at)

    def to_response(self, response, chat, started_at):
        """Convert Anthropic response format to OpenAI-compatible format."""
        # Transform Anthropic response to OpenAI format
        openai_response = {
            "id": response.get("id", ""),
            "object": "chat.completion",
            "created": int(started_at),
            "model": response.get("model", ""),
            "choices": [],
            "usage": {},
        }

        # Transform content blocks to message content
        content_parts = []
        thinking_parts = []

        for block in response.get("content", []):
            if block.get("type") == "text":
                content_parts.append(block.get("text", ""))
            elif block.get("type") == "thinking":
                # Store thinking blocks separately (some models include reasoning)
                thinking_parts.append(block.get("thinking", ""))

        # Combine all text content
        message_content = "\n".join(content_parts) if content_parts else ""

        # Create the choice object
        choice = {
            "index": 0,
            "message": {"role": "assistant", "content": message_content},
            "finish_reason": response.get("stop_reason", "stop"),
        }

        # Add thinking as metadata if present
        if thinking_parts:
            choice["message"]["thinking"] = "\n".join(thinking_parts)

        openai_response["choices"].append(choice)

        # Transform usage
        if "usage" in response:
            usage = response["usage"]
            openai_response["usage"] = {
                "prompt_tokens": usage.get("input_tokens", 0),
                "completion_tokens": usage.get("output_tokens", 0),
                "total_tokens": usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
            }

        # Add metadata
        if "metadata" not in openai_response:
            openai_response["metadata"] = {}
        openai_response["metadata"]["duration"] = int((time.time() - started_at) * 1000)

        if chat is not None and "model" in chat:
            cost = self.model_cost(chat["model"])
            if cost and "input" in cost and "output" in cost:
                openai_response["metadata"]["pricing"] = f"{cost['input']}/{cost['output']}"

        _log(json.dumps(openai_response, indent=2))
        return openai_response


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
                "cost": {
                    "input": 0,
                    "output": 0,
                },
            }
        self.models = models

    def test(self, **kwargs):
        return True


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


# class GoogleOpenAiProvider(OpenAiCompatible):
#     sdk = "google-openai-compatible"

#     def __init__(self, api_key, **kwargs):
#         super().__init__(api="https://generativelanguage.googleapis.com", api_key=api_key, **kwargs)
#         self.chat_url = "https://generativelanguage.googleapis.com/v1beta/chat/completions"


class GoogleProvider(OpenAiCompatible):
    sdk = "@ai-sdk/google"

    def __init__(self, **kwargs):
        new_kwargs = {"api": "https://generativelanguage.googleapis.com", **kwargs}
        super().__init__(**new_kwargs)
        self.safety_settings = kwargs.get("safety_settings")
        self.thinking_config = kwargs.get("thinking_config")
        self.curl = kwargs.get("curl")
        self.headers = kwargs.get("headers", {"Content-Type": "application/json"})
        # Google fails when using Authorization header, use query string param instead
        if "Authorization" in self.headers:
            del self.headers["Authorization"]

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


async def chat_completion(chat):
    model = chat["model"]
    # get first provider that has the model
    candidate_providers = [name for name, provider in g_handlers.items() if provider.provider_model(model)]
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


def load_config(config, providers, verbose=None):
    global g_config, g_providers, g_verbose
    g_config = config
    g_providers = providers
    if verbose:
        g_verbose = verbose


def init_llms(config, providers):
    global g_config, g_handlers

    load_config(config, providers)
    g_handlers = {}
    # iterate over config and replace $ENV with env value
    for key, value in g_config.items():
        if isinstance(value, str) and value.startswith("$"):
            g_config[key] = os.environ.get(value[1:], "")

    # if g_verbose:
    #     printdump(g_config)
    providers = g_config["providers"]

    for id, orig in providers.items():
        definition = orig.copy()
        if "enabled" in definition and not definition["enabled"]:
            continue

        provider_id = definition.get("id", id)
        if "id" not in definition:
            definition["id"] = provider_id
        provider = g_providers.get(provider_id)
        constructor_kwargs = create_provider_kwargs(definition, provider)
        provider = create_provider(constructor_kwargs)

        if provider and provider.test(**constructor_kwargs):
            g_handlers[id] = provider
    return g_handlers


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
            provider["api_key"] = os.environ.get(value[1:], "")

    if "api_key" not in provider and "env" in provider:
        for env_var in provider["env"]:
            val = os.environ.get(env_var)
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


async def update_providers(home_providers_path):
    global g_providers
    text = await get_text("https://models.dev/api.json")
    all_providers = json.loads(text)

    filtered_providers = {}
    for id, provider in all_providers.items():
        if id in g_config["providers"]:
            filtered_providers[id] = provider

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
    return f"{os.environ.get('HOME')}/.llms/{filename}"


def get_cache_path(filename):
    return home_llms_path(f"cache/{filename}")


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
    home_providers_path = home_llms_path("providers.json")
    if os.path.exists(home_config_path) and os.path.exists(home_ui_path) and os.path.exists(home_providers_path):
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

        if not os.path.exists(home_providers_path):
            providers_json = await text_from_resource_or_url("providers.json")
            with open(home_providers_path, "w", encoding="utf-8") as f:
                f.write(providers_json)
            _log(f"Created default providers config at {home_providers_path}")
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


def get_session_token(request):
    return request.query.get("session") or request.headers.get("X-Session-Token") or request.cookies.get("llms-token")


class AppExtensions:
    """
    APIs extensions can use to extend the app
    """

    def __init__(self, cli_args, extra_args):
        self.cli_args = cli_args
        self.extra_args = extra_args
        self.ui_extensions = []
        self.chat_request_filters = []
        self.chat_response_filters = []
        self.server_add_get = []
        self.server_add_post = []
        self.all_providers = [
            OpenAiCompatible,
            OpenAiProvider,
            AnthropicProvider,
            MistralProvider,
            GroqProvider,
            XaiProvider,
            CodestralProvider,
            GoogleProvider,
            OllamaProvider,
            LMStudioProvider,
        ]


class ExtensionContext:
    def __init__(self, app, path):
        self.app = app
        self.path = path
        self.name = os.path.basename(path)
        self.ext_prefix = f"/ext/{self.name}"

    def log(self, message):
        print(f"[{self.name}] {message}", flush=True)

    def dbg(self, message):
        if DEBUG:
            print(f"DEBUG [{self.name}]: {message}", flush=True)

    def err(self, message, e):
        print(f"ERROR [{self.name}]: {message}", e)
        if g_verbose:
            print(traceback.format_exc(), flush=True)

    def add_provider(self, provider):
        self.log(f"Registered provider: {provider}")
        self.app.all_providers.append(provider)

    def register_ui_extension(self, index):
        path = os.path.join(self.ext_prefix, index)
        self.log(f"Registered UI extension: {path}")
        self.app.ui_extensions.append({"id": self.name, "path": path})

    def register_chat_request_filter(self, handler):
        self.log(f"Registered chat request filter: {handler}")
        self.app.chat_request_filters.append(handler)

    def register_chat_response_filter(self, handler):
        self.log(f"Registered chat response filter: {handler}")
        self.app.chat_response_filters.append(handler)

    def add_static_files(self, ext_dir):
        self.log(f"Registered static files: {ext_dir}")

        async def serve_static(request):
            path = request.match_info["path"]
            file_path = os.path.join(ext_dir, path)
            if os.path.exists(file_path):
                return web.FileResponse(file_path)
            return web.Response(status=404)

        self.app.server_add_get.append((os.path.join(self.ext_prefix, "{path:.*}"), serve_static, {}))

    def add_get(self, path, handler, **kwargs):
        self.dbg(f"Registered GET: {os.path.join(self.ext_prefix, path)}")
        self.app.server_add_get.append((os.path.join(self.ext_prefix, path), handler, kwargs))

    def add_post(self, path, handler, **kwargs):
        self.dbg(f"Registered POST: {os.path.join(self.ext_prefix, path)}")
        self.app.server_add_post.append((os.path.join(self.ext_prefix, path), handler, kwargs))

    def get_config(self):
        return g_config

    def chat_completion(self, chat):
        return chat_completion(chat)

    def get_providers(self):
        return g_handlers

    def get_provider(self, name):
        return g_handlers.get(name)

    def get_session(self, request):
        session_token = get_session_token(request)

        if not session_token or session_token not in g_sessions:
            return None

        session_data = g_sessions[session_token]
        return session_data

    def get_username(self, request):
        session = self.get_session(request)
        if session:
            return session.get("userName")
        return None


def get_extensions_path():
    return os.path.join(Path.home(), ".llms", "extensions")


def init_extensions(parser):
    extensions_path = get_extensions_path()
    os.makedirs(extensions_path, exist_ok=True)

    for item in os.listdir(extensions_path):
        item_path = os.path.join(extensions_path, item)
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
    extensions_path = get_extensions_path()
    os.makedirs(extensions_path, exist_ok=True)

    ext_count = len(os.listdir(extensions_path))
    if ext_count == 0:
        _log("No extensions found")
        return

    _log(f"Installing {ext_count} extension{'' if ext_count == 1 else 's'}...")

    sys.path.append(extensions_path)

    for item in os.listdir(extensions_path):
        item_path = os.path.join(extensions_path, item)
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

                        install_func = getattr(module, "__install__", None)
                        if callable(install_func):
                            install_func(ctx)
                            _log(f"Extension {item} installed")
                        else:
                            _dbg(f"Extension {item} has no __install__ function")
                    else:
                        _dbg(f"Extension {item} has no __init__.py")

                    # if ui folder exists, serve as static files at /ext/{item}/
                    ui_path = os.path.join(item_path, "ui")
                    if os.path.exists(ui_path):
                        ctx.add_static_files(ui_path)

                        # Register UI extension if index.mjs exists (/ext/{item}/index.mjs)
                        if os.path.exists(os.path.join(ui_path, "index.mjs")):
                            ctx.register_ui_extension("index.mjs")

                except Exception as e:
                    _err(f"Failed to install extension {item}", e)
            else:
                _dbg(f"Extension {init_file} not found")
        else:
            _dbg(f"Extension {item} not found: {item_path} is not a directory {os.path.exists(item_path)}")


def run_extension_cli():
    """
    Run the CLI for an extension.
    """
    extensions_path = get_extensions_path()
    os.makedirs(extensions_path, exist_ok=True)

    for item in os.listdir(extensions_path):
        item_path = os.path.join(extensions_path, item)
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
                        handled = run_func(ctx)
                        _log(f"Extension {item} was run")
                        return handled

                except Exception as e:
                    _err(f"Failed to run extension {item}", e)
                    return False


def main():
    global _ROOT, g_verbose, g_default_model, g_logprefix, g_providers, g_config, g_config_path, g_ui_path, g_app

    parser = argparse.ArgumentParser(description=f"llms v{VERSION}")
    parser.add_argument("--config", default=None, help="Path to config file", metavar="FILE")
    parser.add_argument("--providers", default=None, help="Path to models.dev providers file", metavar="FILE")
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
    parser.add_argument("--update", action="store_true", help="Update local models.dev providers.json")

    parser.add_argument("--root", default=None, help="Change root directory for UI files", metavar="PATH")
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

    # Load parser extensions, go through all extensions and load their parser arguments
    init_extensions(parser)

    cli_args, extra_args = parser.parse_known_args()

    g_app = AppExtensions(cli_args, extra_args)

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
    home_providers_path = home_llms_path("providers.json")

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

        if os.path.exists(home_providers_path):
            print(f"providers.json already exists at {home_providers_path}")
        else:
            asyncio.run(save_text_url(github_url("providers.json"), home_providers_path))
            print(f"Created default providers config at {home_providers_path}")
        exit(0)

    if cli_args.providers:
        if not os.path.exists(cli_args.providers):
            print(f"providers.json not found at {cli_args.providers}")
            exit(1)
        g_providers = json.loads(text_from_file(cli_args.providers))

    if cli_args.config:
        # read contents
        g_config_path = cli_args.config
        with open(g_config_path, encoding="utf-8") as f:
            config_json = f.read()
            g_config = load_config_json(config_json)

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

        if not g_providers and os.path.exists(os.path.join(config_dir, "providers.json")):
            g_providers = json.loads(text_from_file(os.path.join(config_dir, "providers.json")))

    else:
        # ensure llms.json and ui.json exist in home directory
        asyncio.run(save_home_configs())
        g_config_path = home_config_path
        g_ui_path = home_ui_path
        g_config = load_config_json(text_from_file(g_config_path))

    if not g_providers:
        g_providers = json.loads(text_from_file(home_providers_path))

    if cli_args.update:
        asyncio.run(update_providers(home_providers_path))
        print(f"Updated {home_providers_path}")
        exit(0)

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
            exit(0)

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
                    subprocess.run(
                        [sys.executable, "-m", "pip", "install", "-r", "requirements.txt"], cwd=target_path, check=True
                    )
                    print("Dependencies installed successfully.")

                print(f"Extension {target_name} installed successfully.")

            except subprocess.CalledProcessError as e:
                print(f"Failed to install extension: {e}")
                # cleanup if clone failed but directory was created (unlikely with simple git clone but good practice)
                if os.path.exists(target_path) and not os.listdir(target_path):
                    os.rmdir(target_path)

        asyncio.run(install_extension(cli_args.add))
        exit(0)

    if cli_args.remove is not None:
        if cli_args.remove == "ls":
            # List installed extensions
            extensions_path = get_extensions_path()
            extensions = os.listdir(extensions_path)
            if len(extensions) == 0:
                print("No extensions installed.")
                exit(0)
            print("Installed extensions:")
            for extension in extensions:
                print(f"  {extension}")
            exit(0)
        # Remove an extension
        extension_name = cli_args.remove
        extensions_path = get_extensions_path()
        target_path = os.path.join(extensions_path, extension_name)

        if not os.path.exists(target_path):
            print(f"Extension {extension_name} not found at {target_path}")
            exit(1)

        print(f"Removing extension: {extension_name}...")
        try:
            shutil.rmtree(target_path)
            print(f"Extension {extension_name} removed successfully.")
        except Exception as e:
            print(f"Failed to remove extension: {e}")
            exit(1)

        exit(0)

    asyncio.run(reload_providers())

    install_extensions()

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
                client_id = client_id[1:]
            if client_secret.startswith("$"):
                client_secret = client_secret[1:]

            client_id = os.environ.get(client_id, client_id)
            client_secret = os.environ.get(client_secret, client_secret)

            if (
                not client_id
                or not client_secret
                or client_id == "GITHUB_CLIENT_ID"
                or client_secret == "GITHUB_CLIENT_SECRET"
            ):
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
            session_token = get_session_token(request)
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

                # Apply pre-chat filters
                context = {"request": request}
                # Apply pre-chat filters
                context = {"request": request}
                for filter_func in g_app.chat_request_filters:
                    chat = await filter_func(chat, context)

                response = await chat_completion(chat)

                # Apply post-chat filters
                # Apply post-chat filters
                for filter_func in g_app.chat_response_filters:
                    response = await filter_func(response, context)

                return web.json_response(response)
            except Exception as e:
                return web.json_response({"error": str(e)}, status=500)

        app.router.add_post("/v1/chat/completions", chat_handler)

        async def extensions_handler(request):
            return web.json_response(g_app.ui_extensions)

        app.router.add_get("/ext", extensions_handler)

        async def models_handler(request):
            return web.json_response(get_models())

        app.router.add_get("/models/list", models_handler)

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

        async def upload_handler(request):
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

            reader = await request.multipart()

            # Read first file field
            field = await reader.next()
            while field and field.name != "file":
                field = await reader.next()

            if not field:
                return web.json_response({"error": "No file provided"}, status=400)

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
                return web.json_response(json.load(open(info_path)))

            os.makedirs(os.path.dirname(full_path), exist_ok=True)

            with open(full_path, "wb") as f:
                f.write(content)

            response_data = {
                "date": int(time.time()),
                "url": f"/~cache/{relative_path}",
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

            return web.json_response(response_data)

        app.router.add_post("/upload", upload_handler)

        async def cache_handler(request):
            path = request.match_info["tail"]
            full_path = get_cache_path(path)

            if "info" in request.query:
                info_path = os.path.splitext(full_path)[0] + ".info.json"
                if not os.path.exists(info_path):
                    return web.Response(text="404: Not Found", status=404)

                # Check for directory traversal for info path
                try:
                    cache_root = Path(get_cache_path(""))
                    requested_path = Path(info_path).resolve()
                    if not str(requested_path).startswith(str(cache_root)):
                        return web.Response(text="403: Forbidden", status=403)
                except Exception:
                    return web.Response(text="403: Forbidden", status=403)

                with open(info_path) as f:
                    content = f.read()
                return web.Response(text=content, content_type="application/json")

            if not os.path.exists(full_path):
                return web.Response(text="404: Not Found", status=404)

            # Check for directory traversal
            try:
                cache_root = Path(get_cache_path(""))
                requested_path = Path(full_path).resolve()
                if not str(requested_path).startswith(str(cache_root)):
                    return web.Response(text="403: Forbidden", status=403)
            except Exception:
                return web.Response(text="403: Forbidden", status=403)

            with open(full_path, "rb") as f:
                content = f.read()

            mimetype = get_file_mime_type(full_path)
            return web.Response(body=content, content_type=mimetype)

        app.router.add_get("/~cache/{tail:.*}", cache_handler)

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
                client_id = client_id[1:]
            if redirect_uri.startswith("$"):
                redirect_uri = redirect_uri[1:]

            client_id = os.environ.get(client_id, client_id)
            redirect_uri = os.environ.get(redirect_uri, redirect_uri)

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
                restrict_to = restrict_to[1:]

            restrict_to = os.environ.get(restrict_to, None if restrict_to == "GITHUB_USERS" else restrict_to)

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

            # Handle malformed URLs where query params are appended with & instead of ?
            if not code and "tail" in request.match_info:
                tail = request.match_info["tail"]
                if tail.startswith("&"):
                    params = parse_qs(tail[1:])
                    code = params.get("code", [None])[0]
                    state = params.get("state", [None])[0]

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
                client_id = client_id[1:]
            if client_secret.startswith("$"):
                client_secret = client_secret[1:]
            if redirect_uri.startswith("$"):
                redirect_uri = redirect_uri[1:]

            client_id = os.environ.get(client_id, client_id)
            client_secret = os.environ.get(client_secret, client_secret)
            redirect_uri = os.environ.get(redirect_uri, redirect_uri)

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
            response = web.HTTPFound(f"/?session={session_token}")
            response.set_cookie("llms-token", session_token, httponly=True, path="/", max_age=86400)
            return response

        async def session_handler(request):
            """Validate and return session info"""
            session_token = get_session_token(request)

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
            session_token = get_session_token(request)

            if session_token and session_token in g_sessions:
                del g_sessions[session_token]

            response = web.json_response({"success": True})
            response.del_cookie("llms-token")
            return response

        async def auth_handler(request):
            """Check authentication status and return user info"""
            # Check for OAuth session token
            session_token = get_session_token(request)

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
        app.router.add_get("/auth/github/callback{tail:.*}", github_callback_handler)
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

        # go through and register all g_app extensions
        for handler in g_app.server_add_get:
            app.router.add_get(handler[0], handler[1], **handler[2])
        for handler in g_app.server_add_post:
            app.router.add_post(handler[0], handler[1], **handler[2])

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

        # go through and register all g_app extensions

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
        provider_model = get_provider_model(default_model)
        if provider_model is None:
            print(f"Model {default_model} not found")
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

    handled = run_extension_cli()

    if not handled:
        # show usage from ArgumentParser
        parser.print_help()


if __name__ == "__main__":
    main()
