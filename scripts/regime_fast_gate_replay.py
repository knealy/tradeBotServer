"""Shared replay helpers for regime fast gate sim + sweep."""

from __future__ import annotations

import os
import statistics as stats
import tempfile
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

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
    raw = __import__("json").loads(path.read_text(encoding="utf-8"))["trades_flat"]
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
    session_halt_enabled: bool = True,
    weekly_mgc_loss_usd: Optional[float] = None,
    weekly_mgc_enabled: bool = True,
    rolling_window: int = 3,
    rolling_warmup: Optional[int] = None,
    rolling_mean_max: float = 0.0,
    throttle_duration: int = 5,
    throttle_mult: float = 0.5,
) -> Dict[str, Any]:
    from core.regime_fast_gate import RegimeFastGate

    os.environ["REGIME_FAST_GATE_ENABLED"] = "1"
    halt = session_halt_usd if session_halt_usd is not None else dll_usd * 0.40
    wmgc = weekly_mgc_loss_usd if weekly_mgc_loss_usd is not None else dll_usd * 0.30
    warmup = rolling_warmup if rolling_warmup is not None else rolling_window

    with tempfile.TemporaryDirectory() as td:
        gate = RegimeFastGate(
            "sim",
            state_dir=Path(td),
            dll_usd=dll_usd,
            session_halt_usd=halt,
            session_halt_enabled=session_halt_enabled,
            weekly_mgc_loss_usd=wmgc,
            weekly_mgc_enabled=weekly_mgc_enabled,
            rolling_window=rolling_window,
            rolling_warmup=warmup,
            rolling_mean_max=rolling_mean_max,
            throttle_duration=throttle_duration,
            throttle_mult=throttle_mult,
        )
        sim_rows = []
        for r in rows:
            mult, reason = gate.resolve_multiplier(r["strategy"], r["symbol"])
            adj_q = apply_qty(r["requested"], mult)
            adj_pnl = 0.0 if adj_q == 0 else r["pnl"] * adj_q / r["requested"]
            sim_rows.append({**r, "mult": mult, "adj_pnl": adj_pnl, "reason": reason})
            gate.record_trade(r["strategy"], r["symbol"], r["pnl"], exit_time=r["exit"])

    return {"rows": sim_rows, "halt_usd": halt, "weekly_mgc_usd": wmgc}


def summarize_metrics(sim_rows: list) -> Dict[str, Any]:
    base = sum(r["pnl"] for r in sim_rows)
    adj = sum(r["adj_pnl"] for r in sim_rows)
    seq_b = [r["pnl"] for r in sim_rows]
    seq_a = [r["adj_pnl"] for r in sim_rows]
    dd_b = max_drawdown(seq_b)
    dd_a = max_drawdown(seq_a)

    base_w: Dict[str, float] = defaultdict(float)
    adj_w: Dict[str, float] = defaultdict(float)
    for r in sim_rows:
        wk = r["exit"].astimezone(ET).strftime("%Y-W%W")
        base_w[wk] += r["pnl"]
        adj_w[wk] += r["adj_pnl"]

    rows_2026 = [r for r in sim_rows if r["exit"].astimezone(ET).year >= 2026]
    b26 = sum(r["pnl"] for r in rows_2026)
    a26 = sum(r["adj_pnl"] for r in rows_2026)
    dd_b26 = max_drawdown([r["pnl"] for r in rows_2026])
    dd_a26 = max_drawdown([r["adj_pnl"] for r in rows_2026])

    blocked = sum(1 for r in sim_rows if r["mult"] <= 0)
    throttled = sum(1 for r in sim_rows if 0 < r["mult"] < 0.999)

    pct_ret = 100 * adj / base if base else None
    pct_ret_26 = 100 * a26 / b26 if b26 else None
    endurance = adj / max(dd_a, 1.0)
    endurance_26 = a26 / max(dd_a26, 1.0)

    return {
        "baseline_pnl": round(base, 2),
        "gated_pnl": round(adj, 2),
        "pnl_cost": round(base - adj, 2),
        "pct_retained": round(pct_ret, 2) if pct_ret is not None else None,
        "baseline_pnl_2026": round(b26, 2),
        "gated_pnl_2026": round(a26, 2),
        "pnl_cost_2026": round(b26 - a26, 2),
        "pct_retained_2026": round(pct_ret_26, 2) if pct_ret_26 is not None else None,
        "max_dd_baseline": round(dd_b, 2),
        "max_dd_gated": round(dd_a, 2),
        "dd_reduction": round(dd_b - dd_a, 2),
        "max_dd_baseline_2026": round(dd_b26, 2),
        "max_dd_gated_2026": round(dd_a26, 2),
        "dd_reduction_2026": round(dd_b26 - dd_a26, 2),
        "worst_week_baseline": round(min(base_w.values()), 2) if base_w else 0,
        "worst_week_gated": round(min(adj_w.values()), 2) if adj_w else 0,
        "blocked_trades": blocked,
        "throttled_trades": throttled,
        "endurance_rf": round(endurance, 3),
        "endurance_rf_2026": round(endurance_26, 3),
        "endurance_score": round(endurance * (pct_ret / 100 if pct_ret else 0), 3),
        "endurance_score_2026": round(
            endurance_26 * (pct_ret_26 / 100 if pct_ret_26 else 0), 3,
        ),
    }


def evaluate_trial(rows: list, params: Dict[str, Any]) -> Dict[str, Any]:
    result = run_replay(rows, **params)
    metrics = summarize_metrics(result["rows"])
    return {"params": params, "metrics": metrics}
