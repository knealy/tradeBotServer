"""
Test suite for AuthManager module.

Tests authentication functionality in isolation.
"""

import asyncio
import os
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from core.auth import AuthManager, AuthenticationError


async def test_auth_manager_initialization():
    """Test AuthManager initialization."""
    print("Testing AuthManager initialization...")
    
    # Test with environment variables
    auth = AuthManager()
    assert auth.base_url == "https://api.topstepx.com", "Default base URL should be set"
    print("✅ AuthManager initialization test passed")


async def test_token_expiration_check():
    """Test token expiration checking."""
    print("Testing token expiration check...")
    
    auth = AuthManager()
    
    # Test with no token
    assert auth._is_token_expired() == True, "Should be expired if no token"
    
    # Test with token but no expiry
    auth.session_token = "test_token"
    auth.token_expiry = None
    assert auth._is_token_expired() == True, "Should be expired if no expiry time"
    
    print("✅ Token expiration check test passed")


async def test_auth_headers():
    """Test authentication headers generation."""
    print("Testing auth headers generation...")
    
    auth = AuthManager()
    
    # Test with no token
    headers = auth.get_auth_headers()
    assert headers == {}, "Should return empty dict if no token"
    
    # Test with token
    auth.session_token = "test_token_12345"
    headers = auth.get_auth_headers()
    assert "Authorization" in headers, "Should include Authorization header"
    assert headers["Authorization"] == "Bearer test_token_12345", "Should have correct Bearer token"
    
    print("✅ Auth headers test passed")


async def test_get_token():
    """Test token retrieval."""
    print("Testing token retrieval...")
    
    auth = AuthManager()
    
    # Test with no token
    token = auth.get_token()
    assert token is None, "Should return None if no token"
    
    # Test with token
    auth.session_token = "test_token"
    token = auth.get_token()
    assert token == "test_token", "Should return the token"
    
    print("✅ Get token test passed")


async def run_all_tests():
    """Run all tests."""
    print("\n" + "="*60)
    print("AUTH MANAGER TEST SUITE")
    print("="*60 + "\n")
    
    tests = [
        test_auth_manager_initialization,
        test_token_expiration_check,
        test_auth_headers,
        test_get_token,
    ]
    
    passed = 0
    failed = 0
    
    for test in tests:
        try:
            await test()
            passed += 1
        except Exception as e:
            print(f"❌ {test.__name__} failed: {e}")
            failed += 1
    
    print("\n" + "="*60)
    print("TEST RESULTS")
    print("="*60)
    print(f"Passed: {passed}")
    print(f"Failed: {failed}")
    print(f"Total: {passed + failed}")
    print("="*60 + "\n")
    
    return failed == 0


if __name__ == "__main__":
    success = asyncio.run(run_all_tests())
    sys.exit(0 if success else 1)

