# Unit Tests for HTTP Optimization

This directory contains unit tests for the HTTP optimization implementation (replacing subprocess curl with requests library).

## Test Coverage

The test suite covers:

1. **Session Creation**
   - HTTP session initialization with connection pooling
   - Proper adapter configuration

2. **Request Methods**
   - GET requests
   - POST requests with JSON data
   - PUT/PATCH requests
   - Custom headers support

3. **Error Handling**
   - Timeout errors
   - Connection errors
   - HTTP error status codes (5xx)
   - Invalid JSON responses
   - Empty responses

4. **Connection Pooling**
   - Session reuse across multiple requests
   - Proper timeout configuration from environment variables

## Running Tests

### Install Dependencies

First, install the test dependencies:

```bash
pip install -r requirements.txt
```

This will install:
- `pytest>=7.0.0`
- `pytest-asyncio>=0.21.0`
- `pytest-mock>=3.10.0`

### Run All Tests

```bash
# Using pytest directly
pytest test_http_optimization.py -v

# Using the test runner script
python run_tests.py

# With more verbose output
python run_tests.py -vv
```

### Run Specific Tests

```bash
# Run a specific test
pytest test_http_optimization.py::TestHTTPOptimization::test_get_request_success -v

# Run tests matching a pattern
pytest test_http_optimization.py -k "error" -v
```

### Run with Coverage

```bash
# Install coverage tool
pip install pytest-cov

# Run tests with coverage report
pytest test_http_optimization.py --cov=trading_bot --cov-report=html
```

## Test Structure

```
test_http_optimization.py
├── TestHTTPOptimization (test class)
    ├── test_create_http_session       # Session creation
    ├── test_session_initialized_in_bot  # Initialization check
    ├── test_get_request_success         # GET requests
    ├── test_post_request_success        # POST requests
    ├── test_empty_response              # Empty response handling
    ├── test_timeout_error               # Timeout handling
    ├── test_connection_error            # Connection error handling
    ├── test_http_error_status_code      # HTTP error handling
    ├── test_invalid_json_response       # JSON parsing errors
    ├── test_custom_headers              # Custom headers
    ├── test_timeout_from_env            # Environment variable timeout
    ├── test_put_patch_methods           # PUT/PATCH methods
    └── test_connection_pooling_reuse     # Connection pooling
```

## Mocking Strategy

Tests use `unittest.mock` to:
- Mock the `requests.Session` to avoid real HTTP calls
- Mock responses to test different scenarios
- Verify that the session is reused (connection pooling)

## Expected Output

When tests pass, you should see:

```
test_http_optimization.py::TestHTTPOptimization::test_create_http_session PASSED
test_http_optimization.py::TestHTTPOptimization::test_session_initialized_in_bot PASSED
test_http_optimization.py::TestHTTPOptimization::test_get_request_success PASSED
...
```

## Notes

- All tests are isolated and don't make real API calls
- Tests use fixtures to set up test data
- Mock responses simulate various API scenarios
- Tests verify both success and error paths

