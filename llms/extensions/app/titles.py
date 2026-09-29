"""Independent title generation. No conversation filters, synthetic turns or job table:
the thread row's titleSource/titleStatus/titleVersion are the only state."""
import asyncio
import copy
import json
import re
from contextlib import suppress


def prompt_text(messages):
    for message in messages:
        if message.get("role") == "user":
            content = message.get("content")
            return content if isinstance(content, str) else " ".join(
                p.get("text", "") for p in (content or []) if p.get("type") == "text")
    return ""


_packaged_summarize = []


def packaged_summarize_template():
    """defaults.summarize from the llms.json shipped with this version, or None"""
    if not _packaged_summarize:
        try:
            from importlib.resources import files
            config = json.loads(files("llms").joinpath("llms.json").read_text(encoding="utf-8"))
            _packaged_summarize.append((config.get("defaults") or {}).get("summarize"))
        except Exception:
            _packaged_summarize.append(None)
    return copy.deepcopy(_packaged_summarize[0])


def normalize_title(value):
    if not isinstance(value, str):
        return None
    value = re.sub(r"^(?:title|chat title)\s*:\s*", "", value.strip(), flags=re.I)
    value = " ".join(value.strip('"\'`#* ').split())
    return value[:80] if value and len(value) <= 300 else None


class TitleWorker:
    TIMEOUT = 15
    RETRY_DELAY = 5

    def __init__(self, db, ctx, notify, concurrency=2):
        self.db, self.ctx, self.notify = db, ctx, notify
        self.tasks = set()
        # Bounded so title requests can't crowd out primary chat work.
        self.semaphore = asyncio.Semaphore(concurrency)

    def template(self):
        defaults = self.ctx.config.get("defaults") or {}
        if "summarize" in defaults:
            return defaults["summarize"]  # an explicit null disables titles
        # Configs created before titles existed don't have the key: use the packaged default
        return packaged_summarize_template()

    def set_status(self, thread_id, status, where="titleStatus='idle'"):
        with self.db.create_writer_connection() as conn:
            changed = self.db.db.exec(conn, f"UPDATE thread SET titleStatus=:status WHERE id=:id AND {where}",
                                      {"id": thread_id, "status": status}).rowcount
            conn.commit()
        return changed

    def enqueue(self, thread, messages, user=None):
        """Start title generation for a thread's first accepted turn. Returns the task, if any."""
        if thread.get("titleSource") != "fallback":
            return None
        prompt = prompt_text(messages).strip()[:12000]
        if not prompt or not self.template():
            self.set_status(thread["id"], "skipped")
            return None
        # idle -> pending is the claim: retries and follow-up turns never start a second request.
        if not self.set_status(thread["id"], "pending"):
            return None
        return self.spawn(thread["id"], thread.get("titleVersion") or 0, prompt, user or thread.get("user"))

    def spawn(self, thread_id, version, prompt, user=None):
        task = asyncio.get_running_loop().create_task(self.generate(thread_id, version, prompt, user))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return task

    def start(self):
        """Resume titles interrupted by a restart, using the thread's first user message."""
        rows = self.db.db.all("SELECT id,user,titleVersion FROM thread WHERE titleSource='fallback' "
                              "AND titleStatus IN ('idle','pending')")
        for row in rows:
            first = self.db.db.one("SELECT message FROM chat_message WHERE threadId=:id AND role='user' "
                                   "AND active=1 ORDER BY sequence LIMIT 1", {"id": row["id"]})
            try:
                prompt = prompt_text([json.loads(first["message"])]).strip()[:12000] if first else ""
            except (ValueError, TypeError):
                prompt = ""
            if prompt and self.template():
                self.set_status(row["id"], "pending", "titleStatus IN ('idle','pending')")
                self.spawn(row["id"], row.get("titleVersion") or 0, prompt, row.get("user"))
            else:
                self.set_status(row["id"], "skipped", "titleStatus IN ('idle','pending')")

    async def stop(self):
        tasks = list(self.tasks)
        for task in tasks:
            task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError):
                await task

    async def request_title(self, prompt, user=None):
        template = copy.deepcopy(self.template())
        if not template:
            raise ValueError("Title generation disabled")
        # Use configured handlers directly: app/agent/skill filters must not inject
        # workspace prompts, tools, or persistence identifiers into this service call.
        provider = next((p for p in self.ctx.get_providers().values() if p.provider_model(template["model"])), None)
        if provider is None:
            raise ValueError("Title model unavailable")
        info = provider.model_info(template["model"]) or {}
        budget = max(256, min(12000, ((info.get("limit") or {}).get("context") or 4096) - 512))
        template["messages"] = [m for m in template.get("messages", []) if m.get("role") == "system"]
        template["messages"].append({"role": "user", "content": prompt[:budget]})
        template["stream"] = False
        for key in ("tools", "metadata", "title", "threadId", "submissionId", "projectId"):
            template.pop(key, None)
        context = {"purpose": "thread_title", "user": user, "tools": "none", "nohistory": True, "chat": template, "modelInfo": info}
        response = await asyncio.wait_for(provider.chat(template, context=context), self.TIMEOUT)
        title = normalize_title(response.get("choices", [{}])[0].get("message", {}).get("content"))
        if not title:
            raise ValueError("Invalid title response")
        return title

    async def generate(self, thread_id, version, prompt, user=None):
        title = None
        try:
            async with self.semaphore:
                for attempt in range(2):
                    try:
                        title = await self.request_title(prompt, user)
                        break
                    except (TimeoutError, ConnectionError, OSError):
                        # One retry for transient failures; config/output errors are terminal.
                        if attempt:
                            raise
                        await asyncio.sleep(self.RETRY_DELAY)
        except asyncio.CancelledError:
            raise
        except Exception as ex:
            self.ctx.dbg(f"Title generation failed for thread {thread_id}: {ex}")
        params = {"id": thread_id, "version": version, "title": title}
        # Conditional on version/source: manual renames and deleted threads discard late results.
        with self.db.create_writer_connection() as conn:
            if title:
                self.db.db.exec(conn, """UPDATE thread SET title=:title,titleSource='generated',
                    titleStatus='complete',metadataVersion=COALESCE(metadataVersion,0)+1
                    WHERE id=:id AND titleVersion=:version AND titleSource='fallback'""", params)
            else:
                self.db.db.exec(conn, "UPDATE thread SET titleStatus='failed' "
                                "WHERE id=:id AND titleVersion=:version AND titleSource='fallback'", params)
            conn.commit()
        self.notify(thread_id)
        return title
