#!/usr/bin/env python3
"""Wave-based parameter sweeps for class-strategy CSV replay.

Each **wave** in a JSON manifest varies one conceptual knob (e.g. ATR SL/TP, overnight
session end, body threshold). Runs ``core/backtest_executor.py --replay --format=json``
with per-run **environment** overrides (same mechanism as live: ``StrategyConfig`` env
prefix + dotted key → ``OVERNIGHT_RANGE_SIGNAL_STOP_ATR_MULTIPLIER``, etc.).

Outputs (default ``docs/perf/parameter_sweeps/<run_id>/``):

- ``summary.tsv`` — wave, label, strategy, symbol, total_pnl, trades, win_rate_pct,
  avg_reward_risk, sharpe_ratio, max_drawdown
- ``by_wave.md`` — best row per wave (by total_pnl)
- ``results.jsonl`` — one JSON object per run (full executor payload)

Example::

  ENABLE_SIGNALR=false .venv/bin/python scripts/strategy_parameter_sweep.py \\
    --manifest config/perf_sweep/default_wave_manifest.json \\
    --run-id 20260512_atr \\
    --waves overnight_atr_bracket

See ``config/perf_sweep/default_wave_manifest.json`` for editable waves.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent


def _load_manifest(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _run_backtest_json(
    *,
    strategy: str,
    symbol: str,
    csv_path: Path,
    start: str,
    end: str,
    timeframe: str,
    extra_env: Dict[str, str],
) -> Tuple[bool, Dict[str, Any]]:
    env = os.environ.copy()
    env.setdefault("ENABLE_SIGNALR", "false")
    env.setdefault("PYTHONUNBUFFERED", "1")
    for k, v in extra_env.items():
        env[str(k)] = str(v)
    cmd = [
        sys.executable,
        str(ROOT / "core" / "backtest_executor.py"),
        f"--strategy={strategy}",
        f"--symbol={symbol}",
        f"--timeframe={timeframe}",
        f"--csv={csv_path}",
        f"--start={start}",
        f"--end={end}",
        "--replay",
        "--format=json",
    ]
    p = subprocess.run(cmd, cwd=str(ROOT), env=env, capture_output=True, text=True)
    raw = (p.stdout or "").strip()
    if not raw:
        return False, {"error": "empty stdout", "stderr": (p.stderr or "")[-4000:]}
    last = raw.splitlines()[-1]
    try:
        d = json.loads(last)
    except json.JSONDecodeError as e:
        return False, {"error": str(e), "tail": raw[-2000:]}
    if p.returncode != 0:
        d = d if isinstance(d, dict) else {}
        d["exit"] = p.returncode
        return False, d
    if not d.get("ok", True):
        return False, d
    return True, d


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        "--manifest",
        type=Path,
        default=ROOT / "config" / "perf_sweep" / "default_wave_manifest.json",
        help="JSON manifest path",
    )
    ap.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="Output directory (default: docs/perf/parameter_sweeps/<run-id>)",
    )
    ap.add_argument(
        "--run-id",
        type=str,
        default=None,
        help="Subfolder name under docs/perf/parameter_sweeps (default: UTC timestamp)",
    )
    ap.add_argument(
        "--waves",
        type=str,
        default="",
        help="Comma-separated wave ids to run (default: all in manifest)",
    )
    ap.add_argument("--dry-run", action="store_true", help="Print planned runs and exit")
    args = ap.parse_args()

    if not args.manifest.is_file():
        print(f"error: manifest not found: {args.manifest}", file=sys.stderr)
        return 2

    data = _load_manifest(args.manifest)
    defaults = data.get("defaults") or {}
    waves_in = data.get("waves") or []
    if not isinstance(waves_in, list) or not waves_in:
        print("error: manifest has no waves[]", file=sys.stderr)
        return 2

    want = {w.strip() for w in args.waves.split(",") if w.strip()}
    waves: List[Dict[str, Any]] = []
    for w in waves_in:
        wid = str(w.get("id", "")).strip()
        if want and wid not in want:
            continue
        waves.append(w)
    if not waves:
        print("error: no waves selected (check --waves)", file=sys.stderr)
        return 2

    run_id = args.run_id or datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    out_dir = args.out_dir or (ROOT / "docs" / "perf" / "parameter_sweeps" / run_id)

    tmpl = str(
        defaults.get("csv_template") or "{root}/historical_data/price/{sym_lower}_5m_databento.csv"
    )
    base_start = str(defaults.get("start") or "")
    base_end = str(defaults.get("end") or "")
    base_tf = str(defaults.get("timeframe") or "5m")

    plan: List[Tuple[str, str, str, str, Path, Dict[str, str]]] = []
    for w in waves:
        wid = str(w.get("id", ""))
        strat = str(w.get("strategy", "")).strip()
        syms = w.get("symbols") or ["MNQ"]
        if not strat:
            print(f"error: wave {wid!r} missing strategy", file=sys.stderr)
            return 2
        w_start = str(w.get("start") or base_start)
        w_end = str(w.get("end") or base_end)
        w_tf = str(w.get("timeframe") or base_tf)
        w_tmpl = str(w.get("csv_template") or tmpl)
        if not w_start or not w_end:
            print(f"error: wave {wid!r} needs start/end (set in wave or defaults)", file=sys.stderr)
            return 2
        runs = w.get("runs") or []
        for run in runs:
            if not isinstance(run, dict):
                continue
            label = str(run.get("label", "run"))
            env = run.get("env") or {}
            if not isinstance(env, dict):
                env = {}
            env_s = {str(k): str(v) for k, v in env.items()}
            for sym in syms:
                sym_u = str(sym).upper()
                csv_p = Path(
                    w_tmpl.format(
                        root=str(ROOT),
                        sym=sym_u,
                        sym_lower=sym_u.lower(),
                        SYM=sym_u,
                    )
                )
                plan.append((wid, label, strat, sym_u, csv_p, env_s))

    if not args.dry_run:
        for _, _, _, _, csv_p, _ in plan:
            if not csv_p.is_file():
                print(f"error: missing CSV: {csv_p}", file=sys.stderr)
                return 2

    print(f"run_id={run_id} runs={len(plan)} out_dir={out_dir}", file=sys.stderr)
    if args.dry_run:
        for row in plan:
            print(f"  {row[0]} {row[1]} {row[2]} {row[3]} csv={row[4]}", file=sys.stderr)
        return 0

    out_dir.mkdir(parents=True, exist_ok=True)

    jsonl = out_dir / "results.jsonl"
    tsv = out_dir / "summary.tsv"
    rows: List[Dict[str, Any]] = []

    with jsonl.open("w", encoding="utf-8") as jf:
        for wid, label, strat, sym, csv_p, env_s in plan:
            w = next(x for x in waves if str(x.get("id")) == wid)
            w_start = str(w.get("start") or base_start)
            w_end = str(w.get("end") or base_end)
            w_tf = str(w.get("timeframe") or base_tf)
            ok, d = _run_backtest_json(
                strategy=strat,
                symbol=sym,
                csv_path=csv_p,
                start=w_start,
                end=w_end,
                timeframe=w_tf,
                extra_env=env_s,
            )
            rec = {
                "ok": ok,
                "wave_id": wid,
                "label": label,
                "strategy": strat,
                "symbol": sym,
                "csv": str(csv_p),
                "start": w_start,
                "end": w_end,
                "timeframe": w_tf,
                "env": env_s,
                "payload": d,
            }
            jf.write(json.dumps(rec, default=str) + "\n")
            r = (d.get("result") or {}) if ok else {}
            rows.append(
                {
                    "wave_id": wid,
                    "label": label,
                    "strategy": strat,
                    "symbol": sym,
                    "total_pnl": float(r.get("total_pnl") or 0.0),
                    "total_trades": int(r.get("total_trades") or 0),
                    "win_rate_pct": float(r.get("win_rate") or 0.0),
                    "avg_reward_risk": float(r.get("avg_reward_risk") or 0.0),
                    "sharpe_ratio": float(r.get("sharpe_ratio") or 0.0),
                    "max_drawdown": float(r.get("max_drawdown") or 0.0),
                }
            )
            if not ok:
                print(f"FAIL {wid} {label} {strat} {sym}: {d.get('error', d)}", file=sys.stderr)

    with tsv.open("w", encoding="utf-8") as f:
        f.write(
            "wave_id\tlabel\tstrategy\tsymbol\ttotal_pnl\ttotal_trades\twin_rate_pct\t"
            "avg_reward_risk\tsharpe_ratio\tmax_drawdown\n"
        )
        for r in rows:
            f.write(
                f"{r['wave_id']}\t{r['label']}\t{r['strategy']}\t{r['symbol']}\t{r['total_pnl']:.2f}\t"
                f"{r['total_trades']}\t{r['win_rate_pct']:.4f}\t{r['avg_reward_risk']:.4f}\t"
                f"{r['sharpe_ratio']:.4f}\t{r['max_drawdown']:.2f}\n"
            )

    by_wave: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_wave[r["wave_id"]].append(r)

    md = out_dir / "by_wave.md"
    with md.open("w", encoding="utf-8") as f:
        f.write("# Parameter sweep — best per wave (by total_pnl)\n\n")
        f.write(f"- Manifest: `{args.manifest}`\n")
        f.write(f"- Period (defaults): **{base_start}** → **{base_end}**  \n")
        f.write(f"- Metrics: **win_rate_pct** (0–100), **avg_reward_risk** = mean PnL / initial bracket risk $\n\n")
        for wid in sorted(by_wave.keys()):
            block = sorted(by_wave[wid], key=lambda x: x["total_pnl"], reverse=True)
            best = block[0]
            f.write(f"## `{wid}`\n\n")
            f.write(
                f"Best: **{best['label']}** / {best['strategy']} / **{best['symbol']}** — "
                f"pnl **{best['total_pnl']:.2f}**, trades {best['total_trades']}, "
                f"WR **{best['win_rate_pct']:.2f}%**, avg R **{best['avg_reward_risk']:.3f}**, "
                f"Sharpe {best['sharpe_ratio']:.3f}, maxDD {best['max_drawdown']:.2f}\n\n"
            )
            f.write("| label | sym | pnl | trades | win% | avg R | Sharpe | maxDD |\n")
            f.write("|---|---|---:|---:|---:|---:|---:|---:|\n")
            for r in block[:12]:
                f.write(
                    f"| `{r['label']}` | {r['symbol']} | {r['total_pnl']:.2f} | {r['total_trades']} | "
                    f"{r['win_rate_pct']:.2f} | {r['avg_reward_risk']:.3f} | {r['sharpe_ratio']:.3f} | "
                    f"{r['max_drawdown']:.2f} |\n"
                )
            f.write("\n")

    print(f"wrote {tsv} {md} {jsonl}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
