"""
Conditional probability / independence scans on OHLCV bars.

Research-only: not imported by live trading paths. Uses Fisher exact tests
for 2×2 tables and Benjamini–Hochberg FDR across many simultaneous tests.

Typical workflow: load 1m CSV via HistoricalDataLoader, resample to 5m in
America/New_York, run ``collect_pattern_results`` → markdown via
``render_pattern_report``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from scipy.stats import binomtest, fisher_exact

NY = "America/New_York"


@dataclass
class PatternRow:
    """One hypothesis: feature present vs absent × binary outcome."""

    name: str
    description: str
    n_feat: int  # rows with feature True
    k_feat: int  # outcome True among feature rows
    n_rest: int  # rows with feature False (eligible baseline)
    k_rest: int  # outcome True among non-feature rows
    p_fisher: float
    rate_feat: float
    rate_rest: float
    lift: float  # rate_feat / rate_rest if rate_rest > 0 else nan

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "n_feat": self.n_feat,
            "k_feat": self.k_feat,
            "n_rest": self.n_rest,
            "k_rest": self.k_rest,
            "p_fisher": self.p_fisher,
            "rate_feat": self.rate_feat,
            "rate_rest": self.rate_rest,
            "lift": self.lift,
        }


def benjamini_hochberg(p_values: np.ndarray, alpha: float = 0.05) -> np.ndarray:
    """Return boolean mask: True for hypotheses rejected by BH FDR (significant)."""
    m = len(p_values)
    if m == 0:
        return np.array([], dtype=bool)
    order = np.argsort(p_values)
    sp = p_values[order]
    crit = (np.arange(1, m + 1) / m) * alpha
    below = sp <= crit
    if not below.any():
        cutoff = float("-inf")
    else:
        last = int(np.where(below)[0].max())
        cutoff = float(sp[last])
    return p_values <= cutoff


def _fisher_table(feat: np.ndarray, outcome: np.ndarray) -> Optional[Tuple[int, int, int, int]]:
    """2×2: [[feat&out, feat&~out], [~feat&out, ~feat&~out]]. None if degenerate."""
    ff = feat & outcome
    ft = feat & ~outcome
    tf = ~feat & outcome
    tt = ~feat & ~outcome
    a, b, c, d = int(ff.sum()), int(ft.sum()), int(tf.sum()), int(tt.sum())
    if min(a, b, c, d) == 0 and a + b + c + d == 0:
        return None
    return a, b, c, d


def _row_from_table(
    name: str,
    desc: str,
    a: int,
    b: int,
    c: int,
    d: int,
) -> PatternRow:
    _, p = fisher_exact([[a, b], [c, d]])
    n_feat, k_feat = a + b, a
    n_rest, k_rest = c + d, c
    rf = k_feat / n_feat if n_feat else float("nan")
    rr = k_rest / n_rest if n_rest else float("nan")
    lift = rf / rr if rr and rr > 0 else float("nan")
    return PatternRow(name, desc, n_feat, k_feat, n_rest, k_rest, float(p), rf, rr, lift)


def ensure_ny_index(df: pd.DataFrame) -> pd.DataFrame:
    """Ensure DatetimeIndex is tz-aware in America/New_York."""
    out = df.copy()
    if out.index.tz is None:
        out.index = out.index.tz_localize("UTC", ambiguous="infer", nonexistent="shift_forward")
    out.index = out.index.tz_convert(NY)
    return out


def resample_ohlcv(df: pd.DataFrame, rule: str = "5min") -> pd.DataFrame:
    """Resample OHLCV on a sorted tz-aware index."""
    o = df["open"].resample(rule, label="right", closed="right").first()
    h = df["high"].resample(rule, label="right", closed="right").max()
    l = df["low"].resample(rule, label="right", closed="right").min()
    c = df["close"].resample(rule, label="right", closed="right").last()
    v = df["volume"].resample(rule, label="right", closed="right").sum()
    out = pd.concat([o, h, l, c, v], axis=1)
    out.columns = ["open", "high", "low", "close", "volume"]
    return out.dropna(how="any")


def _rth_mask(idx: pd.DatetimeIndex) -> pd.Series:
    minutes = idx.hour * 60 + idx.minute
    rth_start = 9 * 60 + 30
    rth_end = 16 * 60
    return pd.Series((minutes >= rth_start) & (minutes < rth_end), index=idx)


def _ny_day(idx: pd.DatetimeIndex) -> pd.Series:
    return pd.Series(idx.normalize(), index=idx)


def attach_prev_day_levels(df5: pd.DataFrame) -> pd.DataFrame:
    """Add prev_day_high / prev_day_low from full ETH calendar day in NY."""
    out = df5.copy()
    out["_day"] = pd.Series(out.index.tz_convert(NY).normalize(), index=out.index)
    daily = out.groupby("_day", sort=True).agg({"high": "max", "low": "min"})
    ph = daily["high"].shift(1)
    pl = daily["low"].shift(1)
    out["prev_day_high"] = out["_day"].map(ph)
    out["prev_day_low"] = out["_day"].map(pl)
    return out


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    up = delta.clip(lower=0.0)
    down = (-delta).clip(lower=0.0)
    ma_up = up.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    ma_down = down.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = ma_up / ma_down.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def session_vwap(df5: pd.DataFrame) -> pd.Series:
    """Reset VWAP each NY calendar day (ETH session, simple cumulative)."""
    tp = (df5["high"] + df5["low"] + df5["close"]) / 3.0
    day = pd.Series(df5.index.tz_convert(NY).normalize(), index=df5.index)
    pv = tp * df5["volume"]
    pv_c = pv.groupby(day).cumsum()
    v_c = df5["volume"].groupby(day).cumsum()
    return pv_c / v_c.replace(0, np.nan)


def bollinger(close: pd.Series, n: int = 20, k: float = 2.0) -> Tuple[pd.Series, pd.Series, pd.Series]:
    mid = close.rolling(n, min_periods=n).mean()
    std = close.rolling(n, min_periods=n).std(ddof=0)
    upper = mid + k * std
    lower = mid - k * std
    return lower, mid, upper


def collect_pattern_results(
    df5: pd.DataFrame,
    min_cell: int = 5,
    min_feat: int = 40,
    min_rest: int = 40,
) -> List[PatternRow]:
    """
    Build many 2×2 tests on 5m bars (index NY).

    Outcome definitions use bar t+1 relative to features known at end of bar t.
    """
    df = df5.copy()
    df = df.sort_index()
    rows: List[PatternRow] = []

    o, h, l, c = df["open"], df["high"], df["low"], df["close"]
    v = df["volume"]

    bull = (c > o).astype(bool)
    bear = (c < o).astype(bool)
    next_bull = bull.shift(-1).fillna(False).astype(bool)
    next_bear = bear.shift(-1).fillna(False).astype(bool)
    ret1 = (c.shift(-1) / c - 1.0).fillna(0)

    # valid interior: need t-2,t-1,t,t+1
    valid = pd.Series(True, index=df.index)
    valid.iloc[:3] = False
    valid.iloc[-2:] = False
    validv = valid.to_numpy()

    def add_mask(name: str, desc: str, feat: pd.Series, outcome: pd.Series) -> None:
        ff = feat.reindex(df.index).fillna(False).astype(bool).to_numpy() & validv
        oo = outcome.reindex(df.index).fillna(False).astype(bool).to_numpy()
        tab = _fisher_table(ff, oo)
        if tab is None:
            return
        a, b, c, d = tab
        if (a + b) < min_feat or (c + d) < min_rest:
            return
        if min(a, b, c, d) < min_cell:
            return
        rows.append(_row_from_table(name, desc, a, b, c, d))

    # --- Last 2 candles → next 5m bullish ---
    b1 = bull.shift(1).fillna(False).astype(bool)
    b2 = bull.shift(2).fillna(False).astype(bool)
    r1 = bear.shift(1).fillna(False).astype(bool)
    r2 = bear.shift(2).fillna(False).astype(bool)
    add_mask(
        "2bull→next_bull",
        "Prior two 5m bars bullish (close>open); outcome: next bar bullish.",
        b1 & b2,
        next_bull,
    )
    add_mask(
        "2bear→next_bull",
        "Prior two 5m bars bearish; outcome: next bar bullish.",
        r1 & r2,
        next_bull,
    )
    add_mask(
        "2bull→next_bear",
        "Prior two bullish; outcome: next bar bearish.",
        b1 & b2,
        next_bear,
    )
    add_mask(
        "inside→next_break_up",
        "Inside bar (high<prev high & low>prev low); next bar closes above current high.",
        (h < h.shift(1)) & (l > l.shift(1)),
        c.shift(-1) > h,
    )
    add_mask(
        "inside→next_break_dn",
        "Inside bar; next bar closes below current low.",
        (h < h.shift(1)) & (l > l.shift(1)),
        c.shift(-1) < l,
    )

    r = rsi(c, 14)
    add_mask(
        "RSI>70→next_bear",
        "RSI(14) > 70 at bar close; next bar bearish.",
        (r > 70) & r.notna(),
        next_bear,
    )
    add_mask(
        "RSI<30→next_bull",
        "RSI(14) < 30; next bar bullish.",
        (r < 30) & r.notna(),
        next_bull,
    )
    add_mask(
        "RSI>70→next_bull",
        "RSI > 70; next bar bullish (fade-up continuation check).",
        (r > 70) & r.notna(),
        next_bull,
    )

    sma20 = c.rolling(20, min_periods=20).mean()
    add_mask(
        "close>SMA20→next_neg_ret",
        "Close above 20-bar SMA; next-bar return (close[t+1]/close[t]-1) < 0.",
        (c > sma20) & sma20.notna(),
        ret1 < 0,
    )
    add_mask(
        "close<SMA20→next_pos_ret",
        "Close below SMA20; next-bar return > 0.",
        (c < sma20) & sma20.notna(),
        ret1 > 0,
    )

    low_bb, mid_bb, up_bb = bollinger(c, 20, 2.0)
    add_mask(
        "touch_lower_BB→next_bull",
        "Low at or below lower Bollinger (20,2); next bar bullish.",
        (l <= low_bb) & low_bb.notna(),
        next_bull,
    )
    add_mask(
        "touch_upper_BB→next_bear",
        "High at or above upper BB; next bar bearish.",
        (h >= up_bb) & up_bb.notna(),
        next_bear,
    )

    vol_ma = v.rolling(50, min_periods=50).mean()
    vol_spike = v > (2.0 * vol_ma)
    add_mask(
        "vol_spike→next_bull",
        "Volume > 2×50-bar mean; next bar bullish.",
        vol_spike & vol_ma.notna(),
        next_bull,
    )
    add_mask(
        "vol_spike→next_bear",
        "Volume spike; next bar bearish.",
        vol_spike & vol_ma.notna(),
        next_bear,
    )

    vw = session_vwap(df)
    dev = (c - vw) / vw.replace(0, np.nan)
    add_mask(
        "close>VWAP+0.05pct→next_neg_ret",
        "Close > session VWAP by >0.05%; next-bar return < 0.",
        (dev > 0.0005) & vw.notna(),
        ret1 < 0,
    )
    add_mask(
        "close<VWAP-0.05pct→next_pos_ret",
        "Close < VWAP −0.05%; next-bar return > 0.",
        (dev < -0.0005) & vw.notna(),
        ret1 > 0,
    )

    # Hour-of-day: first hour of RTH vs next bar
    hod = df.index.hour
    rth_s = _rth_mask(df.index)
    add_mask(
        "RTH_h9→next_bull",
        "Bar ends 9:30–9:59 ET; next 5m bullish.",
        pd.Series((hod == 9) & rth_s.to_numpy(), index=df.index),
        next_bull,
    )
    add_mask(
        "RTH_h14→next_bull",
        "Bar ends 14:00–14:59 ET; next 5m bullish.",
        pd.Series((hod == 14) & rth_s.to_numpy(), index=df.index),
        next_bull,
    )

    # Strong trend bar body
    rng = (h - l).replace(0, np.nan)
    body = (c - o).abs() / rng
    add_mask(
        "big_body>0.7_up→next_bull",
        "Bullish bar with body > 70% of range; next bar bullish.",
        ((c > o) & (body > 0.7) & rng.notna()),
        next_bull,
    )
    add_mask(
        "big_body>0.7_dn→next_bear",
        "Bearish bar body > 70% of range; next bar bearish.",
        ((c < o) & (body > 0.7) & rng.notna()),
        next_bear,
    )

    return rows


def prior_day_level_reversion_stats(df5: pd.DataFrame, max_bars_after: int = 78) -> Dict[str, Any]:
    """
    RTH-only: after first print above prior **calendar** day high (full ETH day),
    does price trade back to or through that high same RTH session within N 5m bars?
    """
    df = attach_prev_day_levels(df5)
    rth = _rth_mask(df.index).to_numpy()
    day = df["_day"].to_numpy()
    ph = df["prev_day_high"].to_numpy()
    pl = df["prev_day_low"].to_numpy()
    hi = df["high"].to_numpy()
    lo = df["low"].to_numpy()

    breaks_high = 0
    revert_high = 0
    breaks_low = 0
    revert_low = 0

    unique_days = pd.unique(day)
    for d in unique_days:
        if pd.isna(d):
            continue
        mask = (day == d) & rth
        if not mask.any():
            continue
        pos = np.where(mask)[0]
        pvh = ph[pos[0]] if len(pos) else np.nan
        pvl = pl[pos[0]] if len(pos) else np.nan
        if np.isnan(pvh) or np.isnan(pvl):
            continue
        # first break above prior day high
        broke_h = False
        first_h = -1
        for i in pos:
            if hi[i] > pvh:
                broke_h = True
                first_h = i
                break
        if broke_h and first_h >= 0:
            breaks_high += 1
            bars_after = 0
            for j in pos:
                if j <= first_h:
                    continue
                bars_after += 1
                if bars_after > max_bars_after:
                    break
                if lo[j] <= pvh:
                    revert_high += 1
                    break
        # first break below prior day low
        broke_l = False
        first_l = -1
        for i in pos:
            if lo[i] < pvl:
                broke_l = True
                first_l = i
                break
        if broke_l and first_l >= 0:
            breaks_low += 1
            bars_after = 0
            for j in pos:
                if j <= first_l:
                    continue
                bars_after += 1
                if bars_after > max_bars_after:
                    break
                if hi[j] >= pvl:
                    revert_low += 1
                    break

    def _binom(k: int, n: int, p0: float = 0.5) -> Tuple[float, float]:
        if n <= 0:
            return float("nan"), float("nan")
        bt = binomtest(k, n, p=p0, alternative="two-sided")
        return float(bt.pvalue), k / n

    p_h, rate_h = _binom(revert_high, breaks_high) if breaks_high else (float("nan"), float("nan"))
    p_l, rate_l = _binom(revert_low, breaks_low) if breaks_low else (float("nan"), float("nan"))

    return {
        "rth_sessions_break_prev_day_high": int(breaks_high),
        "rth_sessions_revert_touch_prev_day_high": int(revert_high),
        "conditional_revert_rate_given_break_high": rate_h,
        "binomial_p_vs_50pct_revert_high": p_h,
        "rth_sessions_break_prev_day_low": int(breaks_low),
        "rth_sessions_revert_touch_prev_day_low": int(revert_low),
        "conditional_revert_rate_given_break_low": rate_l,
        "binomial_p_vs_50pct_revert_low": p_l,
        "note": "Reversion = touch back through prior ETH-day level same RTH date, within max_bars_after 5m bars.",
        "max_bars_after": max_bars_after,
    }


def apply_fdr_to_rows(rows: List[PatternRow], alpha: float = 0.05) -> List[Dict[str, Any]]:
    """Attach q_BH and significant flag."""
    if not rows:
        return []
    ps = np.array([r.p_fisher for r in rows], dtype=float)
    sig = benjamini_hochberg(ps, alpha=alpha)
    out = []
    for i, r in enumerate(rows):
        d = r.to_dict()
        d["significant_fdr"] = bool(sig[i])
        out.append(d)
    return out


def render_pattern_report(
    symbol: str,
    csv_path: str,
    start: Optional[str],
    end: Optional[str],
    n_bars_1m: int,
    n_bars_5m: int,
    rows_fdr: List[Dict[str, Any]],
    level_stats: Dict[str, Any],
    alpha: float = 0.05,
) -> str:
    lines = [
        f"# Conditional pattern scan — {symbol}",
        "",
        f"**CSV:** `{csv_path}`",
        f"**Window filter:** start={start or 'file min'} end={end or 'file max'}",
        f"**Bars:** {n_bars_1m:,} × 1m → {n_bars_5m:,} × 5m (America/New_York)",
        "",
        "This report is **exploratory**. Many simultaneous tests inflate false positives; "
        f"only rows with **FDR q ≤ {alpha}** (Benjamini–Hochberg on Fisher p-values) are highlighted as significant. "
        "Economic significance (lift, costs, slippage) still requires OOS backtests per [BACKTEST_RESEARCH.md](../BACKTEST_RESEARCH.md).",
        "",
        "## Prior calendar-day high / low (RTH breakout → same-day re-touch)",
        "",
        "| Metric | Value |",
        "|--------|-------|",
    ]
    for k, v in level_stats.items():
        if k == "note":
            continue
        lines.append(f"| `{k}` | {v} |")
    lines.append("")
    lines.append(f"_Note: {level_stats.get('note', '')}_")
    lines.append("")
    lines.append(
        "**Caveat (prior-day re-touch):** Intraday paths often **recross** a prior reference level "
        "without a clean economic edge; high conditional rates can reflect path continuity and the "
        "definition of “touch,” not a standalone fade strategy. Validate with tick-aware execution "
        "and net-of-costs backtests before promoting."
    )
    lines.append("")
    lines.append("## 5m feature × next-bar outcomes (Fisher independence)")
    lines.append("")
    lines.append(
        "| name | n(feat) | rate(feat) | n(rest) | rate(rest) | lift | p_Fisher | FDR sig |"
    )
    lines.append("|------|---------|------------|---------|------------|------|----------|---------|")
    for d in sorted(rows_fdr, key=lambda x: x["p_fisher"]):
        sig = "✅" if d["significant_fdr"] else "—"
        lines.append(
            f"| `{d['name']}` | {d['n_feat']} | {d['rate_feat']:.3f} | {d['n_rest']} | {d['rate_rest']:.3f} | "
            f"{d['lift']:.3f} | {d['p_fisher']:.4g} | {sig} |"
        )
    lines.append("")
    lines.append("### Interpretation")
    lines.append("")
    lines.append(
        "- **lift** = P(outcome|feature) / P(outcome|¬feature). With huge **n**, tiny lifts (e.g. 1.03) "
        "can still pass Fisher + FDR yet be **untradeable** after fees/slippage; treat |lift|−1 as "
        "effect size, not p-value alone."
    )
    lines.append(
        "- **Next steps:** promote patterns with ✅ into a dedicated strategy config + "
        "`core/backtest_executor.py --replay` grid; see [CANDIDATES.md](../perf/sweeps/CANDIDATES.md) and "
        "[ALPHA_DISCOVERY.md](../ALPHA_DISCOVERY.md)."
    )
    lines.append("")
    return "\n".join(lines)
