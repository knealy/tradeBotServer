"""
Trend Detection Module

Provides reusable trend quality scoring system to distinguish trending markets
from choppy/consolidating markets. Can be used by any strategy to filter trades.

Key Features:
- Multi-factor scoring system (0-100)
- EMA separation analysis
- Direction consistency checks
- Momentum and volatility indicators
- Conservative thresholds (can be adjusted)
"""

import logging
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class TrendQualityScore:
    """Trend quality score and breakdown."""
    total_score: int  # 0-100
    ema_separation_score: int
    direction_consistency_score: int
    momentum_score: int
    atr_expansion_score: int
    candle_consistency_score: int
    is_trending: bool  # True if score >= minimum threshold
    trend_direction: Optional[str]  # "LONG", "SHORT", or None
    details: Dict  # Additional details for debugging


class TrendDetector:
    """
    Detects trending vs choppy market conditions using multi-factor analysis.
    
    Usage:
        detector = TrendDetector(
            ema_fast_period=8,
            ema_slow_period=21,
            min_trend_score=40  # Conservative threshold
        )
        
        score = await detector.calculate_trend_quality(symbol, bars)
        if score.is_trending:
            # Safe to trade
            pass
    """
    
    def __init__(
        self,
        ema_fast_period: int = 8,
        ema_slow_period: int = 21,
        atr_period: int = 14,
        min_trend_score: int = 40,  # VERY conservative - start low
        lookback_bars: int = 10
    ):
        """
        Initialize trend detector.
        
        Args:
            ema_fast_period: Fast EMA period (default: 8)
            ema_slow_period: Slow EMA period (default: 21)
            atr_period: ATR period for volatility (default: 14)
            min_trend_score: Minimum score to consider trending (default: 40 - very conservative)
            lookback_bars: Number of bars to analyze for consistency (default: 10)
        """
        self.ema_fast_period = ema_fast_period
        self.ema_slow_period = ema_slow_period
        self.atr_period = atr_period
        self.min_trend_score = min_trend_score
        self.lookback_bars = lookback_bars
        
        # Conservative thresholds (can be adjusted)
        self.ema_separation_min = 0.05  # 0.05% = ~12 points for MNQ (very conservative)
        self.ema_separation_good = 0.10  # 0.10% = ~25 points
        self.ema_separation_excellent = 0.15  # 0.15% = ~37 points
        
        self.momentum_min = 0.05  # 0.05% = ~12 points (very conservative)
        self.momentum_good = 0.15  # 0.15% = ~37 points
        self.momentum_excellent = 0.25  # 0.25% = ~62 points
        
        self.atr_expansion_min = 1.05  # 5% expansion (very conservative)
        self.atr_expansion_good = 1.15  # 15% expansion
        self.atr_expansion_excellent = 1.25  # 25% expansion
        
        self.direction_consistency_min = 0.50  # 50% same direction (very conservative)
        self.direction_consistency_good = 0.70  # 70% same direction
        self.direction_consistency_excellent = 0.85  # 85% same direction
        
        logger.info(f"✅ TrendDetector initialized: min_score={min_trend_score}, "
                   f"EMA={ema_fast_period}/{ema_slow_period}, ATR={atr_period}")
    
    def _get_close(self, bar: Dict) -> float:
        """Extract close price from bar (handles different formats)."""
        return bar.get('close', bar.get('Close', bar.get('c', 0)))
    
    def _get_high(self, bar: Dict) -> float:
        """Extract high price from bar."""
        return bar.get('high', bar.get('High', bar.get('h', 0)))
    
    def _get_low(self, bar: Dict) -> float:
        """Extract low price from bar."""
        return bar.get('low', bar.get('Low', bar.get('l', 0)))
    
    def _calculate_ema(self, closes: List[float], period: int) -> float:
        """Calculate EMA from list of closing prices."""
        if len(closes) < period:
            return 0.0
        
        multiplier = 2 / (period + 1)
        # Start with SMA of first period values
        ema = sum(closes[:period]) / period
        
        # Calculate EMA for remaining values
        for close in closes[period:]:
            ema = (close * multiplier) + (ema * (1 - multiplier))
        
        return ema
    
    def _calculate_atr(self, bars: List[Dict], period: int) -> float:
        """Calculate ATR from bars."""
        if len(bars) < period + 1:
            return 0.0
        
        true_ranges = []
        for i in range(1, len(bars)):
            high = self._get_high(bars[i])
            low = self._get_low(bars[i])
            prev_close = self._get_close(bars[i-1])
            
            if high == 0 or low == 0 or prev_close == 0:
                continue
            
            tr = max(
                high - low,
                abs(high - prev_close),
                abs(low - prev_close)
            )
            true_ranges.append(tr)
        
        if len(true_ranges) < period:
            return 0.0
        
        return sum(true_ranges[-period:]) / period
    
    def _score_ema_separation(
        self, 
        fast_ema: float, 
        slow_ema: float, 
        current_price: float
    ) -> Tuple[int, Dict]:
        """
        Score EMA separation (0-25 points).
        
        Returns:
            (score, details_dict)
        """
        if current_price == 0:
            return 0, {"error": "Invalid price"}
        
        separation = abs(fast_ema - slow_ema)
        separation_pct = (separation / current_price) * 100
        
        details = {
            "separation": separation,
            "separation_pct": separation_pct,
            "fast_ema": fast_ema,
            "slow_ema": slow_ema
        }
        
        if separation_pct >= self.ema_separation_excellent:
            return 25, details
        elif separation_pct >= self.ema_separation_good:
            return 15, details
        elif separation_pct >= self.ema_separation_min:
            return 8, details
        else:
            return 0, details
    
    def _score_direction_consistency(
        self, 
        bars: List[Dict], 
        fast_ema: float, 
        slow_ema: float
    ) -> Tuple[int, Dict]:
        """
        Score EMA direction consistency (0-20 points).
        
        Checks how many recent bars have price moving in same direction as EMA trend.
        Simplified: checks if recent price movement aligns with EMA direction.
        
        Returns:
            (score, details_dict)
        """
        if len(bars) < self.lookback_bars:
            return 0, {"error": "Not enough bars"}
        
        # Current EMA direction
        current_ema_direction = fast_ema > slow_ema  # True = LONG, False = SHORT
        
        # Check recent price movement direction
        closes = [self._get_close(bar) for bar in bars[-self.lookback_bars:]]
        if len(closes) < 2:
            return 0, {"error": "Not enough closes"}
        
        # Count how many recent price moves align with EMA direction
        aligned_moves = 0
        for i in range(1, len(closes)):
            price_move_up = closes[i] > closes[i-1]
            # If EMA says LONG and price moved up, or EMA says SHORT and price moved down
            if (current_ema_direction and price_move_up) or (not current_ema_direction and not price_move_up):
                aligned_moves += 1
        
        consistency_ratio = aligned_moves / (len(closes) - 1) if len(closes) > 1 else 0
        
        details = {
            "aligned_moves": aligned_moves,
            "total_moves": len(closes) - 1,
            "consistency_ratio": consistency_ratio,
            "ema_direction": "LONG" if current_ema_direction else "SHORT"
        }
        
        if consistency_ratio >= self.direction_consistency_excellent:
            return 20, details
        elif consistency_ratio >= self.direction_consistency_good:
            return 12, details
        elif consistency_ratio >= self.direction_consistency_min:
            return 5, details
        else:
            return 0, details
    
    def _score_momentum(
        self, 
        bars: List[Dict]
    ) -> Tuple[int, Dict]:
        """
        Score price momentum (0-20 points).
        
        Returns:
            (score, details_dict)
        """
        if len(bars) < self.lookback_bars:
            return 0, {"error": "Not enough bars"}
        
        current_price = self._get_close(bars[-1])
        past_price = self._get_close(bars[-self.lookback_bars])
        
        if past_price == 0:
            return 0, {"error": "Invalid past price"}
        
        momentum = current_price - past_price
        momentum_pct = (momentum / past_price) * 100
        
        details = {
            "momentum": momentum,
            "momentum_pct": momentum_pct,
            "current_price": current_price,
            "past_price": past_price
        }
        
        abs_momentum_pct = abs(momentum_pct)
        
        if abs_momentum_pct >= self.momentum_excellent:
            return 20, details
        elif abs_momentum_pct >= self.momentum_good:
            return 12, details
        elif abs_momentum_pct >= self.momentum_min:
            return 5, details
        else:
            return 0, details
    
    def _score_atr_expansion(
        self, 
        bars: List[Dict]
    ) -> Tuple[int, Dict]:
        """
        Score ATR expansion (0-15 points).
        
        Returns:
            (score, details_dict)
        """
        if len(bars) < self.atr_period + 20:
            return 0, {"error": "Not enough bars for ATR analysis"}
        
        current_atr = self._calculate_atr(bars, self.atr_period)
        
        # Calculate average ATR over last 20 periods
        atr_values = []
        for i in range(len(bars) - 20, len(bars) - self.atr_period):
            if i < self.atr_period:
                continue
            atr_i = self._calculate_atr(bars[:i+1], self.atr_period)
            atr_values.append(atr_i)
        
        if not atr_values:
            return 0, {"error": "Could not calculate average ATR"}
        
        avg_atr = sum(atr_values) / len(atr_values)
        
        if avg_atr == 0:
            return 0, {"error": "Zero average ATR"}
        
        atr_ratio = current_atr / avg_atr
        
        details = {
            "current_atr": current_atr,
            "average_atr": avg_atr,
            "atr_ratio": atr_ratio
        }
        
        if atr_ratio >= self.atr_expansion_excellent:
            return 15, details
        elif atr_ratio >= self.atr_expansion_good:
            return 10, details
        elif atr_ratio >= self.atr_expansion_min:
            return 5, details
        else:
            return 0, details
    
    def _score_candle_consistency(
        self, 
        bars: List[Dict]
    ) -> Tuple[int, Dict]:
        """
        Score candle direction consistency (0-10 points).
        
        Returns:
            (score, details_dict)
        """
        if len(bars) < 8:
            return 0, {"error": "Not enough bars"}
        
        # Check last 8 candles
        recent_bars = bars[-8:]
        
        same_direction_count = 0
        prev_direction = None
        
        for bar in recent_bars:
            open_price = bar.get('open', bar.get('Open', bar.get('o', 0)))
            close_price = self._get_close(bar)
            
            if open_price == 0 or close_price == 0:
                continue
            
            direction = "UP" if close_price > open_price else "DOWN"
            
            if prev_direction is None:
                prev_direction = direction
                same_direction_count = 1
            elif direction == prev_direction:
                same_direction_count += 1
            else:
                # Direction changed, reset count
                same_direction_count = 1
                prev_direction = direction
        
        consistency_ratio = same_direction_count / len(recent_bars)
        
        details = {
            "same_direction_count": same_direction_count,
            "total_candles": len(recent_bars),
            "consistency_ratio": consistency_ratio
        }
        
        if consistency_ratio >= 0.75:  # 6+ out of 8
            return 10, details
        elif consistency_ratio >= 0.50:  # 4+ out of 8
            return 5, details
        else:
            return 0, details
    
    async def calculate_trend_quality(
        self,
        symbol: str,
        bars: List[Dict],
        fast_ema: Optional[float] = None,
        slow_ema: Optional[float] = None
    ) -> TrendQualityScore:
        """
        Calculate comprehensive trend quality score.
        
        Args:
            symbol: Trading symbol (for logging)
            bars: List of bar dictionaries (OHLC data)
            fast_ema: Pre-calculated fast EMA (optional, will calculate if None)
            slow_ema: Pre-calculated slow EMA (optional, will calculate if None)
        
        Returns:
            TrendQualityScore object with total score and breakdown
        """
        try:
            if not bars or len(bars) < self.ema_slow_period * 2:
                logger.warning(f"Not enough bars for trend analysis: {len(bars) if bars else 0}")
                return TrendQualityScore(
                    total_score=0,
                    ema_separation_score=0,
                    direction_consistency_score=0,
                    momentum_score=0,
                    atr_expansion_score=0,
                    candle_consistency_score=0,
                    is_trending=False,
                    trend_direction=None,
                    details={"error": "Insufficient data"}
                )
            
            # Extract closing prices
            closes = [self._get_close(bar) for bar in bars if self._get_close(bar) > 0]
            
            if len(closes) < self.ema_slow_period:
                return TrendQualityScore(
                    total_score=0,
                    ema_separation_score=0,
                    direction_consistency_score=0,
                    momentum_score=0,
                    atr_expansion_score=0,
                    candle_consistency_score=0,
                    is_trending=False,
                    trend_direction=None,
                    details={"error": "Not enough closing prices"}
                )
            
            # Calculate EMAs if not provided
            if fast_ema is None:
                fast_ema = self._calculate_ema(closes, self.ema_fast_period)
            if slow_ema is None:
                slow_ema = self._calculate_ema(closes, self.ema_slow_period)
            
            current_price = closes[-1]
            
            # Determine trend direction
            trend_direction = None
            if fast_ema > slow_ema:
                trend_direction = "LONG"
            elif fast_ema < slow_ema:
                trend_direction = "SHORT"
            
            # Calculate individual scores
            ema_sep_score, ema_sep_details = self._score_ema_separation(
                fast_ema, slow_ema, current_price
            )
            
            dir_cons_score, dir_cons_details = self._score_direction_consistency(
                bars, fast_ema, slow_ema
            )
            
            momentum_score, momentum_details = self._score_momentum(bars)
            
            atr_score, atr_details = self._score_atr_expansion(bars)
            
            candle_score, candle_details = self._score_candle_consistency(bars)
            
            # Total score
            total_score = (
                ema_sep_score +
                dir_cons_score +
                momentum_score +
                atr_score +
                candle_score
            )
            
            is_trending = total_score >= self.min_trend_score
            
            # Compile details
            details = {
                "fast_ema": fast_ema,
                "slow_ema": slow_ema,
                "current_price": current_price,
                "ema_separation": ema_sep_details,
                "direction_consistency": dir_cons_details,
                "momentum": momentum_details,
                "atr_expansion": atr_details,
                "candle_consistency": candle_details
            }
            
            return TrendQualityScore(
                total_score=total_score,
                ema_separation_score=ema_sep_score,
                direction_consistency_score=dir_cons_score,
                momentum_score=momentum_score,
                atr_expansion_score=atr_score,
                candle_consistency_score=candle_score,
                is_trending=is_trending,
                trend_direction=trend_direction,
                details=details
            )
            
        except Exception as e:
            logger.error(f"Error calculating trend quality for {symbol}: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return TrendQualityScore(
                total_score=0,
                ema_separation_score=0,
                direction_consistency_score=0,
                momentum_score=0,
                atr_expansion_score=0,
                candle_consistency_score=0,
                is_trending=False,
                trend_direction=None,
                details={"error": str(e)}
            )

