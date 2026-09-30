#!/usr/bin/env python3
"""Keep the independently packaged desktop artifacts on the llms-py version."""

import argparse
import json
import sys
from pathlib import Path


DESKTOP_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = DESKTOP_ROOT.parent


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", help="Release tag to verify against the package version")
    args = parser.parse_args()
    if sys.version_info < (3, 11):
        raise RuntimeError("Version checks require Python 3.11 or newer")
    import tomllib

    with (PROJECT_ROOT / "pyproject.toml").open("rb") as stream:
        package_version = tomllib.load(stream)["project"]["version"]
    with (DESKTOP_ROOT / "src-tauri" / "Cargo.toml").open("rb") as stream:
        rust_version = tomllib.load(stream)["package"]["version"]
    with (DESKTOP_ROOT / "src-tauri" / "Cargo.lock").open("rb") as stream:
        lock_version = next(
            package["version"] for package in tomllib.load(stream)["package"] if package["name"] == "llms-desktop"
        )
    with (DESKTOP_ROOT / "src-tauri" / "tauri.conf.json").open(encoding="utf-8") as stream:
        tauri_version = json.load(stream)["version"]

    versions = {
        "pyproject.toml": package_version,
        "desktop/src-tauri/Cargo.toml": rust_version,
        "desktop/src-tauri/Cargo.lock": lock_version,
        "desktop/src-tauri/tauri.conf.json": tauri_version,
    }
    if len(set(versions.values())) != 1:
        details = ", ".join(f"{path}={version}" for path, version in versions.items())
        print(f"Desktop version mismatch: {details}", file=sys.stderr)
        return 1
    if args.tag and args.tag not in {f"v{package_version}", f"desktop-v{package_version}"}:
        print(
            f"Desktop release tag mismatch: expected v{package_version} or desktop-v{package_version}, got {args.tag}",
            file=sys.stderr,
        )
        return 1
    print(f"Desktop version: {package_version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
