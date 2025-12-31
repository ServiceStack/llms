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

        async def chat(self, chat):
            chat["model"] = self.provider_model(chat["model"]) or chat["model"]

            chat = await self.process_chat(chat)
            generation_config = {}

            # Filter out system messages and convert to proper Gemini format
            contents = []
            system_prompt = None

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
                        if isinstance(message["content"], list):
                            parts = []
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
                            if len(parts) > 0:
                                contents.append(
                                    {
                                        "role": message["role"]
                                        if "role" in message and message["role"] == "user"
                                        else "model",
                                        "parts": parts,
                                    }
                                )
                        else:
                            content = message["content"]
                            contents.append(
                                {
                                    "role": message["role"]
                                    if "role" in message and message["role"] == "user"
                                    else "model",
                                    "parts": [{"text": content}],
                                }
                            )

                gemini_chat = {
                    "contents": contents,
                }

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

                if "tools" in chat:
                    # gemini_chat["tools"] = chat["tools"]
                    ctx.log("Error: tools not supported in Gemini")
                elif self.tools:
                    # gemini_chat["tools"] = self.tools.copy()
                    ctx.log("Error: tools not supported in Gemini")

                if "modalities" in chat:
                    generation_config["responseModalities"] = [modality.upper() for modality in chat["modalities"]]
                    if "image" in chat["modalities"] and "image_config" in chat:
                        # delete thinkingConfig
                        del generation_config["thinkingConfig"]
                        config_map = {
                            "aspect_ratio": "aspectRatio",
                            "image_size": "imageSize",
                        }
                        generation_config["imageConfig"] = {
                            config_map[k]: v for k, v in chat["image_config"].items() if k in config_map
                        }
                    if "audio" in chat["modalities"] and self.speech_config:
                        del generation_config["thinkingConfig"]
                        generation_config["speechConfig"] = self.speech_config.copy()
                        # Currently Google Audio Models only accept AUDIO
                        generation_config["responseModalities"] = ["AUDIO"]

                started_at = int(time.time() * 1000)
                gemini_chat_url = f"https://generativelanguage.googleapis.com/v1beta/models/{chat['model']}:generateContent?key={self.api_key}"

                ctx.log(f"POST {gemini_chat_url}")
                ctx.log(gemini_chat_summary(gemini_chat))
                started_at = time.time()

                if ctx.MOCK and "modalities" in chat:
                    print("Mocking Google Gemini Image")
                    with open(f"{ctx.MOCK_DIR}/gemini-image.json") as f:
                        obj = json.load(f)
                else:
                    try:
                        async with session.post(
                            gemini_chat_url,
                            headers=self.headers,
                            data=json.dumps(gemini_chat),
                            timeout=aiohttp.ClientTimeout(total=120),
                        ) as res:
                            obj = await self.response_json(res)
                    except Exception as e:
                        ctx.log(f"Error: {res.status} {res.reason}: {e}")
                        text = await res.text()
                        try:
                            obj = json.loads(text)
                        except:
                            ctx.log(text)
                            raise e

                if "error" in obj:
                    ctx.log(f"Error: {obj['error']}")
                    raise Exception(obj["error"]["message"])

                if ctx.debug:
                    ctx.dbg(json.dumps(gemini_response_summary(obj), indent=2))

                # calculate cost per generation
                cost = None
                token_costs = obj.get("metadata", {}).get("pricing", "")
                if token_costs:
                    input_price, output_price = token_costs.split("/")
                    input_per_token = float(input_price) / 1000000
                    output_per_token = float(output_price) / 1000000
                    if "usageMetadata" in obj:
                        input_tokens = obj["usageMetadata"]["promptTokenCount"]
                        output_tokens = obj["usageMetadata"]["candidatesTokenCount"]
                        cost = (input_per_token * input_tokens) + (output_per_token * output_tokens)

                response = {
                    "id": f"chatcmpl-{started_at}",
                    "created": started_at,
                    "model": obj.get("modelVersion", chat["model"]),
                }
                choices = []
                for i, candidate in enumerate(obj["candidates"]):
                    role = "assistant"
                    if "content" in candidate and "role" in candidate["content"]:
                        role = "assistant" if candidate["content"]["role"] == "model" else candidate["content"]["role"]

                    # Safely extract content from all text parts
                    content = ""
                    reasoning = ""
                    images = []
                    audios = []
                    if "content" in candidate and "parts" in candidate["content"]:
                        text_parts = []
                        reasoning_parts = []
                        for part in candidate["content"]["parts"]:
                            if "text" in part:
                                if "thought" in part and part["thought"]:
                                    reasoning_parts.append(part["text"])
                                else:
                                    text_parts.append(part["text"])
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
                            "content": content,
                        },
                    }
                    if reasoning:
                        choice["message"]["reasoning"] = reasoning
                    if len(images) > 0:
                        choice["message"]["images"] = images
                    if len(audios) > 0:
                        choice["message"]["audios"] = audios
                    choices.append(choice)
                response["choices"] = choices
                if "usageMetadata" in obj:
                    usage = obj["usageMetadata"]
                    response["usage"] = {
                        "completion_tokens": usage["candidatesTokenCount"],
                        "total_tokens": usage["totalTokenCount"],
                        "prompt_tokens": usage["promptTokenCount"],
                    }

                return ctx.log_json(self.to_response(response, chat, started_at))

    ctx.add_provider(GoogleProvider)
