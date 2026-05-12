#!/usr/bin/env python3
"""Fast, narrow-window checks before committing to a full replay.

Presets call **in-process** logic (no ``backtest_executor`` subprocess) so they
stay quick on large CSVs when you clip the date range.

Example (last 90 calendar days of MNQ 5m — seconds instead of full history)::

  .venv/bin/python scripts/strategy_litmus.py morning_range \\
    --csv historical_data/price/MNQ_5m_databento.csv --last-days 90

Compare conservative vs optimistic intrabar for the morning-range sieve::

  .venv/bin/python scripts/strategy_litmus.py morning_range \\
    --csv historical_data/price/MNQ_5m_databento.csv --last-days 120 --compare-intrabar

Explain common gaps vs third-party headline stats::

  .venv/bin/python scripts/strategy_litmus.py morning_range --why
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import pandas as pd

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from core.backtest.data_loader import HistoricalDataLoader  # noqa: E402
from strategies.morning_range_reversion_strategy import sieve_simulate_from_ohlcv  # noqa: E402


def _utc_naive_ts(value: str) -> pd.Timestamp:
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


def _load_window(
    csv: Path,
    symbol: str,
    *,
    start: str | None,
    end: str | None,
    last_days: int | None,
) -> pd.DataFrame:
    loader = HistoricalDataLoader()
    df = loader.load_from_csv(str(csv), symbol=symbol)
    df = _ensure_index_utc_naive(df)
    if last_days is not None and last_days > 0:
        hi = df.index.max()
        if pd.isna(hi):
            raise ValueError("empty index after load")
        lo = hi - pd.Timedelta(days=int(last_days) + 2)
        df = df[df.index >= lo]
    if start:
        df = df[df.index >= _utc_naive_ts(start)]
    if end:
        df = df[df.index < _utc_naive_ts(end)]
    return df


def _summarize_sieve(trades: list) -> dict:
    n = len(trades)
    if not n:
        return {"n": 0, "wins": 0, "win_rate": 0.0, "sessions": 0}
    wins = sum(1 for t in trades if t.outcome == "win")
    sess = len({t.session_date for t in trades})
    return {"n": n, "wins": wins, "win_rate": wins / n, "sessions": sess}


def _print_why() -> None:
    print(
        """
Why our morning-range sieve can disagree with a ~92% third-party headline
------------------------------------------------------------------------
- Intrabar: without **1m** data we default to **stop before target** when both
  touch one 5m bar (or TP-first with ``--optimistic-intrabar``). With
  ``--1m-csv`` / ``validate_morning_range_reversion.py --1m-csv``, the sieve
  walks **1m** O→H→L→C paths inside that 5m window instead of assuming.
- Trade count: we allow **multiple** sweep→re-entry sequences per **ET
  calendar day** when flat again; some studies count **≤1** trade/day or a
  capped horizon after the first sweep.
- Data: **Databento / stitched MNQ** vs vendor chart; roll / microstructure /
  session inclusion can change whether a bar **closes** beyond the range.
- Definition drift: 7:00–7:59 **EST** wording vs **America/New_York** (DST);
  we use proper Eastern with naive-UTC bar instants (same convention as
  ``resample_ohlcv_csv`` / ``HistoricalDataLoader``).

Timezone sanity: if naive timestamps were **actually** Chicago or ET values
mis-tagged as UTC, the 7–8 ET window would land on the wrong bars — see
``docs/BACKTESTING.md`` § Timestamps.
""".strip()
    )


def cmd_morning_range(args: argparse.Namespace) -> int:
    if args.why:
        _print_why()
        if not args.csv:
            return 0

    if not args.csv.is_file():
        print(f"Error: missing CSV {args.csv}", file=sys.stderr)
        return 1

    if args.start or args.end:
        last_days_eff = None
    elif args.last_days == 0:
        last_days_eff = None
    else:
        last_days_eff = args.last_days

    t0 = time.perf_counter()
    df = _load_window(
        args.csv,
        args.symbol,
        start=args.start,
        end=args.end,
        last_days=last_days_eff,
    )
    load_s = time.perf_counter() - t0
    if len(df) < 80:
        print(f"Error: need more rows after window (got {len(df)})", file=sys.stderr)
        return 1

    df1m = None
    if getattr(args, "csv_1m", None):
        if not args.csv_1m.is_file():
            print(f"Error: missing 1m CSV {args.csv_1m}", file=sys.stderr)
            return 1
        df1m = _load_window(
            args.csv_1m,
            args.symbol,
            start=args.start,
            end=args.end,
            last_days=last_days_eff,
        )

    t1 = time.perf_counter()
    tr_cons = sieve_simulate_from_ohlcv(
        df,
        stop_before_target_same_bar=True,
        one_minute_df=df1m,
        require_reentry_close=args.legacy_reentry,
    )
    sieve_s = time.perf_counter() - t1
    s0 = _summarize_sieve(tr_cons)

    print("litmus  preset=morning_range  (sieve only, not backtest_executor)")
    print(f"  csv:       {args.csv.resolve()}")
    if df1m is not None:
        print(f"  1m_csv:    {args.csv_1m.resolve()}  rows={len(df1m)}")
    print(f"  rows:      {len(df)}  load_s={load_s:.2f}  sieve_s={sieve_s:.2f}")
    print(
        f"  window:    last_days={last_days_eff!r}  start={args.start!r}  end={args.end!r}"
    )
    print(
        f"  conservative: trades={s0['n']}  win_rate={s0['win_rate']:.4f}  "
        f"session_days_with_trade={s0['sessions']}"
    )

    if args.compare_intrabar:
        tr_opt = sieve_simulate_from_ohlcv(
            df,
            stop_before_target_same_bar=False,
            one_minute_df=df1m,
            require_reentry_close=args.legacy_reentry,
        )
        s1 = _summarize_sieve(tr_opt)
        print(
            f"  optimistic:   trades={s1['n']}  win_rate={s1['win_rate']:.4f}  "
            f"session_days_with_trade={s1['sessions']}"
        )

    if not args.compare_intrabar:
        print("  hint: --compare-intrabar to see TP-first vs stop-first same-bar resolution")
    print("  full history: scripts/validate_morning_range_reversion.py (same sieve, wider window)")
    return 0


PRESETS = {
    "morning_range": cmd_morning_range,
}


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Fast litmus checks for strategy ideas (narrow CSV window)."
    )
    ap.add_argument(
        "preset",
        choices=sorted(PRESETS),
        help="Which litmus to run",
    )
    ap.add_argument("--csv", type=Path, default=None, help="OHLCV CSV path")
    ap.add_argument(
        "--1m-csv",
        dest="csv_1m",
        type=Path,
        default=None,
        help="(morning_range) optional 1m CSV for intrabar TP vs SL resolution",
    )
    ap.add_argument("--symbol", default="MNQ", help="Loader label (default MNQ)")
    ap.add_argument("--start", default=None, help="Inclusive YYYY-MM-DD")
    ap.add_argument("--end", default=None, help="Exclusive YYYY-MM-DD")
    ap.add_argument(
        "--last-days",
        type=int,
        default=90,
        metavar="N",
        help="Keep rows with index >= (max_ts - N days) after load; default 90. "
        "Use 0 to skip clipping (full CSV, then optional --start/--end only). "
        "Ignored if --start or --end is set.",
    )
    ap.add_argument(
        "--compare-intrabar",
        action="store_true",
        help="(morning_range) run sieve twice: conservative vs optimistic same-bar",
    )
    ap.add_argument(
        "--why",
        action="store_true",
        help="(morning_range) print why third-party stats may differ; can combine with --csv",
    )
    ap.add_argument(
        "--legacy-reentry",
        action="store_true",
        help="(morning_range) wait for close inside range before arming (legacy sieve).",
    )
    args = ap.parse_args()
    return PRESETS[args.preset](args)


if __name__ == "__main__":
    raise SystemExit(main())
