#!/usr/bin/env python3
"""
Alpha Discovery CLI

Scans historical bar data for statistically significant signal conditions
that predict profitable overnight-range breakout trades.

Usage:
    # From a pre-exported CSV (recommended)
    python scripts/alpha_discovery.py --symbol MNQ --csv historical_data/MNQ_5m.csv

    # Pull fresh data from TopStepX API (needs .env credentials)
    python scripts/alpha_discovery.py --symbol MNQ --days 180 --mode api

    # Out-of-sample check: hypothesis tests on first 70% / last 30% of *sessions*
    # (features + sim use full bar history so daily ATR context is preserved)
    python scripts/alpha_discovery.py --symbol MNQ --csv historical_data/MNQ_5m.csv --oos-split 0.3

    # Custom SL/TP (override TOML defaults for sensitivity testing)
    python scripts/alpha_discovery.py --symbol MNQ --csv historical_data/MNQ_5m.csv --stop-atr 1.0 --tp-atr 2.5

    # Save report to file
    python scripts/alpha_discovery.py --symbol MNQ --csv historical_data/MNQ_5m.csv \\
        --output docs/alpha/MNQ_180d.md --json-output docs/alpha/MNQ_180d.json

    # Multi-symbol (runs each independently)
    python scripts/alpha_discovery.py --symbol MNQ MES MGC --csv historical_data/MNQ_5m.csv historical_data/MES_5m.csv historical_data/MGC_5m.csv
"""

import sys
import os
import argparse
import asyncio
import json
import logging
from pathlib import Path
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
import numpy as np

from core.alpha import AlphaScanner, AlphaReport
from core.backtest.data_loader import HistoricalDataLoader

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("alpha_discovery")

# Suppress noisy sub-loggers
for name in ("core.alpha.features", "core.alpha.hypothesis", "core.alpha.scanner"):
    logging.getLogger(name).setLevel(logging.WARNING)


def _load_csv(path: str, symbol: str) -> pd.DataFrame:
    loader = HistoricalDataLoader()
    df = loader.load_from_csv(path, symbol)
    logger.info(f"Loaded {len(df):,} bars from {path} ({df.index[0]} → {df.index[-1]})")
    return df


async def _load_api(symbol: str, days: int, timeframe: str) -> pd.DataFrame:
    from dotenv import load_dotenv
    load_dotenv()

    from brokers.topstepx_adapter import TopStepXAdapter
    from core.auth import AuthManager

    api_key  = os.getenv("PROJECT_X_API_KEY") or os.getenv("TOPSTEPX_API_KEY") or ""
    username = os.getenv("PROJECT_X_USERNAME") or os.getenv("TOPSTEPX_USERNAME") or ""
    if not api_key or not username:
        sys.exit("ERROR: PROJECT_X_API_KEY and PROJECT_X_USERNAME required for --mode api")

    adapter = TopStepXAdapter(api_key=api_key, username=username)
    await adapter.initialize()

    end   = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    loader = HistoricalDataLoader(broker_adapter=adapter)
    df = await loader.load_from_api(symbol=symbol, timeframe=timeframe,
                                     start_date=start, end_date=end)
    logger.info(f"Loaded {len(df):,} bars from API ({df.index[0]} → {df.index[-1]})")
    return df


def _run_one(
    symbol: str,
    bars: pd.DataFrame,
    args,
    is_is: bool = True,
    oos_label: str = "",
) -> tuple[AlphaScanner, list]:
    """Run scanner on one bar DataFrame; return (scanner, results)."""
    scanner = AlphaScanner(
        symbol=symbol,
        stop_atr_mult=args.stop_atr,
        tp_atr_mult=args.tp_atr,
        quantity=args.quantity,
        slippage_pts=args.slippage,
        atr_period=args.atr_period,
    )
    label = f"{symbol} {'[IS]' if is_is else '[OOS]'}{oos_label}"
    logger.info(f"=== Scanning {label} ({len(bars):,} bars) ===")
    results = scanner.run(bars)
    n_sig = sum(1 for r in results if r.significant)
    logger.info(f"    → {len(results)} features tested, {n_sig} significant after FDR")
    return scanner, results


def _print_report(
    scanner: AlphaScanner,
    results: list,
    symbol: str,
    days: int,
    args,
    out_path: str | None,
    json_path: str | None,
    merged_for_stats: pd.DataFrame | None = None,
) -> None:
    md = merged_for_stats if merged_for_stats is not None else scanner.merged_df
    stats = scanner.summary_stats(merged_for_stats)
    n_sessions = md["session_date"].nunique() if md is not None and not md.empty else 0
    n_trades = len(md) if md is not None else 0

    report = AlphaReport(
        symbol=symbol,
        days=days,
        n_sessions=n_sessions,
        n_trades=n_trades,
        summary=stats,
        stop_atr=args.stop_atr,
        tp_atr=args.tp_atr,
    )

    md = report.to_markdown(results)
    jd = report.to_json(results)

    if out_path:
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_text(md)
        logger.info(f"Markdown report → {out_path}")
    else:
        print("\n" + md)

    if json_path:
        Path(json_path).parent.mkdir(parents=True, exist_ok=True)
        Path(json_path).write_text(json.dumps(jd, indent=2))
        logger.info(f"JSON report → {json_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Alpha discovery for overnight_range strategy")

    parser.add_argument("--symbol", nargs="+", default=["MNQ"],
                        help="Symbol(s) to scan (default: MNQ)")
    parser.add_argument("--csv", nargs="+", default=[],
                        help="Path(s) to CSV file(s). One per symbol, or one shared file.")
    parser.add_argument("--mode", choices=["csv", "api"], default="csv",
                        help="Data source (default: csv)")
    parser.add_argument("--days", type=int, default=180,
                        help="Days of history to pull (api mode only, default: 180)")
    parser.add_argument("--timeframe", default="5m",
                        help="Bar timeframe (api mode only, default: 5m)")

    # Strategy sim params
    parser.add_argument("--stop-atr", type=float, default=1.25,
                        help="Stop-loss ATR multiplier (default: 1.25, matches overnight_range.toml)")
    parser.add_argument("--tp-atr", type=float, default=2.0,
                        help="Take-profit ATR multiplier (default: 2.0)")
    parser.add_argument("--quantity", type=int, default=1,
                        help="Contracts per trade (default: 1)")
    parser.add_argument("--slippage", type=float, default=0.25,
                        help="Slippage per side in points (default: 0.25)")
    parser.add_argument("--atr-period", type=int, default=14,
                        help="ATR period on daily bars (default: 14)")

    # OOS validation
    parser.add_argument(
        "--oos-split",
        type=float,
        default=0.0,
        help="OOS fraction of *sessions* (not bars) for IC comparison [0–0.5]. "
        "Features + simulation always use the full CSV; only hypothesis tests are split. 0 = disabled.",
    )

    # Output
    parser.add_argument("--output", default=None,
                        help="Path for markdown report (default: stdout)")
    parser.add_argument("--json-output", default=None,
                        help="Path for JSON report (default: none)")
    parser.add_argument("--verbose", action="store_true",
                        help="Show DEBUG logging from scanner internals")

    args = parser.parse_args()

    if args.verbose:
        for name in ("core.alpha.features", "core.alpha.hypothesis", "core.alpha.scanner"):
            logging.getLogger(name).setLevel(logging.DEBUG)

    symbols  = args.symbol
    csv_paths = args.csv

    # Validate inputs
    if args.mode == "csv" and not csv_paths:
        parser.error("--csv required when --mode csv")
    if args.mode == "csv" and len(csv_paths) not in (1, len(symbols)):
        parser.error("--csv must supply one path (shared) or one path per symbol")

    for sym_idx, symbol in enumerate(symbols):
        symbol = symbol.upper()

        # Load bars
        if args.mode == "csv":
            csv_path = csv_paths[0] if len(csv_paths) == 1 else csv_paths[sym_idx]
            bars = _load_csv(csv_path, symbol)
        else:
            bars = asyncio.run(_load_api(symbol, args.days, args.timeframe))

        days = int((bars.index[-1] - bars.index[0]).days) + 1

        # Determine output paths for this symbol
        out_path  = args.output
        json_path = args.json_output
        if len(symbols) > 1:
            # Auto-suffix multi-symbol outputs
            if out_path:
                p = Path(out_path)
                out_path = str(p.parent / f"{p.stem}_{symbol}{p.suffix}")
            if json_path:
                p = Path(json_path)
                json_path = str(p.parent / f"{p.stem}_{symbol}{p.suffix}")

        if args.oos_split > 0:
            scanner = AlphaScanner(
                symbol=symbol,
                stop_atr_mult=args.stop_atr,
                tp_atr_mult=args.tp_atr,
                quantity=args.quantity,
                slippage_pts=args.slippage,
                atr_period=args.atr_period,
            )
            logger.info(
                f"=== Scanning {symbol} (full history: {len(bars):,} bars; "
                f"OOS split by session after merge) ==="
            )
            scanner.run(bars)
            if scanner.merged_df is None or scanner.merged_df.empty:
                logger.warning("No merged trades — skipping IS/OOS reports")
                continue

            md = scanner.merged_df
            session_dates = sorted(md["session_date"].unique())
            split_i = int(len(session_dates) * (1 - args.oos_split))
            if split_i <= 0:
                split_i = 1
            if split_i >= len(session_dates):
                logger.warning(
                    "OOS session window empty for this split; use a smaller --oos-split or more data"
                )
                _print_report(scanner, scanner.raw_results, symbol, days, args, out_path, json_path)
                continue

            is_dates = set(session_dates[:split_i])
            oos_dates = set(session_dates[split_i:])
            is_merged = md[md["session_date"].isin(is_dates)]
            oos_merged = md[md["session_date"].isin(oos_dates)]

            logger.info(
                f"IS: {len(is_dates)} sessions, {len(is_merged)} trades "
                f"({session_dates[0].date()} → {session_dates[split_i - 1].date()})"
            )
            logger.info(
                f"OOS: {len(oos_dates)} sessions, {len(oos_merged)} trades "
                f"({session_dates[split_i].date()} → {session_dates[-1].date()})"
            )

            is_results = scanner.hypothesis_battery(is_merged)
            oos_results = scanner.hypothesis_battery(oos_merged)

            _print_report(
                scanner,
                is_results,
                symbol,
                days,
                args,
                out_path=_insert_label(out_path, "_IS"),
                json_path=_insert_label(json_path, "_IS"),
                merged_for_stats=is_merged,
            )
            _print_report(
                scanner,
                oos_results,
                symbol,
                days,
                args,
                out_path=_insert_label(out_path, "_OOS"),
                json_path=_insert_label(json_path, "_OOS"),
                merged_for_stats=oos_merged,
            )

            _print_ic_comparison(is_results, oos_results, symbol)
        else:
            scanner, results = _run_one(symbol, bars, args)
            _print_report(scanner, results, symbol, days, args, out_path, json_path)


def _insert_label(path: str | None, label: str) -> str | None:
    if path is None:
        return None
    p = Path(path)
    return str(p.parent / f"{p.stem}{label}{p.suffix}")


def _print_ic_comparison(is_results: list, oos_results: list, symbol: str) -> None:
    """Print IS vs OOS IC comparison to detect overfitting."""
    oos_map = {r.feature: r for r in oos_results}
    print(f"\n{'='*60}")
    print(f"  IS → OOS IC Stability Check — {symbol}")
    print(f"{'='*60}")
    print(f"  {'Feature':<25} {'IS IC':>8}  {'OOS IC':>8}  {'Stable?'}")
    print(f"  {'-'*55}")
    for r in is_results:
        oos_r = oos_map.get(r.feature)
        oos_ic = oos_r.ic if oos_r else float("nan")
        stable = "✅" if (abs(r.ic) > 0.05 and abs(oos_ic) > 0.02
                         and r.ic * oos_ic > 0) else "⚠️"
        print(f"  {r.feature:<25} {r.ic:>+8.3f}  {oos_ic:>+8.3f}  {stable}")
    print(f"{'='*60}\n")
    print("Unstable IC (sign flip IS→OOS) = likely overfit. Do NOT act on those signals.")


if __name__ == "__main__":
    main()
