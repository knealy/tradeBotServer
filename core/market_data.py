"""
Market Data Manager - Handles contract management and market data operations.

This module provides contract ID resolution, caching, and symbol management
that can be shared across broker adapters.
"""

import logging
import re
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta
from threading import Lock
from collections import OrderedDict

logger = logging.getLogger(__name__)


class ContractManager:
    """
    Manages contract ID resolution and caching.
    
    Handles dynamic contract selection based on volume and expiration.
    """
    
    def __init__(self):
        """Initialize contract manager."""
        self._contract_cache: Optional[Dict] = None
        self._contract_cache_lock = Lock()
        logger.debug("ContractManager initialized")
    
    def set_contract_cache(self, contracts: List[Dict], ttl_minutes: int = 60) -> None:
        """
        Set the contract cache.
        
        Args:
            contracts: List of contract dictionaries
            ttl_minutes: Cache TTL in minutes
        """
        with self._contract_cache_lock:
            self._contract_cache = {
                'contracts': contracts.copy(),
                'timestamp': datetime.now(),
                'ttl_minutes': ttl_minutes
            }
            logger.debug(f"Cached {len(contracts)} contracts")
    
    def get_contract_cache(self) -> Optional[Dict]:
        """Get the contract cache."""
        with self._contract_cache_lock:
            return self._contract_cache.copy() if self._contract_cache else None
    
    def clear_cache(self) -> None:
        """Clear the contract cache."""
        with self._contract_cache_lock:
            self._contract_cache = None
            logger.debug("Contract cache cleared")
    
    def get_contract_id(self, symbol: str) -> str:
        """
        Get contract ID for a symbol from cache.
        
        Selects the most recent active contract with highest volume.
        
        Args:
            symbol: Trading symbol (e.g., "MNQ", "ES")
            
        Returns:
            Contract ID string
            
        Raises:
            ValueError: If contract cache is empty or symbol not found
        """
        symbol = symbol.upper()
        
        with self._contract_cache_lock:
            if self._contract_cache is None:
                error_msg = (
                    f"Contract cache is empty. "
                    f"Please fetch contracts first using 'get_available_contracts()' or run 'contracts' command."
                )
                logger.error(f"❌ {error_msg}")
                raise ValueError(error_msg)
            
            contracts = self._contract_cache['contracts']
            if not contracts:
                error_msg = (
                    f"Contract cache is empty (no contracts found). "
                    f"Please fetch contracts first using 'get_available_contracts()' or run 'contracts' command."
                )
                logger.error(f"❌ {error_msg}")
                raise ValueError(error_msg)
            
            # Look for contract matching the symbol
            matching_contracts = []
            for contract in contracts:
                if not isinstance(contract, dict):
                    continue
                
                # Try to get contract ID first
                contract_id = (
                    contract.get('contractId') or
                    contract.get('ContractId') or
                    contract.get('id') or
                    contract.get('Id') or
                    contract.get('contract_id') or
                    contract.get('contractID')
                )
                
                if not contract_id:
                    continue
                
                # Try various field names for symbol
                contract_symbol = (
                    contract.get('symbol') or
                    contract.get('Symbol') or
                    contract.get('ticker') or
                    contract.get('Ticker') or
                    contract.get('instrument') or
                    contract.get('Instrument')
                )
                
                # If symbol field not found, try to extract from contract ID
                # Format: CON.F.US.MNQ.Z25 -> extract MNQ (second to last part)
                if not contract_symbol and contract_id:
                    if '.' in str(contract_id):
                        parts = str(contract_id).split('.')
                        if len(parts) >= 4:
                            contract_symbol = parts[-2]
                
                # Also try extracting from name field
                if not contract_symbol:
                    name = contract.get('name') or contract.get('Name') or contract.get('description') or contract.get('Description')
                    if name:
                        name_str = str(name).upper()
                        for test_symbol in [symbol, symbol[:3], symbol[:2]]:
                            if test_symbol in name_str:
                                pattern = r'\b' + re.escape(test_symbol) + r'\b'
                                if re.search(pattern, name_str):
                                    contract_symbol = test_symbol
                                    break
                
                # Normalize symbol for comparison
                if contract_symbol:
                    contract_symbol = str(contract_symbol).upper().strip()
                
                # Check if symbol matches
                if contract_symbol == symbol:
                    # Extract metadata for sorting
                    expiration = contract.get('expiration') or contract.get('Expiration') or contract.get('expiry') or contract.get('Expiry')
                    volume = contract.get('volume') or contract.get('Volume') or contract.get('dailyVolume') or contract.get('openInterest') or 0
                    if not isinstance(volume, (int, float)):
                        volume = 0
                    
                    # Try to extract expiration from contract ID if not in separate field
                    if not expiration and contract_id and '.' in str(contract_id):
                        parts = str(contract_id).split('.')
                        if len(parts) >= 1:
                            expiration = parts[-1]
                    
                    matching_contracts.append({
                        'contract_id': str(contract_id),
                        'contract': contract,
                        'expiration': expiration,
                        'volume': volume,
                        'raw_contract_id': contract_id
                    })
            
            if not matching_contracts:
                # Log available symbols for debugging
                available_symbols = set()
                for contract in contracts[:20]:
                    if isinstance(contract, dict):
                        sym = (
                            contract.get('symbol') or
                            contract.get('Symbol') or
                            contract.get('ticker') or
                            contract.get('Ticker')
                        )
                        if not sym and contract.get('contractId'):
                            cid = str(contract.get('contractId'))
                            if '.' in cid:
                                parts = cid.split('.')
                                if len(parts) >= 4:
                                    sym = parts[-2]
                        if sym:
                            available_symbols.add(str(sym).upper())
                
                error_msg = (
                    f"Symbol '{symbol}' not found in contract cache. "
                    f"Available symbols (sample): {sorted(list(available_symbols))[:10]}. "
                    f"Please ensure contracts are fetched and the symbol is correct."
                )
                logger.error(f"❌ {error_msg}")
                logger.debug(f"Contract cache contains {len(contracts)} contracts")
                raise ValueError(error_msg)
            
            # Sort contracts: prefer most recent expiration and highest volume
            def sort_key(c):
                volume_score = c['volume'] if isinstance(c['volume'], (int, float)) else 0
                exp_score = str(c['expiration'] or '').upper()
                return (-volume_score, exp_score)  # Negative volume for descending order
            
            matching_contracts.sort(key=sort_key, reverse=True)
            
            # Select the best contract
            best_contract = matching_contracts[0]
            contract_id = best_contract['contract_id']
            
            logger.debug(f"Found contract ID for {symbol}: {contract_id} (from {len(matching_contracts)} matches)")
            if len(matching_contracts) > 1:
                logger.debug(f"   Other matches: {[c['contract_id'] for c in matching_contracts[1:3]]}")
            
            return str(contract_id)
    
    def extract_symbol_from_contract_id(self, contract_id: str) -> Optional[str]:
        """
        Extract symbol from contract ID.
        
        Args:
            contract_id: Contract ID (e.g., "CON.F.US.MNQ.Z25")
            
        Returns:
            Symbol (e.g., "MNQ") or None if cannot extract
        """
        if not contract_id or '.' not in str(contract_id):
            return None
        
        parts = str(contract_id).split('.')
        if len(parts) >= 4:
            return parts[-2].upper()
        return None

