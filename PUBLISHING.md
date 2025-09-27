# Publishing llms-py to PyPI

This document explains how to publish the `llms-py` package to PyPI.

## Prerequisites

1. **PyPI Account**: Create accounts on both [PyPI](https://pypi.org/account/register/) and [TestPyPI](https://test.pypi.org/account/register/)

2. **API Tokens**: Generate API tokens for both PyPI and TestPyPI:
   - PyPI: https://pypi.org/manage/account/token/
   - TestPyPI: https://test.pypi.org/manage/account/token/

3. **Install Dependencies**:
   ```bash
   pip install build twine
   ```

4. **Configure twine** (optional but recommended):
   Create `~/.pypirc`:
   ```ini
   [distutils]
   index-servers =
       pypi
       testpypi

   [pypi]
   username = __token__
   password = pypi-YOUR_API_TOKEN_HERE

   [testpypi]
   repository = https://test.pypi.org/legacy/
   username = __token__
   password = pypi-YOUR_TESTPYPI_TOKEN_HERE
   ```

## Publishing Steps

### 1. Test the Package Locally

```bash
# Test the package works
python test_package.py

# Test building
python publish.py --build
```

### 2. Publish to TestPyPI (Recommended First)

```bash
# Upload to TestPyPI
python publish.py --test
```

Then test installation from TestPyPI:
```bash
# Create a new virtual environment for testing
python -m venv test_env
source test_env/bin/activate  # On Windows: test_env\Scripts\activate

# Install from TestPyPI
pip install --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ llms-py

# Test the installation
llms --help
llms --init

# Clean up
deactivate
rm -rf test_env
```

### 3. Publish to PyPI

Once you've verified everything works on TestPyPI:

```bash
# Upload to PyPI
python publish.py --prod
```

### 4. Verify Installation

```bash
# Test installation from PyPI
pip install llms-py
llms --help
```

## Version Management

To publish a new version:

1. Update the version in `setup.py` and `pyproject.toml`
2. Update the version in `llms.py` (VERSION variable)
3. Update the changelog/release notes
4. Follow the publishing steps above

## Package Structure

The package includes:

- `llms.py` - Main module with CLI functionality
- `setup.py` - Legacy setup script (for compatibility)
- `pyproject.toml` - Modern Python packaging configuration
- `MANIFEST.in` - Specifies additional files to include
- `requirements.txt` - Dependencies
- `README.md` - Package documentation
- `LICENSE` - BSD-3-Clause license

## Entry Points

The package creates a console script entry point:
- Command: `llms`
- Function: `llms:main`

This allows users to run `llms-py` directly from the command line after installation.

## Files Included in Distribution

- Python module: `llms.py`
- Configuration: `llms.json`
- Documentation: `README.md`
- License: `LICENSE`
- Dependencies: `requirements.txt`

## Troubleshooting

### Common Issues

1. **Package name already exists**: The name "llms-py" should be available, but if not, you'll need to choose a different name.

2. **Authentication errors**: Make sure your API tokens are correct and have the right permissions.

3. **Build errors**: Check that all required files are present and the package structure is correct.

4. **Import errors**: Ensure the main function is properly defined and accessible.

### Useful Commands

```bash
# Check package contents
python -m tarfile -l dist/llms-py-*.tar.gz

# Validate package
python -m twine check dist/*

# Test installation in isolated environment
python -m venv test_install
source test_install/bin/activate
pip install dist/llms-py-*.whl
llms --help
deactivate
rm -rf test_install
```

## Security Notes

- Never commit API tokens to version control
- Use environment variables or secure configuration files for tokens
- Consider using GitHub Actions or other CI/CD for automated publishing
- Always test on TestPyPI before publishing to PyPI
