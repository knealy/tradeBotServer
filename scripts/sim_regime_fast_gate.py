#!/usr/bin/env python3
"""Replay sim for ``core/regime_fast_gate`` on walk-forward MRR trades.

Default thresholds assume **$1k DLL** prop account:
  session halt −$400 (40%), weekly MGC −$300 (30%), 3-session roll → 0.5× × 5 sessions.

Usage::

    .venv/bin/python scripts/sim_regime_fast_gate.py
    .venv/bin/python scripts/sim_regime_fast_gate.py \\
        --walkforward-dir docs/perf/regime_longspan_450d \\
        --dll-usd 1000 --out docs/perf/regime_fast_gate_replay.json
"""

from __future__ import annotations

import argparse
import json
import os
import statistics as stats
import sys
import tempfile
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

ET = ZoneInfo("America/New_York")
DEFAULT_QTY = {"MNQ": 4, "MGC": 2}


def parse_ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def apply_qty(requested: int, mult: float) -> int:
    if mult <= 0:
        return 0
    if mult >= 0.999:
        return requested
    adj = max(0, int(requested * mult))
    if adj < 1 and requested >= 1:
        adj = 1
    return adj


def max_drawdown(pnls: List[float]) -> float:
    peak = eq = 0.0
    m = 0.0
    for p in pnls:
        eq += p
        peak = max(peak, eq)
        m = max(m, peak - eq)
    return m


def load_mrr_trades(walkforward_dir: Path, strategy: str = "morning_range_reversion") -> list:
    path = walkforward_dir / "metrics_insights.json"
    raw = json.loads(path.read_text(encoding="utf-8"))["trades_flat"]
    rows = []
    for t in raw:
        if t.get("strategy") != strategy:
            continue
        sym = str(t["symbol"]).upper()
        rows.append({
            "entry": parse_ts(t["entry_time"]),
            "exit": parse_ts(t["exit_time"]),
            "strategy": t["strategy"],
            "symbol": sym,
            "pnl": float(t["pnl"]),
            "requested": DEFAULT_QTY.get(sym, 1),
        })
    rows.sort(key=lambda r: (r["entry"], r["exit"]))
    return rows


def run_replay(
    rows: list,
    *,
    dll_usd: float = 1000.0,
    session_halt_usd: Optional[float] = None,
    session_halt_enabled: Optional[bool] = None,
    weekly_mgc_loss_usd: Optional[float] = None,
    weekly_mgc_enabled: Optional[bool] = None,
    rolling_window: Optional[int] = None,
    rolling_warmup: Optional[int] = None,
    rolling_mean_max: Optional[float] = None,
    throttle_duration: Optional[int] = None,
    throttle_mult: float = 0.5,
) -> Dict[str, Any]:
    from core.regime_fast_gate import RegimeFastGate

    os.environ["REGIME_FAST_GATE_ENABLED"] = "1"
    os.environ["REGIME_FAST_DLL_USD"] = str(dll_usd)

    kwargs: Dict[str, Any] = {
        "dll_usd": dll_usd,
        "throttle_mult": throttle_mult,
    }
    if session_halt_usd is not None:
        kwargs["session_halt_usd"] = session_halt_usd
    if session_halt_enabled is not None:
        kwargs["session_halt_enabled"] = session_halt_enabled
    if weekly_mgc_loss_usd is not None:
        kwargs["weekly_mgc_loss_usd"] = weekly_mgc_loss_usd
    if weekly_mgc_enabled is not None:
        kwargs["weekly_mgc_enabled"] = weekly_mgc_enabled
    if rolling_window is not None:
        kwargs["rolling_window"] = rolling_window
    if rolling_warmup is not None:
        kwargs["rolling_warmup"] = rolling_warmup
    if rolling_mean_max is not None:
        kwargs["rolling_mean_max"] = rolling_mean_max
    if throttle_duration is not None:
        kwargs["throttle_duration"] = throttle_duration

    with tempfile.TemporaryDirectory() as td:
        gate = RegimeFastGate("sim", state_dir=Path(td), **kwargs)
        sim_rows = []
        for r in rows:
            mult, reason = gate.resolve_multiplier(r["strategy"], r["symbol"])
            adj_q = apply_qty(r["requested"], mult)
            adj_pnl = 0.0 if adj_q == 0 else r["pnl"] * adj_q / r["requested"]
            sim_rows.append({**r, "mult": mult, "adj_pnl": adj_pnl, "reason": reason})
            gate.record_trade(r["strategy"], r["symbol"], r["pnl"], exit_time=r["exit"])

    return {
        "rows": sim_rows,
        "halt_usd": gate.session_halt_usd,
        "weekly_mgc_usd": gate.weekly_mgc_loss_usd,
        "rolling_window": gate.rolling_window,
        "rolling_mean_max": gate.rolling_mean_max,
        "throttle_duration": gate.throttle_duration,
        "weekly_mgc_enabled": gate.weekly_mgc_enabled,
    }


def summarize_by_year(sim_rows: list) -> Dict[str, Any]:
    by_year: Dict[int, Dict[str, Any]] = defaultdict(lambda: {
        "base": 0.0, "adj": 0.0, "n": 0, "blocked": 0,
        "throttled": 0, "reasons": Counter(),
    })
    for r in sim_rows:
        yr = r["exit"].astimezone(ET).year
        d = by_year[yr]
        d["base"] += r["pnl"]
        d["adj"] += r["adj_pnl"]
        d["n"] += 1
        if r["mult"] <= 0:
            d["blocked"] += 1
        elif r["mult"] < 0.999:
            d["throttled"] += 1
        if r["mult"] < 0.999:
            d["reasons"][r["reason"].split("→")[0].strip()[:40]] += 1

    out = {}
    for yr in sorted(by_year):
        d = by_year[yr]
        seq_b = [x["pnl"] for x in sim_rows if x["exit"].astimezone(ET).year == yr]
        seq_a = [x["adj_pnl"] for x in sim_rows if x["exit"].astimezone(ET).year == yr]
        out[str(yr)] = {
            "trades": d["n"],
            "baseline_pnl": round(d["base"], 2),
            "gated_pnl": round(d["adj"], 2),
            "pnl_cost": round(d["base"] - d["adj"], 2),
            "pct_retained": round(100 * d["adj"] / d["base"], 1) if d["base"] else None,
            "blocked_trades": d["blocked"],
            "throttled_trades": d["throttled"],
            "max_dd_baseline": round(max_drawdown(seq_b), 2),
            "max_dd_gated": round(max_drawdown(seq_a), 2),
            "dd_reduction": round(max_drawdown(seq_b) - max_drawdown(seq_a), 2),
            "trigger_reasons": dict(d["reasons"]),
        }
    return out


def weekly_compare(sim_rows: list) -> Dict[str, Any]:
    base_w: Dict[str, float] = defaultdict(float)
    adj_w: Dict[str, float] = defaultdict(float)
    for r in sim_rows:
        wk = r["exit"].astimezone(ET).strftime("%Y-W%W")
        base_w[wk] += r["pnl"]
        adj_w[wk] += r["adj_pnl"]
    weeks = sorted(base_w)
    diffs = [adj_w[w] - base_w[w] for w in weeks]
    helped = sum(1 for w in weeks if adj_w[w] > base_w[w] and base_w[w] < 0)
    hurt = sum(1 for w in weeks if adj_w[w] < base_w[w] and base_w[w] > 0)
    return {
        "weeks": len(weeks),
        "mean_weekly_cost": round(stats.mean(diffs), 2) if diffs else 0,
        "weeks_helped_on_losses": helped,
        "weeks_hurt_on_wins": hurt,
        "worst_week_baseline": round(min(base_w.values()), 2),
        "worst_week_gated": round(min(adj_w.values()), 2),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Replay sim for regime fast gate")
    ap.add_argument(
        "--walkforward-dir",
        type=Path,
        default=Path("docs/perf/regime_longspan_450d"),
    )
    ap.add_argument("--strategy", default="morning_range_reversion")
    ap.add_argument("--dll-usd", type=float, default=1000.0)
    ap.add_argument("--session-halt-usd", type=float, default=0.0)
    ap.add_argument("--weekly-mgc-loss-usd", type=float, default=0.0)
    ap.add_argument("--rolling-window", type=int, default=3)
    ap.add_argument("--out", type=Path, default=Path("docs/perf/regime_fast_gate_replay.json"))
    args = ap.parse_args()

    rows = load_mrr_trades(args.walkforward_dir, strategy=args.strategy)
    if not rows:
        print("No trades found")
        return 1

    result = run_replay(
        rows,
        dll_usd=args.dll_usd,
        session_halt_usd=args.session_halt_usd or None,
        weekly_mgc_loss_usd=args.weekly_mgc_loss_usd or None,
        rolling_window=args.rolling_window,
    )
    sim_rows = result["rows"]
    base = sum(r["pnl"] for r in sim_rows)
    adj = sum(r["adj_pnl"] for r in sim_rows)

    report = {
        "meta": {
            "strategy": args.strategy,
            "n_trades": len(sim_rows),
            "dll_usd": args.dll_usd,
            "session_halt_usd": result["halt_usd"],
            "weekly_mgc_loss_usd": result["weekly_mgc_usd"],
            "rolling_window": result.get("rolling_window", args.rolling_window),
            "rolling_mean_max": result.get("rolling_mean_max"),
            "throttle_duration": result.get("throttle_duration"),
            "source": str(args.walkforward_dir),
        },
        "total": {
            "baseline_pnl": round(base, 2),
            "gated_pnl": round(adj, 2),
            "pnl_cost": round(base - adj, 2),
            "pct_retained": round(100 * adj / base, 1) if base else None,
            "blocked_trades": sum(1 for r in sim_rows if r["mult"] <= 0),
            "throttled_trades": sum(1 for r in sim_rows if 0 < r["mult"] < 0.999),
        },
        "by_year": summarize_by_year(sim_rows),
        "weekly": weekly_compare(sim_rows),
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print("=" * 72)
    print(f"REGIME FAST GATE REPLAY — {args.strategy} ({len(rows)} trades)")
    print(f"DLL=${args.dll_usd:,.0f}  session_halt=−${result['halt_usd']:,.0f}  "
          f"roll={args.rolling_window} mean<{result.get('rolling_mean_max', 0):.0f}  "
          f"dur={result.get('throttle_duration', 3)}")
    print("=" * 72)
    print(f"Total: base=${base:,.0f}  gated=${adj:,.0f}  cost=${base-adj:,.0f}  "
          f"({report['total']['pct_retained']}% retained)")
    print(f"Blocked={report['total']['blocked_trades']}  "
          f"Throttled={report['total']['throttled_trades']}")
    for yr, s in report["by_year"].items():
        print(f"\n  [{yr}] base=${s['baseline_pnl']:,.0f} → gated=${s['gated_pnl']:,.0f}  "
              f"cost=${s['pnl_cost']:,.0f}  DD ${s['max_dd_baseline']:,.0f}→${s['max_dd_gated']:,.0f} "
              f"(saved ${s['dd_reduction']:,.0f})")
        if s["trigger_reasons"]:
            print(f"    triggers: {s['trigger_reasons']}")
    wk = report["weekly"]
    print(f"\nWeekly: mean cost ${wk['mean_weekly_cost']:,.0f}/wk  "
          f"helped on {wk['weeks_helped_on_losses']} losing weeks  "
          f"worst wk ${wk['worst_week_baseline']:,.0f}→${wk['worst_week_gated']:,.0f}")
    print(f"\nWrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
