import io
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import llms.extensions.core_tools as core_tools

# Mock g_ctx
core_tools.g_ctx = MagicMock()


class TestCoreToolsDirect(unittest.TestCase):
    def test_calc(self):
        # Simple list comprehension
        res = core_tools.calc("sum([x * 2 for x in [1, 2, 3]])")
        self.assertEqual(res, 12)

        # List comprehension with condition
        res = core_tools.calc("sum([x for x in [1, 2, 3, 4] if x > 2])")
        self.assertEqual(res, 7)

        # Range support
        res = core_tools.calc("sum([x for x in range(5)])")
        self.assertEqual(res, 10)

    def test_html_to_markdown_parser(self):
        html = """
        <!DOCTYPE html>
        <html>
        <head><title>Test Page</title><style>body { color: red; }</style></head>
        <body>
            <script>console.log('ignore me');</script>
            <h1>Main Title</h1>
            <p>This is a paragraph with <b>bold text</b>, <i>italic text</i>, and a <a href="/docs/guide">Documentation Link</a>.</p>
            <pre><code>def hello():
    print("Hello world")</code></pre>
            <ul>
                <li>Item 1</li>
                <li>Item 2</li>
            </ul>
            <blockquote>A wise quote</blockquote>
            <table>
                <tr><th>Name</th><th>Role</th></tr>
                <tr><td>Alice</td><td>Admin</td></tr>
            </table>
        </body>
        </html>
        """
        parser = core_tools.HTMLToMarkdownParser(base_url="https://example.com/base/")
        parser.feed(html)
        md = parser.get_markdown()

        self.assertIn("# Main Title", md)
        self.assertIn("**bold text**", md)
        self.assertIn("*italic text*", md)
        self.assertIn("[Documentation Link](https://example.com/docs/guide)", md)
        self.assertIn("```\ndef hello():\n    print(\"Hello world\")\n```", md)
        self.assertIn("- Item 1", md)
        self.assertIn("- Item 2", md)
        self.assertIn("> A wise quote", md)
        self.assertNotIn("console.log", md)
        self.assertNotIn("body { color: red; }", md)

    def test_fetch_url(self):
        sample_html = b"<html><body><h1>Hello World</h1><p>Fetched content</p></body></html>"

        mock_resp = MagicMock()
        mock_resp.headers.get.return_value = "text/html; charset=utf-8"
        mock_resp.headers.get_content_charset.return_value = "utf-8"
        mock_resp.read.return_value = sample_html
        mock_resp.__enter__.return_value = mock_resp
        mock_resp.__exit__.return_value = None

        with patch("urllib.request.urlopen", return_value=mock_resp):
            res = core_tools.fetch_url("https://example.com/test")
            self.assertIn("# Hello World", res)
            self.assertIn("Fetched content", res)

            # Test truncation
            res_trunc = core_tools.fetch_url("https://example.com/test", max_length=10)
            self.assertIn("Truncated", res_trunc)

    def test_grep_search(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            f1 = os.path.join(tmp_dir, "test1.py")
            f2 = os.path.join(tmp_dir, "test2.txt")
            ignored_sub = os.path.join(tmp_dir, "node_modules")
            os.makedirs(ignored_sub, exist_ok=True)
            f3 = os.path.join(ignored_sub, "ignored.py")

            with open(f1, "w", encoding="utf-8") as f:
                f.write("def calculate_total(a, b):\n    return a + b\n")
            with open(f2, "w", encoding="utf-8") as f:
                f.write("Note: calculate_total should be tested.\nAnother line.\n")
            with open(f3, "w", encoding="utf-8") as f:
                f.write("calculate_total in node_modules\n")

            # Search literal
            res = core_tools.grep_search("calculate_total", path=tmp_dir)
            self.assertIn("test1.py:1: def calculate_total", res)
            self.assertIn("test2.txt:1: Note: calculate_total", res)
            self.assertNotIn("node_modules", res)

            # Search with file pattern
            res_pattern = core_tools.grep_search("calculate_total", path=tmp_dir, file_pattern="*.py")
            self.assertIn("test1.py:1:", res_pattern)
            self.assertNotIn("test2.txt", res_pattern)

            # Search regex
            res_regex = core_tools.grep_search(r"def\s+\w+\(", path=tmp_dir, is_regex=True)
            self.assertIn("test1.py:1: def calculate_total", res_regex)
            self.assertNotIn("test2.txt", res_regex)

            # Search non-matching
            res_none = core_tools.grep_search("non_existent_symbol", path=tmp_dir)
            self.assertEqual(res_none, "No matches found for 'non_existent_symbol'.")


if __name__ == "__main__":
    unittest.main()
