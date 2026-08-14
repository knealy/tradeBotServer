"""
Session Fibonacci Sweep — pure math (bot-port ready).

Mirrors the Pine indicator level / zone / fade-setup logic with no I/O.
Keep ratios and zone names in sync with strategies/session_fibonacci_sweep.pine.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Literal, Optional, Sequence, Tuple

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
    "Tokyo": ("19:00", "00:00"),
    "London": ("00:00", "08:00"),
    "NY AM": ("08:00", "13:00"),
    "NY PM": ("13:00", "19:00"),
}

DEFAULT_TIMEZONE = "America/New_York"

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
    mode: Literal["previous", "atr"] = "previous",
    atr_len: int = 5,
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
    stop_buffer_ratio: float = 0.05,
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


def levels_as_dict(book: LevelBook) -> Dict[str, Dict[str, float]]:
    """JSON-friendly dump of projected levels."""
    return {
        "anchor": book.anchor,
        "distance": book.distance,
        "up": {str(k): v for k, v in book.up.items()},
        "down": {str(k): v for k, v in book.down.items()},
    }


__all__ = [
    "FIB_RATIOS",
    "ZONES",
    "ZONE_ORDER",
    "DEFAULT_SESSIONS",
    "DEFAULT_TIMEZONE",
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
    "first_swept_zone",
    "build_fade_setup",
    "ib_anchor_ready",
    "levels_as_dict",
]
