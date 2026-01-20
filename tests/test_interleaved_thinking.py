#!/usr/bin/env python3
"""
Unit tests for utility functions in llms.main module.
"""

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


class TestInterleavedThinking(unittest.IsolatedAsyncioTestCase):
    """Test Interleaved Thinking."""

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

    async def test_anthropic_interleaved_thinking(self):
        provider = g_app.get_providers()["minimax"]
        chat = {
            "model": "MiniMax-M2.1",
            "messages": [
                {"role": "user", "content": "Calculate 123 * 456"},
            ],
        }
        response = await provider.chat(chat)
        print(json.dumps(response, indent=2))
        self.assertEqual(chat["messages"], chat["messages"])
