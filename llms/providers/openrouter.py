import json
import time

import aiohttp


def install(ctx):
    from llms.main import GeneratorBase

    # https://openrouter.ai/docs/guides/overview/multimodal/image-generation
    class OpenRouterGenerator(GeneratorBase):
        sdk = "openrouter/image"

        def __init__(self, **kwargs):
            super().__init__(**kwargs)

        def to_response(self, response, chat, started_at):
            # go through all image responses and save them to cache
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
                                info = {
                                    "model": model,
                                    "prompt": ctx.last_user_prompt(chat),
                                }
                                relative_url, info = ctx.save_image_to_cache(base64_data, filename, info)
                                image["image_url"]["url"] = relative_url

            return response

        async def chat(self, chat, provider=None):
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
                chat = await self.process_chat(chat, provider_id=self.id)
                ctx.log(f"POST {chat_url}")
                ctx.log(provider.chat_summary(chat))
                # remove metadata if any (conflicts with some providers, e.g. Z.ai)
                chat.pop("metadata", None)

                async with aiohttp.ClientSession() as session, session.post(
                    chat_url,
                    headers=headers,
                    data=json.dumps(chat),
                    timeout=aiohttp.ClientTimeout(total=300),
                ) as response:
                    return ctx.log_json(self.to_response(await self.response_json(response), chat, started_at))

    ctx.add_provider(OpenRouterGenerator)


__install__ = install
