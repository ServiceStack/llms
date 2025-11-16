# Tests for llms-py

This directory contains unit and integration tests for the llms-py package.

## Test Structure

- **test_utils.py** - Tests for utility functions including:
  - URL validation (`is_url`)
  - Filename extraction (`get_filename`)
  - Parameter parsing (`parse_args_params`, `apply_args_to_chat`)
  - Base64 validation (`is_base_64`)
  - MIME type detection (`get_file_mime_type`)
  - Price formatting (`price_to_string`)
  - Chat summarization (`chat_summary`, `gemini_chat_summary`)

- **test_config.py** - Tests for configuration and provider management:
  - Home directory path utilities (`home_llms_path`)
  - Provider initialization (`init_llms`)
  - Configuration validation
  - Environment variable substitution

- **test_async.py** - Tests for async functions:
  - Chat processing (`process_chat`)
  - Async helper functions

- **test_integration.py** - Integration tests:
  - CLI command testing
  - Module imports and structure
  - Configuration file handling
  - Module constants (image/audio extensions)

## Running Tests

### Run all tests
```bash
python -m unittest discover -s tests -p 'test_*.py'
```

### Run all tests with verbose output
```bash
python -m unittest discover -s tests -p 'test_*.py' -v
```

### Run a specific test file
```bash
python -m unittest tests.test_utils
```

### Run a specific test class
```bash
python -m unittest tests.test_utils.TestUrlUtils
```

### Run a specific test method
```bash
python -m unittest tests.test_utils.TestUrlUtils.test_is_url_with_http
```

### Using npm script (if package.json is configured)
```bash
npm test
```

## Test Coverage

The test suite currently includes **67 tests** covering:

- ✅ URL and file path utilities
- ✅ Parameter parsing and type conversion
- ✅ Chat message processing
- ✅ Configuration management
- ✅ Provider initialization
- ✅ Async operations
- ✅ Module structure and exports
- ✅ CLI integration

## Adding New Tests

When adding new tests:

1. Create test methods that start with `test_`
2. Use descriptive test names that explain what is being tested
3. Include docstrings explaining the test purpose
4. Group related tests in test classes
5. Use `setUp()` and `tearDown()` methods for test fixtures when needed

Example:
```python
class TestNewFeature(unittest.TestCase):
    """Test new feature functionality."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.test_data = {'key': 'value'}
    
    def test_feature_works(self):
        """Test that the feature works as expected."""
        result = new_feature(self.test_data)
        self.assertEqual(result, expected_value)
```

## Dependencies

Tests use Python's built-in `unittest` framework and require:
- Python 3.7+
- aiohttp (for async tests)
- llms package installed or available in path

## Continuous Integration

These tests are designed to run in CI/CD pipelines and should all pass before merging code changes.

