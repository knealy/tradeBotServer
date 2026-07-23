#!/usr/bin/env python3
"""Validate calendar-era and KER/ADX regime gates against walk-forward trades.

Answers TODO questions:
- How much PnL would ``core/regime_sizing`` have blocked vs retained?
- Does entry-time ``core.regime`` label separate winners from losers?
- How often does the 5m classifier flip label (chop/mixed/trend) per week?

Example::

  .venv/bin/python scripts/regime_gate_validation.py \\
    --walkforward-dir docs/perf/regime_longspan_450d \\
    --csv-dir historical_data/price \\
    --out docs/perf/regime_longspan_450d/regime_gate_validation.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.regime import RegimeConfig, classify
from core.regime_sizing import apply_regime_sizing_quantity, resolve_regime_sizing_multiplier
from scripts.regime_performance_report import (
    load_bars_csv,
    load_trades_from_insights,
    regime_label_for_trade,
)

_ET = ZoneInfo("America/New_York")


def _parse_iso(s: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _session_date_et(trade: Dict[str, Any]) -> Optional[date]:
    et = _parse_iso(str(trade.get("entry_time") or ""))
    if not et:
        return None
    return et.astimezone(_ET).date()


def gate_trade_calendar(
    trade: Dict[str, Any],
) -> Tuple[bool, float, str]:
    """Return (would_block, multiplier, reason). Block when multiplier == 0."""
    strat = str(trade.get("strategy") or "")
    sym = str(trade.get("symbol") or "").upper()
    sd = _session_date_et(trade)
    if sd is None:
        return False, 1.0, "no_date"
    mult, reason = resolve_regime_sizing_multiplier(
        strat, sym, session_date=sd, regime_label=None,
    )
    return mult <= 0, mult, reason


def gate_trade_full(
    trade: Dict[str, Any],
    regime_label: str,
) -> Tuple[bool, float, str]:
    """Calendar + regime-label overlay (mirrors live ``apply_regime_sizing_quantity``)."""
    strat = str(trade.get("strategy") or "")
    sym = str(trade.get("symbol") or "").upper()
    sd = _session_date_et(trade)
    qty, reason = apply_regime_sizing_quantity(
        strat, sym, 1, session_date=sd, regime_label=regime_label,
    )
    return qty <= 0, 0.0 if qty <= 0 else 1.0, reason


def summarize_gate(
    trades: List[Dict[str, Any]],
    *,
    gate_fn,
) -> Dict[str, Any]:
    blocked: List[Dict[str, Any]] = []
    kept: List[Dict[str, Any]] = []
    for t in trades:
        blocked_flag, _, _ = gate_fn(t)
        if blocked_flag:
            blocked.append(t)
        else:
            kept.append(t)

    def _pnl(ts: List[Dict[str, Any]]) -> float:
        return sum(float(x.get("pnl") or 0) for x in ts)

    def _wins(ts: List[Dict[str, Any]]) -> int:
        return sum(1 for x in ts if float(x.get("pnl") or 0) > 0)

    b_pnl = _pnl(blocked)
    k_pnl = _pnl(kept)
    return {
        "n_total": len(trades),
        "n_blocked": len(blocked),
        "n_kept": len(kept),
        "blocked_pnl": round(b_pnl, 2),
        "kept_pnl": round(k_pnl, 2),
        "baseline_pnl": round(b_pnl + k_pnl, 2),
        "blocked_winners": _wins(blocked),
        "blocked_losers": len(blocked) - _wins(blocked),
        "kept_winners": _wins(kept),
        "pnl_delta_vs_baseline": round(k_pnl - (b_pnl + k_pnl), 2),
    }


def summarize_by_regime_label(
    trades: List[Dict[str, Any]],
    bars_by_symbol: Dict[str, List[Dict[str, Any]]],
    cfg: RegimeConfig,
) -> Dict[str, Dict[str, Any]]:
    buckets: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for t in trades:
        lbl = regime_label_for_trade(t, bars_by_symbol, cfg)
        buckets[lbl].append(t)

    out: Dict[str, Dict[str, Any]] = {}
    for lbl, ts in sorted(buckets.items()):
        pnl = sum(float(x.get("pnl") or 0) for x in ts)
        wins = sum(1 for x in ts if float(x.get("pnl") or 0) > 0)
        out[lbl] = {
            "n": len(ts),
            "pnl": round(pnl, 2),
            "win_rate": round(wins / len(ts), 4) if ts else 0.0,
            "avg_pnl": round(pnl / len(ts), 2) if ts else 0.0,
        }
    return out


def label_flip_stats(
    bars: List[Dict[str, Any]],
    cfg: RegimeConfig,
    *,
    step: int = 12,
) -> Dict[str, Any]:
    """Count regime label changes every ``step`` bars (~1h on 5m)."""
    if len(bars) < cfg.ker_lookback + step:
        return {"flips": 0, "windows": 0, "labels": {}}

    labels: List[str] = []
    for i in range(cfg.ker_lookback, len(bars), step):
        window = bars[i - cfg.ker_lookback : i]
        labels.append(classify(window, cfg).label)

    flips = sum(1 for i in range(1, len(labels)) if labels[i] != labels[i - 1])
    dist: Dict[str, int] = defaultdict(int)
    for lbl in labels:
        dist[lbl] += 1
    return {
        "flips": flips,
        "windows": max(0, len(labels) - 1),
        "flip_rate": round(flips / max(1, len(labels) - 1), 4),
        "label_share": {k: round(v / len(labels), 4) for k, v in dist.items()},
        "dominant": max(dist, key=dist.get) if dist else "mixed",
    }


def boundary_alignment(
    bars: List[Dict[str, Any]],
    boundary: date,
    cfg: RegimeConfig,
    *,
    days_before: int = 30,
    days_after: int = 30,
) -> Dict[str, Any]:
    """Label distribution in ET days around a known calendar boundary."""
    start = datetime(boundary.year, boundary.month, boundary.day, tzinfo=_ET).timestamp()
    span_before = days_before * 86400
    span_after = days_after * 86400

    before_labels: List[str] = []
    after_labels: List[str] = []
    for i in range(cfg.ker_lookback, len(bars), 12):
        ts = float(bars[i].get("_ts", 0))
        if ts <= 0:
            continue
        window = bars[i - cfg.ker_lookback : i]
        lbl = classify(window, cfg).label
        if start - span_before <= ts < start:
            before_labels.append(lbl)
        elif start <= ts < start + span_after:
            after_labels.append(lbl)

    def _share(ls: List[str]) -> Dict[str, float]:
        if not ls:
            return {}
        d: Dict[str, int] = defaultdict(int)
        for x in ls:
            d[x] += 1
        return {k: round(v / len(ls), 4) for k, v in d.items()}

    return {
        "boundary": boundary.isoformat(),
        "before_n": len(before_labels),
        "after_n": len(after_labels),
        "before_share": _share(before_labels),
        "after_share": _share(after_labels),
    }


def run_validation(
    walkforward_dir: Path,
    csv_dir: Path,
    *,
    symbols: Tuple[str, ...] = ("MNQ", "MGC"),
) -> Dict[str, Any]:
    import os

    os.environ["REGIME_SIZING_ENABLED"] = "1"

    insights = walkforward_dir / "metrics_insights.json"
    trades = load_trades_from_insights(insights)
    cfg = RegimeConfig()

    bars_by_symbol = {sym: load_bars_csv(csv_dir, sym, "5m") for sym in symbols}

    for t in trades:
        t["_regime_label"] = regime_label_for_trade(t, bars_by_symbol, cfg)

    def calendar_gate(t: Dict[str, Any]) -> Tuple[bool, float, str]:
        sd = _session_date_et(t)
        mult, reason = resolve_regime_sizing_multiplier(
            str(t.get("strategy") or ""),
            str(t.get("symbol") or "").upper(),
            session_date=sd,
            regime_label=None,
        )
        return mult <= 0, mult, reason

    def full_gate(t: Dict[str, Any]) -> Tuple[bool, float, str]:
        return gate_trade_full(t, str(t.get("_regime_label") or "mixed"))

    by_strat: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for t in trades:
        by_strat[str(t.get("strategy") or "unknown")].append(t)

    strat_calendar = {
        k: summarize_gate(v, gate_fn=calendar_gate) for k, v in by_strat.items()
    }
    strat_full = {k: summarize_gate(v, gate_fn=full_gate) for k, v in by_strat.items()}

    regime_breakdown = {
        strat: summarize_by_regime_label(ts, bars_by_symbol, cfg)
        for strat, ts in by_strat.items()
    }

    flip_stats = {
        sym: label_flip_stats(bars_by_symbol.get(sym) or [], cfg) for sym in symbols
    }

    boundaries = {
        "mgc_mrr_jan14_2026": boundary_alignment(
            bars_by_symbol.get("MGC") or [], date(2026, 1, 14), cfg,
        ),
        "or_oct_2025": boundary_alignment(
            bars_by_symbol.get("MNQ") or [], date(2025, 10, 1), cfg,
        ),
    }

    return {
        "source": str(insights),
        "n_trades": len(trades),
        "calendar_gate_all": summarize_gate(trades, gate_fn=calendar_gate),
        "full_gate_all": summarize_gate(trades, gate_fn=full_gate),
        "calendar_gate_by_strategy": strat_calendar,
        "full_gate_by_strategy": strat_full,
        "regime_label_breakdown": regime_breakdown,
        "label_flip_stats_5m": flip_stats,
        "boundary_alignment": boundaries,
        "interpretation": {
            "calendar_gate_note": (
                "Retrospective era boundaries from 450d study. "
                "High blocked_pnl magnitude with negative sign = gate removes historical losses. "
                "blocked_winners = opportunity cost."
            ),
            "regime_label_note": (
                "KER+ADX at entry labels ~250min lookback on 5m. "
                "Modal label is mixed; trend bucket is sparse — not a primary live gate yet."
            ),
        },
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Validate regime sizing gates on walk-forward trades")
    ap.add_argument(
        "--walkforward-dir",
        type=Path,
        default=ROOT / "docs/perf/regime_longspan_450d",
    )
    ap.add_argument("--csv-dir", type=Path, default=ROOT / "historical_data/price")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args(argv)

    out_path = args.out or (args.walkforward_dir / "regime_gate_validation.json")
    doc = run_validation(args.walkforward_dir, args.csv_dir)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(doc, indent=2), encoding="utf-8")

    cal = doc["calendar_gate_all"]
    full = doc["full_gate_all"]
    print(f"Wrote {out_path}")
    print(
        f"Calendar gate: blocked {cal['n_blocked']}/{cal['n_total']} trades, "
        f"blocked_pnl=${cal['blocked_pnl']:.0f}, kept_pnl=${cal['kept_pnl']:.0f}"
    )
    print(
        f"Full gate (+regime): blocked {full['n_blocked']}/{full['n_total']} trades, "
        f"kept_pnl=${full['kept_pnl']:.0f}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
