"""
Real-time bar aggregator for converting SignalR quote updates into OHLCV bars.

Aggregates tick data into time-based bars (1m, 5m, etc.) and streams them
to WebSocket clients for real-time chart updates.
"""

import asyncio
import logging
import os
import time
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from typing import Dict, Optional, Callable, Any, Iterable, Set, List
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class Bar:
    """OHLCV bar data."""
    symbol: str
    timeframe: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int = 0
    tick_count: int = 0


@dataclass(slots=True)
class BarBuilder:
    """Builds a bar from tick data."""
    symbol: str
    timeframe: str
    bar_start: datetime
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    close: Optional[float] = None
    volume: int = 0
    tick_count: int = 0
    last_update: Optional[datetime] = None
    
    def add_tick(self, price: float, volume: int = 0, timestamp: Optional[datetime] = None):
        """Add a tick to the current bar."""
        if timestamp is None:
            timestamp = datetime.now(timezone.utc)
        
        if self.open is None:
            self.open = price
            self.high = price
            self.low = price
        else:
            if price > self.high:
                self.high = price
            if price < self.low:
                self.low = price
        
        self.close = price
        self.volume += volume
        self.tick_count += 1
        self.last_update = timestamp
    
    def to_bar(self) -> Bar:
        """Convert builder to final bar."""
        if self.open is None:
            raise ValueError("Bar has no data")
        return Bar(
            symbol=self.symbol,
            timeframe=self.timeframe,
            timestamp=self.bar_start,
            open=self.open,
            high=self.high or self.open,
            low=self.low or self.open,
            close=self.close or self.open,
            volume=self.volume,
            tick_count=self.tick_count
        )


class BarAggregator:
    """
    Aggregates real-time quote updates into OHLCV bars.
    
    Features:
    - Multiple timeframes (1m, 5m, 15m, etc.)
    - Real-time bar updates (3-5 per second)
    - Automatic bar completion and new bar creation
    - WebSocket broadcasting
    """
    
    def __init__(self, broadcast_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
                 default_timeframes: Optional[Iterable[str]] = None):
        """
        Initialize bar aggregator.
        
        Args:
            broadcast_callback: Function to call when a bar update is ready
        """
        self.broadcast_callback = broadcast_callback
        # Additional fan-out for completed bars (live strategies, caches, drift monitor, …).
        # Each callback receives a fully-built ``Bar`` instance synchronously on the aggregator
        # loop. Best-effort: exceptions are caught and logged so a misbehaving subscriber
        # cannot corrupt the broadcast pipeline.
        self._completed_bar_callbacks: List[Callable[[Bar], None]] = []
        self.bar_builders: Dict[str, Dict[str, BarBuilder]] = defaultdict(dict)  # {symbol: {timeframe: BarBuilder}}
        self.completed_bars: Dict[str, Dict[str, Bar]] = defaultdict(dict)  # {symbol: {timeframe: Bar}}
        self._broadcast_log_counts: Dict[str, int] = defaultdict(int)
        self._last_partial_emit: Dict[str, float] = {}
        self._aggregator_loop: Optional[asyncio.AbstractEventLoop] = None
        self._running = False
        # ── Periodic bar-completion safety net (2026-06-12 fix) ───────────────
        # Force-closes builders whose ``bar_end`` has elapsed even when no fresh
        # tick has arrived. This eliminates the "quote dead-zone race" where
        # SignalR pauses for a few seconds right at a bar boundary and the
        # builder stays "open" indefinitely until the next tick fires. Without
        # this loop the live-bar cache in ``trading_bot`` can stay 10+ minutes
        # behind real time even though hundreds of thousands of quotes are
        # flowing — the events update the in-progress bar without ever
        # crossing the strict ``current_time >= bar_end`` boundary inside
        # ``add_quote``. See ``docs/CHANGELOG.md`` 2026-06-12 entry.
        self._periodic_completion_task: Optional[asyncio.Task] = None
        try:
            self._periodic_completion_interval: float = float(
                os.getenv("BAR_PERIODIC_COMPLETION_INTERVAL", "10.0")
            )
        except (TypeError, ValueError):
            self._periodic_completion_interval = 10.0
        self._periodic_completions_total: int = 0
        # Determine default timeframes (support env override)
        env_frames = os.getenv('BAR_DEFAULT_TIMEFRAMES')
        frames: Iterable[str]
        if default_timeframes is not None:
            frames = default_timeframes
        elif env_frames:
            frames = env_frames.split(',')
        else:
            # Support all timeframes including seconds
            frames = ['5s', '15s', '30s', '1m', '2m', '5m', '15m', '30m', '1h']
        self.default_timeframes: List[str] = [
            self._normalize_timeframe(tf) for tf in frames if tf and tf.strip()
        ]
        if not self.default_timeframes:
            self.default_timeframes = ['5s', '15s', '30s', '1m', '2m', '5m', '15m', '30m', '1h']
        self.symbol_timeframes: Dict[str, Set[str]] = defaultdict(set)
        
    async def start(self):
        """Start the bar aggregator (event-driven broadcasts + boundary safety net)."""
        if self._running:
            logger.warning("⚠️  Bar aggregator already running")
            return
        self._running = True
        try:
            self._aggregator_loop = asyncio.get_running_loop()
        except RuntimeError:
            self._aggregator_loop = None
        logger.info(
            "📊 Bar aggregator started - tracking %d timeframes: %s",
            len(self.default_timeframes),
            ", ".join(self.default_timeframes),
        )
        if (
            self._periodic_completion_interval > 0
            and self._periodic_completion_task is None
        ):
            try:
                self._periodic_completion_task = asyncio.create_task(
                    self._periodic_bar_completion_loop(),
                    name="bar_periodic_completion",
                )
                logger.info(
                    "🛡️  Periodic bar-completion safety net active (interval=%.1fs)",
                    self._periodic_completion_interval,
                )
            except RuntimeError:
                # No running loop (e.g. in some tests) — skip safety net silently.
                self._periodic_completion_task = None

    async def stop(self):
        """Stop the bar aggregator (and its periodic-completion safety net)."""
        self._running = False
        task = self._periodic_completion_task
        self._periodic_completion_task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        self._aggregator_loop = None
        logger.info(
            "📊 Bar aggregator stopped (%d periodic force-completion(s) this run)",
            self._periodic_completions_total,
        )

    def _partial_min_interval(self) -> float:
        try:
            return float(os.getenv("BAR_PARTIAL_MIN_INTERVAL", "0.15"))
        except ValueError:
            return 0.15

    def _enqueue_broadcast(self, message: Dict[str, Any], debug_key: Optional[str] = None) -> None:
        """Schedule broadcast on the aggregator loop (safe from SignalR threads)."""
        if not self.broadcast_callback or not self._running:
            return
        loop = self._aggregator_loop
        if loop is None:
            return

        def _run() -> None:
            try:
                self.broadcast_callback(message)
            except Exception as exc:
                logger.debug("Error broadcasting bar update: %s", exc)
                return
            if (
                debug_key
                and os.getenv("BAR_AGG_DEBUG", "0").lower() in ("1", "true", "yes", "on")
            ):
                count = self._broadcast_log_counts[debug_key]
                if count < 5:
                    data = message.get("data") or {}
                    b = data.get("bar") or {}
                    logger.info(
                        "📡 Broadcasted %s bar update for %s: O:%s H:%s L:%s C:%s",
                        data.get("timeframe"),
                        data.get("symbol"),
                        b.get("open"),
                        b.get("high"),
                        b.get("low"),
                        b.get("close"),
                    )
                    self._broadcast_log_counts[debug_key] = count + 1

        try:
            loop.call_soon_threadsafe(_run)
        except RuntimeError:
            _run()

    def _emit_partial_bar(self, symbol_key: str, timeframe: str, builder: BarBuilder) -> None:
        if builder.close is None or builder.open is None:
            return
        key = f"{symbol_key}:{timeframe}"
        now = time.monotonic()
        min_iv = self._partial_min_interval()
        last = self._last_partial_emit.get(key, 0.0)
        if now - last < min_iv:
            return
        self._last_partial_emit[key] = now
        bar_data = {
            "symbol": symbol_key,
            "timeframe": timeframe,
            "timestamp": builder.bar_start.isoformat(),
            "bar": {
                "open": builder.open,
                "high": builder.high or builder.open,
                "low": builder.low or builder.open,
                "close": builder.close,
                "volume": builder.volume,
            },
            "is_partial": True,
        }
        self._enqueue_broadcast(
            {
                "type": "market_update",
                "data": bar_data,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
            debug_key=key,
        )

    def _emit_completed_bar(self, bar: Bar) -> None:
        bar_data = {
            "symbol": bar.symbol,
            "timeframe": bar.timeframe,
            "timestamp": bar.timestamp.isoformat(),
            "bar": {
                "open": bar.open,
                "high": bar.high,
                "low": bar.low,
                "close": bar.close,
                "volume": bar.volume,
            },
            "is_partial": False,
        }
        self._enqueue_broadcast(
            {
                "type": "market_update",
                "data": bar_data,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
            debug_key=f"{bar.symbol}:{bar.timeframe}",
        )
        if self._completed_bar_callbacks:
            for cb in list(self._completed_bar_callbacks):
                try:
                    cb(bar)
                except Exception as exc:
                    logger.debug("completed-bar callback %r failed: %s", cb, exc)

    async def _periodic_bar_completion_loop(self) -> None:
        """Force-close builders whose ``bar_end`` has elapsed, even without a fresh tick.

        Closes the quote dead-zone race: ``add_quote`` is the ONLY path that
        rolls over a bar via ``_should_start_new_bar(current_time >= bar_end)``.
        If the SignalR quote stream has even a brief pause right at the
        boundary, the in-progress bar stays open until the next tick — which
        can be many seconds (or minutes, on a quiet symbol). The live-bar
        cache in ``trading_bot`` then stays stuck at the previous tail and
        strategies see false ``⛔ STALE DATA`` errors.

        This task runs on the aggregator event loop at
        ``BAR_PERIODIC_COMPLETION_INTERVAL`` seconds (default 10) and walks
        all live builders. Any builder that has at least one tick AND whose
        ``bar_end`` is in the past is force-completed; a fresh empty builder
        is opened for the current boundary.

        Builders with no ticks yet (``builder.open is None``) are skipped —
        we only emit bars with real OHLCV data.
        """
        try:
            while self._running:
                await asyncio.sleep(self._periodic_completion_interval)
                if not self._running:
                    break
                try:
                    self._sweep_force_complete()
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "periodic-bar-completion sweep error (continuing): %s",
                        exc,
                    )
        except asyncio.CancelledError:
            pass

    def _sweep_force_complete(self) -> None:
        """Single pass of the periodic-completion logic (extracted for tests)."""
        now = datetime.now(timezone.utc)
        # Snapshot to avoid mutation during iteration (other threads call
        # ``add_quote`` which may insert new symbol/timeframe entries).
        for symbol_key in list(self.bar_builders.keys()):
            tfs = self.bar_builders.get(symbol_key)
            if not tfs:
                continue
            for timeframe in list(tfs.keys()):
                builder = tfs.get(timeframe)
                if builder is None or builder.open is None:
                    continue
                if builder.bar_start is None:
                    continue
                bar_end = self._get_bar_end_time(builder.bar_start, timeframe)
                if now < bar_end:
                    continue
                # Bar end has elapsed but no new tick rolled it over.
                try:
                    completed = builder.to_bar()
                except ValueError:
                    continue
                self.completed_bars[symbol_key][timeframe] = completed
                self._periodic_completions_total += 1
                logger.debug(
                    "🛡️  periodic force-close %s %s "
                    "(bar_start=%s bar_end=%s now=%s, %d total)",
                    symbol_key, timeframe,
                    builder.bar_start.isoformat(),
                    bar_end.isoformat(),
                    now.isoformat(),
                    self._periodic_completions_total,
                )
                self._emit_completed_bar(completed)
                # Open a fresh builder for the current boundary.
                new_start = self._get_bar_start_time(now, timeframe)
                tfs[timeframe] = BarBuilder(symbol_key, timeframe, new_start)

    def register_completed_bar_callback(self, callback: Callable[[Bar], None]) -> None:
        """Register a callback invoked synchronously whenever a bar closes.

        Used by strategies / live caches that want fresh OHLCV without waiting for
        the next REST poll. Multiple subscribers are supported; each receives the
        same ``Bar`` instance. Exceptions are isolated per-subscriber.
        """
        if callback in self._completed_bar_callbacks:
            return
        self._completed_bar_callbacks.append(callback)
        name = getattr(callback, "__name__", repr(callback))
        logger.debug("Registered completed-bar callback: %s", name)

    def unregister_completed_bar_callback(self, callback: Callable[[Bar], None]) -> None:
        """Remove a previously registered completed-bar callback (no-op if missing)."""
        try:
            self._completed_bar_callbacks.remove(callback)
        except ValueError:
            pass

    def add_quote(self, symbol: str, price: float, volume: int = 0, timestamp: Optional[datetime] = None):
        """
        Add a quote update to the aggregator.
        
        Args:
            symbol: Trading symbol
            price: Last price
            volume: Volume (if available)
            timestamp: Quote timestamp
        """
        if timestamp is None:
            timestamp = datetime.now(timezone.utc)
        
        symbol_key = symbol.upper()
        if symbol_key not in self.bar_builders:
            self._initialize_symbol(symbol_key, timestamp)
        
        active_frames = self.bar_builders.get(symbol_key, {})
        # Update bars for all active timeframes
        for timeframe, builder in active_frames.items():
            # Check if we need to start a new bar
            if self._should_start_new_bar(builder, timeframe, timestamp):
                # Complete the old bar
                if builder.open is not None:
                    completed_bar = builder.to_bar()
                    self.completed_bars[symbol_key][timeframe] = completed_bar
                    logger.debug(f"Completed bar for {symbol_key} {timeframe}: {completed_bar.close}")
                    self._emit_completed_bar(completed_bar)

                # Start new bar
                bar_start = self._get_bar_start_time(timestamp, timeframe)
                builder = BarBuilder(symbol_key, timeframe, bar_start)
                self.bar_builders[symbol_key][timeframe] = builder

            # Add tick to current bar
            builder.add_tick(price, volume, timestamp)
            self._emit_partial_bar(symbol_key, timeframe, builder)

    def subscribe_timeframe(self, symbol: str, timeframe: str):
        """
        Subscribe to bar updates for a symbol/timeframe.
        
        Args:
            symbol: Trading symbol
            timeframe: Bar timeframe (e.g., '1m', '5m', '15m')
        """
        symbol_key = symbol.upper()
        normalized_tf = self._normalize_timeframe(timeframe)
        self.symbol_timeframes[symbol_key].add(normalized_tf)
        if normalized_tf not in self.bar_builders[symbol_key]:
            now = datetime.now(timezone.utc)
            bar_start = self._get_bar_start_time(now, normalized_tf)
            builder = BarBuilder(symbol_key, normalized_tf, bar_start)
            self.bar_builders[symbol_key][normalized_tf] = builder
            logger.debug(f"Subscribed to {symbol_key} {normalized_tf} bars")
    
    def register_timeframes(self, symbol: str, timeframes: Iterable[str]):
        """Register one or more timeframes for a symbol (ensures builders exist)."""
        symbol_key = symbol.upper()
        now = datetime.now(timezone.utc)
        for tf in timeframes:
            normalized = self._normalize_timeframe(tf)
            if not normalized:
                continue
            self.symbol_timeframes[symbol_key].add(normalized)
            if normalized not in self.bar_builders[symbol_key]:
                bar_start = self._get_bar_start_time(now, normalized)
                self.bar_builders[symbol_key][normalized] = BarBuilder(symbol_key, normalized, bar_start)
                logger.debug(f"Registered timeframe {normalized} for {symbol_key}")
    
    def unsubscribe_timeframe(self, symbol: str, timeframe: str):
        """Unsubscribe from bar updates for a symbol/timeframe."""
        symbol_key = symbol.upper()
        if symbol_key in self.bar_builders:
            self.bar_builders[symbol_key].pop(timeframe, None)
            if not self.bar_builders[symbol_key]:
                del self.bar_builders[symbol_key]
        if symbol_key in self.symbol_timeframes:
            self.symbol_timeframes[symbol_key].discard(self._normalize_timeframe(timeframe))
    
    def _should_start_new_bar(self, builder: BarBuilder, timeframe: str, current_time: datetime) -> bool:
        """Check if we should start a new bar based on timeframe."""
        if builder.bar_start is None:
            return True
        
        bar_end = self._get_bar_end_time(builder.bar_start, timeframe)
        return current_time >= bar_end
    
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
    
    def _get_daily_bar_end_time(self, bar_start: datetime) -> datetime:
        """
        Get the end time for a daily bar based on EST market hours.
        
        Rules:
        - Daily bars end at 18:00 ET (6pm) the next day
        - This aligns with the overnight session: 18:00 to 18:00
        """
        try:
            import pytz
            et_tz = pytz.timezone('US/Eastern')
        except ImportError:
            et_tz = timezone(timedelta(hours=-5))
        
        # Convert to EST
        if bar_start.tzinfo is None:
            bar_start = bar_start.replace(tzinfo=timezone.utc)
        bar_start_et = bar_start.astimezone(et_tz)
        
        # All daily bars end at 18:00 ET the next day
        bar_end_et = (bar_start_et + timedelta(days=1)).replace(hour=18, minute=0, second=0, microsecond=0)
        
        # Convert back to UTC
        return bar_end_et.astimezone(timezone.utc)
    
    def _get_bar_start_time(self, timestamp: datetime, timeframe: str) -> datetime:
        """Get the start time for a bar given a timestamp and timeframe."""
        # Special handling for daily bars
        if timeframe.endswith('d'):
            return self._get_daily_bar_start_time(timestamp)
        
        # Parse timeframe (e.g., '5m' -> 5 minutes)
        if timeframe.endswith('m'):
            minutes = int(timeframe[:-1])
            bar_seconds = minutes * 60
        elif timeframe.endswith('s'):
            bar_seconds = int(timeframe[:-1])
        elif timeframe.endswith('h'):
            hours = int(timeframe[:-1])
            bar_seconds = hours * 3600
        else:
            # Default to 1 minute if unknown timeframe
            bar_seconds = 60
        total_seconds = timestamp.timestamp()
        bar_start_seconds = (int(total_seconds) // bar_seconds) * bar_seconds
        return datetime.fromtimestamp(bar_start_seconds, tz=timezone.utc)
    
    def _get_bar_end_time(self, bar_start: datetime, timeframe: str) -> datetime:
        """Get the end time for a bar."""
        # Special handling for daily bars
        if timeframe.endswith('d'):
            return self._get_daily_bar_end_time(bar_start)
        
        if timeframe.endswith('m'):
            minutes = int(timeframe[:-1])
            return bar_start + timedelta(minutes=minutes)
        elif timeframe.endswith('s'):
            seconds = int(timeframe[:-1])
            return bar_start + timedelta(seconds=seconds)
        elif timeframe.endswith('h'):
            hours = int(timeframe[:-1])
            return bar_start + timedelta(hours=hours)
        else:
            return bar_start + timedelta(minutes=1)
    
    def get_current_bar(self, symbol: str, timeframe: str) -> Optional[Bar]:
        """Get the current (forming) bar for a symbol/timeframe."""
        symbol_key = symbol.upper()
        if symbol_key in self.bar_builders:
            builder = self.bar_builders[symbol_key].get(timeframe)
            if builder and builder.open is not None:
                return builder.to_bar()
        return None
    
    def get_last_completed_bar(self, symbol: str, timeframe: str) -> Optional[Bar]:
        """Get the last completed bar for a symbol/timeframe."""
        symbol_key = symbol.upper()
        if symbol_key in self.completed_bars:
            return self.completed_bars[symbol_key].get(timeframe)
        return None

    def _normalize_timeframe(self, timeframe: str) -> str:
        """Normalize timeframe strings (strip spaces, lower-case)."""
        return timeframe.strip().lower()
    
    def _initialize_symbol(self, symbol_key: str, timestamp: datetime):
        """Initialize builders for a symbol using registered or default timeframes."""
        frames = self.symbol_timeframes.get(symbol_key)
        if not frames:
            frames = set(self.default_timeframes)
            self.symbol_timeframes[symbol_key] = set(frames)
        
        # Always ensure all default timeframes are registered for the symbol
        # This ensures bars are built for all timeframes when quotes arrive
        for tf in self.default_timeframes:
            normalized = self._normalize_timeframe(tf)
            if not normalized:
                continue
            self.symbol_timeframes[symbol_key].add(normalized)
            if normalized not in self.bar_builders[symbol_key]:
                bar_start = self._get_bar_start_time(timestamp, normalized)
                self.bar_builders[symbol_key][normalized] = BarBuilder(symbol_key, normalized, bar_start)
                logger.debug(f"Initialized {normalized} bar builder for {symbol_key}")
        
        logger.info(f"📊 Initialized {symbol_key} with {len(self.symbol_timeframes[symbol_key])} timeframes: {', '.join(sorted(self.symbol_timeframes[symbol_key]))}")

