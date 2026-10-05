import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock
from aiohttp import web

from llms.extensions.projects import install, kebab_case


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
        self.set_sidebar_visibility_handler = None

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
            elif path == "order":
                self.order_handler = handler
        for args in self.mock_ctx.add_patch.call_args_list:
            if args[0][0] == "sidebar/{id}":
                self.set_sidebar_visibility_handler = args[0][1]
            elif args[0][0] == "archive/{id}":
                self.archive_handler = args[0][1]

    def request(self, data, **match_info):
        request = MagicMock()
        async def body():
            return data
        request.json = body
        request.match_info = match_info
        return request

    async def seed_organization(self):
        response = await self.save_projects_handler(self.request([
            {'name': 'One', 'folder': 'one', 'description': 'First'},
            {'name': 'Two', 'folder': 'two', 'showInSidebar': False},
            {'name': 'Three', 'folder': 'three'},
        ]))
        return json.loads(response.text)

    async def test_order_preserves_metadata_and_rejects_stale_membership(self):
        projects = await self.seed_organization()
        ids = [p['id'] for p in projects]
        response = await self.order_handler(self.request({'ids': ids[::-1]}))
        self.assertEqual(json.loads(response.text), projects[::-1])
        self.assertEqual(self.mock_ctx.projects.get_user_projects('testuser'), projects[::-1])
        self.mock_ctx.notify_sidebar.assert_called()
        for invalid in [ids[:2], [*ids, 'foreign']]:
            with self.assertRaises(web.HTTPConflict):
                await self.order_handler(self.request({'ids': invalid}))
        for invalid in [[ids[0], ids[0]], 'bad', [1]]:
            with self.assertRaises(web.HTTPBadRequest):
                await self.order_handler(self.request({'ids': invalid}))
        self.assertEqual(self.mock_ctx.projects.get_user_projects('testuser'), projects[::-1])

    async def test_archive_hides_and_restores_without_changing_workspace(self):
        projects = await self.seed_organization()
        one, two, three = projects
        workspace = self.mock_ctx.projects.resolve_workspace(one['id'], 'testuser')
        file = os.path.join(workspace['directories'][0], 'keep.txt')
        with open(file, 'w') as f:
            f.write('keep')
        await self.set_active_handler(self.request({'name': 'One'}))
        response = await self.archive_handler(self.request({'archived': True}, id=one['id']))
        archived = json.loads(response.text)
        self.assertEqual([p['id'] for p in archived], [two['id'], three['id'], one['id']])
        self.assertTrue(archived[-1]['archived'])
        self.assertFalse(archived[-1]['showInSidebar'])
        self.assertIsNone(self.mock_ctx.get_user_pref('project', user='testuser'))
        self.assertEqual(self.allowed_directories['testuser'], [])
        self.assertEqual(self.mock_ctx.projects.resolve_workspace(one['id'], 'testuser'), workspace)
        self.assertTrue(os.path.isfile(file))
        with self.assertRaises(web.HTTPConflict):
            await self.set_sidebar_visibility_handler(self.request({'showInSidebar': True}, id=one['id']))
        with self.assertRaises(web.HTTPConflict):
            await self.set_active_handler(self.request({'name': 'One'}))
        with self.assertRaises(web.HTTPConflict):
            await self.order_handler(self.request({'ids': [p['id'] for p in projects]}))
        await self.order_handler(self.request({'ids': [three['id'], two['id']]}))
        response = await self.archive_handler(self.request({'archived': False}, id=one['id']))
        restored = json.loads(response.text)
        self.assertEqual([p['id'] for p in restored], [three['id'], two['id'], one['id']])
        self.assertTrue(restored[-1]['showInSidebar'])
        self.assertNotIn('archivedSidebarVisibility', restored[-1])
        repeated = await self.archive_handler(self.request({'archived': False}, id=one['id']))
        self.assertEqual(json.loads(repeated.text), restored)

    async def test_unarchive_preserves_previously_hidden_folder(self):
        projects = await self.seed_organization()
        project_id = projects[1]['id']
        await self.archive_handler(self.request({'archived': True}, id=project_id))
        await self.archive_handler(self.request({'archived': True}, id=project_id))
        response = await self.archive_handler(self.request({'archived': False}, id=project_id))
        project = next(p for p in json.loads(response.text) if p['id'] == project_id)
        self.assertFalse(project['showInSidebar'])

    async def test_legacy_saves_cannot_unarchive_or_delete_omitted_archives(self):
        projects = await self.seed_organization()
        one = projects[0]
        await self.archive_handler(self.request({'archived': True}, id=one['id']))
        response = await self.save_project_handler(self.request({**one, 'archived': False, 'showInSidebar': True}, name='One'))
        archived = next(p for p in json.loads(response.text) if p['id'] == one['id'])
        self.assertTrue(archived['archived'])
        self.assertFalse(archived['showInSidebar'])
        response = await self.save_projects_handler(self.request(projects[1:]))
        retained = json.loads(response.text)
        self.assertEqual(retained[-1], archived)
        response = await self.save_projects_handler(self.request([{**p, 'archived': False} for p in retained]))
        self.assertTrue(json.loads(response.text)[-1]['archived'])

    async def test_archive_validates_state_and_project_ownership(self):
        projects = await self.seed_organization()
        with self.assertRaises(web.HTTPBadRequest):
            await self.archive_handler(self.request({'archived': 'true'}, id=projects[0]['id']))
        with self.assertRaises(web.HTTPNotFound):
            await self.archive_handler(self.request({'archived': True}, id='foreign'))
        self.assertEqual(self.mock_ctx.projects.get_user_projects('testuser'), projects)

    def tearDown(self):
        os.chdir(self.initial_cwd)
        shutil.rmtree(self.temp_dir)

    def test_kebab_case(self):
        self.assertEqual(kebab_case("Tic Tac Toe"), "tic-tac-toe")
        self.assertEqual(kebab_case("Breakout"), "breakout")
        self.assertEqual(kebab_case("2048"), "2048")
        self.assertEqual(kebab_case("My App (v2)"), "my-app-v2")

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
            {"name": "Tic Tac Toe", "folder": "tic-tac-toe", "description": "Creating tic tac toe in React"}
        ]

        with open(projects_file, "w", encoding="utf-8") as f:
            f.write(json.dumps(projects_data))

        request = MagicMock()
        response = await self.get_projects_handler(request)
        self.assertEqual(response.status, 200)
        result = json.loads(response.text)
        self.assertTrue(result[0]["id"])
        self.assertEqual(json.loads((await self.get_projects_handler(request)).text), result)
        self.assertEqual([{k:v for k,v in p.items() if k != "id"} for p in result],
                         [{k:v for k,v in p.items() if k != "id"} for p in projects_data])

    async def test_save_projects(self):
        projects_data = [
            {"name": "Tic Tac Toe", "folder": "tic-tac-toe", "description": "Creating tic tac toe in React"},
            {"name": "Workspace Root", "folder": "workspace-root"},
        ]

        request = MagicMock()

        async def mock_json():
            return projects_data

        request.json = mock_json

        response = await self.save_projects_handler(request)
        self.assertEqual(response.status, 200)
        result = json.loads(response.text)
        self.assertTrue(result[0]["id"])
        self.assertEqual(json.loads((await self.get_projects_handler(request)).text), result)
        self.assertEqual([{k:v for k,v in p.items() if k != "id"} for p in result],
                         [{k:v for k,v in p.items() if k != "id"} for p in projects_data])

        # Verify file is saved in the user's projects path
        projects_file = os.path.join(self.temp_dir, "projects", "projects.json")
        self.assertTrue(os.path.exists(projects_file))
        with open(projects_file, encoding="utf-8") as f:
            saved_data = json.load(f)
        self.assertEqual(saved_data, projects_data)

        # Verify project folders were automatically created
        self.assertTrue(os.path.exists(os.path.join(self.temp_dir, "projects", "tic-tac-toe")))
        self.assertTrue(os.path.exists(os.path.join(self.temp_dir, "projects", "workspace-root")))

    async def test_set_active_project(self):
        # Setup existing projects
        projects_dir = os.path.join(self.temp_dir, "projects")
        os.makedirs(projects_dir, exist_ok=True)
        projects_file = os.path.join(projects_dir, "projects.json")

        projects_data = [{"name": "My Project", "folder": "my-project"}]
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

        expected_project_path = os.path.join(self.temp_dir, "projects", "my-project")

        # Verify user preference set
        self.assertEqual(self.mock_ctx.get_user_pref("project", user="testuser"), "My Project")
        # Verify allowed directories were set to the project folder
        self.assertEqual(self.allowed_directories.get("testuser"), [expected_project_path])

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
        self.allowed_directories["testuser"] = [os.path.join(self.temp_dir, "projects", "old-project")]

        # Save a list that DOES NOT include "Old Project" (it was deleted)
        projects_data = [{"name": "New Project", "folder": "new-project"}]
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

    async def test_save_project_new_auto_populates_folder(self):
        project_data = {"name": "New Project"}
        request = MagicMock()
        request.match_info = {"name": "New Project"}

        async def mock_json():
            return project_data
        request.json = mock_json

        response = await self.save_project_handler(request)
        self.assertEqual(response.status, 200)

        expected_data = [{"name": "New Project", "folder": "new-project"}]
        result = json.loads(response.text)
        self.assertTrue(result[0]["id"])
        self.assertEqual([{k:v for k,v in p.items() if k != "id"} for p in result], expected_data)

        # Verify saved file contents
        projects_file = os.path.join(self.temp_dir, "projects", "projects.json")
        with open(projects_file, encoding="utf-8") as f:
            saved_data = json.load(f)
        self.assertEqual(saved_data, result)

        # Verify folder created
        self.assertTrue(os.path.exists(os.path.join(self.temp_dir, "projects", "new-project")))

    async def test_save_project_update(self):
        # Setup existing projects
        projects_dir = os.path.join(self.temp_dir, "projects")
        os.makedirs(projects_dir, exist_ok=True)
        projects_file = os.path.join(projects_dir, "projects.json")

        initial_data = [
            {"name": "Project One", "folder": "project-one"},
            {"name": "Project Two", "folder": "project-two"},
        ]
        with open(projects_file, "w", encoding="utf-8") as f:
            f.write(json.dumps(initial_data))

        updated_project = {"name": "Project One", "folder": "project-one", "publish": "dist"}
        request = MagicMock()
        request.match_info = {"name": "Project One"}

        async def mock_json():
            return updated_project
        request.json = mock_json

        response = await self.save_project_handler(request)
        self.assertEqual(response.status, 200)
        result = json.loads(response.text)
        self.assertEqual(result[0], updated_project)
        self.assertTrue(result[1]["id"])
        self.assertEqual(result[1]["name"], "Project Two")

        # Verify merged file contents
        with open(projects_file, encoding="utf-8") as f:
            saved_data = json.load(f)
        self.assertEqual(saved_data, result)

    async def test_save_project_rename_active(self):
        # Setup existing projects & active project preference
        projects_dir = os.path.join(self.temp_dir, "projects")
        os.makedirs(projects_dir, exist_ok=True)
        projects_file = os.path.join(projects_dir, "projects.json")

        initial_data = [{"name": "Old Project Name", "folder": "old-project-name"}]
        with open(projects_file, "w", encoding="utf-8") as f:
            f.write(json.dumps(initial_data))

        self.mock_ctx.set_user_pref("project", "Old Project Name", user="testuser")
        self.allowed_directories["testuser"] = [os.path.join(self.temp_dir, "projects", "old-project-name")]

        # Rename project
        updated_project = {"name": "New Project Name", "folder": "new-project-name"}
        request = MagicMock()
        request.match_info = {"name": "Old Project Name"}

        async def mock_json():
            return updated_project
        request.json = mock_json

        response = await self.save_project_handler(request)
        self.assertEqual(response.status, 200)

        expected_new_path = os.path.join(self.temp_dir, "projects", "new-project-name")

        # Verify preference and allowed directories are updated
        self.assertEqual(self.mock_ctx.get_user_pref("project", user="testuser"), "New Project Name")
        self.assertEqual(self.allowed_directories.get("testuser"), [expected_new_path])

    async def test_sidebar_visibility_is_persistent_and_legacy_saves_preserve_it(self):
        request = MagicMock()
        request.match_info = {"name": "Visible"}
        async def create():
            return {"name": "Visible", "folder": "visible"}
        request.json = create
        project = json.loads((await self.save_project_handler(request)).text)[0]

        request.match_info = {"id": project["id"]}
        async def hide():
            return {"showInSidebar": False}
        request.json = hide
        response = await self.set_sidebar_visibility_handler(request)
        self.assertFalse(json.loads(response.text)[0]["showInSidebar"])

        request.match_info = {"name": "Visible"}
        request.json = create
        response = await self.save_project_handler(request)
        self.assertFalse(json.loads(response.text)[0]["showInSidebar"])

        request.match_info = {"id": project["id"]}
        async def show():
            return {"showInSidebar": True}
        request.json = show
        response = await self.set_sidebar_visibility_handler(request)
        self.assertTrue(json.loads(response.text)[0]["showInSidebar"])
        self.assertTrue(json.loads((await self.get_projects_handler(request)).text)[0]["showInSidebar"])

    def test_sanitize_publish_path_absolute(self):
        from llms.extensions.projects import sanitize_publish_path
        project_dir = "/home/user/projects/my-app"

        self.assertEqual(sanitize_publish_path("/home/user/projects/my-app", project_dir), "")
        self.assertEqual(sanitize_publish_path("/home/user/projects/my-app/dist", project_dir), "dist")
        self.assertEqual(sanitize_publish_path("dist", project_dir), "dist")
        self.assertEqual(sanitize_publish_path("/dist", project_dir), "dist")
        self.assertEqual(sanitize_publish_path("../../../etc/passwd", project_dir), "etc/passwd")
