#!/usr/bin/env python3
"""Independent sieve for the 7am range / sweep / re-entry strategy.

Loads canonical 5m OHLCV (naive UTC timestamps = bar **open**, see
``historical_data/resample_ohlcv_csv.py``), runs
``sieve_simulate_from_ohlcv`` from ``strategies.morning_range_reversion_strategy``,
and prints counts vs the third-party headline stats (do not assume those are
correct).

Example:
  .venv/bin/python scripts/validate_morning_range_reversion.py \\
    --csv historical_data/price/MNQ_5m_databento.csv \\
    --start 2024-01-01 --end 2026-05-01

Quick tail-window check (same sieve, default last 90 days): ``scripts/strategy_litmus.py morning_range``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from core.backtest.data_loader import HistoricalDataLoader  # noqa: E402
from strategies.morning_range_reversion_strategy import sieve_simulate_from_ohlcv  # noqa: E402


def _utc_naive_ts(value: str) -> pd.Timestamp:
    """Parse YYYY-MM-DD for filtering; match naive UTC index from ``load_from_csv``."""
    t = pd.Timestamp(value)
    if t.tzinfo is not None:
        return t.tz_convert("UTC").tz_localize(None)
    return t


def _ensure_index_utc_naive(df: pd.DataFrame) -> pd.DataFrame:
    idx = df.index
    if getattr(idx, "tz", None) is None:
        return df
    out = df.copy()
    out.index = idx.tz_convert("UTC").tz_localize(None)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Sieve morning range reversion stats from 5m CSV.")
    ap.add_argument("--csv", required=True, type=Path, help="5m OHLCV CSV (UTC naive)")
    ap.add_argument(
        "--1m-csv",
        dest="csv_1m",
        type=Path,
        default=None,
        help="Optional 1m OHLCV (same naive UTC index) to resolve TP vs SL inside a 5m bar",
    )
    ap.add_argument("--symbol", default="MNQ", help="Label only (loader metadata)")
    ap.add_argument("--start", default=None, help="Inclusive date YYYY-MM-DD (optional)")
    ap.add_argument("--end", default=None, help="Exclusive date YYYY-MM-DD (optional)")
    ap.add_argument(
        "--optimistic-intrabar",
        action="store_true",
        help="If SL and TP both touch same bar, count TP first (not conservative).",
    )
    ap.add_argument(
        "--legacy-reentry",
        action="store_true",
        help="Wait for close back inside range before arming (matches older docs / grid studies).",
    )
    ap.add_argument("--tp-mult", type=float, default=None, help="TP distance = (W/2)*tp_mult (default 1.0)")
    ap.add_argument("--sl-mult", type=float, default=None, help="SL distance = (W/2)*sl_mult (default 1.0)")
    ap.add_argument(
        "--reentry-frac",
        type=float,
        default=None,
        help="Inner re-entry band fraction (0..0.49, default 0.0)",
    )
    ap.add_argument(
        "--max-fades",
        type=int,
        default=None,
        help="Max fade arms per session (0 = unlimited; validated preset often uses 1)",
    )
    ap.add_argument(
        "--require-high-atr",
        action="store_true",
        help="BONGO §4.3: gate arms on rolling ATR vs trailing quantile (needs long history)",
    )
    args = ap.parse_args()

    if not args.csv.is_file():
        print(f"Error: missing CSV {args.csv}", file=sys.stderr)
        return 1

    loader = HistoricalDataLoader()
    df = loader.load_from_csv(str(args.csv), symbol=args.symbol)
    df = _ensure_index_utc_naive(df)
    if args.start:
        df = df[df.index >= _utc_naive_ts(args.start)]
    if args.end:
        df = df[df.index < _utc_naive_ts(args.end)]
    if len(df) < 100:
        print(f"Error: need more rows after filter (got {len(df)})", file=sys.stderr)
        return 1

    df1m = None
    if args.csv_1m is not None:
        if not args.csv_1m.is_file():
            print(f"Error: missing 1m CSV {args.csv_1m}", file=sys.stderr)
            return 1
        df1m = loader.load_from_csv(str(args.csv_1m), symbol=args.symbol)
        df1m = _ensure_index_utc_naive(df1m)
        if args.start:
            df1m = df1m[df1m.index >= _utc_naive_ts(args.start)]
        if args.end:
            df1m = df1m[df1m.index < _utc_naive_ts(args.end)]

    sieve_kw = dict(
        stop_before_target_same_bar=not args.optimistic_intrabar,
        one_minute_df=df1m,
        require_reentry_close=args.legacy_reentry,
    )
    if args.tp_mult is not None:
        sieve_kw["tp_mult"] = float(args.tp_mult)
    if args.sl_mult is not None:
        sieve_kw["sl_mult"] = float(args.sl_mult)
    if args.reentry_frac is not None:
        sieve_kw["reentry_frac"] = float(args.reentry_frac)
    if args.max_fades is not None:
        sieve_kw["max_fades_per_session"] = int(args.max_fades)
    if args.require_high_atr:
        sieve_kw["require_high_atr"] = True

    trades = sieve_simulate_from_ohlcv(df, **sieve_kw)
    n = len(trades)
    wins = sum(1 for t in trades if t.outcome == "win")
    losses = sum(1 for t in trades if t.outcome == "loss")
    high_s = [t for t in trades if t.sweep == "high"]
    low_s = [t for t in trades if t.sweep == "low"]
    sess = {t.session_date for t in trades}

    print("morning_range_reversion sieve")
    print(f"  legacy_reentry (--legacy-reentry): {args.legacy_reentry}")
    if args.tp_mult is not None or args.sl_mult is not None:
        print(f"  tp_mult={args.tp_mult!r}  sl_mult={args.sl_mult!r}")
    if args.reentry_frac is not None:
        print(f"  reentry_frac={args.reentry_frac!r}")
    if args.max_fades is not None:
        print(f"  max_fades_per_session={args.max_fades!r}")
    if args.require_high_atr:
        print("  require_high_atr=True")
    print(f"  csv:          {args.csv.resolve()}")
    if args.csv_1m:
        print(f"  1m_csv:       {args.csv_1m.resolve()}")
    print(f"  rows:         {len(df)}  trades: {n}  session_days_with_trade: {len(sess)}")
    if n:
        print(f"  win_rate:     {wins / n:.4f}  ({wins}/{n})")
        print(f"  losses:       {losses}")
        print(f"  high_sweep_n: {len(high_s)}  win_rate_high: {sum(1 for t in high_s if t.outcome == 'win') / max(1, len(high_s)):.4f}")
        print(f"  low_sweep_n:  {len(low_s)}  win_rate_low:  {sum(1 for t in low_s if t.outcome == 'win') / max(1, len(low_s)):.4f}")
    else:
        print("  (no trades — check date range / range window / data gaps)")
    print(
        "  note: intrabar path assumes stop-before-target when both hit (default); "
        "third-party ~92% headline used optimistic assumptions and unknown data."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
