import base64
import io
import json
import os
import time
import wave

import aiohttp


def install_openrouter(ctx):
    from llms.main import GeneratorBase, OpenAiCompatible

    # https://openrouter.ai/docs/guides/overview/multimodal/image-generation
    class OpenRouterImageGenerator(GeneratorBase):
        sdk = "openrouter/image"

        def __init__(self, **kwargs):
            super().__init__(**kwargs)

        def to_response(self, response, chat, started_at, context=None):
            # go through all image responses and save them to cache
            cost = None
            if "usage" in response and "cost" in response["usage"]:
                cost = response["usage"]["cost"]
            for choice in response["choices"]:
                if "message" in choice and "images" in choice["message"]:
                    for image in choice["message"]["images"]:
                        if choice["message"]["content"] == "":
                            choice["message"]["content"] = self.default_content
                        if "image_url" in image:
                            data_uri = image["image_url"]["url"]
                            if data_uri.startswith("data:"):
                                parts = data_uri.split(",", 1)
                                ext = parts[0].split(";")[0].split("/")[1]
                                base64_data = parts[1]
                                model = chat["model"].split("/")[-1]
                                filename = f"{model}-{choice['index']}.{ext}"
                                relative_url, info = ctx.save_image_to_cache(
                                    base64_data, filename, ctx.to_file_info(chat, {"cost": cost}), context=context
                                )
                                image["image_url"]["url"] = relative_url

            return response

        async def process_chat(self, chat, provider_id=None):
            clone = json.loads(json.dumps(chat))
            # remove tools from chat
            clone.pop("tools", None)
            # OpenRouter doesn't support multiple messages for image generation
            clone["messages"] = [clone["messages"][-1]]
            return await super().process_chat(clone, provider_id)

        async def chat(self, chat, provider=None, context=None):
            headers = self.get_headers(provider, chat)
            if provider is not None:
                chat["model"] = provider.provider_model(chat["model"]) or chat["model"]

            started_at = time.time()
            if ctx.MOCK:
                print("Mocking OpenRouterGenerator")
                text = ctx.text_from_file(f"{ctx.MOCK_DIR}/openrouter-image.json")
                return ctx.log_json(self.to_response(json.loads(text), chat, started_at))
            else:
                chat_url = provider.chat_url
                # most image models fail if specifying text modality, e.g: ["image","text"]
                chat["modalities"] = ["image"]
                chat = await self.process_chat(chat, provider_id=self.id)
                ctx.log(f"POST {chat_url}")
                ctx.log(provider.chat_summary(chat))
                # remove metadata if any (conflicts with some providers, e.g. Z.ai)
                metadata = chat.pop("metadata", None)

                async with aiohttp.ClientSession() as session, session.post(
                    chat_url,
                    headers=headers,
                    data=json.dumps(chat),
                    timeout=aiohttp.ClientTimeout(total=300),
                ) as response:
                    if metadata:
                        chat["metadata"] = metadata
                    return ctx.log_json(
                        self.to_response(await self.response_json(response), chat, started_at, context=context)
                    )

    class OpenRouterTextToSpeech(GeneratorBase):
        sdk = "openrouter/text-to-speech"

        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.response_format = kwargs.get("response_format", "mp3")
            self.speed = kwargs.get("speed")
            self.default_content = "I've generated the audio for you."

        def to_response(self, audio_data, chat, started_at, provider=None, context=None):
            model = chat.get("model", "")

            prompt = ctx.last_user_prompt(chat)
            cost = 0.0
            pricing_info = None

            if provider:
                model_info = provider.model_info(model)
                if model_info and "cost" in model_info:
                    pricing = model_info["cost"]
                    input_pricing = pricing.get("input", 0.0)
                    cost = (len(prompt) / 1000000.0) * input_pricing
                    if "input" in pricing and "output" in pricing:
                        pricing_info = f"{pricing['input']}/{pricing['output']}"

            model_defaults = model_info.get("defaults", {}) if model_info else {}
            response_format = chat.get("response_format") or model_defaults.get("response_format") or "mp3"

            if response_format == "pcm":
                wav_io = io.BytesIO()
                with wave.open(wav_io, "wb") as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(2)
                    wf.setframerate(24000)
                    wf.writeframes(audio_data)
                audio_data = wav_io.getvalue()
                response_format = "wav"

            filename = f"{model.split('/')[-1]}.{response_format}"

            relative_url, info = ctx.save_bytes_to_cache(
                audio_data, filename, ctx.to_file_info(chat, {"cost": cost}), context=context
            )

            duration = int((time.time() - started_at) * 1000)

            response = {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": self.default_content,
                            "audios": [
                                {
                                    "type": "audio_url",
                                    "audio_url": {
                                        "url": relative_url,
                                    },
                                }
                            ],
                        }
                    }
                ],
                "created": int(time.time()),
                "cost": cost,
                "usage": {
                    "prompt_tokens": len(prompt),
                    "completion_tokens": 0,
                    "total_tokens": len(prompt),
                    "cost": cost,
                },
                "metadata": {"duration": duration},
            }

            if pricing_info:
                response["metadata"]["pricing"] = pricing_info

            if context is not None:
                context["providerResponse"] = response

            return response

        async def chat(self, chat, provider=None, context=None):
            headers = self.get_headers(provider, chat)
            if provider is not None:
                chat["model"] = provider.provider_model(chat["model"]) or chat["model"]

            model = chat["model"]
            if provider:
                model_info = provider.model_info(model)
            if not model_info:
                ctx.err(f"Could not find model_info for {model}")
                raise Exception(f"Could not find model_info for {model}")
            model_defaults = model_info.get("defaults", {})
            model_metadata = chat.get("metadata", {})

            started_at = time.time()
            api = self.api or (provider.api if provider else None) or "https://openrouter.ai/api/v1"
            api_url = f"{api.rstrip('/')}/audio/speech"

            prompt = ctx.last_user_prompt(chat)
            speed = chat.get("speed") or self.speed

            payload = {
                "model": model,
                "input": prompt,
                "voice": model_metadata.get("voice") or model_defaults.get("voice"),
                "response_format": chat.get("response_format")
                or model_metadata.get("response_format")
                or model_defaults.get("response_format")
                or "mp3",
            }

            provider_options = model_defaults.get("options")
            if provider_options:
                payload["provider"] = {"options": provider_options}

            if speed is not None:
                payload["speed"] = speed

            ctx.log(f"POST {api_url}")
            ctx.log(json.dumps(payload, indent=2))

            if ctx.MOCK:
                print("Mocking OpenRouterTextToSpeech")
                audio_data = b"MOCK_AUDIO_DATA_FOR_OPENROUTER_TTS"
                mock_file = f"{ctx.MOCK_DIR}/openrouter-speech.mp3"
                if os.path.exists(mock_file):
                    with open(mock_file, "rb") as f:
                        audio_data = f.read()
                return self.to_response(audio_data, chat, started_at, provider=provider, context=context)
            else:
                async with aiohttp.ClientSession() as session, session.post(
                    api_url,
                    headers=headers,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=300),
                ) as response:
                    if response.status < 300:
                        audio_data = await response.read()
                        generation_id = response.headers.get("X-Generation-Id")
                        if generation_id:
                            ctx.log(f"X-Generation-Id: {generation_id}")
                        return self.to_response(audio_data, chat, started_at, provider=provider, context=context)
                    else:
                        text = await response.text()
                        try:
                            data = json.loads(text)
                            if "error" in data and "message" in data["error"]:
                                raise Exception(data["error"]["message"])
                        except json.JSONDecodeError:
                            pass
                        raise Exception(f"Failed to generate speech {response.status}: {text}")

    class OpenRouterProvider(OpenAiCompatible):
        sdk = "@openrouter/ai-sdk-provider"

        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.modalities["image"] = OpenRouterImageGenerator(**kwargs)
            self.modalities["audio"] = OpenRouterAudioGenerator(**kwargs)
            self.modalities["speech"] = OpenRouterTextToSpeech(**kwargs)

        async def process_chat(self, chat, provider_id=None):
            ret = await super().process_chat(chat, provider_id)
            chat.pop("modalities", None)
            chat.pop("enable_thinking", None)
            messages = chat.get("messages", []).copy()
            for message in messages:
                message.pop("timestamp", None)
                message.pop("reasoning", None)
                message.pop("refusal", None)
            ret["messages"] = messages
            return ret

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

            self.init_chat(chat)

            chat = await self.process_chat(chat, provider_id=self.id)

            ctx.log(f"POST {self.chat_url}")
            ctx.log(self.chat_summary(chat))

            metadata = chat.pop("metadata", None)

            async with aiohttp.ClientSession() as session:
                started_at = time.time()
                async with session.post(
                    self.chat_url, headers=self.headers, data=json.dumps(chat), timeout=ctx.get_client_timeout()
                ) as response:
                    chat["metadata"] = metadata
                    return self.to_response(await self.response_json(response), chat, started_at, context=context)

    class OpenRouterAudioGenerator(GeneratorBase):
        sdk = "openrouter/audio"

        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.default_content = "I've generated the audio for you."

        def to_response(self, audio_data, chat, started_at, provider=None, context=None):
            model = chat.get("model", "")
            is_openai = "openai/" in model.lower()

            ext = "wav" if is_openai else "mp3"
            filename = f"{model.split('/')[-1]}.{ext}"

            if is_openai:
                # Convert raw 16-bit PCM to standard WAV format
                wav_io = io.BytesIO()
                with wave.open(wav_io, "wb") as wav_file:
                    wav_file.setnchannels(1)
                    wav_file.setsampwidth(2)  # 16-bit
                    wav_file.setframerate(24000)  # 24kHz
                    wav_file.writeframes(audio_data)
                audio_data = wav_io.getvalue()

            prompt = ctx.last_user_prompt(chat)
            cost = 0.0
            pricing_info = None

            if provider:
                model_info = provider.model_info(model)
                if model_info and "cost" in model_info:
                    pricing = model_info["cost"]
                    input_pricing = pricing.get("input", 0.0)
                    cost = (len(prompt) / 1000000.0) * input_pricing
                    if "input" in pricing and "output" in pricing:
                        pricing_info = f"{pricing['input']}/{pricing['output']}"

            relative_url, info = ctx.save_bytes_to_cache(
                audio_data, filename, ctx.to_file_info(chat, {"cost": cost}), context=context
            )

            duration = int((time.time() - started_at) * 1000)

            response = {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": self.default_content,
                            "audios": [
                                {
                                    "type": "audio_url",
                                    "audio_url": {
                                        "url": relative_url,
                                    },
                                }
                            ],
                        }
                    }
                ],
                "created": int(time.time()),
                "cost": cost,
                "usage": {
                    "prompt_tokens": len(prompt),
                    "completion_tokens": 0,
                    "total_tokens": len(prompt),
                    "cost": cost,
                },
                "metadata": {"duration": duration},
            }

            if pricing_info:
                response["metadata"]["pricing"] = pricing_info

            if context is not None:
                context["providerResponse"] = response

            return response

        async def chat(self, chat, provider=None, context=None):
            headers = self.get_headers(provider, chat)
            if provider is not None:
                chat["model"] = provider.provider_model(chat["model"]) or chat["model"]

            model = chat["model"]
            is_openai = "openai/" in model.lower()
            started_at = time.time()
            api = self.api or (provider.api if provider else None) or "https://openrouter.ai/api/v1"
            api_url = f"{api.rstrip('/')}/chat/completions"

            # Set up the request payload for audio output
            chat["modalities"] = ["text", "audio"]
            chat["audio"] = {"voice": "alloy", "format": "pcm16" if is_openai else "mp3"}
            chat["stream"] = True

            ctx.log(f"POST {api_url}")
            ctx.log(json.dumps(chat, indent=2))

            if ctx.MOCK:
                print("Mocking OpenRouterAudioGenerator")
                audio_data = b"MOCK_AUDIO_DATA_FOR_OPENROUTER_AUDIO_GEN"
                mock_file = f"{ctx.MOCK_DIR}/openrouter-speech.mp3"
                if os.path.exists(mock_file):
                    with open(mock_file, "rb") as f:
                        audio_data = f.read()
                return self.to_response(audio_data, chat, started_at, provider=provider, context=context)
            else:
                audio_bytes = bytearray()
                metadata = chat.pop("metadata", None)
                # Remove tools as audio output models usually do not support tool calling
                chat.pop("tools", None)
                async with aiohttp.ClientSession() as session, session.post(
                    api_url,
                    headers=headers,
                    data=json.dumps(chat),
                    timeout=aiohttp.ClientTimeout(total=300),
                ) as response:
                    if metadata:
                        chat["metadata"] = metadata
                    if response.status < 300:
                        response.content._high_water = 10 * 1024 * 1024
                        async for line in response.content:
                            if not line:
                                continue
                            line_str = line.decode("utf-8").strip()
                            if line_str.startswith("data: "):
                                data_content = line_str[6:]
                                if data_content == "[DONE]":
                                    break
                                try:
                                    chunk = json.loads(data_content)
                                    delta = chunk["choices"][0]["delta"]
                                    if "audio" in delta and "data" in delta["audio"]:
                                        base64_data = delta["audio"]["data"]
                                        audio_bytes.extend(base64.b64decode(base64_data))
                                except (json.JSONDecodeError, KeyError, IndexError):
                                    pass
                        return self.to_response(
                            bytes(audio_bytes), chat, started_at, provider=provider, context=context
                        )
                    else:
                        text = await response.text()
                        try:
                            data = json.loads(text)
                            if "error" in data and "message" in data["error"]:
                                raise Exception(data["error"]["message"])
                        except json.JSONDecodeError:
                            pass
                        raise Exception(f"Failed to generate audio {response.status}: {text}")

    ctx.add_provider(OpenRouterImageGenerator)
    ctx.add_provider(OpenRouterAudioGenerator)
    ctx.add_provider(OpenRouterTextToSpeech)
    ctx.add_provider(OpenRouterProvider)
