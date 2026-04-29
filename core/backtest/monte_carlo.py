"""
Monte Carlo Simulator

Runs Monte Carlo simulations on trade sequences to analyze strategy robustness.
"""

from __future__ import annotations

from typing import List, Dict, Optional, Any
import logging
import random
from .models import BacktestTrade

logger = logging.getLogger(__name__)


class MonteCarloSimulator:
    """
    Monte Carlo simulator for trading strategy analysis.
    
    Randomizes trade order to understand:
    - Distribution of possible outcomes
    - Confidence intervals for returns
    - Risk of ruin
    - Drawdown probability
    - Strategy robustness
    """
    
    def __init__(self, initial_capital: float = 50000.0):
        """
        Initialize Monte Carlo simulator.
        
        Args:
            initial_capital: Starting capital for simulations
        """
        self.initial_capital = initial_capital
    
    def run_simulations(
        self,
        trades: List[BacktestTrade],
        num_simulations: int = 1000,
        seed: Optional[int] = None,
        *,
        simulation_mode: str = "shuffle",
    ) -> Dict[str, Any]:
        """
        Run Monte Carlo simulations on trade sequence.
        
        ``shuffle``: permute trade order (legacy). ``bootstrap``: iid resample trade P&Ls
        with replacement (parametric bootstrap on the empirical P&L distribution).
        
        Args:
            trades: List of historical trades
            num_simulations: Number of simulations to run
            seed: Random seed for reproducibility
            simulation_mode: ``shuffle`` or ``bootstrap``
            
        Returns:
            Dict with simulation results
        """
        if not trades:
            logger.warning("No trades provided for Monte Carlo simulation")
            return {}

        import numpy as np
        import pandas as pd
        from .metrics import PerformanceMetrics

        logger.info(f"🎲 Running {num_simulations} Monte Carlo simulations...")
        logger.info(f"   Base trades: {len(trades)}")
        logger.info(f"   Initial capital: ${self.initial_capital:,.2f}")
        
        if seed is not None:
            random.seed(seed)
            np.random.seed(seed)
        
        # Extract trade P&Ls
        trade_pnls = [t.pnl for t in trades]
        
        # Run simulations
        simulation_results = []
        final_capitals = []
        max_drawdowns = []
        sharpe_ratios = []
        
        mode = (simulation_mode or "shuffle").strip().lower()
        if mode not in ("shuffle", "bootstrap"):
            mode = "shuffle"

        for sim in range(num_simulations):
            if mode == "bootstrap":
                randomized_pnls = [random.choice(trade_pnls) for _ in range(len(trade_pnls))]
            else:
                randomized_pnls = random.sample(trade_pnls, len(trade_pnls))
            
            # Simulate equity curve
            capital = self.initial_capital
            equity_curve = [capital]
            
            for pnl in randomized_pnls:
                capital += pnl
                equity_curve.append(capital)
            
            final_capitals.append(capital)
            
            # Calculate max drawdown for this simulation
            _, max_dd_pct, _, _ = PerformanceMetrics.calculate_max_drawdown(equity_curve)
            max_drawdowns.append(max_dd_pct)
            
            # Calculate Sharpe for this simulation
            returns = pd.Series(randomized_pnls) / self.initial_capital
            sharpe = PerformanceMetrics.calculate_sharpe_ratio(returns)
            sharpe_ratios.append(sharpe)
            
            simulation_results.append({
                'final_capital': capital,
                'total_return': ((capital - self.initial_capital) / self.initial_capital) * 100,
                'max_drawdown_pct': max_dd_pct,
                'sharpe_ratio': sharpe
            })
        
        # Calculate statistics across all simulations
        final_capitals_array = np.array(final_capitals)
        returns_array = ((final_capitals_array - self.initial_capital) / self.initial_capital) * 100
        
        results = {
            'num_simulations': num_simulations,
            'num_trades': len(trades),
            'initial_capital': self.initial_capital,
            
            # Return distribution
            'mean_final_capital': np.mean(final_capitals),
            'median_final_capital': np.median(final_capitals),
            'std_final_capital': np.std(final_capitals),
            'min_final_capital': np.min(final_capitals),
            'max_final_capital': np.max(final_capitals),
            
            # Return percentiles
            'return_percentile_5': np.percentile(returns_array, 5),
            'return_percentile_25': np.percentile(returns_array, 25),
            'return_percentile_50': np.percentile(returns_array, 50),
            'return_percentile_75': np.percentile(returns_array, 75),
            'return_percentile_95': np.percentile(returns_array, 95),
            
            # Confidence intervals
            'return_95pct_ci': (np.percentile(returns_array, 2.5), np.percentile(returns_array, 97.5)),
            'return_99pct_ci': (np.percentile(returns_array, 0.5), np.percentile(returns_array, 99.5)),
            
            # Drawdown statistics
            'mean_max_drawdown': np.mean(max_drawdowns),
            'median_max_drawdown': np.median(max_drawdowns),
            'worst_drawdown': np.max(max_drawdowns),
            'best_drawdown': np.min(max_drawdowns),
            
            # Sharpe statistics
            'mean_sharpe': np.mean(sharpe_ratios),
            'median_sharpe': np.median(sharpe_ratios),
            
            # Risk metrics
            'probability_of_profit': sum(1 for c in final_capitals if c > self.initial_capital) / num_simulations,
            'probability_of_loss': sum(1 for c in final_capitals if c < self.initial_capital) / num_simulations,
            'probability_of_10pct_dd': sum(1 for dd in max_drawdowns if dd > 10) / num_simulations,
            'probability_of_20pct_dd': sum(1 for dd in max_drawdowns if dd > 20) / num_simulations,
            
            # Raw simulation data (for plotting)
            'simulation_results': simulation_results
        }
        
        logger.info(f"✅ Monte Carlo complete:")
        logger.info(f"   Mean return: {np.mean(returns_array):.2f}%")
        logger.info(f"   95% CI: [{results['return_95pct_ci'][0]:.2f}%, {results['return_95pct_ci'][1]:.2f}%]")
        logger.info(f"   Probability of profit: {results['probability_of_profit']*100:.1f}%")
        logger.info(f"   Mean max DD: {results['mean_max_drawdown']:.2f}%")
        
        return results
    
    def bootstrap_returns(
        self,
        trades: List[BacktestTrade],
        num_samples: int = 1000,
        sample_size: Optional[int] = None
    ) -> List[float]:
        """
        Bootstrap trade returns to estimate distribution.
        
        Samples WITH replacement to estimate variability.
        
        Args:
            trades: List of historical trades
            num_samples: Number of bootstrap samples
            sample_size: Size of each sample (default: same as trades)
            
        Returns:
            List of bootstrapped total returns
        """
        if not trades:
            return []
        
        if sample_size is None:
            sample_size = len(trades)

        import numpy as np
        
        trade_pnls = [t.pnl for t in trades]
        bootstrapped_returns = []
        
        for _ in range(num_samples):
            # Sample WITH replacement
            sample = np.random.choice(trade_pnls, size=sample_size, replace=True)
            total_return = sum(sample)
            bootstrapped_returns.append(total_return)
        
        return bootstrapped_returns
    
    def analyze_worst_case(
        self,
        trades: List[BacktestTrade],
        percentile: float = 5.0
    ) -> Dict[str, Any]:
        """
        Analyze worst-case scenarios (bottom percentile).
        
        Args:
            trades: List of historical trades
            percentile: Percentile for worst-case (default: 5%)
            
        Returns:
            Dict with worst-case analysis
        """
        if not trades:
            return {}

        import numpy as np
        
        # Sort trades by P&L
        sorted_trades = sorted(trades, key=lambda t: t.pnl)
        
        # Get worst percentile
        worst_count = max(1, int(len(sorted_trades) * (percentile / 100)))
        worst_trades = sorted_trades[:worst_count]
        
        return {
            'percentile': percentile,
            'num_trades': len(worst_trades),
            'total_pnl': sum(t.pnl for t in worst_trades),
            'average_loss': np.mean([t.pnl for t in worst_trades]),
            'max_loss': min(t.pnl for t in worst_trades),
            'total_drawdown': abs(sum(t.pnl for t in worst_trades if t.pnl < 0))
        }
    
    def generate_report(self, results: Dict[str, Any]) -> str:
        """
        Generate formatted Monte Carlo report.
        
        Args:
            results: Results from run_simulations()
            
        Returns:
            Formatted report string
        """
        import numpy as np

        report = f"""
{'='*80}
MONTE CARLO SIMULATION RESULTS
{'='*80}

Simulations: {results['num_simulations']:,}
Trades per simulation: {results['num_trades']}
Initial Capital: ${results['initial_capital']:,.2f}

{'='*80}
RETURN DISTRIBUTION
{'='*80}

Mean Return: {np.mean([r['total_return'] for r in results['simulation_results']]):.2f}%
Median Return: {results['return_percentile_50']:.2f}%
Std Deviation: {np.std([r['total_return'] for r in results['simulation_results']]):.2f}%

Percentiles:
  5th:  {results['return_percentile_5']:.2f}%
  25th: {results['return_percentile_25']:.2f}%
  50th: {results['return_percentile_50']:.2f}%
  75th: {results['return_percentile_75']:.2f}%
  95th: {results['return_percentile_95']:.2f}%

95% Confidence Interval: [{results['return_95pct_ci'][0]:.2f}%, {results['return_95pct_ci'][1]:.2f}%]
99% Confidence Interval: [{results['return_99pct_ci'][0]:.2f}%, {results['return_99pct_ci'][1]:.2f}%]

{'='*80}
RISK METRICS
{'='*80}

Mean Max Drawdown: {results['mean_max_drawdown']:.2f}%
Median Max Drawdown: {results['median_max_drawdown']:.2f}%
Worst Drawdown: {results['worst_drawdown']:.2f}%
Best Drawdown: {results['best_drawdown']:.2f}%

Probability of Profit: {results['probability_of_profit']*100:.1f}%
Probability of Loss: {results['probability_of_loss']*100:.1f}%
Probability of >10% DD: {results['probability_of_10pct_dd']*100:.1f}%
Probability of >20% DD: {results['probability_of_20pct_dd']*100:.1f}%

Mean Sharpe Ratio: {results['mean_sharpe']:.2f}
Median Sharpe Ratio: {results['median_sharpe']:.2f}

{'='*80}
        """
        return report.strip()
