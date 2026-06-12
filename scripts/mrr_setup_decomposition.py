#!/usr/bin/env python3
"""Per-setup edge decomposition for ``morning_range_reversion``.

Loads N months of trades from an existing walk-forward recap directory (which
already ran the engine fold-by-fold in parallel via its decision cache),
parses the per-trade overlays embedded in each ``trades/*_trade_chart.html``,
then groups them along several setup dimensions and prints WR / expectancy /
total-PnL per cell. Designed to find filters that lift the strategy's
expected value by *removing* losing setups rather than adding more parameters.

The HTML-parsing path is *much* faster than re-running a 6m backtest in a
single subprocess (parallel walk-forward folds finish in ~15 s; a single
6m backtest with 1m intrabar resolution takes ~10 min). The recap script
already did the work — we just harvest the trade overlays.

Dimensions emitted:
  - symbol × side
  - side × weekday (ET)
  - side × entry_hour_et
  - side × anchor_width_quartile
  - side × month
  - side × prior_day_direction  (recomputed from databento daily change)
  - composite cells flagged when (n >= 4) AND (WR <= 40% OR expectancy <= -50)

The script is **read-only** — it does not modify any TOML. It emits a
ranked "candidate filters" list at the end that the operator (you) can apply
manually after reviewing.

Example::

    # 1. Generate a walk-forward report
    python scripts/walkforward_trade_recap_report.py \\
        --days=180 --folds=9 --strategies=morning_range_reversion \\
        --symbols=MGC,MNQ --out=docs/perf/_grind_6m_setup_decomp

    # 2. Decompose its trades
    python scripts/mrr_setup_decomposition.py \\
        --recap-dir docs/perf/_grind_6m_setup_decomp \\
        --json out.json
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CSV_DIR = ROOT / "historical_data" / "price"
DEFAULT_TZ = ZoneInfo("America/New_York")

# Point value per symbol — keep in sync with core/risk_sizer.point_value()
_POINT_VALUE = {"MGC": 10.0, "MNQ": 2.0, "MES": 5.0, "ES": 50.0, "NQ": 20.0}


def _point_value(sym: str) -> float:
    return _POINT_VALUE.get(sym.upper(), 1.0)


_OVERLAY_RE = re.compile(r"let tradeOverlays = (\[\{.*?\}\]);", re.DOTALL)
_CHART_FNAME_RE = re.compile(
    r"^fold(\d+)_morning_range_reversion_([A-Z]+)_T\d+_\d+_trade_chart\.html$"
)


def _parse_trade_chart_html(path: Path) -> Optional[Dict[str, Any]]:
    """Return the single trade overlay embedded in a recap-generated chart HTML.

    The recap emits one chart per trade (filename: ``foldN_..._T000NNN_idx_trade_chart.html``)
    so ``tradeOverlays`` is always a length-1 list. Returns None for files
    that don't match the expected pattern or whose overlay is missing."""
    m = _CHART_FNAME_RE.match(path.name)
    if not m:
        return None
    symbol = m.group(2)
    text = path.read_text(encoding="utf-8", errors="replace")
    om = _OVERLAY_RE.search(text)
    if not om:
        return None
    try:
        overlays = json.loads(om.group(1))
    except json.JSONDecodeError:
        return None
    if not overlays:
        return None
    o = overlays[0]
    return {**o, "symbol": symbol}


def _load_recap_trades(recap_dir: Path) -> List[Dict[str, Any]]:
    """Walk recap_dir/trades/*.html and return all per-trade overlays."""
    trades_dir = recap_dir / "trades"
    if not trades_dir.is_dir():
        raise FileNotFoundError(f"No trades dir under {recap_dir}")
    trades: List[Dict[str, Any]] = []
    for p in sorted(trades_dir.glob("*_trade_chart.html")):
        ov = _parse_trade_chart_html(p)
        if ov is not None:
            trades.append(ov)
    return trades


# ───────────────────── feature engineering ─────────────────────────────────────


def _to_utc_dt(ts: Any) -> datetime:
    """Accept either ISO string (engine JSON) or unix-int (chart overlay)."""
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(int(ts), tz=timezone.utc)
    s = str(ts).replace("Z", "+00:00")
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _entry_features(trade: Dict[str, Any]) -> Dict[str, Any]:
    et = _to_utc_dt(trade["entry_time"])
    et_local = et.astimezone(DEFAULT_TZ)
    return {
        "session_date_et": et_local.date(),
        "weekday_et": et_local.strftime("%a"),
        "entry_hour_et": et_local.hour,
        "month_et": et_local.strftime("%Y-%m"),
    }


def _compute_pnl(trade: Dict[str, Any], quantity: int = 2) -> float:
    """Recompute PnL from entry/exit prices since chart overlays don't carry it.
    ``quantity`` defaults to 2 because MGC/MNQ live trades use 2-contract sizing;
    the per-trade *expectancy* across cells is what matters for ranking — the
    absolute multiplier shifts everything by the same constant."""
    try:
        ep = float(trade["entry_price"]); xp = float(trade["exit_price"])
    except (KeyError, ValueError, TypeError):
        return 0.0
    sym = trade.get("symbol", "?")
    side = str(trade.get("side", "")).upper()
    pv = _point_value(sym)
    sign = 1.0 if side == "BUY" else -1.0
    return round((xp - ep) * sign * pv * quantity, 2)


def _load_daily_closes(symbol: str) -> Dict[date, float]:
    """Compute daily closes from databento 1m CSV (or 5m fallback). Used to
    derive ``prior_day_direction`` (close-on-close)."""
    csv_path = DEFAULT_CSV_DIR / f"{symbol}_1m_databento.csv"
    if not csv_path.is_file():
        csv_path = DEFAULT_CSV_DIR / f"{symbol}_5m_databento.csv"
        if not csv_path.is_file():
            return {}
    daily: Dict[date, float] = {}
    with csv_path.open() as f:
        rdr = csv.DictReader(f)
        for row in rdr:
            ts_str = row.get("timestamp", "")
            try:
                dt_utc = datetime.fromisoformat(ts_str.replace(" ", "T")).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            d_et = dt_utc.astimezone(DEFAULT_TZ).date()
            try:
                daily[d_et] = float(row["close"])
            except (KeyError, ValueError):
                continue
    return daily


def _load_anchor_widths(symbol: str) -> Dict[date, float]:
    """Compute anchor width (07:00-08:00 ET) per session from databento 5m."""
    csv_path = DEFAULT_CSV_DIR / f"{symbol}_5m_databento.csv"
    if not csv_path.is_file():
        return {}
    widths: Dict[date, Dict[str, float]] = defaultdict(lambda: {"hi": float("-inf"), "lo": float("inf")})
    with csv_path.open() as f:
        rdr = csv.DictReader(f)
        for row in rdr:
            ts_str = row.get("timestamp", "")
            try:
                dt_utc = datetime.fromisoformat(ts_str.replace(" ", "T")).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            ts_et = dt_utc.astimezone(DEFAULT_TZ)
            if not (7 <= ts_et.hour < 8):
                continue
            d = ts_et.date()
            try:
                h = float(row["high"]); l_ = float(row["low"])
            except (KeyError, ValueError):
                continue
            widths[d]["hi"] = max(widths[d]["hi"], h)
            widths[d]["lo"] = min(widths[d]["lo"], l_)
    return {d: hl["hi"] - hl["lo"] for d, hl in widths.items() if hl["hi"] != float("-inf") and hl["lo"] != float("inf")}


def _enrich(trades: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Add weekday / hour / month / prior_day_direction / anchor_width_pct to each trade."""
    daily_closes_by_sym = {sym: _load_daily_closes(sym) for sym in {t["symbol"] for t in trades}}
    anchor_widths_by_sym = {sym: _load_anchor_widths(sym) for sym in {t["symbol"] for t in trades}}

    # Compute per-symbol width quartile cuts so the "quartile" label is comparable across
    # the dataset for one symbol (but not across symbols — MGC ~16, MNQ ~75).
    width_cuts: Dict[str, List[float]] = {}
    for sym, widths in anchor_widths_by_sym.items():
        vals = sorted(widths.values())
        if len(vals) >= 4:
            q1 = vals[len(vals) // 4]
            q2 = vals[len(vals) // 2]
            q3 = vals[3 * len(vals) // 4]
            width_cuts[sym] = [q1, q2, q3]
        else:
            width_cuts[sym] = [0, 0, 0]

    enriched: List[Dict[str, Any]] = []
    for t in trades:
        feats = _entry_features(t)
        sd = feats["session_date_et"]
        sym = t["symbol"]
        closes = daily_closes_by_sym.get(sym, {})
        prior_d = max((d for d in closes if d < sd), default=None)
        prior_pp = max((d for d in closes if d < prior_d), default=None) if prior_d else None
        if prior_d and prior_pp:
            change = closes[prior_d] - closes[prior_pp]
            atr_like = abs(change)
            if change > 0.5 * atr_like or change > 0:
                prior_dir = "up" if change > 0 else "down"
            else:
                prior_dir = "flat"
            # simpler: just up / down / flat-by-tiny-margin
            if abs(change) < 1e-9:
                prior_dir = "flat"
            else:
                prior_dir = "up" if change > 0 else "down"
        else:
            prior_dir = "unknown"

        anchor_w = anchor_widths_by_sym.get(sym, {}).get(sd)
        if anchor_w is None:
            q_label = "unknown"
        else:
            cuts = width_cuts.get(sym, [0, 0, 0])
            if anchor_w <= cuts[0]:
                q_label = "Q1_narrow"
            elif anchor_w <= cuts[1]:
                q_label = "Q2"
            elif anchor_w <= cuts[2]:
                q_label = "Q3"
            else:
                q_label = "Q4_wide"

        enriched.append({
            **t,
            **feats,
            "prior_day_direction": prior_dir,
            "anchor_width": anchor_w,
            "anchor_width_quartile": q_label,
        })
    return enriched


# ───────────────────── cell decomposition ──────────────────────────────────────


def _summarize_cells(
    trades: List[Dict[str, Any]],
    keys: Tuple[str, ...],
) -> List[Dict[str, Any]]:
    cells: Dict[Tuple, List[Dict[str, Any]]] = defaultdict(list)
    for t in trades:
        cells[tuple(t.get(k) for k in keys)].append(t)
    rows = []
    for key_vals, ts in cells.items():
        n = len(ts)
        wins = sum(1 for t in ts if float(t.get("pnl") or 0) > 0)
        pnl = sum(float(t.get("pnl") or 0) for t in ts)
        rows.append({
            "cell": dict(zip(keys, key_vals)),
            "n": n,
            "wins": wins,
            "losses": n - wins,
            "win_rate": wins / n if n else 0.0,
            "total_pnl": round(pnl, 2),
            "expectancy": round(pnl / n, 2) if n else 0.0,
        })
    rows.sort(key=lambda r: (r["expectancy"], r["total_pnl"]))
    return rows


def _format_cell_table(name: str, rows: List[Dict[str, Any]]) -> str:
    lines = [f"\n### {name}", "-" * 80]
    if not rows:
        lines.append("(no cells)")
        return "\n".join(lines)
    keys = list(rows[0]["cell"].keys())
    header = " | ".join(
        [*(f"{k:<12}" for k in keys), f"{'n':>3}", f"{'W':>2}", f"{'L':>2}", f"{'WR%':>5}", f"{'PnL$':>8}", f"{'Exp$':>7}"]
    )
    lines.append(header)
    lines.append("-" * len(header))
    for r in rows:
        cell = r["cell"]
        vals = " | ".join([*(f"{str(cell[k]):<12}" for k in keys),
                            f"{r['n']:>3}", f"{r['wins']:>2}", f"{r['losses']:>2}",
                            f"{r['win_rate']*100:>5.1f}",
                            f"{r['total_pnl']:>8.2f}", f"{r['expectancy']:>7.2f}"])
        lines.append(vals)
    return "\n".join(lines)


def _candidate_filters(trades: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Find cells where (n >= 4) AND (expectancy <= -50 OR WR <= 35%).
    These are the "always-loses" cells worth filtering OUT."""
    candidates: List[Dict[str, Any]] = []
    dimensions: List[Tuple[str, ...]] = [
        ("symbol", "side"),
        ("symbol", "weekday_et"),
        ("symbol", "side", "weekday_et"),
        ("symbol", "side", "entry_hour_et"),
        ("symbol", "side", "anchor_width_quartile"),
        ("symbol", "side", "prior_day_direction"),
        ("symbol", "side", "month_et"),
    ]
    seen: set = set()
    for keys in dimensions:
        for row in _summarize_cells(trades, keys):
            if row["n"] < 4:
                continue
            bad = row["expectancy"] <= -50 or row["win_rate"] <= 0.35
            if not bad:
                continue
            fp = tuple(sorted(row["cell"].items()))
            if fp in seen:
                continue
            seen.add(fp)
            candidates.append({"dimensions": list(keys), **row})
    candidates.sort(key=lambda r: r["expectancy"])
    return candidates


# ───────────────────── main ────────────────────────────────────────────────────


def main(argv: Optional[Iterable[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--recap-dir", type=Path, required=True,
                    help="Walk-forward recap output directory containing trades/*.html")
    ap.add_argument("--json", dest="json_path", type=Path, default=None,
                    help="If set, dump enriched trades + cell tables to this JSON file")
    args = ap.parse_args(argv)

    if not args.recap_dir.is_dir():
        print(f"error: recap dir not found: {args.recap_dir}", file=sys.stderr)
        return 2

    print(f"▶ loading trades from {args.recap_dir}/trades/...")
    raw_trades = _load_recap_trades(args.recap_dir)
    print(f"  → loaded {len(raw_trades)} trade overlays")
    if not raw_trades:
        print("(no trades)")
        return 1

    # Recompute pnl since overlays don't carry it
    for t in raw_trades:
        if "pnl" not in t:
            t["pnl"] = _compute_pnl(t)

    enriched = _enrich(raw_trades)

    print(_format_cell_table("symbol × side", _summarize_cells(enriched, ("symbol", "side"))))
    print(_format_cell_table("symbol × weekday_et", _summarize_cells(enriched, ("symbol", "weekday_et"))))
    print(_format_cell_table("symbol × side × weekday_et",
                              _summarize_cells(enriched, ("symbol", "side", "weekday_et"))))
    print(_format_cell_table("symbol × side × entry_hour_et",
                              _summarize_cells(enriched, ("symbol", "side", "entry_hour_et"))))
    print(_format_cell_table("symbol × side × anchor_width_quartile",
                              _summarize_cells(enriched, ("symbol", "side", "anchor_width_quartile"))))
    print(_format_cell_table("symbol × side × prior_day_direction",
                              _summarize_cells(enriched, ("symbol", "side", "prior_day_direction"))))

    cands = _candidate_filters(enriched)
    print("\n\n══════════════════════════════════════════════════════════════════════════════")
    print("CANDIDATE FILTERS  (n >= 4 AND expectancy <= -50 OR WR <= 35%)")
    print("══════════════════════════════════════════════════════════════════════════════")
    if not cands:
        print("(no cells met the threshold — strategy edge appears symmetric across setups)")
    else:
        for i, c in enumerate(cands, 1):
            cell_str = ", ".join(f"{k}={v}" for k, v in c["cell"].items())
            print(
                f"{i:>2}. [{','.join(c['dimensions'])}]  {cell_str}\n"
                f"     n={c['n']}  WR={c['win_rate']*100:.0f}%  PnL=${c['total_pnl']:.2f}  Exp/trade=${c['expectancy']:.2f}"
            )

    if args.json_path:
        out = {
            "enriched_trades": [
                {**t,
                 "session_date_et": str(t["session_date_et"]),
                 "entry_time": str(t["entry_time"]),
                 "exit_time": str(t["exit_time"])}
                for t in enriched
            ],
            "candidate_filters": cands,
        }
        args.json_path.write_text(json.dumps(out, indent=2, default=str))
        print(f"\nWrote {args.json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
