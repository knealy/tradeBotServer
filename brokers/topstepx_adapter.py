# -*- coding: utf-8 -*-
"""
TopStepX Broker Adapter - Implements broker interfaces for TopStepX API.

This adapter implements the translation layer interfaces, allowing
the trading bot to work with TopStepX while remaining broker-agnostic.
"""

import os
import json
import asyncio
import logging
from typing import Optional, List, Dict, Any, Tuple
from datetime import datetime, timedelta, timezone

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
        
        # Use auth manager's HTTP session
        self._http_session = auth_manager._http_session
        
        # Historical data cache to prevent duplicate requests
        # Key: (symbol, timeframe, limit, start_time_str, end_time_str)
        # Value: (bars, timestamp)
        self._historical_cache: Dict[Tuple[str, str, int, Optional[str], Optional[str]], Tuple[List[Bar], float]] = {}
        self._cache_lock = asyncio.Lock()
        self._cache_ttl_seconds = 5  # Cache for 5 seconds to prevent duplicate requests

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
        
        logger.debug("TopStepX adapter initialized")
    
    def _make_request(
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
        return self.auth._make_request(method, endpoint, data, request_headers, timeout)
    
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

        # Prepare order data for TopStepX API
        order_data = {
            "accountId": int(account_id),
            "contractId": contract_id,
            "type": order_type_value,
            "side": side_value,
            "size": quantity,
            "limitPrice": limit_price if order_type == "limit" else None,
            "stopPrice": stop_price if order_type == "stop" else None,
            "reduceOnly": reduce_only  # CRITICAL: When true, order auto-cancels if position closes
        }

        # Add custom tag if provided
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
        
        # Log order details
        logger.info(f"Order data: {json.dumps({k: v for k, v in order_data.items() if v is not None}, indent=2)}")
        
        # Make API call
        headers = {
            "accept": "text/plain",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.auth.get_token()}"
        }
        
        response = self._make_request("POST", "/api/Order/place", data=order_data, headers=headers)
        
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
                response = self._make_request("POST", "/api/Order/place", data=order_data, headers=headers)
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
            logger.error(f"API returned success but NO order ID! Full response: {json.dumps(response, indent=2)}")
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

        # Get order info to determine type and check if it's a bracket order
        order_info = None
        if quantity is not None or price is not None:
            # Get open orders to find this order
            open_orders = await self.get_open_orders(account_id=account_id)
            for order in open_orders:
                if str(order.get("id", "")) == str(order_id):
                    order_info = order
                    break

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
                actual_order_type = order_info.get("type", order_type)
            elif order_type is not None:
                actual_order_type = order_type
            else:
                # Need to fetch order type
                if order_info is None:
                    open_orders = await self.get_open_orders(account_id=account_id)
                    for order in open_orders:
                        if str(order.get("id", "")) == str(order_id):
                            order_info = order
                            break
                actual_order_type = order_info.get("type") if order_info else 1  # Default to limit

            if actual_order_type == 4:  # Stop order
                modify_data["stopPrice"] = price
            else:  # Limit order or other types
                modify_data["limitPrice"] = price

        response = self._make_request("POST", "/api/Order/modify", data=modify_data, headers=headers)

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
        
        response = self._make_request("POST", "/api/Order/cancel", data=cancel_data, headers=headers)
        
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
            
            logger.info(f"Fetching open orders for account {account_id}")
            
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
            
            response = self._make_request("POST", "/api/Order/search", data=search_data, headers=headers)
            
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
                logger.info(f"No open orders found for account {account_id}")
                return []
            # IMPORTANT: do NOT filter by status==1.
            # TopStepX "Open" search can include related bracket child orders that are "SuspENDED"
            # until the parent triggers. Filtering would hide those brackets from the GUI.
            logger.info(f"Found {len(orders)} open-related orders (includes suspended brackets when returned by API)")
            return orders
            
        except Exception as e:
            logger.error(f"Failed to fetch orders: {str(e)}")
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
        logger.info(f"Found {len(orders)} open-related orders via Rust (includes suspended brackets when returned by API)")
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
            
            logger.info(f"Fetching order history for account {account_id}")
            
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
            
            response = self._make_request("POST", "/api/Order/search", data=search_data, headers=headers)
            
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
                logger.info(f"No historical orders found for account {account_id}")
                return []
            
            # Filter to only filled/executed orders for history (status == 2)
            filled_orders = [o for o in orders if o.get("status") == 2 and o.get("fillVolume", 0) > 0]
            
            # Try Fill/search endpoint if no filled orders
            if not filled_orders:
                logger.debug("No filled orders from Order/search, trying Fill/search endpoint")
                fill_search_data = {
                    "accountId": int(account_id),
                    "startTime": start_time.isoformat(),
                    "endTime": end_time.isoformat(),
                    "limit": limit
                }
                
                fill_response = self._make_request("POST", "/api/Fill/search", data=fill_search_data, headers=headers)
                
                if fill_response and "error" not in fill_response and fill_response.get("success"):
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
            
            # Limit results
            if len(filled_orders) > limit:
                filled_orders = filled_orders[:limit]
            
            logger.info(f"Found {len(filled_orders)} historical filled orders")
            return filled_orders
            
        except Exception as e:
            logger.error(f"Failed to fetch order history: {str(e)}")
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
            
            response = self._make_request("POST", "/api/Position/searchOpen", data=search_data, headers=headers)
            
            if "error" in response:
                logger.error(f"Failed to fetch positions: {response['error']}")
                return []
            
            if not response.get("success"):
                logger.error(f"API returned error: {response}")
                return []
            
            positions_data = response.get("positions", [])
            if not positions_data:
                logger.info(f"No open positions found for account {account_id}")
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
            logger.error(f"Failed to fetch positions: {str(e)}")
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
            
            logger.info(f"Fetching position details for position {position_id}")
            
            headers = {
                "accept": "text/plain",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.auth.get_token()}"
            }
            
            response = self._make_request("GET", f"/api/Position/{position_id}", headers=headers)
            
            if "error" in response:
                logger.error(f"Failed to fetch position details: {response['error']}")
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
            
            response = self._make_request("POST", "/api/Position/closeContract", data=close_data, headers=headers)
            
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
                import time
                cache_key = (
                    symbol_up,
                    timeframe,
                    limit,
                    start_time.isoformat() if start_time else None,
                    end_time.isoformat() if end_time else None
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
                return await self._fetch_historical_data_impl(
                    symbol_up, timeframe, limit, start_time, end_time, _skip_aggregation, **kwargs
                )
            
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
                import time
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
        **kwargs
    ) -> List[Bar]:
        """
        Internal implementation to fetch historical data.
        This contains the actual API call logic.
        """
        try:
            symbol_up = symbol.upper()
            
            # Get contract ID
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
            
            timeframe_lower = timeframe.lower()
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
            # This is especially important when market is closed to get data up to last close
            timeframe_lower = timeframe.lower()
            if timeframe_lower.endswith("s") or timeframe_lower in ["1m", "2m", "3m", "5m", "10m"]:
                current_time = datetime.now(timezone.utc)
                time_diff = (current_time - end_time).total_seconds()
                # Get the threshold based on timeframe
                if timeframe_lower.endswith("s"):
                    threshold = max(int(timeframe_lower[:-1]) * 2, 60)  # At least 1 minute for seconds
                elif timeframe_lower in ["1m", "2m"]:
                    threshold = 120  # 2 minutes
                else:
                    threshold = 600  # 10 minutes for 5m/10m
                
                # If end_time is older than threshold, update it
                if time_diff > threshold:
                    # Use the earlier of current time or last market close
                    potential_end = min(current_time, last_close)
                    if potential_end > end_time:
                        logger.info(
                            f"📊 {timeframe} timeframe: end_time is {time_diff:.0f}s old, updating to {potential_end} for fresh data"
                        )
                        end_time = potential_end
            
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
            
            bars_request = {
                "contractId": contract_id,
                "live": False,
                "startTime": start_str,
                "endTime": end_str,
                "unit": unit,
                "unitNumber": unit_number,
                "limit": api_limit,
                "includePartialBar": True
            }
            
            logger.debug(f"Fetching {timeframe} bars for {symbol_up} from {start_str} to {end_str}")
            try:
                logger.debug(f"🔍 History API request: {json.dumps(bars_request, indent=2)}")
            except Exception as e:
                logger.debug(f"🔍 History API request (json serialization failed): {bars_request}")
            
            response = self._make_request("POST", "/api/History/retrieveBars", data=bars_request, headers=headers)
            
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
                        response_str = json.dumps(response, indent=2, default=str)
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
                        response_str = json.dumps(response, indent=2, default=str)
                        logger.debug(f"🔍 Full response structure: {response_str[:1000]}")
                    except Exception as e:
                        logger.debug(f"🔍 Full response (json serialization failed): {str(response)[:1000]}")
            
            if not bars_data:
                logger.warning("API returned empty bars data")
                logger.warning(f"Request was: contractId={contract_id}, startTime={start_str}, endTime={end_str}, unit={unit}, unitNumber={unit_number}, limit={api_limit}")
                
                # If we got an empty response and we're in bar count mode, try adjusting the time range
                # to avoid weekends/closed periods
                if not original_start_time_provided and isinstance(response, dict) and response.get('success') != False:
                    logger.info("🔄 Empty response in bar count mode - trying to adjust time range to avoid closed periods")
                    # Try going back further to ensure we hit market-open periods
                    # For weekends, go back to last Friday's market open
                    import pytz
                    et_tz = pytz.timezone('US/Eastern')
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
                            
                            # Retry the request with adjusted time range
                            bars_request = {
                                "contractId": contract_id,
                                "live": False,
                                "startTime": start_str,
                                "endTime": end_str,
                                "unit": unit,
                                "unitNumber": unit_number,
                                "limit": api_limit,
                                "includePartialBar": True
                            }
                            
                            logger.info(f"🔄 Retrying with adjusted time range: startTime={start_str}, endTime={end_str}")
                            response = self._make_request("POST", "/api/History/retrieveBars", data=bars_request, headers=headers)
                            
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
            # This ensures accurate, up-to-date data using reliable 1m source
            # Skip aggregation if we're already fetching 1m data (prevents recursion)
            target_seconds = self._parse_timeframe_to_seconds(timeframe)
            if not _skip_aggregation and target_seconds and target_seconds > 60:
                logger.debug(f"📊 Using 1m aggregation strategy: will fetch 1m data and aggregate to {timeframe}")
                
                # Fetch 1m data instead (with _skip_aggregation=True to prevent recursion)
                one_min_bars = await self.get_historical_data(
                    symbol=symbol,
                    timeframe="1m",
                    limit=limit * (target_seconds // 60) + 100,  # Request enough 1m bars
                    start_time=start_time,
                    end_time=end_time,
                    _skip_aggregation=True,  # Prevent recursion
                    **kwargs
                )
                
                if not one_min_bars:
                    logger.warning(f"No 1m data available for aggregation to {timeframe}")
                    return []
                
                logger.debug(f"📊 Aggregating {len(one_min_bars)} 1m bars into {timeframe} bars...")
                
                # Use Rust aggregation if available, otherwise Python fallback
                if RUST_AVAILABLE and self._use_rust:
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
                            # Adjust timestamp for daily bars to use correct market hours
                            if timeframe.endswith('d'):
                                bar_start = self._get_daily_bar_start_time(dt)
                                # For display, use the trading day's date (next day if starts at 18:00)
                                dt = self._get_daily_bar_display_date(bar_start)
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
                    dt = datetime.fromtimestamp(bar_dict['timestamp'], tz=timezone.utc)
                    # Adjust timestamp for daily bars to use correct market hours
                    if timeframe.endswith('d'):
                        bar_start = self._get_daily_bar_start_time(dt)
                        # For display, use the trading day's date (next day if starts at 18:00)
                        dt = self._get_daily_bar_display_date(bar_start)
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
            
            # CRITICAL: Sort by timestamp (oldest first) before limiting
            # This ensures we always get the most recent bars, regardless of API response order
            bars.sort(key=lambda b: b.timestamp if b.timestamp else datetime.min.replace(tzinfo=timezone.utc))
            
            # Limit to requested number (only in bar count mode, not date range mode)
            # In date range mode, return ALL bars between the dates
            if not original_start_time_provided and len(bars) > limit:
                bars = bars[-limit:]
            
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
    
    def _get_daily_bar_start_time(self, timestamp: datetime) -> datetime:
        """
        Get the start time for a daily bar based on EST market hours.
        
        Rules:
        - Every day opens at 18:00 ET (6pm) the previous day
        - Every day closes at 17:00 ET (5pm) that day
        
        Examples:
        - Monday bar: Sunday 18:00 ET to Monday 17:00 ET
        - Tuesday bar: Monday 18:00 ET to Tuesday 17:00 ET
        - Wednesday bar: Tuesday 18:00 ET to Wednesday 17:00 ET
        - Thursday bar: Wednesday 18:00 ET to Thursday 17:00 ET
        - Friday bar: Thursday 18:00 ET to Friday 17:00 ET
        """
        try:
            import pytz
            et_tz = pytz.timezone('US/Eastern')
        except ImportError:
            # Fallback if pytz not available
            et_tz = timezone(timedelta(hours=-5))  # EST offset (approximate)
        
        # Convert to EST
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        timestamp_et = timestamp.astimezone(et_tz)
        
        weekday = timestamp_et.weekday()  # 0=Monday, 1=Tuesday, ..., 6=Sunday
        hour = timestamp_et.hour
        
        # Calculate daily bar start - simplified logic
        # If before 17:00 (5pm), we're still in today's bar (which started yesterday 18:00)
        # If at or after 17:00 (5pm), we're in tomorrow's bar (which starts today 18:00)
        if hour < 17:
            # Before 17:00 - still in today's bar, which started yesterday 18:00
            days_back = 1
            bar_start_et = (timestamp_et - timedelta(days=days_back)).replace(hour=18, minute=0, second=0, microsecond=0)
        else:
            # At or after 17:00 - this is tomorrow's bar, which starts today 18:00
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
        try:
            import pytz
            et_tz = pytz.timezone('US/Eastern')
        except ImportError:
            et_tz = timezone(timedelta(hours=-5))
        
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
        """
        if not bars:
            return []
        
        target_seconds = self._parse_timeframe_to_seconds(target_timeframe)
        if target_seconds is None or target_seconds <= 60:
            return bars
        
        aggregated = []
        current_group = []
        current_group_start = None
        
        for bar in bars:
            ts = bar.get('timestamp') or bar.get('time')
            # Special handling for daily bars
            if target_timeframe.endswith('d'):
                dt = datetime.fromtimestamp(ts, tz=timezone.utc)
                bar_start_dt = self._get_daily_bar_start_time(dt)
                bar_start_seconds = int(bar_start_dt.timestamp())
            else:
                bar_start_seconds = (ts // target_seconds) * target_seconds
            
            if current_group_start is None or bar_start_seconds != current_group_start:
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
                "unit": 2,  # Minutes
                "unitNumber": 1,
                "limit": 5,
                "includePartialBar": True
            }
            
            response = self._make_request("POST", "/api/History/retrieveBars", data=bars_request, headers=headers)
            
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
        logger.info(f"⚡ Rust get_market_quote execution: {elapsed_ms:.2f}ms")
        
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
                    resp = self._make_request("GET", endpoint, headers=headers)
                    if resp and "error" not in resp and resp != {"success": True, "message": "Operation completed successfully"}:
                        response = resp
                        break
                except Exception:
                    continue
            
            # If no specific endpoint worked, try generic with contract ID
            if not response:
                for endpoint in ["/api/MarketData/depth", "/api/MarketData/orderbook", "/api/MarketData/level2"]:
                    try:
                        resp = self._make_request("POST", endpoint, data={"contractId": contract_id}, headers=headers)
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
        logger.info(f"⚡ Rust get_market_depth execution: {elapsed_ms:.2f}ms")
        
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
            
            cache_ttl_minutes = kwargs.get('cache_ttl_minutes', 60)
            
            # Check cache first if enabled
            if use_cache:
                cache = self.contract_manager.get_contract_cache()
                if cache:
                    from datetime import datetime, timedelta
                    cache_age = datetime.now() - cache['timestamp']
                    if cache_age < timedelta(minutes=cache_ttl_minutes):
                        logger.debug(f"Using cached contract list ({len(cache['contracts'])} contracts)")
                        return cache['contracts'].copy()
            
            logger.info("Fetching available contracts...")
            
            headers = {
                "accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.auth.get_token()}"
            }
            
            response = self._make_request(
                "POST",
                "/api/Contract/available",
                data={"live": False},
                headers=headers
            )
            
            if isinstance(response, dict) and response.get("error"):
                logger.error(f"API error: {response['error']}")
                return []
            
            contracts = response if isinstance(response, list) else response.get("contracts", [])
            
            if not contracts:
                logger.warning("No contracts returned from API")
                return []
            
            # Update contract cache
            self.contract_manager.set_contract_cache(contracts, cache_ttl_minutes)
            
            logger.info(f"✅ Retrieved {len(contracts)} available contracts")
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
                    logger.warning(f"⚠️  Rust OCO bracket returned failure, falling back to Python: {getattr(rust_resp, 'error', None)}")
                except Exception as e:
                    logger.warning(f"⚠️  Rust execution failed, falling back to Python: {e}")
                    import traceback
                    logger.debug(f"Rust error traceback: {traceback.format_exc()}")
            
            # Python fallback
            logger.info("🔄 Using Python fallback for stop bracket order")
            print("🔄 Using Python fallback for stop bracket order (Rust path unavailable)")
            
            # Ensure valid token before placing order
            print("🔐 Ensuring valid token before placing order...")
            token_valid = await self.auth.ensure_valid_token()
            if not token_valid:
                error_msg = "Failed to ensure valid token before placing order"
                logger.error(f"❌ {error_msg}")
                print(f"❌ {error_msg}")
                return OrderResponse(success=False, error=error_msg)
            print("✅ Token validated")
            
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
            
            logger.debug(f"Stop Loss: {stop_loss_ticks} ticks, Take Profit: {take_profit_ticks} ticks")
            
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
            
            # Debug: Log order parameters
            logger.debug(f"Stop bracket order data: {json.dumps(order_data, indent=2)}")
            print(f"📋 Order parameters:")
            print(f"   Symbol: {symbol}, Side: {side}, Qty: {quantity}")
            print(f"   Entry (Stop): ${entry_price:.2f}")
            print(f"   Stop Loss: {stop_loss_ticks} ticks (${stop_loss_price:.2f})")
            print(f"   Take Profit: {take_profit_ticks} ticks (${take_profit_price:.2f})")
            
            # Make API call
            headers = {
                "accept": "text/plain",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.auth.get_token()}"
            }
            
            response = self._make_request("POST", "/api/Order/place", data=order_data, headers=headers)
            
            # Handle 500 errors with automatic token refresh and retry
            if "error" in response and ("500" in str(response.get("error", "")) or response.get("status_code") == 500):
                logger.warning("⚠️  Received 500 error on order placement. Attempting token refresh and retry...")
                print("⚠️  Received 500 error. This might indicate:")
                print("   1. TopStepX server is temporarily unavailable")
                print("   2. Token may have expired (force refreshing token...)")
                print("   3. Account settings issue (check 'Auto OCO Brackets' for bracket orders)")
                
                # Force token refresh even if it appears valid (server might have invalidated it)
                token_refreshed = await self.auth.ensure_valid_token(force_refresh=True)
                if token_refreshed:
                    headers["Authorization"] = f"Bearer {self.auth.get_token()}"
                    await asyncio.sleep(0.75)
                    logger.info("🔄 Retrying order placement with refreshed token...")
                    print("🔄 Retrying order placement with refreshed token...")
                    response = self._make_request("POST", "/api/Order/place", data=order_data, headers=headers)
                    
                    # Check if retry succeeded
                    if "error" in response and "500" in str(response.get("error", "")):
                        logger.error("❌ Retry also failed with 500 error. This likely indicates:")
                        logger.error("   1. Server issue - TopStepX API may be temporarily down")
                        logger.error("   2. Account settings - 'Auto OCO Brackets' may not be enabled")
                        logger.error("   3. Invalid order parameters - check prices and tick sizes")
                        print("❌ Retry also failed with 500 error")
                        print("   This likely indicates a server issue or account settings problem")
                        print("   Please check:")
                        print("   - TopStepX account settings: 'Auto OCO Brackets' must be enabled")
                        print(f"   - Order parameters: Entry=${entry_price:.2f}, SL=${stop_loss_price:.2f}, TP=${take_profit_price:.2f}")
                else:
                    logger.error("❌ Failed to refresh token, cannot retry")
                    print("❌ Failed to refresh token, cannot retry")
            
            if "error" in response:
                error_msg = response.get("error", "")
                logger.error(f"Failed to create stop bracket order: {error_msg}")
                print(f"❌ Python fallback: Stop bracket order failed: {error_msg}")
                return OrderResponse(success=False, error=error_msg, raw_response=response)
            
            # Check success field
            if response.get("success") == False:
                error_code = response.get("errorCode", "Unknown")
                error_message = response.get("errorMessage", "No error message")
                logger.error(f"Bracket order failed: Error Code {error_code}, Message: {error_message}")
                print(f"❌ Python fallback: Bracket order failed: {error_message} (Code: {error_code})")
                return OrderResponse(success=False, error=f"Bracket order failed: {error_message} (Code: {error_code})", raw_response=response)
            
            order_id = response.get("orderId") or response.get("id")
            if not order_id:
                logger.error(f"API returned success but NO order ID! Full response: {json.dumps(response, indent=2)}")
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
                    self._trading_bot.discord_notifier.send_order_notification(notification_data, account_name)
                    logger.info(f"📧 Discord notification sent for strategy order: {strategy_name} - {side} {quantity} {symbol}")
                except Exception as notif_err:
                    logger.debug(f"Could not send Discord notification for strategy order: {notif_err}")
            
            logger.info(f"✅ OCO bracket order placed successfully with ID: {order_id}")
            print(f"✅ Python fallback: OCO bracket order placed successfully with ID: {order_id}")

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
                self._trading_bot.discord_notifier.send_order_notification(notification_data, account_name)
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
            
            response = self._make_request("POST", "/api/Order/place", data=order_data, headers=headers)
            
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
                logger.error(f"API returned success but NO order ID! Full response: {json.dumps(response, indent=2)}")
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
            
            # Log order data for debugging (without sensitive info)
            logger.debug(f"Sending bracket order request: accountId={account_id}, contractId={contract_id}, "
                        f"side={side}, size={quantity}, stopLossTicks={stop_loss_ticks}, takeProfitTicks={take_profit_ticks}")
            
            # Make API call
            headers = {
                "accept": "text/plain",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.auth.get_token()}"
            }
            
            response = self._make_request("POST", "/api/Order/place", data=order_data, headers=headers)
            
            # If we got 500 errors and retries are exhausted, try refreshing token and retrying once
            if "error" in response and response.get("retry_exhausted") and response.get("status_code") == 500:
                logger.warning("⚠️  Received 500 errors, attempting token refresh and retry...")
                # Refresh token
                if await self.auth.authenticate():
                    logger.info("Token refreshed, retrying bracket order...")
                    # Update headers with new token
                    headers["Authorization"] = f"Bearer {self.auth.get_token()}"
                    # Retry once more
                    response = self._make_request("POST", "/api/Order/place", data=order_data, headers=headers)
                    if "error" in response:
                        logger.error(f"Failed to create bracket order after token refresh: {response['error']}")
                        return OrderResponse(success=False, error=response['error'], raw_response=response)
                else:
                    logger.error("Failed to refresh token, cannot retry bracket order")
                    return OrderResponse(success=False, error="Failed to refresh token after 500 errors", raw_response=response)
            
            if "error" in response:
                logger.error(f"Failed to create bracket order: {response['error']}")
                return OrderResponse(success=False, error=response['error'], raw_response=response)
            
            # Check success field
            if response.get("success") == False:
                error_code = response.get("errorCode", "Unknown")
                error_message = response.get("errorMessage", "No error message")
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
                logger.error(f"API returned success but NO order ID! Full response: {json.dumps(response, indent=2)}")
                return OrderResponse(success=False, error="Order rejected: No order ID returned", raw_response=response)
            
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

