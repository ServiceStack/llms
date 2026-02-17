#!/usr/bin/env python3
"""
Unit tests for XDG Base Directory path resolution in llms.main module.
"""

import os
import sys
import unittest
from unittest.mock import patch

# Add parent directory to path to import llms module
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from llms.main import (
    home_llms_path,
    get_data_path,
    get_cache_path,
    get_xdg_config_home,
    get_xdg_data_home,
    get_xdg_cache_home,
)


class TestXDGHelpers(unittest.TestCase):
    """Test XDG helper functions."""

    @patch.dict(os.environ, {}, clear=True)
    def test_get_xdg_config_home_no_home(self):
        """Test XDG_CONFIG_HOME works even without HOME env var"""
        result = get_xdg_config_home()
        # Should use os.path.expanduser("~") which works without HOME
        self.assertTrue(result.endswith("/.config"))

    @patch.dict(os.environ, {"HOME": "/home/testuser"}, clear=True)
    def test_get_xdg_config_home_default(self):
        """Test XDG_CONFIG_HOME defaults to ~/.config"""
        result = get_xdg_config_home()
        self.assertEqual(result, "/home/testuser/.config")

    @patch.dict(os.environ, {"HOME": "/home/testuser", "XDG_CONFIG_HOME": "/custom/config"}, clear=True)
    def test_get_xdg_config_home_custom(self):
        """Test XDG_CONFIG_HOME respects environment variable"""
        result = get_xdg_config_home()
        self.assertEqual(result, "/custom/config")

    @patch.dict(os.environ, {"HOME": "/home/testuser"}, clear=True)
    def test_get_xdg_data_home_default(self):
        """Test XDG_DATA_HOME defaults to ~/.local/share"""
        result = get_xdg_data_home()
        self.assertEqual(result, "/home/testuser/.local/share")

    @patch.dict(os.environ, {"HOME": "/home/testuser", "XDG_DATA_HOME": "/custom/data"}, clear=True)
    def test_get_xdg_data_home_custom(self):
        """Test XDG_DATA_HOME respects environment variable"""
        result = get_xdg_data_home()
        self.assertEqual(result, "/custom/data")

    @patch.dict(os.environ, {"HOME": "/home/testuser"}, clear=True)
    def test_get_xdg_cache_home_default(self):
        """Test XDG_CACHE_HOME defaults to ~/.cache"""
        result = get_xdg_cache_home()
        self.assertEqual(result, "/home/testuser/.cache")

    @patch.dict(os.environ, {"HOME": "/home/testuser", "XDG_CACHE_HOME": "/custom/cache"}, clear=True)
    def test_get_xdg_cache_home_custom(self):
        """Test XDG_CACHE_HOME respects environment variable"""
        result = get_xdg_cache_home()
        self.assertEqual(result, "/custom/cache")


class TestHomeLlmsPathXDG(unittest.TestCase):
    """Test home_llms_path with XDG compliance."""

    @patch.dict(os.environ, {"HOME": "/home/testuser"}, clear=True)
    def test_home_llms_path_xdg_default(self):
        """Test home_llms_path uses XDG config directory by default"""
        result = home_llms_path("llms.json")
        self.assertEqual(result, "/home/testuser/.config/llms/llms.json")

    @patch.dict(os.environ, {"HOME": "/home/testuser", "XDG_CONFIG_HOME": "/custom/config"}, clear=True)
    def test_home_llms_path_xdg_custom(self):
        """Test home_llms_path respects XDG_CONFIG_HOME"""
        result = home_llms_path("llms.json")
        self.assertEqual(result, "/custom/config/llms/llms.json")

    @patch.dict(os.environ, {"HOME": "/home/testuser", "LLMS_CONFIG_HOME": "/llms/config"}, clear=True)
    def test_home_llms_path_llms_config_home(self):
        """Test home_llms_path respects LLMS_CONFIG_HOME"""
        result = home_llms_path("llms.json")
        self.assertEqual(result, "/llms/config/llms.json")

    @patch.dict(os.environ, {"HOME": "/home/testuser", "LLMS_HOME": "/legacy/llms"}, clear=True)
    def test_home_llms_path_legacy_llms_home(self):
        """Test home_llms_path respects LLMS_HOME for backward compatibility"""
        result = home_llms_path("llms.json")
        self.assertEqual(result, "/legacy/llms/llms.json")

    @patch.dict(
        os.environ,
        {
            "HOME": "/home/testuser",
            "LLMS_HOME": "/legacy/llms",
            "LLMS_CONFIG_HOME": "/llms/config",
            "XDG_CONFIG_HOME": "/custom/config",
        },
        clear=True,
    )
    def test_home_llms_path_priority(self):
        """Test LLMS_HOME takes priority over all other settings"""
        result = home_llms_path("llms.json")
        self.assertEqual(result, "/legacy/llms/llms.json")


class TestGetDataPath(unittest.TestCase):
    """Test get_data_path for user data."""

    @patch.dict(os.environ, {"HOME": "/home/testuser"}, clear=True)
    def test_get_data_path_default(self):
        """Test get_data_path uses XDG data directory by default"""
        result = get_data_path("user/default")
        self.assertEqual(result, "/home/testuser/.local/share/llms/user/default")

    @patch.dict(os.environ, {"HOME": "/home/testuser", "XDG_DATA_HOME": "/custom/data"}, clear=True)
    def test_get_data_path_xdg_custom(self):
        """Test get_data_path respects XDG_DATA_HOME"""
        result = get_data_path("user/default")
        self.assertEqual(result, "/custom/data/llms/user/default")

    @patch.dict(os.environ, {"HOME": "/home/testuser", "LLMS_DATA_HOME": "/llms/data"}, clear=True)
    def test_get_data_path_llms_data_home(self):
        """Test get_data_path respects LLMS_DATA_HOME"""
        result = get_data_path("user/default")
        self.assertEqual(result, "/llms/data/user/default")

    @patch.dict(os.environ, {"HOME": "/home/testuser", "LLMS_HOME": "/legacy/llms"}, clear=True)
    def test_get_data_path_legacy_llms_home(self):
        """Test get_data_path respects LLMS_HOME for backward compatibility"""
        result = get_data_path("user/default")
        self.assertEqual(result, "/legacy/llms/user/default")

    @patch.dict(os.environ, {"HOME": "/home/testuser"}, clear=True)
    def test_get_data_path_no_relative(self):
        """Test get_data_path returns base directory when no relative path"""
        result = get_data_path()
        self.assertEqual(result, "/home/testuser/.local/share/llms")


class TestGetCachePath(unittest.TestCase):
    """Test get_cache_path for cache files."""

    @patch.dict(os.environ, {"HOME": "/home/testuser"}, clear=True)
    def test_get_cache_path_default(self):
        """Test get_cache_path uses XDG cache directory by default"""
        result = get_cache_path("media")
        self.assertEqual(result, "/home/testuser/.cache/llms/media")

    @patch.dict(os.environ, {"HOME": "/home/testuser", "XDG_CACHE_HOME": "/custom/cache"}, clear=True)
    def test_get_cache_path_xdg_custom(self):
        """Test get_cache_path respects XDG_CACHE_HOME"""
        result = get_cache_path("media")
        self.assertEqual(result, "/custom/cache/llms/media")

    @patch.dict(os.environ, {"HOME": "/home/testuser", "LLMS_CACHE_HOME": "/llms/cache"}, clear=True)
    def test_get_cache_path_llms_cache_home(self):
        """Test get_cache_path respects LLMS_CACHE_HOME"""
        result = get_cache_path("media")
        self.assertEqual(result, "/llms/cache/media")

    @patch.dict(os.environ, {"HOME": "/home/testuser", "LLMS_HOME": "/legacy/llms"}, clear=True)
    def test_get_cache_path_legacy_llms_home(self):
        """Test get_cache_path respects LLMS_HOME for backward compatibility"""
        result = get_cache_path("media")
        self.assertEqual(result, "/legacy/llms/cache/media")

    @patch.dict(os.environ, {"HOME": "/home/testuser"}, clear=True)
    def test_get_cache_path_no_relative(self):
        """Test get_cache_path returns base directory when no relative path"""
        result = get_cache_path()
        self.assertEqual(result, "/home/testuser/.cache/llms")


if __name__ == "__main__":
    unittest.main()
