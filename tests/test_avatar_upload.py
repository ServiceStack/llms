import io
import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock

from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop

from llms.extensions.agents import install as install_agents
from llms.extensions.app import install as install_app
from llms.main import remove_avatar_files


class TestAvatarUpload(AioHTTPTestCase):
    async def get_application(self):
        self.temp_dir = tempfile.mkdtemp()
        self.user_dir = os.path.join(self.temp_dir, "user", "admin")
        os.makedirs(os.path.join(self.user_dir, "profiles", "custom_assistant"), exist_ok=True)

        with open(
            os.path.join(self.user_dir, "profiles", "custom_assistant", "config.json"), "w", encoding="utf-8"
        ) as f:
            json.dump({"name": "Custom Assistant", "model": "gpt-4o"}, f)

        app = web.Application()

        mock_ctx = MagicMock()
        mock_ctx.get_username = lambda req: "admin"
        mock_ctx.get_user_path = lambda user=None: self.user_dir
        mock_ctx.dbg = lambda msg: None
        mock_ctx.err = lambda msg, e: None
        mock_ctx.remove_avatar_files = remove_avatar_files

        routes = []

        def add_get(path, handler):
            routes.append(("GET", path, handler))

        def add_post(path, handler):
            routes.append(("POST", path, handler))

        mock_ctx.add_get = add_get
        mock_ctx.add_post = add_post
        mock_ctx.add_put = MagicMock()
        mock_ctx.add_delete = MagicMock()

        install_app(mock_ctx)
        install_agents(mock_ctx)

        for method, path, handler in routes:
            web_path = path
            if not web_path.startswith("/") and not web_path.startswith("{"):
                web_path = f"/ext/agents/{path}"
            elif web_path.startswith("{"):
                web_path = f"/ext/agents/{path}"

            if method == "GET":
                app.router.add_get(web_path, handler)
            elif method == "POST":
                app.router.add_post(web_path, handler)

        return app

    def tearDown(self):
        super().tearDown()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_remove_avatar_files_function(self):
        # Create dummy avatar files of various extensions and casing
        test_files = [
            "avatar.svg",
            "avatar.png",
            "avatar.WEBP",
            "avatar.dark.jpg",
            "agent.svg",
            "agent.png",
            "agent.light.jpeg",
            "config.json",
            "SYSTEM.md",
        ]
        for tf in test_files:
            with open(os.path.join(self.user_dir, tf), "w") as f:
                f.write("dummy")

        remove_avatar_files(self.user_dir, prefixes=["avatar", "avatar.dark", "avatar.light"])

        # Avatar files removed
        self.assertFalse(os.path.exists(os.path.join(self.user_dir, "avatar.svg")))
        self.assertFalse(os.path.exists(os.path.join(self.user_dir, "avatar.png")))
        self.assertFalse(os.path.exists(os.path.join(self.user_dir, "avatar.WEBP")))
        self.assertFalse(os.path.exists(os.path.join(self.user_dir, "avatar.dark.jpg")))

        # Agent files preserved since not in prefixes
        self.assertTrue(os.path.exists(os.path.join(self.user_dir, "agent.svg")))
        self.assertTrue(os.path.exists(os.path.join(self.user_dir, "agent.png")))
        self.assertTrue(os.path.exists(os.path.join(self.user_dir, "agent.light.jpeg")))

        # Non-avatar files preserved
        self.assertTrue(os.path.exists(os.path.join(self.user_dir, "config.json")))
        self.assertTrue(os.path.exists(os.path.join(self.user_dir, "SYSTEM.md")))

    @unittest_run_loop
    async def test_agent_profile_avatar_upload_overrides_formats(self):
        profile_dir = os.path.join(self.user_dir, "profiles", "custom_assistant")

        # 1. Upload SVG avatar
        resp = await self.client.post(
            "/ext/agents/custom_assistant/avatar", data=b"<svg></svg>", headers={"Content-Type": "image/svg+xml"}
        )
        self.assertEqual(resp.status, 200)
        self.assertTrue(os.path.exists(os.path.join(profile_dir, "avatar.svg")))

        # 2. Upload PNG avatar -> SVG should be removed
        resp = await self.client.post(
            "/ext/agents/custom_assistant/avatar", data=b"fake-png-data", headers={"Content-Type": "image/png"}
        )
        self.assertEqual(resp.status, 200)
        self.assertTrue(os.path.exists(os.path.join(profile_dir, "avatar.png")))
        self.assertFalse(os.path.exists(os.path.join(profile_dir, "avatar.svg")))

        # 3. Upload WEBP avatar -> PNG should be removed
        resp = await self.client.post(
            "/ext/agents/custom_assistant/avatar", data=b"fake-webp-data", headers={"Content-Type": "image/webp"}
        )
        self.assertEqual(resp.status, 200)
        self.assertTrue(os.path.exists(os.path.join(profile_dir, "avatar.webp")))
        self.assertFalse(os.path.exists(os.path.join(profile_dir, "avatar.png")))
