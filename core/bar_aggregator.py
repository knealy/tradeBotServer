"""
Real-time bar aggregator for converting SignalR quote updates into OHLCV bars.

Aggregates tick data into time-based bars (1m, 5m, etc.) and streams them
to WebSocket clients for real-time chart updates.
"""

import asyncio
import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Dict, Optional, Callable, Any
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
    
    def __init__(self, broadcast_callback: Optional[Callable[[Dict[str, Any]], None]] = None):
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
        
    async def start(self):
        """Start the bar aggregator update loop."""
        if self._running:
            return
        self._running = True
        self._update_task = asyncio.create_task(self._update_loop())
        logger.info("📊 Bar aggregator started")
    
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
        
        # Auto-subscribe to common timeframes if not already subscribed
        # This ensures bars are built even if frontend hasn't explicitly subscribed
        symbol_key = symbol.upper()
        common_timeframes = ['1m', '5m', '15m', '1h']
        
        if symbol_key not in self.bar_builders:
            # Auto-subscribe to common timeframes for this symbol
            for tf in common_timeframes:
                bar_start = self._get_bar_start_time(timestamp, tf)
                self.bar_builders[symbol_key][tf] = BarBuilder(symbol_key, tf, bar_start)
            logger.info(f"📊 Auto-subscribed {symbol_key} to timeframes: {', '.join(common_timeframes)}")
        
        # Update bars for all active timeframes
        if symbol_key in self.bar_builders:
            for timeframe, builder in self.bar_builders[symbol_key].items():
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
        if symbol_key not in self.bar_builders:
            self.bar_builders[symbol_key] = {}
        
        if timeframe not in self.bar_builders[symbol_key]:
            # Start a new bar for this timeframe
            now = datetime.now(timezone.utc)
            bar_start = self._get_bar_start_time(now, timeframe)
            builder = BarBuilder(symbol_key, timeframe, bar_start)
            self.bar_builders[symbol_key][timeframe] = builder
            logger.debug(f"Subscribed to {symbol_key} {timeframe} bars")
    
    def unsubscribe_timeframe(self, symbol: str, timeframe: str):
        """Unsubscribe from bar updates for a symbol/timeframe."""
        symbol_key = symbol.upper()
        if symbol_key in self.bar_builders:
            self.bar_builders[symbol_key].pop(timeframe, None)
            if not self.bar_builders[symbol_key]:
                del self.bar_builders[symbol_key]
    
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
            # Round down to the nearest bar start
            total_seconds = timestamp.timestamp()
            bar_seconds = minutes * 60
            bar_start_seconds = (int(total_seconds) // bar_seconds) * bar_seconds
            return datetime.fromtimestamp(bar_start_seconds, tz=timezone.utc)
        elif timeframe.endswith('s'):
            seconds = int(timeframe[:-1])
            total_seconds = timestamp.timestamp()
            bar_start_seconds = (int(total_seconds) // seconds) * seconds
            return datetime.fromtimestamp(bar_start_seconds, tz=timezone.utc)
        else:
            # Default to current time
            return timestamp
    
    def _get_bar_end_time(self, bar_start: datetime, timeframe: str) -> datetime:
        """Get the end time for a bar."""
        if timeframe.endswith('m'):
            minutes = int(timeframe[:-1])
            from datetime import timedelta
            return bar_start + timedelta(minutes=minutes)
        elif timeframe.endswith('s'):
            seconds = int(timeframe[:-1])
            from datetime import timedelta
            return bar_start + timedelta(seconds=seconds)
        else:
            # Default to 1 minute
            from datetime import timedelta
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

