import base64
import json
import mimetypes
import time

import aiohttp

# mistral_models = [
#     "voxtral-mini-latest",
#     "voxtral-small-latest",
#     "codestral-latest",
#     "devstral-latest",
#     "devstral-medium-latest",
#     "devstral-small-latest",
#     "mistral-tiny-latest",
#     "mistral-small-latest",
#     "mistral-medium-latest",
#     "mistral-large-latest",
#     "magistral-small-latest",
#     "magistral-medium-latest",
#     "ministral-3b-latest",
#     "ministral-8b-latest",
#     "ministral-14b-latest",
#     "mistral-moderation-latest",
#     "mistral-ocr-latest",
#     "pixtral-large-latest",
#     "mistral-vibe-cli-latest",
# ]


def install_mistral(ctx):
    from llms.main import GeneratorBase, OpenAiCompatible

    async def get_models(request):
        mistral = ctx.get_provider("mistral")
        url = mistral.api + "/models"
        async with aiohttp.ClientSession() as session, session.get(
            url, headers=mistral.headers, timeout=ctx.get_client_timeout()
        ) as response:
            return aiohttp.web.json_response(await response.json())

    ctx.add_get("mistral/models", get_models)

    # https://docs.mistral.ai/api/endpoint/audio/transcriptions
    class MistralTranscriptionGenerator(GeneratorBase):
        sdk = "mistral/transcriptions"
        api_url = "https://api.mistral.ai/v1/audio/transcriptions"

        def __init__(self, **kwargs):
            super().__init__(**kwargs)

        async def chat(self, chat, provider=None, context=None):
            headers = self.get_headers(provider, chat)
            # Remove Content-Type to allow aiohttp to set it for FormData
            if "Content-Type" in headers:
                del headers["Content-Type"]

            # Ensure x-api-key is present if Authorization is available
            if "Authorization" in headers and "x-api-key" not in headers:
                token = headers["Authorization"].replace("Bearer ", "")
                headers["x-api-key"] = token

            model = provider.provider_model(chat["model"]) or chat["model"] or "voxtral-mini-latest"
            # Replace internal alias with actual model name
            if model == "voxtral-mini-transcription":
                model = "voxtral-mini-latest"

            # Process chat to handle inputs (downloads URLs, reads files, converts to base64)
            chat = await self.process_chat(chat, provider_id=self.id)

            # Find audio data
            audio_data = None
            filename = "audio.mp3"

            # Search for input_audio or file in the messages
            for message in reversed(chat["messages"]):
                content = message.get("content")
                if isinstance(content, list):
                    for item in content:
                        if item.get("type") == "input_audio":
                            audio_data = item["input_audio"]["data"]
                            fmt = item["input_audio"].get("format", "mp3")
                            filename = f"audio.{fmt}"
                            break
                        # Support 'file' type if it appears to be audio
                        elif item.get("type") == "file":
                            file_data = item["file"].get("file_data")
                            fn = item["file"].get("filename", "")
                            if fn:
                                ext = fn.split(".")[-1]
                                if ext.lower() in ["mp3", "wav", "ogg", "flac", "m4a"]:
                                    audio_data = file_data
                                    filename = fn
                                    break
                if audio_data:
                    break

            if not audio_data:
                raise Exception(
                    "No audio file found in the request. Please provide an audio file via --audio or as an attachment."
                )

            # Decode base64 data
            if audio_data.startswith("data:"):
                # Handle data URI scheme: data:audio/mp3;base64,...
                audio_data = audio_data.split(";base64,")[1]

            try:
                file_bytes = base64.b64decode(audio_data)
            except Exception as e:
                raise Exception(f"Failed to decode audio data: {e}") from e

            # Prepare FormData
            data = aiohttp.FormData()
            data.add_field("model", model)
            data.add_field(
                "file", file_bytes, filename=filename, content_type=mimetypes.guess_type(filename)[0] or "audio/mpeg"
            )

            ctx.log(f"POST {self.api_url} model={model} file={filename} ({len(file_bytes)} bytes)")

            async with aiohttp.ClientSession() as session, session.post(
                self.api_url, headers=headers, data=data
            ) as response:
                text = await response.text()
                if response.status != 200:
                    raise Exception(f"Mistral API Error {response.status}: {text}")

                context["providerResponse"] = text

                try:
                    result = json.loads(text)
                except Exception:
                    result = {"text": text}  # Fallback

                transcription = result.get("text", "")

                ret = {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": transcription,
                            }
                        }
                    ],
                    "created": result.get("created", int(time.time())),
                }

                if "model" in result:
                    ret["model"] = result["model"]

                if "usage" in result:
                    ret["usage"] = result["usage"]

                return ret

    class MistralProvider(OpenAiCompatible):
        sdk = "@ai-sdk/mistral"

        def __init__(self, **kwargs):
            if "api" not in kwargs:
                kwargs["api"] = "https://api.mistral.ai/v1"
            super().__init__(**kwargs)
            self.transcription = MistralTranscriptionGenerator(**kwargs)

        async def process_chat(self, chat, provider_id=None):
            ret = await super().process_chat(chat, provider_id)
            messages = chat.get("messages", []).copy()
            for message in messages:
                message.pop("timestamp", None)  # mistral doesn't support extra fields
            ret["messages"] = messages
            return ret

        async def chat(self, chat, context=None):
            model = self.provider_model(chat["model"]) or chat["model"]
            model_info = self.model_info(model)
            model_modalities = model_info.get("modalities", {})
            input_modalities = model_modalities.get("input", [])
            # if only audio modality, use transcription
            if "audio" in input_modalities and len(input_modalities) == 1:
                return await self.transcription.chat(chat, provider=self, context=context)
            return await super().chat(chat, context=context)

    ctx.add_provider(MistralTranscriptionGenerator)
    ctx.add_provider(MistralProvider)
