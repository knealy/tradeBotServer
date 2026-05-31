"""7:00–7:55 US/Eastern 5m range sweep → re-entry fade (research / optional live).

Third-party write-up: ``docs/alpha/morning_reversion_info.md``. This module
implements the same *mechanical* rules so we can replay them independently;
**do not** trust external win-rate claims until you run the sieve / replay.

**Rules (CSV timestamps = bar open, naive UTC — same as ``resample_ohlcv_csv``):**

0. Per **US/Eastern calendar date**, ignore bars before **07:00** ET until the morning
   window is processed (futures CSVs usually have overnight bars first). This is **not**
   NYSE cash open: **RTH is 9:30 ET**; the 7–8 ET window is **before** that (pre-open
   / overnight futures context in the third-party write-up).

1. Range = high/low of all 5m bars whose **open** in US/Eastern is in ``[07:00, 08:00)``.
2. After the range window, first **close** above range high ⇒ **high sweep**; below range low ⇒ **low sweep**.
   Bracket **fills** may occur on later bars once the stop triggers (often **after 08:00 ET**); the 7–8 window defines the anchor box, not the only wall-clock time when trades may complete.
   **Fade validity:** new sweeps / re-entry arms are ignored after ``range_end_open`` on the anchor ET date plus ``range_effectiveness_hours`` (default **4** → cutoff **12:00 ET** when the box ends at 08:00).
3. **Advance stop (``signal.reentry_threshold_points > 0``)** — on the **first close outside** the box,
   immediately place stop-entry + brackets ``threshold`` pts back inside (overnight_range style):
   low sweep → **LONG** stop at **L + threshold**; high sweep → **SHORT** stop at **H − threshold**.
   **Immediate at extreme (``require_reentry_close = false`` and threshold = 0)** — entry at range **high** / **low**.
   **Legacy candle-close (``require_reentry_close = true`` and threshold = 0)** — wait for **close** back
   inside ``[L + reentry_frac·W, H − reentry_frac·W]``.
4. Multiple sequences per session are allowed after the simulated trade is flat again (new sweep required).

Intrabar: pass ``one_minute_df`` into ``sieve_simulate_from_ohlcv`` to resolve
TP vs SL using **1m** paths inside each 5m bar; otherwise if both touch on 5m,
**stop before target** by default (see ``_resolve_both_hit_outcome``).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from datetime import time as dt_time
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from core.risk_management import _order_counts_as_working_entry_for_risk
from core.strategy_config import load_strategy_config
from strategies.strategy_base import (
    BaseStrategy,
    MarketCondition,
    StrategyConfig,
    StrategyStatus,
)

logger = logging.getLogger(__name__)


def _parse_hhmm(value: str, fallback: time) -> time:
    try:
        hh, mm = value.split(":")
        return time(int(hh), int(mm))
    except Exception:
        return fallback


def _load_tz(name: str):
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(name)
    except Exception:
        try:
            import pytz

            return pytz.timezone(name)
        except Exception:
            return timezone.utc


@dataclass
class SieveTrade:
    session_date: date
    side: str  # "SHORT" | "LONG"
    sweep: str  # "high" | "low"
    range_high: float
    range_low: float
    mid: float
    width: float
    entry: float
    stop: float
    target: float
    entry_ts: pd.Timestamp
    outcome: str  # "win" | "loss" | "open"
    exit_ts: Optional[pd.Timestamp]


def _bar_open_utc(ts) -> pd.Timestamp:
    t = pd.Timestamp(ts)
    if t.tzinfo is None:
        t = t.tz_localize("UTC")
    return t


def _open_et(ts, tz) -> datetime:
    t = _bar_open_utc(ts)
    return t.tz_convert(tz).to_pydatetime()


def _in_morning_range_window(open_et: datetime, start_t: time, end_open_t: time) -> bool:
    t = open_et.time()
    return start_t <= t < end_open_t


def _et_datetime_combine(tz, d: date, t: time) -> datetime:
    """Wall-clock *d* + *t* in *tz* (pytz or ZoneInfo)."""
    naive = datetime.combine(d, t)
    loc = getattr(tz, "localize", None)
    if callable(loc):
        return loc(naive)
    return naive.replace(tzinfo=tz)


def _sieve_tr_atr_arrays(
    high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int
) -> np.ndarray:
    """Rolling mean TR (aligned with ``body_reversion`` warm-up style)."""
    n = len(high)
    tr = np.zeros(n, dtype=float)
    tr[0] = float(high[0]) - float(low[0])
    for i in range(1, n):
        tr[i] = max(
            float(high[i]) - float(low[i]),
            abs(float(high[i]) - float(close[i - 1])),
            abs(float(low[i]) - float(close[i - 1])),
        )
    return pd.Series(tr).rolling(int(period), min_periods=int(period)).mean().to_numpy()


def _sieve_atr_high_regime_ok(
    atr: np.ndarray,
    row_idx: int,
    lookback: int,
    quantile: float,
    n_rows: int,
) -> bool:
    """True when current ATR exceeds the ``quantile`` of the trailing window (BONGO §4.3)."""
    if row_idx < 0 or row_idx >= len(atr):
        return False
    cur = float(atr[row_idx])
    if cur != cur:
        return False
    start = max(0, row_idx - int(lookback) + 1)
    window = atr[start : row_idx + 1]
    finite = [float(x) for x in window if x == x]
    min_need = max(8, min(50, max(15, n_rows // 3)))
    if len(finite) < min_need:
        return False
    sf = sorted(finite)
    q = max(0.0, min(1.0, float(quantile)))
    cut_idx = int(len(sf) * q)
    if cut_idx >= len(sf):
        cut_idx = len(sf) - 1
    thr = sf[cut_idx]
    return cur > thr


def _ensure_ohlcv_index_naive_utc(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or len(df) == 0:
        return df
    idx = df.index
    if getattr(idx, "tz", None) is None:
        return df.sort_index()
    out = df.copy()
    out.index = idx.tz_convert("UTC").tz_localize(None)
    return out.sort_index()


def _vertices_1m_bar(o: float, h: float, l: float, c: float) -> List[float]:
    """Open → extremes → close (bullish: O-H-L-C, bearish: O-L-H-C)."""
    if c >= o:
        seq = (o, h, l, c)
    else:
        seq = (o, l, h, c)
    out: List[float] = []
    for x in seq:
        if not out or abs(float(x) - out[-1]) > 1e-9:
            out.append(float(x))
    return out


def _segment_hit_times_short(p0: float, p1: float, stop_px: float, tgt_px: float) -> tuple:
    """Return (t_stop, t_tp) in (0, 1] along segment p0→p1, or (None, None)."""
    if abs(p1 - p0) < 1e-12:
        return None, None
    t_sl = t_tp = None
    if p1 > p0 and p0 < stop_px <= p1:
        t_sl = (stop_px - p0) / (p1 - p0)
    if p0 > p1 and p1 <= tgt_px <= p0:
        t_tp = (tgt_px - p0) / (p1 - p0)
    return t_sl, t_tp


def _segment_hit_times_long(p0: float, p1: float, stop_px: float, tgt_px: float) -> tuple:
    """LONG: stop below entry, target above."""
    if abs(p1 - p0) < 1e-12:
        return None, None
    t_sl = t_tp = None
    if p0 > p1 and p1 <= stop_px <= p0:
        t_sl = (stop_px - p0) / (p1 - p0)
    if p1 > p0 and p0 < tgt_px <= p1:
        t_tp = (tgt_px - p0) / (p1 - p0)
    return t_sl, t_tp


def resolve_ambiguous_5m_bar_with_1m(
    side: str,
    stop_px: float,
    tgt_px: float,
    five_o: float,
    five_h: float,
    five_l: float,
    five_c: float,
    five_open_ts: pd.Timestamp,
    df_1m: Optional[pd.DataFrame],
    tie_stop_first: bool,
) -> Optional[str]:
    """When a 5m bar's high/low hits **both** SL and TP, walk **1m** OHLC paths.

    Each 1m bar uses bullish O→H→L→C or bearish O→L→H→C; consecutive minutes
    chain through **close → next open** (gap segment). **SHORT**: stop on
    **up** moves (``high`` path), TP on **down** moves toward ``tgt_px``.
    **LONG**: mirrored.

    Returns ``\"win\"``, ``\"loss\"``, or ``None`` if no 1m rows in
    ``[five_open_ts, five_open_ts + 5min)`` (caller falls back to tie flag).
    """
    if df_1m is None or len(df_1m) == 0:
        return None
    t0 = pd.Timestamp(five_open_ts)
    if t0.tzinfo is not None:
        t0 = t0.tz_convert("UTC").tz_localize(None)
    t1 = t0 + pd.Timedelta(minutes=5)
    m = df_1m.loc[(df_1m.index >= t0) & (df_1m.index < t1)]
    if len(m) == 0:
        return None

    hit_fn = _segment_hit_times_short if side == "SHORT" else _segment_hit_times_long
    best_key: Optional[tuple] = None  # (segment_ix, t_local)
    best_out: Optional[str] = None

    def add(seg_ix: int, t_loc: Optional[float], outcome: str) -> None:
        nonlocal best_key, best_out
        if t_loc is None or t_loc <= 1e-15 or t_loc > 1.0 + 1e-12:
            return
        k = (seg_ix, float(t_loc))
        if best_key is None or k < best_key:
            best_key, best_out = k, outcome
        elif best_key is not None and k == best_key:
            if tie_stop_first and outcome == "loss":
                best_out = "loss"
            elif not tie_stop_first and outcome == "win":
                best_out = "win"

    seg_ix = 0
    prev_close: Optional[float] = None
    for _, row in m.iterrows():
        o1 = float(row["open"])
        h = float(row["high"])
        l = float(row["low"])
        c = float(row["close"])
        if prev_close is not None and abs(prev_close - o1) > 1e-9:
            t_sl, t_tp = hit_fn(prev_close, o1, stop_px, tgt_px)
            add(seg_ix, t_sl, "loss")
            add(seg_ix, t_tp, "win")
            seg_ix += 1
        verts = _vertices_1m_bar(o1, h, l, c)
        for a, b in zip(verts[:-1], verts[1:]):
            t_sl, t_tp = hit_fn(a, b, stop_px, tgt_px)
            add(seg_ix, t_sl, "loss")
            add(seg_ix, t_tp, "win")
            seg_ix += 1
        prev_close = c

    if best_out is None:
        return None
    return best_out


def _resolve_both_hit_outcome(
    side: str,
    stop_px: float,
    tgt_px: float,
    o: float,
    hi: float,
    lo: float,
    c: float,
    ts,
    intrabar_1m: Optional[pd.DataFrame],
    tie_stop_first: bool,
) -> str:
    ro = resolve_ambiguous_5m_bar_with_1m(
        side, stop_px, tgt_px, o, hi, lo, c, pd.Timestamp(ts), intrabar_1m, tie_stop_first
    )
    if ro is not None:
        return ro
    return "loss" if tie_stop_first else "win"


def sieve_simulate_from_ohlcv(
    df: pd.DataFrame,
    *,
    session_tz: str = "America/New_York",
    range_start: time = time(7, 0),
    range_end_open: time = time(8, 0),
    stop_before_target_same_bar: bool = True,
    one_minute_df: Optional[pd.DataFrame] = None,
    require_reentry_close: bool = False,
    max_fades_per_session: int = 0,
    sl_mult: float = 1.0,
    tp_mult: float = 1.0,
    reentry_frac: float = 0.0,
    reentry_threshold_points: float = 0.0,
    require_high_atr: bool = False,
    atr_period: int = 14,
    atr_regime_lookback: int = 500,
    atr_regime_quantile: float = 0.75,
    range_effectiveness_hours: float = 4.0,
    sl_fixed_pts: float = 0.0,
    min_range_width_points: float = 0.0,
    max_range_width_points: float = 0.0,
) -> List[SieveTrade]:
    """Offline trade list + win/loss labels (no execution model).

    Expects a DataFrame indexed by **naive UTC** timestamps (bar open) with
    columns open, high, low, close.

    If ``one_minute_df`` is provided (same index convention, 1m bars), any 5m
    bar where **both** stop and target are touched uses
    :func:`resolve_ambiguous_5m_bar_with_1m` instead of assuming stop-first or
    TP-first for the whole bar.

    ``require_reentry_close`` (default **False**, matching live ``analyze``):
    when **False**, arm the fade as soon as a bar **closes** outside the range
    (high sweep → SHORT at ``H``). When **True**, wait for a later close back
    **inside** the inner band ``[L + reentry_frac·W, H − reentry_frac·W]`` (same
    as live ``analyze``).

    ``max_fades_per_session`` (default **0** = unlimited): cap how many times the
    sieve may arm a new fade per **session calendar date** (after each exit,
    in/out resets can otherwise produce many trades off the same anchor).

    ``sl_mult`` / ``tp_mult`` scale stop / target vs half-range (defaults match
    prior sieve: stop at ±0.5×width, target at midpoint when ``tp_mult=1``).

    ``require_high_atr`` (BONGO §4.3): when **True**, only arm when the current
    bar's rolling ATR exceeds the rolling ``atr_regime_quantile`` of the prior
    ``atr_regime_lookback`` ATR samples (same spirit as ``body_reversion``).

    ``range_effectiveness_hours`` (default **4.0**, use **0** to disable): after
    ``range_end_open`` on the anchor session date in ``session_tz``, no new
    fade arms (``scan`` / ``wait_re``) once the bar open is at or past that
    instant plus this many hours (matches live ``analyze``).
    """
    tz = _load_tz(session_tz)
    if df is None or len(df) == 0:
        return []

    work = df.sort_index()
    required = {"open", "high", "low", "close"}
    miss = required - set(work.columns)
    if miss:
        raise ValueError(f"sieve: missing columns {miss}")

    intrabar_1m: Optional[pd.DataFrame] = None
    if one_minute_df is not None and len(one_minute_df) > 0:
        intrabar_1m = _ensure_ohlcv_index_naive_utc(one_minute_df)

    sl_m = max(0.1, float(sl_mult))
    tp_m = max(0.1, float(tp_mult))
    r_frac = max(0.0, min(0.49, float(reentry_frac)))
    threshold_pts = max(0.0, float(reentry_threshold_points or 0.0))
    sl_fp = max(0.0, float(sl_fixed_pts or 0.0))
    min_w = max(0.0, float(min_range_width_points or 0.0))
    max_w = max(0.0, float(max_range_width_points or 0.0))
    atr_arr: Optional[np.ndarray] = None
    if require_high_atr:
        h_a = work["high"].to_numpy(dtype=float, copy=False)
        l_a = work["low"].to_numpy(dtype=float, copy=False)
        c_a = work["close"].to_numpy(dtype=float, copy=False)
        atr_arr = _sieve_tr_atr_arrays(h_a, l_a, c_a, int(atr_period))

    trades: List[SieveTrade] = []
    session_d: Optional[date] = None
    range_hi: Optional[float] = None
    range_lo: Optional[float] = None
    range_ready = False
    phase = "build"  # build | scan | wait_re
    sweep_side: Optional[str] = None
    in_trade = False
    # After an immediate (non-reentry) arm, wait for a close inside [L,H] before arming again.
    block_next_immediate = False
    fades_this_session = 0

    H = L = mid = width = 0.0
    pending: Optional[Dict[str, Any]] = None

    def arm_from_sweep(
        sv: str,
        ts: pd.Timestamp,
        o: float,
        hi: float,
        lo: float,
        c: float,
        d: date,
        bar_idx: int,
        *,
        entry_depth_pts: float = 0.0,
    ) -> None:
        """Arm synthetic entry at the range edge (default) or ``entry_depth_pts`` inside the
        range (used by the points-threshold re-entry mode so the fill simulates the broker
        stop-entry waiting for price to traverse back to the trigger level)."""
        nonlocal pending, in_trade, phase, sweep_side, trades, fades_this_session
        if max_fades_per_session and fades_this_session >= max_fades_per_session:
            return
        if require_high_atr and atr_arr is not None:
            if not _sieve_atr_high_regime_ok(
                atr_arr,
                bar_idx,
                int(atr_regime_lookback),
                float(atr_regime_quantile),
                len(work),
            ):
                return
        # Range-width guards.
        if min_w > 0 and width < min_w:
            return
        if max_w > 0 and width > max_w:
            return
        fades_this_session += 1
        half = width / 2.0
        depth = max(0.0, float(entry_depth_pts or 0.0))
        max_depth = max(0.0, half - 1e-6)
        if depth > max_depth:
            depth = max_depth
        if sv == "high":
            entry = H - depth
            if sl_fp > 0:
                stop_px = entry + sl_fp
            else:
                stop_px = H + half * sl_m
            tgt_px = H - half * tp_m
            side = "SHORT"
        else:
            entry = L + depth
            if sl_fp > 0:
                stop_px = entry - sl_fp
            else:
                stop_px = L - half * sl_m
            tgt_px = L + half * tp_m
            side = "LONG"

        pending = {
            "side": side,
            "sweep": sv,
            "entry": entry,
            "stop": stop_px,
            "target": tgt_px,
            "entry_ts": pd.Timestamp(ts),
        }
        in_trade = True
        sweep_side = None
        phase = "scan"

        hi_b = hi
        lo_b = lo
        hit_sl = hit_tp = False
        if side == "SHORT":
            hit_sl = hi_b >= stop_px
            hit_tp = lo_b <= tgt_px
        else:
            hit_sl = lo_b <= stop_px
            hit_tp = hi_b >= tgt_px
        if hit_sl and hit_tp:
            outcome = _resolve_both_hit_outcome(
                side, stop_px, tgt_px, o, hi_b, lo_b, c, ts, intrabar_1m, stop_before_target_same_bar
            )
            trades.append(
                SieveTrade(
                    session_date=d,
                    side=side,
                    sweep=pending["sweep"],
                    range_high=H,
                    range_low=L,
                    mid=mid,
                    width=width,
                    entry=entry,
                    stop=stop_px,
                    target=tgt_px,
                    entry_ts=pending["entry_ts"],
                    outcome=outcome,
                    exit_ts=pd.Timestamp(ts),
                )
            )
            pending = None
            in_trade = False
        elif hit_sl:
            trades.append(
                SieveTrade(
                    session_date=d,
                    side=side,
                    sweep=pending["sweep"],
                    range_high=H,
                    range_low=L,
                    mid=mid,
                    width=width,
                    entry=entry,
                    stop=stop_px,
                    target=tgt_px,
                    entry_ts=pending["entry_ts"],
                    outcome="loss",
                    exit_ts=pd.Timestamp(ts),
                )
            )
            pending = None
            in_trade = False
        elif hit_tp:
            trades.append(
                SieveTrade(
                    session_date=d,
                    side=side,
                    sweep=pending["sweep"],
                    range_high=H,
                    range_low=L,
                    mid=mid,
                    width=width,
                    entry=entry,
                    stop=stop_px,
                    target=tgt_px,
                    entry_ts=pending["entry_ts"],
                    outcome="win",
                    exit_ts=pd.Timestamp(ts),
                )
            )
            pending = None
            in_trade = False

    def reset_session(d: date) -> None:
        nonlocal range_hi, range_lo, range_ready, phase, sweep_side, in_trade
        nonlocal H, L, mid, width, pending, block_next_immediate, fades_this_session
        range_hi = range_lo = None
        range_ready = False
        phase = "build"
        sweep_side = None
        in_trade = False
        block_next_immediate = False
        fades_this_session = 0
        H = L = mid = width = 0.0
        pending = None

    for bar_idx, (ts, row) in enumerate(work.iterrows()):
        o = float(row["open"])
        hi = float(row["high"])
        lo = float(row["low"])
        c = float(row["close"])
        open_et = _open_et(ts, tz)
        d = open_et.date()

        if session_d != d:
            session_d = d
            reset_session(d)

        # --- build range ---
        if not range_ready:
            if _in_morning_range_window(open_et, range_start, range_end_open):
                if range_hi is None:
                    range_hi = hi
                    range_lo = lo
                else:
                    range_hi = max(range_hi, hi)
                    range_lo = min(range_lo, lo)
                continue

            # Bars before 07:00 ET on this session date: keep building state; do **not**
            # finalize to idle (that would skip the morning window when CSV has overnight
            # bars first — the bug that produced 0 trades on full MNQ history).
            if open_et.time() < range_start:
                continue

            # First bar at/after range_end_open: finalize
            if range_hi is None or range_lo is None or range_hi <= range_lo:
                range_ready = True
                phase = "idle"
                continue

            H, L = float(range_hi), float(range_lo)
            width = H - L
            mid = (H + L) / 2.0
            range_ready = True
            phase = "scan"
            sweep_side = None

        if phase == "idle":
            continue

        # --- simulate pending trade from previous bars ---
        if pending is not None and in_trade:
            side = pending["side"]
            stop_px = pending["stop"]
            tgt_px = pending["target"]
            entry_ts = pending["entry_ts"]

            hit_sl = hit_tp = False
            if side == "SHORT":
                hit_sl = hi >= stop_px
                hit_tp = lo <= tgt_px
            else:
                hit_sl = lo <= stop_px
                hit_tp = hi >= tgt_px

            if hit_sl and hit_tp:
                outcome = _resolve_both_hit_outcome(
                    side, stop_px, tgt_px, o, hi, lo, c, ts, intrabar_1m, stop_before_target_same_bar
                )
                trades.append(
                    SieveTrade(
                        session_date=d,
                        side=side,
                        sweep=pending["sweep"],
                        range_high=H,
                        range_low=L,
                        mid=mid,
                        width=width,
                        entry=pending["entry"],
                        stop=stop_px,
                        target=tgt_px,
                        entry_ts=entry_ts,
                        outcome=outcome,
                        exit_ts=pd.Timestamp(ts),
                    )
                )
                pending = None
                in_trade = False
                phase = "scan"
                sweep_side = None
            elif hit_sl:
                trades.append(
                    SieveTrade(
                        session_date=d,
                        side=side,
                        sweep=pending["sweep"],
                        range_high=H,
                        range_low=L,
                        mid=mid,
                        width=width,
                        entry=pending["entry"],
                        stop=stop_px,
                        target=tgt_px,
                        entry_ts=entry_ts,
                        outcome="loss",
                        exit_ts=pd.Timestamp(ts),
                    )
                )
                pending = None
                in_trade = False
                phase = "scan"
                sweep_side = None
            elif hit_tp:
                trades.append(
                    SieveTrade(
                        session_date=d,
                        side=side,
                        sweep=pending["sweep"],
                        range_high=H,
                        range_low=L,
                        mid=mid,
                        width=width,
                        entry=pending["entry"],
                        stop=stop_px,
                        target=tgt_px,
                        entry_ts=entry_ts,
                        outcome="win",
                        exit_ts=pd.Timestamp(ts),
                    )
                )
                pending = None
                in_trade = False
                phase = "scan"
                sweep_side = None
            else:
                continue

        if not range_ready or phase == "idle":
            continue

        if (
            range_effectiveness_hours > 0
            and not in_trade
            and open_et
            >= _et_datetime_combine(tz, d, range_end_open)
            + timedelta(hours=float(range_effectiveness_hours))
        ):
            phase = "idle"
            sweep_side = None
            continue

        # Same as live analyze(): allow another immediate arm only after a close inside the range.
        if L <= c <= H:
            block_next_immediate = False

        # --- sweep / optional re-entry / immediate arm ---
        if phase == "scan" and not in_trade:
            if sweep_side is None:
                if c > H:
                    if threshold_pts > 0.0:
                        if not block_next_immediate:
                            arm_from_sweep(
                                "high", ts, o, hi, lo, c, d, bar_idx,
                                entry_depth_pts=threshold_pts,
                            )
                            block_next_immediate = True
                    elif require_reentry_close:
                        sweep_side = "high"
                        phase = "wait_re"
                    else:
                        if block_next_immediate:
                            sweep_side = None
                        else:
                            arm_from_sweep("high", ts, o, hi, lo, c, d, bar_idx)
                            block_next_immediate = True
                elif c < L:
                    if threshold_pts > 0.0:
                        if not block_next_immediate:
                            arm_from_sweep(
                                "low", ts, o, hi, lo, c, d, bar_idx,
                                entry_depth_pts=threshold_pts,
                            )
                            block_next_immediate = True
                    elif require_reentry_close:
                        sweep_side = "low"
                        phase = "wait_re"
                    else:
                        if block_next_immediate:
                            sweep_side = None
                        else:
                            arm_from_sweep("low", ts, o, hi, lo, c, d, bar_idx)
                            block_next_immediate = True

        if phase == "wait_re" and not in_trade and sweep_side is not None:
            if threshold_pts > 0.0:
                # Points-threshold re-entry: arm the moment price traverses ``threshold_pts``
                # into the range (intra-bar). Entry rides the trigger level itself so the
                # simulated stop-entry direction is valid.
                if sweep_side == "high":
                    trigger_px = H - threshold_pts
                    crossed = lo <= trigger_px
                else:
                    trigger_px = L + threshold_pts
                    crossed = hi >= trigger_px
                if crossed:
                    arm_from_sweep(
                        sweep_side, ts, o, hi, lo, c, d, bar_idx,
                        entry_depth_pts=threshold_pts,
                    )
                continue
            inner_lo = L + r_frac * width
            inner_hi = H - r_frac * width
            if inner_lo <= c <= inner_hi:
                arm_from_sweep(sweep_side, ts, o, hi, lo, c, d, bar_idx)

    return trades


class MorningRangeReversionStrategy(BaseStrategy):
    """5m morning-range breakout fade: stop-entry + OCO brackets at the range extreme.

    Default: on first **close** outside the anchor range, submit a **stop entry** with
    bracket legs (``place_oco_bracket_with_stop_entry``, same pathway as overnight_range).
    Set ``signal.require_reentry_close = true`` to restore the legacy “sweep then wait
    for close back inside (inner band via ``reentry_frac``)” flow.

    ``signal.range_effectiveness_hours`` (default **4**): after ``range_end_open`` on the
    anchor ET session date, no new fade signals (including re-entry) once wall-clock is
    past that instant plus the configured hours.
    """

    NAME = "morning_range_reversion"

    def __init__(self, trading_bot, config: Optional[StrategyConfig] = None):
        if config is None:
            config = StrategyConfig.from_env(self.NAME)
        super().__init__(trading_bot, config)

        self._cfg = load_strategy_config(self.NAME, env_prefix=f"{self.NAME.upper()}_")
        self.timeframe: str = self._cfg.get_str("signal.timeframe", "5m") or "5m"
        self.lookback_bars: int = int(self._cfg.get_int("signal.lookback_bars", 400))
        self.session_zone: str = self._cfg.get_str("signal.session_timezone", "America/New_York")
        self._tz = _load_tz(self.session_zone)
        self.range_start: time = _parse_hhmm(
            self._cfg.get_str("signal.range_start", "07:00"), time(7, 0)
        )
        self.range_end_open: time = _parse_hhmm(
            self._cfg.get_str("signal.range_end_open", "08:00"), time(8, 0)
        )
        self.flat_before: time = _parse_hhmm(
            self._cfg.get_str("signal.flat_before", "16:00"), time(16, 0)
        )
        self.max_hold_bars: int = int(self._cfg.get_int("signal.max_hold_bars", 96))
        self.allow_long: bool = bool(self._cfg.get_bool("signal.allow_long", True))
        self.allow_short: bool = bool(self._cfg.get_bool("signal.allow_short", True))
        # Execution geometry knobs (can be overridden by replay runner via setattr)
        self.sl_mult: float = float(self._cfg.get_float("signal.sl_mult", 1.0) or 1.0)
        self.tp_mult: float = float(self._cfg.get_float("signal.tp_mult", 1.0) or 1.0)
        # Fixed-pts SL override: when > 0, stop is placed sl_fixed_pts away from entry
        # rather than at L/H ± half * sl_mult.  Improves R-ratio on typical-range days.
        self.sl_fixed_pts: float = float(self._cfg.get_float("signal.sl_fixed_pts", 0.0) or 0.0)
        # Range-width guard: skip fade when morning range is outside [min, max] (0 = off).
        self.min_range_width_points: float = float(
            self._cfg.get_float("signal.min_range_width_points", 0.0) or 0.0
        )
        self.max_range_width_points: float = float(
            self._cfg.get_float("signal.max_range_width_points", 0.0) or 0.0
        )
        # 2026-05-29 fix — far-sweep guard.
        # The sweep detector ``c > H`` / ``c < L`` is direction-only; it never asked
        # *how far* past the boundary the price drifted. On 2026-05-29 13:47 ET
        # (5h45m after the 08:00 ET anchor close, with ``range_effectiveness_hours=8``)
        # MGC was trading ~$43 above its 15.10pt anchor high. The strategy still
        # signalled a SHORT stop-entry at H=4571.90; the broker rejected it with
        # ``Invalid price. Price is outside allowed range.`` (Code 2) because the
        # stop trigger was far below current market — a "catch a knife from above"
        # entry that had essentially zero fill probability anyway.
        #
        # ``max_sweep_distance_widths`` caps the allowed close distance past the
        # boundary as a multiple of the anchor range width.  Default 2.0: with an
        # MGC width of 15.10pts the strategy will skip a fade once close > H + 30pts
        # (~$300 over the anchor).  Symbol overrides let MES use a different value
        # if its typical sweep amplitude differs.  Set to 0 to disable the guard
        # (legacy behaviour).
        self.max_sweep_distance_widths: float = max(
            0.0,
            float(self._cfg.get_float("signal.max_sweep_distance_widths", 2.0) or 0.0),
        )
        self.reentry_frac: float = float(self._cfg.get_float("signal.reentry_frac", 0.0) or 0.0)
        # False: first close outside range → stop-entry + brackets at once (overnight-style OCO path).
        # True: wait for a close back inside the inner band (reentry_frac) before signaling (legacy).
        self.require_reentry_close: bool = bool(
            self._cfg.get_bool("signal.require_reentry_close", False)
        )
        # Points-threshold re-entry trigger (replaces the candle-close wait when > 0). Detects
        # price physically returning into the range by this many points (intra-bar) and places the
        # stop-entry at the trigger level itself — guaranteed valid stop direction (price has to
        # traverse back to fire), and matches the realistic broker flow the legacy candle-close
        # path violated. See ``BacktestEngine`` placement_price guard for the fill semantics.
        self.reentry_threshold_points: float = float(
            self._cfg.get_float("signal.reentry_threshold_points", 7.0) or 0.0
        )
        # 0 = unlimited; else max completed fade *signals* per ET session date (same anchor day).
        self.max_fades_per_session: int = int(self._cfg.get_int("signal.max_fades_per_session", 0))
        # BONGO §4.3 — optional ATR regime gate (borrowed from body_reversion; default off).
        self.require_high_atr: bool = bool(self._cfg.get_bool("signal.require_high_atr", False))
        self.atr_period: int = int(self._cfg.get_int("signal.atr_period", 14))
        self.atr_regime_lookback: int = int(self._cfg.get_int("signal.atr_regime_lookback", 500))
        self.atr_regime_quantile: float = float(self._cfg.get_float("signal.atr_regime_quantile", 0.75))
        # Inverse ATR gate: skip entries when current ATR is *above* the
        # ``skip_high_atr_quantile`` percentile of the prior ATR samples.
        # Mutually exclusive with ``require_high_atr`` (the original gate
        # filters TO the high-vol regime; this one filters AWAY from it).
        # 0.0 = disabled; 0.90 = skip entries on days where today's ATR is
        # in the top 10% of the lookback window (volatility spike days).
        self.skip_high_atr_quantile: float = float(self._cfg.get_float("signal.skip_high_atr_quantile", 0.0))
        # ── Kaufman Efficiency Ratio (KER) trend-regime gate ────────────
        # KER = |close[t] - close[t-N]| / Σ |close[i] - close[i-1]| over N days.
        # KER ≈ 1 → perfect trend (every step in same direction).
        # KER ≈ 0 → perfect range (oscillation, no net movement).
        # Set ``skip_above_efficiency_ratio > 0`` and ``efficiency_ratio_lookback_days
        # > 0`` to skip entries when today's KER exceeds the threshold.
        # Per-symbol override-able. Default off (=0.0) so existing configs
        # are unaffected.
        self.skip_above_efficiency_ratio: float = float(
            self._cfg.get_float("signal.skip_above_efficiency_ratio", 0.0) or 0.0
        )
        self.efficiency_ratio_lookback_days: int = int(
            self._cfg.get_int("signal.efficiency_ratio_lookback_days", 5) or 5
        )
        # After range_end_open on the anchor ET date, ignore new sweeps / re-arms past this horizon (0 = off).
        self.range_effectiveness_hours: float = float(
            self._cfg.get_float("signal.range_effectiveness_hours", 4.0) or 4.0
        )
        # Maximum age of the most recent bar before live ``analyze()`` aborts loudly.
        # See toml comment + docs/GOTCHAS.md (2026-05-21 outage). Replay bypasses.
        self.max_bar_staleness_seconds: float = float(
            self._cfg.get_float("signal.max_bar_staleness_seconds", 600.0) or 0.0
        )
        # Throttle the STALE DATA error log to 1× per ``_stale_log_throttle_seconds``
        # so we don't flood the file when REST has been frozen for an hour.
        self._stale_log_throttle_seconds: float = 60.0
        self._stale_last_logged_at: Dict[str, float] = {}

        # BONGO §1B — live-only broker breakeven (``TopStepXTradingBot`` generic monitor).
        self.breakeven_enabled: bool = bool(
            self._cfg.get_bool("position_management.breakeven_enabled", False)
        )
        self.breakeven_trigger_r: float = float(
            self._cfg.get_float("position_management.breakeven_trigger_r", 0.5) or 0.5
        )
        # Optional price-pt offset applied to the breakeven stop in the trade's favour
        # (LONG → entry+offset, SHORT → entry−offset). Clamped to >= 0 here so a sloppy
        # negative TOML value can't accidentally tighten the stop into a loss zone.
        self.breakeven_offset: float = max(
            0.0,
            float(self._cfg.get_float("position_management.breakeven_offset", 0.0) or 0.0),
        )

        self.tick_sizes: Dict[str, float] = {
            "MNQ": 0.25,
            "NQ": 0.25,
            "MES": 0.25,
            "ES": 0.25,
            "MGC": 0.10,
            "GC": 0.10,
        }

        self._bar_seq: int = 0
        self._last_signal_bar: Dict[str, datetime] = {}
        self._entry_bar_seq: Dict[str, int] = {}
        self._state: Dict[str, Dict[str, Any]] = {}
        self._live_managed_symbols: set[str] = set()

        # Compact init banner: groups related knobs on separate lines for terminal
        # readability. The single-line summary used to overflow most terminals and
        # made it hard to spot which knob you cared about; this stacks them into
        # logical groups (timing, R-multiples, re-entry, session caps, filters).
        logger.info(
            "✅ morning_range_reversion ready  |  tf=%s  session=%s  "
            "build=%s→%s ET  flat_before=%s",
            self.timeframe, self.session_zone,
            self.range_start, self.range_end_open, self.flat_before,
        )
        logger.info(
            "   R-multiples   :  sl=%.2f  tp=%.2f  reentry=%.2f  "
            "reentry_threshold=%.2fpts  require_reentry_close=%s",
            self.sl_mult, self.tp_mult, self.reentry_frac,
            self.reentry_threshold_points, self.require_reentry_close,
        )
        logger.info(
            "   session caps  :  max_fades_per_session=%s  range_effectiveness=%.1fh  "
            "high_atr_filter=%s  partial_tp=%s",
            self.max_fades_per_session, float(self.range_effectiveness_hours),
            "on" if self.require_high_atr else "off",
            "on" if bool(self._cfg.get_bool("signal.partial_tp_enabled", False)) else "off",
        )

    def _tp_mult(self, symbol: str) -> float:
        """Per-symbol ``signal.tp_mult`` (``[symbols.<SYM>.signal]`` override)."""
        v = self._cfg.symbol_override(str(symbol).upper(), "signal.tp_mult", default=None)
        if v is not None:
            return max(0.1, float(v))
        return max(0.1, float(self.tp_mult))

    def _reentry_threshold_pts(self, symbol: str) -> float:
        """Per-symbol ``signal.reentry_threshold_points`` (``[symbols.<SYM>.signal]`` override).

        Returns 0 when explicitly set to <=0 so callers can disable the threshold without having
        to also set ``require_reentry_close=false``. The base TOML default is **7**, calibrated
        for MNQ; smaller-contract symbols (MES/MGC) need a finer trigger via override.
        """
        v = self._cfg.symbol_override(str(symbol).upper(), "signal.reentry_threshold_points", default=None)
        if v is not None:
            return max(0.0, float(v))
        return max(0.0, float(self.reentry_threshold_points))

    def _sl_fixed_pts(self, symbol: str) -> float:
        """Per-symbol ``signal.sl_fixed_pts`` override. 0 = use legacy half-range SL."""
        v = self._cfg.symbol_override(str(symbol).upper(), "signal.sl_fixed_pts", default=None)
        if v is not None:
            return max(0.0, float(v))
        return max(0.0, float(self.sl_fixed_pts))

    def _sl_mult(self, symbol: str) -> float:
        """Per-symbol ``signal.sl_mult`` override.

        Lets smaller-contract symbols (MES/MGC) use range-anchored stops while
        MNQ runs a fixed-pts override — the smaller contracts have wildly
        different per-point dollar values (MNQ $2 vs MES $5 vs MGC $10), so a
        blanket fixed-pts stop scales their per-trade dollar risk unevenly.
        Range-anchored ``sl_mult`` keeps the SL proportional to the day's
        anchor range, which is the natural volatility regime for each symbol.

        Walk-forward 9m sweep (2026-05-29): MGC ``sl_mult=3.0`` produced
        composite 41850 / DD 62% vs slfix=35's composite 26905 / DD 105% —
        same return with 40pp lower drawdown thanks to range-aware sizing.
        """
        v = self._cfg.symbol_override(str(symbol).upper(), "signal.sl_mult", default=None)
        if v is not None:
            return max(0.1, float(v))
        return max(0.1, float(getattr(self, "sl_mult", 1.0) or 1.0))

    def _sl_max_pts(self, symbol: str) -> float:
        """Per-symbol ``signal.sl_max_pts`` — safety CAP on the final SL distance.

        Bounds the worst-case per-trade risk regardless of which SL mode is
        active.  When non-zero, the computed SL distance (from either
        ``sl_fixed_pts`` or ``sl_mult × half_width``) is clamped to at most
        ``sl_max_pts``.  Zero / negative disables the cap.

        Rationale: ``sl_mult`` makes stops proportional to range, which is
        usually a feature — but extreme-range days (volatility spikes, news
        flush) can produce outsized stops (e.g. MGC 50pt range × sl_mult=3 →
        75pt SL = $750 risk per contract).  ``sl_max_pts`` caps that tail.
        """
        v = self._cfg.symbol_override(str(symbol).upper(), "signal.sl_max_pts", default=None)
        if v is None:
            v = self._cfg.get_float("signal.sl_max_pts", 0.0)
        try:
            return max(0.0, float(v or 0.0))
        except (TypeError, ValueError):
            return 0.0

    def _sl_min_pts(self, symbol: str) -> float:
        """Per-symbol ``signal.sl_min_pts`` — safety FLOOR on the final SL distance.

        Mirror of ``_sl_max_pts``: enforces a minimum SL distance so the stop
        never sits unrealistically close to entry on a compressed-range day
        where ``sl_mult × half_width`` would otherwise be tiny.  Zero / negative
        disables the floor.
        """
        v = self._cfg.symbol_override(str(symbol).upper(), "signal.sl_min_pts", default=None)
        if v is None:
            v = self._cfg.get_float("signal.sl_min_pts", 0.0)
        try:
            return max(0.0, float(v or 0.0))
        except (TypeError, ValueError):
            return 0.0

    def _sl_max_pct_of_range(self, symbol: str) -> float:
        """Per-symbol ``signal.sl_max_pct_of_range`` — *dynamic* cap on the SL
        distance, expressed as a fraction of the anchor range width.

        Operates *in addition to* the absolute ``sl_max_pts`` cap — the final
        SL is clamped by *both* (whichever is tighter on a given day).  Lets
        the SL scale with each day's volatility regime: on a compressed-range
        day the cap also tightens, on a wide-range day it loosens.

        Example: ``sl_max_pct_of_range = 1.5`` and a 12 pt anchor width →
        the SL distance is capped at 18 pt that day.  ``0`` disables this cap.
        """
        v = self._cfg.symbol_override(str(symbol).upper(), "signal.sl_max_pct_of_range", default=None)
        if v is None:
            v = self._cfg.get_float("signal.sl_max_pct_of_range", 0.0)
        try:
            return max(0.0, float(v or 0.0))
        except (TypeError, ValueError):
            return 0.0

    def _entry_window(self, symbol: str) -> tuple[Optional[dt_time], Optional[dt_time]]:
        """Per-symbol entry-time window ``[entry_start_et, entry_end_et]``.

        Returns a ``(start, end)`` tuple of ``datetime.time`` objects (both
        inclusive). Either side ``None`` means "no bound on that side".
        Times are interpreted in Eastern Time and compared against the bar
        timestamp in ET.

        Use case: skip entries during high-volatility regimes (e.g. first
        15 min of the cash open is whipsaw; last 30 min sees position-
        unwind tape) where the fade-after-sweep edge degrades.
        """
        start_raw = self._cfg.symbol_override(str(symbol).upper(), "signal.entry_start_et", default=None)
        if start_raw is None:
            start_raw = self._cfg.get("signal.entry_start_et", None)
        end_raw = self._cfg.symbol_override(str(symbol).upper(), "signal.entry_end_et", default=None)
        if end_raw is None:
            end_raw = self._cfg.get("signal.entry_end_et", None)
        start = self._parse_et_time(start_raw) if start_raw else None
        end = self._parse_et_time(end_raw) if end_raw else None
        return (start, end)

    @staticmethod
    def _parse_et_time(raw: Any) -> Optional[dt_time]:
        if raw is None:
            return None
        s = str(raw).strip()
        if not s:
            return None
        # Accept HH:MM (24h) or HH:MM:SS forms; tolerate stray quoting.
        s = s.strip("'\"")
        try:
            parts = s.split(":")
            h = int(parts[0])
            m = int(parts[1]) if len(parts) > 1 else 0
            sec = int(parts[2]) if len(parts) > 2 else 0
            return dt_time(hour=h, minute=m, second=sec)
        except (ValueError, IndexError):
            return None

    def _max_consecutive_losses(self, symbol: str) -> int:
        """Per-symbol ``signal.max_consecutive_losses`` — cross-session circuit
        breaker.  Once the *most-recent contiguous losing streak* on this
        symbol reaches the threshold, entries are halted until either
        (a) ``loss_streak_cooldown_sessions`` calendar days have elapsed
        since the tripping loss, or (b) a winning trade resets the streak.
        ``0`` disables the breaker.

        The streak is read from ``self._replay_engine.trades`` (backtest)
        or from the live bot's fill handler (production — wired separately).
        No per-bar strategy state is mutated, so the breaker is a pure
        function of engine state.
        """
        v = self._cfg.symbol_override(str(symbol).upper(), "signal.max_consecutive_losses", default=None)
        if v is None:
            v = self._cfg.get_int("signal.max_consecutive_losses", 0)
        try:
            return max(0, int(v or 0))
        except (TypeError, ValueError):
            return 0

    def _breaker_min_efficiency_ratio(self, symbol: str) -> float:
        """Per-symbol ``signal.breaker_min_efficiency_ratio`` — *regime* gate
        ANDed with the count-based ``max_consecutive_losses`` breaker.

        When > 0, the breaker only trips if today's Kaufman Efficiency Ratio
        (over ``breaker_efficiency_ratio_lookback_days``) is *above* this
        threshold.  Discriminates between:
          - Profitable-fold 2-loss streaks (low KER, mean-revert regime
            persists → breaker should NOT trip) → preserves recovery wins
          - Trend-cluster 2-loss streaks (high KER, sustained-direction
            regime → breaker SHOULD trip) → caps DD bleed

        Empirically (MGC 2025-08 → 2026-05 daily-close inspection):
          - Bad folds (1, 3): KER_10d mean 0.45-0.51
          - Profitable folds (7, 8): KER_10d mean 0.31-0.33
        A threshold ~0.35-0.40 cleanly separates the two regimes.
        ``0`` (default) disables — breaker uses count + magnitude only.
        """
        v = self._cfg.symbol_override(str(symbol).upper(), "signal.breaker_min_efficiency_ratio", default=None)
        if v is None:
            v = self._cfg.get_float("signal.breaker_min_efficiency_ratio", 0.0)
        try:
            return max(0.0, min(1.0, float(v or 0.0)))
        except (TypeError, ValueError):
            return 0.0

    def _breaker_efficiency_ratio_lookback_days(self, symbol: str) -> int:
        """Per-symbol ``signal.breaker_efficiency_ratio_lookback_days`` — KER
        lookback used by the ``breaker_min_efficiency_ratio`` gate.
        Default 10 days (gives cleaner regime separation than 5 in MGC
        empirical inspection)."""
        v = self._cfg.symbol_override(str(symbol).upper(), "signal.breaker_efficiency_ratio_lookback_days", default=None)
        if v is None:
            v = self._cfg.get_int("signal.breaker_efficiency_ratio_lookback_days", 10)
        try:
            return max(2, int(v or 10))
        except (TypeError, ValueError):
            return 10

    def _rolling_loss_threshold_dollars(self, symbol: str) -> float:
        """Per-symbol ``signal.rolling_pnl_loss_threshold_dollars`` — *magnitude*
        filter that ANDs with the count-based ``max_consecutive_losses``
        breaker.  When > 0, the breaker only trips if the *cumulative dollar
        PnL* of the loss streak is also worse than ``-threshold``.

        Designed to distinguish *shallow normal-market streaks* (recover
        next day) from *deep regime-change streaks* (sustained bleed).
        Using dollar units lets each symbol calibrate to its own
        per-trade PnL geometry (MGC ≫ MNQ in $/trade).

        ``0`` (default) disables the magnitude check — breaker becomes
        count-only.
        """
        v = self._cfg.symbol_override(str(symbol).upper(), "signal.rolling_pnl_loss_threshold_dollars", default=None)
        if v is None:
            v = self._cfg.get_float("signal.rolling_pnl_loss_threshold_dollars", 0.0)
        try:
            return max(0.0, float(v or 0.0))
        except (TypeError, ValueError):
            return 0.0

    def _loss_streak_cooldown_sessions(self, symbol: str) -> int:
        """Per-symbol ``signal.loss_streak_cooldown_sessions`` — number of
        *calendar days* to skip entries on this symbol once the
        ``max_consecutive_losses`` breaker has tripped.  ``0`` means "halt
        permanently until a winning trade resets the streak" — useful in
        live trading where the operator manually resumes, less so for
        backtesting where the breaker would never release.
        """
        v = self._cfg.symbol_override(str(symbol).upper(), "signal.loss_streak_cooldown_sessions", default=None)
        if v is None:
            v = self._cfg.get_int("signal.loss_streak_cooldown_sessions", 0)
        try:
            return max(0, int(v or 0))
        except (TypeError, ValueError):
            return 0

    def _consec_loss_breaker_status(self, symbol: str, bar_session_date: date,
                                     bars: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        """Inspect the engine's closed-trade history for ``symbol`` and return
        the breaker status as a dict::

            {
              "blocked": bool,
              "streak": int,               # current contiguous loss streak
              "trip_session_date": date|None,  # session of the Nth (tripping) loss
              "days_since_trip": int|None,     # calendar days since trip_session_date
              "cooldown": int,                  # configured cooldown window
              "reason": "ok" | "trip_active" | "trip_cooldown_elapsed" | "no_engine",
            }

        Reads ``self._replay_engine.trades`` (set by ``StrategyReplayEngine``
        at startup) — a pure read, no state writes, so this is safe to call
        on every bar.  Live mode falls through with ``blocked=False`` since
        the live bot tracks fills via its own ``brokers/topstepx_adapter``
        pipeline (live wiring is a follow-up).
        """
        mcl = self._max_consecutive_losses(symbol)
        if mcl <= 0:
            return {"blocked": False, "streak": 0, "reason": "ok"}
        engine = getattr(self, "_replay_engine", None)
        if engine is None or not hasattr(engine, "trades"):
            return {"blocked": False, "streak": 0, "reason": "no_engine"}
        sym_upper = str(symbol).upper()
        streak = 0
        streak_pnl_sum = 0.0
        latest_loss_date: Optional[date] = None
        for trade in reversed(getattr(engine, "trades", [])):
            t_sym = str(getattr(trade, "symbol", "") or "").upper()
            if t_sym != sym_upper:
                continue
            try:
                pnl = float(getattr(trade, "pnl", 0.0) or 0.0)
            except (TypeError, ValueError):
                pnl = 0.0
            if pnl >= 0:
                break  # winner ends the contiguous-loss walk
            # First losing trade we encounter walking backward = the most-recent
            # loss; record its date once and keep walking to grow the streak.
            if latest_loss_date is None:
                exit_ts = getattr(trade, "exit_time", None) or getattr(trade, "entry_time", None)
                latest_loss_date = self._normalize_to_date(exit_ts) or bar_session_date
            streak += 1
            streak_pnl_sum += pnl
        # Cooldown is measured from the MOST-RECENT loss (latest data point)
        # so a fresh loss in an existing streak re-arms the cooldown timer.
        trip_session_date = latest_loss_date
        if streak < mcl:
            return {"blocked": False, "streak": streak, "reason": "ok"}
        # Optional magnitude filter (ANDed with count).  Skip the trip
        # when the streak's cumulative PnL is shallower than the per-symbol
        # threshold — protects "normal-market 2-loss recoveries" while
        # still catching deeper "regime-change" streaks.
        loss_thr = self._rolling_loss_threshold_dollars(symbol)
        if loss_thr > 0.0 and streak_pnl_sum > -loss_thr:
            return {
                "blocked": False,
                "streak": streak,
                "streak_pnl": streak_pnl_sum,
                "magnitude_threshold": -loss_thr,
                "reason": "shallow_streak_skipped",
            }
        # Optional KER regime gate (ANDed with count + magnitude).  Skip
        # the trip when the recent regime is *not* trending — even after
        # an N-loss streak.  Designed to discriminate normal-market noise
        # streaks (low KER, mean-revert) from trend-cluster streaks
        # (high KER) without losing the DD protection on the latter.
        ker_min = self._breaker_min_efficiency_ratio(symbol)
        if ker_min > 0.0 and bars:
            ker_lookback = self._breaker_efficiency_ratio_lookback_days(symbol)
            ker_val = self._compute_efficiency_ratio(bars, bar_session_date, ker_lookback)
            if ker_val is not None and ker_val < ker_min:
                return {
                    "blocked": False,
                    "streak": streak,
                    "streak_pnl": streak_pnl_sum,
                    "ker_value": ker_val,
                    "ker_threshold": ker_min,
                    "reason": "low_ker_skipped_trip",
                }
        cooldown = self._loss_streak_cooldown_sessions(symbol)
        if cooldown <= 0:
            return {
                "blocked": True,
                "streak": streak,
                "streak_pnl": streak_pnl_sum,
                "trip_session_date": trip_session_date,
                "days_since_trip": None,
                "cooldown": 0,
                "reason": "trip_active",
            }
        days_elapsed = (bar_session_date - trip_session_date).days if trip_session_date else 0
        if days_elapsed < cooldown:
            return {
                "blocked": True,
                "streak": streak,
                "streak_pnl": streak_pnl_sum,
                "trip_session_date": trip_session_date,
                "days_since_trip": days_elapsed,
                "cooldown": cooldown,
                "reason": "trip_active",
            }
        return {
            "blocked": False,
            "streak": streak,
            "streak_pnl": streak_pnl_sum,
            "trip_session_date": trip_session_date,
            "days_since_trip": days_elapsed,
            "cooldown": cooldown,
            "reason": "trip_cooldown_elapsed",
        }

    @staticmethod
    def _normalize_to_date(ts: Any) -> Optional[date]:
        """Coerce engine timestamp (datetime / pandas.Timestamp / str) → ``date``."""
        if ts is None:
            return None
        if isinstance(ts, date) and not isinstance(ts, datetime):
            return ts
        if isinstance(ts, datetime):
            return ts.date()
        # pandas.Timestamp has a .to_pydatetime()
        if hasattr(ts, "to_pydatetime"):
            try:
                return ts.to_pydatetime().date()
            except Exception:  # noqa: BLE001
                pass
        if hasattr(ts, "date") and callable(getattr(ts, "date", None)):
            try:
                return ts.date()
            except Exception:  # noqa: BLE001
                pass
        return None

    def _min_range_width(self, symbol: str) -> float:
        """Per-symbol ``signal.min_range_width_points`` override. 0 = no minimum."""
        v = self._cfg.symbol_override(str(symbol).upper(), "signal.min_range_width_points", default=None)
        if v is not None:
            return max(0.0, float(v))
        return max(0.0, float(self.min_range_width_points))

    def _max_range_width(self, symbol: str) -> float:
        """Per-symbol ``signal.max_range_width_points`` override. 0 = no cap."""
        v = self._cfg.symbol_override(str(symbol).upper(), "signal.max_range_width_points", default=None)
        if v is not None:
            return max(0.0, float(v))
        return max(0.0, float(self.max_range_width_points))

    def _max_sweep_distance_widths(self, symbol: str) -> float:
        """Per-symbol ``signal.max_sweep_distance_widths`` override. 0 = no cap."""
        v = self._cfg.symbol_override(str(symbol).upper(), "signal.max_sweep_distance_widths", default=None)
        if v is not None:
            return max(0.0, float(v))
        return max(0.0, float(self.max_sweep_distance_widths))

    def _max_staleness_seconds(self, symbol: str) -> float:
        """Per-symbol ``signal.max_bar_staleness_seconds`` override. 0 = guard disabled."""
        v = self._cfg.symbol_override(str(symbol).upper(), "signal.max_bar_staleness_seconds", default=None)
        if v is not None:
            return max(0.0, float(v))
        return max(0.0, float(self.max_bar_staleness_seconds))

    # Mon=0 .. Sun=6 (matches ``datetime.date.weekday()``).
    _WEEKDAY_ALIASES = {
        "mon": 0, "monday": 0, "0": 0,
        "tue": 1, "tues": 1, "tuesday": 1, "1": 1,
        "wed": 2, "weds": 2, "wednesday": 2, "2": 2,
        "thu": 3, "thur": 3, "thurs": 3, "thursday": 3, "3": 3,
        "fri": 4, "friday": 4, "4": 4,
        "sat": 5, "saturday": 5, "5": 5,
        "sun": 6, "sunday": 6, "6": 6,
    }
    _WEEKDAY_ALIASES_INV = {0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu", 4: "Fri", 5: "Sat", 6: "Sun"}

    def _skip_weekdays(self, symbol: str) -> frozenset[int]:
        """Set of weekday ints (Mon=0..Sun=6) for which to suppress all anchor sessions.

        Reads ``signal.skip_weekdays`` (per-symbol override supported).  Accepts a
        list of names (``["Fri"]``) or ints (``[4]``) in TOML, OR a comma/space
        separated string (``"Fri, Mon"``) when supplied via env var. Unknown
        tokens are logged once and ignored. Empty / missing = no weekday gate.
        """
        raw = self._cfg.symbol_override(str(symbol).upper(), "signal.skip_weekdays", default=None)
        if raw is None:
            raw = self._cfg.get("signal.skip_weekdays", default=None)
        if raw is None or raw == "" or raw == []:
            return frozenset()
        tokens: List[str]
        if isinstance(raw, (list, tuple)):
            tokens = [str(x).strip() for x in raw]
        else:
            tokens = [t.strip() for t in str(raw).replace(",", " ").split() if t.strip()]
        out: set[int] = set()
        bad: list[str] = []
        for tok in tokens:
            key = tok.lower()
            if key in self._WEEKDAY_ALIASES:
                out.add(self._WEEKDAY_ALIASES[key])
            else:
                bad.append(tok)
        if bad and not getattr(self, "_warned_bad_skip_weekday", False):
            logger.warning(
                "morning_range_reversion: ignoring unknown skip_weekdays token(s) %r "
                "(accepts: Mon/Tue/.../Sun or 0..6)",
                bad,
            )
            self._warned_bad_skip_weekday = True
        return frozenset(out)

    def _bars_are_stale(self, symbol: str, bars: List[Dict[str, Any]]) -> bool:
        """Return True if the most recent bar is older than the configured guard.

        Skipped automatically during replay/backtest where ``_is_strategy_replay`` is set
        on the bot or ``_current_bar_timestamp`` anchors the clock. Logs a throttled
        ``⛔ STALE DATA`` error on the first hit (per-symbol, once per minute).
        """
        threshold = self._max_staleness_seconds(symbol)
        if threshold <= 0:
            return False
        bot = self.trading_bot
        if getattr(bot, "_is_strategy_replay", False):
            return False
        if getattr(bot, "_current_bar_timestamp", None) is not None:
            return False
        if not bars:
            return False
        last = bars[-1]
        ts = last.get("timestamp") or last.get("time") or last.get("t")
        if ts is None:
            return False
        try:
            if isinstance(ts, datetime):
                last_dt = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
            else:
                last_dt = pd.to_datetime(ts, utc=True).to_pydatetime()
        except Exception:
            return False
        now_utc = datetime.now(timezone.utc)
        age_s = (now_utc - last_dt).total_seconds()
        if age_s <= threshold:
            return False

        import time as _time

        now_mono = _time.monotonic()
        last_logged = self._stale_last_logged_at.get(symbol, 0.0)

        # ── Downgrade severity when stuck-REST detector just rotated the session
        #    (2026-05-29 fix). If ``trading_bot._last_session_reset_at_mono`` is
        #    within the cooldown window, the operator already saw a ``🧊 REST
        #    feed cold-start stale`` WARNING + ``🔁 HTTP session reset`` line —
        #    surfacing the same situation as ERROR right after is misleading.
        #    Log at WARNING for the first 30s post-rotation so the operator can
        #    see "this is the SAME outage, still recovering" without an ERROR
        #    storm. Steady-state stale data (no recent rotation) keeps logging
        #    ERROR because that's a real broker outage.
        last_reset_mono = getattr(bot, "_last_session_reset_at_mono", None)
        reset_cooldown_s = float(getattr(bot, "_session_reset_cooldown_s", 10.0))
        # 3× cooldown gives the bot 30s post-reset to actually pull fresh bars
        # before we go back to ERROR severity.
        in_active_recovery = (
            last_reset_mono is not None
            and (now_mono - float(last_reset_mono)) < (reset_cooldown_s * 3.0)
        )

        if now_mono - last_logged >= self._stale_log_throttle_seconds:
            if in_active_recovery:
                logger.warning(
                    "⛔ STALE DATA for %s: last bar %s is %.0fs old (threshold %.0fs) — "
                    "skipping analyze. Session was just rotated %.1fs ago — waiting for fresh "
                    "REST/SignalR data to flow in.",
                    symbol, last_dt.isoformat(), age_s, threshold,
                    now_mono - last_reset_mono,
                )
            else:
                logger.error(
                    "⛔ STALE DATA for %s: last bar %s is %.0fs old (threshold %.0fs) — "
                    "skipping analyze. Both REST historical feed AND Market Hub live cache are "
                    "stale; verify Market Hub log line '📡 Market Hub wired …' on startup, "
                    "check SignalR connectivity, and inspect broker /api/History/retrieveBars "
                    "(see docs/GOTCHAS.md → 'Live data freshness').",
                    symbol, last_dt.isoformat(), age_s, threshold,
                )
            self._stale_last_logged_at[symbol] = now_mono
        else:
            logger.debug(
                "stale data continues for %s: %.0fs old (next log in %.0fs)",
                symbol, age_s, self._stale_log_throttle_seconds - (now_mono - last_logged),
            )
        return True

    def _tick_size(self, symbol: str) -> float:
        return float(self.tick_sizes.get(symbol.upper(), 0.25))

    def _round_px(self, symbol: str, x: float) -> float:
        t = self._tick_size(symbol)
        return round(round(x / t) * t, 6)

    def _fade_deadline_et(self, anchor_et_date: date) -> datetime:
        return _et_datetime_combine(self._tz, anchor_et_date, self.range_end_open) + timedelta(
            hours=float(self.range_effectiveness_hours or 0.0)
        )

    def _bar_timestamp_eastern(self, bar: Dict[str, Any]) -> Optional[datetime]:
        ts = bar.get("timestamp") or bar.get("time") or bar.get("t")
        if ts is None:
            return None
        if isinstance(ts, datetime):
            dt = ts
        else:
            try:
                dt = pd.to_datetime(ts, utc=True).to_pydatetime()
            except Exception:
                try:
                    dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
                except Exception:
                    return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(self._tz)

    def _now_eastern(self) -> datetime:
        anchor = getattr(self.trading_bot, "_current_bar_timestamp", None)
        if anchor is not None:
            if anchor.tzinfo is None:
                anchor = anchor.replace(tzinfo=timezone.utc)
            return anchor.astimezone(self._tz)
        return datetime.now(self._tz)

    def _session_tz_wall_now(self) -> datetime:
        """Wall clock in ``signal.session_timezone`` (used by :meth:`_in_trading_window`)."""
        return datetime.now(self._tz)

    @staticmethod
    def _val(bar: Dict[str, Any], *keys: str, default: float = 0.0) -> float:
        for k in keys:
            v = bar.get(k)
            if v is not None:
                try:
                    return float(v)
                except (TypeError, ValueError):
                    continue
        return default

    def _live_tag_is_entry(self, tag: str) -> bool:
        t = str(tag).lower()
        if "morning_range_reversion" not in t and "morning-range-reversion" not in t:
            return False
        if "-sl" in t or "-tp" in t:
            return False
        return "stop_entry" in t or "stop_bracket" in t or "stop-entry" in t or "stop-bracket" in t

    def _live_order_is_pending_entry(self, order: Dict[str, Any], sym: str) -> bool:
        if not _order_counts_as_working_entry_for_risk(order):
            return False
        osym = (order.get("symbol") or "").upper()
        if osym != sym.upper():
            parts = str(order.get("symbolId", "")).split(".")
            if parts:
                osym = parts[-1].upper()
        if osym != sym.upper():
            return False
        tag = order.get("customTag") or order.get("custom_tag") or ""
        return self._live_tag_is_entry(tag)

    async def _has_open_position_or_pending_entry_async(self, symbol: str) -> bool:
        bot = self.trading_bot
        engine = getattr(bot, "backtest_engine", None) or getattr(self, "_replay_engine", None)
        if engine is not None:
            return self._has_open_position_or_pending_entry(symbol)
        if getattr(bot, "_is_strategy_replay", False):
            return self._has_open_position_or_pending_entry(symbol)

        account_id = None
        if isinstance(bot.selected_account, dict):
            account_id = bot.selected_account.get("id")
        elif bot.selected_account:
            account_id = str(bot.selected_account)
        if not account_id:
            return False
        try:
            positions = await bot.get_open_positions(account_id=account_id)
            for p in positions:
                if (p.get("symbol") or "").upper() == symbol.upper():
                    return True
            orders = await bot.get_open_orders(account_id=account_id)
            for o in orders:
                if self._live_order_is_pending_entry(o, symbol):
                    return True
        except Exception as exc:
            logger.debug("morning_range_reversion live lockout check failed: %s", exc)
            return False
        return False

    def _has_open_position_or_pending_entry(self, symbol: str) -> bool:
        bot = self.trading_bot
        positions = getattr(bot, "active_positions", None)
        if positions:
            for p in positions if isinstance(positions, list) else positions.values():
                psym = (p.get("symbol") if isinstance(p, dict) else getattr(p, "symbol", "")) or ""
                if psym.upper() == symbol.upper():
                    return True

        engine = getattr(bot, "backtest_engine", None) or getattr(self, "_replay_engine", None)
        if engine is not None:
            poss = getattr(engine, "positions", None) or {}
            if symbol.upper() in {k.upper() for k in poss}:
                return True
            pend = getattr(engine, "pending_orders", None) or []
            for o in pend:
                osym = getattr(o, "symbol", "") or ""
                if osym.upper() != symbol.upper():
                    continue
                if getattr(o, "oco_group", None):
                    ot = getattr(o, "order_type", None)
                    name = getattr(ot, "name", str(ot)).upper()
                    if name in ("STOP", "LIMIT") and "EXIT" not in name:
                        return True
        return False

    def _get_state(self, symbol: str) -> Dict[str, Any]:
        sym = symbol.upper()
        if sym not in self._state:
            self._state[sym] = {
                "session_date": None,
                "range_hi": None,
                "range_lo": None,
                "range_ready": False,
                "phase": "build",
                "sweep_side": None,
                "H": None,
                "L": None,
                "mid": None,
                "width": None,
                # After an immediate-stop signal, require a close back inside [L,H] before another
                # immediate fade this session (stops churn while price stays beyond the range).
                "immediate_block_until_inside": False,
                "fades_this_session": 0,
            }
        return self._state[sym]

    def record_trade_outcome(self, symbol: str, pnl: float) -> None:
        """Legacy live-mode hook for the consec-loss breaker.

        **No longer used in backtest** — the breaker now reads
        ``self._replay_engine.trades`` directly in
        ``_consec_loss_breaker_status`` (a pure read, no state writes),
        which avoids the per-bar non-determinism a hook-based dispatch
        caused (2026-05-29 9m walk-forward: +4 MNQ trades vs no-hook run
        even when the hook body was a no-op).

        Kept as a no-op stub for forward compatibility — if live trading
        later wants to drive the breaker from the bot's fill handler, the
        live path can build its own per-symbol trade-history list and
        adapt ``_consec_loss_breaker_status`` to read from that source.
        """
        return

    def _seed_range_from_history(
        self,
        bars: List[Dict[str, Any]],
        session_date,
    ) -> tuple:
        """Scan ``bars`` for completed bars whose ET timestamp falls inside the
        ``[range_start, range_end_open)`` window for ``session_date`` and return
        ``(range_hi, range_lo, bars_used)``.

        Used by ``analyze()`` to **backfill** the anchor range when the executor
        is started mid-session (i.e. after ``range_end_open`` ET has already
        passed). Without this, the per-symbol state machine — which expects to
        be alive through every build-window bar via the wake-driven loop —
        finalises from an empty state on the very first invocation, marks the
        session ``idle``, and emits no signals for the rest of the day. That's
        exactly what happened on 2026-05-27: the bot started at 08:07:30 ET,
        ~8 minutes past the 08:00 ET anchor close, ran for 60 minutes burning
        REST budget, and never traded.

        Returns ``(None, None, 0)`` if no completed bars fall inside the window
        (caller treats that as "still pre-anchor" or "no data for today").
        """
        if not bars:
            return (None, None, 0)
        hi: Optional[float] = None
        lo: Optional[float] = None
        n = 0
        rs = self.range_start
        re_open = self.range_end_open
        for b in bars:
            bt = self._bar_timestamp_eastern(b)
            if bt is None:
                continue
            if bt.date() != session_date:
                continue
            t_open = bt.time()
            if t_open < rs or t_open >= re_open:
                continue
            try:
                b_hi = self._val(b, "high", "h")
                b_lo = self._val(b, "low", "l")
            except Exception:
                continue
            if hi is None:
                hi = b_hi
                lo = b_lo
            else:
                hi = max(hi, b_hi)
                lo = min(lo, b_lo)
            n += 1
        return (hi, lo, n)

    @staticmethod
    def _morning_bar_val(bar: Dict[str, Any], *keys: str, default: float = 0.0) -> float:
        for k in keys:
            v = bar.get(k)
            if v is not None:
                try:
                    return float(v)
                except (TypeError, ValueError):
                    continue
        return default

    def _morning_atr_series(self, bars: List[Dict[str, Any]]) -> List[float]:
        """Wilder-style rolling mean TR (same construction as ``body_reversion``)."""
        period = max(2, int(self.atr_period))
        n = len(bars)
        out: List[float] = [float("nan")] * n
        if n < period + 1:
            return out
        trs: List[float] = []
        prev_close = self._morning_bar_val(bars[0], "close", "c")
        for i in range(1, n):
            hi = self._morning_bar_val(bars[i], "high", "h")
            lo = self._morning_bar_val(bars[i], "low", "l")
            cl = self._morning_bar_val(bars[i], "close", "c")
            tr = max(hi - lo, abs(hi - prev_close), abs(lo - prev_close))
            trs.append(tr)
            prev_close = cl
            if len(trs) >= period:
                out[i] = sum(trs[-period:]) / period
        return out

    def _skip_above_efficiency_ratio(self, symbol: str) -> float:
        """Per-symbol ``signal.skip_above_efficiency_ratio`` (default 0 = off).

        When > 0, skip entries on days where the Kaufman Efficiency Ratio
        computed over the last ``efficiency_ratio_lookback_days`` daily
        closes exceeds this threshold.  Catches sustained-trend regimes
        (e.g. late-2025 MGC) BEFORE the strategy takes its first losing
        fade — a *proactive* counterpart to the *reactive*
        ``max_consecutive_losses`` breaker.
        """
        v = self._cfg.symbol_override(str(symbol).upper(), "signal.skip_above_efficiency_ratio", default=None)
        if v is None:
            v = self._cfg.get_float("signal.skip_above_efficiency_ratio", 0.0)
        try:
            return max(0.0, min(1.0, float(v or 0.0)))
        except (TypeError, ValueError):
            return 0.0

    def _efficiency_ratio_lookback_days(self, symbol: str) -> int:
        v = self._cfg.symbol_override(str(symbol).upper(), "signal.efficiency_ratio_lookback_days", default=None)
        if v is None:
            v = self._cfg.get_int("signal.efficiency_ratio_lookback_days", 5)
        try:
            return max(2, int(v or 5))
        except (TypeError, ValueError):
            return 5

    def _compute_efficiency_ratio(self, bars: List[Dict[str, Any]], session_date: date,
                                    lookback_days: int) -> Optional[float]:
        """Kaufman Efficiency Ratio over the last ``lookback_days`` *daily*
        closes (one per session-date strictly before ``session_date``).

        Returns the ER in [0, 1] or ``None`` if there isn't enough history.
        Reduces 5-min bars to daily closes by taking the *last* completed
        bar for each calendar date in the ET-aware session-zone.

        ER = |close[t] - close[t-N]| / Σ |close[i] - close[i-1]|.
        A value of 1.0 means every step was in the same direction (perfect
        trend); 0.0 means perfect mean-reversion / sideways.
        """
        if not bars or lookback_days < 2:
            return None
        # Walk bars in reverse, taking the *last* bar of each distinct
        # session-date strictly before ``session_date``. Stop once we have
        # ``lookback_days + 1`` daily closes (one extra for the leading diff).
        seen: Dict[date, float] = {}
        for b in reversed(bars):
            bt = self._bar_timestamp_eastern(b)
            if bt is None:
                continue
            d = bt.date()
            if d >= session_date:
                continue
            if d in seen:
                continue  # already have the latest bar for this date
            try:
                seen[d] = float(self._val(b, "close", "c"))
            except Exception:  # noqa: BLE001
                continue
            if len(seen) >= lookback_days + 1:
                break
        if len(seen) < lookback_days + 1:
            return None
        # Sort ascending so we walk chronologically.
        closes = [seen[d] for d in sorted(seen.keys())]
        # Keep the last (lookback + 1) closes (ascending).
        closes = closes[-(lookback_days + 1):]
        net = abs(closes[-1] - closes[0])
        gross = sum(abs(closes[i] - closes[i - 1]) for i in range(1, len(closes)))
        if gross <= 0.0:
            return None
        return min(1.0, max(0.0, net / gross))

    def _morning_regime_atr_allows(self, bars: List[Dict[str, Any]]) -> bool:
        # Both gates off → always allow.
        if not self.require_high_atr and float(self.skip_high_atr_quantile or 0.0) <= 0.0:
            return True
        look = max(60, int(self.atr_regime_lookback))
        if len(bars) < max(self.atr_period + 20, 30):
            return False
        atrs = self._morning_atr_series(bars)
        window = atrs[-look:]
        finite = [a for a in window if a == a]
        cur = atrs[-1]
        min_need = max(8, min(50, max(15, len(bars) // 3)))
        if not (cur == cur) or len(finite) < min_need:
            return False
        sf = sorted(finite)
        # Require-high gate: today's ATR must be above the require quantile.
        if self.require_high_atr:
            q = max(0.0, min(1.0, float(self.atr_regime_quantile)))
            cut_idx = int(len(sf) * q)
            if cut_idx >= len(sf):
                cut_idx = len(sf) - 1
            thr = sf[cut_idx]
            if cur <= thr:
                return False
        # Skip-high gate: today's ATR must be at or below the skip quantile.
        skip_q = max(0.0, min(1.0, float(self.skip_high_atr_quantile or 0.0)))
        if skip_q > 0.0:
            cut_idx = int(len(sf) * skip_q)
            if cut_idx >= len(sf):
                cut_idx = len(sf) - 1
            thr_skip = sf[cut_idx]
            if cur > thr_skip:
                return False
        return True

    def _fade_signal_after_sweep(
        self,
        symbol: str,
        bar_et: datetime,
        sweep: str,
        H: float,
        L: float,
        mid: float,
        width: float,
        reason_tag: str,
        bars: Optional[List[Dict[str, Any]]] = None,
        *,
        entry_depth_pts: float = 0.0,
    ) -> Optional[Dict[str, Any]]:
        """Build fade signal (SHORT on high sweep, LONG on low sweep).

        With ``entry_depth_pts > 0`` the entry is shifted *inside* the range by that many points
        (used by the points-threshold re-entry mode so the stop-entry is placed at the trigger
        level, which guarantees a valid stop direction). The stop loss stays anchored to the
        range extreme so risk geometry vs the range is preserved.
        """
        # Consult the ATR regime gate whenever either side is configured —
        # ``require_high_atr`` filters TO the high-vol regime, while
        # ``skip_high_atr_quantile`` (mutually compatible) filters AWAY from
        # it. Both off → ``_morning_regime_atr_allows`` returns True early.
        if self.require_high_atr or float(self.skip_high_atr_quantile or 0.0) > 0.0:
            if not bars:
                return None
            if not self._morning_regime_atr_allows(bars):
                return None
        st = self._get_state(symbol)
        mf = int(getattr(self, "max_fades_per_session", 0) or 0)
        if mf > 0 and int(st.get("fades_this_session", 0) or 0) >= mf:
            if not st.get("_logged_max_fades"):
                logger.info(
                    "🛑 %-3s max_fades_per_session=%d reached — idle for the day "
                    "(no more sweep entries this session)",
                    symbol, mf,
                )
                st["_logged_max_fades"] = True
            return None

        # Time-of-day entry window — skip entries outside [entry_start_et, entry_end_et]
        # so we can clip first-N-minutes / last-N-minutes regimes that
        # systematically underperform.  Both bounds inclusive; either ``None`` =
        # unbounded on that side.
        win_start, win_end = self._entry_window(symbol)
        if win_start is not None or win_end is not None:
            bar_time = bar_et.time()
            if (win_start is not None and bar_time < win_start) or \
               (win_end is not None and bar_time > win_end):
                if not st.get("_logged_entry_window_skip"):
                    logger.info(
                        "⏰ %-3s entry skipped @ %s ET — outside window [%s, %s]",
                        symbol, bar_time.strftime("%H:%M"),
                        win_start.strftime("%H:%M") if win_start else "—",
                        win_end.strftime("%H:%M") if win_end else "—",
                    )
                    st["_logged_entry_window_skip"] = True
                return None

        # Kaufman Efficiency Ratio regime gate — proactive trend-day
        # detector.  Computes net-movement / sum-of-step-movements over the
        # last N daily closes; if today's KER exceeds the per-symbol
        # threshold, the underlying has been *trending* recently and a
        # mean-reversion fade is unlikely to mean-revert.  Off by default.
        ker_thr = self._skip_above_efficiency_ratio(symbol)
        if ker_thr > 0.0:
            lookback = self._efficiency_ratio_lookback_days(symbol)
            if bars:
                ker_val = self._compute_efficiency_ratio(bars, bar_et.date(), lookback)
                if ker_val is not None and ker_val > ker_thr:
                    cur_session = bar_et.date()
                    if st.get("_logged_ker_skip_session") != cur_session:
                        logger.info(
                            "📈 %-3s KER=%.2f > %.2f (lookback=%dd) — recent regime is "
                            "trending; skipping morning fade",
                            symbol, ker_val, ker_thr, lookback,
                        )
                        st["_logged_ker_skip_session"] = cur_session
                    return None

        # Cross-session consecutive-loss circuit breaker — inspects the
        # backtest engine's closed-trade list (or live broker's fill
        # history) to detect a contiguous losing streak that crosses
        # session boundaries.  When the streak ≥ ``max_consecutive_losses``,
        # entries are halted on this symbol for
        # ``loss_streak_cooldown_sessions`` calendar days from the tripping
        # loss.  Resets implicitly when a winning trade enters the history
        # OR the cooldown elapses.  Pure read of engine state — no
        # per-bar strategy mutations (the unguarded ``record_trade_outcome``
        # hook approach from a previous iteration caused a +4 MNQ trade
        # drift on 9m for unbisected reasons; this design avoids that
        # path entirely).
        breaker = self._consec_loss_breaker_status(symbol, bar_et.date(), bars=bars)
        if breaker.get("blocked"):
            cur_session = bar_et.date()
            if st.get("_logged_breaker_session") != cur_session:
                trip_d = breaker.get("trip_session_date")
                cooldown = breaker.get("cooldown") or 0
                days_since = breaker.get("days_since_trip")
                if cooldown > 0 and days_since is not None:
                    logger.info(
                        "🚨 %-3s consec-loss breaker active — streak=%d, tripped on %s, "
                        "day %d of %d-day cooldown (skipping entries)",
                        symbol, breaker.get("streak", 0), trip_d, days_since, cooldown,
                    )
                else:
                    logger.info(
                        "🚨 %-3s consec-loss breaker active — streak=%d (cooldown=0, halt "
                        "until winning trade resets the streak)",
                        symbol, breaker.get("streak", 0),
                    )
                st["_logged_breaker_session"] = cur_session
            return None

        half = width / 2.0

        # Range-width guards: skip if range is too narrow (near-zero TP) or too wide (oversized risk).
        min_w = self._min_range_width(symbol)
        max_w = self._max_range_width(symbol)
        if min_w > 0 and width < min_w:
            logger.debug(
                "morning_range_reversion %s: skip fade — range %.2f pts narrower than min %.2f",
                symbol, width, min_w,
            )
            return None
        if max_w > 0 and width > max_w:
            logger.debug(
                "morning_range_reversion %s: skip fade — range %.2f pts wider than max %.2f",
                symbol, width, max_w,
            )
            return None

        sl_mult = self._sl_mult(symbol)
        tp_mult = self._tp_mult(symbol)
        sl_fp = self._sl_fixed_pts(symbol)
        sl_cap = self._sl_max_pts(symbol)
        sl_floor = self._sl_min_pts(symbol)
        sl_pct_cap = self._sl_max_pct_of_range(symbol)
        # Dynamic cap = X * width. Combined with absolute sl_max_pts via min().
        sl_pct_cap_abs = sl_pct_cap * width if sl_pct_cap > 0 else 0.0
        depth = max(0.0, float(entry_depth_pts or 0.0))
        # Cap depth so the entry never crosses to the wrong side of the range midpoint.
        max_depth = max(0.0, half - 1e-6)
        if depth > max_depth:
            depth = max_depth
        if sweep == "high":
            if not self.allow_short:
                return None
            action = "SHORT"
            entry = self._round_px(symbol, H - depth)
            # Compute raw SL distance (from entry) — apply ``sl_max_pts`` /
            # ``sl_min_pts`` safety bounds before placing the stop. The fixed-
            # vs range-anchored geometry differs by which side the stop sits
            # on relative to the high; bound the *entry→stop distance* so the
            # cap/floor semantics are symmetrical across modes.
            if sl_fp > 0:
                sl_dist = sl_fp
            else:
                sl_dist = (H + half * sl_mult) - entry
            if sl_cap > 0:
                sl_dist = min(sl_dist, sl_cap)
            if sl_pct_cap_abs > 0:
                sl_dist = min(sl_dist, sl_pct_cap_abs)
            if sl_floor > 0:
                sl_dist = max(sl_dist, sl_floor)
            stop = self._round_px(symbol, entry + sl_dist)
            tp = self._round_px(symbol, H - half * tp_mult)
        else:
            if not self.allow_long:
                return None
            action = "LONG"
            entry = self._round_px(symbol, L + depth)
            if sl_fp > 0:
                sl_dist = sl_fp
            else:
                sl_dist = entry - (L - half * sl_mult)
            if sl_cap > 0:
                sl_dist = min(sl_dist, sl_cap)
            if sl_pct_cap_abs > 0:
                sl_dist = min(sl_dist, sl_pct_cap_abs)
            if sl_floor > 0:
                sl_dist = max(sl_dist, sl_floor)
            stop = self._round_px(symbol, entry - sl_dist)
            tp = self._round_px(symbol, L + half * tp_mult)

        self._last_signal_bar[symbol] = bar_et
        self._entry_bar_seq[symbol] = self._bar_seq
        reason = (
            f"morning_range_reversion sweep={sweep} {reason_tag} H={H:.2f} L={L:.2f} "
            f"mid={mid:.2f} W={width:.2f}"
        )
        logger.info("🎯 %-5s %-3s @ %9.2f  (%s)", action, symbol, entry, reason)
        st["fades_this_session"] = int(st.get("fades_this_session", 0) or 0) + 1
        return {
            "action": action,
            "symbol": symbol,
            "entry_price": entry,
            "stop_loss": stop,
            "take_profit": tp,
            "confidence": 0.5,
            "reason": reason,
        }

    async def analyze(self, symbol: str) -> Optional[Dict[str, Any]]:
        try:
            await self.manage_positions()

            bars = await self.trading_bot.get_historical_data(
                symbol=symbol,
                timeframe=self.timeframe,
                limit=self.lookback_bars,
            )
            if not bars or len(bars) < 10:
                return None

            if self._bars_are_stale(symbol, bars):
                return None

            last = bars[-1]
            bar_et = self._bar_timestamp_eastern(last)
            if bar_et is None:
                return None

            st = self._get_state(symbol)
            self._bar_seq += 1
            d = bar_et.date()

            # ── Weekday skip-gate (durable across all bars of the day) ─────────
            # Check BEFORE the new-session block: a skipped day never gets the
            # range-build / finalize path which would reset ``phase`` to
            # ``scan``. The check runs on EVERY bar so the gate stays active
            # even after ``session_date`` has been pinned to a skipped day —
            # putting it inside the ``if session_date != d`` block lets
            # subsequent bars of the same skipped day reach the range-build
            # branch and finalize-into-``scan`` happens at line ~1705.
            if d.weekday() in self._skip_weekdays(symbol):
                if st["session_date"] != d:
                    if not st.get("_logged_skip_weekday"):
                        logger.info(
                            "🚫 %-3s session skipped %s (%s in skip_weekdays=%s)",
                            symbol, d, d.strftime("%a"),
                            sorted(self._WEEKDAY_ALIASES_INV[i] for i in self._skip_weekdays(symbol)),
                        )
                        st["_logged_skip_weekday"] = True
                    st["session_date"] = d
                    st["phase"] = "idle"
                    st["range_ready"] = False
                    st["range_hi"] = st["range_lo"] = None
                    st["sweep_side"] = None
                    st["H"] = st["L"] = st["mid"] = st["width"] = None
                    st["immediate_block_until_inside"] = False
                    st["fades_this_session"] = 0
                    st["_logged_range_ready"] = False
                    st["_logged_range_degenerate"] = False
                    st["_logged_deadline"] = False
                    st["_logged_max_fades"] = False
                    st["_logged_entry_window_skip"] = False
                    st["_logged_breaker_session"] = None
                    st["_logged_ker_skip_session"] = None
                return None

            if st["session_date"] != d:
                # ── Lifecycle log: new session starting. Fires once per (symbol, date)
                # so the operator can confirm the strategy is rolling cleanly over to a
                # new day. Matches the verbosity overnight_range emits at its own
                # session boundaries (see strategies/overnight_range_strategy.py:936).
                if st["session_date"] is not None:
                    logger.info(
                        "🌅 %-3s new session %s  (anchor build %s → %s ET, fade window through %s ET)",
                        symbol, d, self.range_start, self.range_end_open, self.flat_before,
                    )
                st["session_date"] = d
                st["range_hi"] = st["range_lo"] = None
                st["range_ready"] = False
                st["phase"] = "build"
                st["sweep_side"] = None
                st["H"] = st["L"] = st["mid"] = st["width"] = None
                st["immediate_block_until_inside"] = False
                st["fades_this_session"] = 0
                # Per-session "already-logged" guards so the deadline / max-fades /
                # degenerate-range INFO lines fire at most once per (symbol, date).
                st["_logged_range_ready"] = False
                st["_logged_range_degenerate"] = False
                st["_logged_deadline"] = False
                st["_logged_max_fades"] = False
                st["_logged_entry_window_skip"] = False
                st["_logged_breaker_session"] = None
                st["_logged_ker_skip_session"] = None

                # NOTE: the ``skip_weekdays`` gate is checked above this block, so
                # by the time we reach here the current ``d`` is a traded weekday.
                st["_logged_skip_weekday"] = False

                # ── Mid-session start backfill (2026-05-27 fix) ───────────────────────
                # The per-bar build branch below only triggers when ``bars[-1]`` has
                # ``range_start <= t_open < range_end_open``. If the executor is
                # started **after** ``range_end_open`` ET, that branch never fires for
                # this session — the very first invocation falls through to the
                # finalize block with ``range_hi/range_lo == None`` and the symbol
                # goes idle for the whole day (no signals, no trades).
                #
                # The fix: scan the already-fetched 400-bar history for *completed*
                # bars whose ET timestamp lies inside the anchor window for the
                # current session date, and seed ``range_hi``/``range_lo`` from them.
                # This lets the executor recover the range whether it was started:
                #   • before 07:00 ET → no completed build bars yet, seed returns
                #     ``(None, None, 0)``; the per-bar branch builds the range live.
                #   • during 07:00-08:00 ET → seed captures completed build bars; the
                #     per-bar branch keeps extending H/L as the remaining bars close.
                #   • after 08:00 ET (the bug case) → seed captures the full window;
                #     finalisation below promotes ``range_ready=True`` and arms the
                #     fade scanner immediately on the very first invocation.
                seed_hi, seed_lo, seeded_n = self._seed_range_from_history(bars, d)
                if seeded_n > 0:
                    st["range_hi"] = seed_hi
                    st["range_lo"] = seed_lo
                    logger.info(
                        "🔁 %-3s anchor backfill from history for %s:  "
                        "seeded H=%9.2f  L=%9.2f  from %d build-window bar(s) "
                        "(executor started %s anchor close)",
                        symbol, d, float(seed_hi), float(seed_lo), seeded_n,
                        "past" if bar_et.time() >= self.range_end_open else "during",
                    )

            flat = not await self._has_open_position_or_pending_entry_async(symbol)

            hi = self._val(last, "high", "h")
            lo = self._val(last, "low", "l")
            c = self._val(last, "close", "c")
            t_open = bar_et.time()

            # Build range
            if not st["range_ready"]:
                if self.range_start <= t_open < self.range_end_open:
                    if st["range_hi"] is None:
                        st["range_hi"] = hi
                        st["range_lo"] = lo
                    else:
                        st["range_hi"] = max(float(st["range_hi"]), hi)
                        st["range_lo"] = min(float(st["range_lo"]), lo)
                    return None

                # Overnight / pre-07:00 ET bars: wait; do not mark idle (same bug as sieve).
                if t_open < self.range_start:
                    return None

                # finalize
                if st["range_hi"] is None or st["range_lo"] is None:
                    st["range_ready"] = True
                    st["phase"] = "idle"
                    if not st.get("_logged_range_degenerate"):
                        logger.warning(
                            "⚠️  morning_range_reversion %s anchor range invalid for %s "
                            "— no bars covered %s-%s ET, idle for the day",
                            symbol, d, self.range_start, self.range_end_open,
                        )
                        st["_logged_range_degenerate"] = True
                    return None
                H = float(st["range_hi"])
                L = float(st["range_lo"])
                if H <= L:
                    st["range_ready"] = True
                    st["phase"] = "idle"
                    if not st.get("_logged_range_degenerate"):
                        logger.warning(
                            "⚠️  morning_range_reversion %s anchor range degenerate for %s "
                            "(H=%.2f ≤ L=%.2f) — idle for the day",
                            symbol, d, H, L,
                        )
                        st["_logged_range_degenerate"] = True
                    return None
                st["H"], st["L"] = H, L
                st["width"] = H - L
                st["mid"] = (H + L) / 2.0
                st["range_ready"] = True

                # ── Range-width pre-filter (2026-05-29 fix) ─────────────────────────────
                # Previously the per-symbol ``min_range_width_points`` /
                # ``max_range_width_points`` guards lived inside
                # ``_fade_signal_after_sweep`` and rejected matches at ``logger.debug``
                # — invisible at INFO. Net effect: the strategy printed
                # ``📐 fade scanner armed`` for symbols whose range could never produce a
                # signal (e.g. MES width=11.75 vs root min=20), burned analyze cycles all
                # day, and left operators wondering why MES/MGC never traded.
                #
                # The fix: evaluate the filter here, at range-finalisation, and idle the
                # symbol immediately with a one-shot WARNING log. This mirrors the
                # ``range_invalid`` / ``range_degenerate`` UX so all "no-trade-today"
                # outcomes converge on the same log shape. The per-sweep filter inside
                # ``_fade_signal_after_sweep`` is kept as a defence-in-depth guard.
                min_w = self._min_range_width(symbol)
                max_w = self._max_range_width(symbol)
                width = H - L

                # ── Unified "anchor range built" lifecycle log (2026-05-29) ────────────
                # Emit ONE line per symbol/day that shows the operator the full filter
                # math at a glance: width, the bounds, and a ✓/✗ verdict. The terminal
                # sees this at INFO (via the lifecycle filter+formatter — the timestamp
                # and logger-name prefix are stripped so all three symbol lines align
                # visually). The file always sees it with full context.
                #
                # Fixed-width formatting (``{H:>9.2f}`` = 9 chars right-aligned with 2
                # decimals) keeps the H=/L=/width=/filter= columns vertically aligned
                # across symbols even when their magnitudes differ wildly (MNQ ~30000,
                # MGC ~4500, MES ~7500). Width=10 chars padding fits up to 9999999.99.
                # Symbol gets a 3-char right-pad so "MNQ"/"MES"/"MGC" line up.
                filter_desc = "" if min_w <= 0 and max_w <= 0 else (
                    f" filter={min_w:g}≤w≤{max_w:g}"
                    if max_w > 0 else f" filter≥{min_w:g}"
                )
                if min_w > 0 and width < min_w:
                    verdict = f"IDLED              ← {width:>6.2f} < {min_w:<3g} ✗"
                elif max_w > 0 and width > max_w:
                    verdict = f"IDLED              ← {width:>6.2f} > {max_w:<3g} ✗"
                else:
                    if min_w > 0 and max_w > 0:
                        verdict = f"fade scanner armed ← {min_w:>3g} ≤ {width:.2f} ≤ {max_w:<3g} ✓"
                    elif min_w > 0:
                        verdict = f"fade scanner armed ← {width:>6.2f} ≥ {min_w:<3g} ✓"
                    elif max_w > 0:
                        verdict = f"fade scanner armed ← {width:>6.2f} ≤ {max_w:<3g} ✓"
                    else:
                        verdict = "fade scanner armed"

                if not st.get("_logged_range_ready"):
                    logger.info(
                        "📐 %-3s anchor range built for %s:  H=%9.2f  L=%9.2f  width=%6.2fpts  mid=%9.2f%s  —  %s",
                        symbol, d, H, L, width, (H + L) / 2.0, filter_desc, verdict,
                    )
                    st["_logged_range_ready"] = True

                if min_w > 0 and width < min_w:
                    st["phase"] = "idle"
                    st["sweep_side"] = None
                    if not st.get("_logged_range_degenerate"):
                        logger.warning(
                            "📏 morning_range_reversion %s anchor range too narrow for %s "
                            "(width=%.2fpts < min=%.2fpts) — idle for the day. "
                            "Add a per-symbol override in [symbols.%s.signal] if this "
                            "range is normal for this contract.",
                            symbol, d, width, min_w, str(symbol).upper(),
                        )
                        st["_logged_range_degenerate"] = True
                    return None
                if max_w > 0 and width > max_w:
                    st["phase"] = "idle"
                    st["sweep_side"] = None
                    if not st.get("_logged_range_degenerate"):
                        logger.warning(
                            "📏 morning_range_reversion %s anchor range too wide for %s "
                            "(width=%.2fpts > max=%.2fpts) — idle for the day "
                            "(per-trade risk would exceed configured cap).",
                            symbol, d, width, max_w,
                        )
                        st["_logged_range_degenerate"] = True
                    return None

                st["phase"] = "scan"
                st["sweep_side"] = None

            if st["phase"] == "idle":
                return None

            H, L = float(st["H"]), float(st["L"])
            mid = float(st["mid"])
            width = float(st["width"])

            if float(self.range_effectiveness_hours or 0.0) > 0 and bar_et >= self._fade_deadline_et(d):
                if st["phase"] != "idle" and not st.get("_logged_deadline"):
                    fades_used = int(st.get("fades_this_session", 0) or 0)
                    logger.info(
                        "⏰ %-3s fade deadline reached (%.1fh after anchor close = %s ET) "
                        "— idle for the day, fades_used=%d",
                        symbol, float(self.range_effectiveness_hours),
                        self._fade_deadline_et(d).time(), fades_used,
                    )
                    st["_logged_deadline"] = True
                st["phase"] = "idle"
                st["sweep_side"] = None
                return None

            # Reset immediate-streak guard when price trades back through the range (incl. edges).
            if L <= c <= H:
                st["immediate_block_until_inside"] = False

            prev_sig = self._last_signal_bar.get(symbol)
            if prev_sig is not None and bar_et <= prev_sig:
                return None

            if not flat:
                return None

            # sweep: advance stop-entry at trigger (threshold mode), wait for re-entry, or immediate at extreme
            if st["phase"] == "scan" and st["sweep_side"] is None:
                threshold_pts = self._reentry_threshold_pts(symbol)
                # ── Far-sweep guard (2026-05-29 fix) ────────────────────────────────
                # ``c > H`` / ``c < L`` is direction-only — it never asked HOW FAR
                # past the boundary price has drifted. Without this guard the
                # strategy will happily place a stop-entry at the anchor extreme
                # even when current market is double-digit widths away (e.g. 2026-05-29
                # MGC: 43pt sweep over a 15.10pt range while the entry was pinned
                # to H=4571.90 → broker rejected as ``Invalid price. Price is
                # outside allowed range.``). When the close is > N × width past
                # the boundary, skip the signal with a one-shot lifecycle log and
                # keep the symbol in ``scan`` so it can still arm if price
                # eventually retraces back into striking range.
                far_widths = self._max_sweep_distance_widths(symbol)
                if far_widths > 0 and width > 0:
                    if c > H:
                        sweep_distance = c - H
                        sweep_side_dbg = "high"
                    elif c < L:
                        sweep_distance = L - c
                        sweep_side_dbg = "low"
                    else:
                        sweep_distance = 0.0
                        sweep_side_dbg = ""
                    if sweep_side_dbg and sweep_distance > far_widths * width:
                        log_key = f"_logged_far_sweep_{sweep_side_dbg}"
                        if not st.get(log_key):
                            logger.warning(
                                "🛰️  %-3s sweep too far for fade — close=%.2f is %.2fpts past "
                                "%s=%.2f (%.2f× width=%.2f, cap=%.2f×). Skipping signal until "
                                "price retraces inside cap. (signal.max_sweep_distance_widths=%.2f)",
                                symbol, c, sweep_distance,
                                "H" if sweep_side_dbg == "high" else "L",
                                H if sweep_side_dbg == "high" else L,
                                sweep_distance / width, width,
                                far_widths, far_widths,
                            )
                            st[log_key] = True
                        return None
                    # Re-arm the one-shot log if price has retraced back into the cap
                    # so a *fresh* far-sweep on the same side gets a new warning.
                    if sweep_side_dbg:
                        opposite_key = (
                            "_logged_far_sweep_low" if sweep_side_dbg == "high"
                            else "_logged_far_sweep_high"
                        )
                        if sweep_distance <= far_widths * width:
                            st.pop("_logged_far_sweep_" + sweep_side_dbg, None)
                            st.pop(opposite_key, None)
                if c > H:
                    sweep = "high"
                    if threshold_pts > 0.0:
                        # Overnight-range style: on first close outside the box, place stop-entry
                        # ``threshold`` pts back inside (SHORT at H − threshold).
                        if st.get("immediate_block_until_inside"):
                            return None
                        sig = self._fade_signal_after_sweep(
                            symbol,
                            bar_et,
                            sweep,
                            H,
                            L,
                            mid,
                            width,
                            "sweep_advance_stop",
                            bars,
                            entry_depth_pts=threshold_pts,
                        )
                        if sig:
                            st["immediate_block_until_inside"] = True
                        return sig
                    st["sweep_side"] = sweep
                    if self.require_reentry_close:
                        st["phase"] = "wait_re"
                        return None
                    if st.get("immediate_block_until_inside"):
                        st["sweep_side"] = None
                        return None
                    st["sweep_side"] = None
                    sig = self._fade_signal_after_sweep(
                        symbol, bar_et, sweep, H, L, mid, width, "immediate_stop", bars
                    )
                    if sig:
                        st["immediate_block_until_inside"] = True
                    return sig
                if c < L:
                    sweep = "low"
                    if threshold_pts > 0.0:
                        if st.get("immediate_block_until_inside"):
                            return None
                        sig = self._fade_signal_after_sweep(
                            symbol,
                            bar_et,
                            sweep,
                            H,
                            L,
                            mid,
                            width,
                            "sweep_advance_stop",
                            bars,
                            entry_depth_pts=threshold_pts,
                        )
                        if sig:
                            st["immediate_block_until_inside"] = True
                        return sig
                    st["sweep_side"] = sweep
                    if self.require_reentry_close:
                        st["phase"] = "wait_re"
                        return None
                    if st.get("immediate_block_until_inside"):
                        st["sweep_side"] = None
                        return None
                    st["sweep_side"] = None
                    sig = self._fade_signal_after_sweep(
                        symbol, bar_et, sweep, H, L, mid, width, "immediate_stop", bars
                    )
                    if sig:
                        st["immediate_block_until_inside"] = True
                    return sig
                return None

            if st["phase"] == "wait_re" and st["sweep_side"] is not None:
                threshold_pts = self._reentry_threshold_pts(symbol)
                sweep = st["sweep_side"]
                if threshold_pts > 0.0:
                    # Points-threshold trigger: arm the moment price physically retraces
                    # ``threshold_pts`` into the range from the sweep side. Entry is the trigger
                    # level itself so the stop-entry has a valid direction (price must traverse
                    # back to fire — no more imaginary fills at the range extreme).
                    if sweep == "high":
                        trigger_px = H - threshold_pts
                        crossed = lo <= trigger_px
                    else:
                        trigger_px = L + threshold_pts
                        crossed = hi >= trigger_px
                    if not crossed:
                        return None
                    st["sweep_side"] = None
                    st["phase"] = "scan"
                    return self._fade_signal_after_sweep(
                        symbol,
                        bar_et,
                        sweep,
                        H,
                        L,
                        mid,
                        width,
                        "reentry_threshold_pts",
                        bars,
                        entry_depth_pts=threshold_pts,
                    )
                # Legacy candle-close path: wait for a 5m close back inside the inner band.
                frac = max(0.0, min(0.49, float(getattr(self, "reentry_frac", 0.0) or 0.0)))
                inner_lo = L + frac * width
                inner_hi = H - frac * width
                if not (inner_lo <= c <= inner_hi):
                    return None
                st["sweep_side"] = None
                st["phase"] = "scan"
                return self._fade_signal_after_sweep(
                    symbol, bar_et, sweep, H, L, mid, width, "reentry_confirm", bars
                )

            return None
        except Exception as exc:
            logger.error("morning_range_reversion.analyze error for %s: %s", symbol, exc, exc_info=True)
            return None

    def _position_size(self, symbol: str) -> int:
        """Resolve effective contract count for ``symbol``.

        Precedence: ``[symbols.<SYM>.risk].position_size`` (TOML) →
        ``self.config.position_size`` (strategy-base / root TOML) → 1.

        Round-24 weighting sweep showed MNQ benefits from 2× sizing on every
        window (DD modest, RF improves) while MES/MGC stay at 1×; keeping the
        knob per-symbol-overridable lets future regimes re-tune without code.
        """
        override = self._cfg.symbol_override(
            str(symbol).upper(), "risk.position_size", default=None
        )
        if override is not None:
            try:
                return max(int(override), 1)
            except (TypeError, ValueError):
                logger.warning(
                    "morning_range_reversion: invalid [symbols.%s.risk].position_size=%r, "
                    "falling back to root",
                    symbol, override,
                )
        return max(int(self.config.position_size or 1), 1)

    async def execute(self, signal: Dict[str, Any]) -> bool:
        try:
            side = "BUY" if signal["action"] == "LONG" else "SELL"
            qty = self._position_size(signal["symbol"])
            r0 = abs(float(signal["entry_price"]) - float(signal["stop_loss"]))
            be_thr = (
                float(self.breakeven_trigger_r) * r0
                if self.breakeven_enabled and r0 > 0
                else None
            )
            partial_on = bool(self._cfg.get_bool("signal.partial_tp_enabled", False))
            partial_r = float(self._cfg.get_float("signal.partial_tp_scalp_r", 1.0) or 1.0)
            result = await self.place_bracket_order(
                symbol=signal["symbol"],
                side=side,
                quantity=qty,
                entry_price=signal["entry_price"],
                stop_loss_price=signal["stop_loss"],
                take_profit_price=signal["take_profit"],
                enable_breakeven=False,
                breakeven_profit_threshold=be_thr,
                breakeven_offset=(self.breakeven_offset if be_thr else 0.0),
                partial_tp_enabled=partial_on and qty >= 2,
                partial_tp_scalp_r=partial_r,
            )
            if result and result.get("error"):
                logger.warning(
                    "morning_range_reversion execute rejected %s %s: %s",
                    signal["action"],
                    signal["symbol"],
                    result.get("error"),
                )
                return False
            if not getattr(self.trading_bot, "_is_strategy_replay", False):
                self._live_managed_symbols.add(str(signal["symbol"]).upper())
            self.daily_trades += 1
            return True
        except Exception as exc:
            logger.error("morning_range_reversion.execute error: %s", exc, exc_info=True)
            return False

    async def manage_positions(self) -> None:
        if self.max_hold_bars <= 0:
            return None
        bot = self.trading_bot
        engine = getattr(bot, "backtest_engine", None) or getattr(self, "_replay_engine", None)
        now_et = self._now_eastern()
        if now_et.time() >= self.flat_before:
            # Flatten handled per-position in live path; replay uses bar timeout below.
            pass

        if engine is None:
            if getattr(bot, "_is_strategy_replay", False):
                return None
            await self._manage_positions_live()
            return None

        positions = getattr(engine, "positions", None) or {}
        pending_orders = getattr(engine, "pending_orders", None)
        if pending_orders is not None and positions is not None:
            for o in list(pending_orders):
                if not getattr(o, "oco_group", None):
                    continue
                osym = getattr(o, "symbol", "")
                if osym in positions:
                    continue
                try:
                    pending_orders.remove(o)
                except ValueError:
                    pass

        for sym, pos in list(positions.items()):
            # ── max_hold_bars is "bars since the entry FILLED", not "bars since
            # the signal was emitted". For threshold/advance-stop mode the
            # entry order can sit pending for an hour or more before price
            # retraces back to the trigger; counting from the signal bar made
            # the trade time out *prematurely* relative to the user's intent.
            # Example: 2026-03-30 MNQ — sweep at 09:25 ET, entry filled at
            # 11:00 ET, ``max_hold_bars=46`` (3h50m). Old code counted from
            # 09:25 → timed out at 13:15 ET after only 2h15m of actual hold,
            # *before* price would have hit either the SL or TP. The
            # ``BacktestPosition`` records ``entry_bar_index`` at the actual
            # fill, so prefer that. Falls back to the legacy ``_entry_bar_seq``
            # (which is what live mode still uses, since it has no engine).
            entry_bar_idx = getattr(pos, "entry_bar_index", None)
            if entry_bar_idx is not None and hasattr(engine, "current_bar_index"):
                held = int(engine.current_bar_index) - int(entry_bar_idx)
            else:
                entry_seq = self._entry_bar_seq.get(sym)
                if entry_seq is None:
                    continue
                held = self._bar_seq - entry_seq
            if held < self.max_hold_bars:
                continue
            logger.debug(
                "morning_range_reversion timeout-close %s after %d bars (max=%d, "
                "entry_bar_idx=%s)",
                sym,
                held,
                self.max_hold_bars,
                entry_bar_idx,
            )
            pending = getattr(engine, "pending_orders", None) or []
            for o in list(pending):
                if getattr(o, "symbol", "") != sym:
                    continue
                if not getattr(o, "oco_group", None):
                    continue
                try:
                    pending.remove(o)
                except ValueError:
                    pass

            from core.backtest.models import OrderSide, OrderType

            close_side = (
                OrderSide.SELL if getattr(pos, "side", None) == OrderSide.BUY else OrderSide.BUY
            )
            try:
                close_id = engine.place_order(
                    symbol=sym,
                    side=close_side,
                    quantity=int(getattr(pos, "quantity", 1)),
                    order_type=OrderType.MARKET,
                    price=float(getattr(pos, "current_price", getattr(pos, "entry_price", 0.0))),
                )
                for o in engine.pending_orders:
                    if o.order_id == close_id:
                        o.exit_reason = "timeout"
                        break
            except Exception as exc:
                logger.debug("morning_range_reversion timeout-close failed for %s: %s", sym, exc)
            finally:
                self._entry_bar_seq.pop(sym, None)
        return None

    async def _manage_positions_live(self) -> None:
        bot = self.trading_bot
        account_id = None
        if isinstance(bot.selected_account, dict):
            account_id = bot.selected_account.get("id")
        elif bot.selected_account:
            account_id = str(bot.selected_account)
        if not account_id:
            return
        try:
            positions = await bot.get_open_positions(account_id=account_id)
        except Exception as exc:
            logger.warning("morning_range_reversion live manage_positions failed: %s", exc)
            return

        now_et = self._now_eastern()
        if now_et.time() < self.flat_before:
            return

        for pos in positions:
            sym = (pos.get("symbol") or "").upper()
            if not sym or sym not in self._live_managed_symbols:
                continue
            pid = str(pos.get("id") or pos.get("position_id") or "").strip()
            if not pid:
                continue
            logger.info("morning_range_reversion live flat_before exit %s", sym)
            try:
                await bot.close_position(position_id=pid, account_id=account_id)
            except Exception as exc:
                logger.warning("morning_range_reversion close_position failed %s: %s", sym, exc)
            finally:
                self._live_managed_symbols.discard(sym)
                self._entry_bar_seq.pop(sym, None)

    async def cleanup(self) -> None:
        self.status = StrategyStatus.IDLE

    def get_market_condition(self, symbol: str) -> MarketCondition:
        return MarketCondition.RANGING

    def _in_trading_window(self) -> bool:
        """Same minute-window rules as :meth:`BaseStrategy._in_trading_window`, but **US/Eastern**.

        ``StrategyConfig.trading_start_time`` / ``trading_end_time`` (from TOML ``start_time`` /
        ``end_time``) are interpreted in ``signal.session_timezone`` so a laptop in Tokyo or
        California still arms during the documented 07:00–08:00 ET anchor window.
        """
        if getattr(self.trading_bot, "_is_strategy_replay", False):
            return True
        now_et = self._session_tz_wall_now()
        current_time = now_et.hour * 60 + now_et.minute

        start_hour, start_min = map(int, self.config.trading_start_time.split(":"))
        end_hour, end_min = map(int, self.config.trading_end_time.split(":"))

        if self.config.no_trade_start and self.config.no_trade_start.strip():
            no_trade_start_h, no_trade_start_m = map(int, self.config.no_trade_start.split(":"))
            no_trade_start = no_trade_start_h * 60 + no_trade_start_m
        else:
            no_trade_start = -1

        if self.config.no_trade_end and self.config.no_trade_end.strip():
            no_trade_end_h, no_trade_end_m = map(int, self.config.no_trade_end.split(":"))
            no_trade_end = no_trade_end_h * 60 + no_trade_end_m
        else:
            no_trade_end = -1

        start_time = start_hour * 60 + start_min
        end_time = end_hour * 60 + end_min

        if not (start_time <= current_time <= end_time):
            return False

        if no_trade_start >= 0 and no_trade_end >= 0:
            if no_trade_start <= current_time <= no_trade_end:
                return False

        return True
