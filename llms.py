#!/usr/bin/env python

import os
import time
import json
import argparse
import asyncio
import subprocess
import base64
import traceback

import aiohttp
from aiohttp import web

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

async def process_chat(chat):
    if not chat:
        raise Exception("No chat provided")
    if not 'stream' in chat:
        chat['stream'] = False
    if not 'messages' in chat:
        return chat

    async with aiohttp.ClientSession() as session:
        for message in chat['messages']:
            if not 'content' in message:
                continue
            if isinstance(message['content'], list):
                for item in message['content']:
                    if not ('type' in item and item['type'] == 'image_url' and 'image_url' in item):
                        continue
                    image_url = item['image_url']
                    if not 'url' in image_url:
                        continue
                    url = image_url['url']
                    if not url.startswith('http'):
                        continue
                    _log(f"Downloading image: {url}")
                    async with session.get(url, timeout=aiohttp.ClientTimeout(total=120)) as response:
                        response.raise_for_status()
                        content = await response.read()
                        # get mimetype from response headers
                        mimetype = "image/png"
                        if 'Content-Type' in response.headers:
                            mimetype = response.headers['Content-Type']
                        # convert to data uri
                        image_url['url'] = f"data:{mimetype};base64,{base64.b64encode(content).decode('utf-8')}"
    _log(f"process_chat: {json.dumps(chat, indent=2)}")
    return chat

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

    async def chat(self, chat):
        model = chat['model']
        if model in self.models:
            chat['model'] = self.models[model]

        # with open(os.path.join(os.path.dirname(__file__), 'chat.wip.json'), "w") as f:
        #     f.write(json.dumps(chat, indent=2))

        chat = await process_chat(chat)
        async with aiohttp.ClientSession() as session:
            async with session.post(self.chat_url, headers=self.headers, data=json.dumps(chat), timeout=aiohttp.ClientTimeout(total=120)) as response:
                response.raise_for_status()
                body = await response.json()
                return body

class OllamaProvider(OpenAiProvider):
    def __init__(self, base_url, models, all_models=False, **kwargs):
        super().__init__(base_url=base_url, models=models, **kwargs)
        if all_models:
            # Note: get_models is now async, so we'll need to handle this differently
            # For now, we'll initialize with empty models and populate them later
            self.models = {}
            self._all_models = True
        else:
            self._all_models = False

    async def get_models(self):
        ret = {}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{self.base_url}/api/tags", headers=self.headers, timeout=aiohttp.ClientTimeout(total=120)) as response:
                    response.raise_for_status()
                    data = await response.json()
                    for model in data.get('models', []):
                        name = model['model']
                        if name.endswith(":latest"):
                            name = name[:-7]
                        ret[name] = name
        except Exception as e:
            _log(f"Error getting Ollama models: {e}")
            # return empty dict if ollama is not available
        return ret

    async def initialize_models(self):
        """Initialize models if all_models was requested"""
        if self._all_models:
            self.models = await self.get_models()

    @classmethod
    def test(cls, base_url=None, models={}, **kwargs):
        return base_url is not None and len(models) > 0

class GoogleOpenAiProvider(OpenAiProvider):
    def __init__(self, api_key, models, **kwargs):
        super().__init__(base_url="https://generativelanguage.googleapis.com", api_key=api_key, models=models, **kwargs)
        self.chat_url = "https://generativelanguage.googleapis.com/v1beta/chat/completions"

    @classmethod
    def test(cls, api_key=None, models={}, **kwargs):
        return api_key is not None and len(models) > 0

class GoogleProvider(OpenAiProvider):
    def __init__(self, models, api_key, safety_settings=None, curl=False, **kwargs):
        super().__init__(base_url="https://generativelanguage.googleapis.com", api_key=api_key, models=models, **kwargs)
        self.safety_settings = safety_settings
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
                            if ('type' in item and item['type'] == 'image_url' and 'image_url' in item):
                                image_url = item['image_url']
                                if not 'url' in image_url:
                                    continue
                                url = image_url['url']
                                if not url.startswith('http'):
                                    continue
                                _log(f"Downloading image: {url}")
                                async with session.get(url, timeout=aiohttp.ClientTimeout(total=120)) as response:
                                    response.raise_for_status()
                                    content = await response.read()
                                    # get mimetype from response headers
                                    mimetype = "image/png"
                                    if 'Content-Type' in response.headers:
                                        mimetype = response.headers['Content-Type']
                                        if ';' in mimetype:
                                            mimetype = mimetype.split(';')[0]
                                        if not mimetype.startswith('image/'):
                                            mimetype = "image/png"
                                    parts.append({
                                        "inline_data": {
                                            "mime_type": mimetype,
                                            "data": base64.b64encode(content).decode('utf-8')
                                        }
                                    })
                            elif 'text' in item:
                                text = item['text']
                                parts.append({"text": text})
                        if len(parts) > 0:
                            contents.append({
                                "parts": parts
                            })
                    else:
                        content = message['content']
                        contents.append({
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
            if len(generationConfig) > 0:
                gemini_chat['generationConfig'] = generationConfig

            started_at = int(time.time() * 1000)
            gemini_chat_url = f"https://generativelanguage.googleapis.com/v1beta/models/{chat['model']}:generateContent?key={self.api_key}"

            _log(f"gemini_chat: {gemini_chat_url}")
            if g_verbose:
                print(json.dumps(gemini_chat))

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
                    res.raise_for_status()
                    obj = await res.json()

            response = {
                "id": f"chatcmpl-{started_at}",
                "created": started_at,
                "model": obj.get('modelVersion', chat['model']),
            }
            choices = []
            i = 0
            _log(json.dumps(obj))
            if 'error' in obj:
                _log(f"Error: {obj['error']}")
                raise Exception(obj['error']['message'])
            for candidate in obj['candidates']:
                role = "assistant"
                if 'content' in candidate and 'role' in candidate['content']:
                    role = "assistant" if candidate['content']['role'] == 'model' else candidate['content']['role']

                # Safely extract content from all text parts
                content = ""
                if 'content' in candidate and 'parts' in candidate['content']:
                    text_parts = []
                    for part in candidate['content']['parts']:
                        if 'text' in part:
                            text_parts.append(part['text'])
                    content = ' '.join(text_parts)

                choices.append({
                    "index": i,
                    "finish_reason": candidate.get('finishReason', 'stop'),
                    "message": {
                        "role": role,
                        "content": content
                    },
                })
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

async def cli_chat(chat, raw=False):
    if g_default_model:
        chat['model'] = g_default_model
    if g_verbose:
        printdump(chat)
    response = await chat_completion(chat)
    if raw:
        print(json.dumps(response, indent=2))
        exit(0)
    else:
        answer = response['choices'][0]['message']['content']
        print(answer)

def config_str(key):
    return key in g_config and g_config[key] or None

def init_llms(config):
    global g_config

    g_config = config
    # iterate over config and replace $ENV with env value
    for key, value in g_config.items():
        if isinstance(value, str) and value.startswith("$"):
            g_config[key] = os.environ.get(value[1:], "")

    if g_verbose:
        printdump(g_config)
    providers = g_config['providers']

    for name, definition in providers.items():
        provider_type = definition['type']

        # Replace API keys with environment variables if they start with $
        if 'api_key' in definition:
            value = definition['api_key']
            if isinstance(value, str) and value.startswith("$"):
                definition['api_key'] = os.environ.get(value[1:], "")

        # Create a copy of definition without the 'type' key for constructor kwargs
        constructor_kwargs = {k: v for k, v in definition.items() if k != 'type'}
        constructor_kwargs['headers'] = g_config['defaults']['headers'].copy()

        if provider_type == 'OpenAiProvider' and OpenAiProvider.test(**constructor_kwargs):
            g_handlers[name] = OpenAiProvider(**constructor_kwargs)
        elif provider_type == 'OllamaProvider' and OllamaProvider.test(**constructor_kwargs):
            provider = OllamaProvider(**constructor_kwargs)
            # Initialize models if all_models was requested
            if hasattr(provider, '_all_models') and provider._all_models:
                asyncio.run(provider.initialize_models())
            g_handlers[name] = provider
        elif provider_type == 'GoogleProvider' and GoogleProvider.test(**constructor_kwargs):
            g_handlers[name] = GoogleProvider(**constructor_kwargs)
        elif provider_type == 'GoogleOpenAiProvider' and GoogleOpenAiProvider.test(**constructor_kwargs):
            g_handlers[name] = GoogleOpenAiProvider(**constructor_kwargs)

    return g_handlers


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='llms')
    parser.add_argument('--config',       default=None, help='Path to config file', metavar='FILE')
    parser.add_argument('-m', '--model',  default=None, help='Model to use')
    parser.add_argument('--logprefix',    default="",   help='Prefix used in log messages', metavar='PREFIX')
    parser.add_argument('--verbose',      action='store_true', help='Verbose output')
    parser.add_argument('--raw',          action='store_true', help='Return raw AI JSON response')

    parser.add_argument('--chat',         default=None, help='OpenAI Chat Completion Request to send', metavar='REQUEST')
    parser.add_argument('-s', '--system', default=None, help='System prompt to use for chat completion', metavar='PROMPT')

    parser.add_argument('--list',         action='store_true', help='Show list of enabled providers and their models (alias ls)')

    parser.add_argument('--serve',        default=None, help='Port to start an OpenAI Chat compatible server on', metavar='PORT')

    cli_args, extra_args = parser.parse_known_args()
    if cli_args.verbose:
        g_verbose = True
        printdump(cli_args)
    if cli_args.model:
        g_default_model = cli_args.model
    if cli_args.logprefix:
        g_logger_prefix = cli_args.logprefix

    if cli_args.config is not None:
        full_config_path = os.path.join(os.path.dirname(__file__), cli_args.config)

    config_path = cli_args.config
    if config_path:
        full_config_path = os.path.join(os.path.dirname(__file__), config_path)
    else:
        check_paths = [
            "./llms.json",
            "../../user/comfy_agent/llms.json",
        ]
        for check_path in check_paths:
            full_config_path = os.path.join(os.path.dirname(__file__), check_path)
            if os.path.exists(full_config_path):
                break

    if not os.path.exists(full_config_path):
        _log("Config file not found. Usage --config <path>")
        exit(1)

    # read contents
    with open(full_config_path, "r") as f:
        config_json = f.read()
        init_llms(json.loads(config_json))

    # print names
    _log(f"enabled providers: {g_handlers.keys()}")

    if len(extra_args) > 0:
        arg = extra_args[0]
        if arg == 'ls':
            cli_args.list = True

    if cli_args.list:
        # Show list of enabled providers and their models
        for name, provider in g_handlers.items():
            print(f"{name}:")
            for model in provider.models:
                print(f"  {model}")
        exit(0)

    if cli_args.serve is not None:
        port = int(cli_args.serve)

        async def chat_handler(request):
            try:
                chat = await request.json()
                response = await chat_completion(chat)
                return web.json_response(response)
            except Exception as e:
                return web.json_response({"error": str(e)}, status=500)

        app = web.Application()
        app.router.add_post('/v1/chat/completions', chat_handler)

        _log(f"Starting server on port {port}...")
        web.run_app(app, host='0.0.0.0', port=port)
        exit(0)

    if cli_args.chat is not None or len(extra_args) > 0:
        try:
            chat = g_config['defaults']['text']
            if cli_args.chat is not None:
                chat_path = os.path.join(os.path.dirname(__file__), cli_args.chat)
                if not os.path.exists(chat_path):
                    _log(f"Chat file not found: {chat_path}")
                    exit(1)
                _log(f"chat_path: {chat_path}")

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
            asyncio.run(cli_chat(chat, raw=cli_args.raw))
            exit(0)
        except Exception as e:
            print(f"{cli_args.logprefix}Error: {e}")
            if cli_args.verbose:
                traceback.print_exc()
            exit(1)

    # show usage from ArgumentParser
    parser.print_help()
