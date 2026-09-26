"""Crash-resilient local search index worker."""

import os
import threading
import time

from . import ingest
from . import search


class SearchWorker:
    retry_delay = 5

    def __init__(self, ctx, db):
        self.ctx = ctx
        self.db = db.clone()
        self.running = False
        self.lock = threading.Lock()
        self.cancelled = threading.Event()
        self.restart_requested = False
        self.worker_token = None
        self.progress = {"total": 0, "done": 0, "failed": 0, "startedAt": None}

    def start(self):
        with self.lock:
            if self.running:
                # A producer may queue work while this worker is between its final queue read and
                # shutdown. Remember the wake-up so that empty read cannot strand the new work.
                self.restart_requested = True
                return
            self.running = True
            self.restart_requested = False
            self.cancelled.clear()
            self.progress = {"total": 0, "done": 0, "failed": 0, "startedAt": time.time()}
            token = self.worker_token = object()
            threading.Thread(target=self.run, args=(token,), daemon=True).start()

    def cancel(self):
        self.cancelled.set()

    def status(self):
        return {**self.progress, "running": self.running, "cancelled": self.cancelled.is_set()}

    def run(self, token):
        # Only suppress the exact version already attempted during this run. If an import updates
        # the same row while the worker is active, its new desired hash must still be indexed.
        completed = set()
        try:
            # Recalculate signatures on startup so extractor/index changes also re-index documents
            # that were already complete before this process started.
            while not self.cancelled.is_set():
                try:
                    documents = self.db.db.all(
                        "SELECT * FROM document WHERE tombstonedAt IS NULL ORDER BY id") or []
                    break
                except Exception as error:
                    self.ctx.err("SearchWorker failed preparing its queue; retrying", error)
                    if self.cancelled.wait(self.retry_delay):
                        return
            else:
                return
            for document in documents:
                desired = search.desired_hash(document)
                if document.get("searchHash") != desired:
                    self.db.update_document(
                        document["id"], {"searchHash": desired}, user=document.get("user"))
            self.db.db.task_queue.join()
            while not self.cancelled.is_set():
                with self.lock:
                    self.restart_requested = False
                try:
                    candidates = [d for d in self.db.get_search_candidates(100)
                                  if (d.get("id"), search.desired_hash(d)) not in completed]
                except Exception as error:
                    self.ctx.err("SearchWorker failed reading its queue; retrying", error)
                    if self.cancelled.wait(self.retry_delay):
                        break
                    continue
                if not candidates:
                    with self.lock:
                        if self.restart_requested:
                            continue
                        self.running = False
                        if self.worker_token is token:
                            self.worker_token = None
                    return
                self.progress["total"] += len(candidates)
                for document in candidates:
                    if self.cancelled.is_set():
                        break
                    completed.add((document.get("id"), search.desired_hash(document)))
                    try:
                        self.index_document(document)
                        self.progress["done"] += 1
                    except Exception as error:
                        self.progress["failed"] += 1
                        self.ctx.err(f"Failed indexing document {document.get('id')} for Search", error)
                        self.db.update_document(
                            document.get("id"), {
                                "searchError": self.ctx.error_message(error),
                                "searchStartedAt": None,
                            },
                            user=document.get("user"),
                        )
        except Exception as error:
            self.ctx.err("SearchWorker", error)
        finally:
            with self.lock:
                if self.worker_token is token:
                    self.running = False
                    self.restart_requested = False
                    self.worker_token = None

    def index_document(self, document):
        desired = search.desired_hash(document)
        self.db.mark_search_started(document["id"])
        if document.get("searchHash") != desired:
            self.db.update_document(document["id"], {"searchHash": desired}, user=document.get("user"))
        url = document.get("url") or ""
        if not url.startswith("/~cache/"):
            raise ValueError("Document has no local cached content")
        path = self.ctx.get_cache_path(url[len("/~cache/"):])
        if not os.path.exists(path):
            raise FileNotFoundError("Cached document content is missing")
        with open(path, "rb") as handle:
            content = handle.read()
        filename = document.get("filename") or document.get("sourceKey") or document.get("displayName") or "document.txt"
        text, frontmatter, skip = ingest.extract(content, filename, {"minWords": 0})
        if skip:
            # Source imports cache their already-extracted HTML as Markdown. Direct binary uploads
            # remain Gemini-only until a dependency-free local extractor exists.
            if ingest.ext_of(filename) in ingest.BINARY_DOC_EXTS:
                self.db.replace_search_sections(document, [], desired)
                self.db.update_document(
                    document["id"], {"searchError": f"Not locally searchable: {skip}"},
                    user=document.get("user"),
                )
                return
            raise ValueError(skip)
        sections = search.split_sections(
            text or "", document, document_title=(frontmatter or {}).get("title"))
        self.db.replace_search_sections(document, sections, desired)
