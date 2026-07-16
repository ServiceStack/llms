import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock

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
        self.allowed_directories = {}

        def get_allowed_directories(user="default"):
            return self.allowed_directories.get(user or "default", [])

        def set_allowed_directories(paths, user="default"):
            self.allowed_directories[user or "default"] = paths

        self.mock_ctx.get_allowed_directories = get_allowed_directories
        self.mock_ctx.set_allowed_directories = set_allowed_directories

        def resolve_directory(path_str):
            if path_str.startswith("$"):
                return self.mock_ctx.app.aliased_directories.get(path_str) if hasattr(self.mock_ctx, "app") else None
            return os.path.abspath(path_str)
        self.mock_ctx.resolve_directory = resolve_directory

        self.user_prefs = {}

        def get_user_pref(key, user=None):
            return self.user_prefs.get((user, key))

        def set_user_pref(key, value, user=None):
            self.user_prefs[(user, key)] = value

        self.mock_ctx.get_user_pref = get_user_pref
        self.mock_ctx.set_user_pref = set_user_pref

        # Install the projects extension
        install(self.mock_ctx)

        # Retrieve the registered handlers
        self.get_projects_handler = None
        self.save_projects_handler = None
        self.save_project_handler = None
        self.set_active_handler = None

        for args in self.mock_ctx.add_get.call_args_list:
            path, handler = args[0][0], args[0][1]
            if path == "projects.json":
                self.get_projects_handler = handler

        for args in self.mock_ctx.add_post.call_args_list:
            path, handler = args[0][0], args[0][1]
            if path == "projects.json":
                self.save_projects_handler = handler
            elif path == "save/{name}":
                self.save_project_handler = handler
            elif path == "active":
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
            {"name": "Tic Tac Toe", "description": "Creating tic tac toe in React", "paths": ["/path/to/tic-tac-toe"]}
        ]

        with open(projects_file, "w", encoding="utf-8") as f:
            f.write(json.dumps(projects_data))

        request = MagicMock()
        response = await self.get_projects_handler(request)
        self.assertEqual(response.status, 200)
        self.assertEqual(json.loads(response.text), projects_data)

    async def test_save_projects(self):
        projects_data = [
            {"name": "Tic Tac Toe", "description": "Creating tic tac toe in React", "paths": ["/path/to/tic-tac-toe"]},
            {"name": "Workspace Root", "paths": ["$WORKSPACE"]},
        ]

        request = MagicMock()

        async def mock_json():
            return projects_data

        request.json = mock_json

        response = await self.save_projects_handler(request)
        self.assertEqual(response.status, 200)
        self.assertEqual(json.loads(response.text), projects_data)

        # Verify file is saved in the user's projects path
        projects_file = os.path.join(self.temp_dir, "projects", "projects.json")
        self.assertTrue(os.path.exists(projects_file))
        with open(projects_file, encoding="utf-8") as f:
            saved_data = json.load(f)
        self.assertEqual(saved_data, projects_data)

    async def test_set_active_project(self):
        # Setup existing projects
        projects_dir = os.path.join(self.temp_dir, "projects")
        os.makedirs(projects_dir, exist_ok=True)
        projects_file = os.path.join(projects_dir, "projects.json")

        projects_data = [{"name": "My Project", "paths": ["/path/to/my-project"]}]
        with open(projects_file, "w", encoding="utf-8") as f:
            f.write(json.dumps(projects_data))

        # 1. Set active project to "My Project"
        request = MagicMock()

        async def mock_json():
            return {"name": "My Project"}

        request.json = mock_json

        response = await self.set_active_handler(request)
        self.assertEqual(response.status, 200)
        res_data = json.loads(response.text)
        self.assertEqual(res_data["name"], "My Project")

        # Verify user preference set
        self.assertEqual(self.mock_ctx.get_user_pref("project", user="testuser"), "My Project")
        # Verify allowed directories were set
        self.assertEqual(self.allowed_directories.get("testuser"), ["/path/to/my-project"])

        # 2. Reset active project to None (unselected)
        async def mock_json_reset():
            return {"name": None}

        request.json = mock_json_reset

        response = await self.set_active_handler(request)
        self.assertEqual(response.status, 200)
        res_data = json.loads(response.text)
        self.assertIsNone(res_data)

        # Verify user preference cleared
        self.assertIsNone(self.mock_ctx.get_user_pref("project", user="testuser"))

    async def test_save_projects_resets_deleted_active_project(self):
        # Setup active project
        self.mock_ctx.set_user_pref("project", "Old Project", user="testuser")
        self.allowed_directories["testuser"] = ["/some/path"]

        # Save a list that DOES NOT include "Old Project" (it was deleted)
        projects_data = [{"name": "New Project", "paths": ["/some/new/path"]}]
        request = MagicMock()

        async def mock_json():
            return projects_data

        request.json = mock_json

        response = await self.save_projects_handler(request)
        self.assertEqual(response.status, 200)

        # Verify active project preference is cleared
        self.assertIsNone(self.mock_ctx.get_user_pref("project", user="testuser"))
        # Verify allowed directories reset
        self.assertEqual(self.allowed_directories.get("testuser"), [])

    async def test_save_projects_creates_non_existent_paths(self):
        non_existent_dir = os.path.join(self.temp_dir, "new_project_folder")
        self.assertFalse(os.path.exists(non_existent_dir))

        projects_data = [
            {"name": "Tic Tac Toe", "paths": [non_existent_dir, "$WORKSPACE"]}
        ]

        self.mock_ctx.app = MagicMock()
        self.mock_ctx.app.aliased_directories = {"$WORKSPACE": self.temp_dir}

        request = MagicMock()
        async def mock_json():
            return projects_data
        request.json = mock_json

        response = await self.save_projects_handler(request)
        self.assertEqual(response.status, 200)

        self.assertTrue(os.path.exists(non_existent_dir))

    async def test_save_project_new(self):
        project_data = {"name": "New Project", "paths": ["/path/to/new-project"]}
        request = MagicMock()
        request.match_info = {"name": "New Project"}
        async def mock_json():
            return project_data
        request.json = mock_json

        response = await self.save_project_handler(request)
        self.assertEqual(response.status, 200)
        self.assertEqual(json.loads(response.text), [project_data])

        # Verify saved file contents
        projects_file = os.path.join(self.temp_dir, "projects", "projects.json")
        with open(projects_file, encoding="utf-8") as f:
            saved_data = json.load(f)
        self.assertEqual(saved_data, [project_data])

    async def test_save_project_update(self):
        # Setup existing projects
        projects_dir = os.path.join(self.temp_dir, "projects")
        os.makedirs(projects_dir, exist_ok=True)
        projects_file = os.path.join(projects_dir, "projects.json")

        initial_data = [
            {"name": "Project One", "paths": ["/path1"]},
            {"name": "Project Two", "paths": ["/path2"]},
        ]
        with open(projects_file, "w", encoding="utf-8") as f:
            f.write(json.dumps(initial_data))

        updated_project = {"name": "Project One", "paths": ["/path1-updated"], "publish": "dist"}
        request = MagicMock()
        request.match_info = {"name": "Project One"}
        async def mock_json():
            return updated_project
        request.json = mock_json

        response = await self.save_project_handler(request)
        self.assertEqual(response.status, 200)
        self.assertEqual(json.loads(response.text), [
            updated_project,
            {"name": "Project Two", "paths": ["/path2"]},
        ])

        # Verify merged file contents
        with open(projects_file, encoding="utf-8") as f:
            saved_data = json.load(f)
        self.assertEqual(saved_data, [
            updated_project,
            {"name": "Project Two", "paths": ["/path2"]},
        ])

    async def test_save_project_rename_active(self):
        # Setup existing projects & active project preference
        projects_dir = os.path.join(self.temp_dir, "projects")
        os.makedirs(projects_dir, exist_ok=True)
        projects_file = os.path.join(projects_dir, "projects.json")

        initial_data = [{"name": "Old Project Name", "paths": ["/path1"]}]
        with open(projects_file, "w", encoding="utf-8") as f:
            f.write(json.dumps(initial_data))

        self.mock_ctx.set_user_pref("project", "Old Project Name", user="testuser")
        self.allowed_directories["testuser"] = ["/path1"]

        # Rename project
        updated_project = {"name": "New Project Name", "paths": ["/path1"]}
        request = MagicMock()
        request.match_info = {"name": "Old Project Name"}
        async def mock_json():
            return updated_project
        request.json = mock_json

        response = await self.save_project_handler(request)
        self.assertEqual(response.status, 200)

        # Verify preference and allowed directories are updated
        self.assertEqual(self.mock_ctx.get_user_pref("project", user="testuser"), "New Project Name")
        self.assertEqual(self.allowed_directories.get("testuser"), ["/path1"])
