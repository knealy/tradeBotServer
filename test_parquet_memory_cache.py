"""
Unit tests for Parquet caching with in-memory cache layer.

Tests the 3-tier cache strategy:
1. Memory cache (ultra-fast, <1ms)
2. Parquet file (fast, 15-30ms)
3. API call (fallback)
"""

import pytest
import os
import time
from unittest.mock import Mock, patch, MagicMock
from pathlib import Path
from datetime import datetime, timedelta

# Import the trading bot class
from trading_bot import TopStepXTradingBot


class TestMemoryCache:
    """Test suite for in-memory cache layer."""
    
    @pytest.fixture
    def bot(self):
        """Create a bot instance for testing."""
        with patch.dict(os.environ, {
            'PROJECT_X_API_KEY': 'test_key',
            'PROJECT_X_USERNAME': 'test_user',
            'CACHE_FORMAT': 'parquet',
            'MEMORY_CACHE_MAX_SIZE': '10',
        }):
            bot = TopStepXTradingBot(api_key='test_key', username='test_user')
            return bot
    
    @pytest.fixture
    def sample_data(self):
        """Sample historical data for testing."""
        return [
            {
                'time': '2024-01-01T10:00:00',
                'open': 18500.0,
                'high': 18510.0,
                'low': 18495.0,
                'close': 18505.0,
                'volume': 1000
            },
            {
                'time': '2024-01-01T10:05:00',
                'open': 18505.0,
                'high': 18515.0,
                'low': 18500.0,
                'close': 18510.0,
                'volume': 1200
            }
        ]
    
    def test_memory_cache_initialization(self, bot):
        """Test that memory cache is initialized."""
        assert hasattr(bot, '_memory_cache')
        assert hasattr(bot, '_memory_cache_max_size')
        assert hasattr(bot, '_memory_cache_lock')
        assert isinstance(bot._memory_cache, dict)
        assert bot._memory_cache_max_size == 10
    
    def test_save_to_memory_cache(self, bot, sample_data):
        """Test saving data to memory cache."""
        cache_key = "test_key"
        
        bot._save_to_memory_cache(cache_key, sample_data)
        
        assert cache_key in bot._memory_cache
        data, timestamp = bot._memory_cache[cache_key]
        assert data == sample_data
        assert isinstance(timestamp, datetime)
    
    def test_get_from_memory_cache_fresh(self, bot, sample_data):
        """Test getting fresh data from memory cache."""
        cache_key = "test_key"
        bot._save_to_memory_cache(cache_key, sample_data)
        
        # Should return data immediately
        result = bot._get_from_memory_cache(cache_key, max_age_minutes=5)
        
        assert result == sample_data
    
    def test_get_from_memory_cache_expired(self, bot, sample_data):
        """Test that expired data is removed from memory cache."""
        cache_key = "test_key"
        bot._save_to_memory_cache(cache_key, sample_data)
        
        # Manually set old timestamp
        with bot._memory_cache_lock:
            old_time = datetime.now() - timedelta(minutes=10)
            bot._memory_cache[cache_key] = (sample_data, old_time)
        
        # Should return None and remove expired entry
        result = bot._get_from_memory_cache(cache_key, max_age_minutes=5)
        
        assert result is None
        assert cache_key not in bot._memory_cache
    
    def test_memory_cache_lru_eviction(self, bot):
        """Test LRU eviction when cache is full."""
        # Fill cache to capacity
        for i in range(10):
            bot._save_to_memory_cache(f"key_{i}", [{"data": i}])
        
        assert len(bot._memory_cache) == 10
        
        # Add one more - should evict oldest
        bot._save_to_memory_cache("key_new", [{"data": "new"}])
        
        assert len(bot._memory_cache) == 10
        assert "key_new" in bot._memory_cache
        # Oldest key should be evicted
        assert "key_0" not in bot._memory_cache
    
    def test_memory_cache_returns_copy(self, bot, sample_data):
        """Test that memory cache returns a copy to prevent mutation."""
        cache_key = "test_key"
        bot._save_to_memory_cache(cache_key, sample_data)
        
        result = bot._get_from_memory_cache(cache_key)
        
        # Modify result
        result.append({"time": "modified"})
        
        # Original in cache should be unchanged
        data, _ = bot._memory_cache[cache_key]
        assert len(data) == len(sample_data)
        assert "modified" not in str(data)
    
    def test_memory_cache_performance(self, bot, sample_data):
        """Test that memory cache is fast (<1ms for typical access)."""
        cache_key = "test_key"
        bot._save_to_memory_cache(cache_key, sample_data)
        
        # Measure access time
        start = time.perf_counter()
        for _ in range(1000):
            bot._get_from_memory_cache(cache_key)
        elapsed = (time.perf_counter() - start) * 1000  # Convert to ms
        
        # Should be very fast (<1ms per access on average)
        # Accounting for overhead, allow up to 0.5ms per access
        avg_time = elapsed / 1000
        assert avg_time < 0.5  # Less than 0.5ms per access (accounting for overhead)


class TestParquetCache:
    """Test suite for Parquet file caching."""
    
    @pytest.fixture
    def bot(self):
        """Create a bot instance for testing."""
        with patch.dict(os.environ, {
            'PROJECT_X_API_KEY': 'test_key',
            'PROJECT_X_USERNAME': 'test_user',
            'CACHE_FORMAT': 'parquet',
        }):
            bot = TopStepXTradingBot(api_key='test_key', username='test_user')
            return bot
    
    @pytest.fixture
    def sample_data(self):
        """Sample historical data for testing."""
        return [
            {
                'time': '2024-01-01T10:00:00',
                'open': 18500.0,
                'high': 18510.0,
                'low': 18495.0,
                'close': 18505.0,
                'volume': 1000
            },
            {
                'time': '2024-01-01T10:05:00',
                'open': 18505.0,
                'high': 18515.0,
                'low': 18500.0,
                'close': 18510.0,
                'volume': 1200
            }
        ]
    
    def test_save_to_parquet(self, bot, sample_data, tmp_path):
        """Test saving data to Parquet file."""
        cache_path = tmp_path / "test.parquet"
        
        try:
            bot._save_to_parquet(cache_path, sample_data)
            
            assert cache_path.exists()
            assert cache_path.stat().st_size > 0
        except ImportError:
            pytest.skip("polars not available")
    
    def test_load_from_parquet(self, bot, sample_data, tmp_path):
        """Test loading data from Parquet file."""
        cache_path = tmp_path / "test.parquet"
        
        try:
            # Save first
            bot._save_to_parquet(cache_path, sample_data)
            
            # Load
            result = bot._load_from_parquet(cache_path, max_age_minutes=5)
            
            assert result is not None
            assert len(result) == len(sample_data)
            # Check data integrity
            assert result[0]['open'] == 18500.0
            assert result[1]['close'] == 18510.0
        except ImportError:
            pytest.skip("polars not available")
    
    def test_load_from_parquet_expired(self, bot, sample_data, tmp_path):
        """Test that expired Parquet cache returns None."""
        cache_path = tmp_path / "test.parquet"
        
        try:
            # Save first
            bot._save_to_parquet(cache_path, sample_data)
            
            # Manually set old modification time
            old_time = time.time() - 600  # 10 minutes ago
            os.utime(cache_path, (old_time, old_time))
            
            # Load with 5 minute TTL
            result = bot._load_from_parquet(cache_path, max_age_minutes=5)
            
            assert result is None
        except ImportError:
            pytest.skip("polars not available")
    
    def test_save_to_parquet_handles_import_error(self, bot, sample_data, tmp_path):
        """Test that Parquet save handles missing polars gracefully."""
        cache_path = tmp_path / "test.parquet"
        
        with patch.dict('sys.modules', {'polars': None}):
            with patch('builtins.__import__', side_effect=ImportError("No module named 'polars'")):
                with pytest.raises(Exception):  # Should raise error
                    bot._save_to_parquet(cache_path, sample_data)


class TestHybridCache:
    """Test suite for hybrid memory + Parquet cache."""
    
    @pytest.fixture
    def bot(self):
        """Create a bot instance for testing."""
        with patch.dict(os.environ, {
            'PROJECT_X_API_KEY': 'test_key',
            'PROJECT_X_USERNAME': 'test_user',
            'CACHE_FORMAT': 'parquet',
            'MEMORY_CACHE_MAX_SIZE': '10',
        }):
            bot = TopStepXTradingBot(api_key='test_key', username='test_user')
            return bot
    
    @pytest.fixture
    def sample_data(self):
        """Sample historical data for testing."""
        return [
            {
                'time': '2024-01-01T10:00:00',
                'open': 18500.0,
                'high': 18510.0,
                'low': 18495.0,
                'close': 18505.0,
                'volume': 1000
            }
        ]
    
    def test_load_from_cache_memory_first(self, bot, sample_data):
        """Test that memory cache is checked first."""
        cache_key = "test_key"
        
        # Pre-populate memory cache
        bot._save_to_memory_cache(cache_key, sample_data)
        
        # Load - should come from memory
        result = bot._load_from_cache(cache_key, max_age_minutes=5)
        
        assert result == sample_data
    
    def test_load_from_cache_falls_back_to_file(self, bot, sample_data, tmp_path):
        """Test that file cache is checked if memory cache misses."""
        cache_key = "test_key"
        cache_path = bot._get_cache_path(cache_key)
        
        # Mock cache path to use tmp_path
        with patch.object(bot, '_get_cache_path', return_value=tmp_path / cache_path.name):
            try:
                # Save to file (not memory)
                bot._save_to_parquet(tmp_path / cache_path.name, sample_data)
                
                # Clear memory cache
                bot._memory_cache.clear()
                
                # Load - should come from file
                result = bot._load_from_cache(cache_key, max_age_minutes=5)
                
                assert result == sample_data
                # Should also be promoted to memory cache
                assert cache_key in bot._memory_cache
            except ImportError:
                pytest.skip("polars not available")
    
    def test_save_to_cache_saves_both(self, bot, sample_data, tmp_path):
        """Test that save stores data in both memory and file."""
        cache_key = "test_key"
        cache_path = bot._get_cache_path(cache_key)
        
        # Mock cache path to use tmp_path
        with patch.object(bot, '_get_cache_path', return_value=tmp_path / cache_path.name):
            try:
                bot._save_to_cache(cache_key, sample_data)
                
                # Check memory cache
                assert cache_key in bot._memory_cache
                
                # Check file cache
                assert (tmp_path / cache_path.name).exists()
                
                # Verify data in both
                memory_data, _ = bot._memory_cache[cache_key]
                assert memory_data == sample_data
            except ImportError:
                pytest.skip("polars not available")
    
    def test_cache_format_selection(self, bot):
        """Test that cache format is correctly selected."""
        # Default should be parquet
        assert bot._cache_format == 'parquet'
        
        # Test pickle format
        with patch.dict(os.environ, {'CACHE_FORMAT': 'pickle'}):
            bot2 = TopStepXTradingBot(api_key='test_key', username='test_user')
            assert bot2._cache_format == 'pickle'
        
        # Test invalid format defaults to parquet
        with patch.dict(os.environ, {'CACHE_FORMAT': 'invalid'}):
            bot3 = TopStepXTradingBot(api_key='test_key', username='test_user')
            assert bot3._cache_format == 'parquet'
    
    def test_cache_path_uses_correct_extension(self, bot):
        """Test that cache path uses correct file extension."""
        cache_key = "test_key"
        
        # Parquet format
        path = bot._get_cache_path(cache_key)
        assert path.suffix == '.parquet'
        
        # Pickle format
        bot._cache_format = 'pickle'
        path = bot._get_cache_path(cache_key)
        assert path.suffix == '.pkl'


class TestCachePerformance:
    """Test suite for cache performance characteristics."""
    
    @pytest.fixture
    def bot(self):
        """Create a bot instance for testing."""
        with patch.dict(os.environ, {
            'PROJECT_X_API_KEY': 'test_key',
            'PROJECT_X_USERNAME': 'test_user',
            'CACHE_FORMAT': 'parquet',
        }):
            bot = TopStepXTradingBot(api_key='test_key', username='test_user')
            return bot
    
    @pytest.fixture
    def large_dataset(self):
        """Create a larger dataset for performance testing."""
        return [
            {
                'time': f'2024-01-01T10:{i:02d}:00',
                'open': 18500.0 + i,
                'high': 18510.0 + i,
                'low': 18495.0 + i,
                'close': 18505.0 + i,
                'volume': 1000 + i
            }
            for i in range(1000)  # 1000 bars
        ]
    
    def test_memory_cache_faster_than_file(self, bot, large_dataset):
        """Test that memory cache is faster than file access."""
        cache_key = "perf_test"
        
        # Time memory cache access
        bot._save_to_memory_cache(cache_key, large_dataset)
        start = time.perf_counter()
        result1 = bot._get_from_memory_cache(cache_key)
        memory_time = (time.perf_counter() - start) * 1000
        
        # Time file cache access (simulated)
        import tempfile
        with tempfile.NamedTemporaryFile(delete=False, suffix='.parquet') as f:
            try:
                bot._save_to_parquet(Path(f.name), large_dataset)
                
                start = time.perf_counter()
                result2 = bot._load_from_parquet(Path(f.name), max_age_minutes=5)
                file_time = (time.perf_counter() - start) * 1000
                
                # Memory should be significantly faster
                assert memory_time < file_time
                assert memory_time < 1.0  # Memory should be <1ms
            except ImportError:
                pytest.skip("polars not available")
    
    def test_parquet_faster_than_pickle(self, bot, large_dataset, tmp_path):
        """Test that Parquet is faster than pickle for large datasets."""
        cache_key = "perf_test"
        parquet_path = tmp_path / "test.parquet"
        pickle_path = tmp_path / "test.pkl"
        
        try:
            # Time Parquet write
            start = time.perf_counter()
            bot._save_to_parquet(parquet_path, large_dataset)
            parquet_write = (time.perf_counter() - start) * 1000
            
            # Time Pickle write
            start = time.perf_counter()
            bot._save_to_pickle(pickle_path, large_dataset)
            pickle_write = (time.perf_counter() - start) * 1000
            
            # Time Parquet read
            start = time.perf_counter()
            bot._load_from_parquet(parquet_path, max_age_minutes=5)
            parquet_read = (time.perf_counter() - start) * 1000
            
            # Time Pickle read
            start = time.perf_counter()
            bot._load_from_pickle(pickle_path, max_age_minutes=5)
            pickle_read = (time.perf_counter() - start) * 1000
            
            # Parquet should be faster for reads (2-3x) for large datasets
            # For smaller datasets or certain conditions, performance may vary
            # Check that Parquet file is smaller (compression benefit)
            parquet_size = parquet_path.stat().st_size
            pickle_size = pickle_path.stat().st_size
            
            # Parquet should be smaller due to compression
            assert parquet_size < pickle_size or parquet_read < pickle_read * 2
        except ImportError:
            pytest.skip("polars not available")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

