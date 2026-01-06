"""
Backtest Executor

Command-line interface for running backtests with various parameters.

Usage:
    python core/backtest_executor.py --strategy=overnight_range --symbol=MNQ --timeframe=5m --days=30
    python core/backtest_executor.py --strategy=trend_scalping --symbol=MNQ --start=2024-01-01 --end=2024-12-31
    python core/backtest_executor.py --strategy=mean_reversion --symbol=MES --days=90 --monte-carlo=1000
"""

import sys
import os
import asyncio
import argparse
import logging
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
import pandas as pd

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.backtest import HistoricalDataLoader, BacktestEngine, PerformanceMetrics, MonteCarloSimulator
from core.backtest.models import OrderSide, OrderType
from core.backtest.strategy_replay import StrategyReplayEngine
from brokers.topstepx_adapter import TopStepXAdapter
from core.auth import AuthManager

logger = logging.getLogger(__name__)


class BacktestExecutor:
    """
    Execute backtests from command line with various strategies and parameters.
    """
    
    def __init__(self, broker_adapter=None):
        """
        Initialize backtest executor.
        
        Args:
            broker_adapter: Optional broker adapter for real historical data
        """
        self.loader = HistoricalDataLoader(broker_adapter=broker_adapter)
        self.results_cache = {}
    
    async def run_backtest(
        self,
        strategy_name: str,
        symbol: str,
        timeframe: str = "1m",
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        days: Optional[int] = None,
        initial_capital: float = 50000.0,
        use_sample_data: bool = False,
        csv_file: Optional[str] = None,
        slippage_ticks: float = 0.5,
        **strategy_params
    ) -> Dict[str, Any]:
        """
        Run a backtest with specified parameters.
        
        Args:
            strategy_name: Name of strategy to backtest
            symbol: Trading symbol
            timeframe: Bar interval (30s, 1m, 5m, etc.)
            start_date: Start date for backtest
            end_date: End date for backtest
            days: Number of days to backtest (alternative to start/end)
            initial_capital: Starting capital
            use_sample_data: Use generated sample data instead of real data
            csv_file: Path to CSV file with historical data
            **strategy_params: Additional strategy parameters
            
        Returns:
            Dict with backtest results
        """
        print(f"\n{'='*80}")
        print(f"BACKTESTING: {strategy_name.upper()}")
        print(f"{'='*80}")
        print(f"Symbol: {symbol}")
        print(f"Timeframe: {timeframe}")
        print(f"Initial Capital: ${initial_capital:,.2f}")
        
        # Load historical data
        if csv_file:
            # Load from CSV file (from history command export)
            print(f"\n📊 Loading data from CSV: {csv_file}")
            data = self.loader.load_from_csv(
                filepath=csv_file,
                symbol=symbol
            )
            if isinstance(data, pd.DataFrame):
                print(f"✅ Loaded {len(data)} bars from CSV")
                # Convert DataFrame to list of dicts for replay mode
                data_dicts = []
                for idx, row in data.iterrows():
                    data_dicts.append({
                        'timestamp': idx if isinstance(idx, datetime) else pd.to_datetime(idx),
                        'open': float(row.get('open', row.get('Open', 0))),
                        'high': float(row.get('high', row.get('High', 0))),
                        'low': float(row.get('low', row.get('Low', 0))),
                        'close': float(row.get('close', row.get('Close', 0))),
                        'volume': int(row.get('volume', row.get('Volume', 0)))
                    })
                data = data_dicts
            else:
                print(f"✅ Loaded {len(data)} bars from CSV")
        elif use_sample_data or not self.loader.broker_adapter:
            print(f"\n📊 Generating sample data...")
            data_df = self.loader.get_sample_data(
                symbol=symbol,
                days=days or 30,
                timeframe=timeframe
            )
            print(f"✅ Generated {len(data_df)} bars of sample data")
            # Convert DataFrame to list of dicts for replay mode
            data = []
            for idx, row in data_df.iterrows():
                data.append({
                    'timestamp': idx if isinstance(idx, datetime) else pd.to_datetime(idx),
                    'open': float(row.get('open', row.get('Open', 0))),
                    'high': float(row.get('high', row.get('High', 0))),
                    'low': float(row.get('low', row.get('Low', 0))),
                    'close': float(row.get('close', row.get('Close', 0))),
                    'volume': int(row.get('volume', row.get('Volume', 0)))
                })
        else:
            # Use broker adapter to fetch real historical data
            if not self.loader.broker_adapter:
                print("❌ No broker adapter available - cannot load real historical data")
                print("   Use --sample for sample data or provide --csv file")
                return None
            
            # Calculate date range
            if days and not start_date:
                end_date = end_date or datetime.now()
                start_date = end_date - timedelta(days=days)
            
            if not start_date or not end_date:
                print("❌ Start and end dates are required for API data loading")
                print("   Use --start and --end or --days")
                return None
            
            print(f"\n📊 Loading historical data from TopStepX API...")
            print(f"   Period: {start_date.date()} to {end_date.date()}")
            print(f"   Symbol: {symbol}, Timeframe: {timeframe}")
            
            # Use broker adapter's get_historical_data (returns List[Bar])
            bars = await self.loader.broker_adapter.get_historical_data(
                symbol=symbol,
                timeframe=timeframe,
                start_time=start_date,
                end_time=end_date,
                limit=20000  # API max
            )
            
            if not bars:
                print("❌ No data returned from API")
                return None
            
            print(f"✅ Loaded {len(bars)} bars from API")
            
            # Convert Bar objects to list of dicts for replay mode
            data = []
            for bar in bars:
                data.append({
                    'timestamp': bar.timestamp,
                    'open': float(bar.open),
                    'high': float(bar.high),
                    'low': float(bar.low),
                    'close': float(bar.close),
                    'volume': int(bar.volume or 0)
                })
        
        # Validate data (skip for replay mode if data is list of dicts)
        if isinstance(data, pd.DataFrame):
            if not self.loader.validate_data(data):
                print("❌ Data validation failed!")
                return None
            # Add indicators if needed (only for DataFrame)
            data = self.loader.add_indicators(data, indicators=['ema_89', 'ema_233', 'atr_14', 'rsi_14', 'sma_20'])
        elif isinstance(data, list):
            # For replay mode with list of dicts, basic validation
            if not data or len(data) < 2:
                print("❌ Insufficient data for backtest!")
                return None
            print(f"✅ Data validated: {len(data)} bars")
        else:
            print(f"❌ Invalid data type: {type(data)}")
            return None
        
        # Check if this is a replay mode strategy (class-based)
        strategy_func = self._get_strategy_function(strategy_name, **strategy_params)
        
        if strategy_func is None:
            # This is a class-based strategy - use replay mode
            return await self._run_strategy_replay(
                strategy_name=strategy_name,
                symbol=symbol,
                bars=data,
                initial_capital=initial_capital,
                slippage_ticks=slippage_ticks,
                **strategy_params
            )
        
        # Run traditional function-based backtest
        print(f"\n🔬 Running backtest...")
        engine = BacktestEngine(
            initial_capital=initial_capital,
            commission_per_contract=2.50,
            slippage_ticks=slippage_ticks,
            point_value=self._get_point_value(symbol)
        )
        
        result = await engine.run(
            strategy_func=strategy_func,
            data=data,
            symbol=symbol,
            strategy_name=strategy_name,
            tick_size=self._get_tick_size(symbol)
        )
        
        # Print results
        print("\n" + PerformanceMetrics.generate_report(result))
        
        # Cache results
        cache_key = f"{strategy_name}_{symbol}_{timeframe}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        self.results_cache[cache_key] = result
        
        return {
            'result': result,
            'cache_key': cache_key,
            'data': data
        }
    
    async def run_monte_carlo(
        self,
        result_or_cache_key,
        num_simulations: int = 1000,
        initial_capital: float = 50000.0
    ) -> Dict[str, Any]:
        """
        Run Monte Carlo simulation on backtest results.
        
        Args:
            result_or_cache_key: BacktestResult object or cache key
            num_simulations: Number of simulations to run
            initial_capital: Starting capital
            
        Returns:
            Dict with Monte Carlo results
        """
        # Get result
        if isinstance(result_or_cache_key, str):
            result = self.results_cache.get(result_or_cache_key)
            if not result:
                print(f"❌ No cached result found for key: {result_or_cache_key}")
                return None
        else:
            result = result_or_cache_key
        
        if not result.trades:
            print("❌ No trades to analyze!")
            return None
        
        print(f"\n🎲 Running {num_simulations} Monte Carlo simulations...")
        
        mc = MonteCarloSimulator(initial_capital=initial_capital)
        mc_results = mc.run_simulations(
            trades=result.trades,
            num_simulations=num_simulations
        )
        
        print("\n" + mc.generate_report(mc_results))
        
        return mc_results
    
    async def run_optimization(
        self,
        strategy_name: str,
        symbol: str,
        param_ranges: Dict[str, list],
        timeframe: str = "1m",
        days: int = 30,
        metric: str = "sharpe_ratio"
    ) -> Dict[str, Any]:
        """
        Run parameter optimization for a strategy.
        
        Args:
            strategy_name: Strategy to optimize
            symbol: Trading symbol
            param_ranges: Dict of parameter names to lists of values
            timeframe: Bar interval
            days: Days to backtest
            metric: Metric to optimize (sharpe_ratio, total_return_pct, etc.)
            
        Returns:
            Dict with optimization results
        """
        print(f"\n{'='*80}")
        print(f"PARAMETER OPTIMIZATION: {strategy_name.upper()}")
        print(f"{'='*80}")
        print(f"Symbol: {symbol}")
        print(f"Optimizing: {metric}")
        print(f"Parameters: {param_ranges}")
        
        best_params = None
        best_score = float('-inf')
        results = []
        
        # Generate parameter combinations
        import itertools
        param_names = list(param_ranges.keys())
        param_values = list(param_ranges.values())
        combinations = list(itertools.product(*param_values))
        
        print(f"\n🔬 Testing {len(combinations)} parameter combinations...")
        
        for i, values in enumerate(combinations, 1):
            params = dict(zip(param_names, values))
            print(f"\n[{i}/{len(combinations)}] Testing: {params}")
            
            # Run backtest
            backtest_result = await self.run_backtest(
                strategy_name=strategy_name,
                symbol=symbol,
                timeframe=timeframe,
                days=days,
                use_sample_data=True,  # Use sample for speed
                **params
            )
            
            if not backtest_result:
                continue
            
            result = backtest_result['result']
            score = getattr(result, metric, 0)
            
            results.append({
                'params': params,
                'score': score,
                'total_return': result.total_return_pct,
                'sharpe_ratio': result.sharpe_ratio,
                'max_drawdown': result.max_drawdown_pct,
                'win_rate': result.win_rate,
                'total_trades': result.total_trades
            })
            
            if score > best_score:
                best_score = score
                best_params = params
                print(f"   🎯 NEW BEST: {metric}={score:.2f}")
        
        # Summary
        print(f"\n{'='*80}")
        print(f"OPTIMIZATION COMPLETE")
        print(f"{'='*80}")
        print(f"Best {metric}: {best_score:.2f}")
        print(f"Best parameters: {best_params}")
        
        return {
            'best_params': best_params,
            'best_score': best_score,
            'all_results': results
        }
    
    def _get_strategy_function(self, strategy_name: str, **params):
        """Get strategy function based on name."""
        
        # Available function-based strategies
        function_strategies = ['ma_crossover', 'rsi_mean_reversion', 'ema_trend']
        
        # Available class-based strategies (for replay mode)
        class_strategies = ['simple_candle', 'overnight_range', 'mean_reversion', 
                           'trend_following', 'trend_scalping', 'simple_momentum']
        
        all_strategies = function_strategies + class_strategies
        
        # Validate strategy name
        if strategy_name not in all_strategies:
            print(f"\n❌ ERROR: Unknown strategy '{strategy_name}'")
            print(f"\n📋 Available function-based strategies:")
            for s in function_strategies:
                print(f"   - {s}")
            print(f"\n📋 Available class-based strategies (use --replay flag):")
            for s in class_strategies:
                print(f"   - {s}")
            print(f"\nExample: python core/backtest_executor.py --strategy=simple_candle --symbol=MNQ --start=2025-12-01 --end=2025-12-07 --replay")
            raise ValueError(f"Unknown strategy: {strategy_name}. Available: {', '.join(all_strategies)}")
        
        # Return None for class-based strategies (handled separately in replay mode)
        if strategy_name in class_strategies:
            return None  # Signal to use replay mode
        
        # Filter params for each strategy (only pass relevant parameters)
        if strategy_name == "ma_crossover":
            ma_params = {k: v for k, v in params.items() if k in ['fast_period', 'slow_period']}
            return self._ma_crossover_strategy(**ma_params)
        elif strategy_name == "rsi_mean_reversion":
            rsi_params = {k: v for k, v in params.items() if k in ['rsi_oversold', 'rsi_overbought']}
            return self._rsi_strategy(**rsi_params)
        elif strategy_name == "ema_trend":
            ema_params = {k: v for k, v in params.items() if k in ['ema_short', 'ema_long']}
            return self._ema_trend_strategy(**ema_params)
    
    def _ma_crossover_strategy(self, fast_period: int = 10, slow_period: int = 50):
        """Moving average crossover strategy."""
        async def strategy(bar_index, data, engine):
            if len(data) < slow_period + 1:
                return None
            
            fast_ma = data['close'].rolling(fast_period).mean().iloc[-1]
            slow_ma = data['close'].rolling(slow_period).mean().iloc[-1]
            
            fast_prev = data['close'].iloc[:-1].rolling(fast_period).mean().iloc[-1]
            slow_prev = data['close'].iloc[:-1].rolling(slow_period).mean().iloc[-1]
            
            # Bullish cross
            if fast_prev <= slow_prev and fast_ma > slow_ma:
                return {'action': 'BUY', 'quantity': 1}
            
            # Bearish cross
            if fast_prev >= slow_prev and fast_ma < slow_ma:
                return {'action': 'SELL', 'quantity': 1}
            
            return None
        
        return strategy
    
    def _rsi_strategy(self, rsi_oversold: int = 30, rsi_overbought: int = 70):
        """RSI mean reversion strategy."""
        async def strategy(bar_index, data, engine):
            if 'rsi_14' not in data.columns or len(data) < 15:
                return None
            
            rsi = data['rsi_14'].iloc[-1]
            
            if rsi < rsi_oversold:
                return {'action': 'BUY', 'quantity': 1}
            elif rsi > rsi_overbought:
                return {'action': 'SELL', 'quantity': 1}
            
            return None
        
        return strategy
    
    def _ema_trend_strategy(self, ema_short: int = 89, ema_long: int = 233):
        """EMA trend following strategy."""
        async def strategy(bar_index, data, engine):
            if f'ema_{ema_short}' not in data.columns or f'ema_{ema_long}' not in data.columns:
                return None
            
            if len(data) < max(ema_short, ema_long) + 1:
                return None
            
            ema_s = data[f'ema_{ema_short}'].iloc[-1]
            ema_l = data[f'ema_{ema_long}'].iloc[-1]
            
            ema_s_prev = data[f'ema_{ema_short}'].iloc[-2]
            ema_l_prev = data[f'ema_{ema_long}'].iloc[-2]
            
            # Bullish cross
            if ema_s_prev <= ema_l_prev and ema_s > ema_l:
                return {'action': 'BUY', 'quantity': 1}
            
            # Bearish cross
            if ema_s_prev >= ema_l_prev and ema_s < ema_l:
                return {'action': 'SELL', 'quantity': 1}
            
            return None
        
        return strategy
    
    def _get_point_value(self, symbol: str) -> float:
        """Get point value for symbol."""
        point_values = {
            'MNQ': 2.0,
            'NQ': 20.0,
            'MES': 5.0,
            'ES': 50.0,
            'MYM': 0.5,
            'YM': 5.0,
            'M2K': 5.0,
            'RTY': 50.0,
            'MGC': 10.0,
            'GC': 100.0
        }
        return point_values.get(symbol.upper(), 2.0)
    
    def _get_tick_size(self, symbol: str) -> float:
        """Get tick size for symbol."""
        tick_sizes = {
            'MNQ': 0.25,
            'NQ': 0.25,
            'MES': 0.25,
            'ES': 0.25,
            'MYM': 1.0,
            'YM': 1.0,
            'M2K': 0.10,
            'RTY': 0.10,
            'MGC': 0.10,
            'GC': 0.10
        }
        return tick_sizes.get(symbol.upper(), 0.25)
    
    async def _run_strategy_replay(
        self,
        strategy_name: str,
        symbol: str,
        bars: List[Dict],
        initial_capital: float = 50000.0,
        slippage_ticks: float = 0.5,
        **strategy_params
    ) -> Dict[str, Any]:
        """
        Run backtest using actual strategy class (replay mode).
        
        Args:
            strategy_name: Strategy class name
            symbol: Trading symbol
            bars: List of historical bar dicts
            initial_capital: Starting capital
            slippage_ticks: Slippage in ticks
            **strategy_params: Strategy parameters
            
        Returns:
            Dict with backtest results
        """
        print(f"\n🔄 Running strategy replay mode: {strategy_name}")
        print(f"   This uses the actual strategy class code (same as live trading)")
        
        # Import strategy class
        strategy_class = self._get_strategy_class(strategy_name)
        if not strategy_class:
            return None
        
        # Create trading bot instance for strategy
        # Use broker adapter's trading bot if available, otherwise create mock
        trading_bot = None
        if hasattr(self.loader, 'broker_adapter') and self.loader.broker_adapter:
            # Try to get trading bot from broker adapter if it has one
            if hasattr(self.loader.broker_adapter, '_trading_bot') and self.loader.broker_adapter._trading_bot:
                trading_bot = self.loader.broker_adapter._trading_bot
            elif self.trading_bot:
                trading_bot = self.trading_bot
        
        if not trading_bot:
            # Create mock trading bot with broker adapter for historical data
            trading_bot = self._create_mock_trading_bot(bars, broker_adapter=self.loader.broker_adapter)
        
        # Create strategy instance
        strategy = strategy_class(trading_bot=trading_bot)
        
        # Apply strategy params if any
        if strategy_params:
            for key, value in strategy_params.items():
                if hasattr(strategy, key):
                    setattr(strategy, key, value)
        
        # Create replay engine
        replay_engine = StrategyReplayEngine(
            strategy_instance=strategy,
            trading_bot=trading_bot,
            initial_capital=initial_capital,
            commission_per_contract=2.50,
            slippage_ticks=slippage_ticks,
            point_value=self._get_point_value(symbol)
        )
        
        # Run replay
        result = await replay_engine.replay(
            symbol=symbol,
            bars=bars,
            tick_size=self._get_tick_size(symbol)
        )
        
        # Print results
        print("\n" + PerformanceMetrics.generate_report(result))
        
        # Cache results
        cache_key = f"{strategy_name}_{symbol}_replay_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        self.results_cache[cache_key] = result
        
        return {
            'result': result,
            'cache_key': cache_key,
            'data': bars
        }
    
    def _get_strategy_class(self, strategy_name: str):
        """Get strategy class by name."""
        try:
            if strategy_name == 'simple_candle':
                from strategies.simple_candle_strategy import SimpleCandleStrategy
                return SimpleCandleStrategy
            elif strategy_name == 'overnight_range':
                from strategies.overnight_range_strategy import OvernightRangeStrategy
                return OvernightRangeStrategy
            elif strategy_name == 'mean_reversion':
                from strategies.mean_reversion_strategy import MeanReversionStrategy
                return MeanReversionStrategy
            elif strategy_name == 'trend_following':
                from strategies.trend_following_strategy import TrendFollowingStrategy
                return TrendFollowingStrategy
            elif strategy_name == 'trend_scalping':
                from strategies.trend_scalping_strategy import TrendScalpingStrategy
                return TrendScalpingStrategy
            elif strategy_name == 'simple_momentum':
                from strategies.simple_momentum_strategy import SimpleMomentumStrategy
                return SimpleMomentumStrategy
            else:
                return None
        except ImportError as e:
            logger.error(f"Failed to import strategy {strategy_name}: {e}")
            return None
    
    def _create_mock_trading_bot(self, bars: List[Dict], broker_adapter=None):
        """Create a minimal trading bot mock for strategy replay."""
        class MockTradingBot:
            def __init__(self, bars, broker_adapter=None):
                self.bars = bars
                self.selected_account = {'id': 'backtest_account'}
                self.broker_adapter = broker_adapter
            
            async def get_historical_data(self, symbol, timeframe=None, limit=None, **kwargs):
                """Return bars for strategy analysis."""
                # Return bars up to current point (handled by replay engine)
                # The replay engine updates self.bars as it progresses
                return self.bars[-limit:] if limit else self.bars
            
            async def get_market_quote(self, symbol):
                """Return mock quote from latest bar."""
                if self.bars:
                    last_bar = self.bars[-1]
                    close_price = float(last_bar.get('close', last_bar.get('c', 0)))
                    return {
                        'bid': close_price,
                        'ask': close_price,
                        'last': close_price
                    }
                return {'bid': 0, 'ask': 0, 'last': 0}
            
            async def get_open_positions(self, account_id=None):
                """Return empty positions (managed by replay engine)."""
                return []
            
            async def calculate_atr(self, symbol, period=None):
                """Calculate ATR from bars if available."""
                if self.bars and len(self.bars) >= (period or 14):
                    highs = [float(b.get('high', b.get('h', 0))) for b in self.bars[-(period or 14):]]
                    lows = [float(b.get('low', b.get('l', 0))) for b in self.bars[-(period or 14):]]
                    closes = [float(b.get('close', b.get('c', 0))) for b in self.bars[-(period or 14):]]
                    # Simple ATR calculation
                    trs = []
                    for i in range(1, len(highs)):
                        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
                        trs.append(tr)
                    return sum(trs) / len(trs) if trs else 3.0
                return 3.0  # Default ATR
            
            async def calculate_ema(self, symbol, period):
                """Calculate EMA from bars if available."""
                if self.bars and len(self.bars) >= period:
                    closes = [float(b.get('close', b.get('c', 0))) for b in self.bars[-period:]]
                    # Simple EMA: weighted average (more weight on recent)
                    weights = [i+1 for i in range(len(closes))]
                    weighted_sum = sum(c * w for c, w in zip(closes, weights))
                    weight_sum = sum(weights)
                    return weighted_sum / weight_sum if weight_sum > 0 else None
                return None
        
        return MockTradingBot(bars, broker_adapter=broker_adapter)


async def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description='Backtest trading strategies')
    
    # Required arguments
    parser.add_argument('--strategy', type=str, required=True,
                       help='Strategy to backtest (ma_crossover, rsi_mean_reversion, ema_trend)')
    parser.add_argument('--symbol', type=str, required=True,
                       help='Trading symbol (MNQ, MES, etc.)')
    
    # Data arguments
    parser.add_argument('--timeframe', type=str, default='1m',
                       help='Bar interval (30s, 1m, 2m, 5m, 15m, 1h, 1d)')
    parser.add_argument('--days', type=int,
                       help='Number of days to backtest')
    parser.add_argument('--start', type=str,
                       help='Start date (YYYY-MM-DD)')
    parser.add_argument('--end', type=str,
                       help='End date (YYYY-MM-DD)')
    parser.add_argument('--sample', action='store_true',
                       help='Use generated sample data')
    parser.add_argument('--csv', type=str,
                       help='Path to CSV file with historical data (exported from history command)')
    parser.add_argument('--replay', action='store_true',
                       help='Use replay mode (for class-based strategies like simple_candle)')
    
    # Backtest parameters
    parser.add_argument('--capital', type=float, default=50000.0,
                       help='Initial capital (default: 50000)')
    parser.add_argument('--slippage-ticks', type=float, default=0.5,
                       help='Slippage in ticks (default: 0.5)')
    
    # Monte Carlo
    parser.add_argument('--monte-carlo', type=int,
                       help='Run Monte Carlo simulation with N iterations')
    
    # Optimization
    parser.add_argument('--optimize', action='store_true',
                       help='Run parameter optimization')
    parser.add_argument('--param', type=str, action='append',
                       help='Parameter range for optimization (param=min:max:step)')
    
    # Strategy-specific parameters
    parser.add_argument('--fast-period', type=int, default=10,
                       help='Fast MA period (for MA crossover)')
    parser.add_argument('--slow-period', type=int, default=50,
                       help='Slow MA period (for MA crossover)')
    parser.add_argument('--rsi-oversold', type=int, default=30,
                       help='RSI oversold level')
    parser.add_argument('--rsi-overbought', type=int, default=70,
                       help='RSI overbought level')
    parser.add_argument('--ema-short', type=int, default=89,
                       help='Short EMA period')
    parser.add_argument('--ema-long', type=int, default=233,
                       help='Long EMA period')
    
    args = parser.parse_args()
    
    # Parse dates
    start_date = datetime.strptime(args.start, '%Y-%m-%d') if args.start else None
    end_date = datetime.strptime(args.end, '%Y-%m-%d') if args.end else None
    
    # Create executor with broker adapter for real data
    broker_adapter = None
    if not args.sample and not args.csv:
        # Initialize broker adapter for real API data
        try:
            from core.auth import AuthManager
            from brokers.topstepx_adapter import TopStepXAdapter
            from core.rate_limiter import RateLimiter
            
            print("\n🔐 Initializing broker adapter for real historical data...")
            auth_manager = AuthManager()
            rate_limiter = RateLimiter(max_calls=60, period=60)
            
            # Authenticate
            auth_success = await auth_manager.authenticate()
            if not auth_success:
                print("⚠️  Authentication failed. Falling back to sample data.")
                print("   Tip: Use --sample flag to skip authentication, or --csv to use CSV file")
                args.sample = True  # Auto-fallback to sample data
            else:
                broker_adapter = TopStepXAdapter(
                    auth_manager=auth_manager,
                    rate_limiter=rate_limiter
                )
                print("✅ Broker adapter initialized")
        except Exception as e:
            print(f"⚠️  Could not initialize broker adapter: {e}")
            print("   Falling back to sample data. Use --sample or --csv for alternatives")
            args.sample = True  # Auto-fallback to sample data
    
    executor = BacktestExecutor(broker_adapter=broker_adapter)
    
    # Strategy parameters
    strategy_params = {
        'fast_period': args.fast_period,
        'slow_period': args.slow_period,
        'rsi_oversold': args.rsi_oversold,
        'rsi_overbought': args.rsi_overbought,
        'ema_short': args.ema_short,
        'ema_long': args.ema_long
    }
    
    if args.optimize:
        # Run optimization
        param_ranges = {}
        if args.param:
            for param_spec in args.param:
                name, range_spec = param_spec.split('=')
                min_val, max_val, step = map(int, range_spec.split(':'))
                param_ranges[name] = list(range(min_val, max_val + 1, step))
        
        if not param_ranges:
            # Default optimization ranges
            if args.strategy == 'ma_crossover':
                param_ranges = {
                    'fast_period': [5, 10, 20, 30],
                    'slow_period': [30, 50, 100, 200]
                }
        
        result = await executor.run_optimization(
            strategy_name=args.strategy,
            symbol=args.symbol,
            param_ranges=param_ranges,
            timeframe=args.timeframe,
            days=args.days or 30
        )
    else:
        # Run single backtest
        result = await executor.run_backtest(
            strategy_name=args.strategy,
            symbol=args.symbol,
            timeframe=args.timeframe,
            start_date=start_date,
            end_date=end_date,
            days=args.days,
            initial_capital=args.capital,
            use_sample_data=args.sample,
            csv_file=args.csv,
            slippage_ticks=args.slippage_ticks,
            **strategy_params
        )
        
        # Monte Carlo if requested
        if args.monte_carlo and result:
            await executor.run_monte_carlo(
                result['result'],
                num_simulations=args.monte_carlo,
                initial_capital=args.capital
            )


if __name__ == '__main__':
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n👋 Backtest interrupted by user")
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
