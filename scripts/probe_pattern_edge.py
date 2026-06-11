#!/usr/bin/env python3
"""Truth-mode-always pattern edge probe across MES + MNQ + MGC.

The legacy ``scripts/simulate_price_action_trades.py`` does everything,
but it has a sprawling CLI and a legacy (over-optimistic) simulator
path that should NEVER be used for forward research after the 2026-06-11
truth-mode convergence work.  This probe is the **opinionated** entry
point for "does pattern X have an edge across the arsenal?":

  * Truth-mode is ALWAYS ON — entry-slippage, stop gap-through clamp,
    commissions, force-flat at 16:00 ET, 1m intrabar resolution when
    the canonical ``*_1m_databento.csv`` exists.
  * Runs all three production symbols (MES + MNQ + MGC) in a single
    invocation with per-symbol point-value / tick-size conventions
    baked in.
  * Optional structural filters (Order Block, FVG, session, prior-
    day H/L proximity) mirror the simulator but with a SHORT flag
    syntax tailored for one-line iteration.
  * Outputs a clean comparison table + a JSON summary that
    downstream tooling (capital allocator, automated promotion
    pipeline) can read directly.

Usage::

    # Truth-mode probe of dragonfly_doji + Order Block confluence
    # across all three symbols, 9 months, contrarian fade, 1×ATR
    # stop / 2R target.
    .venv/bin/python scripts/probe_pattern_edge.py \\
        --pattern dragonfly_doji --bias contrarian \\
        --require-ob --since 2025-09-01 --until 2026-06-01

    # Without OB filter for a baseline read
    .venv/bin/python scripts/probe_pattern_edge.py \\
        --pattern gravestone_doji --bias contrarian \\
        --since 2025-12-01 --until 2026-06-01 --json

The exit code is 0 iff every symbol produced ≥ ``--min-n`` (default 20)
trades — if any symbol falls short, the exit is 2 so CI / promotion
pipelines see the sample-size warning as a hard failure.

Decision rule (printed at the bottom):

  * **PRODUCTIONABLE** → mean R ≥ +0.25 AND n ≥ 30 AND WR ≥ 35 % on
    AT LEAST 2 of the 3 symbols.  Worth spawning an MVP.
  * **MARGINAL**       → at least one symbol with mean R ∈ [+0.10, +0.25].
    Track but don't deploy.
  * **NO EDGE**        → otherwise.

After 2026-06-11, this is the ONE tool that should be referenced when
promoting / retiring a pattern.  The legacy simulator stays for
deep-dive research (multi-pattern, stress-tests, SMC compound setups);
this is the screening front-door.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Reuse the canonical simulator helpers so the engine model lives in
# ONE place (truth-mode logic, ATR, pattern detection — all shared).
from scripts.simulate_price_action_trades import (  # type: ignore
    CandleBar,
    _read_csv,
    _wilder_atr,
    _simulate_trade_truth,
    _direction_for,
    _DIRECTIONS,
)
from core.price_action import Pattern, detect_patterns
from core.market_structure import (
    classify_session,
    find_order_blocks,
    is_inside_order_block,
    find_fair_value_gaps,
    is_inside_fvg,
    Session,
)


# ────────────────────── per-symbol conventions ───────────────────────


_SYMBOLS: Dict[str, Dict[str, Any]] = {
    "MES": {
        "tick_size": 0.25, "point_value": 5.0,
        "csv_5m": "historical_data/price/MES_5m_databento.csv",
        "csv_1m": "historical_data/price/MES_1m_databento.csv",
    },
    "MNQ": {
        "tick_size": 0.25, "point_value": 2.0,
        "csv_5m": "historical_data/price/MNQ_5m_databento.csv",
        "csv_1m": "historical_data/price/MNQ_1m_databento.csv",
    },
    "MGC": {
        "tick_size": 0.1, "point_value": 10.0,
        "csv_5m": "historical_data/price/MGC_5m_databento.csv",
        "csv_1m": "historical_data/price/MGC_1m_databento.csv",
    },
}


# ────────────────────── result aggregation ───────────────────────────


@dataclass
class _SymbolReport:
    symbol: str
    n_signals: int = 0
    n_filtered_pre_trade: int = 0  # patterns rejected by structural filter
    n_invalid: int = 0             # truth-mode returned "invalid"
    n_trades: int = 0
    n_wins: int = 0
    n_losses: int = 0
    n_breakeven: int = 0
    r_total: float = 0.0
    r_sq: float = 0.0
    best_r: float = 0.0
    worst_r: float = 0.0
    exits: Dict[str, int] = field(default_factory=dict)

    def add(self, r: float, exit_reason: str) -> None:
        self.n_trades += 1
        self.r_total += r
        self.r_sq += r * r
        self.best_r = max(self.best_r, r)
        self.worst_r = min(self.worst_r, r)
        self.exits[exit_reason] = self.exits.get(exit_reason, 0) + 1
        if r > 1e-9:
            self.n_wins += 1
        elif r < -1e-9:
            self.n_losses += 1
        else:
            self.n_breakeven += 1

    @property
    def mean_r(self) -> float:
        return self.r_total / self.n_trades if self.n_trades else 0.0

    @property
    def std_r(self) -> float:
        if self.n_trades < 2:
            return 0.0
        m = self.mean_r
        var = (self.r_sq / self.n_trades) - (m * m)
        return var ** 0.5 if var > 0 else 0.0

    @property
    def wr_pct(self) -> float:
        return (self.n_wins / self.n_trades * 100.0) if self.n_trades else 0.0

    @property
    def profit_factor(self) -> float:
        """Gross winning R / |gross losing R|.  Infinity → no losses."""
        # Note: r_total = win_r + loss_r.  We don't store win_r and
        # loss_r separately, so derive from extremes-aware sums.
        if self.n_losses == 0 and self.n_wins == 0:
            return 0.0
        if self.n_losses == 0:
            return float("inf")
        # Approximate: average winning R × n_wins / |average losing R × n_losses|.
        # Since we only have totals + counts, just use n-weighted:
        # gross_win_r ≈ n_wins × avg_win_r; we don't track avg_win_r
        # separately so PF is an APPROXIMATION using mean_r split.
        # For probe purposes mean_r already tells the story; PF is a
        # secondary heuristic.  Track gross win/loss r separately if
        # this ever matters for promotion logic.
        return float("nan")

    @property
    def sharpe_ish(self) -> float:
        s = self.std_r
        return self.mean_r / s if s > 0 else 0.0

    def to_json(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "n_signals": self.n_signals,
            "n_filtered_pre_trade": self.n_filtered_pre_trade,
            "n_invalid": self.n_invalid,
            "n_trades": self.n_trades,
            "n_wins": self.n_wins,
            "n_losses": self.n_losses,
            "n_breakeven": self.n_breakeven,
            "wr_pct": round(self.wr_pct, 2),
            "mean_r": round(self.mean_r, 3),
            "std_r": round(self.std_r, 3),
            "sharpe_ish": round(self.sharpe_ish, 3),
            "best_r": round(self.best_r, 3),
            "worst_r": round(self.worst_r, 3),
            "exits": dict(sorted(self.exits.items())),
        }


# ────────────────────── per-symbol probe ─────────────────────────────


def _probe_symbol(
    symbol: str,
    *,
    pattern_name: str,
    bias: str,
    args: argparse.Namespace,
) -> _SymbolReport:
    cfg = _SYMBOLS[symbol]
    csv5 = ROOT / cfg["csv_5m"]
    csv1 = ROOT / cfg["csv_1m"]
    if not csv5.exists():
        print(f"  ⚠  {symbol}: missing 5m CSV {csv5}; skipping")
        return _SymbolReport(symbol=symbol)

    since = datetime.fromisoformat(args.since) if args.since else None
    until = datetime.fromisoformat(args.until) if args.until else None
    bars = _read_csv(csv5, since, until)
    if not bars:
        print(f"  ⚠  {symbol}: 0 bars after since/until filter")
        return _SymbolReport(symbol=symbol)

    bars_1m_by_ns: Optional[Dict[int, CandleBar]] = None
    if csv1.exists() and not args.no_1m:
        bars_1m = _read_csv(csv1, since, until)
        bars_1m_by_ns = {int(b.timestamp.timestamp() * 1e9): b for b in bars_1m}

    atrs = _wilder_atr(bars, args.atr_period)

    # Pre-compute order blocks / FVGs if a structural filter is requested.
    obs = find_order_blocks(bars, atr_period=args.atr_period,
                            impulse_threshold_atr=args.ob_impulse_atr,
                            window=args.ob_window) if args.require_ob else []
    fvgs = find_fair_value_gaps(bars) if args.require_fvg else []

    # Optional session filter
    session_filter: Optional[Session] = None
    if args.session:
        try:
            session_filter = Session(args.session)
        except ValueError:
            sys.exit(f"❌ unknown --session {args.session!r}; choices: {[s.value for s in Session]}")

    target_pattern = pattern_name
    report = _SymbolReport(symbol=symbol)

    # Force-flat minutes (default 16:00 ET = 960; pass empty/0 to disable).
    fflat_min: Optional[int]
    if args.force_flat_et:
        h, m = args.force_flat_et.split(":")
        fflat_min = int(h) * 60 + int(m)
    else:
        fflat_min = None

    # Pattern detection minimum history (varies by pattern; the detector
    # handles its own min-bars internally so we pass full slices).
    for i in range(args.atr_period + 5, len(bars) - 1):
        atr = atrs[i]
        if atr is None or atr <= 0:
            continue
        # Slice up to and including bar `i`.  detect_patterns returns
        # events tagged to the LAST bar of the slice.
        window = bars[max(0, i - 30): i + 1]
        events = detect_patterns(window)
        hit = next((e for e in events if e.name == target_pattern), None)
        if hit is None:
            continue
        report.n_signals += 1

        # ── Structural pre-trade filters ───────────────────────────────
        if session_filter is not None:
            if classify_session(bars[i].timestamp) != session_filter:
                report.n_filtered_pre_trade += 1
                continue

        direction = _direction_for(target_pattern, bias)
        if direction == 0:
            report.n_filtered_pre_trade += 1
            continue

        if args.require_ob:
            # OB ``direction`` is the OB KIND (+1 = bullish OB = support,
            # -1 = bearish OB = resistance), NOT the trade direction.
            # A SHORT trade should fire at a BEARISH OB → ``direction=-1``;
            # a LONG trade at a BULLISH OB → ``direction=+1``.  In both
            # cases that's the SAME sign as the trade ``direction``.
            ob_match = is_inside_order_block(
                obs, i, bars[i].close,
                direction=direction, require_unmitigated=True,
            )
            if ob_match is None:
                report.n_filtered_pre_trade += 1
                continue

        if args.require_fvg:
            # FVG in same direction as the trade.
            fvg_match = is_inside_fvg(
                fvgs, i, bars[i].close,
                direction=direction, require_unmitigated=True,
            )
            if fvg_match is None:
                report.n_filtered_pre_trade += 1
                continue

        # ── Trade simulation (truth-mode always) ──────────────────────
        stop_dist = atr * args.stop_atr
        tp_dist = atr * args.tp_atr
        r_pnl, exit_reason, bars_held = _simulate_trade_truth(
            bars, i, direction, stop_dist, tp_dist, args.max_bars,
            commission_per_trade=args.commission_per_trade,
            slippage_ticks=args.slippage_ticks,
            tick_size=cfg["tick_size"],
            point_value=cfg["point_value"],
            bars_1m_by_ns=bars_1m_by_ns,
            agg_minutes=5,
            force_flat_et_minutes=fflat_min,
        )
        if exit_reason == "invalid":
            report.n_invalid += 1
            continue
        report.add(r_pnl, exit_reason)

    return report


# ────────────────────── verdict logic ────────────────────────────────


def _verdict(reports: List[_SymbolReport]) -> str:
    productionable_syms = sum(
        1 for r in reports
        if r.n_trades >= 30 and r.mean_r >= 0.25 and r.wr_pct >= 35
    )
    if productionable_syms >= 2:
        return "PRODUCTIONABLE"
    if any(r.n_trades >= 20 and 0.10 <= r.mean_r < 0.25 for r in reports):
        return "MARGINAL"
    return "NO EDGE"


# ────────────────────── main / output ────────────────────────────────


def _print_table(reports: List[_SymbolReport]) -> None:
    print()
    print("  Truth-mode per-symbol report")
    print("  " + "─" * 84)
    print(f"  {'symbol':<6}  {'signals':>7}  {'filtered':>8}  {'trades':>6}  "
          f"{'WR%':>6}  {'mean R':>8}  {'std R':>6}  {'sharpe-ish':>10}")
    print("  " + "─" * 84)
    for r in reports:
        wr = f"{r.wr_pct:.1f}" if r.n_trades else "—"
        mr = f"{r.mean_r:+.3f}" if r.n_trades else "—"
        sr = f"{r.std_r:.3f}" if r.n_trades else "—"
        sh = f"{r.sharpe_ish:+.3f}" if r.n_trades else "—"
        print(f"  {r.symbol:<6}  {r.n_signals:>7}  {r.n_filtered_pre_trade:>8}  "
              f"{r.n_trades:>6}  {wr:>6}  {mr:>8}  {sr:>6}  {sh:>10}")


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    pat = ap.add_argument_group("Pattern selection")
    pat.add_argument("--pattern", required=True,
                     help="Pattern name (one of Pattern.* — e.g. dragonfly_doji, "
                          "gravestone_doji, bullish_engulfing, bullish_marubozu)")
    pat.add_argument("--bias", choices=["continuation", "contrarian"],
                     default="contrarian",
                     help="contrarian = fade the pattern; continuation = trade with the pattern")

    win = ap.add_argument_group("Time window")
    win.add_argument("--since", default=None,
                     help="ISO date — drop bars older than this (e.g. 2025-09-01)")
    win.add_argument("--until", default=None,
                     help="ISO date — drop bars newer than this (e.g. 2026-06-01)")

    sym = ap.add_argument_group("Symbol selection")
    sym.add_argument("--symbols", default="MES,MNQ,MGC",
                     help="Comma-separated subset of {MES,MNQ,MGC} (default: all)")

    geo = ap.add_argument_group("Trade geometry")
    geo.add_argument("--atr-period", type=int, default=14)
    geo.add_argument("--stop-atr", type=float, default=1.0,
                     help="Stop distance in ATR multiples (default 1.0)")
    geo.add_argument("--tp-atr", type=float, default=2.0,
                     help="TP distance in ATR multiples (default 2.0 = 2R)")
    geo.add_argument("--max-bars", type=int, default=12,
                     help="Max bars held before timed-exit fallback (default 12)")

    flt = ap.add_argument_group("Structural filters (optional)")
    flt.add_argument("--require-ob", action="store_true",
                     help="Require pattern bar's close inside an unmitigated, look-ahead-safe Order Block in opposing direction")
    flt.add_argument("--require-fvg", action="store_true",
                     help="Require pattern bar's close inside an unmitigated FVG in trade direction")
    flt.add_argument("--ob-impulse-atr", type=float, default=2.0,
                     help="Order Block impulse threshold in ATR (default 2.0)")
    flt.add_argument("--ob-window", type=int, default=5,
                     help="Order Block confirmation window in bars (default 5)")
    flt.add_argument("--session", default=None,
                     help="Restrict to ET session bucket "
                          "(asia/london/premarket/nyam/lunch/nypm/close/afterhours)")

    eng = ap.add_argument_group("Engine fill semantics (truth-mode is ALWAYS ON)")
    eng.add_argument("--commission-per-trade", type=float, default=5.0)
    eng.add_argument("--slippage-ticks", type=float, default=0.5)
    eng.add_argument("--force-flat-et", default="16:00",
                     help="ET cutoff for force-flat (HH:MM); pass '' to disable")
    eng.add_argument("--no-1m", action="store_true",
                     help="Skip 1m intrabar resolution even if canonical 1m CSV exists")

    out = ap.add_argument_group("Output")
    out.add_argument("--min-n", type=int, default=20,
                     help="Minimum trades per symbol for the run to exit 0 (default 20)")
    out.add_argument("--json", action="store_true",
                     help="Emit JSON summary to stdout (machine-readable for downstream tools)")

    args = ap.parse_args()

    # Validate pattern name early — fail loudly if typo'd.
    valid_patterns = set(getattr(Pattern, "ALL", []))
    if valid_patterns and args.pattern not in valid_patterns:
        sys.exit(f"❌ unknown pattern {args.pattern!r}.  Valid: {sorted(valid_patterns)}")
    if args.pattern not in _DIRECTIONS:
        sys.exit(f"❌ pattern {args.pattern!r} has no direction policy in simulate_price_action_trades._DIRECTIONS")

    syms_requested = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    unknown = [s for s in syms_requested if s not in _SYMBOLS]
    if unknown:
        sys.exit(f"❌ unknown symbol(s): {unknown}.  Valid: {sorted(_SYMBOLS.keys())}")

    print(f"probing pattern={args.pattern!r}  bias={args.bias!r}  "
          f"since={args.since!r}  until={args.until!r}")
    if args.require_ob or args.require_fvg or args.session:
        f = []
        if args.require_ob: f.append("OB")
        if args.require_fvg: f.append("FVG")
        if args.session: f.append(f"session={args.session}")
        print(f"  filters: {' + '.join(f)}")

    reports: List[_SymbolReport] = []
    for sym_name in syms_requested:
        print(f"  → {sym_name} …", flush=True)
        rep = _probe_symbol(sym_name, pattern_name=args.pattern,
                             bias=args.bias, args=args)
        reports.append(rep)

    if args.json:
        out_doc = {
            "pattern": args.pattern,
            "bias": args.bias,
            "since": args.since,
            "until": args.until,
            "filters": {
                "require_ob": bool(args.require_ob),
                "require_fvg": bool(args.require_fvg),
                "session": args.session,
            },
            "geometry": {
                "stop_atr": args.stop_atr,
                "tp_atr": args.tp_atr,
                "max_bars": args.max_bars,
                "atr_period": args.atr_period,
            },
            "engine": {
                "commission_per_trade": args.commission_per_trade,
                "slippage_ticks": args.slippage_ticks,
                "force_flat_et": args.force_flat_et or None,
                "intrabar_1m": not args.no_1m,
            },
            "per_symbol": [r.to_json() for r in reports],
            "verdict": _verdict(reports),
        }
        print(json.dumps(out_doc, indent=2))
    else:
        _print_table(reports)
        print()
        print(f"  VERDICT: {_verdict(reports)}")

    # Sample-size gate
    failed = [r for r in reports if r.n_trades < args.min_n]
    if failed:
        if not args.json:
            print(f"  ⚠  {len(failed)} symbol(s) below min-n={args.min_n}: "
                  f"{[r.symbol for r in failed]}")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
