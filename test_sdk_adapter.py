#!/usr/bin/env python3
"""
Minimal test to verify sdk_adapter can import and report availability.
This does not require credentials and is safe to run in CI.
"""

import os

# Ensure default is set (module also sets default)
os.environ.setdefault("USE_PROJECTX_SDK", "0")

import sdk_adapter  # noqa: E402


def test_sdk_import_and_availability():
	available = sdk_adapter.is_sdk_available()
	print(f"ProjectX SDK available: {available}")
	assert isinstance(available, bool)


if __name__ == "__main__":
	test_sdk_import_and_availability()
