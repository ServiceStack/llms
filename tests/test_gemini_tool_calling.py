#!/usr/bin/env python3
"""
Unit tests for utility functions in llms.main module.
"""

import inspect
import json
import os
import sys
import unittest

# from dotenv import load_dotenv

# Add parent directory to path to import llms module
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from llms.main import (
    cli,
    get_app,
    load_config,
)

# Load environment variables from .env file
# load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

config_path = os.path.join(os.path.dirname(__file__), "..", "llms", "llms.json")
with open(config_path) as f:
    config = json.load(f)
providers_path = os.path.join(os.path.dirname(__file__), "..", "llms", "providers.json")
with open(providers_path) as f:
    providers = json.load(f)

g_app = None


class TestGeminiToolCalling(unittest.IsolatedAsyncioTestCase):
    """Test Gemini Tool Calling."""

    def setUp(self):
        load_config(
            config, providers, debug=True, verbose=True, disable_extensions=["duckduckgo", "fast_mcp", "gemini", "xmas"]
        )
        cli("ls minimax")
        global g_app
        g_app = get_app()

    @classmethod
    def tearDownClass(cls):
        g_app.shutdown()

    async def test_gemini_tool_calling(self):
        provider = g_app.get_providers()["google"]
        chat = {
            "model": "Gemini Flash-Lite Latest",
            "messages": [
                {"role": "user", "content": "Calculate 12 * 34 + 56"},
            ],
        }
        response = await provider.chat(chat)
        print("GEMINI RESPONSE:")
        print(json.dumps(response, indent=2))
        print("GEMINI RESULT:")
        content = response["choices"][0]["message"]["content"]
        print(content)
        self.assertIn("464", content)

    async def test_gemini_multi_step_tool_calling(self):
        # provider = g_app.get_providers()["google"]
        filename = "test_multi_step.txt"
        content_to_write = "Multi-step test content"

        # Ensure cleanup
        if os.path.exists(filename):
            os.remove(filename)

        chat = {
            "model": "Gemini Flash-Lite Latest",
            "messages": [
                {
                    "role": "user",
                    "content": f"Create a file named '{filename}' with the content '{content_to_write}', and then read it back to me.",
                },
            ],
        }

        response = await g_app.chat_completion(chat)

        last_message = response["choices"][0]["message"]
        print(f"GEMINI RESULT (Multi-step):\n{json.dumps(last_message, indent=2)}")

        # Verify file exists and content
        with open(filename) as f:
            self.assertEqual(f.read(), content_to_write)

        # Cleanup
        if os.path.exists(filename):
            os.remove(filename)


if __name__ == "__main__":
    unittest.main()
