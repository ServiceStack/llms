#!/usr/bin/env python

# A lightweight CLI tool and OpenAI-compatible server for querying multiple Large Language Model (LLM) providers.
# Docs: https://github.com/ServiceStack/llms

import os
import time
import json
import argparse
import asyncio
import subprocess
import base64
import mimetypes
import traceback
import sys
import site

import aiohttp
from aiohttp import web

from pathlib import Path
from importlib import resources   # Py≥3.9  (pip install importlib_resources for 3.7/3.8)

VERSION = "2.0.5"
_ROOT = None
g_config_path = None
g_ui_path = None
g_config = None
g_handlers = {}
g_verbose = False
g_logprefix=""
g_default_model=""

def _log(message):
    """Helper method for logging from the global polling task."""
    if g_verbose:
        print(f"{g_logprefix}{message}", flush=True)

def printdump(obj):
    args = obj.__dict__ if hasattr(obj, '__dict__') else obj
    print(json.dumps(args, indent=2))

def print_chat(chat):
    _log(f"Chat: {chat_summary(chat)}")

def chat_summary(chat):
    """Summarize chat completion request for logging."""
    # replace image_url.url with <image>
    clone = json.loads(json.dumps(chat))
    for message in clone['messages']:
        if 'content' in message:
            if isinstance(message['content'], list):
                for item in message['content']:
                    if 'image_url' in item:
                        if 'url' in item['image_url']:
                            url = item['image_url']['url']
                            prefix = url.split(',', 1)[0]
                            item['image_url']['url'] = prefix + f",({len(url) - len(prefix)})"
                    elif 'input_audio' in item:
                        if 'data' in item['input_audio']:
                            data = item['input_audio']['data']
                            item['input_audio']['data'] = f"({len(data)})"
                    elif 'file' in item:
                        if 'file_data' in item['file']:
                            data = item['file']['file_data']
                            item['file']['file_data'] = f"({len(data)})"
    return json.dumps(clone, indent=2)

image_exts = 'png,webp,jpg,jpeg,gif,bmp,svg,tiff,ico'.split(',')
audio_exts = 'mp3,wav,ogg,flac,m4a,opus,webm'.split(',')

def is_file_path(path):
    # macOs max path is 1023
    return path and len(path) < 1024 and os.path.exists(path)

def is_url(url):
    return url and (url.startswith('http://') or url.startswith('https://'))

def get_filename(file):
    return file.rsplit('/',1)[1] if '/' in file else 'file'

def is_base_64(data):
    try:
        base64.b64decode(data)
        return True
    except Exception:
        return False

def get_file_mime_type(filename):
    mime_type, _ = mimetypes.guess_type(filename)
    return mime_type or "application/octet-stream"

async def process_chat(chat):
    if not chat:
        raise Exception("No chat provided")
    if 'stream' not in chat:
        chat['stream'] = False
    if 'messages' not in chat:
        return chat

    async with aiohttp.ClientSession() as session:
        for message in chat['messages']:
            if 'content' not in message:
                continue

            if isinstance(message['content'], list):
                for item in message['content']:
                    if 'type' not in item:
                        continue
                    if item['type'] == 'image_url' and 'image_url' in item:
                        image_url = item['image_url']
                        if 'url' in image_url:
                            url = image_url['url']
                            if is_url(url):
                                _log(f"Downloading image: {url}")
                                async with session.get(url, timeout=aiohttp.ClientTimeout(total=120)) as response:
                                    response.raise_for_status()
                                    content = await response.read()
                                    # get mimetype from response headers
                                    mimetype = get_file_mime_type(get_filename(url))
                                    if 'Content-Type' in response.headers:
                                        mimetype = response.headers['Content-Type']
                                    # convert to data uri
                                    image_url['url'] = f"data:{mimetype};base64,{base64.b64encode(content).decode('utf-8')}"
                            elif is_file_path(url):
                                _log(f"Reading image: {url}")
                                with open(url, "rb") as f:
                                    content = f.read()
                                    ext = os.path.splitext(url)[1].lower().lstrip('.') if '.' in url else 'png'
                                    # get mimetype from file extension
                                    mimetype = get_file_mime_type(get_filename(url))
                                    # convert to data uri
                                    image_url['url'] = f"data:{mimetype};base64,{base64.b64encode(content).decode('utf-8')}"
                            elif url.startswith('data:'):
                                pass
                            else:
                                raise Exception(f"Invalid image: {url}")
                    elif item['type'] == 'input_audio' and 'input_audio' in item:
                        input_audio = item['input_audio']
                        if 'data' in input_audio:
                            url = input_audio['data']
                            mimetype = get_file_mime_type(get_filename(url))
                            if is_url(url):
                                _log(f"Downloading audio: {url}")
                                async with session.get(url, timeout=aiohttp.ClientTimeout(total=120)) as response:
                                    response.raise_for_status()
                                    content = await response.read()
                                    # get mimetype from response headers
                                    if 'Content-Type' in response.headers:
                                        mimetype = response.headers['Content-Type']
                                    # convert to base64
                                    input_audio['data'] = base64.b64encode(content).decode('utf-8')
                                    input_audio['format'] = mimetype.rsplit('/',1)[1]
                            elif is_file_path(url):
                                _log(f"Reading audio: {url}")
                                with open(url, "rb") as f:
                                    content = f.read()
                                    # convert to base64
                                    input_audio['data'] = base64.b64encode(content).decode('utf-8')
                                    input_audio['format'] = mimetype.rsplit('/',1)[1]
                            elif is_base_64(url):
                                pass # use base64 data as-is
                            else:
                                raise Exception(f"Invalid audio: {url}")
                    elif item['type'] == 'file' and 'file' in item:
                        file = item['file']
                        if 'file_data' in file:
                            url = file['file_data']
                            mimetype = get_file_mime_type(get_filename(url))
                            if is_url(url):
                                _log(f"Downloading file: {url}")
                                async with session.get(url, timeout=aiohttp.ClientTimeout(total=120)) as response:
                                    response.raise_for_status()
                                    content = await response.read()
                                    file['filename'] = get_filename(url)
                                    file['file_data'] = f"data:{mimetype};base64,{base64.b64encode(content).decode('utf-8')}"
                            elif is_file_path(url):
                                _log(f"Reading file: {url}")
                                with open(url, "rb") as f:
                                    content = f.read()
                                    file['filename'] = get_filename(url)
                                    file['file_data'] = f"data:{mimetype};base64,{base64.b64encode(content).decode('utf-8')}"
                            elif is_base_64(url):
                                file['filename'] = 'file'
                                pass # use base64 data as-is
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
    def __init__(self, base_url, api_key=None, models={}, **kwargs):
        self.base_url = base_url.strip("/")
        self.api_key = api_key
        self.models = models

        self.chat_url = f"{base_url}/v1/chat/completions"
        self.headers = kwargs['headers'] if 'headers' in kwargs else {
            "Content-Type": "application/json",
        }
        if api_key is not None:
            self.headers["Authorization"] = f"Bearer {api_key}"

    @classmethod
    def test(cls, base_url=None, api_key=None, models={}, **kwargs):
        return base_url is not None and api_key is not None and len(models) > 0

    async def load(self):
        pass

    async def chat(self, chat):
        model = chat['model']
        if model in self.models:
            chat['model'] = self.models[model]

        # with open(os.path.join(os.path.dirname(__file__), 'chat.wip.json'), "w") as f:
        #     f.write(json.dumps(chat, indent=2))

        chat = await process_chat(chat)
        _log(f"POST {self.chat_url}")
        _log(chat_summary(chat))
        async with aiohttp.ClientSession() as session:
            async with session.post(self.chat_url, headers=self.headers, data=json.dumps(chat), timeout=aiohttp.ClientTimeout(total=120)) as response:
                return await response_json(response)

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
                async with session.get(f"{self.base_url}/api/tags", headers=self.headers, timeout=aiohttp.ClientTimeout(total=120)) as response:
                    data = await response_json(response)
                    for model in data.get('models', []):
                        name = model['model']
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
    def test(cls, base_url=None, models={}, all_models=False, **kwargs):
        return base_url is not None and (len(models) > 0 or all_models)

class GoogleOpenAiProvider(OpenAiProvider):
    def __init__(self, api_key, models, **kwargs):
        super().__init__(base_url="https://generativelanguage.googleapis.com", api_key=api_key, models=models, **kwargs)
        self.chat_url = "https://generativelanguage.googleapis.com/v1beta/chat/completions"

    @classmethod
    def test(cls, api_key=None, models={}, **kwargs):
        return api_key is not None and len(models) > 0

class GoogleProvider(OpenAiProvider):
    def __init__(self, models, api_key, safety_settings=None, thinking_config=None, curl=False, **kwargs):
        super().__init__(base_url="https://generativelanguage.googleapis.com", api_key=api_key, models=models, **kwargs)
        self.safety_settings = safety_settings
        self.thinking_config = thinking_config
        self.curl = curl
        self.headers = kwargs['headers'] if 'headers' in kwargs else {
            "Content-Type": "application/json",
        }
        # Google fails when using Authorization header, use query string param instead
        if 'Authorization' in self.headers:
            del self.headers['Authorization']

    @classmethod
    def test(cls, api_key=None, models={}, **kwargs):
        return api_key is not None and len(models) > 0

    async def chat(self, chat):
        model = chat['model']
        if model in self.models:
            chat['model'] = self.models[model]

        chat = await process_chat(chat)
        generationConfig = {}

        # Filter out system messages and convert to proper Gemini format
        contents = []
        system_prompt = None

        async with aiohttp.ClientSession() as session:
            for message in chat['messages']:
                if message['role'] == 'system':
                    system_prompt = message
                elif 'content' in message:
                    if isinstance(message['content'], list):
                        parts = []
                        for item in message['content']:
                            if 'type' in item:
                                if item['type'] == 'image_url' and 'image_url' in item:
                                    image_url = item['image_url']
                                    if 'url' not in image_url:
                                        continue
                                    url = image_url['url']
                                    if not url.startswith('data:'):
                                        raise(Exception("Image was not downloaded: " + url))
                                    # Extract mime type from data uri
                                    mimetype = url.split(';',1)[0].split(':',1)[1] if ';' in url else "image/png"
                                    base64Data = url.split(',',1)[1]
                                    parts.append({
                                        "inline_data": {
                                            "mime_type": mimetype,
                                            "data": base64Data
                                        }
                                    })
                                elif item['type'] == 'input_audio' and 'input_audio' in item:
                                    input_audio = item['input_audio']
                                    if 'data' not in input_audio:
                                        continue
                                    data = input_audio['data']
                                    format = input_audio['format']
                                    mimetype = f"audio/{format}"
                                    parts.append({
                                        "inline_data": {
                                            "mime_type": mimetype,
                                            "data": data
                                        }
                                    })
                                elif item['type'] == 'file' and 'file' in item:
                                    file = item['file']
                                    if 'file_data' not in file:
                                        continue
                                    data = file['file_data']
                                    if not data.startswith('data:'):
                                        raise(Exception("File was not downloaded: " + data))
                                    # Extract mime type from data uri
                                    mimetype = data.split(';',1)[0].split(':',1)[1] if ';' in data else "application/octet-stream"
                                    base64Data = data.split(',',1)[1]
                                    parts.append({
                                        "inline_data": {
                                            "mime_type": mimetype,
                                            "data": base64Data
                                        }
                                    })
                            if 'text' in item:
                                text = item['text']
                                parts.append({"text": text})
                        if len(parts) > 0:
                            contents.append({
                                "role": message['role'] if 'role' in message and message['role'] == 'user' else 'model',
                                "parts": parts
                            })
                    else:
                        content = message['content']
                        contents.append({
                                "role": message['role'] if 'role' in message and message['role'] == 'user' else 'model',
                            "parts": [{"text": content}]
                        })

            gemini_chat = {
                "contents": contents,
            }

            if self.safety_settings:
                gemini_chat['safetySettings'] = self.safety_settings

            # Add system instruction if present
            if system_prompt is not None:
                gemini_chat['systemInstruction'] = {
                    "parts": [{"text": system_prompt['content']}]
                }

            if 'stop' in chat:
                generationConfig['stopSequences'] = [chat['stop']]
            if 'temperature' in chat:
                generationConfig['temperature'] = chat['temperature']
            if 'top_p' in chat:
                generationConfig['topP'] = chat['top_p']
            if 'top_logprobs' in chat:
                generationConfig['topK'] = chat['top_logprobs']

            if 'thinkingConfig' in chat:
                generationConfig['thinkingConfig'] = chat['thinkingConfig']
            elif self.thinking_config:
                generationConfig['thinkingConfig'] = self.thinking_config

            if len(generationConfig) > 0:
                gemini_chat['generationConfig'] = generationConfig

            started_at = int(time.time() * 1000)
            gemini_chat_url = f"https://generativelanguage.googleapis.com/v1beta/models/{chat['model']}:generateContent?key={self.api_key}"

            _log(f"POST {gemini_chat_url}")
            _log(json.dumps(gemini_chat, indent=2))

            if self.curl:
                curl_args = [
                    'curl',
                    '-X', 'POST',
                    '-H', 'Content-Type: application/json',
                    '-d', json.dumps(gemini_chat),
                    gemini_chat_url
                ]
                try:
                    o = subprocess.run(curl_args, check=True, capture_output=True, text=True, timeout=120)
                    obj = json.loads(o.stdout)
                except Exception as e:
                    raise Exception(f"Error executing curl: {e}")
            else:
                async with session.post(gemini_chat_url, headers=self.headers, data=json.dumps(gemini_chat), timeout=aiohttp.ClientTimeout(total=120)) as res:
                    obj = await response_json(res)
                    _log(f"google response:\n{json.dumps(obj, indent=2)}")

            response = {
                "id": f"chatcmpl-{started_at}",
                "created": started_at,
                "model": obj.get('modelVersion', chat['model']),
            }
            choices = []
            i = 0
            if 'error' in obj:
                _log(f"Error: {obj['error']}")
                raise Exception(obj['error']['message'])
            for candidate in obj['candidates']:
                role = "assistant"
                if 'content' in candidate and 'role' in candidate['content']:
                    role = "assistant" if candidate['content']['role'] == 'model' else candidate['content']['role']

                # Safely extract content from all text parts
                content = ""
                reasoning = ""
                if 'content' in candidate and 'parts' in candidate['content']:
                    text_parts = []
                    reasoning_parts = []
                    for part in candidate['content']['parts']:
                        if 'text' in part:
                            if 'thought' in part and part['thought']:
                                reasoning_parts.append(part['text'])
                            else:
                                text_parts.append(part['text'])
                    content = ' '.join(text_parts)
                    reasoning = ' '.join(reasoning_parts)

                choice = {
                    "index": i,
                    "finish_reason": candidate.get('finishReason', 'stop'),
                    "message": {
                        "role": role,
                        "content": content,
                    },
                }
                if reasoning:
                    choice['message']['reasoning'] = reasoning
                choices.append(choice)
                i += 1
            response['choices'] = choices
            if 'usageMetadata' in obj:
                usage = obj['usageMetadata']
                response['usage'] = {
                    "completion_tokens": usage['candidatesTokenCount'],
                    "total_tokens": usage['totalTokenCount'],
                    "prompt_tokens": usage['promptTokenCount'],
                }
            return response

def get_models():
    ret = []
    for provider in g_handlers.values():
        for model in provider.models.keys():
            if model not in ret:
                ret.append(model)
    ret.sort()
    return ret

async def chat_completion(chat):
    model = chat['model']
    # get first provider that has the model
    candidate_providers = [name for name, provider in g_handlers.items() if model in provider.models]
    if len(candidate_providers) == 0:
        raise(Exception(f"Model {model} not found"))

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

async def cli_chat(chat, image=None, audio=None, file=None, raw=False):
    if g_default_model:
        chat['model'] = g_default_model

    # process_chat downloads the image, just adding the reference here
    if image is not None:
        first_message = None
        for message in chat['messages']:
            if message['role'] == 'user':
                first_message = message
                break
        image_content = {
            "type": "image_url",
            "image_url": {
                "url": image
            }
        }
        if 'content' in first_message:
            if isinstance(first_message['content'], list):
                image_url = None
                for item in first_message['content']:
                    if 'image_url' in item:
                        image_url = item['image_url']
                # If no image_url, add one
                if image_url is None:
                    first_message['content'].insert(0,image_content)
                else:
                    image_url['url'] = image
            else:
                first_message['content'] = [
                    image_content,
                    { "type": "text", "text": first_message['content'] }
                ]
    if audio is not None:
        first_message = None
        for message in chat['messages']:
            if message['role'] == 'user':
                first_message = message
                break
        audio_content = {
            "type": "input_audio",
            "input_audio": {
                "data": audio,
                "format": "mp3"
            }
        }
        if 'content' in first_message:
            if isinstance(first_message['content'], list):
                input_audio = None
                for item in first_message['content']:
                    if 'input_audio' in item:
                        input_audio = item['input_audio']
                # If no input_audio, add one
                if input_audio is None:
                    first_message['content'].insert(0,audio_content)
                else:
                    input_audio['data'] = audio
            else:
                first_message['content'] = [
                    audio_content,
                    { "type": "text", "text": first_message['content'] }
                ]
    if file is not None:
        first_message = None
        for message in chat['messages']:
            if message['role'] == 'user':
                first_message = message
                break
        file_content = {
            "type": "file",
            "file": {
                "filename": get_filename(file),
                "file_data": file
            }
        }
        if 'content' in first_message:
            if isinstance(first_message['content'], list):
                file_data = None
                for item in first_message['content']:
                    if 'file' in item:
                        file_data = item['file']
                # If no file_data, add one
                if file_data is None:
                    first_message['content'].insert(0,file_content)
                else:
                    file_data['filename'] = get_filename(file)
                    file_data['file_data'] = file
            else:
                first_message['content'] = [
                    file_content,
                    { "type": "text", "text": first_message['content'] }
                ]

    if g_verbose:
        printdump(chat)

    try:
        response = await chat_completion(chat)
        if raw:
            print(json.dumps(response, indent=2))
            exit(0)
        else:
            answer = response['choices'][0]['message']['content']
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
    providers = g_config['providers']

    for name, orig in providers.items():
        definition = orig.copy()
        provider_type = definition['type']
        if 'enabled' in definition and not definition['enabled']:
            continue

        # Replace API keys with environment variables if they start with $
        if 'api_key' in definition:
            value = definition['api_key']
            if isinstance(value, str) and value.startswith("$"):
                definition['api_key'] = os.environ.get(value[1:], "")

        # Create a copy of definition without the 'type' key for constructor kwargs
        constructor_kwargs = {k: v for k, v in definition.items() if k != 'type' and k != 'enabled'}
        constructor_kwargs['headers'] = g_config['defaults']['headers'].copy()

        if provider_type == 'OpenAiProvider' and OpenAiProvider.test(**constructor_kwargs):
            g_handlers[name] = OpenAiProvider(**constructor_kwargs)
        elif provider_type == 'OllamaProvider' and OllamaProvider.test(**constructor_kwargs):
            g_handlers[name] = OllamaProvider(**constructor_kwargs)
        elif provider_type == 'GoogleProvider' and GoogleProvider.test(**constructor_kwargs):
            g_handlers[name] = GoogleProvider(**constructor_kwargs)
        elif provider_type == 'GoogleOpenAiProvider' and GoogleOpenAiProvider.test(**constructor_kwargs):
            g_handlers[name] = GoogleOpenAiProvider(**constructor_kwargs)

    return g_handlers

async def load_llms():
    global g_handlers
    _log("Loading providers...")
    for name, provider in g_handlers.items():
        await provider.load()

def save_config(config):
    global g_config
    g_config = config
    with open(g_config_path, "w") as f:
        json.dump(g_config, f, indent=4)
        _log(f"Saved config to {g_config_path}")

def github_url(filename):
    return f"https://raw.githubusercontent.com/ServiceStack/llms/refs/heads/main/{filename}"

async def save_text(url, save_path):
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            text = await resp.text()
            if resp.status >= 400:
                raise HTTPError(resp.status, reason=resp.reason, body=text, headers=dict(resp.headers))
            os.makedirs(os.path.dirname(save_path), exist_ok=True)
            with open(save_path, "w") as f:
                f.write(text)
            return text

async def save_default_config(config_path):
    global g_config
    config_json = await save_text(github_url("llms.json"), config_path)
    g_config = json.loads(config_json)

async def update_llms():
    """
    Update llms.py from GitHub
    """    
    await save_text(github_url("llms.py"), __file__)

def provider_status():
    enabled = list(g_handlers.keys())
    disabled = [provider for provider in g_config['providers'].keys() if provider not in enabled]
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
    ui_paths = [
        home_llms_path("ui.json"),
        "ui.json"
    ]
    for ui_path in ui_paths:
        if os.path.exists(ui_path):
            return ui_path
    return None

def enable_provider(provider):
    msg = None
    provider_config = g_config['providers'][provider]
    provider_config['enabled'] = True
    if 'api_key' in provider_config:
        api_key = provider_config['api_key']
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
    provider_config = g_config['providers'][provider]
    provider_config['enabled'] = False
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
            if hasattr(pkg_files, 'is_dir') and (pkg_files / "ui").is_dir():
                _log(f"RESOURCE ROOT (package): {pkg_files}")
                return pkg_files
        except (FileNotFoundError, AttributeError, TypeError):
            # Package doesn't have the resources, try other methods
            pass
    except ImportError:
        # importlib.resources not available (Python < 3.9)
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
        possible_roots.extend([
            Path(site_dir),
            Path(site_dir).parent,
            Path(site_dir).parent / "share",
        ])

    # Add user site directory
    try:
        user_site = site.getusersitepackages()
        if user_site:
            possible_roots.extend([
                Path(user_site),
                Path(user_site).parent,
                Path(user_site).parent / "share",
            ])
    except AttributeError:
        pass

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
        if hasattr(resource_path, 'is_file'):
            return resource_path.is_file()
        else:
            return os.path.exists(resource_path)
    except (OSError, AttributeError):
        pass

def read_resource_text(resource_path):
    if hasattr(resource_path, 'read_text'):
        return resource_path.read_text()
    else:
        with open(resource_path, "r") as f:
            return f.read()

def read_resource_file_bytes(resource_file):
    try:
        if hasattr(_ROOT, 'joinpath'):
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

def main():
    global _ROOT, g_verbose, g_default_model, g_logprefix, g_config_path, g_ui_path

    parser = argparse.ArgumentParser(description=f"llms v{VERSION}")
    parser.add_argument('--config',       default=None, help='Path to config file', metavar='FILE')
    parser.add_argument('-m', '--model',  default=None, help='Model to use')

    parser.add_argument('--chat',         default=None, help='OpenAI Chat Completion Request to send', metavar='REQUEST')
    parser.add_argument('-s', '--system', default=None, help='System prompt to use for chat completion', metavar='PROMPT')
    parser.add_argument('--image',        default=None, help='Image input to use in chat completion')
    parser.add_argument('--audio',        default=None, help='Audio input to use in chat completion')
    parser.add_argument('--file',         default=None, help='File input to use in chat completion')
    parser.add_argument('--raw',          action='store_true', help='Return raw AI JSON response')

    parser.add_argument('--list',         action='store_true', help='Show list of enabled providers and their models (alias ls provider?)')

    parser.add_argument('--serve',        default=None, help='Port to start an OpenAI Chat compatible server on', metavar='PORT')

    parser.add_argument('--enable',       default=None, help='Enable a provider', metavar='PROVIDER')
    parser.add_argument('--disable',      default=None, help='Disable a provider', metavar='PROVIDER')
    parser.add_argument('--default',      default=None, help='Configure the default model to use', metavar='MODEL')

    parser.add_argument('--init',         action='store_true', help='Create a default llms.json')

    parser.add_argument('--root',         default=None, help='Change root directory for UI files', metavar='PATH')
    parser.add_argument('--logprefix',    default="",   help='Prefix used in log messages', metavar='PREFIX')
    parser.add_argument('--verbose',      action='store_true', help='Verbose output')
    parser.add_argument('--update',       action='store_true', help='Update to latest version')

    cli_args, extra_args = parser.parse_known_args()
    if cli_args.verbose:
        g_verbose = True
        # printdump(cli_args)
    if cli_args.model:
        g_default_model = cli_args.model
    if cli_args.logprefix:
        g_logprefix = cli_args.logprefix

    if cli_args.config is not None:
        g_config_path = os.path.join(os.path.dirname(__file__), cli_args.config)

    _ROOT = resolve_root()
    if cli_args.root:
        _ROOT = Path(cli_args.root)

    if not _ROOT:
        print("Resource root not found")
        exit(1)

    g_config_path = os.path.join(os.path.dirname(__file__), cli_args.config) if cli_args.config else get_config_path()
    g_ui_path = get_ui_path()

    home_config_path = home_llms_path("llms.json")
    resource_config_path = _ROOT / "llms.json"
    home_ui_path = home_llms_path("ui.json")
    resource_ui_path = _ROOT / "ui.json"

    if cli_args.init:
        if os.path.exists(home_config_path):
            print(f"llms.json already exists at {home_config_path}")
        else:
            asyncio.run(save_default_config(home_config_path))
            print(f"Created default config at {home_config_path}")

        if os.path.exists(home_ui_path):
            print(f"ui.json already exists at {home_ui_path}")
        else:
            asyncio.run(save_text(github_url("ui.json"), home_ui_path))
            print(f"Created default ui config at {home_ui_path}")
        exit(0)

    if not g_config_path or not os.path.exists(g_config_path):
        # copy llms.json and ui.json to llms_home
 
        if not os.path.exists(home_config_path) and resource_exists(resource_config_path):
            llms_home = os.path.dirname(home_config_path)
            os.makedirs(llms_home, exist_ok=True)

            # Read config from resource (handle both Path and Traversable objects)
            try:
                config_json = read_resource_text(resource_config_path)
                with open(home_config_path, "w") as f:
                    f.write(config_json)
                    _log(f"Created default config at {home_config_path}")
            except (OSError, AttributeError) as e:
                _log(f"Error reading resource config: {e}")

            # Read UI config from resource
            if not os.path.exists(home_ui_path) and resource_exists(resource_ui_path):
                try:
                    ui_json = read_resource_text(resource_ui_path)
                    with open(home_ui_path, "w") as f:
                        f.write(ui_json)
                        _log(f"Created default ui config at {home_ui_path}")
                except (OSError, AttributeError) as e:
                    _log(f"Error reading resource ui config: {e}")

            # Update g_config_path to point to the copied file
            g_config_path = home_config_path
        else:
            print("Config file not found. Create one with --init or use --config <path>")
            exit(1)

    # read contents
    with open(g_config_path, "r") as f:
        config_json = f.read()
        init_llms(json.loads(config_json))
        asyncio.run(load_llms())

    # print names
    _log(f"enabled providers: {', '.join(g_handlers.keys())}")

    filter_list = []
    if len(extra_args) > 0:
        arg = extra_args[0]
        if arg == 'ls':
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

    if cli_args.serve is not None:
        port = int(cli_args.serve)

        app = web.Application()

        async def chat_handler(request):
            try:
                chat = await request.json()
                response = await chat_completion(chat)
                return web.json_response(response)
            except Exception as e:
                return web.json_response({"error": str(e)}, status=500)
        app.router.add_post('/v1/chat/completions', chat_handler)

        async def models_handler(request):
            return web.json_response(get_models())
        app.router.add_get('/models', models_handler)

        async def status_handler(request):
            enabled, disabled = provider_status()
            return web.json_response({
                "all": list(g_config['providers'].keys()),
                "enabled": enabled,
                "disabled": disabled,
            })
        app.router.add_get('/status', status_handler)

        async def provider_handler(request):
            provider = request.match_info.get('provider', "")
            data = await request.json()
            msg = None
            if provider:                
                if data.get('enable', False):
                    provider_config, msg = enable_provider(provider)
                    _log(f"Enabled provider {provider}")
                    await load_llms()
                elif data.get('disable', False):
                    disable_provider(provider)
                    _log(f"Disabled provider {provider}")
            enabled, disabled = provider_status()
            return web.json_response({
                "enabled": enabled,
                "disabled": disabled,
                "feedback": msg or "",
            })
        app.router.add_post('/providers/{provider}', provider_handler)

        async def ui_static(request: web.Request) -> web.Response:
            path = Path(request.match_info["path"])

            try:
                # Handle both Path objects and importlib.resources Traversable objects
                if hasattr(_ROOT, 'joinpath'):
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
                    except ValueError:
                        raise web.HTTPBadRequest(text="Invalid path")
                    content = resource.read_bytes()

                content_type, _ = mimetypes.guess_type(str(path))
                if content_type is None:
                    content_type = "application/octet-stream"
                return web.Response(body=content, content_type=content_type)
            except (OSError, PermissionError, AttributeError):
                raise web.HTTPNotFound

        app.router.add_get("/ui/{path:.*}", ui_static, name="ui_static")

        async def not_found_handler(request):
            return web.Response(text="404: Not Found", status=404)
        app.router.add_get('/favicon.ico', not_found_handler)

        # Serve index.html from root
        async def index_handler(request):
            index_content = read_resource_file_bytes("index.html")
            if index_content is None:
                raise web.HTTPNotFound
            return web.Response(body=index_content, content_type='text/html')
        app.router.add_get('/', index_handler)

        # Serve index.html as fallback route (SPA routing)
        app.router.add_route('*', '/{tail:.*}', index_handler)
        
        if os.path.exists(g_ui_path):
            async def ui_json_handler(request):
                with open(g_ui_path, "r") as f:
                    ui = json.load(f)
                    if 'defaults' not in ui:
                        ui['defaults'] = g_config['defaults']
                    enabled, disabled = provider_status()
                    ui['status'] = {
                        "all": list(g_config['providers'].keys()),
                        "enabled": enabled, 
                        "disabled": disabled 
                    }
                    return web.json_response(ui)
            app.router.add_get('/ui.json', ui_json_handler)

        print(f"Starting server on port {port}...")
        web.run_app(app, host='0.0.0.0', port=port)
        exit(0)

    if cli_args.enable is not None:
        if cli_args.enable.endswith(','):
            cli_args.enable = cli_args.enable[:-1].strip()
        enable_providers = [cli_args.enable]
        all_providers = g_config['providers'].keys()
        msgs = []
        if len(extra_args) > 0:
            for arg in extra_args:
                if arg.endswith(','):
                    arg = arg[:-1].strip()
                if arg in all_providers:
                    enable_providers.append(arg)

        for provider in enable_providers:
            if provider not in g_config['providers']:
                print(f"Provider {provider} not found")
                print(f"Available providers: {', '.join(g_config['providers'].keys())}")
                exit(1)
            if provider in g_config['providers']:
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
        if cli_args.disable.endswith(','):
            cli_args.disable = cli_args.disable[:-1].strip()
        disable_providers = [cli_args.disable]
        all_providers = g_config['providers'].keys()
        if len(extra_args) > 0:
            for arg in extra_args:
                if arg.endswith(','):
                    arg = arg[:-1].strip()
                if arg in all_providers:
                    disable_providers.append(arg)

        for provider in disable_providers:
            if provider not in g_config['providers']:
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
        default_text = g_config['defaults']['text']
        default_text['model'] = default_model
        save_config(g_config)
        print(f"\nDefault model set to: {default_model}")
        exit(0)

    if cli_args.update:
        asyncio.run(update_llms())
        print(f"{__file__} updated")
        exit(0)

    if cli_args.chat is not None or cli_args.image is not None or cli_args.audio is not None or cli_args.file is not None or len(extra_args) > 0:
        try:
            chat = g_config['defaults']['text']
            if cli_args.image is not None:
                chat = g_config['defaults']['image']
            elif cli_args.audio is not None:
                chat = g_config['defaults']['audio']
            elif cli_args.file is not None:
                chat = g_config['defaults']['file']
            if cli_args.chat is not None:
                chat_path = os.path.join(os.path.dirname(__file__), cli_args.chat)
                if not os.path.exists(chat_path):
                    print(f"Chat request template not found: {chat_path}")
                    exit(1)
                _log(f"Using chat: {chat_path}")

                with open (chat_path, "r") as f:
                    chat_json = f.read()
                    chat = json.loads(chat_json)

            if cli_args.system is not None:
                chat['messages'].insert(0, {'role': 'system', 'content': cli_args.system})

            if len(extra_args) > 0:
                prompt = ' '.join(extra_args)
                # replace content of last message if exists, else add
                last_msg = chat['messages'][-1]
                if last_msg['role'] == 'user':
                    last_msg['content'] = prompt
                else:
                    chat['messages'].append({'role': 'user', 'content': prompt})

            asyncio.run(cli_chat(chat, image=cli_args.image, audio=cli_args.audio, file=cli_args.file, raw=cli_args.raw))
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
