"""Accepted indexing operations survive retries and process restarts without another upload."""

import copy
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import URLError

from llms.extensions.gemini.client import GeminiApiError, _object
from llms.extensions.gemini.db import GeminiDB
from llms.extensions.gemini.upload_worker import UploadWorker


class UploadPollingTests(unittest.TestCase):
    def worker(self):
        worker = UploadWorker(MagicMock(), MagicMock(), MagicMock())
        worker.cancelled.wait = MagicMock(side_effect=lambda _: worker.cancelled.is_set())
        return worker

    def test_transient_failures_retry_the_same_operation(self):
        for error in (GeminiApiError(503, "Unavailable"), GeminiApiError(429, "Quota"),
                      GeminiApiError(408, "Timed out"), URLError("Disconnected"), TimeoutError("Read timeout")):
            with self.subTest(error=error):
                worker = self.worker()
                worker.client.operations.get.side_effect = [error, _object({"done": True})]
                result = worker.wait_for_operation(_object({"name": "operations/accepted"}))
                self.assertTrue(result.done)
                self.assertEqual(worker.client.operations.get.call_count, 2)
                for call in worker.client.operations.get.call_args_list:
                    self.assertEqual(call.args, ("operations/accepted",))

    def test_permanent_error_is_immediate_and_reports_status_and_operation(self):
        worker = self.worker()
        worker.client.operations.get.side_effect = GeminiApiError(403, "Permission denied")
        with self.assertRaisesRegex(RuntimeError, "operations/accepted: HTTP 403: Permission denied"):
            worker.wait_for_operation(_object({"name": "operations/accepted"}))
        self.assertEqual(worker.client.operations.get.call_count, 1)

    def test_consecutive_poll_errors_are_bounded(self):
        worker = self.worker()
        worker.client.operations.get.side_effect = GeminiApiError(503, "Unavailable")
        with patch("llms.extensions.gemini.upload_worker.GEMINI_UPLOAD_MAX_RETRIES", 3):
            with self.assertRaisesRegex(RuntimeError, "Retry to check the same operation"):
                worker.wait_for_operation(_object({"name": "operations/accepted"}))
        self.assertEqual(worker.client.operations.get.call_count, 3)

    def test_successful_pending_poll_resets_retry_budget(self):
        worker = self.worker()
        worker.client.operations.get.side_effect = [GeminiApiError(503, "Unavailable"),
            _object({"name": "operations/accepted"}), GeminiApiError(503, "Unavailable"), _object({"done": True})]
        with patch("llms.extensions.gemini.upload_worker.GEMINI_UPLOAD_MAX_RETRIES", 2):
            self.assertTrue(worker.wait_for_operation(_object({"name": "operations/accepted"})).done)

    def test_cancellation_interrupts_poll_backoff(self):
        worker = self.worker()
        def poll(_):
            worker.cancelled.set()
            raise GeminiApiError(503, "Unavailable")
        worker.client.operations.get.side_effect = poll
        with self.assertRaisesRegex(RuntimeError, "Cancelled.*operations/accepted"):
            worker.wait_for_operation(_object({"name": "operations/accepted"}))
        self.assertEqual(worker.client.operations.get.call_count, 1)


class UploadReceiptTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.ctx = MagicMock(debug=False)
        self.ctx.error_message.side_effect = str
        self.ctx.get_cache_path.side_effect = lambda rel: os.path.join(self.temp.name, rel)
        os.makedirs(os.path.join(self.temp.name, "aa"))
        with open(os.path.join(self.temp.name, "aa/test.md"), "w") as f:
            f.write("Document")
        self.path = os.path.join(self.temp.name, "gemini.sqlite")
        self.db = GeminiDB(self.ctx, self.path)
        store = await self.db.create_filestore_async({"name": "fileSearchStores/docs"}, user="alice")
        self.id = await self.db.create_document_async({"filestoreId": store, "url": "/~cache/aa/test.md",
            "hash": "hash", "displayName": "Test"}, user="alice")
        self.client = MagicMock()
        self.client.file_search_stores.documents.get.side_effect = GeminiApiError(404, "Not found")
        self.client.file_search_stores.upload_to_file_search_store.return_value = _object({"name": "operations/accepted"})

    async def asyncTearDown(self):
        self.db.db.close()

    def worker(self):
        worker = UploadWorker(self.ctx, self.db, self.client)
        worker.cancelled.wait = MagicMock(side_effect=lambda _: worker.cancelled.is_set())
        return worker

    def row(self):
        return self.db.get_document(self.id, user="alice")

    def flush(self):
        self.db.update_document(self.id, {}, user="alice", wait_for_commit=True)

    def fail_poll(self):
        def poll(_):
            # This uses a separate reader connection, proving the receipt committed before GET.
            self.assertEqual(self.row()["uploadOperationName"], "operations/accepted")
            raise GeminiApiError(403, "Denied")
        self.client.operations.get.side_effect = poll
        with self.assertRaisesRegex(RuntimeError, "HTTP 403"):
            self.worker().process_doc(copy.deepcopy(self.row()), self.db)
        self.flush()
        self.assertIsNone(self.row()["uploadedAt"])
        self.assertEqual(self.row()["uploadOperationName"], "operations/accepted")

    async def test_retry_after_database_reopen_resumes_without_upload(self):
        self.fail_poll()
        dto = self.db.to_dto(self.row(), [])
        self.assertNotIn("uploadOperationName", dto)
        self.assertNotIn("uploadOperationHash", dto)
        self.db.db.close()
        self.db = GeminiDB(self.ctx, self.path)
        self.client.operations.get.side_effect = None
        self.client.operations.get.return_value = _object({"done": True, "response": {"documentName": "documents/ready"}})
        self.worker().process_doc(self.row(), self.db)
        self.assertEqual(self.client.file_search_stores.upload_to_file_search_store.call_count, 1)
        self.assertEqual(self.row()["name"], "documents/ready")
        self.assertIsNotNone(self.row()["uploadedAt"])
        self.assertIsNone(self.row()["error"])
        self.assertIsNone(self.row()["uploadOperationName"])

    async def test_changed_metadata_cannot_adopt_previous_operation(self):
        self.fail_poll()
        self.db.update_document(self.id, {"category": "changed"}, user="alice", wait_for_commit=True)
        self.client.operations.get.side_effect = None
        self.client.operations.get.return_value = _object({"done": True, "response": {"documentName": "documents/new"}})
        self.worker().process_doc(self.row(), self.db)
        self.assertEqual(self.client.file_search_stores.upload_to_file_search_store.call_count, 2)

    async def test_terminal_failure_clears_receipt_and_explicit_retry_uploads_again(self):
        self.client.operations.get.return_value = _object({"done": True, "error": {"code": 3, "message": "Invalid content"}})
        with self.assertRaisesRegex(RuntimeError, "operations/accepted failed.*code 3.*Invalid content"):
            self.worker().process_doc(self.row(), self.db)
        self.flush()
        self.assertIsNone(self.row()["uploadOperationName"])
        self.client.operations.get.return_value = _object({"done": True, "response": {"documentName": "documents/new"}})
        self.worker().process_doc(self.row(), self.db)
        self.assertEqual(self.client.file_search_stores.upload_to_file_search_store.call_count, 2)

    async def test_cancellation_keeps_receipt_and_document_queued(self):
        worker = self.worker()
        def poll(_):
            worker.cancelled.set()
            raise TimeoutError("Read timeout")
        self.client.operations.get.side_effect = poll
        with self.assertRaisesRegex(RuntimeError, "Cancelled"):
            worker.process_doc(self.row(), self.db)
        self.flush()
        self.assertEqual(self.row()["uploadOperationName"], "operations/accepted")
        self.assertIsNone(self.row()["error"])
        self.assertIsNone(self.row()["uploadedAt"])

    async def test_existing_database_adds_receipt_columns_without_changing_documents(self):
        self.db.db.close()
        with sqlite3.connect(self.path) as connection:
            connection.execute("ALTER TABLE document DROP COLUMN uploadOperationName")
            connection.execute("ALTER TABLE document DROP COLUMN uploadOperationHash")
        self.db = GeminiDB(self.ctx, self.path)
        self.assertEqual(self.row()["displayName"], "Test")
        self.assertIsNone(self.row()["uploadOperationName"])
        self.assertIsNone(self.row()["uploadOperationHash"])
