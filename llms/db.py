import json
import os
import sqlite3
import threading
from queue import Empty, Queue
from threading import Event, Thread

POOL = os.getenv("LLMS_POOL", "0") == "1"


def create_reader_connection(db_path):
    # isolation_level=None leaves the connection in autocommit mode
    conn = sqlite3.connect(
        db_path, timeout=1.0, check_same_thread=False, isolation_level=None
    )  # Lower - reads should be fast
    conn.execute("PRAGMA query_only=1")  # Read-only optimization
    return conn


def create_writer_connection(db_path):
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA busy_timeout=5000")  # Reasonable timeout for busy connections
    conn.execute("PRAGMA journal_mode=WAL")  # Enable WAL mode for better concurrency
    conn.execute("PRAGMA cache_size=-128000")  # Increase cache size for better performance
    conn.execute("PRAGMA synchronous=NORMAL")  # Reasonable durability/performance balance
    return conn


def writer_thread(ctx, db_path, task_queue, stop_event):
    conn = create_writer_connection(db_path)
    try:
        while not stop_event.is_set():
            try:
                # Use timeout to check stop_event periodically
                task = task_queue.get(timeout=0.1)

                if task is None:  # Poison pill for clean shutdown
                    break

                sql, args, callback = task  # Optional callback for results

                try:
                    ctx.dbg("SQL>" + ("\n" if "\n" in sql else " ") + sql + ("\n" if args else " ") + str(args))
                    cursor = conn.execute(sql, args)
                    conn.commit()
                    ctx.dbg(f"lastrowid {cursor.lastrowid}, rowcount {cursor.rowcount}")
                    if callback:
                        callback(cursor.lastrowid, cursor.rowcount)
                except sqlite3.Error as e:
                    ctx.err("writer_thread", e)
                    if callback:
                        callback(None, None, error=e)
                finally:
                    task_queue.task_done()

            except Empty:
                continue

    finally:
        conn.close()


def to_dto(ctx, row, json_columns):
    # as=column -> [0,1,2]
    if not isinstance(row, dict):
        return row

    to = {}
    for k, v in row.items():
        if k in json_columns and v is not None and isinstance(v, str):
            try:
                to[k] = json.loads(v)
            except Exception as e:
                print(f"Failed to parse JSON for {k}: {v} ({type(v)})", e)
                to[k] = v
        else:
            to[k] = v
    return to


def valid_columns(all_columns, fields):
    if fields:
        if not isinstance(fields, list):
            fields = fields.split(",")
        cols = []
        for k in fields:
            k = k.strip()
            if k in all_columns:
                cols.append(k)
        return cols
    return []


def table_columns(all_columns, fields):
    cols = valid_columns(all_columns, fields)
    return ", ".join(cols) if len(cols) > 0 else ", ".join(all_columns)


def select_columns(all_columns, fields, select=None):
    columns = table_columns(all_columns, fields)
    if select == "distinct":
        return f"SELECT DISTINCT {columns}"
    return f"SELECT {columns}"


def order_by(all_columns, sort):
    cols = []
    for k in sort.split(","):
        k = k.strip()
        by = ""
        if k[0] == "-":
            by = " DESC"
            k = k[1:]
        if k in all_columns:
            cols.append(f"{k}{by}")
    return f"ORDER BY {', '.join(cols)} " if len(cols) > 0 else ""


class DbManager:
    def __init__(self, ctx, db_path, clone=None):
        if db_path is None:
            raise ValueError("db_path is required")
        self.ctx = ctx
        self.db_path = db_path
        self.read_only_pool = Queue()
        if not clone:
            self.task_queue = Queue()
            self.stop_event = Event()
            self.writer_thread = Thread(target=writer_thread, args=(ctx, db_path, self.task_queue, self.stop_event))
            self.writer_thread.start()
        else:
            # share singleton writer thread in clones
            self.task_queue = clone.task_queue
            self.stop_event = clone.stop_event
            self.writer_thread = clone.writer_thread

    def create_reader_connection(self):
        return create_reader_connection(self.db_path)

    def create_writer_connection(self):
        return create_writer_connection(self.db_path)

    def resolve_connection(self):
        if POOL:
            try:
                return self.read_only_pool.get_nowait()
            except Empty:
                return self.create_reader_connection()
        else:
            return self.create_reader_connection()

    def release_connection(self, conn):
        if POOL:
            conn.rollback()
            self.read_only_pool.put(conn)
        else:
            conn.close()

    def write(self, query, args=None, callback=None):
        """
        Execute a write operation asynchronously.

        Args:
            query (str): The SQL query to execute.
            args (tuple, optional): Arguments for the query.
            callback (callable, optional): A function called after execution with signature:
                callback(lastrowid, rowcount, error=None)
                - lastrowid (int): output of cursor.lastrowid
                - rowcount (int): output of cursor.rowcount
                - error (Exception): exception if operation failed, else None
        """
        self.task_queue.put((query, args, callback))

    def log_sql(self, sql, parameters=None):
        if self.ctx.debug:
            self.ctx.dbg(
                "SQL>" + ("\n" if "\n" in sql else " ") + sql + ("\n" if parameters else " ") + str(parameters)
            )

    def exec(self, connection, sql, parameters=None):
        self.log_sql(sql, parameters)
        return connection.execute(sql, parameters or ())

    def all(self, sql, parameters=None, connection=None):
        """
        Execute a query and return all rows as a list of dictionaries.
        """
        conn = self.resolve_connection() if connection is None else connection

        try:
            self.log_sql(sql, parameters)
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(sql, parameters or ())
            rows = [dict(row) for row in cursor.fetchall()]
            return rows
        finally:
            if connection is None:
                conn.row_factory = None
                self.release_connection(conn)

    def one(self, sql, parameters=None, connection=None):
        """
        Execute a query and return the first row as a dictionary.
        """
        conn = self.resolve_connection() if connection is None else connection

        try:
            self.log_sql(sql, parameters)
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(sql, parameters or ())
            row = cursor.fetchone()
            return dict(row) if row else None
        finally:
            if connection is None:
                conn.row_factory = None
                self.release_connection(conn)

    def scalar(self, sql, parameters=None, connection=None):
        """
        Execute a scalar query and return the first column of the first row.
        """
        conn = self.resolve_connection() if connection is None else connection

        try:
            self.log_sql(sql, parameters)
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(sql, parameters or ())
            row = cursor.fetchone()
            return row[0] if row else None
        finally:
            if connection is None:
                conn.row_factory = None
                self.release_connection(conn)

    def column(self, sql, parameters=None, connection=None):
        """
        Execute a 1 column query and return the values as a list.
        """
        conn = self.resolve_connection() if connection is None else connection

        try:
            self.log_sql(sql, parameters)
            cursor = conn.execute(sql, parameters or ())
            return [row[0] for row in cursor.fetchall()]
        finally:
            if connection is None:
                self.release_connection(conn)

    def dict(self, sql, parameters=None, connection=None):
        """
        Execute a 2 column query and return the keys as the first column and the values as the second column.
        """
        conn = self.resolve_connection() if connection is None else connection

        try:
            self.log_sql(sql, parameters)
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(sql, parameters or ())
            rows = cursor.fetchall()
            return {row[0]: row[1] for row in rows}
        finally:
            if connection is None:
                conn.row_factory = None
                self.release_connection(conn)

    # Helper to safely dump JSON if value exists
    def value(self, val):
        if val is None or val == "":
            return None
        if isinstance(val, (dict, list)):
            return json.dumps(val)
        return val

    def insert(self, table, columns, info, callback=None):
        if not info:
            raise Exception("info is required")

        args = {}
        known_columns = columns.keys()
        for k, val in info.items():
            if k in known_columns and k != "id":
                args[k] = self.value(val)

        insert_keys = list(args.keys())
        insert_body = ", ".join(insert_keys)
        insert_values = ", ".join(["?" for _ in insert_keys])

        sql = f"INSERT INTO {table} ({insert_body}) VALUES ({insert_values})"

        self.write(sql, tuple(args[k] for k in insert_keys), callback)

    async def insert_async(self, table, columns, info):
        event = threading.Event()

        ret = [None, None]

        def cb(lastrowid, rowcount, error=None):
            nonlocal ret
            if error:
                ret[1] = error
            else:
                ret[0] = lastrowid
            event.set()

        self.insert(table, columns, info, cb)
        event.wait()
        if ret[1]:
            raise ret[1]
        return ret[0]

    def update(self, table, columns, info, callback=None):
        if not info:
            raise Exception("info is required")

        args = {}
        known_columns = columns.keys()
        for k, val in info.items():
            if k in known_columns and k != "id":
                args[k] = self.value(val)

        update_keys = list(args.keys())
        update_body = ", ".join([f"{k} = :{k}" for k in update_keys])

        args["id"] = info["id"]
        sql = f"UPDATE {table} SET {update_body} WHERE id = :id"

        self.write(sql, args, callback)

    async def update_async(self, table, columns, info):
        event = threading.Event()

        ret = [None, None]

        def cb(lastrowid, rowcount, error=None):
            nonlocal ret
            if error:
                ret[1] = error
            else:
                ret[0] = rowcount
            event.set()

        self.update(table, columns, info, cb)
        event.wait()
        if ret[1]:
            raise ret[1]
        return ret[0]

    def close(self):
        self.ctx.dbg("Closing database")
        self.stop_event.set()
        self.task_queue.put(None)  # Poison pill to signal shutdown
        self.writer_thread.join()

        while not self.read_only_pool.empty():
            try:
                conn = self.read_only_pool.get_nowait()
                conn.close()
            except Empty:
                break
