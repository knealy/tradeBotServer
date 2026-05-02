"""
AlphaScanner — orchestrates the full alpha discovery pipeline.

1. Extracts session-level features from bar data (features.py).
2. Simulates overnight-range trades on those sessions (built-in sim).
3. Joins features → outcomes, fills look-back columns.
4. Runs hypothesis tests (hypothesis.py) on every feature.
5. Returns FDR-corrected, ranked HypothesisResult list.

The built-in sim mirrors the overnight_range_strategy logic:
  - LONG entry  = range_high (stop-buy trigger)
  - SHORT entry = range_low  (stop-sell trigger)
  - SL / TP derived from ATR and configurable multipliers
  - 5-min bar walk to determine which exits first
  - Only one side triggers per session (whichever range wall breaks first)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import pytz

from .features import SessionFeatureExtractor, ET, POINT_VALUES
from .hypothesis import (
    HypothesisResult,
    test_categorical,
    test_continuous,
    apply_fdr_correction,
)

logger = logging.getLogger(__name__)

DOW_LABELS = {0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu", 4: "Fri"}
TREND_LABELS = {0: "Downtrend", 1: "Uptrend"}
PRIOR_WIN_LABELS = {0.0: "Prior Loss/Flat", 1.0: "Prior Win"}
SIDE_LABELS = {"LONG": "LONG", "SHORT": "SHORT"}

CATEGORICAL_TESTS: Dict[str, Optional[dict]] = {
    "dow":               DOW_LABELS,
    "trend_up":          TREND_LABELS,
    "breakout_side":     SIDE_LABELS,
    "prior_session_win": PRIOR_WIN_LABELS,
}

CONTINUOUS_TESTS = [
    "range_pts",
    "range_atr_ratio",
    "gap_abs_pts",
    "gap_atr_ratio",
    "atr_pts",
    "atr_pct_rank",
    "range_position",
    "early_range_pct",
    "consecutive_wins",
]


# ---------------------------------------------------------------------------
# Simulation
# ---------------------------------------------------------------------------

@dataclass
class SimTrade:
    session_date: pd.Timestamp
    symbol: str
    side: str              # "LONG" | "SHORT"
    entry_price: float
    exit_price: float
    sl_price: float
    tp_price: float
    exit_reason: str       # "tp" | "sl" | "timeout"
    pnl: float             # dollars
    realized_r: float      # pnl / initial_risk_dollars
    bars_held: int


def _simulate_session(
    session_date: pd.Timestamp,
    df_et: pd.DataFrame,
    range_high: float,
    range_low: float,
    atr: float,
    symbol: str,
    stop_atr_mult: float,
    tp_atr_mult: float,
    point_value: float,
    quantity: int,
    slippage_pts: float,
) -> List[SimTrade]:
    """
    Simulate overnight-range breakout trades for one session.

    Walks 5-min (or finer) bars from 9:30 AM to 4:00 PM ET and checks
    whether each side's entry stop is triggered.  Once triggered, walks
    further bars for SL/TP.  Both sides are evaluated independently.
    """
    sd = session_date.date()
    session_open = ET.localize(datetime.combine(sd, time(9, 30)))
    session_close = ET.localize(datetime.combine(sd, time(16, 0)))

    day_bars = df_et[
        (df_et.index >= session_open) & (df_et.index < session_close)
    ]

    if day_bars.empty:
        return []

    trades: List[SimTrade] = []

    for side, entry_trigger, direction in [
        ("LONG",  range_high, 1),
        ("SHORT", range_low,  -1),
    ]:
        entry_fill = entry_trigger + direction * slippage_pts
        sl = entry_fill - direction * stop_atr_mult * atr
        tp = entry_fill + direction * tp_atr_mult * atr
        initial_risk = abs(entry_fill - sl) * point_value * quantity

        in_trade = False
        entry_bar_i = None

        for i, (ts, bar) in enumerate(day_bars.iterrows()):
            if not in_trade:
                # Check entry trigger: LONG needs bar.high >= trigger, SHORT needs bar.low <= trigger
                if side == "LONG"  and bar["high"] >= entry_trigger:
                    in_trade = True
                    entry_bar_i = i
                elif side == "SHORT" and bar["low"] <= entry_trigger:
                    in_trade = True
                    entry_bar_i = i
                continue

            # In trade — check SL and TP on this bar
            if side == "LONG":
                sl_hit = bar["low"]  <= sl
                tp_hit = bar["high"] >= tp
            else:
                sl_hit = bar["high"] >= sl
                tp_hit = bar["low"]  <= tp

            exit_reason = None
            exit_price  = None

            if tp_hit and sl_hit:
                # Both hit same bar — conservative: assume SL hit first
                exit_reason, exit_price = "sl", sl
            elif tp_hit:
                exit_reason, exit_price = "tp", tp
            elif sl_hit:
                exit_reason, exit_price = "sl", sl

            # Session timeout
            if exit_reason is None and i == len(day_bars) - 1:
                exit_reason, exit_price = "timeout", float(bar["close"])

            if exit_reason:
                exit_fill  = exit_price - direction * slippage_pts
                pnl_pts    = (exit_fill - entry_fill) * direction
                pnl_usd    = pnl_pts * point_value * quantity
                realized_r = pnl_usd / initial_risk if initial_risk > 0 else 0.0
                trades.append(SimTrade(
                    session_date=session_date,
                    symbol=symbol,
                    side=side,
                    entry_price=entry_fill,
                    exit_price=exit_fill,
                    sl_price=sl,
                    tp_price=tp,
                    exit_reason=exit_reason,
                    pnl=pnl_usd,
                    realized_r=realized_r,
                    bars_held=i - (entry_bar_i or 0),
                ))
                break

    return trades


# ---------------------------------------------------------------------------
# Main scanner class
# ---------------------------------------------------------------------------

class AlphaScanner:
    """
    Runs the full alpha discovery pipeline on a single symbol.

    Parameters
    ----------
    symbol           : futures root (MNQ, MES, MGC …)
    stop_atr_mult    : ATR multiplier for stop loss (mirrors overnight_range.toml)
    tp_atr_mult      : ATR multiplier for take profit
    quantity         : contracts per trade (for P&L calculation)
    slippage_pts     : slippage per side in points
    atr_period       : ATR look-back (daily bars)
    """

    def __init__(
        self,
        symbol: str,
        stop_atr_mult: float = 1.25,
        tp_atr_mult: float = 2.0,
        quantity: int = 1,
        slippage_pts: float = 0.25,
        atr_period: int = 14,
    ):
        self.symbol = symbol.upper()
        self.stop_atr_mult = stop_atr_mult
        self.tp_atr_mult = tp_atr_mult
        self.quantity = quantity
        self.slippage_pts = slippage_pts
        self.atr_period = atr_period
        self.point_value = POINT_VALUES.get(self.symbol, 2.0)
        self._extractor = SessionFeatureExtractor(symbol, atr_period)

        # Populated by run()
        self.merged_df: Optional[pd.DataFrame] = None
        self.raw_results: List[HypothesisResult] = []

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    def run(self, bars: pd.DataFrame) -> List[HypothesisResult]:
        """
        Full pipeline: bars → features → simulation → hypothesis tests.

        Returns
        -------
        FDR-corrected list of HypothesisResult, sorted by rank().
        """
        sessions = self._extractor.extract(bars)
        if sessions.empty:
            logger.error("No sessions extracted — cannot run alpha scan")
            return []

        df_et = self._to_et(bars)
        trades_df = self._simulate_all(sessions, df_et)

        if trades_df.empty or len(trades_df) < 10:
            logger.warning(
                f"Only {len(trades_df)} simulated trades — scan needs ≥ 10. "
                "Try more days or a finer timeframe."
            )
            return []

        merged = self._merge(sessions, trades_df)
        self.merged_df = merged

        logger.info(
            f"[{self.symbol}] Running hypothesis tests on "
            f"{len(merged)} trades × {len(merged.columns)} features"
        )

        results = self.hypothesis_battery(merged)
        self.raw_results = results
        return results

    def hypothesis_battery(self, merged: pd.DataFrame) -> List[HypothesisResult]:
        """
        Run FDR-corrected tests on a merged feature+outcome frame.

        Used by ``run()`` on the full sample, and by callers that split rows
        (e.g. IS/OOS by session date) without re-running feature extraction.
        """
        if merged.empty:
            return []

        results: List[HypothesisResult] = []

        for feat, label_map in CATEGORICAL_TESTS.items():
            if feat not in merged.columns:
                continue
            r = test_categorical(merged, feat, label_map=label_map)
            if r:
                results.append(r)

        for feat in CONTINUOUS_TESTS:
            if feat not in merged.columns:
                continue
            r = test_continuous(merged, feat)
            if r:
                results.append(r)

        results = apply_fdr_correction(results, alpha=0.05)
        return self.rank(results)

    @staticmethod
    def rank(results: List[HypothesisResult]) -> List[HypothesisResult]:
        """Sort: significant first, then by |IC| descending, then p-value."""
        return sorted(results, key=lambda r: (not r.significant, -abs(r.ic), r.p_value))

    def summary_stats(self, merged: Optional[pd.DataFrame] = None) -> Dict:
        """Overall backtest stats for the simulated trade set (or a subset of merged rows)."""
        df = merged if merged is not None else self.merged_df
        if df is None or df.empty:
            return {}
        r = df["realized_r"].dropna()
        wins = r[r > 0]
        losses = r[r < 0]
        pf = (
            abs(wins.sum()) / abs(losses.sum())
            if len(losses) > 0 and abs(losses.sum()) > 0
            else float("inf")
        )
        pnl = df["pnl"].dropna()
        equity = pnl.cumsum()
        dd = equity - equity.cummax()
        return {
            "n_trades":       len(r),
            "win_rate":       float(len(wins) / len(r)) if len(r) > 0 else 0.0,
            "avg_r":          float(r.mean()),
            "profit_factor":  float(pf),
            "total_pnl":      float(pnl.sum()),
            "max_drawdown":   float(dd.min()),
            "sharpe":         float(r.mean() / r.std() * (252 ** 0.5)) if r.std() > 0 else 0.0,
        }

    # ------------------------------------------------------------------
    # private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _to_et(bars: pd.DataFrame) -> pd.DataFrame:
        df = bars.copy()
        df.columns = [c.lower() for c in df.columns]
        if not isinstance(df.index, pd.DatetimeIndex):
            df.index = pd.to_datetime(df.index)
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        df.index = df.index.tz_convert(ET)
        return df.sort_index()

    def _simulate_all(
        self, sessions: pd.DataFrame, df_et: pd.DataFrame
    ) -> pd.DataFrame:
        """Run simulation for every session row and return flat trades DataFrame."""
        all_trades: List[dict] = []
        for sess_date, row in sessions.iterrows():
            trades = _simulate_session(
                session_date=sess_date,
                df_et=df_et,
                range_high=float(row["range_high"]),
                range_low=float(row["range_low"]),
                atr=float(row["atr_pts"]),
                symbol=self.symbol,
                stop_atr_mult=self.stop_atr_mult,
                tp_atr_mult=self.tp_atr_mult,
                point_value=self.point_value,
                quantity=self.quantity,
                slippage_pts=self.slippage_pts,
            )
            for t in trades:
                all_trades.append({
                    "session_date": t.session_date,
                    "symbol":       t.symbol,
                    "side":         t.side,
                    "entry_price":  t.entry_price,
                    "exit_price":   t.exit_price,
                    "sl_price":     t.sl_price,
                    "tp_price":     t.tp_price,
                    "exit_reason":  t.exit_reason,
                    "pnl":          t.pnl,
                    "realized_r":   t.realized_r,
                    "bars_held":    t.bars_held,
                })

        if not all_trades:
            return pd.DataFrame()

        tdf = pd.DataFrame(all_trades)
        tdf["session_date"] = pd.to_datetime(tdf["session_date"])
        logger.info(
            f"[{self.symbol}] Simulated {len(tdf)} trades across "
            f"{tdf['session_date'].nunique()} sessions"
        )
        return tdf

    def _merge(self, sessions: pd.DataFrame, trades: pd.DataFrame) -> pd.DataFrame:
        """Join trades → session features, fill look-back columns."""
        sessions_reset = sessions.reset_index()
        sessions_reset["session_date"] = pd.to_datetime(sessions_reset["session_date"])
        # Drop placeholder columns that will be filled in below
        for col in ("prior_session_win", "consecutive_wins"):
            if col in sessions_reset.columns:
                sessions_reset = sessions_reset.drop(columns=[col])
        merged = trades.merge(sessions_reset, on="session_date", how="inner")

        # breakout_side from trade side column
        merged["breakout_side"] = merged["side"]

        # prior_session_win: outcome (win=1, loss=0) of PREVIOUS session
        # Average across both sides if multiple trades per session
        session_results = (
            merged.groupby("session_date")["realized_r"]
            .mean()
            .rename("session_r")
        )
        session_win = (session_results > 0).astype(float).rename("session_win")
        merged = merged.join(session_win.shift(1).rename("prior_session_win"), on="session_date")

        # consecutive_wins: rolling count before this session
        cumulative_wins = []
        streak = 0
        for _, (date, r) in session_results.reset_index().iterrows():
            if r > 0:
                streak += 1
            else:
                streak = 0
            cumulative_wins.append((date, max(0, streak - 1)))  # -1: exclude current

        streak_df = pd.DataFrame(cumulative_wins, columns=["session_date", "consecutive_wins"])
        merged = merged.merge(streak_df, on="session_date", how="left")

        return merged.reset_index(drop=True)
