import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, patch

from aiohttp import web
from aiohttp.test_utils import TestServer

from llms.extensions.jev.importing import download_recipe, import_url
from llms.extensions.jev.schema import MAX_BYTES, ValidationError
from llms.extensions.jev.sharing import Sharing
from llms.extensions.jev.storage import ConflictError, write_json
from tests.test_jev import starter


class ImportUrlTests(TestCase):
    def test_share_links_become_json_downloads_and_other_urls_stay_unchanged(self):
        config = {"baseUrl": "https://ai.llmspy.org"}
        for suffix in ("", ".json", "/recipe.json", "?light#result"):
            self.assertEqual(import_url(config, "https://ai.llmspy.org/d/zoL2HG" + suffix), "https://ai.llmspy.org/d/zoL2HG.json")
        other = "https://files.example/export.json?version=2"
        self.assertEqual(import_url(config, other), other)
        self.assertEqual(import_url(config, "https://files.example/d/zoL2HG"), "https://files.example/d/zoL2HG")
        self.assertEqual(import_url({"baseUrl": "https://publisher.example"}, "https://publisher.example/d/abc"), "https://publisher.example/d/abc.json")

    def test_invalid_or_credentialed_urls_are_rejected(self):
        for url in (None, "recipe.json", "file:///etc/passwd", "https://user:secret@files.example/x.json", "https://files.example:bad/x.json"):
            with self.subTest(url=url), self.assertRaises(ValidationError):
                import_url({}, url)


class RecipeDownloadTests(IsolatedAsyncioTestCase):
    async def test_json_redirects_and_attachment_filename_without_credentials(self):
        document = starter()
        seen = []

        async def handler(request):
            seen.append(request)
            if request.path == "/redirect":
                raise web.HTTPFound("/export.json")
            return web.json_response(document, headers={"Content-Disposition": "attachment; filename*=UTF-8''caf%C3%A9.json"})

        app = web.Application()
        app.router.add_get("/{path}", handler)
        async with TestServer(app) as server:
            result = await download_recipe(str(server.make_url("/redirect")))
        self.assertEqual(result["document"], document)
        self.assertEqual(result["filename"], "café.json")
        self.assertTrue(result["downloadUrl"].endswith("/export.json"))
        self.assertEqual(len(seen), 2)
        self.assertTrue(all("Authorization" not in request.headers and "Cookie" not in request.headers for request in seen))

    async def test_invalid_json_invalid_recipe_and_oversized_exports_are_rejected(self):
        async def handler(request):
            values = {"html": "<html>Not an export</html>", "recipe": "{}", "large": " " * (MAX_BYTES + 1), "nonfinite": '{"score":NaN}'}
            return web.Response(text=values[request.match_info["path"]])

        app = web.Application()
        app.router.add_get("/{path}", handler)
        async with TestServer(app) as server:
            for name in ("html", "recipe", "large", "nonfinite"):
                with self.subTest(name=name), self.assertRaises(ValidationError):
                    await download_recipe(str(server.make_url("/" + name)))

    async def test_share_preview_downloads_json_and_preserves_public_example(self):
        fixture = next(case for case in json.loads((Path(__file__).parent / "fixtures/jev-sharing-contract.json").read_text()) if case["name"] == "sentiment" and case["valid"])
        detail = {"externalRef": "abc", "revision": 2, "contentHash": "reviewed", "filename": fixture["filename"], "document": fixture["document"], "execution": fixture["execution"]}
        seen = []

        async def handler(request):
            seen.append(request.path)
            self.assertNotIn("Authorization", request.headers)
            return web.json_response(fixture["document"] if request.path == "/d/abc.json" else detail)

        app = web.Application()
        app.router.add_get("/d/abc.json", handler)
        app.router.add_get("/publish/decision/abc", handler)
        async with TestServer(app) as server:
            with tempfile.TemporaryDirectory() as directory:
                config = {"baseUrl": str(server.make_url("/")).rstrip("/"), "allowHttp": True, "apiKey": "never-send-this"}
                write_json(Path(directory) / "publish/config.json", config)
                ctx = SimpleNamespace(app=SimpleNamespace(publisher_available=True), get_user_path=lambda user=None: directory)
                sharing = Sharing(ctx)
                for suffix in ("", ".json", "/recipe.json"):
                    seen.clear()
                    result = await sharing.preview("alice", config["baseUrl"] + "/d/abc" + suffix)
                    self.assertEqual(seen, ["/d/abc.json", "/publish/decision/abc"])
                    self.assertEqual(result["document"], fixture["document"])
                    self.assertEqual(result["execution"], fixture["execution"])
                    self.assertEqual(result["downloadUrl"], config["baseUrl"] + "/d/abc.json")
                detail["document"] = {**fixture["document"], "description": "Changed during download"}
                with self.assertRaises(ConflictError):
                    await sharing.preview("alice", config["baseUrl"] + "/d/abc")

    async def test_generic_url_does_not_require_publish_extension(self):
        ctx = SimpleNamespace(app=SimpleNamespace(publisher_available=False), get_user_path=lambda user=None: "/nonexistent-import-fixture")
        export = {"document": starter(), "filename": "recipe.json"}
        with patch("llms.extensions.jev.sharing.download_recipe", AsyncMock(return_value=export)) as download:
            result = await Sharing(ctx).preview("alice", "https://files.example/export.json")
        self.assertEqual(result, export)
        download.assert_awaited_once_with("https://files.example/export.json")
