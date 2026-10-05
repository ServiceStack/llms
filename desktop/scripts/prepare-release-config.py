#!/usr/bin/env python3
"""Create the Tauri release overlay, optionally enabling signed updates."""

import argparse
import json
import os
from pathlib import Path
import uuid


DESKTOP_ROOT = Path(__file__).resolve().parents[1]
OUTPUT = DESKTOP_ROOT / "build" / "tauri.release.conf.json"
DEFAULT_ENDPOINT = "https://github.com/ServiceStack/llms/releases/latest/download/latest.json"
SIGNING_VARIABLES = (
    "APPLE_CERTIFICATE", "APPLE_CERTIFICATE_PASSWORD", "APPLE_SIGNING_IDENTITY",
    "APPLE_ID", "APPLE_PASSWORD", "APPLE_TEAM_ID",
    "TAURI_SIGNING_PRIVATE_KEY", "TAURI_SIGNING_PRIVATE_KEY_PASSWORD",
)


def export_signing_environment() -> None:
    """Unset secrets must stay absent: Tauri treats empty Apple variables as configured."""
    if not (environment_path := os.environ.get("GITHUB_ENV")):
        return
    with open(environment_path, "a", encoding="utf-8") as output:
        for name in SIGNING_VARIABLES:
            value = os.environ.get(name, "")
            # An actual passwordless certificate still needs a present password
            # variable for Tauri to import it. With no certificate, omit both.
            passwordless_certificate = (
                name == "APPLE_CERTIFICATE_PASSWORD"
                and bool(os.environ.get("APPLE_CERTIFICATE", "").strip())
            )
            if not value.strip() and not passwordless_certificate:
                continue
            delimiter = f"llms_{uuid.uuid4().hex}"
            output.write(f"{name}<<{delimiter}\n{value}\n{delimiter}\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-unsigned", action="store_true", help="Build installers when no updater keys are configured")
    args = parser.parse_args()
    public_key = os.environ.get("TAURI_UPDATER_PUBLIC_KEY", "").strip()
    private_key = os.environ.get("TAURI_SIGNING_PRIVATE_KEY", "").strip()
    if args.allow_unsigned and bool(public_key) != bool(private_key):
        raise RuntimeError("Signed updates require both TAURI_UPDATER_PUBLIC_KEY and TAURI_SIGNING_PRIVATE_KEY")
    if not public_key and not args.allow_unsigned:
        raise RuntimeError("TAURI_UPDATER_PUBLIC_KEY is required for a release build")
    endpoint = os.environ.get("TAURI_UPDATER_ENDPOINT", DEFAULT_ENDPOINT).strip()
    if public_key and not endpoint.startswith("https://"):
        raise RuntimeError("TAURI_UPDATER_ENDPOINT must use HTTPS")
    identity = os.environ.get("APPLE_SIGNING_IDENTITY", "").strip()
    certificate = os.environ.get("APPLE_CERTIFICATE", "").strip()
    # Infer the identity from a configured certificate, otherwise sign the
    # complete app ad hoc. An unsigned bundle can be reported as damaged.
    macos_identity = identity or (None if certificate else "-")

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(
            {
                "bundle": {
                    "createUpdaterArtifacts": bool(public_key),
                    "macOS": {"signingIdentity": macos_identity},
                },
                "plugins": {"updater": {"pubkey": public_key, "endpoints": [endpoint] if public_key else []}},
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    if output_path := os.environ.get("GITHUB_OUTPUT"):
        with open(output_path, "a", encoding="utf-8") as output:
            output.write(f"updater_enabled={'true' if public_key else 'false'}\n")
    export_signing_environment()
    if not public_key:
        print("Updater keys are not configured; building installers without signed updates.")
    print(f"Release updater config: {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
