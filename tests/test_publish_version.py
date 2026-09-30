import contextlib
import io
import json
import os
import shutil
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest.mock import patch

import publish


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class TestPublishVersion(unittest.TestCase):
    def test_bump_keeps_desktop_metadata_in_sync(self):
        for desktop_version in ("4.0.14", publish.get_current_version()):
            with self.subTest(desktop_version=desktop_version), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                for filename in (
                    "pyproject.toml",
                    "setup.py",
                    "llms/main.py",
                    "llms/ui/ai.mjs",
                    "desktop/src-tauri/Cargo.toml",
                    "desktop/src-tauri/Cargo.lock",
                    "desktop/src-tauri/tauri.conf.json",
                ):
                    destination = root / filename
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(PROJECT_ROOT / filename, destination)

                # An unrelated dependency can have the same version as the app.
                lock_path = root / "desktop/src-tauri/Cargo.lock"
                with lock_path.open("a", encoding="utf-8") as stream:
                    stream.write(f'\n[[package]]\nname = "unrelated"\nversion = "{desktop_version}"\n')
                previous_directory = Path.cwd()
                try:
                    os.chdir(root)
                    publish.update_desktop_version(desktop_version)
                    with patch.object(publish, "run_command") as command, contextlib.redirect_stdout(io.StringIO()):
                        publish.bump_version()
                    self.assertEqual(command.call_count, 4)
                    current = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text())["project"]["version"]
                    major, minor, patch_version = map(int, current.split("."))
                    expected = f"{major}.{minor}.{patch_version + 1}"
                    self.assertEqual(publish.get_current_version(), expected)
                    self.assertEqual(
                        tomllib.loads((root / "desktop/src-tauri/Cargo.toml").read_text())["package"]["version"],
                        expected,
                    )
                    self.assertEqual(
                        json.loads((root / "desktop/src-tauri/tauri.conf.json").read_text())["version"], expected
                    )
                    packages = tomllib.loads(lock_path.read_text())["package"]
                    self.assertEqual(
                        next(package["version"] for package in packages if package["name"] == "llms-desktop"), expected
                    )
                    self.assertEqual(
                        next(package["version"] for package in packages if package["name"] == "unrelated"), desktop_version
                    )
                finally:
                    os.chdir(previous_directory)
