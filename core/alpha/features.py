"""
Session-level feature extraction for alpha discovery.

All features are computed strictly from information available at signal time
(9:29 AM ET) — no lookahead. The resulting DataFrame has one row per trading
session and feeds directly into AlphaScanner.
"""
from __future__ import annotations

import logging
from datetime import datetime, time, timedelta
from typing import Optional

import numpy as np
import pandas as pd
import pytz

logger = logging.getLogger(__name__)

ET = pytz.timezone("US/Eastern")

# Overnight session boundaries (ET)
SESSION_START_HOUR = 18   # 6:00 PM — range tracking begins
SIGNAL_HOUR = 9
SIGNAL_MINUTE = 29        # 9:29 AM — scan moment, feature cutoff

POINT_VALUES: dict[str, float] = {
    "MNQ": 2.0, "MES": 5.0, "MGC": 10.0, "MYM": 0.5, "M2K": 5.0,
    "NQ": 20.0, "ES": 50.0, "GC": 100.0, "CL": 1000.0,
}


def _ewm_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """ATR using exponential weighting. Works on any OHLCV DataFrame."""
    h, l, c = df["high"], df["low"], df["close"].shift(1)
    tr = pd.concat([h - l, (h - c).abs(), (l - c).abs()], axis=1).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()


class SessionFeatureExtractor:
    """
    Produces a per-session feature DataFrame from intraday OHLCV bars.

    Parameters
    ----------
    symbol      : futures root symbol (MNQ, MES, MGC …)
    atr_period  : ATR look-back period on daily bars (default 14)
    """

    # Columns emitted, in order — used downstream to validate joins
    FEATURE_COLS = [
        "symbol",
        "range_pts",          # overnight high − low (raw points)
        "range_high",
        "range_low",
        "range_atr_ratio",    # range_pts / daily_atr  — is this a big/small range day?
        "gap_pts",            # today_open − prev_close  (signed: + = gap up)
        "gap_abs_pts",
        "gap_atr_ratio",      # gap size vs ATR
        "atr_pts",            # daily ATR at signal time (14-period EWM)
        "atr_pct_rank",       # ATR's percentile vs prior 30 sessions (0 = low vol, 1 = high vol)
        "today_open",
        "range_position",     # 0 = open at range low, 1 = open at range high
        "dow",                # day of week: 0=Mon … 4=Fri
        "trend_up",           # 1 if today_open > 20-day EMA, else 0
        "early_range_pct",    # fraction of overnight range formed in first 3 hours
        "overnight_volume",   # total volume during overnight session
        "prior_session_win",  # 1 if previous session had positive P&L (filled in by scanner)
        "consecutive_wins",   # rolling count of consecutive positive sessions before this one
    ]

    def __init__(self, symbol: str, atr_period: int = 14):
        self.symbol = symbol.upper()
        self.point_value = POINT_VALUES.get(self.symbol, 2.0)
        self.atr_period = atr_period

    def extract(self, bars: pd.DataFrame) -> pd.DataFrame:
        """
        Parameters
        ----------
        bars : OHLCV DataFrame.  Index must be timezone-aware (UTC) or naive UTC.
               Columns: open, high, low, close, volume.

        Returns
        -------
        DataFrame indexed by session_date (the calendar date of the 9:30 AM open),
        with one row per session that had sufficient overnight bar coverage.
        """
        df = self._normalise_index(bars)
        df_et = df.copy()
        df_et.index = df_et.index.tz_convert(ET)

        # Daily bars for ATR + EMA context (resampled from intraday)
        daily = (
            df.resample("1D")
            .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
            .dropna(subset=["open"])
        )
        daily_atr = _ewm_atr(daily, self.atr_period)
        daily_ema20 = daily["close"].ewm(span=20, adjust=False).mean()

        session_dates = self._find_session_dates(df_et)
        logger.info(f"[{self.symbol}] Extracting features for {len(session_dates)} sessions")

        rows = []
        for sess_date in session_dates:
            try:
                row = self._session_row(df_et, daily, daily_atr, daily_ema20, sess_date)
                if row is not None:
                    rows.append(row)
            except Exception as exc:
                logger.debug(f"Skipping {sess_date}: {exc}")

        if not rows:
            logger.warning(f"[{self.symbol}] No sessions extracted — check bar data coverage")
            return pd.DataFrame()

        result = pd.DataFrame(rows).set_index("session_date")
        result.index = pd.to_datetime(result.index)

        # Fill consecutive_wins / prior_session_win with NaN — scanner fills these
        # once trade outcomes are attached.  Here they are placeholder columns.
        result["prior_session_win"] = np.nan
        result["consecutive_wins"] = np.nan

        logger.info(f"[{self.symbol}] Extracted {len(result)} sessions with features")
        return result

    # ------------------------------------------------------------------
    # private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _normalise_index(bars: pd.DataFrame) -> pd.DataFrame:
        df = bars.copy()
        df.columns = [c.lower() for c in df.columns]
        if not isinstance(df.index, pd.DatetimeIndex):
            df.index = pd.to_datetime(df.index)
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        else:
            df.index = df.index.tz_convert("UTC")
        df = df.sort_index()
        return df

    @staticmethod
    def _find_session_dates(df_et: pd.DataFrame) -> list:
        """Return sorted list of dates where we have a 9:29–9:31 bar."""
        window = df_et.between_time("09:29", "09:31")
        return sorted(window.index.normalize().unique().tolist())

    def _prev_trading_day(self, d) -> pd.Timestamp:
        """Return the previous calendar day that is Mon–Fri."""
        prev = pd.Timestamp(d) - timedelta(days=1)
        while prev.weekday() >= 5:
            prev -= timedelta(days=1)
        return prev

    def _session_row(
        self,
        df_et: pd.DataFrame,
        daily: pd.DataFrame,
        daily_atr: pd.Series,
        daily_ema20: pd.Series,
        sess_date: pd.Timestamp,
    ) -> Optional[dict]:
        sd = sess_date.date()
        prev_day = self._prev_trading_day(sd)

        # Overnight window: previous_day 18:00 ET → this_day 09:29:59 ET
        on_start = ET.localize(datetime.combine(prev_day, time(SESSION_START_HOUR, 0)))
        on_end   = ET.localize(datetime.combine(sd, time(SIGNAL_HOUR, SIGNAL_MINUTE, 59)))

        overnight = df_et[(df_et.index >= on_start) & (df_et.index <= on_end)]
        if len(overnight) < 5:
            return None

        range_high = float(overnight["high"].max())
        range_low  = float(overnight["low"].min())
        range_pts  = range_high - range_low
        if range_pts <= 0:
            return None

        # Daily ATR at signal time — use last available daily bar before this session
        daily_tz = daily.index.tz_localize("UTC") if daily.index.tz is None else daily.index
        eligible = daily_tz[daily_tz.normalize() < pd.Timestamp(sd, tz="UTC")]
        if len(eligible) < self.atr_period + 2:
            return None

        atr_val    = float(daily_atr.loc[eligible[-1]])
        ema20_val  = float(daily_ema20.loc[eligible[-1]])

        if atr_val <= 0:
            return None

        # Today's open (first bar at / after 9:29 ET)
        today_bars = df_et[df_et.index.date == sd]
        open_bars  = today_bars.between_time("09:29", "09:35")
        if open_bars.empty:
            return None
        today_open = float(open_bars.iloc[0]["open"])

        # Previous session close (last bar before 16:00 ET on previous trading day)
        prev_bars  = df_et[df_et.index.date == prev_day.date()]
        prev_close_bars = prev_bars[prev_bars.index.hour < 16]
        if prev_close_bars.empty:
            return None
        prev_close = float(prev_close_bars.iloc[-1]["close"])

        # Gap
        gap_pts     = today_open - prev_close
        gap_abs_pts = abs(gap_pts)

        # ATR percentile rank vs prior 30 daily sessions
        prior_atr_slice = daily_atr.loc[eligible[-30:]]
        atr_pct_rank = float((prior_atr_slice < atr_val).mean()) if len(prior_atr_slice) >= 5 else 0.5

        # Fraction of range formed in first 3 hours of overnight
        first_3h = overnight[overnight.index < (on_start + timedelta(hours=3))]
        if len(first_3h) >= 2:
            early_range = first_3h["high"].max() - first_3h["low"].min()
            early_range_pct = min(float(early_range / range_pts), 1.0)
        else:
            early_range_pct = np.nan

        overnight_volume = (
            float(overnight["volume"].sum()) if "volume" in overnight.columns else np.nan
        )

        return {
            "session_date":     pd.Timestamp(sd),
            "symbol":           self.symbol,
            "range_pts":        range_pts,
            "range_high":       range_high,
            "range_low":        range_low,
            "range_atr_ratio":  range_pts / atr_val,
            "gap_pts":          gap_pts,
            "gap_abs_pts":      gap_abs_pts,
            "gap_atr_ratio":    gap_abs_pts / atr_val,
            "atr_pts":          atr_val,
            "atr_pct_rank":     atr_pct_rank,
            "today_open":       today_open,
            "range_position":   (today_open - range_low) / range_pts,
            "dow":              pd.Timestamp(sd).weekday(),
            "trend_up":         int(today_open > ema20_val),
            "early_range_pct":  early_range_pct,
            "overnight_volume": overnight_volume,
        }
