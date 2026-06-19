"""OHLCV helpers for replay-style backtests (vectorized where possible)."""

from __future__ import annotations

import bisect
import math
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


def ohlcv_index_naive_utc(df: Optional[pd.DataFrame]) -> Optional[pd.DataFrame]:
    """Force a bar DataFrame index to **naive UTC** (canonical bar clock in this repo).

    Databento CSVs, ``parquet_cache.load_ohlcv_cached``, and replay loaders all
    use naive UTC indexes. Broker/API frames may be tz-aware. **Always normalize
    before ``pd.concat``, ``sort_index``, or slicing against naive bounds** — see
    ``docs/GOTCHAS.md`` (Timestamps & time zones).

    Returns a sorted copy; empty/None inputs pass through unchanged.
    """
    if df is None or df.empty:
        return df
    out = df.copy()
    idx = pd.to_datetime(out.index, utc=True)
    out.index = idx.tz_convert(None)
    return out.sort_index()


def snap_trade_unix_to_chart_bar_open(target_unix: int, bar_times: List[int]) -> int:
    """Map a replay fill instant to the chart bar **open** (``time`` on the x-axis).

    ``bar_times`` must be sorted ascending (Unix seconds, one value per candle open).
    For left-labeled bars covering ``[open, next_open)``, the candle that *contains*
    ``target_unix`` is the **last** bar with ``open <= target_unix`` — **not** the
    temporally nearest open. Nearest-neighbor snapping can move a fill onto the next
    minute where price never touched the recorded fill, which makes recap overlays
    look impossible vs OHLC.
    """
    if not bar_times:
        return int(target_unix)
    t = int(target_unix)
    i = bisect.bisect_right(bar_times, t) - 1
    if i < 0:
        return int(bar_times[0])
    return int(bar_times[i])


# Single-bar prints > this many points beyond min(O,C) / max(O,C) are treated as bad ticks
# (stitched feeds, vendor glitches). Clips wicks before replay fills / LWC so TP/SL and
# candles do not react to prices the session never really traded.
DEFAULT_MAX_BODY_WICK_PT = 200.0


def sanitize_ohlcv_ohlc(
    o: float,
    h: float,
    lo: float,
    c: float,
    *,
    max_body_wick_pt: float = DEFAULT_MAX_BODY_WICK_PT,
) -> Tuple[float, float, float, float]:
    """Clip absurd one-bar wicks vs the candle body, then coerce a valid OHLC envelope."""
    o, h, lo, c = float(o), float(h), float(lo), float(c)
    if not all(map(math.isfinite, (o, h, lo, c))):
        return o, h, lo, c
    body_lo = min(o, c)
    body_hi = max(o, c)
    if lo < body_lo - max_body_wick_pt:
        lo = body_lo - max_body_wick_pt
    if h > body_hi + max_body_wick_pt:
        h = body_hi + max_body_wick_pt
    hi = max(o, h, c, lo)
    lo2 = min(o, h, c, lo)
    return o, hi, lo2, c


def sanitize_bar_dict_ohlc(
    bar: Dict[str, Any],
    *,
    max_body_wick_pt: float = DEFAULT_MAX_BODY_WICK_PT,
) -> Dict[str, Any]:
    """Return a shallow copy of ``bar`` with ``open/high/low/close`` sanitized (timestamp/volume unchanged)."""
    o = float(bar.get("open", bar.get("o", 0)) or 0.0)
    h = float(bar.get("high", bar.get("h", 0)) or 0.0)
    lo = float(bar.get("low", bar.get("l", 0)) or 0.0)
    c = float(bar.get("close", bar.get("c", 0)) or 0.0)
    o2, h2, lo2, c2 = sanitize_ohlcv_ohlc(o, h, lo, c, max_body_wick_pt=max_body_wick_pt)
    out = dict(bar)
    out["open"] = o2
    out["high"] = h2
    out["low"] = lo2
    out["close"] = c2
    return out


def sanitize_replay_bars_list(
    bars: List[Dict[str, Any]],
    *,
    max_body_wick_pt: float = DEFAULT_MAX_BODY_WICK_PT,
) -> List[Dict[str, Any]]:
    """Sanitize every bar dict in-place shape-safe list (used by replay + 1m intrabar streams)."""
    return [sanitize_bar_dict_ohlc(b, max_body_wick_pt=max_body_wick_pt) for b in bars]


# --------------------------------------------------------------------------- #
# Dual-contract de-roller
#
# Some vendor CSVs (e.g. Databento NQ/MNQ across a quarterly roll) interleave
# front-month and back-month bars within the same minute timestamps. The two
# tracks differ by the cost-of-carry basis (~150–300 pt for MNQ across one
# quarter) and visually render as **two parallel candle sequences** at the same
# x-positions. Worse, replay fills can falsely trigger on a phantom-contract
# low/high that the contract you are actually trading never touched.
#
# Detection is per-session (day) via **adjacent-bar jumps**: real intraday
# moves are smooth bar-to-bar even on volatile days, while contract
# interleaving causes frequent ±150–300pt minute jumps. If more than
# ``DEFAULT_DEROLL_MIXED_JUMP_PCT`` of bars jump by more than
# ``DEFAULT_DEROLL_JUMP_THRESHOLD_PT`` from the previous bar, the day is a
# roll mix. We then 1-D 2-means cluster the closes and keep the cluster that
# matches the *continuing* contract — picked by walking backward in time so
# the chosen track follows the contract's day-to-day price drift instead of
# anchoring to a far-future absolute price level.
# --------------------------------------------------------------------------- #
DEFAULT_DEROLL_JUMP_THRESHOLD_PT = 100.0
DEFAULT_DEROLL_MIXED_JUMP_PCT = 5.0


def _bar_unix(bar: Dict[str, Any]) -> Optional[int]:
    ts = bar.get("timestamp") or bar.get("time") or bar.get("t")
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        v = float(ts)
        if v > 1e12:
            v /= 1000.0
        return int(v)
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return int(ts.timestamp())
    if isinstance(ts, str):
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return int(dt.timestamp())
        except ValueError:
            return None
    if hasattr(ts, "timestamp"):
        try:
            return int(ts.timestamp())
        except Exception:  # pragma: no cover - defensive
            return None
    return None


def _bar_close(bar: Dict[str, Any]) -> Optional[float]:
    for k in ("close", "c"):
        if k in bar and bar[k] is not None:
            try:
                return float(bar[k])
            except (TypeError, ValueError):
                continue
    return None


def _two_means_1d(values: List[float]) -> Tuple[float, float]:
    """Tiny deterministic 1-D 2-means split. Returns ``(lo_center, hi_center)``."""
    if not values:
        return 0.0, 0.0
    s = sorted(values)
    if len(s) == 1:
        return s[0], s[0]
    # Seed with min and max; 8 iterations is overkill for k=2 in 1-D but cheap.
    lo, hi = s[0], s[-1]
    if hi <= lo:
        return lo, hi
    for _ in range(8):
        thresh = (lo + hi) / 2.0
        below = [v for v in s if v <= thresh]
        above = [v for v in s if v > thresh]
        if not below or not above:
            break
        new_lo = below[len(below) // 2]
        new_hi = above[len(above) // 2]
        if new_lo == lo and new_hi == hi:
            break
        lo, hi = new_lo, new_hi
    return lo, hi


def _bar_session_key(bar: Dict[str, Any]) -> Optional[str]:
    u = _bar_unix(bar)
    if u is None:
        return None
    # UTC date is fine — roll mixing is a per-day signal regardless of zone.
    return datetime.fromtimestamp(int(u), tz=timezone.utc).strftime("%Y-%m-%d")


def _is_session_mixed_by_jumps(
    closes_time_order: List[float],
    *,
    jump_threshold_pts: float,
    mixed_jump_pct: float,
) -> bool:
    """Adjacent-bar jump test for contract interleaving.

    A day where the CSV alternates between two contract months produces frequent
    bar-to-bar gaps of ~the contract basis (≥100pt for MNQ). A high-volatility
    single-contract day moves smoothly bar-to-bar even if its daily range is
    wide. Counting jumps directly is the most reliable separator.
    """
    if len(closes_time_order) < 30:
        return False
    n_jumps = 0
    n_total = len(closes_time_order) - 1
    for i in range(1, len(closes_time_order)):
        if abs(closes_time_order[i] - closes_time_order[i - 1]) > jump_threshold_pts:
            n_jumps += 1
    if n_total <= 0:
        return False
    return (n_jumps / n_total) * 100.0 > mixed_jump_pct


def deroll_dual_contract_bars(
    bars: List[Dict[str, Any]],
    *,
    jump_threshold_pts: float = DEFAULT_DEROLL_JUMP_THRESHOLD_PT,
    mixed_jump_pct: float = DEFAULT_DEROLL_MIXED_JUMP_PCT,
    min_bars_per_session: int = 60,
) -> List[Dict[str, Any]]:
    """Drop bars from the non-continuing contract in dual-contract roll sessions.

    Detection per UTC session day uses adjacent-bar jumps: if more than
    ``mixed_jump_pct`` % of bars jump by more than ``jump_threshold_pts`` from the
    previous bar, the session is a roll mix.

    For mixed sessions, the bars are 1-D 2-means clustered on close and the
    continuing cluster is chosen by walking **backward** in time: each mixed
    session's chosen cluster centre is the one closer to the *next* session's
    chosen centre. This tracks the continuing contract's day-to-day price drift
    (the new front-month is not flat across the roll window) instead of anchoring
    to a far-future absolute price level.

    Clean sessions pass through unchanged. Bar order is preserved.
    """
    if not bars:
        return []

    sessions: Dict[str, List[int]] = {}
    session_order: List[str] = []
    for i, b in enumerate(bars):
        key = _bar_session_key(b)
        if key is None:
            continue
        if key not in sessions:
            session_order.append(key)
            sessions[key] = []
        sessions[key].append(i)

    if not session_order:
        return list(bars)

    sess_lo: Dict[str, float] = {}
    sess_hi: Dict[str, float] = {}
    sess_median: Dict[str, float] = {}
    sess_is_mixed: Dict[str, bool] = {}
    for key in session_order:
        closes_time_order: List[float] = []
        for i in sessions[key]:
            c = _bar_close(bars[i])
            if c is not None:
                closes_time_order.append(c)
        if len(closes_time_order) < min_bars_per_session:
            sess_is_mixed[key] = False
            s_sorted = sorted(closes_time_order)
            sess_median[key] = float(s_sorted[len(s_sorted) // 2]) if s_sorted else 0.0
            sess_lo[key] = sess_hi[key] = sess_median[key]
            continue
        s_sorted = sorted(closes_time_order)
        lo, hi = _two_means_1d(closes_time_order)
        sess_lo[key] = lo
        sess_hi[key] = hi
        sess_median[key] = float(s_sorted[len(s_sorted) // 2])
        sess_is_mixed[key] = _is_session_mixed_by_jumps(
            closes_time_order,
            jump_threshold_pts=jump_threshold_pts,
            mixed_jump_pct=mixed_jump_pct,
        )

    # Walk **backward**: anchor each mixed session to the next session's
    # continuing centre. This is essential because the continuing contract
    # itself drifts day-to-day, so anchoring to a far-future clean session
    # would pick the wrong cluster.
    chosen_center: Dict[str, float] = {}
    for s_i in range(len(session_order) - 1, -1, -1):
        key = session_order[s_i]
        if not sess_is_mixed.get(key):
            chosen_center[key] = sess_median[key]
            continue
        # Anchor = the next session's chosen centre, or its median if not yet
        # resolved (shouldn't happen given backward walk, but be safe).
        anchor: Optional[float] = None
        if s_i + 1 < len(session_order):
            nkey = session_order[s_i + 1]
            anchor = chosen_center.get(nkey, sess_median.get(nkey))
        if anchor is None:
            # No forward neighbour — fall back to the lo cluster arbitrarily
            # (rare; only happens if every following session is mixed).
            chosen_center[key] = sess_lo[key]
            continue
        lo_c = sess_lo[key]
        hi_c = sess_hi[key]
        chosen_center[key] = hi_c if abs(hi_c - anchor) <= abs(lo_c - anchor) else lo_c

    drop_idx: set = set()
    for key in session_order:
        if not sess_is_mixed.get(key):
            continue
        lo_c = sess_lo[key]
        hi_c = sess_hi[key]
        cc = chosen_center[key]
        keep_hi = abs(hi_c - cc) <= abs(lo_c - cc)
        thresh = (lo_c + hi_c) / 2.0
        for i in sessions[key]:
            c = _bar_close(bars[i])
            if c is None:
                continue
            on_hi = c > thresh
            if on_hi != keep_hi:
                drop_idx.add(i)

    if drop_idx:
        bars = [b for i, b in enumerate(bars) if i not in drop_idx]

    # Second pass: isolated single-bar phantom that survived day-level filtering
    # (e.g. one off-track bar on a day that didn't meet the 5% jump threshold).
    # Drop a bar when it jumps > jump_threshold_pts from BOTH adjacent bars while
    # the two adjacent bars themselves remain close together.
    return _drop_isolated_outlier_bars(bars, jump_threshold_pts=jump_threshold_pts)


def _drop_isolated_outlier_bars(
    bars: List[Dict[str, Any]],
    *,
    jump_threshold_pts: float,
) -> List[Dict[str, Any]]:
    """Drop bars whose close jumps > ``jump_threshold_pts`` away from BOTH neighbours
    while the neighbours themselves are within half that threshold of each other.

    Catches the single-bar phantom prints that escape the per-day ratio test.
    """
    if len(bars) < 3:
        return list(bars)
    closes = [_bar_close(b) for b in bars]
    keep = [True] * len(bars)
    half = jump_threshold_pts / 2.0
    for i in range(1, len(bars) - 1):
        c_prev = closes[i - 1]
        c_curr = closes[i]
        c_next = closes[i + 1]
        if c_prev is None or c_curr is None or c_next is None:
            continue
        in_dev = abs(c_curr - c_prev)
        out_dev = abs(c_next - c_curr)
        neigh_gap = abs(c_next - c_prev)
        if in_dev > jump_threshold_pts and out_dev > jump_threshold_pts and neigh_gap < half:
            keep[i] = False
    return [b for b, k in zip(bars, keep) if k]


# (canonical name, aliases in lowercase for header match)
_OHLCV_ALIASES: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("open", ("open", "o")),
    ("high", ("high", "h")),
    ("low", ("low", "l")),
    ("close", ("close", "c")),
    ("volume", ("volume", "v")),
)


def _resolve_ohlcv_columns(df: pd.DataFrame) -> Dict[str, str]:
    """Map canonical OHLCV keys to actual column names (case-insensitive)."""
    lower: Dict[str, str] = {}
    for c in df.columns:
        key = str(c).strip().lower()
        if key not in lower:
            lower[key] = str(c)
    out: Dict[str, str] = {}
    for canonical, aliases in _OHLCV_ALIASES:
        for a in aliases:
            if a in lower:
                out[canonical] = lower[a]
                break
    return out


def dataframe_to_chart_bars_unix(
    df: pd.DataFrame,
    *,
    deroll: bool = True,
) -> Tuple[List[Dict[str, Any]], List[int]]:
    """Indexed OHLCV DataFrame → LWC-style bar dicts (unix ``timestamp``) + unix times.

    Column names are matched **case-insensitively** so broker CSVs (``Open``, …) and
    canonical files (``open``, …) both work. Used by replay / walkforward chart scripts.

    When ``deroll`` is ``True`` (default), per-day dual-contract interleaving is removed
    via :func:`deroll_dual_contract_bars` so charts don't show two parallel candle
    sequences across a futures roll.
    """
    if df is None or len(df) == 0:
        return [], []
    cmap = _resolve_ohlcv_columns(df)
    for req in ("open", "high", "low", "close"):
        if req not in cmap:
            raise ValueError(
                "dataframe_to_chart_bars_unix: missing required column "
                f"{req!r} (columns={list(df.columns)!r})"
            )
    vol_col = cmap.get("volume")
    idx = df.index
    o = df[cmap["open"]].to_numpy(dtype=float, copy=False)
    h = df[cmap["high"]].to_numpy(dtype=float, copy=False)
    lo = df[cmap["low"]].to_numpy(dtype=float, copy=False)
    c = df[cmap["close"]].to_numpy(dtype=float, copy=False)
    v = df[vol_col].to_numpy(dtype=float, copy=False) if vol_col else None

    # Same Unix bar time more than once (stitched feeds, bad merges) makes LWC draw
    # overlapping candles ("double vision"). Last row wins per timestamp.
    by_u: Dict[int, Dict[str, Any]] = {}
    n = len(df)
    for i in range(n):
        ts = pd.Timestamp(idx[i])
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        else:
            ts = ts.tz_convert("UTC")
        u = int(ts.timestamp())
        ivol = 0
        if v is not None:
            vol = float(v[i])
            ivol = 0 if math.isnan(vol) else int(vol)
        o2, h2, lo2, c2 = sanitize_ohlcv_ohlc(float(o[i]), float(h[i]), float(lo[i]), float(c[i]))
        by_u[u] = {
            "timestamp": u,
            "open": o2,
            "high": h2,
            "low": lo2,
            "close": c2,
            "volume": ivol,
        }
    ordered = sorted(by_u.keys())
    rows = [by_u[k] for k in ordered]
    if deroll:
        rows = deroll_dual_contract_bars(rows)
    times = [int(r["timestamp"]) for r in rows]
    return rows, times


def replay_bars_from_ohlcv_df(
    data: pd.DataFrame,
    *,
    deroll: bool = True,
) -> List[Dict[str, Any]]:
    """
    Convert an indexed OHLCV DataFrame to the list[dict] shape ``StrategyReplayEngine`` expects.

    Tier 4 hot-path: was profiled at 451 ms (43 % of a 3-month replay) due to
    per-bar ``pd.Timestamp(idx[i]).to_pydatetime()`` calls. The fix is to
    materialize all Python ``datetime`` objects once via ``idx.to_pydatetime()``
    (a vectorized C call that returns a NumPy object array) and vectorize the
    OHLC + volume sanitization. Output is byte-identical to the legacy
    per-bar path — regression-pinned in
    ``tests/test_backtest_fast_loop_parity.py``.

    When ``deroll`` is ``True`` (default), per-day dual-contract interleaving is
    removed via :func:`deroll_dual_contract_bars` so the strategy doesn't fill
    on prices the contract it would actually trade never touched.
    """
    if data is None or len(data) == 0:
        return []
    n = len(data)
    idx = data.index

    # Vectorized timestamp materialization. ``DatetimeIndex.to_pydatetime``
    # returns a numpy object array of native ``datetime`` instances in a
    # single C call — replaces 17 k × ``pd.Timestamp(...).to_pydatetime()``
    # round-trips with one bulk extraction. Fallback handles the rare case
    # where the index isn't a DatetimeIndex (e.g. test fixtures).
    to_pydatetime = getattr(idx, "to_pydatetime", None)
    if callable(to_pydatetime):
        ts_arr = to_pydatetime()
    else:
        ts_arr = np.asarray([pd.Timestamp(t).to_pydatetime() for t in idx], dtype=object)

    # Pull OHLCV columns as NumPy arrays once. ``copy=False`` keeps the view
    # if the underlying dtype already matches.
    o = data["open"].to_numpy(dtype=np.float64, copy=False)
    h = data["high"].to_numpy(dtype=np.float64, copy=False)
    lo = data["low"].to_numpy(dtype=np.float64, copy=False)
    c = data["close"].to_numpy(dtype=np.float64, copy=False)
    v_raw = data["volume"].to_numpy(copy=False)

    # Vectorized OHLC sanitization mirrors ``sanitize_ohlcv_ohlc``: clip wicks
    # against ``DEFAULT_MAX_BODY_WICK_PT``, then coerce a valid OHLC envelope
    # (high = max-of-four, low = min-of-four). NaN rows are returned as-is so
    # downstream code sees the same nan propagation it did before.
    finite_mask = np.isfinite(o) & np.isfinite(h) & np.isfinite(lo) & np.isfinite(c)
    body_lo = np.minimum(o, c)
    body_hi = np.maximum(o, c)
    h_clipped = np.where(
        finite_mask & (h > body_hi + DEFAULT_MAX_BODY_WICK_PT),
        body_hi + DEFAULT_MAX_BODY_WICK_PT,
        h,
    )
    lo_clipped = np.where(
        finite_mask & (lo < body_lo - DEFAULT_MAX_BODY_WICK_PT),
        body_lo - DEFAULT_MAX_BODY_WICK_PT,
        lo,
    )
    # After clipping, the OHLC envelope is enforced via element-wise max/min
    # across all four legs (matching the original per-bar code).
    hi_final = np.where(
        finite_mask,
        np.maximum(np.maximum(o, h_clipped), np.maximum(c, lo_clipped)),
        h_clipped,
    )
    lo_final = np.where(
        finite_mask,
        np.minimum(np.minimum(o, h_clipped), np.minimum(c, lo_clipped)),
        lo_clipped,
    )

    # Volume → int with NaN → 0 (matching the legacy ``int(...)`` cast).
    v_float = v_raw.astype(np.float64, copy=False)
    v_int = np.where(np.isnan(v_float), 0, v_float).astype(np.int64, copy=False)

    # Materialize as Python floats once per column to match the legacy
    # ``float(o[i])`` casts (strategies sometimes type-check via ``isinstance``).
    o_list = o.tolist()
    c_list = c.tolist()
    hi_list = hi_final.tolist()
    lo_list = lo_final.tolist()
    v_list = v_int.tolist()
    ts_list = ts_arr.tolist() if hasattr(ts_arr, "tolist") else list(ts_arr)

    out: List[Dict[str, Any]] = [
        {
            "timestamp": ts_list[i],
            "open": o_list[i],
            "high": hi_list[i],
            "low": lo_list[i],
            "close": c_list[i],
            "volume": v_list[i],
        }
        for i in range(n)
    ]
    if deroll:
        out = deroll_dual_contract_bars(out)
    return out
