#!/usr/bin/env python3
"""Coordinate-descent optimization harness for ``morning_range_reversion``.

Runs ``scripts/walkforward_trade_recap_report.py`` with sets of ``--env`` overrides
(StrategyConfig precedence: CLI > env > TOML > default), parses each trial's
``metrics_insights.json``, ranks by a composite objective, and picks the round
winner. Designed to be called once per round; the orchestrator outside this
script (the chat agent) accumulates winners across rounds.

Usage:
    .venv/bin/python scripts/optimize_morning_range.py \\
        --round-label round1_sl_tp \\
        --days 90 --folds 6 --symbols MNQ,MES,MGC \\
        --max-parallel 5 \\
        --trials-json trials.json \\
        --results-out docs/perf/_opt_runs/round1.json

Trials file format (JSON list of dicts)::

    [
        {"label": "baseline",          "env": {}},
        {"label": "sl0.75_tp1.25",     "env": {"MORNING_RANGE_REVERSION_SIGNAL_SL_MULT": "0.75",
                                                 "MORNING_RANGE_REVERSION_SIGNAL_TP_MULT": "1.25"}},
        ...
    ]

Objective (composite score): ``final_equity * recovery_factor`` ; ties broken
by higher actual_win_rate. Negative recovery factor → score = final_equity / 10
(penalty so a -RF combo can still rank but never beats a +RF combo with similar
return). Trades below ``--min-trades`` get score = -inf to discourage parameter
sets that simply stop trading.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import shutil
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


# ───────────────────────────────────────────────────────────────────────────


@dataclass
class TrialResult:
    label: str
    env: Dict[str, str]
    out_dir: Path
    duration_s: float
    metrics: Dict[str, Any] = field(default_factory=dict)
    score: float = float("-inf")
    error: Optional[str] = None

    def as_row(self) -> Dict[str, Any]:
        m = self.metrics
        gs = m.get("grand", {}).get("equity_summary", {})
        gx = m.get("grand", {}).get("extended", {})
        return {
            "label": self.label,
            "score": round(self.score, 2),
            "n_trades": gs.get("n_trades", 0),
            "final_equity": gs.get("final_equity", 0),
            "total_return_pct": gs.get("total_return_pct", 0),
            "max_dd_pct": gs.get("max_drawdown_pct", 0),
            "recovery_factor": gx.get("recovery_factor_pnl_vs_seq_dd", 0),
            "actual_win_rate": gx.get("actual_win_rate", 0),
            "duration_s": round(self.duration_s, 1),
            "env": self.env,
            "out_dir": str(self.out_dir),
        }


# ───────────────────────────────────────────────────────────────────────────
# Composite scoring
# ───────────────────────────────────────────────────────────────────────────


def composite_score(metrics: Dict[str, Any], min_trades: int) -> float:
    g = metrics.get("grand", {})
    es = g.get("equity_summary", {}) or {}
    ex = g.get("extended", {}) or {}
    n = int(es.get("n_trades", 0) or 0)
    if n < min_trades:
        return float("-inf")
    fin = float(es.get("final_equity", 0.0) or 0.0)
    rf = float(ex.get("recovery_factor_pnl_vs_seq_dd", 0.0) or 0.0)
    if rf >= 0:
        return fin * rf
    # Negative recovery factor → soft penalty, never beats a +RF combo with similar fin.
    return fin / 10.0 + rf * 100.0  # rf pulls it down further as it gets more negative


# ───────────────────────────────────────────────────────────────────────────
# Subprocess runner with ETA reporting
# ───────────────────────────────────────────────────────────────────────────


async def _run_trial(
    trial: Dict[str, Any],
    *,
    days: int,
    folds: int,
    symbols: str,
    out_base: Path,
    sem: asyncio.Semaphore,
) -> TrialResult:
    label = trial["label"]
    env_overrides = dict(trial.get("env", {}))
    out_dir = out_base / label
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Build CLI
    cmd = [
        ".venv/bin/python",
        "scripts/walkforward_trade_recap_report.py",
        "--days", str(days),
        "--folds", str(folds),
        "--symbols", symbols,
        "--strategies", "morning_range_reversion",
        "--last-trades", "1",  # min cap inside the script
        "--out-dir", str(out_dir),
    ]
    for k, v in env_overrides.items():
        cmd.extend(["--env", f"{k}={v}"])

    env = os.environ.copy()
    env["ENABLE_SIGNALR"] = "false"
    env["PYTHONUNBUFFERED"] = "1"

    async with sem:
        t0 = time.monotonic()
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
            cwd=str(ROOT),
        )
        stdout, _ = await proc.communicate()
        dur = time.monotonic() - t0
        if proc.returncode != 0:
            log_path = out_dir / "stdout.log"
            log_path.write_bytes(stdout or b"")
            return TrialResult(
                label=label, env=env_overrides, out_dir=out_dir, duration_s=dur,
                error=f"exit={proc.returncode}; see {log_path}",
            )

    # Parse metrics
    insights_path = out_dir / "metrics_insights.json"
    if not insights_path.exists():
        return TrialResult(
            label=label, env=env_overrides, out_dir=out_dir, duration_s=dur,
            error=f"missing {insights_path}",
        )
    with open(insights_path) as f:
        metrics = json.load(f)
    return TrialResult(
        label=label, env=env_overrides, out_dir=out_dir, duration_s=dur, metrics=metrics,
    )


def _fmt_dur(s: float) -> str:
    if s < 60:
        return f"{s:.1f}s"
    m, sec = divmod(s, 60)
    if m < 60:
        return f"{int(m)}m{int(sec):02d}s"
    h, mr = divmod(int(m), 60)
    return f"{h}h{mr:02d}m"


async def _progress_monitor(
    tasks: List[asyncio.Task],
    *,
    n_total: int,
    started_at: float,
    label: str,
) -> None:
    """Logs ETA every 30s once total wall-clock exceeds 5 minutes.
    Cancelled when all trials complete."""
    while True:
        await asyncio.sleep(30)
        n_done = sum(1 for t in tasks if t.done())
        elapsed = time.monotonic() - started_at
        if elapsed < 300:
            # Quiet for the first 5 minutes.
            continue
        if n_done == 0:
            eta = "unknown"
        else:
            rate = n_done / elapsed
            remaining = max(0, n_total - n_done)
            eta_s = remaining / rate if rate > 0 else 0
            eta = _fmt_dur(eta_s)
        print(
            f"   ⏱  [{label}] {n_done}/{n_total} done, "
            f"elapsed={_fmt_dur(elapsed)}, ETA≈{eta}",
            flush=True,
        )


async def run_trials_async(
    trials: List[Dict[str, Any]],
    *,
    days: int,
    folds: int,
    symbols: str,
    out_base: Path,
    max_parallel: int,
    round_label: str,
) -> List[TrialResult]:
    sem = asyncio.Semaphore(max_parallel)
    started = time.monotonic()
    tasks = [
        asyncio.create_task(
            _run_trial(
                t, days=days, folds=folds, symbols=symbols,
                out_base=out_base, sem=sem,
            )
        )
        for t in trials
    ]
    mon = asyncio.create_task(
        _progress_monitor(
            tasks, n_total=len(trials), started_at=started, label=round_label,
        )
    )
    try:
        results = await asyncio.gather(*tasks)
    finally:
        mon.cancel()
        try:
            await mon
        except asyncio.CancelledError:
            pass
    return results


# ───────────────────────────────────────────────────────────────────────────
# Main
# ───────────────────────────────────────────────────────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--round-label", required=True)
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--folds", type=int, default=6)
    ap.add_argument("--symbols", type=str, default="MNQ,MES,MGC")
    ap.add_argument("--max-parallel", type=int, default=5)
    ap.add_argument("--min-trades", type=int, default=10,
                    help="Trials with fewer total trades get -inf score "
                         "(penalises parameter sets that silently stop trading).")
    ap.add_argument("--trials-json", type=Path, required=True,
                    help="JSON list of {label,env} trials.")
    ap.add_argument("--out-base", type=Path,
                    default=ROOT / "docs" / "perf" / "_opt_runs")
    ap.add_argument("--results-out", type=Path, required=True,
                    help="Where to write the round summary JSON.")
    args = ap.parse_args()

    with open(args.trials_json) as f:
        trials = json.load(f)
    if not isinstance(trials, list):
        print("error: --trials-json must be a JSON list", file=sys.stderr)
        return 2

    out_base = (args.out_base if args.out_base.is_absolute() else ROOT / args.out_base) / args.round_label
    out_base.mkdir(parents=True, exist_ok=True)

    print(
        f"🚀 Running {len(trials)} trials [{args.round_label}]  "
        f"window={args.days}d folds={args.folds} symbols={args.symbols} "
        f"parallel={args.max_parallel}",
        flush=True,
    )

    started = time.monotonic()
    results = asyncio.run(
        run_trials_async(
            trials,
            days=args.days, folds=args.folds, symbols=args.symbols,
            out_base=out_base, max_parallel=args.max_parallel,
            round_label=args.round_label,
        )
    )
    total_elapsed = time.monotonic() - started

    # Score + rank
    for r in results:
        if r.error:
            continue
        r.score = composite_score(r.metrics, args.min_trades)
    ranked = sorted(results, key=lambda r: r.score, reverse=True)

    # Print table
    print()
    print(f"📊 [{args.round_label}] complete in {_fmt_dur(total_elapsed)}")
    print("-" * 130)
    print(f"{'rank':>4} {'label':<35} {'score':>10} {'n':>5} {'ret%':>8} {'dd%':>7} {'rf':>6} {'wr':>6} {'dur':>7}")
    print("-" * 130)
    for i, r in enumerate(ranked):
        row = r.as_row()
        if r.error:
            print(f"{i+1:>4} {r.label:<35} ERROR: {r.error[:80]}")
            continue
        print(
            f"{i+1:>4} {row['label']:<35} "
            f"{row['score']:>10.1f} "
            f"{row['n_trades']:>5} "
            f"{row['total_return_pct']:>8.2f} "
            f"{row['max_dd_pct']:>7.2f} "
            f"{row['recovery_factor']:>6.2f} "
            f"{row['actual_win_rate']:>6.2%} "
            f"{_fmt_dur(row['duration_s']):>7}"
        )

    # Write summary JSON for the orchestrator (chat agent)
    summary = {
        "round_label": args.round_label,
        "window_days": args.days,
        "folds": args.folds,
        "symbols": args.symbols,
        "started_iso": datetime.now(timezone.utc).isoformat(),
        "total_elapsed_s": total_elapsed,
        "ranked": [r.as_row() for r in ranked],
        "winner": ranked[0].as_row() if ranked and ranked[0].score > float("-inf") else None,
    }
    args.results_out.parent.mkdir(parents=True, exist_ok=True)
    args.results_out.write_text(json.dumps(summary, indent=2))
    print(f"\n📝 Wrote summary → {args.results_out}")
    print(f"   Winner: {summary['winner']['label'] if summary['winner'] else 'NONE'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
