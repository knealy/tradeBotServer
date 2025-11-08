"""
Unit tests for HTTP optimization (requests library with connection pooling).

Tests the replacement of subprocess curl with requests.Session for better performance.
"""

import pytest
import json
import os
from unittest.mock import Mock, patch, MagicMock
from requests.exceptions import Timeout, ConnectionError, HTTPError
from requests import Response

# Import the trading bot class
from trading_bot import TopStepXTradingBot


class TestHTTPOptimization:
    """Test suite for HTTP optimization using requests library."""
    
    @pytest.fixture
    def bot(self):
        """Create a bot instance for testing."""
        with patch.dict(os.environ, {
            'PROJECT_X_API_KEY': 'test_key',
            'PROJECT_X_USERNAME': 'test_user',
            'API_TIMEOUT': '30'
        }):
            bot = TopStepXTradingBot(api_key='test_key', username='test_user')
            # Replace the real session with a mock for testing
            bot._http_session = MagicMock()
            return bot
    
    def test_create_http_session(self, bot):
        """Test that HTTP session is created with proper configuration."""
        session = bot._create_http_session()
        
        assert session is not None
        assert hasattr(session, 'mount')
        assert hasattr(session, 'request')
        
        # Verify adapters are mounted
        assert 'http://' in session.adapters
        assert 'https://' in session.adapters
    
    def test_session_initialized_in_bot(self, bot):
        """Test that session is initialized when bot is created."""
        assert hasattr(bot, '_http_session')
        assert bot._http_session is not None
    
    def test_get_request_success(self, bot):
        """Test successful GET request."""
        mock_response = Mock(spec=Response)
        mock_response.text = '{"data": "test"}'
        mock_response.json.return_value = {"data": "test"}
        mock_response.status_code = 200
        mock_response.raise_for_status = Mock()
        bot._http_session.request.return_value = mock_response
        
        result = bot._make_curl_request("GET", "/api/test")
        
        assert result == {"data": "test"}
        bot._http_session.request.assert_called_once()
        call_kwargs = bot._http_session.request.call_args[1]
        assert call_kwargs['method'] == "GET"
        assert call_kwargs['url'] == "https://api.topstepx.com/api/test"
    
    def test_post_request_success(self, bot):
        """Test successful POST request with JSON data."""
        mock_response = Mock(spec=Response)
        mock_response.text = '{"success": true, "orderId": 123}'
        mock_response.json.return_value = {"success": True, "orderId": 123}
        mock_response.status_code = 200
        mock_response.raise_for_status = Mock()
        bot._http_session.request.return_value = mock_response
        
        data = {"symbol": "MNQ", "quantity": 1}
        result = bot._make_curl_request("POST", "/api/Order/place", data=data)
        
        assert result == {"success": True, "orderId": 123}
        bot._http_session.request.assert_called_once()
        call_kwargs = bot._http_session.request.call_args[1]
        assert call_kwargs['method'] == "POST"
        assert call_kwargs['json'] == data
        assert 'Content-Type' in call_kwargs['headers']
        assert call_kwargs['headers']['Content-Type'] == 'application/json'
    
    def test_empty_response(self, bot):
        """Test handling of empty response."""
        mock_response = Mock(spec=Response)
        mock_response.text = ''
        mock_response.status_code = 200
        mock_response.raise_for_status = Mock()
        bot._http_session.request.return_value = mock_response
        
        result = bot._make_curl_request("POST", "/api/test", data={})
        
        assert result == {"success": True, "message": "Operation completed successfully"}
    
    def test_timeout_error(self, bot):
        """Test handling of timeout errors."""
        bot._http_session.request.side_effect = Timeout("Request timed out")
        
        result = bot._make_curl_request("GET", "/api/test")
        
        assert "error" in result
        assert "timed out" in result["error"].lower()
    
    def test_connection_error(self, bot):
        """Test handling of connection errors."""
        bot._http_session.request.side_effect = ConnectionError("Connection failed")
        
        result = bot._make_curl_request("GET", "/api/test")
        
        assert "error" in result
        assert "connection" in result["error"].lower()
    
    def test_http_error_status_code(self, bot):
        """Test handling of HTTP error status codes."""
        mock_response = Mock(spec=Response)
        mock_response.status_code = 500
        mock_response.text = '{"error": "Internal server error"}'
        mock_response.raise_for_status.side_effect = HTTPError("500 Internal Server Error")
        bot._http_session.request.return_value = mock_response
        
        result = bot._make_curl_request("GET", "/api/test")
        
        assert "error" in result
        assert "500" in result["error"]
    
    def test_invalid_json_response(self, bot):
        """Test handling of invalid JSON responses."""
        mock_response = Mock(spec=Response)
        mock_response.text = 'not valid json'
        mock_response.status_code = 200
        mock_response.raise_for_status = Mock()
        mock_response.json.side_effect = json.JSONDecodeError("Expecting value", "", 0)
        bot._http_session.request.return_value = mock_response
        
        result = bot._make_curl_request("GET", "/api/test")
        
        assert "error" in result
        assert "json" in result["error"].lower()
    
    def test_custom_headers(self, bot):
        """Test that custom headers are passed correctly."""
        mock_response = Mock(spec=Response)
        mock_response.text = '{"success": true}'
        mock_response.json.return_value = {"success": True}
        mock_response.status_code = 200
        mock_response.raise_for_status = Mock()
        bot._http_session.request.return_value = mock_response
        
        headers = {"Authorization": "Bearer token123", "Custom-Header": "value"}
        result = bot._make_curl_request("GET", "/api/test", headers=headers)
        
        assert result == {"success": True}
        call_kwargs = bot._http_session.request.call_args[1]
        assert call_kwargs['headers']['Authorization'] == "Bearer token123"
        assert call_kwargs['headers']['Custom-Header'] == "value"
    
    def test_timeout_from_env(self, bot):
        """Test that timeout is read from environment variable."""
        mock_response = Mock(spec=Response)
        mock_response.text = '{"success": true}'
        mock_response.json.return_value = {"success": True}
        mock_response.status_code = 200
        mock_response.raise_for_status = Mock()
        bot._http_session.request.return_value = mock_response
        
        with patch.dict(os.environ, {'API_TIMEOUT': '60'}):
            bot._make_curl_request("GET", "/api/test")
        
        call_kwargs = bot._http_session.request.call_args[1]
        assert call_kwargs['timeout'] == 60
    
    def test_put_patch_methods(self, bot):
        """Test PUT and PATCH methods also send JSON data."""
        mock_response = Mock(spec=Response)
        mock_response.text = '{"success": true}'
        mock_response.json.return_value = {"success": True}
        mock_response.status_code = 200
        mock_response.raise_for_status = Mock()
        bot._http_session.request.return_value = mock_response
        
        data = {"field": "value"}
        
        # Test PUT
        bot._make_curl_request("PUT", "/api/test", data=data)
        call_kwargs = bot._http_session.request.call_args[1]
        assert call_kwargs['method'] == "PUT"
        assert call_kwargs['json'] == data
        
        # Reset mock for next call
        bot._http_session.request.reset_mock()
        bot._http_session.request.return_value = mock_response
        
        # Test PATCH
        bot._make_curl_request("PATCH", "/api/test", data=data)
        call_kwargs = bot._http_session.request.call_args[1]
        assert call_kwargs['method'] == "PATCH"
        assert call_kwargs['json'] == data
    
    def test_connection_pooling_reuse(self, bot):
        """Test that same session is reused for multiple requests."""
        mock_response = Mock(spec=Response)
        mock_response.text = '{"success": true}'
        mock_response.json.return_value = {"success": True}
        mock_response.status_code = 200
        mock_response.raise_for_status = Mock()
        bot._http_session.request.return_value = mock_response
        
        # Make multiple requests
        bot._make_curl_request("GET", "/api/test1")
        bot._make_curl_request("GET", "/api/test2")
        bot._make_curl_request("POST", "/api/test3", data={"test": "data"})
        
        # Verify same session was used for all requests
        assert bot._http_session.request.call_count == 3


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

