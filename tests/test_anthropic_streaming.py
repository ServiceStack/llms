#!/usr/bin/env python3
"""
Unit tests for AnthropicProvider streaming functionality.
"""

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

# Ensure test setup
os.environ["MOCK"] = "0"
os.environ["ANTHROPIC_API_KEY"] = "test-api-key"

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


class TestAnthropicStreaming(unittest.IsolatedAsyncioTestCase):
    """Test AnthropicProvider streaming and live thread updates."""

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

        cli(f"ls anthropic --config {self.temp_config_path} --providers {self.temp_providers_path}")
        self.app = get_app()
        self.provider = self.app.get_providers().get("anthropic") or g_handlers.get("anthropic")

    def tearDown(self):
        self.temp_dir.cleanup()

    async def test_anthropic_streaming_thread_updates(self):
        """Test streaming mode parses Anthropic SSE events, calls update_thread_async, and returns completed response."""
        sse_lines = [
            'event: message_start\n',
            'data: {"type":"message_start","message":{"id":"msg_100","type":"message","role":"assistant","content":[],"model":"claude-3-5-sonnet-20241022","stop_reason":null,"usage":{"input_tokens":12,"output_tokens":1}}}\n',
            'event: content_block_start\n',
            'data: {"type":"content_block_start","index":0,"content_block":{"type":"thinking","thinking":"Let me analyze."}}\n',
            'event: content_block_delta\n',
            'data: {"type":"content_block_delta","index":0,"delta":{"type":"thinking_delta","thinking":".. OK."}}\n',
            'event: content_block_start\n',
            'data: {"type":"content_block_start","index":1,"content_block":{"type":"text","text":"Hello"}}\n',
            'event: content_block_delta\n',
            'data: {"type":"content_block_delta","index":1,"delta":{"type":"text_delta","text":" world!"}}\n',
            'event: message_delta\n',
            'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":15}}\n',
            'event: message_stop\n',
            'data: {"type":"message_stop"}\n'
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
            "model": "claude-3-5-sonnet-20241022",
            "messages": [{"role": "user", "content": "Hi"}],
            "stream": True,
        }
        context = {"threadId": "thread_anthropic_123", "user": "test_user"}

        from llms.extensions.providers import anthropic
        module_ctx = getattr(anthropic, "ctx", None)

        with patch("aiohttp.ClientSession", return_value=mock_session):
            if module_ctx:
                with patch.object(module_ctx, "threads", mock_threads, create=True):
                    res = await self.provider.chat(chat_data, context=context)
            else:
                res = await self.provider.chat(chat_data, context=context)

        # Verify final response structure in OpenAI format
        self.assertEqual(res["id"], "msg_100")
        self.assertEqual(res["choices"][0]["message"]["content"], "Hello world!")
        self.assertEqual(res["choices"][0]["message"]["thinking"], "Let me analyze... OK.")
        self.assertEqual(res["choices"][0]["finish_reason"], "end_turn")
        self.assertEqual(res["usage"]["prompt_tokens"], 12)
        self.assertEqual(res["usage"]["completion_tokens"], 15)
        self.assertEqual(res["usage"]["total_tokens"], 27)

        # Verify context["providerResponse"] populated
        self.assertIn("providerResponse", context)
        self.assertEqual(context["providerResponse"]["id"], "msg_100")

    async def test_anthropic_non_streaming(self):
        """Test non-streaming fallback when stream=False."""
        chat_data = {
            "model": "claude-3-5-sonnet-20241022",
            "messages": [{"role": "user", "content": "Hi"}],
            "stream": False,
        }
        response_json_payload = {
            "id": "msg_200",
            "type": "message",
            "role": "assistant",
            "model": "claude-3-5-sonnet-20241022",
            "content": [{"type": "text", "text": "Non-stream anthropic response"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 8, "output_tokens": 4},
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

        self.assertEqual(res["id"], "msg_200")
        self.assertEqual(res["choices"][0]["message"]["content"], "Non-stream anthropic response")

    async def test_anthropic_streaming_cancellation(self):
        """Test that streaming returns None immediately when cancelled."""
        sse_lines = [
            'event: message_start\n',
            'data: {"type":"message_start","message":{"id":"msg_300","type":"message","role":"assistant","content":[],"model":"claude-3-5-sonnet-20241022","stop_reason":null,"usage":{"input_tokens":5,"output_tokens":1}}}\n',
            'event: content_block_start\n',
            'data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":"Hello"}}\n',
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
            "model": "claude-3-5-sonnet-20241022",
            "messages": [{"role": "user", "content": "Hi"}],
            "stream": True,
        }
        context = {"threadId": "thread_cancelled", "user": "test_user", "cancelled": True}

        with patch("aiohttp.ClientSession", return_value=mock_session):
            res = await self.provider.chat(chat_data, context=context)

        self.assertIsNone(res)


if __name__ == "__main__":
    unittest.main()
