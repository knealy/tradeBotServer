"""Persist live anchor ranges to disk for backtest-vs-live diffing.

Why this exists
---------------
The 2026-06-11 ``morning_range_reversion`` MGC trade exposed a failure mode
the existing tooling could not surface quickly:

- live bot computed an anchor width of **16.18 pt** during 07:00-08:00 ET
- the databento walk-forward computed an anchor width of **16.70 pt** for the
  same window — a **0.52 pt difference**
- because MRR's stop loss is ``half_width × sl_mult``, that 0.52 pt anchor
  disagreement put the live SL ~0.9 pt closer to entry than the simulator's SL
- the actual price low (4073.10) hit the live SL but JUST missed the
  simulator's SL, producing opposite outcomes for the same trade

This module is the *first line of defense* against silent anchor drift. Each
time a strategy finalizes an anchor range it calls :func:`record_anchor`,
which writes a single JSON document to ``data/anchors/`` keyed by
``{strategy}_{symbol}_{session_date_et}.json``. The companion script
``scripts/diff_anchor_live_vs_databento.py`` then re-computes the same anchor
from local databento CSVs and reports any divergence ≥ 1 tick.

Design notes
------------
- The function is **best-effort**: any IOError or unexpected shape is logged
  at WARNING and never raises. We do not want to take a strategy offline
  because the anchor cache directory is missing.
- We only write the file ONCE per session-date / strategy / symbol. Overwriting
  on every bar would obscure the original snapshot if the strategy later
  re-finalized the anchor (which it should not, but defensively…).
- ``record_anchor`` accepts a free-form ``extra`` dict so callers can stamp
  data-feed health, regime flags, or any other context without forcing this
  module to learn about them. The diff tool only depends on the core fields.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional


logger = logging.getLogger(__name__)


def _resolve_anchor_dir(override: Optional[Path] = None) -> Path:
    """Return the directory anchor JSON files are written to.

    Resolution order:
    1. Explicit ``override`` parameter (used in tests).
    2. ``ANCHOR_PERSISTENCE_DIR`` environment variable.
    3. ``data/anchors/`` under the repo root (default).
    """
    if override is not None:
        return Path(override)
    env = os.environ.get("ANCHOR_PERSISTENCE_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parent.parent / "data" / "anchors"


def anchor_json_path(
    *,
    strategy: str,
    symbol: str,
    session_date_et: date,
    base_dir: Optional[Path] = None,
) -> Path:
    """Return the canonical path for a session's anchor JSON file."""
    base = _resolve_anchor_dir(base_dir)
    fname = f"{strategy}_{symbol}_{session_date_et.isoformat()}.json"
    return base / fname


def record_anchor(
    *,
    strategy: str,
    symbol: str,
    session_date_et: date,
    high: float,
    low: float,
    window_start_et: str,
    window_end_et: str,
    n_bars_used: Optional[int] = None,
    first_bar_ts_utc: Optional[datetime] = None,
    last_bar_ts_utc: Optional[datetime] = None,
    extra: Optional[Dict[str, Any]] = None,
    base_dir: Optional[Path] = None,
    overwrite: bool = False,
) -> Optional[Path]:
    """Persist one anchor snapshot to disk.

    Returns the path written, or ``None`` if the call was a no-op (file
    already existed and ``overwrite`` was false, or an exception occurred).

    Parameters
    ----------
    strategy : str
        Strategy name (e.g. ``"morning_range_reversion"``).
    symbol : str
        Trading symbol (e.g. ``"MGC"``).
    session_date_et : date
        Session date in the strategy's timezone (typically America/New_York).
        Used as part of the filename and the JSON payload.
    high, low : float
        Anchor extremes in points. Must satisfy ``high >= low``; if not, a
        WARNING is logged and the call returns None.
    window_start_et, window_end_et : str
        Anchor window expressed in ET (e.g. ``"07:00"`` / ``"08:00"``).
        Stored verbatim for the diff tool.
    n_bars_used, first_bar_ts_utc, last_bar_ts_utc : optional
        Provenance hints. The diff tool uses ``n_bars_used`` to detect when
        live and backtest disagree on how many bars qualified.
    extra : optional dict
        Free-form payload merged into the top-level JSON. Caller is expected
        to keep this serializable.
    base_dir : optional Path
        Override the destination directory. Used by tests.
    overwrite : bool
        If false (default) and the file already exists, do nothing.
    """
    if high < low:
        logger.warning(
            "anchor_persistence: refusing to write %s/%s %s anchor with H=%.4f < L=%.4f",
            strategy, symbol, session_date_et, high, low,
        )
        return None
    width = round(float(high) - float(low), 6)
    payload: Dict[str, Any] = {
        "strategy": str(strategy),
        "symbol": str(symbol),
        "session_date_et": session_date_et.isoformat(),
        "computed_at_utc": datetime.now(timezone.utc).isoformat(),
        "anchor": {
            "high": round(float(high), 6),
            "low": round(float(low), 6),
            "width": width,
            "mid": round((float(high) + float(low)) / 2.0, 6),
        },
        "window": {
            "start_et": str(window_start_et),
            "end_et": str(window_end_et),
            "n_bars_used": int(n_bars_used) if n_bars_used is not None else None,
            "first_bar_ts_utc": first_bar_ts_utc.isoformat() if first_bar_ts_utc else None,
            "last_bar_ts_utc": last_bar_ts_utc.isoformat() if last_bar_ts_utc else None,
        },
    }
    if extra:
        for k, v in extra.items():
            if k in payload:
                logger.warning(
                    "anchor_persistence: extra key %r collides with core field — keeping core value",
                    k,
                )
                continue
            payload[k] = v
    try:
        out_path = anchor_json_path(
            strategy=strategy, symbol=symbol,
            session_date_et=session_date_et, base_dir=base_dir,
        )
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if out_path.exists() and not overwrite:
            return None
        out_path.write_text(json.dumps(payload, indent=2, sort_keys=False), encoding="utf-8")
        logger.info(
            "📌 anchor persisted: %s %s %s  H=%.4f L=%.4f width=%.4f → %s",
            strategy, symbol, session_date_et.isoformat(),
            high, low, width, out_path,
        )
        return out_path
    except OSError as exc:
        # Disk/permissions issues must NEVER take the strategy offline.
        logger.warning(
            "anchor_persistence: failed to write %s/%s %s anchor (%s) — continuing without persistence",
            strategy, symbol, session_date_et, exc,
        )
        return None


def load_anchor(
    *,
    strategy: str,
    symbol: str,
    session_date_et: date,
    base_dir: Optional[Path] = None,
) -> Optional[Dict[str, Any]]:
    """Read a previously-persisted anchor snapshot. Returns None if missing/corrupt."""
    p = anchor_json_path(
        strategy=strategy, symbol=symbol,
        session_date_et=session_date_et, base_dir=base_dir,
    )
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("anchor_persistence: %s unreadable (%s)", p, exc)
        return None


def patch_session_activity(
    *,
    strategy: str,
    symbol: str,
    session_date_et: date,
    patch: Dict[str, Any],
    base_dir: Optional[Path] = None,
) -> bool:
    """Merge ``patch`` into ``session_activity`` on the anchor JSON for this day.

    Creates a minimal anchor stub when the range snapshot has not been written
    yet (mid-session crash before finalisation).  Best-effort — never raises.
    """
    if not patch:
        return False
    try:
        p = anchor_json_path(
            strategy=strategy, symbol=symbol,
            session_date_et=session_date_et, base_dir=base_dir,
        )
        p.parent.mkdir(parents=True, exist_ok=True)
        if p.is_file():
            try:
                doc = json.loads(p.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                doc = {}
        else:
            doc = {
                "strategy": str(strategy),
                "symbol": str(symbol),
                "session_date_et": session_date_et.isoformat(),
                "anchor": None,
            }
        activity = dict(doc.get("session_activity") or {})
        activity.update(patch)
        activity["updated_at_utc"] = datetime.now(timezone.utc).isoformat()
        doc["session_activity"] = activity
        p.write_text(json.dumps(doc, indent=2, sort_keys=False), encoding="utf-8")
        return True
    except OSError as exc:
        logger.debug(
            "anchor_persistence: patch_session_activity failed for %s/%s %s (%s)",
            strategy, symbol, session_date_et, exc,
        )
        return False


def load_session_activity(
    *,
    strategy: str,
    symbol: str,
    session_date_et: date,
    base_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Return persisted ``session_activity`` dict, or ``{}`` if absent."""
    doc = load_anchor(
        strategy=strategy, symbol=symbol,
        session_date_et=session_date_et, base_dir=base_dir,
    )
    if not doc:
        return {}
    activity = doc.get("session_activity")
    return dict(activity) if isinstance(activity, dict) else {}
