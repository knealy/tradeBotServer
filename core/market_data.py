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
                
                # If contractId is null, try to build from symbolId or name
                if not contract_id:
                    symbol_id = contract.get('symbolId') or contract.get('SymbolId')
                    name = contract.get('name') or contract.get('Name')
                    if symbol_id:
                        # symbolId like "F.US.MGC" → use as contract ID with expiration from name
                        if name:
                            # name like "MGCJ6" → extract J6 using string operations
                            name_upper = str(name).upper()
                            # Find first digit position to extract expiration code
                            exp_start = -1
                            for i, c in enumerate(name_upper):
                                if c in 'FGHJKMNQUVXZ' and i + 1 < len(name_upper) and name_upper[i+1].isdigit():
                                    exp_start = i
                                    break
                            if exp_start >= 0:
                                exp = name_upper[exp_start:exp_start+2]  # e.g. "J6"
                                # Convert J6 → J26 (assume 20xx)
                                if len(exp) == 2 and exp[1].isdigit():
                                    exp = exp[0] + '2' + exp[1]
                                contract_id = f"CON.{symbol_id}.{exp}"
                            else:
                                contract_id = f"CON.{symbol_id}"
                        else:
                            contract_id = f"CON.{symbol_id}"
                    elif name:
                        # Use name as contract ID (e.g. "MGCJ6")
                        contract_id = name
                
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
                # Format: CON.F.US.MNQ.Z25 -> extract MNQ (fourth element, or second-to-last as fallback)
                if not contract_symbol and contract_id:
                    if '.' in str(contract_id):
                        parts = str(contract_id).split('.')
                        # Prefer parts[3] for CON.F.US.SYM.EXP format
                        if len(parts) >= 5:
                            contract_symbol = parts[3]
                        elif len(parts) >= 4:
                            contract_symbol = parts[-2]
                
                # Also try extracting from name field or symbolId
                if not contract_symbol:
                    symbol_id = contract.get('symbolId') or contract.get('SymbolId')
                    name = contract.get('name') or contract.get('Name') or contract.get('description') or contract.get('Description')
                    
                    # Try symbolId first: "F.US.MGC" → "MGC"
                    if symbol_id and '.' in str(symbol_id):
                        parts = str(symbol_id).split('.')
                        contract_symbol = parts[-1]
                    # Then try name: "MGCJ6" → "MGC", "GCJ6" → "GC"
                    elif name:
                        name_str = str(name).upper()
                        # Extract leading letters, stopping at month code (F,G,H,J,K,M,N,Q,U,V,X,Z)
                        symbol_part = ""
                        month_codes = "FGHJKMNQUVXZ"
                        for i, c in enumerate(name_str):
                            if c.isalpha():
                                # Check if this is a month code followed by a digit (expiration code)
                                if c in month_codes and i + 1 < len(name_str) and name_str[i+1].isdigit():
                                    break  # Stop before month code
                                symbol_part += c
                            else:
                                break
                        if symbol_part:
                            contract_symbol = symbol_part
                
                # Normalize symbol for comparison
                if contract_symbol:
                    contract_symbol = str(contract_symbol).upper().strip()
                
                # Check if symbol matches
                if contract_symbol == symbol or contract_symbol.startswith(symbol):
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
                                # Use parts[3] for CON.F.US.SYM.EXP format
                                if len(parts) >= 5:
                                    sym = parts[3]
                                elif len(parts) >= 4:
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

    def get_contract_ids_for_symbol(self, symbol: str, ascending: bool = True) -> List[str]:
        """
        Get all contract IDs for a symbol, sorted by expiration.

        Args:
            symbol: Root symbol (e.g., "MNQ")
            ascending: If True, oldest -> newest. If False, newest -> oldest.

        Returns:
            List of contract IDs (strings).
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

            def _parse_expiration_key(expiration_value: Any, contract_id: Optional[str]) -> tuple:
                month_map = {
                    "F": 1, "G": 2, "H": 3, "J": 4, "K": 5, "M": 6,
                    "N": 7, "Q": 8, "U": 9, "V": 10, "X": 11, "Z": 12
                }

                # If already a datetime/date, use it directly
                if isinstance(expiration_value, datetime):
                    return (expiration_value.year, expiration_value.month, expiration_value.day)

                exp_str = str(expiration_value or "").strip().upper()

                # Try ISO-ish date formats
                for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
                    try:
                        dt = datetime.strptime(exp_str, fmt)
                        return (dt.year, dt.month, dt.day)
                    except Exception:
                        pass

                # Try futures code like H26 or H2026
                m = re.match(r"^([FGHJKMNQUVXZ])(\d{2,4})$", exp_str)
                if m:
                    month = month_map.get(m.group(1), 12)
                    year = int(m.group(2))
                    if year < 100:
                        year += 2000
                    return (year, month, 1)

                # Try parsing from contractId suffix if expiration not available
                if contract_id and "." in str(contract_id):
                    suffix = str(contract_id).split(".")[-1].strip().upper()
                    m2 = re.match(r"^([FGHJKMNQUVXZ])(\d{2,4})$", suffix)
                    if m2:
                        month = month_map.get(m2.group(1), 12)
                        year = int(m2.group(2))
                        if year < 100:
                            year += 2000
                        return (year, month, 1)

                # Unknown expiration goes last
                return (9999, 12, 31)

            matching_contracts = []
            for contract in contracts:
                if not isinstance(contract, dict):
                    continue

                contract_id = (
                    contract.get('contractId') or
                    contract.get('ContractId') or
                    contract.get('id') or
                    contract.get('Id') or
                    contract.get('contract_id') or
                    contract.get('contractID')
                )
                
                # If contractId is null, try to build from symbolId or name
                if not contract_id:
                    symbol_id = contract.get('symbolId') or contract.get('SymbolId')
                    name = contract.get('name') or contract.get('Name')
                    if symbol_id:
                        # symbolId like "F.US.MGC" → use as contract ID with expiration from name
                        if name:
                            # name like "MGCJ6" → extract J6 and build CON.F.US.MGC.J26
                            exp_match = re.search(r'([FGHJKMNQUVXZ]\d+)$', str(name).upper())
                            if exp_match:
                                exp = exp_match.group(1)
                                # Convert J6 → J26 (assume 20xx)
                                if len(exp) == 2:
                                    exp = exp[0] + '2' + exp[1]
                                contract_id = f"CON.{symbol_id}.{exp}"
                            else:
                                contract_id = f"CON.{symbol_id}"
                        else:
                            contract_id = f"CON.{symbol_id}"
                    elif name:
                        # Use name as contract ID (e.g. "MGCJ6")
                        contract_id = name
                
                if not contract_id:
                    continue

                contract_symbol = (
                    contract.get('symbol') or
                    contract.get('Symbol') or
                    contract.get('ticker') or
                    contract.get('Ticker') or
                    contract.get('instrument') or
                    contract.get('Instrument')
                )

                if not contract_symbol and contract_id:
                    if '.' in str(contract_id):
                        parts = str(contract_id).split('.')
                        if len(parts) >= 5:
                            contract_symbol = parts[3]
                        elif len(parts) >= 4:
                            contract_symbol = parts[-2]
                
                # Also try extracting from symbolId or name
                if not contract_symbol:
                    symbol_id = contract.get('symbolId') or contract.get('SymbolId')
                    name = contract.get('name') or contract.get('Name')
                    if symbol_id and '.' in str(symbol_id):
                        # "F.US.MGC" → "MGC"
                        parts = str(symbol_id).split('.')
                        contract_symbol = parts[-1]
                    elif name:
                        # "MGCJ6" → "MGC", "GCJ6" → "GC" - extract leading letters, stop at month code
                        name_str = str(name).upper()
                        symbol_part = ""
                        month_codes = "FGHJKMNQUVXZ"
                        for i, c in enumerate(name_str):
                            if c.isalpha():
                                # Check if this is a month code followed by a digit (expiration code)
                                if c in month_codes and i + 1 < len(name_str) and name_str[i+1].isdigit():
                                    break  # Stop before month code
                                symbol_part += c
                            else:
                                break
                        if symbol_part:
                            contract_symbol = symbol_part

                if contract_symbol:
                    contract_symbol = str(contract_symbol).upper().strip()

                # Match if exact match or if contract_symbol starts with symbol (e.g. "GCE" starts with "GC")
                if contract_symbol == symbol or (contract_symbol and contract_symbol.startswith(symbol)):
                    expiration = contract.get('expiration') or contract.get('Expiration') or contract.get('expiry') or contract.get('Expiry')
                    volume = contract.get('volume') or contract.get('Volume') or contract.get('dailyVolume') or contract.get('openInterest') or 0
                    if not isinstance(volume, (int, float)):
                        volume = 0
                    exp_key = _parse_expiration_key(expiration, str(contract_id))
                    matching_contracts.append({
                        "contract_id": str(contract_id),
                        "expiration_key": exp_key,
                        "volume": volume
                    })

            if not matching_contracts:
                raise ValueError(f"Symbol '{symbol}' not found in contract cache.")

            # Sort by expiration, then volume as tiebreaker
            matching_contracts.sort(key=lambda c: (c["expiration_key"], -c["volume"]))
            if not ascending:
                matching_contracts.reverse()

            return [c["contract_id"] for c in matching_contracts]
    
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
        
        # Strip any trailing dots first
        contract_id = str(contract_id).rstrip('.')
        parts = contract_id.split('.')
        if len(parts) >= 4:
            candidate = parts[-2]
            if candidate:
                return candidate.upper()
        return None

