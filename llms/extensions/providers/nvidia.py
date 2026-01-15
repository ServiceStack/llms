import json
import time

import aiohttp


def install_nvidia(ctx):
    from llms.main import GeneratorBase

    class NvidiaGenAi(GeneratorBase):
        sdk = "nvidia/image"

        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.width = int(kwargs.get("width", 1024))
            self.height = int(kwargs.get("height", 1024))
            self.cfg_scale = float(kwargs.get("cfg_scale", 3))
            self.steps = int(kwargs.get("steps", 20))
            self.mode = kwargs.get("mode", "base")
            self.gen_url = kwargs.get("api", "https://ai.api.nvidia.com/v1/genai")

        def to_response(self, response, chat, started_at):
            if "artifacts" in response:
                for artifact in response["artifacts"]:
                    base64 = artifact.get("base64")
                    seed = artifact.get("seed")
                    filename = f"{seed}.png"
                    if "model" in chat:
                        last_model = "/" in chat["model"] and chat["model"].split("/")[-1] or chat["model"]
                        filename = f"{last_model}_{seed}.png"

                    relative_url, info = ctx.save_image_to_cache(
                        base64,
                        filename,
                        ctx.to_file_info(chat, {"seed": seed}),
                    )
                    return {
                        "choices": [
                            {
                                "message": {
                                    "role": "assistant",
                                    "content": self.default_content,
                                    "images": [
                                        {
                                            "type": "image_url",
                                            "image_url": {
                                                "url": relative_url,
                                            },
                                        }
                                    ],
                                }
                            }
                        ]
                    }
            raise Exception("No artifacts in response")

        async def chat(self, chat, provider=None, context=None):
            headers = self.get_headers(provider, chat)
            if provider is not None:
                chat["model"] = provider.provider_model(chat["model"]) or chat["model"]

            prompt = ctx.last_user_prompt(chat)

            gen_request = {
                "prompt": prompt,
            }
            modalities = chat.get("modalities", ["text"])
            if "image" in modalities:
                aspect_ratio = ctx.chat_to_aspect_ratio(chat) or "1:1"
                dimension = ctx.app.aspect_ratios.get(aspect_ratio)
                if dimension:
                    width, height = dimension.split("×")
                    gen_request["width"] = int(width)
                    gen_request["height"] = int(height)
                else:
                    gen_request["width"] = self.width
                    gen_request["height"] = self.height

                gen_request["mode"] = self.mode
                gen_request["cfg_scale"] = self.cfg_scale
                gen_request["steps"] = self.steps

            gen_url = f"{self.gen_url}/{chat['model']}"
            ctx.log(f"POST {gen_url}")
            ctx.log(self.gen_summary(gen_request))
            # remove metadata if any (conflicts with some providers, e.g. Z.ai)
            gen_request.pop("metadata", None)
            started_at = time.time()

            if ctx.MOCK:
                ctx.log("Mocking NvidiaGenAi")
                text = ctx.text_from_file(f"{ctx.MOCK_DIR}/nvidia-image.json")
                return self.to_response(json.loads(text), chat, started_at)
            else:
                async with aiohttp.ClientSession() as session, session.post(
                    gen_url,
                    headers=headers,
                    data=json.dumps(gen_request),
                    timeout=aiohttp.ClientTimeout(total=120),
                ) as response:
                    return self.to_response(await self.response_json(response), chat, started_at, context=context)

    ctx.add_provider(NvidiaGenAi)
