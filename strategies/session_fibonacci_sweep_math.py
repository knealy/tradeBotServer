"""
Session Fibonacci Sweep — pure math (bot-port ready).

Mirrors the Pine indicator level / zone / fade-setup logic with no I/O.
Keep ratios and zone names in sync with strategies/session_fibonacci_sweep.pine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Literal, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

# -----------------------------------------------------------------------------
# Shared constants (must match Pine)
# -----------------------------------------------------------------------------

FIB_RATIOS: Tuple[float, ...] = (
    0.236,
    0.2795,
    0.5,
    0.559,
    0.8365,
    1.0,
    1.382,
    1.5,
    1.618,
)

# name -> (lo_ratio, hi_ratio)
ZONES: Dict[str, Tuple[float, float]] = {
    "inner": (0.236, 0.2795),
    "mid": (0.5, 0.559),
    "full": (0.8365, 1.0),
    "extension": (1.382, 1.618),
}

# Midline ratio inside each zone (matches Pine dotted midlines)
ZONE_MIDLINES: Dict[str, float] = {
    "inner": (0.236 + 0.2795) / 2.0,  # 0.25775
    "mid": (0.5 + 0.559) / 2.0,  # 0.5295
    "full": (0.8365 + 1.0) / 2.0,  # 0.91825
    "extension": 1.5,
}
ZONE_ORDER: Tuple[str, ...] = ("inner", "mid", "full", "extension")

# Default session clocks in America/New_York (same as Pine defaults)
DEFAULT_SESSIONS: Dict[str, Tuple[str, str]] = {
    "Tokyo": ("18:30", "00:00"),
    "London": ("01:30", "05:00"),
    "NY AM": ("08:00", "11:00"),
    "NY PM": ("13:00", "16:00"),
}

DEFAULT_TIMEZONE = "America/New_York"
DEFAULT_ATR_LEN = 2
DEFAULT_ZONE_NAMES: Tuple[str, ...] = ("inner", "mid", "extension")

Side = Literal["up", "down"]
FadeSide = Literal["short", "long"]  # fade up-sweep => short; fade down-sweep => long


@dataclass(frozen=True)
class LevelBook:
    """Projected Fibonacci prices around an anchor (fib 0)."""

    anchor: float
    distance: float
    up: Dict[float, float]
    down: Dict[float, float]

    def price(self, ratio: float, side: Side) -> float:
        table = self.up if side == "up" else self.down
        if ratio not in table:
            raise KeyError(f"ratio {ratio} not in level book")
        return table[ratio]


@dataclass(frozen=True)
class ZoneBand:
    name: str
    lo_ratio: float
    hi_ratio: float
    lo: float
    hi: float
    mid: float


@dataclass(frozen=True)
class FadeSetup:
    """Fade a liquidity sweep of a fib zone."""

    swept_side: Side
    fade: FadeSide
    zone: str
    entry: float
    stop: float
    tp1: float  # fib 0 (anchor)
    tp2: float  # opposite same-zone mid
    tp3: float  # opposite next-wider zone mid (or extension mid)
    stop_buffer: float


def project_levels(
    anchor: float,
    distance: float,
    ratios: Sequence[float] = FIB_RATIOS,
) -> LevelBook:
    """Place fib 0 at ``anchor`` and project ``ratios * distance`` both sides."""
    if distance is None or distance <= 0:
        raise ValueError("distance must be positive")
    up = {r: anchor + r * distance for r in ratios}
    down = {r: anchor - r * distance for r in ratios}
    return LevelBook(anchor=anchor, distance=distance, up=up, down=down)


def zone_band(anchor: float, distance: float, name: str, side: Side) -> ZoneBand:
    """Absolute price band for one named zone on one side of the anchor."""
    if name not in ZONES:
        raise KeyError(f"unknown zone {name!r}; expected one of {list(ZONES)}")
    lo_r, hi_r = ZONES[name]
    if side == "up":
        lo = anchor + lo_r * distance
        hi = anchor + hi_r * distance
    else:
        # down side: hi is closer to anchor (less negative offset)
        hi = anchor - lo_r * distance
        lo = anchor - hi_r * distance
    return ZoneBand(
        name=name,
        lo_ratio=lo_r,
        hi_ratio=hi_r,
        lo=lo,
        hi=hi,
        mid=(lo + hi) / 2.0,
    )


def all_zone_bands(anchor: float, distance: float) -> Dict[str, Dict[Side, ZoneBand]]:
    """All named zones on both sides."""
    out: Dict[str, Dict[Side, ZoneBand]] = {}
    for name in ZONE_ORDER:
        out[name] = {
            "up": zone_band(anchor, distance, name, "up"),
            "down": zone_band(anchor, distance, name, "down"),
        }
    return out


def avg_like_session_ranges(ranges: Sequence[float], n: int) -> Optional[float]:
    """SMA of the last N like-session H–L ranges (Pine ATR mode)."""
    if n < 1 or not ranges:
        return None
    take = list(ranges)[-n:]
    if not take:
        return None
    return sum(take) / float(len(take))


def resolve_distance(
    last_range: Optional[float],
    ranges: Sequence[float],
    mode: Literal["previous", "atr"] = "atr",
    atr_len: int = DEFAULT_ATR_LEN,
    min_tick: float = 0.0,
) -> Optional[float]:
    """
    Resolve projection distance.

    mode ``previous`` → last completed like-session range
    mode ``atr`` → SMA of last ``atr_len`` like-session ranges
    """
    if mode == "atr":
        d = avg_like_session_ranges(ranges, atr_len)
    else:
        d = last_range
    if d is None or d <= min_tick:
        return None
    return float(d)


def next_wider_zone(zone: str) -> str:
    """Next wider zone name; extension stays extension."""
    try:
        idx = ZONE_ORDER.index(zone)
    except ValueError as exc:
        raise KeyError(zone) from exc
    if idx >= len(ZONE_ORDER) - 1:
        return ZONE_ORDER[-1]
    return ZONE_ORDER[idx + 1]


def price_touches_zone(high: float, low: float, band: ZoneBand) -> bool:
    """True if bar range overlaps the zone band."""
    return low <= band.hi and high >= band.lo


def sweep_pierced(
    high: float,
    low: float,
    band: ZoneBand,
    side: Side,
    full_pierce: bool = True,
) -> bool:
    """True if the wick pierced the zone on ``side``."""
    if side == "up":
        return high >= (band.hi if full_pierce else band.lo)
    return low <= (band.lo if full_pierce else band.hi)


def sweep_confirmed(
    high: float,
    low: float,
    close: float,
    band: ZoneBand,
    side: Side,
    full_pierce: bool = True,
) -> bool:
    """
    Wick pierces the zone, then close returns toward the anchor (fib 0).

    Up: pierce high side, close back below near edge (band.lo).
    Down: pierce low side, close back above near edge (band.hi).
    """
    if not sweep_pierced(high, low, band, side, full_pierce=full_pierce):
        return False
    if side == "up":
        return close < band.lo
    return close > band.hi


def first_swept_zone(
    high: float,
    low: float,
    anchor: float,
    distance: float,
    prefer: Sequence[str] = ZONE_ORDER,
) -> Optional[Tuple[Side, str]]:
    """
    First zone touched this bar, preferring inner → extension.

    Returns (side, zone_name) or None.
    If both sides touch, the side whose closest touched zone is nearer the
    anchor wins; ties break toward ``up``.
    """
    candidates: List[Tuple[float, Side, str]] = []
    for name in prefer:
        for side in ("up", "down"):
            band = zone_band(anchor, distance, name, side)  # type: ignore[arg-type]
            if price_touches_zone(high, low, band):
                # distance from anchor to near edge
                near = abs(band.lo - anchor) if side == "up" else abs(band.hi - anchor)
                candidates.append((near, side, name))  # type: ignore[arg-type]
    if not candidates:
        return None
    candidates.sort(key=lambda t: (t[0], 0 if t[1] == "up" else 1))
    _, side, name = candidates[0]
    return side, name  # type: ignore[return-value]


def build_fade_setup(
    anchor: float,
    distance: float,
    swept_side: Side,
    zone: str,
    stop_buffer_ratio: float = 0.15,
    entry_at: Literal["mid", "near", "far"] = "mid",
) -> FadeSetup:
    """
    Build fade levels after a zone sweep.

    Entry: inside the swept zone (mid / near / far edge from anchor).
    Stop: beyond the far edge + ``stop_buffer_ratio * distance``.
    TP1: fib 0 (anchor).
    TP2: opposite same-zone mid.
    TP3: opposite next-wider zone mid.
    """
    swept = zone_band(anchor, distance, zone, swept_side)
    opp_side: Side = "down" if swept_side == "up" else "up"
    opp = zone_band(anchor, distance, zone, opp_side)
    wider = zone_band(anchor, distance, next_wider_zone(zone), opp_side)
    buf = max(0.0, stop_buffer_ratio) * distance

    if entry_at == "near":
        entry = swept.lo if swept_side == "up" else swept.hi
    elif entry_at == "far":
        entry = swept.hi if swept_side == "up" else swept.lo
    else:
        entry = swept.mid

    if swept_side == "up":
        stop = swept.hi + buf
        fade: FadeSide = "short"
    else:
        stop = swept.lo - buf
        fade = "long"

    return FadeSetup(
        swept_side=swept_side,
        fade=fade,
        zone=zone,
        entry=entry,
        stop=stop,
        tp1=anchor,
        tp2=opp.mid,
        tp3=wider.mid,
        stop_buffer=buf,
    )


def ib_anchor_ready(
    session_start_ms: int,
    bar_time_ms: int,
    prev_bar_time_ms: Optional[int],
    ib_minutes: int,
) -> bool:
    """
    True on the first bar whose time is at/after session_start + IB minutes.

    Matches Pine: print when ``time >= startTime + ibMs`` and it was not yet so.
    """
    ib_ms = ib_minutes * 60 * 1000
    end = session_start_ms + ib_ms
    if bar_time_ms < end:
        return False
    if prev_bar_time_ms is None:
        return True
    return prev_bar_time_ms < end


def detect_fvg(
    high: float,
    low: float,
    high_2: float,
    low_2: float,
) -> Optional[Tuple[Side, float, float]]:
    """
    Classic 3-candle FVG on the current bar vs bar[2].

    Returns (side, top, bottom) where side ``up`` = bullish FVG, ``down`` = bearish.
    If both form (rare), prefer bullish.
    """
    if low > high_2:
        return ("up", low, high_2)
    if high < low_2:
        return ("down", low_2, high)
    return None


def fvg_inverted(
    close: float,
    top: float,
    bottom: float,
    fvg_side: Side,
) -> bool:
    """True when close trades through an FVG against its original bias."""
    if fvg_side == "up":
        return close < bottom
    return close > top


def ifvg_dir_after_invert(fvg_side: Side) -> Side:
    """Bullish FVG inverted → bearish IFVG (resistance); opposite for bearish FVG."""
    return "down" if fvg_side == "up" else "up"


def overlaps_band(a_lo: float, a_hi: float, b_lo: float, b_hi: float) -> bool:
    return min(a_hi, b_hi) >= max(a_lo, b_lo)


def sweep_ifvg_confirmed(
    high: float,
    low: float,
    close: float,
    band: ZoneBand,
    side: Side,
    ifvg_top: float,
    ifvg_bottom: float,
    ifvg_side: Side,
    full_pierce: bool = True,
) -> bool:
    """
    Sweep confirmation plus overlapping IFVG in the fade direction.

    Up-sweep fade (short) wants bearish IFVG (``down``).
    Down-sweep fade (long) wants bullish IFVG (``up``).
    """
    if not sweep_confirmed(high, low, close, band, side, full_pierce=full_pierce):
        return False
    want: Side = "down" if side == "up" else "up"
    if ifvg_side != want:
        return False
    return overlaps_band(ifvg_bottom, ifvg_top, band.lo, band.hi)


def levels_as_dict(book: LevelBook) -> Dict[str, Dict[str, float]]:
    """JSON-friendly dump of projected levels."""
    return {
        "anchor": book.anchor,
        "distance": book.distance,
        "up": {str(k): v for k, v in book.up.items()},
        "down": {str(k): v for k, v in book.down.items()},
    }


# -----------------------------------------------------------------------------
# Chart overlay builder (master GUI /api/chart/session_fib_sweep)
# -----------------------------------------------------------------------------

def parse_hhmm_to_minutes(raw: str) -> int:
    """Parse ``HH:MM`` or ``HHMM`` into minutes-from-midnight."""
    s = str(raw or "").strip()
    if not s:
        raise ValueError("empty time")
    if ":" in s:
        parts = s.split(":")
        return int(parts[0]) * 60 + int(parts[1])
    if len(s) == 4 and s.isdigit():
        return int(s[:2]) * 60 + int(s[2:])
    raise ValueError(f"bad time {raw!r}")


def minutes_in_session(mins: int, start_hm: str, end_hm: str) -> bool:
    """True if ``mins`` (0–1439) falls in ``[start, end)`` (handles midnight wrap)."""
    start = parse_hhmm_to_minutes(start_hm)
    end = parse_hhmm_to_minutes(end_hm)
    mins = int(mins) % 1440
    if start == end:
        return True
    if start < end:
        return start <= mins < end
    return mins >= start or mins < end


def session_duration_minutes(start_hm: str, end_hm: str) -> int:
    start = parse_hhmm_to_minutes(start_hm)
    end = parse_hhmm_to_minutes(end_hm)
    if start < end:
        return end - start
    return (1440 - start) + end


def _bar_unix_sec(bar: Dict[str, Any]) -> int:
    t = bar.get("time")
    if t is None:
        raise KeyError("bar missing time")
    n = float(t)
    if n > 1e12:
        n = n / 1000.0
    return int(n)


@dataclass
class _SessionTracker:
    name: str
    start_hm: str
    end_hm: str
    ranges: List[float] = field(default_factory=list)
    zone_names: Tuple[str, ...] = DEFAULT_ZONE_NAMES
    was_in: bool = False
    saw_start: bool = False
    grid_started: bool = False
    start_sec: Optional[int] = None
    sess_open: Optional[float] = None
    run_high: Optional[float] = None
    run_low: Optional[float] = None
    active: Optional[Dict[str, Any]] = None


def normalize_zone_names(raw: Optional[Iterable[str]]) -> Tuple[str, ...]:
    """Map aliases (ext/in/md/fl) onto ZONE_ORDER names; drop unknowns."""
    aliases = {"ext": "extension", "in": "inner", "md": "mid", "fl": "full"}
    out: List[str] = []
    seen = set()
    for item in raw or ():
        key = str(item).strip().lower()
        key = aliases.get(key, key)
        if key in ZONES and key not in seen:
            seen.add(key)
            out.append(key)
    return tuple(out) if out else DEFAULT_ZONE_NAMES


def _zone_payload(anchor: float, distance: float, zone_names: Sequence[str]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for name in zone_names:
        if name not in ZONES:
            continue
        for side in ("up", "down"):
            band = zone_band(anchor, distance, name, side)  # type: ignore[arg-type]
            out.append(
                {
                    "name": name,
                    "side": side,
                    "lo": band.lo,
                    "hi": band.hi,
                    "mid": band.mid,
                }
            )
    return out


def _emit_grid(
    tracker: _SessionTracker,
    *,
    grid_start_sec: int,
    anchor: float,
    distance: float,
    zone_names: Sequence[str],
    grid_width_frac: float,
    grid_end_trim_sec: int,
) -> Optional[Dict[str, Any]]:
    if tracker.start_sec is None:
        return None
    duration_sec = session_duration_minutes(tracker.start_hm, tracker.end_hm) * 60
    session_end_sec = tracker.start_sec + duration_sec
    span = max(1, session_end_sec - grid_start_sec)
    scaled = grid_start_sec + max(1, int(span * grid_width_frac))
    grid_end_sec = max(grid_start_sec, scaled - max(0, grid_end_trim_sec))
    return {
        "name": tracker.name,
        "anchor": float(anchor),
        "distance": float(distance),
        "session_start": int(tracker.start_sec),
        "session_end": int(session_end_sec),
        "grid_start": int(grid_start_sec),
        "grid_end": int(grid_end_sec),
        "zones": _zone_payload(anchor, distance, zone_names),
    }


def build_session_fib_overlays(
    bars: Sequence[Dict[str, Any]],
    *,
    atr_len: int = DEFAULT_ATR_LEN,
    ib_minutes: int = 30,
    delay_until_ib: bool = True,
    range_mode: Literal["atr", "previous"] = "atr",
    zone_names: Sequence[str] = DEFAULT_ZONE_NAMES,
    session_zones: Optional[Dict[str, Sequence[str]]] = None,
    max_sessions: int = 8,
    timezone_name: str = DEFAULT_TIMEZONE,
    sessions: Optional[Dict[str, Tuple[str, str]]] = None,
    grid_width_frac: float = 0.85,
    grid_end_trim_sec: int = 180,
    min_tick: float = 0.0,
) -> List[Dict[str, Any]]:
    """
    Walk OHLCV bars and emit Session Fibonacci Sweep grids (Pine-parity).

    Each bar dict needs ``time`` (unix sec or ms), ``open``, ``high``, ``low``,
    ``close``. Returns newest-first list capped at ``max_sessions``.
    """
    if not bars:
        return []
    tz = ZoneInfo(timezone_name or DEFAULT_TIMEZONE)
    sess_map = sessions or DEFAULT_SESSIONS
    default_zones = normalize_zone_names(zone_names)
    per_sess = session_zones or {}
    trackers = [
        _SessionTracker(
            name=n,
            start_hm=w[0],
            end_hm=w[1],
            zone_names=normalize_zone_names(per_sess.get(n, default_zones)),
        )
        for n, w in sess_map.items()
    ]
    completed: List[Dict[str, Any]] = []
    prev_sec: Optional[int] = None
    prev_close: Optional[float] = None

    mode: Literal["atr", "previous"] = "atr" if range_mode == "atr" else "previous"

    for bar in bars:
        try:
            t_sec = _bar_unix_sec(bar)
            o = float(bar["open"])
            h = float(bar["high"])
            l = float(bar["low"])
            c = float(bar["close"])
        except (KeyError, TypeError, ValueError):
            continue
        local = datetime.fromtimestamp(t_sec, tz=timezone.utc).astimezone(tz)
        mins = local.hour * 60 + local.minute

        for tr in trackers:
            in_now = minutes_in_session(mins, tr.start_hm, tr.end_hm)
            if in_now:
                if not tr.was_in:
                    tr.sess_open = o
                    tr.start_sec = t_sec
                    tr.run_high = h
                    tr.run_low = l
                    tr.saw_start = prev_sec is not None
                    tr.grid_started = False
                    tr.active = None
                else:
                    if tr.run_high is not None:
                        tr.run_high = max(tr.run_high, h)
                    if tr.run_low is not None:
                        tr.run_low = min(tr.run_low, l)

                # Print grid (once) when ready — mirrors Pine tryPrintGrid
                if tr.saw_start and not tr.grid_started:
                    ready = (not delay_until_ib) or (
                        tr.start_sec is not None
                        and t_sec >= tr.start_sec + ib_minutes * 60
                    )
                    if ready:
                        ib_just = False
                        if delay_until_ib and tr.start_sec is not None:
                            end_ib = tr.start_sec + ib_minutes * 60
                            ib_just = t_sec >= end_ib and (
                                prev_sec is None or prev_sec < end_ib
                            )
                            if ib_just:
                                tr.sess_open = prev_close if prev_close is not None else o
                            elif tr.sess_open is None:
                                tr.sess_open = o
                        dist = resolve_distance(
                            tr.ranges[-1] if tr.ranges else None,
                            tr.ranges,
                            mode=mode,
                            atr_len=atr_len,
                            min_tick=min_tick,
                        )
                        if dist is not None and tr.sess_open is not None:
                            grid = _emit_grid(
                                tr,
                                grid_start_sec=t_sec if delay_until_ib else int(tr.start_sec or t_sec),
                                anchor=float(tr.sess_open),
                                distance=float(dist),
                                zone_names=tr.zone_names,
                                grid_width_frac=grid_width_frac,
                                grid_end_trim_sec=grid_end_trim_sec,
                            )
                            if grid is not None:
                                tr.active = grid
                                tr.grid_started = True
            elif tr.was_in:
                # Session ended — commit H–L range and archive active grid
                if (
                    tr.saw_start
                    and tr.run_high is not None
                    and tr.run_low is not None
                ):
                    rng = float(tr.run_high) - float(tr.run_low)
                    if rng > min_tick:
                        tr.ranges.append(rng)
                        cap = max(atr_len, 20)
                        if len(tr.ranges) > cap:
                            tr.ranges = tr.ranges[-cap:]
                if tr.active is not None:
                    completed.append(tr.active)
                    tr.active = None
                tr.saw_start = False
                tr.grid_started = False
            tr.was_in = in_now

        prev_sec = t_sec
        prev_close = c

    # Active (still in-session) grids count toward the overlay set
    actives = [tr.active for tr in trackers if tr.active is not None]
    all_grids = completed + actives
    all_grids.sort(key=lambda g: int(g.get("grid_start") or 0), reverse=True)
    cap_n = max(0, int(max_sessions))
    return all_grids[:cap_n] if cap_n else []


__all__ = [
    "FIB_RATIOS",
    "ZONES",
    "ZONE_ORDER",
    "DEFAULT_SESSIONS",
    "DEFAULT_TIMEZONE",
    "DEFAULT_ATR_LEN",
    "DEFAULT_ZONE_NAMES",
    "LevelBook",
    "ZoneBand",
    "FadeSetup",
    "project_levels",
    "zone_band",
    "all_zone_bands",
    "avg_like_session_ranges",
    "resolve_distance",
    "next_wider_zone",
    "price_touches_zone",
    "sweep_pierced",
    "sweep_confirmed",
    "first_swept_zone",
    "build_fade_setup",
    "ib_anchor_ready",
    "detect_fvg",
    "fvg_inverted",
    "ifvg_dir_after_invert",
    "overlaps_band",
    "sweep_ifvg_confirmed",
    "levels_as_dict",
    "parse_hhmm_to_minutes",
    "minutes_in_session",
    "session_duration_minutes",
    "normalize_zone_names",
    "build_session_fib_overlays",
]
