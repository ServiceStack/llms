#!/usr/bin/env python3
"""Upload Apple signing secrets to the desktop-release GitHub environment."""

import argparse
import base64
import getpass
import shutil
import subprocess
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--certificate", type=Path, required=True, help="Exported certificate and private key (.p12)")
    parser.add_argument("--repo", default="ServiceStack/llms")
    parser.add_argument("--environment", default="desktop-release")
    args = parser.parse_args()
    if not sys.stdin.isatty():
        parser.error("Run in an interactive terminal so passwords can be entered privately")
    gh = shutil.which("gh")
    if not gh:
        parser.error("GitHub CLI (gh) is required")
    certificate = args.certificate.expanduser()
    if not certificate.is_file() or certificate.suffix.lower() not in {".p12", ".pfx"}:
        parser.error("--certificate must be an existing .p12 or .pfx export")
    encoded_certificate = base64.b64encode(certificate.read_bytes()).decode("ascii")
    if not encoded_certificate or len(encoded_certificate) > 48 * 1024:
        parser.error("Certificate export must be nonempty and fit GitHub's 48 KB secret limit after base64 encoding")
    auth = subprocess.run([gh, "auth", "status"], capture_output=True)
    if auth.returncode:
        parser.error("Authenticate first with gh auth login")

    email = input("Apple Account email: ").strip()
    certificate_password = getpass.getpass("Password for the exported .p12 (hidden): ")
    apple_password = getpass.getpass("Apple app-specific password (hidden): ")
    if not email or not apple_password.strip():
        parser.error("Apple Account email and app-specific password are required")
    secrets = {
        "APPLE_CERTIFICATE": encoded_certificate,
        "APPLE_CERTIFICATE_PASSWORD": certificate_password,
        "APPLE_ID": email,
        "APPLE_PASSWORD": apple_password,
    }
    for name, value in secrets.items():
        # Use stdin rather than --body to keep secrets out of process arguments.
        completed = subprocess.run(
            [gh, "secret", "set", name, "--repo", args.repo, "--env", args.environment],
            input=value.encode("utf-8"), capture_output=True,
        )
        if completed.returncode:
            print(f"Could not set {name}; earlier secrets may have been saved. Rerun to finish.", file=sys.stderr)
            return 1
        print(f"Saved {name}")
    print(f"Apple credentials saved to {args.repo} / {args.environment}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
