#!/usr/bin/env python3
"""Body-reversion combo-trigger audit.

For each symbol (MNQ / MES / MGC by default) and each direction (LONG /
SHORT), pair the **big-body anchor** (`big_bear_body>0.9` for LONG,
`big_bull_body>0.9` for SHORT) with each of a curated list of **co-triggers**
(VWAP deviation, ATR regime, RTH phase, volume spike, gap, RSI, SMA-side,
range expansion, follow-through bars, …). For every joint mask, simulate:

    1. **Stop-only / fixed-hold**: stop = 0.5 × ATR, hold = 6 bars (= 30 min)
    2. **Triple-barrier**: stop = 0.5 × ATR, TP = 3.0 R, max_bars = 6

R is normalised by stop distance. Output is a per-symbol markdown table
sorted by mean_R, plus a delta column vs the unconditional anchor — so you
can see which co-triggers genuinely amplify the edge after costs.

Outputs ``docs/alpha/body_reversion_combos.md``.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from core.backtest.data_loader import HistoricalDataLoader
from core.research.deep_pattern_scan import (
    BracketResult,
    build_features,
    realized_r_with_brackets,
    realized_stop_only_fixed_hold,
)
from core.research.pattern_conditional import ensure_ny_index, resample_ohlcv

_NY = ZoneInfo("America/New_York")

_TICK_SIZES: Dict[str, float] = {
    "MNQ": 0.25, "NQ": 0.25,
    "MES": 0.25, "ES": 0.25,
    "MGC": 0.10, "GC": 0.10,
}
_POINT_VALUES: Dict[str, float] = {
    "MNQ": 2.0, "NQ": 20.0,
    "MES": 5.0, "ES": 50.0,
    "MGC": 10.0, "GC": 100.0,
}

# (anchor_feature, direction)
_ANCHORS: List[Tuple[str, str]] = [
    ("big_bear_body>0.9", "LONG"),
    ("big_bull_body>0.9", "SHORT"),
]

# Co-triggers to AND with the anchor. We curate semantically distinct features
# (no body-fraction overlaps), and for some co-triggers we also include a
# direction-aware variant (e.g. only `vwap_dev<-5bp` is sensible to AND with
# the bear-anchor / LONG case).
_GENERIC_COTRIGGERS: List[str] = [
    "atr_high_q4",
    "atr_low_q1",
    "vol_spike_2x",
    "vol_spike_3x",
    "range_expand_1.5x",
    "range_contract_0.5x",
    "rth_open_60m",
    "rth_mid",
    "rth_close_120m",
    # Note: eth_overnight is NOT a feature key — we'll synthesize it as ~rth_any.
]

# Direction-specific co-triggers: applied only when the trade direction is
# semantically aligned (e.g. `vwap_dev<-5bp` only on LONG; `gap_dn_5bp` only
# on LONG; `bull_engulf` is reversion-bullish so only on LONG, etc).
_LONG_COTRIGGERS: List[str] = [
    "vwap_dev<-5bp",
    "below_sma20",
    "rsi<30",
    "gap_dn_5bp",
    "bb_lower_touch",
    "2bear",
]
_SHORT_COTRIGGERS: List[str] = [
    "vwap_dev>+5bp",
    "above_sma20",
    "rsi>70",
    "gap_up_5bp",
    "bb_upper_touch",
    "2bull",
]


def _default_databento_5m_csv(symbol: str) -> Path:
    p = REPO / "historical_data" / "price" / f"{symbol}_5m_databento.csv"
    if p.exists():
        return p
    matches = sorted((REPO / "historical_data" / "price").glob(f"{symbol}_1m_databento_*.csv"))
    if not matches:
        raise FileNotFoundError(f"No {symbol}_1m_databento_*.csv or 5m resampled CSV found")
    return matches[-1]


def _parse_date(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _is_5m_csv(p: Path) -> bool:
    return "_5m_" in p.name


def _load_5m(csv: Path, symbol: str, start: Optional[datetime], end: Optional[datetime]) -> pd.DataFrame:
    loader = HistoricalDataLoader()
    df = loader.load_from_csv(str(csv), symbol)
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
    if not _is_5m_csv(csv):
        df = resample_ohlcv(df, "5min")
    return df


def _eth_overnight_mask(feats: Dict[str, np.ndarray]) -> np.ndarray:
    rth_any = feats.get("rth_any")
    if rth_any is None:
        # Fallback — should never happen with the current build_features.
        return np.zeros(0, dtype=bool)
    return ~rth_any


_TRIPLE_COTRIGGERS_LONG: List[Tuple[str, str]] = [
    ("atr_high_q4", "vwap_dev<-5bp"),
    ("atr_high_q4", "below_sma20"),
    ("atr_high_q4", "range_expand_1.5x"),
    ("range_expand_1.5x", "vwap_dev<-5bp"),
    ("range_expand_1.5x", "below_sma20"),
    ("atr_high_q4", "bb_lower_touch"),
]
_TRIPLE_COTRIGGERS_SHORT: List[Tuple[str, str]] = [
    ("atr_high_q4", "vwap_dev>+5bp"),
    ("atr_high_q4", "above_sma20"),
    ("atr_high_q4", "range_expand_1.5x"),
    ("range_expand_1.5x", "vwap_dev>+5bp"),
    ("range_expand_1.5x", "above_sma20"),
    ("atr_high_q4", "bb_upper_touch"),
]


def _audit_one(
    symbol: str,
    df5: pd.DataFrame,
    *,
    stop_atr: float,
    hold_bars: int,
    tp_r: float,
    max_bars: int,
) -> List[Dict[str, object]]:
    feats = build_features(df5)
    feats = dict(feats)  # local copy
    feats["eth_overnight"] = _eth_overnight_mask(feats)
    tick = _TICK_SIZES.get(symbol.upper(), 0.25)
    point_value = _POINT_VALUES.get(symbol.upper(), 2.0)

    rows: List[Dict[str, object]] = []
    for anchor_name, direction in _ANCHORS:
        anchor = feats.get(anchor_name)
        if anchor is None or anchor.sum() == 0:
            continue

        # Baseline (anchor only)
        so = realized_stop_only_fixed_hold(
            df5, anchor, direction, stop_atr_mult=stop_atr, hold_bars=hold_bars,
            tick_size=tick,
        )
        bk = realized_r_with_brackets(
            df5, anchor, direction, stop_atr_mult=stop_atr, tp_atr_mult=tp_r,
            max_bars=max_bars, tick_size=tick,
        )
        baseline_so_meanr = so["mean_r"]
        baseline_so_pf = so["profit_factor"]
        rows.append(
            {
                "symbol": symbol,
                "anchor": anchor_name,
                "direction": direction,
                "co_trigger": "(baseline — anchor only)",
                "n": so["n"],
                "win%": so["win_rate"],
                "mean_R_so": so["mean_r"],
                "PF_so": so["profit_factor"],
                "mean_R_bk": bk["mean_r"],
                "PF_bk": bk["profit_factor"],
                "delta_R": 0.0,
                "delta_PF": 0.0,
                "delta_dollar_per_signal": 0.0,
                "anchor_meanR": baseline_so_meanr,
                "anchor_PF": baseline_so_pf,
            }
        )

        # Build candidate list per direction
        co_list: List[str] = list(_GENERIC_COTRIGGERS)
        if direction == "LONG":
            co_list.extend(_LONG_COTRIGGERS)
        else:
            co_list.extend(_SHORT_COTRIGGERS)
        co_list.append("eth_overnight")

        # Cache median ATR for $-translation
        from core.research.deep_pattern_scan import _atr_series
        atr = _atr_series(df5, 14)
        atr_med = float(np.nanmedian(atr)) if atr.size else float("nan")
        stop_pts = stop_atr * atr_med

        def _evaluate(joint_mask: np.ndarray, label: str) -> None:
            if joint_mask.sum() < 200:
                return
            so_r = realized_stop_only_fixed_hold(
                df5, joint_mask, direction, stop_atr_mult=stop_atr,
                hold_bars=hold_bars, tick_size=tick,
            )
            bk_r = realized_r_with_brackets(
                df5, joint_mask, direction, stop_atr_mult=stop_atr,
                tp_atr_mult=tp_r, max_bars=max_bars, tick_size=tick,
            )
            d = so_r["mean_r"] - baseline_so_meanr
            d_pf = so_r["profit_factor"] - baseline_so_pf
            d_dollar = d * stop_pts * point_value if np.isfinite(stop_pts) else float("nan")
            rows.append(
                {
                    "symbol": symbol,
                    "anchor": anchor_name,
                    "direction": direction,
                    "co_trigger": label,
                    "n": so_r["n"],
                    "win%": so_r["win_rate"],
                    "mean_R_so": so_r["mean_r"],
                    "PF_so": so_r["profit_factor"],
                    "mean_R_bk": bk_r["mean_r"],
                    "PF_bk": bk_r["profit_factor"],
                    "delta_R": d,
                    "delta_PF": d_pf,
                    "delta_dollar_per_signal": d_dollar,
                    "anchor_meanR": baseline_so_meanr,
                    "anchor_PF": baseline_so_pf,
                }
            )

        for co_name in co_list:
            co_mask = feats.get(co_name)
            if co_mask is None:
                continue
            _evaluate(anchor & co_mask, co_name)

        # 3-way combos (anchor × A × B): test the most promising compounding pairs
        triple_pairs = (
            _TRIPLE_COTRIGGERS_LONG if direction == "LONG" else _TRIPLE_COTRIGGERS_SHORT
        )
        for a_name, b_name in triple_pairs:
            ma = feats.get(a_name)
            mb = feats.get(b_name)
            if ma is None or mb is None:
                continue
            _evaluate(anchor & ma & mb, f"{a_name} & {b_name}")
    return rows


def _render_md(
    by_symbol: Dict[str, List[Dict[str, object]]],
    *,
    stop_atr: float,
    hold_bars: int,
    tp_r: float,
    max_bars: int,
) -> str:
    lines: List[str] = [
        "# Body-reversion combo-trigger audit",
        "",
        "Pair the **big-body anchor** with each candidate co-trigger and report",
        "**realized R** under two execution models. R unit = stop distance.",
        "",
        f"- **Stop-only / fixed-hold (`SO`)**: stop = `{stop_atr:.2f}×ATR`, hold = `{hold_bars}` bars (= {hold_bars*5} min)",
        f"- **Triple-barrier (`BK`)**: stop = `{stop_atr:.2f}×ATR`, TP = `{tp_r:.1f}R`, max_bars = `{max_bars}`",
        "",
        "**delta_R / delta_PF** are vs the unconditional anchor (rows tagged",
        "*baseline*). A positive delta_R means the co-trigger genuinely amplifies",
        "the edge; a negative delta_R means the co-trigger over-restricts to a",
        "less-edge subset. **delta_$/signal** approximates the $ improvement per",
        "trade (`delta_R × stop_pts × $/pt`), using the median 14-bar ATR as a",
        "proxy for stop-dollar magnitude.",
        "",
        "_Co-triggers with **n < 200** are dropped (too noisy)._",
        "",
        "Curated co-trigger list:",
        "",
        "- **VWAP**: `vwap_dev<-5bp` (LONG-only), `vwap_dev>+5bp` (SHORT-only)",
        "- **ATR regime**: `atr_high_q4`, `atr_low_q1` (14-bar ATR quartiles)",
        "- **Volume**: `vol_spike_2x`, `vol_spike_3x` (vs 50-bar MA)",
        "- **Range**: `range_expand_1.5x`, `range_contract_0.5x` (vs 20-bar MA)",
        "- **Session phase**: `rth_open_60m`, `rth_mid`, `rth_close_120m`, `eth_overnight`",
        "- **Trend**: `below_sma20` (LONG-only), `above_sma20` (SHORT-only)",
        "- **Oscillator**: `rsi<30` (LONG-only), `rsi>70` (SHORT-only)",
        "- **Gap**: `gap_dn_5bp` (LONG-only), `gap_up_5bp` (SHORT-only)",
        "- **Bollinger**: `bb_lower_touch` (LONG-only), `bb_upper_touch` (SHORT-only)",
        "- **Follow-through**: `2bear` (LONG-only), `2bull` (SHORT-only)",
        "",
    ]

    for symbol, rows in sorted(by_symbol.items()):
        for direction in ("LONG", "SHORT"):
            sub = [r for r in rows if r["direction"] == direction]
            if not sub:
                continue
            anchor = sub[0]["anchor"]
            lines.append(f"## {symbol} — {anchor} ({direction})")
            lines.append("")
            base = next((r for r in sub if r["co_trigger"].startswith("(baseline")), None)
            if base is not None:
                lines.append(
                    f"_Baseline (anchor only, n={base['n']:,}): mean_R_SO=`{base['mean_R_so']:+.3f}`, "
                    f"PF_SO=`{base['PF_so']:.2f}`, mean_R_BK=`{base['mean_R_bk']:+.3f}`, "
                    f"PF_BK=`{base['PF_bk']:.2f}`._"
                )
                lines.append("")
            lines.append(
                "| co_trigger | n | win% | mean_R_SO | PF_SO | mean_R_BK | PF_BK | delta_R | delta_PF | delta_$/sig |"
            )
            lines.append(
                "|------------|---|------|-----------|-------|-----------|-------|---------|----------|-------------|"
            )
            sub_sorted = sorted(
                [r for r in sub if not r["co_trigger"].startswith("(baseline")],
                key=lambda r: -float(r["delta_R"]),
            )
            if base is not None:
                # Always show baseline row first
                show_rows = [base] + sub_sorted
            else:
                show_rows = sub_sorted
            for r in show_rows:
                pf_so = r["PF_so"]
                pf_bk = r["PF_bk"]
                pf_so_str = f"{pf_so:.2f}" if np.isfinite(pf_so) else "∞"
                pf_bk_str = f"{pf_bk:.2f}" if np.isfinite(pf_bk) else "∞"
                dpf = r["delta_PF"]
                dpf_str = f"{dpf:+.2f}" if np.isfinite(dpf) else "—"
                dd = r["delta_dollar_per_signal"]
                dd_str = f"{dd:+.2f}" if np.isfinite(dd) else "—"
                wr = r["win%"]
                wr_str = f"{wr*100:.1f}%" if np.isfinite(wr) else "—"
                lines.append(
                    f"| `{r['co_trigger']}` | {r['n']:,} | {wr_str} | "
                    f"{r['mean_R_so']:+.3f} | {pf_so_str} | "
                    f"{r['mean_R_bk']:+.3f} | {pf_bk_str} | "
                    f"{float(r['delta_R']):+.3f} | {dpf_str} | {dd_str} |"
                )
            lines.append("")
    lines += [
        "## How to read",
        "",
        "1. **Pick co-triggers with `delta_R > 0` AND `n ≥ 500`.** Those are the",
        "   ones that both amplify the edge and have enough population to actually",
        "   trade.",
        "2. **Don't pick the highest delta_R blindly** — a co-trigger that drops `n`",
        "   from 2,500 to 220 is statistical noise even if delta_R is +0.10.",
        "3. **Cross-instrument robustness** matters: prefer a co-trigger whose",
        "   delta_R is positive on **MNQ AND MES AND MGC**, even if any single",
        "   symbol has a more aggressive option.",
        "4. **`PF_BK` < `PF_SO`** is normal: the 0.5×ATR / 3R / 6-bar bracket",
        "   forces an early time-stop on roll-out trades that would have made the",
        "   stop-only/fixed-hold model.",
        "",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbols", nargs="+", default=["MNQ", "MES", "MGC"])
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    ap.add_argument("--stop-atr", type=float, default=0.5)
    ap.add_argument("--hold-bars", type=int, default=6)
    ap.add_argument("--tp-r", type=float, default=3.0)
    ap.add_argument("--max-bars", type=int, default=6)
    ap.add_argument(
        "--output",
        type=Path,
        default=REPO / "docs" / "alpha" / "body_reversion_combos.md",
    )
    args = ap.parse_args()

    start = _parse_date(args.start)
    end = _parse_date(args.end)

    by_symbol: Dict[str, List[Dict[str, object]]] = {}
    for sym in args.symbols:
        sym = sym.upper()
        csv = _default_databento_5m_csv(sym)
        df5 = _load_5m(csv, sym, start, end)
        if len(df5) < 1000:
            print(f"[{sym}] only {len(df5)} 5m bars — skip", file=sys.stderr)
            continue
        rows = _audit_one(
            sym,
            df5,
            stop_atr=args.stop_atr,
            hold_bars=args.hold_bars,
            tp_r=args.tp_r,
            max_bars=args.max_bars,
        )
        by_symbol[sym] = rows
        print(f"[{sym}] {len(rows)} rows on {len(df5):,} 5m bars (csv={csv.name})")

    md = _render_md(
        by_symbol,
        stop_atr=args.stop_atr,
        hold_bars=args.hold_bars,
        tp_r=args.tp_r,
        max_bars=args.max_bars,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(md, encoding="utf-8")
    print(f"Wrote {args.output.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
