from typing import Dict
import _collections_abc
import asyncio
import hashlib
import io
import json
import mimetypes
import os
import time
import uuid
from contextlib import suppress
from datetime import datetime
from typing import Any

from aiohttp import web

from llms.db import count_tokens_approx
from llms.main import AgentSliceYield, remove_avatar_files

from .db import AppDB

g_db = None
active_update_tasks: Dict[str, asyncio.Task] = {}
thread_update_events: Dict[str, asyncio.Event] = {}

DEFAULT_EVENTS_CONFIG = {
    "transport": "auto",
    "longPollTimeoutSeconds": 25,
    "sseHeartbeatSeconds": 15,
    "sseConnectTimeoutSeconds": 5,
    "sseFailureThreshold": 3,
    "sseRetryDelaySeconds": 10,
}


class AgentScheduler:
    """Bounded in-process scheduler whose authoritative queue is `agent_run`."""

    def __init__(self, db, execute_slice, log_error, format_error=str, max_concurrency=2, poll_seconds=1, lease_seconds=300):
        self.db = db
        self.execute_slice = execute_slice
        self.log_error = log_error
        self.format_error = format_error
        self.max_concurrency = max(1, int(max_concurrency))
        self.poll_seconds = max(0.1, float(poll_seconds))
        self.lease_seconds = max(30, int(lease_seconds))
        self.owner = f"{os.getpid()}:{uuid.uuid4().hex}"
        self._coordinator = None
        self._active = {}
        self._wake = asyncio.Event()
        self._stopping = False

    @property
    def running(self):
        return self._coordinator is not None and not self._coordinator.done()

    def start(self):
        if self.running:
            return
        self._stopping = False
        self.db.requeue_interrupted_agent_runs()
        self._coordinator = asyncio.create_task(self._run(), name="agent-scheduler")

    def wake(self):
        if not self.running:
            self.start()
        self._wake.set()

    def cancel(self, run_id):
        task = self._active.get(int(run_id))
        if task and not task.done():
            task.cancel()

    async def stop(self):
        self._stopping = True
        self._wake.set()
        if self._coordinator:
            self._coordinator.cancel()
        for task in list(self._active.values()):
            task.cancel()
        await asyncio.gather(*self._active.values(), return_exceptions=True)
        if self._coordinator:
            with suppress(asyncio.CancelledError):
                await self._coordinator
        self._active.clear()
        self._coordinator = None

    async def _run_claimed(self, run):
        run_id = int(run["id"])
        heartbeat = asyncio.create_task(self._renew_lease(run_id))
        try:
            await self.execute_slice(run)
        except asyncio.CancelledError:
            current = self.db.get_agent_run(run_id, user="all")
            if current and current.get("status") == "running":
                self.db.update_agent_run(run_id, {
                    "status": "queued", "leaseOwner": None, "leaseExpiresAt": None,
                })
            raise
        except Exception as ex:
            current = self.db.get_agent_run(run_id, user="all")
            if current and current.get("status") == "running":
                error = self.format_error(ex)
                self.db.update_agent_run(run_id, {
                    "status": "failed", "error": error, "completedAt": datetime.now(),
                    "leaseOwner": None, "leaseExpiresAt": None,
                })
                await self.db.update_thread_async(
                    run["threadId"],
                    {"completedAt": datetime.now(), "error": error, "status": None},
                    user=run.get("user"),
                )
                notify_thread_update(run["threadId"])
            self.log_error(f"agent run {run_id}", ex)
        finally:
            heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat

    async def _renew_lease(self, run_id):
        while True:
            await asyncio.sleep(max(10, self.lease_seconds / 3))
            if not self.db.renew_agent_run_lease(run_id, self.owner, self.lease_seconds):
                return

    async def _run(self):
        while not self._stopping:
            # Consume the wake that caused this pass before querying. A wake arriving
            # during or after the query remains set, so enqueue/query races are not lost.
            self._wake.clear()
            for run_id, task in list(self._active.items()):
                if task.done():
                    self._active.pop(run_id, None)
                    with suppress(asyncio.CancelledError, Exception):
                        task.result()

            capacity = self.max_concurrency - len(self._active)
            if capacity > 0:
                for run in self.db.claim_agent_runs(self.owner, capacity, self.lease_seconds):
                    run_id = int(run["id"])
                    self._active[run_id] = asyncio.create_task(
                        self._run_claimed(run), name=f"agent-run-{run_id}"
                    )

            if self._active:
                # Poll only while work is active so completed/yielded slices are noticed
                # even though task completion itself is not an asyncio.Event.
                with suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(self._wake.wait(), timeout=self.poll_seconds)
            else:
                # All enqueue paths call wake(). In a single-process host there is no
                # reason to query an idle durable queue every second.
                await self._wake.wait()


def resolve_events_config(config):
    defaults = (config or {}).get("defaults") or {}
    configured = defaults.get("events") or {}
    events = {**DEFAULT_EVENTS_CONFIG, **configured}
    transport = str(events.get("transport") or "auto").lower()
    events["transport"] = transport if transport in ("auto", "sse", "long-poll") else "auto"

    limits = {
        "longPollTimeoutSeconds": (1, 120),
        "sseHeartbeatSeconds": (1, 120),
        "sseConnectTimeoutSeconds": (1, 60),
        "sseFailureThreshold": (1, 20),
        "sseRetryDelaySeconds": (1, 300),
    }
    for name, (minimum, maximum) in limits.items():
        try:
            value = int(events[name])
        except (TypeError, ValueError):
            value = DEFAULT_EVENTS_CONFIG[name]
        events[name] = min(maximum, max(minimum, value))
    return events


def notify_thread_update(thread_id):
    event = thread_update_events.get(str(thread_id))
    if event:
        event.set()


def get_thread_signature(thread: Dict[str, Any]):
    if not thread:
        return ""
    messages = thread.get("messages")
    if isinstance(messages, str):
        msg_str = messages
    elif isinstance(messages, list):
        msg_str = json.dumps(messages, separators=(',', ':'))
    else:
        msg_str = str(messages or "")

    msg_len = len(msg_str)
    msg_tail = msg_str[-100:] if msg_len > 100 else msg_str
    status = str(thread.get("status") or "")
    completed = str(thread.get("completedAt") or "")
    error = str(thread.get("error") or "")

    raw = f"{msg_len}:{msg_tail}:{status}:{completed}:{error}"
    h = hashlib.md5(raw.encode("utf-8")).hexdigest()[:12]
    return f"{msg_len}:{h}"


def install(ctx):
    events_config = resolve_events_config(ctx.config)

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
        "streamingMessage",
        "tools",
        "args",
        "cost",
        "inputTokens",
        "outputTokens",
        "stats",
        "provider",
        "providerModel",
        "startedAt",
        "completedAt",
        "metadata",
        "error",
        "ref",
        "contextTokens",
        "parentId",
        "publishedAt",
        "publishedUrl",
    ]

    def thread_dto(row):
        if not row:
            return None
        dto = g_db.to_dto(
            row,
            [
                "messages",
                "streamingMessage",
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
        if dto:
            # The in-flight message is stored separately so a failed stream can't damage
            # `messages`, but clients read one list, so present it merged on the way out.
            streaming = dto.pop("streamingMessage", None)
            if isinstance(streaming, dict) and isinstance(dto.get("messages"), list):
                dto["messages"] = dto["messages"] + [{**streaming, "streaming": True}]
            dto["sig"] = get_thread_signature(dto)
            # Ownership was enforced by the thread query; include its active run even
            # when a projected thread query omitted the user column.
            run = g_db.get_active_agent_run(dto["id"], user="all")
            if run:
                dto["run"] = run
        return dto

    def message_ranges(messages):
        sequences = sorted({x.get("_sequence") for x in messages if x.get("_sequence") is not None})
        ranges = []
        for sequence in sequences:
            if not ranges or sequence > ranges[-1]["to"] + 1:
                ranges.append({"from": sequence, "to": sequence})
            else:
                ranges[-1]["to"] = sequence
        return ranges

    def limit_message_payload(messages, max_bytes, from_end=False):
        """Apply a soft byte ceiling without splitting tool-call/result groups."""
        groups = []
        index = 0
        while index < len(messages):
            group = [messages[index]]
            if messages[index].get("tool_calls"):
                index += 1
                while index < len(messages) and messages[index].get("role") == "tool":
                    group.append(messages[index])
                    index += 1
            else:
                index += 1
            groups.append(group)
        selected, used = [], 0
        iterable = reversed(groups) if from_end else groups
        for group in iterable:
            size = len(json.dumps(group, separators=(",", ":")).encode("utf-8"))
            if selected and used + size > max_bytes:
                break
            selected.append(group)
            used += size
        if from_end:
            selected.reverse()
        return [message for group in selected for message in group]

    def thread_window_dto(row, head=20, tail=100):
        if not row:
            return None
        rows = g_db.get_chat_message_window(row["id"], head=head, tail=tail)
        bounds = g_db.get_chat_message_bounds(row["id"])
        first_sequence = bounds.get("firstSequence") or 1
        head_rows = [x for x in rows if x.get("_sequence", 0) < first_sequence + head]
        tail_rows = [x for x in rows if x not in head_rows]
        messages = limit_message_payload(head_rows, 128 * 1024) + limit_message_payload(
            tail_rows, 384 * 1024, from_end=True
        )
        projected = dict(row)
        projected["messages"] = json.dumps(messages)
        dto = thread_dto(projected)
        dto["messageCount"] = bounds.get("messageCount") or 0
        dto["messageWindow"] = {**bounds, "ranges": message_ranges(messages)}
        return dto

    def message_page_dto(thread_id, rows, max_bytes=512 * 1024, from_end=False):
        messages = limit_message_payload(rows, max_bytes, from_end=from_end)
        return {
            "messages": messages,
            **g_db.get_chat_message_bounds(thread_id),
            "ranges": message_ranges(messages),
        }

    def request_dto(row):
        return row if isinstance(row, (str, int, float)) else (row and g_db.to_dto(row, ["usage"]))

    def prompt_to_title(prompt):
        return prompt[:100] + ("..." if len(prompt) > 100 else "") if prompt else None

    def timestamp_messages(messages):
        # Drop any in-flight message a client echoed back from a merged DTO, it belongs
        # to a stream still in progress and must not become durable history.
        messages = [m for m in messages if not m.get("streaming")]
        existing = [m.get("timestamp") for m in messages if isinstance(m.get("timestamp"), int)]
        # Tool callbacks can occur several times in one millisecond. Message timestamps
        # are also durable identities, so always advance beyond every existing value.
        timestamp = max(int(time.time() * 1000), max(existing, default=0) + 1)
        for message in messages:
            if "timestamp" not in message:
                message["timestamp"] = timestamp
                timestamp += 1  # make unique
        return messages

    async def query_threads(request):
        query = request.query.copy()
        if "fields" not in query:
            query["fields"] = thread_fields
        user = get_target_user(request)
        rows = g_db.query_threads(query, user=user)
        if len(rows) == 0 and ctx.is_admin(request) and "id" in query and "user" not in query:
            rows = g_db.query_threads(query, user="all")
        dtos = []
        for row in rows:
            dto = thread_dto(row)
            if dto:
                bounds = g_db.get_chat_message_bounds(dto["id"])
                latest = g_db.get_chat_message_page(
                    dto["id"], before=(bounds.get("lastSequence") or 0) + 1, take=1
                ) if bounds.get("messageCount") else []
                dto["messageCount"] = bounds.get("messageCount") or 0
                # Sidebar/recents only need a preview; retain the legacy property
                # shape without returning the complete history for every thread.
                dto["messages"] = latest
            dtos.append(dto)
        return web.json_response(dtos)

    ctx.add_get("threads", query_threads)

    async def create_thread(request):
        thread = await request.json()
        id = await g_db.create_thread_async(thread, user=ctx.get_username(request))
        row = g_db.get_thread(id, user=ctx.get_username(request))
        return web.json_response(thread_window_dto(row) if row else "")

    ctx.add_post("threads", create_thread)

    async def get_thread(request):
        id = request.match_info["id"]
        row = g_db.get_thread(id, user=ctx.get_username(request))
        if not row and ctx.is_admin(request):
            row = g_db.get_thread(id, user="all")
        dto = (thread_dto(row) if request.query.get("allMessages") == "true"
               else thread_window_dto(row)) if row else None
        if dto and dto.get("run") and dto["run"].get("status") in ("queued", "running"):
            scheduler.wake()
        return web.json_response(dto or "")

    ctx.add_get("threads/{id}", get_thread)

    async def get_thread_messages(request):
        id = request.match_info["id"]
        user = ctx.get_username(request)
        row = g_db.get_thread(id, user=user)
        if not row and ctx.is_admin(request):
            row = g_db.get_thread(id, user="all")
        if not row:
            raise web.HTTPNotFound(text="Thread not found")
        take = min(200, max(1, int(request.query.get("take", "100"))))
        max_bytes = min(2 * 1024 * 1024, max(64 * 1024, int(request.query.get("maxBytes", str(512 * 1024)))))
        before = request.query.get("before")
        after = request.query.get("after")
        rows = g_db.get_chat_message_page(
            id,
            before=int(before) if before is not None else None,
            after=int(after) if after is not None else 0,
            take=take,
        )
        return web.json_response(message_page_dto(
            id, rows, max_bytes=max_bytes, from_end=before is not None
        ))

    ctx.add_get("threads/{id}/messages", get_thread_messages)

    async def update_thread(request):
        thread = await request.json()
        id = request.match_info["id"]
        user = ctx.get_username(request)
        row = g_db.get_thread(id, user=user)
        if not row and ctx.is_admin(request):
            row = g_db.get_thread(id, user="all")
            if row:
                user = row.get("user") or "all"
        update_count = await g_db.update_thread_async(id, thread, user=user)
        if update_count == 0:
            raise Exception("Thread not found")
        row = g_db.get_thread(id, user=user)
        return web.json_response(thread_window_dto(row) if row else "")

    ctx.add_patch("threads/{id}", update_thread)

    def truncate_compaction_value(value, max_chars):
        if isinstance(value, str) and len(value) > max_chars:
            head = max_chars * 3 // 4
            tail = max_chars - head
            omitted = len(value) - max_chars
            return value[:head] + f"\n… [{omitted:,} characters omitted from model context] …\n" + value[-tail:]
        if isinstance(value, list):
            return [truncate_compaction_value(x, max_chars) for x in value]
        if isinstance(value, dict):
            return {k: truncate_compaction_value(v, max_chars) for k, v in value.items()}
        return value

    def bound_compaction_messages(messages, per_message_tokens):
        max_chars = max(4000, int(per_message_tokens) * 3)
        return [truncate_compaction_value(message, max_chars) for message in messages]

    def split_compaction_history(messages, recent_count):
        """Keep authoritative instructions and a tool-safe recent tail out of summaries."""
        protected = []
        index = 0
        while index < len(messages) and messages[index].get("role") in ("system", "developer"):
            protected.append(messages[index])
            index += 1
        history = messages[index:]
        cutoff = max(0, len(history) - recent_count)
        # Never retain a tool result without the assistant tool call which produced it.
        while cutoff > 0 and history[cutoff].get("role") == "tool":
            cutoff -= 1
        if cutoff > 0 and history[cutoff - 1].get("tool_calls"):
            cutoff -= 1
        return protected, history[:cutoff], history[cutoff:]

    def partition_compaction_messages(messages, token_limit):
        groups = []
        index = 0
        bounded = bound_compaction_messages(messages, token_limit)
        while index < len(bounded):
            group = [bounded[index]]
            if bounded[index].get("tool_calls"):
                index += 1
                while index < len(bounded) and bounded[index].get("role") == "tool":
                    group.append(bounded[index])
                    index += 1
            else:
                index += 1
            groups.append(group)
        batches, batch, batch_tokens = [], [], 0
        for group in groups:
            group_tokens = count_tokens_approx(group)
            if batch and batch_tokens + group_tokens > token_limit:
                batches.append(batch)
                batch, batch_tokens = [], 0
            batch.extend(group)
            batch_tokens += group_tokens
        if batch:
            batches.append(batch)
        return batches

    async def compact_messages(messages, target_tokens, chunk_tokens, user, recent_count=12, on_progress=None):
        """Shared, bounded and non-persisting compaction engine."""
        compact_template = (ctx.config.get("defaults") or {}).get("compact")
        if not compact_template:
            raise Exception("'compact' template not found in llms.json defaults")
        protected, source, recent = split_compaction_history(messages, recent_count)
        protected_tokens = count_tokens_approx(protected + recent)
        summary_target = max(1000, target_tokens - protected_tokens)
        last_response = None

        async def summarize(batch, target, part, total):
            nonlocal last_response
            if on_progress:
                await on_progress(part, total)
            compact_chat = json.loads(json.dumps(compact_template))
            user_message = compact_chat["messages"][-1]
            content = user_message.get("content") or ""
            content = content.replace("{message_count}", str(len(batch)), 1)
            content = content.replace("{token_count}", str(count_tokens_approx(batch)), 1)
            content = content.replace("{target_tokens}", str(target), 1)
            user_message["content"] = content.replace("{messages_json}", json.dumps(batch), 1)
            response = await ctx.chat_completion(compact_chat, context={
                "chat": compact_chat, "tools": "none", "user": user,
                "nohistory": True, "nostore": True,
            })
            last_response = response
            answer = response.get("choices", [{}])[0].get("message", {}).get("content", "")
            parsed = ctx.parse_json_response(answer)
            result = parsed.get("messages") if isinstance(parsed, dict) else parsed
            if not isinstance(result, list) or not result:
                raise Exception("Invalid compaction response: expected a non-empty messages array")
            if any(not isinstance(x, dict) or x.get("role") not in ("system", "user", "assistant")
                   or not isinstance(x.get("content"), str) for x in result):
                raise Exception("Invalid compaction response: unsupported message shape")
            return result

        summary = source
        for pass_index in range(4):
            if count_tokens_approx(summary) <= summary_target:
                break
            batches = partition_compaction_messages(summary, chunk_tokens)
            per_batch_target = max(1000, summary_target // max(1, len(batches)))
            reduced = []
            before = count_tokens_approx(summary)
            for index, batch in enumerate(batches, 1):
                reduced.extend(await summarize(batch, per_batch_target, index, len(batches)))
            summary = reduced
            after = count_tokens_approx(summary)
            if after >= before:
                raise Exception("Compaction model did not reduce the context")
        if count_tokens_approx(summary) > max(summary_target * 5 // 4, summary_target + 1000):
            raise Exception("Compaction result exceeded its context budget")
        # Chunking may produce one summary per batch. Present them to the
        # main model as one context message so providers with strict role sequencing
        # do not receive synthetic assistant turns without a corresponding user turn.
        if last_response is not None and len(summary) > 1:
            summary = [{
                "role": "system",
                "content": "\n\n".join(x.get("content", "") for x in summary if x.get("content")),
            }]
        elif last_response is not None and summary:
            summary[0] = {**summary[0], "role": "system"}
        return protected + summary + bound_compaction_messages(
            recent, max(1000, max(1, target_tokens - count_tokens_approx(protected + summary)) // max(1, len(recent)))
        ), last_response, len(recent)

    # One provider-neutral implementation is used by durable automatic snapshots
    # and explicit child-thread compaction. Exposing it also enables extension tests.
    ctx.compact_messages = compact_messages

    async def context_for_run(thread, run_id, user):
        """Return a bounded provider context while retaining full canonical history."""
        messages = thread.get("messages") or []
        metadata = thread.get("metadata") or {}
        model_info = thread.get("modelInfo") or {}
        context_limit = ((model_info.get("limit") or {}).get("context")
                         if isinstance(model_info, dict) else None)
        configured_threshold = metadata.get("compactThreshold")
        threshold = int(configured_threshold) if configured_threshold is not None else (
            max(8000, int(context_limit * .8)) if context_limit else 80000
        )
        chunk_tokens = max(8000, int(metadata.get("compactChunkTokens", 60000)))
        recent_count = max(4, int(metadata.get("compactRecentMessages", 12)))
        snapshot = g_db.get_latest_context_snapshot(thread["id"])
        tail_rows = None
        working_messages = messages
        if snapshot:
            tail_rows = g_db.get_chat_messages(thread["id"], after=snapshot["toSequence"])
            snapshot_summary = snapshot.get("summary") or []
            if len(snapshot_summary) > 1:
                snapshot_summary = [{
                    "role": "system",
                    "content": "\n\n".join(
                        x.get("content", "") for x in snapshot_summary if x.get("content")
                    ),
                }]
            elif snapshot_summary:
                snapshot_summary[0] = {**snapshot_summary[0], "role": "system"}
            working_messages = snapshot_summary + [x["message"] for x in tail_rows]
        working_tokens = count_tokens_approx(working_messages)
        g_db.update_agent_run(run_id, {
            "contextTokens": working_tokens, "contextLimit": context_limit,
        })
        if working_tokens < threshold:
            return working_messages

        rows = g_db.get_chat_messages(thread["id"])
        if not rows and not snapshot:
            # Imported/legacy threads may not have normalized message rows yet.
            # Bound their context instead of returning an oversized provider request.
            per_message = max(1000, threshold // max(1, len(messages)))
            return bound_compaction_messages(messages, per_message)

        if snapshot:
            tail_cutoff = max(0, len(tail_rows) - recent_count)
            compact_source = (snapshot.get("summary") or []) + [x["message"] for x in tail_rows[:tail_cutoff]]
            recent = [x["message"] for x in tail_rows[tail_cutoff:]]
            snapshot_from = snapshot["fromSequence"]
            snapshot_to = tail_rows[tail_cutoff - 1]["sequence"] if tail_cutoff else snapshot["toSequence"]
        else:
            cutoff = max(1, len(rows) - recent_count)
            compact_source = [x["message"] for x in rows[:cutoff]]
            recent = [x["message"] for x in rows[cutoff:]]
            snapshot_from = rows[0]["sequence"]
            snapshot_to = rows[cutoff - 1]["sequence"]
        async def update_progress(part, total_parts):
            status = f"Reducing context · {working_tokens:,} tokens · part {part}/{total_parts}"
            await g_db.update_thread_async(thread["id"], {"status": status}, user=user)
        target_total = max(2000, threshold // 4)
        projected, _, retained_count = await compact_messages(
            compact_source + recent, target_total, chunk_tokens, user,
            recent_count=len(recent), on_progress=update_progress,
        )
        summary_count = max(0, len(projected) - retained_count)
        summary, recent = projected[:summary_count], projected[summary_count:]
        g_db.create_context_snapshot(
            thread["id"], run_id, snapshot_from, snapshot_to, summary,
            model=((ctx.config.get("defaults") or {}).get("compact") or {}).get("model"),
        )
        g_db.update_agent_run(run_id, {"contextTokens": count_tokens_approx(projected)})
        await g_db.update_thread_async(
            thread["id"], {"status": f"Continuing · {count_tokens_approx(projected):,} context tokens"}, user=user
        )
        return projected

    async def execute_agent_slice(run):
        """Execute one bounded durable slice claimed by the scheduler."""
        run_id = int(run["id"])
        thread_id = run["threadId"]
        user = run.get("user")
        step_count = run.get("stepCount") or 0
        max_steps = run.get("maxSteps") or 250
        if step_count >= max_steps:
            error = f"Agent run reached its maximum step budget ({max_steps})"
            g_db.update_agent_run(run_id, {
                "status": "failed", "error": error, "completedAt": datetime.now(),
                "leaseOwner": None, "leaseExpiresAt": None,
            })
            await g_db.update_thread_async(
                thread_id, {"completedAt": datetime.now(), "error": error, "status": None}, user=user
            )
            notify_thread_update(thread_id)
            return

        row = g_db.get_thread(thread_id, user=user)
        if row and row.get("completedAt"):
            terminal_status = "failed" if row.get("error") else "completed"
            g_db.update_agent_run(run_id, {
                "status": terminal_status, "error": row.get("error"),
                "completedAt": row.get("completedAt"), "leaseOwner": None, "leaseExpiresAt": None,
            })
            return
        thread = thread_dto(row)
        if not thread:
            g_db.update_agent_run(run_id, {
                "status": "failed", "error": "Thread not found", "completedAt": datetime.now(),
                "leaseOwner": None, "leaseExpiresAt": None,
            })
            return

        metadata = thread.get("metadata") or {}
        chat = {
            "model": thread.get("model"), "messages": await context_for_run(thread, run_id, user),
            "modalities": thread.get("modalities"), "tools": thread.get("tools") or [],
            "metadata": metadata,
        }
        for k, v in (thread.get("args") or {}).items():
            if k in ctx.request_args:
                chat[k] = v
        sequence = step_count + 1
        step_id = g_db.create_agent_step(
            run_id, sequence, "model", idempotencyKey=f"run:{run_id}:step:{sequence}",
            input={"messageCount": len(chat["messages"])}
        )
        g_db.update_agent_run(run_id, {
            "nextAction": "model", "stepCount": sequence,
            "sliceCount": (run.get("sliceCount") or 0) + 1,
        })
        context = {
            "chat": chat, "user": user, "threadId": thread_id, "runId": run_id,
            "stepId": step_id, "metadata": metadata, "tools": metadata.get("tools", "all"),
            "projectedContext": True, "projectedPersistedCount": len(chat["messages"]),
        }
        try:
            response = await ctx.chat_completion(chat, context=context)
            g_db.update_agent_step(step_id, {
                "status": "completed", "output": {"responseId": response and response.get("id")},
                "completedAt": datetime.now(),
            })
            g_db.update_agent_run(run_id, {
                "status": "completed", "nextAction": None, "completedAt": datetime.now(),
                "leaseOwner": None, "leaseExpiresAt": None,
            })
            notify_thread_update(thread_id)
        except AgentSliceYield as yielded:
            g_db.update_agent_step(step_id, {
                "status": "completed", "output": {"yielded": True, "iterations": yielded.iterations},
                "completedAt": datetime.now(),
            })
            g_db.update_agent_run(run_id, {
                "status": "queued", "nextAction": "model", "leaseOwner": None, "leaseExpiresAt": None,
            })
            await g_db.update_thread_async(
                thread_id, {"status": "Continuing…"}, user=user
            )
            notify_thread_update(thread_id)
        except asyncio.CancelledError:
            raise
        except Exception as ex:
            error = ctx.error_message(ex)
            g_db.update_agent_step(step_id, {
                "status": "failed", "error": error, "completedAt": datetime.now()
            })
            g_db.update_agent_run(run_id, {
                "status": "failed", "error": error, "completedAt": datetime.now(),
                "leaseOwner": None, "leaseExpiresAt": None,
            })
            raise

    agent_defaults = (ctx.config.get("defaults") or {}).get("agent") or {}
    scheduler = AgentScheduler(
        g_db,
        execute_agent_slice,
        ctx.err,
        getattr(ctx, "error_message", str),
        max_concurrency=agent_defaults.get("maxConcurrency", 2),
        poll_seconds=agent_defaults.get("pollSeconds", 1),
        lease_seconds=agent_defaults.get("leaseSeconds", 300),
    )

    async def start_agent_scheduler():
        scheduler.start()

    async def stop_agent_scheduler():
        await scheduler.stop()

    if hasattr(ctx, "register_startup_handler"):
        ctx.register_startup_handler(start_agent_scheduler)
    if hasattr(ctx, "register_cleanup_handler"):
        ctx.register_cleanup_handler(stop_agent_scheduler)

    async def delete_thread(request):
        id = request.match_info["id"]
        user = ctx.get_username(request)
        row = g_db.get_thread(id, user=user)
        if not row and ctx.is_admin(request):
            row = g_db.get_thread(id, user="all")
            if row:
                user = row.get("user") or "all"
        g_db.delete_thread(id, user=user)
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
        user = ctx.get_username(request)
        row = g_db.get_thread(id, user=user)
        if not row and ctx.is_admin(request):
            row = g_db.get_thread(id, user="all")
            if row:
                user = row.get("user") or "all"
        thread = thread_dto(row)
        if not thread:
            raise Exception("Thread not found")
        active_run = g_db.get_active_agent_run(id, user=user)
        if active_run:
            raise web.HTTPConflict(text="An agent run is already active for this thread")

        tools = chat.get("tools", thread.get("tools", []))
        update_thread = {
            "messages": messages,
            # editing/redoing a message deliberately rewrites history, everything else
            # may only extend it (see AppDB.guard_messages)
            "truncate": bool(chat.get("truncate")),
            "tools": tools,
            "startedAt": datetime.now(),
            "completedAt": None,
            "error": None,
            "streamingMessage": None,
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

        run_id = g_db.create_agent_run(
            id, user, thread.get("model"), max_steps=int(metadata.get("maxSteps", 250))
        )
        scheduler.wake()
        thread["run"] = g_db.get_agent_run(run_id, user=user)

        return web.json_response(thread_window_dto(g_db.get_thread(id, user=user)))

    ctx.add_post("threads/{id}/chat", queue_chat_handler)

    async def get_thread_updates(request):
        id = request.match_info["id"]
        user = ctx.get_username(request)
        client_sig = request.query.get("sig", "")

        thread = g_db.get_thread(id, user=user)
        if not thread:
            raise Exception("Thread not found")

        dto = thread_window_dto(thread)
        if not dto:
            raise Exception("Thread not found")

        if dto.get("completedAt") or dto.get("error"):
            return web.json_response(dto)

        if client_sig and dto.get("sig") != client_sig:
            return web.json_response(dto)

        event = thread_update_events.setdefault(str(id), asyncio.Event())
        timeout = float(events_config["longPollTimeoutSeconds"])
        end_time = time.time() + timeout

        while time.time() < end_time:
            remaining = max(0.1, end_time - time.time())
            try:
                await asyncio.wait_for(event.wait(), timeout=remaining)
            except asyncio.TimeoutError:
                break
            finally:
                event.clear()

            updated_thread = g_db.get_thread(id, user=user)
            if not updated_thread:
                break

            updated_dto = thread_window_dto(updated_thread)
            if not updated_dto:
                break

            if updated_dto.get("completedAt") or updated_dto.get("error") or updated_dto.get("sig") != client_sig:
                dto = updated_dto
                break

        return web.json_response(dto)

    ctx.add_get("threads/{id}/updates", get_thread_updates)

    async def stream_thread_updates(request):
        if events_config["transport"] == "long-poll":
            raise web.HTTPConflict(text="SSE is disabled by the configured event transport")

        id = request.match_info["id"]
        user = ctx.get_username(request)
        client_sig = request.query.get("sig", "")
        thread = g_db.get_thread(id, user=user)
        dto = thread_window_dto(thread) if thread else None
        if not dto:
            raise web.HTTPNotFound(text="Thread not found")

        response = web.StreamResponse(
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            }
        )
        await response.prepare(request)

        async def send_event(name, data, event_id=None, retry_ms=None):
            lines = []
            if retry_ms:
                lines.append(f"retry: {retry_ms}")
            if event_id:
                lines.append(f"id: {event_id}")
            lines.append(f"event: {name}")
            payload = json.dumps(data, separators=(",", ":"))
            lines.extend(f"data: {line}" for line in payload.splitlines() or [""])
            await response.write(("\n".join(lines) + "\n\n").encode("utf-8"))

        try:
            await send_event(
                "connected", dto, dto.get("sig"),
                events_config["sseRetryDelaySeconds"] * 1000,
            )
            if dto.get("completedAt") or dto.get("error"):
                return response

            event = thread_update_events.setdefault(str(id), asyncio.Event())
            heartbeat = float(events_config["sseHeartbeatSeconds"])
            current_sig = dto.get("sig") or client_sig

            while True:
                try:
                    await asyncio.wait_for(event.wait(), timeout=heartbeat)
                    event.clear()
                except asyncio.TimeoutError:
                    await send_event("heartbeat", {"sig": current_sig})
                    continue

                updated = g_db.get_thread(id, user=user)
                updated_dto = thread_window_dto(updated) if updated else None
                if not updated_dto:
                    break
                updated_sig = updated_dto.get("sig") or ""
                if updated_sig != current_sig:
                    current_sig = updated_sig
                    await send_event("thread", updated_dto, current_sig)
                if updated_dto.get("completedAt") or updated_dto.get("error"):
                    break
        except (ConnectionResetError, BrokenPipeError):
            pass
        except asyncio.CancelledError:
            raise
        finally:
            with suppress(ConnectionResetError, RuntimeError):
                await response.write_eof()
        return response

    ctx.add_get("threads/{id}/updates/stream", stream_thread_updates)

    async def cancel_thread(request):
        id = request.match_info["id"]
        user = ctx.get_username(request)
        run = g_db.get_active_agent_run(id, user=user)
        if run:
            g_db.update_agent_run(run["id"], {"status": "cancelled", "completedAt": datetime.now()})
            scheduler.cancel(run["id"])
        await g_db.update_thread_async(
            id, {"completedAt": datetime.now(), "error": "Request was canceled"}, user=user
        )
        thread = g_db.get_thread(id, user=user)
        ctx.dbg(f"cancel_thread: {id} / {thread.get('error')} / {thread.get('completedAt')}")
        return web.json_response(thread_window_dto(thread))

    ctx.add_post("threads/{id}/cancel", cancel_thread)

    async def get_run(request):
        run_id = request.match_info["id"]
        run = g_db.get_agent_run(run_id, user=ctx.get_username(request))
        if not run:
            raise web.HTTPNotFound(text="Run not found")
        run["steps"] = g_db.get_agent_steps(run_id, after=int(request.query.get("after", 0)))
        return web.json_response(run)

    ctx.add_get("runs/{id}", get_run)

    def get_target_user(request):
        user_param = request.query.get("user")
        if user_param and ctx.is_admin(request):
            return user_param
        return ctx.get_username(request)

    async def query_requests(request):
        rows = g_db.query_requests(request.query, user=get_target_user(request))
        dtos = [request_dto(row) for row in rows]
        return web.json_response(dtos)

    ctx.add_get("requests", query_requests)

    async def delete_request(request):
        id = request.match_info["id"]
        g_db.delete_request(id, user=ctx.get_username(request))
        return web.json_response({})

    ctx.add_delete("requests/{id}", delete_request)

    async def requests_summary(request):
        rows = g_db.get_request_summary(user=get_target_user(request))
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
        summary = g_db.get_daily_request_summary(day, user=get_target_user(request))
        return web.json_response(summary)

    ctx.add_get("requests/summary/{day}", daily_requests_summary)

    async def admin_users_summary(request):
        if not ctx.is_admin(request):
            return web.json_response(ctx.create_error_response("Admin role required", "Forbidden"), status=403)
        summary = g_db.get_users_summary()
        return web.json_response(summary)

    ctx.add_get("requests/users", admin_users_summary)

    async def admin_users_list(request):
        if not ctx.is_admin(request):
            return web.json_response(ctx.create_error_response("Admin role required", "Forbidden"), status=403)
        db_users = set(g_db.get_users_list())
        users_file = os.path.join(ctx.get_user_path(), "users.json")
        if os.path.exists(users_file):
            try:
                with open(users_file, "r") as f:
                    users_data = json.load(f)
                    for uname in users_data.keys():
                        db_users.add(uname)
            except Exception:
                pass
        return web.json_response(sorted(list(db_users)))

    ctx.add_get("requests/users/list", admin_users_list)

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

        thread_messages = json.loads(thread["messages"])
        token_count = count_tokens_approx(thread_messages)
        metadata = thread.get("metadata") or {}
        if isinstance(metadata, str):
            metadata = json.loads(metadata)
        model_info = thread.get("modelInfo") or {}
        if isinstance(model_info, str):
            model_info = json.loads(model_info)
        context_limit = ((model_info.get("limit") or {}).get("context")
                         if isinstance(model_info, dict) else None)
        target_tokens = max(2000, min(
            int(token_count * .3), int(context_limit * .5) if context_limit else token_count
        ))
        compact_messages_value, response, _ = await compact_messages(
            thread_messages,
            target_tokens,
            max(8000, int(metadata.get("compactChunkTokens", 60000))),
            user,
            recent_count=max(4, int(metadata.get("compactRecentMessages", 12))),
        )
        compact_tokens = count_tokens_approx(compact_messages_value)

        child_thread = {
            "user": user,
            "title": thread.get("title"),
            "systemPrompt": thread.get("systemPrompt"),
            "model": thread.get("model"),
            "modelInfo": thread.get("modelInfo"),
            "modalities": thread.get("modalities"),
            "messages": compact_messages_value,
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
        threadId = await g_db.create_thread_async(child_thread, user=user)

        return web.json_response(
            {
                "id": threadId,
            }
        )

    ctx.add_post("threads/{id}/compact", compact_thread)

    async def get_user_avatar(req):
        user = ctx.get_username(req)
        theme = req.query.get("theme")

        # Fall back to default 'user' avatar
        mode = "dark" if theme == "dark" else "light"
        bg_color = "#1e3a8a" if mode == "dark" else "#dbeafe"
        text_color = "#f3f4f6" if mode == "dark" else "#111827"

        if theme:
            config = get_theme_config(theme, req)
            vars = config.get("vars", {})
            mode = vars.get("colorScheme", mode)
            bg_color = vars.get("--user-bg", bg_color)
            text_color = vars.get("--user-text", text_color)

        headers = {"Content-Type": "image/svg+xml"}

        filenames = [
            "avatar." + mode + ".webp",
            "avatar." + mode + ".png",
            "avatar." + mode + ".jpg",
            "avatar." + mode + ".jpeg",
            "avatar." + mode + ".svg",
            "avatar.webp",
            "avatar.png",
            "avatar.jpg",
            "avatar.jpeg",
            "avatar.svg",
        ]
        path = ctx.get_user_avatar_path(user, filenames)

        if path:
            content_type, _ = mimetypes.guess_type(path)
            headers["Content-Type"] = content_type or "image/png"
            return web.FileResponse(path, headers=headers)

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
        theme = req.query.get("theme")

        # Fall back to default 'user' avatar
        mode = "dark" if theme == "dark" else "light"
        # Fall back to default 'agent' avatar
        bg_color = "#1e293b" if mode == "dark" else "#f3f4f6"
        text_color = "#f1f5f9" if mode == "dark" else "#111827"

        if theme:
            config = get_theme_config(theme, req)
            vars = config.get("vars", {})
            mode = vars.get("colorScheme", mode)
            bg_color = vars.get("--assistant-bg", bg_color)
            text_color = vars.get("--assistant-text", text_color)

        headers = {"Content-Type": "image/svg+xml"}

        candidate_paths = [
            os.path.join(ctx.get_user_path(), "agent." + mode + ".webp"),
            os.path.join(ctx.get_user_path(), "agent." + mode + ".png"),
            os.path.join(ctx.get_user_path(), "agent." + mode + ".jpg"),
            os.path.join(ctx.get_user_path(), "agent." + mode + ".jpeg"),
            os.path.join(ctx.get_user_path(), "agent." + mode + ".svg"),
            os.path.join(ctx.get_user_path(), "agent.webp"),
            os.path.join(ctx.get_user_path(), "agent.png"),
            os.path.join(ctx.get_user_path(), "agent.jpg"),
            os.path.join(ctx.get_user_path(), "agent.jpeg"),
            os.path.join(ctx.get_user_path(), "agent.svg"),
        ]

        for path in candidate_paths:
            if os.path.exists(path):
                content_type, _ = mimetypes.guess_type(path)
                headers["Content-Type"] = content_type or "image/png"
                return web.FileResponse(path, headers=headers)

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

        # Remove existing avatar images in all formats before saving new one
        if hasattr(ctx, "remove_avatar_files"):
            ctx.remove_avatar_files(user_path, prefixes=["avatar", "avatar.dark", "avatar.light"])
        else:
            remove_avatar_files(user_path, prefixes=["avatar", "avatar.dark", "avatar.light"])

        if ext == ".svg" or content_type == "image/svg+xml":

            # Save SVG directly
            avatar_path = os.path.join(user_path, "avatar.svg")
            with open(avatar_path, "wb") as f:
                f.write(file_data)
        elif ext == ".webp" or content_type == "image/webp":
            # Save webp directly
            avatar_path = os.path.join(user_path, "avatar.webp")
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

        # Remove existing default agent avatar images in all formats before saving new one
        if hasattr(ctx, "remove_avatar_files"):
            ctx.remove_avatar_files(user_path, prefixes=["agent", "agent.dark", "agent.light"])
        else:
            remove_avatar_files(user_path, prefixes=["agent", "agent.dark", "agent.light"])

        if ext == ".svg" or content_type == "image/svg+xml":
            # Save SVG directly
            avatar_path = os.path.join(user_path, "agent.svg")
            with open(avatar_path, "wb") as f:
                f.write(file_data)
        elif ext == ".webp" or content_type == "image/webp":
            # Save webp directly
            avatar_path = os.path.join(user_path, "agent.webp")
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
            except ImportError as e:
                ctx.err("Error converting avatar", e)
                raise Exception(
                    "Only SVG and PNG formats are supported. Install Pillow to convert other image formats."
                ) from None

        return web.json_response({"success": True, "path": avatar_path})

    ctx.add_post("/agents/avatar", upload_agent_avatar)

    def get_theme_roots(request):
        themes_dirs = [os.path.join(os.path.dirname(__file__), "themes"), os.path.join(ctx.get_user_path(), "themes")]
        user = ctx.get_username(request)
        if user:
            themes_dirs.append(os.path.join(ctx.get_user_path(user), "themes"))
        return themes_dirs

    def get_theme_config(theme, request=None):
        themes_dirs = get_theme_roots(request)
        for themes_dir in themes_dirs:
            if os.path.exists(themes_dir):
                theme_path = os.path.join(themes_dir, theme)
                config_path = os.path.join(theme_path, "theme.json")
                if os.path.isdir(theme_path) and os.path.exists(config_path):
                    try:
                        with open(config_path) as f:
                            return json.load(f)
                    except Exception as e:
                        ctx.dbg(f"Error loading theme {theme}: {e}")
        return None

    # THEMES
    async def get_themes(request):
        themes = {}
        themes_dirs = get_theme_roots(request)
        for themes_dir in themes_dirs:
            if os.path.exists(themes_dir):
                for item in os.listdir(themes_dir):
                    item_path = os.path.join(themes_dir, item)
                    if os.path.isdir(item_path):
                        config_path = os.path.join(item_path, "theme.json")
                        if os.path.exists(config_path):
                            try:
                                with open(config_path, encoding="utf-8") as f:
                                    sub_theme = json.load(f)
                                    if item not in themes:
                                        themes[item] = sub_theme
                                    else:
                                        themes[item].update(sub_theme)
                            except Exception as e:
                                ctx.err(f"Failed to load theme {item}", e)
                    elif item.endswith(".json") and item != "shared.json":
                        theme_name = item[:-5]
                        try:
                            with open(item_path, encoding="utf-8") as f:
                                file_theme = json.load(f)
                                if theme_name not in themes:
                                    themes[theme_name] = file_theme
                                else:
                                    merged = dict(file_theme)
                                    if "vars" in themes[theme_name]:
                                        merged.setdefault("vars", {}).update(themes[theme_name]["vars"])
                                    themes[theme_name] = merged
                        except Exception as e:
                            ctx.err(f"Failed to load theme {theme_name}", e)
        return web.json_response(themes)

    ctx.add_get("/themes", get_themes)

    async def get_theme_file(request):
        theme_name = request.match_info.get("theme")
        file_name = request.match_info.get("file_name")

        def get_file_response(path):
            if not os.path.exists(path):
                return None

            content_type, _ = mimetypes.guess_type(path)
            headers = {}
            if content_type:
                headers["Content-Type"] = content_type
            return web.FileResponse(path, headers=headers)

        themes_dirs = get_theme_roots(request)
        # Search themes in reverse order to return the last overridden theme
        for themes_dir in reversed(themes_dirs):
            theme_path = os.path.join(themes_dir, theme_name)
            if os.path.isdir(theme_path) and os.path.exists(os.path.join(theme_path, "theme.json")):
                response = get_file_response(os.path.join(theme_path, "ui", file_name))
                if response:
                    return response

        response = get_file_response(os.path.join(os.path.dirname(__file__), "themes", theme_name, "ui", file_name))
        if response:
            return response

        return web.HTTPNotFound()

    ctx.add_get("/themes/{theme}/ui/{file_name}", get_theme_file)

    async def chat_request(openai_request, context):
        nohistory = context.get("nohistory")
        chat = openai_request
        user = context.get("user", None)
        provider = context.get("provider", None)
        thread_id = context.get("threadId", None)
        model_info = context.get("modelInfo", None)

        metadata = chat.get("metadata", {})
        model = chat.get("model", None)
        # assign back so an echoed in-flight message isn't sent to the provider either
        chat["messages"] = messages = timestamp_messages(chat.get("messages", []))
        tools = chat.get("tools", [])
        title = context.get("title") or prompt_to_title(ctx.last_user_prompt(chat) if chat else None)
        started_at = context.get("startedAt")
        if not started_at:
            context["startedAt"] = started_at = datetime.now()
        if nohistory:
            return
        if context.get("projectedContext"):
            # Durable runs send a compacted projection to the provider. Canonical
            # history is already persisted and must never be replaced by that view.
            # Track identities rather than a list offset: provider normalization may
            # merge/repair legacy messages and therefore change the projection length.
            context["projectedKnownTimestamps"] = {
                m.get("timestamp") for m in messages if m.get("timestamp") is not None
            }
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
                "status": ctx.next_loading_message(),
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
                "status": ctx.next_loading_message(),
                "streamingMessage": None,  # a previous attempt's partial is stale now
            }
            await g_db.update_thread_async(thread_id, update_thread, user=user)

        completed_at = g_db.get_thread_column(thread_id, "completedAt", user=user)
        if completed_at:
            context["completed"] = True

    ctx.register_chat_request_filter(chat_request)

    async def tool_request(chat_request, context):
        if context.get("nohistory"):
            return
        # Provider-created tool result messages do not carry timestamps. Assign their
        # durable identities before projected-context reconciliation, otherwise the
        # identity-based checkpoint skips every result while retaining its assistant
        # tool call and leaves an invalid history after the next durable slice.
        chat_request["messages"] = messages = timestamp_messages(chat_request.get("messages", []))
        ctx.dbg(f"tool_request: messages {len(messages)}")
        thread_id = context.get("threadId", None)
        if not thread_id:
            ctx.dbg("Missing threadId")
            return
        user = context.get("user", None)
        if context.get("projectedContext"):
            known = context.get("projectedKnownTimestamps")
            if known is not None:
                new_messages = [
                    m for m in messages
                    if m.get("timestamp") is not None and m.get("timestamp") not in known
                ]
                known.update(m.get("timestamp") for m in new_messages)
            else:
                # Compatibility for callers/tests that invoke the tool filter directly.
                start = int(context.get("projectedPersistedCount") or 0)
                new_messages = messages[start:]
                context["projectedPersistedCount"] = len(messages)
            if not new_messages:
                return
            stored = thread_dto(g_db.get_thread(thread_id, user=user)) or {}
            canonical = [m for m in (stored.get("messages") or []) if not m.get("streaming")]
            await g_db.update_thread_async(
                thread_id,
                {"messages": canonical + new_messages, "status": ctx.next_loading_message()},
                user=user,
            )
            g_db.annotate_chat_messages(
                thread_id, new_messages, run_id=context.get("runId"), step_id=context.get("stepId")
            )
            return
        await g_db.update_thread_async(
            thread_id,
            {
                "messages": messages,
                "status": ctx.next_loading_message(),
            },
            user=user,
        )
        g_db.annotate_chat_messages(
            thread_id, messages, run_id=context.get("runId"), step_id=context.get("stepId")
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
        is_per_request = model_cost.get("type") == "request"
        if is_per_request:
            cost = usage.get("cost") or output_price or cost

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
            # Append to the conversation the thread already has rather than rebuilding it
            # from the request: the request's copy is missing anything appended while it
            # was in flight (tool call/result messages) and has been rewritten for the
            # provider, so writing it back would drop messages.
            stored = thread_dto(g_db.get_thread(thread_id, user=user)) or {}
            messages = stored.get("messages")
            if not isinstance(messages, list) or not messages:
                messages = chat.get("messages", [])
            messages = [m for m in messages if not m.get("streaming")]
            last_role = messages[-1].get("role", None) if len(messages) > 0 else None
            input_cost = (input_price * input_tokens) / 1000000 if not is_per_request else cost

            if last_role == "user" or last_role == "tool":
                user_message = messages[-1]
                user_message["model"] = model
                if not input_tokens and user_message.get("content"):
                    input_tokens = count_tokens_approx(user_message.get("content"))
                    input_cost = (input_price * input_tokens) / 1000000 if not is_per_request else cost
                user_message["usage"] = {
                    "tokens": input_tokens,
                    "price": input_price,
                    "cost": input_cost,
                }
            else:
                ctx.dbg(
                    f"Missing user message for thread {thread_id}, {len(messages)} messages, last role: {last_role}"
                )
            assistant_message = ctx.chat_response_to_message(o)
            assistant_message["model"] = model
            if not output_tokens and assistant_message:
                content_text = assistant_message.get("content") or ""
                reasoning_text = assistant_message.get("reasoning") or ""
                output_tokens = count_tokens_approx(content_text) + count_tokens_approx(reasoning_text)

            assistant_cost = (output_price * output_tokens) / 1000000 if not is_per_request else cost
            assistant_message["usage"] = {
                "tokens": output_tokens,
                "price": output_price,
                "cost": assistant_cost,
                "duration": duration,
            }
            if is_per_request:
                assistant_message["usage"]["type"] = "request"

            messages.append(assistant_message)

            tools = chat.get("tools", [])
            update_thread = {
                "model": model,
                "provider": provider,
                "providerModel": o.get("model"),
                "modelInfo": model_info,
                "messages": messages,
                "tools": tools,
                "completedAt": completed_at,
                "status": None,
                "streamingMessage": None,  # the in-flight message is now committed
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
            g_db.annotate_chat_messages(
                thread_id, messages, run_id=context.get("runId"), step_id=context.get("stepId")
            )

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

            if is_per_request:
                stats["type"] = "request"

            g_db.update_thread(
                thread_id,
                {
                    "inputTokens": total_input,
                    "outputTokens": total_output,
                    "cost": total_costs,
                    "stats": stats,
                    "status": ctx.next_loading_message(),
                },
                user=user,
            )

    ctx.register_chat_response_filter(chat_response)

    async def chat_status(status: str, context: Any):
        ctx.dbg(f"Chat status: {status}")
        chat = context.get("chat")
        if not chat:
            ctx.dbg("Missing chat")
            return

        nohistory = context.get("nohistory")
        updated_at = datetime.now()
        user = context.get("user", None)

        thread_id = context.get("threadId", None)
        tasks = []
        if thread_id and not nohistory:
            tasks.append(g_db.update_thread_async(thread_id, {"updatedAt": updated_at, "status": status}, user=user))
        elif not thread_id:
            ctx.dbg("Missing threadId")

        if len(tasks) > 0:
            await asyncio.gather(*tasks)

    ctx.register_chat_status_filter(chat_status)

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
            "status": None,
        }
        if not context.get("nostore"):
            tasks.append(g_db.create_request_async(request, user=user))

        if len(tasks) > 0:
            await asyncio.gather(*tasks)

    ctx.register_chat_error_filter(chat_error)

    class ThreadApi:
        def __init__(self, ctx, g_db):
            self.ctx = ctx
            self.db = g_db

        def get_thread(self, thread_id, user):
            ctx.log(f"get_thread({thread_id},{user})")
            return thread_dto(self.db.get_thread(thread_id, user=user))

        async def update_thread_async(self, id, thread: Dict[str, Any], user=None):
            ctx.log(f"update_thread_async({id},{user})")
            ret = await self.db.update_thread_async(id, thread, user=user)
            notify_thread_update(id)
            return ret

        async def checkpoint_stream_async(self, id, message: Dict[str, Any], user=None):
            """
            Persist the in-flight assistant message. This writes only `streamingMessage`,
            never `messages`, so however a stream fails the conversation is untouched -
            the most that can be lost is the response currently being generated.
            """
            ret = await self.db.update_thread_async(id, {"streamingMessage": message}, user=user)
            notify_thread_update(id)
            return ret

        def get_request(self, request_id, user):
            return request_dto(self.db.get_request(request_id, user=user))


    ctx.threads = ThreadApi(ctx, g_db)


__install__ = install
