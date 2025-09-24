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
import traceback

import aiohttp
from aiohttp import web

VERSION = 1
g_config_path = None
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
    return json.dumps(clone, indent=2)

image_exts = 'png,webp,jpg,jpeg,gif,bmp,svg,tiff,ico'.split(',')

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
                    if not ('type' in item and item['type'] == 'image_url' and 'image_url' in item):
                        continue
                    image_url = item['image_url']
                    if 'url' not in image_url:
                        continue
                    url = image_url['url']
                    if url.startswith('http'):
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
                    elif url.startswith('/') or url.startswith('.'):
                        _log(f"Reading image: {url}")
                        with open(url, "rb") as f:
                            content = f.read()
                            ext = os.path.splitext(url)[1].lower().lstrip('.') if '.' in url else 'png'
                            # get mimetype from file extension
                            mimetype = f"image/{ext}" if ext in image_exts else "image/png"
                            # convert to data uri
                            image_url['url'] = f"data:{mimetype};base64,{base64.b64encode(content).decode('utf-8')}"
                    elif url.startswith('data:'):
                        pass
                    else:
                        raise Exception(f"Invalid image url: {url}")
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

    async def load(self):
        pass

    async def chat(self, chat):
        model = chat['model']
        if model in self.models:
            chat['model'] = self.models[model]

        # with open(os.path.join(os.path.dirname(__file__), 'chat.wip.json'), "w") as f:
        #     f.write(json.dumps(chat, indent=2))

        chat = await process_chat(chat)
        print_chat(chat)
        async with aiohttp.ClientSession() as session:
            async with session.post(self.chat_url, headers=self.headers, data=json.dumps(chat), timeout=aiohttp.ClientTimeout(total=120)) as response:
                response.raise_for_status()
                body = await response.json()
                return body

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
                async with session.get(f"{self.base_url}/api/tags", headers=self.headers, timeout=aiohttp.ClientTimeout(total=120)) as response:
                    response.raise_for_status()
                    data = await response.json()
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
                            if ('type' in item and item['type'] == 'image_url' and 'image_url' in item):
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

async def cli_chat(chat, image=None, raw=False):
    if g_default_model:
        chat['model'] = g_default_model

    if image is not None:
        # process_chat downloads the image, just adding the reference here
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

async def save_default_config(config_path):
    """
    Download default config from https://raw.githubusercontent.com/ServiceStack/llms/refs/heads/main/llms.json using asyncio
    """
    global g_config
    url = "https://raw.githubusercontent.com/ServiceStack/llms/refs/heads/main/llms.json"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            resp.raise_for_status()
            config_json = await resp.text()
            os.makedirs(os.path.dirname(config_path), exist_ok=True)
            with open(config_path, "w") as f:
                f.write(config_json)
            g_config = json.loads(config_json)

async def update_llms():
    """
    Update llms.py and llms.json from https://raw.githubusercontent.com/ServiceStack/llms/refs/heads/main/llms.py
    """
    url = "https://raw.githubusercontent.com/ServiceStack/llms/refs/heads/main/llms.py"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            resp.raise_for_status()
            llms_py = await resp.text()
            with open(__file__, "w") as f:
                f.write(llms_py)

def print_status():
    enabled = list(g_handlers.keys())
    disabled = [provider for provider in g_config['providers'].keys() if provider not in enabled]

    enabled.sort()
    disabled.sort()
    if len(enabled) > 0:
        print(f"\nEnabled: {', '.join(enabled)}")
    else:
        print("\nEnabled: None")
    if len(disabled) > 0:
        print(f"Disabled: {', '.join(disabled)}")
    else:
        print("Disabled: None")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=f"llms v{VERSION}")
    parser.add_argument('--config',       default=None, help='Path to config file', metavar='FILE')
    parser.add_argument('-m', '--model',  default=None, help='Model to use')
    parser.add_argument('--logprefix',    default="",   help='Prefix used in log messages', metavar='PREFIX')
    parser.add_argument('--verbose',      action='store_true', help='Verbose output')
    parser.add_argument('--raw',          action='store_true', help='Return raw AI JSON response')

    parser.add_argument('--chat',         default=None, help='OpenAI Chat Completion Request to send', metavar='REQUEST')
    parser.add_argument('-s', '--system', default=None, help='System prompt to use for chat completion', metavar='PROMPT')
    parser.add_argument('--image',        default=None, help='Image prompt to use in chat completion', metavar='IMAGE')

    parser.add_argument('--list',         action='store_true', help='Show list of enabled providers and their models (alias ls provider?)')

    parser.add_argument('--serve',        default=None, help='Port to start an OpenAI Chat compatible server on', metavar='PORT')

    parser.add_argument('--init',         action='store_true', help='Create a default llms.json')
    parser.add_argument('--enable',       default=None, help='Enable a provider', metavar='PROVIDER')
    parser.add_argument('--disable',      default=None, help='Disable a provider', metavar='PROVIDER')
    parser.add_argument('--default',      default=None, help='Configure the default model to use', metavar='MODEL')
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

    config_path = cli_args.config
    if config_path:
        g_config_path = os.path.join(os.path.dirname(__file__), config_path)
    else:
        home_config_path = f"{os.environ.get('HOME')}/.llms/llms.json"
        check_paths = [
            "./llms.json",
            home_config_path,
        ]
        if os.environ.get("LLMS_CONFIG_PATH"):
            check_paths.insert(0, os.environ.get("LLMS_CONFIG_PATH"))

        for check_path in check_paths:
            g_config_path = os.path.join(os.path.dirname(__file__), check_path)
            if os.path.exists(g_config_path):
                break

    if cli_args.init:
        if os.path.exists(g_config_path):
            print(f"llms.json already exists at {g_config_path}")
            exit(1)
        save_config_path = g_config_path or home_config_path
        asyncio.run(save_default_config(save_config_path))
        print(f"Created default config at {save_config_path}")
        exit(0)

    if not os.path.exists(g_config_path):
        print("Config file not found. Usage --config <path>")
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

        async def chat_handler(request):
            try:
                chat = await request.json()
                response = await chat_completion(chat)
                return web.json_response(response)
            except Exception as e:
                return web.json_response({"error": str(e)}, status=500)

        app = web.Application()
        app.router.add_post('/v1/chat/completions', chat_handler)

        print(f"Starting server on port {port}...")
        web.run_app(app, host='0.0.0.0', port=port)
        exit(0)

    if cli_args.enable is not None:
        if cli_args.enable.endswith(','):
            cli_args.enable = cli_args.enable[:-1].strip()
        enable_providers = [cli_args.enable]
        all_providers = g_config['providers'].keys()
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
                g_config['providers'][provider]['enabled'] = True
                save_config(g_config)
                init_llms(g_config)
                print(f"\nEnabled provider {provider}:")
                printdump(g_config['providers'][provider])
        print_status()
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
            if provider in g_config['providers']:
                g_config['providers'][provider]['enabled'] = False
                save_config(g_config)
                init_llms(g_config)
                print(f"\nDisabled provider {provider}")
                printdump(g_config['providers'][provider])
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

    #TEST
    if cli_args.update:
        asyncio.run(update_llms())
        print(f"{__file__} updated")
        exit(0)

    if cli_args.chat is not None or cli_args.image is not None or len(extra_args) > 0:
        try:
            chat = g_config['defaults']['text']
            if cli_args.image is not None:
                chat = g_config['defaults']['image']
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

            asyncio.run(cli_chat(chat, image=cli_args.image, raw=cli_args.raw))
            exit(0)
        except Exception as e:
            print(f"{cli_args.logprefix}Error: {e}")
            if cli_args.verbose:
                traceback.print_exc()
            exit(1)

    # show usage from ArgumentParser
    parser.print_help()
