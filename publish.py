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

def bump_version():
    """
    Bump the package version.
    This function should implement version bumping logic by 
     - extracting version from pyproject.toml
     - incrementing patch version
     - Use string search/replace to replace old version with new version in:
        - llms/ui/ai.mjs
        - llms/main.py
        - setup.py
        - pyproject.toml
    """
    print("Bumping package version...")
    import re

    version_file = "pyproject.toml"
    with open(version_file, "r") as f:
        content = f.read()
        version = re.search(r"version = \"(\d+\.\d+\.\d+)\"", content).group(1)
        print(f"Current version: {version}")
        major, minor, patch = map(int, version.split("."))
        patch += 1
        new_version = f"{major}.{minor}.{patch}"
        print(f"New version: {new_version}")
        content = content.replace(version, new_version)
        with open(version_file, "w") as f:
            f.write(content)
    # Update other files
    files_to_update = [
        "llms/ui/ai.mjs",
        "llms/main.py",
        "setup.py"
    ]
    for file in files_to_update:
        with open(file, "r") as f:
            content = f.read()
            content = content.replace(version, new_version)
        with open(file, "w") as f:
            f.write(content)
    print("Version bumped successfully.")
    # Create git commit and tag
    run_command(f'git commit -am "Bump version to {new_version}"')
    run_command(f'git tag v{new_version}')
    run_command("git push --tags")
    run_command("git push")

def main():
    parser = argparse.ArgumentParser(description="Publish llms-py package to PyPI")
    parser.add_argument("--bump", action="store_true", help="Bump the package version")
    parser.add_argument("--test", action="store_true", help="Upload to TestPyPI")
    parser.add_argument("--prod", action="store_true", help="Upload to PyPI")
    parser.add_argument("--build", action="store_true", help="Just build the package")
    
    args = parser.parse_args()
    
    if not any([args.bump, args.test, args.prod, args.build]):
        parser.print_help()
        sys.exit(1)
    
    check_dependencies()
    clean_build()
    build_package()
    
    if args.bump:
        bump_version()
    elif args.test:
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
