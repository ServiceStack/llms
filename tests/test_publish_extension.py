import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock

from llms.extensions.publish import install


class TestPublishExtension(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.initial_cwd = os.getcwd()
        os.chdir(self.temp_dir)

        # Mock ExtensionContext
        self.mock_ctx = MagicMock()
        self.mock_ctx.threads.db.update_thread_async = AsyncMock()
        self.mock_ctx.get_user_avatar_path.return_value = None
        self.mock_ctx.get_profile_avatar_path.return_value = None
        self.mock_ctx.path = os.path.join(self.temp_dir, "extensions", "publish")
        os.makedirs(self.mock_ctx.path, exist_ok=True)

        def get_user_path(user=None):
            if not user:
                return os.path.join(self.temp_dir, "user", "default")
            return os.path.join(self.temp_dir, "user", user)

        self.mock_ctx.get_user_path = get_user_path

        # Install the publish extension
        install(self.mock_ctx)

        # Retrieve registered handlers
        self.get_handler = None
        for args in self.mock_ctx.add_get.call_args_list:
            path, handler = args[0][0], args[0][1]
            if path == "config.json":
                self.get_handler = handler

        self.post_handler = None
        for args in self.mock_ctx.add_post.call_args_list:
            path, handler = args[0][0], args[0][1]
            if path == "config.json":
                self.post_handler = handler

    def tearDown(self):
        os.chdir(self.initial_cwd)
        shutil.rmtree(self.temp_dir)

    async def test_get_publish_config_default_none_exists(self):
        self.mock_ctx.get_username.return_value = None
        request = MagicMock()
        response = await self.get_handler(request)
        self.assertEqual(response.status, 200)
        data = json.loads(response.text)
        self.assertIsNone(data["apiKey"])
        self.assertIsNone(data["userName"])
        self.assertIsNone(data["userId"])
        self.assertIn("registerUrl", data)

    async def test_save_publish_config_creates_directories_and_saves(self):
        # We test both default (no user) and specific user.
        # This checks that os.makedirs(..., exist_ok=True) creates non-existent parent directories.
        self.mock_ctx.get_username.return_value = "admin"

        request = MagicMock()
        request.json = AsyncMock(return_value={
            "apiKey": "1234567890abcdef",
            "userName": "admin",
            "userId": "usr_123"
        })

        response = await self.post_handler(request)
        self.assertEqual(response.status, 200)

        # Verify it was saved correctly in the mock user path
        config_file_path = os.path.join(self.mock_ctx.get_user_path("admin"), "publish", "config.json")
        self.assertTrue(os.path.exists(config_file_path))

        with open(config_file_path, encoding="utf-8") as f:
            saved_data = json.load(f)
            self.assertEqual(saved_data["apiKey"], "1234567890abcdef")
            self.assertEqual(saved_data["userName"], "admin")
            self.assertEqual(saved_data["userId"], "usr_123")

        # The returned response text should obscure the api key
        response_data = json.loads(response.text)
        self.assertEqual(response_data["apiKey"], "123******cdef")
        self.assertEqual(response_data["userName"], "admin")
        self.assertEqual(response_data["userId"], "usr_123")

    @unittest.mock.patch("llms.extensions.publish.aiohttp.ClientSession")
    async def test_publish_thread(self, mock_client_session):
        # 1. Retrieve the publish_thread handler
        publish_thread_handler = None
        for args in self.mock_ctx.add_post.call_args_list:
            path, handler = args[0][0], args[0][1]
            if path == "thread/{id}":
                publish_thread_handler = handler
        self.assertIsNotNone(publish_thread_handler)

        # 2. Setup mock_ctx dependencies for publish_thread
        self.mock_ctx.get_username.return_value = "admin"
        thread_data = {"id": "t1", "messages": []}
        self.mock_ctx.threads.get_thread.return_value = thread_data

        # 3. Create config.json file in the admin user path so API key is found
        config_dir = os.path.join(self.mock_ctx.get_user_path("admin"), "publish")
        os.makedirs(config_dir, exist_ok=True)
        with open(os.path.join(config_dir, "config.json"), "w", encoding="utf-8") as f:
            json.dump({"apiKey": "test-key", "baseUrl": "https://test.llmspy.org"}, f)

        # 4. Mock the response from session.post
        mock_resp = AsyncMock()
        mock_resp.text = AsyncMock(return_value=json.dumps({"publishedUrl": "published_successfully_response"}))
        mock_resp.status = 200
        mock_resp.content_type = "application/json"

        mock_session_instance = MagicMock()
        mock_session_instance.post = MagicMock(return_value=mock_resp)
        # Handle async context manager __aenter__ / __aexit__
        mock_session_instance.__aenter__ = AsyncMock(return_value=mock_session_instance)
        mock_session_instance.__aexit__ = AsyncMock()

        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock()

        mock_client_session.return_value = mock_session_instance

        # 5. Build mock request
        request = MagicMock()
        request.match_info = {"id": "t1"}

        # 6. Execute handler
        response = await publish_thread_handler(request)
        self.assertEqual(response.status, 200)
        resp_data = json.loads(response.text)
        self.assertEqual(resp_data.get("publishedUrl"), "published_successfully_response")
        self.assertIn("publishedAt", resp_data)

        # 7. Assert session.post was called with the correct args
        mock_session_instance.post.assert_called_once_with(
            "https://test.llmspy.org/publish/thread",
            headers={"Authorization": "Bearer test-key", "Content-Type": "application/json"},
            json=thread_data,
            ssl=None
        )

    @unittest.mock.patch("llms.extensions.publish.aiohttp.ClientSession")
    async def test_publish_thread_localhost(self, mock_client_session):
        # 1. Retrieve the publish_thread handler
        publish_thread_handler = None
        for args in self.mock_ctx.add_post.call_args_list:
            path, handler = args[0][0], args[0][1]
            if path == "thread/{id}":
                publish_thread_handler = handler
        self.assertIsNotNone(publish_thread_handler)

        # 2. Setup mock_ctx dependencies for publish_thread
        self.mock_ctx.get_username.return_value = "admin"
        thread_data = {"id": "t1", "messages": []}
        self.mock_ctx.threads.get_thread.return_value = thread_data

        # 3. Create config.json file with localhost baseUrl
        config_dir = os.path.join(self.mock_ctx.get_user_path("admin"), "publish")
        os.makedirs(config_dir, exist_ok=True)
        with open(os.path.join(config_dir, "config.json"), "w", encoding="utf-8") as f:
            json.dump({"apiKey": "test-key", "baseUrl": "https://localhost:5001"}, f)

        # 4. Mock the response from session.post
        mock_resp = AsyncMock()
        mock_resp.text = AsyncMock(return_value=json.dumps({"publishedUrl": "published_successfully_response"}))
        mock_resp.status = 200
        mock_resp.content_type = "application/json"

        mock_session_instance = MagicMock()
        mock_session_instance.post = MagicMock(return_value=mock_resp)
        # Handle async context manager __aenter__ / __aexit__
        mock_session_instance.__aenter__ = AsyncMock(return_value=mock_session_instance)
        mock_session_instance.__aexit__ = AsyncMock()

        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock()

        mock_client_session.return_value = mock_session_instance

        # 5. Build mock request
        request = MagicMock()
        request.match_info = {"id": "t1"}

        # 6. Execute handler
        response = await publish_thread_handler(request)
        self.assertEqual(response.status, 200)

        # 7. Assert session.post was called with ssl=False
        mock_session_instance.post.assert_called_once_with(
            "https://localhost:5001/publish/thread",
            headers={"Authorization": "Bearer test-key", "Content-Type": "application/json"},
            json=thread_data,
            ssl=False
        )

    @unittest.mock.patch("llms.extensions.publish.aiohttp.ClientSession")
    async def test_publish_thread_error_forwarding(self, mock_client_session):
        # 1. Retrieve the publish_thread handler
        publish_thread_handler = None
        for args in self.mock_ctx.add_post.call_args_list:
            path, handler = args[0][0], args[0][1]
            if path == "thread/{id}":
                publish_thread_handler = handler
        self.assertIsNotNone(publish_thread_handler)

        # 2. Setup mock_ctx dependencies for publish_thread
        self.mock_ctx.get_username.return_value = "admin"
        thread_data = {"id": "t1", "messages": []}
        self.mock_ctx.threads.get_thread.return_value = thread_data

        # 3. Create config.json file
        config_dir = os.path.join(self.mock_ctx.get_user_path("admin"), "publish")
        os.makedirs(config_dir, exist_ok=True)
        with open(os.path.join(config_dir, "config.json"), "w", encoding="utf-8") as f:
            json.dump({"apiKey": "test-key", "baseUrl": "https://test.llmspy.org"}, f)

        # 4. Mock the response from session.post with a 401 error
        mock_resp = AsyncMock()
        mock_resp.text = AsyncMock(return_value=json.dumps({"message": "Unauthorized"}))
        mock_resp.status = 401
        mock_resp.content_type = "application/json"

        mock_session_instance = MagicMock()
        mock_session_instance.post = MagicMock(return_value=mock_resp)
        mock_session_instance.__aenter__ = AsyncMock(return_value=mock_session_instance)
        mock_session_instance.__aexit__ = AsyncMock()

        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock()

        mock_client_session.return_value = mock_session_instance

        # 5. Build mock request
        request = MagicMock()
        request.match_info = {"id": "t1"}

        # 6. Execute handler
        response = await publish_thread_handler(request)
        self.assertEqual(response.status, 401)
        resp_data = json.loads(response.text)
        self.assertEqual(resp_data.get("message"), "Unauthorized")
        self.assertIn("publishedAt", resp_data)

    @unittest.mock.patch("llms.extensions.publish.aiohttp.ClientSession")
    async def test_publish_thread_with_cache_files(self, mock_client_session):
        # 1. Retrieve the publish_thread handler
        publish_thread_handler = None
        for args in self.mock_ctx.add_post.call_args_list:
            path, handler = args[0][0], args[0][1]
            if path == "thread/{id}":
                publish_thread_handler = handler
        self.assertIsNotNone(publish_thread_handler)

        # 2. Setup mock_ctx dependencies for publish_thread
        self.mock_ctx.get_username.return_value = "admin"
        thread_data = {
            "id": "t1",
            "messages": [
                {"role": "user", "content": "Here is an image: /~cache/ab/abcdef123.png"}
            ]
        }
        self.mock_ctx.threads.get_thread.return_value = thread_data

        # Mock get_cache_path to return a path in our temp directory
        cache_dir = os.path.join(self.temp_dir, "cache")
        os.makedirs(cache_dir, exist_ok=True)

        def get_cache_path(path=""):
            return os.path.join(cache_dir, path)

        self.mock_ctx.get_cache_path = get_cache_path

        # Create dummy cache file and its .info sidecar
        os.makedirs(os.path.join(cache_dir, "ab"), exist_ok=True)
        file_path = os.path.join(cache_dir, "ab", "abcdef123.png")
        sidecar_path = file_path + ".info"

        with open(file_path, "wb") as f:
            f.write(b"dummy image bytes")
        with open(sidecar_path, "w", encoding="utf-8") as f:
            f.write('{"title": "dummy metadata"}')

        # 3. Create config.json file
        config_dir = os.path.join(self.mock_ctx.get_user_path("admin"), "publish")
        os.makedirs(config_dir, exist_ok=True)
        with open(os.path.join(config_dir, "config.json"), "w", encoding="utf-8") as f:
            json.dump({"apiKey": "test-key", "baseUrl": "https://test.llmspy.org"}, f)

        # 4. Mock the response from session.post calls
        mock_resp_cache = AsyncMock()
        mock_resp_cache.text = AsyncMock(return_value=json.dumps({"publishedUrl": "https://cdn.llmspy.org/cache/ab/abcdef123.png"}))
        mock_resp_cache.status = 200
        mock_resp_cache.content_type = "application/json"
        mock_resp_cache.__aenter__ = AsyncMock(return_value=mock_resp_cache)
        mock_resp_cache.__aexit__ = AsyncMock()

        mock_resp_thread = AsyncMock()
        mock_resp_thread.text = AsyncMock(return_value=json.dumps({"publishedUrl": "https://test.llmspy.org/t/t1"}))
        mock_resp_thread.status = 200
        mock_resp_thread.content_type = "application/json"
        mock_resp_thread.__aenter__ = AsyncMock(return_value=mock_resp_thread)
        mock_resp_thread.__aexit__ = AsyncMock()

        responses = {
            "https://test.llmspy.org/publish/cache": [mock_resp_cache],
            "https://test.llmspy.org/publish/thread": [mock_resp_thread]
        }

        def post_side_effect(url, **kwargs):
            return responses[url].pop(0)

        mock_session_instance = MagicMock()
        mock_session_instance.post = MagicMock(side_effect=post_side_effect)
        mock_session_instance.__aenter__ = AsyncMock(return_value=mock_session_instance)
        mock_session_instance.__aexit__ = AsyncMock()

        mock_client_session.return_value = mock_session_instance

        # 5. Build mock request
        request = MagicMock()
        request.match_info = {"id": "t1"}

        # 6. Execute handler
        response = await publish_thread_handler(request)
        self.assertEqual(response.status, 200)

        # 7. Assert session.post was called with the thread DTO containing the unmodified URL
        expected_thread_data = {
            "id": "t1",
            "messages": [
                {"role": "user", "content": "Here is an image: /~cache/ab/abcdef123.png"}
            ]
        }
        mock_session_instance.post.assert_any_call(
            "https://test.llmspy.org/publish/thread",
            headers={"Authorization": "Bearer test-key", "Content-Type": "application/json"},
            json=expected_thread_data,
            ssl=None
        )

    @unittest.mock.patch("llms.extensions.publish.aiohttp.ClientSession")
    async def test_publish_project_success(self, mock_client_session):
        import io
        import tarfile

        # 1. Retrieve the publish_project handler
        publish_project_handler = None
        for args in self.mock_ctx.add_post.call_args_list:
            path, handler = args[0][0], args[0][1]
            if path == "project/{name}":
                publish_project_handler = handler
        self.assertIsNotNone(publish_project_handler)

        self.mock_ctx.get_username.return_value = "admin"

        # Mock project data
        project_data = {
            "name": "ProjectA",
            "description": "My test project",
            "path": "ProjectA_path",
            "publish": "dist_dir"
        }
        self.mock_ctx.projects.get_user_projects.return_value = [project_data]

        # Setup dist directory and files
        dist_dir = os.path.join(self.temp_dir, "dist_dir")
        os.makedirs(dist_dir, exist_ok=True)

        # Create some files inside the dist directory
        with open(os.path.join(dist_dir, "index.html"), "w", encoding="utf-8") as f:
            f.write("<h1>Hello World</h1>")
        os.makedirs(os.path.join(dist_dir, "assets"), exist_ok=True)
        with open(os.path.join(dist_dir, "assets", "app.js"), "w", encoding="utf-8") as f:
            f.write("console.log('test')")

        self.mock_ctx.resolve_directory.return_value = dist_dir

        # Create config.json file so API key is found
        config_dir = os.path.join(self.mock_ctx.get_user_path("admin"), "publish")
        os.makedirs(config_dir, exist_ok=True)
        with open(os.path.join(config_dir, "config.json"), "w", encoding="utf-8") as f:
            json.dump({"apiKey": "test-key", "baseUrl": "https://test.llmspy.org"}, f)

        # Mock the response from session.post
        mock_resp = AsyncMock()
        mock_resp.text = AsyncMock(return_value=json.dumps({"publishedUrl": "https://test.llmspy.org/project/ProjectA"}))
        mock_resp.status = 200
        mock_resp.content_type = "application/json"

        mock_session_instance = MagicMock()
        mock_session_instance.post = MagicMock(return_value=mock_resp)
        mock_session_instance.__aenter__ = AsyncMock(return_value=mock_session_instance)
        mock_session_instance.__aexit__ = AsyncMock()

        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock()

        mock_client_session.return_value = mock_session_instance

        # Build mock request
        request = MagicMock()
        request.match_info = {"name": "ProjectA"}

        # Execute handler
        response = await publish_project_handler(request)
        self.assertEqual(response.status, 200)
        resp_data = json.loads(response.text)
        self.assertEqual(resp_data.get("publishedUrl"), "https://test.llmspy.org/project/ProjectA")

        # Verify projects.json is written with publishedUrl
        projects_file_path = os.path.join(self.mock_ctx.get_user_path("admin"), "projects", "projects.json")
        self.assertTrue(os.path.exists(projects_file_path))
        with open(projects_file_path, encoding="utf-8") as f:
            saved_projects = json.load(f)
            self.assertEqual(len(saved_projects), 1)
            self.assertEqual(saved_projects[0]["name"], "ProjectA")
            self.assertEqual(saved_projects[0]["publishedUrl"], "https://test.llmspy.org/project/ProjectA")

        # Verify post arguments
        mock_session_instance.post.assert_called_once()
        call_args = mock_session_instance.post.call_args
        url = call_args[0][0]
        self.assertEqual(url, "https://test.llmspy.org/publish/project/ProjectA")

        headers = call_args[1]["headers"]
        self.assertEqual(headers["Authorization"], "Bearer test-key")
        self.assertEqual(headers["Accept"], "application/json")

        data_form = call_args[1]["data"]
        # Ensure it contains the fields "info" and "file"
        self.assertEqual(len(data_form._fields), 2)
        info_field = next(f for f in data_form._fields if f[0]["name"] == "info")
        file_field = next(f for f in data_form._fields if f[0]["name"] == "file")

        self.assertEqual(info_field[0]["filename"], "info.json")
        self.assertEqual(file_field[0]["filename"], "ProjectA.tar.gz")

        # Verify tar file contents
        tar_bytes = file_field[2]
        tar_stream = io.BytesIO(tar_bytes)
        with tarfile.open(fileobj=tar_stream, mode="r:gz") as tar:
            members = tar.getnames()
            self.assertIn("index.html", members)
            self.assertIn("assets/app.js", members)

    async def test_publish_project_not_found(self):
        publish_project_handler = None
        for args in self.mock_ctx.add_post.call_args_list:
            path, handler = args[0][0], args[0][1]
            if path == "project/{name}":
                publish_project_handler = handler
        self.assertIsNotNone(publish_project_handler)

        self.mock_ctx.get_username.return_value = "admin"
        self.mock_ctx.projects.get_user_projects.return_value = []

        request = MagicMock()
        request.match_info = {"name": "ProjectA"}

        with self.assertRaises(Exception) as context:
            await publish_project_handler(request)
        self.assertEqual(str(context.exception), "Project not found")

    async def test_publish_project_no_publish_dir(self):
        publish_project_handler = None
        for args in self.mock_ctx.add_post.call_args_list:
            path, handler = args[0][0], args[0][1]
            if path == "project/{name}":
                publish_project_handler = handler
        self.assertIsNotNone(publish_project_handler)

        self.mock_ctx.get_username.return_value = "admin"

        # No 'publish' key in project_data
        project_data = {
            "name": "ProjectA",
            "description": "My test project",
            "path": "ProjectA_path"
        }
        self.mock_ctx.projects.get_user_projects.return_value = [project_data]

        # Create config.json file
        config_dir = os.path.join(self.mock_ctx.get_user_path("admin"), "publish")
        os.makedirs(config_dir, exist_ok=True)
        with open(os.path.join(config_dir, "config.json"), "w", encoding="utf-8") as f:
            json.dump({"apiKey": "test-key", "baseUrl": "https://test.llmspy.org"}, f)

        request = MagicMock()
        request.match_info = {"name": "ProjectA"}

        with self.assertRaises(Exception) as context:
            await publish_project_handler(request)
        self.assertEqual(str(context.exception), "No publish directory configured for the project")

    @unittest.mock.patch("llms.extensions.publish.aiohttp.ClientSession")
    async def test_publish_media_success(self, mock_client_session):
        # 1. Retrieve handler
        publish_media_handler = None
        for args in self.mock_ctx.add_post.call_args_list:
            path, handler = args[0][0], args[0][1]
            if path == "media/{id}":
                publish_media_handler = handler
        self.assertIsNotNone(publish_media_handler)

        self.mock_ctx.get_username.return_value = "admin"

        # 2. Mock media query
        media_data = {
            "id": "m1",
            "hash": "abcdef123",
            "url": "/~cache/ab/abcdef123.png"
        }
        self.mock_ctx.media.query_media.return_value = [media_data]
        self.mock_ctx.media.update_media_async = AsyncMock()

        # 3. Setup cache file
        cache_dir = os.path.join(self.temp_dir, "cache")
        os.makedirs(cache_dir, exist_ok=True)
        def get_cache_path(path=""):
            return os.path.join(cache_dir, path)
        self.mock_ctx.get_cache_path = get_cache_path

        os.makedirs(os.path.join(cache_dir, "ab"), exist_ok=True)
        file_path = os.path.join(cache_dir, "ab", "abcdef123.png")
        with open(file_path, "wb") as f:
            f.write(b"fake image bytes")

        # 4. Create config.json file
        config_dir = os.path.join(self.mock_ctx.get_user_path("admin"), "publish")
        os.makedirs(config_dir, exist_ok=True)
        with open(os.path.join(config_dir, "config.json"), "w", encoding="utf-8") as f:
            json.dump({"apiKey": "test-key", "baseUrl": "https://test.llmspy.org"}, f)

        # 5. Mock ClientSession response
        mock_resp = AsyncMock()
        mock_resp.text = AsyncMock(return_value=json.dumps({"publishedUrl": "https://test.llmspy.org/media/m1"}))
        mock_resp.status = 200
        mock_resp.content_type = "application/json"

        mock_session_instance = MagicMock()
        mock_session_instance.post = MagicMock(return_value=mock_resp)
        mock_session_instance.__aenter__ = AsyncMock(return_value=mock_session_instance)
        mock_session_instance.__aexit__ = AsyncMock()

        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock()

        mock_client_session.return_value = mock_session_instance

        # 6. Execute handler
        request = MagicMock()
        request.match_info = {"id": "m1"}
        response = await publish_media_handler(request)
        self.assertEqual(response.status, 200)
        resp_data = json.loads(response.text)
        self.assertEqual(resp_data.get("publishedUrl"), "https://test.llmspy.org/media/m1")

        # 7. Verify post details
        mock_session_instance.post.assert_called_once()
        call_args = mock_session_instance.post.call_args
        url = call_args[0][0]
        self.assertEqual(url, "https://test.llmspy.org/publish/media")

        headers = call_args[1]["headers"]
        self.assertEqual(headers["Authorization"], "Bearer test-key")
        self.assertEqual(headers["Accept"], "application/json")

        data_form = call_args[1]["data"]
        self.assertEqual(len(data_form._fields), 2)
        info_field = next(f for f in data_form._fields if f[0]["name"] == "info")
        file_field = next(f for f in data_form._fields if f[0]["name"] == "file")

        self.assertEqual(info_field[0]["filename"], "info.json")
        self.assertEqual(json.loads(info_field[2].decode("utf-8")), media_data)
        self.assertEqual(file_field[0]["filename"], "abcdef123.png")
        self.assertEqual(file_field[2], b"fake image bytes")

    async def test_publish_media_not_found(self):
        # 1. Retrieve handler
        publish_media_handler = None
        for args in self.mock_ctx.add_post.call_args_list:
            path, handler = args[0][0], args[0][1]
            if path == "media/{id}":
                publish_media_handler = handler
        self.assertIsNotNone(publish_media_handler)

        self.mock_ctx.get_username.return_value = "admin"
        self.mock_ctx.media.query_media.return_value = []

        request = MagicMock()
        request.match_info = {"id": "nonexistent"}
        response = await publish_media_handler(request)
        self.assertEqual(response.status, 404)
        resp_data = json.loads(response.text)
        self.assertEqual(resp_data.get("error"), "Media not found")

    async def test_publish_media_cache_file_not_found(self):
        # 1. Retrieve handler
        publish_media_handler = None
        for args in self.mock_ctx.add_post.call_args_list:
            path, handler = args[0][0], args[0][1]
            if path == "media/{id}":
                publish_media_handler = handler
        self.assertIsNotNone(publish_media_handler)

        self.mock_ctx.get_username.return_value = "admin"

        # 2. Mock media query with cache file path that doesn't exist
        media_data = {
            "id": "m1",
            "hash": "abcdef123",
            "url": "/~cache/ab/nonexistent.png"
        }
        self.mock_ctx.media.query_media.return_value = [media_data]

        # 3. Setup cache path resolver
        cache_dir = os.path.join(self.temp_dir, "cache")
        def get_cache_path(path=""):
            return os.path.join(cache_dir, path)
        self.mock_ctx.get_cache_path = get_cache_path

        # 4. Create config.json file
        config_dir = os.path.join(self.mock_ctx.get_user_path("admin"), "publish")
        os.makedirs(config_dir, exist_ok=True)
        with open(os.path.join(config_dir, "config.json"), "w", encoding="utf-8") as f:
            json.dump({"apiKey": "test-key", "baseUrl": "https://test.llmspy.org"}, f)

        request = MagicMock()
        request.match_info = {"id": "m1"}
        response = await publish_media_handler(request)
        self.assertEqual(response.status, 404)
        resp_data = json.loads(response.text)
        self.assertIn("Cached file not found", resp_data.get("error"))

