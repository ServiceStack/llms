import json
import unittest
from unittest.mock import AsyncMock, MagicMock

from aiohttp import web

from llms.extensions.viewer import install


class TestViewerExtension(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.mock_ctx = MagicMock()
        self.mock_ctx.app.on_request = AsyncMock()
        self.mock_ctx.app.import_maps = {"vue": "https://unpkg.com/vue@3"}
        self.mock_ctx.app.index_headers = []
        self.mock_ctx.app.index_footers = []
        self.mock_ctx.app.server_add_get = []

        # Install the viewer extension
        install(self.mock_ctx)

        # Retrieve the registered handler
        self.view_thread_handler = None
        for path, handler, _ in self.mock_ctx.app.server_add_get:
            if path == "/t/{id}":
                self.view_thread_handler = handler

    async def test_view_thread_success(self):
        # Prepare a mock thread
        expected_thread = {
            "id": "test-thread-123",
            "title": "My Awesome Conversation",
            "messages": [{"role": "user", "content": "Hello!"}],
            "publishedAt": "2026-07-03 11:00:00"
        }

        self.mock_ctx.get_username.return_value = "john_doe"
        self.mock_ctx.threads.get_thread.return_value = expected_thread

        # Setup mock request
        request = MagicMock()
        request.match_info = {"id": "test-thread-123"}

        # Call the handler
        response = await self.view_thread_handler(request)

        self.assertIsInstance(response, web.Response)
        self.assertEqual(response.status, 200)
        self.assertEqual(response.content_type, "text/html")

        # Verify Vue import map was injected
        self.assertIn("https://unpkg.com/vue@3", response.text)

        # Verify the thread was serialized and assigned to window.ARGS
        serialized_thread = json.dumps(expected_thread, indent=2)
        self.assertIn(f"window.ARGS.currentThread = {serialized_thread}", response.text)

    async def test_view_thread_error(self):
        # Setup get_thread to raise an exception
        self.mock_ctx.get_username.return_value = "john_doe"
        self.mock_ctx.threads.get_thread.side_effect = Exception("Database is down")
        self.mock_ctx.error_message.side_effect = lambda e: str(e)

        # Setup mock request
        request = MagicMock()
        request.match_info = {"id": "test-thread-123"}

        # Call the handler
        response = await self.view_thread_handler(request)

        self.assertIsInstance(response, web.Response)
        self.assertEqual(response.status, 200)
        self.assertEqual(response.content_type, "text/html")

        # Verify the error was serialized and assigned to window.ARGS
        self.assertIn("window.ARGS.error = {", response.text)
        self.assertIn('"message": "Database is down"', response.text)
