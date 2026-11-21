import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def load_script(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


release_config = load_script("prepare-release-config")
check_version = load_script("check-version")
build_desktop = load_script("build-desktop")
apple_secrets = load_script("configure-apple-secrets")


class TestAppleSecrets(unittest.TestCase):
    def test_password_only_update_uses_fixed_account_and_secret_stdin(self):
        output = io.StringIO()
        with (
            patch.object(sys, "argv", ["configure-apple-secrets.py"]),
            patch.object(sys.stdin, "isatty", return_value=True),
            patch.object(apple_secrets.shutil, "which", return_value="gh"),
            patch.object(apple_secrets.subprocess, "run") as run,
            patch.object(apple_secrets.getpass, "getpass", return_value="private-password") as password,
            patch("builtins.input") as prompt,
            contextlib.redirect_stdout(output),
        ):
            run.return_value.returncode = 0
            self.assertEqual(apple_secrets.main(), 0)
            prompt.assert_not_called()
            password.assert_called_once()
            writes = run.call_args_list[1:]
            self.assertEqual(len(writes), 2)
            self.assertEqual(writes[0].args[0][3], "APPLE_ID")
            self.assertEqual(writes[0].kwargs["input"], b"team@servicestack.net")
            self.assertEqual(writes[1].args[0][3], "APPLE_PASSWORD")
            self.assertEqual(writes[1].kwargs["input"], b"private-password")
            self.assertNotIn("private-password", output.getvalue())
            self.assertNotIn("private-password", str([call.args for call in writes]))


class TestLocalNotarization(unittest.TestCase):
    def test_credentials_prompt_without_echoing_password(self):
        with (
            patch.dict(os.environ, {"APPLE_SIGNING_IDENTITY": "Developer ID Application: Test (TEAMID)"}, clear=True),
            patch.object(sys, "platform", "darwin"),
            patch("builtins.input", return_value="TEAMID") as prompt,
            patch.object(build_desktop.getpass, "getpass", return_value="app-specific-password") as password,
        ):
            build_desktop.configure_notarization()
            self.assertEqual(prompt.call_count, 1)
            password.assert_called_once()
            self.assertEqual(os.environ["APPLE_ID"], "team@servicestack.net")
            self.assertEqual(os.environ["APPLE_PASSWORD"], "app-specific-password")

    def test_existing_credentials_use_fixed_account_and_prompt_for_fresh_password(self):
        with (
            patch.dict(os.environ, {
                "APPLE_SIGNING_IDENTITY": "Developer ID Application: Test (TEAMID)",
                "APPLE_ID": "test@example.com", "APPLE_TEAM_ID": "TEAMID", "APPLE_PASSWORD": "configured",
            }, clear=True),
            patch.object(sys, "platform", "darwin"),
            patch("builtins.input") as prompt,
            patch.object(build_desktop.getpass, "getpass", return_value="fresh-password") as password,
        ):
            build_desktop.configure_notarization()
            prompt.assert_not_called()
            password.assert_called_once()
            self.assertEqual(os.environ["APPLE_ID"], "team@servicestack.net")
            self.assertEqual(os.environ["APPLE_PASSWORD"], "fresh-password")

    def test_ad_hoc_signing_cannot_be_notarized(self):
        with patch.dict(os.environ, {"APPLE_SIGNING_IDENTITY": "-"}, clear=True), patch.object(sys, "platform", "darwin"):
            with self.assertRaisesRegex(RuntimeError, "Developer ID Application"):
                build_desktop.configure_notarization()


class TestReleaseConfig(unittest.TestCase):
    def export_environment(self, environment):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "github-env"
            with patch.dict(os.environ, {**environment, "GITHUB_ENV": str(path)}, clear=True):
                release_config.export_signing_environment()
            # Parse GitHub's multiline environment-file syntax like the runner does.
            lines = iter(path.read_text().splitlines())
            result = {}
            for line in lines:
                name, delimiter = line.split("<<", 1)
                values = []
                for value in lines:
                    if value == delimiter:
                        break
                    values.append(value)
                result[name] = "\n".join(values)
            return result

    def test_empty_signing_secrets_stay_unset_for_the_bundler(self):
        environment = dict.fromkeys(release_config.SIGNING_VARIABLES, "")
        self.assertEqual(self.export_environment(environment), {})
        environment["APPLE_CERTIFICATE"] = "  \n"
        self.assertEqual(self.export_environment(environment), {})

    def test_configured_signing_secrets_are_preserved_without_exporting_other_variables(self):
        environment = {
            "APPLE_CERTIFICATE": "base64-certificate",
            "APPLE_CERTIFICATE_PASSWORD": " password with spaces ",
            "TAURI_SIGNING_PRIVATE_KEY": "first line\nsecond line",
            "UNRELATED_SECRET": "not-exported",
        }
        expected = {key: value for key, value in environment.items() if key != "UNRELATED_SECRET"}
        self.assertEqual(self.export_environment(environment), expected)

    def test_passwordless_certificate_retains_its_empty_password(self):
        self.assertEqual(
            self.export_environment({"APPLE_CERTIFICATE": "base64-certificate"}),
            {"APPLE_CERTIFICATE": "base64-certificate", "APPLE_CERTIFICATE_PASSWORD": ""},
        )

    def generate(self, environment, *arguments):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "github-output"
            with (
                patch.object(release_config, "OUTPUT", root / "release.json"),
                patch.dict(os.environ, {**environment, "GITHUB_OUTPUT": str(output)}, clear=True),
                patch.object(sys, "argv", ["prepare-release-config.py", *arguments]),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(release_config.main(), 0)
                return json.loads(release_config.OUTPUT.read_text()), output.read_text()

    def test_installers_build_without_updater_keys(self):
        config, output = self.generate({}, "--allow-unsigned")
        self.assertFalse(config["bundle"]["createUpdaterArtifacts"])
        self.assertEqual(config["bundle"]["macOS"]["signingIdentity"], "-")
        self.assertEqual(config["plugins"]["updater"], {"pubkey": "", "endpoints": []})
        self.assertEqual(output, "updater_enabled=false\n")

    def test_apple_certificate_identity_is_inferred_without_forcing_ad_hoc_signing(self):
        config, _ = self.generate({"APPLE_CERTIFICATE": "certificate"}, "--allow-unsigned")
        self.assertIsNone(config["bundle"]["macOS"]["signingIdentity"])

    def test_explicit_apple_identity_is_preserved(self):
        identity = "Developer ID Application: ServiceStack (TEAMID)"
        config, _ = self.generate({"APPLE_SIGNING_IDENTITY": identity}, "--allow-unsigned")
        self.assertEqual(config["bundle"]["macOS"]["signingIdentity"], identity)

    def test_both_keys_enable_signed_updates(self):
        config, output = self.generate(
            {"TAURI_UPDATER_PUBLIC_KEY": "public-key", "TAURI_SIGNING_PRIVATE_KEY": "private-key"},
            "--allow-unsigned",
        )
        self.assertTrue(config["bundle"]["createUpdaterArtifacts"])
        self.assertEqual(config["plugins"]["updater"]["pubkey"], "public-key")
        self.assertEqual(config["plugins"]["updater"]["endpoints"], [release_config.DEFAULT_ENDPOINT])
        self.assertEqual(output, "updater_enabled=true\n")
        self.assertNotIn("private-key", json.dumps(config))

    def test_partial_signing_configuration_fails(self):
        for key in ("TAURI_UPDATER_PUBLIC_KEY", "TAURI_SIGNING_PRIVATE_KEY"):
            with self.subTest(key=key), self.assertRaisesRegex(RuntimeError, "both"):
                self.generate({key: "configured-key"}, "--allow-unsigned")

    def test_explicit_signed_build_still_requires_public_key(self):
        with self.assertRaisesRegex(RuntimeError, "TAURI_UPDATER_PUBLIC_KEY is required"):
            self.generate({})

    def test_signed_updates_require_https(self):
        with self.assertRaisesRegex(RuntimeError, "HTTPS"):
            self.generate(
                {
                    "TAURI_UPDATER_PUBLIC_KEY": "public-key",
                    "TAURI_SIGNING_PRIVATE_KEY": "private-key",
                    "TAURI_UPDATER_ENDPOINT": "http://example.com/latest.json",
                },
                "--allow-unsigned",
            )


class TestReleaseVersion(unittest.TestCase):
    def check(self, tag):
        with (
            patch.object(sys, "argv", ["check-version.py", "--tag", tag]),
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            return check_version.main()

    def test_standard_and_desktop_release_tags_match_package(self):
        import tomllib

        with (SCRIPTS.parents[1] / "pyproject.toml").open("rb") as stream:
            version = tomllib.load(stream)["project"]["version"]
        for tag in (f"v{version}", f"desktop-v{version}"):
            with self.subTest(tag=tag):
                self.assertEqual(self.check(tag), 0)

    def test_mismatched_release_tag_fails(self):
        self.assertEqual(self.check("v0.0.0"), 1)
