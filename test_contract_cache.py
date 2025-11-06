"""
Unit tests for contract list caching optimization.

Tests caching of contract lists to reduce API calls and improve performance.
"""

import pytest
import os
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime, timedelta

# Import the trading bot class
from trading_bot import TopStepXTradingBot


class TestContractCache:
    """Test suite for contract list caching."""
    
    @pytest.fixture
    def bot(self):
        """Create a bot instance for testing."""
        with patch.dict(os.environ, {
            'PROJECT_X_API_KEY': 'test_key',
            'PROJECT_X_USERNAME': 'test_user',
        }):
            bot = TopStepXTradingBot(api_key='test_key', username='test_user')
            bot.session_token = "test_token"
            return bot
    
    @pytest.fixture
    def mock_contracts(self):
        """Sample contract data for testing."""
        return [
            {"symbol": "MNQ", "contractId": "CON.F.US.MNQ.Z25", "name": "Micro E-mini NASDAQ-100"},
            {"symbol": "ES", "contractId": "CON.F.US.ES.Z25", "name": "E-mini S&P 500"},
            {"symbol": "NQ", "contractId": "CON.F.US.NQ.Z25", "name": "E-mini NASDAQ-100"},
            {"Symbol": "YM", "ContractId": "CON.F.US.YM.Z25", "Name": "E-mini Dow Jones"},
            {"ticker": "RTY", "id": "CON.F.US.RTY.Z25", "name": "E-mini Russell 2000"},
        ]
    
    def test_contract_cache_initialization(self, bot):
        """Test that contract cache is initialized."""
        assert hasattr(bot, '_contract_cache')
        assert bot._contract_cache is None  # Should start empty
        assert hasattr(bot, '_contract_cache_lock')
    
    @pytest.mark.asyncio
    async def test_get_available_contracts_caches_result(self, bot, mock_contracts):
        """Test that get_available_contracts caches the result."""
        with patch.object(bot, '_make_curl_request', return_value=mock_contracts):
            # First call - should fetch and cache
            result1 = await bot.get_available_contracts()
            
            assert len(result1) == len(mock_contracts)
            assert bot._contract_cache is not None
            assert bot._contract_cache['contracts'] == mock_contracts
            assert 'timestamp' in bot._contract_cache
            assert bot._contract_cache['ttl_minutes'] == 60  # Default TTL
            
            # Verify _make_curl_request was called
            bot._make_curl_request.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_get_available_contracts_uses_cache(self, bot, mock_contracts):
        """Test that get_available_contracts uses cached data when fresh."""
        # Pre-populate cache
        bot._contract_cache = {
            'contracts': mock_contracts,
            'timestamp': datetime.now(),
            'ttl_minutes': 60
        }
        
        with patch.object(bot, '_make_curl_request') as mock_request:
            # Call should use cache, not make API request
            result = await bot.get_available_contracts()
            
            assert len(result) == len(mock_contracts)
            assert result == mock_contracts
            mock_request.assert_not_called()  # Should not call API
    
    @pytest.mark.asyncio
    async def test_get_available_contracts_expired_cache(self, bot, mock_contracts):
        """Test that expired cache triggers a new fetch."""
        # Pre-populate cache with expired timestamp
        bot._contract_cache = {
            'contracts': mock_contracts,
            'timestamp': datetime.now() - timedelta(minutes=70),  # Expired (TTL is 60)
            'ttl_minutes': 60
        }
        
        new_contracts = [{"symbol": "ES", "contractId": "CON.F.US.ES.Z26"}]  # Different contract
        
        with patch.object(bot, '_make_curl_request', return_value=new_contracts):
            result = await bot.get_available_contracts()
            
            assert len(result) == len(new_contracts)
            assert result == new_contracts
            # Should have updated cache with new data
            assert bot._contract_cache['contracts'] == new_contracts
            bot._make_curl_request.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_get_available_contracts_custom_ttl(self, bot, mock_contracts):
        """Test that custom TTL is respected."""
        with patch.object(bot, '_make_curl_request', return_value=mock_contracts):
            result = await bot.get_available_contracts(cache_ttl_minutes=30)
            
            assert bot._contract_cache['ttl_minutes'] == 30
    
    @pytest.mark.asyncio
    async def test_get_available_contracts_cache_disabled(self, bot, mock_contracts):
        """Test that cache can be disabled."""
        with patch.object(bot, '_make_curl_request', return_value=mock_contracts):
            # First call with cache enabled
            await bot.get_available_contracts(use_cache=True)
            assert bot._contract_cache is not None
            
            # Second call with cache disabled
            with patch.object(bot, '_make_curl_request', return_value=mock_contracts) as mock_request:
                result = await bot.get_available_contracts(use_cache=False)
                # Should still call API even if cache exists
                mock_request.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_get_available_contracts_api_error_returns_stale_cache(self, bot, mock_contracts):
        """Test that API errors return stale cache if available."""
        # Pre-populate cache (even if expired)
        bot._contract_cache = {
            'contracts': mock_contracts,
            'timestamp': datetime.now() - timedelta(minutes=70),
            'ttl_minutes': 60
        }
        
        with patch.object(bot, '_make_curl_request', return_value={"error": "API error"}):
            result = await bot.get_available_contracts()
            
            # Should return stale cache on error
            assert result == mock_contracts
    
    @pytest.mark.asyncio
    async def test_get_available_contracts_exception_returns_stale_cache(self, bot, mock_contracts):
        """Test that exceptions return stale cache if available."""
        # Pre-populate cache
        bot._contract_cache = {
            'contracts': mock_contracts,
            'timestamp': datetime.now() - timedelta(minutes=70),
            'ttl_minutes': 60
        }
        
        with patch.object(bot, '_make_curl_request', side_effect=Exception("Network error")):
            result = await bot.get_available_contracts()
            
            # Should return stale cache on exception
            assert result == mock_contracts
    
    @pytest.mark.asyncio
    async def test_get_available_contracts_no_session_token(self, bot):
        """Test behavior when session token is missing."""
        bot.session_token = None
        
        result = await bot.get_available_contracts()
        
        assert result == []
    
    @pytest.mark.asyncio
    async def test_get_available_contracts_parses_different_formats(self, bot):
        """Test that different API response formats are parsed correctly."""
        # Test list format
        list_response = [{"symbol": "MNQ", "contractId": "CON.F.US.MNQ.Z25"}]
        bot._clear_contract_cache()  # Clear cache before each test
        with patch.object(bot, '_make_curl_request', return_value=list_response):
            result = await bot.get_available_contracts(use_cache=False)  # Disable cache to force fetch
            assert result == list_response
        
        # Test dict with "contracts" key
        dict_contracts = {"contracts": [{"symbol": "ES", "contractId": "CON.F.US.ES.Z25"}]}
        bot._clear_contract_cache()
        with patch.object(bot, '_make_curl_request', return_value=dict_contracts):
            result = await bot.get_available_contracts(use_cache=False)
            assert result == dict_contracts["contracts"]
        
        # Test dict with "data" key
        dict_data = {"data": [{"symbol": "NQ", "contractId": "CON.F.US.NQ.Z25"}]}
        bot._clear_contract_cache()
        with patch.object(bot, '_make_curl_request', return_value=dict_data):
            result = await bot.get_available_contracts(use_cache=False)
            assert result == dict_data["data"]
        
        # Test dict with "result" key
        dict_result = {"result": [{"symbol": "YM", "contractId": "CON.F.US.YM.Z25"}]}
        bot._clear_contract_cache()
        with patch.object(bot, '_make_curl_request', return_value=dict_result):
            result = await bot.get_available_contracts(use_cache=False)
            assert result == dict_result["result"]
    
    def test_get_contract_id_uses_cache(self, bot, mock_contracts):
        """Test that _get_contract_id uses cached contract list."""
        # Pre-populate cache
        bot._contract_cache = {
            'contracts': mock_contracts,
            'timestamp': datetime.now(),
            'ttl_minutes': 60
        }
        
        # Should find MNQ in cache
        contract_id = bot._get_contract_id("MNQ")
        assert contract_id == "CON.F.US.MNQ.Z25"
        
        # Should find ES in cache
        contract_id = bot._get_contract_id("ES")
        assert contract_id == "CON.F.US.ES.Z25"
    
    def test_get_contract_id_case_insensitive(self, bot, mock_contracts):
        """Test that _get_contract_id is case-insensitive."""
        bot._contract_cache = {
            'contracts': mock_contracts,
            'timestamp': datetime.now(),
            'ttl_minutes': 60
        }
        
        # Should work with lowercase
        contract_id = bot._get_contract_id("mnq")
        assert contract_id == "CON.F.US.MNQ.Z25"
        
        # Should work with uppercase
        contract_id = bot._get_contract_id("MNQ")
        assert contract_id == "CON.F.US.MNQ.Z25"
    
    def test_get_contract_id_different_field_names(self, bot, mock_contracts):
        """Test that _get_contract_id handles different field name variations."""
        bot._contract_cache = {
            'contracts': mock_contracts,
            'timestamp': datetime.now(),
            'ttl_minutes': 60
        }
        
        # Test with "Symbol" (capital S) and "ContractId"
        contract_id = bot._get_contract_id("YM")
        assert contract_id == "CON.F.US.YM.Z25"
        
        # Test with "ticker" and "id"
        contract_id = bot._get_contract_id("RTY")
        assert contract_id == "CON.F.US.RTY.Z25"
    
    def test_get_contract_id_falls_back_to_hardcoded(self, bot):
        """Test that _get_contract_id falls back to hardcoded mappings when cache is empty."""
        # No cache
        bot._contract_cache = None
        
        # Should use hardcoded mapping
        contract_id = bot._get_contract_id("MNQ")
        assert contract_id == "CON.F.US.MNQ.Z25"
        
        contract_id = bot._get_contract_id("ES")
        assert contract_id == "CON.F.US.ES.Z25"
    
    def test_get_contract_id_falls_back_when_not_in_cache(self, bot, mock_contracts):
        """Test that _get_contract_id falls back when symbol not found in cache."""
        bot._contract_cache = {
            'contracts': mock_contracts,  # Doesn't include "GC"
            'timestamp': datetime.now(),
            'ttl_minutes': 60
        }
        
        # GC not in cache, should use hardcoded mapping
        contract_id = bot._get_contract_id("GC")
        assert contract_id == "CON.F.US.GC.Z25"
    
    def test_get_contract_id_generic_format(self, bot):
        """Test that _get_contract_id uses generic format for unknown symbols."""
        bot._contract_cache = None
        
        # Unknown symbol should use generic format
        contract_id = bot._get_contract_id("UNKNOWN")
        assert contract_id == "CON.F.US.UNKNOWN.Z25"
    
    def test_clear_contract_cache(self, bot, mock_contracts):
        """Test that _clear_contract_cache clears the cache."""
        # Pre-populate cache
        bot._contract_cache = {
            'contracts': mock_contracts,
            'timestamp': datetime.now(),
            'ttl_minutes': 60
        }
        
        assert bot._contract_cache is not None
        
        # Clear cache
        bot._clear_contract_cache()
        
        assert bot._contract_cache is None
    
    @pytest.mark.asyncio
    async def test_cache_thread_safety(self, bot, mock_contracts):
        """Test that cache operations are thread-safe."""
        import asyncio
        
        async def concurrent_access():
            # Multiple concurrent calls
            tasks = []
            for _ in range(10):
                tasks.append(bot.get_available_contracts())
            return await asyncio.gather(*tasks)
        
        with patch.object(bot, '_make_curl_request', return_value=mock_contracts):
            results = await concurrent_access()
            
            # All should return the same data
            assert all(r == mock_contracts for r in results)
            # Cache should be set
            assert bot._contract_cache is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

