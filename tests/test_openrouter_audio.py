#!/usr/bin/env python3
"""
Unit tests for OpenRouterAudioGenerator modality provider.
"""

import json
import os
import sys
import tempfile
import unittest
import unittest.mock

# Ensure mock mode is active
os.environ["MOCK"] = "1"
os.environ["OPENROUTER_API_KEY"] = "test-api-key"
os.environ["MOCK_DIR"] = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "mocks"))

# Add parent directory to path to import llms module
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from llms.main import cli, get_app, get_cache_path


class TestOpenRouterAudio(unittest.IsolatedAsyncioTestCase):
    """Test OpenRouter Audio Modality Provider."""

    def setUp(self):
        # Load main configurations
        config_path = os.path.join(os.path.dirname(__file__), "..", "llms", "llms.json")
        with open(config_path) as f:
            self.config = json.load(f)
        providers_path = os.path.join(os.path.dirname(__file__), "..", "llms", "providers.json")
        with open(providers_path) as f:
            self.providers = json.load(f)

        # Merge in providers-extra.json to make sure the extra models are loaded
        extra_path = os.path.join(os.path.dirname(__file__), "..", "llms", "providers-extra.json")
        if os.path.exists(extra_path):
            with open(extra_path) as f:
                extra = json.load(f)
                for k, v in extra.items():
                    if k in self.providers:
                        if "models" not in self.providers[k]:
                            self.providers[k]["models"] = {}
                        self.providers[k]["models"].update(v.get("models", {}))
                    else:
                        self.providers[k] = v

        # Write merged config and providers to temp files
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_config_path = os.path.join(self.temp_dir.name, "llms.json")
        self.temp_providers_path = os.path.join(self.temp_dir.name, "providers.json")

        with open(self.temp_config_path, "w") as f:
            json.dump(self.config, f)
        with open(self.temp_providers_path, "w") as f:
            json.dump(self.providers, f)

        # Run cli to load our temp config/providers, listing openrouter to exit immediately
        cli(f"ls openrouter --config {self.temp_config_path} --providers {self.temp_providers_path}")
        self.app = get_app()

    def tearDown(self):
        self.temp_dir.cleanup()

    @classmethod
    def tearDownClass(cls):
        app = get_app()
        if app:
            app.shutdown()

    async def test_openrouter_audio_registration(self):
        """Test that OpenRouterAudioGenerator is registered to audio modality."""
        provider = self.app.get_providers().get("openrouter")
        self.assertIsNotNone(provider)

        await provider.load()

        # Verify the modality audio is registered to OpenRouterAudioGenerator
        self.assertIn("audio", provider.modalities)
        self.assertEqual(provider.modalities["audio"].__class__.__name__, "OpenRouterAudioGenerator")

    async def test_openrouter_audio_generation(self):
        """Test mock audio generation and cache saving."""
        chat = {
            "model": "google/lyria-3-clip-preview",
            "messages": [
                {
                    "role": "user",
                    "content": "A beautiful synth theme."
                }
            ],
            "modalities": ["audio"]
        }

        response = await self.app.chat_completion(chat)

        self.assertIsNotNone(response)
        self.assertIn("choices", response)
        self.assertTrue(len(response["choices"]) > 0)

        message = response["choices"][0]["message"]
        self.assertEqual(message["role"], "assistant")
        self.assertEqual(message["content"], "I've generated the audio for you.")
        self.assertIn("audios", message)

        audios = message["audios"]
        self.assertTrue(len(audios) > 0)
        self.assertEqual(audios[0]["type"], "audio_url")

        url = audios[0]["audio_url"]["url"]
        self.assertTrue(url.startswith("/~cache/"))
        self.assertTrue(url.endswith(".mp3"))

        # Verify cached files exist
        cache_rel_path = url[8:] # Strip /~cache/
        cache_full_path = get_cache_path(cache_rel_path)
        self.assertTrue(os.path.exists(cache_full_path))
        self.assertTrue(os.path.exists(os.path.splitext(cache_full_path)[0] + ".info.json"))

    async def test_openrouter_gpt_audio_generation(self):
        """Test mock OpenAI audio generation and WAV container formatting."""
        chat = {
            "model": "openai/gpt-audio-mini",
            "messages": [
                {
                    "role": "user",
                    "content": "Say hello world."
                }
            ],
            "modalities": ["audio"]
        }

        response = await self.app.chat_completion(chat)

        self.assertIsNotNone(response)
        self.assertIn("choices", response)
        self.assertTrue(len(response["choices"]) > 0)

        message = response["choices"][0]["message"]
        self.assertEqual(message["role"], "assistant")
        self.assertEqual(message["content"], "I've generated the audio for you.")
        self.assertIn("audios", message)

        audios = message["audios"]
        self.assertTrue(len(audios) > 0)
        self.assertEqual(audios[0]["type"], "audio_url")

        url = audios[0]["audio_url"]["url"]
        self.assertTrue(url.startswith("/~cache/"))
        self.assertTrue(url.endswith(".wav"))

        # Verify cached files exist
        cache_rel_path = url[8:] # Strip /~cache/
        cache_full_path = get_cache_path(cache_rel_path)
        self.assertTrue(os.path.exists(cache_full_path))
        self.assertTrue(os.path.exists(os.path.splitext(cache_full_path)[0] + ".info.json"))

        # Verify it has a valid WAV header
        with open(cache_full_path, "rb") as f:
            header = f.read(44)
            self.assertEqual(header[:4], b"RIFF")
            self.assertEqual(header[8:12], b"WAVE")
            self.assertEqual(header[12:16], b"fmt ")

    @unittest.mock.patch("aiohttp.ClientSession")
    async def test_openrouter_audio_non_streamed_json(self, mock_client_session):
        """Test processing of normal non-streamed JSON audio completions."""
        # Find providers context and save original mock state
        providers_ctx = None
        for ext in self.app.extensions:
            if ext.get("name") == "providers":
                providers_ctx = ext.get("ctx")
                break
        self.assertIsNotNone(providers_ctx, "Could not find providers extension context")
        orig_mock = providers_ctx.MOCK
        providers_ctx.MOCK = False
        try:
            # Prepare mock response data with RIFF-encoded (WAV) audio data
            # base64 encoded for "RIFF" plus some dummy bytes
            wav_base64 = "UklGRigAAABXQVZFZm10IBAAAAABAAEARKwAAIhYAQACABAAZGF0YQQAAAAAAA=="
            mock_json_response = {
                "id": "chatcmpl-123",
                "object": "chat.completion",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "Hello! It's great to talk to you.",
                            "audio": {
                                "id": "audio_abc123",
                                "expires_at": 1799999999,
                                "data": wav_base64,
                                "transcript": "Hello! It's great to talk to you."
                            }
                        },
                        "finish_reason": "stop"
                    }
                ]
            }

            # Setup mock aiohttp response
            from unittest.mock import AsyncMock, MagicMock
            mock_resp = AsyncMock()
            mock_resp.status = 200
            mock_resp.headers = {"Content-Type": "application/json"}
            mock_resp.text = AsyncMock(return_value=json.dumps(mock_json_response))

            mock_session_instance = MagicMock()
            mock_session_instance.post = MagicMock(return_value=mock_resp)
            mock_session_instance.__aenter__ = AsyncMock(return_value=mock_session_instance)
            mock_session_instance.__aexit__ = AsyncMock()

            mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
            mock_resp.__aexit__ = AsyncMock()

            mock_client_session.return_value = mock_session_instance

            chat = {
                "model": "openai/gpt-audio-mini",
                "messages": [
                    {
                        "role": "user",
                        "content": "Hello!"
                    }
                ],
                "modalities": ["audio"]
            }

            response = await self.app.chat_completion(chat)

            self.assertIsNotNone(response)
            self.assertIn("choices", response)
            message = response["choices"][0]["message"]
            self.assertEqual(message["content"], "Hello! It's great to talk to you.")
            self.assertIn("audios", message)
            url = message["audios"][0]["audio_url"]["url"]
            self.assertTrue(url.endswith(".wav"))

            # Verify the cached file exists and starts with RIFF
            cache_rel_path = url[8:]
            cache_full_path = get_cache_path(cache_rel_path)
            self.assertTrue(os.path.exists(cache_full_path))
            with open(cache_full_path, "rb") as f:
                content = f.read()
                self.assertTrue(content.startswith(b"RIFF"))

        finally:
            if providers_ctx is not None:
                providers_ctx.MOCK = orig_mock

    @unittest.mock.patch("aiohttp.ClientSession")
    async def test_openrouter_audio_non_streamed_m4a(self, mock_client_session):
        """Test processing of normal non-streamed M4A audio completions."""
        # Find providers context and save original mock state
        providers_ctx = None
        for ext in self.app.extensions:
            if ext.get("name") == "providers":
                providers_ctx = ext.get("ctx")
                break
        self.assertIsNotNone(providers_ctx, "Could not find providers extension context")
        orig_mock = providers_ctx.MOCK
        providers_ctx.MOCK = False
        try:
            # Prepare mock response data with M4A-encoded (MP4 container) audio data
            # base64 encoded for something starting with "ftyp" at index 4
            import base64
            m4a_base64 = base64.b64encode(b"aaaaftypM4A dummy audio data").decode("utf-8")
            mock_json_response = {
                "id": "chatcmpl-123",
                "object": "chat.completion",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "Hello in m4a format!",
                            "audio": {
                                "id": "audio_m4a123",
                                "expires_at": 1799999999,
                                "data": m4a_base64,
                                "transcript": "Hello in m4a format!"
                            }
                        },
                        "finish_reason": "stop"
                    }
                ]
            }

            # Setup mock aiohttp response
            from unittest.mock import AsyncMock, MagicMock
            mock_resp = AsyncMock()
            mock_resp.status = 200
            mock_resp.headers = {"Content-Type": "application/json"}
            mock_resp.text = AsyncMock(return_value=json.dumps(mock_json_response))

            mock_session_instance = MagicMock()
            mock_session_instance.post = MagicMock(return_value=mock_resp)
            mock_session_instance.__aenter__ = AsyncMock(return_value=mock_session_instance)
            mock_session_instance.__aexit__ = AsyncMock()

            mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
            mock_resp.__aexit__ = AsyncMock()

            mock_client_session.return_value = mock_session_instance

            chat = {
                "model": "openai/gpt-audio-mini",
                "messages": [
                    {
                        "role": "user",
                        "content": "Hello!"
                    }
                ],
                "modalities": ["audio"]
            }

            response = await self.app.chat_completion(chat)

            self.assertIsNotNone(response)
            self.assertIn("choices", response)
            message = response["choices"][0]["message"]
            self.assertEqual(message["content"], "Hello in m4a format!")
            self.assertIn("audios", message)
            url = message["audios"][0]["audio_url"]["url"]
            self.assertTrue(url.endswith(".m4a"))

            # Verify the cached file exists and starts with "aaaaftypM4A "
            cache_rel_path = url[8:]
            cache_full_path = get_cache_path(cache_rel_path)
            self.assertTrue(os.path.exists(cache_full_path))
            with open(cache_full_path, "rb") as f:
                content = f.read()
                self.assertTrue(content.startswith(b"aaaaftypM4A "))

        finally:
            if providers_ctx is not None:
                providers_ctx.MOCK = orig_mock

if __name__ == "__main__":
    unittest.main()
