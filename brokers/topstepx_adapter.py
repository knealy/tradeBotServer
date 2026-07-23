# -*- coding: utf-8 -*-
"""
TopStepX Broker Adapter - Implements broker interfaces for TopStepX API.

This adapter implements the translation layer interfaces, allowing
the trading bot to work with TopStepX while remaining broker-agnostic.
"""

import os
import asyncio
import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple
from datetime import datetime, timedelta, timezone, date
from functools import lru_cache
from zoneinfo import ZoneInfo

from core.interfaces import (
    OrderInterface,
    PositionInterface,
    MarketDataInterface,
    OrderResponse,
    ModifyOrderResponse,
    CancelResponse,
    Position,
    CloseResponse,
    Bar,
    Quote,
    Depth,
    DepthLevel,
)
from core.json_fast import dumps_bytes, dumps_str
from core.auth import AuthManager
from core.rate_limiter import RateLimiter
from core.market_data import ContractManager

# Try to import Rust module for high-performance order execution
try:
    import trading_bot_rust
    RUST_AVAILABLE = True
    logger_rust = logging.getLogger(__name__ + ".rust")
    logger_rust.info("✅ Rust order execution module loaded")
except ImportError:
    RUST_AVAILABLE = False
    trading_bot_rust = None

logger = logging.getLogger(__name__)


class TopStepXAdapter(OrderInterface, PositionInterface, MarketDataInterface):
    """
    TopStepX broker adapter implementing all broker interfaces.
    
    This adapter handles all TopStepX-specific API calls and data transformations,
    providing a clean interface for the trading bot core.
    """
    
    # Mapping for micro contracts with limited API history → use full-size contract for historical data
    # Only include contracts that have insufficient daily history (< 15 bars typically)
    # MNQ, MES, MYM already have good history and should NOT use proxy
    HISTORY_PROXY_MAP: Dict[str, str] = {
        # Intentionally empty for now.
        #
        # IMPORTANT: For MGC TradingView parity, we *must* stitch MGC contract months directly
        # (e.g. CON.F.US.MGC.G26 + CON.F.US.MGC.J26). Proxying to GC introduces vendor differences.
    }
    
    def __init__(
        self,
        auth_manager: AuthManager,
        contract_manager: Optional[ContractManager] = None,
        rate_limiter: Optional[RateLimiter] = None,
        base_url: str = "https://api.topstepx.com",
        use_rust: Optional[bool] = None  # None = auto-detect, True/False = force
    ):
        """
        Initialize TopStepX adapter.
        
        Args:
            auth_manager: AuthManager instance for authentication
            contract_manager: Optional ContractManager for contract ID resolution
            rate_limiter: Optional RateLimiter for API rate limiting
            base_url: TopStepX API base URL
            use_rust: Whether to use Rust executor (None=auto, True=force, False=disable)
        """
        self.auth = auth_manager
        self.contract_manager = contract_manager or ContractManager()
        self.rate_limiter = rate_limiter
        self.base_url = base_url

        # Historical data cache to prevent duplicate requests
        # Key: (symbol, timeframe, limit, start_time_str, end_time_str)
        # Value: (bars, timestamp)
        self._historical_cache: Dict[Tuple[str, str, int, Optional[str], Optional[str]], Tuple[List[Bar], float]] = {}
        self._cache_lock = asyncio.Lock()
        self._cache_ttl_seconds = 5  # Cache for 5 seconds to prevent duplicate requests
        # Single-flight refresh for contract list (many coroutines can miss cache together)
        self._contract_fetch_async_lock = asyncio.Lock()

        # Track pending requests to prevent duplicate concurrent requests
        self._pending_requests: Dict = {}
        
        # Initialize Rust executors if available
        self._use_rust = False
        self._rust_executor = None
        self._query_executor = None
        
        if use_rust is False:
            # Explicitly disabled
            logger.info("⚠️  Rust executor disabled by user")
        elif RUST_AVAILABLE:
            try:
                self._rust_executor = trading_bot_rust.OrderExecutor(base_url=base_url)
                self._query_executor = trading_bot_rust.QueryExecutor(base_url=base_url)
                self._use_rust = True
                logger.info("🚀 Rust hot path enabled for order execution and queries (optimized)")
            except Exception as e:
                logger.warning(f"⚠️  Failed to initialize Rust executor: {e}. Using Python fallback.")
                self._use_rust = False
        else:
            logger.info("⚠️  Rust module not available. Using Python implementation.")

        # On-disk Parquet tier for historical bars (Phase 2.10): warm repeat requests without Postgres/API.
        _p = os.getenv("HISTORICAL_PARQUET_CACHE", "1").strip().lower()
        self._parquet_cache_enabled: bool = _p not in ("0", "false", "no", "off")
        self._parquet_cache_dir: Path = Path(
            os.getenv("HISTORICAL_PARQUET_DIR", ".cache/historical_parquet")
        ).resolve()
        try:
            self._parquet_ttl_minutes: float = float(
                os.getenv("HISTORICAL_PARQUET_TTL_MINUTES", "60")
            )
        except ValueError:
            self._parquet_ttl_minutes = 60.0
        
        logger.debug("TopStepX adapter initialized")

    def _historical_parquet_path(self, cache_key: tuple) -> Path:
        digest = hashlib.sha256(dumps_bytes(cache_key, default=str)).hexdigest()
        return self._parquet_cache_dir / f"{digest}.parquet"

    def _parquet_file_expired(self, path: Path) -> bool:
        if not path.is_file():
            return True
        mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        return (datetime.now(timezone.utc) - mtime) > timedelta(
            minutes=self._parquet_ttl_minutes
        )

    def _bars_to_parquet_rows(self, bars: List[Bar]) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for b in bars:
            rows.append(
                {
                    "timestamp": b.timestamp.isoformat() if b.timestamp else "",
                    "open": float(b.open),
                    "high": float(b.high),
                    "low": float(b.low),
                    "close": float(b.close),
                    "volume": int(b.volume or 0),
                    "symbol": str(b.symbol or ""),
                    "timeframe": str(b.timeframe or ""),
                }
            )
        return rows

    def _rows_to_bars(self, rows: List[Dict[str, Any]]) -> List[Bar]:
        out: List[Bar] = []
        for r in rows:
            ts_raw = r.get("timestamp") or r.get("time")
            if not ts_raw:
                continue
            if isinstance(ts_raw, datetime):
                ts = ts_raw
            else:
                ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
            out.append(
                Bar(
                    timestamp=ts,
                    open=float(r["open"]),
                    high=float(r["high"]),
                    low=float(r["low"]),
                    close=float(r["close"]),
                    volume=int(r.get("volume") or 0),
                    symbol=str(r.get("symbol") or ""),
                    timeframe=str(r.get("timeframe") or ""),
                    raw_data=None,
                )
            )
        return out

    def _load_parquet_disk_sync(self, path: Path) -> Optional[List[Bar]]:
        try:
            import polars as pl
        except ImportError:
            return None
        if self._parquet_file_expired(path):
            return None
        try:
            df = pl.read_parquet(path)
            return self._rows_to_bars(df.to_dicts())
        except Exception as exc:
            logger.debug("Parquet historical cache read failed: %s", exc)
            return None

    def _save_parquet_disk_sync(self, path: Path, bars: List[Bar]) -> None:
        import polars as pl

        rows = self._bars_to_parquet_rows(bars)
        if not rows:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        df = pl.DataFrame(rows)
        df.write_parquet(path, compression="lz4")

    async def _parquet_disk_load(self, cache_key: tuple) -> Optional[List[Bar]]:
        if not self._parquet_cache_enabled:
            return None
        path = self._historical_parquet_path(cache_key)
        return await asyncio.to_thread(self._load_parquet_disk_sync, path)

    async def _parquet_disk_save(self, cache_key: tuple, bars: List[Bar]) -> None:
        if not self._parquet_cache_enabled or not bars:
            return
        path = self._historical_parquet_path(cache_key)
        try:
            await asyncio.to_thread(self._save_parquet_disk_sync, path, bars)
        except Exception as exc:
            logger.debug("Parquet historical cache write failed: %s", exc)
    
    async def _make_request(
        self,
        method: str,
        endpoint: str,
        data: Optional[Dict] = None,
        headers: Optional[Dict] = None,
        timeout: int = 30
    ) -> Dict[str, Any]:
        """
        Make HTTP request to TopStepX API with rate limiting.
        
        Args:
            method: HTTP method
            endpoint: API endpoint
            data: Request data
            headers: Request headers
            timeout: Request timeout
            
        Returns:
            Response dictionary
        """
        # Apply rate limiting if available
        if self.rate_limiter:
            self.rate_limiter.acquire()
        
        # Get auth headers
        auth_headers = self.auth.get_auth_headers()
        request_headers = {**(headers or {}), **auth_headers}
        
        # Use auth manager's request method
        return await self.auth._make_request(method, endpoint, data, request_headers, timeout)
    
    # ==================== Order Interface Implementation ====================
    
    async def place_market_order(
        self,
        symbol: str,
        side: str,
        quantity: int,
        account_id: Optional[str] = None,
        **kwargs
    ) -> OrderResponse:
        """
        Place a market order on TopStepX.
        
        Uses Rust executor for hot path (20-30x faster) with Python fallback.
        
        Args:
            symbol: Trading symbol (e.g., "MNQ")
            side: Order side ("BUY" or "SELL")
            quantity: Number of contracts
            account_id: Account ID
            **kwargs: Additional parameters:
                - stop_loss_ticks: Optional stop loss in ticks
                - take_profit_ticks: Optional take profit in ticks
                - limit_price: Optional limit price (makes it a limit order)
                - order_type: "market" or "limit" (default: "market")
                - custom_tag: Optional custom tag for order tracking
                - strategy_name: Optional strategy name
            
        Returns:
            OrderResponse with order details
        """
        try:
            # Try Rust hot path first (20-30x faster)
            if self._use_rust and self._rust_executor:
                try:
                    return await self._place_market_order_rust(
                        symbol, side, quantity, account_id, **kwargs
                    )
                except Exception as e:
                    logger.warning(f"⚠️  Rust execution failed, falling back to Python: {e}")
                    # Fall through to Python implementation
            
            # Python implementation (fallback or when Rust disabled)
            return await self._place_market_order_python(
                symbol, side, quantity, account_id, **kwargs
            )
        except Exception as e:
            logger.error(f"❌ Order placement failed: {e}", exc_info=True)
            return OrderResponse(
                success=False,
                error=f"Order placement failed: {str(e)}",
                raw_response=None
            )
    
    async def _place_market_order_rust(
        self,
        symbol: str,
        side: str,
        quantity: int,
        account_id: Optional[str],
        **kwargs
    ) -> OrderResponse:
        """Place order using Rust executor (hot path - 20-30x faster)."""
        import time
        start_time = time.perf_counter()
        
        # Ensure valid token
        await self.auth.ensure_valid_token()
        
        if not account_id:
            return OrderResponse(success=False, error="Account ID is required")
        
        # Update Rust executor with current token and contract
        token = self.auth.get_token()
        self._rust_executor.set_token(token)
        
        # Get contract ID and cache it in Rust executor
        try:
            contract_id = self.contract_manager.get_contract_id(symbol)
            # Contract IDs from TopStepX are strings like "CON.F.US.MNQ.Z25"
            self._rust_executor.set_contract_id(symbol, contract_id)
        except ValueError as e:
            return OrderResponse(
                success=False,
                error=f"Cannot place order: {e}. Please fetch contracts first."
            )
        
        # Extract kwargs for Rust
        stop_loss_ticks = kwargs.get('stop_loss_ticks')
        take_profit_ticks = kwargs.get('take_profit_ticks')
        limit_price = kwargs.get('limit_price')
        order_type = kwargs.get('order_type', 'market')
        custom_tag = kwargs.get('custom_tag')
        
        # Convert ticks to int if provided
        stop_loss_ticks_int = int(stop_loss_ticks) if stop_loss_ticks is not None else None
        take_profit_ticks_int = int(take_profit_ticks) if take_profit_ticks is not None else None
        
        # Call Rust async method
        rust_result = await self._rust_executor.place_market_order(
            symbol=symbol,
            side=side,
            quantity=quantity,
            account_id=int(account_id),
            stop_loss_ticks=stop_loss_ticks_int,
            take_profit_ticks=take_profit_ticks_int,
            limit_price=limit_price,
            order_type=order_type,
            custom_tag=custom_tag
        )
        
        elapsed_ms = (time.perf_counter() - start_time) * 1000
        logger.info(f"⚡ Rust execution: {elapsed_ms:.2f}ms")
        
        # Convert Rust response to OrderResponse
        order_response = OrderResponse(
            success=rust_result.get('success', False),
            order_id=rust_result.get('order_id'),
            message=rust_result.get('message'),
            error=rust_result.get('error'),
            raw_response=rust_result.get('raw_response')
        )
        
        # Record audit trail if order was successful
        if order_response.success and order_response.order_id:
            try:
                from core.order_audit import record_order_audit
                strategy_name = kwargs.get('strategy_name')
                execution_method = 'rust'  # This is Rust path
                order_type_val = kwargs.get('order_type', 'market')
                entry_trigger = 'market' if order_type_val == 'market' else 'limit' if order_type_val == 'limit' else 'stop'
                
                record_order_audit(
                    order_id=str(order_response.order_id),
                    strategy_name=strategy_name,
                    execution_method=execution_method,
                    entry_trigger=entry_trigger,
                    symbol=symbol,
                    side=side,
                    quantity=quantity
                )
            except Exception as audit_err:
                logger.debug(f"Could not record audit trail: {audit_err}")
        
        return order_response
    
    async def _place_market_order_python(
        self,
        symbol: str,
        side: str,
        quantity: int,
        account_id: Optional[str],
        **kwargs
    ) -> OrderResponse:
        """Place order using Python implementation (fallback)."""
        import time
        start_time = time.perf_counter()
        
        # Ensure valid token
        await self.auth.ensure_valid_token()
        
        if not account_id:
            return OrderResponse(
                success=False,
                error="Account ID is required"
            )
        
        if side.upper() not in ["BUY", "SELL"]:
            return OrderResponse(
                success=False,
                error="Side must be 'BUY' or 'SELL'"
            )
        
        # Extract kwargs
        stop_loss_ticks = kwargs.get('stop_loss_ticks')
        take_profit_ticks = kwargs.get('take_profit_ticks')
        limit_price = kwargs.get('limit_price')
        stop_price = kwargs.get('stop_price')
        order_type = kwargs.get('order_type', 'market').lower()
        custom_tag = kwargs.get('custom_tag')
        reduce_only = kwargs.get('reduce_only', False)  # Critical for linking orders to positions

        if order_type == "limit" and limit_price is None:
            return OrderResponse(
                success=False,
                error="Limit price is required for limit orders"
            )
        
        if order_type == "stop" and stop_price is None:
            return OrderResponse(
                success=False,
                error="Stop price is required for stop orders"
            )

        logger.info(f"🐍 Python execution: Placing {side} {order_type} order for {quantity} {symbol} on account {account_id}{' (reduce-only)' if reduce_only else ''}")

        # Convert side to numeric value (TopStepX API uses numbers)
        side_value = 0 if side.upper() == "BUY" else 1

        # Get contract ID
        try:
            contract_id = self.contract_manager.get_contract_id(symbol)
        except ValueError as e:
            error_msg = f"Cannot place order: {e}. Please fetch contracts first."
            logger.error(f"❌ {error_msg}")
            return OrderResponse(success=False, error=error_msg)

        # Determine order type (TopStepX API uses numbers)
        if order_type == "limit":
            order_type_value = 1  # Limit order
        elif order_type == "stop":
            order_type_value = 4  # Stop order
        elif order_type == "bracket":
            order_type_value = 2  # Market order for entry, brackets handled separately
        else:
            order_type_value = 2  # Market order

        # ── J: Build minimal order payload (skip null/default fields entirely) ──
        # Smaller payload → faster orjson encode → faster TLS write. Auth's
        # ``cleaned_data`` no-op-strips ``None`` fields anyway, but skipping them at
        # source avoids an N-key dict comprehension on the hot path.
        order_data = {
            "accountId": int(account_id),
            "contractId": contract_id,
            "type": order_type_value,
            "side": side_value,
            "size": quantity,
        }
        if order_type == "limit" and limit_price is not None:
            order_data["limitPrice"] = limit_price
        if order_type == "stop" and stop_price is not None:
            order_data["stopPrice"] = stop_price
        # Only emit reduceOnly when truthy — TopStepX defaults it to false server-side,
        # so omitting it on entry orders is safe and saves payload bytes.
        if reduce_only:
            order_data["reduceOnly"] = True
        if custom_tag:
            order_data["customTag"] = custom_tag
        
        # Add bracket orders if specified
        # NOTE: Do NOT set reduceOnly=True on brackets for entry orders!
        # The entry hasn't filled yet, so there's no position to reduce.
        # TopStepX automatically handles bracket attachment once the entry fills.
        if stop_loss_ticks is not None or take_profit_ticks is not None:
            if stop_loss_ticks is not None:
                order_data["stopLossBracket"] = {
                    "ticks": stop_loss_ticks,
                    "type": 4,  # Stop loss type
                    "size": quantity
                    # reduceOnly removed - brackets auto-attach after entry fills
                }
            
            if take_profit_ticks is not None:
                order_data["takeProfitBracket"] = {
                    "ticks": take_profit_ticks,
                    "type": 1,  # Take profit type
                    "size": quantity
                    # reduceOnly removed - brackets auto-attach after entry fills
                }
        
        # Log order details (debug only; payloads are large and spammy at INFO).
        logger.debug(f"Order data: {dumps_str({k: v for k, v in order_data.items() if v is not None})}")
        
        # Make API call
        headers = {
            "accept": "text/plain",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.auth.get_token()}"
        }
        
        response = await self._make_request("POST", "/api/Order/place", data=order_data, headers=headers)
        
        # Handle 500 errors with automatic token refresh and retry
        if "error" in response and "500" in str(response.get("error", "")):
            logger.warning("⚠️  Received 500 error on order placement. Attempting token refresh and retry...")
            
            # Refresh token
            token_refreshed = await self.auth.ensure_valid_token()
            if token_refreshed:
                # Update headers with new token
                headers["Authorization"] = f"Bearer {self.auth.get_token()}"
                
                # Add small delay before retry (0.75s as recommended)
                await asyncio.sleep(0.75)
                
                logger.info("🔄 Retrying order placement with refreshed token...")
                # Retry the request
                response = await self._make_request("POST", "/api/Order/place", data=order_data, headers=headers)
                logger.info(f"   Retry response: {'success' if 'error' not in response else 'failed'}")
        
        # Check for explicit errors
        if "error" in response:
            logger.error(f"API returned error: {response['error']}")
            return OrderResponse(
                success=False,
                error=response['error'],
                raw_response=response
            )
        
        # Validate response structure
        if not isinstance(response, dict):
            logger.error(f"API returned non-dict response: {type(response)}")
            return OrderResponse(
                success=False,
                error=f"Invalid API response type: {type(response)}",
                raw_response=response
            )
        
        # Check success field
        success = response.get("success")
        if success is False or success is None or success == "false":
            error_code = response.get("errorCode", "Unknown")
            error_message = response.get("errorMessage", response.get("message", "No error message"))
            logger.error(f"Order failed - success={success}, errorCode={error_code}, message={error_message}")
            return OrderResponse(
                success=False,
                error=f"Order failed: {error_message} (Code: {error_code})",
                raw_response=response
            )
        
        elapsed_ms = (time.perf_counter() - start_time) * 1000
        logger.info(f"🐍 Python execution: {elapsed_ms:.2f}ms")
        
        # Check for order ID
        order_id = response.get("orderId") or response.get("id") or response.get("data", {}).get("orderId")
        if not order_id:
            logger.error("API returned success but NO order ID (see DEBUG for full response).")
            logger.debug(f"Full response: {dumps_str(response)}")
            return OrderResponse(
                success=False,
                error="Order rejected: No order ID returned",
                raw_response=response
            )
        
        logger.info(f"✅ Order placed successfully with ID: {order_id}")
        
        # Record audit trail
        try:
            from core.order_audit import record_order_audit
            # Extract metadata from kwargs if available
            strategy_name = kwargs.get('strategy_name')
            execution_method = 'python'  # This is Python path
            entry_trigger = 'market' if order_type == 'market' else 'limit' if order_type == 'limit' else 'stop'
            
            record_order_audit(
                order_id=str(order_id),
                strategy_name=strategy_name,
                execution_method=execution_method,
                entry_trigger=entry_trigger,
                symbol=symbol,
                side=side,
                quantity=quantity
            )
        except Exception as audit_err:
            logger.debug(f"Could not record audit trail: {audit_err}")
        
        return OrderResponse(
            success=True,
            order_id=str(order_id),
            message="Order placed successfully",
            raw_response=response
        )
    
    async def place_limit_order(
        self,
        symbol: str,
        side: str,
        quantity: int,
        price: float,
        account_id: Optional[str] = None,
        **kwargs
    ) -> OrderResponse:
        """
        Place a limit order.
        
        Uses Rust executor for hot path with Python fallback.
        """
        try:
            # Try Rust hot path first
            if self._use_rust and self._rust_executor:
                try:
                    return await self._place_limit_order_rust(
                        symbol, side, quantity, price, account_id, **kwargs
                    )
                except Exception as e:
                    logger.warning(f"⚠️  Rust execution failed, falling back to Python: {e}")
            
            # Python fallback
            return await self._place_limit_order_python(
                symbol, side, quantity, price, account_id, **kwargs
            )
        except Exception as e:
            logger.error(f"❌ Limit order placement failed: {e}", exc_info=True)
            return OrderResponse(
                success=False,
                error=f"Limit order placement failed: {str(e)}",
                raw_response=None
            )
    
    async def _place_limit_order_rust(
        self,
        symbol: str,
        side: str,
        quantity: int,
        price: float,
        account_id: Optional[str],
        **kwargs
    ) -> OrderResponse:
        """Place limit order using Rust executor."""
        import time
        start_time = time.perf_counter()
        
        await self.auth.ensure_valid_token()
        
        if not account_id:
            return OrderResponse(success=False, error="Account ID is required")
        
        token = self.auth.get_token()
        self._rust_executor.set_token(token)
        
        try:
            contract_id = self.contract_manager.get_contract_id(symbol)
            self._rust_executor.set_contract_id(symbol, contract_id)
        except ValueError as e:
            return OrderResponse(
                success=False,
                error=f"Cannot place order: {e}. Please fetch contracts first."
            )
        
        stop_loss_ticks = kwargs.get('stop_loss_ticks')
        take_profit_ticks = kwargs.get('take_profit_ticks')
        custom_tag = kwargs.get('custom_tag')
        
        stop_loss_ticks_int = int(stop_loss_ticks) if stop_loss_ticks is not None else None
        take_profit_ticks_int = int(take_profit_ticks) if take_profit_ticks is not None else None
        
        rust_result = await self._rust_executor.place_order(
            symbol=symbol,
            side=side,
            quantity=quantity,
            account_id=int(account_id),
            stop_loss_ticks=stop_loss_ticks_int,
            take_profit_ticks=take_profit_ticks_int,
            limit_price=price,
            stop_price=None,
            order_type="limit",
            custom_tag=custom_tag
        )
        
        elapsed_ms = (time.perf_counter() - start_time) * 1000
        logger.info(f"⚡ Rust limit order execution: {elapsed_ms:.2f}ms")
        
        return OrderResponse(
            success=rust_result.get('success', False),
            order_id=rust_result.get('order_id'),
            message=rust_result.get('message'),
            error=rust_result.get('error'),
            raw_response=rust_result.get('raw_response')
        )
    
    async def _place_limit_order_python(
        self,
        symbol: str,
        side: str,
        quantity: int,
        price: float,
        account_id: Optional[str],
        **kwargs
    ) -> OrderResponse:
        """Place limit order using Python implementation."""
        # Use place_market_order with limit_price and order_type
        return await self._place_market_order_python(
            symbol, side, quantity, account_id,
            limit_price=price,
            order_type="limit",
            **kwargs
        )
    
    async def place_stop_order(
        self,
        symbol: str,
        side: str,
        quantity: int,
        stop_price: float,
        account_id: Optional[str] = None,
        **kwargs
    ) -> OrderResponse:
        """
        Place a stop order.
        
        Uses Rust executor for hot path with Python fallback.
        """
        try:
            # Try Rust hot path first
            if self._use_rust and self._rust_executor:
                try:
                    return await self._place_stop_order_rust(
                        symbol, side, quantity, stop_price, account_id, **kwargs
                    )
                except Exception as e:
                    logger.warning(f"⚠️  Rust execution failed, falling back to Python: {e}")
            
            # Python fallback
            return await self._place_stop_order_python(
                symbol, side, quantity, stop_price, account_id, **kwargs
            )
        except Exception as e:
            logger.error(f"❌ Stop order placement failed: {e}", exc_info=True)
            return OrderResponse(
                success=False,
                error=f"Stop order placement failed: {str(e)}",
                raw_response=None
            )
    
    async def _place_stop_order_rust(
        self,
        symbol: str,
        side: str,
        quantity: int,
        stop_price: float,
        account_id: Optional[str],
        **kwargs
    ) -> OrderResponse:
        """Place stop order using Rust executor."""
        import time
        start_time = time.perf_counter()
        
        await self.auth.ensure_valid_token()
        
        if not account_id:
            return OrderResponse(success=False, error="Account ID is required")
        
        token = self.auth.get_token()
        self._rust_executor.set_token(token)
        
        try:
            contract_id = self.contract_manager.get_contract_id(symbol)
            self._rust_executor.set_contract_id(symbol, contract_id)
        except ValueError as e:
            return OrderResponse(
                success=False,
                error=f"Cannot place order: {e}. Please fetch contracts first."
            )
        
        stop_loss_ticks = kwargs.get('stop_loss_ticks')
        take_profit_ticks = kwargs.get('take_profit_ticks')
        custom_tag = kwargs.get('custom_tag')
        
        stop_loss_ticks_int = int(stop_loss_ticks) if stop_loss_ticks is not None else None
        take_profit_ticks_int = int(take_profit_ticks) if take_profit_ticks is not None else None
        
        rust_result = await self._rust_executor.place_order(
            symbol=symbol,
            side=side,
            quantity=quantity,
            account_id=int(account_id),
            stop_loss_ticks=stop_loss_ticks_int,
            take_profit_ticks=take_profit_ticks_int,
            limit_price=None,
            stop_price=stop_price,
            order_type="stop",
            custom_tag=custom_tag
        )
        
        elapsed_ms = (time.perf_counter() - start_time) * 1000
        logger.info(f"⚡ Rust stop order execution: {elapsed_ms:.2f}ms")
        
        return OrderResponse(
            success=rust_result.get('success', False),
            order_id=rust_result.get('order_id'),
            message=rust_result.get('message'),
            error=rust_result.get('error'),
            raw_response=rust_result.get('raw_response')
        )
    
    async def _place_stop_order_python(
        self,
        symbol: str,
        side: str,
        quantity: int,
        stop_price: float,
        account_id: Optional[str],
        **kwargs
    ) -> OrderResponse:
        """Place stop order using Python implementation."""
        # Pass stop_price and set order_type to "stop"
        return await self._place_market_order_python(
            symbol, side, quantity, account_id,
            stop_price=stop_price,
            order_type="stop",
            **kwargs
        )
    
    async def modify_order(
        self,
        order_id: str,
        price: Optional[float] = None,
        quantity: Optional[int] = None,
        account_id: Optional[str] = None,
        **kwargs
    ) -> ModifyOrderResponse:
        """
        Modify an existing order.
        
        Uses Rust executor for hot path (10-15x faster) with Python fallback.
        
        Args:
            order_id: Order ID to modify
            price: New price (optional)
            quantity: New quantity (optional)
            account_id: Account ID
            **kwargs: Additional parameters (order_type, etc.)
            
        Returns:
            ModifyOrderResponse with modification details
        """
        try:
            # Try Rust hot path first
            if self._use_rust and self._rust_executor:
                try:
                    return await self._modify_order_rust(order_id, price, quantity, account_id, **kwargs)
                except Exception as e:
                    logger.warning(f"⚠️  Rust modify failed, falling back to Python: {e}")
            
            # Python implementation
            return await self._modify_order_python(order_id, price, quantity, account_id, **kwargs)
        except Exception as e:
            logger.error(f"❌ Order modification failed: {e}", exc_info=True)
            return ModifyOrderResponse(
                success=False,
                error=f"Order modification failed: {str(e)}"
            )
    
    async def _modify_order_rust(
        self,
        order_id: str,
        price: Optional[float],
        quantity: Optional[int],
        account_id: Optional[str],
        **kwargs
    ) -> ModifyOrderResponse:
        """Modify order using Rust executor (hot path)."""
        import time
        start_time = time.perf_counter()
        
        await self.auth.ensure_valid_token()
        
        if not account_id:
            return ModifyOrderResponse(success=False, error="Account ID is required")
        
        # Update Rust executor with current token
        self._rust_executor.set_token(self.auth.get_token())
        
        # Call Rust async method
        rust_result = await self._rust_executor.modify_order(
            order_id=order_id,
            price=price,
            quantity=quantity
        )
        
        elapsed_ms = (time.perf_counter() - start_time) * 1000
        logger.info(f"⚡ Rust modify: {elapsed_ms:.2f}ms")
        
        # Convert to ModifyOrderResponse
        return ModifyOrderResponse(
            success=rust_result.get('success', False),
            order_id=rust_result.get('order_id'),
            message=rust_result.get('message'),
            error=rust_result.get('error'),
            raw_response=rust_result.get('raw_response')
        )
    
    async def _modify_order_python(
        self,
        order_id: str,
        price: Optional[float],
        quantity: Optional[int],
        account_id: Optional[str],
        **kwargs
    ) -> ModifyOrderResponse:
        """Modify order using Python implementation (fallback)."""
        import time
        start_time = time.perf_counter()

        await self.auth.ensure_valid_token()

        if not account_id:
            return ModifyOrderResponse(
                success=False,
                error="Account ID is required",
                raw_response=None
            )

        def _coerce_order_type(val) -> int:
            if val is None:
                return 1
            if isinstance(val, int):
                return val
            s = str(val).strip().upper()
            if s in ("3", "4", "STOP") or "STOP" in s:
                return 4
            if s in ("1", "LIMIT"):
                return 1
            try:
                return int(float(s))
            except (TypeError, ValueError):
                return 1

        def _order_row_id(order: dict) -> str:
            return str(order.get("id") or order.get("orderId") or order.get("order_id") or "")

        def _symbol_from_order_row(order: Optional[dict]) -> str:
            if not order:
                return ""
            sym = str(order.get("symbol") or "").strip().upper()
            if sym and sym != "CON":
                return sym
            contract_id = str(order.get("contractId") or order.get("contract_id") or "")
            if contract_id and hasattr(self, "contract_manager") and self.contract_manager:
                extracted = self.contract_manager.extract_symbol_from_contract_id(contract_id)
                if extracted:
                    return extracted.upper()
            if contract_id:
                parts = contract_id.rstrip(".").split(".")
                if len(parts) >= 4:
                    return parts[-2].upper()
            return sym

        # Get order info to determine type and check if it's a bracket order
        order_info = None
        if quantity is not None or price is not None:
            # Get open orders to find this order
            open_orders = await self.get_open_orders(account_id=account_id)
            for order in open_orders:
                if _order_row_id(order) == str(order_id):
                    order_info = order
                    break

            if order_info is None:
                return ModifyOrderResponse(
                    success=False,
                    error=f"Order {order_id} not found or no longer open",
                    order_id=order_id,
                    raw_response=None,
                )

            # Check if order is a bracket order (no customTag) and trying to modify size
            if quantity is not None and order_info and not order_info.get("customTag"):
                return ModifyOrderResponse(
                    success=False,
                    error=(
                        "Cannot modify size of bracket order attached to position. "
                        "Bracket orders automatically match position size. "
                        "You can only modify the price, or close the position to remove the bracket orders."
                    ),
                    raw_response=None
                )

        logger.info(f"Modifying order {order_id} on account {account_id}")

        headers = {
            "accept": "text/plain",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.auth.get_token()}",
        }

        modify_data = {
            "orderId": int(order_id),
            "accountId": int(account_id),
        }

        # Only include size if provided
        if quantity is not None:
            modify_data["size"] = quantity

        if price is not None:
            # Determine price field based on order type
            order_type = kwargs.get("order_type")
            if order_info:
                actual_order_type = _coerce_order_type(order_info.get("type", order_type))
            else:
                actual_order_type = _coerce_order_type(order_type)

            symbol = _symbol_from_order_row(order_info)
            if symbol:
                try:
                    tick_size = await self._get_tick_size(symbol)
                    price = self._round_to_tick_size(float(price), tick_size)
                except Exception as exc:
                    logger.debug("Modify-order tick round skipped for %s: %s", symbol, exc)

            if actual_order_type in (3, 4):  # Stop / stop-limit
                modify_data["stopPrice"] = price
            else:  # Limit order or other types
                modify_data["limitPrice"] = price

        response = await self._make_request("POST", "/api/Order/modify", data=modify_data, headers=headers)

        if "error" in response:
            logger.error(f"Failed to modify order: {response['error']}")
            return ModifyOrderResponse(
                success=False,
                error=response["error"],
                order_id=order_id,
                raw_response=response
            )

        # Check if the API response indicates success
        if response.get("success") is False:
            error_code = response.get("errorCode", "Unknown")
            error_message = response.get("errorMessage", "No error message")
            logger.error(f"Order modification failed: Error Code {error_code}, Message: {error_message}")
            return ModifyOrderResponse(
                success=False,
                error=f"Order modification failed: {error_message} (Code: {error_code})",
                order_id=order_id,
                raw_response=response
            )

        elapsed_ms = (time.perf_counter() - start_time) * 1000
        logger.info(f"🐍 Python modify: {elapsed_ms:.2f}ms")
        logger.info(f"✅ Order modified successfully: {order_id}")

        return ModifyOrderResponse(
            success=True,
            order_id=order_id,
            message="Order modified successfully",
            raw_response=response
        )
    
    async def cancel_order(
        self,
        order_id: str,
        account_id: Optional[str] = None,
        **kwargs
    ) -> CancelResponse:
        """
        Cancel an order.
        
        Uses Rust executor for hot path (10-15x faster) with Python fallback.
        Both implementations require account_id as it's required by the API.
        
        Args:
            order_id: Order ID to cancel
            account_id: Account ID (required by API)
            **kwargs: Additional parameters
            
        Returns:
            CancelResponse with cancellation details
        """
        try:
            # Account ID is required by the API
            if not account_id:
                return CancelResponse(
                    success=False,
                    error="Account ID is required for order cancellation",
                    order_id=order_id
                )
            
            # Try Rust hot path first (now supports account_id)
            if self._use_rust and self._rust_executor:
                try:
                    return await self._cancel_order_rust(order_id, account_id, **kwargs)
                except Exception as e:
                    logger.warning(f"⚠️  Rust cancel failed, falling back to Python: {e}")
            
            # Python implementation (fallback)
            return await self._cancel_order_python(order_id, account_id, **kwargs)
        except Exception as e:
            logger.error(f"❌ Order cancellation failed: {e}", exc_info=True)
            return CancelResponse(
                success=False,
                error=f"Order cancellation failed: {str(e)}",
                order_id=order_id
            )
    
    async def _cancel_order_rust(
        self,
        order_id: str,
        account_id: Optional[str],
        **kwargs
    ) -> CancelResponse:
        """Cancel order using Rust executor (hot path)."""
        import time
        start_time = time.perf_counter()
        
        await self.auth.ensure_valid_token()
        
        if not account_id:
            return CancelResponse(success=False, error="Account ID is required", order_id=order_id)
        
        # Update Rust executor with current token and account_id
        self._rust_executor.set_token(self.auth.get_token())
        self._rust_executor.set_account_id(int(account_id))
        
        # Call Rust async method (using state-based account_id)
        try:
            rust_result = await self._rust_executor.cancel_order(order_id)
        except (TypeError, AttributeError) as e:
            error_str = str(e)
            if "unexpected keyword argument" in error_str or "needs rebuild" in error_str.lower():
                # Old Rust library doesn't support account_id yet - use Python fallback
                raise Exception("Rust library needs rebuild - using Python fallback")
            # Log the actual error for debugging
            logger.error(f"Rust cancel_order error: {e}", exc_info=True)
            raise
        
        elapsed_ms = (time.perf_counter() - start_time) * 1000
        logger.info(f"⚡ Rust cancel: {elapsed_ms:.2f}ms")
        
        # Convert to CancelResponse
        return CancelResponse(
            success=rust_result.get('success', False),
            order_id=rust_result.get('order_id') or order_id,
            message=rust_result.get('message'),
            error=rust_result.get('error'),
            raw_response=rust_result.get('raw_response')
        )
    
    async def _cancel_order_python(
        self,
        order_id: str,
        account_id: Optional[str],
        **kwargs
    ) -> CancelResponse:
        """Cancel order using Python implementation (fallback)."""
        import time
        start_time = time.perf_counter()
        
        await self.auth.ensure_valid_token()
        
        if not account_id:
            return CancelResponse(
                success=False,
                error="Account ID is required"
            )
        
        logger.info(f"🐍 Python execution: Canceling order {order_id} on account {account_id}")
        
        headers = {
            "accept": "text/plain",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.auth.get_token()}"
        }
        
        cancel_data = {
            "orderId": order_id,
            "accountId": int(account_id)
        }
        
        response = await self._make_request("POST", "/api/Order/cancel", data=cancel_data, headers=headers)
        
        if "error" in response:
            logger.error(f"Failed to cancel order: {response['error']}")
            return CancelResponse(
                success=False,
                error=response['error'],
                order_id=order_id
            )
        
        elapsed_ms = (time.perf_counter() - start_time) * 1000
        logger.info(f"🐍 Python cancel: {elapsed_ms:.2f}ms")
        logger.info(f"✅ Order canceled successfully: {order_id}")
        return CancelResponse(
            success=True,
            order_id=order_id,
            message="Order canceled successfully"
        )
    
    async def get_open_orders(
        self,
        account_id: Optional[str] = None,
        **kwargs
    ) -> list:
        """
        Get all open orders.
        
        Uses Rust executor for hot path with Python fallback.
        
        Args:
            account_id: Account ID
            **kwargs: Additional parameters
            
        Returns:
            List of open orders
        """
        try:
            # Try Rust hot path first
            if self._use_rust and self._query_executor:
                try:
                    return await self._get_open_orders_rust(account_id)
                except Exception as e:
                    logger.warning(f"⚠️  Rust execution failed, falling back to Python: {e}")
            
            # Python fallback
            await self.auth.ensure_valid_token()
            
            if not account_id:
                logger.error("Account ID is required")
                return []
            
            logger.debug("Fetching open orders for account %s", account_id)
            
            headers = {
                "accept": "text/plain",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.auth.get_token()}"
            }
            
            # Use TopStepX Gateway API for orders
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc)
            start_time = now.replace(hour=0, minute=0, second=0, microsecond=0)
            
            search_data = {
                "accountId": int(account_id),
                "startTimestamp": start_time.isoformat(),
                "endTimestamp": now.isoformat(),
                "request": {
                    "accountId": int(account_id),
                    "status": "Open"
                }
            }
            
            response = await self._make_request("POST", "/api/Order/search", data=search_data, headers=headers)
            
            if "error" in response:
                logger.error(f"Failed to fetch orders: {response['error']}")
                return []
            
            if not response.get("success"):
                logger.error(f"API returned error: {response}")
                return []
            
            # Check for different possible order data fields
            orders = []
            for field in ["orders", "data", "result", "items", "list"]:
                if field in response and isinstance(response[field], list):
                    orders = response[field]
                    break
            
            if not orders:
                logger.debug("No open orders found for account %s", account_id)
                return []
            # IMPORTANT: do NOT filter by status==1.
            # TopStepX "Open" search can include related bracket child orders that are "SuspENDED"
            # until the parent triggers. Filtering would hide those brackets from the GUI.
            logger.debug(f"Found {len(orders)} open-related orders (includes suspended brackets when returned by API)")  # Reduced to DEBUG
            return orders
            
        except Exception as e:
            # str(e) is empty for many aiohttp transients (ServerDisconnectedError,
            # ClientPayloadError, ConnectionResetError). Logging type(e).__name__
            # turns "Failed to fetch orders: " into "Failed to fetch orders:
            # ServerDisconnectedError" so a benign keepalive blip is distinguishable
            # from a real error without grepping for a stack trace.
            logger.error(f"Failed to fetch orders: {type(e).__name__}: {str(e) or repr(e)}")
            return []
    
    async def _get_open_orders_rust(self, account_id: Optional[str] = None) -> list:
        """
        Get open orders using Rust QueryExecutor.
        
        Args:
            account_id: Account ID (required)
            
        Returns:
            List of order dictionaries
        """
        if not self._query_executor:
            raise RuntimeError("Rust QueryExecutor not available")
        
        if not account_id:
            raise ValueError("Account ID is required")
        
        # Set token if needed
        token = self.auth.get_token()
        if token:
            self._query_executor.set_token(token)
        
        # Call Rust method
        orders_py = await self._query_executor.get_open_orders(int(account_id))
        
        # Convert Python list to list of dicts
        orders = []
        if isinstance(orders_py, list):
            for order in orders_py:
                if isinstance(order, dict):
                    orders.append(order)
        
        # IMPORTANT: do NOT filter by status==1.
        # Rust QueryExecutor already queries TopStepX "Open" search; results may include suspended bracket children.
        logger.debug(f"Found {len(orders)} open-related orders via Rust (includes suspended brackets when returned by API)")  # Reduced to DEBUG
        return orders
    
    async def get_order_history(
        self,
        account_id: Optional[str] = None,
        limit: int = 100,
        **kwargs
    ) -> list:
        """
        Get order history.
        
        Uses Rust executor for hot path with Python fallback.
        
        Args:
            account_id: Account ID
            limit: Maximum number of orders to return
            **kwargs: Additional parameters (start_timestamp, end_timestamp)
            
        Returns:
            List of historical orders
        """
        try:
            # Note: Rust implementation for order history not yet available
            # Using Python path for now
            await self.auth.ensure_valid_token()
            
            if not account_id:
                logger.error("Account ID is required")
                return []
            
            logger.debug("Fetching order history for account %s", account_id)
            
            headers = {
                "accept": "text/plain",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.auth.get_token()}"
            }
            
            # Use datetime for time range
            from datetime import datetime, timezone, timedelta
            
            start_timestamp = kwargs.get('start_timestamp')
            end_timestamp = kwargs.get('end_timestamp')
            
            if start_timestamp:
                start_time = datetime.fromisoformat(start_timestamp.replace('Z', '+00:00'))
            else:
                now = datetime.now(timezone.utc)
                start_time = now - timedelta(days=7)
            
            if end_timestamp:
                end_time = datetime.fromisoformat(end_timestamp.replace('Z', '+00:00'))
            else:
                end_time = datetime.now(timezone.utc)
            
            search_data = {
                "accountId": int(account_id),
                "startTimestamp": start_time.isoformat(),
                "endTimestamp": end_time.isoformat(),
                "request": {
                    "accountId": int(account_id),
                    "limit": limit
                }
            }
            
            response = await self._make_request("POST", "/api/Order/search", data=search_data, headers=headers)
            
            if "error" in response:
                logger.error(f"Failed to fetch order history: {response['error']}")
                return []
            
            if not response.get("success"):
                logger.error(f"API returned error: {response}")
                return []
            
            # Check for different possible order data fields
            orders = []
            for field in ["orders", "data", "result", "items", "list"]:
                if field in response and isinstance(response[field], list):
                    orders = response[field]
                    break
            
            if not orders:
                logger.debug("No historical orders found for account %s", account_id)
                return []
            
            # Filter to only filled/executed orders for history (status == 2)
            filled_orders = [o for o in orders if o.get("status") == 2 and o.get("fillVolume", 0) > 0]
            
            # Try Fill/search endpoint if no filled orders
            # NOTE: This endpoint may not exist (404) - handle gracefully
            if not filled_orders:
                logger.debug("No filled orders from Order/search, trying Fill/search endpoint")
                fill_search_data = {
                    "accountId": int(account_id),
                    "startTime": start_time.isoformat(),
                    "endTime": end_time.isoformat(),
                    "limit": limit
                }
                
                fill_response = await self._make_request("POST", "/api/Fill/search", data=fill_search_data, headers=headers)
                
                # Handle 404 errors gracefully - endpoint may not exist
                if fill_response and fill_response.get("status_code") == 404:
                    logger.debug("Fill/search endpoint not available (404) - skipping fallback")
                elif fill_response and "error" not in fill_response and fill_response.get("success"):
                    fills = []
                    for field in ["fills", "data", "result", "items", "list"]:
                        if field in fill_response and isinstance(fill_response[field], list):
                            fills = fill_response[field]
                            break
                    
                    if fills:
                        # Convert fills to order format
                        for fill in fills:
                            filled_orders.append({
                                'id': fill.get('id') or fill.get('fillId'),
                                'symbol': fill.get('symbol') or fill.get('contractId'),
                                'side': fill.get('side'),
                                'quantity': fill.get('quantity') or fill.get('qty'),
                                'price': fill.get('price') or fill.get('fillPrice'),
                                'timestamp': fill.get('timestamp') or fill.get('fillTime'),
                                'status': 2,
                                'orderId': fill.get('orderId'),
                                **fill
                            })
                elif fill_response and "error" in fill_response:
                    # Check if it's a 404 - if so, just skip silently
                    error_str = str(fill_response.get("error", ""))
                    if "404" not in error_str:
                        # Only log non-404 errors
                        logger.debug(f"Fill/search endpoint returned error: {fill_response.get('error')}")
            
            # Limit results
            if len(filled_orders) > limit:
                filled_orders = filled_orders[:limit]
            
            logger.debug("Found %d historical filled orders", len(filled_orders))
            return filled_orders
            
        except Exception as e:
            logger.error(f"Failed to fetch order history: {str(e)}")
            return []
    
    async def get_trades(
        self,
        account_id: Optional[str] = None,
        start_timestamp: Optional[str] = None,
        end_timestamp: Optional[str] = None,
        **kwargs
    ) -> List[Dict]:
        """
        Get trades from TopStepX Trade/search API endpoint.
        
        This endpoint provides trades with pre-calculated profitAndLoss,
        which is more accurate than consolidating orders manually.
        
        Args:
            account_id: Account ID (required)
            start_timestamp: Start timestamp in ISO format (required)
            end_timestamp: End timestamp in ISO format (optional)
            **kwargs: Additional parameters
            
        Returns:
            List[Dict]: List of trades with profitAndLoss, fees, etc.
        """
        try:
            await self.auth.ensure_valid_token()
            
            if not account_id:
                logger.error("Account ID is required for Trade/search")
                return []
            
            logger.debug("Fetching trades from Trade/search API for account %s", account_id)
            
            headers = {
                "accept": "text/plain",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.auth.get_token()}"
            }
            
            # Parse timestamps
            from datetime import datetime, timezone, timedelta
            
            if start_timestamp:
                if isinstance(start_timestamp, str):
                    start_time = datetime.fromisoformat(start_timestamp.replace('Z', '+00:00'))
                else:
                    start_time = start_timestamp
            else:
                # Default to 7 days ago
                start_time = datetime.now(timezone.utc) - timedelta(days=7)
            
            if end_timestamp:
                if isinstance(end_timestamp, str):
                    end_time = datetime.fromisoformat(end_timestamp.replace('Z', '+00:00'))
                else:
                    end_time = end_timestamp
            else:
                end_time = datetime.now(timezone.utc)
            
            # Prepare request body according to API docs
            search_data = {
                "accountId": int(account_id),
                "startTimestamp": start_time.isoformat(),
                "endTimestamp": end_time.isoformat() if end_timestamp else None
            }
            
            # Remove None values (endTimestamp is optional)
            search_data = {k: v for k, v in search_data.items() if v is not None}
            
            response = await self._make_request("POST", "/api/Trade/search", data=search_data, headers=headers)
            
            if "error" in response:
                logger.error(f"Failed to fetch trades: {response['error']}")
                return []
            
            if not response.get("success", False):
                error_msg = response.get("errorMessage") or response.get("error", "Unknown error")
                logger.error(f"Trade/search API returned error: {error_msg}")
                return []
            
            # Extract trades from response
            trades = response.get("trades", [])
            
            if not trades:
                logger.debug(
                    "No trades found for account %s in the specified time range", account_id
                )
                return []

            logger.debug("Found %d trades from Trade/search API", len(trades))
            
            # Normalize trade data to match our expected format
            normalized_trades = []
            for trade in trades:
                # Extract symbol from contractId (format: CON.F.US.MNQ.Z25 -> MNQ)
                contract_id = trade.get("contractId", "")
                symbol = "UNKNOWN"
                if contract_id:
                    parts = contract_id.split(".")
                    if len(parts) >= 4:
                        symbol = parts[-2].upper()  # Second to last part is usually the symbol
                
                # Normalize side (0=BUY, 1=SELL)
                side_value = trade.get("side", 0)
                side = "BUY" if side_value == 0 else "SELL"
                
                # profitAndLoss is null for half-turn trades (open positions)
                profit_and_loss = trade.get("profitAndLoss")
                pnl = float(profit_and_loss) if profit_and_loss is not None else None
                
                # Fees from API - note: this might be per-side, so we may need to double it
                # TopStepX commission is typically $0.74 per contract per side = $1.48 round trip
                # For 5 contracts: $0.74 * 5 * 2 = $7.40, but API shows $3.1
                # This suggests fees might already be the full round-trip, or profitAndLoss already has fees deducted
                api_fees = float(trade.get("fees", 0.0))
                
                # Calculate net PnL: profitAndLoss from API might already have fees deducted
                # OR fees might need to be doubled. Based on user's data:
                # - profitAndLoss: $210, actual net: $203.80, difference: $6.20
                # - API fees: $3.1, but actual commission: $6.20
                # So we need to double the fees to get the actual commission
                # OR profitAndLoss already has $3.1 deducted, and we need to subtract another $3.1
                # Let's assume fees need to be doubled for round-trip commission
                actual_fees = api_fees * 2 if api_fees > 0 else 0.0
                
                normalized_trade = {
                    "id": str(trade.get("id", "")),
                    "order_id": str(trade.get("orderId", "")),
                    "account_id": str(trade.get("accountId", account_id)),
                    "symbol": symbol,
                    "side": side,
                    "quantity": int(trade.get("size", 0)),
                    "price": float(trade.get("price", 0.0)),
                    "pnl": pnl,  # Gross PnL (may already have some fees deducted)
                    "profitAndLoss": pnl,  # Keep original field name
                    "fees": actual_fees,  # Full round-trip fees/commission
                    "net_pnl": pnl - actual_fees if pnl is not None else None,
                    "timestamp": trade.get("creationTimestamp", ""),
                    "creationTimestamp": trade.get("creationTimestamp", ""),
                    "voided": trade.get("voided", False),
                    "contractId": contract_id,
                    "status": "filled" if not trade.get("voided", False) else "voided",
                    # Mark as half-turn trade if profitAndLoss is null
                    "is_half_turn": profit_and_loss is None
                }
                normalized_trades.append(normalized_trade)
            
            return normalized_trades
            
        except Exception as e:
            logger.error(f"Failed to fetch trades from Trade/search API: {str(e)}")
            import traceback
            logger.debug(traceback.format_exc())
            return []
    
    # ==================== Position Interface Implementation ====================
    
    def _convert_position_dict_to_object(self, pos_dict: Dict[str, Any]) -> Position:
        """
        Convert position dictionary to Position object.
        
        Args:
            pos_dict: Position dictionary from API
            
        Returns:
            Position object
        """
        # Extract symbol from contract ID if needed
        symbol = pos_dict.get('symbol')
        if not symbol:
            contract_id = pos_dict.get('contractId') or pos_dict.get('contract_id')
            if contract_id:
                symbol = self.contract_manager.extract_symbol_from_contract_id(str(contract_id)) or "UNKNOWN"
        
        # Determine side (0 = Long, 1 = Short)
        side_value = pos_dict.get('side', 0)
        side = "LONG" if side_value == 0 else "SHORT"
        
        return Position(
            position_id=str(pos_dict.get('id', '')),
            symbol=symbol or "UNKNOWN",
            side=side,
            quantity=pos_dict.get('size', 0) or pos_dict.get('quantity', 0),
            entry_price=pos_dict.get('entryPrice', 0.0) or pos_dict.get('entry_price', 0.0),
            current_price=pos_dict.get('currentPrice') or pos_dict.get('current_price'),
            unrealized_pnl=pos_dict.get('unrealizedPnl') or pos_dict.get('unrealized_pnl'),
            account_id=str(pos_dict.get('accountId', '')) or str(pos_dict.get('account_id', '')),
            raw_data=pos_dict
        )
    
    async def get_positions(
        self,
        account_id: Optional[str] = None,
        **kwargs
    ) -> List[Position]:
        """
        Get all open positions.
        
        Uses Rust executor for hot path with Python fallback.
        
        Args:
            account_id: Account ID
            **kwargs: Additional parameters
            
        Returns:
            List of Position objects
        """
        try:
            # Try Rust hot path first
            if self._use_rust and self._query_executor:
                try:
                    return await self._get_positions_rust(account_id)
                except Exception as e:
                    logger.warning(f"⚠️  Rust execution failed, falling back to Python: {e}")
            
            # Python fallback
            await self.auth.ensure_valid_token()
            
            if not account_id:
                logger.error("Account ID is required")
                return []
            
            logger.debug(f"Fetching open positions for account {account_id}")
            
            headers = {
                "accept": "text/plain",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.auth.get_token()}"
            }
            
            search_data = {
                "accountId": int(account_id)
            }
            
            response = await self._make_request("POST", "/api/Position/searchOpen", data=search_data, headers=headers)
            
            if "error" in response:
                logger.error(f"Failed to fetch positions: {response['error']}")
                return []
            
            if not response.get("success"):
                logger.error(f"API returned error: {response}")
                return []
            
            positions_data = response.get("positions", [])
            if not positions_data:
                logger.debug("No open positions found for account %s", account_id)
                return []
            
            # Convert to Position objects
            positions = []
            for pos_dict in positions_data:
                try:
                    position = self._convert_position_dict_to_object(pos_dict)
                    positions.append(position)
                except Exception as e:
                    logger.warning(f"Failed to convert position: {e}")
                    continue
            
            logger.debug(f"Found {len(positions)} open positions")
            return positions
            
        except Exception as e:
            # See "Failed to fetch orders" rationale above for the type(e).__name__
            # treatment — aiohttp transients have empty str(e) and the bare prefix
            # makes blips look like fatal errors.
            logger.error(f"Failed to fetch positions: {type(e).__name__}: {str(e) or repr(e)}")
            return []
    
    async def _get_positions_rust(self, account_id: Optional[str] = None) -> List[Position]:
        """
        Get open positions using Rust QueryExecutor.
        
        Args:
            account_id: Account ID (required)
            
        Returns:
            List of Position objects
        """
        if not self._query_executor:
            raise RuntimeError("Rust QueryExecutor not available")
        
        if not account_id:
            raise ValueError("Account ID is required")
        
        # Set token if needed
        token = self.auth.get_token()
        if token:
            self._query_executor.set_token(token)
        
        # Call Rust method
        positions_py = await self._query_executor.get_positions(int(account_id))
        
        # Convert Python list of dicts to Position objects
        positions = []
        if isinstance(positions_py, list):
            for pos_dict in positions_py:
                try:
                    if isinstance(pos_dict, dict):
                        position = self._convert_position_dict_to_object(pos_dict)
                        positions.append(position)
                except Exception as e:
                    logger.warning(f"Failed to convert position from Rust: {e}")
                    continue
        
        return positions
    
    # Alias for backward compatibility
    async def get_open_positions(
        self,
        account_id: Optional[str] = None,
        **kwargs
    ) -> List[Position]:
        """Alias for get_positions for backward compatibility."""
        return await self.get_positions(account_id=account_id, **kwargs)

    async def get_positions_and_open_orders_parallel(
        self,
        account_id: str,
    ) -> Tuple[List[Position], List[Dict[str, Any]]]:
        """
        Fetch open positions and open-related orders concurrently.

        TopStepX exposes separate REST endpoints; this overlaps the two waits with
        ``asyncio.gather`` so callers needing both pay ~one RTT instead of two sequential.
        """
        positions, orders = await asyncio.gather(
            self.get_positions(account_id=account_id),
            self.get_open_orders(account_id=account_id),
        )
        return positions, orders
    
    async def get_position_details(
        self,
        position_id: str,
        account_id: Optional[str] = None,
        **kwargs
    ) -> Optional[Position]:
        """
        Get details for a specific position.
        
        Args:
            position_id: Position ID
            account_id: Account ID
            **kwargs: Additional parameters
            
        Returns:
            Position object or None if not found
        """
        try:
            await self.auth.ensure_valid_token()
            
            if not account_id:
                logger.error("Account ID is required")
                return None
            
            logger.debug("Fetching position details for position %s", position_id)
            
            headers = {
                "accept": "text/plain",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.auth.get_token()}"
            }
            
            response = await self._make_request("GET", f"/api/Position/{position_id}", headers=headers)
            
            if "error" in response:
                if response.get("status_code") == 404:
                    logger.debug(
                        "Position %s not found (404) — likely closed or stale id",
                        position_id,
                    )
                else:
                    logger.error("Failed to fetch position details: %s", response["error"])
                return None
            
            # Convert to Position object
            try:
                position = self._convert_position_dict_to_object(response)
                return position
            except Exception as e:
                logger.warning(f"Failed to convert position details: {e}")
                return None
            
        except Exception as e:
            logger.error(f"Failed to fetch position details: {str(e)}")
            return None
    
    async def close_position(
        self,
        position_id: str,
        quantity: Optional[int] = None,
        account_id: Optional[str] = None,
        **kwargs
    ) -> CloseResponse:
        """
        Close a position (fully or partially).
        
        Uses Rust executor for hot path with Python fallback.
        
        Args:
            position_id: Position ID to close
            quantity: Quantity to close (None = close all)
            account_id: Account ID
            **kwargs: Additional parameters
            
        Returns:
            CloseResponse with close operation details
        """
        try:
            # Try Rust hot path first (only for full closes, partial requires Python logic)
            if self._use_rust and self._query_executor and quantity is None:
                try:
                    rust_resp = await self._close_position_rust(position_id, account_id)
                    # If Rust failed due to schema/lookup issues, fall back to Python.
                    # This keeps flatten reliable even if TopStepX returns numeric IDs etc.
                    if not rust_resp.success and (rust_resp.error or "").lower().find("could not find contract id") != -1:
                        logger.warning(f"⚠️  Rust close_position couldn't resolve contractId, falling back to Python: {rust_resp.error}")
                    else:
                        return rust_resp
                except Exception as e:
                    logger.warning(f"⚠️  Rust execution failed, falling back to Python: {e}")
            
            # Python fallback (handles partial closes and full closes)
            await self.auth.ensure_valid_token()
            
            if not account_id:
                return CloseResponse(
                    success=False,
                    error="Account ID is required",
                    position_id=position_id
                )
            
            logger.info(f"Closing position {position_id} on account {account_id}")
            if quantity:
                logger.info(f"Closing {quantity} contracts (partial close)")
            
            headers = {
                "accept": "text/plain",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.auth.get_token()}"
            }
            
            # Get the contract ID for this position
            positions = await self.get_positions(account_id=account_id)
            contract_id = None
            for pos in positions:
                if str(pos.position_id) == str(position_id):
                    contract_id = pos.raw_data.get('contractId') if pos.raw_data else None
                    break
            
            if not contract_id:
                # Try to get from position details
                position_details = await self.get_position_details(position_id, account_id=account_id)
                if position_details and position_details.raw_data:
                    contract_id = position_details.raw_data.get('contractId')
            
            if not contract_id:
                return CloseResponse(
                    success=False,
                    error=f"Could not find contract ID for position {position_id}",
                    position_id=position_id
                )
            
            close_data = {
                "accountId": int(account_id),
                "contractId": contract_id
            }
            
            if quantity:
                close_data["quantity"] = quantity
            
            response = await self._make_request("POST", "/api/Position/closeContract", data=close_data, headers=headers)
            
            # Log the full response for debugging
            logger.info(f"Position close API response: {response}")
            
            if "error" in response:
                logger.error(f"Failed to close position: {response['error']}")
                return CloseResponse(
                    success=False,
                    error=response['error'],
                    position_id=position_id,
                    raw_response=response
                )
            
            # Check if response indicates success - some APIs return 200 OK even on failure
            # Verify by checking if the response contains success indicators
            if isinstance(response, dict):
                # Check for explicit success/failure fields
                if response.get("success") is False:
                    error_msg = response.get("message") or response.get("error") or "Position close failed"
                    logger.error(f"Position close API returned failure: {error_msg}")
                    return CloseResponse(
                        success=False,
                        error=error_msg,
                        position_id=position_id,
                        raw_response=response
                    )
            
            logger.info(f"✅ Position close API call successful for {position_id}")
            return CloseResponse(
                success=True,
                position_id=position_id,
                message="Position closed successfully",
                raw_response=response
            )
            
        except Exception as e:
            logger.error(f"Failed to close position: {str(e)}")
            return CloseResponse(
                success=False,
                error=str(e),
                position_id=position_id
            )
    
    async def _close_position_rust(
        self,
        position_id: str,
        account_id: Optional[str]
    ) -> CloseResponse:
        """Close position using Rust executor (full close only)."""
        import time
        start_time = time.perf_counter()
        
        await self.auth.ensure_valid_token()
        
        if not account_id:
            return CloseResponse(
                success=False,
                error="Account ID is required",
                position_id=position_id
            )
        
        token = self.auth.get_token()
        self._query_executor.set_token(token)
        
        rust_result = await self._query_executor.close_position(
            position_id=position_id,
            account_id=int(account_id)
        )
        
        elapsed_ms = (time.perf_counter() - start_time) * 1000
        logger.info(f"⚡ Rust close_position execution: {elapsed_ms:.2f}ms")
        
        return CloseResponse(
            success=rust_result.get('success', False),
            position_id=rust_result.get('position_id') or position_id,
            message=rust_result.get('message'),
            error=rust_result.get('error')
        )
    
    async def flatten_all_positions(
        self,
        account_id: Optional[str] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Close all open positions (flatten account).
        
        Args:
            account_id: Account ID
            **kwargs: Additional parameters (interactive, etc.)
            
        Returns:
            Dictionary with flatten operation results
        """
        try:
            await self.auth.ensure_valid_token()
            
            if not account_id:
                return {"success": False, "error": "Account ID is required"}
            
            logger.info(f"Flattening all positions on account {account_id}")
            
            # Get all open positions - use get_open_positions for consistency
            positions = await self.get_open_positions(account_id=account_id)
            logger.info(f"Found {len(positions)} open positions to close")
            if not positions:
                logger.info("No open positions found to close")
                # Still need to cancel orders even if no positions
                positions = []  # Will be handled below
            else:
                # Log position details
                for pos in positions:
                    logger.info(f"  Position: {pos.position_id} - {pos.symbol} - Quantity: {pos.quantity} - Side: {pos.side}")
            
            # Close each position
            closed_positions = []
            failed_positions = []
            
            for position in positions:
                try:
                    logger.info(f"Attempting to close position {position.position_id} ({position.symbol}, {position.quantity} {position.side})")
                    result = await self.close_position(
                        position_id=position.position_id,
                        account_id=account_id
                    )
                    
                    if result.success:
                        closed_positions.append(position.position_id)
                        logger.info(f"✅ Successfully closed position {position.position_id}")
                    else:
                        failed_positions.append({
                            "id": position.position_id,
                            "error": result.error or "Unknown error"
                        })
                        logger.error(f"❌ Failed to close position {position.position_id}: {result.error}")
                except Exception as e:
                    logger.error(f"❌ Exception closing position {position.position_id}: {e}", exc_info=True)
                    failed_positions.append({
                        "id": position.position_id,
                        "error": str(e)
                    })
            
            # Cancel all open orders
            orders = await self.get_open_orders(account_id=account_id)
            canceled_orders = []
            failed_orders = []
            
            for order in orders:
                try:
                    order_id = str(order.get('id', ''))
                    if not order_id:
                        continue
                    
                    result = await self.cancel_order(order_id=order_id, account_id=account_id)
                    
                    if result.success:
                        canceled_orders.append(order_id)
                        logger.info(f"Successfully canceled order {order_id}")
                    else:
                        failed_orders.append({
                            "id": order_id,
                            "error": result.error
                        })
                except Exception as e:
                    logger.error(f"Exception canceling order: {e}")
            
            # Verify positions were actually closed by checking again.
            # The API can be eventually-consistent; retry briefly before marking a close as failed.
            if closed_positions:
                import asyncio
                still_open = list(closed_positions)
                for attempt in range(3):
                    await asyncio.sleep(0.5 + attempt * 0.5)  # 0.5s, 1.0s, 1.5s
                    remaining_positions = await self.get_open_positions(account_id=account_id)
                    remaining_ids = {p.position_id for p in remaining_positions}
                    still_open = [pid for pid in closed_positions if pid in remaining_ids]
                    if not still_open:
                        break
                
                if still_open:
                    logger.warning(f"⚠️  Position close reported success but positions still open after retries: {still_open}")
                    for pid in still_open:
                        if pid in closed_positions:
                            closed_positions.remove(pid)
                        failed_positions.append({
                            "id": pid,
                            "error": "Position close reported success but position still exists (after retries)"
                        })
            
            result = {
                "success": len(failed_positions) == 0 and len(failed_orders) == 0,
                "closed_positions": closed_positions,
                "canceled_orders": canceled_orders,
                "failed_positions": failed_positions,
                "failed_orders": failed_orders,
                "positions_count": len(closed_positions),
                "orders_count": len(canceled_orders)
            }
            
            logger.info(f"Flatten complete: {len(closed_positions)} positions closed, {len(canceled_orders)} orders canceled")
            if failed_positions:
                logger.warning(f"⚠️  {len(failed_positions)} positions failed to close")
            if failed_orders:
                logger.warning(f"⚠️  {len(failed_orders)} orders failed to cancel")
            
            # Handle edge case where positions list is empty but we need to check orders
            if not positions:
                # Get and cancel all open orders even if no positions
                logger.info("No positions found, checking for orders to cancel")
                orders = await self.get_open_orders(account_id=account_id)
                canceled_orders = []
                failed_orders = []

                for order in orders:
                    try:
                        order_id = str(order.get('id', ''))
                        if not order_id:
                            continue

                        result = await self.cancel_order(order_id=order_id, account_id=account_id)

                        if result.success:
                            canceled_orders.append(order_id)
                            logger.info(f"Successfully canceled order {order_id}")
                        else:
                            failed_orders.append({
                                "id": order_id,
                                "error": result.error
                            })
                    except Exception as e:
                        logger.error(f"Exception canceling order: {e}")

                result = {
                    "success": True,
                    "closed_positions": [],
                    "canceled_orders": canceled_orders,
                    "failed_positions": [],
                    "failed_orders": failed_orders,
                    "positions_count": 0,
                    "orders_count": len(canceled_orders)
                }
                
                logger.info(f"Flatten complete: No positions, {len(canceled_orders)} orders canceled")
                return result

            logger.info(f"Flatten complete: {len(closed_positions)} positions closed, {len(canceled_orders)} orders canceled")
            return result

        except Exception as e:
            logger.error(f"Failed to flatten positions: {str(e)}")
            return {"success": False, "error": str(e)}
    
    async def get_linked_orders(
        self,
        position_id: str,
        account_id: Optional[str] = None,
        all_orders: Optional[List[Dict[str, Any]]] = None,
        position_data: Optional[Dict[str, Any]] = None,
        **kwargs
    ) -> List[Dict[str, Any]]:
        """
        Get linked orders (stop loss, take profit) for a position.

        Args:
            position_id: Position ID
            account_id: Account ID
            all_orders: Pre-fetched orders (optimization to avoid re-fetching)
            position_data: Pre-fetched position data (optimization to avoid re-querying)
            **kwargs: Additional parameters

        Returns:
            List of linked orders
        """
        try:
            await self.auth.ensure_valid_token()

            if not account_id:
                logger.error("Account ID is required for get_linked_orders")
                return []

            logger.debug(f"Fetching linked orders for position {position_id}")

            # Get the position details to know its contract and side
            if position_data:
                # Use pre-fetched position data (optimization)
                position = position_data
            else:
                # Fallback: fetch positions
                positions = await self.get_positions(account_id=account_id)
                position = None
                for pos in positions:
                    if str(pos.position_id) == str(position_id):
                        position = pos
                        break

            if not position:
                logger.warning(f"Position {position_id} not found")
                return []

            # Get all open orders for the account (or use pre-fetched)
            if all_orders is None:
                # Fallback: fetch orders (slower path)
                all_orders = await self.get_open_orders(account_id=account_id)
                logger.debug("Fetched orders for single position (consider batching for better performance)")
            else:
                logger.debug(f"Using pre-fetched orders (optimization: batch query)")
            
            # Filter orders linked to this position using multiple criteria
            linked_orders = []
            for order in all_orders:
                # Method 1: Check positionId field (most reliable if present)
                order_position_id = order.get('positionId') or order.get('position_id')
                if order_position_id and str(order_position_id) == str(position_id):
                    linked_orders.append(order)
                    logger.debug(f"Matched order {order.get('id')} via positionId")
                    continue
                
                # Method 2: Check customTag for AutoBracket
                custom_tag = order.get('customTag', '') or ''
                if custom_tag and 'AutoBracket' in custom_tag:
                    # Extract position ID from tag if present (format: AutoBracket-{positionId}-SL or -TP)
                    if f"-{position_id}-" in custom_tag or custom_tag.endswith(f"-{position_id}"):
                        linked_orders.append(order)
                        logger.debug(f"Matched order {order.get('id')} via AutoBracket tag")
                        continue
                
                # Method 3: Match by contract + order type (stop/limit) + opposite side
                # This catches bracket orders that don't have explicit linking
                order_contract = order.get('contractId', '')
                # position may be a Position object or a dict (depending on caller)
                if isinstance(position, dict):
                    position_contract = position.get('contractId') or position.get('contract_id') or ''
                    position_side = position.get('side')
                    # Normalize if numeric side
                    if isinstance(position_side, int):
                        position_side = 'LONG' if position_side == 0 else 'SHORT'
                else:
                    position_contract = position.raw_data.get('contractId', '') if getattr(position, 'raw_data', None) else ''
                    position_side = position.side
                
                if order_contract and position_contract and order_contract == position_contract:
                    order_type = order.get('type', 0)
                    order_side = order.get('side', -1)
                    
                    # For LONG positions: stop loss is SELL STOP (side=1, type=4), TP is SELL LIMIT (side=1, type=1)
                    # For SHORT positions: stop loss is BUY STOP (side=0, type=4), TP is BUY LIMIT (side=0, type=1)
                    is_opposite_side = False
                    if position_side == 'LONG' and order_side == 1:  # SELL orders for LONG position
                        is_opposite_side = True
                    elif position_side == 'SHORT' and order_side == 0:  # BUY orders for SHORT position
                        is_opposite_side = True
                    
                    # Only link if it's a stop or limit order on the opposite side
                    if is_opposite_side and order_type in [1, 4]:  # 1=Limit, 4=Stop
                        linked_orders.append(order)
                        logger.debug(f"Matched order {order.get('id')} via contract+type+side heuristic")
            
            logger.debug(f"Found {len(linked_orders)} linked orders for position {position_id}")
            return linked_orders
            
        except Exception as e:
            logger.error(f"Failed to get linked orders for position {position_id}: {e}")
            return []
    
    # ==================== Market Data Interface Implementation ====================
    
    async def get_historical_data(
        self,
        symbol: str,
        timeframe: str = "1m",
        limit: int = 100,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        _skip_aggregation: bool = False,  # Internal flag to prevent recursion
        _use_cache: bool = True,  # Enable caching to prevent duplicate requests
        **kwargs
    ) -> List[Bar]:
        """
        Get historical bar data with caching to prevent duplicate requests.
        
        Args:
            symbol: Trading symbol
            timeframe: Timeframe (e.g., "1m", "5m", "1h")
            limit: Maximum number of bars
            start_time: Start time (optional)
            end_time: End time (optional)
            _skip_aggregation: Internal flag to prevent recursion
            _use_cache: Whether to use cache (default: True)
            **kwargs: Additional parameters
            
        Returns:
            List of Bar objects
        """
        try:
            await self.auth.ensure_valid_token()
            
            symbol_up = symbol.upper()
            
            # Check cache first (only for non-aggregation calls to avoid recursion issues)
            cache_key = None
            if _use_cache and not _skip_aggregation:
                contract_id_override = kwargs.get("contract_id_override")
                continuous_daily = kwargs.get("continuous_daily")
                include_partial_daily = kwargs.get("include_partial_daily")
                cache_key = (
                    symbol_up,
                    timeframe,
                    limit,
                    start_time.isoformat() if start_time else None,
                    end_time.isoformat() if end_time else None,
                    contract_id_override,
                    continuous_daily,
                    include_partial_daily
                )
                
                async with self._cache_lock:
                    if cache_key in self._historical_cache:
                        cached_bars, cache_timestamp = self._historical_cache[cache_key]
                        age = time.time() - cache_timestamp
                        if age < self._cache_ttl_seconds:
                            logger.debug(f"📦 Cache HIT for {symbol_up} {timeframe} {limit} bars (age: {age:.1f}s)")
                            return cached_bars
                        else:
                            # Cache expired, remove it
                            del self._historical_cache[cache_key]
                            logger.debug(f"📦 Cache EXPIRED for {symbol_up} {timeframe} {limit} bars (age: {age:.1f}s)")

                if self._parquet_cache_enabled:
                    pq_bars = await self._parquet_disk_load(cache_key)
                    if pq_bars:
                        logger.debug(
                            "📦 Parquet disk HIT for %s %s (%d bars)",
                            symbol_up,
                            timeframe,
                            len(pq_bars),
                        )
                        async with self._cache_lock:
                            self._historical_cache[cache_key] = (pq_bars, time.time())
                        return pq_bars

                async with self._cache_lock:
                    # Check if there's a pending request for the same key (prevent duplicate requests)
                    if cache_key in self._pending_requests:
                        # Wait for the pending request to complete
                        logger.debug(f"⏳ Waiting for pending request for {symbol_up} {timeframe} {limit} bars")
                        try:
                            return await self._pending_requests[cache_key]
                        except Exception as e:
                            logger.warning(f"Pending request failed: {e}")
                            # Fall through to make new request
                            if cache_key in self._pending_requests:
                                del self._pending_requests[cache_key]
            
            # Create a wrapper function that will be called to fetch data
            async def _fetch_data() -> List[Bar]:
                """Internal function to fetch data - will be cached or awaited if pending."""
                bars = await self._fetch_historical_data_impl(
                    symbol_up, timeframe, limit, start_time, end_time, _skip_aggregation, **kwargs
                )
                if (
                    self._parquet_cache_enabled
                    and cache_key is not None
                    and bars
                ):
                    await self._parquet_disk_save(cache_key, bars)
                return bars
            
            # If caching enabled, create task and track it
            if _use_cache and not _skip_aggregation and cache_key:
                async with self._cache_lock:
                    if cache_key not in self._pending_requests:
                        # Create new request task
                        request_task = asyncio.create_task(_fetch_data())
                        self._pending_requests[cache_key] = request_task
                    else:
                        # Use existing pending request
                        request_task = self._pending_requests[cache_key]
                
                # Wait for the request to complete
                bars = await request_task
                
                # Store in cache and clean up
                async with self._cache_lock:
                    self._historical_cache[cache_key] = (bars, time.time())
                    if cache_key in self._pending_requests:
                        del self._pending_requests[cache_key]
                    # Clean up old cache entries (keep last 100)
                    if len(self._historical_cache) > 100:
                        sorted_entries = sorted(
                            self._historical_cache.items(),
                            key=lambda x: x[1][1]  # Sort by timestamp
                        )
                        for key in sorted_entries[:len(sorted_entries) - 100]:
                            del self._historical_cache[key[0]]
                
                return bars
            else:
                # No caching - fetch directly
                return await _fetch_data()
            
        except Exception as e:
            logger.error(f"Failed to fetch historical data: {str(e)}")
            # Clean up pending request on error
            if _use_cache and not _skip_aggregation and cache_key:
                async with self._cache_lock:
                    if cache_key in self._pending_requests:
                        del self._pending_requests[cache_key]
            return []
    
    async def _fetch_historical_data_impl(
        self,
        symbol: str,
        timeframe: str = "1m",
        limit: int = 100,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        _skip_aggregation: bool = False,
        contract_id_override: Optional[str] = None,
        _continuous_roll: bool = False,
        **kwargs
    ) -> List[Bar]:
        """
        Internal implementation to fetch historical data.
        This contains the actual API call logic.
        """
        try:
            symbol_up = symbol.upper()
            timeframe_lower = timeframe.lower()
            continuous_daily = kwargs.pop("continuous_daily", None)
            include_partial_daily = kwargs.pop("include_partial_daily", None)

            if continuous_daily is None:
                continuous_daily = (timeframe_lower == "1d")
            if include_partial_daily is None:
                include_partial_daily = (timeframe_lower == "1d")

            # Get contract ID
            if contract_id_override:
                contract_id = contract_id_override
            else:
                try:
                    contract_id = self.contract_manager.get_contract_id(symbol_up)
                except ValueError:
                    # Contract cache might be empty - try fetching contracts
                    logger.info("Contract cache empty, fetching contracts...")
                    await self.get_available_contracts(use_cache=False)
                    contract_id = self.contract_manager.get_contract_id(symbol_up)
            
            # Parse timeframe to API format
            # Simplified parser - supports common timeframes
            unit_map = {
                's': 1,  # Seconds
                'm': 2,  # Minutes
                'h': 3,  # Hours
                'd': 4,  # Days
                'w': 5,  # Weeks
                'M': 6   # Months
            }
            
            unit = None
            unit_number = 1
            
            # Parse timeframe (e.g., "1m", "5m", "1h", "1d")
            if timeframe_lower.endswith('s'):
                unit = 1
                unit_number = int(timeframe_lower[:-1]) if timeframe_lower[:-1].isdigit() else 1
            elif timeframe_lower.endswith('m'):
                unit = 2
                unit_number = int(timeframe_lower[:-1]) if timeframe_lower[:-1].isdigit() else 1
            elif timeframe_lower.endswith('h'):
                unit = 3
                unit_number = int(timeframe_lower[:-1]) if timeframe_lower[:-1].isdigit() else 1
            elif timeframe_lower.endswith('d'):
                unit = 4
                unit_number = int(timeframe_lower[:-1]) if timeframe_lower[:-1].isdigit() else 1
            elif timeframe_lower.endswith('w'):
                unit = 5
                unit_number = int(timeframe_lower[:-1]) if timeframe_lower[:-1].isdigit() else 1
            elif timeframe_lower.endswith('M'):
                unit = 6
                unit_number = int(timeframe_lower[:-1]) if timeframe_lower[:-1].isdigit() else 1
            else:
                logger.error(f"Invalid timeframe: {timeframe}")
                return []
            
            # Calculate time range
            from datetime import datetime, timedelta, timezone
            
            # CRITICAL: Adjust end_time to last market close if market is currently closed
            # This ensures we don't request data from weekends/after-hours when market wasn't open
            def _get_last_market_close() -> datetime:
                """Get the last market close time, accounting for weekends and daily breaks.
                
                Futures market hours (EST):
                - Sunday 6pm - Friday 5pm (with daily break 5pm-6pm Mon-Thu)
                - Closed: Friday 5pm - Sunday 6pm
                """
                import pytz
                et_tz = pytz.timezone('US/Eastern')
                now_et = datetime.now(et_tz)
                daily_close_hour = 17  # 5pm EST
                weekend_open_hour = 18  # 6pm EST Sunday
                
                weekday = now_et.weekday()  # 0=Monday, 1=Tuesday, ..., 6=Sunday
                current_hour = now_et.hour
                
                # Check if market is currently open (Sunday 6pm - Friday 5pm, excluding daily breaks)
                # Sunday 6pm onwards (including Monday morning before 5pm)
                if weekday == 6 and current_hour >= weekend_open_hour:
                    # Sunday evening session - market is open
                    return datetime.now(timezone.utc)
                # Monday morning (before daily close at 5pm) - still in Sunday evening session
                elif weekday == 0 and current_hour < daily_close_hour:
                    # Market is open (Sunday evening session continues until Monday 5pm)
                    return datetime.now(timezone.utc)
                # Monday-Thursday: market open except during daily break (5pm-6pm)
                elif weekday in [0, 1, 2, 3]:  # Monday-Thursday
                    if current_hour >= weekend_open_hour or current_hour < daily_close_hour:
                        # Market is open (after 6pm or before 5pm)
                        return datetime.now(timezone.utc)
                    else:
                        # Daily break (5pm-6pm) - use today's 5pm
                        last_close = now_et.replace(hour=daily_close_hour, minute=0, second=0, microsecond=0)
                        return last_close.astimezone(timezone.utc)
                # Friday: market open until 5pm
                elif weekday == 4 and current_hour < daily_close_hour:
                    # Friday before 5pm - market is open
                    return datetime.now(timezone.utc)
                # Friday after 5pm - market closed
                elif weekday == 4 and current_hour >= daily_close_hour:
                    last_close = now_et.replace(hour=daily_close_hour, minute=0, second=0, microsecond=0)
                    return last_close.astimezone(timezone.utc)
                # Saturday or Sunday before 6pm - market closed, use last Friday 5pm
                elif weekday == 5 or (weekday == 6 and current_hour < weekend_open_hour):
                    # Calculate days back to Friday
                    if weekday == 5:  # Saturday
                        days_back = 1
                    else:  # Sunday before 6pm
                        days_back = 2
                    last_close = now_et.replace(hour=daily_close_hour, minute=0, second=0, microsecond=0) - timedelta(days=days_back)
                    return last_close.astimezone(timezone.utc)
                else:
                    # Fallback: use current time
                    return datetime.now(timezone.utc)
            
            # CRITICAL: Always use current time as end_time to ensure we get data up to the current moment
            # This mirrors the original trading_bot implementation: end_time defaults to "now".
            if end_time is None:
                end_time = datetime.now(timezone.utc)
                logger.debug(f"Using current time as end_time: {end_time}")
            elif end_time.tzinfo is None:
                end_time = end_time.replace(tzinfo=timezone.utc)
            
            # Adjust end_time to last market close if market is currently closed
            # This prevents requesting data from weekends/after-hours periods
            last_close = _get_last_market_close()
            if end_time > last_close:
                logger.debug(f"Market is closed. Adjusting end_time from {end_time} to last market close: {last_close}")
                end_time = last_close
            elif end_time < last_close:
                # If end_time is before last_close, it's in the past - that's fine, use it as-is
                logger.debug(f"end_time {end_time} is before last market close {last_close} - using as-is")
            
            # For short timeframes (seconds, 1-10m), ensure end_time is very recent to get latest data
            # when the request is "live" (end_time within last 24h). Do NOT overwrite when end_time
            # is far in the past (e.g. analyze_date, backtest) or when _skip_aggregation (1m for 5m/1h).
            # Skip when _skip_aggregation: we're fetching 1m for aggregation and must respect end_time.
            timeframe_lower = timeframe.lower()
            if not _skip_aggregation and (timeframe_lower.endswith("s") or timeframe_lower in ["1m", "2m", "3m", "5m", "10m"]):
                current_time = datetime.now(timezone.utc)
                time_diff = (current_time - end_time).total_seconds()
                # Do not overwrite when end_time is far in the past (analyze_date / backtest)
                if time_diff <= 86400:  # 24 hours
                    # Get the threshold based on timeframe (slightly stale = freshen)
                    if timeframe_lower.endswith("s"):
                        threshold = max(int(timeframe_lower[:-1]) * 2, 60)  # At least 1 minute for seconds
                    elif timeframe_lower in ["1m", "2m"]:
                        threshold = 120  # 2 minutes
                    else:
                        threshold = 600  # 10 minutes for 5m/10m
                    # If end_time is older than threshold but within 24h, update to fresh
                    if time_diff > threshold:
                        potential_end = min(current_time, last_close)
                        if potential_end > end_time:
                            logger.info(
                                f"📊 {timeframe} timeframe: end_time is {time_diff:.0f}s old, updating to {potential_end} for fresh data"
                            )
                            end_time = potential_end
                else:
                    logger.debug(
                        f"📊 {timeframe} timeframe: end_time is {time_diff:.0f}s in the past (historical), not overwriting"
                    )
            
            # Track if start_time was originally provided (date range mode)
            original_start_time_provided = start_time is not None
            
            # ORIGINAL LOOKBACK LOGIC (ported from legacy trading_bot.get_historical_data):
            # - For seconds:   at least 3–5 days of data (2000+ bars)
            # - For 1–10m:     enough to cover multiple trading days (1500+ bars)
            # - For 15m+:      smaller multiplier
            if start_time is None:
                # Calculate start time based on limit and timeframe
                # Estimate time delta per bar
                if unit == 1:  # Seconds
                    delta_per_bar = timedelta(seconds=unit_number)
                elif unit == 2:  # Minutes
                    delta_per_bar = timedelta(minutes=unit_number)
                elif unit == 3:  # Hours
                    delta_per_bar = timedelta(hours=unit_number)
                elif unit == 4:  # Days
                    delta_per_bar = timedelta(days=unit_number)
                elif unit == 5:  # Weeks
                    delta_per_bar = timedelta(weeks=unit_number)
                else:  # Months
                    delta_per_bar = timedelta(days=30 * unit_number)
                
                # Determine how many bars' worth of time to look back, based on timeframe
                timeframe_lower = timeframe.lower()
                if timeframe_lower.endswith("s"):  # Seconds timeframes
                    # Go back at least 3–5 days worth of data
                    lookback_bars = max(limit * 20, 2000)
                elif timeframe_lower in ["1m", "2m", "3m", "5m", "10m"]:
                    # For sub‑15m minutes: capture at least 2–3 trading days
                    lookback_bars = max(limit * 15, 1500)
                else:
                    # For 15m and above, use a standard multiplier
                    lookback_bars = max(limit * 3, limit + 100)
                
                start_time = end_time - (delta_per_bar * lookback_bars)
                
                # Ensure start_time doesn't go into weekend/closed periods
                # The end_time has already been adjusted to last market close, so we need to ensure
                # start_time is also within market hours (9:30 AM - 5:00 PM EST, Mon-Fri)
                import pytz
                et_tz = pytz.timezone('US/Eastern')
                start_et = start_time.astimezone(et_tz)
                
                # If start_time is on weekend (Saturday=5, Sunday=6), adjust to last Friday 9:30 AM
                start_weekday = start_et.weekday()
                if start_weekday >= 5:  # Saturday or Sunday
                    days_back = start_weekday - 4  # 1 for Saturday, 2 for Sunday
                    friday_open = (start_et - timedelta(days=days_back)).replace(hour=9, minute=30, second=0, microsecond=0)
                    friday_open_utc = friday_open.astimezone(timezone.utc)
                    logger.debug(f"start_time is on weekend, adjusting from {start_time} to {friday_open_utc} (last Friday market open)")
                    start_time = friday_open_utc
                    start_et = start_time.astimezone(et_tz)  # Recalculate after adjustment
                
                # If start_time is before market open (9:30 AM) or after market close (5:00 PM)
                start_hour = start_et.hour
                if start_hour < 9 or (start_hour == 9 and start_et.minute < 30) or start_hour >= 17:
                    # Adjust to market open of that day (or previous trading day if after close)
                    if start_hour >= 17:
                        # After market close - use today's market open, or previous day if it's Monday
                        if start_et.weekday() == 0:  # Monday
                            # Use last Friday's market open
                            market_open = (start_et - timedelta(days=3)).replace(hour=9, minute=30, second=0, microsecond=0)
                        else:
                            # Use today's market open
                            market_open = start_et.replace(hour=9, minute=30, second=0, microsecond=0)
                    else:
                        # Before market open - use today's market open
                        market_open = start_et.replace(hour=9, minute=30, second=0, microsecond=0)
                    market_open_utc = market_open.astimezone(timezone.utc)
                    logger.debug(f"start_time is outside market hours, adjusting from {start_time} to {market_open_utc}")
                    start_time = market_open_utc
                
                lookback_days = (end_time - start_time).total_seconds() / 86400
                logger.debug(
                    f"Bar count mode: {lookback_bars} bars worth of time = {lookback_days:.1f} days "
                    f"for {symbol_up} {timeframe}"
                )
            elif start_time.tzinfo is None:
                # Date range mode: respect explicit start_time, just normalize tz
                start_time = start_time.replace(tzinfo=timezone.utc)
            
            # Format timestamps - API expects ISO 8601 format with Z suffix for UTC
            start_str = start_time.strftime("%Y-%m-%dT%H:%M:%S") + "Z"
            end_str = end_time.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
            
            headers = {
                "Authorization": f"Bearer {self.auth.get_token()}",
                "Content-Type": "application/json",
                "accept": "text/plain"
            }
            
            # API expects unit as AggregateBarUnit (integer enum), not string. 1=Second, 2=Minute, 3=Hour, 4=Day, 5=Week, 6=Month.
            is_1d = (unit == 4 and unit_number == 1)
            if is_1d:
                logger.debug("📊 1d timeframe: using API unit=4 (Day), unitNumber=1 for deep history retention")

            # Determine API limit based on mode
            # Date range mode: fetch ALL bars between dates (up to API max)
            # Bar count mode: request extra bars to account for gaps/closures
            if original_start_time_provided:
                # Date range mode: calculate estimated max bars and use API max
                # Estimate: (end_time - start_time) / bar_duration, with buffer for gaps
                time_range_seconds = (end_time - start_time).total_seconds()
                if unit == 1:  # Seconds
                    estimated_bars = int(time_range_seconds / unit_number) + 1000
                elif unit == 2:  # Minutes
                    estimated_bars = int(time_range_seconds / (unit_number * 60)) + 1000
                elif unit == 3:  # Hours
                    estimated_bars = int(time_range_seconds / (unit_number * 3600)) + 1000
                elif unit == 4:  # Days
                    estimated_bars = int(time_range_seconds / (unit_number * 86400)) + 100
                elif unit == 5:  # Weeks
                    estimated_bars = int(time_range_seconds / (unit_number * 604800)) + 50
                else:  # Months
                    estimated_bars = int(time_range_seconds / (unit_number * 2592000)) + 50
                
                # Use API max (20000) to ensure we get all bars
                api_limit = min(estimated_bars, 20000)
                logger.debug(f"📅 Date range mode: requesting up to {api_limit} {timeframe} bars between {start_str} and {end_str}")
            else:
                # Bar count mode: request extra bars to account for gaps/closures
                api_limit = min(limit * 3, 20000)
                logger.debug(f"📊 Bar count mode: requesting {limit} {timeframe} bars (API limit: {api_limit})")
            
            # If requested, stitch daily history across multiple contracts (continuous history)
            if continuous_daily and timeframe_lower == "1d" and not _continuous_roll and not contract_id_override:
                # History proxy (optional): for some micros we can use a larger contract.
                # NOTE: For MGC specifically, TradingView parity requires using MGC contract months directly
                # (MGC.G26, MGC.J26, ...) rather than GC, because tiny vendor differences compound.
                history_symbol = self.HISTORY_PROXY_MAP.get(symbol_up, symbol_up)
                use_history_proxy = history_symbol != symbol_up
                if use_history_proxy:
                    logger.info(f"📊 Using {history_symbol} history for {symbol_up} (history proxy)")
                
                try:
                    contract_ids = self.contract_manager.get_contract_ids_for_symbol(history_symbol, ascending=True)
                except Exception as e:
                    logger.warning(f"Failed to get contract list for {symbol_up}: {e}")
                    contract_ids = [contract_id]
                
                if contract_ids:
                    logger.info(
                        f"📅 Continuous daily: {symbol_up} contracts={len(contract_ids)} "
                        f"first={contract_ids[0]} last={contract_ids[-1]}"
                    )
                else:
                    logger.warning(f"📅 Continuous daily: no contracts found for {symbol_up}, using current only")
                    contract_ids = [contract_id]

                # For history proxy (MGC→GC), fetch 1h bars and aggregate to daily with session-aligned logic
                # TradingView continuous symbols roll across contract months. For best parity:
                # - Stitch *daily* bars across inferred contract months
                # - Use a deterministic roll rule (see below)
                # - Optionally build a partial "today" bar from 1h if the API hasn't emitted the latest 1d bar
                #
                # We run this path for:
                # - history proxies (history_symbol != symbol_up)
                # - MGC specifically (needs multi-contract stitching even without proxy to match TradingView)
                if use_history_proxy or symbol_up == "MGC":
                    logger.info(f"📊 Continuous daily stitching for {symbol_up}: base_symbol={history_symbol}")

                    # Infer likely contract IDs around the currently available contract.
                    # Example for GC: CON.F.US.GCE.J26 (Apr) and CON.F.US.GCE.G26 (Feb) both return data.
                    month_cycle = "GJMQZ"  # Gold/Metals standard months (best-effort)
                    month_to_num = {"F": 1, "G": 2, "H": 3, "J": 4, "K": 5, "M": 6, "N": 7, "Q": 8, "U": 9, "V": 10, "X": 11, "Z": 12}

                    def _parse_suffix(sfx: str) -> Optional[tuple]:
                        sfx = str(sfx or "").strip().upper()
                        if len(sfx) == 3 and sfx[0].isalpha() and sfx[1:].isdigit():
                            return (sfx[0], int(sfx[1:]))
                        return None

                    def _suffix_key(sfx: str) -> tuple:
                        parsed = _parse_suffix(sfx)
                        if not parsed:
                            return (9999, 12)
                        m, yy = parsed
                        year = 2000 + yy
                        return (year, month_to_num.get(m, 12))

                    def _prev_suffix(cur_sfx: str) -> Optional[str]:
                        parsed = _parse_suffix(cur_sfx)
                        if not parsed:
                            return None
                        cur_m, cur_yy = parsed
                        if cur_m not in month_cycle:
                            return None
                        idx = month_cycle.index(cur_m)
                        prev_m = month_cycle[idx - 1] if idx > 0 else month_cycle[-1]
                        prev_yy = cur_yy if idx > 0 else (cur_yy - 1)
                        return f"{prev_m}{prev_yy:02d}"

                    # Choose a base contract to infer prefix/suffix from
                    base_cid = contract_ids[-1] if contract_ids else contract_id
                    base_parts = str(base_cid).split(".")
                    # Expect CON.F.US.<SYMBOL>.<SUFFIX>
                    base_suffix = base_parts[-1] if len(base_parts) >= 2 else ""
                    symbol_id = ".".join(base_parts[1:4]) if len(base_parts) >= 5 else None  # e.g. F.US.GCE
                    cid_prefix = f"CON.{symbol_id}." if symbol_id else None

                    candidate_cids: List[str] = []
                    if cid_prefix and base_suffix:
                        prev1 = _prev_suffix(base_suffix)
                        prev2 = _prev_suffix(prev1) if prev1 else None
                        for sfx in [prev2, prev1, base_suffix]:
                            if sfx:
                                candidate_cids.append(f"{cid_prefix}{sfx}")
                    else:
                        # Fallback to current contract id only
                        candidate_cids = [str(base_cid)]

                    # De-dup while preserving order
                    seen = set()
                    candidate_cids = [c for c in candidate_cids if not (c in seen or seen.add(c))]

                    per_contract_limit = max(limit * 6, 120)  # extra buffer for overlap/roll detection / partial-day stitching

                    # Collect bars per contract by ET date (TradingView-style daily date).
                    by_contract_day: Dict[str, Dict[date, Bar]] = {}
                    et_tz = self._get_et_tz()

                    for cand_cid in candidate_cids:
                        try:
                            bars = await self._fetch_historical_data_impl(
                                symbol=history_symbol,
                                timeframe="1d",
                                limit=per_contract_limit,
                                start_time=start_time,
                                end_time=end_time,
                                _skip_aggregation=_skip_aggregation,
                                contract_id_override=cand_cid,
                                _continuous_roll=True,
                                continuous_daily=False,
                                include_partial_daily=False,
                                **kwargs,
                            )
                        except Exception as e:
                            logger.info(f"📊 History proxy: contract {cand_cid} fetch failed: {e}")
                            continue

                        if not bars:
                            continue

                        cand_suffix = str(cand_cid).split(".")[-1]
                        cand_key = _suffix_key(cand_suffix)

                        for b in bars:
                            if not getattr(b, "timestamp", None):
                                continue
                            bar_start_dt = b.timestamp if b.timestamp.tzinfo else b.timestamp.replace(tzinfo=timezone.utc)
                            # TradingView daily candle date (for these TopStepX 1d bars) matches the
                            # ET calendar date of the bar's timestamp (typically 18:00 ET).
                            # Important: do NOT +1 day here; doing so shifts bars onto weekends and
                            # mislabels Thu/Fri sessions.
                            bar_start_et = bar_start_dt.astimezone(et_tz)
                            display_date_et = bar_start_et.date()
                            # Use 18:00 ET on the display date as the canonical timestamp (converted to UTC).
                            display_dt = datetime(
                                display_date_et.year,
                                display_date_et.month,
                                display_date_et.day,
                                18, 0, 0,
                                tzinfo=et_tz,
                            ).astimezone(timezone.utc)
                            day_key = display_date_et

                            vol = int(getattr(b, "volume", 0) or 0)
                            new_bar = Bar(
                                timestamp=display_dt,
                                open=b.open,
                                high=b.high,
                                low=b.low,
                                close=b.close,
                                volume=vol,
                                symbol=symbol_up,
                                timeframe="1d",
                                raw_data={
                                    "proxy_symbol": history_symbol,
                                    "source_contract_id": cand_cid,
                                    "source_contract_suffix": cand_suffix,
                                    "source_contract_key": cand_key,
                                    "bar_start": bar_start_dt.isoformat(),
                                    "bar_start_et": bar_start_et.isoformat(),
                                    "display_date_et": str(display_date_et),
                                },
                            )

                            by_contract_day.setdefault(cand_suffix, {})[day_key] = new_bar

                    # Determine a deterministic roll schedule that matches TradingView 1! behavior better
                    # than pure volume for Gold: roll the Feb (G) contract to Apr (J) near end of Jan.
                    # Heuristic: roll on the last business day of the month prior to delivery month, minus 1 business day.
                    def _last_business_day(year: int, month: int) -> date:
                        from calendar import monthrange
                        d = date(year, month, monthrange(year, month)[1])
                        while d.weekday() >= 5:
                            d = d - timedelta(days=1)
                        return d

                    def _roll_threshold_for_old(old_suffix: str) -> Optional[date]:
                        parsed = _parse_suffix(old_suffix)
                        if not parsed:
                            return None
                        m, yy = parsed
                        delivery_month = month_to_num.get(m)
                        if not delivery_month:
                            return None
                        year = 2000 + yy
                        # Month prior to delivery month
                        prior_month = delivery_month - 1
                        prior_year = year
                        if prior_month == 0:
                            prior_month = 12
                            prior_year -= 1
                        lbd = _last_business_day(prior_year, prior_month)
                        # Roll one business day before last business day
                        roll = lbd - timedelta(days=1)
                        while roll.weekday() >= 5:
                            roll = roll - timedelta(days=1)
                        return roll

                    # Choose old/new suffixes from inferred candidates (prev1 as old, base_suffix as new)
                    old_suffix = _prev_suffix(base_suffix) if base_suffix else None
                    new_suffix = base_suffix if base_suffix else None
                    roll_threshold = _roll_threshold_for_old(old_suffix) if old_suffix else None

                    # Build merged bars by selecting contract based on roll threshold.
                    all_days: List[date] = sorted({d for m in by_contract_day.values() for d in m.keys()})
                    merged: List[Bar] = []
                    for d in all_days:
                        chosen: Optional[Bar] = None
                        if old_suffix and new_suffix and roll_threshold:
                            # Before threshold -> old; on/after threshold -> new
                            prefer = new_suffix if d >= roll_threshold else old_suffix
                            chosen = by_contract_day.get(prefer, {}).get(d)
                            if chosen is None:
                                # Fallback to the other if missing
                                other = old_suffix if prefer == new_suffix else new_suffix
                                chosen = by_contract_day.get(other, {}).get(d)
                        else:
                            # If we couldn't infer roll, just take the newest suffix available for that day
                            for sfx in sorted(by_contract_day.keys(), key=_suffix_key, reverse=True):
                                cand = by_contract_day[sfx].get(d)
                                if cand:
                                    chosen = cand
                                    break
                        if chosen:
                            merged.append(chosen)

                    merged.sort(key=lambda b: b.timestamp if b.timestamp else datetime.min.replace(tzinfo=timezone.utc))
                    if merged:
                        # If the API hasn't emitted the most recent 1d bar yet (common around rollover/session cutoffs),
                        # build the next day bar from 1h data for the *new* contract (best match for TradingView's latest day).
                        try:
                            last_day = merged[-1].timestamp.astimezone(et_tz).date()
                            next_day = last_day + timedelta(days=1)
                            # Skip weekend days
                            while next_day.weekday() >= 5:
                                next_day = next_day + timedelta(days=1)

                            # For MGC (and some contracts), TradingView's table often includes the current
                            # partially-formed daily candle. Build it from 1h once the session has opened.
                            should_try_partial = (symbol_up == "MGC") or (len(merged) < limit)
                            if not original_start_time_provided and should_try_partial and new_suffix and cid_prefix:
                                # TradingView's latest daily candle (e.g. Fri 30) may not be present in API 1d yet.
                                # Build it from intraday bars spanning the full futures session:
                                #   (prev_day 18:00 ET) → (day 17:00 ET)
                                now_et = datetime.now(et_tz)
                                session_start_et = datetime(next_day.year, next_day.month, next_day.day, 18, 0, 0, tzinfo=et_tz) - timedelta(days=1)
                                session_end_et = datetime(next_day.year, next_day.month, next_day.day, 17, 0, 0, tzinfo=et_tz)
                                # Only build once the session is complete (after 17:00 ET on that day)
                                if now_et >= session_end_et:
                                    start_utc = session_start_et.astimezone(timezone.utc)
                                    end_utc = session_end_et.astimezone(timezone.utc)
                                    one_h = await self._fetch_historical_data_impl(
                                        symbol=history_symbol,
                                        timeframe="1h",
                                        limit=2000,
                                        start_time=start_utc,
                                        end_time=end_utc,
                                        _skip_aggregation=True,
                                        contract_id_override=f"{cid_prefix}{new_suffix}",
                                        _continuous_roll=True,
                                        continuous_daily=False,
                                        include_partial_daily=False,
                                        **kwargs,
                                    )
                                    one_h = [b for b in (one_h or []) if getattr(b, "timestamp", None)]
                                    if one_h:
                                        one_h.sort(key=lambda b: b.timestamp if b.timestamp else datetime.min.replace(tzinfo=timezone.utc))
                                        o = one_h[0].open
                                        h = max(b.high for b in one_h)
                                        l = min(b.low for b in one_h)
                                        c = one_h[-1].close
                                        v = int(sum(int(getattr(b, "volume", 0) or 0) for b in one_h))
                                        # Use 18:00 ET of the trading day as the canonical daily timestamp
                                        daily_ts = datetime(next_day.year, next_day.month, next_day.day, 18, 0, 0, tzinfo=et_tz).astimezone(timezone.utc)
                                        merged.append(Bar(
                                            timestamp=daily_ts,
                                            open=o,
                                            high=h,
                                            low=l,
                                            close=c,
                                            volume=v,
                                            symbol=symbol_up,
                                            timeframe="1d",
                                            raw_data={
                                                "proxy_symbol": history_symbol,
                                                "source_contract_id": f"{cid_prefix}{new_suffix}",
                                                "built_full_session_from_1h": True,
                                                "session_start_et": session_start_et.isoformat(),
                                                "session_end_et": session_end_et.isoformat(),
                                                "session_start_utc": start_utc.isoformat(),
                                                "session_end_utc": end_utc.isoformat(),
                                            },
                                        ))
                                        merged.sort(key=lambda b: b.timestamp if b.timestamp else datetime.min.replace(tzinfo=timezone.utc))
                        except Exception as _e:
                            logger.info(f"📊 Partial daily build skipped/failed for {symbol_up}: {_e}")

                        # Apply holiday folding to match TradingView missing-holiday-candle behavior.
                        merged = self._merge_holiday_daily_bars_into_next(merged)
                        merged.sort(key=lambda b: b.timestamp if b.timestamp else datetime.min.replace(tzinfo=timezone.utc))
                        if not original_start_time_provided and len(merged) > limit:
                            merged = merged[-limit:]
                        logger.info(
                            f"✅ History proxy: stitched {len(merged)} daily bars for {symbol_up} "
                            f"from {history_symbol} using candidates={candidate_cids}"
                        )
                        return merged

                    logger.warning(f"History proxy: no daily bars produced for {symbol_up} from {history_symbol}; falling back to 1h aggregation")
                
                # Standard flow for non-proxy symbols (or fallback if proxy fails)
                # Request more bars per contract so we get as much history as the API has (commodities
                # like MGC often return very few daily bars per contract; indices like MNQ return more).
                per_contract_limit = max(limit, 500)
                merged_by_date: Dict[datetime.date, Bar] = {}
                for cid in contract_ids:
                    bars = await self._fetch_historical_data_impl(
                        symbol=history_symbol,  # Use history symbol for fetching
                        timeframe=timeframe,
                        limit=per_contract_limit,
                        start_time=start_time,
                        end_time=end_time,
                        _skip_aggregation=_skip_aggregation,
                        contract_id_override=cid,
                        _continuous_roll=True,
                        continuous_daily=False,
                        include_partial_daily=False,
                        **kwargs
                    )
                    for b in bars or []:
                        if not getattr(b, "timestamp", None):
                            continue
                        # Use original symbol for bar objects
                        b.symbol = symbol_up
                        merged_by_date[b.timestamp.date()] = b  # newer contracts override older

                merged = sorted(merged_by_date.values(), key=lambda b: b.timestamp)

                if include_partial_daily:
                    partial_bar = await self._build_partial_daily_bar(
                        symbol=symbol,
                        end_time=end_time,
                        contract_id_override=contract_ids[-1] if contract_ids else contract_id
                    )
                    if partial_bar and (not merged or partial_bar.timestamp > merged[-1].timestamp):
                        merged.append(partial_bar)

                if not original_start_time_provided and len(merged) > limit:
                    merged = merged[-limit:]

                # Fallback for instruments (e.g. MGC) where API returns very few daily bars per contract:
                # build daily bars from 1h bars so ATR/zones can be calculated.
                min_bars_required = min(limit, 14)  # at least 14 for ATR(14)
                if len(merged) < min_bars_required and start_time is not None and end_time is not None:
                    logger.info(
                        f"📊 Only {len(merged)} daily bars for {symbol_up} (need {min_bars_required}); "
                        f"building daily from 1h bars (commodity/limited-history fallback)"
                    )
                    try:
                        # Request enough 1h bars to build at least min_bars_required days (and up to 60 days)
                        n_1h = 24 * max(limit, min_bars_required, 60)
                        # Use history_symbol if available (e.g. GC for MGC)
                        fallback_symbol = self.HISTORY_PROXY_MAP.get(symbol_up, symbol)
                        if fallback_symbol != symbol:
                            logger.info(f"📊 1h fallback: using {fallback_symbol} history for {symbol_up}")
                        one_h_bars = await self.get_historical_data(
                            symbol=fallback_symbol,
                            timeframe="1h",
                            limit=n_1h,
                            start_time=start_time,
                            end_time=end_time,
                            _skip_aggregation=True,
                            contract_id_override=None,  # Let it resolve the correct contract for fallback_symbol
                            continuous_daily=False,
                            include_partial_daily=False,
                            **kwargs
                        )
                        if one_h_bars and len(one_h_bars) >= 24:
                            one_h_bars.sort(key=lambda b: b.timestamp if b.timestamp else datetime.min.replace(tzinfo=timezone.utc))
                            bars_dict = [
                                {
                                    "timestamp": int(b.timestamp.timestamp()) if b.timestamp else 0,
                                    "time": int(b.timestamp.timestamp()) if b.timestamp else 0,
                                    "open": b.open,
                                    "high": b.high,
                                    "low": b.low,
                                    "close": b.close,
                                    "volume": getattr(b, "volume", 0) or 0,
                                }
                                for b in one_h_bars
                            ]
                            daily_dicts = self._aggregate_bars(bars_dict, "1d")
                            if daily_dicts:
                                merged = []
                                for d in daily_dicts:
                                    dt = datetime.fromtimestamp(d["timestamp"], tz=timezone.utc)
                                    merged.append(Bar(
                                        timestamp=dt,
                                        open=float(d["open"]),
                                        high=float(d["high"]),
                                        low=float(d["low"]),
                                        close=float(d["close"]),
                                        volume=int(d.get("volume", 0)),
                                        symbol=symbol_up,
                                        timeframe="1d",
                                    ))
                                if os.getenv("MERGE_DAILY_HOLIDAY_BARS", "true").lower() in ("1", "true", "yes", "on"):
                                    merged = self._merge_holiday_daily_bars_into_next(merged)
                                merged.sort(key=lambda b: b.timestamp if b.timestamp else datetime.min.replace(tzinfo=timezone.utc))
                                if not original_start_time_provided and len(merged) > limit:
                                    merged = merged[-limit:]
                                logger.info(f"✅ Built {len(merged)} daily bars from 1h for {symbol_up}")
                    except Exception as fallback_err:
                        logger.warning(f"1h→daily fallback failed for {symbol_up}: {fallback_err}")

                return merged

            # API expects RetrieveBarRequest at body root. unit is an AggregateBarUnit enum value (integer).
            bars_request = {
                "contractId": contract_id,
                "live": False,
                "startTime": start_str,
                "endTime": end_str,
                "unit": unit,
                "unitNumber": unit_number,
                "limit": api_limit
            }

            logger.debug(f"Fetching {timeframe} bars for {symbol_up} from {start_str} to {end_str}")
            try:
                logger.debug(f"🔍 History API request: {dumps_str(bars_request)}")
            except Exception as e:
                logger.debug(f"🔍 History API request (json serialization failed): {bars_request}")

            response = await self._make_request("POST", "/api/History/retrieveBars", data=bars_request, headers=headers)
            
            # Debug: log response structure
            logger.debug(f"🔍 History API response type: {type(response)}")
            if isinstance(response, dict):
                logger.debug(f"🔍 History API response keys: {list(response.keys())}")
                logger.debug(f"🔍 History API response success: {response.get('success')}")
                logger.debug(f"🔍 History API response errorCode: {response.get('errorCode')}")
                logger.debug(f"🔍 History API response errorMessage: {response.get('errorMessage')}")
            elif isinstance(response, list):
                logger.debug(f"🔍 History API response is list with {len(response)} items")
            
            if "error" in response:
                logger.error(f"API error: {response['error']}")
                return []
            
            # Check for errors first
            if response.get('success') == False or (response.get('errorCode') and response.get('errorCode') != 0):
                error_code = response.get('errorCode', 'Unknown')
                error_msg = response.get('errorMessage', 'No error message')
                logger.error(f"API returned error: Code {error_code}, Message: {error_msg}")
                return []
            
            # Parse bars from response
            bars_data = None
            if isinstance(response, list):
                bars_data = response
                logger.debug(f"🔍 Parsed bars_data from list: {len(bars_data)} bars")
            elif isinstance(response, dict):
                # Try multiple possible field names - check if key exists, not just if value is truthy
                # (empty lists are falsy but valid)
                if 'bars' in response:
                    bars_data = response['bars']
                    if isinstance(bars_data, list):
                        logger.debug(f"🔍 Found 'bars' key with {len(bars_data)} bars")
                    else:
                        logger.debug(f"🔍 Found 'bars' key but it's not a list, type: {type(bars_data)}")
                elif 'data' in response:
                    bars_data = response['data']
                    logger.debug(f"🔍 Found 'data' key with {len(bars_data) if isinstance(bars_data, list) else 'non-list'} value")
                elif 'candles' in response:
                    bars_data = response['candles']
                    logger.debug(f"🔍 Found 'candles' key")
                elif 'result' in response:
                    bars_data = response['result']
                    logger.debug(f"🔍 Found 'result' key")
                else:
                    bars_data = []
                    logger.warning(f"🔍 Response dict has no bars/data/candles/result fields. Available keys: {list(response.keys())}")
                    # Log a sample of the response (first 500 chars) to help debug
                    try:
                        response_str = dumps_str(response)
                        logger.debug(f"🔍 Full response (first 500 chars): {response_str[:500]}")
                    except Exception as e:
                        logger.debug(f"🔍 Full response (json serialization failed): {str(response)[:500]}")
                
                # Ensure bars_data is a list
                if bars_data is None:
                    bars_data = []
                elif not isinstance(bars_data, list):
                    logger.warning(f"🔍 bars_data is not a list, type: {type(bars_data)}, value: {bars_data}")
                    bars_data = []
                
                logger.debug(f"🔍 Parsed bars_data from dict: {len(bars_data)} bars")
                
                # If bars is empty, log the full response for debugging
                if not bars_data and isinstance(response, dict):
                    logger.warning(f"🔍 Empty bars array. Full response: success={response.get('success')}, errorCode={response.get('errorCode')}, errorMessage={response.get('errorMessage')}")
                    try:
                        response_str = dumps_str(response)
                        logger.debug(f"🔍 Full response structure: {response_str[:1000]}")
                    except Exception as e:
                        logger.debug(f"🔍 Full response (json serialization failed): {str(response)[:1000]}")
            
            if not bars_data:
                logger.warning("API returned empty bars data")
                logger.warning(f"Request was: contractId={contract_id}, unit={unit}, unitNumber={unit_number}, limit={api_limit}, startTime={start_str}, endTime={end_str}")
                
                # If we got an empty response and we're in bar count mode, try adjusting the time range
                # to avoid weekends/closed periods
                if not original_start_time_provided and isinstance(response, dict) and response.get('success') != False:
                    logger.info("🔄 Empty response in bar count mode - trying to adjust time range to avoid closed periods")
                    # Try going back further to ensure we hit market-open periods
                    # For weekends, go back to last Friday's market open
                    et_tz = self._get_et_tz()
                    now_et = datetime.now(et_tz)
                    weekday = now_et.weekday()
                    
                    # If it's weekend, adjust start_time to last Friday's market open (9:30 AM EST)
                    if weekday >= 5:  # Saturday or Sunday
                        # Go back to last Friday 9:30 AM EST
                        days_back = weekday - 4  # 1 for Saturday, 2 for Sunday
                        friday_open = (now_et - timedelta(days=days_back)).replace(hour=9, minute=30, second=0, microsecond=0)
                        friday_open_utc = friday_open.astimezone(timezone.utc)
                        
                        # Recalculate start_time from Friday open instead of current time
                        if start_time < friday_open_utc:
                            logger.info(f"🔄 Adjusting start_time from {start_time} to {friday_open_utc} (last Friday market open)")
                            start_time = friday_open_utc
                            start_str = start_time.strftime("%Y-%m-%dT%H:%M:%S") + "Z"
                            
                            # Retry with same payload (camelCase inner props)
                            bars_request = {
                                "contractId": contract_id,
                                "live": False,
                                "startTime": start_str,
                                "endTime": end_str,
                                "unit": unit,
                                "unitNumber": unit_number,
                                "limit": api_limit
                            }

                            logger.info(f"🔄 Retrying with adjusted time range: startTime={start_str}, endTime={end_str}")
                            response = await self._make_request("POST", "/api/History/retrieveBars", data=bars_request, headers=headers)
                            
                            # Re-parse the response
                            if isinstance(response, dict) and 'bars' in response:
                                bars_data = response['bars']
                                if isinstance(bars_data, list) and bars_data:
                                    logger.info(f"✅ Retry successful: got {len(bars_data)} bars")
                                else:
                                    logger.warning("🔄 Retry still returned empty bars")
                            else:
                                logger.warning("🔄 Retry failed or returned unexpected format")
                
                if not bars_data:
                    return []
            
            # Debug: log raw last bar timestamp from API to compare with now
            try:
                from datetime import timezone as _tz

                last_raw = None
                if isinstance(bars_data, list) and bars_data:
                    last = bars_data[-1]
                    last_raw = (
                        last.get("t")
                        or last.get("time")
                        or last.get("timestamp")
                        or last.get("Time")
                        or last.get("Timestamp")
                    )
                logger.debug(f"🔍 History API raw last bar timestamp field (t/time/timestamp) = {last_raw}")
                logger.debug(f"🔍 History API now UTC                                    = {datetime.now(_tz.utc)}")
            except Exception as dbg_err:
                logger.debug(f"Failed to log raw last bar timestamp: {dbg_err}")

            # Convert to Bar objects (canonical adapter representation)
            bars = []
            for bar in bars_data:
                try:
                    # API uses single-letter keys: t, o, h, l, c, v
                    timestamp_val = bar.get("t") or bar.get("time") or bar.get("timestamp")
                    
                    # Parse timestamp
                    if isinstance(timestamp_val, str):
                        dt = datetime.fromisoformat(timestamp_val.replace("Z", "+00:00"))
                    elif isinstance(timestamp_val, (int, float)):
                        if timestamp_val > 10000000000:  # Milliseconds
                            dt = datetime.fromtimestamp(timestamp_val / 1000, tz=timezone.utc)
                        else:  # Seconds
                            dt = datetime.fromtimestamp(timestamp_val, tz=timezone.utc)
                    else:
                        logger.warning(f"Invalid timestamp: {timestamp_val}")
                        continue
                    
                    # Get OHLCV values
                    open_price = float(bar.get("o") or bar.get("open") or 0)
                    high_price = float(bar.get("h") or bar.get("high") or 0)
                    low_price = float(bar.get("l") or bar.get("low") or 0)
                    close_price = float(bar.get("c") or bar.get("close") or 0)
                    volume = int(bar.get("v") or bar.get("volume") or 0)
                    
                    bars.append(Bar(
                        timestamp=dt,
                        open=open_price,
                        high=high_price,
                        low=low_price,
                        close=close_price,
                        volume=volume,
                        symbol=symbol_up,
                        timeframe=timeframe,
                        raw_data=bar
                    ))
                except Exception as e:
                    logger.warning(f"Failed to parse bar: {e}")
                    continue

            # If timeframe > 1m, use 1m aggregation strategy (fetch 1m data and aggregate)
            # For 1d we use API daily bars (barType "Day") for deep history; skip aggregation.
            # For 2d+ we still aggregate from 1m (short retention) unless we add 1d→2d aggregation.
            # Skip aggregation if we're already fetching 1m data (prevents recursion)
            target_seconds = self._parse_timeframe_to_seconds(timeframe)
            is_daily = timeframe.endswith('d')

            # For 2d+ daily bars, aggregate from 1m (short retention). For 1d we used API daily bars above.
            if is_daily and not _skip_aggregation and not is_1d:
                logger.info(f"📊 Daily bars ({timeframe}) requested: aggregating from 1m bars (API daily only supports 1d)")
                bars = []

            # Aggregate from 1m bars for timeframes > 1m OR for 2d+ daily. Never for 1d (we use API daily bars).
            # For 1h in date-range mode, use API 1h directly: 1m aggregation would need 21.6k+ 1m bars
            # for 15 session days but the API limit is 20k, yielding ~333h; session-aligned daily ATR
            # needs 360+ 1h bars. Skipping aggregation for 1h when start_time was provided fixes that.
            if not _skip_aggregation and not is_1d and target_seconds and (target_seconds > 60 or (is_daily and not is_1d)) and not (timeframe_lower == '1h' and original_start_time_provided):
                logger.debug(f"📊 Using 1m aggregation strategy: will fetch 1m data and aggregate to {timeframe}")
                
                # Fetch 1m data instead (with _skip_aggregation=True to prevent recursion)
                one_min_bars = await self.get_historical_data(
                    symbol=symbol,
                    timeframe="1m",
                    limit=limit * (target_seconds // 60) + 100,  # Request enough 1m bars
                    start_time=start_time,
                    end_time=end_time,
                    contract_id_override=contract_id_override,
                    _skip_aggregation=True,  # Prevent recursion
                    **kwargs
                )
                
                if not one_min_bars:
                    logger.warning(f"No 1m data available for aggregation to {timeframe}")
                    return []
                
                # CRITICAL: Sort 1-minute bars by timestamp (oldest first) before aggregation
                # This ensures correct grouping for daily bars (18:00 ET to 18:00 ET)
                one_min_bars.sort(key=lambda b: b.timestamp if b.timestamp else datetime.min.replace(tzinfo=timezone.utc))
                
                # Debug logging for daily bars
                if timeframe.endswith('d') and one_min_bars:
                    logger.info(f"📊 Daily aggregation: {len(one_min_bars)} 1m bars, first={one_min_bars[0].timestamp}, last={one_min_bars[-1].timestamp}")
                    logger.info(f"   First bar: open={one_min_bars[0].open}, high={one_min_bars[0].high}, low={one_min_bars[0].low}, close={one_min_bars[0].close}")
                
                logger.debug(f"📊 Aggregating {len(one_min_bars)} 1m bars into {timeframe} bars (sorted by timestamp)...")
                
                # Use Rust aggregation if available, otherwise Python fallback
                # CRITICAL: For daily bars, always use Python aggregation because Rust uses UTC midnight boundaries
                # instead of 18:00 ET boundaries. Rust's grouping would produce incorrect OHLC values.
                if RUST_AVAILABLE and self._use_rust and not timeframe.endswith('d'):
                    try:
                        # Convert Bar objects to dict format for Rust
                        bars_dict = []
                        for bar in one_min_bars:
                            bars_dict.append({
                                'timestamp': int(bar.timestamp.timestamp()),
                                'time': int(bar.timestamp.timestamp()),
                                'open': bar.open,
                                'high': bar.high,
                                'low': bar.low,
                                'close': bar.close,
                                'volume': bar.volume,
                            })
                        
                        # Use optimized Rust aggregation
                        timestamps = [b['timestamp'] for b in bars_dict]
                        opens = [b['open'] for b in bars_dict]
                        highs = [b['high'] for b in bars_dict]
                        lows = [b['low'] for b in bars_dict]
                        closes = [b['close'] for b in bars_dict]
                        volumes = [b['volume'] for b in bars_dict]
                        
                        aggregated_rust = trading_bot_rust.aggregate_bars_raw(
                            timestamps, opens, highs, lows, closes, volumes,
                            timeframe, symbol_up
                        )
                        
                        # Convert back to Bar objects
                        aggregated_bars = []
                        for rust_bar in aggregated_rust:
                            dt = datetime.fromtimestamp(rust_bar.timestamp, tz=timezone.utc)
                            aggregated_bars.append(Bar(
                                timestamp=dt,
                                open=rust_bar.open,
                                high=rust_bar.high,
                                low=rust_bar.low,
                                close=rust_bar.close,
                                volume=rust_bar.volume,
                                symbol=symbol_up,
                                timeframe=timeframe,
                            ))
                        
                        # Filter out Saturday bars for daily timeframes (market is closed on Saturday)
                        if timeframe.endswith('d'):
                            try:
                                import pytz
                                et_tz = pytz.timezone('US/Eastern')
                            except ImportError:
                                et_tz = timezone(timedelta(hours=-5))
                            
                            filtered_bars = []
                            for bar in aggregated_bars:
                                if bar.timestamp:
                                    # Convert to ET to check weekday
                                    if bar.timestamp.tzinfo is None:
                                        bar_et = bar.timestamp.replace(tzinfo=timezone.utc).astimezone(et_tz)
                                    else:
                                        bar_et = bar.timestamp.astimezone(et_tz)
                                    # Skip Saturday (weekday 5)
                                    if bar_et.weekday() == 5:
                                        logger.debug(f"Filtering out Saturday bar from Rust aggregation: {bar.timestamp} (ET: {bar_et})")
                                    else:
                                        filtered_bars.append(bar)
                                else:
                                    filtered_bars.append(bar)
                            aggregated_bars = filtered_bars
                            logger.debug(f"Filtered daily bars from Rust aggregation: {len(aggregated_bars)} bars remaining after removing Saturday bars")
                        
                        # Limit to requested count (only in bar count mode, not date range mode)
                        if not original_start_time_provided and len(aggregated_bars) > limit:
                            aggregated_bars = aggregated_bars[-limit:]
                        
                        logger.debug(f"✅ Aggregated to {len(aggregated_bars)} {timeframe} bars (Rust)")
                        return aggregated_bars
                    except Exception as e:
                        logger.warning(f"Rust aggregation failed, falling back to Python: {e}")
                        # Fall through to Python implementation
                
                # Python fallback aggregation
                bars_dict = []
                for bar in one_min_bars:
                    bars_dict.append({
                        'timestamp': int(bar.timestamp.timestamp()),
                        'time': int(bar.timestamp.timestamp()),
                        'open': bar.open,
                        'high': bar.high,
                        'low': bar.low,
                        'close': bar.close,
                        'volume': bar.volume,
                    })
                
                aggregated_dict = self._aggregate_bars(bars_dict, timeframe)
                
                # Convert back to Bar objects
                aggregated_bars = []
                for bar_dict in aggregated_dict:
                    # The timestamp in bar_dict is already the bar start time (from _aggregate_bars)
                    # For daily bars, keep the actual start time (18:00 ET previous day), not display date
                    dt = datetime.fromtimestamp(bar_dict['timestamp'], tz=timezone.utc)
                    aggregated_bars.append(Bar(
                        timestamp=dt,
                        open=bar_dict['open'],
                        high=bar_dict['high'],
                        low=bar_dict['low'],
                        close=bar_dict['close'],
                        volume=bar_dict['volume'],
                        symbol=symbol_up,
                        timeframe=timeframe,
                    ))
                
                # Filter out Saturday bars for daily timeframes (market is closed on Saturday)
                if timeframe.endswith('d'):
                    try:
                        import pytz
                        et_tz = pytz.timezone('US/Eastern')
                    except ImportError:
                        et_tz = timezone(timedelta(hours=-5))
                    
                    filtered_bars = []
                    for bar in aggregated_bars:
                        if bar.timestamp:
                            # Convert to ET to check weekday
                            if bar.timestamp.tzinfo is None:
                                bar_et = bar.timestamp.replace(tzinfo=timezone.utc).astimezone(et_tz)
                            else:
                                bar_et = bar.timestamp.astimezone(et_tz)
                            # Skip Saturday (weekday 5)
                            if bar_et.weekday() == 5:
                                logger.debug(f"Filtering out Saturday bar from Python aggregation: {bar.timestamp} (ET: {bar_et})")
                            else:
                                filtered_bars.append(bar)
                        else:
                            filtered_bars.append(bar)
                    aggregated_bars = filtered_bars
                    logger.debug(f"Filtered daily bars from Python aggregation: {len(aggregated_bars)} bars remaining after removing Saturday bars")
                
                # Limit to requested count (only in bar count mode, not date range mode)
                if not original_start_time_provided and len(aggregated_bars) > limit:
                    aggregated_bars = aggregated_bars[-limit:]
                
                logger.debug(f"✅ Aggregated to {len(aggregated_bars)} {timeframe} bars (Python)")
                return aggregated_bars
            
            # Adjust timestamps for daily bars from API to use correct market hours
            if timeframe.endswith('d'):
                for bar in bars:
                    if bar.timestamp:
                        # Get the correct start time
                        bar_start = self._get_daily_bar_start_time(bar.timestamp)
                        # For display, use the trading day's date (next day if starts at 18:00)
                        bar.timestamp = self._get_daily_bar_display_date(bar_start)
                
                # Deduplicate daily bars with the same display timestamp
                # Group by display timestamp and merge bars
                bars_by_date = {}
                for bar in bars:
                    if bar.timestamp:
                        display_key = int(bar.timestamp.timestamp())
                        if display_key not in bars_by_date:
                            bars_by_date[display_key] = bar
                        else:
                            # Merge bars with same display date (take the one with more volume or later data)
                            existing = bars_by_date[display_key]
                            # Use the bar with higher volume, or if equal, the one with later timestamp
                            if bar.volume > existing.volume or (bar.volume == existing.volume and bar.timestamp > existing.timestamp):
                                bars_by_date[display_key] = bar
                
                # Convert back to list
                bars = list(bars_by_date.values())
                
                # Filter out Saturday bars (market is closed on Saturday)
                # Saturday is weekday 5 (0=Monday, 1=Tuesday, ..., 5=Saturday, 6=Sunday)
                # Check weekday in ET timezone to match display date logic
                try:
                    import pytz
                    et_tz = pytz.timezone('US/Eastern')
                except ImportError:
                    et_tz = timezone(timedelta(hours=-5))
                
                filtered_bars = []
                for bar in bars:
                    if bar.timestamp:
                        # Convert to ET to check weekday
                        if bar.timestamp.tzinfo is None:
                            bar_et = bar.timestamp.replace(tzinfo=timezone.utc).astimezone(et_tz)
                        else:
                            bar_et = bar.timestamp.astimezone(et_tz)
                        # Skip Saturday (weekday 5)
                        weekday = bar_et.weekday()
                        if weekday == 5:
                            logger.info(f"🗑️  Filtering out Saturday bar: {bar.timestamp} (ET: {bar_et}, weekday={weekday})")
                        else:
                            filtered_bars.append(bar)
                    else:
                        filtered_bars.append(bar)
                bars = filtered_bars
                if len(bars) < len(bars_by_date):
                    logger.info(f"✅ Filtered daily bars: {len(bars)} bars remaining after removing {len(bars_by_date) - len(bars)} Saturday bars")

                # After converting to "display timestamps" (trading-day labels), apply the holiday-merge again.
                # This is the path that drives the CLI `history <symbol> 1d ...` output.
                if timeframe_lower == "1d" and os.getenv("MERGE_DAILY_HOLIDAY_BARS", "true").lower() in ("1", "true", "yes", "on"):
                    bars = self._merge_holiday_daily_bars_into_next(bars)
            
            # CRITICAL: Sort by timestamp (oldest first) before limiting
            # This ensures we always get the most recent bars, regardless of API response order
            bars.sort(key=lambda b: b.timestamp if b.timestamp else datetime.min.replace(tzinfo=timezone.utc))
            
            # Limit to requested number (only in bar count mode, not date range mode)
            # In date range mode, return ALL bars between the dates
            if not original_start_time_provided and len(bars) > limit:
                bars = bars[-limit:]
            
            # Optionally append current partial daily bar (aggregated from 1m)
            if include_partial_daily and timeframe_lower == "1d":
                partial_bar = await self._build_partial_daily_bar(
                    symbol=symbol,
                    end_time=end_time,
                    contract_id_override=contract_id_override or contract_id
                )
                if partial_bar and (not bars or partial_bar.timestamp > bars[-1].timestamp):
                    bars.append(partial_bar)

            # Additional debug: log parsed last bar timestamp vs now
            if bars:
                try:
                    from datetime import timezone as _tz

                    last_bar_ts = bars[-1].timestamp
                    logger.debug(f"🔍 Parsed last bar timestamp UTC = {last_bar_ts}")
                    logger.debug(f"🔍 Adapter now UTC               = {datetime.now(_tz.utc)}")
                except Exception as dbg_err:
                    logger.debug(f"Failed to log parsed last bar timestamp: {dbg_err}")

            logger.debug(f"✅ Retrieved {len(bars)} {timeframe} bars for {symbol_up}")
            return bars
            
        except Exception as e:
            logger.error(f"Failed to fetch historical data (impl): {str(e)}")
            return []
            
        except Exception as e:
            logger.error(f"Failed to fetch historical data: {str(e)}")
            return []
    
    async def _build_partial_daily_bar(
        self,
        symbol: str,
        end_time: Optional[datetime],
        contract_id_override: Optional[str] = None
    ) -> Optional[Bar]:
        """
        Build a partial current daily bar by aggregating 1m bars from the current daily session.
        """
        try:
            symbol_up = symbol.upper()
            from datetime import datetime, timezone

            end_time = end_time or datetime.now(timezone.utc)
            session_start = self._get_daily_bar_start_time(end_time)

            one_min_bars = await self.get_historical_data(
                symbol=symbol,
                timeframe="1m",
                limit=20000,
                start_time=session_start,
                end_time=end_time,
                _skip_aggregation=True,
                contract_id_override=contract_id_override,
                _continuous_roll=True,
                continuous_daily=False,
                include_partial_daily=False
            )
            if not one_min_bars:
                return None

            bars_dict = []
            for bar in one_min_bars:
                if hasattr(bar, "timestamp"):
                    ts = int(bar.timestamp.timestamp())
                    bars_dict.append({
                        "timestamp": ts,
                        "time": ts,
                        "open": bar.open,
                        "high": bar.high,
                        "low": bar.low,
                        "close": bar.close,
                        "volume": bar.volume
                    })
                elif isinstance(bar, dict):
                    ts = bar.get("timestamp") or bar.get("time")
                    if ts is None:
                        continue
                    bars_dict.append({
                        "timestamp": int(ts),
                        "time": int(ts),
                        "open": bar.get("open", bar.get("o", 0)),
                        "high": bar.get("high", bar.get("h", 0)),
                        "low": bar.get("low", bar.get("l", 0)),
                        "close": bar.get("close", bar.get("c", 0)),
                        "volume": bar.get("volume", bar.get("v", 0))
                    })

            if not bars_dict:
                return None

            bars_dict.sort(key=lambda b: b["timestamp"])
            daily_dicts = self._aggregate_bars(bars_dict, "1d")
            if not daily_dicts:
                return None

            last = daily_dicts[-1]
            ts = last.get("timestamp") or last.get("time")
            if ts is None:
                return None

            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            return Bar(
                timestamp=dt,
                open=float(last.get("open", 0)),
                high=float(last.get("high", 0)),
                low=float(last.get("low", 0)),
                close=float(last.get("close", 0)),
                volume=int(last.get("volume", 0)),
                symbol=symbol_up,
                timeframe="1d",
                raw_data=last
            )
        except Exception as e:
            logger.debug(f"Failed to build partial daily bar for {symbol}: {e}")
            return None

    def _get_daily_bar_start_time(self, timestamp: datetime) -> datetime:
        """
        Get the start time for a daily bar based on EST market hours.
        
        Rules:
        - Every day opens at 18:00 ET (6pm) the previous day
        - Every day closes at 18:00 ET (6pm) that day
        
        Examples:
        - Monday bar: Sunday 18:00 ET to Monday 18:00 ET
        - Tuesday bar: Monday 18:00 ET to Tuesday 18:00 ET
        - Wednesday bar: Tuesday 18:00 ET to Wednesday 18:00 ET
        - Thursday bar: Wednesday 18:00 ET to Thursday 18:00 ET
        - Friday bar: Thursday 18:00 ET to Friday 18:00 ET
        """
        et_tz = self._get_et_tz()
        
        # Convert to EST
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        timestamp_et = timestamp.astimezone(et_tz)
        
        weekday = timestamp_et.weekday()  # 0=Monday, 1=Tuesday, ..., 6=Sunday
        hour = timestamp_et.hour
        
        # Calculate daily bar start - simplified logic
        # If before 18:00 (6pm), we're still in today's bar (which started yesterday 18:00)
        # If at or after 18:00 (6pm), we're in tomorrow's bar (which starts today 18:00)
        if hour < 18:
            # Before 18:00 - still in today's bar, which started yesterday 18:00
            days_back = 1
            bar_start_et = (timestamp_et - timedelta(days=days_back)).replace(hour=18, minute=0, second=0, microsecond=0)
        else:
            # At or after 18:00 - this is tomorrow's bar, which starts today 18:00
            bar_start_et = timestamp_et.replace(hour=18, minute=0, second=0, microsecond=0)
        
        # Convert back to UTC
        return bar_start_et.astimezone(timezone.utc)
    
    def _get_daily_bar_display_date(self, bar_start_timestamp: datetime) -> datetime:
        """
        Get the display date for a daily bar.
        
        For daily bars, the timestamp shows the start time (18:00 ET previous day),
        but we want to display it with the trading day's date.
        
        Examples:
        - Bar starting Sunday 18:00 ET should display as Monday's date
        - Bar starting Monday 18:00 ET should display as Tuesday's date
        - Bar starting Tuesday 18:00 ET should display as Wednesday's date
        - Bar starting Wednesday 18:00 ET should display as Thursday's date
        - Bar starting Thursday 18:00 ET should display as Friday's date
        """
        et_tz = self._get_et_tz()
        
        # Convert to EST
        if bar_start_timestamp.tzinfo is None:
            bar_start_timestamp = bar_start_timestamp.replace(tzinfo=timezone.utc)
        bar_start_et = bar_start_timestamp.astimezone(et_tz)
        
        weekday = bar_start_et.weekday()  # 0=Monday, 1=Tuesday, ..., 6=Sunday
        hour = bar_start_et.hour
        
        # If the bar starts at 18:00 ET, it represents the NEXT trading day
        if hour == 18:
            if weekday == 6:  # Sunday 18:00 -> Monday's bar
                display_date_et = (bar_start_et + timedelta(days=1)).replace(hour=18, minute=0, second=0, microsecond=0)
            elif weekday == 0:  # Monday 18:00 -> Tuesday's bar
                display_date_et = (bar_start_et + timedelta(days=1)).replace(hour=18, minute=0, second=0, microsecond=0)
            elif weekday == 1:  # Tuesday 18:00 -> Wednesday's bar
                display_date_et = (bar_start_et + timedelta(days=1)).replace(hour=18, minute=0, second=0, microsecond=0)
            elif weekday == 2:  # Wednesday 18:00 -> Thursday's bar
                display_date_et = (bar_start_et + timedelta(days=1)).replace(hour=18, minute=0, second=0, microsecond=0)
            elif weekday == 3:  # Thursday 18:00 -> Friday's bar
                display_date_et = (bar_start_et + timedelta(days=1)).replace(hour=18, minute=0, second=0, microsecond=0)
            else:
                # Shouldn't happen, but fallback
                display_date_et = bar_start_et
        else:
            # Not a standard daily bar start time, use as-is
            display_date_et = bar_start_et
        
        # Convert back to UTC
        return display_date_et.astimezone(timezone.utc)
    
    def _get_daily_bar_end_time(self, bar_start: datetime) -> datetime:
        """
        Get the end time for a daily bar based on EST market hours.
        
        Rules:
        - Daily bars end at 18:00 ET (6pm) the next day
        - This aligns with the overnight session: 18:00 to 18:00
        """
        et_tz = self._get_et_tz()
        
        # Convert to EST
        if bar_start.tzinfo is None:
            bar_start = bar_start.replace(tzinfo=timezone.utc)
        bar_start_et = bar_start.astimezone(et_tz)
        
        # All daily bars end at 18:00 ET the next day
        bar_end_et = (bar_start_et + timedelta(days=1)).replace(hour=18, minute=0, second=0, microsecond=0)
        
        # Convert back to UTC
        return bar_end_et.astimezone(timezone.utc)

    def _get_et_tz(self) -> ZoneInfo:
        """America/New_York timezone (handles DST without pytz)."""
        return ZoneInfo("America/New_York")

    @staticmethod
    def _nth_weekday_of_month(year: int, month: int, weekday: int, n: int) -> date:
        """Return the n-th weekday (0=Mon) in a given month."""
        first = date(year, month, 1)
        offset = (weekday - first.weekday() + 7) % 7
        return first + timedelta(days=offset + 7 * (n - 1))

    @staticmethod
    def _last_weekday_of_month(year: int, month: int, weekday: int) -> date:
        """Return the last weekday (0=Mon) in a given month."""
        import calendar
        last_day = calendar.monthrange(year, month)[1]
        last = date(year, month, last_day)
        offset = (last.weekday() - weekday + 7) % 7
        return last - timedelta(days=offset)

    @staticmethod
    def _easter_sunday(year: int) -> date:
        """Anonymous Gregorian computus (Meeus/Jones/Butcher)."""
        a = year % 19
        b = year // 100
        c = year % 100
        d = b // 4
        e = b % 4
        f = (b + 8) // 25
        g = (b - f + 1) // 3
        h = (19 * a + b - d - g + 15) % 30
        i = c // 4
        k = c % 4
        l = (32 + 2 * e + 2 * i - h - k) % 7
        m = (a + 11 * h + 22 * l) // 451
        month = (h + l - 7 * m + 114) // 31
        day = ((h + l - 7 * m + 114) % 31) + 1
        return date(year, month, day)

    @staticmethod
    def _observed_us_holiday(d: date) -> date:
        """Observed date for fixed-date holidays (Sat->Fri, Sun->Mon)."""
        if d.weekday() == 5:  # Saturday
            return d - timedelta(days=1)
        if d.weekday() == 6:  # Sunday
            return d + timedelta(days=1)
        return d

    @lru_cache(maxsize=32)
    def _cme_equity_index_holidays(self, year: int) -> frozenset[date]:
        """
        Approximation of US holidays that TradingView commonly omits as separate *daily candles*
        for CME equity index futures continuous series.
        """
        holidays: set[date] = set()
        # Fixed-date (observed)
        holidays.add(self._observed_us_holiday(date(year, 1, 1)))   # New Year's Day
        holidays.add(self._observed_us_holiday(date(year, 6, 19)))  # Juneteenth (observed)
        holidays.add(self._observed_us_holiday(date(year, 7, 4)))   # Independence Day (observed)
        holidays.add(self._observed_us_holiday(date(year, 12, 25))) # Christmas (observed)

        # Monday-based
        holidays.add(self._nth_weekday_of_month(year, 1, 0, 3))  # MLK Day (3rd Monday Jan)
        holidays.add(self._nth_weekday_of_month(year, 2, 0, 3))  # Presidents' Day (3rd Monday Feb)
        holidays.add(self._last_weekday_of_month(year, 5, 0))    # Memorial Day (last Monday May)
        holidays.add(self._nth_weekday_of_month(year, 9, 0, 1))  # Labor Day (1st Monday Sep)

        # Thanksgiving
        holidays.add(self._nth_weekday_of_month(year, 11, 3, 4))  # 4th Thursday Nov

        # Good Friday (2 days before Easter)
        holidays.add(self._easter_sunday(year) - timedelta(days=2))

        return frozenset(holidays)

    def _merge_holiday_daily_bars_into_next(self, bars: List[Bar]) -> List[Bar]:
        """
        Merge any daily bar whose *display-date* falls on a holiday into the next non-holiday bar.
        This matches TradingView behavior where the holiday candle is absent but its price action
        is reflected in the next trading day's OHLC.
        """
        if not bars:
            return bars

        et_tz = self._get_et_tz()
        sorted_bars = sorted(
            [b for b in bars if getattr(b, "timestamp", None)],
            key=lambda b: b.timestamp if b.timestamp else datetime.min.replace(tzinfo=timezone.utc),
        )
        passthrough = [b for b in bars if not getattr(b, "timestamp", None)]

        carry: Optional[Bar] = None
        out: List[Bar] = []

        def merge_into(target: Bar, prior: Bar) -> Bar:
            return Bar(
                timestamp=target.timestamp,
                open=prior.open,
                high=max(prior.high, target.high),
                low=min(prior.low, target.low),
                close=target.close,
                volume=int(getattr(prior, "volume", 0) or 0) + int(getattr(target, "volume", 0) or 0),
                symbol=target.symbol,
                timeframe=target.timeframe,
                raw_data={"merged_holiday_into_next": True, "prior_raw": getattr(prior, "raw_data", None), "target_raw": getattr(target, "raw_data", None)},
            )

        for bar in sorted_bars:
            et_day = bar.timestamp.astimezone(et_tz).date()
            holidays = self._cme_equity_index_holidays(et_day.year)

            if et_day in holidays:
                if carry is None:
                    carry = bar
                else:
                    # Extend the carry window across consecutive holiday bars.
                    carry = Bar(
                        timestamp=carry.timestamp,
                        open=carry.open,
                        high=max(carry.high, bar.high),
                        low=min(carry.low, bar.low),
                        close=bar.close,
                        volume=int(getattr(carry, "volume", 0) or 0) + int(getattr(bar, "volume", 0) or 0),
                        symbol=carry.symbol,
                        timeframe=carry.timeframe,
                        raw_data={"merged_holiday_chain": True, "parts": [getattr(carry, "raw_data", None), getattr(bar, "raw_data", None)]},
                    )
                logger.info(f"📅 Holiday daily bar detected ({et_day}); will merge into next trading day to match TradingView")
                continue

            if carry is not None:
                bar = merge_into(bar, carry)
                carry = None

            out.append(bar)

        # If the last bar(s) were holiday bars with no following bar, drop them (can't merge forward).
        # This matches TradingView's "missing candle" behavior best, and avoids inventing a candle.
        merged = out + passthrough
        merged.sort(key=lambda b: b.timestamp if getattr(b, "timestamp", None) else datetime.min.replace(tzinfo=timezone.utc))
        return merged
    
    def _parse_timeframe_to_seconds(self, timeframe: str) -> Optional[int]:
        """Parse timeframe string to seconds."""
        timeframe = timeframe.strip().lower()
        
        if timeframe.endswith('s'):
            return int(timeframe[:-1]) if timeframe[:-1].isdigit() else None
        elif timeframe.endswith('m'):
            return int(timeframe[:-1]) * 60 if timeframe[:-1].isdigit() else 60
        elif timeframe.endswith('h'):
            return int(timeframe[:-1]) * 3600 if timeframe[:-1].isdigit() else None
        elif timeframe.endswith('d'):
            return int(timeframe[:-1]) * 86400 if timeframe[:-1].isdigit() else None
        elif timeframe.endswith('w'):
            return int(timeframe[:-1]) * 604800 if timeframe[:-1].isdigit() else None
        return None
    
    def _aggregate_bars(self, bars: List[Dict], target_timeframe: str) -> List[Dict]:
        """
        Aggregate 1-minute bars into higher timeframes (5m, 15m, 30m, 1h, etc.).
        
        This is a Python fallback implementation. Rust version is preferred when available.
        For daily bars, this is the primary implementation (Rust not used).
        """
        if not bars:
            return []
        
        target_seconds = self._parse_timeframe_to_seconds(target_timeframe)
        if target_seconds is None or target_seconds <= 60:
            return bars
        
        aggregated = []
        current_group = []
        current_group_start = None
        is_daily = target_timeframe.endswith('d')
        
        for bar in bars:
            ts = bar.get('timestamp') or bar.get('time')
            # Special handling for daily bars
            if target_timeframe.endswith('d'):
                dt = datetime.fromtimestamp(ts, tz=timezone.utc)
                bar_start_dt = self._get_daily_bar_start_time(dt)
                bar_start_seconds = int(bar_start_dt.timestamp())
                
                # For daily bars, only include bars within the period (>= bar_start and < bar_end)
                bar_end_dt = self._get_daily_bar_end_time(bar_start_dt)
                if dt < bar_start_dt or dt >= bar_end_dt:
                    continue
            else:
                bar_start_seconds = (ts // target_seconds) * target_seconds
            
            if current_group_start is None or bar_start_seconds != current_group_start:
                if current_group:
                    # Debug logging for daily bars
                    if is_daily:
                        first_ts = datetime.fromtimestamp(current_group[0].get('timestamp') or current_group[0].get('time'), tz=timezone.utc)
                        last_ts = datetime.fromtimestamp(current_group[-1].get('timestamp') or current_group[-1].get('time'), tz=timezone.utc)
                        bar_start_display = datetime.fromtimestamp(current_group_start, tz=timezone.utc)
                        logger.info(f"   Python daily bar: start={bar_start_display}, open={current_group[0].get('open', 0)}, "
                                   f"first_ts={first_ts}, last_ts={last_ts}, bars={len(current_group)}")
                    
                if current_group:
                    agg_bar = {
                        'timestamp': current_group_start,
                        'time': current_group_start,
                        'open': current_group[0].get('open', 0),
                        'high': max(b.get('high', 0) for b in current_group),
                        'low': min(b.get('low', float('inf')) for b in current_group if b.get('low') is not None),
                        'close': current_group[-1].get('close', 0),
                        'volume': sum(b.get('volume', 0) or 0 for b in current_group),
                    }
                    if agg_bar['low'] == float('inf'):
                        agg_bar['low'] = agg_bar['open']
                    aggregated.append(agg_bar)
                
                current_group = [bar]
                current_group_start = bar_start_seconds
            else:
                current_group.append(bar)
        
        if current_group:
            agg_bar = {
                'timestamp': current_group_start,
                'time': current_group_start,
                'open': current_group[0].get('open', 0),
                'high': max(b.get('high', 0) for b in current_group),
                'low': min(b.get('low', float('inf')) for b in current_group if b.get('low') is not None),
                'close': current_group[-1].get('close', 0),
                'volume': sum(b.get('volume', 0) or 0 for b in current_group),
            }
            if agg_bar['low'] == float('inf'):
                agg_bar['low'] = agg_bar['open']
            aggregated.append(agg_bar)
        
        return aggregated
    
    async def get_market_quote(
        self,
        symbol: str,
        **kwargs
    ) -> Optional[Quote]:
        """
        Get current market quote.
        
        Args:
            symbol: Trading symbol
            **kwargs: Additional parameters
            
        Returns:
            Quote object or None if unavailable
        """
        try:
            # Skip Rust for quotes - SignalR quotes are more reliable and real-time
            # Rust REST API endpoint returns 404, so we use Python fallback which can access bars
            # Python fallback
            await self.auth.ensure_valid_token()
            
            symbol_up = symbol.upper()
            
            # Get contract ID
            try:
                contract_id = self.contract_manager.get_contract_id(symbol_up)
            except ValueError as e:
                logger.error(f"Cannot get quote: {e}")
                return None
            
            headers = {
                "accept": "text/plain",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.auth.get_token()}"
            }
            
            # Skip REST quote endpoint - it returns 404 and isn't reliable
            # Go straight to bars fallback which is more reliable
            # Fallback to recent bars for last price
            from datetime import datetime, timezone, timedelta
            now = datetime.now(timezone.utc)
            start_time = now - timedelta(seconds=30)
            
            bars_request = {
                "contractId": contract_id,
                "live": False,
                "startTime": start_time.isoformat(),
                "endTime": now.isoformat(),
                "unit": 2,  # AggregateBarUnit: 2 = Minute
                "unitNumber": 1,
                "limit": 5
            }
            response = await self._make_request("POST", "/api/History/retrieveBars", data=bars_request, headers=headers)

            if "error" not in response and response.get("success"):
                bars = response.get("bars", [])
                if bars:
                    latest_bar = bars[-1]
                    current_price = latest_bar.get("c")  # Close price
                    if current_price is not None:
                        return Quote(
                            symbol=symbol_up,
                            last=float(current_price),
                            raw_data={"bar_data": latest_bar, "source": "bars_fallback"}
                        )
            
            logger.warning(f"No market quote available for {symbol_up}")
            return None
            
        except Exception as e:
            logger.error(f"Failed to fetch market quote: {str(e)}")
            return None
    
    async def _get_market_quote_rust(
        self,
        symbol: str
    ) -> Optional[Quote]:
        """Get market quote using Rust executor."""
        import time
        start_time = time.perf_counter()
        
        await self.auth.ensure_valid_token()
        
        symbol_up = symbol.upper()
        
        try:
            contract_id = self.contract_manager.get_contract_id(symbol_up)
        except ValueError:
            return None
        
        token = self.auth.get_token()
        self._query_executor.set_token(token)
        
        # Try symbol variants like Python fallback does
        # Try symbol first (e.g., "MNQ"), then contract_id
        symbol_variants = [symbol_up]
        if '.' in contract_id:
            parts = contract_id.split('.')
            if len(parts) >= 4:
                symbol_variants.append(parts[-2])  # Extract symbol from contract ID
        symbol_variants.append(contract_id)  # Try contract_id last
        
        rust_result = None
        last_error = None
        for variant in symbol_variants:
            try:
                rust_result = await self._query_executor.get_market_quote(contract_id=variant)
                # Check if we got a valid quote with at least one price field
                if rust_result and isinstance(rust_result, dict):
                    bid = rust_result.get('bid') or rust_result.get('bestBid')
                    ask = rust_result.get('ask') or rust_result.get('bestAsk')
                    last = rust_result.get('last') or rust_result.get('lastPrice') or rust_result.get('price')
                    if bid is not None or ask is not None or last is not None:
                        # Found valid quote, break
                        logger.debug(f"Rust quote succeeded for variant: {variant}")
                        break
                    else:
                        logger.debug(f"Rust quote returned empty data for variant: {variant}, result: {rust_result}")
                        rust_result = None
                elif rust_result is None:
                    logger.debug(f"Rust quote returned None for variant: {variant}")
                else:
                    logger.debug(f"Rust quote returned unexpected type for variant: {variant}, type: {type(rust_result)}")
                    rust_result = None
            except Exception as e:
                last_error = e
                logger.debug(f"Rust quote attempt failed for {variant}: {e}")
                rust_result = None
                continue
        
        elapsed_ms = (time.perf_counter() - start_time) * 1000
        logger.debug("Rust get_market_quote execution: %.2fms", elapsed_ms)
        
        if not rust_result:
            if last_error:
                logger.warning(f"Rust get_market_quote failed for all variants, last error: {last_error}")
            else:
                logger.warning(f"Rust get_market_quote returned None/empty for all variants: {symbol_variants}")
            return None
        
        # Convert Rust dict to Quote object - handle different field names like Python fallback
        try:
            from core.interfaces import Quote
            bid = rust_result.get('bid') or rust_result.get('bestBid')
            ask = rust_result.get('ask') or rust_result.get('bestAsk')
            last = rust_result.get('last') or rust_result.get('lastPrice') or rust_result.get('price')
            volume = rust_result.get('volume') or rust_result.get('totalVolume')
            
            # Only return if we have at least one price field
            if not any(v is not None for v in (bid, ask, last)):
                return None
            
            return Quote(
                symbol=symbol_up,
                bid=float(bid) if bid is not None else None,
                ask=float(ask) if ask is not None else None,
                last=float(last) if last is not None else None,
                volume=int(volume) if volume is not None else None,
                raw_data=rust_result
            )
        except Exception as e:
            logger.warning(f"Failed to convert quote: {e}")
            return None
    
    async def get_market_depth(
        self,
        symbol: str,
        **kwargs
    ) -> Optional[Depth]:
        """
        Get market depth (order book).
        
        Uses Rust executor for hot path with Python fallback.
        
        Args:
            symbol: Trading symbol
            **kwargs: Additional parameters
            
        Returns:
            Depth object or None if unavailable
        """
        try:
            # Try Rust hot path first
            if self._use_rust and self._query_executor:
                try:
                    return await self._get_market_depth_rust(symbol)
                except Exception as e:
                    logger.warning(f"⚠️  Rust execution failed, falling back to Python: {e}")
            
            # Python fallback
            await self.auth.ensure_valid_token()
            
            symbol_up = symbol.upper()
            
            # Get contract ID
            try:
                contract_id = self.contract_manager.get_contract_id(symbol_up)
            except ValueError as e:
                logger.error(f"Cannot get depth: {e}")
                return None
            
            headers = {
                "accept": "text/plain",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.auth.get_token()}"
            }
            
            # Try different possible endpoints for market depth
            endpoints_to_try = [
                f"/api/MarketData/orderbook/{contract_id}",
                f"/api/MarketData/level2/{contract_id}",
                f"/api/MarketData/depth/{contract_id}",
            ]
            
            response = None
            for endpoint in endpoints_to_try:
                try:
                    resp = await self._make_request("GET", endpoint, headers=headers)
                    if resp and "error" not in resp and resp != {"success": True, "message": "Operation completed successfully"}:
                        response = resp
                        break
                except Exception:
                    continue
            
            # If no specific endpoint worked, try generic with contract ID
            if not response:
                for endpoint in ["/api/MarketData/depth", "/api/MarketData/orderbook", "/api/MarketData/level2"]:
                    try:
                        resp = await self._make_request("POST", endpoint, data={"contractId": contract_id}, headers=headers)
                        if resp and "error" not in resp:
                            response = resp
                            break
                    except Exception:
                        continue
            
            if not response or "error" in response:
                logger.warning(f"Could not fetch market depth for {symbol_up}")
                # Return empty depth
                return Depth(
                    symbol=symbol_up,
                    bids=[],
                    asks=[]
                )
            
            # Parse market depth response
            if isinstance(response, dict):
                if "bids" in response and "asks" in response:
                    bids = response["bids"]
                    asks = response["asks"]
                elif "data" in response:
                    data = response["data"]
                    bids = data.get("bids", [])
                    asks = data.get("asks", [])
                elif "result" in response:
                    result = response["result"]
                    bids = result.get("bids", [])
                    asks = result.get("asks", [])
                else:
                    bids = []
                    asks = []
                
                # Convert to DepthLevel objects
                from core.interfaces import DepthLevel
                bid_levels = []
                ask_levels = []
                
                for bid in bids:
                    if isinstance(bid, dict):
                        bid_levels.append(DepthLevel(
                            price=float(bid.get("price", 0)),
                            size=int(bid.get("size", 0) or bid.get("quantity", 0))
                        ))
                    elif isinstance(bid, (list, tuple)) and len(bid) >= 2:
                        bid_levels.append(DepthLevel(price=float(bid[0]), size=int(bid[1])))
                
                for ask in asks:
                    if isinstance(ask, dict):
                        ask_levels.append(DepthLevel(
                            price=float(ask.get("price", 0)),
                            size=int(ask.get("size", 0) or ask.get("quantity", 0))
                        ))
                    elif isinstance(ask, (list, tuple)) and len(ask) >= 2:
                        ask_levels.append(DepthLevel(price=float(ask[0]), size=int(ask[1])))
                
                return Depth(
                    symbol=symbol_up,
                    bids=bid_levels,
                    asks=ask_levels,
                    raw_data=response
                )
            
            # Return empty depth if parsing failed
            return Depth(symbol=symbol_up, bids=[], asks=[])
            
        except Exception as e:
            logger.error(f"Failed to fetch market depth: {str(e)}")
            return None
    
    async def _get_market_depth_rust(
        self,
        symbol: str
    ) -> Optional[Depth]:
        """Get market depth using Rust executor."""
        import time
        start_time = time.perf_counter()
        
        await self.auth.ensure_valid_token()
        
        try:
            contract_id = self.contract_manager.get_contract_id(symbol)
        except ValueError:
            return None
        
        token = self.auth.get_token()
        self._query_executor.set_token(token)
        
        rust_result = await self._query_executor.get_market_depth(contract_id=contract_id)
        
        elapsed_ms = (time.perf_counter() - start_time) * 1000
        logger.debug("Rust get_market_depth execution: %.2fms", elapsed_ms)
        
        if not rust_result:
            return Depth(symbol=symbol.upper(), bids=[], asks=[])
        
        # Convert Rust dict to Depth object
        try:
            from core.interfaces import Depth, DepthLevel
            bids = rust_result.get('bids', [])
            asks = rust_result.get('asks', [])
            
            bid_levels = []
            for bid in bids:
                if isinstance(bid, dict):
                    bid_levels.append(DepthLevel(
                        price=float(bid.get('price', 0)),
                        size=int(bid.get('size', 0) or bid.get('quantity', 0))
                    ))
                elif isinstance(bid, (list, tuple)) and len(bid) >= 2:
                    bid_levels.append(DepthLevel(price=float(bid[0]), size=int(bid[1])))
            
            ask_levels = []
            for ask in asks:
                if isinstance(ask, dict):
                    ask_levels.append(DepthLevel(
                        price=float(ask.get('price', 0)),
                        size=int(ask.get('size', 0) or ask.get('quantity', 0))
                    ))
                elif isinstance(ask, (list, tuple)) and len(ask) >= 2:
                    ask_levels.append(DepthLevel(price=float(ask[0]), size=int(ask[1])))
            
            return Depth(
                symbol=symbol.upper(),
                bids=bid_levels,
                asks=ask_levels,
                raw_data=rust_result
            )
        except Exception as e:
            logger.warning(f"Failed to convert depth: {e}")
            return Depth(symbol=symbol.upper(), bids=[], asks=[])
    
    async def get_available_contracts(
        self,
        use_cache: bool = True,
        **kwargs
    ) -> List[Dict[str, Any]]:
        """
        Get available trading contracts.
        
        Uses Rust executor for hot path with Python fallback.
        
        Args:
            use_cache: If True, use cached contracts if available
            **kwargs: Additional parameters (cache_ttl_minutes, etc.)
            
        Returns:
            List of contract dictionaries
        """
        try:
            # Try Rust hot path first (bypasses cache for fresh data)
            if self._use_rust and self._query_executor and not use_cache:
                try:
                    return await self._get_available_contracts_rust()
                except Exception as e:
                    logger.warning(f"⚠️  Rust execution failed, falling back to Python: {e}")
            
            # Python fallback (with caching)
            await self.auth.ensure_valid_token()

            cache_ttl_minutes = kwargs.get("cache_ttl_minutes", 60)

            if use_cache:
                cache = self.contract_manager.get_contract_cache()
                if cache:
                    cache_age = datetime.now() - cache["timestamp"]
                    if cache_age < timedelta(minutes=cache_ttl_minutes):
                        logger.debug(
                            "Using cached contract list (%d contracts)", len(cache["contracts"])
                        )
                        return cache["contracts"].copy()

            async with self._contract_fetch_async_lock:
                if use_cache:
                    cache = self.contract_manager.get_contract_cache()
                    if cache:
                        cache_age = datetime.now() - cache["timestamp"]
                        if cache_age < timedelta(minutes=cache_ttl_minutes):
                            logger.debug(
                                "Using cached contract list after refresh wait (%d contracts)",
                                len(cache["contracts"]),
                            )
                            return cache["contracts"].copy()

                logger.debug("Fetching available contracts from API...")

                headers = {
                    "accept": "application/json",
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.auth.get_token()}",
                }

                response = await self._make_request(
                    "POST",
                    "/api/Contract/available",
                    data={"live": False},
                    headers=headers,
                )

                if isinstance(response, dict) and response.get("error"):
                    logger.error("API error: %s", response["error"])
                    return []

                contracts = response if isinstance(response, list) else response.get("contracts", [])

                if not contracts:
                    logger.warning("No contracts returned from API")
                    return []

                self.contract_manager.set_contract_cache(contracts, cache_ttl_minutes)

                logger.debug("Retrieved %d available contracts", len(contracts))
                return contracts

        except Exception as e:
            logger.error(f"Failed to fetch available contracts: {str(e)}")
            return []
    
    async def _get_available_contracts_rust(self) -> List[Dict[str, Any]]:
        """
        Get available contracts using Rust QueryExecutor.
        
        Returns:
            List of contract dictionaries
        """
        if not self._query_executor:
            raise RuntimeError("Rust QueryExecutor not available")
        
        # Set token if needed
        token = self.auth.get_token()
        if token:
            self._query_executor.set_token(token)
        
        # Call Rust method
        contracts_py = await self._query_executor.get_available_contracts()
        
        # Convert Python list of dicts to proper format
        contracts = []
        if isinstance(contracts_py, list):
            for contract in contracts_py:
                if isinstance(contract, dict):
                    contracts.append(dict(contract))
        
        # Update contract cache
        if contracts:
            self.contract_manager.set_contract_cache(contracts, 60)
        
        return contracts
    
    # ============================================================================
    # ADVANCED ORDER METHODS
    # ============================================================================

    async def _snapshot_quote_for_bracket_diag(self, symbol: str) -> Dict[str, Any]:
        """Best-effort last/bid/ask snapshot for bracket place/reject forensics.

        Prefer live SignalR cache on the attached bot (bid+ask+last). Fall back to
        ``get_market_quote`` (often last-only from recent bars). Never raises; never
        blocks the order path for more than ~2s.
        """
        out: Dict[str, Any] = {
            "symbol": str(symbol).upper(),
            "last": None,
            "bid": None,
            "ask": None,
            "source": None,
            "ts": None,
        }
        try:
            bot = getattr(self, "_trading_bot", None)
            if bot is not None:
                cache = getattr(bot, "_quote_cache", None)
                lock = getattr(bot, "_quote_cache_lock", None)
                if isinstance(cache, dict):
                    sym = str(symbol).upper()
                    live = None
                    if lock is not None:
                        with lock:
                            live = cache.get(sym)
                    else:
                        live = cache.get(sym)
                    if live and any(live.get(k) is not None for k in ("bid", "ask", "last")):
                        out.update(
                            last=live.get("last"),
                            bid=live.get("bid"),
                            ask=live.get("ask"),
                            source="signalr_cache",
                            ts=live.get("ts"),
                        )
                        return out

            try:
                quote = await asyncio.wait_for(self.get_market_quote(symbol), timeout=2.0)
            except asyncio.TimeoutError:
                out["source"] = "timeout"
                return out

            if quote is None:
                out["source"] = "unavailable"
                return out
            if isinstance(quote, Quote):
                out.update(
                    last=quote.last,
                    bid=quote.bid,
                    ask=quote.ask,
                    source="adapter_quote",
                )
                if isinstance(quote.raw_data, dict):
                    out["quote_raw_source"] = quote.raw_data.get("source")
            elif isinstance(quote, dict) and "error" not in quote:
                out.update(
                    last=quote.get("last"),
                    bid=quote.get("bid"),
                    ask=quote.get("ask"),
                    source=quote.get("source") or "adapter_quote_dict",
                    ts=quote.get("ts"),
                )
            else:
                out["source"] = "unavailable"
        except Exception as exc:
            out["source"] = f"error:{type(exc).__name__}"
            logger.debug("bracket diag quote snapshot failed: %s", exc, exc_info=True)
        return out

    @staticmethod
    def _bracket_price_vs_last_ticks(
        price: Optional[float],
        last: Optional[float],
        tick_size: Optional[float],
    ) -> Optional[int]:
        """Signed ticks of ``price - last`` (positive = price above last)."""
        try:
            if price is None or last is None:
                return None
            ts = float(tick_size or 0.0)
            if ts <= 0:
                return None
            return int(round((float(price) - float(last)) / ts))
        except (TypeError, ValueError):
            return None

    async def _log_bracket_order_diag(
        self,
        *,
        event: str,
        symbol: str,
        side: str,
        order_data: Dict[str, Any],
        quote: Optional[Dict[str, Any]] = None,
        entry_price: Optional[float] = None,
        stop_loss_price: Optional[float] = None,
        take_profit_price: Optional[float] = None,
        tick_size: Optional[float] = None,
        response: Optional[Any] = None,
        error: Optional[str] = None,
        path: str = "python",
    ) -> Dict[str, Any]:
        """INFO-log last/bid/ask + full ``/api/Order/place`` payload (place/accept/reject)."""
        q = quote if quote is not None else await self._snapshot_quote_for_bracket_diag(symbol)
        last = q.get("last")
        entry_vs = self._bracket_price_vs_last_ticks(entry_price, last, tick_size)
        sl_vs = self._bracket_price_vs_last_ticks(stop_loss_price, last, tick_size)
        tp_vs = self._bracket_price_vs_last_ticks(take_profit_price, last, tick_size)
        try:
            payload_json = dumps_str(order_data)
        except Exception:
            payload_json = str(order_data)
        resp_json = None
        if response is not None:
            try:
                resp_json = dumps_str(response) if not isinstance(response, str) else response
            except Exception:
                resp_json = str(response)

        logger.info(
            "BRACKET_DIAG event=%s path=%s %s %s | quote last=%s bid=%s ask=%s source=%s ts=%s | "
            "vs_last_ticks entry=%s sl=%s tp=%s (tick_size=%s) | entry=%s sl=%s tp=%s | "
            "payload=%s%s%s",
            event,
            path,
            side,
            symbol,
            q.get("last"),
            q.get("bid"),
            q.get("ask"),
            q.get("source"),
            q.get("ts"),
            entry_vs,
            sl_vs,
            tp_vs,
            tick_size,
            entry_price,
            stop_loss_price,
            take_profit_price,
            payload_json,
            f" | error={error}" if error else "",
            f" | response={resp_json}" if resp_json is not None else "",
        )
        return q

    async def place_oco_bracket_with_stop_entry(
        self,
        symbol: str,
        side: str,
        quantity: int,
        entry_price: float,
        stop_loss_price: float,
        take_profit_price: float,
        account_id: Optional[str] = None,
        enable_breakeven: bool = False,
        strategy_name: Optional[str] = None
    ) -> OrderResponse:
        """
        Place OCO bracket order with stop order as entry.
        
        Works like native bracket but uses a stop order for entry instead of market order.
        Uses the same /api/Order/place endpoint with stopLossBracket and takeProfitBracket.
        
        Uses Rust executor for hot path with Python fallback.
        
        Args:
            symbol: Trading symbol (e.g., "MNQ", "ES")
            side: "BUY" or "SELL"
            quantity: Number of contracts
            entry_price: Stop price for entry
            stop_loss_price: Stop loss price
            take_profit_price: Take profit price
            account_id: Account ID
            enable_breakeven: Enable breakeven stop monitoring (default: False)
            strategy_name: Optional strategy name for tracking
            
        Returns:
            OrderResponse with order details
        """
        try:
            # Try Rust hot path first
            if self._use_rust and self._rust_executor:
                try:
                    rust_resp = await self._place_oco_bracket_rust(
                        symbol, side, quantity, entry_price, stop_loss_price,
                        take_profit_price, account_id, strategy_name
                    )
                    # IMPORTANT: Rust can return `success=false` without raising (e.g. HTTP 500),
                    # which previously prevented Python fallback from running.
                    if rust_resp and getattr(rust_resp, "success", False):
                        return rust_resp
                    
                    # Rust execution completed but API rejected the order
                    # API errors (network, server, validation) will fail the same in Python,
                    # so don't waste resources on fallback. Only fallback for Rust library exceptions.
                    rust_error = getattr(rust_resp, 'error', None) if rust_resp else None
                    rust_error_str = str(rust_error) if rust_error else ""
                    
                    logger.warning(f"⚠️  Rust OCO bracket execution completed but API rejected order: {rust_error_str}")
                    logger.warning(f"   Order: {side} {quantity} {symbol} @ {entry_price:.2f}")
                    logger.warning(f"   Skipping Python fallback - API errors will fail the same way in Python")
                    try:
                        rust_payload = {
                            "accountId": int(account_id) if account_id else None,
                            "type": 4,
                            "side": 0 if side.upper() == "BUY" else 1,
                            "size": quantity,
                            "stopPrice": entry_price,
                            "stopLossPrice": stop_loss_price,
                            "takeProfitPrice": take_profit_price,
                            "_note": "rust_path_reconstructed_diag_payload",
                        }
                        await self._log_bracket_order_diag(
                            event="reject",
                            symbol=symbol,
                            side=side,
                            order_data=rust_payload,
                            entry_price=entry_price,
                            stop_loss_price=stop_loss_price,
                            take_profit_price=take_profit_price,
                            response=getattr(rust_resp, "raw_response", None),
                            error=rust_error_str,
                            path="rust_stop_entry",
                        )
                    except Exception:
                        logger.debug("BRACKET_DIAG rust reject log failed", exc_info=True)
                    return OrderResponse(
                        success=False,
                        error=f"Order rejected by API: {rust_error_str}",
                        raw_response=getattr(rust_resp, 'raw_response', None)
                    )
                except Exception as e:
                    # This is a Rust library exception (PyO3, serialization, etc.), not an API error
                    # Python fallback might work if it's a Rust-specific issue
                    logger.warning(f"⚠️  Rust library exception (not API error), falling back to Python: {e}")
                    import traceback
                    logger.debug(f"Rust error traceback: {traceback.format_exc()}")
            
            # Python fallback (use logger only so messages get configured timestamps)
            logger.info("Using Python fallback for stop bracket order")
            t_py0 = time.perf_counter()
            wall_start_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
            logger.info("Stop bracket Python path wall_start_utc=%s", wall_start_utc)
            
            # Ensure valid token before placing order
            logger.info("Ensuring valid token before placing stop bracket order")
            token_valid = await self.auth.ensure_valid_token()
            if not token_valid:
                error_msg = "Failed to ensure valid token before placing order"
                logger.error(f"❌ {error_msg}")
                return OrderResponse(success=False, error=error_msg)
            logger.info("Token validated for stop bracket order")
            
            if not account_id:
                return OrderResponse(success=False, error="Account ID is required")
            
            if side.upper() not in ["BUY", "SELL"]:
                return OrderResponse(success=False, error="Side must be 'BUY' or 'SELL'")
            
            logger.debug(f"Placing OCO bracket with stop entry: {side} {quantity} {symbol}")
            logger.debug(f"  Entry (stop): ${entry_price:.2f}, SL: ${stop_loss_price:.2f}, TP: ${take_profit_price:.2f}")
            
            # Validate prices are positive (absolute prices, not relative)
            if entry_price <= 0 or stop_loss_price <= 0 or take_profit_price <= 0:
                error_msg = f"All prices must be positive absolute prices. Got: entry={entry_price}, SL={stop_loss_price}, TP={take_profit_price}"
                logger.error(f"❌ {error_msg}")
                return OrderResponse(success=False, error=error_msg)
            
            # Get contract ID
            try:
                contract_id = self.contract_manager.get_contract_id(symbol)
            except ValueError as e:
                error_msg = f"Cannot create bracket order: {e}. Please fetch contracts first."
                logger.error(f"❌ {error_msg}")
                return OrderResponse(success=False, error=error_msg)
            
            # Get tick size
            tick_size = await self._get_tick_size(symbol)
            
            # Round prices to valid tick sizes
            entry_price = self._round_to_tick_size(entry_price, tick_size)
            stop_loss_price = self._round_to_tick_size(stop_loss_price, tick_size)
            take_profit_price = self._round_to_tick_size(take_profit_price, tick_size)
            
            logger.debug(f"Rounded prices: Entry=${entry_price:.2f}, SL=${stop_loss_price:.2f}, TP=${take_profit_price:.2f} (tick_size={tick_size})")
            
            # Convert side to numeric value
            side_value = 0 if side.upper() == "BUY" else 1
            
            # Calculate stop loss ticks from entry price (stop_price)
            if side.upper() == "BUY":
                # BUY stop: stop loss must be below entry
                if stop_loss_price >= entry_price:
                    error_msg = f"Stop loss price ({stop_loss_price}) must be below entry price ({entry_price}) for BUY stop orders"
                    logger.error(f"❌ {error_msg}")
                    return OrderResponse(success=False, error=error_msg)
                price_diff = entry_price - stop_loss_price
                stop_loss_ticks = int(price_diff / tick_size)
                if stop_loss_ticks > 0:
                    stop_loss_ticks = -stop_loss_ticks
            else:  # SELL
                # SELL stop: stop loss must be above entry
                if stop_loss_price <= entry_price:
                    error_msg = f"Stop loss price ({stop_loss_price}) must be above entry price ({entry_price}) for SELL stop orders"
                    logger.error(f"❌ {error_msg}")
                    return OrderResponse(success=False, error=error_msg)
                price_diff = stop_loss_price - entry_price
                stop_loss_ticks = int(price_diff / tick_size)
                if stop_loss_ticks < 0:
                    stop_loss_ticks = -stop_loss_ticks
            
            # Calculate take profit ticks from entry price (stop_price)
            if side.upper() == "BUY":
                # BUY stop: take profit must be above entry
                if take_profit_price <= entry_price:
                    error_msg = f"Take profit price ({take_profit_price}) must be above entry price ({entry_price}) for BUY stop orders"
                    logger.error(f"❌ {error_msg}")
                    return OrderResponse(success=False, error=error_msg)
                price_diff = take_profit_price - entry_price
                take_profit_ticks = int(price_diff / tick_size)
                if take_profit_ticks < 0:
                    take_profit_ticks = -take_profit_ticks
            else:  # SELL
                # SELL stop: take profit must be below entry
                if take_profit_price >= entry_price:
                    error_msg = f"Take profit price ({take_profit_price}) must be below entry price ({entry_price}) for SELL stop orders"
                    logger.error(f"❌ {error_msg}")
                    return OrderResponse(success=False, error=error_msg)
                price_diff = entry_price - take_profit_price
                take_profit_ticks = int(price_diff / tick_size)
                if take_profit_ticks > 0:
                    take_profit_ticks = -take_profit_ticks
            
            logger.debug("Stop Loss: %s ticks, Take Profit: %s ticks", stop_loss_ticks, take_profit_ticks)
            
            # Validate tick values (TopStepX has limits)
            if abs(stop_loss_ticks) > 1000:
                logger.warning(f"Stop loss ticks ({stop_loss_ticks}) exceeds 1000 limit, capping at 1000")
                stop_loss_ticks = 1000 if stop_loss_ticks > 0 else -1000
            if abs(take_profit_ticks) > 1000:
                logger.warning(f"Take profit ticks ({take_profit_ticks}) exceeds 1000 limit, capping at 1000")
                take_profit_ticks = 1000 if take_profit_ticks > 0 else -1000
            
            # Prepare order data
            order_data = {
                "accountId": int(account_id),
                "contractId": contract_id,
                "type": 4,  # Stop-market order for entry
                "side": side_value,
                "size": quantity,
                "stopPrice": entry_price,
                "customTag": self._generate_unique_custom_tag("stop_bracket", strategy_name)
            }
            
            # Add bracket orders
            # NOTE: Do NOT set reduceOnly=True here! The entry order hasn't filled yet,
            # so there's no position to reduce. TopStepX automatically handles bracket
            # attachment once the entry fills. Setting reduceOnly=True causes 500 errors.
            order_data["stopLossBracket"] = {
                "ticks": stop_loss_ticks,
                "type": 4,  # Stop loss type
                "size": quantity
                # reduceOnly removed - brackets auto-attach after entry fills
            }
            
            order_data["takeProfitBracket"] = {
                "ticks": take_profit_ticks,
                "type": 1,  # Take profit type
                "size": quantity
                # reduceOnly removed - brackets auto-attach after entry fills
            }
            
            sl_signed, tp_signed = stop_loss_ticks, take_profit_ticks
            logger.info(
                "Stop bracket order parameters: %s %s qty=%s entry(stop)=%.2f tick_size=%.4f | "
                "SL API ticks=%s (abs=%s, price %.2f) | TP API ticks=%s (abs=%s, price %.2f); "
                "signed ticks are TopStepX convention (SELL TP is negative when TP is below entry)",
                symbol,
                side,
                quantity,
                entry_price,
                tick_size,
                sl_signed,
                abs(sl_signed),
                stop_loss_price,
                tp_signed,
                abs(tp_signed),
                take_profit_price,
            )
            # Forensic snapshot: last/bid/ask + full place payload (Invalid-price diagnosis).
            quote_snap = await self._log_bracket_order_diag(
                event="place",
                symbol=symbol,
                side=side,
                order_data=order_data,
                entry_price=entry_price,
                stop_loss_price=stop_loss_price,
                take_profit_price=take_profit_price,
                tick_size=tick_size,
                path="python_stop_entry",
            )
            
            # Make API call
            headers = {
                "accept": "text/plain",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.auth.get_token()}"
            }
            
            response = await self._make_request("POST", "/api/Order/place", data=order_data, headers=headers)
            
            # Handle 500 errors with automatic token refresh and retry
            if "error" in response and ("500" in str(response.get("error", "")) or response.get("status_code") == 500):
                logger.warning(
                    "Received 500 on stop bracket place; retrying after token refresh "
                    "(server load, expired token, or account Auto OCO Brackets setting)"
                )
                
                # Force token refresh even if it appears valid (server might have invalidated it)
                token_refreshed = await self.auth.ensure_valid_token(force_refresh=True)
                if token_refreshed:
                    headers["Authorization"] = f"Bearer {self.auth.get_token()}"
                    await asyncio.sleep(0.75)
                    logger.info("Retrying stop bracket placement with refreshed token")
                    response = await self._make_request("POST", "/api/Order/place", data=order_data, headers=headers)
                    
                    # Check if retry succeeded
                    if "error" in response and "500" in str(response.get("error", "")):
                        logger.error(
                            "Retry also failed with 500 (server, Auto OCO Brackets, or invalid params). "
                            "Entry=%.2f SL=%.2f TP=%.2f",
                            entry_price,
                            stop_loss_price,
                            take_profit_price,
                        )
                else:
                    logger.error("Failed to refresh token, cannot retry stop bracket")
            
            if "error" in response:
                error_msg = response.get("error", "")
                await self._log_bracket_order_diag(
                    event="reject",
                    symbol=symbol,
                    side=side,
                    order_data=order_data,
                    quote=quote_snap,
                    entry_price=entry_price,
                    stop_loss_price=stop_loss_price,
                    take_profit_price=take_profit_price,
                    tick_size=tick_size,
                    response=response,
                    error=str(error_msg),
                    path="python_stop_entry",
                )
                logger.error(
                    "Failed to create stop bracket order: %s round_trip_ms=%.1f wall_start_utc=%s",
                    error_msg,
                    (time.perf_counter() - t_py0) * 1000,
                    wall_start_utc,
                )
                return OrderResponse(success=False, error=error_msg, raw_response=response)
            
            # Check success field
            if response.get("success") == False:
                error_code = response.get("errorCode", "Unknown")
                error_message = response.get("errorMessage", "No error message")
                await self._log_bracket_order_diag(
                    event="reject",
                    symbol=symbol,
                    side=side,
                    order_data=order_data,
                    quote=quote_snap,
                    entry_price=entry_price,
                    stop_loss_price=stop_loss_price,
                    take_profit_price=take_profit_price,
                    tick_size=tick_size,
                    response=response,
                    error=f"Code {error_code}: {error_message}",
                    path="python_stop_entry",
                )
                logger.error(
                    "Bracket order failed: Code %s Message: %s round_trip_ms=%.1f wall_start_utc=%s",
                    error_code,
                    error_message,
                    (time.perf_counter() - t_py0) * 1000,
                    wall_start_utc,
                )
                return OrderResponse(success=False, error=f"Bracket order failed: {error_message} (Code: {error_code})", raw_response=response)
            
            order_id = response.get("orderId") or response.get("id")
            if not order_id:
                await self._log_bracket_order_diag(
                    event="reject",
                    symbol=symbol,
                    side=side,
                    order_data=order_data,
                    quote=quote_snap,
                    entry_price=entry_price,
                    stop_loss_price=stop_loss_price,
                    take_profit_price=take_profit_price,
                    tick_size=tick_size,
                    response=response,
                    error="No order ID returned",
                    path="python_stop_entry",
                )
                logger.error(
                    "API returned success but NO order ID round_trip_ms=%.1f wall_start_utc=%s",
                    (time.perf_counter() - t_py0) * 1000,
                    wall_start_utc,
                )
                logger.debug(f"Full response: {dumps_str(response)}")
                return OrderResponse(success=False, error="Order rejected: No order ID returned", raw_response=response)
            
            # Send Discord notification for strategy-initiated orders
            if strategy_name and order_id and hasattr(self, '_trading_bot') and self._trading_bot:
                try:
                    account_name = 'Unknown'
                    if hasattr(self._trading_bot, 'selected_account') and self._trading_bot.selected_account:
                        if isinstance(self._trading_bot.selected_account, dict):
                            account_name = self._trading_bot.selected_account.get('name', 'Unknown')
                        else:
                            account_name = str(self._trading_bot.selected_account)
                    
                    notification_data = {
                        'symbol': symbol,
                        'side': side,
                        'quantity': quantity,
                        'price': f"${entry_price:.2f} (Stop Entry)",
                        'order_type': 'Bracket (Stop Entry)',
                        'order_id': order_id,
                        'status': 'Placed',
                        'account_id': account_id,
                        'stop_loss': stop_loss_price,
                        'take_profit': take_profit_price,
                        'strategy': strategy_name
                    }
                    asyncio.create_task(
                        self._trading_bot.discord_notifier.send_order_notification(notification_data, account_name)
                    )
                    logger.info(f"📧 Discord notification sent for strategy order: {strategy_name} - {side} {quantity} {symbol}")
                except Exception as notif_err:
                    logger.debug(f"Could not send Discord notification for strategy order: {notif_err}")
            
            await self._log_bracket_order_diag(
                event="accept",
                symbol=symbol,
                side=side,
                order_data=order_data,
                quote=quote_snap,
                entry_price=entry_price,
                stop_loss_price=stop_loss_price,
                take_profit_price=take_profit_price,
                tick_size=tick_size,
                response={"orderId": order_id, "success": True},
                path="python_stop_entry",
            )
            logger.info(
                "OCO bracket order placed successfully (Python path) order_id=%s round_trip_ms=%.1f wall_start_utc=%s",
                order_id,
                (time.perf_counter() - t_py0) * 1000,
                wall_start_utc,
            )

            # Attach execution-path metadata for callers (GUI/strategy logging)
            raw = response
            if isinstance(raw, dict):
                raw = dict(raw)
                raw["_execution_path"] = "python"
            
            return OrderResponse(
                success=True,
                order_id=str(order_id),
                message="OCO bracket order placed successfully (python)",
                raw_response=raw if isinstance(raw, dict) else response
            )
            
        except Exception as e:
            logger.error(f"Failed to place OCO bracket order: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            return OrderResponse(success=False, error=str(e))

    async def place_oco_bracket_stop_entry_partial_tp_v1(
        self,
        symbol: str,
        side: str,
        quantity: int,
        entry_price: float,
        stop_loss_price: float,
        take_profit_full_price: float,
        scalp_r_multiple: float = 1.0,
        account_id: Optional[str] = None,
        enable_breakeven: bool = False,
        strategy_name: Optional[str] = None,
    ) -> OrderResponse:
        """BONGO §1A — two native stop-entry OCO brackets (scalp qty + runner qty).

        Uses :func:`core.bracket_orders.build_partial_tp_stop_entry_plan` then calls
        :meth:`place_oco_bracket_with_stop_entry` twice (same entry/stop, different TP
        and contract counts). Returned ``order_id`` is ``scalp_id|runner_id`` for
        strategy bookkeeping.

        **Caveats**
        - Both legs share the same protective stop price; the venue sees two
          independent stop entries at the same level — confirm account limits.
        - Runner stop is **not** auto-moved to breakeven after the scalp fills on
          this path; that behaviour is modeled in replay. ``enable_breakeven`` is
          reserved for a future fill-driven tighten on the runner leg.
        """
        _ = enable_breakeven
        try:
            from core.bracket_orders import build_partial_tp_stop_entry_plan

            if int(quantity) < 2:
                return OrderResponse(success=False, error="partial_tp_requires_quantity_ge_2")
            if not account_id:
                return OrderResponse(success=False, error="Account ID is required")
            su = side.upper()
            if su not in ("BUY", "SELL"):
                return OrderResponse(success=False, error="Side must be 'BUY' or 'SELL'")

            plan = build_partial_tp_stop_entry_plan(
                symbol=symbol,
                side=side,
                quantity=int(quantity),
                entry_stop_price=float(entry_price),
                stop_loss_price=float(stop_loss_price),
                take_profit_full_price=float(take_profit_full_price),
                scalp_r_multiple=float(scalp_r_multiple or 1.0),
            )

            base_tag = (strategy_name or "partial_tp").strip() or "partial_tp"
            scalp_tag = f"{base_tag}_ptp_scalp"
            runner_tag = f"{base_tag}_ptp_runner"

            scalp_resp = await self.place_oco_bracket_with_stop_entry(
                symbol,
                side,
                plan.scalp.quantity,
                entry_price,
                stop_loss_price,
                plan.scalp.take_profit_price,
                account_id,
                enable_breakeven=False,
                strategy_name=scalp_tag,
            )
            if not scalp_resp or not getattr(scalp_resp, "success", False):
                err = getattr(scalp_resp, "error", None) if scalp_resp else None
                return OrderResponse(
                    success=False,
                    error=f"partial_tp_scalp_leg_failed: {err or 'unknown'}",
                    raw_response=getattr(scalp_resp, "raw_response", None) if scalp_resp else None,
                )

            await asyncio.sleep(0.2)

            runner_resp = await self.place_oco_bracket_with_stop_entry(
                symbol,
                side,
                plan.runner.quantity,
                entry_price,
                stop_loss_price,
                plan.runner.take_profit_price,
                account_id,
                enable_breakeven=False,
                strategy_name=runner_tag,
            )
            if not runner_resp or not getattr(runner_resp, "success", False):
                rerr = getattr(runner_resp, "error", None) if runner_resp else None
                raw_out: Dict[str, Any] = {
                    "partial_tp_scalp_order_id": scalp_resp.order_id,
                    "runner_error": rerr,
                    "runner_raw": getattr(runner_resp, "raw_response", None) if runner_resp else None,
                }
                return OrderResponse(
                    success=False,
                    error=f"partial_tp_runner_leg_failed: {rerr or 'unknown'}",
                    raw_response=raw_out,
                )

            s_id = str(scalp_resp.order_id or "")
            r_id = str(runner_resp.order_id or "")
            raw_out = {
                "scalp_order_id": s_id,
                "runner_order_id": r_id,
                "_execution_path": "python_partial_tp_v1_dual_oco",
            }
            if isinstance(scalp_resp.raw_response, dict):
                raw_out["scalp_raw"] = dict(scalp_resp.raw_response)
            if isinstance(runner_resp.raw_response, dict):
                raw_out["runner_raw"] = dict(runner_resp.raw_response)

            return OrderResponse(
                success=True,
                order_id=f"{s_id}|{r_id}",
                message="partial_tp_dual_oco_stop_entry_v1",
                raw_response=raw_out,
            )
        except Exception as e:
            logger.error("place_oco_bracket_stop_entry_partial_tp_v1 failed: %s", e, exc_info=True)
            return OrderResponse(success=False, error=str(e))

    async def _place_oco_bracket_rust(
        self,
        symbol: str,
        side: str,
        quantity: int,
        entry_price: float,
        stop_loss_price: float,
        take_profit_price: float,
        account_id: Optional[str],
        strategy_name: Optional[str]
    ) -> OrderResponse:
        """Place OCO bracket order using Rust executor."""
        import time
        start_time = time.perf_counter()

        # CRITICAL: Refresh token BEFORE every Rust call (not just once at start)
        # This ensures token is fresh even for retries and repeated order attempts
        token_valid = await self.auth.ensure_valid_token()
        if not token_valid:
            return OrderResponse(success=False, error="Failed to ensure valid token")

        if not account_id:
            return OrderResponse(success=False, error="Account ID is required")

        token = self.auth.get_token()
        self._rust_executor.set_token(token)
        
        try:
            contract_id = self.contract_manager.get_contract_id(symbol)
            self._rust_executor.set_contract_id(symbol, contract_id)
        except ValueError as e:
            return OrderResponse(
                success=False,
                error=f"Cannot place order: {e}. Please fetch contracts first."
            )
        
        # Get tick size for calculating ticks
        tick_size = await self._get_tick_size(symbol)

        # Round prices to valid tick sizes
        entry_price = self._round_to_tick_size(entry_price, tick_size)
        stop_loss_price = self._round_to_tick_size(stop_loss_price, tick_size)
        take_profit_price = self._round_to_tick_size(take_profit_price, tick_size)

        # Calculate stop loss and take profit ticks from entry price
        if side.upper() == "BUY":
            stop_loss_ticks = int((entry_price - stop_loss_price) / tick_size)
            if stop_loss_ticks > 0:
                stop_loss_ticks = -stop_loss_ticks
            take_profit_ticks = int((take_profit_price - entry_price) / tick_size)
            if take_profit_ticks < 0:
                take_profit_ticks = -take_profit_ticks
        else:
            stop_loss_ticks = int((stop_loss_price - entry_price) / tick_size)
            if stop_loss_ticks < 0:
                stop_loss_ticks = -stop_loss_ticks
            take_profit_ticks = int((entry_price - take_profit_price) / tick_size)
            if take_profit_ticks > 0:
                take_profit_ticks = -take_profit_ticks
        
        # Cap ticks at 1000 (TopStepX limit)
        stop_loss_ticks = max(-1000, min(1000, stop_loss_ticks))
        take_profit_ticks = max(-1000, min(1000, take_profit_ticks))
        
        custom_tag = self._generate_unique_custom_tag("stop_bracket", strategy_name)
        
        rust_result = await self._rust_executor.place_order(
            symbol=symbol,
            side=side,
            quantity=quantity,
            account_id=int(account_id),
            stop_loss_ticks=stop_loss_ticks,
            take_profit_ticks=take_profit_ticks,
            limit_price=None,
            stop_price=entry_price,
            order_type="stop",
            custom_tag=custom_tag
        )
        
        elapsed_ms = (time.perf_counter() - start_time) * 1000
        logger.info(f"⚡ Rust OCO bracket execution: {elapsed_ms:.2f}ms")

        # Discord notification for strategy-initiated orders (Rust path)
        try:
            if strategy_name and hasattr(self, '_trading_bot') and self._trading_bot:
                account_name = 'Unknown'
                if getattr(self._trading_bot, 'selected_account', None):
                    if isinstance(self._trading_bot.selected_account, dict):
                        account_name = self._trading_bot.selected_account.get('name', 'Unknown')
                    else:
                        account_name = str(self._trading_bot.selected_account)

                notification_data = {
                    'symbol': symbol,
                    'side': side,
                    'quantity': quantity,
                    'price': f"${entry_price:.2f} (Stop Entry)",
                    'order_type': 'Bracket (Stop Entry)',
                    'order_id': rust_result.get('order_id') or rust_result.get('orderId') or 'Unknown',
                    'status': 'Placed',
                    'account_id': account_id,
                    'stop_loss': stop_loss_price,
                    'take_profit': take_profit_price,
                    'strategy': strategy_name
                }
                asyncio.create_task(
                    self._trading_bot.discord_notifier.send_order_notification(notification_data, account_name)
                )
        except Exception as notif_err:
            logger.debug(f"Could not send Discord notification for strategy order (Rust path): {notif_err}")

        # Attach execution-path metadata for callers (GUI/strategy logging)
        raw = rust_result.get('raw_response') if isinstance(rust_result, dict) else None
        if isinstance(raw, dict):
            raw = dict(raw)
            raw["_execution_path"] = "rust"
            raw["_custom_tag"] = custom_tag

        return OrderResponse(
            success=rust_result.get('success', False),
            order_id=rust_result.get('order_id'),
            message=rust_result.get('message') or "OCO bracket order placed successfully (rust)",
            error=rust_result.get('error'),
            raw_response=raw if isinstance(raw, dict) else rust_result.get('raw_response')
        )
    
    async def place_trailing_stop_order(
        self,
        symbol: str,
        side: str,
        quantity: int,
        trail_amount: float,
        account_id: Optional[str] = None
    ) -> OrderResponse:
        """
        Place a trailing stop order.
        
        Args:
            symbol: Trading symbol (e.g., "MNQ", "ES")
            side: "BUY" or "SELL"
            quantity: Number of contracts
            trail_amount: Trail amount in price units (e.g., 25.00 for $25)
            account_id: Account ID
            
        Returns:
            OrderResponse with order details
        """
        try:
            # Try Rust hot path first
            if self._use_rust and self._rust_executor:
                try:
                    return await self._place_trailing_stop_rust(
                        symbol, side, quantity, trail_amount, account_id
                    )
                except Exception as e:
                    logger.warning(f"⚠️  Rust execution failed, falling back to Python: {e}")
            
            # Python fallback
            await self.auth.ensure_valid_token()
            
            if not account_id:
                return OrderResponse(success=False, error="Account ID is required")
            
            if side.upper() not in ["BUY", "SELL"]:
                return OrderResponse(success=False, error="Side must be 'BUY' or 'SELL'")
            
            logger.info(f"Placing trailing stop order for {side} {quantity} {symbol} with trail ${trail_amount}")
            
            # Get contract ID
            try:
                contract_id = self.contract_manager.get_contract_id(symbol)
            except ValueError as e:
                error_msg = f"Cannot place trailing stop order: {e}. Please fetch contracts first."
                logger.error(f"❌ {error_msg}")
                return OrderResponse(success=False, error=error_msg)
            
            # Get tick size
            tick_size = await self._get_tick_size(symbol)
            
            # Convert trail amount to ticks
            trail_ticks = trail_amount / tick_size
            
            # Server-side limit defaults to 1000 ticks
            max_ticks = 1000
            clamped = False
            if trail_ticks > max_ticks:
                clamped = True
                trail_ticks = max_ticks
                trail_amount = max_ticks * tick_size
                logger.warning(f"Trail exceeded max; clamped to {max_ticks} ticks -> ${trail_amount:.2f}")
            
            logger.info(f"Trail amount: ${trail_amount} = {trail_ticks:.0f} ticks (tick_size: {tick_size})")
            
            # Convert side to numeric value
            side_value = 0 if side.upper() == "BUY" else 1
            
            # Prepare order data for trailing stop
            order_data = {
                "accountId": int(account_id),
                "contractId": contract_id,
                "type": 5,  # Trailing stop order type
                "side": side_value,
                "size": quantity,
                "trailDistance": int(trail_ticks),
            }
            
            # Make API call
            headers = {
                "accept": "text/plain",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.auth.get_token()}"
            }
            
            response = await self._make_request("POST", "/api/Order/place", data=order_data, headers=headers)
            
            if "error" in response:
                error_msg = response.get("error", "")
                logger.error(f"Failed to place trailing stop order: {error_msg}")
                return OrderResponse(success=False, error=error_msg, raw_response=response)
            
            # Check success field
            if response.get("success") == False:
                error_code = response.get("errorCode", "Unknown")
                error_message = response.get("errorMessage", "No error message")
                logger.error(f"Trailing stop order failed: Error Code {error_code}, Message: {error_message}")
                return OrderResponse(success=False, error=f"Trailing stop order failed: {error_message} (Code: {error_code})", raw_response=response)
            
            order_id = response.get("orderId") or response.get("id")
            if not order_id:
                logger.error("API returned success but NO order ID (see DEBUG for full response).")
                logger.debug(f"Full response: {dumps_str(response)}")
                return OrderResponse(success=False, error="Order rejected: No order ID returned", raw_response=response)
            
            logger.info(f"✅ Trailing stop order placed successfully with ID: {order_id}")
            
            result = OrderResponse(
                success=True,
                order_id=str(order_id),
                message="Trailing stop order placed successfully",
                raw_response=response
            )
            
            if clamped:
                result.raw_response = {**(result.raw_response or {}), "clamped": True, "trail_price_used": trail_amount, "trail_ticks_used": int(max_ticks)}
            
            return result
            
        except Exception as e:
            logger.error(f"Failed to place trailing stop order: {str(e)}")
            return OrderResponse(success=False, error=str(e))
    
    async def _place_trailing_stop_rust(
        self,
        symbol: str,
        side: str,
        quantity: int,
        trail_amount: float,
        account_id: Optional[str]
    ) -> OrderResponse:
        """Place trailing stop order using Rust executor."""
        import time
        start_time = time.perf_counter()
        
        await self.auth.ensure_valid_token()
        
        if not account_id:
            return OrderResponse(success=False, error="Account ID is required")
        
        token = self.auth.get_token()
        self._rust_executor.set_token(token)
        
        try:
            contract_id = self.contract_manager.get_contract_id(symbol)
            self._rust_executor.set_contract_id(symbol, contract_id)
        except ValueError as e:
            return OrderResponse(
                success=False,
                error=f"Cannot place order: {e}. Please fetch contracts first."
            )
        
        # Get tick size
        tick_size = await self._get_tick_size(symbol)
        
        # Convert trail amount to ticks
        trail_ticks = int(trail_amount / tick_size)
        
        # Cap at 1000 ticks (TopStepX limit)
        trail_ticks = min(1000, trail_ticks)
        
        rust_result = await self._rust_executor.place_order(
            symbol=symbol,
            side=side,
            quantity=quantity,
            account_id=int(account_id),
            stop_loss_ticks=None,
            take_profit_ticks=None,
            limit_price=None,
            stop_price=None,
            trail_distance_ticks=trail_ticks,
            order_type="trailing",
            custom_tag=None
        )
        
        elapsed_ms = (time.perf_counter() - start_time) * 1000
        logger.info(f"⚡ Rust trailing stop execution: {elapsed_ms:.2f}ms")
        
        return OrderResponse(
            success=rust_result.get('success', False),
            order_id=rust_result.get('order_id'),
            message=rust_result.get('message'),
            error=rust_result.get('error'),
            raw_response=rust_result.get('raw_response')
        )
    
    async def create_bracket_order(
        self,
        symbol: str,
        side: str,
        quantity: int,
        stop_loss_price: Optional[float] = None,
        take_profit_price: Optional[float] = None,
        stop_loss_ticks: Optional[int] = None,
        take_profit_ticks: Optional[int] = None,
        account_id: Optional[str] = None,
        strategy_name: Optional[str] = None
    ) -> OrderResponse:
        """
        Create a native TopStepX bracket order with linked stop loss and take profit.
        
        Args:
            symbol: Trading symbol (e.g., "ES", "NQ", "MNQ", "YM")
            side: "BUY" or "SELL"
            quantity: Number of contracts
            stop_loss_price: Stop loss price (optional if stop_loss_ticks provided)
            take_profit_price: Take profit price (optional if take_profit_price provided)
            stop_loss_ticks: Stop loss in ticks (optional if stop_loss_price provided)
            take_profit_ticks: Take profit in ticks (optional if take_profit_price provided)
            account_id: Account ID
            strategy_name: Optional strategy name for tracking
            
        Returns:
            OrderResponse with order details
        """
        try:
            await self.auth.ensure_valid_token()
            
            if not account_id:
                return OrderResponse(success=False, error="Account ID is required")
            
            if side.upper() not in ["BUY", "SELL"]:
                return OrderResponse(success=False, error="Side must be 'BUY' or 'SELL'")
            
            logger.warning("⚠️  IMPORTANT: Bracket orders require 'Auto OCO Brackets' to be enabled in your TopStepX account settings.")
            logger.info(f"Creating bracket order for {side} {quantity} {symbol} on account {account_id}")
            logger.debug(f"Bracket order data: stop_loss_ticks={stop_loss_ticks}, take_profit_ticks={take_profit_ticks}")
            
            # Get contract ID
            try:
                contract_id = self.contract_manager.get_contract_id(symbol)
            except ValueError as e:
                error_msg = f"Cannot create bracket order: {e}. Please fetch contracts first."
                logger.error(f"❌ {error_msg}")
                return OrderResponse(success=False, error=error_msg)
            
            # Convert side to numeric value
            side_value = 0 if side.upper() == "BUY" else 1
            
            # Get tick size
            tick_size = await self._get_tick_size(symbol)
            logger.debug(f"Bracket context: contract={contract_id}, tick_size={tick_size}")
            
            # Calculate stop loss ticks from entry price if price provided
            if stop_loss_price is not None and stop_loss_ticks is None:
                try:
                    # Get current market price as entry price
                    quote = await self.get_market_quote(symbol)
                    # Check if quote is None or has error - handle both Quote object and dict cases
                    if quote is None:
                        logger.error(f"get_market_quote returned None for {symbol}")
                        return OrderResponse(success=False, error=f"Could not get market price for {symbol}. Market data is required for bracket orders.")
                    
                    # Handle Quote object (from adapter) or dict (from trading_bot)
                    from core.interfaces import Quote
                    if isinstance(quote, Quote):
                        # Quote object - access attributes directly
                        if not (quote.bid or quote.ask or quote.last):
                            logger.error(f"get_market_quote returned invalid Quote object: {quote}")
                            return OrderResponse(success=False, error=f"Could not get market price for {symbol}. Market data is required for bracket orders.")
                        if side.upper() == "BUY":
                            entry_price = float(quote.ask or quote.last or 0)
                        else:
                            entry_price = float(quote.bid or quote.last or 0)
                    elif isinstance(quote, dict):
                        # Dict format - check for errors and access with .get()
                        if "error" in quote:
                            logger.error(f"get_market_quote returned error: {quote.get('error')}")
                            return OrderResponse(success=False, error=f"Could not get market price for {symbol}: {quote.get('error')}")
                        if not (quote.get("bid") or quote.get("ask") or quote.get("last")):
                            logger.error(f"get_market_quote returned invalid data: {quote}")
                            return OrderResponse(success=False, error=f"Could not get market price for {symbol}. Market data is required for bracket orders.")
                        if side.upper() == "BUY":
                            entry_price = float(quote.get("ask") or quote.get("last") or 0)
                        else:
                            entry_price = float(quote.get("bid") or quote.get("last") or 0)
                    else:
                        logger.error(f"get_market_quote returned unexpected type: {type(quote)}")
                        return OrderResponse(success=False, error=f"Could not get market price for {symbol}. Market data is required for bracket orders.")
                    
                    logger.info(f"Using current market price as entry: ${entry_price:.2f}")
                    
                    # Validate stop_loss_price is a positive absolute price (not relative)
                    if stop_loss_price <= 0:
                        logger.error(f"Invalid stop_loss_price: {stop_loss_price}. Must be a positive absolute price.")
                        return OrderResponse(success=False, error=f"Stop loss price must be a positive absolute price, got: {stop_loss_price}")
                    
                    # Calculate stop loss ticks - must match TopStepX convention:
                    # BUY (long): stop loss is BELOW entry, ticks must be NEGATIVE
                    # SELL (short): stop loss is ABOVE entry, ticks must be POSITIVE
                    if side.upper() == "BUY":
                        # For long: stop loss below entry
                        if stop_loss_price >= entry_price:
                            logger.error(f"Invalid stop loss price for BUY: {stop_loss_price} >= entry {entry_price}. Stop loss must be below entry.")
                            return OrderResponse(success=False, error=f"Stop loss price ({stop_loss_price}) must be below entry price ({entry_price}) for BUY orders")
                        price_diff = entry_price - stop_loss_price  # Should be positive (entry > stop)
                        stop_loss_ticks = int(price_diff / tick_size)
                        # Ensure negative for long positions
                        if stop_loss_ticks > 0:
                            stop_loss_ticks = -stop_loss_ticks
                    else:  # SELL
                        # For short: stop loss above entry
                        if stop_loss_price <= entry_price:
                            logger.error(f"Invalid stop loss price for SELL: {stop_loss_price} <= entry {entry_price}. Stop loss must be above entry.")
                            return OrderResponse(success=False, error=f"Stop loss price ({stop_loss_price}) must be above entry price ({entry_price}) for SELL orders")
                        price_diff = stop_loss_price - entry_price  # Should be positive (stop > entry)
                        stop_loss_ticks = int(price_diff / tick_size)
                        # Ensure positive for short positions
                        if stop_loss_ticks < 0:
                            stop_loss_ticks = -stop_loss_ticks
                    
                    logger.debug(f"Stop Loss Calculation: Entry=${entry_price:.2f}, Target=${stop_loss_price:.2f}, Diff=${price_diff:.2f}, Ticks={stop_loss_ticks} (tick_size={tick_size})")
                    
                    # Validate sign based on side
                    if side.upper() == "BUY" and stop_loss_ticks > 0:
                        logger.warning(f"Stop loss ticks should be negative for BUY orders, correcting {stop_loss_ticks} to {-stop_loss_ticks}")
                        stop_loss_ticks = -stop_loss_ticks
                    elif side.upper() == "SELL" and stop_loss_ticks < 0:
                        logger.warning(f"Stop loss ticks should be positive for SELL orders, correcting {stop_loss_ticks} to {-stop_loss_ticks}")
                        stop_loss_ticks = -stop_loss_ticks
                    
                    # Validate and cap at 1000 ticks (preserve sign)
                    if abs(stop_loss_ticks) > 1000:
                        logger.warning(f"Stop loss ticks ({stop_loss_ticks}) exceeds 1000 limit, capping at {'-1000' if stop_loss_ticks < 0 else '1000'}")
                        stop_loss_ticks = -1000 if stop_loss_ticks < 0 else 1000
                except Exception as e:
                    logger.error(f"Failed to calculate stop loss ticks: {e}")
                    return OrderResponse(success=False, error=f"Failed to calculate stop loss ticks: {e}")
            
            # Calculate take profit ticks from entry price if price provided
            if take_profit_price is not None and take_profit_ticks is None:
                try:
                    quote = await self.get_market_quote(symbol)
                    # Check if quote is None or has error - handle both Quote object and dict cases
                    if quote is None:
                        return OrderResponse(success=False, error=f"Could not get market price for {symbol}. Market data is required for bracket orders.")
                    
                    # Handle Quote object (from adapter) or dict (from trading_bot)
                    from core.interfaces import Quote
                    if isinstance(quote, Quote):
                        # Quote object - access attributes directly
                        if not (quote.bid or quote.ask or quote.last):
                            return OrderResponse(success=False, error=f"Could not get market price for {symbol}. Market data is required for bracket orders.")
                        if side.upper() == "BUY":
                            entry_price = float(quote.ask or quote.last or 0)
                        else:
                            entry_price = float(quote.bid or quote.last or 0)
                    elif isinstance(quote, dict):
                        # Dict format - check for errors and access with .get()
                        if "error" in quote or not (quote.get("bid") or quote.get("ask") or quote.get("last")):
                            return OrderResponse(success=False, error=f"Could not get market price for {symbol}. Market data is required for bracket orders.")
                        if side.upper() == "BUY":
                            entry_price = float(quote.get("ask") or quote.get("last") or 0)
                        else:
                            entry_price = float(quote.get("bid") or quote.get("last") or 0)
                    else:
                        return OrderResponse(success=False, error=f"Could not get market price for {symbol}. Market data is required for bracket orders.")
                    
                    # Calculate take profit ticks - must match TopStepX convention:
                    # BUY (long): take profit is ABOVE entry, ticks must be POSITIVE
                    # SELL (short): take profit is BELOW entry, ticks must be NEGATIVE
                    if side.upper() == "BUY":
                        # For long: take profit above entry
                        if take_profit_price <= entry_price:
                            logger.error(f"Invalid take profit price for BUY: {take_profit_price} <= entry {entry_price}. Take profit must be above entry.")
                            return OrderResponse(success=False, error=f"Take profit price ({take_profit_price}) must be above entry price ({entry_price}) for BUY orders")
                        price_diff = take_profit_price - entry_price  # Should be positive (profit > entry)
                        take_profit_ticks = int(price_diff / tick_size)
                        # Ensure positive for long positions
                        if take_profit_ticks < 0:
                            take_profit_ticks = -take_profit_ticks
                    else:  # SELL
                        # For short: take profit below entry
                        if take_profit_price >= entry_price:
                            logger.error(f"Invalid take profit price for SELL: {take_profit_price} >= entry {entry_price}. Take profit must be below entry.")
                            return OrderResponse(success=False, error=f"Take profit price ({take_profit_price}) must be below entry price ({entry_price}) for SELL orders")
                        price_diff = entry_price - take_profit_price  # Should be positive (entry > profit)
                        take_profit_ticks = int(price_diff / tick_size)
                        # Ensure negative for short positions
                        if take_profit_ticks > 0:
                            take_profit_ticks = -take_profit_ticks
                    
                    # Validate take_profit_price is a positive absolute price (not relative)
                    if take_profit_price <= 0:
                        logger.error(f"Invalid take_profit_price: {take_profit_price}. Must be a positive absolute price.")
                        return OrderResponse(success=False, error=f"Take profit price must be a positive absolute price, got: {take_profit_price}")
                    
                    logger.debug(f"Take Profit Calculation: Entry=${entry_price:.2f}, Target=${take_profit_price:.2f}, Diff=${price_diff:.2f}, Ticks={take_profit_ticks} (tick_size={tick_size})")
                    
                    # Validate sign based on side
                    if side.upper() == "BUY" and take_profit_ticks < 0:
                        logger.warning(f"Take profit ticks should be positive for BUY orders, correcting {take_profit_ticks} to {-take_profit_ticks}")
                        take_profit_ticks = -take_profit_ticks
                    elif side.upper() == "SELL" and take_profit_ticks > 0:
                        logger.warning(f"Take profit ticks should be negative for SELL orders, correcting {take_profit_ticks} to {-take_profit_ticks}")
                        take_profit_ticks = -take_profit_ticks
                    
                    # Validate and cap at 1000 ticks (preserve sign)
                    if abs(take_profit_ticks) > 1000:
                        logger.warning(f"Take profit ticks ({take_profit_ticks}) exceeds 1000 limit, capping at {'-1000' if take_profit_ticks < 0 else '1000'}")
                        take_profit_ticks = -1000 if take_profit_ticks < 0 else 1000
                except Exception as e:
                    logger.error(f"Failed to calculate take profit ticks: {e}")
                    return OrderResponse(success=False, error=f"Failed to calculate take profit ticks: {e}")
            
            # Validate that we have ticks
            if stop_loss_ticks is None and take_profit_ticks is None:
                return OrderResponse(success=False, error="Either stop_loss_price/take_profit_price or stop_loss_ticks/take_profit_ticks must be provided")
            
            # Prepare order data
            order_data = {
                "accountId": int(account_id),
                "contractId": contract_id,
                "type": 2,  # Market order for entry
                "side": side_value,
                "size": quantity,
                "customTag": self._generate_unique_custom_tag("bracket", strategy_name)
            }
            
            # Add bracket orders
            # NOTE: Do NOT set reduceOnly=True on brackets for entry orders!
            # The market entry order hasn't filled yet, so there's no position to reduce.
            # TopStepX automatically handles bracket attachment once the entry fills.
            if stop_loss_ticks is not None:
                order_data["stopLossBracket"] = {
                    "ticks": stop_loss_ticks,
                    "type": 4,  # Stop loss type
                    "size": quantity
                    # reduceOnly removed - brackets auto-attach after entry fills
                }
                logger.debug(f"Added stop loss bracket: {stop_loss_ticks} ticks, size: {quantity}")
            
            if take_profit_ticks is not None:
                order_data["takeProfitBracket"] = {
                    "ticks": take_profit_ticks,
                    "type": 1,  # Take profit type
                    "size": quantity
                    # reduceOnly removed - brackets auto-attach after entry fills
                }
                logger.debug(f"Added take profit bracket: {take_profit_ticks} ticks, size: {quantity}")
            
            # Ensure token is valid before making request
            await self.auth.ensure_valid_token()
            
            # Forensic snapshot: last/bid/ask + full place payload.
            mkt_entry = locals().get("entry_price")
            mkt_sl = locals().get("stop_loss_price")
            mkt_tp = locals().get("take_profit_price")
            mkt_tick = locals().get("tick_size")
            quote_snap = await self._log_bracket_order_diag(
                event="place",
                symbol=symbol,
                side=side,
                order_data=order_data,
                entry_price=mkt_entry if isinstance(mkt_entry, (int, float)) else None,
                stop_loss_price=mkt_sl if isinstance(mkt_sl, (int, float)) else None,
                take_profit_price=mkt_tp if isinstance(mkt_tp, (int, float)) else None,
                tick_size=mkt_tick if isinstance(mkt_tick, (int, float)) else None,
                path="python_market_entry",
            )
            
            # Make API call
            headers = {
                "accept": "text/plain",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.auth.get_token()}"
            }
            
            response = await self._make_request("POST", "/api/Order/place", data=order_data, headers=headers)
            
            # If we got 500 errors and retries are exhausted, try refreshing token and retrying once
            if "error" in response and response.get("retry_exhausted") and response.get("status_code") == 500:
                logger.warning("⚠️  Received 500 errors, attempting token refresh and retry...")
                # Refresh token
                if await self.auth.authenticate():
                    logger.info("Token refreshed, retrying bracket order...")
                    # Update headers with new token
                    headers["Authorization"] = f"Bearer {self.auth.get_token()}"
                    # Retry once more
                    response = await self._make_request("POST", "/api/Order/place", data=order_data, headers=headers)
                    if "error" in response:
                        await self._log_bracket_order_diag(
                            event="reject",
                            symbol=symbol,
                            side=side,
                            order_data=order_data,
                            quote=quote_snap,
                            entry_price=mkt_entry if isinstance(mkt_entry, (int, float)) else None,
                            stop_loss_price=mkt_sl if isinstance(mkt_sl, (int, float)) else None,
                            take_profit_price=mkt_tp if isinstance(mkt_tp, (int, float)) else None,
                            tick_size=mkt_tick if isinstance(mkt_tick, (int, float)) else None,
                            response=response,
                            error=str(response.get("error")),
                            path="python_market_entry",
                        )
                        logger.error(f"Failed to create bracket order after token refresh: {response['error']}")
                        return OrderResponse(success=False, error=response['error'], raw_response=response)
                else:
                    logger.error("Failed to refresh token, cannot retry bracket order")
                    return OrderResponse(success=False, error="Failed to refresh token after 500 errors", raw_response=response)
            
            if "error" in response:
                await self._log_bracket_order_diag(
                    event="reject",
                    symbol=symbol,
                    side=side,
                    order_data=order_data,
                    quote=quote_snap,
                    entry_price=mkt_entry if isinstance(mkt_entry, (int, float)) else None,
                    stop_loss_price=mkt_sl if isinstance(mkt_sl, (int, float)) else None,
                    take_profit_price=mkt_tp if isinstance(mkt_tp, (int, float)) else None,
                    tick_size=mkt_tick if isinstance(mkt_tick, (int, float)) else None,
                    response=response,
                    error=str(response.get("error")),
                    path="python_market_entry",
                )
                logger.error(f"Failed to create bracket order: {response['error']}")
                return OrderResponse(success=False, error=response['error'], raw_response=response)
            
            # Check success field
            if response.get("success") == False:
                error_code = response.get("errorCode", "Unknown")
                error_message = response.get("errorMessage", "No error message")
                await self._log_bracket_order_diag(
                    event="reject",
                    symbol=symbol,
                    side=side,
                    order_data=order_data,
                    quote=quote_snap,
                    entry_price=mkt_entry if isinstance(mkt_entry, (int, float)) else None,
                    stop_loss_price=mkt_sl if isinstance(mkt_sl, (int, float)) else None,
                    take_profit_price=mkt_tp if isinstance(mkt_tp, (int, float)) else None,
                    tick_size=mkt_tick if isinstance(mkt_tick, (int, float)) else None,
                    response=response,
                    error=f"Code {error_code}: {error_message}",
                    path="python_market_entry",
                )
                logger.error(f"Bracket order failed: Error Code {error_code}, Message: {error_message}")
                
                # If bracket order fails due to tick limits, try a regular market order
                if "ticks" in error_message.lower() and "1000" in error_message:
                    logger.warning("Bracket order failed due to tick limits, falling back to regular market order")
                    fallback_result = await self.place_market_order(symbol, side, quantity, account_id=account_id)
                    if fallback_result.success:
                        logger.info("Fallback market order placed successfully")
                        return OrderResponse(
                            success=True,
                            order_id=fallback_result.order_id,
                            message="Bracket order failed due to tick limits, placed regular market order instead",
                            raw_response=fallback_result.raw_response
                        )
                    else:
                        return OrderResponse(success=False, error=f"Both bracket order and fallback market order failed. Bracket: {error_message}, Market: {fallback_result.error}")
                else:
                    return OrderResponse(success=False, error=f"Bracket order failed: {error_message} (Code: {error_code})", raw_response=response)
            
            order_id = response.get("orderId") or response.get("id")
            if not order_id:
                await self._log_bracket_order_diag(
                    event="reject",
                    symbol=symbol,
                    side=side,
                    order_data=order_data,
                    quote=quote_snap,
                    entry_price=mkt_entry if isinstance(mkt_entry, (int, float)) else None,
                    stop_loss_price=mkt_sl if isinstance(mkt_sl, (int, float)) else None,
                    take_profit_price=mkt_tp if isinstance(mkt_tp, (int, float)) else None,
                    tick_size=mkt_tick if isinstance(mkt_tick, (int, float)) else None,
                    response=response,
                    error="No order ID returned",
                    path="python_market_entry",
                )
                logger.error("API returned success but NO order ID (see DEBUG for full response).")
                logger.debug(f"Full response: {dumps_str(response)}")
                return OrderResponse(success=False, error="Order rejected: No order ID returned", raw_response=response)
            
            await self._log_bracket_order_diag(
                event="accept",
                symbol=symbol,
                side=side,
                order_data=order_data,
                quote=quote_snap,
                entry_price=mkt_entry if isinstance(mkt_entry, (int, float)) else None,
                stop_loss_price=mkt_sl if isinstance(mkt_sl, (int, float)) else None,
                take_profit_price=mkt_tp if isinstance(mkt_tp, (int, float)) else None,
                tick_size=mkt_tick if isinstance(mkt_tick, (int, float)) else None,
                response={"orderId": order_id, "success": True},
                path="python_market_entry",
            )
            logger.info(f"✅ Bracket order created successfully with ID: {order_id}")
            
            return OrderResponse(
                success=True,
                order_id=str(order_id),
                message="Bracket order created successfully",
                raw_response=response
            )
            
        except Exception as e:
            logger.error(f"Failed to create bracket order: {str(e)}")
            return OrderResponse(success=False, error=str(e))
    
    # ============================================================================
    # HELPER METHODS
    # ============================================================================
    
    async def _get_tick_size(self, symbol: str) -> float:
        """
        Get the tick size for a trading symbol.
        
        Args:
            symbol: Trading symbol (e.g., "ES", "NQ", "MNQ", "YM")
            
        Returns:
            float: Tick size for the symbol
        """
        symbol = symbol.upper()
        
        # Tick sizes for common futures contracts
        tick_sizes = {
            "ES": 0.25,      # E-mini S&P 500
            "MES": 0.25,     # Micro E-mini S&P 500
            "NQ": 0.25,      # E-mini NASDAQ-100
            "MNQ": 0.25,     # Micro E-mini NASDAQ-100
            "YM": 1.0,       # E-mini Dow Jones
            "MYM": 0.5,      # Micro E-mini Dow Jones (0.5 point ticks)
            "RTY": 0.1,      # E-mini Russell 2000
            "M2K": 0.1,      # Micro E-mini Russell 2000
            "CL": 0.01,      # Crude Oil
            "NG": 0.001,     # Natural Gas
            "GC": 0.1,       # Gold
            "SI": 0.005,     # Silver
            "MGC": 0.1,      # Micro Gold
        }
        
        if symbol in tick_sizes:
            base_ts = tick_sizes[symbol]
            # Hard guard for critical micros
            hard_map = {"MNQ": 0.25, "MES": 0.25, "MYM": 0.5, "MGC": 0.1}
            if symbol in hard_map and base_ts != hard_map[symbol]:
                logger.warning(f"Hard guard: overriding tick size for {symbol} to {hard_map[symbol]} (was {base_ts})")
                return hard_map[symbol]
            return base_ts
        
        # Try to discover tick size from contract metadata via API
        try:
            contracts = await self.get_available_contracts()
            for c in contracts or []:
                sym = (c.get("symbol") or c.get("name") or "").upper()
                cid = (c.get("contractId") or c.get("id") or "").upper()
                if symbol in sym or f".{symbol}." in cid:
                    for key in ("tickSize", "minTick", "priceIncrement", "minimumPriceIncrement", "tick"):
                        if c.get(key):
                            try:
                                ts = float(c.get(key))
                                if ts > 0:
                                    hard_map = {"MNQ": 0.25, "MES": 0.25, "MYM": 0.5, "MGC": 0.1}
                                    if symbol in hard_map and abs(ts - hard_map[symbol]) > 1e-9:
                                        logger.warning(f"Hard guard: API tick for {symbol}={ts} differs from expected {hard_map[symbol]}; using expected")
                                        return hard_map[symbol]
                                    logger.info(f"Discovered tick size from API for {symbol}: {ts} (key {key})")
                                    return ts
                            except Exception:
                                pass
        except Exception as e:
            logger.debug(f"Tick size discovery via API failed for {symbol}: {e}")
        
        # Default tick size
        hard_map = {"MNQ": 0.25, "MES": 0.25, "MYM": 0.5, "MGC": 0.1}
        if symbol in hard_map:
            logger.warning(f"Hard guard default: using {hard_map[symbol]} for {symbol}")
            return hard_map[symbol]
        logger.warning(f"Unknown symbol {symbol}, using default tick size: 0.25")
        return 0.25
    
    def _round_to_tick_size(self, price: float, tick_size: float) -> float:
        """Round price to nearest valid tick size."""
        if tick_size <= 0:
            return price
        return round(price / tick_size) * tick_size
    
    def _generate_unique_custom_tag(self, order_type: str = "order", strategy_name: Optional[str] = None) -> str:
        """
        Generate a unique custom tag for orders.
        
        Args:
            order_type: Type of order (e.g., "market", "stop_bracket", "bracket")
            strategy_name: Optional strategy name to include in tag for tracking
        
        Returns:
            str: Unique custom tag
        """
        from datetime import datetime
        import uuid
        
        # IMPORTANT: TopStepX appears to have a max length / validation on customTag.
        # Too-long tags can cause opaque HTTP 500 responses (sometimes with empty body).
        # Keep tags short, deterministic, and ASCII-safe.
        base_tag = f"TB-{order_type}"
        if strategy_name:
            # Sanitize and shorten strategy name
            safe = ''.join(ch if ch.isalnum() or ch in ('_', '-') else '_' for ch in str(strategy_name).lower())
            if len(safe) > 16:
                safe = safe[:16]
            base_tag += f"-{safe}"
        
        # Add timestamp and unique ID for uniqueness
        timestamp = datetime.now().strftime("%y%m%d%H%M%S")  # shorter
        unique_id = str(uuid.uuid4())[:6]  # shorter

        tag = f"{base_tag}-{timestamp}-{unique_id}"
        # Hard cap
        if len(tag) > 64:
            tag = tag[:64]
        return tag

