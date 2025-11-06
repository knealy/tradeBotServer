"""
Unit tests for cache expiration optimization.

Tests dynamic cache TTL based on market hours.
"""

import pytest
import os
import pickle
import time
from unittest.mock import Mock, patch, MagicMock
from pathlib import Path
from datetime import datetime, timedelta, timezone

# Import the trading bot class
from trading_bot import TopStepXTradingBot


class TestCacheExpiration:
    """Test suite for cache expiration optimization."""
    
    @pytest.fixture
    def bot(self):
        """Create a bot instance for testing."""
        with patch.dict(os.environ, {
            'PROJECT_X_API_KEY': 'test_key',
            'PROJECT_X_USERNAME': 'test_user',
            'CACHE_TTL_MARKET_HOURS': '2',
            'CACHE_TTL_OFF_HOURS': '15',
            'CACHE_TTL_DEFAULT': '5'
        }):
            bot = TopStepXTradingBot(api_key='test_key', username='test_user')
            return bot
    
    @pytest.fixture
    def cache_dir(self, tmp_path):
        """Create a temporary cache directory."""
        cache_dir = tmp_path / ".cache"
        cache_dir.mkdir()
        return cache_dir
    
    def test_is_market_hours_during_market(self, bot):
        """Test market hours detection during market hours."""
        # Market hours: 13:00-03:00 UTC (8 AM - 10 PM ET)
        market_times = [
            datetime(2024, 1, 1, 13, 0, 0),  # 1 PM UTC
            datetime(2024, 1, 1, 18, 0, 0),  # 6 PM UTC
            datetime(2024, 1, 1, 23, 0, 0),  # 11 PM UTC
            datetime(2024, 1, 2, 2, 0, 0),   # 2 AM UTC (next day)
        ]
        for dt in market_times:
            assert bot._is_market_hours(dt), f"Should be market hours at {dt}"
    
    def test_is_market_hours_off_hours(self, bot):
        """Test market hours detection during off-hours."""
        # Off hours: 03:00-13:00 UTC
        off_hour_times = [
            datetime(2024, 1, 1, 3, 0, 0),    # 3 AM UTC
            datetime(2024, 1, 1, 6, 0, 0),     # 6 AM UTC
            datetime(2024, 1, 1, 12, 0, 0),   # 12 PM UTC
        ]
        for dt in off_hour_times:
            assert not bot._is_market_hours(dt), f"Should be off-hours at {dt}"
    
    def test_is_market_hours_timezone_aware(self, bot):
        """Test market hours detection with timezone-aware datetime."""
        # Create timezone-aware datetime (EST is UTC-5)
        est_tz = timezone(timedelta(hours=-5))
        dt_est = datetime(2024, 1, 1, 8, 0, 0, tzinfo=est_tz)  # 8 AM EST = 1 PM UTC
        
        assert bot._is_market_hours(dt_est), "8 AM EST should be market hours"
    
    def test_get_cache_ttl_market_hours(self, bot):
        """Test cache TTL during market hours."""
        with patch.object(bot, '_is_market_hours', return_value=True):
            ttl = bot._get_cache_ttl_minutes()
            assert ttl == 2, "Market hours should use CACHE_TTL_MARKET_HOURS (2 minutes)"
    
    def test_get_cache_ttl_off_hours(self, bot):
        """Test cache TTL during off-hours."""
        with patch.object(bot, '_is_market_hours', return_value=False):
            ttl = bot._get_cache_ttl_minutes()
            assert ttl == 15, "Off-hours should use CACHE_TTL_OFF_HOURS (15 minutes)"
    
    def test_get_cache_ttl_custom_values(self, bot):
        """Test cache TTL with custom environment values."""
        with patch.dict(os.environ, {
            'CACHE_TTL_MARKET_HOURS': '3',
            'CACHE_TTL_OFF_HOURS': '20'
        }):
            with patch.object(bot, '_is_market_hours', return_value=True):
                ttl = bot._get_cache_ttl_minutes()
                assert ttl == 3, "Should use custom market hours TTL"
            
            with patch.object(bot, '_is_market_hours', return_value=False):
                ttl = bot._get_cache_ttl_minutes()
                assert ttl == 20, "Should use custom off-hours TTL"
    
    def test_get_cache_ttl_invalid_values(self, bot):
        """Test cache TTL with invalid environment values."""
        with patch.dict(os.environ, {
            'CACHE_TTL_MARKET_HOURS': 'invalid',
            'CACHE_TTL_DEFAULT': '5'
        }):
            with patch.object(bot, '_is_market_hours', return_value=True):
                ttl = bot._get_cache_ttl_minutes()
                assert ttl == 5, "Should fall back to default on invalid value"
    
    def test_get_cache_ttl_out_of_range(self, bot):
        """Test cache TTL with out-of-range values."""
        with patch.dict(os.environ, {
            'CACHE_TTL_MARKET_HOURS': '100',  # Out of range (1-60)
            'CACHE_TTL_DEFAULT': '5'
        }):
            with patch.object(bot, '_is_market_hours', return_value=True):
                ttl = bot._get_cache_ttl_minutes()
                assert ttl == 5, "Should fall back to default for out-of-range value"
    
    def test_load_from_cache_fresh(self, bot, cache_dir, tmp_path):
        """Test loading from cache when data is fresh."""
        # Mock cache directory
        with patch.object(bot, '_get_cache_path') as mock_path:
            cache_file = tmp_path / "test_cache.pkl"
            test_data = [{"timestamp": "2024-01-01", "close": 100.0}]
            
            # Create cache file
            with open(cache_file, 'wb') as f:
                pickle.dump(test_data, f)
            
            # Set file modification time to 1 minute ago (fresh)
            os.utime(cache_file, (time.time() - 60, time.time() - 60))
            
            mock_path.return_value = cache_file
            
            # Test with 5 minute TTL
            result = bot._load_from_cache("test_key", max_age_minutes=5)
            
            assert result == test_data, "Should load fresh cache data"
    
    def test_load_from_cache_expired(self, bot, cache_dir, tmp_path):
        """Test loading from cache when data is expired."""
        with patch.object(bot, '_get_cache_path') as mock_path:
            cache_file = tmp_path / "test_cache.pkl"
            test_data = [{"timestamp": "2024-01-01", "close": 100.0}]
            
            # Create cache file
            with open(cache_file, 'wb') as f:
                pickle.dump(test_data, f)
            
            # Set file modification time to 10 minutes ago (expired)
            os.utime(cache_file, (time.time() - 600, time.time() - 600))
            
            mock_path.return_value = cache_file
            
            # Test with 5 minute TTL
            result = bot._load_from_cache("test_key", max_age_minutes=5)
            
            assert result is None, "Should return None for expired cache"
    
    def test_load_from_cache_dynamic_ttl_market_hours(self, bot, cache_dir, tmp_path):
        """Test dynamic TTL during market hours."""
        with patch.object(bot, '_get_cache_path') as mock_path:
            with patch.object(bot, '_is_market_hours', return_value=True):
                with patch.object(bot, '_get_cache_ttl_minutes', return_value=2):
                    cache_file = tmp_path / "test_cache.pkl"
                    test_data = [{"timestamp": "2024-01-01", "close": 100.0}]
                    
                    # Create cache file
                    with open(cache_file, 'wb') as f:
                        pickle.dump(test_data, f)
                    
                    # Set file modification time to 1 minute ago (fresh for 2 min TTL)
                    os.utime(cache_file, (time.time() - 60, time.time() - 60))
                    
                    mock_path.return_value = cache_file
                    
                    # Test with None (dynamic TTL)
                    result = bot._load_from_cache("test_key", max_age_minutes=None)
                    
                    assert result == test_data, "Should load cache with dynamic TTL"
    
    def test_load_from_cache_dynamic_ttl_expired(self, bot, cache_dir, tmp_path):
        """Test dynamic TTL when cache is expired."""
        with patch.object(bot, '_get_cache_path') as mock_path:
            with patch.object(bot, '_is_market_hours', return_value=True):
                with patch.object(bot, '_get_cache_ttl_minutes', return_value=2):
                    cache_file = tmp_path / "test_cache.pkl"
                    test_data = [{"timestamp": "2024-01-01", "close": 100.0}]
                    
                    # Create cache file
                    with open(cache_file, 'wb') as f:
                        pickle.dump(test_data, f)
                    
                    # Set file modification time to 5 minutes ago (expired for 2 min TTL)
                    os.utime(cache_file, (time.time() - 300, time.time() - 300))
                    
                    mock_path.return_value = cache_file
                    
                    # Test with None (dynamic TTL)
                    result = bot._load_from_cache("test_key", max_age_minutes=None)
                    
                    assert result is None, "Should return None for expired cache"
    
    def test_load_from_cache_missing_file(self, bot):
        """Test loading from cache when file doesn't exist."""
        with patch.object(bot, '_get_cache_path') as mock_path:
            mock_path.return_value = Path("/nonexistent/cache.pkl")
            
            result = bot._load_from_cache("test_key", max_age_minutes=5)
            
            assert result is None, "Should return None for missing cache file"
    
    def test_load_from_cache_corrupted_file(self, bot, cache_dir, tmp_path):
        """Test loading from cache when file is corrupted."""
        with patch.object(bot, '_get_cache_path') as mock_path:
            cache_file = tmp_path / "test_cache.pkl"
            
            # Create corrupted cache file
            with open(cache_file, 'wb') as f:
                f.write(b"corrupted data")
            
            os.utime(cache_file, (time.time(), time.time()))
            
            mock_path.return_value = cache_file
            
            # Should handle gracefully
            result = bot._load_from_cache("test_key", max_age_minutes=5)
            
            # Should return None or handle error gracefully
            assert result is None or isinstance(result, list)
    
    def test_get_historical_data_uses_dynamic_cache(self, bot):
        """Test that get_historical_data uses dynamic cache TTL."""
        with patch.object(bot, '_load_from_cache') as mock_load:
            mock_load.return_value = [{"timestamp": "2024-01-01", "close": 100.0}] * 100
            
            # Mock SDK to avoid actual API calls
            with patch('trading_bot.sdk_adapter', None):
                import asyncio
                try:
                    asyncio.run(bot.get_historical_data("MNQ", "5m", limit=10))
                except Exception:
                    pass  # Expected to fail without full setup
            
            # Verify that _load_from_cache was called
            assert mock_load.called, "Should call _load_from_cache"
            
            # Check that max_age_minutes was passed as None (dynamic TTL)
            call_args = mock_load.call_args
            if call_args:
                # call_args can be either (args, kwargs) or just args
                if len(call_args) == 2:
                    args, kwargs = call_args
                    if 'max_age_minutes' in kwargs:
                        assert kwargs['max_age_minutes'] is None, "Should pass None for dynamic TTL"
                    elif len(args) >= 2:
                        assert args[1] is None, "Should pass None for dynamic TTL"
                elif len(call_args) >= 2:
                    assert call_args[1] is None, "Should pass None for dynamic TTL"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

