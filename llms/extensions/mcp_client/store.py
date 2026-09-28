"""Owner-scoped SQLite state and atomic claims."""

import json
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime

from .common import McpError, dumps

TERMINAL = ("completed", "failed", "rejected", "canceled")


def now():
    return datetime.now(UTC).isoformat()


class Store:
    def __init__(self, path):
        self.path = str(path)
        directory = os.path.dirname(self.path) or "."
        os.makedirs(directory, mode=0o700, exist_ok=True)
        if os.name != "nt":
            os.chmod(directory, 0o700)
        with self.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS mcp_binding (
                    owner TEXT NOT NULL, server TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0,
                    disconnected INTEGER NOT NULL DEFAULT 0, credential TEXT,
                    clientSecret TEXT,
                    lease TEXT, leaseUntil REAL, PRIMARY KEY(owner,server));
                CREATE TABLE IF NOT EXISTS mcp_oauth (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, server TEXT NOT NULL,
                    revision INTEGER NOT NULL, expires REAL NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS mcp_batch (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, threadId TEXT NOT NULL,
                    runId INTEGER, message TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'waiting');
                CREATE TABLE IF NOT EXISTS mcp_approval (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, batchId TEXT NOT NULL,
                    owner TEXT NOT NULL, threadId TEXT NOT NULL, toolCallId TEXT NOT NULL,
                    status TEXT NOT NULL, updated REAL NOT NULL, data TEXT NOT NULL,
                    UNIQUE(batchId,toolCallId));
                CREATE INDEX IF NOT EXISTS mcp_approval_thread ON mcp_approval(owner,threadId);
                CREATE TABLE IF NOT EXISTS mcp_approval_grant (
                    owner TEXT NOT NULL, server TEXT NOT NULL, tool TEXT NOT NULL,
                    configHash TEXT NOT NULL, schemaHash TEXT NOT NULL, created REAL NOT NULL,
                    PRIMARY KEY(owner,server,tool,configHash,schemaHash));
                CREATE TABLE IF NOT EXISTS mcp_invocation (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, status TEXT NOT NULL,
                    updated REAL NOT NULL, result TEXT);
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(mcp_binding)")}
            if "clientSecret" not in columns:
                db.execute("ALTER TABLE mcp_binding ADD COLUMN clientSecret TEXT")
        if os.name != "nt":
            os.chmod(self.path, 0o600)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def has_approval_grant(self, owner, server, tool, config_hash, schema_hash):
        with self.connection() as db:
            return db.execute(
                "SELECT 1 FROM mcp_approval_grant WHERE owner=? AND server=? AND tool=? AND configHash=? AND schemaHash=?",
                (owner, server, tool, config_hash, schema_hash),
            ).fetchone() is not None

    def save_approval_grant(self, owner, server, tool, config_hash, schema_hash):
        with self.connection() as db:
            db.execute(
                "INSERT OR REPLACE INTO mcp_approval_grant VALUES (?,?,?,?,?,?)",
                (owner, server, tool, config_hash, schema_hash, time.time()),
            )

    def approval_grants(self, owner, server, config_hash):
        with self.connection() as db:
            return [dict(row) for row in db.execute(
                "SELECT tool,schemaHash FROM mcp_approval_grant WHERE owner=? AND server=? AND configHash=? ORDER BY tool",
                (owner, server, config_hash),
            )]

    def revoke_approval_grant(self, owner, server, tool):
        with self.connection() as db:
            db.execute("DELETE FROM mcp_approval_grant WHERE owner=? AND server=? AND tool=?", (owner, server, tool))

    def binding(self, owner, server):
        with self.connection() as db:
            row = db.execute("SELECT * FROM mcp_binding WHERE owner=? AND server=?", (owner, server)).fetchone()
            return (
                dict(row)
                if row
                else {"owner": owner, "server": server, "revision": 0, "disconnected": 0,
                      "credential": None, "clientSecret": None}
            )

    def ensure_binding(self, owner, server):
        with self.connection() as db:
            db.execute("INSERT OR IGNORE INTO mcp_binding(owner,server) VALUES (?,?)", (owner, server))
        return self.binding(owner, server)

    def cancel_authorizations(self, owner, server):
        with self.connection() as db:
            db.execute("DELETE FROM mcp_oauth WHERE owner=? AND server=?", (owner, server))

    def connect(self, owner, server, disconnected=False, delete=False, preserve_client_secret=False):
        with self.connection() as db:
            db.execute("INSERT OR IGNORE INTO mcp_binding(owner,server) VALUES (?,?)", (owner, server))
            db.execute(
                "UPDATE mcp_binding SET revision=revision+1,disconnected=?,lease=NULL,leaseUntil=NULL "
                "WHERE owner=? AND server=?",
                (int(disconnected), owner, server),
            )
            if delete:
                if preserve_client_secret:
                    db.execute("UPDATE mcp_binding SET credential=NULL WHERE owner=? AND server=?", (owner, server))
                else:
                    db.execute("UPDATE mcp_binding SET credential=NULL,clientSecret=NULL WHERE owner=? AND server=?", (owner, server))
            db.execute("DELETE FROM mcp_oauth WHERE owner=? AND server=?", (owner, server))

    def credential(self, owner, server, revision, payload):
        with self.connection() as db:
            changed = db.execute(
                "UPDATE mcp_binding SET credential=? WHERE owner=? AND server=? AND revision=? AND disconnected=0",
                (payload, owner, server, revision),
            ).rowcount
            if not changed:
                raise McpError("stale_tool", "Connection changed during authorization")

    def client_secret(self, owner, server, revision, payload):
        with self.connection() as db:
            changed = db.execute(
                "UPDATE mcp_binding SET clientSecret=? WHERE owner=? AND server=? AND revision=?",
                (payload, owner, server, revision),
            ).rowcount
            if not changed:
                raise McpError("stale_tool", "Connection changed while saving OAuth credentials")

    @contextmanager
    def credential_lease(self, owner, server):
        claim = uuid.uuid4().hex
        with self.connection() as db:
            changed = db.execute(
                "UPDATE mcp_binding SET lease=?,leaseUntil=? WHERE owner=? AND server=? "
                "AND disconnected=0 AND (lease IS NULL OR leaseUntil<?)",
                (claim, time.time() + 360, owner, server, time.time()),
            ).rowcount
            if not changed:
                raise McpError("busy", "Credentials are being refreshed")
        try:
            yield
        finally:
            with self.connection() as db:
                db.execute(
                    "UPDATE mcp_binding SET lease=NULL,leaseUntil=NULL WHERE owner=? AND server=? AND lease=?",
                    (owner, server, claim),
                )

    def oauth_start(self, ident, owner, server, revision, payload):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM mcp_oauth WHERE expires<?", (time.time(),))
            if db.execute("SELECT 1 FROM mcp_oauth WHERE owner=? AND server=?", (owner, server)).fetchone():
                raise McpError("busy", "Sign-in is already in progress")
            if db.execute("SELECT COUNT(*) FROM mcp_oauth").fetchone()[0] >= 128:
                raise McpError("busy", "Too many sign-in requests")
            db.execute(
                "INSERT INTO mcp_oauth VALUES (?,?,?,?,?,?)",
                (ident, owner, server, revision, time.time() + 300, payload),
            )

    def oauth_pending(self, owner, server):
        with self.connection() as db:
            return db.execute(
                "SELECT 1 FROM mcp_oauth WHERE owner=? AND server=? AND expires>?",
                (owner, server, time.time()),
            ).fetchone() is not None

    def oauth_claim(self, ident, owner):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM mcp_oauth WHERE id=? AND owner=? AND expires>?", (ident, owner, time.time())
            ).fetchone()
            if not row:
                raise McpError("invalid_oauth_state")
            db.execute("DELETE FROM mcp_oauth WHERE id=?", (ident,))
            return dict(row)

    def invocation_claim(self, ident, owner):
        with self.connection() as db:
            inserted = db.execute(
                "INSERT OR IGNORE INTO mcp_invocation VALUES (?,?,'prepared',?,NULL)", (ident, owner, time.time())
            ).rowcount
            if not inserted:
                row = db.execute("SELECT * FROM mcp_invocation WHERE id=? AND owner=?", (ident, owner)).fetchone()
                if row and row["status"] in ("completed", "failed") and row["result"]:
                    return json.loads(row["result"])
                raise McpError("outcome_unknown", "Invocation was already claimed; it will not be repeated")
        return None

    def invocation_state(self, ident, owner, state, result=None):
        with self.connection() as db:
            db.execute(
                "UPDATE mcp_invocation SET status=?,updated=?,result=? WHERE id=? AND owner=?",
                (state, time.time(), dumps(result) if result is not None else None, ident, owner),
            )

    def batch(self, ident, owner, thread, run, message, rows):
        with self.connection() as db:
            inserted = db.execute(
                "INSERT OR IGNORE INTO mcp_batch(id,owner,threadId,runId,message) VALUES (?,?,?,?,?)",
                (ident, owner, str(thread), run, dumps(message)),
            ).rowcount
            if inserted:
                for row in rows:
                    row.update(batchId=ident, threadId=thread, createdAt=now(), updatedAt=now())
                    db.execute(
                        "INSERT INTO mcp_approval(batchId,owner,threadId,toolCallId,status,updated,data) "
                        "VALUES (?,?,?,?,?,?,?)",
                        (ident, owner, str(thread), row["toolCallId"], row["status"], time.time(), dumps(row)),
                    )

    @staticmethod
    def dto(row):
        if not row:
            return None
        value = json.loads(row["data"])
        value.update(id=row["id"], status=row["status"])
        value["batchStatus"] = "completed" if row["batchStatus"] == "completed" else "pending"
        return value

    @staticmethod
    def recover_claims(db, owner):
        # Recover a completed journal entry after a crash between saving the result
        # and resolving its approval. Unknown remote effects still need reconciliation.
        for row in db.execute(
            "SELECT * FROM mcp_approval WHERE owner=? AND status='executing' AND updated<?", (owner, time.time() - 600)
        ).fetchall():
            value = json.loads(row["data"])
            invocation = db.execute(
                "SELECT * FROM mcp_invocation WHERE id=? AND owner=?", (value["invocationId"], owner)
            ).fetchone()
            if invocation and invocation["status"] in ("completed", "failed") and invocation["result"]:
                status = "completed"
                value["result"] = json.loads(invocation["result"])
            elif value["source"] == "local":
                # Local automatic tools have no interactive approval card. Report an
                # interrupted result instead of rerunning them or stranding the batch.
                status = "failed"
                value["error"] = "Local call was interrupted; its outcome is unknown and it was not replayed"
            else:
                status = "outcome_unknown"
                value["error"] = "outcome_unknown"
            value.update(status=status, updatedAt=now(), resolvedAt=now() if status in TERMINAL else None)
            db.execute(
                "UPDATE mcp_approval SET status=?,updated=?,data=? WHERE id=? AND status='executing'",
                (status, time.time(), dumps(value), row["id"]),
            )

    def rows(self, owner, thread=None, batch=None):
        with self.connection() as db:
            # The stale claim deadline exceeds the maximum call and cleanup deadlines.
            db.execute("BEGIN IMMEDIATE")
            self.recover_claims(db, owner)
            clauses, args = ["owner=?"], [owner]
            if thread is not None:
                clauses.append("threadId=?")
                args.append(str(thread))
            if batch is not None:
                clauses.append("batchId=?")
                args.append(batch)
            return [
                self.dto(r)
                for r in db.execute(
                    "SELECT *, (SELECT status FROM mcp_batch WHERE id=mcp_approval.batchId) AS batchStatus "
                    "FROM mcp_approval WHERE " + " AND ".join(clauses) + " ORDER BY id",
                    args,
                )
            ]

    def row(self, ident, owner):
        with self.connection() as db:
            return self.dto(
                db.execute(
                    "SELECT *, (SELECT status FROM mcp_batch WHERE id=mcp_approval.batchId) AS batchStatus "
                    "FROM mcp_approval WHERE id=? AND owner=?",
                    (ident, owner),
                ).fetchone()
            )

    def claim(self, ident, owner, expected="pending", status="executing"):
        with self.connection() as db:
            return (
                db.execute(
                    "UPDATE mcp_approval SET status=?,updated=? WHERE id=? AND owner=? AND status=?",
                    (status, time.time(), ident, owner, expected),
                ).rowcount
                == 1
            )

    def finish(self, ident, owner, state, **values):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM mcp_approval WHERE id=? AND owner=?", (ident, owner)).fetchone()
            if not row:
                raise McpError("not_found")
            value = json.loads(row["data"])
            value.update(values, status=state, updatedAt=now(), resolvedAt=now() if state in TERMINAL else None)
            db.execute(
                "UPDATE mcp_approval SET status=?,updated=?,data=? WHERE id=? AND owner=?",
                (state, time.time(), dumps(value), ident, owner),
            )

    def batches(self, owner, thread):
        with self.connection() as db:
            return [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM mcp_batch WHERE owner=? AND threadId=? AND status!='completed' ORDER BY rowid",
                    (owner, str(thread)),
                )
            ]

    def pending_batches(self):
        with self.connection() as db:
            return [dict(row) for row in db.execute("SELECT * FROM mcp_batch WHERE status!='completed'")]

    def complete_batch(self, ident, owner):
        with self.connection() as db:
            db.execute("UPDATE mcp_batch SET status='completed' WHERE id=? AND owner=?", (ident, owner))

    def get_batch(self, ident, owner):
        with self.connection() as db:
            row = db.execute("SELECT * FROM mcp_batch WHERE id=? AND owner=?", (ident, owner)).fetchone()
            return dict(row) if row else None

    def attach_run(self, ident, owner, run_id):
        with self.connection() as db:
            db.execute("UPDATE mcp_batch SET runId=? WHERE id=? AND owner=? AND runId IS NULL", (run_id, ident, owner))

    def delete_user(self, owner):
        with self.connection() as db:
            for table in ("mcp_binding", "mcp_oauth", "mcp_invocation", "mcp_approval", "mcp_batch", "mcp_approval_grant"):
                db.execute(f"DELETE FROM {table} WHERE owner=?", (owner,))
