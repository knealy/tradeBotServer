"""
Performance Metrics Calculator

Comprehensive metrics for evaluating trading strategy performance.
"""

from __future__ import annotations

from typing import List, Dict, Optional
from datetime import datetime, timedelta
import logging
from .models import BacktestTrade, BacktestResult

logger = logging.getLogger(__name__)


class PerformanceMetrics:
    """
    Calculate comprehensive performance metrics for trading strategies.
    
    Metrics include:
    - Return metrics (total return, CAGR, monthly returns)
    - Risk metrics (Sharpe, Sortino, max DD, volatility)
    - Trade metrics (win rate, profit factor, expectancy)
    - Distribution metrics (skewness, kurtosis)
    """
    
    @staticmethod
    def calculate_sharpe_ratio(
        returns: pd.Series,
        risk_free_rate: float = 0.02,
        periods_per_year: int = 252
    ) -> float:
        """
        Calculate annualized Sharpe ratio.
        
        Args:
            returns: Series of period returns
            risk_free_rate: Annual risk-free rate (default: 2%)
            periods_per_year: Trading periods per year (252 for daily, 252*6.5 for hourly)
            
        Returns:
            Sharpe ratio
        """
        import numpy as np

        if len(returns) < 2 or returns.std() == 0:
            return 0.0
        
        # Convert annual risk-free rate to period rate
        rf_period = risk_free_rate / periods_per_year
        
        excess_returns = returns - rf_period
        return (excess_returns.mean() / excess_returns.std()) * np.sqrt(periods_per_year)
    
    @staticmethod
    def calculate_sortino_ratio(
        returns: pd.Series,
        risk_free_rate: float = 0.02,
        periods_per_year: int = 252
    ) -> float:
        """
        Calculate annualized Sortino ratio (downside deviation only).
        
        Args:
            returns: Series of period returns
            risk_free_rate: Annual risk-free rate
            periods_per_year: Trading periods per year
            
        Returns:
            Sortino ratio
        """
        import numpy as np

        if len(returns) < 2:
            return 0.0
        
        rf_period = risk_free_rate / periods_per_year
        excess_returns = returns - rf_period
        downside_returns = excess_returns[excess_returns < 0]
        
        if len(downside_returns) == 0 or downside_returns.std() == 0:
            return 0.0
        
        return (excess_returns.mean() / downside_returns.std()) * np.sqrt(periods_per_year)
    
    @staticmethod
    def calculate_max_drawdown(equity_curve: List[float]) -> tuple:
        """
        Calculate maximum drawdown and peak-to-trough dates.
        
        Args:
            equity_curve: List of equity values
            
        Returns:
            (max_dd_dollars, max_dd_percent, peak_idx, trough_idx)
        """
        if not equity_curve:
            return 0.0, 0.0, 0, 0
        
        peak = equity_curve[0]
        peak_idx = 0
        max_dd = 0.0
        max_dd_pct = 0.0
        trough_idx = 0
        
        for i, value in enumerate(equity_curve):
            if value > peak:
                peak = value
                peak_idx = i
            
            dd = peak - value
            dd_pct = (dd / peak) * 100 if peak > 0 else 0.0
            
            if dd > max_dd:
                max_dd = dd
                max_dd_pct = dd_pct
                trough_idx = i
        
        return max_dd, max_dd_pct, peak_idx, trough_idx
    
    @staticmethod
    def calculate_calmar_ratio(
        total_return: float,
        max_drawdown_pct: float,
        years: float
    ) -> float:
        """
        Calculate Calmar ratio (CAGR / Max Drawdown).
        
        Args:
            total_return: Total return percentage
            max_drawdown_pct: Maximum drawdown percentage
            years: Number of years
            
        Returns:
            Calmar ratio
        """
        if years == 0 or max_drawdown_pct == 0:
            return 0.0
        
        cagr = ((1 + total_return / 100) ** (1 / years) - 1) * 100
        return cagr / max_drawdown_pct
    
    @staticmethod
    def calculate_profit_factor(trades: List[BacktestTrade]) -> float:
        """
        Calculate profit factor (gross profit / gross loss).
        
        Args:
            trades: List of completed trades
            
        Returns:
            Profit factor
        """
        if not trades:
            return 0.0
        
        gross_profit = sum(t.pnl for t in trades if t.pnl > 0)
        gross_loss = abs(sum(t.pnl for t in trades if t.pnl < 0))
        
        if gross_loss == 0:
            return float('inf') if gross_profit > 0 else 0.0
        
        return gross_profit / gross_loss
    
    @staticmethod
    def calculate_win_rate(trades: List[BacktestTrade]) -> float:
        """
        Calculate win rate percentage.
        
        Args:
            trades: List of completed trades
            
        Returns:
            Win rate (0-100)
        """
        if not trades:
            return 0.0
        
        winning = sum(1 for t in trades if t.pnl > 0)
        return (winning / len(trades)) * 100
    
    @staticmethod
    def calculate_expectancy(trades: List[BacktestTrade]) -> float:
        """
        Calculate expectancy (average P&L per trade).
        
        Args:
            trades: List of completed trades
            
        Returns:
            Expectancy in dollars
        """
        if not trades:
            return 0.0
        
        return sum(t.pnl for t in trades) / len(trades)
    
    @staticmethod
    def calculate_consecutive_wins_losses(trades: List[BacktestTrade]) -> Dict[str, int]:
        """
        Calculate max consecutive wins and losses.
        
        Args:
            trades: List of completed trades
            
        Returns:
            Dict with max_consecutive_wins and max_consecutive_losses
        """
        if not trades:
            return {'max_consecutive_wins': 0, 'max_consecutive_losses': 0}
        
        max_wins = 0
        max_losses = 0
        current_wins = 0
        current_losses = 0
        
        for trade in trades:
            if trade.pnl > 0:
                current_wins += 1
                current_losses = 0
                max_wins = max(max_wins, current_wins)
            else:
                current_losses += 1
                current_wins = 0
                max_losses = max(max_losses, current_losses)
        
        return {
            'max_consecutive_wins': max_wins,
            'max_consecutive_losses': max_losses
        }
    
    @staticmethod
    def calculate_risk_of_ruin(
        win_rate: float,
        avg_win: float,
        avg_loss: float,
        capital: float,
        risk_per_trade: float
    ) -> float:
        """
        Calculate risk of ruin (probability of losing all capital).
        
        Uses simplified formula: RoR = ((1-W)/W) ^ (Capital/Risk)
        
        Args:
            win_rate: Win rate (0-1)
            avg_win: Average winning trade
            avg_loss: Average losing trade
            capital: Total capital
            risk_per_trade: Risk per trade
            
        Returns:
            Risk of ruin (0-1)
        """
        if win_rate >= 1.0 or win_rate <= 0.0:
            return 0.0
        
        if risk_per_trade <= 0 or capital <= 0:
            return 1.0
        
        # Number of trades until ruin
        trades_to_ruin = capital / risk_per_trade
        
        # Simplified risk of ruin formula
        p_loss = 1 - win_rate
        ror = (p_loss / win_rate) ** trades_to_ruin
        
        return min(ror, 1.0)
    
    @staticmethod
    def analyze_trade_distribution(trades: List[BacktestTrade]) -> Dict[str, float]:
        """
        Analyze distribution of trade returns.
        
        Args:
            trades: List of completed trades
            
        Returns:
            Dict with distribution statistics
        """
        if not trades:
            return {}
        
        import numpy as np
        import pandas as pd

        returns = [t.pnl for t in trades]
        
        return {
            'mean': np.mean(returns),
            'median': np.median(returns),
            'std': np.std(returns),
            'skewness': pd.Series(returns).skew(),
            'kurtosis': pd.Series(returns).kurtosis(),
            'min': min(returns),
            'max': max(returns),
            'percentile_25': np.percentile(returns, 25),
            'percentile_75': np.percentile(returns, 75)
        }
    
    @staticmethod
    def calculate_recovery_factor(total_return: float, max_drawdown: float) -> float:
        """
        Calculate recovery factor (net profit / max drawdown).
        
        Args:
            total_return: Total return in dollars
            max_drawdown: Maximum drawdown in dollars
            
        Returns:
            Recovery factor
        """
        if max_drawdown == 0:
            return float('inf') if total_return > 0 else 0.0
        
        return total_return / max_drawdown
    
    @staticmethod
    def calculate_monthly_returns(
        equity_curve: List[tuple]
    ) -> pd.DataFrame:
        """
        Calculate month-by-month returns.
        
        Args:
            equity_curve: List of (timestamp, equity) tuples
            
        Returns:
            DataFrame with monthly returns
        """
        import pandas as pd

        if not equity_curve:
            return pd.DataFrame()
        
        df = pd.DataFrame(equity_curve, columns=['timestamp', 'equity'])
        df.set_index('timestamp', inplace=True)
        
        # Resample to monthly
        monthly = df.resample('M').last()
        monthly['return'] = monthly['equity'].pct_change() * 100
        monthly['return_dollars'] = monthly['equity'].diff()
        
        return monthly
    
    @staticmethod
    def generate_report(result: BacktestResult) -> str:
        """
        Generate formatted text report.
        
        Args:
            result: BacktestResult object
            
        Returns:
            Formatted report string
        """
        report = f"""
{'='*80}
BACKTEST RESULTS: {result.strategy_name}
{'='*80}

Symbol: {result.symbol}
Period: {result.start_date.date()} to {result.end_date.date()}
Initial Capital: ${result.initial_capital:,.2f}
Final Capital: ${result.final_capital:,.2f}

{'='*80}
PERFORMANCE SUMMARY
{'='*80}

Total Return: ${result.total_pnl:,.2f} ({result.total_return_pct:.2f}%)
Total Trades: {result.total_trades}
Win Rate: {result.win_rate:.1f}%
Avg Reward/Risk: {result.avg_reward_risk:.3f}  (mean PnL / initial bracket risk $; 0 if unknown)
Profit Factor: {result.profit_factor:.2f}
Expectancy: ${result.expectancy:.2f}

Sharpe Ratio: {result.sharpe_ratio:.2f}
Sortino Ratio: {result.sortino_ratio:.2f}
Max Drawdown: ${result.max_drawdown:,.2f} ({result.max_drawdown_pct:.2f}%)

{'='*80}
TRADE STATISTICS
{'='*80}

Winning Trades: {result.winning_trades}
Losing Trades: {result.losing_trades}
Average Win: ${result.average_win:.2f}
Average Loss: ${result.average_loss:.2f}
Largest Win: ${result.largest_win:.2f}
Largest Loss: ${result.largest_loss:.2f}

Average Bars Held: {result.average_bars_held:.1f}

{'='*80}
COSTS
{'='*80}

Total Commission: ${result.total_commission:.2f}
Total Slippage: ${result.total_slippage:.2f}

{'='*80}
        """
        return report.strip()
