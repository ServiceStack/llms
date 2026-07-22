#!/usr/bin/env python3
"""
Unit tests for refactored g_chat_completion in llms.main module.
"""

import argparse
import asyncio
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import llms.main as main
from llms.main import AppExtensions, g_chat_completion, g_handlers


class MockProvider:
    def __init__(self, name="mock_provider", responses=None):
        self.name = name
        self.responses = responses or []
        self.call_count = 0

    def provider_model(self, model):
        return True

    def model_info(self, model):
        return {"tool_call": True, "cost": {"input": 0.001, "output": 0.002}}

    def model_cost(self, model):
        return {"input": 0.001, "output": 0.002}

    async def chat(self, chat_req, context=None):
        if self.call_count < len(self.responses):
            res = self.responses[self.call_count]
            self.call_count += 1
            return res
        raise Exception(f"{self.name} out of responses")


class TestGChatCompletionRefactor(unittest.TestCase):

    def setUp(self):
        self.app = AppExtensions(argparse.Namespace(), {})
        main.g_app = self.app
        self.original_handlers = dict(g_handlers)
        g_handlers.clear()

    def tearDown(self):
        g_handlers.clear()
        g_handlers.update(self.original_handlers)

    def test_token_usage_not_double_counted(self):
        """Test prompt tokens reflect final step input count rather than double counting."""

        async def run_test():
            responses = [
                # Turn 1: LLM returns tool call
                {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "tool_calls": [
                                    {
                                        "id": "call_1",
                                        "function": {"name": "test_tool", "arguments": '{"x": 1}'},
                                    }
                                ],
                            }
                        }
                    ],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
                },
                # Turn 2: LLM returns final text answer
                {
                    "choices": [{"message": {"role": "assistant", "content": "Done!"}}],
                    "usage": {"prompt_tokens": 150, "completion_tokens": 30, "total_tokens": 180},
                },
            ]

            provider = MockProvider("test_provider", responses)
            g_handlers["test_provider"] = provider

            async def dummy_tool(x):
                return f"Result {x}"

            self.app.tools["test_tool"] = dummy_tool

            chat = {"model": "test-model", "messages": [{"role": "user", "content": "Hi"}]}
            res = await g_chat_completion(chat)

            # Check usage aggregation
            self.assertIn("usage", res)
            # prompt_tokens should be 150 (last step), completion_tokens should be 50 (20 + 30)
            self.assertEqual(res["usage"]["prompt_tokens"], 150)
            self.assertEqual(res["usage"]["completion_tokens"], 50)
            self.assertEqual(res["usage"]["total_tokens"], 200)

        asyncio.run(run_test())

    def test_state_isolation_on_provider_failover(self):
        """Test that failure in Provider 1 does not pollute Provider 2's message history."""

        async def run_test():
            # Provider 1 succeeds on 1 tool call then throws on turn 2
            p1 = MockProvider(
                "failing_p1",
                [
                    {
                        "choices": [
                            {
                                "message": {
                                    "role": "assistant",
                                    "tool_calls": [
                                        {
                                            "id": "p1_call",
                                            "function": {"name": "test_tool", "arguments": '{"x": 1}'},
                                        }
                                    ],
                                }
                            }
                        ],
                        "usage": {"prompt_tokens": 50, "completion_tokens": 10},
                    }
                ],
            )

            # Provider 2 succeeds directly
            p2_chat_messages = []

            class Provider2(MockProvider):

                async def chat(self, chat_req, context=None):
                    nonlocal p2_chat_messages
                    p2_chat_messages = list(chat_req["messages"])
                    return {
                        "choices": [{"message": {"role": "assistant", "content": "P2 Success"}}],
                        "usage": {"prompt_tokens": 60, "completion_tokens": 15},
                    }

            p2 = Provider2("working_p2")

            g_handlers["failing_p1"] = p1
            g_handlers["working_p2"] = p2

            async def dummy_tool(x):
                return f"Result {x}"

            self.app.tools["test_tool"] = dummy_tool

            chat = {"model": "test-model", "messages": [{"role": "user", "content": "Hello"}]}
            res = await g_chat_completion(chat)

            self.assertEqual(res["choices"][0]["message"]["content"], "P2 Success")
            # Verify p2 received pristine base messages, NOT p1's tool_call messages
            roles = [m.get("role") for m in p2_chat_messages]
            self.assertNotIn("tool", roles)
            self.assertEqual(roles, ["user"])

        asyncio.run(run_test())

    def test_max_iterations_exception(self):
        """Test that reaching max iterations raises an explicit exception."""

        async def run_test():
            # Provider returns tool calls continuously
            infinite_tool_response = {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "id": "loop_call",
                                    "function": {"name": "test_tool", "arguments": '{"x": 1}'},
                                }
                            ],
                        }
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            }

            p1 = MockProvider("loop_provider", [infinite_tool_response] * 15)
            g_handlers["loop_provider"] = p1

            async def dummy_tool(x):
                return "ok"

            self.app.tools["test_tool"] = dummy_tool

            chat = {"model": "test-model", "messages": [{"role": "user", "content": "Loop test"}]}
            context = {"max_iterations": 3}

            with self.assertRaises(Exception) as ctx:
                await g_chat_completion(chat, context=context)

            self.assertIn("Reached maximum tool iterations (3)", str(ctx.exception))

        asyncio.run(run_test())


if __name__ == "__main__":
    unittest.main()
