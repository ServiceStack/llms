"""Repeated imports converge to the same content and metadata as a fresh import."""

import asyncio
import copy
import pathlib
import unittest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import urlsplit

from llms.extensions.gemini import crawl
from llms.extensions.gemini.client import GeminiApiError, _object
from llms.extensions.gemini.tests import test_saved_imports as harness
from llms.extensions.gemini.upload_worker import UploadWorker


class ImportIdempotencyTests(unittest.IsolatedAsyncioTestCase):
    setUp = harness.SavedImportTests.setUp
    call = harness.SavedImportTests.call
    manifest = harness.SavedImportTests.manifest
    sync = harness.SavedImportTests.sync

    def fake_remote(self):
        self.remote = {}
        self.remote_sequence = 0

        def get_remote(name):
            if name not in self.remote:
                raise GeminiApiError(404, "Not found")
            return self.remote[name]

        self.client.file_search_stores.documents.get.side_effect = get_remote
        self.client.file_search_stores.documents.list.side_effect = lambda parent: list(self.remote.values())
        self.client.file_search_stores.documents.delete.side_effect = lambda name, **kwargs: self.remote.pop(name, None)
        self.worker = UploadWorker(self.ctx, self.db, self.client)

        def upload(store_name, path, config):
            self.remote_sequence += 1
            name = f"remote/{self.remote_sequence}"
            self.remote[name] = _object({
                "name": name, "displayName": config["display_name"], "state": "STATE_ACTIVE",
                "sizeBytes": pathlib.Path(path).stat().st_size, "mimeType": "text/markdown",
                "createTime": datetime.now().isoformat(), "updateTime": datetime.now().isoformat(),
                "customMetadata": config["custom_metadata"],
            })
            return _object({"done": True, "response": {"documentName": name}})

        self.worker.upload_with_retry = MagicMock(side_effect=upload)

    def drain(self):
        for doc in list(self.db.documents.values()):
            if not doc.get("uploadedAt") and not doc.get("tombstonedAt"):
                self.worker.process_doc(copy.deepcopy(doc), self.db)

    async def register(self, folder):
        return await self.call("post", "sources/load", {"path": str(folder / "import.json"), "filestoreId": 1})

    async def run_import(self, id, dry_run=False):
        return await self.call("post", "sources/{id}/run", {"dryRun": dry_run}, id=str(id))

    def remote_contents(self):
        # Gemini names and local numeric ids differ between a fresh import and an incremental
        # one. Compare the actual indexed content hashes and retrieval metadata instead.
        return sorted((doc.display_name, tuple(sorted(
            (item.key, str(item.get("string_value") or item.get("string_list_value") or item.get("numeric_value")))
            for item in doc.custom_metadata if item.key != "id"
        ))) for doc in self.remote.values())

    async def test_repeated_previews_and_pending_imports_do_not_duplicate_documents(self):
        folder = self.manifest("docs")
        (folder / "guide.md").write_text("Guide text")
        source = await self.register(folder)
        for _ in range(2):
            preview = await self.run_import(source["id"], dry_run=True)
            self.assertEqual(preview["added"], 1)
            self.assertFalse(self.db.documents)
        first = await self.run_import(source["id"])
        queued = copy.deepcopy(self.db.documents)
        second = await self.run_import(source["id"])
        self.assertEqual(first["queued"], 1)
        self.assertEqual(second["queued"], 0)
        self.assertEqual((second["pendingUploads"], second["uploadTotal"]), (1, 1))
        self.assertEqual(second["unchanged"], 1)
        self.assertEqual(self.db.documents, queued)
        preview = await self.run_import(source["id"], dry_run=True)
        self.assertEqual(preview["embeds"], 0)
        self.assertEqual(preview["pendingUploads"], 1)
        self.assertEqual(self.db.documents, queued)
        self.fake_remote()
        self.drain()
        self.assertEqual(len(self.remote), 1)
        self.assertEqual(self.worker.upload_with_retry.call_count, 1)
        completed = await self.run_import(source["id"])
        self.assertEqual((completed["queued"], completed["pendingUploads"], completed["uploadTotal"]), (0, 0, 0))

    async def test_missing_remote_document_is_repaired_by_run_despite_unchanged_source(self):
        folder = self.manifest("docs")
        (folder / "guide.md").write_text("Guide text")
        source = await self.register(folder)
        self.fake_remote()
        await self.run_import(source["id"])
        self.drain()
        document_id = next(iter(self.db.documents))
        self.remote.clear()
        self.db.documents[document_id]["state"] = "MISSING_FROM_REMOTE"
        preview = await self.run_import(source["id"], dry_run=True)
        self.assertEqual((preview["unchanged"], preview["changed"], preview["embeds"]), (0, 1, 1))
        result = await self.run_import(source["id"])
        self.assertEqual((result["queued"], result["uploadTotal"]), (1, 1))
        self.drain()
        self.assertEqual(set(self.db.documents), {document_id})
        self.assertEqual(len(self.remote), 1)
        self.assertEqual((await self.run_import(source["id"]))["uploadTotal"], 0)

    async def test_incremental_import_matches_fresh_import_and_repeating_is_a_noop(self):
        folder = self.manifest("docs")
        for name in ("change", "metadata", "remove", "unchanged1", "unchanged2"):
            (folder / f"{name}.md").write_text(f"Original {name}")
        (folder / "unchanged2.md").write_text("---\ntitle: Original title\n---\nSame body")
        source = await self.register(folder)
        self.fake_remote()
        self.assertEqual((await self.run_import(source["id"]))["queued"], 5)
        self.drain()
        original_ids = {doc["sourceKey"]: doc["id"] for doc in self.db.documents.values()}
        original_remote = self.remote_contents()
        preview = await self.run_import(source["id"], dry_run=True)
        self.assertEqual(preview["unchanged"], 5)
        self.assertEqual(preview["embeds"], 0)
        self.assertEqual((await self.run_import(source["id"]))["queued"], 0)
        self.drain()
        self.assertEqual(self.worker.upload_with_retry.call_count, 5)
        self.assertEqual(self.remote_contents(), original_remote)
        (folder / "change.md").write_text("Changed content")
        (folder / "new.md").write_text("New content")
        (folder / "unchanged2.md").write_text("---\ntitle: Updated title\n---\nSame body")
        (folder / "remove.md").unlink()
        path = str(folder / "import.json")
        cfg = crawl.read_json(path)
        cfg["metadata"]["rules"] = [{"match": "metadata.md", "set": {"tags": ["updated"]}}]
        crawl.write_json(path, cfg)
        before = copy.deepcopy(self.db.documents)
        preview = await self.run_import(source["id"], dry_run=True)
        self.assertEqual((preview["added"], preview["changed"], preview["metadataOnly"], preview["removed"], preview["unchanged"]), (1, 1, 2, 1, 1))
        self.assertEqual(self.db.documents, before)
        imported = await self.run_import(source["id"])
        self.assertEqual(imported["queued"], 4)
        self.assertEqual(imported["removedApplied"], 1)
        self.drain()
        for doc in self.db.documents.values():
            if doc["sourceKey"] in original_ids:
                self.assertEqual(doc["id"], original_ids[doc["sourceKey"]])
        expected = self.remote_contents()
        self.assertEqual(len(expected), 5)
        self.assertEqual(self.worker.upload_with_retry.call_count, 9)
        unchanged = await self.run_import(source["id"])
        self.assertEqual((unchanged["queued"], unchanged["removedApplied"], unchanged["unchanged"]), (0, 0, 5))
        self.drain()
        self.assertEqual(self.remote_contents(), expected)
        self.assertEqual(self.worker.upload_with_retry.call_count, 9)
        report = await self.sync()
        for name in ("Missing from Local", "Missing from Gemini", "Missing Metadata", "Metadata Mismatch", "Duplicate Documents", "Pending Uploads"):
            self.assertEqual(report[name]["count"], 0, name)
        # Import the final input from scratch, through the very same pipeline and worker.
        self.db.documents.clear()
        self.remote.clear()
        fresh = await self.run_import(source["id"])
        self.assertEqual((fresh["added"], fresh["changed"], fresh["metadataOnly"]), (5, 0, 0))
        self.drain()
        self.assertEqual(self.remote_contents(), expected)

    async def test_simultaneous_first_imports_serialize_the_source_diff(self):
        folder = self.manifest("docs")
        (folder / "guide.md").write_text("Guide text")
        source = await self.register(folder)
        results = await asyncio.gather(self.run_import(source["id"]), self.run_import(source["id"]))
        self.assertEqual([result["queued"] for result in results], [1, 0])
        self.assertEqual(len(self.db.documents), 1)

    async def test_remove_and_reload_keeps_documents_and_remote_identity(self):
        folders = [self.manifest(name) for name in ("first", "second")]
        sources = []
        self.fake_remote()
        for folder in folders:
            (folder / "guide.md").write_text("Identical guide text")
            source = await self.register(folder)
            sources.append(source)
            await self.run_import(source["id"])
        self.drain()
        original_documents = copy.deepcopy(self.db.documents)
        original_remote = copy.deepcopy(self.remote)
        for source in sources:
            await self.call("delete", "sources/{id}", id=str(source["id"]))
        self.assertTrue(all(doc["sourceId"] is None for doc in self.db.documents.values()))
        for folder, original_source in zip(folders, sources):
            restored = await self.register(folder)
            self.assertNotEqual(restored["id"], original_source["id"])
            preview = await self.run_import(restored["id"], dry_run=True)
            self.assertEqual((preview["added"], preview["unchanged"], preview["embeds"]), (0, 1, 0))
            self.assertEqual((await self.run_import(restored["id"]))["queued"], 0)
        self.drain()
        self.assertEqual(self.remote, original_remote)
        self.assertEqual(self.worker.upload_with_retry.call_count, 2)
        for id, document in self.db.documents.items():
            self.assertEqual({key: value for key, value in document.items() if key != "sourceId"},
                             {key: value for key, value in original_documents[id].items() if key != "sourceId"})

    async def test_private_crawl_copy_reuses_documents_after_recrawling_and_reloading(self):
        folder = self.manifest("external-website")
        path = str(folder / "import.json")
        cfg = crawl.read_json(path)
        cfg["crawl"] = {"url": "https://example.test", "generated": ["guide.md"]}
        crawl.write_json(path, cfg)
        (folder / "guide.md").write_text("Guide text")
        source = await self.register(folder)
        owned = pathlib.Path(source["config"]["path"])
        self.fake_remote()
        await self.run_import(source["id"])
        self.drain()
        original_id = next(iter(self.db.documents))
        (folder / "guide.md").write_text("Original workspace changed independently")
        repeated = await self.register(folder)
        self.assertEqual(repeated["id"], source["id"])
        with patch.object(crawl, "crawl_site", AsyncMock(return_value={"path": str(owned)})) as recrawl:
            response = await self.call("post", "imports/crawl", {"sourceId": source["id"], "filestoreId": 1, "url": "https://example.test"})
        self.assertEqual(response["source"]["id"], source["id"])
        self.assertEqual(recrawl.call_args.kwargs["target_path"], str(owned))
        self.assertEqual((await self.run_import(source["id"]))["queued"], 1)
        self.drain()
        self.assertEqual(set(self.db.documents), {original_id})
        original_remote = copy.deepcopy(self.remote)
        original_documents = copy.deepcopy(self.db.documents)
        await self.call("delete", "sources/{id}", id=str(source["id"]))
        restored = await self.register(folder)
        self.assertEqual(restored["config"]["path"], str(owned))
        self.assertEqual((await self.run_import(restored["id"]))["queued"], 0)
        self.drain()
        self.assertEqual(self.worker.upload_with_retry.call_count, 2)
        self.assertEqual(self.remote, original_remote)
        for id, document in self.db.documents.items():
            self.assertEqual({key: value for key, value in document.items() if key != "sourceId"},
                             {key: value for key, value in original_documents[id].items() if key != "sourceId"})

    async def test_recrawl_and_folder_import_sync_latest_pages_without_new_folders_or_duplicates(self):
        folder = self.manifest("external-website")
        path = str(folder / "import.json")
        cfg = crawl.read_json(path)
        cfg["crawl"] = {"url": "https://example.test", "name": "example.test", "respectRobots": False}
        crawl.write_json(path, cfg)
        source = await self.register(folder)
        owned = pathlib.Path(source["config"]["path"])
        self.assertEqual(owned.name, "example.test")
        pages = {
            "/": '<title>Home</title><p>Welcome</p><a href="/guide">Guide</a><a href="/gone">Gone</a>',
            "/guide": '<title>Guide</title><p>Original guide content</p>',
            "/gone": '<title>Gone</title><p>This page will disappear</p>',
        }

        class Response:
            status = 200
            headers = {"Content-Type": "text/html"}
            def __init__(self, url): self.url = url
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            async def text(self, **kwargs): return pages[urlsplit(self.url).path or "/"]

        class Session:
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            def get(self, url, **kwargs): return Response(url)

        async def recrawl():
            result = await self.call("post", "imports/crawl", {"sourceId": source["id"], "filestoreId": 1,
                "url": "https://example.test", "name": "example.test", "respectRobots": False})
            self.assertEqual(result["source"]["id"], source["id"])
            self.assertEqual(result["path"], str(owned))

        self.fake_remote()
        with patch("aiohttp.ClientSession", return_value=Session()):
            await recrawl()
            self.assertEqual((await self.run_import(source["id"]))["queued"], 3)
            self.drain()
            original_ids = {doc["sourceKey"]: doc["id"] for doc in self.db.documents.values()}
            pages["/"] = '<title>Home</title><p>Welcome</p><a href="/guide">Guide</a><a href="/new">New</a>'
            pages["/guide"] = '<title>Guide</title><p>Latest guide content</p>'
            pages["/new"] = '<title>New</title><p>Brand new page</p>'
            del pages["/gone"]
            await recrawl()
            self.assertIn("Latest guide content", (owned / "guide.md").read_text())
            self.assertTrue((owned / "new.md").exists())
            self.assertFalse((owned / "gone.md").exists())
            applied = await self.run_import(source["id"])
            self.assertEqual((applied["added"], applied["changed"], applied["removed"]), (1, 2, 1))
            self.drain()
            self.assertEqual(len(self.remote), 3)
            for doc in self.db.documents.values():
                if doc["sourceKey"] in original_ids:
                    self.assertEqual(doc["id"], original_ids[doc["sourceKey"]])
            expected = copy.deepcopy(self.remote)
            uploads = self.worker.upload_with_retry.call_count
            await recrawl()
            preview = await self.run_import(source["id"], dry_run=True)
            self.assertEqual((preview["added"], preview["changed"], preview["removed"], preview["embeds"]), (0, 0, 0, 0))
            self.assertEqual((await self.run_import(source["id"]))["queued"], 0)
            self.drain()
            self.assertEqual(self.remote, expected)
            self.assertEqual(self.worker.upload_with_retry.call_count, uploads)
            self.assertEqual([p.name for p in owned.parent.iterdir()], ["example.test"])
            self.assertEqual(len(self.db.sources), 1)

    async def test_failed_upstream_delete_is_retried_on_the_next_import(self):
        folder = self.manifest("docs")
        (folder / "remove.md").write_text("Removed content")
        source = await self.register(folder)
        self.fake_remote()
        await self.run_import(source["id"])
        self.drain()
        (folder / "remove.md").unlink()
        self.client.file_search_stores.documents.delete.side_effect = TimeoutError("Provider timed out")
        failed = await self.run_import(source["id"])
        self.assertEqual(failed["removedApplied"], 0)
        self.assertEqual(failed["deleteErrors"][0]["error"], "Provider timed out")
        self.assertFalse(self.db.documents[1].get("tombstonedAt"))
        self.assertEqual(len(self.remote), 1)
        self.assertIn("Provider timed out", self.db.sources[source["id"]]["error"])
        self.client.file_search_stores.documents.delete.side_effect = lambda name, **kwargs: self.remote.pop(name)
        retried = await self.run_import(source["id"])
        self.assertEqual(retried["removedApplied"], 1)
        self.assertEqual(retried["deleteErrors"], [])
        self.assertTrue(self.db.documents[1]["tombstonedAt"])
        self.assertFalse(self.remote)
        self.assertIsNone(self.db.sources[source["id"]]["error"])
        repeated = await self.run_import(source["id"])
        self.assertEqual(repeated["removedApplied"], 0)

    async def test_reimport_retries_a_failed_upload_without_creating_another_document(self):
        folder = self.manifest("docs")
        (folder / "guide.md").write_text("Guide text")
        source = await self.register(folder)
        self.fake_remote()
        upload = self.worker.upload_with_retry.side_effect
        await self.run_import(source["id"])
        self.worker.upload_with_retry.side_effect = TimeoutError("Provider timed out")
        with self.assertRaises(TimeoutError):
            self.drain()
        self.assertEqual(self.db.documents[1]["error"], "Provider timed out")
        self.assertFalse(self.remote)
        preview = await self.run_import(source["id"], dry_run=True)
        self.assertEqual(preview["embeds"], 1)
        self.assertEqual(self.db.documents[1]["error"], "Provider timed out")
        retried = await self.run_import(source["id"])
        self.assertEqual(retried["queued"], 1)
        self.assertEqual(set(self.db.documents), {1})
        self.assertIsNone(self.db.documents[1]["error"])
        self.worker.upload_with_retry.side_effect = upload
        self.drain()
        self.assertEqual(len(self.remote), 1)
        self.assertEqual((await self.run_import(source["id"]))["queued"], 0)
