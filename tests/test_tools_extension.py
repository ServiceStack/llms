import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock

from llms.extensions.tools import install


class TestToolsExtension(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.initial_cwd = os.getcwd()
        os.chdir(self.temp_dir)

        # Mock ExtensionContext
        self.mock_ctx = MagicMock()
        self.mock_ctx.path = os.path.join(self.temp_dir, "extensions", "tools")
        os.makedirs(os.path.join(self.mock_ctx.path, "ui"), exist_ok=True)

        self.user_paths = {}

        def get_user_path(user=None):
            if not user:
                user = "default"
            path = os.path.join(self.temp_dir, "user", user)
            os.makedirs(path, exist_ok=True)
            return path

        self.mock_ctx.app.get_user_path = get_user_path

        # Install the tools extension
        install(self.mock_ctx)

        # Retrieve the registered server_tools_handler
        self.server_tools_handler = None
        for args in self.mock_ctx.add_get.call_args_list:
            path, handler = args[0][0], args[0][1]
            if path == "server":
                self.server_tools_handler = handler

    def tearDown(self):
        os.chdir(self.initial_cwd)
        shutil.rmtree(self.temp_dir)

    async def test_server_tools_fallback_to_ui(self):
        # Create default file in extensions/tools/ui/providers-tools.json
        fallback_file = os.path.join(self.mock_ctx.path, "ui", "server-tools.json")
        expected_data = [{"tool": "default_ui_tool"}]
        with open(fallback_file, "w", encoding="utf-8") as f:
            json.dump(expected_data, f)

        self.mock_ctx.get_username.return_value = None

        request = MagicMock()
        response = await self.server_tools_handler(request)
        self.assertEqual(response.status, 200)
        self.assertEqual(json.loads(response.text), expected_data)

    async def test_server_tools_user_override(self):
        # Create user override file
        self.mock_ctx.get_username.return_value = "john"
        user_dir = self.mock_ctx.app.get_user_path("john")
        override_file = os.path.join(user_dir, "server-tools.json")
        expected_data = [{"tool": "johns_custom_tool"}]
        with open(override_file, "w", encoding="utf-8") as f:
            json.dump(expected_data, f)

        request = MagicMock()
        response = await self.server_tools_handler(request)
        self.assertEqual(response.status, 200)
        self.assertEqual(json.loads(response.text), expected_data)

    async def test_server_tools_default_override(self):
        # Create default override file
        self.mock_ctx.get_username.return_value = None
        default_dir = self.mock_ctx.app.get_user_path("default")
        override_file = os.path.join(default_dir, "server-tools.json")
        expected_data = [{"tool": "default_override_tool"}]
        with open(override_file, "w", encoding="utf-8") as f:
            json.dump(expected_data, f)

        request = MagicMock()
        response = await self.server_tools_handler(request)
        self.assertEqual(response.status, 200)
        self.assertEqual(json.loads(response.text), expected_data)
