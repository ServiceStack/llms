#!/usr/bin/env python3
"""
Unit tests for GoogleProvider streaming functionality.
"""

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

# Ensure test setup
os.environ["MOCK"] = "0"
os.environ["GEMINI_API_KEY"] = "test-api-key"

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


class TestGoogleStreaming(unittest.IsolatedAsyncioTestCase):
    """Test GoogleProvider streaming and live thread updates."""

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

        cli(f"ls google --config {self.temp_config_path} --providers {self.temp_providers_path}")
        self.app = get_app()
        self.provider = self.app.get_providers().get("google") or g_handlers.get("google")

    def tearDown(self):
        self.temp_dir.cleanup()

    async def test_google_streaming_thread_updates(self):
        """Test streaming mode parses Google Gemini SSE events, updates thread, and returns response."""
        sse_lines = [
            'data: {"candidates":[{"content":{"parts":[{"text":"Hello"}],"role":"model"},"index":0}],"modelVersion":"gemini-2.0-flash"}\n',
            'data: {"candidates":[{"content":{"parts":[{"text":" world!"}],"role":"model"},"finishReason":"STOP","index":0}],"usageMetadata":{"promptTokenCount":10,"candidatesTokenCount":5,"totalTokenCount":15},"modelVersion":"gemini-2.0-flash"}\n',
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
            "model": "gemini-2.0-flash",
            "messages": [{"role": "user", "content": "Hi"}],
            "stream": True,
        }
        context = {"threadId": "thread_google_123", "user": "test_user"}

        from llms.extensions.providers import google
        module_ctx = getattr(google, "ctx", None)

        with patch("aiohttp.ClientSession", return_value=mock_session):
            if module_ctx:
                with patch.object(module_ctx, "threads", mock_threads, create=True):
                    res = await self.provider.chat(chat_data, context=context)
            else:
                res = await self.provider.chat(chat_data, context=context)

        # Verify streamGenerateContent URL was called
        mock_session.post.assert_called_once()
        posted_url = mock_session.post.call_args[0][0]
        self.assertIn(":streamGenerateContent", posted_url)
        self.assertIn("alt=sse", posted_url)

        # Verify final response structure
        self.assertEqual(res["model"], "gemini-2.0-flash")
        self.assertEqual(res["choices"][0]["message"]["content"], "Hello world!")
        self.assertEqual(res["choices"][0]["finish_reason"], "STOP")
        self.assertEqual(res["usage"]["prompt_tokens"], 10)
        self.assertEqual(res["usage"]["completion_tokens"], 5)
        self.assertEqual(res["usage"]["total_tokens"], 15)

        # Verify update_thread_async was called
        if module_ctx and hasattr(module_ctx, "threads"):
            self.assertTrue(mock_update_thread_async.called)
            last_call_args = mock_update_thread_async.call_args_list[-1]
            self.assertEqual(last_call_args[0][0], "thread_google_123")
            updated_messages = last_call_args[0][1]["messages"]
            self.assertEqual(len(updated_messages), 2)
            self.assertEqual(updated_messages[1]["role"], "assistant")
            self.assertEqual(updated_messages[1]["content"], "Hello world!")

    async def test_google_default_streaming(self):
        """Test streaming mode is used by default when 'stream' is not explicitly specified in chat."""
        sse_lines = [
            'data: {"candidates":[{"content":{"parts":[{"text":"Default stream"}],"role":"model"},"finishReason":"STOP","index":0}]}\n',
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
            "model": "gemini-2.0-flash",
            "messages": [{"role": "user", "content": "Hi"}],
            # Note: "stream" key is intentionally omitted
        }

        with patch("aiohttp.ClientSession", return_value=mock_session):
            res = await self.provider.chat(chat_data)

        posted_url = mock_session.post.call_args[0][0]
        self.assertIn(":streamGenerateContent", posted_url)
        self.assertEqual(res["choices"][0]["message"]["content"], "Default stream")

    async def test_google_streaming_with_reasoning(self):
        """Test streaming mode parses Gemini reasoning/thinking parts."""
        sse_lines = [
            'data: {"candidates":[{"content":{"parts":[{"text":"Thinking about this...","thought":true}],"role":"model"},"index":0}],"modelVersion":"gemini-2.0-flash-thinking-exp"}\n',
            'data: {"candidates":[{"content":{"parts":[{"text":"Here is the answer."}],"role":"model"},"finishReason":"STOP","index":0}],"usageMetadata":{"promptTokenCount":8,"candidatesTokenCount":12,"totalTokenCount":20},"modelVersion":"gemini-2.0-flash-thinking-exp"}\n'
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
            "model": "gemini-2.0-flash-thinking-exp",
            "messages": [{"role": "user", "content": "Solve math problem"}],
            "stream": True,
        }

        with patch("aiohttp.ClientSession", return_value=mock_session):
            res = await self.provider.chat(chat_data)

        self.assertEqual(res["choices"][0]["message"]["content"], "Here is the answer.")
        self.assertEqual(res["choices"][0]["message"]["reasoning"], "Thinking about this...")

    async def test_google_non_streaming(self):
        """Test non-streaming fallback when stream=False."""
        chat_data = {
            "model": "gemini-2.0-flash",
            "messages": [{"role": "user", "content": "Hi"}],
            "stream": False,
        }
        response_json_payload = {
            "candidates": [
                {
                    "content": {"parts": [{"text": "Non-stream google response"}], "role": "model"},
                    "finishReason": "STOP",
                }
            ],
            "usageMetadata": {"promptTokenCount": 6, "candidatesTokenCount": 4, "totalTokenCount": 10},
            "modelVersion": "gemini-2.0-flash",
        }

        mock_response = AsyncMock()
        mock_response.status = 200

        mock_session_post = MagicMock()
        mock_session_post.__aenter__.return_value = mock_response
        mock_session_post.__aexit__.return_value = None

        mock_session = MagicMock()
        mock_session.post.return_value = mock_session_post
        mock_session.__aenter__.return_value = mock_session
        mock_session.__aexit__.return_value = None

        from llms.extensions.providers import google
        with patch("aiohttp.ClientSession", return_value=mock_session):
            with patch.object(self.provider, "response_json", AsyncMock(return_value=response_json_payload)):
                res = await self.provider.chat(chat_data)

        posted_url = mock_session.post.call_args[0][0]
        self.assertIn(":generateContent", posted_url)
        self.assertNotIn(":streamGenerateContent", posted_url)
        self.assertEqual(res["choices"][0]["message"]["content"], "Non-stream google response")

    async def test_google_streaming_cancellation(self):
        """Test that streaming returns None immediately when cancelled."""
        sse_lines = [
            'data: {"candidates":[{"content":{"parts":[{"text":"Hello"}],"role":"model"},"index":0}]}\n',
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
            "model": "gemini-2.0-flash",
            "messages": [{"role": "user", "content": "Hi"}],
            "stream": True,
        }
        context = {"threadId": "thread_google_cancel", "user": "test_user", "cancelled": True}

        with patch("aiohttp.ClientSession", return_value=mock_session):
            res = await self.provider.chat(chat_data, context=context)

        self.assertIsNone(res)

    async def test_google_thinking_budget_error_fallback(self):
        """Test retrying without thinkingConfig when Google API returns thinking budget error."""
        chat_data = {
            "model": "gemini-1.5-flash",
            "messages": [{"role": "user", "content": "Hi"}],
            "thinkingConfig": {"thinkingBudget": 1024},
            "stream": False,
        }
        error_response_payload = {
            "error": {
                "code": 400,
                "message": "Thinking budget is not supported for this model.",
                "status": "INVALID_ARGUMENT",
            }
        }
        success_response_payload = {
            "candidates": [
                {
                    "content": {"parts": [{"text": "Recovered response"}], "role": "model"},
                    "finishReason": "STOP",
                }
            ],
            "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 3, "totalTokenCount": 8},
            "modelVersion": "gemini-1.5-flash",
        }

        mock_response = AsyncMock()
        mock_response.status = 200

        mock_session_post = MagicMock()
        mock_session_post.__aenter__.return_value = mock_response
        mock_session_post.__aexit__.return_value = None

        mock_session = MagicMock()
        mock_session.post.return_value = mock_session_post
        mock_session.__aenter__.return_value = mock_session
        mock_session.__aexit__.return_value = None

        # Set provider thinking_config explicitly to simulate global provider configuration
        self.provider.thinking_config = {"thinkingBudget": 1024}

        with patch("aiohttp.ClientSession", return_value=mock_session):
            with patch.object(
                self.provider,
                "response_json",
                AsyncMock(side_effect=[error_response_payload, success_response_payload]),
            ):
                res = await self.provider.chat(chat_data)

        self.assertEqual(res["choices"][0]["message"]["content"], "Recovered response")


if __name__ == "__main__":
    unittest.main()
