#!/usr/bin/env python3
"""Build the private Python runtime followed by the native Tauri bundle."""

import argparse
import getpass
import os
import shutil
import subprocess
import sys
from pathlib import Path


DESKTOP_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = DESKTOP_ROOT.parent


def configure_notarization() -> None:
    """Collect Apple ID credentials locally, without saving the password to disk."""
    if sys.platform != "darwin":
        raise RuntimeError("Apple notarization must run on macOS")
    identity = os.environ.get("APPLE_SIGNING_IDENTITY", "").strip()
    if not identity.startswith("Developer ID Application:"):
        raise RuntimeError("Set APPLE_SIGNING_IDENTITY to your Developer ID Application certificate name")
    for name, prompt in (
        ("APPLE_ID", "Apple Account email: "),
        ("APPLE_TEAM_ID", "Apple Developer Team ID: "),
        ("APPLE_PASSWORD", "Apple app-specific password (hidden): "),
    ):
        if not os.environ.get(name, "").strip():
            value = getpass.getpass(prompt) if name == "APPLE_PASSWORD" else input(prompt).strip()
            if not value.strip():
                raise RuntimeError(f"{name} is required for notarization")
            os.environ[name] = value


def tauri_command() -> list[str]:
    cargo = shutil.which("cargo")
    if not cargo:
        raise RuntimeError("Rust is required to build the desktop shell: https://rustup.rs")
    completed = subprocess.run(
        [cargo, "tauri", "--version"],
        cwd=DESKTOP_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode:
        raise RuntimeError("Tauri CLI v2 is required; install it with: cargo install tauri-cli --version '^2' --locked")
    return [cargo, "tauri"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundles", help="Comma-separated Tauri bundle types, such as app,dmg or deb,appimage")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--skip-sidecar", action="store_true")
    parser.add_argument("--release-updater", action="store_true")
    parser.add_argument("--notarize", action="store_true", help="Prompt locally for Apple ID notarization credentials")
    args = parser.parse_args()
    if args.notarize:
        if args.debug:
            parser.error("--notarize requires a release build; omit --debug")
        configure_notarization()

    subprocess.run([sys.executable, str(DESKTOP_ROOT / "scripts" / "check-version.py")], cwd=PROJECT_ROOT, check=True)
    if not args.skip_sidecar:
        subprocess.run([sys.executable, str(DESKTOP_ROOT / "scripts" / "build-sidecar.py")], cwd=PROJECT_ROOT, check=True)

    command = [*tauri_command(), "build"]
    if args.release_updater:
        subprocess.run(
            [sys.executable, str(DESKTOP_ROOT / "scripts" / "prepare-release-config.py")],
            cwd=PROJECT_ROOT,
            check=True,
        )
        command.extend(["--config", "build/tauri.release.conf.json"])
    if args.debug:
        command.append("--debug")
    if args.bundles:
        command.extend(["--bundles", args.bundles])
    subprocess.run(command, cwd=DESKTOP_ROOT, check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
