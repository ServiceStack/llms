#!/usr/bin/env python3
"""
Script to help publish the llms-py package to PyPI.

Usage:
    python publish.py --test    # Upload to TestPyPI
    python publish.py --prod    # Upload to PyPI
    python publish.py --build   # Just build the package
"""

import subprocess
import sys
import os
import argparse

def run_command(cmd, check=True):
    """Run a shell command and return the result."""
    print(f"Running: {cmd}")
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if check and result.returncode != 0:
        print(f"Error running command: {cmd}")
        print(f"stdout: {result.stdout}")
        print(f"stderr: {result.stderr}")
        sys.exit(1)
    return result

def clean_build():
    """Clean previous build artifacts."""
    print("Cleaning previous build artifacts...")
    run_command("rm -rf build/ dist/ *.egg-info/", check=False)

def build_package():
    """Build the package."""
    print("Building package...")
    run_command("python -m build")

def upload_to_testpypi():
    """Upload to TestPyPI."""
    print("Uploading to TestPyPI...")
    run_command("python -m twine upload --repository testpypi dist/* --verbose")

def upload_to_pypi():
    """Upload to PyPI."""
    print("Uploading to PyPI...")
    run_command("python -m twine upload dist/*")

def check_dependencies():
    """Check if required tools are installed."""
    try:
        import build
        import twine
    except ImportError as e:
        print(f"Missing dependency: {e}")
        print("Please install required dependencies:")
        print("pip install build twine")
        sys.exit(1)

def main():
    parser = argparse.ArgumentParser(description="Publish llms-py package to PyPI")
    parser.add_argument("--test", action="store_true", help="Upload to TestPyPI")
    parser.add_argument("--prod", action="store_true", help="Upload to PyPI")
    parser.add_argument("--build", action="store_true", help="Just build the package")
    
    args = parser.parse_args()
    
    if not any([args.test, args.prod, args.build]):
        parser.print_help()
        sys.exit(1)
    
    check_dependencies()
    clean_build()
    build_package()
    
    if args.test:
        upload_to_testpypi()
        print("\nPackage uploaded to TestPyPI!")
        print("You can test install with:")
        print("pip install --index-url https://test.pypi.org/simple/ llms-py")
    elif args.prod:
        upload_to_pypi()
        print("\nPackage uploaded to PyPI!")
        print("You can install with:")
        print("pip install llms-py")
        print("\nUpgrade with:")
        print("pip install llms-py --upgrade")
    else:
        print("\nPackage built successfully!")
        print("Files created in dist/:")
        for file in os.listdir("dist"):
            print(f"  {file}")

if __name__ == "__main__":
    main()
