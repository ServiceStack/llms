"""Resuming queued uploads preserves identities and survives worker shutdown races."""

import copy
import os
import unittest
from unittest.mock import MagicMock, patch

from llms.extensions import gemini
from llms.extensions.gemini.db import GeminiDB
from llms.extensions.gemini.tests import test_saved_imports as harness
from llms.extensions.gemini.upload_worker import UploadWorker


class ResumeUploadTests(unittest.IsolatedAsyncioTestCase):
    setUp = harness.SavedImportTests.setUp
    call = harness.SavedImportTests.call

    async def test_resume_only_selects_queued_files_and_never_changes_rows(self):
        db = GeminiDB(self.ctx, os.path.join(self.root, "queue.sqlite"))
        try:
            store = await db.create_filestore_async({"name": "remote/store", "displayName": "Test"}, user="alice")
            other_store = await db.create_filestore_async({"name": "remote/other", "displayName": "Other"}, user="alice")
            ids = []
            for user, store_id, category, fields in [
                ("alice", store, "blog", {}), ("alice", store, "blog/2026", {}),
                ("alice", store, "blog", {"uploadedAt": "2026-10-04", "name": "remote/healthy"}),
                ("alice", store, "blog", {"error": "Failed"}),
                ("alice", store, "blog", {"tombstonedAt": "2026-10-04"}),
                ("alice", store, "blog-internal", {}), ("alice", other_store, "blog", {}),
                ("bob", store, "blog", {}),
            ]:
                parts = category.split("/")
                ids.append(await db.create_document_async({"filestoreId": store_id, "category": category,
                    "categoryPath": ["/".join(parts[:i+1]) for i in range(len(parts))],
                    "displayName": category, **fields}, user=user))
            before = copy.deepcopy(db.db.all("SELECT * FROM document ORDER BY id"))
            gemini.g_worker.status.return_value = {"running": True, "cancelled": False}
            with patch.object(gemini, "g_db", db):
                for _ in range(2):
                    result = await self.call("post", "filestores/{id}/resume-uploads", {"categoryUnder": "blog"}, id=str(store))
                    self.assertEqual(result["ids"], ids[:2])
                    self.assertEqual(result["queued"], 2)
                result = await self.call("post", "filestores/{id}/resume-uploads", {"ids": [ids[0], ids[2], ids[7]]}, id=str(store))
                self.assertEqual(result["ids"], [ids[0]])
                calls = gemini.g_worker.start.call_count
                result = await self.call("post", "filestores/{id}/resume-uploads", {"ids": [ids[2]]}, id=str(store))
                self.assertEqual(result["queued"], 0)
                self.assertEqual(gemini.g_worker.start.call_count, calls)
                with self.assertRaisesRegex(ValueError, "Filestore does not exist"):
                    await self.call("post", "filestores/{id}/resume-uploads", id="999")
            self.assertEqual(db.db.all("SELECT * FROM document ORDER BY id"), before)
            self.assertEqual([doc["id"] for doc in db.get_pending_documents(limit=1, exclude_ids=ids[:2])], [ids[5]])
        finally:
            db.db.close()


class UploadWorkerQueueTests(unittest.TestCase):
    def worker(self):
        db = MagicMock()
        db.clone.return_value = db
        worker = UploadWorker(MagicMock(), db, MagicMock())
        worker.running = True
        worker.refresh_filestores = MagicMock()
        return worker

    def test_delayed_completion_writes_cannot_hide_the_rest_of_the_queue(self):
        worker = self.worker()
        docs = [{"id": i, "filestoreId": 1} for i in range(1, 21)]

        def pending(limit, exclude_ids=None):
            # Simulate DB completion writes lagging every read: all rows still appear pending.
            return [doc for doc in docs if doc["id"] not in (exclude_ids or set())][:limit]

        worker.db.get_pending_documents.side_effect = pending
        processed = []
        worker._process = lambda doc: processed.append(doc["id"])
        worker.run()
        self.assertEqual(sorted(processed), list(range(1, 21)))
        self.assertFalse(worker.running)

    def test_resume_during_final_refresh_keeps_the_next_worker_running(self):
        worker = self.worker()
        worker.db.get_pending_documents.return_value = []
        worker.refresh_filestores.side_effect = lambda _: worker.start()
        with patch("llms.extensions.gemini.upload_worker.threading.Thread") as thread:
            worker.run()
            thread.assert_called_once()
            thread.return_value.start.assert_called_once()
            self.assertTrue(worker.running)
        worker.refresh_filestores.side_effect = None
        worker.run()
        self.assertFalse(worker.running)
