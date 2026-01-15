import json
import time
from typing import Optional

import aiohttp


def install_zai(ctx):
    from llms.main import GeneratorBase

    # https://docs.z.ai/guides/image/glm-image
    class ZaiGenerator(GeneratorBase):
        sdk = "zai/image"

        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.aspect_ratios = {
                "1:1": "1280×1280",
                "2:3": "1056×1568",
                "3:2": "1568×1056",
                "3:4": "1088×1472",
                "4:3": "1472×1088",
                "4:5": "1088×1472",
                "5:4": "1472×1088",
                "9:16": "960×1728",
                "16:9": "1728×960",
                "21:9": "1728×960",
            }
            self.model: str = kwargs.get("model", "glm-image")
            self.n: Optional[int] = kwargs.get("n")
            self.quality: Optional[str] = kwargs.get("quality")
            self.response_format: Optional[str] = kwargs.get("response_format")
            self.size: Optional[str] = kwargs.get("size")
            self.style: Optional[str] = kwargs.get("style")
            self.sensitive_word_check: Optional[str] = kwargs.get("sensitive_word_check")
            self.user: Optional[str] = kwargs.get("user")
            self.request_id: Optional[str] = kwargs.get("request_id")
            self.user_id: Optional[str] = kwargs.get("user_id")
            self.extra_headers: Optional[dict] = kwargs.get("extra_headers")
            self.extra_body: Optional[dict] = kwargs.get("extra_body")
            self.disable_strict_validation: Optional[bool] = kwargs.get("disable_strict_validation")
            self.timeout: Optional[float] = float(kwargs.get("timeout") or 300)
            self.watermark_enabled: Optional[bool] = kwargs.get("watermark_enabled")

        async def chat(self, chat, provider=None, context=None):
            headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
            if self.extra_headers:
                headers.update(self.extra_headers)

            chat_url = "https://api.z.ai/api/paas/v4/images/generations"
            if provider is not None:
                headers["Authorization"] = f"Bearer {provider.api_key}"
                chat["model"] = provider.provider_model(chat["model"]) or chat["model"]
                chat_url = provider.api + "/images/generations"

            body = {}
            attrs = [
                "model",
                "n",
                "quality",
                "response_format",
                "size",
                "style",
                "sensitive_word_check",
                "user",
                "request_id",
                "user_id",
                "disable_strict_validation",
                "watermark_enabled",
            ]
            for attr in attrs:
                if hasattr(self, attr) and getattr(self, attr) is not None:
                    body[attr] = getattr(self, attr)

            if self.extra_body:
                body.update(self.extra_body)

            if "model" in chat:
                body["model"] = chat["model"]

            body["prompt"] = ctx.last_user_prompt(chat)

            aspect_ratio = ctx.chat_to_aspect_ratio(chat) or "1:1"
            size = self.aspect_ratios.get(aspect_ratio, "1280x1280").replace("×", "x")
            body["size"] = size

            username = ctx.context_to_username(context)
            if username:
                body["user"] = username

            ctx.dbg(f"ZaiProvider.chat: {chat_url}")
            ctx.dbg(json.dumps(body, indent=2))
            started_at = time.time()
            async with aiohttp.ClientSession() as session, session.post(
                chat_url,
                headers=headers,
                data=json.dumps(body),
                timeout=aiohttp.ClientTimeout(total=self.timeout),
            ) as response:
                # Example Response
                # {
                #   "created": 1768451303,
                #   "data": [
                #     {
                #       "url": "https://mfile.z.ai/1768451374203-b334959408a643a8a6c74eb104746dcb.png?ufileattname=202601151228236805d575507d4570_watermark.png"
                #     }
                #   ],
                #   "id": "202601151228236805d575507d4570",
                #   "request_id": "202601151228236805d575507d4570",
                #   "usage": {
                #     "tokens": 0,
                #     "price": 0,
                #     "cost": 0.0,
                #     "duration": 71
                #   },
                #   "timestamp": 1768451374519,
                #   "model": "GLM-Image"
                # }

                response_json = await self.response_json(response)
                duration = int(time.time() - started_at)
                usage = response_json.get("usage", {})
                if context is not None:
                    context["providerResponse"] = response_json
                    if "cost" in usage:
                        context["cost"] = usage.get("cost")

                images = []
                for image in response_json.get("data", []):
                    url = image.get("url")
                    if not url:
                        continue
                    # download url with aiohttp
                    async with session.get(url) as image_response:
                        headers = image_response.headers
                        # get filename from Content-Disposition
                        # attachment; filename="202601151228236805d575507d4570_watermark.png"
                        mime_type = headers.get("Content-Type") or "image/png"
                        disposition = headers.get("Content-Disposition")
                        if disposition:
                            start = disposition.index('filename="') + len('filename="')
                            end = disposition.index('"', start)
                            filename = disposition[start:end]
                        else:
                            ext = mime_type.split("/")[1]
                            filename = f"{body['model'].lower()}-{response_json.get('id', int(started_at))}.{ext}"
                        image_bytes = await image_response.read()

                        info = {
                            "prompt": body["prompt"],
                            "type": mime_type,
                            "width": int(size.split("x")[0]),
                            "height": int(size.split("x")[1]),
                            "duration": duration,
                        }
                        info.update(usage)
                        cache_url, info = ctx.save_image_to_cache(
                            image_bytes, filename, image_info=info, ignore_info=True
                        )

                    images.append(
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": cache_url,
                            },
                        }
                    )

                chat_response = {
                    "choices": [{"message": {"role": "assistant", "content": self.default_content, "images": images}}],
                    "created": int(time.time()),
                    "usage": {
                        "prompt_tokens": 0,
                        "completion_tokens": 1_000_000,  # Price per image is 0.015, so 1M token is 0.015
                    },
                }
                if "cost" in usage:
                    chat_response["cost"] = usage["cost"]
                return ctx.log_json(chat_response)

    ctx.add_provider(ZaiGenerator)
