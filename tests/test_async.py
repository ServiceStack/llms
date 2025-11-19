#!/usr/bin/env python3
"""
Unit tests for async functions in llms.main module.
"""

import asyncio
import os
import sys
import unittest

# Add parent directory to path to import llms module
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from llms.main import process_chat


class TestProcessChat(unittest.TestCase):
    """Test async chat processing functions."""

    def test_process_chat_no_chat(self):
        """Test that process_chat raises exception with no chat."""

        async def run_test():
            with self.assertRaises(Exception) as context:
                await process_chat(None)
            self.assertIn("No chat provided", str(context.exception))

        asyncio.run(run_test())

    def test_process_chat_adds_stream_default(self):
        """Test that process_chat adds stream=False by default."""

        async def run_test():
            chat = {"messages": []}
            result = await process_chat(chat)
            self.assertIn("stream", result)
            self.assertEqual(result["stream"], False)

        asyncio.run(run_test())

    def test_process_chat_preserves_stream(self):
        """Test that process_chat preserves existing stream value."""

        async def run_test():
            chat = {"messages": [], "stream": True}
            result = await process_chat(chat)
            self.assertEqual(result["stream"], True)

        asyncio.run(run_test())

    def test_process_chat_no_messages(self):
        """Test that process_chat handles chat without messages."""

        async def run_test():
            chat = {"model": "gpt-4"}
            result = await process_chat(chat)
            self.assertEqual(result, chat)

        asyncio.run(run_test())

    def test_process_chat_simple_text_message(self):
        """Test that process_chat handles simple text messages."""

        async def run_test():
            chat = {"messages": [{"role": "user", "content": "Hello, world!"}]}
            result = await process_chat(chat)
            self.assertIn("messages", result)
            self.assertEqual(len(result["messages"]), 1)
            self.assertEqual(result["messages"][0]["content"], "Hello, world!")

        asyncio.run(run_test())

    def test_process_chat_message_without_content(self):
        """Test that process_chat handles messages without content."""

        async def run_test():
            chat = {"messages": [{"role": "system"}]}
            result = await process_chat(chat)
            self.assertIn("messages", result)
            self.assertEqual(len(result["messages"]), 1)

        asyncio.run(run_test())

    def test_process_chat_multiple_messages(self):
        """Test that process_chat handles multiple messages."""

        async def run_test():
            chat = {
                "messages": [
                    {"role": "system", "content": "You are a helpful assistant."},
                    {"role": "user", "content": "Hello!"},
                    {"role": "assistant", "content": "Hi there!"},
                ]
            }
            result = await process_chat(chat)
            self.assertIn("messages", result)
            self.assertEqual(len(result["messages"]), 3)

        asyncio.run(run_test())


class TestAsyncHelpers(unittest.TestCase):
    """Test async helper functions."""

    def test_async_function_runs(self):
        """Test that async functions can be run."""

        async def simple_async():
            return "success"

        result = asyncio.run(simple_async())
        self.assertEqual(result, "success")


if __name__ == "__main__":
    unittest.main()
