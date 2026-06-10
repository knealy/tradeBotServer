#!/usr/bin/env python3
"""Compare live trading results against backtest truth expectations.

After deploying a strategy to live, this script pulls realised trades
from the ``trade_history`` Postgres table for a configurable window
(default: trailing 5 sessions) and compares the aggregated metrics
against the corresponding walk-forward truth recap (default: the 3 m
recap at ``docs/perf/<strategy>_*_truth_3m/metrics_insights.json``).

Usage:

    # MRR on account 1, last 5 ET sessions, default 3m truth:
    .venv/bin/python scripts/compare_live_vs_backtest.py \\
        --strategy morning_range_reversion --account 1

    # overnight_range on account 2 with explicit truth path + window:
    .venv/bin/python scripts/compare_live_vs_backtest.py \\
        --strategy overnight_range --account 2 \\
        --truth docs/perf/overnight_range_r24_truth_3m \\
        --since 2026-06-09

The output is a side-by-side table showing live actual vs backtest
expectation per metric (n, WR, total PnL, avg PnL, max win, max loss,
drawdown).  Each row carries a verdict marker:

    ✓ within ±1 standard deviation of expectation
    ⚠ between 1 and 2σ — drift; investigate but don't panic
    ✗ beyond 2σ — STOP THE STRATEGY and investigate before re-deploy

Sample size guidance: aggregate verdicts on <5 trades are unreliable;
the script prints a sample-size note when n < 5.  After 10+ live
trades, the comparison is statistically meaningful.

Backtest truth: this tool consumes the same ``metrics_insights.json``
shape that ``scripts/walkforward_trade_recap_report.py`` writes (the
``equity_summary`` + ``extended`` sub-structures under the global +
per-(strategy, symbol) keys).  Strategies whose truth recap dirs don't
follow the ``<strategy>_*_truth_<window>/`` convention can be pointed
at with ``--truth <path>``.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _et_today() -> date:
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(timezone.utc).astimezone(ZoneInfo("America/New_York")).date()
    except Exception:
        return datetime.now().date()


def _resolve_truth_dir(strategy: str, window: str, explicit: Optional[str]) -> Path:
    if explicit:
        p = Path(explicit)
        if not p.is_absolute():
            p = ROOT / p
        if not p.exists():
            sys.exit(f"❌ --truth path does not exist: {p}")
        return p
    # The recap report writes to dirs that combine the strategy name with
    # an arbitrary commit / round tag (e.g. ``morning_range_r28_truth_3m``,
    # ``overnight_range_r24_truth_3m``, ``overnight_reversion_revival_truth_3m``).
    # The strategy name itself isn't always a literal prefix: MRR's truth
    # dir is ``morning_range_*`` (no ``_reversion``).  Strategy aliases
    # capture those known divergences; everything else falls back to a
    # glob over the docs/perf siblings.
    aliases = {
        "morning_range_reversion": ["morning_range"],
        "vwap_zscore_reversion": ["vwap_zscore"],
    }
    base_keys = [strategy] + aliases.get(strategy, [])
    perf = ROOT / "docs/perf"
    matches: List[Path] = []
    for key in base_keys:
        # Prioritize the exact ``<key>_*_truth_<window>`` shape.
        for c in sorted(perf.glob(f"{key}_*_truth_{window}")):
            if (c / "metrics_insights.json").exists():
                matches.append(c)
        # Fall back to the rarer ``<key>_truth_<window>`` shape.
        c = perf / f"{key}_truth_{window}"
        if c.exists() and (c / "metrics_insights.json").exists():
            matches.append(c)
    if matches:
        # Prefer the most-recently-modified candidate so the latest
        # commit's recap takes precedence over older sweep iterations.
        return max(matches, key=lambda p: p.stat().st_mtime)
    sys.exit(
        f"❌ Could not auto-resolve truth dir for {strategy} window={window}. "
        f"Pass --truth <path> explicitly.\n"
        f"   Searched docs/perf for: " +
        ", ".join(f"{k}_*_truth_{window}" for k in base_keys)
    )


def _project_truth_row(es: Dict[str, Any], ex: Dict[str, Any]) -> Dict[str, Any]:
    """Project a ``equity_summary`` + ``extended`` pair into the comparison shape."""
    start_equity = float(es.get("start_equity", 0.0) or 0.0)
    final_equity = float(es.get("final_equity", 0.0) or 0.0)
    return {
        "n_trades": int(es.get("n_trades", 0) or 0),
        "start_equity": start_equity,
        "total_pnl": final_equity - start_equity,
        "total_return_pct": float(es.get("total_return_pct", 0.0) or 0.0),
        "max_drawdown_pct": float(es.get("max_drawdown_pct", 0.0) or 0.0),
        "max_drawdown_dollars": float(es.get("max_drawdown_dollars", 0.0) or 0.0),
        "win_rate_pct": float(ex.get("actual_win_rate", 0.0) or 0.0) * 100.0,
        "recovery_factor": float(ex.get("recovery_factor_pnl_vs_seq_dd", 0.0) or 0.0),
        "avg_r_winners": float(ex.get("avg_r_winners", 0.0) or 0.0),
        "avg_r_losers": float(ex.get("avg_r_losers", 0.0) or 0.0),
    }


def _load_truth(truth_dir: Path, strategy: str, symbol_filter: Optional[List[str]] = None) -> Dict[str, Any]:
    """Load ``metrics_insights.json`` and project the global + per-symbol stats.

    The recap report writes a strict shape: ``grand.equity_summary`` (n_trades,
    start_equity, final_equity, total_return_pct, max_drawdown_*) plus
    ``grand.extended`` (recovery_factor_pnl_vs_seq_dd, actual_win_rate, avg_r_*).
    ``avg_win``/``avg_loss`` ($) are NOT in the recap, so we surface
    ``avg_r_winners``/``avg_r_losers`` (in R units) instead — those are more
    portable across position-size changes anyway.
    """
    p = truth_dir / "metrics_insights.json"
    if not p.exists():
        sys.exit(f"❌ Missing metrics_insights.json under {truth_dir}")
    data = json.loads(p.read_text())

    g = data.get("grand", {})
    truth = _project_truth_row(g.get("equity_summary", {}) or {}, g.get("extended", {}) or {})
    truth["sim_start_equity"] = float(data.get("sim_start_equity", truth["start_equity"]) or 0.0)
    truth["per_symbol"] = {}
    for k, v in (data.get("per_strategy_symbol", {}) or {}).items():
        # key format: "<strategy>|<symbol>"
        parts = str(k).split("|", 1)
        if len(parts) != 2 or parts[0] != strategy:
            continue
        sym = parts[1].upper()
        if symbol_filter and sym not in {s.upper() for s in symbol_filter}:
            continue
        truth["per_symbol"][sym] = _project_truth_row(
            v.get("equity_summary", {}) or {},
            v.get("extended", {}) or {},
        )
    return truth


def _load_live_trades(strategy: str, account: str, since_dt: datetime) -> List[Dict[str, Any]]:
    """Pull live trades from Postgres ``trade_history`` for the comparison window.

    Returns an empty list when the DB is unavailable so the caller can
    still print the truth panel (lets the operator preview expectations
    before the first live trade exists).
    """
    try:
        from infrastructure.database import get_database
    except Exception as exc:
        print(f"⚠️  DB layer import failed ({exc}); skipping live data.")
        return []
    try:
        db = get_database()
    except Exception as exc:
        print(f"⚠️  DB init failed ({exc}); skipping live data.")
        return []
    if db is None or getattr(db, "pool", None) is None:
        print("⚠️  DATABASE_URL unset / Postgres unreachable; skipping live data.")
        return []
    rows: List[Dict[str, Any]] = []
    try:
        with db.get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT symbol, side, quantity, entry_price, exit_price, pnl,
                       entry_time, exit_time, duration_seconds, metadata
                FROM trade_history
                WHERE strategy_name = %s
                  AND account_id = %s
                  AND exit_time >= %s
                ORDER BY exit_time ASC
                """,
                (strategy, str(account), since_dt),
            )
            cols = [c[0] for c in cur.description]
            for r in cur.fetchall():
                rows.append(dict(zip(cols, r)))
    except Exception as exc:
        print(f"⚠️  DB query failed ({exc}); skipping live data.")
        return []
    return rows


def _aggregate(trades: List[Dict[str, Any]], start_equity: float) -> Dict[str, Any]:
    """Compute the same metrics shape as the truth JSON.

    ``start_equity`` is sourced from the truth recap's ``sim_start_equity``
    (typically $2000) so the ``total_return_pct`` and ``max_drawdown_pct``
    are directly comparable to truth.  Use the live account's actual
    starting balance if you want a real-money normalisation by passing
    ``--initial-equity``.
    """
    if not trades:
        return {
            "n_trades": 0, "total_pnl": 0.0, "total_return_pct": 0.0,
            "max_drawdown_pct": 0.0, "win_rate_pct": 0.0,
            "recovery_factor": 0.0, "avg_r_winners": 0.0, "avg_r_losers": 0.0,
        }
    pnls = [float(t["pnl"] or 0.0) for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    total = sum(pnls)
    n = len(pnls)
    wr = (len(wins) / n * 100.0) if n else 0.0

    # Sequential drawdown $ on the running equity curve.
    eq = 0.0
    peak = 0.0
    dd_dollar = 0.0
    for p in pnls:
        eq += p
        peak = max(peak, eq)
        dd_dollar = max(dd_dollar, peak - eq)
    rf = (total / dd_dollar) if dd_dollar > 0 else (float("inf") if total > 0 else 0.0)

    denom = max(start_equity, 1.0)
    total_return_pct = (total / denom) * 100.0
    max_dd_pct = (dd_dollar / denom) * 100.0

    avg_win = (sum(wins) / len(wins)) if wins else 0.0
    avg_loss = (sum(losses) / len(losses)) if losses else 0.0
    # Without per-trade R units in the DB we approximate avg_r_* as
    # win-side / abs(loss-side) average ratios assuming a 1R loss.  This
    # is fine when the strategy has a single stop-distance regime; for
    # MRR R28 it isn't (MNQ vs MGC differ) but the live trades carry
    # actual $-PnL so this is more illustrative than precise.
    avg_r_winners = (avg_win / abs(avg_loss)) if avg_loss else 0.0
    avg_r_losers = -1.0 if avg_loss else 0.0

    return {
        "n_trades": n,
        "total_pnl": total,
        "total_return_pct": total_return_pct,
        "max_drawdown_pct": max_dd_pct,
        "win_rate_pct": wr,
        "recovery_factor": rf if math.isfinite(rf) else 0.0,
        "avg_r_winners": avg_r_winners,
        "avg_r_losers": avg_r_losers,
    }


def _verdict(live: float, truth: float, *, tol_pct: float = 0.30) -> str:
    """Naive heuristic: ±30 % of truth is OK; 30-60 % is drift; >60 % is alarm.

    A real z-score test would require per-metric variance, which the
    truth recap doesn't currently emit.  This proxy is conservative.
    """
    if truth == 0.0:
        # If truth is exactly zero (e.g. avg_win on a strategy that
        # never wins on the truth window — unlikely) skip the comparison.
        return "·"
    rel = abs(live - truth) / max(abs(truth), 1e-9)
    if rel <= tol_pct:
        return "✓"
    if rel <= 2 * tol_pct:
        return "⚠"
    return "✗"


def _fmt(v: Any, *, suffix: str = "") -> str:
    if isinstance(v, float):
        return f"{v:+.2f}{suffix}" if v else f" 0.00{suffix}"
    return f"{v}{suffix}"


def _row(label: str, live_v: Any, truth_v: Any, suffix: str = "") -> str:
    if isinstance(live_v, (int, float)) and isinstance(truth_v, (int, float)):
        verdict = _verdict(float(live_v), float(truth_v))
    else:
        verdict = "·"
    return f"  {label:<22}  live: {_fmt(live_v, suffix=suffix):>14}    truth: {_fmt(truth_v, suffix=suffix):>14}   {verdict}"


def _print_panel(title: str, live: Dict[str, Any], truth: Dict[str, Any]) -> None:
    print(f"\n┌──── {title} " + "─" * max(0, 60 - len(title)))
    print(_row("trades (n)", live["n_trades"], truth["n_trades"]))
    print(_row("total PnL ($)", live["total_pnl"], truth["total_pnl"]))
    print(_row("total return (%)", live["total_return_pct"], truth["total_return_pct"], suffix="%"))
    print(_row("win rate (%)", live["win_rate_pct"], truth["win_rate_pct"], suffix="%"))
    print(_row("max DD (%)", live["max_drawdown_pct"], truth["max_drawdown_pct"], suffix="%"))
    print(_row("recovery factor", live["recovery_factor"], truth["recovery_factor"]))
    print(_row("avg R winners", live.get("avg_r_winners", 0.0), truth.get("avg_r_winners", 0.0)))
    print(_row("avg R losers", live.get("avg_r_losers", 0.0), truth.get("avg_r_losers", 0.0)))
    if live["n_trades"] < 5:
        print(f"  ⚠ sample n={live['n_trades']} < 5 — verdicts are noise-dominated")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--strategy", required=True, help="Strategy name (e.g. morning_range_reversion)")
    p.add_argument("--account", required=True, help="account_id used in trade_history (string)")
    p.add_argument("--since", help="ISO date (YYYY-MM-DD); defaults to 5 ET sessions ago")
    p.add_argument("--window", default="3m", choices=["3m", "6m", "9m"],
                   help="Backtest truth window to compare against (default: 3m)")
    p.add_argument("--truth", help="Explicit truth recap dir (overrides --window auto-resolution)")
    p.add_argument("--symbols", help="Comma-separated symbol filter for the per-symbol panels")
    args = p.parse_args()

    if args.since:
        try:
            since_d = date.fromisoformat(args.since)
        except ValueError:
            sys.exit(f"❌ --since must be YYYY-MM-DD; got {args.since!r}")
    else:
        since_d = _et_today() - timedelta(days=5)
    since_dt = datetime.combine(since_d, datetime.min.time(), tzinfo=timezone.utc)

    symbols = [s.strip().upper() for s in (args.symbols or "").split(",") if s.strip()] or None
    truth_dir = _resolve_truth_dir(args.strategy, args.window, args.truth)
    truth = _load_truth(truth_dir, args.strategy, symbol_filter=symbols)
    trades = _load_live_trades(args.strategy, args.account, since_dt)

    print("━" * 75)
    print(f" {args.strategy}  (account={args.account}, since {since_d}, truth={args.window})")
    print(f" Truth recap: {truth_dir.relative_to(ROOT)}  (sim_start=${truth.get('sim_start_equity', 0):.0f})")
    print("━" * 75)

    start_equity = truth.get("sim_start_equity", 2000.0) or 2000.0
    live_global = _aggregate(trades, start_equity)
    # Scale the truth panel to a comparable horizon.  The committed truth
    # recap covers e.g. 3 months = ~62 trading days; the live window is
    # typically 5 days.  Use linear scaling on n/total_pnl/return/DD and
    # leave WR/RF/avg_win/avg_loss as ratios (they're rate-independent).
    window_days = {"3m": 62, "6m": 124, "9m": 186}.get(args.window, 62)
    live_days = max((date.today() - since_d).days, 1)
    scale = live_days / window_days
    truth_global_scaled = dict(truth)
    truth_global_scaled["n_trades"] = int(truth["n_trades"] * scale)
    truth_global_scaled["total_pnl"] = truth["total_pnl"] * scale
    truth_global_scaled["total_return_pct"] = truth["total_return_pct"] * scale
    # DD% does NOT scale linearly — keep as-is (truth max-DD is a ceiling).

    _print_panel(f"global  ({live_days}-day live  vs  {args.window} truth × {scale:.2%})",
                 live_global, truth_global_scaled)

    if not symbols:
        symbols = sorted(set(t["symbol"] for t in trades).union(set(truth["per_symbol"].keys())))
    for sym in symbols:
        sym_trades = [t for t in trades if (t.get("symbol") or "").upper() == sym]
        sym_live = _aggregate(sym_trades, start_equity)
        sym_truth = truth["per_symbol"].get(sym)
        if sym_truth is None and sym_live["n_trades"] == 0:
            continue
        if sym_truth is None:
            print(f"\n┌──── {sym}  (no per-symbol truth row)  ────")
            for k in ("n_trades", "total_pnl", "total_return_pct", "win_rate_pct",
                       "max_drawdown_pct", "recovery_factor", "avg_r_winners", "avg_r_losers"):
                v = sym_live[k]
                suffix = "%" if k.endswith("_pct") or k == "win_rate_pct" else ""
                print(f"  {k:<22}  live: {_fmt(v, suffix=suffix):>14}")
            continue
        truth_sym_scaled = dict(sym_truth)
        truth_sym_scaled["n_trades"] = int(sym_truth["n_trades"] * scale)
        truth_sym_scaled["total_pnl"] = sym_truth["total_pnl"] * scale
        truth_sym_scaled["total_return_pct"] = sym_truth["total_return_pct"] * scale
        _print_panel(f"{sym}  ({live_days}-day live  vs  {args.window} truth × {scale:.2%})",
                     sym_live, truth_sym_scaled)

    print()
    print("legend:  ✓ within ±30% of expectation   ⚠ 30-60% drift   ✗ >60% drift (STOP & investigate)")
    print()


if __name__ == "__main__":
    main()
