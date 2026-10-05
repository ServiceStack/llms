"""Deletion keeps the event loop responsive and retains documents Gemini refused."""

import asyncio
import copy
import os
import tempfile
import threading
import unittest
from unittest.mock import MagicMock

from llms.extensions.gemini.client import GeminiApiError
from llms.extensions.gemini.db import GeminiDB
from llms.extensions.gemini.tests import test_import_persistence as persistence
from llms.extensions.gemini.tests import test_saved_imports as harness


class DocumentDeletionTests(unittest.IsolatedAsyncioTestCase):
    setUp = harness.SavedImportTests.setUp
    call = harness.SavedImportTests.call

    def seed_documents(self, count, other_count=1):
        self.db.documents = {id: {"id": id, "name": f"remote/{id}", "displayName": f"Page {id}",
                                  "filestoreId": 1, "category": "razor"} for id in range(1, count + 1)}
        for id in range(count + 1, count + other_count + 1):
            self.db.documents[id] = {"id": id, "name": f"remote/{id}", "displayName": f"Other {id}",
                                     "filestoreId": 1, "category": "other"}

        def select(query, user=None, **kwargs):
            return copy.deepcopy([doc for doc in self.db.documents.values()
                if ("ids" not in query or doc["id"] in query["ids"])
                and (not query.get("filestoreId") or doc["filestoreId"] == query["filestoreId"])
                and (not query.get("categoryUnder") or doc["category"] == query["categoryUnder"])])

        self.db.bulk_select = MagicMock(side_effect=select)
        self.db.count_documents = lambda query, user=None: len([doc for doc in select(query)
            if query.get("null") != "tombstonedAt" or not doc.get("tombstonedAt")])
        self.db.document_summary = GeminiDB.document_summary.__get__(self.db)
        self.db.get_document = lambda id, user=None: copy.deepcopy(self.db.documents.get(int(id)))

        async def delete_document(id, user=None):
            self.db.documents.pop(int(id), None)

        self.db.delete_document_async = delete_document

    async def test_bulk_delete_does_not_block_progress_and_bounds_concurrency(self):
        self.seed_documents(8)
        first_started, release = threading.Event(), threading.Event()
        lock = threading.Lock()
        active, peak, completed = 0, 0, 0

        def delete_remote(**kwargs):
            nonlocal active, peak, completed
            with lock:
                active += 1
                peak = max(peak, active)
            first_started.set()
            release.wait(1)
            with lock:
                active -= 1
                completed += 1

        self.client.file_search_stores.documents.delete.side_effect = delete_remote
        task = asyncio.create_task(self.call("post", "documents/delete", {"filter": {"filestoreId": 1, "categoryUnder": "razor"}}))
        try:
            self.assertTrue(await asyncio.wait_for(asyncio.to_thread(first_started.wait, 1), 2))
            # A heartbeat can run while remote deletion is blocked. The former synchronous
            # handler could only reach here after finishing the entire deletion loop.
            self.assertEqual(completed, 0)
            self.assertEqual(len(self.db.documents), 9)
        finally:
            release.set()
        result = await task
        self.assertEqual(result["deleted"], 8)
        self.assertEqual(result["errors"], [])
        self.assertEqual(set(self.db.documents), {9})
        self.assertGreater(peak, 1)
        self.assertLessEqual(peak, 4)
        for call in self.client.file_search_stores.documents.delete.call_args_list:
            self.assertEqual(call.kwargs["timeout"], 60)
        self.client.file_search_stores.get.assert_called_once_with(name="fileSearchStores/test", timeout=30)

    async def test_remote_errors_are_named_and_failed_documents_remain(self):
        self.seed_documents(4)

        def delete_remote(name, **kwargs):
            if name == "remote/2":
                raise GeminiApiError(404, "Already gone")
            if name == "remote/3":
                raise GeminiApiError(500, "Provider failed")
            if name == "remote/4":
                raise TimeoutError("timed out")

        self.client.file_search_stores.documents.delete.side_effect = delete_remote
        result = await self.call("post", "documents/delete", {"ids": [1, 2, 3, 4]})
        self.assertEqual(result["ids"], [1, 2])
        self.assertEqual(result["selected"], 4)
        self.assertEqual(result["deleted"], 2)
        self.assertEqual([error["id"] for error in result["errors"]], [3, 4])
        self.assertIn("Provider failed", result["errors"][0]["error"])
        self.assertEqual(result["errors"][1]["error"], "timed out")
        self.assertEqual(set(self.db.documents), {3, 4, 5})

    async def test_response_waits_for_local_commit(self):
        self.seed_documents(1)
        entered, committed = asyncio.Event(), asyncio.Event()

        async def delayed_delete(id, user=None):
            entered.set()
            await committed.wait()
            self.db.documents.pop(id)

        self.db.delete_document_async = delayed_delete
        task = asyncio.create_task(self.call("delete", "documents/{id}", id="1"))
        await asyncio.wait_for(entered.wait(), 1)
        try:
            self.assertFalse(task.done())
            self.assertIn(1, self.db.documents)
        finally:
            committed.set()
        self.assertEqual(await task, {})
        self.assertEqual(set(self.db.documents), {2})

    async def test_failed_local_commit_is_not_reported_as_deleted(self):
        self.seed_documents(1)

        async def failed_delete(id, user=None):
            raise RuntimeError("Database write failed")

        self.db.delete_document_async = failed_delete
        result = await self.call("post", "documents/delete", {"ids": [1]})
        self.assertEqual(result["deleted"], 0)
        self.assertEqual(result["errors"][0]["error"], "Database write failed")
        self.assertIn(1, self.db.documents)

    async def test_root_and_store_wide_deletions_are_rejected_before_remote_calls(self):
        self.seed_documents(2)
        before = copy.deepcopy(self.db.documents)
        for body in [{"filter": {"filestoreId": 1, "categoryUnder": root}}
                     for root in (None, "", "/", "\\", " . ")] + [
                         {"filter": {"filestoreId": 1}}, {"ids": [1, 2, 3]}]:
            with self.subTest(body=body), self.assertRaisesRegex(ValueError, "filestore root"):
                await self.call("post", "documents/delete", body)
        self.client.file_search_stores.documents.delete.assert_not_called()
        self.assertEqual(self.db.documents, before)

    async def test_last_document_cannot_be_deleted_and_summary_explains_why(self):
        self.seed_documents(1, other_count=0)
        summary = await self.call("post", "documents/summary", {"ids": [1], "fields": []})
        self.assertFalse(summary["deleteAllowed"])
        self.assertIn("confirmation dialog", summary["deleteError"])
        with self.assertRaisesRegex(ValueError, "filestore root"):
            await self.call("delete", "documents/{id}", id="1")
        self.client.file_search_stores.documents.delete.assert_not_called()
        self.assertIn(1, self.db.documents)

    async def test_hidden_tombstones_cannot_allow_deleting_all_visible_files(self):
        self.seed_documents(1)
        self.db.documents[2]["tombstonedAt"] = "2026-10-04"
        with self.assertRaisesRegex(ValueError, "filestore root"):
            await self.call("post", "documents/delete", {"ids": [1]})
        self.client.file_search_stores.documents.delete.assert_not_called()

    async def test_concurrent_requests_cannot_jointly_empty_the_store(self):
        self.seed_documents(1)
        results = await asyncio.gather(self.call("delete", "documents/{id}", id="1"),
                                       self.call("delete", "documents/{id}", id="2"), return_exceptions=True)
        self.assertEqual(results[0], {})
        self.assertIsInstance(results[1], ValueError)
        self.assertEqual(set(self.db.documents), {2})
        self.assertEqual(self.client.file_search_stores.documents.delete.call_count, 1)


class DocumentSelectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_bulk_and_list_filters_match_without_crossing_folder_store_or_user(self):
        with tempfile.TemporaryDirectory() as root:
            db = GeminiDB(persistence.ImportPersistenceTests.Context(), os.path.join(root, "gemini.sqlite"))
            try:
                rows = [(1, "alice", "docs"), (1, "alice", "docs/api"), (1, "alice", "docs-internal"),
                        (1, "alice", "pages"), (1, "alice", ""), (2, "alice", "docs"), (1, "bob", "docs")]
                ids = []
                for store, user, category in rows:
                    parts = category.split("/") if category else []
                    ids.append(await db.create_document_async({"filestoreId": store, "category": category,
                        "categoryPath": ["/".join(parts[:i + 1]) for i in range(len(parts))],
                        "displayName": category or "Root", "tags": ["guide"]}, user=user))
                selector = {"filestoreId": 1, "categoryUnder": "docs"}
                selected = db.bulk_select(selector, user="alice", include_tombstoned=True)
                self.assertEqual([doc["id"] for doc in selected], ids[:2])
                self.assertEqual(db.count_documents(selector, user="alice"), 2)
                self.assertEqual(db.document_summary(selected, [])["count"], 2)
                self.assertEqual([doc["id"] for doc in db.bulk_select({**selector, "q": "api", "tags": "guide"}, user="alice")], [ids[1]])
                self.assertEqual([doc["id"] for doc in db.bulk_select({"filestoreId": 1, "category": ""}, user="alice")], [ids[4]])
                self.assertEqual(db.bulk_select({"ids": []}, user="alice"), [])
            finally:
                db.db.close()
