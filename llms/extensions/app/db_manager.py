import json
import sqlite3
from queue import Empty, Queue
from threading import Event, Thread


def create_reader_connection(db_path):
    conn = sqlite3.connect(db_path, timeout=1.0)  # Lower - reads should be fast
    conn.execute("PRAGMA query_only=1")  # Read-only optimization
    return conn


def create_writer_connection(db_path):
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")  # Enable WAL mode for better concurrency
    conn.execute("PRAGMA cache_size=-128000")  # Increase cache size for better performance
    conn.execute("PRAGMA synchronous=NORMAL")  # Reasonable durability/performance balance
    conn.execute("PRAGMA busy_timeout=5000")  # Reasonable timeout for busy connections
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
                    ctx.dbg("SQL>" + ("\n" if "\n" in sql else " ") + sql)
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


class DbManager:
    def __init__(self, ctx, db_path):
        if db_path is None:
            raise ValueError("db_path is required")
        self.ctx = ctx
        self.db_path = db_path
        self.task_queue = Queue()
        self.stop_event = Event()
        self.writer_thread = Thread(target=writer_thread, args=(ctx, db_path, self.task_queue, self.stop_event))
        self.writer_thread.start()
        self.read_only_pool = Queue()

    def create_reader_connection(self):
        return create_reader_connection(self.db_path)

    def create_writer_connection(self):
        return create_writer_connection(self.db_path)

    def resolve_connection(self):
        try:
            return self.read_only_pool.get_nowait()
        except Empty:
            return self.create_reader_connection()

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
            self.ctx.dbg("SQL>" + ("\n" if "\n" in sql else " ") + sql + ("\n" if parameters else "") + str(parameters))

    def exec(self, connection, sql, parameters=None):
        self.log_sql(sql, parameters)
        return connection.execute(sql, parameters or ())

    def all(self, sql, parameters=None, connection=None):
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
                self.read_only_pool.put(conn)

    def one(self, sql, parameters=None, connection=None):
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
                self.read_only_pool.put(conn)

    def scalar(self, sql, parameters=None, connection=None):
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
                self.read_only_pool.put(conn)

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
                self.read_only_pool.put(conn)

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
                self.read_only_pool.put(conn)

    # Helper to safely dump JSON if value exists
    def value(self, val):
        if val is None or val == "":
            return None
        if isinstance(val, (dict, list)):
            return json.dumps(val)
        return val

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
