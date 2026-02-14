import asyncio
import io
import json
import os
import time
from datetime import datetime
from typing import Any

from aiohttp import web

from llms.db import count_tokens_approx

from .db import AppDB

g_db = None


def install(ctx):
    def get_db():
        global g_db
        if g_db is None and AppDB:
            try:
                db_path = os.path.join(ctx.get_user_path(), "app", "app.sqlite")
                g_db = AppDB(ctx, db_path)
                ctx.register_shutdown_handler(g_db.close)

            except Exception as e:
                ctx.err("Failed to init AppDB", e)
        return g_db

    if not get_db():
        return

    thread_fields = [
        "id",
        "threadId",
        "createdAt",
        "updatedAt",
        "title",
        "model",
        "modelInfo",
        "modalities",
        "messages",
        "tools",
        "args",
        "cost",
        "inputTokens",
        "outputTokens",
        "stats",
        "provider",
        "providerModel",
        "publishedAt",
        "startedAt",
        "completedAt",
        "metadata",
        "error",
        "ref",
        "contextTokens",
        "parentId",
    ]

    def thread_dto(row):
        return row and g_db.to_dto(
            row,
            [
                "messages",
                "tools",
                "toolHistory",
                "modalities",
                "args",
                "modelInfo",
                "stats",
                "metadata",
                "providerResponse",
            ],
        )

    def request_dto(row):
        return row and g_db.to_dto(row, ["usage"])

    def prompt_to_title(prompt):
        return prompt[:100] + ("..." if len(prompt) > 100 else "") if prompt else None

    def timestamp_messages(messages):
        timestamp = int(time.time() * 1000)
        for message in messages:
            if "timestamp" not in message:
                message["timestamp"] = timestamp
                timestamp += 1  # make unique
        return messages

    async def query_threads(request):
        query = request.query.copy()
        if "fields" not in query:
            query["fields"] = thread_fields
        rows = g_db.query_threads(query, user=ctx.get_username(request))
        dtos = [thread_dto(row) for row in rows]
        return web.json_response(dtos)

    ctx.add_get("threads", query_threads)

    async def create_thread(request):
        thread = await request.json()
        id = await g_db.create_thread_async(thread, user=ctx.get_username(request))
        row = g_db.get_thread(id, user=ctx.get_username(request))
        return web.json_response(thread_dto(row) if row else "")

    ctx.add_post("threads", create_thread)

    async def get_thread(request):
        id = request.match_info["id"]
        row = g_db.get_thread(id, user=ctx.get_username(request))
        return web.json_response(thread_dto(row) if row else "")

    ctx.add_get("threads/{id}", get_thread)

    async def update_thread(request):
        thread = await request.json()
        id = request.match_info["id"]
        update_count = await g_db.update_thread_async(id, thread, user=ctx.get_username(request))
        if update_count == 0:
            raise Exception("Thread not found")
        row = g_db.get_thread(id, user=ctx.get_username(request))
        return web.json_response(thread_dto(row) if row else "")

    ctx.add_patch("threads/{id}", update_thread)

    async def delete_thread(request):
        id = request.match_info["id"]
        g_db.delete_thread(id, user=ctx.get_username(request))
        return web.json_response({})

    ctx.add_delete("threads/{id}", delete_thread)

    async def queue_chat_handler(request):
        # Check authentication if enabled
        is_authenticated, user_data = ctx.check_auth(request)
        if not is_authenticated:
            return web.json_response(ctx.error_auth_required, status=401)

        if not request.body_exists:
            raise Exception("messages required")

        chat = await request.json()

        messages = timestamp_messages(chat.get("messages", []))
        if len(messages) == 0:
            raise Exception("messages required")

        id = request.match_info["id"]
        thread = thread_dto(g_db.get_thread(id, user=ctx.get_username(request)))
        if not thread:
            raise Exception("Thread not found")

        tools = chat.get("tools", thread.get("tools", []))
        update_thread = {
            "messages": messages,
            "tools": tools,
            "startedAt": datetime.now(),
            "completedAt": None,
            "error": None,
        }

        model = chat.get("model", None)
        if model:
            update_thread["model"] = model
        metadata = chat.get("metadata", {})
        if len(metadata) > 0:
            update_thread["metadata"] = metadata
        if chat.get("modalities") or not thread.get("modalities"):
            update_thread["modalities"] = chat.get("modalities", ["text"])
        system_prompt = ctx.chat_to_system_prompt(chat)
        if system_prompt:
            update_thread["systemPrompt"] = system_prompt

        args = thread.get("args") or {}
        for k, v in chat.items():
            if k in ctx.request_args:
                args[k] = v
        update_thread["args"] = args

        # allow chat to override thread title
        title = chat.get("title")
        if title:
            update_thread["title"] = title
        else:
            # only update thread title if it's not already set
            title = thread.get("title")
            if not title:
                update_thread["title"] = title = prompt_to_title(ctx.last_user_prompt(chat))

        user = ctx.get_username(request)
        await g_db.update_thread_async(
            id,
            update_thread,
            user=user,
        )
        thread = thread_dto(g_db.get_thread(id, user=user))
        if not thread:
            raise Exception("Thread not found")

        metadata = thread.get("metadata") or {}
        chat = {
            "model": thread.get("model"),
            "messages": thread.get("messages"),
            "modalities": thread.get("modalities"),
            "tools": thread.get("tools"),  # tools request
            "metadata": metadata,
        }
        args = thread.get("args") or {}
        for k, v in args.items():
            if k in ctx.request_args:
                chat[k] = v

        ctx.dbg("CHAT\n" + json.dumps(chat, indent=2))

        context = {
            "chat": chat,
            "user": user,
            "threadId": id,
            "metadata": metadata,
            "tools": metadata.get("tools", "all"),  # only tools: all|none|<tool1>,<tool2>,...
        }

        # execute chat in background thread
        async def run_chat(chat_req, context_req):
            try:
                await ctx.chat_completion(chat_req, context=context_req)
            except Exception as ex:
                ctx.err("run_chat", ex)
                # shouldn't be necessary to update thread in db with error as it's done in chat_error filter
                thread = thread_dto(g_db.get_thread(id, user=ctx.get_username(request)))
                if thread and not thread.get("error"):
                    await chat_error(ex, context)

        asyncio.create_task(run_chat(chat, context))

        return web.json_response(thread_dto(thread))

    ctx.add_post("threads/{id}/chat", queue_chat_handler)

    async def get_thread_updates(request):
        id = request.match_info["id"]
        after = request.query.get("after", None)
        user = ctx.get_username(request)
        thread = g_db.get_thread(id, user=user)
        if not thread:
            raise Exception("Thread not found")
        if after:
            started = time.time()
            thread_id = thread.get("id")
            thread_updated_at = thread.get("updatedAt")

            while thread_updated_at <= after:
                thread_updated_at = g_db.get_thread_column(thread_id, "updatedAt", user=user)
                # if thread is not updated in 30 seconds, break
                if time.time() - started > 10:
                    break
                await asyncio.sleep(1)
            ctx.dbg(f"get_thread_updates: {thread_id} / {thread_updated_at} < {after} / {thread_updated_at < after}")
            thread = g_db.get_thread(thread_id, user=user)
        return web.json_response(thread_dto(thread))

    ctx.add_get("threads/{id}/updates", get_thread_updates)

    async def cancel_thread(request):
        id = request.match_info["id"]
        await g_db.update_thread_async(
            id, {"completedAt": datetime.now(), "error": "Request was canceled"}, user=ctx.get_username(request)
        )
        thread = g_db.get_thread(id, user=ctx.get_username(request))
        ctx.dbg(f"cancel_thread: {id} / {thread.get('error')} / {thread.get('completedAt')}")
        return web.json_response(thread_dto(thread))

    ctx.add_post("threads/{id}/cancel", cancel_thread)

    async def query_requests(request):
        rows = g_db.query_requests(request.query, user=ctx.get_username(request))
        dtos = [request_dto(row) for row in rows]
        return web.json_response(dtos)

    ctx.add_get("requests", query_requests)

    async def delete_request(request):
        id = request.match_info["id"]
        g_db.delete_request(id, user=ctx.get_username(request))
        return web.json_response({})

    ctx.add_delete("requests/{id}", delete_request)

    async def requests_summary(request):
        rows = g_db.get_request_summary(user=ctx.get_username(request))
        stats = {
            "dailyData": {},
            "years": [],
            "totalCost": 0,
            "totalRequests": 0,
            "totalInputTokens": 0,
            "totalOutputTokens": 0,
        }
        years = set()
        for row in rows:
            date = row["date"]
            year = int(date[:4])
            years.add(year)
            stats["dailyData"][date] = {
                "cost": row["cost"],
                "requests": row["requests"],
                "inputTokens": row["inputTokens"],
                "outputTokens": row["outputTokens"],
            }
            stats["totalCost"] += row["cost"] or 0
            stats["totalRequests"] += row["requests"] or 0
            stats["totalInputTokens"] += row["inputTokens"] or 0
            stats["totalOutputTokens"] += row["outputTokens"] or 0

        stats["years"] = sorted(years)
        return web.json_response(stats)

    ctx.add_get("requests/summary", requests_summary)

    async def daily_requests_summary(request):
        day = request.match_info["day"]
        summary = g_db.get_daily_request_summary(day, user=ctx.get_username(request))
        return web.json_response(summary)

    ctx.add_get("requests/summary/{day}", daily_requests_summary)

    async def sync_thread(request):
        user = ctx.get_username(request)
        take = min(int(request.query.get("take", "200")), 1000)

        threads = g_db.query_threads({"null": "contextTokens", "take": take}, user=user)
        updated = 0
        for thread in threads:
            id = thread["id"]
            messages = json.loads(thread["messages"])
            context_tokens = count_tokens_approx(messages)
            await g_db.update_thread_async(id, {"contextTokens": context_tokens}, user=user)
            updated += 1

        return web.json_response({"updated": updated})

    ctx.add_get("threads/sync", sync_thread)

    async def compact_thread(request):
        id = request.match_info["id"]
        user = ctx.get_username(request)
        thread = g_db.get_thread(id, user=user)
        if not thread:
            raise Exception("Thread not found")

        messages_json = thread["messages"]
        thread_messages = json.loads(messages_json)
        message_count = len(thread_messages)
        token_count = count_tokens_approx(thread_messages)
        target_tokens = int(token_count * 0.3)  # 30% of original

        compact_template = ctx.config["defaults"]["compact"] if "compact" in ctx.config.get("defaults", {}) else None
        if not compact_template:
            raise Exception("'compact' template not found in llms.json defaults")

        compact_template = compact_template.copy()
        compact_template_messages = compact_template["messages"].copy()
        user_message = compact_template_messages[-1].copy()
        user_content = user_message.get("content", "")
        if not user_content and not isinstance(user_content, str):
            raise Exception("'compact' template has no user message")
        if "{messages_json}" not in user_content:
            raise Exception("'compact' template has no {messages_json} placeholder")
        user_content = user_content.replace("{message_count}", str(message_count), 1)
        user_content = user_content.replace("{token_count}", str(token_count), 1)
        user_content = user_content.replace("{target_tokens}", str(target_tokens), 1)
        user_content = user_content.replace("{messages_json}", messages_json, 1)
        user_message["content"] = user_content
        compact_template_messages[-1] = user_message
        compact_template["messages"] = compact_template_messages

        ctx.dbg(f"compact_thread: {id} / {message_count} / {token_count} / {target_tokens}\n{user_content}\n")
        context = {"chat": compact_template, "tools": "none", "user": user}
        response = await ctx.chat_completion(compact_template, context=context)

        answer = response.get("choices", [{}])[0].get("message", {}).get("content", "")
        if not answer:
            raise Exception("No answer in compact response")

        ctx.dbg(answer)
        compact_messages_response = ctx.parse_json_response(answer)
        if "messages" in compact_messages_response:
            compact_messages = compact_messages_response["messages"]
        elif (
            isinstance(compact_messages_response, list)
            and len(compact_messages_response) > 0
            and compact_messages_response[0].get("role")
        ):
            compact_messages = compact_messages_response
        else:
            raise Exception("Invalid compact messages response")

        threadId = context.get("threadId")
        if not threadId:
            raise Exception("Thread not found")
        compact_tokens = count_tokens_approx(compact_messages)

        update_thread = {
            "user": user,
            "title": thread.get("title"),
            "systemPrompt": thread.get("systemPrompt"),
            "model": thread.get("model"),
            "modelInfo": thread.get("modelInfo"),
            "modalities": thread.get("modalities"),
            "messages": compact_messages,
            "toolHistory": thread.get("toolHistory"),
            "args": thread.get("args"),
            "tools": thread.get("tools"),
            "provider": thread.get("provider"),
            "providerModel": thread.get("providerModel"),
            "completedAt": datetime.now(),
            "metadata": thread.get("metadata"),
            "ref": thread.get("ref"),
            "providerResponse": response,
            "contextTokens": compact_tokens,
            "parentId": thread.get("id"),
        }
        await g_db.update_thread_async(threadId, update_thread, user=user)

        return web.json_response(
            {
                "id": threadId,
            }
        )

    ctx.add_post("threads/{id}/compact", compact_thread)

    async def get_user_avatar(req):
        user = ctx.get_username(req)
        mode = req.query.get("mode", "light")

        # Cache for 1 hour # "Cache-Control": "public, max-age=3600",
        headers = {"Content-Type": "image/svg+xml"}

        candidate_paths = [
            os.path.join(ctx.get_user_path(user=user), "avatar." + mode + ".png"),
            os.path.join(ctx.get_user_path(user=user), "avatar." + mode + ".svg"),
            os.path.join(ctx.get_user_path(user=user), "avatar.png"),
            os.path.join(ctx.get_user_path(user=user), "avatar.svg"),
            os.path.join(ctx.get_user_path(), "avatar." + mode + ".png"),
            os.path.join(ctx.get_user_path(), "avatar." + mode + ".svg"),
            os.path.join(ctx.get_user_path(), "avatar.png"),
            os.path.join(ctx.get_user_path(), "avatar.svg"),
        ]

        for path in candidate_paths:
            if os.path.exists(path):
                headers["Content-Type"] = "image/png" if path.endswith(".png") else "image/svg+xml"
                return web.FileResponse(path, headers=headers)

        # Fall back to default 'user' avatar
        bg_color = "#1e3a8a" if mode == "dark" else "#bfdbfe"
        text_color = "#f3f4f6" if mode == "dark" else "#111827"

        default_avatar = f"""
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32" style="color:{text_color}">
            <circle cx="16" cy="16" r="16" fill="{bg_color}"/>
            <g transform="translate(4, 4)" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/>
            </g>
        </svg>
        """
        return web.Response(text=default_avatar, headers=headers)

    ctx.add_get("/avatar/user", get_user_avatar)

    async def get_agent_avatar(req):
        mode = req.query.get("mode", "light")

        # Cache for 1 hour # "Cache-Control": "public, max-age=3600",
        headers = {"Content-Type": "image/svg+xml"}

        candidate_paths = [
            os.path.join(ctx.get_user_path(), "agent." + mode + ".png"),
            os.path.join(ctx.get_user_path(), "agent." + mode + ".svg"),
            os.path.join(ctx.get_user_path(), "agent.png"),
            os.path.join(ctx.get_user_path(), "agent.svg"),
        ]

        for path in candidate_paths:
            if os.path.exists(path):
                headers["Content-Type"] = "image/png" if path.endswith(".png") else "image/svg+xml"
                return web.FileResponse(path, headers=headers)

        # Fall back to default 'agent' avatar
        bg_color = "#1f2937" if mode == "dark" else "#eceef1"
        text_color = "#f3f4f6" if mode == "dark" else "#111827"

        default_avatar = f"""
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32" style="color:{text_color}">
            <circle cx="16" cy="16" r="16" fill="{bg_color}"/>
            <path fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 20v-8a2.667 2.667 0 1 1 5.333 0v8m-5.333-4h5.333m5.334-6.667v10.667" transform="translate(2.667, 1.5)"/>
        </svg>
        """
        return web.Response(text=default_avatar, headers=headers)

    ctx.add_get("/agents/avatar", get_agent_avatar)

    async def upload_user_avatar(request):
        user = ctx.get_username(request)
        user_path = ctx.get_user_path(user=user)

        # Ensure the user directory exists
        os.makedirs(user_path, exist_ok=True)

        # Parse multipart form data
        reader = await request.multipart()
        field = await reader.next()

        if field is None or field.name != "file":
            raise Exception("No file provided")

        filename = field.filename or ""
        content_type = field.headers.get("Content-Type", "").lower()

        # Read file data
        file_data = await field.read()

        # Determine file type from extension or content type
        ext = os.path.splitext(filename)[1].lower() if filename else ""

        if ext == ".svg" or content_type == "image/svg+xml":
            # Save SVG directly
            avatar_path = os.path.join(user_path, "avatar.svg")
            with open(avatar_path, "wb") as f:
                f.write(file_data)
        elif ext == ".png" or content_type == "image/png":
            # Save PNG directly
            avatar_path = os.path.join(user_path, "avatar.png")
            with open(avatar_path, "wb") as f:
                f.write(file_data)
        else:
            # Try to convert to PNG using Pillow
            try:
                from PIL import Image

                img = Image.open(io.BytesIO(file_data))
                # Convert to RGB if necessary (for formats like JPEG)
                if img.mode in ("RGBA", "P"):
                    img = img.convert("RGBA")
                elif img.mode != "RGB":
                    img = img.convert("RGB")

                avatar_path = os.path.join(user_path, "avatar.png")
                img.save(avatar_path, "PNG")
            except ImportError:
                raise Exception(
                    "Only SVG and PNG formats are supported. Install Pillow to convert other image formats."
                ) from None

        return web.json_response({"success": True, "path": avatar_path})

    ctx.add_post("/user/avatar", upload_user_avatar)

    async def upload_agent_avatar(request):
        user_path = ctx.get_user_path()

        # Ensure the user directory exists
        os.makedirs(user_path, exist_ok=True)

        # Parse multipart form data
        reader = await request.multipart()
        field = await reader.next()

        if field is None or field.name != "file":
            raise Exception("No file provided")

        filename = field.filename or ""
        content_type = field.headers.get("Content-Type", "").lower()

        # Read file data
        file_data = await field.read()

        # Determine file type from extension or content type
        ext = os.path.splitext(filename)[1].lower() if filename else ""

        if ext == ".svg" or content_type == "image/svg+xml":
            # Save SVG directly
            avatar_path = os.path.join(user_path, "agent.svg")
            with open(avatar_path, "wb") as f:
                f.write(file_data)
        elif ext == ".png" or content_type == "image/png":
            # Save PNG directly
            avatar_path = os.path.join(user_path, "agent.png")
            with open(avatar_path, "wb") as f:
                f.write(file_data)
        else:
            # Try to convert to PNG using Pillow
            try:
                from PIL import Image

                img = Image.open(io.BytesIO(file_data))
                # Convert to RGB if necessary (for formats like JPEG)
                if img.mode in ("RGBA", "P"):
                    img = img.convert("RGBA")
                elif img.mode != "RGB":
                    img = img.convert("RGB")

                avatar_path = os.path.join(user_path, "agent.png")
                img.save(avatar_path, "PNG")
            except ImportError:
                raise Exception(
                    "Only SVG and PNG formats are supported. Install Pillow to convert other image formats."
                ) from None

        return web.json_response({"success": True, "path": avatar_path})

    ctx.add_post("/agents/avatar", upload_agent_avatar)

    async def chat_request(openai_request, context):
        nohistory = context.get("nohistory")
        chat = openai_request
        user = context.get("user", None)
        provider = context.get("provider", None)
        thread_id = context.get("threadId", None)
        model_info = context.get("modelInfo", None)

        metadata = chat.get("metadata", {})
        model = chat.get("model", None)
        messages = timestamp_messages(chat.get("messages", []))
        tools = chat.get("tools", [])
        title = context.get("title") or prompt_to_title(ctx.last_user_prompt(chat) if chat else None)
        started_at = context.get("startedAt")
        if not started_at:
            context["startedAt"] = started_at = datetime.now()
        if nohistory:
            return
        if thread_id is None:
            thread = {
                "user": user,
                "model": model,
                "provider": provider,
                "modelInfo": model_info,
                "title": title,
                "messages": messages,
                "tools": tools,
                "systemPrompt": ctx.chat_to_system_prompt(chat),
                "modalities": chat.get("modalities", ["text"]),
                "startedAt": started_at,
                "metadata": metadata,
            }
            thread_id = await g_db.create_thread_async(thread, user=user)
            context["threadId"] = thread_id
        else:
            update_thread = {
                "model": model,
                "provider": provider,
                "modelInfo": model_info,
                "startedAt": started_at,
                "messages": messages,
                "tools": tools,
                "completedAt": None,
                "error": None,
                "metadata": metadata,
            }
            await g_db.update_thread_async(thread_id, update_thread, user=user)

        completed_at = g_db.get_thread_column(thread_id, "completedAt", user=user)
        if completed_at:
            context["completed"] = True

    ctx.register_chat_request_filter(chat_request)

    async def tool_request(chat_request, context):
        if context.get("nohistory"):
            return
        messages = chat_request.get("messages", [])
        ctx.dbg(f"tool_request: messages {len(messages)}")
        thread_id = context.get("threadId", None)
        if not thread_id:
            ctx.dbg("Missing threadId")
            return
        user = context.get("user", None)
        await g_db.update_thread_async(
            thread_id,
            {
                "messages": messages,
            },
            user=user,
        )

        completed_at = g_db.get_thread_column(thread_id, "completedAt", user=user)
        if completed_at:
            context["completed"] = True

    ctx.register_chat_tool_filter(tool_request)

    def truncate_long_strings(obj, max_length=10000):
        """
        Recursively traverse a dictionary/list structure and replace
        string values longer than max_length with their length indicator.

        Args:
            obj: The object to process (dict, list, or other value)
            max_length: Maximum string length before truncation (default 10000)

        Returns:
            A new object with long strings replaced by "({length})"
        """
        if isinstance(obj, dict):
            return {key: truncate_long_strings(value, max_length) for key, value in obj.items()}
        elif isinstance(obj, list):
            return [truncate_long_strings(item, max_length) for item in obj]
        elif isinstance(obj, str):
            if len(obj) > max_length:
                return f"({len(obj)})"
            return obj
        else:
            return obj

    async def chat_response(openai_response, context):
        ctx.dbg("create_response")
        nohistory = context.get("nohistory")
        o = openai_response
        chat = context.get("chat")
        usage = o.get("usage", None)
        if not usage and not chat:
            ctx.dbg("Missing chat and usage")
            return

        user = context.get("user", None)
        thread_id = context.get("threadId", None)
        provider = context.get("provider", None)
        model_info = context.get("modelInfo", None)
        model_cost = context.get("modelCost", model_info.get("cost", None)) or {"input": 0, "output": 0}
        duration = context.get("duration", 0)

        metadata = o.get("metadata", {})
        choices = o.get("choices", [])
        tasks = []
        title = context.get("title") or prompt_to_title(ctx.last_user_prompt(chat) if chat else None)
        completed_at = datetime.now()

        model = model_info.get("name") or model_info.get("id")
        finish_reason = choices[0].get("finish_reason", None) if len(choices) > 0 else None
        input_price = model_cost.get("input", 0)
        output_price = model_cost.get("output", 0)
        input_tokens = usage.get("prompt_tokens", 0)
        output_tokens = usage.get("completion_tokens", 0)
        total_tokens = usage.get("total_tokens", input_tokens + output_tokens)
        cost = usage.get("cost") or o.get(
            "cost", ((input_price * input_tokens) + (output_price * output_tokens)) / 1000000
        )

        request = {
            "user": user,
            "model": model,
            "duration": duration,
            "cost": cost,
            "inputPrice": input_price,
            "inputTokens": input_tokens,
            "inputCachedTokens": usage.get("inputCachedTokens", 0),
            "outputPrice": output_price,
            "outputTokens": output_tokens,
            "finishReason": finish_reason,
            "provider": provider,
            "providerModel": o.get("model", None),
            "providerRef": o.get("provider", None),
            "threadId": thread_id,
            "title": title,
            "startedAt": context.get("startedAt"),
            "totalTokens": total_tokens,
            "usage": usage,
            "completedAt": completed_at,
            "ref": o.get("id", None),
        }
        if not context.get("nostore"):
            tasks.append(g_db.create_request_async(request, user=user))

        if thread_id and not nohistory:
            messages = chat.get("messages", [])
            last_role = messages[-1].get("role", None) if len(messages) > 0 else None
            if last_role == "user" or last_role == "tool":
                user_message = messages[-1]
                user_message["model"] = model
                user_message["usage"] = {
                    "tokens": input_tokens,
                    "price": input_price,
                    "cost": (input_price * input_tokens) / 1000000,
                }
            else:
                ctx.dbg(
                    f"Missing user message for thread {thread_id}, {len(messages)} messages, last role: {last_role}"
                )
            assistant_message = ctx.chat_response_to_message(o)
            assistant_message["model"] = model
            assistant_message["usage"] = {
                "tokens": output_tokens,
                "price": output_price,
                "cost": (output_price * output_tokens) / 1000000,
                "duration": duration,
            }
            messages.append(assistant_message)

            tools = chat.get("tools", [])
            update_thread = {
                "model": model,
                "providerModel": o.get("model"),
                "modelInfo": model_info,
                "messages": messages,
                "tools": tools,
                "completedAt": completed_at,
            }
            tool_history = o.get("tool_history", None)
            if tool_history:
                update_thread["toolHistory"] = tool_history
            if "error" in metadata:
                update_thread["error"] = metadata["error"]
            provider_response = context.get("providerResponse", None)
            if provider_response:
                update_thread["providerResponse"] = truncate_long_strings(provider_response)
            tasks.append(g_db.update_thread_async(thread_id, update_thread, user=user))
        elif not thread_id:
            ctx.dbg("Missing thread_id")

        await asyncio.gather(*tasks)

        if thread_id and not nohistory:
            # Update thread costs from all thread requests
            thread_requests = g_db.query_requests({"threadId": thread_id}, user=user)
            total_costs = 0
            total_input = 0
            total_output = 0
            for request in thread_requests:
                total_costs += request.get("cost", 0) or 0
                total_input += request.get("inputTokens", 0) or 0
                total_output += request.get("outputTokens", 0) or 0
            stats = {
                "inputTokens": total_input,
                "outputTokens": total_output,
                "cost": total_costs,
                "duration": duration,
                "requests": len(thread_requests),
            }
            g_db.update_thread(
                thread_id,
                {
                    "inputTokens": total_input,
                    "outputTokens": total_output,
                    "cost": total_costs,
                    "stats": stats,
                },
                user=user,
            )

    ctx.register_chat_response_filter(chat_response)

    async def chat_error(e: Exception, context: Any):
        error = ctx.error_message(e)
        ctx.dbg(f"Chat error: {error}")
        chat = context.get("chat")
        if not chat:
            ctx.dbg("Missing chat")
            return

        nohistory = context.get("nohistory")
        title = context.get("title") or prompt_to_title(ctx.last_user_prompt(chat) if chat else None)
        completed_at = datetime.now()
        user = context.get("user", None)

        thread_id = context.get("threadId", None)
        tasks = []
        if thread_id and not nohistory:
            tasks.append(g_db.update_thread_async(thread_id, {"completedAt": completed_at, "error": error}, user=user))
        elif not thread_id:
            ctx.dbg("Missing threadId")

        request = {
            "user": user,
            "model": chat.get("model", None),
            "title": title,
            "threadId": thread_id,
            "startedAt": context.get("startedAt"),
            "completedAt": completed_at,
            "error": error,
            "stackTrace": context.get("stackTrace", None),
        }
        if not context.get("nostore"):
            tasks.append(g_db.create_request_async(request, user=user))

        if len(tasks) > 0:
            await asyncio.gather(*tasks)

    ctx.register_chat_error_filter(chat_error)


__install__ = install
