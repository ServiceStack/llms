"""
XDG Base Directory migration utilities.

This module provides automatic migration from the legacy ~/.llms/ directory
to XDG-compliant locations. This module can eventually be deprecated once
all users have migrated.
"""

import os
import shutil

from .xdg_paths import get_cache_path, get_data_path, home_llms_path


def migrate_to_xdg():
    """
    Migrate data from old ~/.llms/ to XDG-compliant directories.
    This function is called on startup to ensure backward compatibility.
    
    Migration paths:
    - Config: ~/.llms/ -> ~/.config/llms/
    - Data: ~/.llms/user/ -> ~/.local/share/llms/user/
    - Cache: ~/.llms/cache/ -> ~/.cache/llms/
    """
    # Don't migrate if LLMS_HOME is set (user wants custom location)
    if os.getenv("LLMS_HOME"):
        return
    
    old_home = os.path.join(os.path.expanduser("~"), ".llms")
    
    # Only migrate if old directory exists
    if not os.path.exists(old_home):
        return
    
    migrated_any = False
    
    # Migrate config files (llms.json, providers.json, extensions/)
    new_config_dir = home_llms_path("")
    config_files = ["llms.json", "providers.json"]
    
    for config_file in config_files:
        old_file = os.path.join(old_home, config_file)
        new_file = os.path.join(new_config_dir, config_file)
        
        if os.path.exists(old_file) and not os.path.exists(new_file):
            try:
                os.makedirs(new_config_dir, exist_ok=True)
                shutil.copy2(old_file, new_file)
                print(f"Migrated config: {old_file} -> {new_file}")
                migrated_any = True
            except Exception as e:
                print(f"Warning: Failed to migrate {old_file}: {e}")
    
    # Migrate extensions directory
    old_ext = os.path.join(old_home, "extensions")
    new_ext = os.path.join(new_config_dir, "extensions")
    
    if os.path.exists(old_ext) and not os.path.exists(new_ext):
        try:
            os.makedirs(new_config_dir, exist_ok=True)
            shutil.copytree(old_ext, new_ext)
            print(f"Migrated extensions: {old_ext} -> {new_ext}")
            migrated_any = True
        except Exception as e:
            print(f"Warning: Failed to migrate extensions: {e}")
    
    # Migrate user data
    old_user = os.path.join(old_home, "user")
    new_user = os.path.join(get_data_path(), "user")
    
    if os.path.exists(old_user) and not os.path.exists(new_user):
        try:
            os.makedirs(os.path.dirname(new_user), exist_ok=True)
            shutil.copytree(old_user, new_user)
            print(f"Migrated user data: {old_user} -> {new_user}")
            migrated_any = True
        except Exception as e:
            print(f"Warning: Failed to migrate user data: {e}")
    
    # Migrate cache
    old_cache = os.path.join(old_home, "cache")
    new_cache = get_cache_path()
    
    if os.path.exists(old_cache) and not os.path.exists(new_cache):
        try:
            os.makedirs(os.path.dirname(new_cache), exist_ok=True)
            shutil.copytree(old_cache, new_cache)
            print(f"Migrated cache: {old_cache} -> {new_cache}")
            migrated_any = True
        except Exception as e:
            print(f"Warning: Failed to migrate cache: {e}")
    
    if migrated_any:
        print(f"Migration complete. Old directory still exists at {old_home}")
        print(f"You can remove it manually: rm -rf {old_home}")
        
        # Create a warning file in the old directory with a very clear name
        warning_file = os.path.join(old_home, "README__THIS_DIRECTORY_IS_OBSOLETE")
        try:
            with open(warning_file, "w") as f:
                f.write("This directory is obsolete and has been migrated to XDG-compliant locations.\n\n")
                f.write("New locations:\n")
                f.write(f"  - Config:     {home_llms_path('')}\n")
                f.write(f"  - User data:  {get_data_path('')}\n")
                f.write(f"  - Cache:      {get_cache_path('')}\n\n")
                f.write("This directory can be safely deleted:\n")
                f.write(f"  rm -rf {old_home}\n\n")
                f.write("For more information, see: https://specifications.freedesktop.org/basedir-spec/\n")
        except Exception as e:
            print(f"Warning: Failed to create migration notice file: {e}")

