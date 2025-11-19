#!/usr/bin/env python3
"""
Integration tests for llms-py package.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

# Add parent directory to path to import llms module
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


class TestCLIIntegration(unittest.TestCase):
    """Test CLI integration."""

    def test_llms_module_import(self):
        """Test that llms module can be imported."""
        try:
            import llms

            self.assertTrue(hasattr(llms, "main"))
        except ImportError:
            self.fail("Failed to import llms module")

    def test_llms_version(self):
        """Test that version is defined."""
        from llms.main import VERSION

        self.assertIsInstance(VERSION, str)
        self.assertRegex(VERSION, r"^\d+\.\d+\.\d+$")

    def test_llms_help_command(self):
        """Test that llms --help works."""
        result = subprocess.run(["python", "-m", "llms", "--help"], capture_output=True, text=True, timeout=10)
        # Should exit with 0 or show help
        self.assertIn("usage:", result.stdout.lower() + result.stderr.lower())


class TestConfigFiles(unittest.TestCase):
    """Test configuration file handling."""

    def test_create_temp_config(self):
        """Test creating a temporary config file."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            config = {"providers": {"test": {"type": "openai", "enabled": False}}}
            json.dump(config, f)
            temp_path = f.name

        try:
            # Verify file was created
            self.assertTrue(os.path.exists(temp_path))

            # Verify content
            with open(temp_path) as f:
                loaded = json.load(f)
                self.assertEqual(loaded["providers"]["test"]["type"], "openai")
        finally:
            os.unlink(temp_path)


class TestModuleStructure(unittest.TestCase):
    """Test module structure and exports."""

    def test_main_module_exists(self):
        """Test that main module exists."""
        from llms import main

        self.assertIsNotNone(main)

    def test_main_function_exists(self):
        """Test that main function exists."""
        from llms.main import main

        self.assertTrue(callable(main))

    def test_key_functions_exported(self):
        """Test that key functions are exported."""
        from llms.main import (
            get_filename,
            is_url,
            parse_args_params,
            price_to_string,
        )

        self.assertTrue(callable(is_url))
        self.assertTrue(callable(get_filename))
        self.assertTrue(callable(parse_args_params))
        self.assertTrue(callable(price_to_string))


class TestConstants(unittest.TestCase):
    """Test module constants."""

    def test_image_extensions(self):
        """Test that image extensions are defined."""
        from llms.main import image_exts

        self.assertIsInstance(image_exts, list)
        self.assertIn("png", image_exts)
        self.assertIn("jpg", image_exts)

    def test_audio_extensions(self):
        """Test that audio extensions are defined."""
        from llms.main import audio_exts

        self.assertIsInstance(audio_exts, list)
        self.assertIn("mp3", audio_exts)
        self.assertIn("wav", audio_exts)


if __name__ == "__main__":
    unittest.main()
