import json
import os
import re
import struct
from datetime import datetime, timedelta
from typing import Any, Dict
from urllib.parse import urlsplit

from llms.db import DbManager, order_by, select_columns, to_dto, valid_columns

MIN_DATE = "0000-01-01"
MAX_DATE = "9999-12-31"

# Local column -> (Gemini custom_metadata key, value kind). Only these reach the store: a field
# earns a slot by being used in a filter, a citation, or reconciliation (METADATA_SCHEMA.md §1).
#
# Pushed keys are snake_case, and that is load-bearing rather than styling: a camelCase key
# indexes without complaint and then never matches a `metadata_filter`. Probed against the live
# API with the same value under three spellings on one document - `doc_type` and `doctype` filter,
# `docType` returns nothing. Local column names stay camelCase to match the rest of the codebase;
# this mapping is where the two conventions meet, which is why the fix was one table.
PUSHED_METADATA = {
    "id": ("id", "numeric"),
    "hash": ("hash", "string"),
    "category": ("category", "string"),
    "categoryPath": ("category_path", "list"),
    "sourceUrl": ("source_url", "string"),
    "docType": ("doc_type", "string"),
    "sourceUpdatedAt": ("updated_at", "numeric"),
    "status": ("status", "string"),
    "locale": ("locale", "string"),
    "product": ("product", "string"),
    "versions": ("versions", "list"),
    "tags": ("tags", "list"),
}

# A camelCase key is unfilterable, so it must never reach a store. Asserted at import rather than
# left to a test, because the failure it prevents is silent and only shows up as "the filter
# returns nothing" months later.
assert all(k == k.lower() for _, (k, _) in PUSHED_METADATA.items()), (
    "Gemini custom_metadata keys must be lowercase/snake_case: "
    f"{[k for _, (k, _) in PUSHED_METADATA.items() if k != k.lower()]}"
)


def as_list(value):
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return value
    # Gemini's wire form for a stringListValue. Responses come back unwrapped via
    # custom_metadata_dto(), so both shapes reach this function.
    if isinstance(value, dict):
        return value.get("values") or []
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else [parsed]
        except Exception:
            return [value]
    return [value]


def category_ancestors(category):
    """`guides/auth` -> ['guides', 'guides/auth'] so a filter can select a whole subtree."""
    if not category:
        return []
    segs = [s for s in str(category).split("/") if s]
    return ["/".join(segs[: i + 1]) for i in range(len(segs))]


def to_custom_metadata(doc):
    """The custom_metadata list this document should carry in Gemini."""
    out = []
    for col, (key, kind) in PUSHED_METADATA.items():
        val = doc.get(col)
        if kind == "list":
            vals = as_list(val)
            if vals:
                # Must be wrapped: the SDK validates this against its StringList model, so a bare
                # list is rejected before the request is ever sent.
                out.append({"key": key, "string_list_value": {"values": vals}})
        elif kind == "numeric":
            if val is not None and val != "":
                out.append({"key": key, "numeric_value": val})
        else:
            if val is not None and val != "":
                out.append({"key": key, "string_value": str(val)})
    return out


def _canon_metadata(items):
    """A custom_metadata list reduced to a comparable dict, in the form Gemini can store."""
    out = {}
    for item in items or []:
        key = item.get("key")
        if "string_list_value" in item:
            out[key] = sorted(as_list(item.get("string_list_value")))
        elif "numeric_value" in item:
            # Both sides through the same lossy transform: comparing what we sent against what
            # Gemini can actually store is the only comparison that can ever agree.
            out[key] = gemini_numeric(item.get("numeric_value"))
        else:
            out[key] = item.get("string_value")
    return out


def gemini_numeric(value):
    """
    A number as Gemini hands it back, rather than as we sent it.

    `numeric_value` round-trips through a float32 and is then serialised with 8 significant
    digits, so any value above 2**24 returns changed. Proven against the live API on 8 documents:
    1688880329 comes back as 1688880400, 1730696874 as 1730696800.

    This is why the comparison has to normalise instead of testing equality. An epoch-seconds
    `updated_at` differs from its own echo by up to ~64 seconds, which made metadata_differs()
    permanently true for every document carrying one - so the pending count could never reach
    zero and a re-index could never clear it, no matter how many times it ran.

    The precision loss is only acceptable because `updated_at` exists for staleness *filtering*;
    change detection is `contentHash`, which is a string and round-trips exactly.
    """
    try:
        f32 = struct.unpack("f", struct.pack("f", float(value)))[0]
    except (TypeError, ValueError, OverflowError):
        return None
    return float(f"{f32:.8g}")


def metadata_diff_fields(doc):
    """
    Which pushed keys differ from the copy Gemini holds.

    "65 documents are pending" doesn't tell anyone why they should spend a re-index on it.
    "42 have a changed doc_type" does, and it's the same comparison either way.
    """
    want = _canon_metadata(to_custom_metadata(doc))
    have = doc.get("customMetadata")
    if isinstance(have, str):
        try:
            have = json.loads(have)
        except Exception:
            have = None
    if have is None:
        # Never pushed with metadata at all, rather than a per-field edit.
        return sorted(want.keys()) if want else []
    have = _canon_metadata(have)
    return sorted(k for k in set(want) | set(have) if want.get(k) != have.get(k))


def metadata_differs(doc):
    """
    True when the document's local metadata no longer matches what Gemini holds.

    `customMetadata` is the local record of the remote copy, so comparing against it needs no
    dirty flag and can't drift.
    """
    want = to_custom_metadata(doc)
    have = doc.get("customMetadata")
    if isinstance(have, str):
        try:
            have = json.loads(have)
        except Exception:
            return True
    if have is None:
        return bool(want)

    return _canon_metadata(want) != _canon_metadata(have)


def with_user(data, user):
    if user is None:
        if "user" in data:
            del data["user"]
        return data
    else:
        data["user"] = user
        return data


def to_ints(ints):
    ret = []
    if isinstance(ints, (str)):
        ints_str = ints.split(",")
        for int_str in ints_str:
            ret.append(int(int_str))
    elif isinstance(ints, (list)):
        return ints
    return ret


def referrer_domain(origin=None, page_url=None):
    """Return a normalized host (including a meaningful port) from stored browser URLs."""
    for value in (origin, page_url):
        value = str(value or "").strip()
        if not value or value.lower() == "null":
            continue
        try:
            parsed = urlsplit(value if "://" in value else f"//{value}")
            host = (parsed.hostname or "").lower()
            if not host:
                continue
            port = parsed.port
            if ":" in host:
                host = f"[{host}]"
            return f"{host}:{port}" if port is not None else host
        except (TypeError, ValueError):
            continue
    return None


def timestamp_text(value):
    return value.isoformat() if isinstance(value, datetime) else value


class GeminiDB:
    def __init__(self, ctx, db_path=None, clone=None):
        if db_path is None:
            raise Exception("db_path is required")

        self.ctx = ctx
        self.db_path = str(db_path)
        dirname = os.path.dirname(self.db_path)
        if dirname:
            os.makedirs(dirname, exist_ok=True)

        self.db = DbManager(ctx, self.db_path) if not clone else DbManager(ctx, self.db_path, clone=clone.db)
        self.columns = {
            "filestore": {
                "id": "INTEGER",
                "user": "TEXT",
                "createdAt": "TIMESTAMP",
                "updatedAt": "TIMESTAMP",
                "name": "TEXT",
                "displayName": "TEXT",
                "createTime": "TEXT",
                "updateTime": "TEXT",
                "activeDocumentsCount": "INTEGER",
                "pendingDocumentsCount": "INTEGER",
                "failedDocumentsCount": "INTEGER",
                "sizeBytes": "INTEGER",
                "metadata": "JSON",
                "error": "TEXT",
                "ref": "TEXT",
                # public | internal. The security boundary is the store, not a metadata filter
                # (METADATA_SCHEMA.md §2), so this is what a published assistant validates against.
                "visibility": "TEXT",
                # Which columns to surface as facets on this store's page - a display preference,
                # not a schema.
                "facets": "JSON",
            },
            "source": {
                "id": "INTEGER",
                "filestoreId": "INTEGER",
                "user": "TEXT",
                "createdAt": "TIMESTAMP",
                "updatedAt": "TIMESTAMP",
                "name": "TEXT",
                "type": "TEXT",  # folder | zip | sitemap | git | ...
                "enabled": "INTEGER",
                "config": "JSON",  # type-specific: root path, base url, repo, token refs
                "category": "JSON",  # { root, maxDepth }
                "rules": "JSON",  # metadata derivation (INGEST.md §6)
                "include": "JSON",  # glob / url patterns
                "exclude": "JSON",
                "extract": "JSON",  # selector, strip, minWords
                "chunking": "JSON",
                "volatile": "JSON",  # regexes stripped before hashing
                "extractorVer": "TEXT",
                "schedule": "TEXT",
                "onDelete": "TEXT",  # tombstone | remove | ignore
                "cursor": "JSON",  # incremental state
                "lastRunId": "INTEGER",
                "lastRunAt": "TIMESTAMP",
                "error": "TEXT",
            },
            "source_run": {
                "id": "INTEGER",
                "sourceId": "INTEGER",
                "user": "TEXT",
                "startedAt": "TIMESTAMP",
                "completedAt": "TIMESTAMP",
                "status": "TEXT",  # running | completed | failed | cancelled | preview
                "dryRun": "INTEGER",
                "discovered": "INTEGER",
                "added": "INTEGER",
                "changed": "INTEGER",
                "metadataOnly": "INTEGER",
                "unchanged": "INTEGER",
                "removed": "INTEGER",
                "skipped": "INTEGER",
                "failed": "INTEGER",
                "bytes": "INTEGER",
                "plan": "JSON",  # dry-run detail the operator confirms
                "log": "JSON",
                "error": "TEXT",
            },
            "assistant": {
                "id": "INTEGER",
                "filestoreId": "INTEGER",
                "user": "TEXT",
                "createdAt": "TIMESTAMP",
                "updatedAt": "TIMESTAMP",
                "name": "TEXT",
                "publicId": "TEXT",
                "enabled": "INTEGER",
                "publishedAt": "TIMESTAMP",
                "config": "JSON",
            },
            "assistant_conversation": {
                "id": "INTEGER",
                "assistantId": "INTEGER",
                "user": "TEXT",
                "createdAt": "TIMESTAMP",
                "updatedAt": "TIMESTAMP",
                "sessionId": "TEXT",
                "origin": "TEXT",
                "pageUrl": "TEXT",
                "userAgent": "TEXT",
                "title": "TEXT",
                "status": "TEXT",
                "messageCount": "INTEGER",
                "lastMessage": "TEXT",
            },
            "assistant_message": {
                "id": "INTEGER",
                "conversationId": "INTEGER",
                "createdAt": "TIMESTAMP",
                "role": "TEXT",
                "content": "TEXT",
                "citations": "JSON",
                "error": "TEXT",
            },
            "search_widget": {
                "id": "INTEGER",
                "filestoreId": "INTEGER",
                "user": "TEXT",
                "createdAt": "TIMESTAMP",
                "updatedAt": "TIMESTAMP",
                "name": "TEXT",
                "publicId": "TEXT",
                "enabled": "INTEGER",
                "publishedAt": "TIMESTAMP",
                "config": "JSON",
            },
            "search_query": {
                "id": "INTEGER",
                "searchWidgetId": "INTEGER",
                "createdAt": "TIMESTAMP",
                "query": "TEXT",
                "normalizedQuery": "TEXT",
                "groupKey": "TEXT",
                "origin": "TEXT",
                "pageUrl": "TEXT",
                "userAgent": "TEXT",
                "resultCount": "INTEGER",
                "documentCount": "INTEGER",
                "durationMs": "INTEGER",
            },
            "search_click": {
                "id": "INTEGER",
                "searchQueryId": "INTEGER",
                "searchWidgetId": "INTEGER",
                "documentId": "INTEGER",
                "sectionId": "INTEGER",
                "createdAt": "TIMESTAMP",
                "position": "INTEGER",
                "documentTitle": "TEXT",
                "sourceUrl": "TEXT",
                "resultType": "TEXT",
            },
            "search_page_view": {
                "id": "INTEGER",
                "searchWidgetId": "INTEGER",
                "createdAt": "TIMESTAMP",
                "hourKey": "TEXT",
                "dayKey": "TEXT",
                "clientId": "TEXT",
                "sessionId": "TEXT",
                "firstVisit": "INTEGER",
                "origin": "TEXT",
                "pageUrl": "TEXT",
                "pagePath": "TEXT",
                "pageTitle": "TEXT",
                "referrer": "TEXT",
                "userAgent": "TEXT",
                "language": "TEXT",
                "languages": "TEXT",
                "timezone": "TEXT",
                "platform": "TEXT",
                "deviceType": "TEXT",
                "screenWidth": "INTEGER",
                "screenHeight": "INTEGER",
                "viewportWidth": "INTEGER",
                "viewportHeight": "INTEGER",
                "devicePixelRatio": "REAL",
                "colorDepth": "INTEGER",
                "touchPoints": "INTEGER",
                "connectionType": "TEXT",
                "downlink": "REAL",
                "rtt": "INTEGER",
                "saveData": "INTEGER",
                "navigationType": "TEXT",
                "durationMs": "INTEGER",
                "domContentLoadedMs": "INTEGER",
                "loadMs": "INTEGER",
                "utmSource": "TEXT",
                "utmMedium": "TEXT",
                "utmCampaign": "TEXT",
                "utmTerm": "TEXT",
                "utmContent": "TEXT",
            },
            "search_section": {
                "id": "INTEGER",
                "documentId": "INTEGER",
                "filestoreId": "INTEGER",
                "user": "TEXT",
                "ordinal": "INTEGER",
                "documentTitle": "TEXT",
                "heading": "TEXT",
                "headingLevel": "INTEGER",
                "hierarchy": "JSON",
                "anchor": "TEXT",
                "url": "TEXT",
                "kind": "TEXT",
                "content": "TEXT",
                "category": "TEXT",
                "docType": "TEXT",
                "status": "TEXT",
                "locale": "TEXT",
                "product": "TEXT",
                "versions": "JSON",
                "tags": "JSON",
            },
            "document": {
                "id": "INTEGER",
                "filestoreId": "INTEGER",
                "user": "TEXT",
                "createdAt": "TIMESTAMP",
                "filename": "TEXT",
                "url": "TEXT",  # /~cache/23/238841878a0ebeeea8d0034cfdafc82b15d3a6d00c344b0b5e174acbb19572ef.png
                "hash": "TEXT",  # 238841878a0ebeeea8d0034cfdafc82b15d3a6d00c344b0b5e174acbb19572ef
                "size": "INTEGER",  # 1593817 (bytes)
                # https://ai.google.dev/api/file-search/documents
                "displayName": "TEXT",
                "name": "TEXT",
                "customMetadata": "JSON",  # [{object (CustomMetadata)}]
                "createTime": "TEXT",
                "updateTime": "TEXT",
                "sizeBytes": "INTEGER",
                "mimeType": "TEXT",  # text/markdown, application,pdf
                "state": "TEXT",  # STATE_UNSPECIFIED, STATE_PENDING, STATE_ACTIVE, STATE_FAILED
                "category": "text",  # folder path relative to the source root, e.g. guides/auth
                # Where this document lives for a reader, e.g. https://docs.acme.com/guide/auth
                # Citations link here when set, so an answer points at the customer's own page
                # instead of a download of the cached upload.
                "sourceUrl": "TEXT",
                # --- ingest identity & change detection (INGEST.md §3) ---
                "sourceId": "INTEGER",  # null for manual uploads
                "sourceKey": "TEXT",  # stable identity within the source, e.g. guides/auth/jwt.md
                "sourceEtag": "TEXT",  # change token the source gave us (etag, blob sha, ...)
                "contentHash": "TEXT",  # sha256 of normalised extracted text
                "metadataHash": "TEXT",  # sha256 of canonical derived metadata
                "extractorVer": "TEXT",  # which extractor produced contentHash
                "tombstonedAt": "TIMESTAMP",  # removed upstream, kept for reporting
                # --- filterable metadata (METADATA_SCHEMA.md §3-4) ---
                "categoryPath": "JSON",  # ["guides","guides/auth"] - subtree filtering
                "docType": "TEXT",  # guide | reference | api | faq | release-notes | policy
                "status": "TEXT",  # published | draft | deprecated | archived
                "locale": "TEXT",  # en, ja, zh
                "product": "TEXT",
                "versions": "JSON",  # ["v7","v8"]
                # Source-side last-modified. Deliberately NOT named `updatedAt`: prepare_document()
                # stamps that with now() on every write, which would overwrite the source's value.
                "sourceUpdatedAt": "INTEGER",  # epoch seconds
                "tags": "JSON",  # {"bug": 0.9706085920333862, "mask": 0.9348311424255371, "glowing": 0.8394700884819031}
                "startedAt": "TIMESTAMP",
                "uploadedAt": "TIMESTAMP",
                # Local search has its own durable desired/actual state. It must not share the
                # Gemini upload error or timestamp: either side can fail while the other works.
                "searchHash": "TEXT",
                "searchIndexedHash": "TEXT",
                "searchStartedAt": "TIMESTAMP",
                "searchIndexedAt": "TIMESTAMP",
                "searchError": "TEXT",
                "metadata": "JSON",
                "error": "TEXT",
                "ref": "TEXT",
            },
        }
        self.fts5_enabled = clone.fts5_enabled if clone else False
        if not clone:
            conn = self.db.create_writer_connection()
            try:
                self.init_db(conn)
                conn.commit()
            finally:
                conn.close()

    def clone(self):
        return GeminiDB(self.ctx, self.db_path, clone=self)

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
        sql_columns = ",".join(
            [f"{col} {overrides.get(col, dtype)}" for col, dtype in self.columns["filestore"].items()]
        )
        self.db.exec(
            conn,
            f"""
            CREATE TABLE IF NOT EXISTS filestore (
                {sql_columns},
                CONSTRAINT uniq_displayname UNIQUE (user,displayName)
            )
            """,
        )
        self.add_missing_columns(conn, "filestore")
        self.db.exec(conn, "CREATE INDEX IF NOT EXISTS idx_filestore_user ON filestore(user)")
        self.db.exec(
            conn,
            "CREATE INDEX IF NOT EXISTS idx_filestore_createdat ON filestore(createdAt)",
        )
        self.db.exec(
            conn,
            "CREATE INDEX IF NOT EXISTS idx_filestore_updatedat ON filestore(updatedAt)",
        )

        sql_columns = ",".join(
            [f"{col} {overrides.get(col, dtype)}" for col, dtype in self.columns["document"].items()]
        )
        self.db.exec(conn, f"CREATE TABLE IF NOT EXISTS document ({sql_columns})")
        self.add_missing_columns(conn, "document")
        self.db.exec(conn, "CREATE INDEX IF NOT EXISTS idx_document_user ON document(user)")
        self.db.exec(
            conn,
            "CREATE INDEX IF NOT EXISTS idx_document_createdat ON document(createdAt)",
        )
        self.db.exec(conn, "CREATE INDEX IF NOT EXISTS idx_document_filestore ON document(filestoreId)")
        self.db.exec(conn, "CREATE INDEX IF NOT EXISTS idx_document_source ON document(sourceId)")
        self.db.exec(conn, "CREATE INDEX IF NOT EXISTS idx_document_category ON document(filestoreId,category)")
        # Identity is the document's stable key within its source and store.
        self.db.exec(
            conn,
            "CREATE UNIQUE INDEX IF NOT EXISTS uniq_document_source_key "
            "ON document(filestoreId, IFNULL(sourceId,0), sourceKey) WHERE sourceKey IS NOT NULL",
        )

        for table in (
            "source", "source_run", "assistant", "assistant_conversation", "assistant_message",
            "search_widget", "search_query", "search_click", "search_page_view", "search_section",
        ):
            cols = ",".join([f"{col} {overrides.get(col, dtype)}" for col, dtype in self.columns[table].items()])
            self.db.exec(conn, f"CREATE TABLE IF NOT EXISTS {table} ({cols})")
            self.add_missing_columns(conn, table)
        self.db.exec(conn, "CREATE INDEX IF NOT EXISTS idx_source_filestore ON source(filestoreId)")
        self.db.exec(conn, "CREATE INDEX IF NOT EXISTS idx_source_run_source ON source_run(sourceId)")
        self.db.exec(conn, "CREATE INDEX IF NOT EXISTS idx_assistant_filestore ON assistant(filestoreId,user)")
        self.db.exec(conn, "CREATE UNIQUE INDEX IF NOT EXISTS uniq_assistant_public ON assistant(publicId)")
        self.db.exec(conn, "CREATE UNIQUE INDEX IF NOT EXISTS uniq_assistant_name "
                           "ON assistant(IFNULL(user,''),filestoreId,name) WHERE enabled != 0")
        self.db.exec(conn, "CREATE INDEX IF NOT EXISTS idx_assistant_conversation ON assistant_conversation(assistantId,updatedAt)")
        self.db.exec(conn, "CREATE UNIQUE INDEX IF NOT EXISTS uniq_assistant_session ON assistant_conversation(assistantId,sessionId)")
        self.db.exec(conn, "CREATE INDEX IF NOT EXISTS idx_assistant_message ON assistant_message(conversationId,id)")
        self.db.exec(conn, "CREATE INDEX IF NOT EXISTS idx_search_widget_filestore ON search_widget(filestoreId,user)")
        self.db.exec(conn, "CREATE UNIQUE INDEX IF NOT EXISTS uniq_search_widget_public ON search_widget(publicId)")
        self.db.exec(conn, "CREATE UNIQUE INDEX IF NOT EXISTS uniq_search_widget_name "
                           "ON search_widget(IFNULL(user,''),filestoreId,name) WHERE enabled != 0")
        self.db.exec(conn, "CREATE INDEX IF NOT EXISTS idx_search_query_widget_created "
                           "ON search_query(searchWidgetId,createdAt)")
        self.db.exec(conn, "CREATE INDEX IF NOT EXISTS idx_search_query_widget_group "
                           "ON search_query(searchWidgetId,groupKey)")
        self.db.exec(conn, "CREATE INDEX IF NOT EXISTS idx_search_click_widget_created "
                           "ON search_click(searchWidgetId,createdAt)")
        self.db.exec(conn, "CREATE INDEX IF NOT EXISTS idx_search_click_query "
                           "ON search_click(searchQueryId)")
        self.db.exec(conn, "CREATE INDEX IF NOT EXISTS idx_search_click_widget_document "
                           "ON search_click(searchWidgetId,documentId)")
        self.db.exec(conn, "CREATE INDEX IF NOT EXISTS idx_search_page_view_widget_created "
                           "ON search_page_view(searchWidgetId,createdAt)")
        self.db.exec(conn, "CREATE INDEX IF NOT EXISTS idx_search_page_view_widget_day "
                           "ON search_page_view(searchWidgetId,dayKey)")
        self.db.exec(conn, "CREATE INDEX IF NOT EXISTS idx_search_page_view_widget_client "
                           "ON search_page_view(searchWidgetId,clientId)")
        self.db.exec(conn, "CREATE INDEX IF NOT EXISTS idx_search_page_view_widget_session "
                           "ON search_page_view(searchWidgetId,sessionId)")
        self.db.exec(conn, "CREATE INDEX IF NOT EXISTS idx_search_section_document ON search_section(documentId)")
        self.db.exec(conn, "CREATE INDEX IF NOT EXISTS idx_search_section_store ON search_section(filestoreId,user)")

        # FTS5 is optional in SQLite builds. The ordinary section table remains the source of
        # truth and the query path falls back to LIKE when this capability probe fails.
        try:
            existed = self.db.one(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='search_section_fts'",
                connection=conn,
            ) is not None
            self.db.exec(conn, """
                CREATE VIRTUAL TABLE IF NOT EXISTS search_section_fts USING fts5(
                    documentTitle, heading, content,
                    content='search_section', content_rowid='id',
                    tokenize='unicode61 remove_diacritics 2'
                )
            """)
            self.db.exec(conn, """
                CREATE TRIGGER IF NOT EXISTS search_section_ai AFTER INSERT ON search_section BEGIN
                    INSERT INTO search_section_fts(rowid,documentTitle,heading,content)
                    VALUES (new.id,new.documentTitle,new.heading,new.content);
                END
            """)
            self.db.exec(conn, """
                CREATE TRIGGER IF NOT EXISTS search_section_ad AFTER DELETE ON search_section BEGIN
                    INSERT INTO search_section_fts(search_section_fts,rowid,documentTitle,heading,content)
                    VALUES ('delete',old.id,old.documentTitle,old.heading,old.content);
                END
            """)
            self.db.exec(conn, """
                CREATE TRIGGER IF NOT EXISTS search_section_au AFTER UPDATE ON search_section BEGIN
                    INSERT INTO search_section_fts(search_section_fts,rowid,documentTitle,heading,content)
                    VALUES ('delete',old.id,old.documentTitle,old.heading,old.content);
                    INSERT INTO search_section_fts(rowid,documentTitle,heading,content)
                    VALUES (new.id,new.documentTitle,new.heading,new.content);
                END
            """)
            self.db.exec(conn, """
                CREATE TRIGGER IF NOT EXISTS document_search_ad AFTER DELETE ON document BEGIN
                    DELETE FROM search_section WHERE documentId = old.id;
                END
            """)
            if not existed:
                self.db.exec(conn, "INSERT INTO search_section_fts(search_section_fts) VALUES('rebuild')")
            self.fts5_enabled = True
        except Exception as e:
            self.fts5_enabled = False
            self.ctx.log(f"gemini: SQLite FTS5 unavailable, using LIKE search ({e})")

    def to_dto(self, row, json_columns):
        return to_dto(self.ctx, row, json_columns)

    def get_user_filter(self, user=None, params=None):
        if user is None:
            return "WHERE user IS NULL", params or {}
        else:
            args = params.copy() if params else {}
            args.update({"user": user})
            return "WHERE user = :user", args

    def sql_filter(self, all_columns, query: Dict[str, Any], args: Dict[str, Any] = None, user=None):
        # always filter by user
        sql_where, params = self.get_user_filter(user, args)

        filter = {}
        for k in query:
            if k in all_columns:
                filter[k] = query[k]
                params[k] = query[k]

        if len(filter) > 0:
            sql_where += " AND " + " AND ".join([f"{k} = :{k}" for k in filter])

        return sql_where, params

    def get_filestore(self, id, user=None):
        try:
            sql_where, params = self.get_user_filter(user, {"id": id})
            return self.db.one(f"SELECT * FROM filestore {sql_where} AND id = :id", params)
        except Exception as e:
            self.ctx.err(f"get_filestore ({id}, {user})", e)
            return None

    def query_filestores(self, query: Dict[str, Any], user=None):
        try:
            table = "filestore"
            columns = self.columns[table]
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
                    sql_where += " AND " + " AND ".join([
                        f"({k} IS NULL OR {k} = '')" if k == "category" else f"{k} IS NULL"
                        for k in cols
                    ])

            if "not_null" in query:
                cols = valid_columns(all_columns, query.get("not_null"))
                if len(cols) > 0:
                    sql_where += " AND " + " AND ".join([f"{k} IS NOT NULL" for k in cols])

            if "q" in query:
                sql_where += " AND " if sql_where else "WHERE "
                sql_where += "(displayName LIKE :q)"
                params["q"] = f"%{query['q']}%"

            if sort == "failed":
                sql_order_by = "ORDER BY CASE WHEN error IS NOT NULL OR failedDocumentsCount > 0 THEN 0 ELSE 1 END, failedDocumentsCount DESC, createdAt DESC"
            else:
                sql_order_by = order_by(all_columns, sort)

            sql = f"{select_columns(all_columns, query.get('fields'), select=query.get('select'))} FROM filestore {sql_where} {sql_order_by} LIMIT :take OFFSET :skip"

            if query.get("as") == "column":
                return self.db.column(sql, params)
            else:
                return self.db.all(sql, params)

        except Exception as e:
            self.ctx.err(f"query_filestores ({take}, {skip})", e)
            return []

    def prepare_filestore(self, filestore, id=None, user=None):
        now = datetime.now()
        if id:
            filestore["id"] = id
        else:
            filestore["createdAt"] = now
        filestore["updatedAt"] = now
        return with_user(filestore, user=user)

    def create_filestore(self, filestore: Dict[str, Any], user=None):
        return self.db.insert(
            "filestore",
            self.columns["filestore"],
            self.prepare_filestore(filestore, user=user),
        )

    async def create_filestore_async(self, filestore: Dict[str, Any], user=None):
        return await self.db.insert_async(
            "filestore",
            self.columns["filestore"],
            self.prepare_filestore(filestore, user=user),
        )

    def update_filestore(self, id, filestore: Dict[str, Any], user=None):
        return self.db.update(
            "filestore",
            self.columns["filestore"],
            self.prepare_filestore(filestore, id, user=user),
        )

    async def update_filestore_async(self, id, filestore: Dict[str, Any], user=None):
        return await self.db.update_async(
            "filestore",
            self.columns["filestore"],
            self.prepare_filestore(filestore, id, user=user),
        )

    def filestore_delete_summary(self, id, user=None, connection=None):
        """Return the complete local impact of permanently deleting a File Store."""
        filestore_id = int(id)
        sql_where, params = self.get_user_filter(user, {"id": filestore_id})
        store = self.db.one(
            f"SELECT id, name, displayName, activeDocumentsCount, pendingDocumentsCount, "
            f"failedDocumentsCount, sizeBytes FROM filestore {sql_where} AND id = :id",
            params,
            connection=connection,
        )
        if not store:
            return None

        impact = self.db.one(
            """
            SELECT
                (SELECT COUNT(*) FROM document d
                    WHERE d.filestoreId = :id
                       OR d.sourceId IN (SELECT id FROM source WHERE filestoreId = :id)) AS documents,
                (SELECT COALESCE(SUM(COALESCE(d.sizeBytes,d.size,0)),0) FROM document d
                    WHERE d.filestoreId = :id
                       OR d.sourceId IN (SELECT id FROM source WHERE filestoreId = :id)) AS documentBytes,
                (SELECT COUNT(*) FROM source WHERE filestoreId = :id) AS savedImports,
                (SELECT COUNT(*) FROM source_run
                    WHERE sourceId IN (SELECT id FROM source WHERE filestoreId = :id)) AS importRuns,
                (SELECT COUNT(*) FROM assistant WHERE filestoreId = :id) AS assistants,
                (SELECT COUNT(*) FROM assistant
                    WHERE filestoreId = :id AND enabled != 0 AND publishedAt IS NOT NULL) AS publishedAssistants,
                (SELECT COUNT(*) FROM assistant_conversation
                    WHERE assistantId IN (SELECT id FROM assistant WHERE filestoreId = :id)) AS conversations,
                (SELECT COUNT(*) FROM assistant_message
                    WHERE conversationId IN (
                        SELECT id FROM assistant_conversation
                        WHERE assistantId IN (SELECT id FROM assistant WHERE filestoreId = :id)
                    )) AS messages,
                (SELECT COUNT(*) FROM search_widget WHERE filestoreId = :id) AS searchWidgets,
                (SELECT COUNT(*) FROM search_widget
                    WHERE filestoreId = :id AND enabled != 0 AND publishedAt IS NOT NULL) AS publishedSearchWidgets,
                (SELECT COUNT(*) FROM search_section WHERE filestoreId = :id) AS searchSections,
                (SELECT COUNT(*) FROM search_query
                    WHERE searchWidgetId IN (SELECT id FROM search_widget WHERE filestoreId = :id)) AS searches,
                (SELECT COUNT(*) FROM search_click
                    WHERE searchWidgetId IN (SELECT id FROM search_widget WHERE filestoreId = :id)) AS searchClicks,
                (SELECT COUNT(*) FROM search_page_view
                    WHERE searchWidgetId IN (SELECT id FROM search_widget WHERE filestoreId = :id)) AS searchPageViews
            """,
            {"id": filestore_id},
            connection=connection,
        ) or {}
        remote_counts = [store.get(key) for key in (
            "activeDocumentsCount", "pendingDocumentsCount", "failedDocumentsCount")]
        remote_documents = sum(int(value or 0) for value in remote_counts)
        return {
            **store,
            **impact,
            "remoteStoreExists": bool(store.get("name")),
            "remoteDocuments": remote_documents,
            "remoteDocumentBytes": int(store.get("sizeBytes") or 0),
        }

    def delete_filestore(self, id, user=None, callback=None):
        """
        Permanently delete the File Store and every record whose identity belongs to it.

        The ownership check and all dependent deletes share one transaction. Relationship
        deletes intentionally follow the File Store id rather than repeating the user filter:
        if an old row has bad ownership metadata, leaving it behind would create exactly the
        orphan and future identity conflict this operation is meant to prevent.
        """
        filestore_id = int(id)
        conn = self.db.create_writer_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            impact = self.filestore_delete_summary(filestore_id, user=user, connection=conn)
            if not impact:
                conn.rollback()
                return None

            params = {"id": filestore_id}
            statements = [
                "DELETE FROM assistant_message WHERE conversationId IN ("
                "SELECT id FROM assistant_conversation WHERE assistantId IN ("
                "SELECT id FROM assistant WHERE filestoreId = :id))",
                "DELETE FROM assistant_conversation WHERE assistantId IN ("
                "SELECT id FROM assistant WHERE filestoreId = :id)",
                "DELETE FROM assistant WHERE filestoreId = :id",
                "DELETE FROM search_click WHERE searchWidgetId IN ("
                "SELECT id FROM search_widget WHERE filestoreId = :id)",
                "DELETE FROM search_page_view WHERE searchWidgetId IN ("
                "SELECT id FROM search_widget WHERE filestoreId = :id)",
                "DELETE FROM search_query WHERE searchWidgetId IN ("
                "SELECT id FROM search_widget WHERE filestoreId = :id)",
                "DELETE FROM search_widget WHERE filestoreId = :id",
                "DELETE FROM search_section WHERE filestoreId = :id",
                "DELETE FROM source_run WHERE sourceId IN (SELECT id FROM source WHERE filestoreId = :id)",
                "DELETE FROM document WHERE filestoreId = :id "
                "OR sourceId IN (SELECT id FROM source WHERE filestoreId = :id)",
                "DELETE FROM source WHERE filestoreId = :id",
                "DELETE FROM filestore WHERE id = :id",
            ]
            for sql in statements:
                self.db.exec(conn, sql, params)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

        if callback:
            callback(None, 1)
        return impact

    # --- Local search index + published Search widgets --------------------------------

    def search_widget_dto(self, row):
        return self.to_dto(row, ["config"]) if row else None

    def get_search_widget(self, id, user=None):
        sql_where, params = self.get_user_filter(user, {"id": int(id)})
        row = self.db.one(
            f"SELECT w.*,(SELECT COUNT(*) FROM search_query q WHERE q.searchWidgetId=w.id) AS searchCount,"
            f"(SELECT COUNT(*) FROM search_page_view p WHERE p.searchWidgetId=w.id) AS pageViewCount "
            f"FROM search_widget w {sql_where} AND id = :id", params)
        return self.search_widget_dto(row)

    def get_public_search_widget(self, public_id):
        row = self.db.one(
            "SELECT * FROM search_widget WHERE publicId = :publicId AND enabled = 1 AND publishedAt IS NOT NULL",
            {"publicId": public_id},
        )
        return self.search_widget_dto(row)

    def query_search_widgets(self, filestore_id, user=None, include_archived=False):
        sql_where, params = self.get_user_filter(user, {"filestoreId": int(filestore_id)})
        archived = "" if include_archived else " AND enabled != 0"
        rows = self.db.all(
            f"SELECT w.*,(SELECT COUNT(*) FROM search_query q WHERE q.searchWidgetId=w.id) AS searchCount,"
            f"(SELECT COUNT(*) FROM search_page_view p WHERE p.searchWidgetId=w.id) AS pageViewCount "
            f"FROM search_widget w {sql_where} AND filestoreId = :filestoreId{archived} "
            "ORDER BY updatedAt DESC,id DESC", params,
        ) or []
        return [self.search_widget_dto(row) for row in rows]

    def search_widget_name_exists(self, filestore_id, name, user=None, exclude_id=None):
        sql_where, params = self.get_user_filter(user, {"filestoreId": int(filestore_id), "name": name})
        sql = (f"SELECT id FROM search_widget {sql_where} AND filestoreId = :filestoreId "
               "AND name = :name AND enabled != 0")
        if exclude_id is not None:
            sql += " AND id != :excludeId"
            params["excludeId"] = int(exclude_id)
        return self.db.one(sql, params) is not None

    async def create_search_widget_async(self, widget, user=None):
        now = datetime.now()
        data = with_user({**widget, "createdAt": now, "updatedAt": now}, user)
        return await self.db.insert_async("search_widget", self.columns["search_widget"], data)

    async def update_search_widget_async(self, id, widget, user=None):
        data = with_user({**widget, "id": int(id), "updatedAt": datetime.now()}, user)
        return await self.db.update_async("search_widget", self.columns["search_widget"], data)

    async def archive_search_widget_async(self, id, user=None):
        if not self.get_search_widget(id, user=user):
            return False
        await self.update_search_widget_async(id, {"enabled": 0, "publishedAt": None}, user=user)
        return True

    async def restore_search_widget_async(self, id, user=None):
        widget = self.get_search_widget(id, user=user)
        if not widget:
            return None
        if self.search_widget_name_exists(
            widget["filestoreId"], widget["name"], user=user, exclude_id=widget["id"]
        ):
            raise ValueError(f"An active Search widget named '{widget['name']}' already exists")
        await self.update_search_widget_async(id, {"enabled": 1, "publishedAt": None}, user=user)
        return self.get_search_widget(id, user=user)

    def delete_search_widget(self, id, user=None, confirmation=None):
        widget = self.get_search_widget(id, user=user)
        if not widget:
            return None
        if confirmation is not None and confirmation != widget["name"]:
            raise ValueError(f'Type "{widget["name"]}" to confirm permanent deletion')
        conn = self.db.create_writer_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            self.db.exec(conn, "DELETE FROM search_click WHERE searchWidgetId=:id", {"id": int(id)})
            self.db.exec(conn, "DELETE FROM search_page_view WHERE searchWidgetId=:id", {"id": int(id)})
            self.db.exec(conn, "DELETE FROM search_query WHERE searchWidgetId=:id", {"id": int(id)})
            sql_where, params = self.get_user_filter(user, {"id": int(id)})
            self.db.exec(conn, f"DELETE FROM search_widget {sql_where} AND id = :id", params)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return widget

    def clear_search_analytics(self, id, user=None, before=None):
        if not self.get_search_widget(id, user=user):
            return None
        conn = self.db.create_writer_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            suffix = " AND createdAt < :before" if before else ""
            params = {"id": int(id), "before": before}
            counts = {}
            for key, table in (("clicks", "search_click"), ("pageViews", "search_page_view"),
                               ("searches", "search_query")):
                cursor = self.db.exec(conn, f"DELETE FROM {table} WHERE searchWidgetId=:id{suffix}", params)
                counts[key] = max(int(cursor.rowcount or 0), 0)
            conn.commit()
            return counts
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    async def record_search_query_async(self, search_widget_id, query, origin=None, page_url=None,
                                        user_agent=None, result_count=0, document_count=0,
                                        duration_ms=0):
        from llms.extensions.gemini import search
        query = str(query or "").strip()[:200]
        normalized = search.normalize_search_query(query)
        return await self.db.insert_async("search_query", self.columns["search_query"], {
            "searchWidgetId": int(search_widget_id),
            "createdAt": datetime.now(),
            "query": query,
            "normalizedQuery": normalized,
            "groupKey": search.search_query_group_key(normalized),
            "origin": str(origin or "")[:500] or None,
            "pageUrl": str(page_url or "")[:2000] or None,
            "userAgent": str(user_agent or "")[:1000] or None,
            "resultCount": max(int(result_count or 0), 0),
            "documentCount": max(int(document_count or 0), 0),
            "durationMs": max(int(duration_ms or 0), 0),
        })

    async def record_search_click_async(self, search_widget_id, filestore_id, search_query_id,
                                        document_id, section_id=None, position=0,
                                        document_title=None, source_url=None, result_type=None,
                                        user=None):
        query = self.db.one(
            "SELECT id FROM search_query WHERE id=:queryId AND searchWidgetId=:widgetId",
            {"queryId": int(search_query_id), "widgetId": int(search_widget_id)},
        )
        document = self.get_document(int(document_id), user=user)
        if (not query or not document
                or int(document.get("filestoreId") or 0) != int(filestore_id)):
            return None
        section = None
        if section_id:
            section = self.db.one(
                "SELECT id,documentId,documentTitle,url,kind FROM search_section "
                "WHERE id=:id AND documentId=:documentId AND filestoreId=:filestoreId",
                {"id": int(section_id), "documentId": int(document_id),
                 "filestoreId": int(filestore_id)},
            )
        title = str((section or {}).get("documentTitle") or document_title
                    or document.get("displayName") or document.get("sourceKey") or "Document")[:500]
        url = str((section or {}).get("url") or document.get("sourceUrl") or source_url or "").strip()
        if not url.startswith(("http://", "https://")):
            url = ""
        kind = str((section or {}).get("kind") or result_type or "content")[:50]
        return await self.db.insert_async("search_click", self.columns["search_click"], {
            "searchQueryId": int(search_query_id),
            "searchWidgetId": int(search_widget_id),
            "documentId": int(document_id),
            "sectionId": int(section_id) if section_id else None,
            "createdAt": datetime.now(),
            "position": min(max(int(position or 0), 1), 1000),
            "documentTitle": title,
            "sourceUrl": url or None,
            "resultType": kind,
        })

    def prepare_search_page_view(self, search_widget_id, values):
        """Normalize one anonymous page view before it enters the write queue."""
        now = datetime.now()

        def text(name, limit):
            value = str((values or {}).get(name) or "").strip()
            return value[:limit] or None

        def integer(name, minimum=0, maximum=1000000):
            try:
                return min(max(int(float((values or {}).get(name) or 0)), minimum), maximum)
            except (TypeError, ValueError):
                return 0

        def number(name, minimum=0, maximum=1000):
            try:
                return min(max(float((values or {}).get(name) or 0), minimum), maximum)
            except (TypeError, ValueError):
                return 0

        return {
            "searchWidgetId": int(search_widget_id),
            "createdAt": now,
            "hourKey": now.strftime("%Y-%m-%dT%H"),
            "dayKey": now.strftime("%Y-%m-%d"),
            "clientId": text("clientId", 100),
            "sessionId": text("sessionId", 100),
            "firstVisit": 1 if (values or {}).get("firstVisit") else 0,
            "origin": text("origin", 500),
            "pageUrl": text("pageUrl", 2000),
            "pagePath": text("pagePath", 2000),
            "pageTitle": text("pageTitle", 500),
            "referrer": text("referrer", 2000),
            "userAgent": text("userAgent", 1000),
            "language": text("language", 50),
            "languages": text("languages", 500),
            "timezone": text("timezone", 100),
            "platform": text("platform", 100),
            "deviceType": text("deviceType", 20),
            "screenWidth": integer("screenWidth", 0, 20000),
            "screenHeight": integer("screenHeight", 0, 20000),
            "viewportWidth": integer("viewportWidth", 0, 20000),
            "viewportHeight": integer("viewportHeight", 0, 20000),
            "devicePixelRatio": number("devicePixelRatio", 0, 20),
            "colorDepth": integer("colorDepth", 0, 128),
            "touchPoints": integer("touchPoints", 0, 100),
            "connectionType": text("connectionType", 50),
            "downlink": number("downlink", 0, 100000),
            "rtt": integer("rtt", 0, 3600000),
            "saveData": 1 if (values or {}).get("saveData") else 0,
            "navigationType": text("navigationType", 50),
            "durationMs": integer("durationMs", 0, 3600000),
            "domContentLoadedMs": integer("domContentLoadedMs", 0, 3600000),
            "loadMs": integer("loadMs", 0, 3600000),
            "utmSource": text("utmSource", 300),
            "utmMedium": text("utmMedium", 300),
            "utmCampaign": text("utmCampaign", 300),
            "utmTerm": text("utmTerm", 300),
            "utmContent": text("utmContent", 300),
        }

    async def record_search_page_view_async(self, search_widget_id, values):
        """Persist one anonymous page view from an analytics-enabled Search deployment."""
        return await self.db.insert_async(
            "search_page_view", self.columns["search_page_view"],
            self.prepare_search_page_view(search_widget_id, values),
        )

    def search_traffic_analytics(self, search_widget_id, user=None, period="30d",
                                 recent_skip=0, recent_take=10):
        if not self.get_search_widget(search_widget_id, user=user):
            return None
        recent_skip = max(int(recent_skip or 0), 0)
        recent_take = min(max(int(recent_take or 10), 1), 100)
        periods = {"1d": (24, "hour"), "7d": (7, "day"),
                   "30d": (30, "day"), "90d": (90, "day")}
        count, bucket = periods.get(str(period or "30d").lower(), periods["30d"])
        now = datetime.now()
        if bucket == "hour":
            end = now.replace(minute=0, second=0, microsecond=0)
            start = end - timedelta(hours=count - 1)
            key_column = "hourKey"
            keys = [(start + timedelta(hours=i)).strftime("%Y-%m-%dT%H") for i in range(count)]
        else:
            end = now.replace(hour=0, minute=0, second=0, microsecond=0)
            start = end - timedelta(days=count - 1)
            key_column = "dayKey"
            keys = [(start + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(count)]
        params = {"id": int(search_widget_id), "since": start}
        totals = self.db.one("""
            SELECT COUNT(*) AS pageViews,COUNT(DISTINCT clientId) AS visitors,
                COUNT(DISTINCT sessionId) AS sessions,AVG(NULLIF(loadMs,0)) AS averageLoadMs,
                SUM(CASE WHEN firstVisit!=0 THEN 1 ELSE 0 END) AS newVisitors
            FROM search_page_view WHERE searchWidgetId=:id AND createdAt>=:since
        """, params) or {}
        session_summary = self.db.one("""
            SELECT COUNT(*) AS sessions,SUM(CASE WHEN views=1 THEN 1 ELSE 0 END) AS bounced
            FROM (SELECT sessionId,COUNT(*) AS views FROM search_page_view
                  WHERE searchWidgetId=:id AND createdAt>=:since AND sessionId IS NOT NULL
                  GROUP BY sessionId)
        """, params) or {}
        timeline_rows = self.db.all(f"""
            SELECT {key_column} AS bucket,COUNT(*) AS pageViews,
                COUNT(DISTINCT clientId) AS visitors,COUNT(DISTINCT sessionId) AS sessions
            FROM search_page_view WHERE searchWidgetId=:id AND createdAt>=:since
            GROUP BY {key_column} ORDER BY {key_column}
        """, params) or []
        timeline_lookup = {row.get("bucket"): row for row in timeline_rows}
        timeline = [{
            "bucket": key,
            "pageViews": int((timeline_lookup.get(key) or {}).get("pageViews") or 0),
            "visitors": int((timeline_lookup.get(key) or {}).get("visitors") or 0),
            "sessions": int((timeline_lookup.get(key) or {}).get("sessions") or 0),
        } for key in keys]

        def distribution(field, take=20, where=""):
            rows = self.db.all(f"""
                SELECT {field} AS value,COUNT(*) AS count FROM search_page_view
                WHERE searchWidgetId=:id AND createdAt>=:since AND {field} IS NOT NULL
                    AND {field}!='' {where}
                GROUP BY {field} ORDER BY count DESC LIMIT :take
            """, {**params, "take": take}) or []
            return [{"value": row.get("value"), "count": int(row.get("count") or 0)} for row in rows]

        top_pages = self.db.all("""
            SELECT pagePath AS path,COUNT(*) AS views,COUNT(DISTINCT clientId) AS visitors
            FROM search_page_view WHERE searchWidgetId=:id AND createdAt>=:since
                AND pagePath IS NOT NULL AND pagePath!=''
            GROUP BY pagePath ORDER BY views DESC LIMIT 50
        """, params) or []

        def most_used_page_value(field):
            rows = self.db.all(f"""
                SELECT pagePath AS path,{field} AS value,COUNT(*) AS usageCount,
                    MAX(createdAt) AS lastSeen
                FROM search_page_view WHERE searchWidgetId=:id AND createdAt>=:since
                    AND pagePath IS NOT NULL AND pagePath!=''
                    AND {field} IS NOT NULL AND {field}!=''
                GROUP BY pagePath,{field}
                ORDER BY pagePath,usageCount DESC,lastSeen DESC,value
            """, params) or []
            values = {}
            for row in rows:
                values.setdefault(row.get("path"), row.get("value"))
            return values

        page_titles = most_used_page_value("pageTitle")
        page_urls = most_used_page_value("pageUrl")
        for page in top_pages:
            page["views"] = int(page.get("views") or 0)
            page["visitors"] = int(page.get("visitors") or 0)
            page["title"] = page_titles.get(page.get("path"))
            page["url"] = page_urls.get(page.get("path"))
        recent_page_views = self.db.all("""
            SELECT createdAt,pageUrl,pagePath,pageTitle
            FROM search_page_view WHERE searchWidgetId=:id AND createdAt>=:since
            ORDER BY createdAt DESC,id DESC LIMIT :take OFFSET :skip
        """, {**params, "take": recent_take, "skip": recent_skip}) or []
        page_views = int(totals.get("pageViews") or 0)
        sessions = int(totals.get("sessions") or 0)
        bounced = int(session_summary.get("bounced") or 0)
        return {
            "period": period if period in periods else "30d",
            "bucket": bucket,
            "from": start.isoformat(),
            "to": now.isoformat(),
            "pageViews": page_views,
            "visitors": int(totals.get("visitors") or 0),
            "newVisitors": int(totals.get("newVisitors") or 0),
            "sessions": sessions,
            "pagesPerSession": round(page_views / sessions, 2) if sessions else 0,
            "bounceRate": round(bounced * 100 / sessions, 1) if sessions else 0,
            "averageLoadMs": round(float(totals.get("averageLoadMs") or 0)),
            "recentTotal": page_views,
            "recentSkip": recent_skip,
            "recentTake": recent_take,
            "recentPageViews": recent_page_views,
            "timeline": timeline,
            "topPages": top_pages,
            "topReferrers": distribution("referrer"),
            "languages": distribution("language"),
            "timezones": distribution("timezone"),
            "devices": distribution("deviceType"),
            "platforms": distribution("platform"),
            "connections": distribution("connectionType"),
            "campaigns": distribution("utmCampaign"),
        }

    def search_analytics(self, search_widget_id, user=None, group_take=50, recent_take=100):
        widget = self.get_search_widget(search_widget_id, user=user)
        if not widget:
            return None
        group_take = min(max(int(group_take), 1), 200)
        recent_take = min(max(int(recent_take), 1), 500)
        params = {"id": int(search_widget_id)}
        variants = self.db.all("""
            SELECT groupKey,normalizedQuery,MIN(query) AS query,COUNT(*) AS frequency,
                SUM(CASE WHEN resultCount=0 THEN 1 ELSE 0 END) AS noResultCount,
                SUM(resultCount) AS resultTotal,MAX(createdAt) AS lastSearchedAt
            FROM search_query WHERE searchWidgetId=:id
            GROUP BY groupKey,normalizedQuery
            ORDER BY frequency DESC,lastSearchedAt DESC
        """, params) or []
        related = {}
        for variant in variants:
            key = variant.get("groupKey") or variant.get("normalizedQuery") or variant.get("query") or ""
            group = related.setdefault(key, {
                "key": key, "query": variant.get("query"), "count": 0, "noResultCount": 0,
                "resultTotal": 0, "lastSearchedAt": variant.get("lastSearchedAt"), "variants": [],
            })
            frequency = int(variant.get("frequency") or 0)
            group["count"] += frequency
            group["noResultCount"] += int(variant.get("noResultCount") or 0)
            group["resultTotal"] += int(variant.get("resultTotal") or 0)
            if variant.get("lastSearchedAt") and (not group["lastSearchedAt"]
                    or variant["lastSearchedAt"] > group["lastSearchedAt"]):
                group["lastSearchedAt"] = variant["lastSearchedAt"]
            group["variants"].append({"query": variant.get("query"), "count": frequency})
        groups = list(related.values())
        for group in groups:
            group["variants"].sort(key=lambda x: (-x["count"], str(x["query"] or "")))
            group["query"] = group["variants"][0]["query"] if group["variants"] else group["query"]
            group["averageResults"] = round(group.pop("resultTotal") / group["count"], 1) if group["count"] else 0
        groups.sort(key=lambda x: str(x["lastSearchedAt"] or ""), reverse=True)
        groups.sort(key=lambda x: x["count"], reverse=True)
        groups = groups[:group_take]
        group_clicks = {
            row.get("groupKey") or "": row
            for row in (self.db.all("""
                SELECT q.groupKey,COUNT(c.id) AS clickCount,
                    COUNT(DISTINCT c.searchQueryId) AS clickedSearches
                FROM search_click c
                INNER JOIN search_query q ON q.id=c.searchQueryId
                WHERE c.searchWidgetId=:id
                GROUP BY q.groupKey
            """, params) or [])
        }
        for group in groups:
            clicks = group_clicks.get(group["key"], {})
            group["clickCount"] = int(clicks.get("clickCount") or 0)
            group["clickedSearches"] = int(clicks.get("clickedSearches") or 0)
            group["clickThroughRate"] = round(
                group["clickedSearches"] * 100 / group["count"], 1) if group["count"] else 0
        recent = self.db.all("""
            SELECT id,query,createdAt,resultCount,documentCount,durationMs,origin,pageUrl
            FROM search_query WHERE searchWidgetId=:id ORDER BY createdAt DESC,id DESC LIMIT :take
        """, {**params, "take": recent_take}) or []
        total = sum(int(x.get("frequency") or 0) for x in variants)
        no_results = sum(int(x.get("noResultCount") or 0) for x in variants)
        result_total = sum(int(x.get("resultTotal") or 0) for x in variants)
        click_summary = self.db.one("""
            SELECT COUNT(*) AS totalClicks,COUNT(DISTINCT searchQueryId) AS clickedSearches
            FROM search_click WHERE searchWidgetId=:id
        """, params) or {}
        total_clicks = int(click_summary.get("totalClicks") or 0)
        clicked_searches = int(click_summary.get("clickedSearches") or 0)
        popular_documents = self.db.all("""
            SELECT documentId,MAX(documentTitle) AS title,MAX(sourceUrl) AS sourceUrl,
                COUNT(*) AS clickCount,COUNT(DISTINCT searchQueryId) AS uniqueSearches,
                AVG(position) AS averagePosition,MAX(createdAt) AS lastClickedAt
            FROM search_click WHERE searchWidgetId=:id
            GROUP BY documentId ORDER BY clickCount DESC,lastClickedAt DESC LIMIT 50
        """, params) or []
        for document in popular_documents:
            document["clickCount"] = int(document.get("clickCount") or 0)
            document["uniqueSearches"] = int(document.get("uniqueSearches") or 0)
            document["averagePosition"] = round(float(document.get("averagePosition") or 0), 1)
        return {
            "total": total,
            "uniqueQueries": len(variants),
            "relatedGroups": len(related),
            "noResults": no_results,
            "averageResults": round(result_total / total, 1) if total else 0,
            "totalClicks": total_clicks,
            "clickedSearches": clicked_searches,
            "clickThroughRate": round(clicked_searches * 100 / total, 1) if total else 0,
            "popularDocuments": popular_documents,
            "groups": groups,
            "recent": recent,
        }

    def replace_search_sections(self, document, sections, desired_hash):
        """Atomically replace one document's rows and advance its durable indexed hash."""
        doc_id = int(document["id"])
        user = document.get("user")
        conn = self.db.create_writer_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            self.db.exec(conn, "UPDATE document SET searchStartedAt=:now,searchError=NULL WHERE id=:id", {
                "now": datetime.now(), "id": doc_id,
            })
            self.db.exec(conn, "DELETE FROM search_section WHERE documentId=:id", {"id": doc_id})
            columns = self.columns["search_section"].keys()
            insert_columns = [c for c in columns if c != "id"]
            sql = (f"INSERT INTO search_section ({','.join(insert_columns)}) VALUES "
                   f"({','.join(':'+c for c in insert_columns)})")
            for section in sections:
                row = {
                    **section,
                    "documentId": doc_id,
                    "filestoreId": document.get("filestoreId"),
                    "user": user,
                    "category": document.get("category"),
                    "docType": document.get("docType"),
                    "status": document.get("status"),
                    "locale": document.get("locale"),
                    "product": document.get("product"),
                    "versions": document.get("versions"),
                    "tags": document.get("tags"),
                }
                for key in ("hierarchy", "versions", "tags"):
                    if isinstance(row.get(key), (list, dict)):
                        row[key] = json.dumps(row[key], separators=(",", ":"))
                self.db.exec(conn, sql, {c: row.get(c) for c in insert_columns})
            now = datetime.now()
            self.db.exec(conn, """
                UPDATE document SET searchHash=:hash,searchIndexedHash=:hash,
                    searchIndexedAt=:now,searchStartedAt=NULL,searchError=NULL WHERE id=:id
            """, {"hash": desired_hash, "now": now, "id": doc_id})
            conn.commit()
            return len(sections)
        except Exception as e:
            conn.rollback()
            try:
                self.db.write(
                    "UPDATE document SET searchHash=:hash,searchStartedAt=NULL,searchError=:error WHERE id=:id",
                    {"hash": desired_hash, "error": str(e)[:4000], "id": doc_id},
                )
            except Exception:
                pass
            raise
        finally:
            conn.close()

    def mark_search_started(self, document_id):
        """Record an in-flight attempt synchronously so completion cannot be reordered before it."""
        conn = self.db.create_writer_connection()
        try:
            self.db.exec(conn, """
                UPDATE document SET searchStartedAt=:now,searchError=NULL WHERE id=:id
            """, {"now": datetime.now(), "id": int(document_id)})
            conn.commit()
        finally:
            conn.close()

    def pending_search_documents(self, filestore_id=None, user=None, limit=100):
        sql_where, params = self.get_user_filter(user, {})
        sql_where += " AND tombstonedAt IS NULL AND searchHash IS NOT NULL "
        sql_where += "AND (searchIndexedHash IS NULL OR searchIndexedHash != searchHash)"
        if filestore_id is not None:
            sql_where += " AND filestoreId=:filestoreId"
            params["filestoreId"] = int(filestore_id)
        return self.db.all(
            f"SELECT * FROM document {sql_where} ORDER BY id LIMIT :take",
            {**params, "take": min(max(int(limit), 1), 1000)},
        ) or []

    def get_search_candidates(self, limit=100):
        """Worker queue across user partitions, including pre-search-schema documents."""
        return self.db.all(
            """SELECT * FROM document WHERE tombstonedAt IS NULL
               AND (searchHash IS NULL OR searchIndexedHash IS NULL OR searchIndexedHash != searchHash)
               ORDER BY id LIMIT :take""",
            {"take": min(max(int(limit), 1), 1000)},
        ) or []

    def remove_search_document(self, document_id):
        self.db.write("DELETE FROM search_section WHERE documentId=:id", {"id": int(document_id)})

    def search_stats(self, filestore_id, user=None):
        sql_where, params = self.get_user_filter(user, {"id": int(filestore_id)})
        docs = self.db.one(
            f"""SELECT COUNT(*) AS documents,
                SUM(CASE WHEN searchIndexedHash=searchHash AND searchHash IS NOT NULL THEN 1 ELSE 0 END) AS indexed,
                SUM(CASE WHEN searchHash IS NOT NULL AND (searchIndexedHash IS NULL OR searchIndexedHash!=searchHash) THEN 1 ELSE 0 END) AS pending,
                SUM(CASE WHEN searchError IS NOT NULL THEN 1 ELSE 0 END) AS failed,
                SUM(CASE WHEN searchIndexedHash IS NOT NULL AND searchHash IS NOT NULL AND searchIndexedHash!=searchHash THEN 1 ELSE 0 END) AS stale,
                MAX(searchIndexedAt) AS lastIndexedAt,
                MIN(CASE WHEN searchHash IS NOT NULL AND (searchIndexedHash IS NULL OR searchIndexedHash!=searchHash) THEN COALESCE(searchStartedAt,createdAt) END) AS oldestPendingAt
                FROM document {sql_where} AND filestoreId=:id AND tombstonedAt IS NULL""",
            params,
        ) or {}
        errors = self.db.all(
            f"""SELECT id AS documentId,COALESCE(displayName,sourceKey) AS name,searchError AS error,COALESCE(searchStartedAt,createdAt) AS updatedAt
                FROM document {sql_where} AND filestoreId=:id AND tombstonedAt IS NULL
                AND searchError IS NOT NULL ORDER BY COALESCE(searchStartedAt,createdAt) DESC LIMIT 5""", params) or []
        sections = self.db.scalar(
            f"SELECT COUNT(*) FROM search_section {sql_where} AND filestoreId=:id", params,
        ) or 0
        return {
            **{key: (0 if value is None and key in ("documents", "indexed", "pending", "failed", "stale") else value)
               for key, value in docs.items()},
            "sections": int(sections),
            "provider": "sqlite-fts5" if self.fts5_enabled else "sqlite-like",
            "errors": errors,
        }

    @staticmethod
    def _search_scope(scope, alias="s"):
        clauses, params = [], {}
        for field in ("category", "docType", "status", "locale", "product"):
            value = (scope or {}).get(field)
            if value in (None, ""):
                continue
            clauses.append(f"{alias}.{field} = :scope_{field}")
            params[f"scope_{field}"] = value
        for field in ("versions", "tags"):
            value = (scope or {}).get(field)
            if value in (None, ""):
                continue
            clauses.append(
                f"EXISTS (SELECT 1 FROM json_each({alias}.{field}) WHERE value = :scope_{field})"
            )
            params[f"scope_{field}"] = value
        return clauses, params

    def search_sections(self, filestore_id, query, user=None, scope=None, take=30, ranking=None, skip=0):
        """Search via FTS5, then degrade to bounded LIKE for missing/short-token results."""
        text = str(query or "").strip()[:200]
        take = min(max(int(take), 1), 101)
        skip = min(max(int(skip), 0), 1000)
        if not text:
            return []
        user_clause = "s.user IS NULL" if user is None else "s.user = :user"
        candidate_take = 1000
        params = {"filestoreId": int(filestore_id), "take": candidate_take}
        if user is not None:
            params["user"] = user
        scope_clauses, scope_params = self._search_scope(scope)
        params.update(scope_params)
        base = ["s.filestoreId=:filestoreId", user_clause, *scope_clauses]

        rows = []
        tokens = re.findall(r"[\w]+", text, flags=re.UNICODE)
        if self.fts5_enabled and tokens:
            expression = " AND ".join(f'"{token.replace(chr(34), chr(34)*2)}"*' for token in tokens[:10])
            fts_params = {**params, "query": expression}
            try:
                rows = self.db.all(f"""
                    SELECT s.*,bm25(search_section_fts,8.0,5.0,1.0) AS score,
                        snippet(search_section_fts,2,char(1),char(2),'…',24) AS snippet
                    FROM search_section_fts
                    JOIN search_section s ON s.id=search_section_fts.rowid
                    WHERE search_section_fts MATCH :query AND {' AND '.join(base)}
                    ORDER BY score,s.documentId,s.ordinal LIMIT :take
                """, fts_params) or []
            except Exception as e:
                self.ctx.err("FTS5 search failed; falling back to LIKE", e)

        # Native tokenizers intentionally omit some short technical terms. LIKE is also the
        # capability fallback, so use it when FTS returns nothing rather than hiding valid rows.
        if not rows:
            like_clauses = []
            for i, token in enumerate(tokens[:10] or [text]):
                key = f"term_{i}"
                escaped = token.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                params[key] = f"%{escaped}%"
                like_clauses.append(
                    f"(s.documentTitle LIKE :{key} ESCAPE '\\' OR s.heading LIKE :{key} ESCAPE '\\' "
                    f"OR s.content LIKE :{key} ESCAPE '\\')"
                )
            rows = self.db.all(f"""
                SELECT s.*,0.0 AS score,s.content AS snippet FROM search_section s
                WHERE {' AND '.join(base + like_clauses)}
                ORDER BY CASE WHEN s.documentTitle LIKE :title THEN 0
                              WHEN s.heading LIKE :title THEN 1 ELSE 2 END,
                         s.documentId,s.ordinal LIMIT :take
            """, {**params, "title": f"%{text}%"}) or []

        if not rows:
            return []
        document_ids = sorted({int(row.get("documentId") or 0) for row in rows if row.get("documentId")})
        documents = {}
        if document_ids:
            found = self.db.all(
                f"SELECT id,sourceUpdatedAt,uploadedAt,createdAt,docType FROM document WHERE id IN ({','.join(map(str, document_ids))})"
            ) or []
            documents = {int(row["id"]): row for row in found}
        from llms.extensions.gemini import search
        return search.rank_results(rows, text, documents, ranking)[skip:skip + take]

    # --- Published assistants ---------------------------------------------------------

    def assistant_dto(self, row):
        return self.to_dto(row, ["config"]) if row else None

    def get_assistant(self, id, user=None):
        sql_where, params = self.get_user_filter(user, {"id": int(id)})
        return self.assistant_dto(self.db.one(f"SELECT * FROM assistant {sql_where} AND id = :id", params))

    def get_public_assistant(self, public_id):
        row = self.db.one(
            "SELECT * FROM assistant WHERE publicId = :publicId AND enabled = 1 AND publishedAt IS NOT NULL",
            {"publicId": public_id},
        )
        return self.assistant_dto(row)

    def query_assistants(self, filestore_id, user=None, include_archived=False):
        sql_where, params = self.get_user_filter(user, {"filestoreId": int(filestore_id)})
        archived = "" if include_archived else " AND enabled != 0"
        rows = self.db.all(
            f"SELECT a.*,(SELECT COUNT(*) FROM assistant_conversation c "
            f"WHERE c.assistantId = a.id) AS conversationCount FROM assistant a {sql_where} "
            f"AND filestoreId = :filestoreId{archived} ORDER BY updatedAt DESC,id DESC", params,
        )
        return [self.assistant_dto(row) for row in rows]

    def assistant_name_exists(self, filestore_id, name, user=None, exclude_id=None):
        sql_where, params = self.get_user_filter(user, {"filestoreId": int(filestore_id), "name": name})
        sql = (f"SELECT id FROM assistant {sql_where} AND filestoreId = :filestoreId "
               "AND name = :name AND enabled != 0")
        if exclude_id is not None:
            sql += " AND id != :excludeId"
            params["excludeId"] = int(exclude_id)
        return self.db.one(sql, params) is not None

    async def create_assistant_async(self, assistant, user=None):
        now = datetime.now()
        data = with_user({**assistant, "createdAt": now, "updatedAt": now}, user)
        return await self.db.insert_async("assistant", self.columns["assistant"], data)

    async def update_assistant_async(self, id, assistant, user=None):
        data = with_user({**assistant, "id": int(id), "updatedAt": datetime.now()}, user)
        return await self.db.update_async("assistant", self.columns["assistant"], data)

    async def archive_assistant_async(self, id, user=None):
        assistant = self.get_assistant(id, user=user)
        if not assistant:
            return False
        await self.update_assistant_async(id, {"enabled": 0, "publishedAt": None}, user=user)
        return True

    async def restore_assistant_async(self, id, user=None):
        """Restore an archived Assistant as an unpublished draft."""
        assistant = self.get_assistant(id, user=user)
        if not assistant:
            return None
        if self.assistant_name_exists(
            assistant["filestoreId"], assistant["name"], user=user, exclude_id=assistant["id"]
        ):
            raise ValueError(f"An active Assistant named '{assistant['name']}' already exists")
        await self.update_assistant_async(id, {"enabled": 1, "publishedAt": None}, user=user)
        return self.get_assistant(id, user=user)

    def assistant_delete_summary(self, id, user=None, connection=None):
        """Describe all retained data and referring websites affected by permanent deletion."""
        assistant_id = int(id)
        sql_where, params = self.get_user_filter(user, {"id": assistant_id})
        assistant = self.db.one(
            f"SELECT id, name, publicId, enabled, publishedAt FROM assistant "
            f"{sql_where} AND id = :id",
            params,
            connection=connection,
        )
        if not assistant:
            return None

        conversations = self.db.all(
            "SELECT origin, pageUrl, createdAt, updatedAt FROM assistant_conversation "
            "WHERE assistantId = :assistantId",
            {"assistantId": assistant_id},
            connection=connection,
        ) or []
        messages = self.db.scalar(
            "SELECT COUNT(*) FROM assistant_message WHERE conversationId IN ("
            "SELECT id FROM assistant_conversation WHERE assistantId = :assistantId)",
            {"assistantId": assistant_id},
            connection=connection,
        ) or 0

        domains = {}
        unknown = 0
        for conversation in conversations:
            domain = referrer_domain(conversation.get("origin"), conversation.get("pageUrl"))
            if not domain:
                unknown += 1
                continue
            used_at = timestamp_text(conversation.get("updatedAt") or conversation.get("createdAt"))
            current = domains.setdefault(domain, {
                "domain": domain,
                "conversationCount": 0,
                "lastUsedAt": used_at,
            })
            current["conversationCount"] += 1
            if used_at and (not current.get("lastUsedAt") or str(used_at) > str(current["lastUsedAt"])):
                current["lastUsedAt"] = used_at

        referrers = sorted(
            domains.values(),
            key=lambda item: (str(item.get("lastUsedAt") or ""), item["domain"]),
            reverse=True,
        )
        return {
            **assistant,
            "published": bool(assistant.get("enabled") and assistant.get("publishedAt")),
            "conversations": len(conversations),
            "messages": int(messages),
            "referrers": referrers,
            "unknownReferrerConversations": unknown,
        }

    def delete_assistant(self, id, user=None, confirmation=None):
        """Transactionally delete an Assistant, its conversations, and every message."""
        assistant_id = int(id)
        conn = self.db.create_writer_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            impact = self.assistant_delete_summary(assistant_id, user=user, connection=conn)
            if not impact:
                conn.rollback()
                return None
            if confirmation is not None and confirmation != impact["name"]:
                raise ValueError(f'Type "{impact["name"]}" to confirm permanent deletion')
            params = {"assistantId": assistant_id}
            self.db.exec(conn,
                "DELETE FROM assistant_message WHERE conversationId IN "
                "(SELECT id FROM assistant_conversation WHERE assistantId = :assistantId)", params)
            self.db.exec(conn,
                "DELETE FROM assistant_conversation WHERE assistantId = :assistantId", params)
            self.db.exec(conn, "DELETE FROM assistant WHERE id = :assistantId", params)
            conn.commit()
            return impact
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    async def delete_assistant_async(self, id, user=None, confirmation=None):
        # Kept async for the route contract; the short transaction itself must remain indivisible.
        return self.delete_assistant(id, user=user, confirmation=confirmation)

    def query_assistant_conversations(self, assistant_id, user=None, take=100):
        assistant = self.get_assistant(assistant_id, user=user)
        if not assistant:
            return []
        return self.db.all(
            "SELECT c.*,(SELECT COUNT(*) FROM assistant_message m "
            "WHERE m.conversationId = c.id AND m.role = 'user') AS userMessageCount "
            "FROM assistant_conversation c WHERE c.assistantId = :assistantId "
            "ORDER BY c.updatedAt DESC,c.id DESC LIMIT :take",
            {"assistantId": int(assistant_id), "take": min(max(int(take), 1), 500)},
        )

    def get_assistant_conversation(self, conversation_id, assistant_id=None, user=None):
        params = {"id": int(conversation_id)}
        sql = "SELECT * FROM assistant_conversation WHERE id = :id"
        if assistant_id is not None:
            if not self.get_assistant(assistant_id, user=user):
                return None
            sql += " AND assistantId = :assistantId"
            params["assistantId"] = int(assistant_id)
        return self.db.one(sql, params)

    def find_assistant_conversation(self, assistant_id, session_id):
        return self.db.one(
            "SELECT * FROM assistant_conversation WHERE assistantId = :assistantId AND sessionId = :sessionId",
            {"assistantId": int(assistant_id), "sessionId": session_id},
        )

    async def create_assistant_conversation_async(self, assistant, session_id, origin, page_url, user_agent):
        now = datetime.now()
        return await self.db.insert_async("assistant_conversation", self.columns["assistant_conversation"], {
            "assistantId": assistant["id"], "user": assistant.get("user"), "createdAt": now, "updatedAt": now,
            "sessionId": session_id, "origin": origin, "pageUrl": page_url, "userAgent": user_agent,
            "status": "open", "messageCount": 0,
        })

    def query_assistant_messages(self, conversation_id):
        rows = self.db.all(
            "SELECT * FROM assistant_message WHERE conversationId = :conversationId ORDER BY id",
            {"conversationId": int(conversation_id)},
        )
        return [self.to_dto(row, ["citations"]) for row in rows]

    async def add_assistant_message_async(self, conversation, role, content, citations=None, error=None):
        message_id = await self.db.insert_async("assistant_message", self.columns["assistant_message"], {
            "conversationId": conversation["id"], "createdAt": datetime.now(), "role": role,
            "content": content, "citations": citations or [], "error": error,
        })
        count = int(conversation.get("messageCount") or 0) + 1
        title = conversation.get("title") or (content[:100] if role == "user" else None)
        await self.db.update_async("assistant_conversation", self.columns["assistant_conversation"], {
            "id": conversation["id"], "updatedAt": datetime.now(), "title": title,
            "messageCount": count, "lastMessage": content[:500],
        })
        conversation.update({"messageCount": count, "title": title, "lastMessage": content[:500]})
        return message_id

    def document_filter(self, query: Dict[str, Any], args=None, user=None):
        """
        The WHERE clause for a document query, and its params.

        Shared by the row query and the count so the two can never describe different sets - a
        page count derived from anything but the query it pages is a bug waiting to be reported.
        """
        try:
            all_columns = self.columns["document"].keys()
            # List facets are JSON arrays in SQLite. They need membership checks below; allowing
            # sql_filter() to treat them as scalar columns produces `tags = 'redis'`, which can
            # never match the stored `["redis"]`.
            list_columns = ("categoryPath", "versions", "tags")
            scalar_columns = [c for c in all_columns if c not in list_columns]
            scalar_query = dict(query)
            # Root-category documents exist in two representations: older imports wrote an empty
            # string while current imports write NULL. Treat an explicit empty category as the
            # same virtual `(uncategorised)` facet as `?null=category`.
            uncategorised = scalar_query.get("category") == ""
            if uncategorised:
                scalar_query.pop("category")
            sql_where, params = self.sql_filter(scalar_columns, scalar_query, args=dict(args or {}), user=user)

            for field in list_columns:
                value = query.get(field)
                if value is None or value == "":
                    continue
                values = value if isinstance(value, list) else [value]
                for i, item in enumerate(values):
                    param = f"json_{field}_{i}"
                    sql_where += (
                        f" AND EXISTS (SELECT 1 FROM json_each(document.{field}) AS item "
                        f"WHERE item.value = :{param})"
                    )
                    params[param] = item

            if "null" in query:
                cols = valid_columns(all_columns, query["null"])
                if len(cols) > 0:
                    uncategorised = uncategorised or "category" in cols
                    cols = [k for k in cols if k != "category"]
                    if cols:
                        sql_where += " AND " + " AND ".join([f"{k} IS NULL" for k in cols])

            if uncategorised:
                sql_where += " AND (category IS NULL OR category = '')"

            if "not_null" in query:
                cols = valid_columns(all_columns, query.get("not_null"))
                if len(cols) > 0:
                    sql_where += " AND " + " AND ".join([f"{k} IS NOT NULL" for k in cols])

            # Subtree scope, for searching from a folder downwards. `categoryPath` already holds
            # every ancestor of a document's category, so containment is the whole test - no LIKE
            # against the path, and no chance of 'guides' matching 'guides-internal'.
            under = query.get("categoryUnder")
            if under:
                sql_where += (
                    " AND EXISTS (SELECT 1 FROM json_each(document.categoryPath) WHERE value = :categoryUnder)"
                )
                params["categoryUnder"] = under

            ids_in = query.get("ids_in")
            if ids_in:
                ids = to_ints(ids_in)
                id_params = {}
                if len(ids) > 0:
                    i = 0
                    for id in ids:
                        id_params[f"id_{i}"] = id
                        i = i + 1
                    sql_where += " AND id IN (" + ",".join([f":{p}" for p in id_params]) + ")"
                    params.update(id_params)

            displaynames_in = query.get("displayNames")
            if displaynames_in:
                names = displaynames_in if isinstance(displaynames_in, list) else displaynames_in.split(",")
                name_params = {}
                if len(names) > 0:
                    i = 0
                    for name in names:
                        name_params[f"name_{i}"] = name
                        i = i + 1
                    sql_where += " AND displayName IN (" + ",".join([f":{p}" for p in name_params]) + ")"
                    params.update(name_params)

            if "q" in query:
                sql_where += " AND " if sql_where else "WHERE "
                sql_where += "(displayName LIKE :q)"
                params["q"] = f"%{query['q']}%"

            return sql_where, params
        except Exception as e:
            self.ctx.err("document_filter", e)
            return None, None

    def count_documents(self, query: Dict[str, Any], user=None):
        """How many documents this exact query matches, for paging and 'select all matching'."""
        sql_where, params = self.document_filter(query, user=user)
        if sql_where is None:
            return 0
        try:
            row = self.db.one(f"SELECT COUNT(*) AS count FROM document {sql_where}", params)
            return (row or {}).get("count", 0) if isinstance(row, dict) else (row[0] if row else 0)
        except Exception as e:
            self.ctx.err("count_documents", e)
            return 0

    def query_documents(self, query: Dict[str, Any], user=None):
        try:
            all_columns = self.columns["document"].keys()
            take = min(int(query.get("take", "50")), 1000)
            skip = int(query.get("skip", "0"))
            sort = query.get("sort", "-id")

            sql_where, params = self.document_filter(query, args={"take": take, "skip": skip}, user=user)
            if sql_where is None:
                return []

            if sort == "uploading":
                sql_order_by = f"ORDER BY CASE WHEN uploadedAt IS NULL AND error IS NULL THEN createdAt ELSE '{MAX_DATE}' END, uploadedAt DESC"
            elif sort == "failed":
                sql_order_by = (
                    f"ORDER BY CASE WHEN error IS NOT NULL THEN createdAt ELSE '{MIN_DATE}' END DESC, uploadedAt DESC"
                )
            elif sort == "issues":
                sql_order_by = f"ORDER BY CASE WHEN state IN ('STATE_UNSPECIFIED','STATE_PENDING','MISSING_METADATA','DUPLICATE_HASH','MISSING_FROM_REMOTE','METADATA_MISMATCH') THEN uploadedAt ELSE '{MIN_DATE}' END DESC, uploadedAt DESC"
            else:
                sql_order_by = order_by(all_columns, sort)

            sql = f"{select_columns(all_columns, query.get('fields'), select=query.get('select'))} FROM document {sql_where} {sql_order_by} LIMIT :take OFFSET :skip"

            if query.get("as") == "column":
                return self.db.column(sql, params)
            else:
                return self.db.all(sql, params)

        except Exception as e:
            self.ctx.err(f"query_documents ({take}, {skip})", e)
            return []

    def query_documents_all(self, query: Dict[str, Any], user=None):
        """Generator that yields all documents by paginating through query_documents"""
        skip = 0
        page_size = 1000
        query_copy = query.copy()
        query_copy["take"] = page_size

        while True:
            query_copy["skip"] = skip
            docs = self.query_documents(query_copy, user=user)

            if not docs:
                break

            yield from docs

            # If we got fewer than page_size, we've reached the end
            if len(docs) < page_size:
                break

            skip += page_size

    def get_document(self, id, user=None):
        sql_where, params = self.get_user_filter(user, {"id": id})
        return self.db.one(f"SELECT * FROM document {sql_where} AND id = :id", params)

    def find_document(self, query, user=None):
        sql_where, params = self.sql_filter(self.columns["document"].keys(), query, user=user)
        return self.db.one(f"SELECT * FROM document {sql_where} LIMIT 1", params)

    def prepare_document(self, document, id=None, user=None):
        now = datetime.now()
        if id:
            document["id"] = id
        else:
            document["createdAt"] = now
        document["updatedAt"] = now
        return with_user(document, user=user)

    def create_document(self, document: Dict[str, Any], user=None, callback=None):
        return self.db.insert(
            "document",
            self.columns["document"],
            self.prepare_document(document, user=user),
            callback=callback,
        )

    async def create_document_async(self, document: Dict[str, Any], user=None):
        return await self.db.insert_async(
            "document",
            self.columns["document"],
            self.prepare_document(document, user=user),
        )

    def update_document(self, id, document: Dict[str, Any], user=None):
        return self.db.update(
            "document",
            self.columns["document"],
            self.prepare_document(document, id, user=user),
        )

    async def update_document_async(self, id, document: Dict[str, Any], user=None):
        return await self.db.update_async(
            "document",
            self.columns["document"],
            self.prepare_document(document, id, user=user),
        )

    def get_pending_documents(self, limit=10):
        return self.db.all(
            f"SELECT * FROM document WHERE uploadedAt IS NULL AND error IS NULL "
            f"AND tombstonedAt IS NULL ORDER BY id LIMIT {limit}"
        )

    def delete_document(self, id, user=None, callback=None):
        sql_where, params = self.get_user_filter(user, {"id": id})
        self.db.write(f"DELETE FROM document {sql_where} AND id = :id", params, callback)

    def document_categories(self, id, user=None):
        sql_where, params = self.get_user_filter(user, {"id": id})
        sql_where += " AND filestoreId = :id AND tombstonedAt IS NULL"
        return self.db.all(
            f"SELECT IFNULL(NULLIF(category, ''), '') AS category, COUNT(*) as count, SUM(size) AS size "
            f"FROM document {sql_where} GROUP BY IFNULL(NULLIF(category, ''), '') ORDER BY category",
            params,
        )

    # Columns that may be surfaced as facets. `category` is handled separately because it's a
    # path and needs a tree with rollup counts; the JSON ones need json_each to unnest.
    FACET_COLUMNS = ("category", "docType", "status", "locale", "product", "versions", "tags", "categoryPath")
    JSON_FACET_COLUMNS = ("versions", "tags", "categoryPath")

    def document_facets(self, id, fields=None, user=None):
        """
        Distinct values with counts for each requested column, derived from the documents
        themselves rather than from a declared schema (METADATA_SCHEMA.md §5).

        Returns { field: { "values": [{value, count}], "null": n } }. `category` additionally
        gets a `tree` with own/total counts, because once categories are paths a flat GROUP BY
        produces hundreds of rows and a parent whose documents all live in subfolders reads as
        empty.
        """
        fields = [f for f in (fields or self.FACET_COLUMNS) if f in self.FACET_COLUMNS]

        def where(alias=""):
            p = f"{alias}." if alias else ""
            if user is None:
                w = f"WHERE {p}user IS NULL"
                args = {"id": id}
            else:
                w = f"WHERE {p}user = :user"
                args = {"id": id, "user": user}
            return f"{w} AND {p}filestoreId = :id AND {p}tombstonedAt IS NULL", args

        sql_where, params = where()

        ret = {}
        for field in fields:
            try:
                if field in self.JSON_FACET_COLUMNS:
                    # json_each unnests the list so each element is counted independently
                    json_where, json_params = where("d")
                    rows = self.db.all(
                        f"SELECT j.value AS value, COUNT(*) AS count FROM document d, json_each(d.{field}) j "
                        f"{json_where} GROUP BY j.value ORDER BY count DESC, value",
                        json_params,
                    )
                    nulls = self.db.one(
                        f"SELECT COUNT(*) AS count FROM document {sql_where} "
                        f"AND ({field} IS NULL OR {field} = '[]')",
                        params,
                    )
                else:
                    present = f"{field} IS NOT NULL"
                    missing = f"{field} IS NULL"
                    if field == "category":
                        present = "category IS NOT NULL AND category != ''"
                        missing = "(category IS NULL OR category = '')"
                    rows = self.db.all(
                        f"SELECT {field} AS value, COUNT(*) AS count, SUM(size) AS size FROM document "
                        f"{sql_where} AND {present} GROUP BY {field} ORDER BY count DESC, value",
                        params,
                    )
                    nulls = self.db.one(
                        f"SELECT COUNT(*) AS count FROM document {sql_where} AND {missing}", params
                    )
                ret[field] = {"values": rows or [], "null": (nulls or {}).get("count", 0)}
            except Exception as e:
                self.ctx.err(f"document_facets({field})", e)
                ret[field] = {"values": [], "null": 0, "error": self.ctx.error_message(e)}

        if "category" in ret:
            ret["category"]["tree"] = self.category_tree(ret["category"]["values"], ret["category"]["null"])
        return ret

    @staticmethod
    def category_tree(values, root_count=0):
        """
        Turn flat `guides/auth -> 38` counts into a tree carrying own and total counts.

        `total` rolls descendants up into their ancestors, so a folder whose documents all live in
        subfolders shows the size of its subtree instead of appearing empty.
        """
        nodes = {}

        def node(path):
            if path not in nodes:
                nodes[path] = {"path": path, "name": path.rsplit("/", 1)[-1], "own": 0, "total": 0, "children": []}
            return nodes[path]

        # Documents at the source root have no category; surface them as a real node so they're
        # browsable rather than a gap.
        if root_count:
            n = node("")
            n["name"] = "(root)"
            n["own"] = n["total"] = root_count

        for row in values:
            path = row.get("value")
            if not path:
                continue
            node(path)["own"] += row.get("count", 0)
            segs = path.split("/")
            for i in range(len(segs)):
                node("/".join(segs[: i + 1]))["total"] += row.get("count", 0)

        roots = []
        for path, n in nodes.items():
            parent = path.rsplit("/", 1)[0] if "/" in path else None
            if parent is not None and parent in nodes:
                nodes[parent]["children"].append(n)
            else:
                roots.append(n)
        for n in nodes.values():
            n["children"].sort(key=lambda c: c["path"])
        roots.sort(key=lambda c: c["path"])
        return roots

    def get_filestore_stats(self, id, user=None):
        sql_where, params = self.get_user_filter(user, {"id": id})
        sql_where += " AND filestoreId = :id AND tombstonedAt IS NULL"
        return self.db.one(
            f"SELECT COUNT(*) as count, IFNULL(SUM(size), 0) AS size FROM document {sql_where}",
            params,
        )

    # --- bulk metadata (METADATA_UI.md §3) -------------------------------------------------

    # Columns a bulk operation may set, and which are lists.
    BULK_COLUMNS = ("category", "docType", "status", "locale", "product", "versions", "tags", "sourceUrl")
    BULK_LIST_COLUMNS = ("versions", "tags")
    BULK_OPS = ("fill", "set", "clear", "add", "remove")

    def bulk_select(self, query, user=None, include_tombstoned=False):
        """Resolve either {ids:[...]} or a column filter into the documents it selects."""
        q = dict(query or {})
        ids = q.pop("ids", None)
        all_columns = self.columns["document"].keys()
        uncategorised = q.get("category") == ""
        if uncategorised:
            q.pop("category")
        sql_where, params = self.sql_filter(all_columns, q, args={}, user=user)
        # A tombstoned document is hidden from an edit - its metadata is about to be irrelevant -
        # but not from a delete, which is exactly what you want to do with one.
        if not include_tombstoned:
            sql_where += " AND tombstonedAt IS NULL"
        if ids:
            ints = to_ints(ids)
            if not ints:
                return []
            sql_where += f" AND id IN ({','.join(str(int(i)) for i in ints)})"
        if "null" in q:
            null_columns = valid_columns(all_columns, q["null"])
            uncategorised = uncategorised or "category" in null_columns
            for col in (x for x in null_columns if x != "category"):
                sql_where += f" AND {col} IS NULL"
        if uncategorised:
            sql_where += " AND (category IS NULL OR category = '')"
        return self.db.all(f"SELECT * FROM document {sql_where}", params) or []

    @staticmethod
    def bulk_apply(doc, field, op, value):
        """
        What one document would become. Returns (new_value, outcome) where outcome is
        'change' | 'same' | 'skipped'.

        Split out from the write so the dry-run preview is produced by the code that does the
        work, rather than by a parallel estimate that can disagree with it.
        """
        cur = doc.get(field)
        if field in GeminiDB.BULK_LIST_COLUMNS:
            cur_list = cur if isinstance(cur, list) else (json.loads(cur) if cur else [])
            vals = value if isinstance(value, list) else ([value] if value else [])
            if op == "add":
                new = cur_list + [v for v in vals if v not in cur_list]
            elif op == "remove":
                new = [v for v in cur_list if v not in vals]
            elif op == "clear":
                new = []
            else:  # set / replace
                new = list(vals)
            return new, ("same" if new == cur_list else "change")

        if op == "clear":
            return None, ("change" if cur else "same")
        if op == "fill":
            return (value, "change") if not cur else (cur, "skipped")
        return (value, "same" if cur == value else "change")

    @staticmethod
    def bulk_changes(doc, changes):
        """
        What one document would become across *every* field being edited at once.

        Returns (updates, outcome, per_field). The multi-field form exists because the cost that
        matters is per document, not per field: editing three fields on one document is one
        re-index, and summing three per-field counts would price it as three.
        """
        updates, per_field = {}, {}
        for c in changes:
            new, outcome = GeminiDB.bulk_apply(doc, c["field"], c["op"], c.get("value"))
            per_field[c["field"]] = outcome
            if outcome == "change":
                updates[c["field"]] = new
        if updates:
            outcome = "change"
        elif "skipped" in per_field.values():
            outcome = "skipped"
        else:
            outcome = "same"
        return updates, outcome, per_field

    def bulk_preview(self, docs, changes):
        """
        Counts by document, with a per-field breakdown.

        `change` is documents, so it is also the re-index cost; `fields` is what each row of the
        editor is doing, which is how you tell which of five edits is the one doing nothing.
        """
        totals = {"selected": len(docs), "change": 0, "same": 0, "skipped": 0}
        fields = {c["field"]: {"change": 0, "same": 0, "skipped": 0} for c in changes}
        for doc in docs:
            _, outcome, per_field = self.bulk_changes(doc, changes)
            totals[outcome] += 1
            for field, o in per_field.items():
                fields[field][o] += 1
        return {**totals, "fields": fields}

    def bulk_update(self, docs, changes, user=None):
        """Apply locally only. Nothing reaches Gemini until a re-index, which is the undo buffer."""
        for c in changes:
            if c["field"] not in self.BULK_COLUMNS:
                raise Exception(f"'{c['field']}' is not a bulk-editable column")
        changed = []
        for doc in docs:
            updates, outcome, _ = self.bulk_changes(doc, changes)
            if outcome != "change":
                continue
            if "category" in updates:
                updates["categoryPath"] = category_ancestors(updates["category"])
            self.update_document(doc.get("id"), updates, user=user)
            changed.append(doc.get("id"))
        return changed

    def document_summary(self, docs, fields=None, sample=8):
        """
        What a selection currently holds, per field.

        The bulk editor shows this because the alternative - a form of empty inputs over 412
        documents - can't distinguish "they all say guide" from "they say six different things",
        and those want different edits. Store-wide facets can't answer it: they describe the
        store, not the selection.
        """
        # `None` means every field; an explicit `[]` means none of them - the delete confirm wants
        # the count and the names, and has no use for a value breakdown.
        fields = self.BULK_COLUMNS if fields is None else fields
        fields = [f for f in fields if f in self.BULK_COLUMNS]
        out = {}
        for field in fields:
            counts, empty = {}, 0
            for doc in docs:
                cur = doc.get(field)
                if field in self.BULK_LIST_COLUMNS:
                    vals = cur if isinstance(cur, list) else (json.loads(cur) if cur else [])
                    if not vals:
                        empty += 1
                    for v in vals:
                        counts[v] = counts.get(v, 0) + 1
                elif cur is None or cur == "":
                    empty += 1
                else:
                    counts[cur] = counts.get(cur, 0) + 1
            values = [{"value": k, "count": v} for k, v in counts.items()]
            values.sort(key=lambda r: (-r["count"], str(r["value"])))
            out[field] = {"values": values, "empty": empty}
        return {
            "count": len(docs),
            "fields": out,
            "sample": [d.get("displayName") for d in docs[:sample]],
        }

    def pending_documents(self, filestore_id=None, user=None, limit=None):
        """
        Documents whose local metadata differs from the copy Gemini holds.

        Derived by comparing against `customMetadata` - which the upload worker writes from the
        API response and sync refreshes from the remote document - rather than tracked with a
        dirty flag. A crashed re-index, a manual sync or a restore from backup all self-correct,
        because the comparison is against ground truth instead of a boolean someone forgot to clear.
        """
        sql_where, params = self.get_user_filter(user, {})
        sql_where += " AND uploadedAt IS NOT NULL AND tombstonedAt IS NULL"
        if filestore_id:
            sql_where += " AND filestoreId = :filestoreId"
            params["filestoreId"] = int(filestore_id)
        sql = f"SELECT * FROM document {sql_where}"
        if limit:
            sql += f" LIMIT {int(limit)}"
        rows = self.db.all(sql, params) or []
        return [r for r in rows if metadata_differs(r)]

    # --- sources (INGEST.md §2) ------------------------------------------------------------

    def get_source(self, id, user=None):
        sql_where, params = self.get_user_filter(user, {"id": id})
        return self.db.one(f"SELECT * FROM source {sql_where} AND id = :id", params)

    def query_sources(self, query=None, user=None):
        q = dict(query or {})
        sql_where, params = self.sql_filter(self.columns["source"].keys(), q, args={}, user=user)
        return self.db.all(f"SELECT * FROM source {sql_where} ORDER BY id", params) or []

    def prepare_source(self, source, id=None, user=None):
        now = datetime.now()
        if id:
            source["id"] = id
        else:
            source["createdAt"] = now
        source["updatedAt"] = now
        return with_user(source, user=user)

    async def create_source_async(self, source, user=None):
        return await self.db.insert_async("source", self.columns["source"], self.prepare_source(source, user=user))

    def update_source(self, id, source, user=None):
        return self.db.update("source", self.columns["source"], self.prepare_source(source, id, user=user))

    def detach_source_documents(self, id, user=None):
        """
        Keep the documents but forget which source produced them.

        A one-off import deletes its source once it has run - the source row existed only to carry
        the config through the same pipeline a recurring import uses - and the documents it brought
        in have to survive that.
        """
        sql_where, params = self.get_user_filter(user, {"sourceId": int(id)})
        self.db.write(f"UPDATE document SET sourceId = NULL {sql_where} AND sourceId = :sourceId", params)

    def delete_source(self, id, user=None, callback=None):
        sql_where, params = self.get_user_filter(user, {"id": id})
        self.db.write(f"DELETE FROM source_run {sql_where} AND sourceId = :id", params)
        self.db.write(f"DELETE FROM source {sql_where} AND id = :id", params, callback)

    async def create_run_async(self, run, user=None):
        run = with_user(dict(run), user=user)
        run["startedAt"] = datetime.now()
        return await self.db.insert_async("source_run", self.columns["source_run"], run)

    def update_run(self, id, run, user=None):
        run = with_user(dict(run), user=user)
        run["id"] = id
        return self.db.update("source_run", self.columns["source_run"], run)

    def get_run(self, id, user=None):
        sql_where, params = self.get_user_filter(user, {"id": id})
        return self.db.one(f"SELECT * FROM source_run {sql_where} AND id = :id", params)

    def query_runs(self, source_id, take=20, user=None):
        sql_where, params = self.get_user_filter(user, {"sourceId": int(source_id), "take": int(take)})
        return (
            self.db.all(
                f"SELECT * FROM source_run {sql_where} AND sourceId = :sourceId "
                "ORDER BY id DESC LIMIT :take",
                params,
            )
            or []
        )

    def documents_by_source_key(self, filestore_id, source_id, user=None):
        sql_where, params = self.get_user_filter(user, {"id": int(filestore_id), "sourceId": int(source_id)})
        rows = self.db.all(
            f"SELECT * FROM document {sql_where} AND filestoreId = :id AND sourceId = :sourceId", params
        )
        return {r.get("sourceKey"): r for r in (rows or []) if r.get("sourceKey")}

    def custom_metadata_dto(self, custom_metadata):
        if custom_metadata is None:
            return None
        ret = []
        for meta in custom_metadata:
            if meta.numeric_value is not None:
                ret.append({"key": meta.key, "numeric_value": meta.numeric_value})
            elif meta.string_list_value is not None:
                ret.append({"key": meta.key, "string_list_value": meta.string_list_value.values})
            elif meta.string_value is not None:
                ret.append({"key": meta.key, "string_value": meta.string_value})
        return ret
