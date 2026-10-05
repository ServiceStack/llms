"""Cross-repository shared-UI contract; optional when the C# checkout is absent."""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

PYTHON_ROOT = Path(__file__).resolve().parents[1]
CSHARP_ROOT = Path(
    os.environ.get("AI_CHAT_SOURCE", PYTHON_ROOT.parent / "ServiceStack/ServiceStack/src/ServiceStack.AI.Chat")
)


@unittest.skipUnless((CSHARP_ROOT / "sync.sh").is_file() and (CSHARP_ROOT / "sync.py").is_file(), "C# checkout with sync scripts required")
class McpSyncTests(unittest.TestCase):
    def test_csharp_ui_is_byte_identical(self):
        self.assertEqual(
            (PYTHON_ROOT / "llms/extensions/mcp_client/ui/index.mjs").read_bytes(),
            (CSHARP_ROOT / "chat/ext/mcp_client/index.mjs").read_bytes(),
        )

    def test_focused_sync_replaces_shared_assets_and_preserves_unrelated_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            port, package = root / "port", root / "python"
            port.mkdir()
            script = port / "sync.sh"
            shutil.copy2(CSHARP_ROOT / "sync.sh", script)
            shutil.copy2(CSHARP_ROOT / "sync.py", port / "sync.py")
            source = package / "extensions/mcp_client/ui"
            source.mkdir(parents=True)
            (package / "index.html").write_text("fixture")
            (source / "index.mjs").write_text("new shared UI")
            target = port / "chat/ext/mcp_client"
            target.mkdir(parents=True)
            (target / "index.mjs").write_text("old UI")
            (target / "stale.mjs").write_text("stale")
            sentinel = port / "chat/ui/keep.mjs"
            sentinel.parent.mkdir()
            sentinel.write_text("untouched")
            subprocess.run(
                ["bash", str(script), "--extension", "mcp_client", str(package)], check=True, capture_output=True
            )
            self.assertEqual((target / "index.mjs").read_bytes(), (source / "index.mjs").read_bytes())
            self.assertFalse((target / "stale.mjs").exists())
            self.assertEqual(sentinel.read_text(), "untouched")
            rejected = subprocess.run(
                ["bash", str(script), "--extension", "../identity", str(package)], capture_output=True
            )
            self.assertNotEqual(rejected.returncode, 0)


if __name__ == "__main__":
    unittest.main()
