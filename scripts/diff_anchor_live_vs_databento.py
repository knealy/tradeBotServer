#!/usr/bin/env python3
"""Diff a persisted live anchor against what databento would have produced.

The 2026-06-11 MGC ``morning_range_reversion`` post-mortem identified that
the live anchor and the databento anchor differed by 0.52 pt (live width
16.18 vs databento 16.70). Because MRR's SL distance is ``half_width *
sl_mult``, that 0.52 pt difference translated to a 0.90 pt SL placement
disagreement — enough to flip the trade outcome.

This script is the diagnostic counterpart: given a persisted anchor JSON
written by ``core.anchor_persistence``, recompute the same anchor from a
local databento 5m CSV and report any divergence ≥ ``--threshold-pts``.

Typical usage::

    # Diff yesterday's MGC anchor against databento
    python scripts/diff_anchor_live_vs_databento.py \\
        --anchor data/anchors/morning_range_reversion_MGC_2026-06-11.json

    # Diff every anchor under data/anchors with output JSON for CI / dashboards
    python scripts/diff_anchor_live_vs_databento.py --all --json

Exit codes:
    0 — all anchors within the threshold
    1 — at least one anchor diverged by >= --threshold-pts on H, L, or width
    2 — usage / file errors
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ANCHOR_DIR = ROOT / "data" / "anchors"
DEFAULT_CSV_DIR = ROOT / "historical_data" / "price"
DEFAULT_TZ = ZoneInfo("America/New_York")


@dataclass
class DiffResult:
    """One row of the diff table. Serialisable for ``--json``."""
    anchor_path: str
    strategy: str
    symbol: str
    session_date_et: str
    live_high: Optional[float]
    live_low: Optional[float]
    live_width: Optional[float]
    db_high: Optional[float]
    db_low: Optional[float]
    db_width: Optional[float]
    delta_high: Optional[float]
    delta_low: Optional[float]
    delta_width: Optional[float]
    live_n_bars: Optional[int]
    db_n_bars: Optional[int]
    data_feed_safe: Optional[bool]
    data_feed_reason: Optional[str]
    diverged: bool
    error: Optional[str] = None


def _parse_hhmm(s: str, default: time) -> time:
    try:
        hh, mm = s.split(":")
        return time(int(hh), int(mm))
    except (ValueError, AttributeError):
        return default


def _resolve_5m_csv(symbol: str, csv_dir: Path) -> Optional[Path]:
    """Pick the freshest databento 5m CSV for ``symbol`` in ``csv_dir``."""
    candidates = [
        csv_dir / f"{symbol}_5m_databento.csv",
        csv_dir / f"{symbol.lower()}_5m_databento.csv",
        csv_dir / f"{symbol}_5m.csv",
        csv_dir / f"{symbol.lower()}_5m.csv",
    ]
    for c in candidates:
        if c.is_file():
            return c
    return None


def compute_databento_anchor(
    *,
    csv_path: Path,
    session_date_et: date,
    window_start_et: time,
    window_end_et: time,
    session_tz: ZoneInfo = DEFAULT_TZ,
) -> Optional[Dict[str, Any]]:
    """Recompute (high, low, n_bars_used, first_bar_ts, last_bar_ts) from CSV.

    Walks the 5m databento CSV and aggregates bars whose ET timestamp falls
    inside ``[window_start_et, window_end_et)`` for ``session_date_et``. Returns
    ``None`` if no bars qualified — which usually means the CSV doesn't cover
    that date or the timezone interpretation is wrong.

    The bar's ET timestamp is its ``timestamp`` column converted from UTC to
    the strategy's timezone. The CSV's first column is assumed to be a naive
    UTC ISO string (no tz suffix), matching the schema of every databento CSV
    in this repo.
    """
    if not csv_path.is_file():
        return None
    hi = lo = None
    n = 0
    first_ts_utc: Optional[datetime] = None
    last_ts_utc: Optional[datetime] = None
    with csv_path.open() as f:
        rdr = csv.DictReader(f)
        for row in rdr:
            ts_str = row.get("timestamp") or row.get("time") or ""
            if not ts_str.startswith(session_date_et.isoformat()):
                # Cheap prefix prune before parsing — skips ~99.9% of rows.
                # Works because databento CSV timestamps lead with YYYY-MM-DD.
                continue
            try:
                ts_utc_naive = datetime.fromisoformat(ts_str.replace(" ", "T"))
            except ValueError:
                continue
            ts_utc = ts_utc_naive.replace(tzinfo=timezone.utc)
            ts_et = ts_utc.astimezone(session_tz)
            if ts_et.date() != session_date_et:
                continue
            if not (window_start_et <= ts_et.time() < window_end_et):
                continue
            try:
                h = float(row["high"])
                l_ = float(row["low"])
            except (KeyError, ValueError):
                continue
            hi = h if hi is None else max(hi, h)
            lo = l_ if lo is None else min(lo, l_)
            n += 1
            if first_ts_utc is None:
                first_ts_utc = ts_utc
            last_ts_utc = ts_utc
    if hi is None or lo is None or n == 0:
        return None
    return {
        "high": float(hi),
        "low": float(lo),
        "width": float(hi - lo),
        "n_bars_used": int(n),
        "first_bar_ts_utc": first_ts_utc.isoformat() if first_ts_utc else None,
        "last_bar_ts_utc": last_ts_utc.isoformat() if last_ts_utc else None,
    }


def diff_one_anchor(
    anchor_path: Path,
    *,
    csv_dir: Path = DEFAULT_CSV_DIR,
    threshold_pts: float = 0.10,
) -> DiffResult:
    """Compare a persisted anchor JSON to databento. Always returns a row."""
    base = DiffResult(
        anchor_path=str(anchor_path),
        strategy="?", symbol="?", session_date_et="?",
        live_high=None, live_low=None, live_width=None,
        db_high=None, db_low=None, db_width=None,
        delta_high=None, delta_low=None, delta_width=None,
        live_n_bars=None, db_n_bars=None,
        data_feed_safe=None, data_feed_reason=None,
        diverged=False, error=None,
    )
    try:
        payload = json.loads(anchor_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        base.error = f"unreadable anchor JSON ({exc})"
        base.diverged = True
        return base
    base.strategy = str(payload.get("strategy") or "?")
    base.symbol = str(payload.get("symbol") or "?")
    base.session_date_et = str(payload.get("session_date_et") or "?")
    anchor = payload.get("anchor") or {}
    window = payload.get("window") or {}
    base.live_high = anchor.get("high")
    base.live_low = anchor.get("low")
    base.live_width = anchor.get("width")
    base.live_n_bars = window.get("n_bars_used")
    df_health = payload.get("data_feed_health") or {}
    if df_health.get("available"):
        base.data_feed_safe = df_health.get("safe")
        base.data_feed_reason = df_health.get("reason")

    csv_path = _resolve_5m_csv(base.symbol, csv_dir)
    if csv_path is None:
        base.error = f"no databento 5m CSV found for {base.symbol} in {csv_dir}"
        base.diverged = True
        return base
    try:
        sd = date.fromisoformat(base.session_date_et)
    except ValueError:
        base.error = f"bad session_date_et: {base.session_date_et!r}"
        base.diverged = True
        return base
    window_start = _parse_hhmm(window.get("start_et") or "07:00", time(7, 0))
    window_end = _parse_hhmm(window.get("end_et") or "08:00", time(8, 0))
    db = compute_databento_anchor(
        csv_path=csv_path,
        session_date_et=sd,
        window_start_et=window_start,
        window_end_et=window_end,
    )
    if db is None:
        base.error = f"databento CSV {csv_path.name} has no bars in {sd} {window_start}-{window_end} ET"
        base.diverged = True
        return base
    base.db_high = db["high"]
    base.db_low = db["low"]
    base.db_width = db["width"]
    base.db_n_bars = db["n_bars_used"]
    if base.live_high is not None and base.db_high is not None:
        base.delta_high = round(base.live_high - base.db_high, 6)
    if base.live_low is not None and base.db_low is not None:
        base.delta_low = round(base.live_low - base.db_low, 6)
    if base.live_width is not None and base.db_width is not None:
        base.delta_width = round(base.live_width - base.db_width, 6)
    base.diverged = any(
        d is not None and abs(d) >= threshold_pts
        for d in (base.delta_high, base.delta_low, base.delta_width)
    )
    return base


def _format_text_table(rows: List[DiffResult]) -> str:
    if not rows:
        return "(no anchors found)"
    lines: List[str] = []
    header = (
        f"{'strategy':<28} {'sym':<5} {'session':<12} "
        f"{'live H':>10} {'db H':>10} {'ΔH':>7} "
        f"{'live L':>10} {'db L':>10} {'ΔL':>7} "
        f"{'live W':>7} {'db W':>7} {'ΔW':>7} "
        f"{'feed':<6} {'verdict':<10}"
    )
    lines.append(header)
    lines.append("-" * len(header))
    for r in rows:
        feed = "?" if r.data_feed_safe is None else ("ok" if r.data_feed_safe else "STALE")
        verdict = "DIVERGED" if r.diverged else "ok"
        def _f(v, w=10, p=2):
            return f"{'—':>{w}}" if v is None else f"{v:>{w}.{p}f}"
        line = (
            f"{r.strategy:<28} {r.symbol:<5} {r.session_date_et:<12} "
            f"{_f(r.live_high)} {_f(r.db_high)} {_f(r.delta_high, 7)} "
            f"{_f(r.live_low)} {_f(r.db_low)} {_f(r.delta_low, 7)} "
            f"{_f(r.live_width, 7)} {_f(r.db_width, 7)} {_f(r.delta_width, 7)} "
            f"{feed:<6} {verdict:<10}"
        )
        lines.append(line)
        if r.error:
            lines.append(f"  └ error: {r.error}")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--anchor", type=Path, default=None,
                    help="Path to a single anchor JSON (mutually exclusive with --all)")
    ap.add_argument("--all", action="store_true",
                    help="Diff every anchor under --anchor-dir")
    ap.add_argument("--anchor-dir", type=Path, default=DEFAULT_ANCHOR_DIR,
                    help="Directory holding anchor JSONs (default: data/anchors/)")
    ap.add_argument("--csv-dir", type=Path, default=DEFAULT_CSV_DIR,
                    help="Directory holding databento 5m CSVs (default: historical_data/price/)")
    ap.add_argument("--threshold-pts", type=float, default=0.10,
                    help="Min absolute pt divergence on H, L, or width to flag as DIVERGED (default 0.10)")
    ap.add_argument("--json", dest="json_output", action="store_true",
                    help="Emit JSON to stdout instead of a text table")
    args = ap.parse_args(argv)

    if (args.anchor is None) == (not args.all):
        ap.error("exactly one of --anchor or --all is required")

    paths: List[Path] = []
    if args.anchor is not None:
        if not args.anchor.is_file():
            print(f"error: anchor not found: {args.anchor}", file=sys.stderr)
            return 2
        paths = [args.anchor]
    else:
        if not args.anchor_dir.is_dir():
            print(f"error: anchor dir not found: {args.anchor_dir}", file=sys.stderr)
            return 2
        paths = sorted(args.anchor_dir.glob("*.json"))
        if not paths:
            if args.json_output:
                print(json.dumps([]))
            else:
                print(f"(no anchor files in {args.anchor_dir})")
            return 0

    rows = [
        diff_one_anchor(p, csv_dir=args.csv_dir, threshold_pts=args.threshold_pts)
        for p in paths
    ]
    if args.json_output:
        print(json.dumps([asdict(r) for r in rows], indent=2))
    else:
        print(_format_text_table(rows))
    return 1 if any(r.diverged for r in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
