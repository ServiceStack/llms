"""
XDG Base Directory path resolution utilities.

This module provides functions for XDG-compliant path resolution,
implementing the XDG Base Directory Specification.
"""

import os


def get_xdg_config_home():
    """Get XDG_CONFIG_HOME directory, defaulting to ~/.config"""
    xdg_config = os.getenv("XDG_CONFIG_HOME")
    if xdg_config:
        return xdg_config
    return os.path.join(os.path.expanduser("~"), ".config")


def get_xdg_data_home():
    """Get XDG_DATA_HOME directory, defaulting to ~/.local/share"""
    xdg_data = os.getenv("XDG_DATA_HOME")
    if xdg_data:
        return xdg_data
    return os.path.join(os.path.expanduser("~"), ".local", "share")


def get_xdg_cache_home():
    """Get XDG_CACHE_HOME directory, defaulting to ~/.cache"""
    xdg_cache = os.getenv("XDG_CACHE_HOME")
    if xdg_cache:
        return xdg_cache
    return os.path.join(os.path.expanduser("~"), ".cache")


def home_llms_path(filename):
    """
    Get path for config files using XDG Base Directory specification.
    Priority: LLMS_HOME > LLMS_CONFIG_HOME > XDG_CONFIG_HOME > ~/.config/llms
    
    For backward compatibility, LLMS_HOME overrides all XDG paths.
    """
    # LLMS_HOME is legacy and overrides everything
    if os.getenv("LLMS_HOME"):
        home_dir = os.getenv("LLMS_HOME")
    # LLMS_CONFIG_HOME for explicit config override
    elif os.getenv("LLMS_CONFIG_HOME"):
        home_dir = os.getenv("LLMS_CONFIG_HOME")
    # XDG_CONFIG_HOME for XDG-compliant config
    else:
        home_dir = os.path.join(get_xdg_config_home(), "llms")
    
    relative_path = os.path.join(home_dir, filename)
    # return resolved full absolute path
    return os.path.abspath(os.path.normpath(relative_path))


def get_data_path(relative_path=""):
    """
    Get path for user data using XDG Base Directory specification.
    Priority: LLMS_HOME > LLMS_DATA_HOME > XDG_DATA_HOME > ~/.local/share/llms
    
    For backward compatibility, LLMS_HOME overrides all XDG paths.
    """
    # LLMS_HOME is legacy and overrides everything (backward compatibility)
    if os.getenv("LLMS_HOME"):
        data_dir = os.getenv("LLMS_HOME")
    # LLMS_DATA_HOME for explicit data override
    elif os.getenv("LLMS_DATA_HOME"):
        data_dir = os.getenv("LLMS_DATA_HOME")
    # XDG_DATA_HOME for XDG-compliant data
    else:
        data_dir = os.path.join(get_xdg_data_home(), "llms")
    
    if relative_path:
        full_path = os.path.join(data_dir, relative_path)
    else:
        full_path = data_dir
    
    return os.path.abspath(os.path.normpath(full_path))


def get_cache_path(path=""):
    """
    Get path for cache using XDG Base Directory specification.
    Priority: LLMS_HOME > LLMS_CACHE_HOME > XDG_CACHE_HOME > ~/.cache/llms
    
    For backward compatibility, LLMS_HOME overrides all XDG paths.
    """
    # LLMS_HOME is legacy and overrides everything (backward compatibility)
    if os.getenv("LLMS_HOME"):
        cache_dir = os.path.join(os.getenv("LLMS_HOME"), "cache")
    # LLMS_CACHE_HOME for explicit cache override
    elif os.getenv("LLMS_CACHE_HOME"):
        cache_dir = os.getenv("LLMS_CACHE_HOME")
    # XDG_CACHE_HOME for XDG-compliant cache
    else:
        cache_dir = os.path.join(get_xdg_cache_home(), "llms")
    
    if path:
        full_path = os.path.join(cache_dir, path)
    else:
        full_path = cache_dir
    
    return os.path.abspath(os.path.normpath(full_path))
