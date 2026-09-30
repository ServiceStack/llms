import tempfile
import unittest
from pathlib import Path

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from llms.web_assets import asset_response, compress_responses


class TestWebAssets(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "module.mjs"
        self.content = b"export const message = 'hello';\n" * 1000
        self.path.write_bytes(self.content)

        async def asset(request):
            return await asset_response(request, self.path)

        async def models(request):
            return web.json_response({"models": ["example-model"] * 1000})

        async def stream(request):
            response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
            await response.prepare(request)
            await response.write(b"data: hello\n\n")
            return response

        app = web.Application(middlewares=[compress_responses])
        app.router.add_get("/asset", asset)
        app.router.add_get("/models", models)
        app.router.add_get("/stream", stream)
        self.client = TestClient(TestServer(app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self.directory.cleanup()

    async def test_compressed_asset_preserves_content(self):
        response = await self.client.get("/asset", headers={"Accept-Encoding": "gzip"})
        self.assertEqual(response.headers["Content-Encoding"], "gzip")
        self.assertEqual(response.headers["Cache-Control"], "no-cache")
        self.assertIn("Accept-Encoding", response.headers["Vary"])
        self.assertEqual(await response.read(), self.content)

    async def test_identity_asset_and_head(self):
        response = await self.client.get("/asset", headers={"Accept-Encoding": "identity"})
        self.assertNotIn("Content-Encoding", response.headers)
        self.assertEqual(await response.read(), self.content)
        head = await self.client.head("/asset")
        self.assertEqual(await head.read(), b"")
        self.assertIn("ETag", head.headers)

    async def test_revalidation_and_updated_assets(self):
        response = await self.client.get("/asset")
        etag = response.headers["ETag"]
        await response.read()
        for validator in (etag, etag.removeprefix("W/"), '"other", ' + etag, "*"):
            cached = await self.client.get("/asset", headers={"If-None-Match": validator})
            self.assertEqual(cached.status, 304)
            self.assertEqual(await cached.read(), b"")
        self.path.write_bytes(b"changed")
        updated = await self.client.get("/asset", headers={"If-None-Match": etag})
        self.assertEqual(updated.status, 200)
        self.assertNotEqual(updated.headers["ETag"], etag)
        self.assertEqual(await updated.read(), b"changed")

    async def test_large_json_compression(self):
        response = await self.client.get("/models", headers={"Accept-Encoding": "gzip"})
        self.assertEqual(response.headers["Content-Encoding"], "gzip")
        self.assertEqual(len((await response.json())["models"]), 1000)

    async def test_event_stream_is_not_buffered_or_compressed(self):
        response = await self.client.get("/stream", headers={"Accept-Encoding": "gzip"})
        self.assertNotIn("Content-Encoding", response.headers)
        self.assertEqual(await response.read(), b"data: hello\n\n")
