import asyncio
import json
import os
import time
from datetime import datetime
from typing import Any

from aiohttp import web

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

    async def chat_request(openai_request, context):
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
        tasks.append(g_db.create_request_async(request, user=user))

        if thread_id:
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
        else:
            ctx.dbg("Missing thread_id")

        await asyncio.gather(*tasks)

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

        title = context.get("title") or prompt_to_title(ctx.last_user_prompt(chat) if chat else None)
        completed_at = datetime.now()
        user = context.get("user", None)

        thread_id = context.get("threadId", None)
        tasks = []
        if thread_id:
            tasks.append(g_db.update_thread_async(thread_id, {"completedAt": completed_at, "error": error}, user=user))
        else:
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
        tasks.append(g_db.create_request_async(request, user=user))

        if len(tasks) > 0:
            await asyncio.gather(*tasks)

    ctx.register_chat_error_filter(chat_error)


__install__ = install
