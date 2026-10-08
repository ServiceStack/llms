import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from llms.main import load_env


class TestLoadEnv(unittest.TestCase):
    def test_defaults_quotes_comments_and_existing_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text(
                '\ufeff# comment\nAPI_KEY=file-secret\nexport DEBUG=1\n'
                'DOUBLE="with spaces # hash" # comment\nSINGLE=\'literal $value\'\n'
                'EMPTY=\nPLAIN=value # comment\nHASH=abc#123\n'
                'invalid name=value\nMALFORMED="unterminated\n',
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"API_KEY": "shell-secret"}, clear=True):
                load_env(path)
                self.assertEqual(dict(os.environ), {
                    "API_KEY": "shell-secret", "DEBUG": "1", "DOUBLE": "with spaces # hash",
                    "SINGLE": "literal $value", "EMPTY": "", "PLAIN": "value", "HASH": "abc#123",
                })

    def test_missing_file_is_optional(self):
        with tempfile.TemporaryDirectory() as directory:
            load_env(Path(directory) / ".env")

    def test_env_loaded_before_startup_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / ".env").write_text("DEBUG=1\nLLMS_MODE=env-test\n", encoding="utf-8")
            environment = os.environ.copy()
            environment.pop("DEBUG", None)
            environment.pop("LLMS_MODE", None)
            environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
            result = subprocess.run(
                [sys.executable, "-c",
                 "import sys; from llms.main import DEBUG, LLMS_MODE; "
                 "assert DEBUG == (sys.platform == 'win32'); "
                 "assert LLMS_MODE == ('env-test' if sys.platform == 'win32' else 'local')"],
                cwd=directory, env=environment, capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
