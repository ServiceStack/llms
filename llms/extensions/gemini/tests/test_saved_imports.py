"""Saved import manifests and store sync, with Gemini calls replaced by a fake client."""

import copy
import json
import os
import pathlib
import tempfile
import types
import unittest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

from llms.extensions import gemini
from llms.extensions.gemini import crawl, import_manifest, ingest
from llms.extensions.gemini.client import _object
from llms.extensions.gemini.db import GeminiDB, to_custom_metadata
from llms.extensions.gemini.upload_worker import UploadWorker


class MemoryDB:
    custom_metadata_dto = GeminiDB.custom_metadata_dto

    def __init__(self):
        self.sources, self.documents, self.runs = {}, {}, {}
        self.next_source_id = 1

    def clone(self):
        return self

    def to_dto(self, row, fields):
        # SQLite rows return timestamps as text.
        return json.loads(json.dumps(row, default=lambda value: value.isoformat(" ")))

    def get_filestore(self, id, user=None):
        return {"id": 1, "name": "fileSearchStores/test"} if int(id) == 1 else None

    def get_source(self, id, user=None):
        return copy.deepcopy(self.sources.get(id))

    def query_sources(self, query, user=None):
        return [copy.deepcopy(s) for s in self.sources.values()
                if not query.get("filestoreId") or s["filestoreId"] == int(query["filestoreId"])]

    async def detach_source_documents_async(self, id, user=None, manifest_path=None):
        for document in self.documents.values():
            if document.get("sourceId") == id:
                document["sourceId"] = None
                document["sourceManifestPath"] = manifest_path or document.get("sourceManifestPath")

    async def attach_source_documents_async(self, id, filestore_id, manifest_path, user=None):
        for document in self.documents.values():
            if document.get("sourceId") is None and document["filestoreId"] == int(filestore_id) and document.get("sourceManifestPath") == manifest_path:
                document["sourceId"] = id

    async def relocate_source_documents_async(self, original_path, manifest_path, user=None):
        for document in self.documents.values():
            if document.get("sourceManifestPath") == original_path:
                document["sourceManifestPath"] = manifest_path

    async def delete_source_async(self, id, user=None):
        del self.sources[id]
        self.runs = {key: run for key, run in self.runs.items() if run.get("sourceId") != id}

    async def create_source_async(self, source, user=None):
        id = self.next_source_id
        self.next_source_id += 1
        self.sources[id] = {**copy.deepcopy(source), "id": id}
        return id

    def update_source(self, id, source, user=None):
        self.sources[id].update(copy.deepcopy(source))

    async def update_source_async(self, id, source, user=None):
        self.update_source(id, source, user)

    def documents_by_source_key(self, filestore_id, source_id, user=None):
        return {d["sourceKey"]: copy.deepcopy(d) for d in self.documents.values()
                if d.get("sourceId") == source_id and d["filestoreId"] == filestore_id}

    def query_documents_all(self, query, user=None):
        return [copy.deepcopy(d) for d in self.documents.values() if d["filestoreId"] == query["filestoreId"]]

    async def create_document_async(self, doc, user=None):
        id = len(self.documents) + 1
        self.documents[id] = {**copy.deepcopy(doc), "id": id}
        return id

    async def update_document_async(self, id, doc, user=None):
        self.update_document(id, doc, user)

    async def delete_document_async(self, id, user=None):
        self.documents.pop(int(id), None)

    def update_document(self, id, doc, user=None):
        self.documents[id].update(copy.deepcopy(doc))

    async def create_run_async(self, run, user=None):
        id = len(self.runs) + 1
        self.runs[id] = copy.deepcopy(run)
        return id

    def update_run(self, id, run, user=None):
        self.runs[id].update(copy.deepcopy(run))

    def update_filestore(self, id, store, user=None):
        pass

    def remove_search_document(self, id):
        pass

    def search_stats(self, id, user=None):
        return {}


class SavedImportTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)
        self.db = MemoryDB()
        self.ctx = MagicMock()
        self.ctx.get_user_path.return_value = str(self.root)
        self.ctx.get_config.return_value = {}
        self.ctx.get_username.return_value = "alice"
        self.ctx.is_auth_enabled.return_value = False
        self.ctx.is_admin.return_value = True
        self.ctx.error_message.side_effect = str
        self.ctx.get_file_mime_type.return_value = "text/markdown"
        self.ctx.get_cache_path.side_effect = lambda path: str(self.root / "cache" / path)
        self.ctx.get_allowed_directories.return_value = [str(self.root)]
        self.client = MagicMock()
        self.client.file_search_stores.documents.list.return_value = []
        self.client.file_search_stores.get.return_value = None
        for target, value in (("g_db", self.db), ("GeminiClient", MagicMock(return_value=self.client)),
                              ("UploadWorker", MagicMock()), ("SearchWorker", MagicMock())):
            self.addCleanup(patch.stopall)
            patch.object(gemini, target, value).start()
        patch.dict(os.environ, {"GOOGLE_API_KEY": "test-key"}).start()
        gemini.install(self.ctx)
        self.routes = {}
        for method in ("get", "post", "put", "patch", "delete"):
            for call in getattr(self.ctx, f"add_{method}").call_args_list:
                self.routes[method, call.args[0]] = call.args[1]

    async def call(self, method, path, body=None, query=None, **match):
        request = types.SimpleNamespace(json=AsyncMock(return_value=body or {}), can_read_body=True,
                                        query=query or {}, match_info=match)
        response = await self.routes[method, path](request)
        return json.loads(response.text)

    def manifest(self, name, **settings):
        folder = self.root / name
        folder.mkdir(exist_ok=True)
        crawl.write_json(str(folder / "import.json"), {
            "version": 1, "source": {"type": "folder", "name": name,
                                      "config": {"path": ".", **settings}, "extract": {"minWords": 0}},
            "metadata": {"defaults": {"tags": ["docs"]}, "rules": []},
        })
        return folder

    async def save_source(self, folder):
        source = await self.call("post", "imports/load", {"path": str(folder / "import.json")})
        source["filestoreId"] = 1
        source = await self.call("post", "sources", source)
        await self.call("post", "sources/{id}/run", {"dryRun": False, "saveConfig": True}, id=str(source["id"]))
        return source["id"]

    async def sync(self):
        return await self.call("post", "filestores/{id}/sync", id="1")

    async def test_load_registers_folder_and_crawl_once_without_running(self):
        folder = self.manifest("folder", ignore=["private/"])
        web = self.manifest("web")
        path = str(web / "import.json")
        cfg = crawl.read_json(path)
        cfg["crawl"] = {"url": "https://example.test/", "maxPages": 12}
        crawl.write_json(path, cfg)
        for directory in (folder, web):
            first = await self.call("post", "sources/load", {"path": str(directory / "import.json"), "filestoreId": 1})
            second = await self.call("post", "sources/load", {"path": str(directory / "import.json"), "filestoreId": 1})
            self.assertEqual(first["id"], second["id"])
            self.assertTrue(first["config"]["saved"])
        listed = await self.call("get", "sources", query={"filestoreId": "1"})
        self.assertEqual([source["name"] for source in listed], ["folder", "web"])
        self.assertEqual(listed[0]["config"]["ignore"], ["private/"])
        self.assertEqual(listed[1]["importOptions"]["crawl"], {**cfg["crawl"], "name": "example.test"})
        self.assertFalse(self.db.runs)
        self.assertFalse(self.db.documents)

    async def test_crawl_automatically_registers_in_requested_store(self):
        directory = self.manifest("web")
        result = {"name": "web", "path": str(directory)}
        with patch.object(crawl, "crawl_site", AsyncMock(return_value=result)) as start:
            response = await self.call("post", "imports/crawl", {"url": "https://example.test/", "filestoreId": 1})
        start.assert_awaited_once_with(self.ctx, "alice", {"url": "https://example.test/"})
        self.assertEqual(response["source"]["name"], "web")
        self.assertTrue(response["source"]["config"]["saved"])
        self.assertFalse(self.db.runs)
        self.assertFalse(self.db.documents)

    async def test_crawl_rejects_invalid_store_before_fetching(self):
        with patch.object(crawl, "crawl_site", AsyncMock()) as start:
            with self.assertRaisesRegex(ValueError, "Filestore does not exist"):
                await self.call("post", "imports/crawl", {"url": "https://example.test/", "filestoreId": 99})
        start.assert_not_awaited()

    async def test_loaded_crawl_is_copied_and_recrawled_without_duplicate_registration(self):
        directory = self.manifest("sharpscript.net", ignore=["drafts/"])
        path = directory / "import.json"
        cfg = crawl.read_json(str(path))
        cfg.update({"crawl": {"url": "https://sharpscript.net", "name": "sharpscript.net", "generated": ["guide.md"]},
                    "transforms": [{"pattern": "Original", "replacement": "Transformed"}]})
        cfg["source"]["name"] = "Import sharpscript.net"
        cfg["source"]["category"] = {"prefix": "scripts"}
        crawl.write_json(str(path), cfg)
        (directory / "guide.md").write_text("Original guide")
        (directory / "guides").mkdir()
        crawl.write_json(str(directory / "guides" / "import.json"), {"metadata": {"defaults": {"locale": "en"}}})
        (directory / "guides" / "intro.md").write_text("Nested guide")
        before = path.read_bytes()
        source = await self.call("post", "sources/load", {"path": str(path), "filestoreId": 1})
        copied = pathlib.Path(source["config"]["path"])
        self.assertNotEqual(copied, directory)
        self.assertTrue(ingest.within_roots(str(copied), [crawl.imports_root(self.ctx, "alice")]))
        self.assertEqual((copied / "guide.md").read_text(), "Original guide")
        self.assertEqual((copied / "guides" / "import.json").read_bytes(), (directory / "guides" / "import.json").read_bytes())
        self.assertEqual((copied / "guides" / "intro.md").read_text(), "Nested guide")
        self.assertEqual(source["category"], cfg["source"]["category"])
        self.assertEqual(source["config"]["ignore"], ["drafts/"])
        self.assertEqual(path.read_bytes(), before)
        # Selecting the original again refreshes the same workspace instead of making a copy.
        (copied / "guide.md").write_text("Private guide")
        repeated = await self.call("post", "sources/load", {"path": str(path), "filestoreId": 1})
        self.assertEqual(repeated["id"], source["id"])
        self.assertEqual((copied / "guide.md").read_text(), "Original guide")
        for _ in range(2):
            with patch.object(crawl, "crawl_site", AsyncMock(return_value={"path": str(copied)})) as start:
                result = await self.call("post", "imports/crawl", {"sourceId": source["id"], "filestoreId": 1,
                    "url": "https://sharpscript.net", "maxPages": 42,
                    "sourceSettings": {"category": {"prefix": "scripts"}, "config": {"requireSourceUrl": True}}})
            self.assertEqual(start.call_args.kwargs["target_path"], str(copied))
            self.assertEqual(start.call_args.args[2]["maxPages"], 42)
            self.assertEqual(result["source"]["id"], source["id"])
            self.assertEqual(len(self.db.sources), 1)
        pages = await self.call("get", "imports/{name}/pages", query={"sourceId": str(source["id"])}, name="sharpscript.net")
        self.assertEqual(pages["pages"], ["guide.md"])
        page = await self.call("get", "imports/{name}/page", query={"sourceId": str(source["id"]), "path": "guide.md"}, name="sharpscript.net")
        self.assertEqual(page["content"], "Original guide")
        await self.call("post", "imports/{name}/transform", {"transforms": [{"pattern": "Original", "replacement": "Transformed"}]},
                        query={"sourceId": str(source["id"])}, name="sharpscript.net")
        self.assertEqual((copied / "guide.md").read_text(), "Transformed guide")
        self.assertEqual((directory / "guide.md").read_text(), "Original guide")
        self.assertEqual(path.read_bytes(), before)

    async def test_legacy_registered_crawl_moves_to_private_copy_with_same_source_and_documents(self):
        folder = self.manifest("legacy-site")
        path = str(folder / "import.json")
        cfg = crawl.read_json(path)
        cfg["crawl"] = {"url": "https://example.test"}
        crawl.write_json(path, cfg)
        original = (folder / "import.json").read_bytes()
        source = import_manifest.load(path)
        source.update({"filestoreId": 1, "enabled": 1})
        source["config"]["saved"] = True
        id = await self.db.create_source_async(source, user="alice")
        doc_id = await self.db.create_document_async({"filestoreId": 1, "sourceId": id, "sourceKey": "guide.md", "name": "remote/original"})
        restored = await self.call("post", "sources/load", {"path": path, "filestoreId": 1})
        self.assertEqual(restored["id"], id)
        self.assertNotEqual(restored["config"]["manifestPath"], path)
        self.assertEqual(self.db.documents[doc_id]["sourceId"], id)
        self.assertEqual(self.db.documents[doc_id]["name"], "remote/original")
        self.assertEqual((folder / "import.json").read_bytes(), original)

    async def test_actual_recrawl_only_updates_and_prunes_owned_copy(self):
        folder = self.manifest("external-site")
        path = folder / "import.json"
        cfg = crawl.read_json(str(path))
        cfg["crawl"] = {"url": "https://example.test", "name": "external-site", "generated": ["old.md"]}
        cfg["source"]["config"]["requireSourceUrl"] = True
        crawl.write_json(str(path), cfg)
        (folder / "old.md").write_text("Old generated document")
        (folder / "notes.md").write_text("Hand-authored notes")
        snapshot = {name: (folder / name).read_bytes() for name in ("import.json", "old.md", "notes.md")}
        source = await self.call("post", "sources/load", {"path": str(path), "filestoreId": 1})
        owned = pathlib.Path(source["config"]["path"])

        class Response:
            status = 200
            headers = {"Content-Type": "text/html"}
            url = "https://example.test/"
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            async def text(self, **kwargs): return "<html><head><title>Home</title></head><body><p>Updated site content.</p></body></html>"

        class Session:
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            def get(self, *args, **kwargs): return Response()

        with patch("aiohttp.ClientSession", return_value=Session()):
            for _ in range(2):
                result = await self.call("post", "imports/crawl", {"sourceId": source["id"], "filestoreId": 1,
                    "url": "https://example.test", "name": "renamed-folder", "maxPages": 1, "respectRobots": False})
                self.assertEqual(result["source"]["id"], source["id"])
                self.assertEqual(result["path"], str(owned))
        self.assertFalse((owned / "old.md").exists())
        self.assertIn("Updated site content", (owned / "index.md").read_text())
        self.assertEqual((owned / "notes.md").read_text(), "Hand-authored notes")
        self.assertEqual(crawl.read_json(str(owned / "import.json"))["crawl"]["generated"], ["index.md"])
        for name, content in snapshot.items():
            self.assertEqual((folder / name).read_bytes(), content)
        self.assertFalse(pathlib.Path(crawl.workspace_path(self.ctx, "alice", "renamed-folder")).exists())

    async def test_same_named_crawl_from_different_locations_replaces_one_workspace_and_source(self):
        sources = []
        for name in ("first", "second"):
            folder = self.manifest(name)
            path = str(folder / "import.json")
            cfg = crawl.read_json(path)
            cfg["source"]["name"] = "Import website"
            cfg["crawl"] = {"url": "https://example.test"}
            crawl.write_json(path, cfg)
            (folder / "guide.md").write_text(name)
            if name == "first":
                (folder / "removed.md").write_text("Removed from latest workspace")
            sources.append(await self.call("post", "sources/load", {"path": path, "filestoreId": 1}))
        self.assertEqual([source["name"] for source in sources], ["Import website", "Import website"])
        self.assertEqual(sources[0]["id"], sources[1]["id"])
        self.assertEqual(sources[0]["config"]["path"], sources[1]["config"]["path"])
        workspace = pathlib.Path(crawl.workspace_path(self.ctx, "alice", "example.test"))
        self.assertEqual((workspace / "guide.md").read_text(), "second")
        self.assertFalse((workspace / "removed.md").exists())
        self.assertEqual([p.name for p in workspace.parent.iterdir()], ["example.test"])
        self.assertEqual(len(self.db.sources), 1)

    async def test_suffixed_workspace_migrates_to_plain_name_preserving_documents(self):
        owned = pathlib.Path(crawl.imports_root(self.ctx, "alice"))
        legacy = owned / "sharpscript.net-57a399242d"
        legacy.mkdir(parents=True)
        original_path = str(self.root / "original" / "import.json")
        crawl.write_json(str(legacy / "import.json"), {"importedFrom": original_path,
            "crawl": {"url": "https://sharpscript.net", "name": "sharpscript.net"},
            "source": {"type": "folder", "name": "Import sharpscript.net", "config": {"path": "."}}})
        (legacy / "guide.md").write_text("Latest private guide")
        source = import_manifest.load(str(legacy / "import.json"))
        source["filestoreId"] = 1
        id = await self.db.create_source_async(source, user="alice")
        doc_id = await self.db.create_document_async({"filestoreId": 1, "sourceId": id, "sourceKey": "guide.md",
            "sourceManifestPath": str(legacy / "import.json"), "name": "remote/original"})
        restored = await self.call("get", "sources/{id}", id=str(id))
        target = owned / "sharpscript.net"
        self.assertEqual(restored["id"], id)
        self.assertEqual(restored["config"]["path"], str(target))
        self.assertEqual((target / "guide.md").read_text(), "Latest private guide")
        self.assertFalse(legacy.exists())
        self.assertEqual(self.db.documents[doc_id]["name"], "remote/original")
        self.assertEqual(self.db.documents[doc_id]["sourceId"], id)
        self.assertEqual(self.db.documents[doc_id]["sourceManifestPath"], str(target / "import.json"))

    async def test_crawl_without_source_settings_keeps_original_folder_default_name(self):
        folder = self.manifest("friendly-site")
        path = str(folder / "import.json")
        crawl.write_json(path, {"crawl": {"url": "https://example.test"}})
        original = (folder / "import.json").read_bytes()
        source = await self.call("post", "sources/load", {"path": path, "filestoreId": 1})
        self.assertEqual(source["name"], "Import friendly-site")
        self.assertNotEqual(source["config"]["manifestPath"], path)
        self.assertEqual((folder / "import.json").read_bytes(), original)

    async def test_loaded_crawl_rejects_wrong_store_before_fetching_or_copying(self):
        folder = self.manifest("web")
        path = str(folder / "import.json")
        cfg = crawl.read_json(path)
        cfg["crawl"] = {"url": "https://example.test"}
        crawl.write_json(path, cfg)
        source = await self.call("post", "sources/load", {"path": path, "filestoreId": 1})
        self.db.get_filestore = lambda id, user=None: {"id": int(id)}
        with patch.object(crawl, "crawl_site", AsyncMock()) as start:
            with self.assertRaisesRegex(ValueError, "does not belong"):
                await self.call("post", "imports/crawl", {"sourceId": source["id"], "filestoreId": 2, "url": "https://example.test"})
        start.assert_not_awaited()

    async def test_new_crawl_saves_destination_and_metadata_before_opening_editor(self):
        directory = self.manifest("web")
        settings = {"category": {"root": None, "maxDepth": 0, "prefix": "website/docs"},
                    "config": {"requireSourceUrl": True}, "rules": {"defaults": {"locale": "en"}, "rules": []}}
        with patch.object(crawl, "crawl_site", AsyncMock(return_value={"name": "web", "path": str(directory)})) as start:
            response = await self.call("post", "imports/crawl", {
                "url": "https://example.test", "filestoreId": 1, "sourceSettings": settings})
        start.assert_awaited_once_with(self.ctx, "alice", {"url": "https://example.test"})
        self.assertEqual(response["source"]["category"], settings["category"])
        self.assertTrue(response["source"]["config"]["requireSourceUrl"])
        self.assertEqual(crawl.read_json(str(directory / "import.json"))["metadata"], settings["rules"])

    async def test_remove_keeps_manifest_and_imported_documents(self):
        folder = self.manifest("docs")
        (folder / "guide.md").write_text("Guide text")
        source_id = await self.save_source(folder)
        before = (folder / "import.json").read_bytes()
        original = copy.deepcopy(self.db.documents)
        await self.call("delete", "sources/{id}", query={"documents": "keep"}, id=str(source_id))
        self.assertEqual((folder / "import.json").read_bytes(), before)
        self.assertFalse(self.db.sources)
        self.assertEqual(self.db.documents, {id: {**document, "sourceId": None} for id, document in original.items()})
        self.client.file_search_stores.documents.delete.assert_not_called()

    async def test_load_browse_and_save_portable_complete_configuration(self):
        folder = self.manifest("docs", include=["**/*.md"], ignore=["private/"])
        for name in ("z-last", "a-first"):
            (folder / name).mkdir()
        (folder / "guide.md").write_text("Guide text")
        listing = await self.call("get", "imports/browse", query={"path": str(folder)})
        self.assertEqual(listing["entries"], [
            {"name": "import.json", "path": str(folder / "import.json"), "directory": False},
            *[{"name": name, "path": str(folder / name), "directory": True} for name in ("a-first", "z-last")],
        ])
        source_id = await self.save_source(folder)
        cfg = crawl.read_json(str(folder / "import.json"))
        self.assertEqual(cfg["source"]["config"]["path"], ".")
        self.assertEqual(cfg["source"]["config"]["ignore"], ["private/"])
        self.assertEqual(cfg["source"]["extract"], {"minWords": 0})
        self.assertEqual(self.db.sources[source_id]["config"]["manifestPath"], str(folder / "import.json"))
        result = await self.sync()
        self.assertEqual(result["Source Changes"]["count"], 0)
        self.assertEqual(len(self.db.documents), 1)

    async def test_remove_reload_restores_every_import_setting(self):
        folder = self.manifest("docs")
        path = str(folder / "import.json")
        original = crawl.read_json(path)
        original["crawl"] = {"url": "https://example.test"}
        crawl.write_json(path, original)
        source = await self.call("post", "sources/load", {"path": path, "filestoreId": 1})
        path = source["config"]["manifestPath"]
        settings = {
            "name": "Complete import", "type": "folder",
            "category": {"root": "content", "maxDepth": 0, "prefix": "manuals/framework"},
            "config": {**source["config"], "include": ["**/*.md"], "exclude": ["drafts/**"],
                       "ignore": ["private/", "secret.md"], "requireSourceUrl": True},
            "extract": {"minWords": 0, "maxBytes": 100000}, "chunking": {"maxTokens": 400},
            "volatile": ["Generated: .*"], "onDelete": "ignore", "extractorVer": "custom-v1",
            "rules": {"frontmatter": False, "defaults": {"tags": ["docs"], "locale": "en"},
                      "rules": [{"match": "**/*.md", "set": {"status": "published", "product": "framework"}}]},
            "importOptions": {"crawl": {"url": "https://example.test", "query": {"mode": "allow", "allow": ["lang"]}},
                              "transforms": [{"pattern": "Old", "replacement": "New"}]},
        }
        edited = await self.call("patch", "sources/{id}", {**settings, "saveConfig": True}, id=str(source["id"]))
        cfg = crawl.read_json(path)
        for key in import_manifest.SOURCE_FIELDS:
            self.assertEqual(cfg["source"][key], settings[key], key)
        for key in ("include", "exclude", "ignore", "requireSourceUrl"):
            self.assertEqual(cfg["source"]["config"][key], settings["config"][key], key)
        self.assertEqual(cfg["metadata"], settings["rules"])
        self.assertEqual({key: cfg[key] for key in ("crawl", "transforms")}, settings["importOptions"])
        before = pathlib.Path(path).read_bytes()
        await self.call("delete", "sources/{id}", id=str(source["id"]))
        restored = await self.call("post", "sources/load", {"path": path, "filestoreId": 1})
        self.assertNotEqual(restored["id"], source["id"])
        for key in (*import_manifest.SOURCE_FIELDS, "rules", "importOptions"):
            self.assertEqual(restored[key], edited[key], key)
        self.assertEqual(restored["config"], edited["config"])
        self.assertEqual(pathlib.Path(path).read_bytes(), before)

    async def test_legacy_metadata_category_becomes_destination_prefix(self):
        folder = self.manifest("legacy")
        path = str(folder / "import.json")
        cfg = crawl.read_json(path)
        cfg["metadata"]["defaults"]["category"] = "manuals"
        crawl.write_json(path, cfg)
        source = await self.call("post", "sources/load", {"path": path, "filestoreId": 1})
        self.assertEqual(source["category"]["prefix"], "manuals")
        self.assertEqual(crawl.read_json(path)["source"]["category"]["prefix"], "manuals")

    async def test_sync_all_sources_new_changed_removed_and_disabled(self):
        folders = [self.manifest(name) for name in ("first", "second", "disabled")]
        for folder in folders:
            (folder / "guide.md").write_text("Original text")
            await self.save_source(folder)
        self.db.sources[3]["enabled"] = 0
        original = copy.deepcopy(self.db.documents[1])
        self.db.documents[1].update({"name": "remote/old", "uploadedAt": datetime.now()})
        (folders[0] / "guide.md").write_text("Changed text")
        (folders[1] / "guide.md").unlink()
        for folder in folders:
            (folder / "new.md").write_text("New document")
        result = await self.sync()
        self.assertEqual(result["Saved Imports"]["count"], 2)
        self.assertEqual(result["Source Changes"]["count"], 4)
        self.assertEqual(len(self.db.documents), 5)
        self.assertNotEqual(self.db.documents[1]["contentHash"], original["contentHash"])
        self.assertEqual(self.db.documents[1]["name"], "remote/old")
        self.assertTrue(self.db.documents[2]["tombstonedAt"])
        self.client.file_search_stores.documents.delete.assert_not_called()
        # Reappearing files revive their own row, rather than disappearing or duplicating.
        (folders[1] / "guide.md").write_text("Original text")
        await self.sync()
        self.assertIsNone(self.db.documents[2]["tombstonedAt"])
        self.assertEqual(len(self.db.documents), 5)

    async def test_manifest_edits_apply_without_database_settings_edits(self):
        folder = self.manifest("docs")
        (folder / "keep.md").write_text("Public guide")
        (folder / "private.md").write_text("Private guide")
        await self.save_source(folder)
        cfg = crawl.read_json(str(folder / "import.json"))
        cfg["source"]["config"]["ignore"] = ["private.md"]
        cfg["metadata"]["defaults"]["tags"] = ["updated"]
        cfg["source"]["category"] = {"prefix": "manuals"}
        crawl.write_json(str(folder / "import.json"), cfg)
        result = await self.sync()
        self.assertEqual(result["Source Changes"]["count"], 2)
        keep = next(d for d in self.db.documents.values() if d["sourceKey"] == "keep.md")
        private = next(d for d in self.db.documents.values() if d["sourceKey"] == "private.md")
        self.assertEqual(keep["tags"], ["updated"])
        self.assertEqual(keep["category"], "manuals")
        self.assertTrue(private["tombstonedAt"])

    async def test_missing_manifest_reports_error_and_other_sources_sync(self):
        first, second = self.manifest("first"), self.manifest("second")
        for folder in (first, second):
            (folder / "guide.md").write_text("Guide text")
            await self.save_source(folder)
        (first / "import.json").unlink()
        (second / "new.md").write_text("New document")
        result = await self.sync()
        self.assertEqual(result["Source Errors"]["count"], 1)
        self.assertEqual(result["Saved Imports"]["count"], 1)
        self.assertFalse(self.db.documents[1].get("tombstonedAt"))
        self.assertEqual(len(self.db.documents), 3)

    async def test_remote_list_metadata_serializes_and_missing_remote_requeues(self):
        folder = self.manifest("docs")
        (folder / "guide.md").write_text("Guide text")
        await self.save_source(folder)
        doc = self.db.documents[1]
        doc.update({"uploadedAt": datetime.now(), "name": "remote/guide"})
        remote = _object({"name": "remote/guide", "displayName": "guide.md", "state": "STATE_ACTIVE",
                          "customMetadata": to_custom_metadata(doc)})
        self.client.file_search_stores.documents.list.return_value = [remote]
        result = await self.sync()
        self.assertEqual(result["Summary"]["Matched Documents"], 1)
        metadata = json.loads(self.db.documents[1]["customMetadata"])
        self.assertIn({"key": "tags", "string_list_value": ["docs"]}, metadata)
        self.client.file_search_stores.documents.list.return_value = []
        result = await self.sync()
        self.assertEqual(result["Missing from Gemini"]["count"], 1)
        self.assertEqual(result["Pending Uploads"]["count"], 1)
        self.assertIsNone(doc["uploadedAt"])

    async def test_manifest_paths_are_rechecked_when_syncing_as_nonadmin(self):
        folder = self.manifest("docs")
        (folder / "guide.md").write_text("Guide text")
        await self.save_source(folder)
        self.ctx.is_admin.return_value = False
        cfg = crawl.read_json(str(folder / "import.json"))
        cfg["source"]["config"]["path"] = "/etc"
        crawl.write_json(str(folder / "import.json"), cfg)
        result = await self.sync()
        self.assertEqual(result["Source Errors"]["count"], 1)
        self.assertFalse(self.db.documents[1].get("tombstonedAt"))

    async def test_identical_content_from_two_sources_keeps_separate_identity(self):
        for name in ("first", "second"):
            folder = self.manifest(name)
            (folder / "guide.md").write_text("Same guide content")
            await self.save_source(folder)
        remote = []
        for doc in self.db.documents.values():
            doc.update({"uploadedAt": datetime.now(), "name": f"remote/{doc['id']}"})
            remote.append(_object({"name": doc["name"], "state": "STATE_ACTIVE",
                                   "customMetadata": to_custom_metadata(doc)}))
        self.client.file_search_stores.documents.list.return_value = remote
        result = await self.sync()
        self.assertEqual(result["Summary"]["Matched Documents"], 2)
        self.assertEqual(result["Duplicate Documents"]["count"], 0)
        self.assertEqual(result["Missing from Gemini"]["count"], 0)

    async def test_missing_metadata_repairs_local_row_and_retains_old_remote_name(self):
        folder = self.manifest("docs")
        (folder / "guide.md").write_text("Guide content")
        await self.save_source(folder)
        doc = self.db.documents[1]
        doc.update({"uploadedAt": datetime.now(), "name": "remote/old"})
        self.client.file_search_stores.documents.list.return_value = [
            _object({"name": "remote/old", "state": "STATE_ACTIVE", "customMetadata": []})]
        result = await self.sync()
        self.assertEqual(result["Missing Metadata"]["count"], 1)
        self.assertEqual(result["Missing from Gemini"]["count"], 0)
        self.assertEqual(result["Pending Uploads"]["count"], 1)
        self.assertIsNone(doc["uploadedAt"])
        self.assertEqual(doc["name"], "remote/old")

    async def test_legacy_saved_folder_gets_complete_manifest_link(self):
        folder = self.root / "legacy"
        folder.mkdir()
        (folder / "guide.md").write_text("Guide content")
        source_id = await self.db.create_source_async({"name": "legacy", "filestoreId": 1, "type": "folder",
                                                     "config": {"path": str(folder)}, "extract": {"minWords": 0},
                                                     "lastRunId": 1, "enabled": 1})
        await self.sync()
        self.assertEqual(self.db.sources[source_id]["config"]["manifestPath"], str(folder / "import.json"))
        self.assertEqual(crawl.read_json(str(folder / "import.json"))["source"]["extract"], {"minWords": 0})

    async def test_worker_replaces_same_content_when_metadata_changed(self):
        folder = self.manifest("docs")
        (folder / "guide.md").write_text("Guide content")
        await self.save_source(folder)
        doc = self.db.documents[1]
        doc["name"] = "remote/old"
        prior = _object({"name": "remote/old", "state": "STATE_ACTIVE",
                         "customMetadata": to_custom_metadata({**doc, "tags": ["old"]})})
        replacement = _object({"name": "remote/new", "state": "STATE_ACTIVE",
                               "customMetadata": to_custom_metadata(doc)})
        self.client.file_search_stores.documents.get.side_effect = [prior, replacement]
        worker = UploadWorker(self.ctx, self.db, self.client)
        worker.upload_with_retry = MagicMock(return_value=_object({"done": True, "response": {"documentName": "remote/new"}}))
        worker.process_doc(copy.deepcopy(doc), self.db)
        worker.upload_with_retry.assert_called_once()
        self.client.file_search_stores.documents.delete.assert_called_once_with(name="remote/old", config={"force": True})
        self.assertEqual(doc["name"], "remote/new")
        self.assertIsNotNone(doc["uploadedAt"])

    async def test_failed_replacement_leaves_prior_remote_copy_available(self):
        folder = self.manifest("docs")
        (folder / "guide.md").write_text("Guide content")
        await self.save_source(folder)
        doc = self.db.documents[1]
        doc["name"] = "remote/old"
        self.client.file_search_stores.documents.get.return_value = _object({
            "name": "remote/old", "state": "STATE_ACTIVE",
            "customMetadata": to_custom_metadata({**doc, "hash": "old-hash"}),
        })
        worker = UploadWorker(self.ctx, self.db, self.client)
        worker.upload_with_retry = MagicMock(side_effect=RuntimeError("Upload failed"))
        with self.assertRaisesRegex(RuntimeError, "Upload failed"):
            worker.process_doc(copy.deepcopy(doc), self.db)
        self.client.file_search_stores.documents.delete.assert_not_called()
        self.assertEqual(doc["name"], "remote/old")

    async def test_crawl_is_refreshed_with_saved_options_and_transforms(self):
        folder = pathlib.Path(crawl.workspace_path(self.ctx, "alice", "site"))
        folder.mkdir(parents=True)
        crawl.write_json(str(folder / "import.json"), {"crawl": {"url": "https://example.test", "rules": []},
                                                      "transforms": [{"pattern": "Old", "replacement": "New"}]})
        (folder / "guide.md").write_text("Old text")
        # A metadata-only crawl manifest is also loadable as a source.
        source_id = await self.save_source(folder)
        self.db.sources[source_id]["extract"] = {"minWords": 0}
        self.assertEqual(self.db.sources[source_id]["name"], "Import site")
        with patch.object(crawl, "crawl_site", AsyncMock()) as refresh:
            await self.sync()
            refresh.assert_awaited_once()
            self.assertEqual(refresh.call_args.args[2]["url"], "https://example.test")
            self.assertEqual(refresh.call_args.kwargs["target_path"], str(folder))
        self.assertEqual((folder / "guide.md").read_text(), "New text")

    async def test_save_import_registers_without_running_and_store_sync_can_run_it(self):
        folder = self.manifest("docs")
        (folder / "guide.md").write_text("Guide content")
        source = await self.call("post", "imports/load", {"path": str(folder / "import.json")})
        source.update({"filestoreId": 1, "saveConfig": True})
        saved = await self.call("post", "sources", source)
        listed = await self.call("get", "sources", query={"filestoreId": 1})
        self.assertEqual([s["id"] for s in listed], [saved["id"]])
        self.assertEqual(self.db.documents, {})
        self.assertEqual(self.db.runs, {})
        self.assertNotIn("saved", crawl.read_json(str(folder / "import.json"))["source"]["config"])
        result = await self.sync()
        self.assertEqual(result["Saved Imports"]["count"], 1)
        self.assertEqual(len(self.db.documents), 1)

    async def test_edit_saved_import_updates_manifest_without_touching_documents_or_runs(self):
        folder = self.manifest("docs")
        (folder / "guide.md").write_text("Guide content")
        source_id = await self.save_source(folder)
        before_documents, before_runs = copy.deepcopy(self.db.documents), copy.deepcopy(self.db.runs)
        current = await self.call("get", "sources/{id}", id=str(source_id))
        current["config"]["ignore"] = ["private/"]
        current["category"] = {"prefix": "manuals", "maxDepth": 0}
        current["rules"] = {"defaults": {"tags": ["updated"]}, "rules": []}
        current.update({"name": "Updated docs", "saveConfig": True, "filestoreId": 999, "id": 999})
        saved = await self.call("patch", "sources/{id}", current, id=str(source_id))
        self.assertEqual(saved["id"], source_id)
        self.assertEqual(saved["filestoreId"], 1)
        self.assertEqual(len(self.db.sources), 1)
        self.assertEqual(self.db.documents, before_documents)
        self.assertEqual(self.db.runs, before_runs)
        cfg = crawl.read_json(str(folder / "import.json"))
        self.assertEqual(cfg["source"]["name"], "Updated docs")
        self.assertEqual(cfg["source"]["config"]["ignore"], ["private/"])
        self.assertEqual(cfg["metadata"]["defaults"]["tags"], ["updated"])
        await self.sync()
        self.assertEqual(self.db.documents[1]["category"], "manuals")
        self.assertEqual(self.db.documents[1]["tags"], ["updated"])

    async def test_edit_reloads_current_manifest_and_preserves_crawl_settings(self):
        folder = self.manifest("docs")
        source_id = await self.save_source(folder)
        cfg = crawl.read_json(str(folder / "import.json"))
        cfg["source"]["config"]["ignore"] = ["external-edit.md"]
        cfg["crawl"] = {"url": "https://example.test", "maxPages": 20, "rules": []}
        cfg["transforms"] = [{"pattern": "Old", "replacement": "New", "flags": "g"}]
        crawl.write_json(str(folder / "import.json"), cfg)
        current = await self.call("get", "sources/{id}", id=str(source_id))
        self.assertEqual(current["config"]["ignore"], ["external-edit.md"])
        self.assertEqual(current["importOptions"]["crawl"]["maxPages"], 20)
        current["importOptions"]["crawl"]["maxPages"] = 42
        current["importOptions"]["transforms"][0]["replacement"] = "Updated"
        current["saveConfig"] = True
        await self.call("patch", "sources/{id}", current, id=str(source_id))
        saved = crawl.read_json(current["config"]["manifestPath"])
        self.assertEqual(saved["crawl"]["maxPages"], 42)
        self.assertEqual(saved["transforms"][0]["replacement"], "Updated")

    async def test_duplicate_name_and_invalid_transforms_do_not_overwrite_saved_import(self):
        sources = []
        for name in ("first", "second"):
            folder = self.manifest(name)
            source = await self.call("post", "imports/load", {"path": str(folder / "import.json")})
            source.update({"filestoreId": 1, "saveConfig": True})
            sources.append(await self.call("post", "sources", source))
        second = sources[1]
        path = pathlib.Path(second["config"]["manifestPath"])
        before = path.read_text()
        with self.assertRaisesRegex(Exception, "already exists"):
            await self.call("patch", "sources/{id}", {"name": "first", "saveConfig": True}, id=str(second["id"]))
        with self.assertRaisesRegex(ValueError, "invalid"):
            await self.call("patch", "sources/{id}", {"saveConfig": True, "importOptions": {"transforms": [{"pattern": "["}]}}, id=str(second["id"]))
        self.assertEqual(path.read_text(), before)
        self.assertEqual(self.db.sources[second["id"]]["name"], "second")


class ManifestRuleTests(unittest.TestCase):
    def test_nested_ignores_are_relative_and_child_metadata_wins(self):
        with tempfile.TemporaryDirectory() as root:
            root = pathlib.Path(root)
            for name in ("keep.md", "drafts/no.md", "docs/keep.md", "docs/private/no.md", "docs/ignore.txt"):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("Some guide content")
            crawl.write_json(str(root / "import.json"), {"ignore": ["drafts/"], "metadata": {"defaults": {"status": "draft"}}})
            crawl.write_json(str(root / "docs/import.json"), {"ignore": ["private/", "*.txt"],
                                                             "metadata": {"defaults": {"status": "published"}}})
            source = ingest.FolderSource(None, {"path": str(root)})
            plan = ingest.build_plan({"extract": {"minWords": 0}}, source, {})
            self.assertEqual({d["sourceKey"] for d in plan.add}, {"keep.md", "docs/keep.md"})
            self.assertEqual(next(d["status"] for d in plan.add if d["sourceKey"] == "docs/keep.md"), "published")

    def test_invalid_manifest_and_patterns_fail_instead_of_becoming_empty_import(self):
        with tempfile.TemporaryDirectory() as root:
            path = pathlib.Path(root, "import.json")
            path.write_text("[]")
            with self.assertRaises(ValueError):
                import_manifest.load(str(path))
            path.write_text('{"ignore": [17]}')
            with self.assertRaises(ValueError):
                import_manifest.load(str(path))


if __name__ == "__main__":
    unittest.main()
