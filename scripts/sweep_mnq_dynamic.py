#!/usr/bin/env python3
"""Trial-by-trial TOML rewriter for MNQ ``sl_mult`` sweep.

Per-symbol overrides win over env vars in StrategyConfig precedence, so an
env-var sweep can't reach `[symbols.MNQ.signal].sl_mult`. This wrapper edits
the TOML between trials, runs the walkforward, snapshots metrics, and
restores the TOML on exit.

Usage::

    .venv/bin/python scripts/sweep_mnq_dynamic.py --days 270 --folds 9 \\
        --trials 1.0,1.25,1.5,2.0,2.5,3.0
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
TOML = ROOT / "config/strategies/morning_range_reversion.toml"


def patch_toml(sl_mult: float | None) -> str:
    """Return the original TOML contents (for restoration), and write patched.

    Patches the ``[symbols.MNQ.signal]`` block to use the dynamic ``sl_mult``
    (with ``sl_fixed_pts = 0``) instead of the fixed-pts variant. When
    ``sl_mult is None``, leaves the TOML alone (baseline trial)."""
    original = TOML.read_text()
    if sl_mult is None:
        return original

    block = (
        "[symbols.MNQ.signal]\n"
        "# MNQ dynamic-SL sweep (2026-05-29) replacing slfix=50 with sl_mult.\n"
        f"sl_fixed_pts = 0\n"
        f"sl_mult      = {sl_mult}\n"
        f"tp_mult      = 1.25\n"
    )
    pattern = re.compile(
        r"\[symbols\.MNQ\.signal\][^\[]*(?=(?:^\[)|\Z)",
        flags=re.MULTILINE | re.DOTALL,
    )
    patched, n = pattern.subn(block + "\n", original)
    if n != 1:
        raise RuntimeError(f"Failed to patch MNQ block (matches={n})")
    TOML.write_text(patched)
    return original


async def run_one(sl_mult: float | None, days: int, folds: int, out_dir: Path) -> dict:
    label = "baseline_slfix50" if sl_mult is None else f"mnq_slmult_{sl_mult}"
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / label
    original = patch_toml(sl_mult)
    try:
        env = os.environ.copy()
        env.setdefault("ENABLE_SIGNALR", "false")
        start = time.monotonic()
        cmd = [
            sys.executable, str(ROOT / "scripts/walkforward_trade_recap_report.py"),
            "--strategies", "morning_range_reversion",
            "--symbols", "MNQ,MES,MGC",
            "--days", str(days),
            "--folds", str(folds),
            "--out-dir", str(target),
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=str(ROOT), env=env,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        duration = time.monotonic() - start
        if proc.returncode != 0:
            print(f"  ❌ {label} failed:\n{stderr.decode()[:500]}")
            return {"label": label, "ok": False, "duration_s": duration}
        metrics = json.loads((target / "metrics_insights.json").read_text())
        return {"label": label, "ok": True, "duration_s": duration, "metrics": metrics}
    finally:
        TOML.write_text(original)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=270)
    ap.add_argument("--folds", type=int, default=9)
    ap.add_argument("--trials", default="1.0,1.25,1.5,2.0,2.5,3.0",
                    help="Comma-separated sl_mult values. Add 'baseline' to include slfix=50.")
    ap.add_argument("--max-parallel", type=int, default=3)
    ap.add_argument("--out-dir", default="docs/perf/_opt_runs/mnq_dynamic")
    args = ap.parse_args()

    trials: list[float | None] = []
    for t in args.trials.split(","):
        t = t.strip()
        if not t:
            continue
        trials.append(None if t == "baseline" else float(t))

    out_root = ROOT / args.out_dir
    print(f"🚀 MNQ dynamic-SL sweep — {len(trials)} trials, {args.days}d / {args.folds} folds")
    # NB: TOML mutation is serial. parallel runs would race each other on the
    # shared TOML file. Run sequentially to keep the TOML state consistent.
    results = []
    for i, sl_mult in enumerate(trials, 1):
        print(f"  [{i}/{len(trials)}] sl_mult={sl_mult}")
        r = await run_one(sl_mult, args.days, args.folds, out_root)
        results.append(r)
        if r["ok"]:
            ov = r["metrics"].get("overall_equity", {})
            print(f"    → ret={ov.get('total_return_pct', 0):+.2f}%  dd={ov.get('max_drawdown_pct', 0):.2f}%  ({r['duration_s']:.0f}s)")

    print("\n📊 Sweep complete")
    print(f"{'sl_mult':<14}{'ret%':>10}{'dd%':>8}{'rf':>7}{'wr':>9}{'mnq_ret%':>11}{'mnq_dd%':>11}")
    print("-" * 70)
    rows = []
    for r in results:
        if not r["ok"]:
            continue
        m = r["metrics"]
        ov = m.get("overall_equity", {})
        mnq_key = next((k for k in m["per_strategy_symbol"] if "MNQ" in k), None)
        mnq = m["per_strategy_symbol"][mnq_key]["equity_summary"] if mnq_key else {}
        mnq_x = m["per_strategy_symbol"][mnq_key]["extended"] if mnq_key else {}
        rows.append((r["label"], ov, mnq, mnq_x))
        print(
            f"{r['label']:<14}{ov.get('total_return_pct', 0):>+10.2f}{ov.get('max_drawdown_pct', 0):>8.2f}"
            f"{m['per_strategy_symbol'][next(iter(m['per_strategy_symbol']))]['extended'].get('recovery_factor_pnl_vs_seq_dd', 0):>7.2f}"
            f"{m['per_strategy_symbol'][next(iter(m['per_strategy_symbol']))]['extended'].get('actual_win_rate', 0):>8.2%}"
            f"{mnq.get('total_return_pct', 0):>+11.2f}{mnq.get('max_drawdown_pct', 0):>11.2f}"
        )


if __name__ == "__main__":
    asyncio.run(main())
