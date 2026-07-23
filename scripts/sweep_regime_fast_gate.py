#!/usr/bin/env python3
"""Parallel parameter sweep for ``core/regime_fast_gate`` on 450d MRR replay.

Scores configs by endurance (gated PnL / max DD × retention). Outputs JSON +
console Pareto summary.

Usage::

    .venv/bin/python scripts/sweep_regime_fast_gate.py
    .venv/bin/python scripts/sweep_regime_fast_gate.py --jobs 8
"""

from __future__ import annotations

import argparse
import itertools
import json
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.regime_fast_gate_replay import evaluate_trial, load_mrr_trades


def build_grid(dll_usd: float) -> List[Dict[str, Any]]:
    rolling_windows = [3, 5, 7]
    rolling_mean_maxes = [-400, -300, -200, -150, -100, 0]
    throttle_durations = [3, 5, 7]
    session_halts: List[Tuple[bool, float | None]] = [
        (False, None),
        (True, dll_usd * 0.35),
        (True, dll_usd * 0.40),
        (True, dll_usd * 0.50),
        (True, dll_usd * 0.60),
    ]
    weekly_mgc: List[Tuple[bool, float | None]] = [
        (False, None),
        (True, dll_usd * 0.25),
        (True, dll_usd * 0.30),
        (True, dll_usd * 0.40),
        (True, dll_usd * 0.50),
    ]

    grid: List[Dict[str, Any]] = []
    for rw, rmax, td, (sh_en, sh_usd), (wm_en, wm_usd) in itertools.product(
        rolling_windows,
        rolling_mean_maxes,
        throttle_durations,
        session_halts,
        weekly_mgc,
    ):
        grid.append({
            "dll_usd": dll_usd,
            "rolling_window": rw,
            "rolling_mean_max": float(rmax),
            "throttle_duration": td,
            "session_halt_enabled": sh_en,
            "session_halt_usd": sh_usd if sh_usd is not None else dll_usd * 0.40,
            "weekly_mgc_enabled": wm_en,
            "weekly_mgc_loss_usd": wm_usd if wm_usd is not None else dll_usd * 0.30,
            "throttle_mult": 0.5,
        })

    # Baseline: gate fully off (no rules fire)
    grid.append({
        "dll_usd": dll_usd,
        "rolling_window": 3,
        "rolling_mean_max": -1e9,
        "throttle_duration": 0,
        "session_halt_enabled": False,
        "session_halt_usd": dll_usd,
        "weekly_mgc_enabled": False,
        "weekly_mgc_loss_usd": dll_usd,
        "throttle_mult": 0.5,
    })
    return grid


def _worker(payload: Tuple[list, Dict[str, Any]]) -> Dict[str, Any]:
    rows, params = payload
    return evaluate_trial(rows, params)


def _label(p: Dict[str, Any]) -> str:
    sh = "off" if not p.get("session_halt_enabled") else f"${p['session_halt_usd']:.0f}"
    wm = "off" if not p.get("weekly_mgc_enabled") else f"${p['weekly_mgc_loss_usd']:.0f}"
    return (
        f"roll={p['rolling_window']} max={p['rolling_mean_max']:.0f} "
        f"dur={p['throttle_duration']} halt={sh} wmgc={wm}"
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="Parallel sweep regime fast gate params")
    ap.add_argument(
        "--walkforward-dir",
        type=Path,
        default=Path("docs/perf/regime_longspan_450d"),
    )
    ap.add_argument("--dll-usd", type=float, default=1000.0)
    ap.add_argument("--jobs", type=int, default=0)
    ap.add_argument(
        "--out",
        type=Path,
        default=Path("docs/perf/regime_fast_gate_sweep/sweep_results.json"),
    )
    args = ap.parse_args()

    rows = load_mrr_trades(args.walkforward_dir)
    if not rows:
        print("No trades")
        return 1

    grid = build_grid(args.dll_usd)
    jobs = args.jobs or min(8, max(1, (__import__("os").cpu_count() or 4) - 1))
    payloads = [(rows, p) for p in grid]

    print(f"Sweeping {len(grid)} configs with {jobs} workers (DLL=${args.dll_usd:,.0f})...")
    results: List[Dict[str, Any]] = []
    with ProcessPoolExecutor(max_workers=jobs) as pool:
        futs = {pool.submit(_worker, pl): pl[1] for pl in payloads}
        done = 0
        for fut in as_completed(futs):
            results.append(fut.result())
            done += 1
            if done % 100 == 0 or done == len(grid):
                print(f"  {done}/{len(grid)}")

    baseline = next(
        (r for r in results if not r["params"]["session_halt_enabled"]
         and not r["params"]["weekly_mgc_enabled"]
         and r["params"]["rolling_mean_max"] <= -1e8),
        None,
    )
    b_m = baseline["metrics"] if baseline else {}

    # Rank: endurance_score_2026 primary, then dd_reduction_2026, then pct_retained_2026
    ranked = sorted(
        results,
        key=lambda r: (
            r["metrics"]["endurance_score_2026"] or 0,
            r["metrics"]["dd_reduction_2026"],
            r["metrics"]["pct_retained_2026"] or 0,
        ),
        reverse=True,
    )

    # Pareto on 2026: maximize retained + dd_reduction
    pareto: List[Dict[str, Any]] = []
    for r in ranked:
        m = r["metrics"]
        dominated = False
        for o in ranked:
            om = o["metrics"]
            if (
                (om["pct_retained_2026"] or 0) >= (m["pct_retained_2026"] or 0)
                and om["dd_reduction_2026"] >= m["dd_reduction_2026"]
                and (
                    (om["pct_retained_2026"] or 0) > (m["pct_retained_2026"] or 0)
                    or om["dd_reduction_2026"] > m["dd_reduction_2026"]
                )
            ):
                dominated = True
                break
        if not dominated:
            pareto.append(r)

    # Recommended: best endurance_score_2026 with pct_retained_2026 >= 92
    candidates = [
        r for r in ranked
        if (r["metrics"]["pct_retained_2026"] or 0) >= 92.0
        and r["metrics"]["dd_reduction_2026"] >= 100
    ]
    recommended = candidates[0] if candidates else ranked[0]

    default_cfg = next(
        (r for r in results
         if r["params"]["rolling_window"] == 3
         and r["params"]["rolling_mean_max"] == 0
         and r["params"]["throttle_duration"] == 5
         and r["params"]["session_halt_enabled"]
         and abs(r["params"]["session_halt_usd"] - args.dll_usd * 0.40) < 1
         and r["params"]["weekly_mgc_enabled"]
         and abs(r["params"]["weekly_mgc_loss_usd"] - args.dll_usd * 0.30) < 1),
        None,
    )

    report = {
        "meta": {
            "n_trials": len(results),
            "dll_usd": args.dll_usd,
            "source": str(args.walkforward_dir),
            "baseline_metrics": b_m,
        },
        "recommended": {
            "params": recommended["params"],
            "metrics": recommended["metrics"],
            "label": _label(recommended["params"]),
        },
        "default_config": {
            "params": default_cfg["params"] if default_cfg else None,
            "metrics": default_cfg["metrics"] if default_cfg else None,
        },
        "top10_endurance_2026": [
            {"label": _label(r["params"]), "params": r["params"], "metrics": r["metrics"]}
            for r in ranked[:10]
        ],
        "pareto_2026": [
            {"label": _label(r["params"]), "params": r["params"], "metrics": r["metrics"]}
            for r in pareto[:15]
        ],
        "all_results": [
            {"params": r["params"], "metrics": r["metrics"]} for r in ranked
        ],
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print("\n" + "=" * 72)
    print(f"BASELINE (no gate): PnL ${b_m.get('baseline_pnl', 0):,.0f}  "
          f"DD ${b_m.get('max_dd_baseline', 0):,.0f}  "
          f"2026 ${b_m.get('baseline_pnl_2026', 0):,.0f}")
    if default_cfg:
        dm = default_cfg["metrics"]
        print(f"\nDEFAULT (roll3/0/5 halt40%/wmgc30%): "
              f"retained={dm['pct_retained']:.1f}%  "
              f"2026 retained={dm['pct_retained_2026']:.1f}%  "
              f"cost=${dm['pnl_cost']:,.0f}  DD saved=${dm['dd_reduction']:,.0f}")
    rec = recommended["metrics"]
    print(f"\nRECOMMENDED: {_label(recommended['params'])}")
    print(f"  Total: ${rec['gated_pnl']:,.0f} ({rec['pct_retained']:.1f}% retained)  "
          f"cost=${rec['pnl_cost']:,.0f}  DD ${rec['max_dd_gated']:,.0f} "
          f"(saved ${rec['dd_reduction']:,.0f})")
    print(f"  2026: ${rec['gated_pnl_2026']:,.0f} ({rec['pct_retained_2026']:.1f}% retained)  "
          f"DD saved ${rec['dd_reduction_2026']:,.0f}  "
          f"worst wk ${rec['worst_week_gated']:,.0f}")
    print(f"  endurance_score_2026={rec['endurance_score_2026']:.2f}  "
          f"blocked={rec['blocked_trades']} throttled={rec['throttled_trades']}")

    print("\nTOP 10 (endurance_score_2026):")
    for i, r in enumerate(ranked[:10], 1):
        m = r["metrics"]
        print(
            f"  {i:2}. {_label(r['params'])[:55]:55}  "
            f"26ret={m['pct_retained_2026']:5.1f}%  "
            f"DD−=${m['dd_reduction_2026']:5.0f}  "
            f"cost=${m['pnl_cost_2026']:5.0f}  "
            f"sc={m['endurance_score_2026']:5.2f}"
        )
    print(f"\nWrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
