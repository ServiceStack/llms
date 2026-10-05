"""Saved import document identity survives removal, SQLite reopen, and re-registration."""

import os
import tempfile
import unittest

from llms.extensions.gemini.db import GeminiDB


class ImportPersistenceTests(unittest.IsolatedAsyncioTestCase):
    class Context:
        debug = False

        def dbg(self, *_): pass
        def log(self, *_): pass
        def err(self, *args): raise AssertionError(args)

    async def test_upload_progress_is_scoped_to_source_store_user_and_live_documents(self):
        with tempfile.TemporaryDirectory() as root:
            db = GeminiDB(self.Context(), os.path.join(root, "gemini.sqlite"))
            try:
                rows = [(1, "alice", 18, {}), (1, "alice", 18, {"startedAt": "2026-10-04"}),
                        (1, "alice", 18, {"error": "Failed"}),
                        (1, "alice", 18, {"uploadedAt": "2026-10-04"}),
                        (1, "alice", 18, {"tombstonedAt": "2026-10-04"}),
                        (1, "alice", 19, {}), (2, "alice", 18, {}), (1, "bob", 18, {})]
                for store, user, source_id, fields in rows:
                    await db.create_document_async({"filestoreId": store, "sourceId": source_id,
                        "displayName": "Guide", **fields}, user=user)
                counts = db.source_upload_counts(1, user="alice")
                self.assertEqual(counts["18"], {"sourceId": 18, "total": 4, "pending": 2,
                                                "uploading": 1, "failed": 1})
                self.assertEqual(counts["19"]["pending"], 1)
                self.assertEqual(set(counts), {"18", "19"})
            finally:
                db.db.close()

    async def test_detach_and_restore_same_keys_are_scoped_to_manifest_store_and_user(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "gemini.sqlite")
            db = GeminiDB(self.Context(), path)
            try:
                identities = [(1, "alice", "/first/import.json"), (1, "alice", "/second/import.json"),
                              (1, "bob", "/first/import.json"), (2, "alice", "/first/import.json")]
                ids = []
                for store, user, manifest in identities:
                    source_id = await db.create_source_async({"filestoreId": store, "type": "folder",
                        "name": user + manifest, "config": {"manifestPath": manifest}}, user=user)
                    document_id = await db.create_document_async({"filestoreId": store, "sourceId": source_id,
                        "sourceKey": "guide.md", "displayName": "Guide", "name": f"remote/{source_id}"}, user=user)
                    ids.append(document_id)
                    await db.detach_source_documents_async(source_id, user=user, manifest_path=manifest)
                    await db.delete_source_async(source_id, user=user)
                    self.assertIsNone(db.get_source(source_id, user=user))
                    self.assertIsNone(db.get_document(document_id, user=user)["sourceId"])
            finally:
                db.db.close()
            db = GeminiDB(self.Context(), path)
            try:
                await db.relocate_source_documents_async("/first/import.json", "/plain/import.json", user="alice")
                restored = await db.create_source_async({"filestoreId": 1, "type": "folder", "name": "Restored"}, user="alice")
                await db.attach_source_documents_async(restored, 1, "/plain/import.json", user="alice")
                self.assertEqual(list(db.documents_by_source_key(1, restored, user="alice")), ["guide.md"])
                for position, (store, user, manifest) in enumerate(identities):
                    document = db.get_document(ids[position], user=user)
                    expected_manifest = "/plain/import.json" if user == "alice" and manifest == "/first/import.json" else manifest
                    self.assertEqual(document["sourceManifestPath"], expected_manifest)
                    self.assertEqual(document["name"], f"remote/{position + 1}")
                    self.assertEqual(document["sourceId"], restored if position == 0 else None)
            finally:
                db.db.close()
