import argparse
import unittest
from llms.main import AppExtensions

class TestCreateChatWithTools(unittest.TestCase):
    def setUp(self):
        cli_args = argparse.Namespace()
        extra_args = {}
        self.app = AppExtensions(cli_args, extra_args)
        self.app.tool_definitions = [
            {
                "type": "function",
                "function": {
                    "name": "write_file",
                    "description": "Write to a file",
                    "parameters": {"type": "object", "properties": {"path": {"type": "string"}}}
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "read_text_file",
                    "description": "Read a text file",
                    "parameters": {"type": "object", "properties": {"path": {"type": "string"}}}
                }
            }
        ]

    def test_no_existing_tools_all_enabled(self):
        chat = {"messages": []}
        res = self.app.create_chat_with_tools(chat, use_tools="all")
        self.assertEqual(len(res["tools"]), 2)
        tool_names = {t["function"]["name"] for t in res["tools"]}
        self.assertEqual(tool_names, {"write_file", "read_text_file"})

    def test_existing_server_tools_all_enabled(self):
        chat = {
            "messages": [],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "openrouter_datetime_tool",
                        "description": "Get current datetime"
                    }
                }
            ]
        }
        res = self.app.create_chat_with_tools(chat, use_tools="all")
        # Should contain BOTH the server tool and all client tools!
        self.assertEqual(len(res["tools"]), 3)
        tool_names = {t["function"]["name"] for t in res["tools"]}
        self.assertEqual(tool_names, {"openrouter_datetime_tool", "write_file", "read_text_file"})

    def test_use_tools_none(self):
        chat = {
            "messages": [],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "openrouter_datetime_tool",
                        "description": "Get current datetime"
                    }
                }
            ]
        }
        res = self.app.create_chat_with_tools(chat, use_tools="none")
        # Should contain ONLY the server tool (no client tools)
        self.assertEqual(len(res["tools"]), 1)
        self.assertEqual(res["tools"][0]["function"]["name"], "openrouter_datetime_tool")

    def test_use_tools_specific_list(self):
        chat = {
            "messages": [],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "openrouter_datetime_tool",
                        "description": "Get current datetime"
                    }
                }
            ]
        }
        res = self.app.create_chat_with_tools(chat, use_tools="write_file")
        # Should contain the server tool and ONLY the write_file client tool
        self.assertEqual(len(res["tools"]), 2)
        tool_names = {t["function"]["name"] for t in res["tools"]}
        self.assertEqual(tool_names, {"openrouter_datetime_tool", "write_file"})

    def test_existing_non_function_tool(self):
        chat = {
            "messages": [],
            "tools": [
                {
                    "type": "openrouter:datetime",
                    "parameters": {
                        "timezone": "Asia/Tokyo"
                    }
                }
            ]
        }
        res = self.app.create_chat_with_tools(chat, use_tools="all")
        # Should contain the non-function tool and all client tools
        self.assertEqual(len(res["tools"]), 3)
        self.assertEqual(res["tools"][0]["type"], "openrouter:datetime")
        tool_names = {t["function"]["name"] for t in res["tools"][1:]}
        self.assertEqual(tool_names, {"write_file", "read_text_file"})

