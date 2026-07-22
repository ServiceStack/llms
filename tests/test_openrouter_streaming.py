#!/usr/bin/env python3
"""
Unit tests for OpenRouterProvider streaming functionality.
"""

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

# Ensure test setup
os.environ["MOCK"] = "0"
os.environ["OPENROUTER_API_KEY"] = "test-api-key"

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from llms.main import cli, g_handlers, get_app


class DummyStreamReader:
    def __init__(self, lines):
        self.lines = [line.encode("utf-8") if isinstance(line, str) else line for line in lines]
        self.idx = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.idx < len(self.lines):
            line = self.lines[self.idx]
            self.idx += 1
            return line
        raise StopAsyncIteration


class TestOpenRouterStreaming(unittest.IsolatedAsyncioTestCase):
    """Test OpenRouterProvider streaming and live thread updates."""

    def setUp(self):
        config_path = os.path.join(os.path.dirname(__file__), "..", "llms", "llms.json")
        providers_path = os.path.join(os.path.dirname(__file__), "..", "llms", "providers.json")

        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_config_path = os.path.join(self.temp_dir.name, "llms.json")
        self.temp_providers_path = os.path.join(self.temp_dir.name, "providers.json")

        with open(config_path) as f:
            config = json.load(f)
        with open(providers_path) as f:
            providers = json.load(f)

        extra_path = os.path.join(os.path.dirname(__file__), "..", "llms", "providers-extra.json")
        if os.path.exists(extra_path):
            with open(extra_path) as f:
                extra = json.load(f)
                for k, v in extra.items():
                    if k in providers:
                        if "models" not in providers[k]:
                            providers[k]["models"] = {}
                        providers[k]["models"].update(v.get("models", {}))
                    else:
                        providers[k] = v

        with open(self.temp_config_path, "w") as f:
            json.dump(config, f)
        with open(self.temp_providers_path, "w") as f:
            json.dump(providers, f)

        cli(f"ls openrouter --config {self.temp_config_path} --providers {self.temp_providers_path}")
        self.app = get_app()
        self.provider = self.app.get_providers().get("openrouter") or g_handlers.get("openrouter")

    def tearDown(self):
        self.temp_dir.cleanup()

    async def test_openrouter_streaming_thread_updates(self):
        """Test streaming mode calls update_thread_async and returns completed response."""
        sse_lines = [
            'data: {"id":"gen-1","model":"openai/gpt-4o","choices":[{"index":0,"delta":{"role":"assistant","content":"Hello"}}]}\n',
            'data: {"id":"gen-1","model":"openai/gpt-4o","choices":[{"index":0,"delta":{"content":" world!"}}]}\n',
            'data: {"id":"gen-1","model":"openai/gpt-4o","choices":[{"index":0,"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":10,"completion_tokens":5,"total_tokens":15}}\n',
            'data: [DONE]\n'
        ]

        mock_stream = DummyStreamReader(sse_lines)

        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.content = mock_stream

        mock_session_post = MagicMock()
        mock_session_post.__aenter__.return_value = mock_response
        mock_session_post.__aexit__.return_value = None

        mock_session = MagicMock()
        mock_session.post.return_value = mock_session_post
        mock_session.__aenter__.return_value = mock_session
        mock_session.__aexit__.return_value = None

        mock_update_thread_async = AsyncMock()
        mock_threads = MagicMock()
        mock_threads.update_thread_async = mock_update_thread_async

        chat_data = {
            "model": "openai/gpt-4o",
            "messages": [{"role": "user", "content": "Hi"}],
            "stream": True,
        }
        context = {"threadId": "thread_123", "user": "test_user"}

        from llms.extensions.providers import openrouter
        # Get module level ctx
        module_ctx = getattr(openrouter, "ctx", None)

        with patch("aiohttp.ClientSession", return_value=mock_session):
            if module_ctx:
                with patch.object(module_ctx, "threads", mock_threads, create=True):
                    res = await self.provider.chat(chat_data, context=context)
            else:
                res = await self.provider.chat(chat_data, context=context)

        # Verify final response structure
        self.assertEqual(res["id"], "gen-1")
        self.assertEqual(res["choices"][0]["message"]["content"], "Hello world!")
        self.assertEqual(res["choices"][0]["finish_reason"], "stop")
        self.assertEqual(res["usage"]["prompt_tokens"], 10)
        self.assertEqual(res["usage"]["completion_tokens"], 5)

        # Verify update_thread_async was called with accumulated message
        if module_ctx and hasattr(module_ctx, "threads"):
            self.assertTrue(mock_update_thread_async.called)
            last_call_args = mock_update_thread_async.call_args_list[-1]
            self.assertEqual(last_call_args[0][0], "thread_123")
            updated_messages = last_call_args[0][1]["messages"]
            self.assertEqual(len(updated_messages), 2)
            self.assertEqual(updated_messages[1]["role"], "assistant")
            self.assertEqual(updated_messages[1]["content"], "Hello world!")

    async def test_openrouter_non_streaming(self):
        """Test non-streaming fallback when stream=False."""
        chat_data = {
            "model": "openai/gpt-4o",
            "messages": [{"role": "user", "content": "Hi"}],
            "stream": False,
        }
        response_json_payload = {
            "id": "gen-2",
            "object": "chat.completion",
            "created": 10000,
            "model": "openai/gpt-4o",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "Non-stream response"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
        }

        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.text = AsyncMock(return_value=json.dumps(response_json_payload))

        mock_session_post = MagicMock()
        mock_session_post.__aenter__.return_value = mock_response
        mock_session_post.__aexit__.return_value = None

        mock_session = MagicMock()
        mock_session.post.return_value = mock_session_post
        mock_session.__aenter__.return_value = mock_session
        mock_session.__aexit__.return_value = None

        with patch("aiohttp.ClientSession", return_value=mock_session):
            res = await self.provider.chat(chat_data)

        self.assertEqual(res["id"], "gen-2")
        self.assertEqual(res["choices"][0]["message"]["content"], "Non-stream response")

    async def test_openrouter_streaming_cancellation(self):
        """Test that streaming returns None immediately when cancelled."""
        sse_lines = [
            'data: {"id":"gen-1","model":"openai/gpt-4o","choices":[{"index":0,"delta":{"role":"assistant","content":"Hello"}}]}\n',
            'data: {"id":"gen-1","model":"openai/gpt-4o","choices":[{"index":0,"delta":{"content":" world!"}}]}\n',
        ]

        mock_stream = DummyStreamReader(sse_lines)

        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.content = mock_stream

        mock_session_post = MagicMock()
        mock_session_post.__aenter__.return_value = mock_response
        mock_session_post.__aexit__.return_value = None

        mock_session = MagicMock()
        mock_session.post.return_value = mock_session_post
        mock_session.__aenter__.return_value = mock_session
        mock_session.__aexit__.return_value = None

        chat_data = {
            "model": "openai/gpt-4o",
            "messages": [{"role": "user", "content": "Hi"}],
            "stream": True,
        }
        context = {"threadId": "thread_cancelled", "user": "test_user", "cancelled": True}

        with patch("aiohttp.ClientSession", return_value=mock_session):
            res = await self.provider.chat(chat_data, context=context)

        self.assertIsNone(res)


if __name__ == "__main__":
    unittest.main()
