import json
import os
import time
from datetime import datetime, timedelta
from typing import Any, Dict

from llms.db import DbManager, count_tokens_approx, order_by, select_columns, to_dto, valid_columns


def with_user(data, user):
    if user is None:
        if "user" in data:
            del data["user"]
        return data
    else:
        data["user"] = user
        return data


class AppDB:
    def __init__(self, ctx, db_path):
        if db_path is None:
            raise ValueError("db_path is required")

        self.ctx = ctx
        self.db_path = str(db_path)
        self._closed = False

        dirname = os.path.dirname(self.db_path)
        if dirname:
            os.makedirs(dirname, exist_ok=True)

        self.db = DbManager(ctx, self.db_path)
        self.columns = {
            "thread": {
                "id": "INTEGER",
                "user": "TEXT",
                "createdAt": "TIMESTAMP",
                "updatedAt": "TIMESTAMP",
                "title": "TEXT",
                "systemPrompt": "TEXT",
                "model": "TEXT",
                "modelInfo": "JSON",
                "modalities": "JSON",
                "messages": "JSON",
                # in-flight assistant message while streaming, kept out of `messages`
                # so a failed stream can never damage the durable conversation
                "streamingMessage": "JSON",
                "args": "JSON",
                "tools": "JSON",
                "toolHistory": "JSON",
                "cost": "REAL",
                "inputTokens": "INTEGER",
                "outputTokens": "INTEGER",
                "stats": "JSON",
                "provider": "TEXT",
                "providerModel": "TEXT",
                "startedAt": "TIMESTAMP",
                "completedAt": "TIMESTAMP",
                "metadata": "JSON",
                "status": "TEXT",
                "error": "TEXT",
                "ref": "TEXT",
                "providerResponse": "JSON",
                "contextTokens": "INTEGER",
                "parentId": "INTEGER",
                "publishedAt": "TIMESTAMP",
                "publishedUrl": "TEXT",
            },
            "request": {
                "id": "INTEGER",
                "user": "TEXT",
                "threadId": "INTEGER",
                "createdAt": "TIMESTAMP",
                "updatedAt": "TIMESTAMP",
                "title": "TEXT",
                "model": "TEXT",
                "duration": "INTEGER",
                "cost": "REAL",
                "inputPrice": "REAL",
                "inputTokens": "INTEGER",
                "inputCachedTokens": "INTEGER",
                "outputPrice": "REAL",
                "outputTokens": "INTEGER",
                "totalTokens": "INTEGER",
                "usage": "JSON",
                "provider": "TEXT",
                "providerModel": "TEXT",
                "providerRef": "TEXT",
                "finishReason": "TEXT",
                "startedAt": "TIMESTAMP",
                "completedAt": "TIMESTAMP",
                "error": "TEXT",
                "stackTrace": "TEXT",
                "ref": "TEXT",
            },
            "agent_run": {
                "id": "INTEGER",
                "threadId": "INTEGER",
                "user": "TEXT",
                "status": "TEXT",
                "nextAction": "TEXT",
                "model": "TEXT",
                "stepCount": "INTEGER",
                "sliceCount": "INTEGER",
                "maxSteps": "INTEGER",
                "contextTokens": "INTEGER",
                "contextLimit": "INTEGER",
                "leaseOwner": "TEXT",
                "leaseExpiresAt": "TIMESTAMP",
                "nextAttemptAt": "TIMESTAMP",
                "error": "TEXT",
                "createdAt": "TIMESTAMP",
                "updatedAt": "TIMESTAMP",
                "completedAt": "TIMESTAMP",
            },
            "agent_step": {
                "id": "INTEGER",
                "runId": "INTEGER",
                "sequence": "INTEGER",
                "type": "TEXT",
                "status": "TEXT",
                "input": "JSON",
                "output": "JSON",
                "idempotencyKey": "TEXT",
                "attempt": "INTEGER",
                "error": "TEXT",
                "startedAt": "TIMESTAMP",
                "completedAt": "TIMESTAMP",
                "createdAt": "TIMESTAMP",
            },
            "chat_message": {
                "id": "INTEGER",
                "threadId": "INTEGER",
                "sequence": "INTEGER",
                "runId": "INTEGER",
                "stepId": "INTEGER",
                "role": "TEXT",
                "message": "JSON",
                "timestamp": "INTEGER",
                "toolCallId": "TEXT",
                "toolName": "TEXT",
                "tokenCount": "INTEGER",
                "active": "INTEGER",
                "createdAt": "TIMESTAMP",
            },
            "context_snapshot": {
                "id": "INTEGER",
                "threadId": "INTEGER",
                "runId": "INTEGER",
                "version": "INTEGER",
                "fromSequence": "INTEGER",
                "toSequence": "INTEGER",
                "summary": "JSON",
                "tokenCount": "INTEGER",
                "model": "TEXT",
                "createdAt": "TIMESTAMP",
            },
        }
        with self.create_writer_connection() as conn:
            self.init_db(conn)

    def get_connection(self):
        return self.create_reader_connection()

    def create_reader_connection(self):
        return self.db.create_reader_connection()

    def create_writer_connection(self):
        return self.db.create_writer_connection()

    # Check for missing columns and migrate if necessary
    def add_missing_columns(self, conn, table):
        cur = self.db.exec(conn, f"PRAGMA table_info({table})")
        columns = {row[1] for row in cur.fetchall()}

        for col, dtype in self.columns[table].items():
            if col not in columns:
                try:
                    self.db.exec(conn, f"ALTER TABLE {table} ADD COLUMN {col} {dtype}")
                except Exception as e:
                    self.ctx.err(f"adding {table} column {col}", e)

    def init_db(self, conn):
        # Create table with all columns
        # Note: default SQLite timestamp has different tz to datetime.now()
        overrides = {
            "id": "INTEGER PRIMARY KEY AUTOINCREMENT",
            "createdAt": "TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
            "updatedAt": "TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
        }
        sql_columns = ",".join([f"{col} {overrides.get(col, dtype)}" for col, dtype in self.columns["thread"].items()])
        self.db.exec(
            conn,
            f"""
            CREATE TABLE IF NOT EXISTS thread (
                {sql_columns}
            )
            """,
        )
        self.add_missing_columns(conn, "thread")
        self.db.exec(conn, "CREATE INDEX IF NOT EXISTS idx_thread_user ON thread(user)")
        self.db.exec(conn, "CREATE INDEX IF NOT EXISTS idx_thread_createdat ON thread(createdAt)")
        self.db.exec(conn, "CREATE INDEX IF NOT EXISTS idx_thread_updatedat ON thread(updatedAt)")
        self.db.exec(conn, "CREATE INDEX IF NOT EXISTS idx_thread_model ON thread(model)")
        self.db.exec(conn, "CREATE INDEX IF NOT EXISTS idx_thread_cost ON thread(cost)")

        sql_columns = ",".join([f"{col} {overrides.get(col, dtype)}" for col, dtype in self.columns["request"].items()])
        self.db.exec(
            conn,
            f"""
            CREATE TABLE IF NOT EXISTS request (
                {sql_columns}
            )
            """,
        )
        self.add_missing_columns(conn, "request")
        self.db.exec(conn, "CREATE INDEX IF NOT EXISTS idx_request_user ON request(user)")
        self.db.exec(conn, "CREATE INDEX IF NOT EXISTS idx_request_createdat ON request(createdAt)")
        self.db.exec(conn, "CREATE INDEX IF NOT EXISTS idx_request_cost ON request(cost)")
        self.db.exec(conn, "CREATE INDEX IF NOT EXISTS idx_request_threadid ON request(threadId)")

        for table in ("agent_run", "agent_step", "chat_message", "context_snapshot"):
            sql_columns = ",".join(
                f"{col} {overrides.get(col, dtype)}" for col, dtype in self.columns[table].items()
            )
            self.db.exec(conn, f"CREATE TABLE IF NOT EXISTS {table} ({sql_columns})")
            self.add_missing_columns(conn, table)
        self.db.exec(conn, "CREATE INDEX IF NOT EXISTS idx_agent_run_thread ON agent_run(threadId, id)")
        self.db.exec(conn, "CREATE INDEX IF NOT EXISTS idx_agent_run_queue ON agent_run(status, nextAttemptAt)")
        self.db.exec(conn, "CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_step_seq ON agent_step(runId, sequence)")
        self.db.exec(conn, "CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_step_key ON agent_step(idempotencyKey)")
        self.db.exec(conn, "CREATE UNIQUE INDEX IF NOT EXISTS idx_chat_message_seq ON chat_message(threadId, sequence)")
        self.db.exec(conn, "CREATE INDEX IF NOT EXISTS idx_chat_message_active_seq ON chat_message(threadId, active, sequence)")
        self.db.exec(conn, "CREATE INDEX IF NOT EXISTS idx_chat_message_run ON chat_message(runId, sequence)")
        self.db.exec(conn, "CREATE INDEX IF NOT EXISTS idx_context_snapshot_thread ON context_snapshot(threadId, version)")
        self.db.exec(conn, "UPDATE chat_message SET active=1 WHERE active IS NULL")

    def import_db(self, threads, requests):
        self.ctx.log("import threads and requests")
        with self.create_writer_connection() as conn:
            conn.execute("DROP TABLE IF EXISTS thread")
            conn.execute("DROP TABLE IF EXISTS request")
            self.init_db(conn)
            thread_id_map = {}
            for thread in threads:
                thread_id = self.import_thread(conn, thread)
                thread_id_map[thread["id"]] = thread_id
            self.ctx.log(f"imported {len(threads)} threads")
            for request in requests:
                self.import_request(conn, request, thread_id_map)
            self.ctx.log(f"imported {len(requests)} requests")

    def import_date(self, date):
        # "1765794035" or "2025-12-31T05:41:46.686Z" or "2026-01-02 05:00:16"
        str = date or datetime.now().isoformat()
        if isinstance(str, int):
            return datetime.fromtimestamp(str)
        if isinstance(str, float):
            return datetime.fromtimestamp(str)
        return (
            datetime.strptime(str, "%Y-%m-%dT%H:%M:%S.%fZ")
            if "T" in str
            else datetime.strptime(str, "%Y-%m-%d %H:%M:%S")
        )

    def import_thread(self, conn, orig):
        thread = orig.copy()
        thread["refId"] = thread["id"]
        del thread["id"]

        info = thread.get("modelInfo", thread.get("info", {}))
        created_at = self.import_date(thread.get("createdAt"))
        thread["createdAt"] = created_at
        if "updateAt" not in thread:
            thread["updateAt"] = created_at
        thread["modelInfo"] = info
        if "modalities" not in thread:
            if "modalities" in info:
                modalities = info["modalities"]
                if isinstance(modalities, dict):
                    input = modalities.get("input", ["text"])
                    output = modalities.get("output", ["text"])
                    thread["modalities"] = list(set(input + output))
                else:
                    thread["modalities"] = modalities
            else:
                thread["modalities"] = ["text"]
        if "provider" not in thread and "provider" in info:
            thread["provider"] = info["provider"]
        if "providerModel" not in thread and "id" in info:
            thread["providerModel"] = info["id"]

        stats = thread.get("stats", {})
        if "inputTokens" not in thread and "inputTokens" in stats:
            thread["inputTokens"] = stats["inputTokens"]
        if "outputTokens" not in thread and "outputTokens" in stats:
            thread["outputTokens"] = stats["outputTokens"]
        if "cost" not in thread and "cost" in stats:
            thread["cost"] = stats["cost"]
        if "completedAt" not in thread:
            thread["completedAt"] = created_at + timedelta(milliseconds=stats.get("duration", 0))

        sql_columns = []
        sql_params = []
        columns = self.columns["thread"]
        for col in columns:
            if col == "id":
                continue
            sql_columns.append(col)
            val = thread.get(col, None)
            if columns[col] == "JSON" and val is not None:
                val = json.dumps(val)
            sql_params.append(val)

        return conn.execute(
            f"INSERT INTO thread ({', '.join(sql_columns)}) VALUES ({', '.join(['?'] * len(sql_params))})",
            sql_params,
        ).lastrowid

    # run on startup
    def import_request(self, conn, orig, id_map):
        request = orig.copy()
        del request["id"]
        thread_id = request.get("threadId")
        if thread_id:
            request["threadId"] = id_map.get(thread_id, None)

        created_at = self.import_date(request.get("created"))
        request["createdAt"] = created_at
        if "updateAt" not in request:
            request["updateAt"] = created_at
        if "completedAt" not in request:
            request["completedAt"] = created_at + timedelta(milliseconds=request.get("duration", 0))

        sql_columns = []
        sql_params = []
        columns = self.columns["request"]
        for col in columns:
            if col == "id":
                continue
            sql_columns.append(col)
            val = request.get(col, None)
            if columns[col] == "JSON" and val is not None:
                val = json.dumps(val)
            sql_params.append(val)

        return conn.execute(
            f"INSERT INTO request ({', '.join(sql_columns)}) VALUES ({', '.join(['?'] * len(sql_params))})",
            sql_params,
        ).lastrowid

    def to_dto(self, row, json_columns):
        return to_dto(self.ctx, row, json_columns)

    def get_user_filter(self, user=None, params=None):
        args = params.copy() if params else {}
        if user is None or user == "Anonymous" or user == "null" or user == "NULL" or user == "":
            return "WHERE (user IS NULL OR user = '' OR user = 'Anonymous')", args
        elif user == "all" or user == "*":
            return "", args
        else:
            args["user"] = user
            return "WHERE user = :user", args

    def get_thread(self, id, user=None):
        sql_where, params = self.get_user_filter(user, {"id": id})
        return self.db.one(f"SELECT * FROM thread {sql_where} AND id = :id", params)

    def get_thread_column(self, id, column, user=None):
        if column not in self.columns["thread"]:
            self.ctx.err(f"get_thread_column invalid column ({id}, {column}, {user})", None)
            return None

        try:
            sql_where, params = self.get_user_filter(user, {"id": id})
            return self.db.scalar(f"SELECT {column} FROM thread {sql_where} AND id = :id", params)
        except Exception as e:
            self.ctx.err(f"get_thread_column ({id}, {column}, {user})", e)
            return None

    def query_threads(self, query: Dict[str, Any], user=None):
        try:
            columns = self.columns["thread"]
            all_columns = columns.keys()

            take = min(int(query.get("take", "50")), 1000)
            skip = int(query.get("skip", "0"))
            sort = query.get("sort", "-id")

            # always filter by user
            sql_where, params = self.get_user_filter(user, {"take": take, "skip": skip})

            where_conds = []
            if sql_where.startswith("WHERE "):
                where_conds.append(sql_where[6:])

            filter = {}
            for k in query:
                if k in all_columns and k != "user":
                    filter[k] = query[k]
                    params[k] = query[k]

            if len(filter) > 0:
                where_conds.extend([f"{k} = :{k}" for k in filter])

            if "null" in query:
                cols = valid_columns(all_columns, query["null"])
                if len(cols) > 0:
                    where_conds.extend([f"{k} IS NULL" for k in cols])

            if "not_null" in query:
                cols = valid_columns(all_columns, query.get("not_null"))
                if len(cols) > 0:
                    where_conds.extend([f"{k} IS NOT NULL" for k in cols])

            if "q" in query:
                where_conds.append("(title LIKE :q OR messages LIKE :q)")
                params["q"] = f"%{query['q']}%"

            full_where = ("WHERE " + " AND ".join(where_conds)) if where_conds else ""

            sql = f"{select_columns(all_columns, query.get('fields'), select=query.get('select'))} FROM thread {full_where} {order_by(all_columns, sort)} LIMIT :take OFFSET :skip"

            if query.get("as") == "column":
                return self.db.column(sql, params)
            else:
                return self.db.all(sql, params)

        except Exception as e:
            self.ctx.err(f"query_threads ({take}, {skip})", e)
            return []

    def stored_message_count(self, id):
        """Message count without shipping the (potentially MBs of) messages to Python."""
        try:
            return self.db.scalar(
                "SELECT json_array_length(messages) FROM thread WHERE id = :id AND json_valid(messages)", {"id": id}
            )
        except Exception as e:
            self.ctx.err(f"stored_message_count({id})", e)
            return None

    def guard_messages(self, id, thread):
        """
        `messages` is the durable conversation: it must never shrink as a side effect of
        an in-flight request. That is how an entire thread gets erased - one request
        carrying a single turn replaces hundreds of messages.

        Callers that legitimately rewrite history (editing a message, redo, deleting a
        message, compacting) opt in by passing `truncate=True`.

        A shrinking write that hasn't opted in is repaired rather than rejected: the
        stored history is kept and any genuinely new messages in the update are appended,
        so the worst case is a duplicated message rather than a lost conversation.
        """
        truncate = thread.pop("truncate", False)
        messages = thread.get("messages")
        if truncate or not id or not isinstance(messages, list):
            return thread

        stored_count = self.stored_message_count(id)
        if not stored_count or len(messages) >= stored_count:
            return thread

        stored = None
        row = self.db.one("SELECT messages FROM thread WHERE id = :id", {"id": id})
        if row and isinstance(row.get("messages"), str):
            try:
                stored = json.loads(row["messages"])
            except Exception as e:
                self.ctx.err(f"guard_messages({id}) parsing stored messages", e)
        if not isinstance(stored, list) or len(stored) <= len(messages):
            return thread

        known = {m.get("timestamp") for m in stored if isinstance(m, dict)}
        appended = [m for m in messages if isinstance(m, dict) and m.get("timestamp") not in known]
        thread["messages"] = stored + appended
        self.ctx.err(
            f"Refused to shrink thread {id} from {len(stored)} to {len(messages)} messages, "
            f"kept history and appended {len(appended)}",
            None,
        )
        return thread

    def prepare_thread(self, thread, id=None, user=None):
        now = datetime.now()
        # An in-flight message lives in `streamingMessage` and is only merged into
        # `messages` on the way out to clients. Never let one back in: it belongs to a
        # stream still running and is committed by chat_response when it finishes.
        # Filtered before guard_messages so the guard compares the final list.
        if isinstance(thread.get("messages"), list):
            thread["messages"] = [m for m in thread["messages"] if not (isinstance(m, dict) and m.get("streaming"))]
        if id:
            thread["id"] = id
            self.guard_messages(id, thread)
        else:
            thread.pop("truncate", None)
            thread["createdAt"] = now
        thread["updatedAt"] = now
        initial_timestamp = int(time.time() * 1000) + 1
        if "messages" in thread:
            context = {}
            if user:
                context["user"] = user
            for idx, m in enumerate(thread["messages"]):
                self.ctx.cache_message_inline_data(m, context=context)
                if "timestamp" not in m:
                    m["timestamp"] = initial_timestamp + idx
                # remove reasoning_details from all messages (can get huge)
                if "reasoning_details" in m:
                    del m["reasoning_details"]
            thread["contextTokens"] = count_tokens_approx(thread["messages"])
        return with_user(thread, user=user)

    def create_thread(self, thread: Dict[str, Any], user=None):
        prepared = self.prepare_thread(thread, user=user)
        keys = [k for k in self.columns["thread"] if k != "id" and k in prepared]
        params = {k: self.db.value(prepared[k]) for k in keys}
        with self.create_writer_connection() as conn:
            cur = self.db.exec(
                conn, f"INSERT INTO thread ({','.join(keys)}) VALUES ({','.join(':'+k for k in keys)})", params
            )
            conn.commit()
            thread_id = cur.lastrowid
        self.sync_chat_messages(thread_id, prepared.get("messages", []))
        return thread_id

    async def create_thread_async(self, thread: Dict[str, Any], user=None):
        prepared = self.prepare_thread(thread, user=user)
        thread_id = await self.db.insert_async("thread", self.columns["thread"], prepared)
        self.sync_chat_messages(thread_id, prepared.get("messages", []))
        return thread_id

    def update_thread(self, id, thread: Dict[str, Any], user=None):
        truncate = bool(thread.get("truncate"))
        prepared = self.prepare_thread(thread, id, user=user)
        ret = self.db.update("thread", self.columns["thread"], prepared)
        if "messages" in prepared:
            (self.rewrite_chat_messages if truncate else self.sync_chat_messages)(id, prepared["messages"])
        try:
            from . import notify_thread_update
            notify_thread_update(id)
        except Exception:
            pass
        return ret

    async def update_thread_async(self, id, thread: Dict[str, Any], user=None):
        truncate = bool(thread.get("truncate"))
        prepared = self.prepare_thread(thread, id, user=user)
        ret = await self.db.update_async("thread", self.columns["thread"], prepared)
        if "messages" in prepared:
            (self.rewrite_chat_messages if truncate else self.sync_chat_messages)(id, prepared["messages"])
        try:
            from . import notify_thread_update
            notify_thread_update(id)
        except Exception:
            pass
        return ret

    def sync_chat_messages(self, thread_id, messages, run_id=None, step_id=None):
        """Append new canonical messages without rewriting existing normalized rows.

        The legacy thread.messages JSON remains during the compatibility period. Sequence
        rows are the durable source used by runs and can be backfilled idempotently.
        """
        if not isinstance(messages, list):
            return
        with self.create_writer_connection() as conn:
            existing = self.db.exec(
                conn, "SELECT sequence, timestamp FROM chat_message WHERE threadId = :threadId AND active=1 ORDER BY sequence",
                {"threadId": thread_id},
            ).fetchall()
            known_timestamps = {row[1] for row in existing if row[1] is not None}
            max_sequence = self.db.exec(
                conn, "SELECT max(sequence) FROM chat_message WHERE threadId=:threadId", {"threadId": thread_id}
            ).fetchone()[0]
            sequence = (max_sequence or 0) + 1
            for message in messages:
                if not isinstance(message, dict) or message.get("streaming"):
                    continue
                timestamp = message.get("timestamp")
                if timestamp is not None and timestamp in known_timestamps:
                    continue
                tool_call_id = message.get("tool_call_id")
                tool_name = None
                calls = message.get("tool_calls") or []
                if calls and isinstance(calls[0], dict):
                    tool_name = (calls[0].get("function") or {}).get("name")
                self.db.exec(
                    conn,
                    """INSERT INTO chat_message
                       (threadId,sequence,runId,stepId,role,message,timestamp,toolCallId,toolName,tokenCount,active,createdAt)
                       VALUES (:threadId,:sequence,:runId,:stepId,:role,:message,:timestamp,:toolCallId,:toolName,:tokenCount,1,:createdAt)""",
                    {
                        "threadId": thread_id, "sequence": sequence, "runId": run_id, "stepId": step_id,
                        "role": message.get("role"), "message": json.dumps(message), "timestamp": timestamp,
                        "toolCallId": tool_call_id, "toolName": tool_name,
                        "tokenCount": count_tokens_approx([message]), "createdAt": datetime.now(),
                    },
                )
                if timestamp is not None:
                    known_timestamps.add(timestamp)
                sequence += 1
            conn.commit()

    def get_chat_messages(self, thread_id, after=0, take=None):
        limit = " LIMIT :take" if take else ""
        params = {"threadId": thread_id, "after": after}
        if take:
            params["take"] = take
        rows = self.db.all(
            f"SELECT * FROM chat_message WHERE threadId=:threadId AND active=1 AND sequence>:after ORDER BY sequence{limit}", params
        )
        for row in rows:
            if isinstance(row.get("message"), str):
                row["message"] = json.loads(row["message"])
        return rows

    def get_chat_message_page(self, thread_id, before=None, after=None, take=100):
        take = max(1, min(int(take), 200))
        params = {"threadId": thread_id, "take": take}
        if before is not None:
            params["before"] = int(before)
            sql = """SELECT * FROM chat_message
                     WHERE threadId=:threadId AND active=1 AND sequence<:before
                     ORDER BY sequence DESC LIMIT :take"""
            rows = list(reversed(self.db.all(sql, params)))
        else:
            params["after"] = int(after or 0)
            sql = """SELECT * FROM chat_message
                     WHERE threadId=:threadId AND active=1 AND sequence>:after
                     ORDER BY sequence LIMIT :take"""
            rows = self.db.all(sql, params)
        return self._expand_tool_message_boundaries(
            thread_id, [self._chat_message_dto(row) for row in rows]
        )

    def get_chat_message_window(self, thread_id, head=20, tail=100):
        head = max(0, min(int(head), 100))
        tail = max(0, min(int(tail), 200))
        head_rows = self.get_chat_message_page(thread_id, after=0, take=head) if head else []
        tail_rows = self.db.all(
            """SELECT * FROM chat_message WHERE threadId=:threadId AND active=1
               ORDER BY sequence DESC LIMIT :take""",
            {"threadId": thread_id, "take": tail},
        ) if tail else []
        tail_rows = self._expand_tool_message_boundaries(
            thread_id, [self._chat_message_dto(row) for row in reversed(tail_rows)]
        )
        by_sequence = {row["_sequence"]: row for row in head_rows}
        by_sequence.update({row["_sequence"]: row for row in tail_rows})
        return [by_sequence[key] for key in sorted(by_sequence)]

    def get_chat_message_bounds(self, thread_id):
        row = self.db.one(
            """SELECT count(*) AS messageCount, min(sequence) AS firstSequence,
                      max(sequence) AS lastSequence
               FROM chat_message WHERE threadId=:threadId AND active=1""",
            {"threadId": thread_id},
        )
        return row or {"messageCount": 0, "firstSequence": None, "lastSequence": None}

    def _chat_message_dto(self, row):
        row = dict(row)
        if isinstance(row.get("message"), str):
            row["message"] = json.loads(row["message"])
        message = row.get("message") or {}
        return {**message, "_sequence": row.get("sequence")}

    def _expand_tool_message_boundaries(self, thread_id, messages):
        if not messages:
            return messages
        expanded = list(messages)
        # A page beginning with tool results must also contain their originating
        # assistant tool call, otherwise the UI/provider sees an orphaned result.
        while expanded and expanded[0].get("role") == "tool":
            row = self.db.one(
                """SELECT * FROM chat_message WHERE threadId=:threadId AND active=1
                   AND sequence<:sequence ORDER BY sequence DESC LIMIT 1""",
                {"threadId": thread_id, "sequence": expanded[0]["_sequence"]},
            )
            if not row:
                break
            message = self._chat_message_dto(row)
            expanded.insert(0, message)
            if message.get("role") != "tool":
                break
        # Likewise include all contiguous results following a tool call at the end.
        if expanded and expanded[-1].get("tool_calls"):
            rows = self.db.all(
                """SELECT * FROM chat_message WHERE threadId=:threadId AND active=1
                   AND sequence>:sequence ORDER BY sequence LIMIT 100""",
                {"threadId": thread_id, "sequence": expanded[-1]["_sequence"]},
            )
            for row in rows:
                message = self._chat_message_dto(row)
                if message.get("role") != "tool":
                    break
                expanded.append(message)
        return expanded

    def rewrite_chat_messages(self, thread_id, messages):
        """Start a new active history branch while preserving prior rows for audit."""
        with self.create_writer_connection() as conn:
            self.db.exec(conn, "UPDATE chat_message SET active=0 WHERE threadId=:threadId AND active=1", {"threadId": thread_id})
            self.db.exec(conn, "DELETE FROM context_snapshot WHERE threadId=:threadId", {"threadId": thread_id})
            conn.commit()
        self.sync_chat_messages(thread_id, messages)

    def annotate_chat_messages(self, thread_id, messages, run_id=None, step_id=None):
        timestamps = [m.get("timestamp") for m in messages if isinstance(m, dict) and m.get("timestamp") is not None]
        if not timestamps or (run_id is None and step_id is None):
            return
        with self.create_writer_connection() as conn:
            for timestamp in timestamps:
                self.db.exec(conn, """UPDATE chat_message SET
                    runId=COALESCE(runId,:runId), stepId=COALESCE(stepId,:stepId)
                    WHERE threadId=:threadId AND timestamp=:timestamp AND active=1""",
                    {"runId": run_id, "stepId": step_id, "threadId": thread_id, "timestamp": timestamp})
            conn.commit()

    def backfill_chat_messages(self, thread_id):
        row = self.db.one("SELECT messages FROM thread WHERE id=:id", {"id": thread_id})
        if row and isinstance(row.get("messages"), str):
            self.sync_chat_messages(thread_id, json.loads(row["messages"]))

    def create_agent_run(self, thread_id, user, model, max_steps=250):
        now = datetime.now()
        with self.create_writer_connection() as conn:
            cur = self.db.exec(conn, """INSERT INTO agent_run
                (threadId,user,status,nextAction,model,stepCount,sliceCount,maxSteps,nextAttemptAt,createdAt,updatedAt)
                VALUES (:threadId,:user,'queued','model',:model,0,0,:maxSteps,:now,:now,:now)""",
                {"threadId": thread_id, "user": user, "model": model, "maxSteps": max_steps, "now": now})
            conn.commit()
            return cur.lastrowid

    def get_agent_run(self, run_id, user=None):
        sql_where, params = self.get_user_filter(user, {"id": run_id})
        joiner = " AND " if sql_where else " WHERE "
        return self.db.one(f"SELECT * FROM agent_run {sql_where}{joiner}id=:id", params)

    def get_active_agent_run(self, thread_id, user=None):
        sql_where, params = self.get_user_filter(user, {"threadId": thread_id})
        joiner = " AND " if sql_where else " WHERE "
        return self.db.one(
            f"SELECT * FROM agent_run {sql_where}{joiner}threadId=:threadId "
            "AND status IN ('queued','running','waiting_approval') ORDER BY id DESC LIMIT 1", params
        )

    def requeue_interrupted_agent_runs(self):
        """Recover work left running when the previous in-process scheduler stopped."""
        now = datetime.now()
        with self.create_writer_connection() as conn:
            cur = self.db.exec(
                conn,
                """UPDATE agent_run
                   SET status='queued', leaseOwner=NULL, leaseExpiresAt=NULL, updatedAt=:now
                   WHERE status='running'""",
                {"now": now},
            )
            conn.commit()
            return cur.rowcount

    def claim_agent_runs(self, owner, limit=1, lease_seconds=300):
        """Atomically claim eligible queued runs for one bounded in-process worker."""
        if limit <= 0:
            return []
        now = datetime.now()
        lease_expires = now + timedelta(seconds=max(30, lease_seconds))
        claimed = []
        with self.create_writer_connection() as conn:
            rows = self.db.exec(
                conn,
                """SELECT id FROM agent_run
                   WHERE status='queued' AND (nextAttemptAt IS NULL OR nextAttemptAt<=:now)
                   ORDER BY createdAt,id LIMIT :limit""",
                {"now": now, "limit": limit},
            ).fetchall()
            for row in rows:
                run_id = row["id"] if hasattr(row, "keys") else row[0]
                cur = self.db.exec(
                    conn,
                    """UPDATE agent_run
                       SET status='running', leaseOwner=:owner, leaseExpiresAt=:leaseExpiresAt, updatedAt=:now
                       WHERE id=:id AND status='queued'""",
                    {"id": run_id, "owner": owner, "leaseExpiresAt": lease_expires, "now": now},
                )
                if cur.rowcount:
                    claimed.append(run_id)
            conn.commit()
        return [self.get_agent_run(run_id, user="all") for run_id in claimed]

    def renew_agent_run_lease(self, run_id, owner, lease_seconds=300):
        now = datetime.now()
        with self.create_writer_connection() as conn:
            cur = self.db.exec(
                conn,
                """UPDATE agent_run
                   SET leaseExpiresAt=:leaseExpiresAt, updatedAt=:now
                   WHERE id=:id AND status='running' AND leaseOwner=:owner""",
                {
                    "id": run_id,
                    "owner": owner,
                    "leaseExpiresAt": now + timedelta(seconds=max(30, lease_seconds)),
                    "now": now,
                },
            )
            conn.commit()
            return cur.rowcount

    def update_agent_run(self, run_id, values):
        values = {**values, "id": run_id, "updatedAt": datetime.now()}
        return self._update_durable_row("agent_run", run_id, values)

    def create_agent_step(self, run_id, sequence, step_type, status="running", **values):
        now = datetime.now()
        data = {
            "runId": run_id, "sequence": sequence, "type": step_type, "status": status,
            "attempt": values.pop("attempt", 1), "createdAt": now, "startedAt": now, **values,
        }
        keys = [k for k in self.columns["agent_step"] if k != "id" and k in data]
        params = {k: self.db.value(data[k]) for k in keys}
        with self.create_writer_connection() as conn:
            cur = self.db.exec(
                conn,
                f"INSERT INTO agent_step ({','.join(keys)}) VALUES ({','.join(':'+k for k in keys)})",
                params,
            )
            conn.commit()
            return cur.lastrowid

    def update_agent_step(self, step_id, values):
        return self._update_durable_row("agent_step", step_id, values)

    def _update_durable_row(self, table, row_id, values):
        columns = self.columns[table]
        keys = [k for k in values if k in columns and k != "id"]
        if not keys:
            return 0
        params = {k: self.db.value(values[k]) for k in keys}
        params["id"] = row_id
        with self.create_writer_connection() as conn:
            cur = self.db.exec(
                conn, f"UPDATE {table} SET {','.join(k+'=:'+k for k in keys)} WHERE id=:id", params
            )
            conn.commit()
            return cur.rowcount

    def get_agent_steps(self, run_id, after=0):
        rows = self.db.all(
            "SELECT * FROM agent_step WHERE runId=:runId AND sequence>:after ORDER BY sequence",
            {"runId": run_id, "after": after},
        )
        return [self.to_dto(row, ["input", "output"]) for row in rows]

    def create_context_snapshot(self, thread_id, run_id, from_sequence, to_sequence, summary, model=None):
        previous = self.db.scalar(
            "SELECT max(version) FROM context_snapshot WHERE threadId=:threadId", {"threadId": thread_id}
        ) or 0
        now = datetime.now()
        with self.create_writer_connection() as conn:
            cur = self.db.exec(conn, """INSERT INTO context_snapshot
                (threadId,runId,version,fromSequence,toSequence,summary,tokenCount,model,createdAt)
                VALUES (:threadId,:runId,:version,:fromSequence,:toSequence,:summary,:tokenCount,:model,:createdAt)""",
                {"threadId": thread_id, "runId": run_id, "version": previous + 1,
                 "fromSequence": from_sequence, "toSequence": to_sequence,
                 "summary": json.dumps(summary), "tokenCount": count_tokens_approx(summary),
                 "model": model, "createdAt": now})
            conn.commit()
            return cur.lastrowid

    def get_latest_context_snapshot(self, thread_id):
        row = self.db.one(
            "SELECT * FROM context_snapshot WHERE threadId=:threadId ORDER BY version DESC LIMIT 1",
            {"threadId": thread_id},
        )
        return self.to_dto(row, ["summary"]) if row else None

    def delete_thread(self, id, user=None, callback=None):
        sql_where, params = self.get_user_filter(user, {"id": id})
        joiner = " AND " if sql_where else " WHERE "
        with self.create_writer_connection() as conn:
            allowed = self.db.exec(conn, f"SELECT id FROM thread {sql_where}{joiner}id=:id", params).fetchone()
            if not allowed:
                return 0
            self.db.exec(conn, "DELETE FROM agent_step WHERE runId IN (SELECT id FROM agent_run WHERE threadId=:id)", {"id": id})
            self.db.exec(conn, "DELETE FROM agent_run WHERE threadId=:id", {"id": id})
            self.db.exec(conn, "DELETE FROM context_snapshot WHERE threadId=:id", {"id": id})
            self.db.exec(conn, "DELETE FROM chat_message WHERE threadId=:id", {"id": id})
            cur = self.db.exec(conn, "DELETE FROM thread WHERE id=:id", {"id": id})
            conn.commit()
            if callback:
                callback(None, cur.rowcount)
            return cur.rowcount

    def query_requests(self, query: Dict[str, Any], user=None):
        try:
            columns = self.columns["request"]
            all_columns = columns.keys()

            take = min(int(query.get("take", "50")), 10000)
            skip = int(query.get("skip", 0))
            sort = query.get("sort", "-id")

            # always filter by user
            sql_where, params = self.get_user_filter(user, {"take": take, "skip": skip})

            where_conds = []
            if sql_where.startswith("WHERE "):
                where_conds.append(sql_where[6:])

            filter = {}
            for k in query:
                if k in all_columns and k != "user":
                    filter[k] = query[k]
                    params[k] = query[k]

            if len(filter) > 0:
                where_conds.extend([f"{k} = :{k}" for k in filter])

            if "null" in query:
                cols = valid_columns(all_columns, query["null"])
                if len(cols) > 0:
                    where_conds.extend([f"{k} IS NULL" for k in cols])

            if "not_null" in query:
                cols = valid_columns(all_columns, query.get("not_null"))
                if len(cols) > 0:
                    where_conds.extend([f"{k} IS NOT NULL" for k in cols])

            if "q" in query:
                where_conds.append("(title LIKE :q)")
                params["q"] = f"%{query['q']}%"

            if "month" in query:
                where_conds.append("strftime('%Y-%m', createdAt) = :month")
                params["month"] = query["month"]

            full_where = ("WHERE " + " AND ".join(where_conds)) if where_conds else ""

            sql = f"{select_columns(all_columns, query.get('fields'), select=query.get('select'))} FROM request {full_where} {order_by(all_columns, sort)}LIMIT :take OFFSET :skip"

            if query.get("as") == "column":
                return self.db.column(sql, params)
            else:
                return self.db.all(sql, params)
        except Exception as e:
            self.ctx.err(f"query_requests ({take}, {skip})", e)
            return []

    def get_request_summary(self, user=None):
        try:
            sql_where, params = self.get_user_filter(user)
            # Use strftime to format date as YYYY-MM-DD
            sql = f"""
                SELECT
                    strftime('%Y-%m-%d', createdAt) as date,
                    count(id) as requests,
                    sum(cost) as cost,
                    sum(inputTokens) as inputTokens,
                    sum(outputTokens) as outputTokens
                FROM request
                {sql_where}
                GROUP BY date
                ORDER BY date
            """
            return self.db.all(sql, params)
        except Exception as e:
            self.ctx.err(f"get_request_summary ({user})", e)
            return []

    def get_daily_request_summary(self, day, user=None):
        try:
            sql_where, params = self.get_user_filter(user)
            # Add date filter
            if sql_where:
                sql_where += " AND strftime('%Y-%m-%d', createdAt) = :day"
            else:
                sql_where = "WHERE strftime('%Y-%m-%d', createdAt) = :day"
            params["day"] = day

            # Model aggregation
            sql_model = f"""
                SELECT
                    model,
                    count(id) as count,
                    sum(cost) as cost,
                    sum(duration) as duration,
                    sum(inputTokens + outputTokens) as tokens,
                    sum(inputTokens) as inputTokens,
                    sum(outputTokens) as outputTokens
                FROM request
                {sql_where}
                GROUP BY model
            """
            model_data = {}
            for row in self.db.all(sql_model, params):
                model_data[row["model"]] = {
                    "cost": row["cost"] or 0,
                    "count": row["count"],
                    "duration": row["duration"] or 0,
                    "tokens": row["tokens"] or 0,
                    "inputTokens": row["inputTokens"] or 0,
                    "outputTokens": row["outputTokens"] or 0,
                }

            # Provider aggregation
            sql_provider = f"""
                SELECT
                    provider,
                    count(id) as count,
                    sum(cost) as cost,
                    sum(duration) as duration,
                    sum(inputTokens + outputTokens) as tokens,
                    sum(inputTokens) as inputTokens,
                    sum(outputTokens) as outputTokens
                FROM request
                {sql_where}
                AND provider IS NOT NULL
                GROUP BY provider
            """
            provider_data = {}
            for row in self.db.all(sql_provider, params):
                provider_data[row["provider"]] = {
                    "cost": row["cost"] or 0,
                    "count": row["count"],
                    "duration": row["duration"] or 0,
                    "tokens": row["tokens"] or 0,
                    "inputTokens": row["inputTokens"] or 0,
                    "outputTokens": row["outputTokens"] or 0,
                }

            return {"modelData": model_data, "providerData": provider_data}
        except Exception as e:
            self.ctx.err(f"get_daily_request_summary ({day}, {user})", e)
            return {"modelData": {}, "providerData": {}}

    def get_users_summary(self):
        try:
            sql = """
                SELECT
                    COALESCE(NULLIF(user, ''), 'Anonymous') as user,
                    count(id) as requests,
                    sum(cost) as cost,
                    sum(inputTokens) as inputTokens,
                    sum(outputTokens) as outputTokens,
                    max(createdAt) as lastActive
                FROM request
                GROUP BY COALESCE(NULLIF(user, ''), 'Anonymous')
                ORDER BY requests DESC
            """
            rows = self.db.all(sql)
            return [
                {
                    "user": r["user"] or "Anonymous",
                    "requests": r["requests"] or 0,
                    "cost": r["cost"] or 0,
                    "inputTokens": r["inputTokens"] or 0,
                    "outputTokens": r["outputTokens"] or 0,
                    "lastActive": r["lastActive"],
                }
                for r in rows
            ]
        except Exception as e:
            self.ctx.err("get_users_summary", e)
            return []

    def get_users_list(self):
        try:
            sql = "SELECT DISTINCT COALESCE(NULLIF(user, ''), 'Anonymous') as user FROM request ORDER BY user"
            rows = self.db.all(sql)
            return [r["user"] for r in rows if r.get("user")]
        except Exception as e:
            self.ctx.err("get_users_list", e)
            return []

    def create_request(self, request: Dict[str, Any], user=None):
        request["createdAt"] = request["updatedAt"] = datetime.now()
        return self.db.insert("request", self.columns["request"], with_user(request, user=user))

    async def create_request_async(self, request: Dict[str, Any], user=None):
        request["createdAt"] = request["updatedAt"] = datetime.now()
        return await self.db.insert_async("request", self.columns["request"], with_user(request, user=user))

    def update_request(self, id, request: Dict[str, Any], user=None):
        request["id"] = id
        request["updatedAt"] = datetime.now()
        return self.db.update("request", self.columns["request"], with_user(request, user=user))

    async def update_request_async(self, id, request: Dict[str, Any], user=None):
        request["id"] = id
        request["updatedAt"] = datetime.now()
        return await self.db.update_async("request", self.columns["request"], with_user(request, user=user))

    def delete_request(self, id, user=None, callback=None):
        sql_where, params = self.get_user_filter(user, {"id": id})
        self.db.write(f"DELETE FROM request {sql_where} AND id = :id", params, callback)

    def close(self):
        if self._closed:
            return
        self._closed = True
        self.db.close()

        # Durable agent runs were requeued by AgentScheduler.stop(). Keep their
        # threads resumable; only legacy/non-durable unfinished work is terminal.
        with self.db.create_writer_connection() as conn:
            conn.execute(
                """UPDATE thread
                   SET completedAt=NULL, error=NULL, streamingMessage=NULL,
                       status=CASE
                           WHEN EXISTS (
                               SELECT 1 FROM agent_run r
                               WHERE r.threadId=thread.id AND r.status='waiting_approval'
                           ) THEN 'Waiting for approval'
                           WHEN COALESCE((
                               SELECT r.stepCount FROM agent_run r
                               WHERE r.threadId=thread.id
                                 AND r.status IN ('queued','running','waiting_approval')
                               ORDER BY r.id DESC LIMIT 1
                           ),0) > 0 THEN 'Continuing…'
                           ELSE 'Queued'
                       END
                   WHERE completedAt IS NULL AND EXISTS (
                       SELECT 1 FROM agent_run r
                       WHERE r.threadId=thread.id
                         AND r.status IN ('queued','running','waiting_approval')
                   )"""
            )
            conn.execute(
                """UPDATE thread SET completedAt=:completedAt, error=:error, status=NULL
                   WHERE completedAt IS NULL AND NOT EXISTS (
                       SELECT 1 FROM agent_run r
                       WHERE r.threadId=thread.id
                         AND r.status IN ('queued','running','waiting_approval')
                   )""",
                {"completedAt": datetime.now().isoformat(" "), "error": "Server Shutdown"},
            )
            conn.execute(
                "UPDATE request SET completedAt=:completedAt, error=:error WHERE completedAt IS NULL",
                {"completedAt": datetime.now().isoformat(" "), "error": "Server Shutdown"},
            )
