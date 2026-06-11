#!/usr/bin/env python3
"""Per-trade engine-vs-live PnL diff for the first weeks of live trading.

The 2026-06-11 truth-mode work proved that the legacy PA simulator and
the backtest engine could diverge by **+0.788 R** on the same trade —
purely from execution-modelling differences (entry slippage, stop
gap-through, force-flat, commission).  This tool applies the SAME
discipline at the next layer up: ``engine`` versus ``LIVE``.  Once
MRR + overnight_range are deployed live, we can re-play every live
trade through the engine's known fill semantics and surface any
per-trade deltas larger than slippage noise.

Workflow:

1.  Pull live trades from Postgres ``trade_history`` (or load from
    ``--trades-json`` for offline testing).
2.  For each live trade:
       a. Load the historical 5m bars (and optional 1m bars) covering
          the trade's window from ``historical_data/price/``.
       b. Locate the entry bar by ``entry_time``.
       c. Reconstruct ``stop_dist`` / ``tp_dist`` from the trade's
          ``metadata`` (stop_price / tp_price) when present; otherwise
          infer from the position-size + recorded R from the recap.
       d. Call ``_simulate_trade_truth`` to predict the engine's R-PnL,
          exit reason, bars held.
       e. Diff against the live trade's actual PnL (converted to R via
          the same stop_dist).
3.  Verdict per trade:
       ✓  |Δ R| < 0.25  (within slippage noise; engine model OK)
       ⚠  0.25 ≤ |Δ R| < 1.00  (drift — investigate but not catastrophic)
       ✗  |Δ R| ≥ 1.00  (mismatch — engine model is wrong for this trade)
4.  Aggregate summary at the bottom: mean / median / max |Δ R|, counts
    per verdict bucket, total PnL diff in dollars.

Usage::

    # Pull last 10 sessions of live MRR trades on account 1 and diff
    .venv/bin/python scripts/validate_engine_vs_live.py \\
        --strategy morning_range_reversion --account 1 --since 2026-06-04

    # Offline / smoke test using a hand-crafted JSON
    .venv/bin/python scripts/validate_engine_vs_live.py \\
        --trades-json /tmp/smoke_trades.json --symbol MES

When the engine is wrong (``✗`` row), the diff tells you which
fill-semantic is missing or mis-tuned (commission, slippage ticks,
force-flat cutoff, or — most damning — a STOP fill that should have
gap-through-clamped but didn't).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Reuse the canonical truth-mode simulator + bar reader so the engine
# model lives in ONE place (drift between this tool and the PA sim
# would defeat the purpose).
from scripts.simulate_price_action_trades import (  # type: ignore
    CandleBar,
    _read_csv,
    _simulate_trade_truth,
)


# ──────────────── tick / point conventions per symbol ────────────────


# Engine defaults — see core/backtest/engine.py and trading_bot.py.
# These are the same numbers truth-mode uses; centralised here so the
# CLI surfaces them and `--symbol` selects a sane default.
_SYMBOL_PROFILE: Dict[str, Dict[str, float]] = {
    "MES": {"tick_size": 0.25, "point_value": 5.0},
    "MNQ": {"tick_size": 0.25, "point_value": 2.0},
    "MGC": {"tick_size": 0.1,  "point_value": 10.0},
}


def _resolve_csv_paths(symbol: str, csv_1m: Optional[str],
                       csv_5m: Optional[str],
                       *, no_1m: bool = False) -> Tuple[Path, Optional[Path]]:
    sym = symbol.upper()
    canon_5m = ROOT / "historical_data" / "price" / f"{sym}_5m_databento.csv"
    canon_1m = ROOT / "historical_data" / "price" / f"{sym}_1m_databento.csv"
    p5 = Path(csv_5m) if csv_5m else canon_5m
    if not p5.is_absolute():
        p5 = ROOT / p5
    if not p5.exists():
        sys.exit(f"❌ 5m CSV not found: {p5}")
    p1: Optional[Path] = None
    if no_1m:
        return p5, None
    if csv_1m:
        p1 = Path(csv_1m)
        if not p1.is_absolute():
            p1 = ROOT / p1
        if not p1.exists():
            sys.exit(f"❌ 1m CSV not found: {p1}")
    elif canon_1m.exists():
        p1 = canon_1m
    return p5, p1


def _ns(ts: datetime) -> int:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return int(ts.timestamp() * 1e9)


def _index_bars_by_ns(bars: List[CandleBar]) -> Dict[int, CandleBar]:
    return {_ns(b.timestamp): b for b in bars}


def _find_entry_index(bars: List[CandleBar], entry_time: datetime,
                       tol_seconds: int = 600) -> Optional[int]:
    """Locate the bar whose ``timestamp`` brackets ``entry_time``.

    A live entry timestamp rarely lands on an exact bar open; we accept
    the most recent bar whose open is ≤ entry_time AND within
    ``tol_seconds`` (default 10 minutes — wide enough for 5m bars +
    network latency, tight enough to fail loudly if the bar series is
    the wrong day).  Returns ``None`` if no candidate exists.
    """
    if entry_time.tzinfo is None:
        entry_time = entry_time.replace(tzinfo=timezone.utc)
    best: Optional[int] = None
    for i, b in enumerate(bars):
        bt = b.timestamp
        if bt.tzinfo is None:
            bt = bt.replace(tzinfo=timezone.utc)
        if bt > entry_time:
            break
        if (entry_time - bt).total_seconds() <= tol_seconds:
            best = i
    return best


# ──────────────────── live-trade source loaders ──────────────────────


def _load_trades_from_db(strategy: str, account: int,
                          since: datetime, until: Optional[datetime]) -> List[Dict[str, Any]]:
    try:
        from infrastructure.database import Database  # type: ignore
        db = Database()
    except Exception as exc:
        sys.exit(f"❌ DB init failed: {exc}")
    if getattr(db, "pool", None) is None:
        sys.exit("❌ DATABASE_URL unset / Postgres unreachable.")
    where_until = "AND exit_time <= %s" if until else ""
    args = [strategy, str(account), since]
    if until:
        args.append(until)
    rows: List[Dict[str, Any]] = []
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT symbol, side, quantity, entry_price, exit_price, pnl,
                   entry_time, exit_time, duration_seconds, metadata
            FROM trade_history
            WHERE strategy_name = %s
              AND account_id = %s
              AND exit_time >= %s
              {where_until}
            ORDER BY entry_time ASC
            """,
            tuple(args),
        )
        cols = [c[0] for c in cur.description]
        for r in cur.fetchall():
            rows.append(dict(zip(cols, r)))
    return rows


def _load_trades_from_json(path: Path) -> List[Dict[str, Any]]:
    """Load trades from a hand-crafted JSON for offline testing.

    Each entry must carry at minimum:

        {"symbol": "MES", "side": "BUY", "quantity": 1,
         "entry_price": 7500.0, "exit_price": 7507.5, "pnl": 37.50,
         "entry_time": "2026-06-04T13:30:00Z",
         "exit_time":  "2026-06-04T13:45:00Z",
         "metadata": {"stop_price": 7495.0, "tp_price": 7515.0}}
    """
    raw = json.loads(path.read_text())
    if isinstance(raw, dict) and "trades" in raw:
        raw = raw["trades"]
    out = []
    for t in raw:
        # Parse ISO timestamps to tz-aware datetimes.
        for k in ("entry_time", "exit_time"):
            v = t.get(k)
            if isinstance(v, str):
                t[k] = datetime.fromisoformat(v.replace("Z", "+00:00"))
        out.append(t)
    return out


# ──────────────────── per-trade diff ─────────────────────────────────


def _trade_stop_tp(t: Dict[str, Any]) -> Tuple[Optional[float], Optional[float]]:
    """Pull ``stop_price`` + ``tp_price`` from the trade's metadata JSONB."""
    meta = t.get("metadata") or {}
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except Exception:
            meta = {}
    sp = meta.get("stop_price") or meta.get("stop") or meta.get("sl")
    tp = meta.get("tp_price") or meta.get("target") or meta.get("tp")
    return (float(sp) if sp is not None else None,
            float(tp) if tp is not None else None)


def _diff_one_trade(
    t: Dict[str, Any],
    bars_5m: List[CandleBar],
    bars_1m_by_ns: Optional[Dict[int, CandleBar]],
    *,
    commission_per_trade: float,
    slippage_ticks: float,
    tick_size: float,
    point_value: float,
    force_flat_et_minutes: Optional[int],
    agg_minutes: int = 5,
    max_bars: int = 96,
) -> Dict[str, Any]:
    """Build a per-trade diff row.  Always returns a row, even if the
    engine simulation cannot resolve (the row's ``verdict`` field carries
    the reason).
    """
    side_str = str(t.get("side", "")).upper()
    side = 1 if side_str in ("BUY", "LONG") else -1 if side_str in ("SELL", "SHORT") else 0
    entry_px_live = float(t["entry_price"])
    exit_px_live = float(t["exit_price"])
    pnl_live = float(t.get("pnl") or 0.0)
    qty = int(t.get("quantity") or 1)
    entry_time = t["entry_time"]
    if isinstance(entry_time, str):
        entry_time = datetime.fromisoformat(entry_time.replace("Z", "+00:00"))

    sp, tp_px = _trade_stop_tp(t)
    if sp is None or tp_px is None:
        return {
            "trade": t, "side": side_str, "entry_time": entry_time,
            "verdict": "skip", "reason": "missing stop/tp in metadata",
        }
    if side == 1:
        stop_dist = entry_px_live - sp
        tp_dist = tp_px - entry_px_live
    else:
        stop_dist = sp - entry_px_live
        tp_dist = entry_px_live - tp_px
    if stop_dist <= 0 or tp_dist <= 0:
        return {
            "trade": t, "side": side_str, "entry_time": entry_time,
            "verdict": "skip", "reason": f"invalid geometry stop={stop_dist} tp={tp_dist}",
        }

    entry_idx = _find_entry_index(bars_5m, entry_time)
    if entry_idx is None:
        return {
            "trade": t, "side": side_str, "entry_time": entry_time,
            "verdict": "skip", "reason": "entry_time outside bar series window",
        }

    r_engine, reason_engine, bars_held = _simulate_trade_truth(
        bars_5m, entry_idx, side, stop_dist, tp_dist, max_bars,
        commission_per_trade=commission_per_trade,
        slippage_ticks=slippage_ticks,
        tick_size=tick_size,
        point_value=point_value,
        bars_1m_by_ns=bars_1m_by_ns,
        agg_minutes=agg_minutes,
        force_flat_et_minutes=force_flat_et_minutes,
    )

    # Convert live $-PnL → R units using the SAME stop_dist + point_value.
    # That makes |Δ R| an apples-to-apples comparison even when contract
    # size or symbol differs across trades.
    r_dollar = stop_dist * point_value * qty
    r_live = (pnl_live / r_dollar) if r_dollar > 1e-9 else 0.0
    diff_r = r_engine - r_live
    abs_diff = abs(diff_r)
    if abs_diff < 0.25:
        verdict = "ok"
    elif abs_diff < 1.0:
        verdict = "drift"
    else:
        verdict = "alarm"

    return {
        "trade": t,
        "side": side_str,
        "entry_time": entry_time,
        "stop_dist": stop_dist,
        "tp_dist": tp_dist,
        "r_engine": r_engine,
        "r_live": r_live,
        "diff_r": diff_r,
        "abs_diff_r": abs_diff,
        "engine_exit_reason": reason_engine,
        "engine_bars_held": bars_held,
        "live_pnl_dollar": pnl_live,
        "engine_pnl_dollar": r_engine * r_dollar,
        "verdict": verdict,
    }


# ─────────────────────── reporting ───────────────────────────────────


def _ansi_for_verdict(v: str) -> Tuple[str, str]:
    use_color = sys.stdout.isatty()
    if not use_color:
        return ("", "")
    if v == "ok":
        return ("\033[32m", "\033[0m")
    if v == "drift":
        return ("\033[33m", "\033[0m")
    if v == "alarm":
        return ("\033[31m", "\033[0m")
    return ("\033[2m", "\033[0m")


def _print_per_trade(rows: List[Dict[str, Any]]) -> None:
    print()
    print("  per-trade engine vs live")
    print("  " + "─" * 110)
    hdr = f"  {'ts':<20}  {'sym':<4}  {'side':<5}  {'stop':>6}  {'tp':>6}  {'r_live':>8}  {'r_eng':>8}  {'ΔR':>8}  exit_reason"
    print(hdr)
    print("  " + "─" * 110)
    for r in rows:
        v = r.get("verdict", "?")
        if v == "skip":
            print(f"  {r['entry_time']!s:<20}  {r['trade'].get('symbol', '?'):<4}  "
                  f"{r['side']:<5}  ── skipped: {r.get('reason', '?')}")
            continue
        ts = r["entry_time"]
        if hasattr(ts, "strftime"):
            ts_s = ts.strftime("%Y-%m-%d %H:%M")
        else:
            ts_s = str(ts)
        on, off = _ansi_for_verdict(v)
        marker = {"ok": "✓", "drift": "⚠", "alarm": "✗"}.get(v, "?")
        print(
            f"  {ts_s:<20}  {r['trade'].get('symbol', '?'):<4}  {r['side']:<5}  "
            f"{r['stop_dist']:>6.2f}  {r['tp_dist']:>6.2f}  "
            f"{r['r_live']:>+8.3f}  {r['r_engine']:>+8.3f}  "
            f"{on}{marker} {r['diff_r']:>+6.3f}{off}  {r['engine_exit_reason']}"
        )


def _print_summary(rows: List[Dict[str, Any]]) -> None:
    diffs = [r for r in rows if r.get("verdict") not in (None, "skip")]
    if not diffs:
        print("\n  no comparable trades — nothing to summarise.")
        return
    abs_rs = [r["abs_diff_r"] for r in diffs]
    abs_rs.sort()
    n = len(abs_rs)
    mean_abs = sum(abs_rs) / n
    median_abs = abs_rs[n // 2]
    worst = max(diffs, key=lambda r: r["abs_diff_r"])
    counts = {"ok": 0, "drift": 0, "alarm": 0}
    for r in diffs:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    live_total = sum(r["live_pnl_dollar"] for r in diffs)
    eng_total = sum(r["engine_pnl_dollar"] for r in diffs)
    print()
    print("  ─── summary ───")
    print(f"    n_compared:    {n}")
    print(f"    ok    (|ΔR| < 0.25):  {counts['ok']}")
    print(f"    drift (< 1.00):       {counts['drift']}")
    print(f"    alarm (≥ 1.00):       {counts['alarm']}")
    print(f"    mean |ΔR|:     {mean_abs:.3f}")
    print(f"    median |ΔR|:   {median_abs:.3f}")
    print(f"    max  |ΔR|:     {worst['abs_diff_r']:.3f}  @ {worst['entry_time']} {worst['trade'].get('symbol', '?')} {worst['side']}")
    print(f"    live total PnL:    ${live_total:>+10.2f}")
    print(f"    engine predicted:  ${eng_total:>+10.2f}")
    print(f"    engine vs live:    ${eng_total - live_total:>+10.2f}  ({(eng_total - live_total) / max(abs(live_total), 1) * 100:+.1f}%)")
    if n < 5:
        print(f"    ⚠  sample n={n} < 5 — verdicts are noise-dominated; revisit after >10 live trades")


# ─────────────────────────── main ────────────────────────────────────


def _parse_hhmm(s: str) -> int:
    parts = s.split(":")
    return int(parts[0]) * 60 + int(parts[1])


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    src = ap.add_argument_group("Live trade source (pick one)")
    src.add_argument("--strategy", type=str,
                     help="Strategy name in trade_history (DB pull)")
    src.add_argument("--account", type=int, default=None,
                     help="Account ID (DB pull)")
    src.add_argument("--since", type=str, default=None,
                     help="ISO date or datetime; DB filter for exit_time >= since")
    src.add_argument("--until", type=str, default=None,
                     help="Optional upper bound exit_time <= until")
    src.add_argument("--trades-json", type=str, default=None,
                     help="Offline alternative: load trades from a JSON file")

    bars = ap.add_argument_group("Historical bars")
    bars.add_argument("--symbol", type=str, default=None,
                       help="MES/MNQ/MGC — used to pick default CSV paths + tick conventions when all trades share a symbol")
    bars.add_argument("--csv-5m", type=str, default=None,
                       help="Override 5m CSV (default historical_data/price/<SYM>_5m_databento.csv)")
    bars.add_argument("--csv-1m", type=str, default=None,
                       help="Override 1m CSV for intrabar resolution (recommended for production validation)")
    bars.add_argument("--no-1m", action="store_true",
                       help="Disable 1m intrabar resolution (useful for tests where the 5m series is synthetic and canonical 1m would inject contradictory ticks)")

    fill = ap.add_argument_group("Engine fill semantics (must match live)")
    fill.add_argument("--commission-per-trade", type=float, default=5.0)
    fill.add_argument("--slippage-ticks", type=float, default=0.5)
    fill.add_argument("--tick-size", type=float, default=None,
                       help="Override per-symbol tick size (default from --symbol profile)")
    fill.add_argument("--point-value", type=float, default=None,
                       help="Override per-symbol point value (default from --symbol profile)")
    fill.add_argument("--force-flat-et", type=str, default="16:00",
                       help="ET wall-clock cutoff for force-flat (HH:MM); pass '' to disable")
    fill.add_argument("--max-bars", type=int, default=96,
                       help="Max bars held before timed-exit fallback (96 × 5m = 8 h)")
    fill.add_argument("--agg-minutes", type=int, default=5,
                       help="5m default; set 1 if you also pass --csv-5m pointing at a 1m file")

    out = ap.add_argument_group("Output")
    out.add_argument("--json", action="store_true",
                     help="Emit machine-readable JSON (one row per trade) to stdout")

    args = ap.parse_args()

    # ── Load trades ────────────────────────────────────────────────────
    if args.trades_json:
        trades = _load_trades_from_json(Path(args.trades_json))
        print(f"loaded {len(trades)} trades from {args.trades_json}")
    elif args.strategy and args.account is not None:
        if args.since is None:
            sys.exit("❌ --since is required when pulling from DB")
        since = datetime.fromisoformat(args.since.replace("Z", "+00:00"))
        until = datetime.fromisoformat(args.until.replace("Z", "+00:00")) if args.until else None
        trades = _load_trades_from_db(args.strategy, args.account, since, until)
        print(f"loaded {len(trades)} trades from trade_history (strategy={args.strategy} account={args.account})")
    else:
        sys.exit("❌ provide either --strategy + --account + --since, OR --trades-json")

    if not trades:
        print("no trades to compare; exiting clean.")
        return 0

    # ── Group by symbol so we only load each CSV once ──────────────────
    by_symbol: Dict[str, List[Dict[str, Any]]] = {}
    for t in trades:
        s = str(t.get("symbol", args.symbol or "?")).upper()
        by_symbol.setdefault(s, []).append(t)

    fflat_min: Optional[int] = None
    if args.force_flat_et:
        try:
            fflat_min = _parse_hhmm(args.force_flat_et)
        except Exception:
            sys.exit(f"❌ invalid --force-flat-et value: {args.force_flat_et!r}")

    all_rows: List[Dict[str, Any]] = []
    for sym, sym_trades in by_symbol.items():
        if sym not in _SYMBOL_PROFILE and args.tick_size is None:
            print(f"  ⚠  unknown symbol {sym!r}; using MES defaults (tick=0.25 pv=5.0). Pass --tick-size / --point-value to override.")
        prof = _SYMBOL_PROFILE.get(sym, _SYMBOL_PROFILE["MES"])
        tick_size = args.tick_size if args.tick_size is not None else prof["tick_size"]
        point_value = args.point_value if args.point_value is not None else prof["point_value"]

        # Bound bar loading to a window around the trades (1 day either
        # side covers force-flat + worst-case overnight holds).
        ts_list = []
        for t in sym_trades:
            et = t["entry_time"]
            if isinstance(et, str):
                et = datetime.fromisoformat(et.replace("Z", "+00:00"))
            ts_list.append(et)
        since_w = min(ts_list).replace(hour=0, minute=0, second=0, microsecond=0)
        until_w = max(ts_list)
        from datetime import timedelta as _td
        since_w = since_w - _td(days=1)
        until_w = until_w + _td(days=2)
        # _read_csv compares against naive timestamps from the CSV file
        # (Databento format strips tz); strip tz to match.
        if since_w.tzinfo is not None:
            since_w = since_w.replace(tzinfo=None)
        if until_w.tzinfo is not None:
            until_w = until_w.replace(tzinfo=None)

        p5, p1 = _resolve_csv_paths(sym, args.csv_1m, args.csv_5m, no_1m=args.no_1m)
        bars_5m = _read_csv(p5, since_w, until_w)
        print(f"  loaded {len(bars_5m)} 5m bars for {sym} (window {since_w.date()} → {until_w.date()})")
        bars_1m_by_ns: Optional[Dict[int, CandleBar]] = None
        if p1 is not None:
            bars_1m = _read_csv(p1, since_w, until_w)
            bars_1m_by_ns = _index_bars_by_ns(bars_1m)
            print(f"  loaded {len(bars_1m)} 1m bars for {sym} (intrabar resolution ON)")

        for t in sym_trades:
            row = _diff_one_trade(
                t, bars_5m, bars_1m_by_ns,
                commission_per_trade=args.commission_per_trade,
                slippage_ticks=args.slippage_ticks,
                tick_size=tick_size,
                point_value=point_value,
                force_flat_et_minutes=fflat_min,
                agg_minutes=args.agg_minutes,
                max_bars=args.max_bars,
            )
            all_rows.append(row)

    if args.json:
        # Strip non-serialisable trade objects + datetimes.
        def _clean(r: Dict[str, Any]) -> Dict[str, Any]:
            o = {k: v for k, v in r.items() if k != "trade"}
            for k, v in list(o.items()):
                if isinstance(v, datetime):
                    o[k] = v.isoformat()
                elif isinstance(v, Decimal):
                    o[k] = float(v)
            o["symbol"] = r["trade"].get("symbol")
            return o
        print(json.dumps([_clean(r) for r in all_rows], indent=2, default=str))
        return 0

    _print_per_trade(all_rows)
    _print_summary(all_rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
