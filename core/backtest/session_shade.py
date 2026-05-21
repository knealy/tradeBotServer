"""Precompute LWC-friendly overnight session range boxes (ET) from bar lists.

Used by ``gui.chart_html.generate_chart_html`` when shading ``overnight_range``
trade recaps. Session **start/end** matches ``strategies/overnight_range_strategy.py``
``track_overnight_range`` (same calendar rules and inclusive end minute).

When ``reference_unix`` is set (typically the trade **entry** time), H/L is taken only
from bars inside that **full** overnight window so the box aligns with the range the
strategy used — not from a truncated visible slice missing the prior evening.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd


def parse_hh_mm(s: str) -> Tuple[int, int]:
    parts = str(s).strip().split(":")
    h = int(parts[0])
    m = int(parts[1]) if len(parts) > 1 else 0
    return h, m


def load_overnight_range_timing_from_toml(
    toml_path: Optional[str] = None,
) -> Tuple[str, str, str]:
    """Return ``(overnight_start, overnight_end, session_timezone)`` from TOML."""
    import tomllib
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    path = Path(toml_path) if toml_path else root / "config" / "strategies" / "overnight_range.toml"
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    timing = data.get("timing") or {}
    start = str(timing.get("overnight_start", "18:00"))
    end = str(timing.get("overnight_end", "09:29"))
    zone = str(timing.get("session_timezone", "US/Eastern")).replace("US/Eastern", "America/New_York")
    return start, end, zone


def overnight_session_start_end_unix(
    reference_unix: int,
    overnight_start: str,
    overnight_end: str,
    zone: str,
) -> Tuple[int, int]:
    """
    Same overnight window as ``OvernightRangeStrategy.track_overnight_range``:
    returns ``(start_unix, end_unix)`` inclusive of the end minute (UTC seconds).
    """
    zi = ZoneInfo(zone)
    now = datetime.fromtimestamp(reference_unix, tz=timezone.utc).astimezone(zi)
    start_hour, start_min = parse_hh_mm(overnight_start)
    end_hour, end_min = parse_hh_mm(overnight_end)
    start_clock = time(start_hour, start_min)
    end_clock = time(end_hour, end_min)
    t = now.time()
    if end_clock <= start_clock:
        if t >= start_clock:
            start_date = now.date()
            end_date = start_date + timedelta(days=1)
        elif t >= end_clock:
            end_date = now.date()
            start_date = end_date - timedelta(days=1)
        else:
            end_date = now.date()
            start_date = end_date - timedelta(days=1)
    else:
        if t >= end_clock:
            end_date = now.date()
            start_date = end_date
        else:
            end_date = (now - timedelta(days=1)).date()
            start_date = end_date

    start_local = datetime.combine(start_date, start_clock, tzinfo=zi)
    end_local = datetime.combine(end_date, end_clock, tzinfo=zi) + timedelta(minutes=1) - timedelta(microseconds=1)
    if start_local >= end_local:
        if end_clock > start_clock:
            start_local = datetime.combine(end_date, start_clock, tzinfo=zi)
        else:
            start_local = datetime.combine(end_date - timedelta(days=1), start_clock, tzinfo=zi)

    su = int(start_local.astimezone(timezone.utc).timestamp())
    eu = int(end_local.astimezone(timezone.utc).timestamp())
    return su, eu


def overnight_recap_df_slice_bounds(
    entry_utc: datetime,
    exit_utc: datetime,
    pad: timedelta,
    overnight_start: str,
    overnight_end: str,
    zone: str,
) -> Tuple[pd.Timestamp, pd.Timestamp]:
    """
    Expand a ``[entry-pad, exit+pad]`` OHLC slice so it always includes the full
    overnight session window used for the range (UTC timestamps for tz-aware CSV indexes).
    """
    ref = int(pd.Timestamp(entry_utc).timestamp())
    su, eu = overnight_session_start_end_unix(ref, overnight_start, overnight_end, zone)
    ses_lo = pd.Timestamp(su, unit="s", tz="UTC")
    ses_hi = pd.Timestamp(eu, unit="s", tz="UTC")
    pad_lo = pd.Timestamp(entry_utc).tz_convert("UTC") - pad
    pad_hi = pd.Timestamp(exit_utc).tz_convert("UTC") + pad
    return min(pad_lo, ses_lo), max(pad_hi, ses_hi)


def overnight_range_baseline_segments(
    chart_bars: List[Dict[str, Any]],
    *,
    overnight_start: str = "19:00",
    overnight_end: str = "9:29",
    zone: str = "America/New_York",
    reference_unix: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """
    Baseline segments for LWC shading.

    If ``reference_unix`` is set (trade entry bar time), only bars with
    ``start_unix <= bar <= end_unix`` for that strategy session contribute to H/L
    (matches live/replay range tracking). Optional second segment only on large gaps
    in the source data.

    If ``reference_unix`` is None, falls back to grouping by ET anchor day (legacy;
    can mis-shade when the chart slice omits the evening leg).
    """
    if not chart_bars:
        return []

    times = [int(b["time"]) for b in chart_bars]
    highs = [float(b.get("high", 0)) for b in chart_bars]
    lows = [float(b.get("low", 0)) for b in chart_bars]

    if reference_unix is not None:
        su, eu = overnight_session_start_end_unix(
            reference_unix, overnight_start, overnight_end, zone
        )
        idxs = [i for i, t in enumerate(times) if su <= t <= eu]
        if not idxs:
            return []
        hi = max(highs[i] for i in idxs)
        lo = min(lows[i] for i in idxs)
        if not (hi > lo):
            return []
        out: List[Dict[str, Any]] = []
        chunk: List[int] = []
        max_gap = 2 * 3600
        for i in sorted(idxs):
            if not chunk:
                chunk = [i]
                continue
            if times[i] - times[chunk[-1]] > max_gap:
                out.append(_segment_dict(times, chunk, hi, lo))
                chunk = [i]
            else:
                chunk.append(i)
        if chunk:
            out.append(_segment_dict(times, chunk, hi, lo))
        return out

    sh, sm = parse_hh_mm(overnight_start)
    eh, em = parse_hh_mm(overnight_end)
    start_min = sh * 60 + sm
    end_min = eh * 60 + em

    idx = pd.to_datetime(times, unit="s", utc=True).tz_convert(zone)
    mins = (idx.hour * 60 + idx.minute).to_numpy(dtype=int)
    day = np.array([pd.Timestamp(ts).date() for ts in idx], dtype=object)

    evening_b = mins >= start_min
    morning_b = mins <= end_min
    in_sess = evening_b | morning_b

    anchors: List[Optional[date]] = []
    phases: List[str] = []
    for i in range(len(idx)):
        if not in_sess[i]:
            anchors.append(None)
            phases.append("none")
            continue
        d = day[i]
        if evening_b[i]:
            anchors.append(d + timedelta(days=1))
            phases.append("eve")
        elif morning_b[i]:
            anchors.append(d)
            phases.append("mor")
        else:
            anchors.append(None)
            phases.append("none")

    out_legacy: List[Dict[str, Any]] = []
    for anchor in sorted({a for a in anchors if a is not None}):
        idx_anchor = [i for i, a in enumerate(anchors) if a == anchor]
        if not idx_anchor:
            continue
        hi = max(highs[i] for i in idx_anchor)
        lo = min(lows[i] for i in idx_anchor)
        if not (hi > lo):
            continue
        for phase in ("eve", "mor"):
            ix = sorted(i for i in idx_anchor if phases[i] == phase)
            if not ix:
                continue
            chunk = []
            max_gap = 2 * 3600
            for i in ix:
                if not chunk:
                    chunk = [i]
                    continue
                prev_t = times[chunk[-1]]
                if times[i] - prev_t > max_gap:
                    out_legacy.append(_segment_dict(times, chunk, hi, lo))
                    chunk = [i]
                else:
                    chunk.append(i)
            if chunk:
                out_legacy.append(_segment_dict(times, chunk, hi, lo))
    return out_legacy


def _segment_dict(times: List[int], chunk: List[int], hi: float, lo: float) -> Dict[str, Any]:
    seg_times = sorted(times[i] for i in chunk)
    return {"hi": hi, "lo": lo, "times": seg_times}


def segments_to_jsonable(segments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Stable JSON for embedding in HTML (times sorted)."""
    return [{"hi": s["hi"], "lo": s["lo"], "times": list(s["times"])} for s in segments]
