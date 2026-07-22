import asyncio
import base64
import json
import shutil
import time

import aiohttp


def detect_image_media_type(base64_data, declared_type=None):
    """Detect actual image media type from base64 data magic bytes.
    Falls back to declared_type if detection fails."""
    try:
        # Decode just enough bytes to check magic numbers
        header = base64.b64decode(base64_data[:32])
        if header[:4] == b"RIFF" and header[8:12] == b"WEBP":
            return "image/webp"
        elif header[:8] == b"\x89PNG\r\n\x1a\n":
            return "image/png"
        elif header[:3] == b"\xff\xd8\xff":
            return "image/jpeg"
        elif header[:4] == b"GIF8":
            return "image/gif"
    except Exception:
        pass
    return declared_type or "image/png"


def to_anthropic_messages(chat, ctx=None):
    """Convert OpenAI-format chat messages to Anthropic message format.

    Returns a tuple of (system_prompt, messages) where system_prompt is a string
    or None, and messages is a list of Anthropic-format messages.
    """
    # Extract system message (Anthropic uses top-level 'system' parameter)
    system_messages = []
    for message in chat.get("messages", []):
        if message.get("role") == "system":
            content = message.get("content", "")
            if isinstance(content, str):
                system_messages.append(content)
            elif isinstance(content, list):
                for item in content:
                    if item.get("type") == "text":
                        system_messages.append(item.get("text", ""))

    system_prompt = "\n".join(system_messages) if system_messages else None

    # Transform messages (exclude system messages)
    messages = []
    for message in chat.get("messages", []):
        if message.get("role") == "system":
            continue

        if message.get("role") == "tool":
            # Convert OpenAI tool response to Anthropic tool_result
            tool_call_id = message.get("tool_call_id")
            content = ctx.to_content(message.get("content", "")) if ctx else message.get("content", "")
            if not isinstance(content, (str, list)):
                content = str(content)

            tool_result = {"type": "tool_result", "tool_use_id": tool_call_id, "content": content}

            # Anthropic requires tool results to be in a user message
            # Check if the last message was a user message, if so append to it
            if messages and messages[-1]["role"] == "user":
                messages[-1]["content"].append(tool_result)
            else:
                messages.append({"role": "user", "content": [tool_result]})
            continue

        anthropic_message = {"role": message.get("role"), "content": []}

        # Handle interleaved thinking (must always be a list if present)
        if "thinking" in message and message["thinking"]:
            anthropic_message["content"].append({"type": "thinking", "thinking": message["thinking"]})

        content = message.get("content", "")
        if isinstance(content, str):
            if anthropic_message["content"] or message.get("tool_calls"):
                # If we have thinking or tools, we must use blocks for text
                if content:
                    anthropic_message["content"].append({"type": "text", "text": content})
            else:
                anthropic_message["content"] = content
        elif isinstance(content, list):
            for item in content:
                if item.get("type") == "text":
                    anthropic_message["content"].append({"type": "text", "text": item.get("text", "")})
                elif item.get("type") == "image_url" and "image_url" in item:
                    # Transform OpenAI image_url format to Anthropic format
                    image_url = item["image_url"].get("url", "")
                    if image_url.startswith("data:"):
                        # Extract media type and base64 data
                        parts = image_url.split(";base64,", 1)
                        if len(parts) == 2:
                            declared_type = parts[0].replace("data:", "")
                            base64_data = parts[1]
                            # Detect actual image type from bytes (file ext may not match after conversion)
                            media_type = detect_image_media_type(base64_data, declared_type)
                            anthropic_message["content"].append(
                                {
                                    "type": "image",
                                    "source": {"type": "base64", "media_type": media_type, "data": base64_data},
                                }
                            )
                elif item.get("type") == "file" and "file" in item:
                    # Transform OpenAI file format to Anthropic document format
                    file_info = item["file"]
                    file_data = file_info.get("file_data", "")
                    if file_data.startswith("data:"):
                        # Extract media type and base64 data from data URI
                        parts = file_data.split(";base64,", 1)
                        if len(parts) == 2:
                            media_type = parts[0].replace("data:", "")
                            b64_data = parts[1]
                            anthropic_message["content"].append(
                                {
                                    "type": "document",
                                    "source": {"type": "base64", "media_type": media_type, "data": b64_data},
                                }
                            )
        # Handle tool_calls
        if "tool_calls" in message and message["tool_calls"]:
            # specific check for content being a string and not empty, because we might have converted it above
            if isinstance(anthropic_message["content"], str):
                anthropic_message["content"] = []
                if content:
                    anthropic_message["content"].append({"type": "text", "text": content})

            for tool_call in message["tool_calls"]:
                function = tool_call.get("function", {})
                tool_use = {
                    "type": "tool_use",
                    "id": tool_call.get("id"),
                    "name": function.get("name"),
                    "input": json.loads(function.get("arguments", "{}")),
                }
                anthropic_message["content"].append(tool_use)

        messages.append(anthropic_message)

    return system_prompt, messages


def install_anthropic(ctx):
    from llms.main import OpenAiCompatible

    class AnthropicProvider(OpenAiCompatible):
        """Anthropic Provider using Anthropic API and API Pricing"""

        sdk = "@ai-sdk/anthropic"

        def __init__(self, **kwargs):
            if "api" not in kwargs:
                kwargs["api"] = "https://api.anthropic.com/v1"
            super().__init__(**kwargs)

            # Anthropic uses x-api-key header instead of Authorization
            if self.api_key:
                self.headers = self.headers.copy()
                if "Authorization" in self.headers:
                    del self.headers["Authorization"]
                self.headers["x-api-key"] = self.api_key

            if "anthropic-version" not in self.headers:
                self.headers = self.headers.copy()
                self.headers["anthropic-version"] = "2023-06-01"
            self.chat_url = f"{self.api}/messages"

        async def chat(self, chat, context=None):
            chat["model"] = self.provider_model(chat["model"]) or chat["model"]

            is_stream = chat["stream"] if "stream" in chat else (self.stream or False)

            chat = await self.process_chat(chat, provider_id=self.id)

            # Transform OpenAI format to Anthropic format
            system_prompt, anthropic_messages = to_anthropic_messages(chat, ctx=ctx)

            anthropic_request = {
                "model": chat["model"],
                "messages": anthropic_messages,
            }

            if system_prompt:
                anthropic_request["system"] = system_prompt

            # Handle max_tokens (required by Anthropic, uses max_tokens not max_completion_tokens)
            if "max_completion_tokens" in chat:
                anthropic_request["max_tokens"] = chat["max_completion_tokens"]
            elif "max_tokens" in chat:
                anthropic_request["max_tokens"] = chat["max_tokens"]
            else:
                # Anthropic requires max_tokens, set a default
                anthropic_request["max_tokens"] = 4096

            # Copy other supported parameters
            if "temperature" in chat:
                anthropic_request["temperature"] = chat["temperature"]
            if "top_p" in chat:
                anthropic_request["top_p"] = chat["top_p"]
            if "top_k" in chat:
                anthropic_request["top_k"] = chat["top_k"]
            if "stop" in chat:
                anthropic_request["stop_sequences"] = chat["stop"] if isinstance(chat["stop"], list) else [chat["stop"]]
            if is_stream:
                anthropic_request["stream"] = True
            elif "stream" in chat:
                anthropic_request["stream"] = chat["stream"]
            if "tools" in chat:
                anthropic_tools = []
                for tool in chat["tools"]:
                    if tool.get("type") == "function":
                        function = tool.get("function", {})
                        anthropic_tool = {
                            "name": function.get("name"),
                            "description": function.get("description"),
                            "input_schema": function.get("parameters"),
                        }
                        anthropic_tools.append(anthropic_tool)
                if anthropic_tools:
                    anthropic_request["tools"] = anthropic_tools
            if "tool_choice" in chat:
                anthropic_request["tool_choice"] = chat["tool_choice"]

            if "response_format" in chat:
                response_format = chat["response_format"]
                if response_format.get("type") == "json_schema":
                    json_schema = response_format.get("json_schema", {})
                    if "schema" in json_schema:
                        anthropic_request["output_config"] = {
                            "format": {
                                "type": "json_schema",
                                "schema": json_schema["schema"],
                            }
                        }

            ctx.log(f"POST {self.chat_url} (stream={is_stream})")
            ctx.log(json.dumps(anthropic_request, indent=2))

            if not is_stream:
                async with aiohttp.ClientSession() as session:
                    started_at = time.time()
                    async with session.post(
                        self.chat_url,
                        headers=self.headers,
                        data=json.dumps(anthropic_request),
                        timeout=ctx.get_client_timeout(),
                    ) as response:
                        return ctx.log_json(
                            self.to_response(await self.response_json(response), chat, started_at, context=context)
                        )

            started_at = time.time()
            async with aiohttp.ClientSession() as session, session.post(
                self.chat_url,
                headers=self.headers,
                data=json.dumps(anthropic_request),
                timeout=ctx.get_client_timeout(),
            ) as response:
                return await self.handle_stream_response(response, chat, started_at, context=context)

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

                    event_type = chunk.get("type")

                    if event_type == "message_start":
                        msg = chunk.get("message", {})
                        if msg.get("id"):
                            response_id = msg["id"]
                        if msg.get("model"):
                            model_name = msg["model"]
                        usage = msg.get("usage", {})
                        if usage:
                            if "input_tokens" in usage:
                                usage_acc["prompt_tokens"] = usage["input_tokens"]
                            if "output_tokens" in usage:
                                usage_acc["completion_tokens"] = usage["output_tokens"]
                            usage_acc["total_tokens"] = (
                                usage_acc.get("prompt_tokens", 0) + usage_acc.get("completion_tokens", 0)
                            )

                    elif event_type == "content_block_start":
                        idx = chunk.get("index", 0)
                        block = chunk.get("content_block", {})
                        block_type = block.get("type")
                        if block_type == "tool_use":
                            tool_calls_dict[idx] = {
                                "id": block.get("id") or "",
                                "type": "function",
                                "function": {
                                    "name": block.get("name") or "",
                                    "arguments": "",
                                },
                            }
                            if "input" in block and block["input"] and isinstance(block["input"], dict):
                                tool_calls_dict[idx]["function"]["arguments"] = json.dumps(block["input"])
                        elif block_type == "thinking":
                            if block.get("thinking"):
                                reasoning_acc += block["thinking"]
                                reasoning_field = "thinking"
                        elif block_type == "text":
                            if block.get("text"):
                                content_acc += block["text"]

                    elif event_type == "content_block_delta":
                        idx = chunk.get("index", 0)
                        delta = chunk.get("delta", {})
                        delta_type = delta.get("type")
                        if delta_type == "text_delta":
                            content_acc += delta.get("text", "")
                        elif delta_type == "thinking_delta":
                            reasoning_acc += delta.get("thinking", "")
                            reasoning_field = "thinking"
                        elif delta_type == "input_json_delta":
                            if idx not in tool_calls_dict:
                                tool_calls_dict[idx] = {
                                    "id": "",
                                    "type": "function",
                                    "function": {"name": "", "arguments": ""},
                                }
                            tool_calls_dict[idx]["function"]["arguments"] += delta.get("partial_json", "")

                    elif event_type == "message_delta":
                        delta = chunk.get("delta", {})
                        if delta.get("stop_reason"):
                            finish_reason = delta["stop_reason"]
                        usage = chunk.get("usage", {})
                        if usage:
                            if "output_tokens" in usage:
                                usage_acc["completion_tokens"] = usage["output_tokens"]
                            if "input_tokens" in usage:
                                usage_acc["prompt_tokens"] = usage["input_tokens"]
                            usage_acc["total_tokens"] = (
                                usage_acc.get("prompt_tokens", 0) + usage_acc.get("completion_tokens", 0)
                            )

                    elif event_type == "message_stop":
                        break

                    elif event_type == "error":
                        err = chunk.get("error", {})
                        msg = err.get("message") if isinstance(err, dict) else str(err)
                        raise Exception(msg or "Anthropic streaming error")

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
                            assistant_msg[reasoning_field or "thinking"] = reasoning_acc
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
                    assistant_msg[reasoning_field or "thinking"] = reasoning_acc
                if tool_calls_dict:
                    assistant_msg["tool_calls"] = [tool_calls_dict[i] for i in sorted(tool_calls_dict.keys())]

                streaming_messages = base_messages + [assistant_msg]
                await threads_api.update_thread_async(thread_id, {"messages": streaming_messages}, user=user)

            message_obj = {
                "role": "assistant",
                "content": content_acc,
            }
            if reasoning_acc:
                message_obj[reasoning_field or "thinking"] = reasoning_acc
            if tool_calls_dict:
                message_obj["tool_calls"] = [tool_calls_dict[i] for i in sorted(tool_calls_dict.keys())]

            choice_obj = {
                "index": 0,
                "message": message_obj,
                "finish_reason": finish_reason or "stop",
            }

            openai_response = {
                "id": response_id or f"gen-{int(started_at)}",
                "object": "chat.completion",
                "created": created_time or int(started_at),
                "model": model_name or chat.get("model"),
                "choices": [choice_obj],
                "usage": usage_acc or {
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                },
                "metadata": {
                    "duration": int((time.time() - started_at) * 1000),
                },
            }

            if chat is not None and "model" in chat:
                cost = self.model_cost(chat["model"])
                if cost and "input" in cost and "output" in cost:
                    openai_response["metadata"]["pricing"] = f"{cost['input']}/{cost['output']}"

            if context is not None:
                context["providerResponse"] = openai_response

            return ctx.log_json(openai_response)

        def to_response(self, response, chat, started_at, context=None):
            """Convert Anthropic response format to OpenAI-compatible format."""
            if context is not None:
                context["providerResponse"] = response
            # Transform Anthropic response to OpenAI format
            ret = {
                "id": response.get("id", ""),
                "object": "chat.completion",
                "created": int(started_at),
                "model": response.get("model", ""),
                "choices": [],
                "usage": {},
            }

            # Transform content blocks to message content
            content_parts = []
            thinking_parts = []
            tool_calls = []

            for block in response.get("content", []):
                if block.get("type") == "text":
                    content_parts.append(block.get("text", ""))
                elif block.get("type") == "thinking":
                    # Store thinking blocks separately (some models include reasoning)
                    thinking_parts.append(block.get("thinking", ""))
                elif block.get("type") == "tool_use":
                    tool_call = {
                        "id": block.get("id"),
                        "type": "function",
                        "function": {
                            "name": block.get("name"),
                            "arguments": json.dumps(block.get("input", {})),
                        },
                    }
                    tool_calls.append(tool_call)

            # Combine all text content
            message_content = "\n".join(content_parts) if content_parts else ""

            # Create the choice object
            choice = {
                "index": 0,
                "message": {"role": "assistant", "content": message_content},
                "finish_reason": response.get("stop_reason", "stop"),
            }

            # Add thinking as metadata if present
            if thinking_parts:
                choice["message"]["thinking"] = "\n".join(thinking_parts)

            # Add tool_calls if present
            if tool_calls:
                choice["message"]["tool_calls"] = tool_calls

            ret["choices"].append(choice)

            # Transform usage
            if "usage" in response:
                usage = response["usage"]
                ret["usage"] = {
                    "prompt_tokens": usage.get("input_tokens", 0),
                    "completion_tokens": usage.get("output_tokens", 0),
                    "total_tokens": usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
                }

            # Add metadata
            if "metadata" not in ret:
                ret["metadata"] = {}
            ret["metadata"]["duration"] = int(time.time() - started_at)

            if chat is not None and "model" in chat:
                cost = self.model_cost(chat["model"])
                if cost and "input" in cost and "output" in cost:
                    ret["metadata"]["pricing"] = f"{cost['input']}/{cost['output']}"

            return ret

    ctx.add_provider(AnthropicProvider)

    class AnthropicProviderCli(OpenAiCompatible):
        """Anthropic Provider using local claude CLI to make use of an existing Claude Code subscription"""

        sdk = "@ai-sdk/anthropic-cli"

        def __init__(self, **kwargs):
            if "api" not in kwargs:
                kwargs["api"] = "https://api.anthropic.com/v1"
            super().__init__(**kwargs)
            self.chat_url = f"{self.api}/messages"
            # Disable tool calling as CLI doesn't support it
            for model in self.models.values():
                model["tool_call"] = False
            # print(f"Anthropic models = {json.dumps(self.models, indent=2)}")

        def validate(self, **kwargs):
            if not shutil.which("claude"):
                return f"Provider '{self.name}' requires 'claude' in PATH"
            return None

        async def chat(self, chat, context=None):
            chat["model"] = self.provider_model(chat["model"]) or chat["model"]

            chat = await self.process_chat(chat, provider_id=self.id)

            # Convert to Anthropic message format using shared method
            system_prompt, anthropic_messages = to_anthropic_messages(chat, ctx=ctx)

            # Build the stream-json user message with Anthropic-format content
            # Find the last user message's content to use as the prompt
            last_user_content = None
            for msg in reversed(anthropic_messages):
                if msg.get("role") == "user":
                    last_user_content = msg.get("content")
                    break

            if last_user_content is None:
                last_user_content = ""

            # Build the stream-json input
            stream_json_message = {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": last_user_content
                    if isinstance(last_user_content, list)
                    else [{"type": "text", "text": last_user_content}]
                    if last_user_content
                    else [{"type": "text", "text": ""}],
                },
            }

            # If there are prior conversation messages, fold them into context
            # The claude CLI stream-json only accepts 'user' and 'control' types
            # So we prepend conversation history as context in the user message
            conversation = []
            if len(anthropic_messages) > 1:
                history_parts = []
                for msg in anthropic_messages[:-1]:
                    role = msg.get("role", "user")
                    content = msg.get("content", "")
                    if isinstance(content, list):
                        # Extract text parts from content blocks
                        text_parts = []
                        for block in content:
                            if isinstance(block, dict) and block.get("type") == "text":
                                text_parts.append(block.get("text", ""))
                        content = "\n".join(text_parts) if text_parts else ""
                    if content:
                        history_parts.append(f"[{role}]: {content}")

                if history_parts:
                    history_context = (
                        "Previous conversation:\n"
                        + "\n\n".join(history_parts)
                        + "\n\nContinue the conversation based on the above context."
                    )
                    # Prepend history to the user message content
                    if isinstance(last_user_content, list):
                        stream_json_message["message"]["content"] = [{"type": "text", "text": history_context}] + (
                            last_user_content
                            if isinstance(last_user_content, list)
                            else [{"type": "text", "text": last_user_content}]
                        )
                    else:
                        stream_json_message["message"]["content"] = [
                            {"type": "text", "text": history_context},
                            {"type": "text", "text": last_user_content or ""},
                        ]

            # Build claude CLI command
            cmd = [
                "claude",
                "-p",
                "--input-format",
                "stream-json",
                "--output-format",
                "stream-json",
                "--verbose",
                "--no-session-persistence",
            ]

            # Map model name to claude CLI model aliases
            model = chat.get("model", "")
            if model:
                cmd.extend(["--model", model])

            # Add system prompt if present
            if system_prompt:
                cmd.extend(["--system-prompt", system_prompt])

            # Add max tokens budget if specified
            max_tokens = chat.get("max_completion_tokens") or chat.get("max_tokens")
            if max_tokens:
                cmd.extend(["--max-budget-usd", str(max_tokens)])

            # Build the JSON input: single user message with conversation context folded in
            json_input = json.dumps(stream_json_message)

            ctx.log(f"Running: claude -p --input-format stream-json --output-format stream-json --model {model}")
            ctx.log(f"Messages: {len(anthropic_messages)}, System: {'yes' if system_prompt else 'no'}")

            started_at = time.time()
            ctx.log(f"JSON input size: {len(json_input)} bytes")

            try:
                process = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await process.communicate(input=json_input.encode("utf-8"))

                if process.returncode != 0:
                    stderr_text = stderr.decode("utf-8", errors="replace").strip() if stderr else ""
                    stdout_text = stdout.decode("utf-8", errors="replace").strip() if stdout else ""
                    error_msg = stderr_text or stdout_text or f"claude exited with code {process.returncode}"
                    raise Exception(f"claude CLI error: {error_msg}")

                # Parse stream-json output: multiple JSON objects, one per line
                # Find the result object (type: "result")
                output = stdout.decode("utf-8", errors="replace").strip()
                cli_response = None
                for line in output.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    # Remove any ANSI escape sequences
                    if line.endswith("\x1b[<u"):
                        line = line[:-4].strip()
                    try:
                        obj = json.loads(line)
                        if obj.get("type") == "result":
                            cli_response = obj
                            break
                    except json.JSONDecodeError:
                        continue

                if cli_response is None:
                    raise Exception("No result found in claude CLI stream-json output")

            except json.JSONDecodeError as e:
                raise Exception(f"Failed to parse claude CLI JSON output: {e}")
            except FileNotFoundError:
                raise Exception("claude CLI not found in PATH")

            # Convert claude CLI response to OpenAI-compatible format
            return ctx.log_json(self.to_cli_response(cli_response, chat, started_at, context=context))

        def to_cli_response(self, cli_response, chat, started_at, context=None):
            """Convert claude CLI JSON response to OpenAI-compatible format."""
            if context is not None:
                context["providerResponse"] = cli_response

            is_error = cli_response.get("is_error", False)
            result_text = cli_response.get("result", "")

            ret = {
                "id": cli_response.get("session_id", ""),
                "object": "chat.completion",
                "created": int(started_at),
                "model": chat.get("model", ""),
                "choices": [],
                "usage": {},
            }

            # Create the choice object
            choice = {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": result_text,
                },
                "finish_reason": "error" if is_error else "stop",
            }
            ret["choices"].append(choice)

            # Transform usage from claude CLI format
            usage = cli_response.get("usage", {})
            input_tokens = (
                usage.get("input_tokens", 0)
                + usage.get("cache_creation_input_tokens", 0)
                + usage.get("cache_read_input_tokens", 0)
            )
            output_tokens = usage.get("output_tokens", 0)
            ret["usage"] = {
                "prompt_tokens": input_tokens,
                "completion_tokens": output_tokens,
                "total_tokens": input_tokens + output_tokens,
            }

            # Add metadata
            ret["metadata"] = {
                "duration": cli_response.get("duration_ms", int((time.time() - started_at) * 1000)),
            }

            # Add cost if available
            if "total_cost_usd" in cli_response:
                ret["metadata"]["cost_usd"] = cli_response["total_cost_usd"]

            if chat is not None and "model" in chat:
                cost = self.model_cost(chat["model"])
                if cost and "input" in cost and "output" in cost:
                    ret["metadata"]["pricing"] = f"{cost['input']}/{cost['output']}"

            return ret

    ctx.add_provider(AnthropicProviderCli)
