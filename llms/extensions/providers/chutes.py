import json
import mimetypes
import time

import aiohttp


def install_chutes(ctx):
    from llms.main import GeneratorBase

    class ChutesImage(GeneratorBase):
        sdk = "chutes/image"

        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.width = int(kwargs.get("width", 1024))
            self.height = int(kwargs.get("height", 1024))
            self.cfg_scale = float(kwargs.get("cfg_scale", 7.5))
            self.steps = int(kwargs.get("steps", 50))
            self.negative_prompt = kwargs.get("negative_prompt", "blur, distortion, low quality")
            self.gen_url = kwargs.get("api", "https://image.chutes.ai/generate")
            self.model_resolutions = {
                "chutes-hidream": {
                    "1:1": "1024x1024",
                    "9:16": "768x1360",
                    "16:9": "1360x768",
                    "3:4": "880x1168",
                    "4:3": "1168x880",
                    "2:3": "832x1248",
                    "3:2": "1248x832",
                }
            }
            self.model_sizes = ["chutes-hunyuan-image-3"]
            self.model_negative_prompt = [
                "chroma",
                "qwen-image-edit-2509",
                "JuggernautXL-Ragnarok",
                "JuggernautXL",
                "Animij",
                "iLustMix",
            ]

        async def chat(self, chat, provider=None, context=None):
            headers = {"Authorization": f"Bearer {self.api_key}"}
            if provider is not None:
                headers["Authorization"] = f"Bearer {provider.api_key}"
                chat["model"] = provider.provider_model(chat["model"]) or chat["model"]

            aspect_ratio = "1:1"
            if "messages" in chat and len(chat["messages"]) > 0:
                aspect_ratio = chat["messages"][0].get("aspect_ratio", "1:1")
            cfg_scale = self.cfg_scale
            steps = self.steps
            width = self.width
            height = self.height
            if chat["model"] == "chutes-z-image-turbo":
                cfg_scale = min(self.cfg_scale, 5)
            payload = {
                "model": chat["model"],
                "prompt": ctx.last_user_prompt(chat),
                "guidance_scale": cfg_scale,
                "width": width,
                "height": height,
                "num_inference_steps": steps,
            }
            if chat["model"] in self.model_negative_prompt:
                payload["negative_prompt"] = self.negative_prompt

            aspect_ratio = ctx.chat_to_aspect_ratio(chat) or "1:1"
            dimension = ctx.app.aspect_ratios.get(aspect_ratio)
            if dimension:
                w, h = dimension.split("×")
                width, height = int(w), int(h)
                payload["width"] = width
                payload["height"] = height

            if chat["model"] in self.model_resolutions:
                # if models use resolution, remove width and height
                del payload["width"]
                del payload["height"]
                resolution = self.model_resolutions[chat["model"]][aspect_ratio]
                payload["resolution"] = resolution
            elif chat["model"] in self.model_sizes:
                del payload["width"]
                del payload["height"]
                payload["size"] = aspect_ratio

            gen_url = self.gen_url
            if chat["model"].startswith("chutes-"):
                model = payload["model"]
                gen_url = f"https://{model}.chutes.ai/generate"
                del payload["model"]

            ctx.log(f"POST {gen_url}")
            ctx.log(json.dumps(payload, indent=2))
            async with aiohttp.ClientSession() as session, session.post(
                gen_url, headers=headers, json=payload
            ) as response:
                if response.status < 300:
                    image_data = await response.read()
                    content_type = response.headers.get("Content-Type")
                    if content_type:
                        ext = mimetypes.guess_extension(content_type)
                        if ext:
                            ext = ext.lstrip(".")  # remove leading dot
                    if not ext:
                        ext = "png"

                    relative_url, info = ctx.save_image_to_cache(
                        image_data,
                        f"{chat['model']}.{ext}",
                        ctx.to_file_info(
                            chat,
                            {
                                "aspect_ratio": aspect_ratio,
                                "width": width,
                                "height": height,
                                "cfg_scale": cfg_scale,
                                "steps": steps,
                            },
                        ),
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
                        ],
                        "created": int(time.time()),
                    }
                else:
                    text = await response.text()
                    try:
                        data = json.loads(text)
                        ctx.log(data)
                        if "detail" in data:
                            raise Exception(data["detail"])
                    except json.JSONDecodeError:
                        pass
                    raise Exception(f"Failed to generate image {response.status}")

    ctx.add_provider(ChutesImage)
