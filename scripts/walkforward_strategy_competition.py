#!/usr/bin/env python3
"""Walk-forward replay matrix: compare strategies on the same calendar folds.

Runs ``core/backtest_executor.py --replay`` per (strategy, symbol, fold), then
aggregates PnL / trades / Sharpe-style headline metrics. Intended for canonical
``historical_data/price/{SYM}_5m_databento.csv`` (naive UTC index).

Example::

  ENABLE_SIGNALR=false .venv/bin/python scripts/walkforward_strategy_competition.py \\
    --days 100 --folds 5 --timeframe 5m \\
    --strategies body_reversion,morning_range_reversion,overnight_range \\
    --symbols MNQ,MES,MGC

  # With strategy TOML overrides (same env keys as live ``StrategyConfig``):

  ENABLE_SIGNALR=false .venv/bin/python scripts/walkforward_strategy_competition.py \\
    --days 100 --folds 5 --out-dir docs/perf/walkforward_wf_morn_tp07 \\
    --strategies morning_range_reversion --symbols MNQ,MES,MGC \\
    --env MORNING_RANGE_REVERSION_SIGNAL_TP_MULT=0.7

  # Core arsenal + ``ema_stack_trend_15m`` (120d, same fold calendar; 5m CSV → 15m resample in replay):

  ENABLE_SIGNALR=false .venv/bin/python scripts/walkforward_strategy_competition.py \\
    --days 120 --folds 5 --timeframe 5m \\
    --strategies overnight_range,overnight_reversion,morning_range_reversion,body_reversion,ema_stack_trend_15m \\
    --symbols MNQ,MES,MGC \\
    --out-dir docs/perf/walkforward_eval_arsenal_core_120d5f_with_ema

Outputs under ``docs/perf/walkforward_competition/`` (override with ``--out-dir``):
  - ``summary.tsv`` — one row per (strategy, symbol, fold)
  - ``leaderboard.md`` — ranked by sum of fold PnL per (strategy, symbol)
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent


def _csv_last_date(csv_path: Path) -> date:
    """Last calendar date of the bar-open timestamp column."""
    import pandas as pd

    df = pd.read_csv(csv_path)
    ts_col = next(
        c for c in df.columns if str(c).lower() in ("timestamp", "time", "date", "datetime")
    )
    ts = pd.to_datetime(df[ts_col].iloc[-1])
    if getattr(ts, "tz", None) is not None:
        ts = ts.tz_convert("UTC").tz_localize(None)
    return pd.Timestamp(ts).date()


def _fold_ranges(
    anchor_end: date, *, total_days: int, folds: int
) -> List[Tuple[date, date, int]]:
    """Non-overlapping folds ending at anchor_end (inclusive-ish on bar data).

    Each fold is ``total_days // folds`` calendar days wide; the oldest fold
    starts first. Returns ``(start, end, fold_ix)`` with ``fold_ix`` 0=oldest.
    """
    width = max(1, total_days // folds)
    ranges: List[Tuple[date, date, int]] = []
    global_start = anchor_end - timedelta(days=total_days)
    for i in range(folds):
        fs = global_start + timedelta(days=i * width)
        fe = fs + timedelta(days=width - 1)
        if i == folds - 1:
            fe = anchor_end
        ranges.append((fs, fe, i))
    return ranges


def _run_one(
    *,
    strategy: str,
    symbol: str,
    csv_path: Path,
    start: date,
    end: date,
    timeframe: str,
    out_json: Path,
    extra_env: Optional[Dict[str, str]] = None,
) -> Tuple[bool, Dict[str, Any]]:
    out_json.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.setdefault("ENABLE_SIGNALR", "false")
    env.setdefault("PYTHONUNBUFFERED", "1")
    if extra_env:
        for k, v in extra_env.items():
            if k:
                env[str(k)] = str(v)
    cmd = [
        sys.executable,
        str(ROOT / "core" / "backtest_executor.py"),
        f"--strategy={strategy}",
        f"--symbol={symbol}",
        f"--timeframe={timeframe}",
        f"--csv={csv_path}",
        f"--start={start.isoformat()}",
        f"--end={end.isoformat()}",
        "--replay",
        "--format=json",
    ]
    log = out_json.with_suffix(out_json.suffix + ".run.log")
    with out_json.open("w", encoding="utf-8") as jf, log.open("w", encoding="utf-8") as lf:
        p = subprocess.run(cmd, cwd=str(ROOT), env=env, stdout=jf, stderr=lf, text=True)
    raw = out_json.read_text(encoding="utf-8", errors="replace").strip()
    if not raw:
        return False, {"error": "empty output", "exit": p.returncode}
    try:
        last_line = raw.splitlines()[-1]
        d = json.loads(last_line)
    except json.JSONDecodeError as e:
        return False, {"error": str(e), "exit": p.returncode, "tail": raw[-2000:]}
    if p.returncode != 0:
        d = d if isinstance(d, dict) else {}
        d["exit"] = p.returncode
        return False, d
    if not d.get("ok", True):
        return False, d
    return True, d


@dataclass
class FoldRow:
    strategy: str
    symbol: str
    fold: int
    start: date
    end: date
    total_pnl: float
    total_trades: int
    win_rate: float
    sharpe: float
    max_dd: float


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=100, help="Total calendar span ending at CSV last date")
    ap.add_argument("--folds", type=int, default=5, help="Number of non-overlapping walk-forward folds")
    ap.add_argument(
        "--strategies",
        type=str,
        default="body_reversion,morning_range_reversion,overnight_range",
        help="Comma-separated strategy ids",
    )
    ap.add_argument("--symbols", type=str, default="MNQ,MES,MGC", help="Comma-separated symbols")
    ap.add_argument("--timeframe", type=str, default="5m", help="Replay bar interval")
    ap.add_argument(
        "--csv-dir",
        type=Path,
        default=ROOT / "historical_data" / "price",
        help="Directory containing {SYM}_5m_databento.csv",
    )
    ap.add_argument(
        "--csv-template",
        type=str,
        default="{csv_dir}/{sym}_5m_databento.csv",
        help="Path template with {csv_dir} and {sym} (lower case sym)",
    )
    ap.add_argument(
        "--out-dir",
        type=Path,
        default=ROOT / "docs" / "perf" / "walkforward_competition",
        help="Output directory for JSON + TSV + markdown",
    )
    ap.add_argument("--dry-run", action="store_true", help="Print folds and exit without running backtests")
    ap.add_argument(
        "--env",
        action="append",
        default=[],
        metavar="KEY=VAL",
        help="Extra env var for each subprocess (repeatable). Example: --env MORNING_RANGE_REVERSION_SIGNAL_TP_MULT=0.7",
    )
    args = ap.parse_args()

    extra_env: Dict[str, str] = {}
    for raw in args.env or []:
        if "=" not in raw:
            print(f"error: --env must be KEY=VAL, got {raw!r}", file=sys.stderr)
            return 2
        k, v = raw.split("=", 1)
        extra_env[k.strip()] = v.strip()

    strategies = [s.strip() for s in args.strategies.split(",") if s.strip()]
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    out_dir: Path = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    csv_paths: Dict[str, Path] = {}
    anchor_dates: Dict[str, date] = {}
    for sym in symbols:
        p = Path(
            args.csv_template.format(csv_dir=str(args.csv_dir), sym=sym.lower(), SYM=sym)
        )
        if not p.is_file():
            print(f"error: missing CSV for {sym}: {p}", file=sys.stderr)
            return 2
        csv_paths[sym] = p
        anchor_dates[sym] = _csv_last_date(p)

    anchor_end = min(anchor_dates.values())
    folds = _fold_ranges(anchor_end, total_days=args.days, folds=args.folds)

    print(
        f"anchor_end={anchor_end} folds={len(folds)} width_days≈{args.days // args.folds} "
        f"strategies={strategies} symbols={symbols}",
        file=sys.stderr,
    )
    for fs, fe, ix in folds:
        print(f"  fold {ix}: {fs} .. {fe}", file=sys.stderr)

    if args.dry_run:
        return 0

    rows: List[FoldRow] = []
    for sym in symbols:
        csv_path = csv_paths[sym]
        for strat in strategies:
            for fs, fe, fold_ix in folds:
                tag = f"{strat}_{sym}_fold{fold_ix}_{fs}_{fe}"
                out_json = out_dir / "runs" / f"{tag}.json"
                ok, d = _run_one(
                    strategy=strat,
                    symbol=sym,
                    csv_path=csv_path,
                    start=fs,
                    end=fe,
                    timeframe=args.timeframe,
                    out_json=out_json,
                    extra_env=extra_env if extra_env else None,
                )
                if not ok:
                    err = d.get("error", d)
                    print(f"FAIL {strat} {sym} fold{fold_ix}: {err}", file=sys.stderr)
                    r: Dict[str, Any] = {}
                else:
                    r = d.get("result") or {}
                rows.append(
                    FoldRow(
                        strategy=strat,
                        symbol=sym,
                        fold=fold_ix,
                        start=fs,
                        end=fe,
                        total_pnl=float(r.get("total_pnl") or 0.0),
                        total_trades=int(r.get("total_trades") or 0),
                        win_rate=float(r.get("win_rate") or 0.0),
                        sharpe=float(r.get("sharpe_ratio") or 0.0),
                        max_dd=float(r.get("max_drawdown") or 0.0),
                    )
                )

    tsv = out_dir / "summary.tsv"
    with tsv.open("w", encoding="utf-8") as f:
        f.write(
            "strategy\tsymbol\tfold\tstart\tend\ttotal_pnl\ttotal_trades\twin_rate\tsharpe_ratio\tmax_drawdown\n"
        )
        for r in rows:
            f.write(
                f"{r.strategy}\t{r.symbol}\t{r.fold}\t{r.start}\t{r.end}\t{r.total_pnl:.2f}\t"
                f"{r.total_trades}\t{r.win_rate:.4f}\t{r.sharpe:.4f}\t{r.max_dd:.2f}\n"
            )

    agg: Dict[Tuple[str, str], Dict[str, Any]] = defaultdict(
        lambda: {
            "sum_pnl": 0.0,
            "sum_trades": 0,
            "wr_times_trades": 0.0,  # sum(win_rate_pct * n_trades) → trade-weighted WR %
            "folds_pos": 0,
            "n": 0,
            "sharpe_sum": 0.0,
        }
    )
    for r in rows:
        k = (r.strategy, r.symbol)
        a = agg[k]
        a["sum_pnl"] += r.total_pnl
        a["sum_trades"] += r.total_trades
        a["wr_times_trades"] += float(r.win_rate) * float(r.total_trades)
        a["sharpe_sum"] += r.sharpe
        a["n"] += 1
        if r.total_pnl > 0:
            a["folds_pos"] += 1

    ranked = sorted(
        agg.items(),
        key=lambda kv: kv[1]["sum_pnl"],
        reverse=True,
    )

    md = out_dir / "leaderboard.md"
    with md.open("w", encoding="utf-8") as f:
        f.write("# Walk-forward strategy competition\n\n")
        f.write(f"- **Bar timeframe:** {args.timeframe}\n")
        f.write(f"- **Span:** last **{args.days}** calendar days ending **{anchor_end}** (per-symbol CSV last date min)\n")
        f.write(f"- **Folds:** {args.folds} non-overlapping segments\n")
        f.write(f"- **Strategies:** {', '.join(strategies)}\n")
        f.write(f"- **Symbols:** {', '.join(symbols)}\n")
        if extra_env:
            f.write(f"- **Env overrides:** `{', '.join(f'{k}={v}' for k, v in sorted(extra_env.items()))}`\n")
        f.write("\n")
        f.write("## Leaderboard (by sum of fold total_pnl)\n\n")
        f.write(
            "Trade-weighted **win_rate** = Σ(fold_win_rate × fold_trades) / Σ(trades) "
            "(same scale as JSON `win_rate`, typically 0–100).\n\n"
        )
        f.write(
            "| rank | strategy | symbol | sum_pnl | total_trades | win_rate_pct | "
            "folds_green | mean_fold_sharpe |\n"
        )
        f.write("|---:|---|---|---:|---:|---:|---:|---:|\n")
        for i, ((st, sy), a) in enumerate(ranked, 1):
            mean_s = a["sharpe_sum"] / max(1, a["n"])
            ntr = int(a["sum_trades"])
            wr_pct = (a["wr_times_trades"] / float(ntr)) if ntr > 0 else 0.0
            f.write(
                f"| {i} | `{st}` | **{sy}** | {a['sum_pnl']:.2f} | {a['sum_trades']} | "
                f"{wr_pct:.2f} | {a['folds_pos']}/{a['n']} | {mean_s:.3f} |\n"
            )
        f.write("\n## Per-fold detail\n\n")
        f.write("See `summary.tsv` and `runs/*.json`.\n")

    print(f"wrote {tsv} and {md}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
