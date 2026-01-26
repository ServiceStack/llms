#!/usr/bin/env python3
"""
Unit tests for utility functions in llms.main module.
"""

import json
import os
import sys
import unittest

# Add parent directory to path to import llms module
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from typing import Annotated, Dict, List, Optional

from llms.main import (
    apply_args_to_chat,
    chat_summary,
    function_to_tool_definition,
    get_file_mime_type,
    get_filename,
    is_base_64,
    is_url,
    parse_args_params,
    price_to_string,
)


class TestToolDefinition(unittest.TestCase):
    """Test function_to_tool_definition."""

    def test_simple_function(self):
        def my_func(a: int, b: str):
            """My description."""
            pass

        tool = function_to_tool_definition(my_func)
        self.assertEqual(tool["function"]["name"], "my_func")
        self.assertEqual(tool["function"]["description"], "My description.")
        props = tool["function"]["parameters"]["properties"]
        self.assertEqual(props["a"]["type"], "integer")
        self.assertEqual(props["b"]["type"], "string")
        self.assertIn("a", tool["function"]["parameters"]["required"])

    def test_optional_args(self):
        def func_opt(head: Optional[int] = None, tail: Optional[int] = None):
            pass

        tool = function_to_tool_definition(func_opt)
        props = tool["function"]["parameters"]["properties"]
        self.assertEqual(props["head"]["type"], "integer")
        self.assertEqual(props["tail"]["type"], "integer")
        self.assertNotIn("head", tool["function"]["parameters"]["required"])

    def test_annotated_args(self):
        def func_anno(path: Annotated[str, "Path to file"]):
            pass

        tool = function_to_tool_definition(func_anno)
        props = tool["function"]["parameters"]["properties"]
        self.assertEqual(props["path"]["type"], "string")
        self.assertEqual(props["path"]["description"], "Path to file")

    def test_annotated_optional(self):
        def func_anno_opt(head: Annotated[Optional[int], "Number of lines"] = None):
            pass

        tool = function_to_tool_definition(func_anno_opt)
        props = tool["function"]["parameters"]["properties"]
        self.assertEqual(props["head"]["type"], "integer")
        self.assertEqual(props["head"]["description"], "Number of lines")

    def test_list_str(self):
        def func_list(paths: List[str]):
            pass

        tool = function_to_tool_definition(func_list)
        props = tool["function"]["parameters"]["properties"]
        self.assertEqual(props["paths"]["type"], "array")
        self.assertEqual(props["paths"]["items"]["type"], "string")

    def test_list_dict(self):
        def func_list_dict(edits: List[Dict[str, str]]):
            pass

        tool = function_to_tool_definition(func_list_dict)
        props = tool["function"]["parameters"]["properties"]
        self.assertEqual(props["edits"]["type"], "array")
        self.assertEqual(props["edits"]["items"]["type"], "object")


class TestUrlUtils(unittest.TestCase):
    """Test URL utility functions."""

    def test_is_url_with_http(self):
        self.assertTrue(is_url("http://example.com"))

    def test_is_url_with_https(self):
        self.assertTrue(is_url("https://example.com"))

    def test_is_url_with_path(self):
        self.assertFalse(is_url("/path/to/file"))

    def test_is_url_with_none(self):
        self.assertFalse(is_url(None))

    def test_is_url_with_empty_string(self):
        self.assertFalse(is_url(""))


class TestFilenameUtils(unittest.TestCase):
    """Test filename utility functions."""

    def test_get_filename_with_path(self):
        self.assertEqual(get_filename("/path/to/file.txt"), "file.txt")

    def test_get_filename_with_url(self):
        self.assertEqual(get_filename("https://example.com/path/to/file.txt"), "file.txt")

    def test_get_filename_without_path(self):
        self.assertEqual(get_filename("file.txt"), "file")

    def test_get_filename_with_multiple_slashes(self):
        self.assertEqual(get_filename("/path/to/nested/file.txt"), "file.txt")


class TestParseArgsParams(unittest.TestCase):
    """Test URL parameter parsing."""

    def test_parse_empty_string(self):
        self.assertEqual(parse_args_params(""), {})

    def test_parse_none(self):
        self.assertEqual(parse_args_params(None), {})

    def test_parse_boolean_true(self):
        result = parse_args_params("stream=true")
        self.assertEqual(result, {"stream": True})

    def test_parse_boolean_false(self):
        result = parse_args_params("stream=false")
        self.assertEqual(result, {"stream": False})

    def test_parse_integer(self):
        result = parse_args_params("max_tokens=100")
        self.assertEqual(result, {"max_tokens": 100})

    def test_parse_float(self):
        result = parse_args_params("temperature=0.7")
        self.assertEqual(result, {"temperature": 0.7})

    def test_parse_string(self):
        result = parse_args_params("model=gpt-4")
        self.assertEqual(result, {"model": "gpt-4"})

    def test_parse_multiple_params(self):
        result = parse_args_params("stream=true&max_tokens=100&temperature=0.7")
        self.assertEqual(result, {"stream": True, "max_tokens": 100, "temperature": 0.7})

    def test_parse_multiple_values(self):
        result = parse_args_params("stop=word1&stop=word2")
        self.assertEqual(result, {"stop": ["word1", "word2"]})


class TestApplyArgsToChat(unittest.TestCase):
    """Test applying parsed arguments to chat requests."""

    def test_apply_empty_args(self):
        chat = {"model": "gpt-4"}
        result = apply_args_to_chat(chat, {})
        self.assertEqual(result, {"model": "gpt-4"})

    def test_apply_none_args(self):
        chat = {"model": "gpt-4"}
        result = apply_args_to_chat(chat, None)
        self.assertEqual(result, {"model": "gpt-4"})

    def test_apply_max_tokens(self):
        chat = {"model": "gpt-4"}
        result = apply_args_to_chat(chat, {"max_tokens": "100"})
        self.assertEqual(result["max_tokens"], 100)

    def test_apply_temperature(self):
        chat = {"model": "gpt-4"}
        result = apply_args_to_chat(chat, {"temperature": "0.7"})
        self.assertEqual(result["temperature"], 0.7)

    def test_apply_stop_single(self):
        chat = {"model": "gpt-4"}
        result = apply_args_to_chat(chat, {"stop": "word"})
        self.assertEqual(result["stop"], "word")

    def test_apply_stop_multiple(self):
        chat = {"model": "gpt-4"}
        result = apply_args_to_chat(chat, {"stop": "word1,word2,word3"})
        self.assertEqual(result["stop"], ["word1", "word2", "word3"])


class TestBase64Utils(unittest.TestCase):
    """Test base64 utility functions."""

    def test_is_base_64_valid(self):
        self.assertTrue(is_base_64("SGVsbG8gV29ybGQ="))

    def test_is_base_64_invalid(self):
        self.assertFalse(is_base_64("not base64!@#$"))

    def test_is_base_64_empty(self):
        self.assertTrue(is_base_64(""))


class TestMimeTypeUtils(unittest.TestCase):
    """Test MIME type utility functions."""

    def test_get_file_mime_type_png(self):
        self.assertEqual(get_file_mime_type("image.png"), "image/png")

    def test_get_file_mime_type_jpg(self):
        self.assertEqual(get_file_mime_type("image.jpg"), "image/jpeg")

    def test_get_file_mime_type_txt(self):
        self.assertEqual(get_file_mime_type("file.txt"), "text/plain")

    def test_get_file_mime_type_json(self):
        self.assertEqual(get_file_mime_type("data.json"), "application/json")

    def test_get_file_mime_type_unknown(self):
        self.assertEqual(get_file_mime_type("file.unknown"), "application/octet-stream")


class TestPriceToString(unittest.TestCase):
    """Test price formatting utility."""

    def test_price_to_string_zero(self):
        self.assertEqual(price_to_string(0), "0")

    def test_price_to_string_none(self):
        self.assertEqual(price_to_string(None), "0")

    def test_price_to_string_string_zero(self):
        self.assertEqual(price_to_string("0"), "0")

    def test_price_to_string_simple_float(self):
        result = price_to_string(0.5)
        self.assertEqual(result, "0.5")

    def test_price_to_string_small_number(self):
        result = price_to_string(0.00015)
        self.assertIsNotNone(result)
        self.assertNotIn("e", result)  # No scientific notation

    def test_price_to_string_recurring_nines(self):
        # Test that recurring 9s are rounded up
        result = price_to_string(0.00014999999999999999)
        self.assertIsNotNone(result)
        self.assertNotIn("9999", result)

    def test_price_to_string_integer(self):
        result = price_to_string(5)
        self.assertEqual(result, "5")

    def test_price_to_string_invalid(self):
        result = price_to_string("invalid")
        self.assertIsNone(result)


class TestChatSummary(unittest.TestCase):
    """Test chat summary functions."""

    def test_chat_summary_simple(self):
        chat = {"messages": [{"role": "user", "content": "Hello"}]}
        result = chat_summary(chat)
        self.assertIsInstance(result, str)
        parsed = json.loads(result)
        self.assertEqual(parsed["messages"][0]["content"], "Hello")

    def test_chat_summary_with_image_url(self):
        chat = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "What is this?"},
                        {"type": "image_url", "image_url": {"url": "data:image/png;base64," + "A" * 1000}},
                    ],
                }
            ]
        }
        result = chat_summary(chat)
        self.assertIsInstance(result, str)
        # Check that the image data is truncated
        self.assertIn("data:image/png;base64,", result)
        self.assertNotIn("A" * 1000, result)

    def test_chat_summary_with_audio(self):
        chat = {
            "messages": [{"role": "user", "content": [{"type": "input_audio", "input_audio": {"data": "B" * 1000}}]}]
        }
        result = chat_summary(chat)
        self.assertIsInstance(result, str)
        # Check that audio data is replaced with size
        self.assertNotIn("B" * 1000, result)


if __name__ == "__main__":
    unittest.main()
