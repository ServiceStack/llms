import base64
import json
import mimetypes
import time

import aiohttp


def install_openai(ctx):
    from llms.main import GeneratorBase, OpenAiCompatible

    class OpenAiProvider(OpenAiCompatible):
        sdk = "@ai-sdk/openai"

        def __init__(self, **kwargs):
            if "api" not in kwargs:
                kwargs["api"] = "https://api.openai.com/v1"
            super().__init__(**kwargs)
            self.modalities["image"] = OpenAiGenerator(**kwargs)

        async def process_chat(self, chat, provider_id=None):
            ret = await super().process_chat(chat, provider_id)
            chat.pop("modalities", None)  # openai chat completion doesn't support modalities
            return ret

    # https://platform.openai.com/docs/api-reference/images
    class OpenAiGenerator(GeneratorBase):
        sdk = "openai/image"

        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.api = "https://api.openai.com/v1/images/generations"
            self.map_image_models = kwargs.get(
                "map_image_models",
                {
                    "gpt-5.1-codex-mini": "gpt-image-1-mini",
                },
            )

        def aspect_ratio_to_size(self, aspect_ratio, model):
            w, h = aspect_ratio.split(":")
            width = int(w)
            height = int(h)
            if model == "dall-e-2":
                return "1024x1024"
            if model == "dall-e-3":
                if width > height:
                    return "1792x1024"
                elif height > width:
                    return "1024x1792"
            if width > height:
                return "1536x1024"
            elif height > width:
                return "1024x1536"
            return "1024x1024"

        async def to_response(self, response, chat, started_at):
            # go through all image responses and save them to cache
            # Try to extract and save images
            images = []
            if "data" in response:
                for i, item in enumerate(response["data"]):
                    image_url = item.get("url")
                    b64_json = item.get("b64_json")

                    ext = "png"
                    image_data = None

                    if b64_json:
                        image_data = base64.b64decode(b64_json)
                    elif image_url:
                        ctx.log(f"GET {image_url}")
                        async with aiohttp.ClientSession() as session, await session.get(image_url) as res:
                            if res.status == 200:
                                image_data = await res.read()
                                content_type = res.headers.get("Content-Type")
                                if content_type:
                                    ext = mimetypes.guess_extension(content_type)
                                    if ext:
                                        ext = ext.lstrip(".")  # remove leading dot
                                    # Fallback if guess_extension returns None or if we want to be safe
                                    if not ext:
                                        ext = "png"
                            else:
                                raise Exception(f"Failed to download image: {res.status}")

                    if image_data:
                        relative_url, info = ctx.save_image_to_cache(
                            image_data,
                            f"{chat['model']}-{i}.{ext}",
                            ctx.to_file_info(chat),
                        )
                        images.append(
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": relative_url,
                                },
                            }
                        )
                    else:
                        raise Exception("No image data found")

                return {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": self.default_content,
                                "images": images,
                            }
                        }
                    ]
                }
            if "error" in response:
                raise Exception(response["error"]["message"])

            ctx.log(json.dumps(response, indent=2))
            raise Exception("No 'data' field in response.")

        async def chat(self, chat, provider=None, context=None):
            headers = self.get_headers(provider, chat)

            if chat["model"] in self.map_image_models:
                chat["model"] = self.map_image_models[chat["model"]]

            aspect_ratio = ctx.chat_to_aspect_ratio(chat) or "1:1"
            payload = {
                "model": chat["model"],
                "prompt": ctx.last_user_prompt(chat),
                "size": self.aspect_ratio_to_size(aspect_ratio, chat["model"]),
            }
            if provider is not None:
                chat["model"] = provider.provider_model(chat["model"]) or chat["model"]

            started_at = time.time()
            if ctx.MOCK:
                print("Mocking OpenAiGenerator")
                text = ctx.text_from_file(f"{ctx.MOCK_DIR}/openai-image.json")
                return await self.to_response(json.loads(text), chat, started_at)
            else:
                ctx.log(f"POST {self.api}")
                # _log(json.dumps(headers, indent=2))
                ctx.log(json.dumps(payload, indent=2))
                async with aiohttp.ClientSession() as session, session.post(
                    self.api, headers=headers, json=payload
                ) as response:
                    text = await response.text()
                    ctx.log(text[:1024] + (len(text) > 1024 and "..." or ""))
                    if response.status < 300:
                        return ctx.log_json(await self.to_response(json.loads(text), chat, started_at, context=context))
                    else:
                        raise Exception(f"Failed to generate image {response.status}")

    ctx.add_provider(OpenAiProvider)
    ctx.add_provider(OpenAiGenerator)


__install__ = install_openai
