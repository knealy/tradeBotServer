"""
Unit tests for CSV export and rate limiting features.

Tests:
1. CSV export functionality for history command
2. Rate limiting to prevent API rate limit violations
"""

import pytest
import os
import csv
import time
from unittest.mock import Mock, patch, MagicMock
from pathlib import Path
from datetime import datetime

# Import the trading bot class and RateLimiter
from trading_bot import TopStepXTradingBot, RateLimiter


class TestCSVExport:
    """Test suite for CSV export functionality."""
    
    @pytest.fixture
    def bot(self):
        """Create a bot instance for testing."""
        with patch.dict(os.environ, {
            'PROJECT_X_API_KEY': 'test_key',
            'PROJECT_X_USERNAME': 'test_user',
        }):
            bot = TopStepXTradingBot(api_key='test_key', username='test_user')
            return bot
    
    @pytest.fixture
    def sample_data(self):
        """Sample historical data for testing."""
        return [
            {
                'time': '2024-01-01T10:00:00-05:00',
                'open': 18500.0,
                'high': 18510.0,
                'low': 18495.0,
                'close': 18505.0,
                'volume': 1000
            },
            {
                'time': '2024-01-01T10:05:00-05:00',
                'open': 18505.0,
                'high': 18515.0,
                'low': 18500.0,
                'close': 18510.0,
                'volume': 1200
            },
            {
                'timestamp': '2024-01-01T10:10:00-05:00',  # Test timestamp field alternative
                'open': 18510.0,
                'high': 18520.0,
                'low': 18505.0,
                'close': 18515.0,
                'volume': 1100
            }
        ]
    
    def test_export_to_csv_creates_file(self, bot, sample_data, tmp_path):
        """Test that CSV export creates a file."""
        with patch('trading_bot.datetime') as mock_datetime:
            mock_datetime.now.return_value.strftime.return_value = "20240101_120000"
            mock_datetime.now.return_value = MagicMock()
            mock_datetime.now.return_value.strftime = lambda fmt: "20240101_120000"
            
            # Mock os.chdir or use tmp_path
            original_cwd = os.getcwd()
            try:
                os.chdir(tmp_path)
                filename = bot._export_to_csv(sample_data, "MNQ", "5m")
                
                assert filename is not None
                assert filename.startswith("MNQ_5m_")
                assert filename.endswith(".csv")
                
                # Verify file exists
                csv_path = Path(filename)
                assert csv_path.exists()
            finally:
                os.chdir(original_cwd)
    
    def test_export_to_csv_contains_correct_data(self, bot, sample_data, tmp_path):
        """Test that CSV export contains correct data."""
        original_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            filename = bot._export_to_csv(sample_data, "MNQ", "5m")
            
            assert filename is not None
            
            # Read and verify CSV content
            with open(filename, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                rows = list(reader)
                
                assert len(rows) == len(sample_data)
                assert rows[0]['Open'] == '18500.0'
                assert rows[0]['High'] == '18510.0'
                assert rows[0]['Low'] == '18495.0'
                assert rows[0]['Close'] == '18505.0'
                assert rows[0]['Volume'] == '1000'
        finally:
            os.chdir(original_cwd)
    
    def test_export_to_csv_handles_missing_fields(self, bot, tmp_path):
        """Test that CSV export handles missing fields gracefully."""
        incomplete_data = [
            {'time': '2024-01-01T10:00:00', 'open': 18500.0}  # Missing other fields
        ]
        
        original_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            filename = bot._export_to_csv(incomplete_data, "MNQ", "5m")
            
            assert filename is not None
            
            # Read CSV and verify missing fields are 0
            with open(filename, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                rows = list(reader)
                
                assert rows[0]['Open'] == '18500.0'
                assert rows[0]['High'] == '0'
                assert rows[0]['Low'] == '0'
        finally:
            os.chdir(original_cwd)
    
    def test_export_to_csv_handles_empty_data(self, bot):
        """Test that CSV export returns None for empty data."""
        filename = bot._export_to_csv([], "MNQ", "5m")
        assert filename is None
    
    def test_export_to_csv_uses_timestamp_field(self, bot, tmp_path):
        """Test that CSV export uses timestamp field if time is missing."""
        data_with_timestamp = [
            {
                'timestamp': '2024-01-01T10:00:00',
                'open': 18500.0,
                'high': 18510.0,
                'low': 18495.0,
                'close': 18505.0,
                'volume': 1000
            }
        ]
        
        original_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            filename = bot._export_to_csv(data_with_timestamp, "MNQ", "5m")
            
            assert filename is not None
            
            with open(filename, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                rows = list(reader)
                
                assert rows[0]['Time'] == '2024-01-01T10:00:00'
        finally:
            os.chdir(original_cwd)
    
    def test_export_to_csv_handles_errors(self, bot):
        """Test that CSV export handles errors gracefully."""
        # Try to export to a non-writable location
        with patch('builtins.open', side_effect=PermissionError("Permission denied")):
            filename = bot._export_to_csv([{'time': '2024-01-01', 'open': 100}], "MNQ", "5m")
            assert filename is None


class TestRateLimiter:
    """Test suite for rate limiting functionality."""
    
    def test_rate_limiter_initialization(self):
        """Test rate limiter initialization."""
        limiter = RateLimiter(max_calls=10, period=60)
        assert limiter.max_calls == 10
        assert limiter.period == 60
        assert len(limiter.calls) == 0
    
    def test_rate_limiter_allows_calls_within_limit(self):
        """Test that rate limiter allows calls within the limit."""
        limiter = RateLimiter(max_calls=5, period=60)
        
        # Make 5 calls - should all succeed immediately
        start_time = time.time()
        for _ in range(5):
            limiter.acquire()
        elapsed = time.time() - start_time
        
        # Should complete quickly (no waiting)
        assert elapsed < 0.1
        assert len(limiter.calls) == 5
    
    def test_rate_limiter_blocks_when_limit_reached(self):
        """Test that rate limiter blocks when limit is reached."""
        limiter = RateLimiter(max_calls=2, period=1)  # 2 calls per second
        
        # Make 2 calls - should succeed
        limiter.acquire()
        limiter.acquire()
        
        # 3rd call should block
        start_time = time.time()
        limiter.acquire()
        elapsed = time.time() - start_time
        
        # Should have waited approximately 1 second (time for oldest call to expire)
        assert elapsed >= 0.9  # Allow some margin for timing
        assert elapsed < 2.0  # Should not wait too long
    
    def test_rate_limiter_removes_expired_calls(self):
        """Test that rate limiter removes expired calls."""
        limiter = RateLimiter(max_calls=2, period=1)
        
        # Make 2 calls
        limiter.acquire()
        limiter.acquire()
        
        # Wait for period to expire
        time.sleep(1.1)
        
        # Next call should not block (old calls expired)
        start_time = time.time()
        limiter.acquire()
        elapsed = time.time() - start_time
        
        # Should complete quickly
        assert elapsed < 0.1
    
    def test_get_remaining_calls(self):
        """Test getting remaining calls."""
        limiter = RateLimiter(max_calls=10, period=60)
        
        # Initially all calls available
        assert limiter.get_remaining_calls() == 10
        
        # Make some calls
        for _ in range(3):
            limiter.acquire()
        
        # Should have 7 remaining
        assert limiter.get_remaining_calls() == 7
        
        # Make more calls to reach limit
        for _ in range(7):
            limiter.acquire()
        
        # Should have 0 remaining
        assert limiter.get_remaining_calls() == 0
    
    def test_reset(self):
        """Test resetting the rate limiter."""
        limiter = RateLimiter(max_calls=10, period=60)
        
        # Make some calls
        for _ in range(5):
            limiter.acquire()
        
        assert len(limiter.calls) == 5
        
        # Reset
        limiter.reset()
        
        assert len(limiter.calls) == 0
        assert limiter.get_remaining_calls() == 10
    
    def test_rate_limiter_thread_safety(self):
        """Test that rate limiter is thread-safe."""
        import threading
        
        limiter = RateLimiter(max_calls=100, period=60)
        results = []
        
        def make_calls():
            for _ in range(10):
                limiter.acquire()
                results.append(1)
        
        # Create multiple threads
        threads = [threading.Thread(target=make_calls) for _ in range(5)]
        
        # Start all threads
        for thread in threads:
            thread.start()
        
        # Wait for all threads
        for thread in threads:
            thread.join()
        
        # Should have made 50 calls total
        assert len(results) == 50
        assert limiter.get_remaining_calls() == 50


class TestRateLimitingIntegration:
    """Test rate limiting integration with HTTP requests."""
    
    @pytest.fixture
    def bot(self):
        """Create a bot instance for testing."""
        with patch.dict(os.environ, {
            'PROJECT_X_API_KEY': 'test_key',
            'PROJECT_X_USERNAME': 'test_user',
            'API_RATE_LIMIT_MAX': '5',
            'API_RATE_LIMIT_PERIOD': '1',
        }):
            bot = TopStepXTradingBot(api_key='test_key', username='test_user')
            return bot
    
    def test_rate_limiter_initialized(self, bot):
        """Test that rate limiter is initialized in bot."""
        assert hasattr(bot, '_rate_limiter')
        assert bot._rate_limiter.max_calls == 5
        assert bot._rate_limiter.period == 1
    
    def test_http_request_applies_rate_limiting(self, bot):
        """Test that HTTP requests apply rate limiting."""
        # Mock the HTTP session
        mock_response = Mock()
        mock_response.text = '{"success": true}'
        mock_response.json.return_value = {"success": True}
        mock_response.status_code = 200
        mock_response.raise_for_status = Mock()
        
        # Replace the real session with a mock
        bot._http_session = MagicMock()
        bot._http_session.request.return_value = mock_response
        
        # Make multiple requests
        start_time = time.time()
        for _ in range(6):  # One more than the limit
            bot._make_curl_request("GET", "/api/test")
        elapsed = time.time() - start_time
        
        # Should have waited for rate limit (6th call)
        assert elapsed >= 0.9  # Allow margin
        assert bot._http_session.request.call_count == 6
    
    def test_skip_rate_limit_parameter(self, bot):
        """Test that skip_rate_limit parameter bypasses rate limiting."""
        mock_response = Mock()
        mock_response.text = '{"success": true}'
        mock_response.json.return_value = {"success": True}
        mock_response.status_code = 200
        mock_response.raise_for_status = Mock()
        
        # Replace the real session with a mock
        bot._http_session = MagicMock()
        bot._http_session.request.return_value = mock_response
        
        # Make requests with rate limiting skipped
        start_time = time.time()
        for _ in range(10):
            bot._make_curl_request("GET", "/api/test", skip_rate_limit=True)
        elapsed = time.time() - start_time
        
        # Should complete quickly (no rate limiting)
        assert elapsed < 0.5
        assert bot._http_session.request.call_count == 10


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

