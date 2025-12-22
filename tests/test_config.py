#!/usr/bin/env python3
"""
Unit tests for configuration and provider management in llms.main module.
"""

import os
import sys
import unittest

# Add parent directory to path to import llms module
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from llms.main import (
    home_llms_path,
    init_llms,
)


class TestHomeLlmsPath(unittest.TestCase):
    """Test home directory path utilities."""

    def test_home_llms_path(self):
        result = home_llms_path("llms.json")
        self.assertIsInstance(result, str)
        self.assertTrue(result.endswith("/.llms/llms.json"))

    def test_home_llms_path_providers(self):
        result = home_llms_path("providers.json")
        self.assertIsInstance(result, str)
        self.assertTrue(result.endswith("/.llms/providers.json"))


class TestProviderStatus(unittest.TestCase):
    """Test provider status functions."""

    def setUp(self):
        """Set up test configuration."""
        self.test_config = {
            "providers": {
                "openai": {"type": "openai", "enabled": True, "api_key": "test-key"},
                "anthropic": {"type": "anthropic", "enabled": False, "api_key": "test-key"},
                "google": {"type": "google", "enabled": True, "api_key": "test-key"},
            }
        }

    def test_init_llms_basic(self):
        """Test basic initialization of llms configuration."""
        config = {"providers": {"test_provider": {"type": "openai", "enabled": False}}}
        init_llms(config)
        # Should not raise an exception

    def test_init_llms_with_env_vars(self):
        """Test initialization with environment variable substitution."""
        os.environ["TEST_API_KEY"] = "secret-key-123"
        config = {"providers": {"test_provider": {"type": "openai", "enabled": False, "api_key": "$TEST_API_KEY"}}}
        init_llms(config)
        # The config should have the env var replaced
        # Note: This modifies the global g_config, so we're just testing it doesn't crash


class TestConfigValidation(unittest.TestCase):
    """Test configuration validation."""

    def test_valid_config_structure(self):
        """Test that a valid config structure is accepted."""
        config = {
            "defaults": {"headers": {}},
            "providers": {
                "openai": {
                    "type": "openai",
                    "base_url": "https://api.openai.com/v1",
                    "api_key": "test-key",
                    "models": ["gpt-4", "gpt-3.5-turbo"],
                    "enabled": False,  # Disabled so it doesn't try to connect
                }
            },
        }
        # Should not raise an exception
        init_llms(config)

    def test_config_with_disabled_provider(self):
        """Test that disabled providers are handled correctly."""
        config = {"providers": {"openai": {"type": "openai", "enabled": False, "api_key": "test-key"}}}
        init_llms(config)
        # Should not raise an exception


if __name__ == "__main__":
    unittest.main()
