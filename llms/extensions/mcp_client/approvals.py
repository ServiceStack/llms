"""Durable mixed tool batches, editable approvals, and no-replay recovery."""

import copy
import json

from .common import McpError, digest, dumps
from .store import TERMINAL


class Approvals:
    def __init__(self, client):
        self.client = client

    async def recover(self):
        # Recover a crash after recording the last decision but before waking the
        # agent. Invocation claims remain authoritative and are never reissued.
        for batch in self.client.store.pending_batches():
            await self.wake_ready(batch["owner"], batch["threadId"])

    def own_thread(self, context, thread_id):
        db = getattr(self.client.app, "agent_db", None)
        thread = db.get_thread(thread_id, user=context.get("user")) if db else None
        if not thread:
            raise McpError("not_found", "Thread not found")
        return thread

    async def prepare(self, message, context):
        c = self.client
        calls = message.get("tool_calls", [])
        handles = context.get("contextualTools", {})
        if not any(t["function"]["name"].startswith("mcp_") for t in calls):
            return None
        if context.get("nohistory") or context.get("nostore") or not context.get("threadId"):
            raise McpError("approval_required", "MCP chat execution requires a persisted conversation")
        self.own_thread(context, context["threadId"])
        owner = context.get("user") or "default"
        if len({t["id"] for t in calls}) != len(calls):
            raise McpError("invalid_tool_calls")
        batch_id = digest(dumps([owner, str(context["threadId"]), [t["id"] for t in calls]]))
        rows = []
        for call in calls:
            name = call["function"]["name"]
            handle = handles.get(name)
            row = {
                "toolCallId": call["id"],
                "toolName": name,
                "apiName": name,
                "source": "mcp_client" if name.startswith("mcp_") else "local",
                "invocationId": digest(batch_id + "\0" + call["id"]),
                "title": name,
                "description": "",
                "schema": {},
                "sourceMetadata": {},
                "safety": "Write",
                "status": "ready",
                "proposedArgs": {},
                "effectiveArgs": None,
                "result": None,
                "error": None,
                "reason": None,
                "toolResult": None,
                "requestType": None,
                "method": None,
                "route": None,
            }
            try:
                args = json.loads(call["function"]["arguments"])
                if not isinstance(args, dict):
                    raise McpError("schema_validation")
                row["proposedArgs"] = args
                if name.startswith("mcp_"):
                    if not handle:
                        raise McpError("access_denied", "Remote tool is not selected or available")
                    await c.verify(handle, context, args)
                    tool, server = handle["tool"], handle["server"]
                    row.update(
                        title=(server.get("displayName") or server["id"]) + ": " + tool["remoteName"],
                        description=tool["description"],
                        schema=tool["schema"],
                        sourceMetadata={
                            "source": "mcp_client",
                            "serverId": server["id"],
                            "remoteName": tool["remoteName"],
                            "schemaHash": tool["schemaHash"],
                            "configurationHash": handle["configurationHash"],
                            "bindingRevision": handle["bindingRevision"],
                            "credentialRevision": handle["credentialRevision"],
                            "version": 1,
                        },
                        status="pending" if c.needs_approval(server, tool, owner) else "ready",
                    )
            except (ValueError, McpError) as exc:
                row.update(status="failed", error=exc.code if isinstance(exc, McpError) else "invalid_arguments")
            rows.append(row)
        c.store.batch(batch_id, owner, context["threadId"], context.get("runId"), message, rows)
        return batch_id

    async def handle(self, row, context):
        c = self.client
        meta = row["sourceMetadata"]
        server = c.server(meta["serverId"], context["owner"])
        tools = await c.catalog(server, context, refresh=True)
        tool = next(
            (t for t in tools if t["remoteName"] == meta["remoteName"] and t["schemaHash"] == meta["schemaHash"]), None
        )
        if not tool:
            raise McpError("stale_tool", "Tool schema changed; request a new approval")
        return dict(
            provider=c,
            server=server,
            tool=tool,
            owner=context["owner"],
            **{k: meta[k] for k in ("configurationHash", "bindingRevision", "credentialRevision")},
        )

    async def decide(self, ident, action, body, context):
        c, owner = self.client, context["owner"]
        row = c.store.row(ident, owner)
        if not row or row["source"] != "mcp_client":
            raise McpError("not_found")
        thread = self.own_thread(context, row["threadId"])
        if thread.get("completedAt") or thread.get("error"):
            raise McpError("canceled")
        call_context = {**context, "threadId": row["threadId"]}
        if action == "approve":
            # A repeated decision reports the stored outcome; it must not rediscover
            # a changed tool or run authorization checks for a call already resolved.
            if row["status"] != "pending":
                await self.continue_after_decision(row, context)
                return row
            args = body.get("args", row["proposedArgs"])
            handle = await self.handle(row, call_context)
            await c.verify(handle, call_context, args)
            if c.store.claim(ident, owner):
                c.store.finish(ident, owner, "executing", effectiveArgs=args)
                try:
                    if body.get("alwaysApprove") is True:
                        meta = row["sourceMetadata"]
                        try:
                            c.store.save_approval_grant(
                                owner, handle["server"]["id"], handle["tool"]["remoteName"],
                                meta["configurationHash"], meta["schemaHash"],
                            )
                        except Exception as exc:
                            raise McpError("storage_error", "Could not save tool approval") from exc
                    result = await c.invoke(
                        handle, args, call_context, approved=True, invocation_id=row["invocationId"]
                    )
                    c.store.finish(ident, owner, "completed", result=result, effectiveArgs=args)
                except McpError as exc:
                    c.store.finish(
                        ident,
                        owner,
                        "outcome_unknown" if exc.code == "outcome_unknown" else "failed",
                        error=exc.code,
                        effectiveArgs=args,
                    )
        elif action == "reject":
            if c.store.claim(ident, owner, status="rejected"):
                c.store.finish(
                    ident, owner, "rejected", reason=str(body.get("reason", "User declined the remote call"))[:4096]
                )
        elif action == "reconcile":
            if row["status"] != "outcome_unknown" or body.get("decision") != "continue_without_replay":
                raise McpError("invalid_decision")
            if c.store.claim(ident, owner, expected="outcome_unknown", status="failed"):
                c.store.finish(
                    ident,
                    owner,
                    "failed",
                    reason="User reconciled the uncertain outcome; continued without replay",
                    error="outcome_unknown",
                )
        else:
            raise McpError("not_found")
        await self.continue_after_decision(row, context)
        return c.store.row(ident, owner)

    async def continue_after_decision(self, row, context):
        c, owner = self.client, context["owner"]
        # A current approval request may authorize continuation of its own run after
        # restart. Otherwise background execution still requires host reauthorization.
        for batch in c.store.batches(owner, row["threadId"]):
            if batch["runId"] and hasattr(c.app, "agent_requests"):
                c.app.agent_requests[batch["runId"]] = context["request"]
        await self.wake_ready(owner, row["threadId"])

    async def wake_ready(self, owner, thread_id):
        c = self.client
        db = getattr(c.app, "agent_db", None)
        scheduler = getattr(c.app, "agent_scheduler", None)
        if not db or not scheduler:
            return
        for batch in c.store.batches(owner, thread_id):
            rows = c.store.rows(owner, batch=batch["id"])
            if any(r["status"] in ("pending", "executing", "outcome_unknown") for r in rows):
                return
            run_id = batch["runId"]
            if run_id:
                # Compare-and-swap prevents resurrecting canceled or running work.
                with db.create_writer_connection() as conn:
                    cur = conn.execute(
                        "UPDATE agent_run SET status='queued',leaseOwner=NULL,leaseExpiresAt=NULL "
                        "WHERE id=? AND status='waiting_approval'",
                        (run_id,),
                    )
                    conn.commit()
                    if cur.rowcount:
                        scheduler.wake()

    async def pause(self, context):
        from llms.main import ToolApprovalPending

        c = self.client
        db = c.app.agent_db
        if not context.get("runId"):
            thread = self.own_thread(context, context["threadId"])
            run_id = db.create_agent_run(context["threadId"], context.get("user"), thread.get("model"))
            db.update_agent_run(run_id, {"status": "waiting_approval", "nextAction": "tools"})
            for batch in c.store.batches(context.get("user") or "default", context["threadId"]):
                c.store.attach_run(batch["id"], context.get("user") or "default", run_id)
            if context.get("request") is not None:
                c.app.agent_requests[run_id] = context["request"]
        await db.update_thread_async(
            context["threadId"], {"status": "Approval required", "streamingMessage": None}, user=context.get("user")
        )
        c.app.notify_thread_update(context["threadId"])
        raise ToolApprovalPending()

    async def execute(self, batch_id, context):
        from llms.main import g_exec_tool

        c, owner = self.client, context.get("user") or "default"
        rows = c.store.rows(owner, batch=batch_id)
        if any(r["status"] in ("pending", "executing", "outcome_unknown") for r in rows):
            await self.pause(context)
        for row in rows:
            if row["status"] != "ready":
                continue
            if not c.store.claim(row["id"], owner, expected="ready"):
                await self.pause(context)
            args = row["proposedArgs"]
            try:
                if row["source"] == "mcp_client":
                    handle = await self.handle(row, context)
                    result = await c.invoke(handle, args, context, invocation_id=row["invocationId"])
                    c.store.finish(row["id"], owner, "completed", result=result, effectiveArgs=args)
                else:
                    text, resources = await g_exec_tool(row["toolName"], args, context=context)
                    c.store.finish(
                        row["id"],
                        owner,
                        "completed",
                        result={"content": text, "resources": resources},
                        effectiveArgs=args,
                    )
            except McpError as exc:
                c.store.finish(
                    row["id"], owner, "outcome_unknown" if exc.code == "outcome_unknown" else "failed", error=exc.code
                )
        rows = c.store.rows(owner, batch=batch_id)
        if any(r["status"] not in TERMINAL for r in rows):
            await self.pause(context)
        return [self.result_message(row) for row in rows]

    @staticmethod
    def result_message(row):
        from llms.main import group_resources, to_content

        result = row.get("result")
        if row["source"] == "local" and row["status"] == "completed":
            content, resources = result["content"], result.get("resources")
        else:
            content = dumps(
                {
                    "status": row["status"],
                    "apiName": row["apiName"],
                    "source": row["source"],
                    "proposedArguments": row["proposedArgs"],
                    "arguments": row.get("effectiveArgs") or row["proposedArgs"],
                    "result": result,
                    "error": row.get("error"),
                    "reason": row.get("reason"),
                }
            )
            resources = result.get("resources") if isinstance(result, dict) else None
        message = {"role": "tool", "tool_call_id": row["toolCallId"], "content": to_content(content)}
        message.update(group_resources(resources))
        return message

    async def resume(self, chat, context):
        c = self.client
        if not context.get("threadId"):
            return
        owner = context.get("user") or "default"
        # Recover a crash between persisting the assistant call and preparing the batch.
        messages = chat.get("messages", [])
        if messages and messages[-1].get("tool_calls"):
            await self.prepare(messages[-1], context)
        for batch in c.store.batches(owner, context["threadId"]):
            outputs = await self.execute(batch["id"], context)
            call = json.loads(batch["message"])
            ids = {t["id"] for t in call["tool_calls"]}
            if not any(ids == {t["id"] for t in m.get("tool_calls", [])} for m in messages):
                # Restore the complete logical unit if a context projection omitted it.
                messages.append(call)
            present = {m.get("tool_call_id") for m in messages if m.get("role") == "tool"}
            messages.extend(copy.deepcopy(m) for m in outputs if m["tool_call_id"] not in present)
            await c.app.on_chat_tool(chat, context)
            c.store.complete_batch(batch["id"], owner)
            context["remoteToolsDispatched"] = True
