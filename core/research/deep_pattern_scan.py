"""
Deep bar-level pattern scan: forward-return effect sizes (multi-horizon),
Welch t-test + Benjamini–Hochberg FDR, IS/OOS sign-stability split, and
RTH session-phase stratification of top findings.

Research-only. Not imported by live trading paths.

Forward returns are computed in **points** (close[t+h] − close[t]) on **5m**
bars indexed in `America/New_York`. Effect size **diff_pts** =
mean(feature) − mean(rest).

A finding is **stable** if mean(forward_return | feature) − mean(forward_return | rest)
has the **same sign** in both the IS half (first 1−`oos_frac` of session days)
and the OOS half. Sign stability is the cheapest defense against
multiple-testing artifacts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from scipy.stats import ttest_ind

from core.research.pattern_conditional import (
    bollinger,
    rsi,
    session_vwap,
)

NY = ZoneInfo("America/New_York")


def _shift1(arr: np.ndarray) -> np.ndarray:
    out = np.empty(arr.shape[0], dtype=float)
    out[0] = np.nan
    out[1:] = arr[:-1].astype(float, copy=False)
    return out


def build_features(df5: pd.DataFrame) -> Dict[str, np.ndarray]:
    """
    Boolean masks (one per feature). Indexes that have undefined inputs
    (rolling warm-up, edges) come back as ``False`` via NaN-safe comparisons.
    """
    o = df5["open"].to_numpy(float, copy=False)
    h = df5["high"].to_numpy(float, copy=False)
    l = df5["low"].to_numpy(float, copy=False)
    c = df5["close"].to_numpy(float, copy=False)
    v = df5["volume"].to_numpy(float, copy=False)

    bull = c > o
    bear = c < o
    rng = h - l
    with np.errstate(divide="ignore", invalid="ignore"):
        body = np.where(rng > 0, np.abs(c - o) / rng, np.nan)

    pc = _shift1(c)
    po = _shift1(o)
    ph = _shift1(h)
    pl = _shift1(l)
    pbull = _shift1(bull.astype(float))
    pbear = _shift1(bear.astype(float))
    p2bull = _shift1(pbull)
    p2bear = _shift1(pbear)

    rng_s = pd.Series(rng)
    nr4_min = rng_s.rolling(4).min().shift(1).to_numpy()
    nr7_min = rng_s.rolling(7).min().shift(1).to_numpy()
    rng_ma20 = rng_s.rolling(20, min_periods=20).mean().to_numpy()

    rsi14 = rsi(df5["close"], 14).to_numpy()
    sma20 = df5["close"].rolling(20, min_periods=20).mean().to_numpy()
    vw = session_vwap(df5).to_numpy()
    with np.errstate(divide="ignore", invalid="ignore"):
        dev = np.where(vw > 0, (c - vw) / vw, np.nan)
    lo_bb, _, up_bb = bollinger(df5["close"], 20, 2.0)
    lo_bb_v = lo_bb.to_numpy()
    up_bb_v = up_bb.to_numpy()
    vol_ma = pd.Series(v).rolling(50, min_periods=50).mean().to_numpy()
    with np.errstate(divide="ignore", invalid="ignore"):
        gap_pct = np.where(pc > 0, (o - pc) / pc, np.nan)

    idx = df5.index
    minutes = np.asarray(idx.hour) * 60 + np.asarray(idx.minute)
    open_phase = (minutes >= 9 * 60 + 30) & (minutes < 10 * 60 + 30)
    mid_phase = (minutes >= 10 * 60 + 30) & (minutes < 14 * 60)
    close_phase = (minutes >= 14 * 60) & (minutes < 16 * 60)
    rth_all = (minutes >= 9 * 60 + 30) & (minutes < 16 * 60)

    feats: Dict[str, np.ndarray] = {
        "2bull": (pbull == 1) & (p2bull == 1),
        "2bear": (pbear == 1) & (p2bear == 1),
        "inside_bar": (h < ph) & (l > pl),
        "outside_bar": (h > ph) & (l < pl),
        "nr4": rng < nr4_min,
        "nr7": rng < nr7_min,
        "big_bull_body>0.7": bull & (body > 0.7),
        "big_bear_body>0.7": bear & (body > 0.7),
        "big_bull_body>0.85": bull & (body > 0.85),
        "big_bear_body>0.85": bear & (body > 0.85),
        "big_bull_body>0.9": bull & (body > 0.9),
        "big_bear_body>0.9": bear & (body > 0.9),
        "vol_spike_3x": v > (3.0 * vol_ma),
        "atr_high_q4": np.zeros_like(bull, dtype=bool),  # filled below
        "atr_low_q1": np.zeros_like(bull, dtype=bool),  # filled below
        "rsi<30": rsi14 < 30,
        "rsi>70": rsi14 > 70,
        "above_sma20": c > sma20,
        "below_sma20": c < sma20,
        "vwap_dev>+5bp": dev > 0.0005,
        "vwap_dev<-5bp": dev < -0.0005,
        "bb_lower_touch": l <= lo_bb_v,
        "bb_upper_touch": h >= up_bb_v,
        "vol_spike_2x": v > (2.0 * vol_ma),
        "gap_up_5bp": gap_pct > 0.0005,
        "gap_dn_5bp": gap_pct < -0.0005,
        "bull_engulf": (pc < po) & (c > o) & (o <= pc) & (c >= po),
        "bear_engulf": (pc > po) & (c < o) & (o >= pc) & (c <= po),
        "rth_open_60m": open_phase,
        "rth_mid": mid_phase,
        "rth_close_120m": close_phase,
        "rth_any": rth_all,
        "range_expand_1.5x": rng > (1.5 * rng_ma20),
        "range_contract_0.5x": rng < (0.5 * rng_ma20),
    }

    # Fill ATR-regime placeholders using a 14-bar ATR distribution
    atr14 = _atr_series(df5, 14)
    if atr14.size and np.any(np.isfinite(atr14)):
        finite = atr14[np.isfinite(atr14)]
        q1 = np.quantile(finite, 0.25)
        q4 = np.quantile(finite, 0.75)
        feats["atr_low_q1"] = np.where(np.isfinite(atr14), atr14 < q1, False)
        feats["atr_high_q4"] = np.where(np.isfinite(atr14), atr14 > q4, False)

    out: Dict[str, np.ndarray] = {}
    for k, vmask in feats.items():
        arr = np.asarray(vmask)
        if arr.dtype != bool:
            arr = np.where(np.isnan(arr.astype(float)) if arr.dtype != bool else False, False, arr).astype(bool)
        out[k] = arr.astype(bool, copy=False)
    return out


@dataclass
class DeepFinding:
    name: str
    horizon: int
    n_feat: int
    n_rest: int
    mean_feat: float
    mean_rest: float
    diff_pts: float
    win_rate_feat: float
    t_stat: float
    p_value: float
    is_diff: float
    oos_diff: float
    sign_stable: bool
    annual_sharpe_like: float
    sig_fdr: bool = False

    def to_dict(self) -> Dict[str, float]:
        return {
            "name": self.name,
            "horizon": self.horizon,
            "n_feat": self.n_feat,
            "n_rest": self.n_rest,
            "mean_feat": self.mean_feat,
            "mean_rest": self.mean_rest,
            "diff_pts": self.diff_pts,
            "win_rate_feat": self.win_rate_feat,
            "t_stat": self.t_stat,
            "p_value": self.p_value,
            "is_diff": self.is_diff,
            "oos_diff": self.oos_diff,
            "sign_stable": self.sign_stable,
            "annual_sharpe_like": self.annual_sharpe_like,
            "sig_fdr": self.sig_fdr,
        }


def _bh_mask(pvals: np.ndarray, alpha: float = 0.05) -> np.ndarray:
    m = len(pvals)
    if m == 0:
        return np.array([], dtype=bool)
    finite = np.isfinite(pvals)
    pf = np.where(finite, pvals, 1.0)
    order = np.argsort(pf)
    sp = pf[order]
    crit = (np.arange(1, m + 1) / m) * alpha
    below = sp <= crit
    if not below.any():
        cutoff = float("-inf")
    else:
        last = int(np.where(below)[0].max())
        cutoff = float(sp[last])
    return finite & (pf <= cutoff)


def _safe_t(x: np.ndarray, y: np.ndarray) -> Tuple[float, float]:
    if len(x) < 5 or len(y) < 5:
        return float("nan"), float("nan")
    try:
        res = ttest_ind(x, y, equal_var=False, nan_policy="omit")
        return float(res.statistic), float(res.pvalue)
    except Exception:
        return float("nan"), float("nan")


def deep_scan(
    df5: pd.DataFrame,
    horizons: Tuple[int, ...] = (1, 3, 6, 12),
    oos_frac: float = 0.3,
    min_feat: int = 100,
    min_rest: int = 100,
) -> List[DeepFinding]:
    """
    Compute forward-return effect sizes per (feature, horizon).

    `horizons` are numbers of 5m bars: 1,3,6,12 ≈ 5/15/30/60 min lookahead.
    """
    df = df5.sort_index()
    closes = df["close"].to_numpy(float, copy=False)
    n = len(df)
    feats = build_features(df)

    fwd: Dict[int, np.ndarray] = {}
    for h in horizons:
        f = np.full(n, np.nan, dtype=float)
        if h < n:
            f[: n - h] = closes[h:] - closes[: n - h]
        fwd[h] = f

    days = pd.Series(df.index.tz_convert(NY).normalize(), index=df.index).to_numpy()
    unique_days = pd.unique(days)
    if len(unique_days) < 5:
        return []
    cut = max(1, int(len(unique_days) * (1.0 - oos_frac)))
    is_day_set = set(unique_days[:cut].tolist())
    in_is = np.array([d in is_day_set for d in days], dtype=bool)

    findings: List[DeepFinding] = []
    for fname, mask in feats.items():
        for h in horizons:
            ret = fwd[h]
            valid = np.isfinite(ret)
            m = mask & valid
            r = (~mask) & valid
            n_m = int(m.sum())
            n_r = int(r.sum())
            if n_m < min_feat or n_r < min_rest:
                continue
            x = ret[m]
            y = ret[r]
            mf = float(np.mean(x))
            mr = float(np.mean(y))
            t_stat, p_value = _safe_t(x, y)
            wr = float(np.mean(x > 0))
            std_x = float(np.std(x, ddof=1)) if len(x) > 1 else float("nan")
            sharpe = (
                (mf / std_x) * np.sqrt(252.0 * 78.0 / max(h, 1))
                if std_x and std_x > 0
                else float("nan")
            )

            mis = m & in_is
            mos = m & ~in_is
            ris = r & in_is
            ros = r & ~in_is
            is_diff = (
                float(np.mean(ret[mis]) - np.mean(ret[ris]))
                if mis.sum() >= 50 and ris.sum() >= 50
                else float("nan")
            )
            oos_diff = (
                float(np.mean(ret[mos]) - np.mean(ret[ros]))
                if mos.sum() >= 50 and ros.sum() >= 50
                else float("nan")
            )
            stable = (
                np.isfinite(is_diff)
                and np.isfinite(oos_diff)
                and (is_diff != 0.0)
                and (oos_diff != 0.0)
                and (np.sign(is_diff) == np.sign(oos_diff))
            )

            findings.append(
                DeepFinding(
                    name=fname,
                    horizon=int(h),
                    n_feat=n_m,
                    n_rest=n_r,
                    mean_feat=mf,
                    mean_rest=mr,
                    diff_pts=mf - mr,
                    win_rate_feat=wr,
                    t_stat=float(t_stat),
                    p_value=float(p_value),
                    is_diff=is_diff,
                    oos_diff=oos_diff,
                    sign_stable=bool(stable),
                    annual_sharpe_like=float(sharpe),
                )
            )

    if findings:
        ps = np.array([f.p_value for f in findings])
        sig = _bh_mask(ps, alpha=0.05)
        for i, f in enumerate(findings):
            f.sig_fdr = bool(sig[i])
    return findings


def stratify_by_session_phase(
    df5: pd.DataFrame,
    feature_name: str,
    horizon: int,
    oos_frac: float = 0.3,
    min_feat: int = 50,
) -> List[DeepFinding]:
    """
    Evaluate one feature within each session phase, but compute forward
    returns on the **full** DataFrame so adjacent-bar arithmetic stays
    meaningful (no overnight skip artefacts from `df.iloc[mask]`).
    """
    feats = build_features(df5)
    base = feats.get(feature_name)
    if base is None:
        return []
    closes = df5["close"].to_numpy(float, copy=False)
    n = len(df5)
    fwd = np.full(n, np.nan, dtype=float)
    if horizon < n:
        fwd[: n - horizon] = closes[horizon:] - closes[: n - horizon]

    phases: Dict[str, np.ndarray] = {
        "rth_open_60m": feats["rth_open_60m"],
        "rth_mid": feats["rth_mid"],
        "rth_close_120m": feats["rth_close_120m"],
        "eth_overnight": ~feats["rth_any"],
    }

    days = pd.Series(df5.index.tz_convert(NY).normalize(), index=df5.index).to_numpy()
    unique_days = pd.unique(days)
    if len(unique_days) < 5:
        return []
    cut = max(1, int(len(unique_days) * (1.0 - oos_frac)))
    is_day_set = set(unique_days[:cut].tolist())
    in_is = np.array([d in is_day_set for d in days], dtype=bool)

    valid = np.isfinite(fwd)
    out: List[DeepFinding] = []
    for ph_name, ph_mask in phases.items():
        m = base & ph_mask & valid
        r = (~base) & ph_mask & valid
        if m.sum() < min_feat or r.sum() < min_feat:
            continue
        x = fwd[m]
        y = fwd[r]
        mf = float(np.mean(x))
        mr = float(np.mean(y))
        t_stat, p_value = _safe_t(x, y)
        wr = float(np.mean(x > 0))
        std_x = float(np.std(x, ddof=1)) if len(x) > 1 else float("nan")
        sharpe = (
            (mf / std_x) * np.sqrt(252.0 * 78.0 / max(horizon, 1))
            if std_x and std_x > 0
            else float("nan")
        )
        mis = m & in_is
        mos = m & ~in_is
        ris = r & in_is
        ros = r & ~in_is
        is_diff = (
            float(np.mean(fwd[mis]) - np.mean(fwd[ris]))
            if mis.sum() >= 30 and ris.sum() >= 30
            else float("nan")
        )
        oos_diff = (
            float(np.mean(fwd[mos]) - np.mean(fwd[ros]))
            if mos.sum() >= 30 and ros.sum() >= 30
            else float("nan")
        )
        stable = (
            np.isfinite(is_diff)
            and np.isfinite(oos_diff)
            and (is_diff != 0.0)
            and (oos_diff != 0.0)
            and (np.sign(is_diff) == np.sign(oos_diff))
        )
        out.append(
            DeepFinding(
                name=f"{feature_name}@{ph_name}",
                horizon=int(horizon),
                n_feat=int(m.sum()),
                n_rest=int(r.sum()),
                mean_feat=mf,
                mean_rest=mr,
                diff_pts=mf - mr,
                win_rate_feat=wr,
                t_stat=float(t_stat),
                p_value=float(p_value),
                is_diff=is_diff,
                oos_diff=oos_diff,
                sign_stable=bool(stable),
                annual_sharpe_like=float(sharpe),
            )
        )

    if out:
        ps = np.array([f.p_value for f in out])
        sig = _bh_mask(ps, alpha=0.05)
        for i, f in enumerate(out):
            f.sig_fdr = bool(sig[i])
    return out


def _atr_series(df5: pd.DataFrame, period: int = 14) -> np.ndarray:
    h = df5["high"].to_numpy(float)
    l = df5["low"].to_numpy(float)
    c = df5["close"].to_numpy(float)
    pc = _shift1(c)
    tr = np.maximum.reduce([h - l, np.abs(h - pc), np.abs(l - pc)])
    s = pd.Series(tr).rolling(period, min_periods=period).mean().to_numpy()
    return s


@dataclass
class BracketResult:
    feature: str
    direction: str
    stop_atr: float
    tp_atr: float
    max_bars: int
    n: int
    win_rate: float
    mean_r: float
    median_r: float
    profit_factor: float
    timeouts: int

    def to_dict(self) -> Dict[str, float]:
        return {
            "feature": self.feature,
            "direction": self.direction,
            "stop_atr": self.stop_atr,
            "tp_atr": self.tp_atr,
            "max_bars": self.max_bars,
            "n": self.n,
            "win_rate": self.win_rate,
            "mean_r": self.mean_r,
            "median_r": self.median_r,
            "profit_factor": self.profit_factor,
            "timeouts": self.timeouts,
        }


def realized_r_with_brackets(
    df5: pd.DataFrame,
    feature_mask: np.ndarray,
    direction: str,
    stop_atr_mult: float,
    tp_atr_mult: float,
    max_bars: int = 12,
    tick_size: float = 0.25,
    atr_period: int = 14,
) -> Dict[str, float]:
    """
    For each True bar in ``feature_mask``: place a stop-bracket entry one tick
    in ``direction`` past close, with stop = ``stop_atr_mult × ATR`` and
    target = ``tp_atr_mult × ATR``. Walk forward at most ``max_bars`` bars and
    record the **first** of stop / target / timeout.

    Realized R unit = ``stop distance``: a stop hit is ``-1``, a target hit is
    ``tp_atr_mult / stop_atr_mult``, a timeout uses ``(close[j] - entry)/stop``.
    Conservative ordering when both stop and target are touched in the same
    bar: assume stop fires first.
    """
    direction = direction.upper()
    if direction not in ("LONG", "SHORT"):
        raise ValueError("direction must be LONG or SHORT")
    h = df5["high"].to_numpy(float)
    l = df5["low"].to_numpy(float)
    c = df5["close"].to_numpy(float)
    n = len(df5)
    atr = _atr_series(df5, atr_period)

    feat_idx = np.flatnonzero(feature_mask)
    rs: List[float] = []
    timeouts = 0

    for i in feat_idx:
        if i + 1 >= n:
            continue
        a = atr[i]
        if not np.isfinite(a) or a <= 0:
            continue
        entry = c[i] + (tick_size if direction == "LONG" else -tick_size)
        stop_dist = stop_atr_mult * a
        tp_dist = tp_atr_mult * a
        if direction == "LONG":
            stop_px = entry - stop_dist
            tp_px = entry + tp_dist
        else:
            stop_px = entry + stop_dist
            tp_px = entry - tp_dist

        last = min(i + max_bars, n - 1)
        outcome_r: Optional[float] = None
        for j in range(i + 1, last + 1):
            hi = h[j]
            lo = l[j]
            if direction == "LONG":
                if lo <= stop_px:
                    outcome_r = -1.0
                    break
                if hi >= tp_px:
                    outcome_r = tp_atr_mult / stop_atr_mult
                    break
            else:
                if hi >= stop_px:
                    outcome_r = -1.0
                    break
                if lo <= tp_px:
                    outcome_r = tp_atr_mult / stop_atr_mult
                    break
        if outcome_r is None:
            timeouts += 1
            j_close = c[last]
            rt = (j_close - entry) / stop_dist
            if direction == "SHORT":
                rt = -rt
            outcome_r = float(rt)
        rs.append(outcome_r)

    if not rs:
        return {
            "n": 0,
            "win_rate": float("nan"),
            "mean_r": float("nan"),
            "median_r": float("nan"),
            "profit_factor": float("nan"),
            "timeouts": 0,
        }
    arr = np.asarray(rs, dtype=float)
    wins = arr[arr > 0]
    losses = arr[arr < 0]
    pf = (
        float(np.sum(wins) / abs(np.sum(losses)))
        if losses.size and abs(np.sum(losses)) > 0
        else float("inf")
    )
    return {
        "n": int(arr.size),
        "win_rate": float(np.mean(arr > 0)),
        "mean_r": float(np.mean(arr)),
        "median_r": float(np.median(arr)),
        "profit_factor": pf,
        "timeouts": int(timeouts),
    }


def realized_stop_only_fixed_hold(
    df5: pd.DataFrame,
    feature_mask: np.ndarray,
    direction: str,
    stop_atr_mult: float,
    hold_bars: int = 12,
    tick_size: float = 0.25,
    atr_period: int = 14,
) -> Dict[str, float]:
    """
    Stop-only: protective stop at ``stop_atr_mult × ATR``, no TP. Exit at the
    close of bar ``i + hold_bars`` if stop never fires. Captures the full mean
    reversion run-out without TP truncation.

    R unit = stop distance.
    """
    direction = direction.upper()
    if direction not in ("LONG", "SHORT"):
        raise ValueError("direction must be LONG or SHORT")
    h = df5["high"].to_numpy(float)
    l = df5["low"].to_numpy(float)
    c = df5["close"].to_numpy(float)
    n = len(df5)
    atr = _atr_series(df5, atr_period)

    rs: List[float] = []
    timeouts = 0
    for i in np.flatnonzero(feature_mask):
        if i + 1 >= n:
            continue
        a = atr[i]
        if not np.isfinite(a) or a <= 0:
            continue
        entry = c[i] + (tick_size if direction == "LONG" else -tick_size)
        stop_dist = stop_atr_mult * a
        stop_px = entry - stop_dist if direction == "LONG" else entry + stop_dist

        last = min(i + hold_bars, n - 1)
        outcome_r: Optional[float] = None
        for j in range(i + 1, last + 1):
            hi = h[j]
            lo = l[j]
            if direction == "LONG" and lo <= stop_px:
                outcome_r = -1.0
                break
            if direction == "SHORT" and hi >= stop_px:
                outcome_r = -1.0
                break
        if outcome_r is None:
            timeouts += 1
            j_close = c[last]
            rt = (j_close - entry) / stop_dist
            if direction == "SHORT":
                rt = -rt
            outcome_r = float(rt)
        rs.append(outcome_r)

    if not rs:
        return {
            "n": 0,
            "win_rate": float("nan"),
            "mean_r": float("nan"),
            "median_r": float("nan"),
            "profit_factor": float("nan"),
            "timeouts": 0,
        }
    arr = np.asarray(rs, dtype=float)
    wins = arr[arr > 0]
    losses = arr[arr < 0]
    pf = (
        float(np.sum(wins) / abs(np.sum(losses)))
        if losses.size and abs(np.sum(losses)) > 0
        else float("inf")
    )
    return {
        "n": int(arr.size),
        "win_rate": float(np.mean(arr > 0)),
        "mean_r": float(np.mean(arr)),
        "median_r": float(np.median(arr)),
        "profit_factor": pf,
        "timeouts": int(timeouts),
    }


def stop_only_grid(
    df5: pd.DataFrame,
    feature_mask: np.ndarray,
    direction: str,
    stop_atr_mults: Tuple[float, ...] = (0.5, 1.0, 1.5, 2.0),
    hold_bars_options: Tuple[int, ...] = (3, 6, 12, 24),
    tick_size: float = 0.25,
) -> List[BracketResult]:
    """Cartesian sweep of (stop_atr, hold_bars) — no TP, fixed time exit."""
    out: List[BracketResult] = []
    for s in stop_atr_mults:
        for hb in hold_bars_options:
            r = realized_stop_only_fixed_hold(
                df5,
                feature_mask,
                direction,
                stop_atr_mult=s,
                hold_bars=hb,
                tick_size=tick_size,
            )
            out.append(
                BracketResult(
                    feature="",
                    direction=direction,
                    stop_atr=s,
                    tp_atr=float(hb),
                    max_bars=hb,
                    n=int(r["n"]),
                    win_rate=float(r["win_rate"]),
                    mean_r=float(r["mean_r"]),
                    median_r=float(r["median_r"]),
                    profit_factor=float(r["profit_factor"]),
                    timeouts=int(r["timeouts"]),
                )
            )
    return out


def bracket_grid(
    df5: pd.DataFrame,
    feature_mask: np.ndarray,
    direction: str,
    stop_atr_mults: Tuple[float, ...] = (0.5, 1.0, 1.5, 2.0),
    tp_atr_mults: Tuple[float, ...] = (0.5, 1.0, 1.5, 2.0, 3.0),
    max_bars: int = 12,
    tick_size: float = 0.25,
) -> List[BracketResult]:
    """Cartesian sweep of (stop_atr, tp_atr); return results table."""
    out: List[BracketResult] = []
    for s in stop_atr_mults:
        for t in tp_atr_mults:
            r = realized_r_with_brackets(
                df5,
                feature_mask,
                direction,
                stop_atr_mult=s,
                tp_atr_mult=t,
                max_bars=max_bars,
                tick_size=tick_size,
            )
            out.append(
                BracketResult(
                    feature="",
                    direction=direction,
                    stop_atr=s,
                    tp_atr=t,
                    max_bars=max_bars,
                    n=int(r["n"]),
                    win_rate=float(r["win_rate"]),
                    mean_r=float(r["mean_r"]),
                    median_r=float(r["median_r"]),
                    profit_factor=float(r["profit_factor"]),
                    timeouts=int(r["timeouts"]),
                )
            )
    return out


def combo_scan(
    df5: pd.DataFrame,
    base_features: List[str],
    horizon: int,
    *,
    oos_frac: float = 0.3,
    min_feat: int = 100,
) -> List[DeepFinding]:
    """
    Pairwise AND combinations of ``base_features``.

    Reports only when the joint mask still has ≥ ``min_feat`` triggers.
    """
    feats = build_features(df5)
    closes = df5["close"].to_numpy(float)
    n = len(df5)
    fwd = np.full(n, np.nan, dtype=float)
    if horizon < n:
        fwd[: n - horizon] = closes[horizon:] - closes[: n - horizon]

    days = pd.Series(df5.index.tz_convert(NY).normalize(), index=df5.index).to_numpy()
    unique_days = pd.unique(days)
    if len(unique_days) < 5:
        return []
    cut = max(1, int(len(unique_days) * (1.0 - oos_frac)))
    is_day_set = set(unique_days[:cut].tolist())
    in_is = np.array([d in is_day_set for d in days], dtype=bool)
    valid = np.isfinite(fwd)

    out: List[DeepFinding] = []
    seen: set = set()
    for i, fa in enumerate(base_features):
        for fb in base_features[i + 1 :]:
            if fa == fb:
                continue
            key = tuple(sorted((fa, fb)))
            if key in seen:
                continue
            seen.add(key)
            ma = feats.get(fa)
            mb = feats.get(fb)
            if ma is None or mb is None:
                continue
            mask = ma & mb
            m = mask & valid
            r = (~mask) & valid
            if m.sum() < min_feat or r.sum() < min_feat:
                continue
            x = fwd[m]
            y = fwd[r]
            mf = float(np.mean(x))
            mr = float(np.mean(y))
            t_stat, p_value = _safe_t(x, y)
            wr = float(np.mean(x > 0))
            std_x = float(np.std(x, ddof=1)) if len(x) > 1 else float("nan")
            sharpe = (
                (mf / std_x) * np.sqrt(252.0 * 78.0 / max(horizon, 1))
                if std_x and std_x > 0
                else float("nan")
            )
            mis = m & in_is
            mos = m & ~in_is
            ris = r & in_is
            ros = r & ~in_is
            is_diff = (
                float(np.mean(fwd[mis]) - np.mean(fwd[ris]))
                if mis.sum() >= 30 and ris.sum() >= 30
                else float("nan")
            )
            oos_diff = (
                float(np.mean(fwd[mos]) - np.mean(fwd[ros]))
                if mos.sum() >= 30 and ros.sum() >= 30
                else float("nan")
            )
            stable = (
                np.isfinite(is_diff)
                and np.isfinite(oos_diff)
                and is_diff != 0.0
                and oos_diff != 0.0
                and (np.sign(is_diff) == np.sign(oos_diff))
            )
            out.append(
                DeepFinding(
                    name=f"{fa} & {fb}",
                    horizon=int(horizon),
                    n_feat=int(m.sum()),
                    n_rest=int(r.sum()),
                    mean_feat=mf,
                    mean_rest=mr,
                    diff_pts=mf - mr,
                    win_rate_feat=wr,
                    t_stat=float(t_stat),
                    p_value=float(p_value),
                    is_diff=is_diff,
                    oos_diff=oos_diff,
                    sign_stable=bool(stable),
                    annual_sharpe_like=float(sharpe),
                )
            )
    if out:
        ps = np.array([f.p_value for f in out])
        sig = _bh_mask(ps, alpha=0.05)
        for i, f in enumerate(out):
            f.sig_fdr = bool(sig[i])
    return out


def render_deep_report(
    symbol: str,
    csv_rel_path: str,
    n_bars_5m: int,
    findings: List[DeepFinding],
    stratified: List[DeepFinding],
    *,
    horizons: Tuple[int, ...],
    oos_frac: float,
    tradeable_min_pts: float,
    combos: Optional[List[DeepFinding]] = None,
    bracket_tables: Optional[List[Tuple[str, str, List[BracketResult]]]] = None,
    stop_only_tables: Optional[List[Tuple[str, str, List[BracketResult]]]] = None,
    tick_size: float = 0.25,
    point_value: float = 2.0,
) -> str:
    if not findings:
        return f"# Deep pattern scan — {symbol}\n\n_No findings (insufficient data)._\n"

    rows = sorted(findings, key=lambda f: -abs(f.diff_pts))
    horizon_label = ", ".join(f"{h * 5}m" for h in horizons)

    lines: List[str] = [
        f"# Deep pattern scan — {symbol}",
        "",
        f"**CSV:** `{csv_rel_path}` · **5m bars:** {n_bars_5m:,} · **Index TZ:** America/New_York",
        f"**Horizons:** {horizon_label} (in 5m bars: {', '.join(str(h) for h in horizons)}) · **OOS frac:** {oos_frac:.2f}",
        "",
        "**Effect size** is mean forward-return in **points** (`close[t+h] − close[t]`) for "
        "feature vs rest. **stable** ✅ = IS / OOS diffs share sign on a session-date split. "
        "**FDR** uses Benjamini–Hochberg at α=0.05 across all (feature × horizon) cells. "
        "`annSharpe` is heuristic (mean / std × √(252·78/h)) — ignores autocorrelation, fees, slippage.",
        "",
        "## Effect sizes (sorted by |diff_pts|)",
        "",
        "| feature | h | n(feat) | mean(feat) | diff_pts | win% | annSharpe | t | p | FDR | IS_diff | OOS_diff | stable |",
        "|---------|---|---------|------------|----------|------|-----------|---|---|-----|---------|----------|--------|",
    ]
    for r in rows[:80]:
        lines.append(
            f"| `{r.name}` | {r.horizon} | {r.n_feat:,} | {r.mean_feat:+.4f} | "
            f"{r.diff_pts:+.4f} | {r.win_rate_feat * 100:.1f}% | {r.annual_sharpe_like:+.2f} | "
            f"{r.t_stat:+.2f} | {r.p_value:.2e} | {'✅' if r.sig_fdr else '—'} | "
            f"{r.is_diff:+.4f} | {r.oos_diff:+.4f} | {'✅' if r.sign_stable else '—'} |"
        )
    lines.append("")

    tradeable = [
        r
        for r in rows
        if r.sign_stable and r.sig_fdr and abs(r.diff_pts) >= tradeable_min_pts
    ]
    lines.append(
        f"## Tradeable candidates (stable IS/OOS sign + FDR sig + |diff_pts| ≥ {tradeable_min_pts:g})"
    )
    lines.append("")
    if not tradeable:
        lines.append(
            "_No findings cleared all three filters at this threshold. "
            "Either (a) the symbol/window has no robust short-horizon edge, "
            "(b) thresholds are too strict — try a smaller `--min-pts` or longer horizon, or "
            "(c) the edges live in **conditional** combinations (RTH phase × pattern) — see next table._"
        )
    else:
        lines.append("| feature | h | n(feat) | diff_pts | win% | annSharpe | IS_diff | OOS_diff |")
        lines.append("|---------|---|---------|----------|------|-----------|---------|----------|")
        for r in tradeable[:20]:
            lines.append(
                f"| `{r.name}` | {r.horizon} | {r.n_feat:,} | {r.diff_pts:+.4f} | "
                f"{r.win_rate_feat * 100:.1f}% | {r.annual_sharpe_like:+.2f} | "
                f"{r.is_diff:+.4f} | {r.oos_diff:+.4f} |"
            )
    lines.append("")

    if stratified:
        lines.append("## Top features stratified by session phase")
        lines.append("")
        lines.append(
            "| feature@phase | h | n(feat) | diff_pts | win% | t | p | FDR† | IS_diff | OOS_diff | stable |"
        )
        lines.append(
            "|---------------|---|---------|----------|------|---|---|------|---------|----------|--------|"
        )
        sps = sorted(stratified, key=lambda f: -abs(f.diff_pts))
        for r in sps[:30]:
            lines.append(
                f"| `{r.name}` | {r.horizon} | {r.n_feat:,} | {r.diff_pts:+.4f} | "
                f"{r.win_rate_feat * 100:.1f}% | {r.t_stat:+.2f} | {r.p_value:.2e} | "
                f"{'✅' if r.sig_fdr else '—'} | {r.is_diff:+.4f} | {r.oos_diff:+.4f} | "
                f"{'✅' if r.sign_stable else '—'} |"
            )
        lines.append("")
        lines.append(
            "_† FDR is computed only within each scan call; phase-stratified rows use FDR over their phase scan._"
        )
        lines.append("")

    if combos:
        lines.append("## Pairwise feature combinations (AND-conjunctions)")
        lines.append("")
        lines.append(
            "These restrict to bars where **both** features are true; sign-stability and "
            "FDR are computed over the combo set."
        )
        lines.append("")
        lines.append(
            "| combo | h | n(feat) | diff_pts | win% | t | p | FDR | IS_diff | OOS_diff | stable |"
        )
        lines.append(
            "|-------|---|---------|----------|------|---|---|-----|---------|----------|--------|"
        )
        cs = sorted(combos, key=lambda f: -abs(f.diff_pts))
        for r in cs[:25]:
            lines.append(
                f"| `{r.name}` | {r.horizon} | {r.n_feat:,} | {r.diff_pts:+.4f} | "
                f"{r.win_rate_feat * 100:.1f}% | {r.t_stat:+.2f} | {r.p_value:.2e} | "
                f"{'✅' if r.sig_fdr else '—'} | {r.is_diff:+.4f} | {r.oos_diff:+.4f} | "
                f"{'✅' if r.sign_stable else '—'} |"
            )
        lines.append("")

    if bracket_tables:
        lines.append("## Realized R under stop / TP brackets (triple-barrier)")
        lines.append("")
        lines.append(
            "For each top feature, simulate a stop-bracket entry one tick in the trade "
            "direction past close; walk forward up to `max_bars` 5m bars; record first of "
            "stop / target / timeout. **R unit = stop distance** (a stop = −1 R; target hit = "
            "tp_atr/stop_atr). Conservative ordering: when a single bar tags both, assume "
            "stop fires first. `mean_r × $/contract` per signal helps gauge tradeability."
        )
        lines.append("")
        for feat_name, direction, brackets in bracket_tables:
            if not brackets:
                continue
            br = sorted(brackets, key=lambda r: -r.mean_r)
            lines.append(f"### `{feat_name}` ({direction})")
            lines.append("")
            lines.append(
                "| stop_ATR | tp_ATR | n | win% | mean_R | median_R | PF | timeouts |"
            )
            lines.append(
                "|----------|--------|---|------|--------|----------|----|----------|"
            )
            for r in br:
                pf_str = f"{r.profit_factor:.2f}" if np.isfinite(r.profit_factor) else "∞"
                lines.append(
                    f"| {r.stop_atr:.1f} | {r.tp_atr:.1f} | {r.n:,} | "
                    f"{r.win_rate * 100:.1f}% | {r.mean_r:+.3f} | {r.median_r:+.3f} | "
                    f"{pf_str} | {r.timeouts:,} |"
                )
            lines.append("")
        lines.append(
            "_Tradeable bracket: PF > 1.2 + mean_R > 0 + n ≥ 200. Below those thresholds the "
            "edge does not survive realistic execution._"
        )
        lines.append("")

    if stop_only_tables:
        lines.append("## Realized R: stop-only with fixed-time exit (no TP)")
        lines.append("")
        lines.append(
            "Same entry as above but only a protective stop — exit at the close of bar "
            "`entry + hold_bars` if the stop never fires. This avoids TP-truncation of the "
            "mean-reversion run-out and is the right execution model when the alpha lives "
            "in the post-event drift, not in a fixed TP. `tp_ATR` column repurposed as `hold_bars`."
        )
        lines.append("")
        for feat_name, direction, brackets in stop_only_tables:
            if not brackets:
                continue
            br = sorted(brackets, key=lambda r: -r.mean_r)
            lines.append(f"### `{feat_name}` ({direction}) — stop-only / fixed hold")
            lines.append("")
            lines.append(
                "| stop_ATR | hold_bars | n | win% | mean_R | median_R | PF | timeouts |"
            )
            lines.append(
                "|----------|-----------|---|------|--------|----------|----|----------|"
            )
            for r in br:
                pf_str = f"{r.profit_factor:.2f}" if np.isfinite(r.profit_factor) else "∞"
                lines.append(
                    f"| {r.stop_atr:.1f} | {int(r.tp_atr)} | {r.n:,} | "
                    f"{r.win_rate * 100:.1f}% | {r.mean_r:+.3f} | {r.median_r:+.3f} | "
                    f"{pf_str} | {r.timeouts:,} |"
                )
            lines.append("")

    lines += [
        "## Caveats",
        "",
        "- **Costs**: round-trip commission + 1 tick of slippage already exceeds **|diff_pts| ≈ 0.05** on MNQ "
        "(at $2.50 commission and $0.50 tick, ~0.5 pts cost equivalent ÷ point value). Treat **|diff_pts| ≥ 0.5** "
        "as the minimum gating bar before live consideration; **anything below ≈0.2** is statistical noise from "
        "a trading perspective.",
        "- **Autocorrelation** between adjacent bars inflates t-stats at horizons > 1; rely on **diff_pts** + "
        "**OOS sign stability** as the primary gates, not p-value alone.",
        "- **Forward returns include all hours** unless feature is `rth_*`. For RTH-only behaviour use the "
        "phase-stratified table above.",
        "- After identifying a candidate: scaffold a strategy in `strategies/` + `config/strategies/<name>.toml`, "
        "register it in `strategies/strategy_manager.py`, then run `python -m core.research.runner --help` for the "
        "full **OOS + Monte Carlo** validation pipeline (see `docs/BACKTEST_RESEARCH.md`).",
        "",
    ]
    return "\n".join(lines)
