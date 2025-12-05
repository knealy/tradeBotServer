"""
TopStepX Broker Adapter - Implements broker interfaces for TopStepX API.

This adapter implements the translation layer interfaces, allowing
the trading bot to work with TopStepX while remaining broker-agnostic.
"""

import os
import json
import asyncio
import logging
from typing import Optional, List, Dict, Any
from datetime import datetime

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
        
        # Initialize Rust executor if available
        self._use_rust = False
        self._rust_executor = None
        
        if use_rust is False:
            # Explicitly disabled
            logger.info("⚠️  Rust executor disabled by user")
        elif RUST_AVAILABLE:
            try:
                self._rust_executor = trading_bot_rust.OrderExecutor(base_url=base_url)
                self._use_rust = True
                logger.info("🚀 Rust hot path enabled for order execution (20-30x faster)")
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
        return OrderResponse(
            success=rust_result.get('success', False),
            order_id=rust_result.get('order_id'),
            message=rust_result.get('message'),
            error=rust_result.get('error'),
            raw_response=rust_result.get('raw_response')
        )
    
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
        order_type = kwargs.get('order_type', 'market').lower()
        custom_tag = kwargs.get('custom_tag')
        
        if order_type == "limit" and limit_price is None:
            return OrderResponse(
                success=False,
                error="Limit price is required for limit orders"
            )
        
        logger.info(f"🐍 Python execution: Placing {side} {order_type} order for {quantity} {symbol} on account {account_id}")
        
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
            "stopPrice": None,
        }
        
        # Add custom tag if provided
        if custom_tag:
            order_data["customTag"] = custom_tag
        
        # Add bracket orders if specified
        if stop_loss_ticks is not None or take_profit_ticks is not None:
            if stop_loss_ticks is not None:
                order_data["stopLossBracket"] = {
                    "ticks": stop_loss_ticks,
                    "type": 4,  # Stop loss type
                    "size": quantity,
                    "reduceOnly": True
                }
            
            if take_profit_ticks is not None:
                order_data["takeProfitBracket"] = {
                    "ticks": take_profit_ticks,
                    "type": 1,  # Take profit type
                    "size": quantity,
                    "reduceOnly": True
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
        """Place a limit order."""
        logger.warning("place_limit_order not yet implemented in adapter")
        return OrderResponse(success=False, error="Not yet implemented")
    
    async def place_stop_order(
        self,
        symbol: str,
        side: str,
        quantity: int,
        stop_price: float,
        account_id: Optional[str] = None,
        **kwargs
    ) -> OrderResponse:
        """Place a stop order."""
        logger.warning("place_stop_order not yet implemented in adapter")
        return OrderResponse(success=False, error="Not yet implemented")
    
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
                error="Account ID is required"
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
            )

        elapsed_ms = (time.perf_counter() - start_time) * 1000
        logger.info(f"🐍 Python modify: {elapsed_ms:.2f}ms")
        logger.info(f"✅ Order modified successfully: {order_id}")

        return ModifyOrderResponse(
            success=True,
            order_id=order_id,
            message="Order modified successfully",
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
        
        Args:
            order_id: Order ID to cancel
            account_id: Account ID
            **kwargs: Additional parameters
            
        Returns:
            CancelResponse with cancellation details
        """
        try:
            # Try Rust hot path first
            if self._use_rust and self._rust_executor:
                try:
                    return await self._cancel_order_rust(order_id, account_id, **kwargs)
                except Exception as e:
                    logger.warning(f"⚠️  Rust cancel failed, falling back to Python: {e}")
            
            # Python implementation
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
        
        # Update Rust executor with current token
        self._rust_executor.set_token(self.auth.get_token())
        
        # Call Rust async method
        rust_result = await self._rust_executor.cancel_order(order_id=order_id)
        
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
        
        Args:
            account_id: Account ID
            **kwargs: Additional parameters
            
        Returns:
            List of open orders
        """
        try:
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
            
            # Filter strictly to OPEN orders (status == 1)
            open_only = [o for o in orders if o.get("status") == 1]
            logger.info(f"Found {len(open_only)} open orders (from {len(orders)} total)")
            
            return open_only
            
        except Exception as e:
            logger.error(f"Failed to fetch orders: {str(e)}")
            return []
    
    async def get_order_history(
        self,
        account_id: Optional[str] = None,
        limit: int = 100,
        **kwargs
    ) -> list:
        """
        Get order history.
        
        Args:
            account_id: Account ID
            limit: Maximum number of orders to return
            **kwargs: Additional parameters (start_timestamp, end_timestamp)
            
        Returns:
            List of historical orders
        """
        try:
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
        
        Args:
            account_id: Account ID
            **kwargs: Additional parameters
            
        Returns:
            List of Position objects
        """
        try:
            await self.auth.ensure_valid_token()
            
            if not account_id:
                logger.error("Account ID is required")
                return []
            
            logger.info(f"Fetching open positions for account {account_id}")
            
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
            
            logger.info(f"Found {len(positions)} open positions")
            return positions
            
        except Exception as e:
            logger.error(f"Failed to fetch positions: {str(e)}")
            return []
    
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
        
        Args:
            position_id: Position ID to close
            quantity: Quantity to close (None = close all)
            account_id: Account ID
            **kwargs: Additional parameters
            
        Returns:
            CloseResponse with close operation details
        """
        try:
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
            
            if "error" in response:
                logger.error(f"Failed to close position: {response['error']}")
                return CloseResponse(
                    success=False,
                    error=response['error'],
                    position_id=position_id,
                    raw_response=response
                )
            
            logger.info(f"✅ Position closed successfully: {position_id}")
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
            
            # Get all open positions
            positions = await self.get_positions(account_id=account_id)
            if not positions:
                logger.info("No open positions found to close")
                return {
                    "success": True,
                    "message": "No positions to close",
                    "closed_positions": [],
                    "canceled_orders": []
                }
            
            # Close each position
            closed_positions = []
            failed_positions = []
            
            for position in positions:
                try:
                    result = await self.close_position(
                        position_id=position.position_id,
                        account_id=account_id
                    )
                    
                    if result.success:
                        closed_positions.append(position.position_id)
                        logger.info(f"Successfully closed position {position.position_id}")
                    else:
                        failed_positions.append({
                            "id": position.position_id,
                            "error": result.error
                        })
                        logger.error(f"Failed to close position {position.position_id}: {result.error}")
                except Exception as e:
                    logger.error(f"Exception closing position {position.position_id}: {e}")
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
            
            result = {
                "success": True,
                "closed_positions": closed_positions,
                "canceled_orders": canceled_orders,
                "failed_positions": failed_positions,
                "failed_orders": failed_orders,
                "positions_count": len(closed_positions),
                "orders_count": len(canceled_orders)
            }
            
            logger.info(f"Flatten complete: {len(closed_positions)} positions closed, {len(canceled_orders)} orders canceled")
            return result
            
        except Exception as e:
            logger.error(f"Failed to flatten positions: {str(e)}")
            return {"success": False, "error": str(e)}
    
    # ==================== Market Data Interface Implementation ====================
    
    async def get_historical_data(
        self,
        symbol: str,
        timeframe: str = "1m",
        limit: int = 100,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        **kwargs
    ) -> List[Bar]:
        """
        Get historical bar data.
        
        Args:
            symbol: Trading symbol
            timeframe: Timeframe (e.g., "1m", "5m", "1h")
            limit: Maximum number of bars
            start_time: Start time (optional)
            end_time: End time (optional)
            **kwargs: Additional parameters
            
        Returns:
            List of Bar objects
        """
        try:
            await self.auth.ensure_valid_token()
            
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
            
            # CRITICAL: Always use current time as end_time to ensure we get data up to the current moment
            # This mirrors the original trading_bot implementation: end_time defaults to "now".
            if end_time is None:
                end_time = datetime.now(timezone.utc)
                logger.debug(f"Using current time as end_time: {end_time}")
            elif end_time.tzinfo is None:
                end_time = end_time.replace(tzinfo=timezone.utc)
            
            # For 1m timeframes, ensure end_time is very recent (within last minute) to get latest data
            if timeframe == "1m":
                current_time = datetime.now(timezone.utc)
                time_diff = (current_time - end_time).total_seconds()
                # If end_time is more than 1 minute old, update it to current time
                if time_diff > 60:
                    logger.info(
                        f"📊 1m timeframe: end_time is {time_diff:.0f}s old, updating to current time for fresh data"
                    )
                    end_time = current_time
            
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
                lookback_days = (end_time - start_time).total_seconds() / 86400
                logger.info(
                    f"Bar count mode: {lookback_bars} bars worth of time = {lookback_days:.1f} days "
                    f"for {symbol_up} {timeframe}"
                )
            elif start_time.tzinfo is None:
                # Date range mode: respect explicit start_time, just normalize tz
                start_time = start_time.replace(tzinfo=timezone.utc)
            
            # Format timestamps
            start_str = start_time.strftime("%Y-%m-%dT%H:%M:%S")
            end_str = end_time.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
            
            headers = {
                "Authorization": f"Bearer {self.auth.get_token()}",
                "Content-Type": "application/json",
                "accept": "text/plain"
            }
            
            # Request extra bars to account for gaps/closures (similar to legacy: limit * 3)
            api_limit = min(limit * 3, 20000)
            
            bars_request = {
                "contractId": contract_id,
                "live": False,
                "startTime": start_str,
                "endTime": end_str,
                "unit": unit,
                "unitNumber": unit_number,
                "limit": api_limit,  # Request extra, cap at API limit
                "includePartialBar": True
            }
            
            logger.info(f"Fetching {limit} {timeframe} bars for {symbol_up} from {start_str} to {end_str}")
            
            response = self._make_request("POST", "/api/History/retrieveBars", data=bars_request, headers=headers)
            
            if "error" in response:
                logger.error(f"API error: {response['error']}")
                return []
            
            if response.get('success') == False or (response.get('errorCode') and response.get('errorCode') != 0):
                error_code = response.get('errorCode', 'Unknown')
                error_msg = response.get('errorMessage', 'No error message')
                logger.error(f"API returned error: Code {error_code}, Message: {error_msg}")
                return []
            
            # Parse bars from response
            bars_data = None
            if isinstance(response, list):
                bars_data = response
            elif isinstance(response, dict):
                bars_data = response.get('bars') or response.get('data') or response.get('candles') or []
            
            if not bars_data:
                logger.warning("API returned empty bars data")
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
                logger.info(f"🔍 History API raw last bar timestamp field (t/time/timestamp) = {last_raw}")
                logger.info(f"🔍 History API now UTC                                    = {datetime.now(_tz.utc)}")
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
            
            # CRITICAL: Sort by timestamp (oldest first) before limiting
            # This ensures we always get the most recent bars, regardless of API response order
            bars.sort(key=lambda b: b.timestamp if b.timestamp else datetime.min.replace(tzinfo=timezone.utc))
            
            # Limit to requested number (take last N bars = most recent)
            if len(bars) > limit:
                bars = bars[-limit:]
            
            # Additional debug: log parsed last bar timestamp vs now
            if bars:
                try:
                    from datetime import timezone as _tz

                    last_bar_ts = bars[-1].timestamp
                    logger.info(f"🔍 Parsed last bar timestamp UTC = {last_bar_ts}")
                    logger.info(f"🔍 Adapter now UTC               = {datetime.now(_tz.utc)}")
                except Exception as dbg_err:
                    logger.debug(f"Failed to log parsed last bar timestamp: {dbg_err}")

            logger.info(f"✅ Retrieved {len(bars)} {timeframe} bars for {symbol_up}")
            return bars
            
        except Exception as e:
            logger.error(f"Failed to fetch historical data: {str(e)}")
            return []
    
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
            
            # Try REST quote endpoint
            # Try different symbol identifier formats
            from core.market_data import ContractManager
            symbol_variants = [symbol_up]
            if '.' in contract_id:
                parts = contract_id.split('.')
                if len(parts) >= 4:
                    symbol_variants.append(parts[-2])  # Extract symbol from contract ID
            
            quote_resp = None
            for variant in symbol_variants:
                try:
                    path = f"/api/MarketData/quote/{variant}"
                    resp = self._make_request("GET", path, headers=headers)
                    if resp and "error" not in resp:
                        quote_resp = resp
                        break
                except Exception:
                    continue
            
            if quote_resp and "error" not in quote_resp:
                bid = quote_resp.get("bid") or quote_resp.get("bestBid")
                ask = quote_resp.get("ask") or quote_resp.get("bestAsk")
                last = quote_resp.get("last") or quote_resp.get("lastPrice") or quote_resp.get("price")
                volume = quote_resp.get("volume") or quote_resp.get("totalVolume")
                
                if any(v is not None for v in (bid, ask, last)):
                    return Quote(
                        symbol=symbol_up,
                        bid=float(bid) if bid is not None else None,
                        ask=float(ask) if ask is not None else None,
                        last=float(last) if last is not None else None,
                        volume=int(volume) if volume is not None else None,
                        raw_data=quote_resp
                    )
            
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
    
    async def get_market_depth(
        self,
        symbol: str,
        **kwargs
    ) -> Optional[Depth]:
        """
        Get market depth (order book).
        
        Args:
            symbol: Trading symbol
            **kwargs: Additional parameters
            
        Returns:
            Depth object or None if unavailable
        """
        try:
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
    
    async def get_available_contracts(
        self,
        use_cache: bool = True,
        **kwargs
    ) -> List[Dict[str, Any]]:
        """
        Get available trading contracts.
        
        Args:
            use_cache: If True, use cached contracts if available
            **kwargs: Additional parameters (cache_ttl_minutes, etc.)
            
        Returns:
            List of contract dictionaries
        """
        try:
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
            
            if "error" in response or not response:
                # Return cached data if available
                if use_cache:
                    cache = self.contract_manager.get_contract_cache()
                    if cache:
                        logger.warning(f"API error, returning stale cached contracts ({len(cache['contracts'])} contracts)")
                        return cache['contracts'].copy()
                return []
            
            # Check API success field
            if isinstance(response, dict) and response.get('success') == False:
                error_code = response.get('errorCode', 'Unknown')
                error_msg = response.get('errorMessage', 'No error message')
                logger.error(f"API returned error: Code {error_code}, Message: {error_msg}")
                # Try cached data
                if use_cache:
                    cache = self.contract_manager.get_contract_cache()
                    if cache:
                        logger.warning(f"Using stale cached contracts due to API error")
                        return cache['contracts'].copy()
                return []
            
            # Parse contracts from response
            if isinstance(response, list):
                contracts = response
            elif isinstance(response, dict):
                contracts = (
                    response.get("contracts") or
                    response.get("data") or
                    response.get("result") or
                    response.get("items") or
                    []
                )
            else:
                logger.warning(f"Unexpected contracts response type: {type(response)}")
                contracts = []
            
            # Cache the contracts
            if use_cache:
                self.contract_manager.set_contract_cache(contracts, cache_ttl_minutes)
                logger.info(f"✅ Cached {len(contracts)} contracts for {cache_ttl_minutes} minutes")
            
            logger.info(f"Found {len(contracts)} available contracts")
            return contracts
            
        except Exception as e:
            logger.error(f"Failed to fetch contracts: {str(e)}")
            # Return cached data if available
            if use_cache:
                cache = self.contract_manager.get_contract_cache()
                if cache:
                    logger.warning(f"Exception during fetch, returning stale cached contracts ({len(cache['contracts'])} contracts)")
                    return cache['contracts'].copy()
            return []
    
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
            await self.auth.ensure_valid_token()
            
            if not account_id:
                return OrderResponse(success=False, error="Account ID is required")
            
            if side.upper() not in ["BUY", "SELL"]:
                return OrderResponse(success=False, error="Side must be 'BUY' or 'SELL'")
            
            logger.info(f"Placing OCO bracket with stop entry: {side} {quantity} {symbol}")
            logger.info(f"  Entry (stop): ${entry_price:.2f}, SL: ${stop_loss_price:.2f}, TP: ${take_profit_price:.2f}")
            
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
            
            logger.info(f"Rounded prices: Entry=${entry_price:.2f}, SL=${stop_loss_price:.2f}, TP=${take_profit_price:.2f} (tick_size={tick_size})")
            
            # Convert side to numeric value
            side_value = 0 if side.upper() == "BUY" else 1
            
            # Calculate stop loss ticks from entry price
            if side.upper() == "BUY":
                price_diff = entry_price - stop_loss_price
                stop_loss_ticks = int(price_diff / tick_size)
                if stop_loss_ticks > 0:
                    stop_loss_ticks = -stop_loss_ticks
            else:
                price_diff = stop_loss_price - entry_price
                stop_loss_ticks = int(price_diff / tick_size)
                if stop_loss_ticks < 0:
                    stop_loss_ticks = -stop_loss_ticks
            
            # Calculate take profit ticks from entry price
            if side.upper() == "BUY":
                price_diff = take_profit_price - entry_price
                take_profit_ticks = int(price_diff / tick_size)
                if take_profit_ticks < 0:
                    take_profit_ticks = -take_profit_ticks
            else:
                price_diff = entry_price - take_profit_price
                take_profit_ticks = int(price_diff / tick_size)
                if take_profit_ticks > 0:
                    take_profit_ticks = -take_profit_ticks
            
            logger.info(f"Stop Loss: {stop_loss_ticks} ticks, Take Profit: {take_profit_ticks} ticks")
            
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
            order_data["stopLossBracket"] = {
                "ticks": stop_loss_ticks,
                "type": 4,  # Stop loss type
                "size": quantity,
                "reduceOnly": True
            }
            
            order_data["takeProfitBracket"] = {
                "ticks": take_profit_ticks,
                "type": 1,  # Take profit type
                "size": quantity,
                "reduceOnly": True
            }
            
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
                
                token_refreshed = await self.auth.ensure_valid_token()
                if token_refreshed:
                    headers["Authorization"] = f"Bearer {self.auth.get_token()}"
                    await asyncio.sleep(0.75)
                    logger.info("🔄 Retrying order placement with refreshed token...")
                    response = self._make_request("POST", "/api/Order/place", data=order_data, headers=headers)
            
            if "error" in response:
                error_msg = response.get("error", "")
                logger.error(f"Failed to create stop bracket order: {error_msg}")
                return OrderResponse(success=False, error=error_msg, raw_response=response)
            
            # Check success field
            if response.get("success") == False:
                error_code = response.get("errorCode", "Unknown")
                error_message = response.get("errorMessage", "No error message")
                logger.error(f"Bracket order failed: Error Code {error_code}, Message: {error_message}")
                return OrderResponse(success=False, error=f"Bracket order failed: {error_message} (Code: {error_code})", raw_response=response)
            
            order_id = response.get("orderId") or response.get("id")
            if not order_id:
                logger.error(f"API returned success but NO order ID! Full response: {json.dumps(response, indent=2)}")
                return OrderResponse(success=False, error="Order rejected: No order ID returned", raw_response=response)
            
            logger.info(f"✅ OCO bracket order placed successfully with ID: {order_id}")
            
            return OrderResponse(
                success=True,
                order_id=str(order_id),
                message="OCO bracket order placed successfully",
                raw_response=response
            )
            
        except Exception as e:
            logger.error(f"Failed to place OCO bracket order: {str(e)}")
            return OrderResponse(success=False, error=str(e))
    
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
            logger.info(f"Bracket context: contract={contract_id}, tick_size={tick_size}")
            
            # Calculate stop loss ticks from entry price if price provided
            if stop_loss_price is not None and stop_loss_ticks is None:
                try:
                    # Get current market price as entry price
                    quote = await self.get_market_quote(symbol)
                    if "error" in quote or not (quote.get("bid") or quote.get("ask") or quote.get("last")):
                        return OrderResponse(success=False, error=f"Could not get market price for {symbol}. Market data is required for bracket orders.")
                    
                    if side.upper() == "BUY":
                        entry_price = float(quote.get("ask") or quote.get("last"))
                    else:
                        entry_price = float(quote.get("bid") or quote.get("last"))
                    
                    logger.info(f"Using current market price as entry: ${entry_price}")
                    
                    if side.upper() == "BUY":
                        price_diff = entry_price - stop_loss_price
                        stop_loss_ticks = int(price_diff / tick_size)
                        if stop_loss_ticks > 0:
                            stop_loss_ticks = -stop_loss_ticks
                    else:
                        price_diff = stop_loss_price - entry_price
                        stop_loss_ticks = int(price_diff / tick_size)
                        if stop_loss_ticks < 0:
                            stop_loss_ticks = -stop_loss_ticks
                    
                    logger.info(f"Stop Loss Calculation: Entry=${entry_price}, Target=${stop_loss_price}, Diff=${price_diff:.2f}, Ticks={stop_loss_ticks} (tick_size={tick_size})")
                    
                    if abs(stop_loss_ticks) > 1000:
                        logger.warning(f"Stop loss ticks ({stop_loss_ticks}) exceeds 1000 limit, capping at 1000")
                        stop_loss_ticks = 1000 if stop_loss_ticks > 0 else -1000
                except Exception as e:
                    logger.error(f"Failed to calculate stop loss ticks: {e}")
                    return OrderResponse(success=False, error=f"Failed to calculate stop loss ticks: {e}")
            
            # Calculate take profit ticks from entry price if price provided
            if take_profit_price is not None and take_profit_ticks is None:
                try:
                    quote = await self.get_market_quote(symbol)
                    if "error" in quote or not (quote.get("bid") or quote.get("ask") or quote.get("last")):
                        return OrderResponse(success=False, error=f"Could not get market price for {symbol}. Market data is required for bracket orders.")
                    
                    if side.upper() == "BUY":
                        entry_price = float(quote.get("ask") or quote.get("last"))
                    else:
                        entry_price = float(quote.get("bid") or quote.get("last"))
                    
                    if side.upper() == "BUY":
                        price_diff = take_profit_price - entry_price
                        take_profit_ticks = int(price_diff / tick_size)
                        if take_profit_ticks < 0:
                            take_profit_ticks = -take_profit_ticks
                    else:
                        price_diff = entry_price - take_profit_price
                        take_profit_ticks = int(price_diff / tick_size)
                        if take_profit_ticks > 0:
                            take_profit_ticks = -take_profit_ticks
                    
                    logger.info(f"Take Profit Calculation: Entry=${entry_price}, Target=${take_profit_price}, Diff=${price_diff:.2f}, Ticks={take_profit_ticks} (tick_size={tick_size})")
                    
                    if abs(take_profit_ticks) > 1000:
                        logger.warning(f"Take profit ticks ({take_profit_ticks}) exceeds 1000 limit, capping at 1000")
                        take_profit_ticks = 1000 if take_profit_ticks > 0 else -1000
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
            if stop_loss_ticks is not None:
                order_data["stopLossBracket"] = {
                    "ticks": stop_loss_ticks,
                    "type": 4,  # Stop loss type
                    "size": quantity,
                    "reduceOnly": True
                }
                logger.info(f"Added stop loss bracket: {stop_loss_ticks} ticks, size: {quantity}, reduceOnly: True")
            
            if take_profit_ticks is not None:
                order_data["takeProfitBracket"] = {
                    "ticks": take_profit_ticks,
                    "type": 1,  # Take profit type
                    "size": quantity,
                    "reduceOnly": True
                }
                logger.info(f"Added take profit bracket: {take_profit_ticks} ticks, size: {quantity}, reduceOnly: True")
            
            # Make API call
            headers = {
                "accept": "text/plain",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.auth.get_token()}"
            }
            
            response = self._make_request("POST", "/api/Order/place", data=order_data, headers=headers)
            
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
        
        base_tag = f"TradingBot-v1.0-{order_type}"
        if strategy_name:
            base_tag += f"-{strategy_name}"
        
        # Add timestamp and unique ID for uniqueness
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        unique_id = str(uuid.uuid4())[:8]
        
        return f"{base_tag}-{timestamp}-{unique_id}"

