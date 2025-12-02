"""
Real-time bar aggregator for converting SignalR quote updates into OHLCV bars.

Aggregates tick data into time-based bars (1m, 5m, etc.) and streams them
to WebSocket clients for real-time chart updates.
"""

import asyncio
import logging
import os
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from typing import Dict, Optional, Callable, Any, Iterable, Set, List
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
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


@dataclass
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
        self.bar_builders: Dict[str, Dict[str, BarBuilder]] = defaultdict(dict)  # {symbol: {timeframe: BarBuilder}}
        self.completed_bars: Dict[str, Dict[str, Bar]] = defaultdict(dict)  # {symbol: {timeframe: Bar}}
        self._broadcast_log_counts: Dict[str, int] = defaultdict(int)
        self.lock = asyncio.Lock()
        self.update_interval = 0.2  # 5 updates per second (200ms)
        self._update_task: Optional[asyncio.Task] = None
        self._running = False
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
        """Start the bar aggregator update loop."""
        if self._running:
            logger.warning("⚠️  Bar aggregator already running")
            return
        self._running = True
        self._update_task = asyncio.create_task(self._update_loop())
        logger.info(f"📊 Bar aggregator started - tracking {len(self.default_timeframes)} timeframes: {', '.join(self.default_timeframes)}")
    
    async def stop(self):
        """Stop the bar aggregator."""
        self._running = False
        if self._update_task:
            self._update_task.cancel()
            try:
                await self._update_task
            except asyncio.CancelledError:
                pass
        logger.info("📊 Bar aggregator stopped")
    
    async def _update_loop(self):
        """Periodic update loop to broadcast bar updates."""
        while self._running:
            try:
                await asyncio.sleep(self.update_interval)
                await self._broadcast_updates()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in bar aggregator update loop: {e}")
    
    async def _broadcast_updates(self):
        """Broadcast current bar updates to WebSocket clients."""
        if not self.broadcast_callback:
            return
        
        async with self.lock:
            for symbol, timeframes in self.bar_builders.items():
                for timeframe, builder in timeframes.items():
                    if builder.last_update and builder.close is not None:
                        # Only broadcast if bar was updated recently (within last 2 seconds)
                        time_since_update = (datetime.now(timezone.utc) - builder.last_update).total_seconds()
                        if time_since_update > 2.0:
                            continue  # Skip stale bars
                        
                        # Create partial bar update
                        bar_data = {
                            "symbol": symbol,
                            "timeframe": timeframe,
                            "timestamp": builder.bar_start.isoformat(),
                            "bar": {
                                "open": builder.open,
                                "high": builder.high or builder.open,
                                "low": builder.low or builder.open,
                                "close": builder.close,
                                "volume": builder.volume,
                            },
                            "is_partial": True,  # Indicates this is a forming bar
                        }
                        
                        # Broadcast via callback
                        if self.broadcast_callback:
                            try:
                                self.broadcast_callback({
                                    "type": "market_update",
                                    "data": bar_data,
                                    "timestamp": datetime.now(timezone.utc).isoformat()
                                })
                                key = f"{symbol}:{timeframe}"
                                count = self._broadcast_log_counts[key]
                                if count < 5:
                                    logger.info(
                                        f"📡 Broadcasted {timeframe} bar update for {symbol}: "
                                        f"O:{bar_data['bar']['open']} H:{bar_data['bar']['high']} "
                                        f"L:{bar_data['bar']['low']} C:{bar_data['bar']['close']} "
                                        f"(tick_count={builder.tick_count})"
                                    )
                                    self._broadcast_log_counts[key] = count + 1
                            except Exception as e:
                                logger.debug(f"Error broadcasting bar update: {e}")
    
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
                
                # Start new bar
                bar_start = self._get_bar_start_time(timestamp, timeframe)
                builder = BarBuilder(symbol_key, timeframe, bar_start)
                self.bar_builders[symbol_key][timeframe] = builder
            
            # Add tick to current bar
            builder.add_tick(price, volume, timestamp)
    
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
    
    def _get_bar_start_time(self, timestamp: datetime, timeframe: str) -> datetime:
        """Get the start time for a bar given a timestamp and timeframe."""
        # Parse timeframe (e.g., '5m' -> 5 minutes)
        if timeframe.endswith('m'):
            minutes = int(timeframe[:-1])
            bar_seconds = minutes * 60
        elif timeframe.endswith('s'):
            bar_seconds = int(timeframe[:-1])
        elif timeframe.endswith('h'):
            hours = int(timeframe[:-1])
            bar_seconds = hours * 3600
        elif timeframe.endswith('d'):
            days = int(timeframe[:-1])
            bar_seconds = days * 86400
        else:
            # Default to 1 minute if unknown timeframe
            bar_seconds = 60
        total_seconds = timestamp.timestamp()
        bar_start_seconds = (int(total_seconds) // bar_seconds) * bar_seconds
        return datetime.fromtimestamp(bar_start_seconds, tz=timezone.utc)
    
    def _get_bar_end_time(self, bar_start: datetime, timeframe: str) -> datetime:
        """Get the end time for a bar."""
        if timeframe.endswith('m'):
            minutes = int(timeframe[:-1])
            return bar_start + timedelta(minutes=minutes)
        elif timeframe.endswith('s'):
            seconds = int(timeframe[:-1])
            return bar_start + timedelta(seconds=seconds)
        elif timeframe.endswith('h'):
            hours = int(timeframe[:-1])
            return bar_start + timedelta(hours=hours)
        elif timeframe.endswith('d'):
            days = int(timeframe[:-1])
            return bar_start + timedelta(days=days)
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

