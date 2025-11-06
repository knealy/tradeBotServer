#!/usr/bin/env python3
"""
Test runner for HTTP optimization tests.

Usage:
    python run_tests.py                    # Run all tests
    python run_tests.py -v                 # Verbose output
    python run_tests.py test_http_optimization.py  # Run specific test file
"""

import sys
import pytest

if __name__ == "__main__":
    # Run pytest with the provided arguments
    exit_code = pytest.main(sys.argv[1:] if len(sys.argv) > 1 else ["-v", "test_http_optimization.py"])
    sys.exit(exit_code)

