import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock

from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase

from llms.extensions.agents import install


class TestAgentsProfileManager(AioHTTPTestCase):
    async def get_application(self):
        self.temp_dir = tempfile.mkdtemp()
        self.user_dir = os.path.join(self.temp_dir, "user", "admin")
        os.makedirs(os.path.join(self.user_dir, "profiles", "custom_assistant"), exist_ok=True)

        with open(os.path.join(self.user_dir, "profiles", "custom_assistant", "config.json"), "w", encoding="utf-8") as f:
            json.dump({"name": "Custom Assistant", "model": "gpt-4o", "theme": "dark"}, f)

        with open(os.path.join(self.user_dir, "profiles", "custom_assistant", "SYSTEM.template"), "w", encoding="utf-8") as f:
            f.write("Hello {IDENTITY}")

        with open(os.path.join(self.user_dir, "profiles", "custom_assistant", "IDENTITY.md"), "w", encoding="utf-8") as f:
            f.write("I am Custom Assistant")

        app = web.Application()

        mock_ctx = MagicMock()
        mock_ctx.get_username = lambda req: "admin"
        mock_ctx.get_user_path = lambda user=None: self.user_dir
        mock_ctx.dbg = lambda msg: None
        mock_ctx.app.tools = {"run_bash": lambda: None, "read_file": lambda: None}
        mock_ctx.app.tool_groups = {"skills": ["create_plan"]}

        routes = []

        def add_get(path, handler):
            routes.append(("GET", path, handler))

        def add_post(path, handler):
            routes.append(("POST", path, handler))

        def add_put(path, handler):
            routes.append(("PUT", path, handler))

        def add_delete(path, handler):
            routes.append(("DELETE", path, handler))

        mock_ctx.add_get = add_get
        mock_ctx.add_post = add_post
        mock_ctx.add_put = add_put
        mock_ctx.add_delete = add_delete

        install(mock_ctx)

        for method, path, handler in routes:
            web_path = f"/ext/agents/{path}".rstrip("/")
            if path == "":
                web_path = "/ext/agents"
            if method == "GET":
                app.router.add_get(web_path, handler)
            elif method == "POST":
                app.router.add_post(web_path, handler)
            elif method == "PUT":
                app.router.add_put(web_path, handler)
            elif method == "DELETE":
                app.router.add_delete(web_path, handler)

        return app

    def tearDown(self):
        super().tearDown()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    async def test_get_profiles_and_is_builtin(self):
        resp = await self.client.get("/ext/agents")
        self.assertEqual(resp.status, 200)
        data = await resp.json()

        # Built-in profile 'chat' should be present and marked as built-in
        self.assertIn("chat", data)
        self.assertTrue(data["chat"]["isBuiltIn"])

        # User profile 'custom_assistant' should be present and NOT built-in
        self.assertIn("custom_assistant", data)
        self.assertFalse(data["custom_assistant"]["isBuiltIn"])
        self.assertIn("SYSTEM.template", data["custom_assistant"]["files"])
        self.assertIn("IDENTITY.md", data["custom_assistant"]["files"])

    async def test_tools_and_skills_endpoint(self):
        resp = await self.client.get("/ext/agents/tools-skills")
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertIn("run_bash", data["tools"])
        self.assertIn("create_plan", data["skills"])

    async def test_read_and_write_file_for_user_profile(self):
        # Read file
        resp = await self.client.get("/ext/agents/custom_assistant/files/IDENTITY.md")
        self.assertEqual(resp.status, 200)
        text = await resp.text()
        self.assertEqual(text, "I am Custom Assistant")

        # Update file
        new_content = "Updated Identity Text"
        resp = await self.client.put("/ext/agents/custom_assistant/files/IDENTITY.md", data=new_content)
        self.assertEqual(resp.status, 200)

        # Verify update on disk
        resp = await self.client.get("/ext/agents/custom_assistant/files/IDENTITY.md")
        self.assertEqual(await resp.text(), new_content)

    async def test_builtin_profile_write_forbidden(self):
        # Updating a built-in profile file should return 403 Forbidden
        resp = await self.client.put("/ext/agents/chat/files/SYSTEM.md", data="Hack")
        self.assertEqual(resp.status, 403)

        # Updating built-in config should return 403 Forbidden
        resp = await self.client.post("/ext/agents/chat/config", json={"name": "Hacked Chat"})
        self.assertEqual(resp.status, 403)

    async def test_update_user_profile_config(self):
        update_data = {
            "name": "Renamed Assistant",
            "model": "claude-3-5-sonnet",
            "theme": "nord",
            "onlyTools": ["run_bash"],
            "onlySkills": ["create_plan"]
        }
        resp = await self.client.post("/ext/agents/custom_assistant/config", json=update_data)
        self.assertEqual(resp.status, 200)
        json_data = await resp.json()
        self.assertEqual(json_data["name"], "Renamed Assistant")
        self.assertEqual(json_data["onlyTools"], ["run_bash"])

    async def test_upload_avatar_for_user_profile(self):
        resp = await self.client.post("/ext/agents/custom_assistant/avatar", data=b"fake-image-data", headers={"Content-Type": "image/png"})
        self.assertEqual(resp.status, 200)
        json_data = await resp.json()
        self.assertEqual(json_data["status"], "ok")

    async def test_create_profile(self):
        resp = await self.client.post("/ext/agents", json={"name": "Code Reviewer"})
        self.assertEqual(resp.status, 200)
        json_data = await resp.json()
        self.assertEqual(json_data["status"], "ok")
        self.assertEqual(json_data["id"], "code-reviewer")

        # Verify disk creation of config.json and SYSTEM.md
        profile_dir = os.path.join(self.user_dir, "profiles", "code-reviewer")
        self.assertTrue(os.path.exists(os.path.join(profile_dir, "config.json")))
        self.assertTrue(os.path.exists(os.path.join(profile_dir, "SYSTEM.md")))

        # Test dynamic avatar for new profile
        avatar_resp = await self.client.get("/ext/agents/code-reviewer/avatar")
        self.assertEqual(avatar_resp.status, 200)
        svg_text = await avatar_resp.text()
        self.assertIn("<svg", svg_text)
        self.assertIn(">C<", svg_text)

    async def test_delete_profile(self):
        # Attempt to delete built-in profile should fail with 403
        resp = await self.client.delete("/ext/agents/chat")
        self.assertEqual(resp.status, 403)

        # Delete user profile 'custom_assistant'
        resp = await self.client.delete("/ext/agents/custom_assistant")
        self.assertEqual(resp.status, 200)
        json_data = await resp.json()
        self.assertEqual(json_data["status"], "ok")

        # Verify folder removed from disk
        profile_dir = os.path.join(self.user_dir, "profiles", "custom_assistant")
        self.assertFalse(os.path.exists(profile_dir))

    async def test_system_template_and_md_renaming(self):
        # Create a new profile (starts with empty SYSTEM.md)
        await self.client.post("/ext/agents", json={"name": "Rename Test"})
        profile_dir = os.path.join(self.user_dir, "profiles", "rename-test")
        self.assertTrue(os.path.exists(os.path.join(profile_dir, "SYSTEM.md")))
        self.assertFalse(os.path.exists(os.path.join(profile_dir, "SYSTEM.template")))

        # Create SYSTEM.template -> should rename SYSTEM.md to SYSTEM.template
        resp = await self.client.post("/ext/agents/rename-test/files", json={"filename": "SYSTEM.template", "content": "Template Content"})
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertEqual(data["filename"], "SYSTEM.template")
        self.assertTrue(os.path.exists(os.path.join(profile_dir, "SYSTEM.template")))
        self.assertFalse(os.path.exists(os.path.join(profile_dir, "SYSTEM.md")))

        # Create SYSTEM.md -> should rename SYSTEM.template to SYSTEM.md
        resp = await self.client.post("/ext/agents/rename-test/files", json={"filename": "SYSTEM.md", "content": "MD Content"})
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertEqual(data["filename"], "SYSTEM.md")
        self.assertTrue(os.path.exists(os.path.join(profile_dir, "SYSTEM.md")))
        self.assertFalse(os.path.exists(os.path.join(profile_dir, "SYSTEM.template")))


if __name__ == "__main__":
    unittest.main()
