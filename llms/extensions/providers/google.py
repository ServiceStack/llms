import asyncio
import base64
import io
import json
import time
import wave

import aiohttp

# class GoogleOpenAiProvider(OpenAiCompatible):
#     sdk = "google-openai-compatible"

#     def __init__(self, api_key, **kwargs):
#         super().__init__(api="https://generativelanguage.googleapis.com", api_key=api_key, **kwargs)
#         self.chat_url = "https://generativelanguage.googleapis.com/v1beta/chat/completions"


def install_google(ctx):
    from llms.main import OpenAiCompatible

    def gemini_chat_summary(gemini_chat):
        """Summarize Gemini chat completion request for logging. Replace inline_data with size of content only"""
        clone = json.loads(json.dumps(gemini_chat))
        for content in clone["contents"]:
            for part in content["parts"]:
                if "inline_data" in part:
                    data = part["inline_data"]["data"]
                    part["inline_data"]["data"] = f"({len(data)})"
        return json.dumps(clone, indent=2)

    def gemini_response_summary(obj):
        to = {}
        for k, v in obj.items():
            if k == "candidates":
                candidates = []
                for candidate in v:
                    c = {}
                    for ck, cv in candidate.items():
                        if ck == "content":
                            content = {}
                            for content_k, content_v in cv.items():
                                if content_k == "parts":
                                    parts = []
                                    for part in content_v:
                                        p = {}
                                        for pk, pv in part.items():
                                            if pk == "inlineData":
                                                p[pk] = {
                                                    "mimeType": pv.get("mimeType"),
                                                    "data": f"({len(pv.get('data'))})",
                                                }
                                            else:
                                                p[pk] = pv
                                        parts.append(p)
                                    content[content_k] = parts
                                else:
                                    content[content_k] = content_v
                            c[ck] = content
                        else:
                            c[ck] = cv
                    candidates.append(c)
                to[k] = candidates
            else:
                to[k] = v
        return to

    def sanitize_parameters(params):
        """Sanitize tool parameters for Google provider."""

        if not isinstance(params, dict):
            return params

        # Create a copy to avoid modifying original tool definition
        p = params.copy()

        # Remove forbidden fields
        for forbidden in ["$schema", "additionalProperties"]:
            if forbidden in p:
                del p[forbidden]

        # Recursively sanitize known nesting fields
        # 1. Properties (dict of schemas)
        if "properties" in p:
            for k, v in p["properties"].items():
                p["properties"][k] = sanitize_parameters(v)

        # 2. Items (schema or list of schemas)
        if "items" in p:
            if isinstance(p["items"], list):
                p["items"] = [sanitize_parameters(i) for i in p["items"]]
            else:
                p["items"] = sanitize_parameters(p["items"])

        # 3. Combinators (list of schemas)
        for combinator in ["allOf", "anyOf", "oneOf"]:
            if combinator in p:
                p[combinator] = [sanitize_parameters(i) for i in p[combinator]]

        # 4. Not (schema)
        if "not" in p:
            p["not"] = sanitize_parameters(p["not"])

        # 5. Definitions (dict of schemas)
        for def_key in ["definitions", "$defs"]:
            if def_key in p:
                for k, v in p[def_key].items():
                    p[def_key][k] = sanitize_parameters(v)

        return p

    class GoogleProvider(OpenAiCompatible):
        sdk = "@ai-sdk/google"

        def __init__(self, **kwargs):
            new_kwargs = {"api": "https://generativelanguage.googleapis.com", **kwargs}
            super().__init__(**new_kwargs)
            self.safety_settings = kwargs.get("safety_settings")
            self.thinking_config = kwargs.get("thinking_config")
            self.speech_config = kwargs.get("speech_config")
            self.tools = kwargs.get("tools")
            self.curl = kwargs.get("curl")
            self.headers = kwargs.get("headers", {"Content-Type": "application/json"})
            # Google fails when using Authorization header, use query string param instead
            if "Authorization" in self.headers:
                del self.headers["Authorization"]

        def provider_model(self, model):
            if model.lower().startswith("gemini-"):
                return model
            return super().provider_model(model)

        def model_info(self, model):
            info = super().model_info(model)
            if info:
                return info
            if model.lower().startswith("gemini-"):
                return {
                    "id": model,
                    "name": model,
                    "cost": {"input": 0, "output": 0},
                }
            return None

        async def chat(self, chat, context=None):
            chat["model"] = self.provider_model(chat["model"]) or chat["model"]
            model_info = (context.get("modelInfo") if context is not None else None) or self.model_info(chat["model"])

            chat = await self.process_chat(chat)
            generation_config = {}
            tools = None
            supports_tool_calls = model_info.get("tool_call", False)

            if "tools" in chat and supports_tool_calls:
                function_declarations = []
                gemini_tools = {}

                for tool in chat["tools"]:
                    if tool["type"] == "function":
                        f = tool["function"]

                        function_declarations.append(
                            {
                                "name": f["name"],
                                "description": f.get("description"),
                                "parameters": sanitize_parameters(f.get("parameters")),
                            }
                        )
                    elif tool["type"] == "file_search":
                        gemini_tools["file_search"] = tool["file_search"]

                if function_declarations:
                    gemini_tools["function_declarations"] = function_declarations

                tools = [gemini_tools] if gemini_tools else None

            # Filter out system messages and convert to proper Gemini format
            contents = []
            system_prompt = None

            # Track tool call IDs to names for response mapping
            tool_id_map = {}

            async with aiohttp.ClientSession() as session:
                for message in chat["messages"]:
                    if message["role"] == "system":
                        content = message["content"]
                        if isinstance(content, list):
                            for item in content:
                                if "text" in item:
                                    system_prompt = item["text"]
                                    break
                        elif isinstance(content, str):
                            system_prompt = content
                    elif "content" in message:
                        role = "user"
                        if "role" in message:
                            if message["role"] == "user":
                                role = "user"
                            elif message["role"] == "assistant":
                                role = "model"
                            elif message["role"] == "tool":
                                role = "function"

                        parts = []

                        # Handle tool calls in assistant messages
                        if message.get("role") == "assistant" and "tool_calls" in message:
                            for tool_call in message["tool_calls"]:
                                tool_id_map[tool_call["id"]] = tool_call["function"]["name"]
                                parts.append(
                                    {
                                        "functionCall": {
                                            "name": tool_call["function"]["name"],
                                            "args": json.loads(tool_call["function"]["arguments"]),
                                        }
                                    }
                                )

                        # Handle tool responses from user
                        if message.get("role") == "tool":
                            # Gemini expects function response in 'functionResponse' part
                            # We need to find the name associated with this tool_call_id
                            tool_call_id = message.get("tool_call_id")
                            name = tool_id_map.get(tool_call_id)
                            # If we can't find the name (maybe from previous turn not in history or restart),
                            # we might have an issue. But let's try to proceed.
                            # Fallback: if we can't find the name, skip or try to infer?
                            # Gemini strict validation requires the name.
                            if name:
                                # content is the string response
                                # Some implementations pass the content directly.
                                # Google docs say: response: { "key": "value" }
                                try:
                                    response_data = json.loads(message["content"])
                                    if not isinstance(response_data, dict):
                                        response_data = {"content": message["content"]}
                                except Exception:
                                    response_data = {"content": message["content"]}

                                parts.append(
                                    {
                                        "functionResponse": {
                                            "name": name,
                                            "response": response_data,
                                        }
                                    }
                                )

                        if isinstance(message["content"], list):
                            for item in message["content"]:
                                if "type" in item:
                                    if item["type"] == "image_url" and "image_url" in item:
                                        image_url = item["image_url"]
                                        if "url" not in image_url:
                                            continue
                                        url = image_url["url"]
                                        if not url.startswith("data:"):
                                            raise Exception("Image was not downloaded: " + url)
                                        # Extract mime type from data uri
                                        mimetype = url.split(";", 1)[0].split(":", 1)[1] if ";" in url else "image/png"
                                        base64_data = url.split(",", 1)[1]
                                        parts.append({"inline_data": {"mime_type": mimetype, "data": base64_data}})
                                    elif item["type"] == "input_audio" and "input_audio" in item:
                                        input_audio = item["input_audio"]
                                        if "data" not in input_audio:
                                            continue
                                        data = input_audio["data"]
                                        format = input_audio["format"]
                                        mimetype = f"audio/{format}"
                                        parts.append({"inline_data": {"mime_type": mimetype, "data": data}})
                                    elif item["type"] == "file" and "file" in item:
                                        file = item["file"]
                                        if "file_data" not in file:
                                            continue
                                        data = file["file_data"]
                                        if not data.startswith("data:"):
                                            raise (Exception("File was not downloaded: " + data))
                                        # Extract mime type from data uri
                                        mimetype = (
                                            data.split(";", 1)[0].split(":", 1)[1]
                                            if ";" in data
                                            else "application/octet-stream"
                                        )
                                        base64_data = data.split(",", 1)[1]
                                        parts.append({"inline_data": {"mime_type": mimetype, "data": base64_data}})
                                if "text" in item:
                                    text = item["text"]
                                    parts.append({"text": text})
                        elif message["content"]:  # String content
                            parts.append({"text": message["content"]})

                        if len(parts) > 0:
                            contents.append(
                                {
                                    "role": role,
                                    "parts": parts,
                                }
                            )

                gemini_chat = {
                    "contents": contents,
                }

                if tools:
                    gemini_chat["tools"] = tools

                if self.safety_settings:
                    gemini_chat["safetySettings"] = self.safety_settings

                # Add system instruction if present
                if system_prompt is not None:
                    gemini_chat["systemInstruction"] = {"parts": [{"text": system_prompt}]}

                if "max_completion_tokens" in chat:
                    generation_config["maxOutputTokens"] = chat["max_completion_tokens"]
                if "stop" in chat:
                    generation_config["stopSequences"] = [chat["stop"]]
                if "temperature" in chat:
                    generation_config["temperature"] = chat["temperature"]
                if "top_p" in chat:
                    generation_config["topP"] = chat["top_p"]
                if "top_logprobs" in chat:
                    generation_config["topK"] = chat["top_logprobs"]

                if "thinkingConfig" in chat:
                    generation_config["thinkingConfig"] = chat["thinkingConfig"]
                elif self.thinking_config:
                    generation_config["thinkingConfig"] = self.thinking_config

                if len(generation_config) > 0:
                    gemini_chat["generationConfig"] = generation_config

                if "modalities" in chat:
                    generation_config["responseModalities"] = [modality.upper() for modality in chat["modalities"]]
                    if "image" in chat["modalities"] and "image_config" in chat:
                        # delete thinkingConfig
                        if "thinkingConfig" in generation_config:
                            del generation_config["thinkingConfig"]
                        config_map = {
                            "aspect_ratio": "aspectRatio",
                            "image_size": "imageSize",
                        }
                        generation_config["imageConfig"] = {
                            config_map[k]: v for k, v in chat["image_config"].items() if k in config_map
                        }
                    if "audio" in chat["modalities"] and self.speech_config:
                        if "thinkingConfig" in generation_config:
                            del generation_config["thinkingConfig"]
                        generation_config["speechConfig"] = self.speech_config.copy()
                        # Currently Google Audio Models only accept AUDIO
                        generation_config["responseModalities"] = ["AUDIO"]

                # Ensure generationConfig is set if we added anything to it
                if len(generation_config) > 0:
                    gemini_chat["generationConfig"] = generation_config

                started_at = int(time.time() * 1000)
                gemini_chat_url = f"https://generativelanguage.googleapis.com/v1beta/models/{chat['model']}:generateContent?key={self.api_key}"

                ctx.log(f"POST {gemini_chat_url}")
                ctx.log(gemini_chat_summary(gemini_chat))
                started_at = time.time()

                max_retries = 3
                for attempt in range(max_retries):
                    if ctx.MOCK and "modalities" in chat:
                        print("Mocking Google Gemini Image")
                        with open(f"{ctx.MOCK_DIR}/gemini-image.json") as f:
                            obj = json.load(f)
                    else:
                        res = None
                        try:
                            if attempt > 0:
                                await asyncio.sleep(attempt * 0.5)
                                ctx.log(f"Retrying request (attempt {attempt + 1}/{max_retries})...")

                            async with session.post(
                                gemini_chat_url,
                                headers=self.headers,
                                data=json.dumps(gemini_chat),
                                timeout=aiohttp.ClientTimeout(total=120),
                            ) as res:
                                obj = await self.response_json(res)
                                if context is not None:
                                    context["providerResponse"] = obj
                        except Exception as e:
                            if res:
                                ctx.err(f"{res.status} {res.reason}", e)
                                try:
                                    text = await res.text()
                                    obj = json.loads(text)
                                except Exception as parseEx:
                                    ctx.err("Failed to parse error response:\n" + text, parseEx)
                                    raise e from None
                            else:
                                ctx.err(f"Request failed: {str(e)}")
                                raise e from None

                    if "error" in obj:
                        ctx.log(f"Error: {obj['error']}")
                        raise Exception(obj["error"]["message"])

                    if ctx.debug:
                        ctx.dbg(json.dumps(gemini_response_summary(obj), indent=2))

                    # Check for empty response "anomaly"
                    has_candidates = obj.get("candidates") and len(obj["candidates"]) > 0
                    if has_candidates:
                        candidate = obj["candidates"][0]
                        raw_content = candidate.get("content", {})
                        raw_parts = raw_content.get("parts", [])

                        if not raw_parts and attempt < max_retries - 1:
                            # It's an empty response candidates list
                            ctx.dbg("Empty candidates parts detected. Retrying...")
                            continue

                    # If we got here, it's either a good response or we ran out of retries
                    break

                # calculate cost per generation
                cost = None
                token_costs = obj.get("metadata", {}).get("pricing", "")
                if token_costs:
                    input_price, output_price = token_costs.split("/")
                    input_per_token = float(input_price) / 1000000
                    output_per_token = float(output_price) / 1000000
                    if "usageMetadata" in obj:
                        input_tokens = obj["usageMetadata"].get("promptTokenCount", 0)
                        output_tokens = obj["usageMetadata"].get("candidatesTokenCount", 0)
                        cost = (input_per_token * input_tokens) + (output_per_token * output_tokens)

                response = {
                    "id": f"chatcmpl-{started_at}",
                    "created": started_at,
                    "model": obj.get("modelVersion", chat["model"]),
                }
                choices = []
                for i, candidate in enumerate(obj.get("candidates", [])):
                    role = "assistant"
                    if "content" in candidate and "role" in candidate["content"]:
                        role = "assistant" if candidate["content"]["role"] == "model" else candidate["content"]["role"]

                    # Safely extract content from all text parts
                    content = ""
                    reasoning = ""
                    images = []
                    audios = []
                    tool_calls = []

                    if "content" in candidate and "parts" in candidate["content"]:
                        text_parts = []
                        reasoning_parts = []
                        for part in candidate["content"]["parts"]:
                            if "text" in part:
                                if "thought" in part and part["thought"]:
                                    reasoning_parts.append(part["text"])
                                else:
                                    text_parts.append(part["text"])
                            if "functionCall" in part:
                                fc = part["functionCall"]
                                tool_calls.append(
                                    {
                                        "id": f"call_{len(tool_calls)}_{int(time.time())}",  # Gemini doesn't return ID, generate one
                                        "type": "function",
                                        "function": {"name": fc["name"], "arguments": json.dumps(fc["args"])},
                                    }
                                )

                            if "inlineData" in part:
                                inline_data = part["inlineData"]
                                mime_type = inline_data.get("mimeType", "image/png")
                                if mime_type.startswith("image"):
                                    ext = mime_type.split("/")[1]
                                    base64_data = inline_data["data"]
                                    filename = f"{chat['model'].split('/')[-1]}-{len(images)}.{ext}"
                                    ctx.log(f"inlineData {len(base64_data)} {mime_type} {filename}")
                                    relative_url, info = ctx.save_image_to_cache(
                                        base64_data,
                                        filename,
                                        ctx.to_file_info(chat, {"cost": cost}),
                                    )
                                    images.append(
                                        {
                                            "type": "image_url",
                                            "index": len(images),
                                            "image_url": {
                                                "url": relative_url,
                                            },
                                        }
                                    )
                                elif mime_type.startswith("audio"):
                                    # mime_type audio/L16;codec=pcm;rate=24000
                                    base64_data = inline_data["data"]

                                    pcm = base64.b64decode(base64_data)
                                    # Convert PCM to WAV
                                    wav_io = io.BytesIO()
                                    with wave.open(wav_io, "wb") as wf:
                                        wf.setnchannels(1)
                                        wf.setsampwidth(2)
                                        wf.setframerate(24000)
                                        wf.writeframes(pcm)
                                    wav_data = wav_io.getvalue()

                                    ext = mime_type.split("/")[1].split(";")[0]
                                    pcm_filename = f"{chat['model'].split('/')[-1]}-{len(audios)}.{ext}"
                                    filename = pcm_filename.replace(f".{ext}", ".wav")
                                    ctx.log(f"inlineData {len(base64_data)} {mime_type} {filename}")

                                    relative_url, info = ctx.save_bytes_to_cache(
                                        wav_data,
                                        filename,
                                        ctx.to_file_info(chat, {"cost": cost}),
                                    )

                                    audios.append(
                                        {
                                            "type": "audio_url",
                                            "index": len(audios),
                                            "audio_url": {
                                                "url": relative_url,
                                            },
                                        }
                                    )
                        content = " ".join(text_parts)
                        reasoning = " ".join(reasoning_parts)

                    choice = {
                        "index": i,
                        "finish_reason": candidate.get("finishReason", "stop"),
                        "message": {
                            "role": role,
                            "content": content if content else "",
                        },
                    }
                    if reasoning:
                        choice["message"]["reasoning"] = reasoning
                    if len(images) > 0:
                        choice["message"]["images"] = images
                    if len(audios) > 0:
                        choice["message"]["audios"] = audios
                    if len(tool_calls) > 0:
                        choice["message"]["tool_calls"] = tool_calls
                        # If we have tool calls, content can be null but message should probably exist

                    choices.append(choice)
                response["choices"] = choices
                if "usageMetadata" in obj:
                    usage = obj["usageMetadata"]
                    response["usage"] = {
                        "completion_tokens": usage.get("candidatesTokenCount", 0),
                        "total_tokens": usage.get("totalTokenCount", 0),
                        "prompt_tokens": usage.get("promptTokenCount", 0),
                    }

                return ctx.log_json(self.to_response(response, chat, started_at))

    ctx.add_provider(GoogleProvider)
