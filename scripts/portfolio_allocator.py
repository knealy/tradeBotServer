#!/usr/bin/env python3
"""Capital-allocator: rank production strategies + suggest per-account assignment.

The 2026-06-09 ``docs/PORTFOLIO_BLUEPRINT.md`` worked out the per-account
assignment by HAND — ordered the four production strategies by expected
$-PnL, called out hard conflicts (same symbol + opposite direction in
same window), and recommended Account-1 ← MRR / Account-2 ← overnight_
range / Account-3 ← vwap_zscore / Account-4 ← overnight_reversion.

This tool DERIVES that allocation programmatically so the operator can:

  1. Re-run after any truth-recap refresh and surface ALLOCATION DRIFT
     (a new round-tune that lifts overnight_range's RF above MRR's
     would suggest a swap, which the manual blueprint wouldn't catch).
  2. Reason about correlations EMPIRICALLY (daily-PnL Pearson across
     overlapping fold windows) — pairs with corr ≥ +0.6 should be
     spread across different accounts to diversify regime exposure.
  3. Add a new validated strategy to the candidate set without
     re-doing the manual ranking.

The MVP scoring rule:

  ``score = (RF * 0.4) + (Sharpe_ish * 0.4) + (-WorstDD% * 0.2)``

where ``Sharpe_ish = mean_daily_R / std_daily_R`` computed across the
strategy's per-day PnL series (extracted from each trade chart HTML).
Higher score = better candidate.  Allocation greedy-assigns the
top-N (where N = ``--accounts``) BUT penalises any candidate whose
Pearson correlation with an already-assigned strategy ≥
``--corr-threshold`` (default 0.6); penalised candidates are demoted
in favor of orthogonal alternatives.

Conflict matrix is derived from each strategy's TOML
(``meta.symbols`` + ``meta.timeframe`` + signal direction policy).
Hard conflicts (same-symbol, opposite-direction, overlapping window)
emit a WARN in the output but do NOT block — they're respected
implicitly by the one-strategy-per-account rule.

Usage::

    # Default: all production-tier strategies, 4 accounts, daily-Pearson corr
    .venv/bin/python scripts/portfolio_allocator.py

    # Specific candidate set + 2 accounts (e.g. only 2 prop seats available)
    .venv/bin/python scripts/portfolio_allocator.py --accounts 2 \\
        --candidates morning_range_reversion,overnight_range

    # JSON output (machine-readable for downstream dashboards)
    .venv/bin/python scripts/portfolio_allocator.py --json

This is a STARTING POINT, not the final allocator.  Open work (filed
as Issue / future CHANGELOG entry):

  * Use 1m intraday correlations not daily (better signal for
    intraday strategies).
  * Add transaction-cost-adjusted Sharpe (commissions matter for
    high-turnover strategies).
  * Honor portfolio-daily-breaker arithmetic so the suggestion never
    pushes COMBINED worst-case-day above $1000.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import statistics
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


# ───────────────── default production-tier strategies ────────────────
# Single source of truth for the strategies in the live arsenal.  When
# new strategies are promoted to production, they go in BOTH the
# STRATEGY_ARSENAL.md "Production tier" section AND here.  The
# truth_recap_dir is the canonical 3m truth recap output dir.

PRODUCTION_TIER: List[Dict[str, str]] = [
    {"id": "morning_range_reversion",
     "truth_recap_dir": "docs/perf/morning_range_r28_truth_3m",
     "toml": "config/strategies/morning_range_reversion.toml"},
    {"id": "overnight_range",
     "truth_recap_dir": "docs/perf/overnight_range_r24_truth_3m",
     "toml": "config/strategies/overnight_range.toml"},
    {"id": "vwap_zscore_reversion",
     "truth_recap_dir": "docs/perf/vwap_zscore_revival_truth_3m",
     "toml": "config/strategies/vwap_zscore_reversion.toml"},
    {"id": "overnight_reversion",
     "truth_recap_dir": "docs/perf/overnight_reversion_revival_truth_3m",
     "toml": "config/strategies/overnight_reversion.toml"},
    {"id": "opening_range_breakout",
     "truth_recap_dir": "docs/perf/_opt_runs/orb_strategy_mnq_focused/3m/best_60min_long_only_skip_fri",
     "toml": "config/strategies/opening_range_breakout.toml"},
]


# ──────────────────── trade-chart parser ─────────────────────────────


# Each trade chart HTML carries a JSON-shaped overlay with
# entry_time / exit_time / pnl / exit_reason.  Centralised regex so
# the parser is testable.
_TRADE_OVERLAY_RE = re.compile(
    r'tradeOverlays\s*=\s*(\[\s*\{[^\[\]]*\}\s*\]);'
)


def _load_strategy_trades(truth_dir: Path) -> List[Dict[str, Any]]:
    """Walk ``truth_dir/trades/`` extracting per-trade overlay JSON.

    Returns a list of ``{trade_id, side, entry_time, exit_time, pnl}``
    dicts (no ``symbol`` — that's encoded in the filename).  Empty
    list if the directory has no trade chart HTML files.
    """
    trades: List[Dict[str, Any]] = []
    trades_dir = truth_dir / "trades"
    if not trades_dir.exists():
        return trades
    for html in sorted(trades_dir.glob("*.html")):
        text = html.read_text(errors="ignore")
        m = _TRADE_OVERLAY_RE.search(text)
        if not m:
            continue
        try:
            payload = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, list) or not payload:
            continue
        # Each html has a SINGLE trade overlay (the trade the chart
        # is centered on).  Extract that one + the symbol from the
        # filename (``foldN_<strategy>_<SYMBOL>_T...html``).
        parts = html.stem.split("_")
        # Filename schema: "fold{N}_{strategy_name_parts...}_{SYMBOL}_T{NNNN}_{idx}_trade_chart"
        # Symbol is the part immediately before T0000NN.
        sym: Optional[str] = None
        for i, p in enumerate(parts):
            if p.startswith("T") and p[1:].isdigit() and i > 0:
                sym = parts[i - 1]
                break
        for trade in payload:
            trade["symbol"] = sym
            trades.append(trade)
    # Sort by exit_time for deterministic downstream processing.
    def _key(t: Dict[str, Any]) -> int:
        ev = t.get("exit_time", 0)
        if isinstance(ev, (int, float)):
            return int(ev)
        if isinstance(ev, str):
            try:
                return int(datetime.fromisoformat(ev.replace("Z", "+00:00")).timestamp())
            except Exception:
                return 0
        return 0
    trades.sort(key=_key)
    return trades


def _trade_exit_date(trade: Dict[str, Any]) -> Optional[date]:
    ev = trade.get("exit_time")
    if isinstance(ev, (int, float)):
        return datetime.fromtimestamp(ev).date()
    if isinstance(ev, str):
        try:
            return datetime.fromisoformat(ev.replace("Z", "+00:00")).date()
        except Exception:
            return None
    return None


# ───────────────────── per-strategy report ───────────────────────────


@dataclass
class _StrategyReport:
    strategy_id: str
    truth_recap_dir: Path
    # Aggregate stats from metrics_insights.json
    n_trades: int = 0
    total_pnl: float = 0.0
    total_return_pct: float = 0.0
    max_drawdown_pct: float = 0.0
    win_rate: float = 0.0
    recovery_factor: float = 0.0
    avg_r_winners: float = 0.0
    avg_r_losers: float = 0.0
    # Per-day PnL series for correlation
    daily_pnl: Dict[date, float] = field(default_factory=dict)
    # Conflict surface from TOML
    symbols: List[str] = field(default_factory=list)
    direction: str = "?"          # "long_only" / "short_only" / "both"
    window_label: str = "?"
    # Computed by `_finalise`
    sharpe_ish: float = 0.0       # mean / stdev of daily PnL
    score: float = 0.0

    def finalise(self) -> None:
        if self.daily_pnl:
            values = list(self.daily_pnl.values())
            mean_d = statistics.fmean(values)
            std_d = statistics.pstdev(values) if len(values) >= 2 else 0.0
            self.sharpe_ish = (mean_d / std_d) if std_d > 0 else 0.0
        # Score: RF * 0.4 + Sharpe * 0.4 + (-DD%) * 0.2 (so lower DD = higher score)
        dd_term = -self.max_drawdown_pct / 100.0  # negative DD% → positive score
        self.score = (self.recovery_factor * 0.4
                      + self.sharpe_ish * 0.4
                      + dd_term * 0.2)

    def to_json(self) -> Dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "n_trades": self.n_trades,
            "total_pnl": round(self.total_pnl, 2),
            "total_return_pct": round(self.total_return_pct, 2),
            "max_drawdown_pct": round(self.max_drawdown_pct, 2),
            "win_rate": round(self.win_rate, 3),
            "recovery_factor": round(self.recovery_factor, 3),
            "avg_r_winners": round(self.avg_r_winners, 3),
            "avg_r_losers": round(self.avg_r_losers, 3),
            "sharpe_ish": round(self.sharpe_ish, 3),
            "score": round(self.score, 3),
            "symbols": self.symbols,
            "direction": self.direction,
            "window": self.window_label,
            "n_trading_days": len(self.daily_pnl),
        }


def _load_strategy_report(spec: Dict[str, str]) -> Optional[_StrategyReport]:
    truth = ROOT / spec["truth_recap_dir"]
    metrics_path = truth / "metrics_insights.json"
    if not metrics_path.exists():
        return None
    data = json.loads(metrics_path.read_text())
    g = data.get("grand", {}) or {}
    es = g.get("equity_summary", {}) or {}
    ex = g.get("extended", {}) or {}

    rep = _StrategyReport(strategy_id=spec["id"], truth_recap_dir=truth)
    rep.n_trades = int(es.get("n_trades", 0) or 0)
    rep.total_pnl = float(es.get("final_equity", 0.0) - es.get("start_equity", 0.0))
    rep.total_return_pct = float(es.get("total_return_pct", 0.0))
    rep.max_drawdown_pct = float(es.get("max_drawdown_pct", 0.0))
    rep.win_rate = float(ex.get("actual_win_rate", 0.0))
    rep.recovery_factor = float(ex.get("recovery_factor_pnl_vs_seq_dd", 0.0))
    rep.avg_r_winners = float(ex.get("avg_r_winners", 0.0) or 0.0)
    rep.avg_r_losers = float(ex.get("avg_r_losers", 0.0) or 0.0)

    # Per-day PnL series from trade chart HTML overlays.  The
    # overlay JSON doesn't carry the ``pnl`` field directly — only
    # entry/exit price + side — so we compute a per-symbol unit-PnL
    # proxy.  This is fine for Pearson correlation (scale-invariant)
    # and good enough for the Sharpe-ish ratio used in the allocator
    # score; for $-PnL aggregation we'd need point_value per symbol.
    POINT_VALUE = {"MES": 5.0, "MNQ": 2.0, "MGC": 10.0}
    trades = _load_strategy_trades(truth)
    for trade in trades:
        d = _trade_exit_date(trade)
        if d is None:
            continue
        pnl_raw = trade.get("pnl")
        if pnl_raw is not None:
            pnl = float(pnl_raw)
        else:
            try:
                ep = float(trade.get("entry_price") or 0.0)
                xp = float(trade.get("exit_price") or 0.0)
                side_sign = 1 if str(trade.get("side", "")).upper() in ("BUY", "LONG") else -1
                sym = str(trade.get("symbol", "")).upper()
                pv = POINT_VALUE.get(sym, 1.0)
                pnl = (xp - ep) * side_sign * pv
            except (TypeError, ValueError):
                continue
        rep.daily_pnl[d] = rep.daily_pnl.get(d, 0.0) + pnl

    # Symbols + direction + window from TOML (best-effort tolerance:
    # if tomllib missing or TOML malformed, we still return the report
    # with empty fields so the user sees aggregate ranking).
    try:
        import tomllib
        toml_path = ROOT / spec["toml"]
        if toml_path.exists():
            tdoc = tomllib.loads(toml_path.read_text())
            meta = tdoc.get("meta") or {}
            sig = tdoc.get("signal") or {}
            rep.symbols = list(meta.get("symbols") or [])
            allow_long = bool(sig.get("allow_long", True))
            allow_short = bool(sig.get("allow_short", True))
            if allow_long and not allow_short:
                rep.direction = "long_only"
            elif allow_short and not allow_long:
                rep.direction = "short_only"
            else:
                rep.direction = "both"
            # Window: pick whichever common timing field exists.
            timing = tdoc.get("timing") or {}
            t_start = timing.get("range_start") or timing.get("start_time") or "?"
            t_end = timing.get("flat_before") or timing.get("end_time") or "?"
            rep.window_label = f"{t_start} → {t_end}"
    except Exception:
        pass

    rep.finalise()
    return rep


# ─────────────────── pairwise correlation ────────────────────────────


def _pearson(xs: List[float], ys: List[float]) -> float:
    n = len(xs)
    if n < 3 or len(ys) != n:
        return 0.0
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((xs[i] - mx) * (ys[i] - my) for i in range(n))
    sx = math.sqrt(sum((xs[i] - mx) ** 2 for i in range(n)))
    sy = math.sqrt(sum((ys[i] - my) ** 2 for i in range(n)))
    if sx <= 0 or sy <= 0:
        return 0.0
    return num / (sx * sy)


def _correlation_matrix(reports: List[_StrategyReport]) -> Dict[Tuple[str, str], float]:
    """Daily-PnL Pearson correlation across all overlapping calendar days."""
    out: Dict[Tuple[str, str], float] = {}
    for i, a in enumerate(reports):
        for j, b in enumerate(reports):
            if i >= j:
                continue
            shared = sorted(set(a.daily_pnl.keys()) & set(b.daily_pnl.keys()))
            if len(shared) < 3:
                out[(a.strategy_id, b.strategy_id)] = float("nan")
                continue
            xs = [a.daily_pnl[d] for d in shared]
            ys = [b.daily_pnl[d] for d in shared]
            out[(a.strategy_id, b.strategy_id)] = _pearson(xs, ys)
    return out


# ───────────────────── conflict matrix ───────────────────────────────


def _conflicts(reports: List[_StrategyReport]) -> List[Tuple[str, str, str]]:
    """Hard conflicts = same symbol + (one allows long AND other allows short)."""
    out: List[Tuple[str, str, str]] = []
    for i, a in enumerate(reports):
        for j, b in enumerate(reports):
            if i >= j:
                continue
            shared_syms = set(a.symbols) & set(b.symbols)
            if not shared_syms:
                continue
            # If either is "both", and they share a symbol, they can
            # produce opposing positions on that symbol.
            either_both = a.direction == "both" or b.direction == "both"
            opposite_one_sided = (
                (a.direction == "long_only" and b.direction == "short_only") or
                (a.direction == "short_only" and b.direction == "long_only")
            )
            if either_both or opposite_one_sided:
                out.append((a.strategy_id, b.strategy_id, ",".join(sorted(shared_syms))))
    return out


# ───────────────────── allocation engine ─────────────────────────────


def _suggest_allocation(reports: List[_StrategyReport],
                        corr: Dict[Tuple[str, str], float],
                        *,
                        n_accounts: int,
                        corr_threshold: float = 0.6) -> List[Dict[str, Any]]:
    """Greedy: sort by score desc; assign top-N; demote candidates whose
    correlation with an already-assigned strategy ≥ corr_threshold."""
    ranked = sorted(reports, key=lambda r: r.score, reverse=True)
    assignments: List[Dict[str, Any]] = []
    assigned_ids: List[str] = []
    for cand in ranked:
        if len(assignments) >= n_accounts:
            break
        # Find max corr against already-assigned set.
        max_corr = 0.0
        worst_partner: Optional[str] = None
        for placed in assigned_ids:
            key = tuple(sorted([cand.strategy_id, placed]))
            c = corr.get(key, 0.0)
            if c != c:  # NaN
                continue
            if abs(c) > max_corr:
                max_corr = abs(c)
                worst_partner = placed
        suppressed = max_corr >= corr_threshold
        if suppressed and len(assigned_ids) < len(ranked) - 1:
            # Look for an orthogonal alternative further down the ranking.
            for alt in ranked:
                if alt.strategy_id in assigned_ids or alt.strategy_id == cand.strategy_id:
                    continue
                alt_max = 0.0
                for placed in assigned_ids:
                    key = tuple(sorted([alt.strategy_id, placed]))
                    c = corr.get(key, 0.0)
                    if c == c:
                        alt_max = max(alt_max, abs(c))
                if alt_max < corr_threshold:
                    cand = alt
                    max_corr = alt_max
                    worst_partner = None
                    break
        assignments.append({
            "account": f"Account {len(assignments) + 1}",
            "strategy": cand.strategy_id,
            "score": round(cand.score, 3),
            "rf": round(cand.recovery_factor, 2),
            "sharpe_ish": round(cand.sharpe_ish, 3),
            "max_dd_pct": round(cand.max_drawdown_pct, 2),
            "symbols": cand.symbols,
            "direction": cand.direction,
            "corr_max_to_assigned": round(max_corr, 3) if max_corr else 0.0,
            "corr_partner": worst_partner,
        })
        assigned_ids.append(cand.strategy_id)
    return assignments


# ───────────────────── output formatting ─────────────────────────────


def _print_table(reports: List[_StrategyReport]) -> None:
    print()
    print("  per-strategy summary (truth recap, 3m)")
    print("  " + "─" * 99)
    print(f"  {'strategy':<28}  {'n':>4}  {'WR%':>5}  {'RF':>6}  {'Ret%':>7}  "
          f"{'DD%':>6}  {'Sharpe':>7}  {'score':>6}")
    print("  " + "─" * 99)
    for r in reports:
        print(f"  {r.strategy_id:<28}  {r.n_trades:>4}  "
              f"{r.win_rate * 100:>5.1f}  {r.recovery_factor:>6.2f}  "
              f"{r.total_return_pct:>7.1f}  {r.max_drawdown_pct:>6.2f}  "
              f"{r.sharpe_ish:>+7.3f}  {r.score:>+6.3f}")


def _print_corr_matrix(reports: List[_StrategyReport],
                        corr: Dict[Tuple[str, str], float]) -> None:
    if len(reports) < 2:
        return
    print()
    print("  daily-PnL Pearson correlation matrix")
    print("  " + "─" * 80)
    ids = [r.strategy_id for r in reports]
    header = "  " + "".join(f"{r.strategy_id[:14]:<16}" for r in reports[1:])
    print("  " + " " * 18 + header)
    for i, a in enumerate(reports[:-1]):
        row = f"  {a.strategy_id[:16]:<16}  "
        for j, b in enumerate(reports[1:], start=1):
            if j <= i:
                row += " " * 16
                continue
            key = tuple(sorted([a.strategy_id, b.strategy_id]))
            c = corr.get(key, 0.0)
            if c != c:
                cell = " (n<3)"
            else:
                marker = "🔴" if abs(c) >= 0.6 else ("🟡" if abs(c) >= 0.4 else "🟢")
                cell = f"{c:+.3f} {marker}"
            row += f"{cell:<16}"
        print(row)


def _print_conflicts(conflicts: List[Tuple[str, str, str]]) -> None:
    if not conflicts:
        return
    print()
    print("  ⚠  HARD CONFLICTS (same symbol, opposing-direction-possible window)")
    for a, b, syms in conflicts:
        print(f"    {a}  ⇄  {b}  on {syms}")
    print()
    print("    Resolution: assign these strategies to DIFFERENT prop accounts.")


def _print_allocation(assignments: List[Dict[str, Any]]) -> None:
    print()
    print("  suggested per-account allocation")
    print("  " + "─" * 92)
    for a in assignments:
        marker = ""
        if a["corr_max_to_assigned"] >= 0.6:
            marker = f"  ⚠ corr {a['corr_max_to_assigned']:+.2f} to {a['corr_partner']}"
        print(f"  {a['account']:<12}  ← {a['strategy']:<28}  "
              f"score={a['score']:+.3f}  RF={a['rf']:.2f}  "
              f"Sharpe={a['sharpe_ish']:+.3f}  DD={a['max_dd_pct']:.1f}%{marker}")


# ─────────────────────────── main ────────────────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--candidates", type=str, default=None,
                     help="Comma-separated strategy IDs (default = all of PRODUCTION_TIER)")
    ap.add_argument("--accounts", type=int, default=4,
                     help="Number of prop accounts to allocate (default 4)")
    ap.add_argument("--corr-threshold", type=float, default=0.6,
                     help="Pearson |corr| ≥ this triggers demote (default 0.6)")
    ap.add_argument("--json", action="store_true",
                     help="Emit JSON to stdout (machine-readable for dashboards)")
    args = ap.parse_args()

    if args.candidates:
        wanted = [s.strip() for s in args.candidates.split(",") if s.strip()]
        specs = [s for s in PRODUCTION_TIER if s["id"] in wanted]
        missing = set(wanted) - {s["id"] for s in specs}
        if missing:
            sys.exit(f"❌ unknown strategy id(s): {sorted(missing)}.  "
                     f"Valid: {[s['id'] for s in PRODUCTION_TIER]}")
    else:
        specs = list(PRODUCTION_TIER)

    reports: List[_StrategyReport] = []
    for spec in specs:
        rep = _load_strategy_report(spec)
        if rep is None:
            print(f"  ⚠  {spec['id']}: missing truth recap at {spec['truth_recap_dir']}")
            continue
        reports.append(rep)
    if not reports:
        sys.exit("❌ no strategy reports loadable; check truth_recap_dir paths in PRODUCTION_TIER")

    # Sort by score for the per-strategy print.
    reports.sort(key=lambda r: r.score, reverse=True)
    corr = _correlation_matrix(reports)
    conflicts = _conflicts(reports)
    assignments = _suggest_allocation(reports, corr, n_accounts=args.accounts,
                                       corr_threshold=args.corr_threshold)

    if args.json:
        out = {
            "reports": [r.to_json() for r in reports],
            "correlation": {
                # Force lexical key ordering so downstream consumers
                # can rely on ``a < b`` (each pair appears exactly once).
                f"{min(a, b)}__vs__{max(a, b)}": (c if c == c else None)
                for (a, b), c in corr.items()
            },
            "conflicts": [{"a": a, "b": b, "shared_symbols": syms}
                          for a, b, syms in conflicts],
            "allocation": assignments,
            "params": {
                "accounts": args.accounts,
                "corr_threshold": args.corr_threshold,
            },
        }
        print(json.dumps(out, indent=2, default=str))
        return 0

    _print_table(reports)
    _print_corr_matrix(reports, corr)
    _print_conflicts(conflicts)
    _print_allocation(assignments)
    return 0


if __name__ == "__main__":
    sys.exit(main())
