import json
import time

import aiohttp


def install_fireworks(ctx):
    from llms.main import GeneratorBase, OpenAiCompatible

    # https://docs.fireworks.ai/api-reference/generate-a-new-image-from-a-text-prompt
    class FireworksGenerator(GeneratorBase):
        sdk = "fireworks/image"

        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.base_url = "https://api.fireworks.ai/inference/v1/workflows"

        def _model_url(self, model):
            """Convert model ID (e.g. fireworks/flux-1-dev-fp8) to API path."""
            if "/" in model:
                parts = model.split("/", 1)
                return f"accounts/{parts[0]}/models/{parts[1]}"
            return model

        def _is_async_model(self, model):
            """Kontext models use async polling instead of synchronous text_to_image."""
            model_name = model.split("/")[-1] if "/" in model else model
            return "kontext" in model_name

        def _save_and_respond(self, image_data, chat, seed=None, ext="png", context=None):
            model_name = chat["model"].split("/")[-1]
            filename = f"{model_name}.{ext}"
            relative_url, info = ctx.save_image_to_cache(
                image_data,
                filename,
                ctx.to_file_info(chat, {"seed": seed}),
                context=context,
            )
            return ctx.log_json(
                {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": self.default_content,
                                "images": [
                                    {
                                        "type": "image_url",
                                        "image_url": {"url": relative_url},
                                    }
                                ],
                            }
                        }
                    ],
                    "created": int(time.time()),
                }
            )

        async def _chat_sync(self, gen_url, headers, gen_request, chat, context=None):
            """Synchronous text_to_image flow for schnell/dev models."""
            headers["Accept"] = "image/png"
            async with aiohttp.ClientSession() as session, session.post(
                gen_url,
                headers=headers,
                data=json.dumps(gen_request),
                timeout=aiohttp.ClientTimeout(total=300),
            ) as response:
                if response.status >= 400:
                    text = await response.text()
                    raise Exception(f"Fireworks API error {response.status}: {text}")

                finish_reason = response.headers.get("Finish-Reason", "")
                if finish_reason == "CONTENT_FILTERED":
                    raise Exception("Image generation was filtered by safety check")

                seed = response.headers.get("Seed")
                image_data = await response.read()
                return self._save_and_respond(image_data, chat, seed=seed, context=context)

        async def _chat_async(self, gen_url, headers, gen_request, chat, context=None):
            """Async polling flow for Kontext models."""
            import asyncio

            headers["Accept"] = "application/json"
            async with aiohttp.ClientSession() as session:
                # Step 1: Submit generation request
                async with session.post(
                    gen_url,
                    headers=headers,
                    data=json.dumps(gen_request),
                    timeout=aiohttp.ClientTimeout(total=60),
                ) as response:
                    if response.status >= 400:
                        text = await response.text()
                        raise Exception(f"Fireworks API error {response.status}: {text}")
                    result = await response.json()

                request_id = result.get("request_id")
                if not request_id:
                    raise Exception(f"No request_id returned: {json.dumps(result)}")
                ctx.log(f"Fireworks async request submitted: {request_id}")

                # Step 2: Poll for result
                result_url = f"{gen_url}/get_result"
                for attempt in range(120):
                    await asyncio.sleep(1)
                    async with session.post(
                        result_url,
                        headers=headers,
                        data=json.dumps({"id": request_id}),
                        timeout=aiohttp.ClientTimeout(total=30),
                    ) as poll_response:
                        poll_result = await poll_response.json()
                        status = poll_result.get("status", "")

                        if status in ("Ready", "Complete", "Finished"):
                            sample = poll_result.get("result", {}).get("sample")
                            if not sample:
                                raise Exception("No image data in completed result")

                            if isinstance(sample, str) and sample.startswith("http"):
                                # Download from URL
                                async with session.get(sample) as img_response:
                                    image_data = await img_response.read()
                            else:
                                # Base64 data
                                import base64

                                image_data = base64.b64decode(sample)

                            seed = poll_result.get("result", {}).get("seed")
                            return self._save_and_respond(image_data, chat, seed=seed, ext="png", context=context)

                        if status in ("Failed", "Error"):
                            details = poll_result.get("details", "Unknown error")
                            raise Exception(f"Fireworks generation failed: {details}")

                        ctx.log(f"Fireworks polling attempt {attempt + 1}: {status}")

                raise Exception("Fireworks generation timed out after 120 seconds")

        async def chat(self, chat, provider=None, context=None):
            headers = self.get_headers(provider, chat)
            if provider is not None:
                chat["model"] = provider.provider_model(chat["model"]) or chat["model"]

            prompt = ctx.last_user_prompt(chat)
            image_config = chat.get("image_config", {})
            aspect_ratio = ctx.chat_to_aspect_ratio(chat) or "1:1"

            gen_request = {
                "prompt": prompt,
                "aspect_ratio": aspect_ratio,
            }
            if "guidance_scale" in image_config:
                gen_request["guidance_scale"] = float(image_config["guidance_scale"])
            if "num_inference_steps" in image_config:
                gen_request["num_inference_steps"] = int(image_config["num_inference_steps"])
            if "seed" in image_config:
                gen_request["seed"] = int(image_config["seed"])

            model_path = self._model_url(chat["model"])
            if self._is_async_model(chat["model"]):
                gen_url = f"{self.base_url}/{model_path}"
                ctx.log(f"POST {gen_url} (async)")
            else:
                gen_url = f"{self.base_url}/{model_path}/text_to_image"
                ctx.log(f"POST {gen_url}")
            ctx.log(self.gen_summary(gen_request))

            if self._is_async_model(chat["model"]):
                return await self._chat_async(gen_url, headers, gen_request, chat, context=context)
            else:
                return await self._chat_sync(gen_url, headers, gen_request, chat, context=context)

    class FireworksProvider(OpenAiCompatible):
        sdk = "@fireworks/ai-sdk-provider"

        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.modalities["image"] = FireworksGenerator(**kwargs)

        async def process_chat(self, chat, provider_id=None):
            ret = await super().process_chat(chat, provider_id)
            chat.pop("modalities", None)
            chat.pop("enable_thinking", None)
            messages = chat.get("messages", []).copy()
            for message in messages:
                # fireworks doesn't support timestamp, reasoning, refusal
                message.pop("timestamp", None)
                message.pop("reasoning", None)
                message.pop("refusal", None)
            ret["messages"] = messages
            return ret

    ctx.add_provider(FireworksProvider)
