#!/usr/bin/env python3
"""
Focused tests for the Atlas Cloud provider configuration.
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from llms import main  # noqa: E402


class TestAtlasCloudProviderConfiguration(unittest.TestCase):
    def test_atlascloud_uses_openai_compatible_provider(self):
        config_path = os.path.join(os.path.dirname(__file__), "..", "llms", "llms.json")
        providers_path = os.path.join(os.path.dirname(__file__), "..", "llms", "providers.json")
        with open(config_path) as f:
            config = json.load(f)
        with open(providers_path) as f:
            providers = json.load(f)

        previous_config = main.g_config
        previous_app = main.g_app
        try:
            main.g_config = {"defaults": {"headers": {"Content-Type": "application/json"}}}
            main.g_app = type("App", (), {"all_providers": [main.OpenAiCompatible]})()

            kwargs = main.create_provider_kwargs(config["providers"]["atlascloud"], providers["atlascloud"])
            provider = main.create_provider(kwargs)

            self.assertIsNotNone(provider)
            self.assertEqual(provider.__class__.__name__, "OpenAiCompatible")
            self.assertEqual(provider.api, "https://api.atlascloud.ai/v1")
            self.assertEqual(provider.env, ["ATLASCLOUD_API_KEY"])
            self.assertEqual(provider.provider_model("deepseek-v4-flash"), "deepseek-ai/deepseek-v4-flash")
            self.assertEqual(provider.provider_model("Kimi K2.7 Code"), "moonshotai/kimi-k2.7-code")
            self.assertEqual(len(provider.models), 8)
        finally:
            main.g_config = previous_config
            main.g_app = previous_app


if __name__ == "__main__":
    unittest.main()
