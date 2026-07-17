#!/usr/bin/env python3
"""
Unit tests for OpenRouterImageGenerator modality provider.
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

from llms.main import cli, get_app, get_cache_path


class TestOpenRouterImage(unittest.IsolatedAsyncioTestCase):
    """Test OpenRouter Image Modality Provider."""

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

    async def test_openrouter_image_registration(self):
        """Test that OpenRouterImageGenerator is registered to image modality."""
        provider = self.app.get_providers().get("openrouter")
        self.assertIsNotNone(provider)

        await provider.load()

        # Verify the modality image is registered to OpenRouterImageGenerator
        self.assertIn("image", provider.modalities)
        self.assertEqual(provider.modalities["image"].__class__.__name__, "OpenRouterImageGenerator")

    async def test_openrouter_image_generation(self):
        """Test mock image generation and cache saving."""
        chat = {
            "model": "openai/gpt-5-image",
            "messages": [
                {
                    "role": "user",
                    "content": "A beautiful neoclassical grand library."
                }
            ],
            "modalities": ["image"]
        }

        response = await self.app.chat_completion(chat)

        self.assertIsNotNone(response)
        self.assertIn("choices", response)
        self.assertTrue(len(response["choices"]) > 0)

        choice = response["choices"][0]
        self.assertIn("message", choice)
        self.assertIn("images", choice["message"])
        self.assertTrue(len(choice["message"]["images"]) > 0)

        image = choice["message"]["images"][0]
        self.assertIn("image_url", image)
        self.assertIn("url", image["image_url"])
        
        # Check that cache path is created/used correctly
        cached_url = image["image_url"]["url"]
        self.assertTrue(cached_url.startswith("/~cache/"))
        
        # Resolve real path and verify it exists
        relative_path = cached_url.replace("/~cache/", "")
        real_cache_path = get_cache_path(relative_path)
        self.assertTrue(os.path.exists(real_cache_path))
        self.assertTrue(os.path.exists(os.path.splitext(real_cache_path)[0] + ".info.json"))
