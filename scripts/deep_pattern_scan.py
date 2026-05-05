#!/usr/bin/env python3
"""
Deep bar-level pattern scan: forward-return effect sizes (multi-horizon),
Welch t-test + BH FDR, IS/OOS sign stability, and RTH phase stratification
on top features.

Outputs ``docs/alpha/deep_scan_<SYM>.md`` (+ a ``deep_scan_INDEX.md`` for
multi-symbol runs).

Examples:

  .venv/bin/python scripts/deep_pattern_scan.py --symbols MNQ MES MGC \\
    --start 2024-01-01

  .venv/bin/python scripts/deep_pattern_scan.py --symbol MNQ \\
    --csv historical_data/price/MNQ_1m_databento_GLBX-20260504-UDPDE7PWXR.csv \\
    --horizons 1 3 6 12 24 --min-pts 0.3
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple
from zoneinfo import ZoneInfo

import pandas as pd

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from core.backtest.data_loader import HistoricalDataLoader
from core.research.deep_pattern_scan import (
    bracket_grid,
    build_features,
    combo_scan,
    deep_scan,
    render_deep_report,
    stop_only_grid,
    stratify_by_session_phase,
)
from core.research.pattern_conditional import ensure_ny_index, resample_ohlcv

_NY = ZoneInfo("America/New_York")


def _default_databento_csv(symbol: str) -> Path:
    root = REPO / "historical_data" / "price"
    matches = sorted(root.glob(f"{symbol}_1m_databento_*.csv"))
    if not matches:
        raise FileNotFoundError(f"No {symbol}_1m_databento_*.csv under {root}")
    return matches[-1]


def _parse_date(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _abs(p: Path) -> Path:
    return p if p.is_absolute() else (REPO / p)


_TICK_SIZES: Dict[str, float] = {
    "MNQ": 0.25, "NQ": 0.25,
    "MES": 0.25, "ES": 0.25,
    "MGC": 0.10, "GC": 0.10,
    "MYM": 1.0, "YM": 1.0,
    "M2K": 0.10, "RTY": 0.10,
}
_POINT_VALUES: Dict[str, float] = {
    "MNQ": 2.0, "NQ": 20.0,
    "MES": 5.0, "ES": 50.0,
    "MGC": 10.0, "GC": 100.0,
    "MYM": 0.5, "YM": 5.0,
    "M2K": 5.0, "RTY": 50.0,
}


def run_one(
    symbol: str,
    csv_path: Path,
    start: Optional[datetime],
    end: Optional[datetime],
    horizons: Tuple[int, ...],
    oos_frac: float,
    tradeable_min_pts: float,
    strat_top_k: int,
    *,
    do_combos: bool = True,
    do_brackets: bool = True,
    bracket_max_bars: int = 12,
) -> str:
    loader = HistoricalDataLoader()
    df = loader.load_from_csv(str(csv_path), symbol)
    df = ensure_ny_index(df)
    if start is not None:
        t0 = pd.Timestamp(start)
        if t0.tz is None:
            t0 = t0.tz_localize(_NY)
        df = df[df.index >= t0]
    if end is not None:
        t1 = pd.Timestamp(end)
        if t1.tz is None:
            t1 = t1.tz_localize(_NY)
        df = df[df.index <= t1]
    if len(df) < 1000:
        raise ValueError(f"{symbol}: only {len(df)} 1m bars after filter")
    df5 = resample_ohlcv(df, "5min")
    findings = deep_scan(df5, horizons=horizons, oos_frac=oos_frac)

    # Pick top-K features by |diff_pts| at the LARGEST horizon to stratify
    big_h = max(horizons)
    top_at_big_h = sorted(
        [f for f in findings if f.horizon == big_h and not f.name.startswith("rth_")],
        key=lambda f: -abs(f.diff_pts),
    )[:strat_top_k]
    stratified: List = []
    for f in top_at_big_h:
        stratified.extend(
            stratify_by_session_phase(
                df5, feature_name=f.name, horizon=big_h, oos_frac=oos_frac
            )
        )

    combos: List = []
    if do_combos:
        # Use top features at horizon=1 (highest signal density / least autocorrelation)
        h1 = sorted(
            [
                f
                for f in findings
                if f.horizon == 1
                and not f.name.startswith("rth_")
                and f.sign_stable
                and f.sig_fdr
            ],
            key=lambda f: -abs(f.diff_pts),
        )[:6]
        names = [f.name for f in h1]
        for h in (1, big_h):
            combos.extend(combo_scan(df5, names, horizon=h, oos_frac=oos_frac))

    bracket_tables: List = []
    if do_brackets:
        feats_built = build_features(df5)
        tick = _TICK_SIZES.get(symbol.upper(), 0.25)
        # Top 4 stable+significant single features at h=1
        h1_top = sorted(
            [
                f
                for f in findings
                if f.horizon == 1
                and not f.name.startswith("rth_")
                and f.sign_stable
                and f.sig_fdr
            ],
            key=lambda f: -abs(f.diff_pts),
        )[:4]
        for f in h1_top:
            mask = feats_built.get(f.name)
            if mask is None:
                continue
            direction = "LONG" if f.diff_pts > 0 else "SHORT"
            grid = bracket_grid(
                df5,
                feature_mask=mask,
                direction=direction,
                stop_atr_mults=(0.5, 1.0, 1.5, 2.0),
                tp_atr_mults=(0.5, 1.0, 1.5, 2.0, 3.0),
                max_bars=bracket_max_bars,
                tick_size=tick,
            )
            for g in grid:
                g.feature = f.name
            bracket_tables.append((f.name, direction, grid))

        # Top 3 stable+significant **combo** features at h=1 — bracket the AND mask.
        if combos:
            combo_top = sorted(
                [
                    c
                    for c in combos
                    if c.horizon == 1 and c.sign_stable and c.sig_fdr
                ],
                key=lambda c: -abs(c.diff_pts),
            )[:3]
            for c in combo_top:
                fa, _, fb = c.name.partition(" & ")
                ma = feats_built.get(fa)
                mb = feats_built.get(fb)
                if ma is None or mb is None:
                    continue
                mask = ma & mb
                direction = "LONG" if c.diff_pts > 0 else "SHORT"
                grid = bracket_grid(
                    df5,
                    feature_mask=mask,
                    direction=direction,
                    stop_atr_mults=(0.5, 1.0, 1.5, 2.0),
                    tp_atr_mults=(0.5, 1.0, 1.5, 2.0, 3.0),
                    max_bars=bracket_max_bars,
                    tick_size=tick,
                )
                for g in grid:
                    g.feature = c.name
                bracket_tables.append((c.name, direction, grid))

    stop_only_tables: List = []
    if do_brackets:
        feats_built = build_features(df5)
        tick = _TICK_SIZES.get(symbol.upper(), 0.25)
        # Stop-only grid for the top 2 single + top 2 combo features
        stop_only_targets: List[Tuple[str, str, np.ndarray]] = []
        h1_top2 = sorted(
            [
                f
                for f in findings
                if f.horizon == 1
                and not f.name.startswith("rth_")
                and f.sign_stable
                and f.sig_fdr
            ],
            key=lambda f: -abs(f.diff_pts),
        )[:2]
        for f in h1_top2:
            mask = feats_built.get(f.name)
            if mask is None:
                continue
            direction = "LONG" if f.diff_pts > 0 else "SHORT"
            stop_only_targets.append((f.name, direction, mask))
        if combos:
            combo_top2 = sorted(
                [c for c in combos if c.horizon == 1 and c.sign_stable and c.sig_fdr],
                key=lambda c: -abs(c.diff_pts),
            )[:2]
            for c in combo_top2:
                fa, _, fb = c.name.partition(" & ")
                ma = feats_built.get(fa)
                mb = feats_built.get(fb)
                if ma is None or mb is None:
                    continue
                mask = ma & mb
                direction = "LONG" if c.diff_pts > 0 else "SHORT"
                stop_only_targets.append((c.name, direction, mask))
        for feat_name, direction, mask in stop_only_targets:
            grid = stop_only_grid(
                df5,
                feature_mask=mask,
                direction=direction,
                stop_atr_mults=(0.5, 1.0, 1.5, 2.0),
                hold_bars_options=(3, 6, 12, 24),
                tick_size=tick,
            )
            for g in grid:
                g.feature = feat_name
            stop_only_tables.append((feat_name, direction, grid))

    try:
        rel = str(csv_path.relative_to(REPO))
    except ValueError:
        rel = str(csv_path)

    return render_deep_report(
        symbol,
        csv_rel_path=rel,
        n_bars_5m=len(df5),
        findings=findings,
        stratified=stratified,
        horizons=horizons,
        oos_frac=oos_frac,
        tradeable_min_pts=tradeable_min_pts,
        combos=combos,
        bracket_tables=bracket_tables,
        stop_only_tables=stop_only_tables,
        tick_size=_TICK_SIZES.get(symbol.upper(), 0.25),
        point_value=_POINT_VALUES.get(symbol.upper(), 1.0),
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbol", help="Single symbol (e.g. MNQ)")
    ap.add_argument(
        "--symbols",
        nargs="+",
        help="Multiple symbols; uses latest databento CSV per symbol if --csv omitted",
    )
    ap.add_argument("--csv", type=Path, help="Path to 1m OHLCV CSV (single --symbol only)")
    ap.add_argument("--start", default=None, help="Inclusive start (ISO date)")
    ap.add_argument("--end", default=None, help="Inclusive end (ISO date)")
    ap.add_argument(
        "--horizons",
        nargs="+",
        type=int,
        default=[1, 3, 6, 12],
        help="Forward windows in 5m bars (default: 1 3 6 12 = 5/15/30/60 min)",
    )
    ap.add_argument("--oos-frac", type=float, default=0.3)
    ap.add_argument(
        "--min-pts",
        type=float,
        default=0.5,
        help="Tradeable filter: minimum |diff_pts| (default 0.5)",
    )
    ap.add_argument(
        "--strat-top-k",
        type=int,
        default=4,
        help="Stratify by RTH phase for the top-K features at the longest horizon",
    )
    ap.add_argument("--no-combos", action="store_true", help="Skip pairwise combo scan")
    ap.add_argument("--no-brackets", action="store_true", help="Skip triple-barrier bracket sweep")
    ap.add_argument(
        "--bracket-max-bars",
        type=int,
        default=12,
        help="Max forward bars for stop/TP simulation (default 12 = 60 min on 5m)",
    )
    ap.add_argument("--output", type=Path, help="Markdown output path (single symbol)")
    ap.add_argument("--output-dir", type=Path, help="Per-symbol files (default docs/alpha)")
    args = ap.parse_args()

    if not args.symbol and not args.symbols:
        args.symbols = ["MNQ", "MES", "MGC"]
    syms: List[str]
    if args.symbol:
        syms = [args.symbol.upper()]
    else:
        syms = [s.upper() for s in (args.symbols or [])]

    start = _parse_date(args.start)
    end = _parse_date(args.end)

    if args.output and len(syms) > 1:
        ap.error("--output is only valid with a single symbol")
    if not args.output and not args.output_dir:
        args.output_dir = Path("docs/alpha")

    out_paths: List[Path] = []
    for sym in syms:
        csv_p = args.csv if args.csv and len(syms) == 1 else _default_databento_csv(sym)
        if args.csv and len(syms) > 1:
            ap.error("Pass per-symbol --csv with --symbol, or omit --csv for --symbols")
        text = run_one(
            sym,
            csv_p,
            start,
            end,
            tuple(args.horizons),
            args.oos_frac,
            args.min_pts,
            args.strat_top_k,
            do_combos=not args.no_combos,
            do_brackets=not args.no_brackets,
            bracket_max_bars=args.bracket_max_bars,
        )
        if args.output:
            outp = _abs(args.output)
        else:
            d = _abs(args.output_dir or Path("docs/alpha"))
            d.mkdir(parents=True, exist_ok=True)
            outp = d / f"deep_scan_{sym}.md"
        outp.write_text(text, encoding="utf-8")
        print(f"Wrote {outp.relative_to(REPO)}")
        out_paths.append(outp)

    if len(out_paths) > 1:
        idx = _abs(Path("docs/alpha")) / "deep_scan_INDEX.md"
        lines = [
            "# Deep pattern scan outputs",
            "",
            "Forward-return effect sizes (points) at multiple horizons; Welch t-test + BH FDR; "
            "IS/OOS sign stability on session-date split; phase-stratified follow-up on top features.",
            "",
            "Regenerate:",
            "",
            "```bash",
            ".venv/bin/python scripts/deep_pattern_scan.py --symbols MNQ MES MGC \\",
            "  --start 2024-01-01",
            "```",
            "",
            "See [`../ALPHA_DISCOVERY.md`](../ALPHA_DISCOVERY.md) § \"Conditional short-horizon scan\".",
            "",
        ]
        for p in out_paths:
            lines.append(f"- [`{p.name}`]({p.name})")
        idx.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"Wrote {idx.relative_to(REPO)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
