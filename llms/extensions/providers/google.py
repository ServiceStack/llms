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

        async def handle_stream_response(self, response, chat, started_at, context=None):
            if response.status >= 300:
                text = await response.text()
                try:
                    data = json.loads(text)
                    if "error" in data and "message" in data["error"]:
                        raise Exception(data["error"]["message"])
                except json.JSONDecodeError:
                    pass
                raise Exception(f"Failed chat completion {response.status}: {text}")

            thread_id = context.get("threadId") if context else None
            user = context.get("user") if context else None
            threads_api = ctx.threads

            response_id = None
            created_time = None
            model_name = None
            content_acc = ""
            reasoning_acc = ""
            reasoning_field = None
            tool_calls_dict = {}
            finish_reason = None
            usage_acc = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
            last_db_update = 0.0
            base_messages = list(chat.get("messages", []))

            async for line in response.content:
                if not line:
                    continue
                line_str = line.decode("utf-8").strip()
                if not line_str or line_str.startswith(":"):
                    continue
                if line_str.startswith("data: "):
                    data_content = line_str[6:].strip()
                    if data_content == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data_content)
                    except json.JSONDecodeError:
                        continue

                    if "error" in chunk:
                        err = chunk["error"]
                        msg = err.get("message") if isinstance(err, dict) else str(err)
                        raise Exception(msg or "Google Gemini streaming error")

                    if chunk.get("modelVersion"):
                        model_name = chunk["modelVersion"]

                    if "usageMetadata" in chunk and isinstance(chunk["usageMetadata"], dict):
                        um = chunk["usageMetadata"]
                        if "promptTokenCount" in um:
                            usage_acc["prompt_tokens"] = um["promptTokenCount"]
                        if "candidatesTokenCount" in um:
                            usage_acc["completion_tokens"] = um["candidatesTokenCount"]
                        if "totalTokenCount" in um:
                            usage_acc["total_tokens"] = um["totalTokenCount"]
                        else:
                            usage_acc["total_tokens"] = (
                                usage_acc.get("prompt_tokens", 0) + usage_acc.get("completion_tokens", 0)
                            )

                    candidates = chunk.get("candidates") or []
                    for candidate in candidates:
                        if candidate.get("finishReason"):
                            finish_reason = candidate["finishReason"]

                        raw_content = candidate.get("content", {})
                        parts = raw_content.get("parts", [])
                        for part in parts:
                            if "text" in part:
                                text_val = part["text"]
                                if part.get("thought"):
                                    reasoning_acc += text_val
                                    reasoning_field = "reasoning"
                                else:
                                    content_acc += text_val
                            if "functionCall" in part:
                                fc = part["functionCall"]
                                idx = len(tool_calls_dict)
                                fn_name = fc.get("name", "")
                                fn_args = (
                                    json.dumps(fc.get("args", {}))
                                    if isinstance(fc.get("args"), dict)
                                    else (fc.get("args") or "")
                                )
                                tc = {
                                    "id": f"call_{idx}_{int(started_at)}",
                                    "type": "function",
                                    "function": {
                                        "name": fn_name,
                                        "arguments": fn_args,
                                    },
                                }
                                signature = part.get("thoughtSignature") or part.get("thought_signature")
                                if signature:
                                    tc["thoughtSignature"] = signature
                                    tc["extra_content"] = {"google": {"thought_signature": signature}}
                                tool_calls_dict[idx] = tc

                    if context and ctx.should_cancel_thread(context):
                        break

                    now = time.time()
                    if threads_api and thread_id and (now - last_db_update >= 0.1):
                        last_db_update = now
                        assistant_msg = {
                            "role": "assistant",
                            "content": content_acc,
                            "model": chat.get("model"),
                        }
                        if reasoning_acc:
                            assistant_msg[reasoning_field or "reasoning"] = reasoning_acc
                        if tool_calls_dict:
                            assistant_msg["tool_calls"] = [tool_calls_dict[i] for i in sorted(tool_calls_dict.keys())]

                        streaming_messages = base_messages + [assistant_msg]
                        await threads_api.update_thread_async(thread_id, {"messages": streaming_messages}, user=user)

            if context and ctx.should_cancel_thread(context):
                ctx.log(f"Stream cancelled for thread {thread_id}")
                return None

            if threads_api and thread_id:
                assistant_msg = {
                    "role": "assistant",
                    "content": content_acc,
                    "model": chat.get("model"),
                }
                if reasoning_acc:
                    assistant_msg[reasoning_field or "reasoning"] = reasoning_acc
                if tool_calls_dict:
                    assistant_msg["tool_calls"] = [tool_calls_dict[i] for i in sorted(tool_calls_dict.keys())]

                streaming_messages = base_messages + [assistant_msg]
                await threads_api.update_thread_async(thread_id, {"messages": streaming_messages}, user=user)

            message_obj = {
                "role": "assistant",
                "content": content_acc,
            }
            if reasoning_acc:
                message_obj[reasoning_field or "reasoning"] = reasoning_acc
            if tool_calls_dict:
                message_obj["tool_calls"] = [tool_calls_dict[i] for i in sorted(tool_calls_dict.keys())]

            choice_obj = {
                "index": 0,
                "message": message_obj,
                "finish_reason": finish_reason or "stop",
            }

            openai_response = {
                "id": response_id or f"chatcmpl-{int(started_at)}",
                "object": "chat.completion",
                "created": created_time or int(started_at),
                "model": model_name or chat.get("model"),
                "choices": [choice_obj],
                "usage": usage_acc,
            }

            return self.to_response(openai_response, chat, started_at, context=context)

        async def chat(self, chat, context=None):
            is_stream = chat["stream"] if "stream" in chat else (self.stream if self.stream is not None else True)
            chat["model"] = self.provider_model(chat["model"]) or chat["model"]
            model_info = (context.get("modelInfo") if context is not None else None) or self.model_info(chat["model"])

            chat = await self.process_chat(chat)
            generation_config = {}
            tools = None

            modalities = chat.get("modalities")
            has_media_modality = modalities and ("image" in modalities or "audio" in modalities)
            if has_media_modality:
                is_stream = False

            if has_media_modality:
                supports_tool_calls = False
                if "tools" in chat:
                    del chat["tools"]
                if "tool_choice" in chat:
                    del chat["tool_choice"]
                if "parallel_tool_calls" in chat:
                    del chat["parallel_tool_calls"]
            else:
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
                                part = {
                                    "functionCall": {
                                        "name": tool_call["function"]["name"],
                                        "args": json.loads(tool_call["function"]["arguments"]),
                                    }
                                }

                                signature = tool_call.get("thoughtSignature") or tool_call.get("thought_signature")
                                if (
                                    not signature
                                    and "extra_content" in tool_call
                                    and isinstance(tool_call["extra_content"], dict)
                                ):
                                    google_extra = tool_call["extra_content"].get("google", {})
                                    if isinstance(google_extra, dict):
                                        signature = google_extra.get("thought_signature") or google_extra.get(
                                            "thoughtSignature"
                                        )
                                if not signature and "function" in tool_call:
                                    signature = tool_call["function"].get("thoughtSignature") or tool_call[
                                        "function"
                                    ].get("thought_signature")

                                if signature:
                                    part["thoughtSignature"] = signature

                                parts.append(part)

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

                # Gemini doesn't support tools and modalities together
                modalities = chat.get("modalities")
                if modalities and ("image" in modalities or "audio" in modalities):
                    supports_tool_calls = False
                    tools = None
                    system_prompt = None
                    if "tools" in chat:
                        del chat["tools"]
                    if "tool_choice" in chat:
                        del chat["tool_choice"]
                    if "parallel_tool_calls" in chat:
                        del chat["parallel_tool_calls"]
                    # if "audio" in modalities and "tts" in chat["model"]:
                    #     system_prompt = "You are a text-to-speech engine. Your only job is to generate audio of the exact text the user provides. Do not converse, do not answer questions, and do not generate any extra text"

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

                model_name_lower = chat.get("model", "").lower()
                is_thinking_model = "thinking" in model_name_lower or (
                    model_info and (model_info.get("thinking") or model_info.get("thinking_budget"))
                )
                enable_thinking = chat.get("enable_thinking")

                if "thinkingConfig" in chat:
                    generation_config["thinkingConfig"] = chat["thinkingConfig"]
                elif enable_thinking is True or (enable_thinking is not False and is_thinking_model and self.thinking_config):
                    if self.thinking_config:
                        generation_config["thinkingConfig"] = self.thinking_config

                if "response_format" in chat:
                    response_format = chat["response_format"]
                    if isinstance(response_format, dict):
                        if response_format.get("type") == "json_object":
                            generation_config["responseMimeType"] = "application/json"
                        elif response_format.get("type") == "json_schema":
                            json_schema = response_format.get("json_schema", {})
                            if "schema" in json_schema:
                                generation_config["responseMimeType"] = "application/json"
                                generation_config["responseJsonSchema"] = sanitize_parameters(json_schema["schema"])

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

                is_stream = is_stream and not has_media_modality
                started_at = time.time()

                max_retries = 3
                for attempt in range(max_retries):
                    if is_stream:
                        gemini_chat_url = f"https://generativelanguage.googleapis.com/v1beta/models/{chat['model']}:streamGenerateContent?key={self.api_key}&alt=sse"
                        ctx.log(f"POST {gemini_chat_url} (stream={is_stream})")
                        ctx.log(gemini_chat_summary(gemini_chat))

                        try:
                            if attempt > 0:
                                await asyncio.sleep(attempt * 0.5)
                                ctx.log(f"Retrying request (attempt {attempt + 1}/{max_retries})...")

                            async with session.post(
                                gemini_chat_url,
                                headers=self.headers,
                                data=json.dumps(gemini_chat),
                                timeout=ctx.get_client_timeout(),
                            ) as response:
                                return await self.handle_stream_response(response, chat, started_at, context=context)
                        except Exception as e:
                            err_msg = str(e)
                            if ("thinking budget" in err_msg.lower() or "thinkingconfig" in err_msg.lower()) and attempt < max_retries - 1:
                                if "generationConfig" in gemini_chat and "thinkingConfig" in gemini_chat["generationConfig"]:
                                    del gemini_chat["generationConfig"]["thinkingConfig"]
                                chat.pop("thinkingConfig", None)
                                ctx.dbg("Thinking budget not supported for model. Retrying stream without thinkingConfig...")
                                continue
                            raise e

                    gemini_chat_url = f"https://generativelanguage.googleapis.com/v1beta/models/{chat['model']}:generateContent?key={self.api_key}"

                    ctx.log(f"POST {gemini_chat_url}")
                    ctx.log(gemini_chat_summary(gemini_chat))

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
                                timeout=ctx.get_client_timeout(),
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
                                ctx.err(f"Request failed: {str(e)}", e)
                                raise e from None

                    if "error" in obj:
                        err_msg = obj["error"].get("message", "") if isinstance(obj["error"], dict) else str(obj["error"])
                        if ("thinking budget" in err_msg.lower() or "thinkingconfig" in err_msg.lower()) and attempt < max_retries - 1:
                            if "generationConfig" in gemini_chat and "thinkingConfig" in gemini_chat["generationConfig"]:
                                del gemini_chat["generationConfig"]["thinkingConfig"]
                            chat.pop("thinkingConfig", None)
                            ctx.dbg("Thinking budget not supported for model. Retrying without thinkingConfig...")
                            continue
                        ctx.log(f"Error: {obj['error']}")
                        raise Exception(obj["error"]["message"])

                    if ctx.debug:
                        ctx.log_json(obj)

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
                                tc = {
                                    "id": f"call_{len(tool_calls)}_{int(time.time())}",  # Gemini doesn't return ID, generate one
                                    "type": "function",
                                    "function": {"name": fc["name"], "arguments": json.dumps(fc["args"])},
                                }
                                signature = part.get("thoughtSignature") or part.get("thought_signature")
                                if signature:
                                    tc["thoughtSignature"] = signature
                                    tc["extra_content"] = {"google": {"thought_signature": signature}}
                                tool_calls.append(tc)

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
                                        context=context,
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

                return ctx.log_json(self.to_response(response, chat, started_at, context=context))

    ctx.add_provider(GoogleProvider)
