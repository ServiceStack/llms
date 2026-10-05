#!/usr/bin/env python3
"""Build the private Python runtime consumed by the Tauri application."""

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path


DESKTOP_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = DESKTOP_ROOT.parent
SPEC_PATH = DESKTOP_ROOT / "pyinstaller" / "llms-desktop.spec"
BUILD_PATH = DESKTOP_ROOT / "build"
DIST_PATH = DESKTOP_ROOT / "dist"
RESOURCE_PATH = DESKTOP_ROOT / "src-tauri" / "resources" / "sidecar"


def native_target() -> str:
    system = platform.system().lower()
    machine = platform.machine().lower()
    if system == "darwin":
        return "aarch64-apple-darwin" if machine in {"arm64", "aarch64"} else "x86_64-apple-darwin"
    if system == "linux":
        return "aarch64-unknown-linux-gnu" if machine in {"arm64", "aarch64"} else "x86_64-unknown-linux-gnu"
    if system == "windows":
        return "aarch64-pc-windows-msvc" if machine in {"arm64", "aarch64"} else "x86_64-pc-windows-msvc"
    raise RuntimeError(f"Unsupported desktop build platform: {system}/{machine}")


def package_version() -> str:
    if sys.version_info < (3, 11):
        raise RuntimeError("The desktop build requires Python 3.11 or newer")
    import tomllib

    with (PROJECT_ROOT / "pyproject.toml").open("rb") as stream:
        return tomllib.load(stream)["project"]["version"]


def remove_generated(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", default=native_target())
    parser.add_argument("--keep-build", action="store_true")
    args = parser.parse_args()

    if not SPEC_PATH.is_file():
        raise RuntimeError(
            f"Desktop PyInstaller spec is missing: {SPEC_PATH}. "
            "Ensure desktop/pyinstaller/llms-desktop.spec is included in the source checkout."
        )

    actual_target = native_target()
    if args.target != actual_target:
        parser.error(
            f"PyInstaller cannot cross-build the Python runtime: requested {args.target}, running on {actual_target}"
        )

    if not args.keep_build:
        remove_generated(BUILD_PATH)
        remove_generated(DIST_PATH)
    remove_generated(RESOURCE_PATH)

    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--workpath",
        str(BUILD_PATH),
        "--distpath",
        str(DIST_PATH),
        str(SPEC_PATH),
    ]
    build_env = os.environ.copy()
    build_env["PYINSTALLER_CONFIG_DIR"] = str(BUILD_PATH / "pyinstaller-cache")
    subprocess.run(command, cwd=PROJECT_ROOT, env=build_env, check=True)

    built_path = DIST_PATH / "llms-desktop"
    executable_name = "llms-desktop.exe" if os.name == "nt" else "llms-desktop"
    if not (built_path / executable_name).is_file():
        raise RuntimeError(f"PyInstaller output is missing {executable_name}")

    # Framework layout and Python's library aliases must survive packaging.
    shutil.copytree(built_path, RESOURCE_PATH, symlinks=sys.platform == "darwin")
    identity = os.environ.get("APPLE_SIGNING_IDENTITY", "").strip()
    if sys.platform == "darwin" and identity and identity != "-":
        # PyInstaller signs individual Mach-O files. Frameworks also need a
        # resource seal after collection, before the outer Tauri app is signed.
        frameworks = sorted(RESOURCE_PATH.rglob("*.framework"), key=lambda path: len(path.parts), reverse=True)
        for framework in frameworks:
            subprocess.run(
                ["codesign", "--force", "--sign", identity, "--timestamp", "--options", "runtime", str(framework)],
                check=True,
            )
            subprocess.run(["codesign", "--verify", "--strict", str(framework)], check=True)
    executable = RESOURCE_PATH / executable_name
    executable.chmod(executable.stat().st_mode | 0o111)
    (RESOURCE_PATH / "build-info.json").write_text(
        json.dumps(
            {
                "version": package_version(),
                "target": args.target,
                "python": platform.python_version(),
                "format": "pyinstaller-onedir",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"Desktop sidecar ready: {RESOURCE_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
