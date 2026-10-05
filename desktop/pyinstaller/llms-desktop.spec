# -*- mode: python ; coding: utf-8 -*-

import ast
import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules


project_root = Path(SPECPATH).parents[1]
desktop_root = project_root / "desktop"
llms_root = project_root / "llms"


def extension_stdlib_imports():
    """Find stdlib imports PyInstaller cannot see in file-loaded extensions."""
    discovered = set()
    for source in (llms_root / "extensions").rglob("*.py"):
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                candidates = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                candidates = [node.module]
            else:
                continue
            for module in candidates:
                if module.split(".", 1)[0] in sys.stdlib_module_names:
                    discovered.add(module)
    return sorted(discovered)

analysis = Analysis(
    [str(desktop_root / "python" / "entrypoint.py")],
    pathex=[str(project_root)],
    binaries=[],
    datas=[(str(llms_root), "llms")],
    # Extensions are discovered from files at runtime, so PyInstaller cannot
    # see their standard-library imports while following the entrypoint.
    hiddenimports=collect_submodules("llms") + extension_stdlib_imports(),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=1,
)

pyz = PYZ(analysis.pure)

executable = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="llms-desktop",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    # Tauri signs the shell, but does not sign binaries copied as resources.
    # PyInstaller signs the sidecar and its collected native libraries together.
    codesign_identity=os.environ.get("APPLE_SIGNING_IDENTITY") or None,
)

collection = COLLECT(
    executable,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="llms-desktop",
)
