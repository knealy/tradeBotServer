#!/usr/bin/env python3
"""Validate ``core.price_action`` detectors on a historical CSV.

For each pattern, computes:
    - n: count of detections in the file
    - p_up / p_down: P(next bar closes higher / lower | pattern fires)
    - mean_ret: mean next-bar return when the pattern fires
    - edge_p_up vs baseline: p_up(pattern) - p_up(no-condition)
    - edge_mean_ret vs baseline: mean next-bar return (pattern) - baseline

Usage:

    .venv/bin/python scripts/validate_price_action_patterns.py \\
        --csv historical_data/price/MNQ_5m_databento.csv \\
        --since 2025-01-01 --until 2026-05-01

    # All available history, all instruments:
    for sym in MNQ MES MGC; do
        .venv/bin/python scripts/validate_price_action_patterns.py \\
            --csv historical_data/price/${sym}_5m_databento.csv
    done

The "edge" columns are the headline output — if a pattern's
``edge_p_up`` is materially positive (and ``n`` is large enough that
the binomial CI doesn't engulf zero), it's a tradeable candidate for
the next-step strategy on top of this primitive.
"""

from __future__ import annotations

import argparse
import csv
import math
import sys
from datetime import datetime
from pathlib import Path
from typing import Iterable, Iterator, List, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.price_action import CandleBar, Pattern, ProbabilityEmitter, detect_patterns


def _parse_ts(s: str) -> datetime:
    s = s.strip()
    # Accept both "YYYY-MM-DD HH:MM:SS" and ISO "T" variants.
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    return datetime.fromisoformat(s)


def _read_csv(path: Path, since: Optional[datetime], until: Optional[datetime]) -> Iterator[CandleBar]:
    with path.open() as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            ts = _parse_ts(row["timestamp"])
            if since and ts < since:
                continue
            if until and ts > until:
                break
            yield CandleBar(
                timestamp=ts,
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row.get("volume", 0) or 0),
            )


def _binomial_ci(n_success: int, n_total: int, *, z: float = 1.96) -> tuple[float, float]:
    """Wilson 95 % CI on a Bernoulli proportion."""
    if n_total == 0:
        return 0.0, 0.0
    p = n_success / n_total
    denom = 1 + (z * z) / n_total
    center = (p + (z * z) / (2 * n_total)) / denom
    half = (z * math.sqrt(p * (1 - p) / n_total + (z * z) / (4 * n_total * n_total))) / denom
    return max(0.0, center - half), min(1.0, center + half)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--csv", required=True, help="Path to OHLCV CSV (timestamp,open,high,low,close,volume)")
    p.add_argument("--since", help="ISO date — drop bars older than this")
    p.add_argument("--until", help="ISO date — drop bars newer than this")
    args = p.parse_args()

    csv_path = Path(args.csv)
    if not csv_path.exists():
        sys.exit(f"❌ CSV not found: {csv_path}")
    since = _parse_ts(args.since) if args.since else None
    until = _parse_ts(args.until) if args.until else None

    emitter = ProbabilityEmitter()
    window: List[CandleBar] = []
    n_bars = 0
    for bar in _read_csv(csv_path, since, until):
        window.append(bar)
        if len(window) > 4:
            window.pop(0)
        emitter.update(window)
        n_bars += 1

    snap = emitter.snapshot()
    base = snap["__baseline__"]

    title = f"price-action pattern edges — {csv_path.name}"
    if since or until:
        title += f"  ({since.date() if since else 'start'} → {until.date() if until else 'end'})"
    print()
    print("━" * 110)
    print(f" {title}")
    print(f" Bars consumed: {n_bars:,}    Baseline p_up: {base['p_up']:.4f}    "
           f"Baseline mean_ret: {base['mean_ret']*1e4:+.2f} bps")
    print("━" * 110)
    hdr = f"  {'pattern':<22}  {'n':>6}  {'p_up':>7}  {'CI_lo':>7}  {'CI_hi':>7}  " \
          f"{'edge_p':>8}  {'mean_ret(bps)':>14}  {'edge_ret(bps)':>14}"
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))

    rows = []
    for name in Pattern.ALL:
        s = snap[name]
        n = int(s["n"])
        if n == 0:
            print(f"  {name:<22}  {n:>6}  {'─':>7}  {'─':>7}  {'─':>7}  {'─':>8}  {'─':>14}  {'─':>14}")
            continue
        # Wilson CI on p_up for sanity.
        n_up_est = int(round(s["p_up"] * n))
        lo, hi = _binomial_ci(n_up_est, n)
        rows.append((name, n, s["p_up"], lo, hi, s["edge_p_up"], s["mean_ret"], s["edge_mean_ret"]))
        print(f"  {name:<22}  {n:>6}  {s['p_up']:>7.4f}  {lo:>7.4f}  {hi:>7.4f}  "
               f"{s['edge_p_up']:>+8.4f}  {s['mean_ret']*1e4:>+14.2f}  {s['edge_mean_ret']*1e4:>+14.2f}")

    print()
    print("legend: edge_p = p_up(pattern) - baseline p_up.  Materially > 0 with CI clear of baseline = candidate edge.")
    print("        edge_ret in basis points (1 bp = 0.01 %).  Sign matters more than magnitude on 1-bar horizon.")
    print()
    # Surface the top-3 candidates by absolute edge magnitude × sqrt(n)
    # (a quick rank that punishes tiny-sample noise).
    rows.sort(key=lambda r: abs(r[5]) * math.sqrt(r[1]), reverse=True)
    if rows:
        print("Top-3 candidates by |edge_p| × √n:")
        for r in rows[:3]:
            name, n, p_up, lo, hi, edge_p, mean_ret, edge_ret = r
            direction = "BULLISH" if edge_p > 0 else "BEARISH"
            print(f"  {name:<22}  {direction:<7}  n={n}  p_up={p_up:.4f}  edge={edge_p:+.4f}  "
                   f"next-bar return={mean_ret*1e4:+.2f} bps")
    print()


if __name__ == "__main__":
    main()
