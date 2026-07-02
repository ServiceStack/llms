#!/usr/bin/env python3
"""
Unit tests for OpenRouterAudioGenerator modality provider.
"""

import json
import os
import sys
import tempfile
import unittest

# Ensure mock mode is active
os.environ["MOCK"] = "1"
os.environ["OPENROUTER_API_KEY"] = "test-api-key"
os.environ["MOCK_DIR"] = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "mocks"))

# Add parent directory to path to import llms module
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from llms.main import (
    get_app,
    cli,
    get_cache_path
)

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

if __name__ == "__main__":
    unittest.main()
