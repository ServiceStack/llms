import asyncio
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from llms.extensions.computer import bash
from llms.extensions.computer.edit import EditTool20250124
from llms.extensions import core_tools
from llms.extensions.share_llmspy import is_path_within
from llms.main import ExtensionContext, path_is_within


class TestWindowsRoutes(unittest.TestCase):
    def setUp(self):
        self.ctx = ExtensionContext.__new__(ExtensionContext)
        self.ctx.app = SimpleNamespace(ui_extensions=[], server_add_get=[])
        self.ctx.name = "demo"
        self.ctx.ext_prefix = "/ext/demo"
        self.ctx.debug = False
        self.ctx.verbose = False

    def test_extension_urls_always_use_forward_slashes(self):
        self.ctx.register_ui_extension("ui/index.mjs")
        self.assertEqual(self.ctx.app.ui_extensions[0]["path"], "/ext/demo/ui/index.mjs")
        self.assertEqual(self.ctx.web_path("GET", "items/{id}"), "/ext/demo/items/{id}")

    def test_leading_slash_keeps_application_level_route(self):
        self.assertEqual(self.ctx.web_path("GET", "/auth/login"), "/auth/login")

    def test_static_route_uses_url_separators(self):
        self.ctx.add_static_files("unused")
        self.assertEqual(self.ctx.app.server_add_get[0][0], "/ext/demo/{path:.*}")


class TestWindowsProcesses(unittest.TestCase):
    def test_code_runner_executes_directly_without_bash_on_windows(self):
        completed = subprocess.CompletedProcess([], 0, stdout="ok\n", stderr="")
        core_tools.g_ctx = SimpleNamespace(dbg=lambda message: None)
        args = [r"C:\Program Files\Python\python.exe", "script.py"]

        with patch.object(core_tools.os, "name", "nt"), patch.object(
            core_tools.subprocess, "run", return_value=completed
        ) as run_process:
            result = core_tools._run_code_process(args, r"C:\Temp\llms", "print('ok')", "run_python")

        self.assertEqual(result, {"stdout": "ok\n", "stderr": "", "returncode": 0})
        self.assertEqual(run_process.call_args.args[0], args)

    def test_windows_shell_uses_comspec(self):
        with patch.object(bash.sys, "platform", "win32"), patch.dict(
            os.environ, {"COMSPEC": r"C:\Windows\System32\cmd.exe"}
        ):
            session = bash._BashSession()

        self.assertTrue(session._is_windows)
        self.assertEqual(session.command, r"C:\Windows\System32\cmd.exe")


class TestPortableEditor(unittest.TestCase):
    def test_directory_view_does_not_require_unix_find(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "visible").mkdir()
            (root / "visible" / "file.txt").write_text("hello", encoding="utf-8")
            (root / ".hidden").write_text("secret", encoding="utf-8")

            result = asyncio.run(EditTool20250124().view(root))

        self.assertIn("visible", result.output)
        self.assertIn("file.txt", result.output)
        self.assertNotIn(".hidden", result.output)


class TestPathContainment(unittest.TestCase):
    def test_sibling_prefix_is_not_inside_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = os.path.join(temp_dir, "project")
            sibling = os.path.join(temp_dir, "project-backup")
            self.assertTrue(is_path_within(os.path.join(base, "dist"), base))
            self.assertFalse(is_path_within(sibling, base))
            self.assertTrue(path_is_within(os.path.join(base, "dist"), base))
            self.assertFalse(path_is_within(sibling, base))


if __name__ == "__main__":
    unittest.main()
