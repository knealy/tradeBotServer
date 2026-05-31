"""
Backtest Executor

Command-line interface for running backtests with various parameters.

Usage:
    python core/backtest_executor.py --strategy=overnight_range --symbol=MNQ --timeframe=5m --days=30
    python core/backtest_executor.py --strategy=trend_scalping --symbol=MNQ --start=2024-01-01 --end=2024-12-31
    python core/backtest_executor.py --strategy=mean_reversion --symbol=MES --days=90 --monte-carlo=1000
    python core/backtest_executor.py --list-strategies
    python core/backtest_executor.py --strategy=ma_crossover --symbol=MNQ --sample --days=14 --format=json
"""

import sys
import os
import asyncio
import argparse
import logging
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, List

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.backtest import HistoricalDataLoader, BacktestEngine, PerformanceMetrics, MonteCarloSimulator
from core.backtest.ohlcv import replay_bars_from_ohlcv_df
from core.backtest.models import OrderSide, OrderType
from core.backtest.strategy_replay import StrategyReplayEngine
from brokers.topstepx_adapter import TopStepXAdapter
from core.auth import AuthManager
from core.json_fast import dumps_str

logger = logging.getLogger(__name__)


def _strategy_replay_tf_minutes(tf: Optional[str]) -> Optional[int]:
    """Bar length in whole minutes for common replay labels (``5m``, ``15m``, ``1h``, ``2h``, ``1d``).

    Returns ``None`` for unsupported / sub-minute strings so callers fall back to raw replay bars.
    """
    if not tf:
        return None
    t = str(tf).strip().lower()
    if len(t) < 2:
        return None
    unit = t[-1]
    num_s = t[:-1]
    if not num_s.isdigit():
        return None
    n = int(num_s)
    if unit == "m":
        return n
    if unit == "h":
        return n * 60
    if unit == "d":
        return n * 24 * 60
    return None


# Must match ``_get_strategy_function`` function-based names (sample/CSV path keeps DataFrame for these).
_FUNCTION_BACKTEST_STRATEGIES = frozenset(
    {"ma_crossover", "rsi_mean_reversion", "ema_trend"}
)

# CLI: ``--format json`` suppresses decorative stdout via ``set_backtest_cli_human_output(False)``.
_cli_human_output = True
CLI_OUTPUT_FORMAT = "human"


def set_backtest_cli_human_output(enabled: bool) -> None:
    """When False, skip decorative ``print`` lines (machine-readable JSON mode)."""
    global _cli_human_output
    _cli_human_output = enabled


def _cli_print(*args, **kwargs) -> None:
    if _cli_human_output:
        print(*args, **kwargs)


def _strip_mc_bulk(mc: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not mc:
        return None
    return {k: v for k, v in mc.items() if k != "simulation_results"}


def _serialize_backtest_bundle(
    bundle: Optional[Dict[str, Any]],
    *,
    ok: bool,
    error: Optional[str] = None,
    monte_carlo: Optional[Dict[str, Any]] = None,
    include_trades: bool = False,
) -> Dict[str, Any]:
    if not ok:
        return {"ok": False, "error": error or "unknown"}
    assert bundle is not None
    r = bundle.get("result")
    if r is not None and hasattr(r, "to_dict"):
        summary = r.to_dict(include_trades=include_trades)
    else:
        summary = {}
    out: Dict[str, Any] = {
        "ok": True,
        "cache_key": bundle.get("cache_key"),
        "result": summary,
    }
    mc = _strip_mc_bulk(monte_carlo)
    if mc is not None:
        out["monte_carlo"] = mc
    return out


def print_registered_strategies() -> None:
    """Stdout listing for ``--list-strategies`` (always prints; ignores JSON quiet mode)."""
    function_strategies = ["ma_crossover", "rsi_mean_reversion", "ema_trend"]
    class_strategies = [
        "overnight_range",
        "overnight_reversion",
        "mean_reversion",
        "trend_following",
        "trend_scalping",
        "simple_momentum",
        "simple_rth",
        "vwap_zscore_reversion",
        "body_reversion",
        "morning_range_reversion",
        "hourly_anchor_retrace",
        "ema_stack_trend_15m",
        "rsi_switch_15m",
    ]
    testing_only = ("simple_candle",)
    for s in function_strategies:
        print(f"  - {s}")
    print("\nClass-based / replay (use --replay):")
    for s in class_strategies:
        print(f"  - {s}")
    print("\nTesting-only replay:")
    for s in testing_only:
        print(f"  - {s}")
    print(
        "\nResearch grid (sample in-sample + OOS + MC): python -m core.research.runner --help"
    )


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
        replay_csv_1m: Optional[str] = None,
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
            replay_csv_1m: Optional 1m OHLCV CSV (with --csv) for intrabar order fills in class replay
            **strategy_params: Additional strategy parameters
            
        Returns:
            Dict with backtest results
        """
        import pandas as pd

        replay_bars_1m: Optional[List[Dict]] = None
        if replay_csv_1m and not csv_file:
            _cli_print("⚠️  --csv-1m requires --csv; ignoring --csv-1m")
            replay_csv_1m = None

        _cli_print(f"\n{'='*80}")
        _cli_print(f"BACKTESTING: {strategy_name.upper()}")
        _cli_print(f"{'='*80}")
        _cli_print(f"Symbol: {symbol}")
        _cli_print(f"Timeframe: {timeframe}")
        _cli_print(f"Initial Capital: ${initial_capital:,.2f}")
        
        # Load historical data
        if csv_file:
            # Load from CSV file (from history command export)
            _cli_print(f"\n📊 Loading data from CSV: {csv_file}")
            data = self.loader.load_from_csv(
                filepath=csv_file,
                symbol=symbol
            )
            if isinstance(data, pd.DataFrame):
                _cli_print(f"✅ Loaded {len(data)} bars from CSV")
                if start_date is not None or end_date is not None:
                    n0 = len(data)
                    if start_date is not None:
                        data = data[data.index >= pd.Timestamp(start_date)]
                    if end_date is not None:
                        data = data[
                            data.index < pd.Timestamp(end_date) + pd.Timedelta(days=1)
                        ]
                    _cli_print(
                        f"   Date filter [{start_date} → {end_date}]: {n0} → {len(data)} bars"
                    )
                if replay_csv_1m and strategy_name in _FUNCTION_BACKTEST_STRATEGIES:
                    _cli_print("⚠️  --csv-1m is ignored for function-based strategies")
                elif replay_csv_1m and strategy_name not in _FUNCTION_BACKTEST_STRATEGIES:
                    _cli_print(f"\n📊 Loading 1m CSV for intrabar replay fills: {replay_csv_1m}")
                    df_1m = self.loader.load_from_csv(
                        filepath=replay_csv_1m,
                        symbol=symbol,
                    )
                    if isinstance(df_1m, pd.DataFrame):
                        if start_date is not None or end_date is not None:
                            if start_date is not None:
                                df_1m = df_1m[df_1m.index >= pd.Timestamp(start_date)]
                            if end_date is not None:
                                df_1m = df_1m[
                                    df_1m.index < pd.Timestamp(end_date) + pd.Timedelta(days=1)
                                ]
                        replay_bars_1m = replay_bars_from_ohlcv_df(df_1m)
                        _cli_print(
                            f"✅ Loaded {len(replay_bars_1m)} 1m bars (same date window as primary CSV)"
                        )
                    else:
                        _cli_print("⚠️  1m CSV did not load as a DataFrame; skipping --csv-1m")
                if strategy_name not in _FUNCTION_BACKTEST_STRATEGIES:
                    data = replay_bars_from_ohlcv_df(data)
            else:
                _cli_print(f"✅ Loaded {len(data)} bars from CSV")
        elif use_sample_data or not self.loader.broker_adapter:
            _cli_print(f"\n📊 Generating sample data...")
            data_df = self.loader.get_sample_data(
                symbol=symbol,
                days=days or 30,
                timeframe=timeframe
            )
            _cli_print(f"✅ Generated {len(data_df)} bars of sample data")
            if strategy_name in _FUNCTION_BACKTEST_STRATEGIES:
                data = data_df
            else:
                # Class / replay strategies expect list[dict] bars
                data = replay_bars_from_ohlcv_df(data_df)
        else:
            # Use broker adapter to fetch real historical data
            if not self.loader.broker_adapter:
                _cli_print("❌ No broker adapter available - cannot load real historical data")
                _cli_print("   Use --sample for sample data or provide --csv file")
                return None
            
            # Calculate date range
            if days and not start_date:
                end_date = end_date or datetime.now()
                start_date = end_date - timedelta(days=days)
            
            if not start_date or not end_date:
                _cli_print("❌ Start and end dates are required for API data loading")
                _cli_print("   Use --start and --end or --days")
                return None
            
            _cli_print(f"\n📊 Loading historical data from TopStepX API...")
            _cli_print(f"   Period: {start_date.date()} to {end_date.date()}")
            _cli_print(f"   Symbol: {symbol}, Timeframe: {timeframe}")
            
            # Use broker adapter's get_historical_data (returns List[Bar])
            bars = await self.loader.broker_adapter.get_historical_data(
                symbol=symbol,
                timeframe=timeframe,
                start_time=start_date,
                end_time=end_date,
                limit=20000  # API max
            )
            
            if not bars:
                _cli_print("❌ No data returned from API")
                return None
            
            _cli_print(f"✅ Loaded {len(bars)} bars from API")
            
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
                _cli_print("❌ Data validation failed!")
                return None
            # Add indicators if needed (only for DataFrame)
            data = self.loader.add_indicators(data, indicators=['ema_89', 'ema_233', 'atr_14', 'rsi_14', 'sma_20'])
        elif isinstance(data, list):
            # For replay mode with list of dicts, basic validation
            if not data or len(data) < 2:
                _cli_print("❌ Insufficient data for backtest!")
                return None
            _cli_print(f"✅ Data validated: {len(data)} bars")
        else:
            _cli_print(f"❌ Invalid data type: {type(data)}")
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
                replay_timeframe=timeframe,
                bars_1m=replay_bars_1m,
                **strategy_params,
            )
        
        # Run traditional function-based backtest
        _cli_print(f"\n🔬 Running backtest...")
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
        _cli_print("\n" + PerformanceMetrics.generate_report(result))
        
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
                _cli_print(f"❌ No cached result found for key: {result_or_cache_key}")
                return None
        else:
            result = result_or_cache_key
        
        if not result.trades:
            _cli_print("❌ No trades to analyze!")
            return None
        
        _cli_print(f"\n🎲 Running {num_simulations} Monte Carlo simulations...")
        
        mc = MonteCarloSimulator(initial_capital=initial_capital)
        mc_results = mc.run_simulations(
            trades=result.trades,
            num_simulations=num_simulations
        )
        
        _cli_print("\n" + mc.generate_report(mc_results))
        
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
        _cli_print(f"\n{'='*80}")
        _cli_print(f"PARAMETER OPTIMIZATION: {strategy_name.upper()}")
        _cli_print(f"{'='*80}")
        _cli_print(f"Symbol: {symbol}")
        _cli_print(f"Optimizing: {metric}")
        _cli_print(f"Parameters: {param_ranges}")
        
        best_params = None
        best_score = float('-inf')
        results = []
        
        # Generate parameter combinations
        import itertools
        param_names = list(param_ranges.keys())
        param_values = list(param_ranges.values())
        combinations = list(itertools.product(*param_values))
        
        _cli_print(f"\n🔬 Testing {len(combinations)} parameter combinations...")
        
        for i, values in enumerate(combinations, 1):
            params = dict(zip(param_names, values))
            _cli_print(f"\n[{i}/{len(combinations)}] Testing: {params}")
            
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
                _cli_print(f"   🎯 NEW BEST: {metric}={score:.2f}")
        
        # Summary
        _cli_print(f"\n{'='*80}")
        _cli_print(f"OPTIMIZATION COMPLETE")
        _cli_print(f"{'='*80}")
        _cli_print(f"Best {metric}: {best_score:.2f}")
        _cli_print(f"Best parameters: {best_params}")
        
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
        class_strategies = [
            "simple_candle",
            "overnight_range",
            "overnight_reversion",
            "mean_reversion",
            "trend_following",
            "trend_scalping",
            "simple_momentum",
            "simple_rth",
            "vwap_zscore_reversion",
            "body_reversion",
            "morning_range_reversion",
            "hourly_anchor_retrace",
            "ema_stack_trend_15m",
            "rsi_switch_15m",
        ]

        all_strategies = function_strategies + class_strategies
        
        # Validate strategy name
        if strategy_name not in all_strategies:
            _cli_print(f"\n❌ ERROR: Unknown strategy '{strategy_name}'")
            _cli_print(f"\n📋 Available function-based strategies:")
            for s in function_strategies:
                _cli_print(f"   - {s}")
            _cli_print(f"\n📋 Available class-based strategies (use --replay flag):")
            for s in class_strategies:
                _cli_print(f"   - {s}")
            _cli_print(
                "\nExample: python core/backtest_executor.py --strategy=body_reversion "
                "--symbol=MNQ --start=2025-12-01 --end=2025-12-07 --replay"
            )
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
        *,
        quiet: bool = False,
        replay_timeframe: Optional[str] = None,
        bars_1m: Optional[List[Dict]] = None,
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
        if not quiet:
            _cli_print(f"\n🔄 Running strategy replay mode: {strategy_name}")
            _cli_print(f"   This uses the actual strategy class code (same as live trading)")
        
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
            trading_bot = self._create_mock_trading_bot(
                bars,
                broker_adapter=self.loader.broker_adapter,
                bars_1m=bars_1m,
                replay_timeframe=replay_timeframe,
            )
        elif bars_1m:
            setattr(trading_bot, "bars_1m", list(bars_1m))
        
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
            tick_size=self._get_tick_size(symbol),
            replay_timeframe=replay_timeframe,
            bars_1m=bars_1m,
        )
        
        # Print results
        if not quiet:
            _cli_print("\n" + PerformanceMetrics.generate_report(result))
        
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
            elif strategy_name == 'overnight_reversion':
                from strategies.overnight_reversion_strategy import OvernightReversionStrategy
                return OvernightReversionStrategy
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
            elif strategy_name == 'simple_rth':
                from strategies.simple_rth_strategy import SimpleRthStrategy
                return SimpleRthStrategy
            elif strategy_name == 'vwap_zscore_reversion':
                from strategies.vwap_zscore_reversion_strategy import (
                    VwapZscoreReversionStrategy,
                )
                return VwapZscoreReversionStrategy
            elif strategy_name == 'body_reversion':
                from strategies.body_reversion_strategy import BodyReversionStrategy
                return BodyReversionStrategy
            elif strategy_name == 'morning_range_reversion':
                from strategies.morning_range_reversion_strategy import (
                    MorningRangeReversionStrategy,
                )
                return MorningRangeReversionStrategy
            elif strategy_name == 'hourly_anchor_retrace':
                from strategies.hourly_anchor_retrace_strategy import (
                    HourlyAnchorRetraceStrategy,
                )
                return HourlyAnchorRetraceStrategy
            elif strategy_name == 'ema_stack_trend_15m':
                from strategies.ema_stack_trend_15m_strategy import EmaStackTrend15mStrategy

                return EmaStackTrend15mStrategy
            elif strategy_name == 'rsi_switch_15m':
                from strategies.rsi_switch_15m_strategy import RsiSwitch15mStrategy

                return RsiSwitch15mStrategy
            else:
                return None
        except ImportError as e:
            logger.error(f"Failed to import strategy {strategy_name}: {e}")
            return None
    
    def _create_mock_trading_bot(
        self,
        bars: List[Dict],
        broker_adapter=None,
        bars_1m: Optional[List[Dict]] = None,
        *,
        replay_timeframe: Optional[str] = None,
    ):
        """Create a minimal trading bot mock for strategy replay."""
        hist_loader = HistoricalDataLoader()

        class MockTradingBot:
            def __init__(self, bars, broker_adapter=None, bars_1m=None, replay_timeframe=None):
                self.bars = bars
                self.bars_1m = list(bars_1m) if bars_1m else []
                self.selected_account = {'id': 'backtest_account', 'name': 'BACKTEST_PRAC_ACCOUNT'}
                self.broker_adapter = broker_adapter
                # Flag read by OvernightRangeStrategy to mimic live cadence (one open window / session).
                self._is_strategy_replay = True
                # Replay CSV cadence (e.g. ``5m``). Used to resample when strategies request other TFs.
                self.replay_timeframe = (replay_timeframe or "5m").strip().lower()
                self._resampled_cache: Dict[str, List[Dict]] = {}
                self._hist_loader = hist_loader

            def _bars_to_ohlcv_dataframe(self):
                """Build a sorted OHLCV DataFrame from ``self.bars`` (replay stream)."""
                import pandas as pd

                rows: List[Dict[str, Any]] = []
                for b in self.bars:
                    ts = self._parse_bar_timestamp(b)
                    if ts is None:
                        continue
                    rows.append(
                        {
                            "timestamp": ts,
                            "open": float(b.get("open", b.get("o", 0))),
                            "high": float(b.get("high", b.get("h", 0))),
                            "low": float(b.get("low", b.get("l", 0))),
                            "close": float(b.get("close", b.get("c", 0))),
                            "volume": float(b.get("volume", b.get("v", 0)) or 0),
                        }
                    )
                if len(rows) < 2:
                    return None
                df = pd.DataFrame(rows)
                df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
                df.set_index("timestamp", inplace=True)
                df.sort_index(inplace=True)
                return df

            def _resample_native_to(self, target_tf: str) -> List[Dict]:
                if target_tf in self._resampled_cache:
                    return self._resampled_cache[target_tf]
                try:
                    df = self._bars_to_ohlcv_dataframe()
                    if df is None or len(df) < 2:
                        out = list(self.bars)
                    else:
                        rs = self._hist_loader.resample(df, target_tf)
                        out = replay_bars_from_ohlcv_df(rs)
                    self._resampled_cache[target_tf] = out
                    return out
                except Exception as exc:
                    logger.warning(
                        "Mock bot: resample replay %s → %s failed (%s); using native replay bars",
                        self.replay_timeframe,
                        target_tf,
                        exc,
                    )
                    out = list(self.bars)
                    self._resampled_cache[target_tf] = out
                    return out

            def _select_historical_source(self, tf: str) -> List[Dict]:
                """Bars for ``timeframe``: 1m slice, native replay list, or OHLCV-resampled coarser TFs."""
                if tf == "1m" and self.bars_1m:
                    return self.bars_1m
                native = self.replay_timeframe
                if tf == native:
                    return self.bars
                req_m = _strategy_replay_tf_minutes(tf)
                nat_m = _strategy_replay_tf_minutes(native)
                if req_m is None or nat_m is None:
                    return self.bars
                if req_m == nat_m:
                    return self.bars
                if req_m < nat_m:
                    logger.debug(
                        "Mock bot: requested %s is finer than replay %s — returning native replay bars",
                        tf,
                        native,
                    )
                    return self.bars
                return self._resample_native_to(tf)

            def _parse_bar_timestamp(self, bar: Dict) -> Optional[datetime]:
                """Parse a bar timestamp into a timezone-aware UTC datetime."""
                ts = bar.get('timestamp') or bar.get('time') or bar.get('t')
                if not ts:
                    return None
                if isinstance(ts, datetime):
                    dt = ts
                elif hasattr(ts, 'to_pydatetime'):
                    # pandas Timestamp-like
                    try:
                        dt = ts.to_pydatetime()
                    except Exception:
                        dt = datetime.fromtimestamp(ts.timestamp(), tz=timezone.utc)
                elif isinstance(ts, str):
                    dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
                elif isinstance(ts, (int, float)):
                    # epoch seconds or ms
                    if ts > 1e12:
                        dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
                    else:
                        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
                else:
                    return None
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.astimezone(timezone.utc)
            
            async def get_open_orders(self, account_id=None):
                return []

            async def get_historical_data(self, symbol, timeframe=None, limit=None, start_time=None, end_time=None, **kwargs):
                """Return bars for strategy analysis, filtered by time range if provided."""
                tf = (timeframe or "1m").strip().lower()
                source = self._select_historical_source(tf)
                # Start with all bars
                filtered_bars = source
                
                # Filter by time range if provided
                if start_time or end_time:
                    filtered_bars = []
                    
                    # Get time range of available bars for debugging
                    if source:
                        first_bar_time = self._parse_bar_timestamp(source[0])
                        last_bar_time = self._parse_bar_timestamp(source[-1])
                        logger.debug(f"Mock bot: Available bars range: {first_bar_time} to {last_bar_time}")
                        logger.debug(f"Mock bot: Requested range: {start_time} to {end_time}")
                    
                    for bar in source:
                        bar_time = self._parse_bar_timestamp(bar)
                        if not bar_time:
                            continue
                        
                        # Normalize start_time and end_time to UTC for comparison
                        if start_time:
                            if start_time.tzinfo is None:
                                start_time_utc = start_time.replace(tzinfo=timezone.utc)
                            else:
                                start_time_utc = start_time.astimezone(timezone.utc)
                            if bar_time < start_time_utc:
                                continue
                        
                        if end_time:
                            if end_time.tzinfo is None:
                                end_time_utc = end_time.replace(tzinfo=timezone.utc)
                            else:
                                end_time_utc = end_time.astimezone(timezone.utc)
                            if bar_time > end_time_utc:
                                continue
                        
                        filtered_bars.append(bar)
                    
                    logger.debug(f"Mock bot: Filtered {len(filtered_bars)} bars from {len(source)} source bars")
                    
                    # Sort by timestamp (oldest first)
                    filtered_bars.sort(key=lambda b: self._parse_bar_timestamp(b) or datetime.min.replace(tzinfo=timezone.utc))
                else:
                    # No time filter - just sort by timestamp
                    filtered_bars.sort(key=lambda b: self._parse_bar_timestamp(b) or datetime.min.replace(tzinfo=timezone.utc))
                
                # Apply limit (take last N bars if limit specified, otherwise all)
                if limit:
                    return filtered_bars[-limit:]
                return filtered_bars
            
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
            
            async def get_available_contracts(self):
                """Return mock contract info for backtest."""
                # Return default contract info for common symbols
                return [
                    {'name': 'MNQ', 'tickSize': 0.25},
                    {'name': 'MES', 'tickSize': 0.25},
                    {'name': 'ES', 'tickSize': 0.25},
                    {'name': 'NQ', 'tickSize': 0.25},
                ]
            
            async def list_accounts(self):
                """Return mock account list for backtest."""
                # Return a mock PRAC account for backtest
                return [
                    {'id': 'backtest_account', 'name': 'BACKTEST_PRAC_ACCOUNT', 'type': 'PRAC'}
                ]
            
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
            ) -> Dict[str, Any]:
                """
                Mock OCO bracket order placement for backtest.
                
                In backtest mode, orders are simulated by the replay engine.
                This method returns a success response so the strategy can continue.
                The actual order simulation is handled by the StrategyReplayEngine.
                """
                # Generate a mock order ID
                import uuid
                mock_order_id = str(uuid.uuid4())[:8]
                
                logger.debug(f"Mock order placement: {side} {quantity} {symbol} @ {entry_price:.2f} (orderId: {mock_order_id})")
                
                return {
                    'success': True,
                    'orderId': mock_order_id,
                    'message': 'Order simulated in backtest (replay engine will handle execution)',
                    'method': 'backtest_mock'
                }

            async def place_oco_bracket_with_stop_entry_partial_tp(
                self,
                symbol: str,
                side: str,
                quantity: int,
                entry_price: float,
                stop_loss_price: float,
                take_profit_full_price: float,
                account_id: Optional[str] = None,
                *,
                scalp_r_multiple: float = 1.0,
                enable_breakeven: bool = False,
                strategy_name: Optional[str] = None,
            ) -> Dict[str, Any]:
                """Replay engine replaces this; stub satisfies ``hasattr`` before intercept."""
                import uuid

                _ = (account_id, scalp_r_multiple, enable_breakeven, strategy_name)
                mock_order_id = str(uuid.uuid4())[:8]
                logger.debug(
                    "Mock partial-TP bracket: %s %s %s @ %s (orderId=%s)",
                    side,
                    quantity,
                    symbol,
                    entry_price,
                    mock_order_id,
                )
                return {
                    "success": True,
                    "orderId": mock_order_id,
                    "message": "Partial-TP order simulated in backtest (replay intercept)",
                    "method": "backtest_mock_partial_tp",
                }

            def register_generic_breakeven_watch(
                self,
                order_id: str,
                *,
                symbol: str,
                side: str,
                entry_price: float,
                profit_threshold: float,
                strategy_name: str = "",
            ) -> None:
                """Stub so ``hasattr(bot, 'register_generic_breakeven_watch')`` is True
                in replay mode.

                ``BaseStrategy.place_bracket_order`` checks the attribute before calling
                — without this stub the call is silently skipped and BONGO §1B watches
                are never armed during backtests. ``StrategyReplayEngine._intercept_trading_bot_methods``
                replaces this stub with the simulated tracking path before any strategy
                runs, so this body never actually executes during a real replay. It exists
                solely as a sentinel for the ``hasattr`` gate.
                """
                _ = (order_id, symbol, side, entry_price, profit_threshold, strategy_name)
                # No-op: the live monitor would start a polling task here, but the replay
                # engine handles the watch deterministically via _evaluate_breakeven_watches.
            
            async def place_stop_order(
                self,
                symbol: str,
                side: str,
                quantity: int,
                stop_price: float,
                account_id: Optional[str] = None,
                reduce_only: bool = False,
                strategy_name: Optional[str] = None
            ) -> Dict[str, Any]:
                """Mock stop order placement for backtest."""
                import uuid
                mock_order_id = str(uuid.uuid4())[:8]
                logger.debug(f"Mock stop order: {side} {quantity} {symbol} @ {stop_price:.2f} (orderId: {mock_order_id})")
                return {
                    'success': True,
                    'orderId': mock_order_id,
                    'message': 'Stop order simulated in backtest',
                    'method': 'backtest_mock'
                }
            
            async def place_limit_order(
                self,
                symbol: str,
                side: str,
                quantity: int,
                limit_price: float,
                account_id: Optional[str] = None,
                reduce_only: bool = False,
                strategy_name: Optional[str] = None
            ) -> Dict[str, Any]:
                """Mock limit order placement for backtest."""
                import uuid
                mock_order_id = str(uuid.uuid4())[:8]
                logger.debug(f"Mock limit order: {side} {quantity} {symbol} @ {limit_price:.2f} (orderId: {mock_order_id})")
                return {
                    'success': True,
                    'orderId': mock_order_id,
                    'message': 'Limit order simulated in backtest',
                    'method': 'backtest_mock'
                }
        
        return MockTradingBot(
            bars,
            broker_adapter=broker_adapter,
            bars_1m=bars_1m,
            replay_timeframe=replay_timeframe,
        )


async def main():
    """Main entry point."""
    global CLI_OUTPUT_FORMAT

    parser = argparse.ArgumentParser(
        description="Backtest trading strategies",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python core/backtest_executor.py --list-strategies
  python core/backtest_executor.py --strategy=ma_crossover --symbol=MNQ --sample --days=14
  python core/backtest_executor.py --strategy=ma_crossover --symbol=MNQ --sample --days=14 --format=json
  python core/backtest_executor.py --strategy=overnight_range --symbol=MNQ --replay --start=2025-01-01 --end=2025-01-14 --sample
Research (grid + OOS + MC gate): python -m core.research.runner --help
  python core/backtest_executor.py --strategy=overnight_range --symbol=MNQ --csv=data.csv \\
    --start=2026-02-01 --end=2026-02-28 --format=json --include-trades
        """.strip(),
    )

    parser.add_argument(
        "--list-strategies",
        action="store_true",
        help="Print function-based and replay-capable strategy names, then exit",
    )
    parser.add_argument(
        "--format",
        choices=("human", "json"),
        default="human",
        help="human: banners and tables to stdout; json: one JSON object at end (no decorative prints)",
    )

    # Required unless --list-strategies
    parser.add_argument(
        "--strategy",
        type=str,
        default=None,
        help="Strategy to backtest (see --list-strategies)",
    )
    parser.add_argument(
        "--symbol",
        type=str,
        default=None,
        help="Trading symbol (MNQ, MES, etc.)",
    )
    
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
    parser.add_argument(
        '--csv-1m',
        dest='replay_csv_1m',
        type=str,
        default=None,
        help='Optional 1m OHLCV CSV (use with --csv): intrabar order fills during class-strategy replay',
    )
    parser.add_argument('--replay', action='store_true',
                       help='Use replay mode (for class-based strategies; see --list-strategies)')
    parser.add_argument(
        '--include-trades',
        action='store_true',
        help='With --format=json, include each completed trade (entry/exit timestamps, prices, pnl)',
    )
    
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

    if args.list_strategies:
        print_registered_strategies()
        return

    if not args.strategy or not args.symbol:
        parser.error("--strategy and --symbol are required (unless using --list-strategies)")

    CLI_OUTPUT_FORMAT = args.format
    set_backtest_cli_human_output(args.format == "human")

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
            
            _cli_print("\n🔐 Initializing broker adapter for real historical data...")
            auth_manager = AuthManager()
            rate_limiter = RateLimiter(max_calls=60, period=60)
            
            # Authenticate
            auth_success = await auth_manager.authenticate()
            if not auth_success:
                _cli_print("⚠️  Authentication failed. Falling back to sample data.")
                _cli_print("   Tip: Use --sample flag to skip authentication, or --csv to use CSV file")
                args.sample = True  # Auto-fallback to sample data
            else:
                broker_adapter = TopStepXAdapter(
                    auth_manager=auth_manager,
                    rate_limiter=rate_limiter
                )
                _cli_print("✅ Broker adapter initialized")
        except Exception as e:
            _cli_print(f"⚠️  Could not initialize broker adapter: {e}")
            _cli_print("   Falling back to sample data. Use --sample or --csv for alternatives")
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
        if args.format == "json":
            print(dumps_str({"ok": True, "optimization": result}))
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
            replay_csv_1m=args.replay_csv_1m,
            slippage_ticks=args.slippage_ticks,
            **strategy_params
        )

        mc_out: Optional[Dict[str, Any]] = None
        if args.monte_carlo and result:
            mc_out = await executor.run_monte_carlo(
                result["result"],
                num_simulations=args.monte_carlo,
                initial_capital=args.capital,
            )

        if args.format == "json":
            if result:
                print(
                    dumps_str(
                        _serialize_backtest_bundle(
                            result,
                            ok=True,
                            monte_carlo=mc_out,
                            include_trades=args.include_trades,
                        )
                    )
                )
            else:
                print(dumps_str({"ok": False, "error": "run_backtest returned no result"}))


if __name__ == "__main__":
    from core.logging_setup import configure_logging

    configure_logging()

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\nBacktest interrupted by user", file=sys.stderr)
    except Exception as e:
        if globals().get("CLI_OUTPUT_FORMAT") == "json":
            print(dumps_str({"ok": False, "error": str(e)}))
        else:
            _cli_print(f"\n❌ Error: {e}")
            import traceback

            traceback.print_exc()
