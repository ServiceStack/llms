#!/usr/bin/env python3
"""
Simple test script to verify the llms-py package works correctly.
"""

import subprocess
import sys
import tempfile
import os

def run_command(cmd, check=True):
    """Run a shell command and return the result."""
    print(f"Testing: {cmd}")
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if check and result.returncode != 0:
        print(f"❌ FAILED: {cmd}")
        print(f"stdout: {result.stdout}")
        print(f"stderr: {result.stderr}")
        return False
    else:
        print(f"✅ PASSED: {cmd}")
        return True

def test_llms_command():
    """Test that the llms command is available and shows help."""
    return run_command("llms --help")

def test_llms_init():
    """Test that llms --init works."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = os.path.join(tmpdir, "test_config.json")
        env = os.environ.copy()
        env["LLMS_CONFIG_PATH"] = config_path

        cmd = f"LLMS_CONFIG_PATH={config_path} llms --init"
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, env=env)

        # Check if config was created or if it already exists
        if result.returncode == 0 and os.path.exists(config_path):
            print("✅ PASSED: llms --init (created new config)")
            return True
        elif result.returncode == 1 and "already exists" in result.stdout:
            print("✅ PASSED: llms --init (config already exists)")
            return True
        else:
            print("❌ FAILED: llms --init")
            print(f"stdout: {result.stdout}")
            print(f"stderr: {result.stderr}")
            return False

def test_python_import():
    """Test that the llms module can be imported."""
    try:
        import llms
        print("✅ PASSED: import llms")
        return True
    except ImportError as e:
        print(f"❌ FAILED: import llms - {e}")
        return False

def main():
    """Run all tests."""
    print("Testing llms package installation...")
    print("=" * 50)
    
    tests = [
        test_python_import,
        test_llms_command,
        test_llms_init,
    ]
    
    passed = 0
    total = len(tests)
    
    for test in tests:
        if test():
            passed += 1
        print()
    
    print("=" * 50)
    print(f"Tests passed: {passed}/{total}")
    
    if passed == total:
        print("🎉 All tests passed! The llms package is working correctly.")
        return 0
    else:
        print("❌ Some tests failed. Please check the installation.")
        return 1

if __name__ == "__main__":
    sys.exit(main())
