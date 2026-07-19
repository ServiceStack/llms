import base64
import io
import json
import mimetypes
import os
import time
import wave

import aiohttp


def install_llmspy(ctx):
    from llms.main import GeneratorBase, OpenAiCompatible

    Provider = ctx.get_provider("@openrouter/ai-sdk-provider")

    ctx.log(f"Creating LlmspyProvider from {Provider.sdk}")

    class LlmspyProvider(Provider):
        sdk = "llms-sdk-provider"

        def __init__(self, **kwargs):
            self.map_models = {}
            super().__init__(**kwargs)
            self.modalities = {
                "image": {
                    "name": "llms.py Image",
                    "npm": "openrouter/image"
                }
            }
            self.models = {
                "llmspy/krea2-turbo": {
                    "id": "llmspy/krea2-turbo",
                    "name": "Krea2 Turbo",
                    "modalities": {
                        "input": [
                            "text"
                        ],
                        "output": [
                            "image"
                        ]
                    },
                    "cost": {
                        "input": 0,
                        "output": 0.03
                    }
                },
                "llmspy/hidream-fast": {
                    "id": "llmspy/hidream-fast",
                    "name": "HiDream Fast",
                    "modalities": {
                        "input": [
                            "text"
                        ],
                        "output": [
                            "image"
                        ]
                    },
                    "cost": {
                        "input": 0,
                        "output": 0.03
                    }
                },
                "llmspy/flux1-krea-dev": {
                    "id": "llmspy/flux1-krea-dev",
                    "name": "Flux Krea Dev",
                    "modalities": {
                        "input": [
                            "text"
                        ],
                        "output": [
                            "image"
                        ]
                    },
                    "cost": {
                        "input": 0,
                        "output": 0.03
                    }
                },
                "llmspy/flux-schnell": {
                    "id": "llmspy/flux-schnell",
                    "name": "Flux Schnell",
                    "modalities": {
                        "input": [
                            "text"
                        ],
                        "output": [
                            "image"
                        ]
                    },
                    "cost": {
                        "input": 0,
                        "output": 0.03
                    }
                },
                "llmspy/stable-audio-music": {
                    "id": "llmspy/stable-audio-music",
                    "name": "Stable Audio Music",
                    "modalities": {
                        "input": [
                            "text"
                        ],
                        "output": [
                            "audio"
                        ]
                    },
                    "cost": {
                        "input": 0,
                        "output": 0.03
                    }
                },
                "llmspy/ace-step": {
                    "id": "llmspy/ace-step",
                    "name": "Ace Step",
                    "modalities": {
                        "input": [
                            "text"
                        ],
                        "output": [
                            "audio"
                        ]
                    },
                    "cost": {
                        "input": 0,
                        "output": 0.03
                    }
                }   
            }

            OpenRouterImageGenerator = ctx.get_provider("openrouter/image")
            OpenRouterAudioGenerator = ctx.get_provider("openrouter/audio")
            self.modalities["image"] = OpenRouterImageGenerator(**kwargs)
            self.modalities["audio"] = OpenRouterAudioGenerator(**kwargs)

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

    ctx.add_provider(LlmspyProvider)
