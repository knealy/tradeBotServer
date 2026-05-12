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
3. **Default (``require_reentry_close = false``)** — on that same bar, arm a **fade** (stop-entry + brackets, same bot path as overnight_range):
   - High sweep → **SHORT** at range **high**, TP at **midpoint** (modulo ``tp_mult``), SL scaled by ``sl_mult``.
   - Low sweep → **LONG** at range **low**, symmetric.
   **Legacy (``signal.require_reentry_close = true``)** — wait for an additional bar whose **close** is back
   inside the **inner band** ``[L + reentry_frac·W, H − reentry_frac·W]`` (``reentry_frac=0`` → full box).
4. Multiple sequences per session are allowed after the simulated trade is flat again (new sweep required).

Intrabar: pass ``one_minute_df`` into ``sieve_simulate_from_ohlcv`` to resolve
TP vs SL using **1m** paths inside each 5m bar; otherwise if both touch on 5m,
**stop before target** by default (see ``_resolve_both_hit_outcome``).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
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
    require_high_atr: bool = False,
    atr_period: int = 14,
    atr_regime_lookback: int = 500,
    atr_regime_quantile: float = 0.75,
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
    ) -> None:
        """Arm synthetic entry at range extreme (mirrors stop-entry fade geometry)."""
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
        fades_this_session += 1
        half = width / 2.0
        if sv == "high":
            entry = H
            stop_px = H + half * sl_m
            tgt_px = H - half * tp_m
            side = "SHORT"
        else:
            entry = L
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

        # Same as live analyze(): allow another immediate arm only after a close inside the range.
        if L <= c <= H:
            block_next_immediate = False

        # --- sweep / optional re-entry / immediate arm ---
        if phase == "scan" and not in_trade:
            if sweep_side is None:
                if c > H:
                    sweep_side = "high"
                    if require_reentry_close:
                        phase = "wait_re"
                    else:
                        if block_next_immediate:
                            sweep_side = None
                        else:
                            arm_from_sweep("high", ts, o, hi, lo, c, d, bar_idx)
                            block_next_immediate = True
                elif c < L:
                    sweep_side = "low"
                    if require_reentry_close:
                        phase = "wait_re"
                    else:
                        if block_next_immediate:
                            sweep_side = None
                        else:
                            arm_from_sweep("low", ts, o, hi, lo, c, d, bar_idx)
                            block_next_immediate = True

        if phase == "wait_re" and not in_trade and sweep_side is not None:
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
        self.reentry_frac: float = float(self._cfg.get_float("signal.reentry_frac", 0.0) or 0.0)
        # False: first close outside range → stop-entry + brackets at once (overnight-style OCO path).
        # True: wait for a close back inside the inner band (reentry_frac) before signaling (legacy).
        self.require_reentry_close: bool = bool(
            self._cfg.get_bool("signal.require_reentry_close", False)
        )
        # 0 = unlimited; else max completed fade *signals* per ET session date (same anchor day).
        self.max_fades_per_session: int = int(self._cfg.get_int("signal.max_fades_per_session", 0))
        # BONGO §4.3 — optional ATR regime gate (borrowed from body_reversion; default off).
        self.require_high_atr: bool = bool(self._cfg.get_bool("signal.require_high_atr", False))
        self.atr_period: int = int(self._cfg.get_int("signal.atr_period", 14))
        self.atr_regime_lookback: int = int(self._cfg.get_int("signal.atr_regime_lookback", 500))
        self.atr_regime_quantile: float = float(self._cfg.get_float("signal.atr_regime_quantile", 0.75))

        # BONGO §1B — live-only broker breakeven (``TopStepXTradingBot`` generic monitor).
        self.breakeven_enabled: bool = bool(
            self._cfg.get_bool("position_management.breakeven_enabled", False)
        )
        self.breakeven_trigger_r: float = float(
            self._cfg.get_float("position_management.breakeven_trigger_r", 0.5) or 0.5
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

        logger.info(
            "✅ morning_range_reversion init: tf=%s session=%s range=%s-%s flat_before=%s "
            "sl=%.2f tp=%.2f reentry=%.2f require_reentry_close=%s max_fades_per_session=%s "
            "high_atr=%s",
            self.timeframe,
            self.session_zone,
            self.range_start,
            self.range_end_open,
            self.flat_before,
            self.sl_mult,
            self.tp_mult,
            self.reentry_frac,
            self.require_reentry_close,
            self.max_fades_per_session,
            "on" if self.require_high_atr else "off",
        )

    def _tick_size(self, symbol: str) -> float:
        return float(self.tick_sizes.get(symbol.upper(), 0.25))

    def _round_px(self, symbol: str, x: float) -> float:
        t = self._tick_size(symbol)
        return round(round(x / t) * t, 6)

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

    def _morning_regime_atr_allows(self, bars: List[Dict[str, Any]]) -> bool:
        if not self.require_high_atr:
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
        q = max(0.0, min(1.0, float(self.atr_regime_quantile)))
        cut_idx = int(len(sf) * q)
        if cut_idx >= len(sf):
            cut_idx = len(sf) - 1
        thr = sf[cut_idx]
        return cur > thr

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
    ) -> Optional[Dict[str, Any]]:
        """Build fade signal (SHORT on high sweep, LONG on low sweep) at range extreme."""
        if self.require_high_atr:
            if not bars:
                return None
            if not self._morning_regime_atr_allows(bars):
                return None
        st = self._get_state(symbol)
        mf = int(getattr(self, "max_fades_per_session", 0) or 0)
        if mf > 0 and int(st.get("fades_this_session", 0) or 0) >= mf:
            return None
        half = width / 2.0
        sl_mult = max(0.1, float(getattr(self, "sl_mult", 1.0) or 1.0))
        tp_mult = max(0.1, float(getattr(self, "tp_mult", 1.0) or 1.0))
        if sweep == "high":
            if not self.allow_short:
                return None
            action = "SHORT"
            entry = self._round_px(symbol, H)
            stop = self._round_px(symbol, H + half * sl_mult)
            tp = self._round_px(symbol, H - half * tp_mult)
        else:
            if not self.allow_long:
                return None
            action = "LONG"
            entry = self._round_px(symbol, L)
            stop = self._round_px(symbol, L - half * sl_mult)
            tp = self._round_px(symbol, L + half * tp_mult)

        self._last_signal_bar[symbol] = bar_et
        self._entry_bar_seq[symbol] = self._bar_seq
        reason = (
            f"morning_range_reversion sweep={sweep} {reason_tag} H={H:.2f} L={L:.2f} "
            f"mid={mid:.2f} W={width:.2f}"
        )
        logger.info("🎯 morning_range_reversion %s on %s @ %.2f (%s)", action, symbol, entry, reason)
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

            last = bars[-1]
            bar_et = self._bar_timestamp_eastern(last)
            if bar_et is None:
                return None

            st = self._get_state(symbol)
            self._bar_seq += 1
            d = bar_et.date()
            if st["session_date"] != d:
                st["session_date"] = d
                st["range_hi"] = st["range_lo"] = None
                st["range_ready"] = False
                st["phase"] = "build"
                st["sweep_side"] = None
                st["H"] = st["L"] = st["mid"] = st["width"] = None
                st["immediate_block_until_inside"] = False
                st["fades_this_session"] = 0

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
                    return None
                H = float(st["range_hi"])
                L = float(st["range_lo"])
                if H <= L:
                    st["range_ready"] = True
                    st["phase"] = "idle"
                    return None
                st["H"], st["L"] = H, L
                st["width"] = H - L
                st["mid"] = (H + L) / 2.0
                st["range_ready"] = True
                st["phase"] = "scan"
                st["sweep_side"] = None

            if st["phase"] == "idle":
                return None

            H, L = float(st["H"]), float(st["L"])
            mid = float(st["mid"])
            width = float(st["width"])

            # Reset immediate-streak guard when price trades back through the range (incl. edges).
            if L <= c <= H:
                st["immediate_block_until_inside"] = False

            prev_sig = self._last_signal_bar.get(symbol)
            if prev_sig is not None and bar_et <= prev_sig:
                return None

            if not flat:
                return None

            # sweep: either arm immediately (stop-entry + brackets) or wait for re-entry close
            if st["phase"] == "scan" and st["sweep_side"] is None:
                if c > H:
                    st["sweep_side"] = "high"
                    if self.require_reentry_close:
                        st["phase"] = "wait_re"
                        return None
                    if st.get("immediate_block_until_inside"):
                        st["sweep_side"] = None
                        return None
                    st["sweep_side"] = None
                    sig = self._fade_signal_after_sweep(
                        symbol, bar_et, "high", H, L, mid, width, "immediate_stop", bars
                    )
                    if sig:
                        st["immediate_block_until_inside"] = True
                    return sig
                if c < L:
                    st["sweep_side"] = "low"
                    if self.require_reentry_close:
                        st["phase"] = "wait_re"
                        return None
                    if st.get("immediate_block_until_inside"):
                        st["sweep_side"] = None
                        return None
                    st["sweep_side"] = None
                    sig = self._fade_signal_after_sweep(
                        symbol, bar_et, "low", H, L, mid, width, "immediate_stop", bars
                    )
                    if sig:
                        st["immediate_block_until_inside"] = True
                    return sig
                return None

            if st["phase"] == "wait_re" and st["sweep_side"] is not None:
                frac = max(0.0, min(0.49, float(getattr(self, "reentry_frac", 0.0) or 0.0)))
                inner_lo = L + frac * width
                inner_hi = H - frac * width
                if not (inner_lo <= c <= inner_hi):
                    return None
                sweep = st["sweep_side"]
                st["sweep_side"] = None
                st["phase"] = "scan"
                return self._fade_signal_after_sweep(
                    symbol, bar_et, sweep, H, L, mid, width, "reentry_confirm", bars
                )

            return None
        except Exception as exc:
            logger.error("morning_range_reversion.analyze error for %s: %s", symbol, exc, exc_info=True)
            return None

    async def execute(self, signal: Dict[str, Any]) -> bool:
        try:
            side = "BUY" if signal["action"] == "LONG" else "SELL"
            qty = max(int(self.config.position_size or 1), 1)
            r0 = abs(float(signal["entry_price"]) - float(signal["stop_loss"]))
            be_thr = (
                float(self.breakeven_trigger_r) * r0
                if self.breakeven_enabled and r0 > 0
                else None
            )
            result = await self.place_bracket_order(
                symbol=signal["symbol"],
                side=side,
                quantity=qty,
                entry_price=signal["entry_price"],
                stop_loss_price=signal["stop_loss"],
                take_profit_price=signal["take_profit"],
                enable_breakeven=False,
                breakeven_profit_threshold=be_thr,
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
            entry_seq = self._entry_bar_seq.get(sym)
            if entry_seq is None:
                continue
            held = self._bar_seq - entry_seq
            if held < self.max_hold_bars:
                continue
            logger.debug(
                "morning_range_reversion timeout-close %s after %d bars (max=%d)",
                sym,
                held,
                self.max_hold_bars,
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
