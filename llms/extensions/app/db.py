import json
import os
import time
from datetime import datetime, timedelta
from typing import Any, Dict

from llms.db import DbManager, order_by, select_columns, to_dto, valid_columns


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
                "args": "JSON",
                "tools": "JSON",
                "toolHistory": "JSON",
                "cost": "REAL",
                "inputTokens": "INTEGER",
                "outputTokens": "INTEGER",
                "stats": "JSON",
                "provider": "TEXT",
                "providerModel": "TEXT",
                "publishedAt": "TIMESTAMP",
                "startedAt": "TIMESTAMP",
                "completedAt": "TIMESTAMP",
                "metadata": "JSON",
                "error": "TEXT",
                "ref": "TEXT",
                "providerResponse": "JSON",
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
        if user is None:
            return "WHERE user IS NULL", params or {}
        else:
            args = params.copy() if params else {}
            args.update({"user": user})
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

            filter = {}
            for k in query:
                if k in all_columns:
                    filter[k] = query[k]
                    params[k] = query[k]

            if len(filter) > 0:
                sql_where += " AND " + " AND ".join([f"{k} = :{k}" for k in filter])

            if "null" in query:
                cols = valid_columns(all_columns, query["null"])
                if len(cols) > 0:
                    sql_where += " AND " + " AND ".join([f"{k} IS NULL" for k in cols])

            if "not_null" in query:
                cols = valid_columns(all_columns, query.get("not_null"))
                if len(cols) > 0:
                    sql_where += " AND " + " AND ".join([f"{k} IS NOT NULL" for k in cols])

            if "q" in query:
                sql_where += " AND " if sql_where else "WHERE "
                sql_where += "(title LIKE :q OR messages LIKE :q)"
                params["q"] = f"%{query['q']}%"

            sql = f"{select_columns(all_columns, query.get('fields'), select=query.get('select'))} FROM thread {sql_where} {order_by(all_columns, sort)} LIMIT :take OFFSET :skip"

            if query.get("as") == "column":
                return self.db.column(sql, params)
            else:
                return self.db.all(sql, params)

        except Exception as e:
            self.ctx.err(f"query_threads ({take}, {skip})", e)
            return []

    def prepare_thread(self, thread, id=None, user=None):
        now = datetime.now()
        if id:
            thread["id"] = id
        else:
            thread["createdAt"] = now
        thread["updatedAt"] = now
        initial_timestamp = int(time.time() * 1000) + 1
        if "messages" in thread:
            for idx, m in enumerate(thread["messages"]):
                self.ctx.cache_message_inline_data(m)
                if "timestamp" not in m:
                    m["timestamp"] = initial_timestamp + idx
        return with_user(thread, user=user)

    def create_thread(self, thread: Dict[str, Any], user=None):
        return self.db.insert("thread", self.columns["thread"], self.prepare_thread(thread, user=user))

    async def create_thread_async(self, thread: Dict[str, Any], user=None):
        return await self.db.insert_async("thread", self.columns["thread"], self.prepare_thread(thread, user=user))

    def update_thread(self, id, thread: Dict[str, Any], user=None):
        return self.db.update("thread", self.columns["thread"], self.prepare_thread(thread, id, user=user))

    async def update_thread_async(self, id, thread: Dict[str, Any], user=None):
        return await self.db.update_async("thread", self.columns["thread"], self.prepare_thread(thread, id, user=user))

    def delete_thread(self, id, user=None, callback=None):
        sql_where, params = self.get_user_filter(user, {"id": id})
        self.db.write(f"DELETE FROM thread {sql_where} AND id = :id", params, callback)

    def query_requests(self, query: Dict[str, Any], user=None):
        try:
            columns = self.columns["request"]
            all_columns = columns.keys()

            take = min(int(query.get("take", "50")), 10000)
            skip = int(query.get("skip", 0))
            sort = query.get("sort", "-id")

            # always filter by user
            sql_where, params = self.get_user_filter(user, {"take": take, "skip": skip})

            filter = {}
            for k in query:
                if k in all_columns:
                    filter[k] = query[k]
                    params[k] = query[k]

            if len(filter) > 0:
                sql_where += " AND " + " AND ".join([f"{k} = :{k}" for k in filter])

            if "null" in query:
                cols = valid_columns(all_columns, query["null"])
                if len(cols) > 0:
                    sql_where += " AND " + " AND ".join([f"{k} IS NULL" for k in cols])

            if "not_null" in query:
                cols = valid_columns(all_columns, query.get("not_null"))
                if len(cols) > 0:
                    sql_where += " AND " + " AND ".join([f"{k} IS NOT NULL" for k in cols])

            if "q" in query:
                sql_where += " AND " if sql_where else "WHERE "
                sql_where += "(title LIKE :q)"
                params["q"] = f"%{query['q']}%"

            if "month" in query:
                sql_where += " AND strftime('%Y-%m', createdAt) = :month"
                params["month"] = query["month"]

            sql = f"{select_columns(all_columns, query.get('fields'), select=query.get('select'))} FROM request {sql_where} {order_by(all_columns, sort)}LIMIT :take OFFSET :skip"

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
            sql_where += " AND strftime('%Y-%m-%d', createdAt) = :day"
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
        self.db.close()

        # complete all in progress tasks
        with self.db.create_writer_connection() as conn:
            conn.execute(
                "UPDATE thread SET completedAt = :completedAt, error = :error WHERE completedAt IS NULL",
                {"completedAt": datetime.now().isoformat(" "), "error": "Server Shutdown"},
            )
            conn.execute(
                "UPDATE request SET completedAt = :completedAt, error = :error WHERE completedAt IS NULL",
                {"completedAt": datetime.now().isoformat(" "), "error": "Server Shutdown"},
            )
