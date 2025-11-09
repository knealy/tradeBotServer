# sdk_adapter.py
#
# Adapter around the ProjectX SDK (project-x-py) to provide
# a narrow, consistent interface for the rest of the codebase.
#
# The adapter exposes simple async functions for historical and
# realtime consumption, and safe sync wrappers for contexts that
# are not async-aware.
#
# All imports and usage of the SDK are guarded to allow the
# application to run without hard-crashing if the dependency is
# missing; callers can check `is_sdk_available()` and choose a
# fallback path.

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable, Dict, List, Optional
from threading import Lock

logger = logging.getLogger(__name__)

# -----------------------------
# Connection Pool/Cache for Historical Fetches and Order Operations
# -----------------------------
_historical_client_cache: Optional[Any] = None
_historical_suite_cache: Optional[Any] = None  # Keep suite alive for client
_order_suite_cache: Optional[Any] = None  # Cached suite for order operations
_cache_lock = Lock()
_cache_initialized = False
_order_cache_initialized = False
_cache_init_lock = Lock()

# Ensure .env is loaded for SDK auth when adapter is imported
try:
	import load_env  # noqa: F401
except Exception:
	pass

try:
	# Import only when available; do not fail module import
	from project_x_py import TradingSuite, EventType  # type: ignore
	# Try multiple import paths for Client (SDK may structure it differently)
	Client = None  # type: ignore
	_CLIENT_AVAILABLE = False
	try:
		from project_x_py.client import Client  # type: ignore
		_CLIENT_AVAILABLE = True
	except ImportError:
		try:
			from project_x_py import Client  # type: ignore
			_CLIENT_AVAILABLE = True
		except ImportError:
			# Client might be accessed via suite.client, which is fine
			_CLIENT_AVAILABLE = False
	_SDK_AVAILABLE = True
except Exception as import_err:  # pragma: no cover - environment dependent
	logger.debug("ProjectX SDK not available: %r", import_err)
	TradingSuite = None  # type: ignore
	EventType = None  # type: ignore
	Client = None  # type: ignore
	_CLIENT_AVAILABLE = False
	_SDK_AVAILABLE = False


def is_sdk_available() -> bool:
	"""Return True if project-x-py SDK is importable."""
	return _SDK_AVAILABLE


async def initialize_historical_client_cache() -> bool:
	"""
	Initialize a cached SDK client for historical data fetches.
	This should be called once at application startup.
	Returns True if successful, False otherwise.
	"""
	global _historical_client_cache, _historical_suite_cache, _cache_initialized
	
	if not _SDK_AVAILABLE:
		logger.warning("SDK not available, cannot initialize cache")
		return False
	
	with _cache_init_lock:
		if _cache_initialized:
			logger.debug("Historical client cache already initialized")
			return True
		
		try:
			logger.info("Initializing cached SDK client for historical fetches...")
			# Use TradingSuite with minimal config (no realtime features)
			# We'll create a lightweight suite for any symbol, then use its client
			suite = await create_suite("MNQ", timeframes=None, initial_days=None)
			# Cache both suite (to keep it alive) and client (for fast access)
			_historical_suite_cache = suite
			_historical_client_cache = suite.client
			_cache_initialized = True
			logger.info("✅ Historical client cache initialized successfully")
			return True
		except Exception as e:
			logger.error(f"Failed to initialize historical client cache: {e}")
			_cache_initialized = False
			return False


async def shutdown_historical_client_cache() -> None:
	"""Shutdown and cleanup the cached historical client."""
	global _historical_client_cache, _historical_suite_cache, _cache_initialized
	
	with _cache_init_lock:
		if not _cache_initialized:
			return
		
		try:
			if _historical_suite_cache is not None:
				try:
					await _historical_suite_cache.disconnect()
				except Exception:
					pass
			elif _historical_client_cache is not None:
				# Fallback: try to close client directly
				if hasattr(_historical_client_cache, 'close'):
					try:
						await _historical_client_cache.close()
					except Exception:
						pass
			
			logger.info("✅ Historical client cache shutdown complete")
		except Exception as e:
			logger.warning(f"Error during cache shutdown: {e}")
		finally:
			_historical_client_cache = None
			_historical_suite_cache = None
			_cache_initialized = False


def is_cache_initialized() -> bool:
	"""Check if the historical client cache is initialized."""
	return _cache_initialized


async def get_or_create_order_suite(symbol: str, account_id: Optional[int] = None) -> Any:
	"""
	Get or create a cached TradingSuite for order operations.
	This avoids re-authenticating and reconnecting for every order.
	
	Args:
		symbol: Trading symbol
		account_id: Optional account ID (for validation/logging)
	
	Returns:
		TradingSuite instance (cached or newly created)
	"""
	global _order_suite_cache, _order_cache_initialized
	
	if not _SDK_AVAILABLE:
		raise RuntimeError("ProjectX SDK not installed. Please install project-x-py[realtime].")
	
	with _cache_init_lock:
		# Check if we have a cached suite
		if _order_cache_initialized and _order_suite_cache is not None:
			logger.debug(f"Using cached SDK order suite for {symbol}")
			return _order_suite_cache
		
		# Create new suite
		logger.info(f"Creating cached SDK order suite for {symbol}...")
		try:
			# Create suite with minimal initialization (no historical data loading)
			suite = await create_suite(symbol, timeframes=None, initial_days=None)
			_order_suite_cache = suite
			_order_cache_initialized = True
			logger.info("✅ Order suite cache initialized successfully")
			return suite
		except Exception as e:
			logger.error(f"Failed to create order suite: {e}")
			_order_cache_initialized = False
			raise


async def shutdown_order_suite_cache() -> None:
	"""Shutdown and cleanup the cached order suite."""
	global _order_suite_cache, _order_cache_initialized
	
	with _cache_init_lock:
		if not _order_cache_initialized:
			return
		
		try:
			if _order_suite_cache is not None:
				try:
					await _order_suite_cache.disconnect()
				except Exception:
					pass
			
			logger.info("✅ Order suite cache shutdown complete")
		except Exception as e:
			logger.warning(f"Error during order suite shutdown: {e}")
		finally:
			_order_suite_cache = None
			_order_cache_initialized = False


# -----------------------------
# Async helpers
# -----------------------------
async def create_suite(
	symbol: str,
	timeframes: Optional[List[str]] = None,
	initial_days: Optional[int] = None,
) -> Any:
	"""Create and return an SDK TradingSuite for a symbol.

	Parameters
	- symbol: instrument symbol, e.g. "MNQ", "ES"
	- timeframes: optional list of timeframe strings (e.g. ["1min", "5min"]) for data manager
	- initial_days: optional number of days of history to prefetch
	"""
	if not _SDK_AVAILABLE:
		raise RuntimeError("ProjectX SDK not installed. Please install project-x-py[realtime].")

	kwargs: Dict[str, Any] = {}
	if timeframes is not None:
		kwargs["timeframes"] = timeframes
	if initial_days is not None:
		kwargs["initial_days"] = initial_days

	return await TradingSuite.create(symbol, **kwargs)  # type: ignore[operator]


async def get_historical_bars(
	symbol: str,
	days: Optional[int] = None,
	interval: Optional[int] = None,
	start_time: Optional[Any] = None,
	end_time: Optional[Any] = None,
) -> Any:
	"""Fetch historical bars via SDK and return a Polars DataFrame-like object.

	At least one of (days or start_time/end_time) should be provided.
	Uses cached client connection if available (fast), otherwise creates new connection.
	"""
	import logging as _logging
	
	# Aggressively suppress all SDK-related loggers during historical fetch
	_prev_levels = {}
	_noisy_prefixes = [
		"project_x_py",
		"SignalRCoreClient",
		"websocket",
		"httpx",
	]
	# Suppress root loggers and all children
	for name in _noisy_prefixes:
		lg = _logging.getLogger(name)
		_prev_levels[name] = lg.level
		lg.setLevel(_logging.CRITICAL)  # Suppress everything except critical
		lg.propagate = False  # Prevent propagation to parent handlers
	
	# Declare globals at function start (required if we modify them)
	global _cache_initialized, _historical_client_cache
	
	try:
		# PRIORITY 1: Use cached client if available (fastest - no initialization overhead)
		with _cache_lock:
			if _cache_initialized and _historical_client_cache is not None:
				# Use separate logger that won't be suppressed
				import logging as _main_log
				_main_logger = _main_log.getLogger(__name__)
				_main_logger.debug(f"Using cached SDK client for {symbol}")
				try:
					client = _historical_client_cache
					if start_time is not None or end_time is not None:
						result = await client.get_bars(symbol, start_time=start_time, end_time=end_time, interval=interval)
						_main_logger.debug(f"Cached client fetch successful for {symbol}")
						return result
					result = await client.get_bars(symbol, days=days, interval=interval)
					_main_logger.debug(f"Cached client fetch successful for {symbol}")
					return result
				except Exception as cache_err:
					_main_logger.warning(f"Cached client failed for {symbol}, will create new connection: {cache_err}")
					# Cache might be stale, mark as invalid and fall through
					_cache_initialized = False
		
		# PRIORITY 2: Try using Client directly if available (no realtime overhead)
		if _CLIENT_AVAILABLE and Client is not None:
			client = Client()  # type: ignore
			try:
				await client.authenticate()
				if start_time is not None or end_time is not None:
					return await client.get_bars(symbol, start_time=start_time, end_time=end_time, interval=interval)
				return await client.get_bars(symbol, days=days, interval=interval)
			finally:
				try:
					await client.close()  # type: ignore
				except Exception:
					pass
		
		# PRIORITY 3: Fallback - Use TradingSuite but skip realtime initialization
		# Create minimal suite without any realtime features
		suite = await create_suite(symbol, timeframes=None, initial_days=None)
		try:
			client = suite.client
			if start_time is not None or end_time is not None:
				return await client.get_bars(symbol, start_time=start_time, end_time=end_time, interval=interval)
			return await client.get_bars(symbol, days=days, interval=interval)
		finally:
			try:
				await suite.disconnect()
			except Exception:
				pass
	finally:
		# Restore logger levels and propagation
		for name, lvl in _prev_levels.items():
			lg = _logging.getLogger(name)
			lg.setLevel(lvl)
			lg.propagate = True  # Restore propagation


async def stream_realtime(
	symbol: str,
	timeframes: Optional[List[str]] = None,
	initial_days: Optional[int] = None,
	on_tick: Optional[Callable[[Any], Awaitable[None]]] = None,
	on_new_bar: Optional[Callable[[Any], Awaitable[None]]] = None,
	run_seconds: Optional[int] = None,
) -> None:
	"""Start a realtime session and optionally run for a fixed duration.

	Callbacks accept the raw event object from SDK and are awaited.
	If run_seconds is None, this will run until cancelled by caller (CancelledError).
	"""
	suite = await create_suite(symbol, timeframes=timeframes, initial_days=initial_days)

	async def _wrap(cb: Optional[Callable[[Any], Awaitable[None]]], event: Any) -> None:
		if cb is None:
			return
		await cb(event)

	if _SDK_AVAILABLE and EventType is not None:
		if on_tick is not None:
			await suite.on(EventType.TICK, lambda e: _wrap(on_tick, e))  # type: ignore[attr-defined]
		if on_new_bar is not None:
			await suite.on(EventType.NEW_BAR, lambda e: _wrap(on_new_bar, e))  # type: ignore[attr-defined]

	try:
		if run_seconds is None:
			while True:
				await asyncio.sleep(1)
		else:
			await asyncio.sleep(run_seconds)
	finally:
		try:
			await suite.disconnect()
		except Exception:
			pass


# -----------------------------
# Sync wrappers for convenience
# -----------------------------

def _ensure_event_loop() -> asyncio.AbstractEventLoop:
	try:
		loop = asyncio.get_event_loop()
		if loop.is_closed():
			raise RuntimeError
		return loop
	except Exception:
		loop = asyncio.new_event_loop()
		asyncio.set_event_loop(loop)
		return loop


def get_historical_bars_sync(
	symbol: str,
	days: Optional[int] = None,
	interval: Optional[int] = None,
	start_time: Optional[Any] = None,
	end_time: Optional[Any] = None,
) -> Any:
	"""Synchronous wrapper for get_historical_bars."""
	loop = _ensure_event_loop()
	return loop.run_until_complete(
		get_historical_bars(
			symbol=symbol,
			days=days,
			interval=interval,
			start_time=start_time,
			end_time=end_time,
		)
	)


def stream_realtime_sync(
	symbol: str,
	timeframes: Optional[List[str]] = None,
	initial_days: Optional[int] = None,
	on_tick_sync: Optional[Callable[[Any], None]] = None,
	on_new_bar_sync: Optional[Callable[[Any], None]] = None,
	run_seconds: Optional[int] = None,
) -> None:
	"""Synchronous wrapper for stream_realtime, adapting sync callbacks."""

	async def _on_tick(event: Any) -> None:
		if on_tick_sync is not None:
			on_tick_sync(event)

	async def _on_new_bar(event: Any) -> None:
		if on_new_bar_sync is not None:
			on_new_bar_sync(event)

	loop = _ensure_event_loop()
	return loop.run_until_complete(
		stream_realtime(
			symbol=symbol,
			timeframes=timeframes,
			initial_days=initial_days,
			on_tick=_on_tick,
			on_new_bar=_on_new_bar,
			run_seconds=run_seconds,
		)
	)
