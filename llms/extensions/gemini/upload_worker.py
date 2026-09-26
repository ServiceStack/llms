import json
import os
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from .client import GeminiClient
from .db import GeminiDB, to_custom_metadata

GEMINI_UPLOAD_MIME_TYPES = os.getenv("GEMINI_UPLOAD_MIME_TYPES", "mdx:text/markdown,cshtml:text/html")
# Uploads are almost entirely waiting on Gemini, so concurrency is what turns a multi-hour import
# into a few minutes. Kept modest by default to stay well inside rate limits.
GEMINI_UPLOAD_CONCURRENCY = int(os.getenv("GEMINI_UPLOAD_CONCURRENCY", "4"))
GEMINI_UPLOAD_MAX_RETRIES = int(os.getenv("GEMINI_UPLOAD_MAX_RETRIES", "4"))


def is_rate_limited(e):
    if getattr(e, "status", None) == 429:
        return True
    msg = str(e).lower()
    return "429" in msg or "resource_exhausted" in msg or "rate limit" in msg or "quota" in msg


def is_retryable(e):
    if getattr(e, "status", None) in (429, 500, 502, 503, 504):
        return True
    msg = str(e).lower()
    return is_rate_limited(e) or any(c in msg for c in ("500", "502", "503", "504", "unavailable", "deadline"))


class UploadWorker:
    """
    Drains the pending-upload queue.

    Runs a pool rather than one document at a time: an import of a few thousand documents is
    otherwise measured in hours, nearly all of it idle. Each pool thread gets its own database
    clone because pooled read connections can't be shared across threads.
    """

    def __init__(self, ctx, db: GeminiDB, client: GeminiClient):
        self.ctx = ctx
        self.running = False
        self.lock = threading.Lock()
        self.db = db.clone()
        self.client = client
        self.cancelled = threading.Event()
        self.restart_requested = False
        self._local = threading.local()
        self.progress = {"total": 0, "done": 0, "failed": 0, "startedAt": None}

        self.include_mime_types = {}
        if GEMINI_UPLOAD_MIME_TYPES:
            for ext_type in GEMINI_UPLOAD_MIME_TYPES.split(","):
                ext_type = ext_type.strip()
                if not ext_type:
                    continue
                ext, mime_type = ext_type.split(":")
                self.include_mime_types[ext] = mime_type

    # --- lifecycle ---------------------------------------------------------------------

    def start(self):
        with self.lock:
            # Do this even while running: it closes the window where the worker has observed an
            # empty queue but has not yet changed `running` back to false.
            self.restart_requested = True
            self.cancelled.clear()
            if self.running:
                return
            self.running = True
            self.progress = {"total": 0, "done": 0, "failed": 0, "startedAt": time.time()}
            threading.Thread(target=self.run, daemon=True).start()

    def cancel(self):
        """Stop after the in-flight documents finish. Already-uploaded work is kept."""
        self.cancelled.set()

    def status(self):
        p = dict(self.progress)
        p["running"] = self.running
        p["cancelled"] = self.cancelled.is_set()
        elapsed = time.time() - p["startedAt"] if p.get("startedAt") else 0
        if p["done"] and elapsed > 0:
            rate = p["done"] / elapsed
            p["rate"] = round(rate, 2)
            remaining = max(0, p["total"] - p["done"] - p["failed"])
            p["etaSeconds"] = int(remaining / rate) if rate else None
        return p

    def thread_db(self):
        db = getattr(self._local, "db", None)
        if db is None:
            db = self._local.db = self.db.clone()
        return db

    # --- main loop ---------------------------------------------------------------------

    def run(self):
        try:
            self.ctx.log(f"UploadWorker started (concurrency={GEMINI_UPLOAD_CONCURRENCY})")
            completed = set()
            filestore_ids = set()

            with ThreadPoolExecutor(max_workers=GEMINI_UPLOAD_CONCURRENCY) as pool:
                while self.running and not self.cancelled.is_set():
                    with self.lock:
                        self.restart_requested = False
                    try:
                        docs = self.db.get_pending_documents(limit=GEMINI_UPLOAD_CONCURRENCY * 4)
                    except Exception as e:
                        # A transient database failure must not kill the only worker and strand
                        # every queued row until a later request happens to call start().
                        self.ctx.err("UploadWorker failed to read its queue; retrying", e)
                        if self.cancelled.wait(5):
                            break
                        continue
                    # Reads can lag a just-committed update, so a document already processed in
                    # this pass can reappear; `completed` keeps it from being uploaded twice.
                    batch = [d for d in docs if d.get("id") not in completed]
                    if not batch:
                        with self.lock:
                            if self.restart_requested:
                                continue
                            self.running = False
                        break

                    self.progress["total"] += len(batch)
                    futures = []
                    for doc in batch:
                        completed.add(doc.get("id"))
                        filestore_ids.add(doc.get("filestoreId"))
                        futures.append(pool.submit(self._process, doc))
                    for f in futures:
                        try:
                            f.result()
                        except Exception as e:
                            self.ctx.err("UploadWorker task", e)

            self.refresh_filestores(filestore_ids)
        except Exception as e:
            self.ctx.err("UploadWorker", e)
        finally:
            with self.lock:
                self.running = False
            self.ctx.log(
                f"UploadWorker stopped ({self.progress['done']} uploaded, {self.progress['failed']} failed)"
            )

    def _process(self, doc):
        if self.cancelled.is_set():
            return
        try:
            self.process_doc(doc, self.thread_db())
            self.progress["done"] += 1
        except Exception:
            self.progress["failed"] += 1

    def refresh_filestores(self, filestore_ids):
        for filestore_id in filestore_ids:
            if not filestore_id:
                continue
            try:
                filestore = self.db.get_filestore(filestore_id)
                if not filestore or not filestore.get("name"):
                    continue
                result = self.client.file_search_stores.get(name=filestore.get("name"))
                if result:
                    self.db.update_filestore(
                        filestore.get("id"),
                        {
                            "displayName": result.display_name,
                            "createTime": result.create_time,
                            "updateTime": result.update_time,
                            "activeDocumentsCount": result.active_documents_count,
                            "pendingDocumentsCount": result.pending_documents_count,
                            "failedDocumentsCount": result.failed_documents_count,
                            "sizeBytes": result.size_bytes,
                        },
                    )
            except Exception as e:
                self.ctx.err(f"Failed refreshing filestore {filestore_id}", e)

    # --- one document ------------------------------------------------------------------

    def process_doc(self, doc, db):
        user = doc.get("user")
        doc_id = doc.get("id")

        try:
            filestore_id = doc.get("filestoreId")
            if not filestore_id:
                raise Exception("Missing filestoreId")

            filestore = db.get_filestore(filestore_id, user=user)
            if not filestore:
                # Fallback to public filestore (user IS NULL) if user-specific not found
                filestore = db.get_filestore(filestore_id, user=None)
            if not filestore:
                raise Exception("Filestore not found")

            store_name = filestore.get("name")
            if not store_name:
                raise Exception("Filestore has no name (not created in Gemini?)")

            # Recover the crash window where Gemini committed the upload but UploadedAt was not
            # persisted locally. Only adopt it when the remote metadata proves it is this exact
            # cached content; a genuine replacement must still be uploaded.
            if doc.get("name"):
                try:
                    store_doc = self.client.file_search_stores.documents.get(name=doc.get("name"))
                    remote_hash = next(
                        (m.string_value for m in (store_doc.custom_metadata or [])
                         if m.key == "hash" and m.string_value), None)
                    if str(store_doc.state).endswith("STATE_ACTIVE") and remote_hash == doc.get("hash"):
                        db.update_document(doc_id, {
                            "uploadedAt": datetime.now(), "startedAt": None,
                            "name": store_doc.name, "displayName": store_doc.display_name,
                            "sizeBytes": store_doc.size_bytes, "mimeType": store_doc.mime_type,
                            "createTime": store_doc.create_time, "updateTime": store_doc.update_time,
                            "state": store_doc.state,
                            "customMetadata": db.custom_metadata_dto(store_doc.custom_metadata),
                        }, user=user)
                        self.ctx.log(f"Recovered completed Gemini upload for {doc.get('displayName')}")
                        return
                except Exception as e:
                    self.ctx.err(f"Could not verify prior Gemini upload for {doc.get('displayName')}", e)

            url = doc.get("url")  # /~cache/xx/xxxx.ext
            if not url or not url.startswith("/~cache/"):
                raise Exception("Invalid URL")

            rel_path = url[len("/~cache/") :]
            full_path = self.ctx.get_cache_path(rel_path)

            if not os.path.exists(full_path):
                raise Exception("File not found on disk")

            self.ctx.log(f"Uploading {doc.get('displayName')} to {store_name}")
            # The copy this upload supersedes, if any. An upload does not replace a document in
            # Gemini - there is no API for that - it adds a second one carrying the same content
            # hash, which is what a sync then reports as DUPLICATE_FILE.
            prior_name = doc.get("name")
            db.update_document(doc_id, {"startedAt": datetime.now()}, user=user)

            config = {
                "display_name": doc.get("displayName"),
                # Built from the document's columns via one explicit mapping, so what's pushed
                # can't drift from what the filters and citations expect.
                "custom_metadata": to_custom_metadata(doc),
                # fails with mime_type application/json, uploading .json succeeds without it
                # "mime_type": doc.get("mimeType"),
            }

            ext = os.path.splitext(full_path)[1].lstrip(".").lower()
            if ext in self.include_mime_types:
                config["mime_type"] = self.include_mime_types[ext]

            chunking = filestore.get("chunkingConfig") or doc.get("chunkingConfig")
            if chunking:
                config["chunking_config"] = chunking

            if self.ctx.debug:
                self.ctx.dbg(f"Uploading {doc.get('displayName')} to {store_name}\n" + json.dumps(config, indent=2))

            operation = self.upload_with_retry(store_name, full_path, config)

            while not operation.done:
                if self.cancelled.is_set():
                    raise Exception("Cancelled")
                time.sleep(2)
                operation = self.client.operations.get(operation)

            if operation.error:
                raise Exception(operation.error.message)

            document_name = operation.response.document_name
            db.update_document(doc_id, {"uploadedAt": datetime.now(), "startedAt": None, "name": document_name}, user=user)

            # After the new copy is in and the row points at it, never before: deleting first
            # would take the document out of the store for the length of the upload, and lose it
            # outright if the upload failed.
            if prior_name and prior_name != document_name:
                try:
                    self.client.file_search_stores.documents.delete(name=prior_name, config={"force": True})
                    self.ctx.dbg(f"Removed superseded copy {prior_name}")
                except Exception as e:
                    # Worth reporting but not worth failing the upload for: the new copy is live,
                    # and a leftover is a sync finding rather than a broken document.
                    self.ctx.err(f"Could not remove superseded copy {prior_name}", e)

            try:
                store_doc = self.client.file_search_stores.documents.get(name=document_name)
                db.update_document(
                    doc_id,
                    {
                        "name": store_doc.name,
                        "displayName": store_doc.display_name,
                        "sizeBytes": store_doc.size_bytes,
                        "mimeType": store_doc.mime_type,
                        "createTime": store_doc.create_time,
                        "updateTime": store_doc.update_time,
                        "state": store_doc.state,
                        "customMetadata": db.custom_metadata_dto(store_doc.custom_metadata),
                    },
                    user=user,
                )
            except Exception as e:
                # The operation succeeded and UploadedAt/name are already durable. A transient
                # enrichment GET should not turn a completed upload into a failed document.
                self.ctx.err(f"Uploaded {document_name}, but could not refresh remote fields", e)

        except Exception as e:
            self.ctx.err(f"Failed to upload doc {doc.get('id')}", e)
            # A deliberate cancellation or process shutdown leaves the row queued so the next
            # worker (including the next app instance) can resume it automatically.
            if doc_id and not self.cancelled.is_set():
                db.update_document(doc_id, {"error": self.ctx.error_message(e)}, user=user)
            raise

    def upload_with_retry(self, store_name, full_path, config):
        """
        Retry transient failures with exponential backoff and jitter.

        Concurrency makes rate limiting likely rather than exceptional, and without backoff a
        large import turns every 429 into a permanently failed document.
        """
        last = None
        for attempt in range(GEMINI_UPLOAD_MAX_RETRIES):
            try:
                return self.client.file_search_stores.upload_to_file_search_store(
                    file_search_store_name=store_name, file=full_path, config=config
                )
            except Exception as e:
                last = e
                if attempt == GEMINI_UPLOAD_MAX_RETRIES - 1 or not is_retryable(e):
                    raise
                delay = min(60, (2**attempt) * (5 if is_rate_limited(e) else 1))
                delay += random.uniform(0, delay * 0.25)  # jitter, so a pool doesn't retry in lockstep
                self.ctx.log(f"Upload retry {attempt + 1}/{GEMINI_UPLOAD_MAX_RETRIES} in {delay:.1f}s: {e}")
                if self.cancelled.wait(delay):
                    raise Exception("Cancelled") from e
        raise last
