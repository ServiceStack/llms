import unittest
import os
import tempfile
import shutil
from unittest.mock import MagicMock
from aiohttp import web
from llms.extensions.projects import install


class TestProjectsExtension(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.initial_cwd = os.getcwd()
        os.chdir(self.temp_dir)

        # Mock ExtensionContext
        self.mock_ctx = MagicMock()
        self.mock_ctx.get_username.return_value = "testuser"
        self.mock_ctx.get_user_path.return_value = self.temp_dir
        self.allowed_directories = []

        def add_allowed_directory(path):
            self.allowed_directories.append(path)

        self.mock_ctx.add_allowed_directory = add_allowed_directory

        # Install the projects extension
        install(self.mock_ctx)

        # Retrieve the registered handlers
        self.get_projects_handler = None
        self.get_active_handler = None
        self.set_active_handler = None

        for args in self.mock_ctx.add_get.call_args_list:
            path, handler = args[0][0], args[0][1]
            if path == "projects.json":
                self.get_projects_handler = handler
            elif path == "active":
                self.get_active_handler = handler

        for args in self.mock_ctx.add_post.call_args_list:
            path, handler = args[0][0], args[0][1]
            if path == "active":
                self.set_active_handler = handler

    def tearDown(self):
        os.chdir(self.initial_cwd)
        shutil.rmtree(self.temp_dir)

    async def test_get_projects_empty(self):
        request = MagicMock()
        response = await self.get_projects_handler(request)
        self.assertEqual(response.status, 200)
        self.assertEqual(response.text, "[]")

    async def test_get_projects_with_data(self):
        # Create user projects.json
        projects_dir = os.path.join(self.temp_dir, "projects")
        os.makedirs(projects_dir, exist_ok=True)
        projects_file = os.path.join(projects_dir, "projects.json")

        projects_data = [
            {
                "name": "Tic Tac Toe",
                "description": "Creating tic tac toe in React",
                "path": "/path/to/tic-tac-toe"
            }
        ]
        import json
        with open(projects_file, "w", encoding="utf-8") as f:
            f.write(json.dumps(projects_data))

        request = MagicMock()
        response = await self.get_projects_handler(request)
        self.assertEqual(response.status, 200)
        self.assertEqual(json.loads(response.text), projects_data)

    async def test_get_and_set_active_project(self):
        # Create a temp target dir for project
        project_dir = os.path.join(self.temp_dir, "my-project")
        os.makedirs(project_dir, exist_ok=True)

        # Initially active is None
        request = MagicMock()
        response = await self.get_active_handler(request)
        import json
        data = json.loads(response.text)
        self.assertIsNone(data["active"])
        self.assertEqual(data["defaultPath"], self.temp_dir)

        # Set active project
        async def mock_json():
            return {"name": "My Project", "path": project_dir}
        request.json = mock_json

        response = await self.set_active_handler(request)
        self.assertEqual(response.status, 200)
        res_data = json.loads(response.text)
        self.assertEqual(res_data["status"], "ok")
        self.assertEqual(res_data["active"]["name"], "My Project")
        self.assertEqual(res_data["active"]["path"], project_dir)

        # Verify CWD changed
        self.assertEqual(os.getcwd(), project_dir)
        self.assertIn(project_dir, self.allowed_directories)

        # Verify active returns the set project
        response = await self.get_active_handler(request)
        active_data = json.loads(response.text)
        self.assertEqual(active_data["active"]["name"], "My Project")

        # Reset to default
        async def mock_json_reset():
            return {"path": None}
        request.json = mock_json_reset
        response = await self.set_active_handler(request)
        self.assertEqual(response.status, 200)
        self.assertEqual(os.getcwd(), self.temp_dir)

        # Verify active returns None again
        response = await self.get_active_handler(request)
        active_data = json.loads(response.text)
        self.assertIsNone(active_data["active"])
