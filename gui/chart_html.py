"""
Local TradingView Lightweight Charts - HTML Generator
Creates a standalone HTML file with TradingView Lightweight Charts that can be opened in any browser.
Supports real-time updates and backtesting mode.
"""

import json
import os
import re
import asyncio
import math
import hashlib
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional, Any, Tuple
from pathlib import Path
from collections import defaultdict
from aiohttp import web, WSMsgType
from aiohttp.client_exceptions import ClientConnectionResetError
import logging

logger = logging.getLogger(__name__)

# Rolling window for GUI PnL backfill when AccountTracker is uninitialized
_GUI_PNL_BACKFILL_DAYS = 30


def _gui_account_blocklist() -> set:
    """Account IDs hidden from the GUI dropdown (comma-separated env)."""
    raw = os.getenv("GUI_ACCOUNT_BLOCKLIST", "").strip()
    if not raw:
        return set()
    return {part.strip() for part in raw.split(",") if part.strip()}


def _account_id_str(acc: Dict[str, Any]) -> str:
    return str(acc.get("id") or acc.get("accountId") or "")


def _gui_eligible_accounts(accounts: List[Dict]) -> List[Dict]:
    """Drop blocklisted or non-tradeable accounts from GUI rotation."""
    blocklist = _gui_account_blocklist()
    eligible: List[Dict] = []
    for acc in accounts or []:
        acc_id = _account_id_str(acc)
        if acc_id and acc_id in blocklist:
            logger.debug("GUI account blocklist: skipping %s", acc_id)
            continue
        status = str(acc.get("status", "active")).lower()
        if status in ("closed", "inactive", "disabled", "suspended", "archived"):
            logger.debug("GUI skipping non-active account %s (status=%s)", acc_id, status)
            continue
        eligible.append(acc)
    return eligible


def _pick_default_gui_account(accounts: List[Dict]) -> Optional[Dict]:
    """Prefer ``GUI_DEFAULT_ACCOUNT_ID``, else highest-balance practice account."""
    if not accounts:
        return None
    preferred = os.getenv("GUI_DEFAULT_ACCOUNT_ID", "").strip()
    if preferred:
        for acc in accounts:
            if _account_id_str(acc) == preferred:
                return acc
    def _score(acc: Dict) -> Tuple[int, float]:
        name = (acc.get("name") or acc.get("accountName") or "").upper()
        bal = float(acc.get("balance", acc.get("currentBalance", 0)) or 0)
        return (1 if "PRAC" in name else 0, bal)
    return max(accounts, key=_score)


async def _ensure_gui_accounts_ready(trading_bot) -> None:
    """Load accounts, apply blocklist, auto-select a tradeable account."""
    try:
        accounts = getattr(trading_bot, "accounts", None) or []
        if not accounts:
            accounts = await trading_bot.list_accounts()
        eligible = _gui_eligible_accounts(accounts)
        if not eligible:
            logger.warning(
                "No GUI-eligible accounts after blocklist filter — showing all %d broker account(s)",
                len(accounts),
            )
            trading_bot.accounts = accounts or []
            return
        if len(eligible) < len(accounts):
            logger.info(
                "GUI account filter: %d eligible of %d broker account(s)",
                len(eligible),
                len(accounts),
            )
        trading_bot.accounts = eligible
        eligible_ids = {_account_id_str(a) for a in eligible}
        sel = getattr(trading_bot, "selected_account", None)
        sel_id = ""
        if isinstance(sel, dict):
            sel_id = _account_id_str(sel)
        elif sel:
            sel_id = str(sel)
        if sel_id not in eligible_ids:
            pick = _pick_default_gui_account(eligible)
            if pick:
                acc_id = _account_id_str(pick)
                await trading_bot.switch_account(acc_id)
                logger.info(
                    "GUI auto-selected account %s (id=%s)",
                    pick.get("name") or pick.get("accountName"),
                    acc_id,
                )
    except Exception as exc:
        logger.warning("GUI account setup failed: %s", exc)


async def _ensure_gui_session_warm(trading_bot, symbol: str, timeframe: str) -> None:
    """Keep REST session alive and start Market Hub for live chart ticks."""
    await _ensure_gui_accounts_ready(trading_bot)
    try:
        if hasattr(trading_bot, "auth_manager") and trading_bot.auth_manager:
            await trading_bot.auth_manager.ensure_valid_token()
    except Exception as exc:
        logger.warning("GUI token refresh on startup failed: %s", exc)
    try:
        await trading_bot.start_keepalive_heartbeat()
    except Exception as exc:
        logger.warning("GUI keep-alive heartbeat failed to start: %s", exc)
    # GUI always attempts Market Hub — unlike strategy_executor, we do not
    # honour ENABLE_SIGNALR=false here (that flag is for offline/backtest runs).
    try:
        ok = await trading_bot.start_market_hub_for_strategies(
            [str(symbol).upper()],
            [str(timeframe)],
        )
        if ok:
            logger.info("GUI Market Hub wired for %s (%s)", symbol, timeframe)
        else:
            logger.warning(
                "GUI Market Hub did not connect — chart will use REST quotes (stale last-tick)"
            )
    except Exception as exc:
        logger.warning("GUI Market Hub startup failed: %s", exc)


def quote_timeframe_bucket_seconds(timeframe: Optional[str]) -> int:
    """Bar period in seconds for aligning synthetic latest_bar from quotes (matches chart TF)."""
    if not timeframe:
        return 60
    m = re.match(r"^(\d+)\s*([smhd])$", str(timeframe).strip().lower())
    if not m:
        return 60
    n, unit = int(m.group(1)), m.group(2)
    mult = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    return max(1, n * mult[unit])


def _parse_strategy_settings_blob(settings: Any) -> Dict[str, Any]:
    if isinstance(settings, dict):
        return settings
    if isinstance(settings, str) and settings.strip():
        try:
            return json.loads(settings)
        except (json.JSONDecodeError, TypeError, ValueError):
            return {}
    return {}


def _aggregate_trade_legs(legs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Group fill-level trade legs into logical trades.

    The TopStepX ``Trade/search`` API emits one record per filled
    contract leg. A 3-contract trade closing in a single fill, or a
    6-contract scale-out across a few minutes, therefore arrives as
    3-6 separate rows that share the same opening fill (same parent
    entry order) but were displayed as if they were 3-6 *independent*
    trades. That triple-counted every metric downstream:
    trade-count, win rate, streaks, max drawdown, profit factor, and
    the equity curve. This collapses those legs back into one logical
    position the way an operator thinks about it.

    Group key, in priority order:

    1. ``(symbol, side, entry_order_id)`` — most precise; legs that
       share a parent opening order ARE the same logical position. The
       opener id is attached to each leg dict by the caller (sourced
       from the FIFO half-turn pairing already done in
       :func:`handle_get_trades`).
    2. ``(symbol, side, entry_time bucketed to 2 seconds)`` — fallback
       for legs whose opener wasn't paired (queue underflow at the
       window edge), which still share an entry timestamp because the
       broker fills atomic position opens at the same instant.

    Returns a new list ordered the same way the input was, with each
    aggregated row carrying:

    * weighted-average ``entry_price`` / ``exit_price`` (qty-weighted)
    * summed ``quantity`` / ``pnl`` / ``fees``
    * earliest leg's ``entry_time``, latest leg's ``exit_time``
    * recomputed ``points`` (signed by position side)
    * ``legs_count`` and ``legs[]`` for drilldown in the recap modal

    The caller is responsible for recomputing ``cumulative_pnl`` and
    ``trade_number`` after aggregation since those depend on the
    collapsed ordering.
    """
    if not legs:
        return []

    def _entry_key(leg: Dict[str, Any]) -> Tuple[Any, ...]:
        sym = (leg.get("symbol") or "").upper()
        side = (leg.get("side") or "").upper()
        eo = leg.get("entry_order_id")
        if eo not in (None, "", 0, "0"):
            return ("order", sym, side, str(eo))
        et = leg.get("entry_time") or ""
        try:
            d = datetime.fromisoformat(str(et).replace("Z", "+00:00"))
            bucket = int(d.timestamp() // 2) * 2
        except (TypeError, ValueError):
            bucket = str(et)
        return ("time", sym, side, bucket)

    grouped: Dict[Any, List[Dict[str, Any]]] = {}
    insertion_order: List[Any] = []
    for leg in legs:
        k = _entry_key(leg)
        if k not in grouped:
            grouped[k] = []
            insertion_order.append(k)
        grouped[k].append(leg)

    out: List[Dict[str, Any]] = []
    for k in insertion_order:
        grp = grouped[k]
        if len(grp) == 1:
            t = dict(grp[0])
            t["legs_count"] = 1
            t["legs"] = []
            out.append(t)
            continue

        grp.sort(key=lambda l: str(l.get("exit_time") or ""))
        total_qty = sum(int(l.get("quantity", 0) or 0) for l in grp) or len(grp)
        total_pnl = sum(float(l.get("pnl", 0) or 0) for l in grp)
        total_fees = sum(float(l.get("fees", 0) or 0) for l in grp)

        def _wavg(field: str) -> float:
            num = 0.0
            for l in grp:
                q = int(l.get("quantity", 0) or 0) or 1
                num += float(l.get(field, 0) or 0) * q
            return num / total_qty

        wavg_entry = _wavg("entry_price")
        wavg_exit = _wavg("exit_price")
        first = grp[0]
        last = grp[-1]
        side_up = (first.get("side") or "").upper()
        if side_up in ("LONG", "BUY"):
            points = wavg_exit - wavg_entry
        else:
            points = wavg_entry - wavg_exit

        merged = dict(first)
        merged.update({
            "id": first.get("id"),
            "entry_time": first.get("entry_time"),
            "exit_time": last.get("exit_time"),
            "entry_price": round(wavg_entry, 2),
            "exit_price": round(wavg_exit, 2),
            "quantity": total_qty,
            "pnl": round(total_pnl, 2),
            "fees": round(total_fees, 2),
            "points": round(points, 2),
            "legs_count": len(grp),
            "legs": [
                {
                    "id": l.get("id"),
                    "order_id": l.get("order_id"),
                    "exit_time": l.get("exit_time"),
                    "quantity": int(l.get("quantity", 0) or 0),
                    "pnl": round(float(l.get("pnl", 0) or 0), 2),
                    "exit_price": round(float(l.get("exit_price", 0) or 0), 2),
                }
                for l in grp
            ],
        })
        out.append(merged)
    return out


def _classify_order_tag(tag: Optional[str]) -> Dict[str, str]:
    """Classify a broker ``customTag`` as auto / manual / bracket."""
    raw = str(tag or "").strip()
    upper = raw.upper()
    if not upper:
        return {"source": "manual", "source_label": "manual", "strategy": "", "custom_tag": ""}
    if "AUTOBRACKET" in upper or "-SL" in upper or "-TP" in upper:
        return {"source": "bracket", "source_label": "bracket", "strategy": "", "custom_tag": raw}
    _known = (
        "overnight_range",
        "morning_range_reversion",
        "opening_range_breakout",
        "overnight_reversion",
        "trend_scalping",
        "mean_reversion",
    )
    strategy = ""
    if upper.startswith("TB-"):
        for part in raw.split("-"):
            if part in _known:
                strategy = part
                break
    if not strategy:
        for name in _known:
            if name.upper().replace("_", "") in upper.replace("_", ""):
                strategy = name
                break
    if upper.startswith("TB-") or "STOP_BRACKET" in upper or any(
        k.upper().replace("_", "") in upper.replace("_", "") for k in _known
    ):
        return {"source": "auto", "source_label": "auto", "strategy": strategy, "custom_tag": raw}
    return {"source": "manual", "source_label": "manual", "strategy": "", "custom_tag": raw}


def broker_bars_to_chart_rows(bars: List[Any]) -> List[Dict[str, Any]]:
    """Normalize ``get_historical_data`` return value into Lightweight Charts payloads."""
    from core.backtest.ohlcv import sanitize_ohlcv_ohlc

    chart_data: List[Dict[str, Any]] = []
    for bar in bars or []:
        try:
            if hasattr(bar, "timestamp"):
                if isinstance(bar.timestamp, datetime):
                    ts = int(bar.timestamp.timestamp())
                elif isinstance(bar.timestamp, str):
                    from datetime import datetime as dt

                    dt_obj = dt.fromisoformat(bar.timestamp.replace("Z", "+00:00"))
                    ts = int(dt_obj.timestamp())
                else:
                    ts = int(bar.timestamp)
            elif isinstance(bar, dict):
                ts_val = bar.get("timestamp")
                if isinstance(ts_val, datetime):
                    ts = int(ts_val.timestamp())
                elif isinstance(ts_val, str):
                    from datetime import datetime as dt

                    dt_obj = dt.fromisoformat(ts_val.replace("Z", "+00:00"))
                    ts = int(dt_obj.timestamp())
                else:
                    ts = int(ts_val) if ts_val else 0
            else:
                continue

            o0 = float(bar.open if hasattr(bar, "open") else bar.get("open", 0))
            h0 = float(bar.high if hasattr(bar, "high") else bar.get("high", 0))
            l0 = float(bar.low if hasattr(bar, "low") else bar.get("low", 0))
            c0 = float(bar.close if hasattr(bar, "close") else bar.get("close", 0))
            o0, h0, l0, c0 = sanitize_ohlcv_ohlc(o0, h0, l0, c0)
            chart_data.append(
                {
                    "time": ts,
                    "open": o0,
                    "high": h0,
                    "low": l0,
                    "close": c0,
                    "volume": float(bar.volume if hasattr(bar, "volume") else bar.get("volume", 0)),
                }
            )
        except Exception as e:
            logger.warning("Failed to convert bar: %s", e)
            continue
    return chart_data


def _trade_time_to_unix(raw: Any) -> Optional[int]:
    """Normalize broker/API timestamps to Unix seconds (UTC)."""
    if raw is None or raw == "":
        return None
    if isinstance(raw, (int, float)):
        n = int(raw)
        return n // 1000 if n > 1_000_000_000_000 else n
    if isinstance(raw, datetime):
        dt = raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    try:
        s = str(raw).strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Strategy idle-state helpers — feed the dashboard's "ready / sleeping" panel.
#
# The bot's range-based strategies (overnight_range, MRR, ORB) trade for a
# narrow window each weekday and sit dormant the rest of the day. The v2
# dashboard's "STRATEGIES at rest" empty state used to be just a launcher
# form, which left the operator blind to the *most useful* questions during
# the long quiet windows: when does the next session arm, and what's the
# last range each strategy recorded? These helpers expose both, by combining
# the persisted strategy_states.settings ranges with hardcoded ET schedules.
# ---------------------------------------------------------------------------

# Hardcoded launch schedules for the time-gated strategies. Source of truth
# is each strategy's ``_in_trading_window`` / time-gate logic in
# ``strategies/<name>_strategy.py``; cross-referenced with the live
# ``config/strategies/<name>.toml`` (``range_start`` / ``range_end_open``).
# The ``time_et`` is when the strategy first becomes eligible to act —
# either the range *start* (range builders that act on the close of the
# build window) or the *signal* time (overnight_range scans at 09:29 ET
# and arms breakout brackets that fill at 09:30 ET market open).
_STRATEGY_SCHEDULES: Dict[str, Dict[str, Any]] = {
    "overnight_range": {
        "time_et": (9, 29),
        "days": "weekday",
        "label": "Mon\u2013Fri 9:29 AM ET",
        "kind": "signal",
    },
    "morning_range_reversion": {
        "time_et": (7, 0),
        "days": "weekday",
        "label": "Mon\u2013Fri 7:00 AM ET (range build, fade window 8\u201316 ET)",
        "kind": "range_build",
    },
    "opening_range_breakout": {
        "time_et": (9, 30),
        "days": "weekday",
        "label": "Mon\u2013Fri 9:30 AM ET (range build, breakout after 10:30)",
        "kind": "range_build",
    },
}


def _compute_next_launch(name: str, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Compute the next eligible launch window in ET for a registered strategy.

    Returns a dict with ``eta_iso`` (UTC ISO timestamp), ``eta_seconds``
    (delta from ``now``), a human ``label`` (e.g. "9:29 AM ET tomorrow"),
    and the ``schedule`` summary string. Strategies without a hardcoded
    schedule (i.e. operator-launched) report ``manual launch`` and
    ``eta_iso=None`` so the frontend can render a different chip.
    """
    meta = _STRATEGY_SCHEDULES.get(name)
    if not meta:
        return {
            "eta_iso": None,
            "eta_seconds": None,
            "label": "manual launch",
            "schedule": "manual",
            "kind": "manual",
        }
    try:
        from zoneinfo import ZoneInfo
        et = ZoneInfo("America/New_York")
    except ImportError:
        # zoneinfo missing on this Python — fall back to fixed EDT (-4h).
        # The dashboard will be off by an hour for ~5 weeks/year on EST,
        # acceptable degradation for an env without zoneinfo.
        from datetime import timedelta as _td
        et = timezone(_td(hours=-4))
    if now is None:
        now = datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now_et = now.astimezone(et)
    h, m = meta["time_et"]
    candidate = now_et.replace(hour=h, minute=m, second=0, microsecond=0)
    if candidate <= now_et:
        from datetime import timedelta as _td
        candidate = candidate + _td(days=1)
    if meta.get("days") == "weekday":
        from datetime import timedelta as _td
        while candidate.weekday() >= 5:  # 5=Sat, 6=Sun
            candidate = candidate + _td(days=1)
    eta_seconds = int((candidate - now_et).total_seconds())
    days_diff = (candidate.date() - now_et.date()).days
    if days_diff == 0:
        when = "today"
    elif days_diff == 1:
        when = "tomorrow"
    else:
        when = candidate.strftime("%A")  # "Monday", etc.
    # %-I is GNU-only; fall back if it raises on Windows runtimes.
    try:
        time_label = candidate.strftime("%-I:%M %p ET")
    except ValueError:
        time_label = candidate.strftime("%I:%M %p ET").lstrip("0")
    return {
        "eta_iso": candidate.astimezone(timezone.utc).isoformat(),
        "eta_seconds": eta_seconds,
        "label": f"{time_label} {when}",
        "schedule": meta["label"],
        "kind": meta["kind"],
    }


def _strategy_idle_state(
    name: str,
    db: Any,
    account_id: Optional[str],
) -> Dict[str, Any]:
    """Build the idle-state block for a non-active registered strategy.

    Pulls the most recent persisted range from ``strategy_states.settings``
    (the same blob the live chart consumes) plus ``last_started`` /
    ``last_stopped`` from the row's audit columns, and annotates with the
    next eligible launch window from :func:`_compute_next_launch`.

    Range source per strategy:
      - ``overnight_range``           → ``settings.or_ranges``  (high, low)
      - ``morning_range_reversion``  → ``settings.mrr_ranges`` (high, low,
        mid, width, session_*)
      - ``opening_range_breakout``    → ``settings.orb_ranges`` (same shape)

    All other strategies get an empty ``last_ranges`` and ``manual``
    schedule — the panel renders them as "manual launch" without a
    countdown.
    """
    out: Dict[str, Any] = {
        "last_ranges": [],
        "last_started": None,
        "last_stopped": None,
        "snapshot_age_seconds": None,
    }
    out.update(_compute_next_launch(name))
    if not (db and account_id):
        return out
    try:
        state = db.get_strategy_state(str(account_id), name) or {}
    except Exception:
        logger.debug("idle_state: get_strategy_state(%s) failed", name, exc_info=True)
        state = {}
    out["last_started"] = state.get("last_started")
    out["last_stopped"] = state.get("last_stopped")
    settings_blob = _parse_strategy_settings_blob(state.get("settings"))
    metadata_blob = state.get("metadata")
    if isinstance(metadata_blob, str):
        try:
            metadata_blob = json.loads(metadata_blob)
        except (json.JSONDecodeError, TypeError, ValueError):
            metadata_blob = {}
    if not isinstance(metadata_blob, dict):
        metadata_blob = {}
    range_key = {
        "overnight_range": "or_ranges",
        "morning_range_reversion": "mrr_ranges",
        "opening_range_breakout": "orb_ranges",
    }.get(name)
    ranges_dict = settings_blob.get(range_key) if range_key else None
    # Pick the right "snapshot age" reference: OR strategy writes
    # metadata.or_ranges_saved_at every 4s while running; MRR/ORB rely on
    # the row's updated_at. Fall back to updated_at in either case.
    snap_ts_iso = None
    if range_key == "or_ranges":
        snap_ts_iso = metadata_blob.get("or_ranges_saved_at")
    if not snap_ts_iso:
        snap_ts_iso = state.get("updated_at")
    if snap_ts_iso:
        try:
            ts = datetime.fromisoformat(str(snap_ts_iso).replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            out["snapshot_age_seconds"] = int(
                (datetime.now(timezone.utc) - ts).total_seconds()
            )
        except (TypeError, ValueError):
            pass
    if isinstance(ranges_dict, dict):
        # Dedupe on (high, low) tuple — strategies write both contract-prefixed
        # ("F.US.MNQ") and short-form ("MNQ") aliases for the same range, and
        # the panel only needs one row per logical symbol.
        seen: set = set()
        for sym, info in ranges_dict.items():
            if not isinstance(info, dict):
                continue
            try:
                hi = float(info.get("high", 0) or 0)
                lo = float(info.get("low", 0) or 0)
            except (TypeError, ValueError):
                continue
            if hi <= 0 or lo <= 0:
                continue
            key = (round(hi, 4), round(lo, 4))
            if key in seen:
                continue
            seen.add(key)
            sym_up = str(sym).upper()
            # Prefer the short form ("MNQ" over "F.US.MNQ") for display.
            if "." in sym_up:
                short = sym_up.split(".")[-1].strip()
                if short:
                    sym_up = short
            mid = info.get("mid")
            try:
                mid_f = float(mid) if mid is not None else (hi + lo) / 2.0
            except (TypeError, ValueError):
                mid_f = (hi + lo) / 2.0
            out["last_ranges"].append({
                "symbol": sym_up,
                "high": hi,
                "low": lo,
                "mid": round(mid_f, 4),
                "width": round(hi - lo, 4),
            })
        out["last_ranges"].sort(key=lambda r: r["symbol"])
    return out


def _or_ranges_from_strategy_state_row(st_row: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not st_row:
        return {}
    blob = _parse_strategy_settings_blob(st_row.get("settings"))
    raw = blob.get("or_ranges") if isinstance(blob, dict) else None
    return raw if isinstance(raw, dict) else {}


def _strategy_ranges_from_db(
    trading_bot: Any,
    account_id: Optional[str],
    strategy_name: str,
    settings_key: str,
) -> Dict[str, Any]:
    """Generic DB read of ``strategy_states.settings[settings_key]``.

    Used by ``handle_strategy_details`` to surface MRR (``mrr_ranges``) and ORB
    (``orb_ranges``) ranges when the strategy runs in a separate executor
    process and the chart-server's strategy_manager has only an idle (empty
    ``_state``/``_sessions``) instance. Mirrors the OR-range fallback pattern
    in ``_overnight_or_ranges_with_executor_fallback``.
    """
    db = getattr(trading_bot, "db", None)
    if not db or not account_id:
        return {}
    try:
        st_primary = db.get_strategy_state(str(account_id), strategy_name)
    except Exception:
        logger.debug("get_strategy_state(%s, %s) failed", account_id, strategy_name, exc_info=True)
        return {}
    if not st_primary:
        return {}
    blob = _parse_strategy_settings_blob(st_primary.get("settings"))
    raw = blob.get(settings_key) if isinstance(blob, dict) else None
    return raw if isinstance(raw, dict) else {}


_DB_RANGE_SETTINGS_KEYS: Dict[str, str] = {
    "morning_range_reversion": "mrr_ranges",
    "opening_range_breakout": "orb_ranges",
}

_STRATEGY_RANGE_SETTINGS_KEYS: Dict[str, str] = {
    "overnight_range": "or_ranges",
    **_DB_RANGE_SETTINGS_KEYS,
}

_RANGE_HISTORY_MAX = 20


def _normalize_symbol_ranges(raw: Optional[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Normalize ``settings.{mrr,orb}_ranges`` blobs to chart overlay shape."""
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    for sym, blob in raw.items():
        if not isinstance(blob, dict):
            continue
        hi = blob.get("high")
        if hi is None:
            hi = blob.get("H") or blob.get("range_hi")
        lo = blob.get("low")
        if lo is None:
            lo = blob.get("L") or blob.get("range_lo")
        try:
            hi_f = float(hi) if hi is not None else None
            lo_f = float(lo) if lo is not None else None
        except (TypeError, ValueError):
            continue
        if hi_f is None or lo_f is None:
            continue
        mid = blob.get("mid")
        try:
            mid_f = float(mid) if mid is not None else (hi_f + lo_f) / 2.0
        except (TypeError, ValueError):
            mid_f = (hi_f + lo_f) / 2.0
        merged: Dict[str, Any] = {
            "high": hi_f,
            "low": lo_f,
            "mid": mid_f,
            "size": hi_f - lo_f,
        }
        for opt in ("session_date", "session_start_et", "session_end_et", "phase"):
            if blob.get(opt) is not None:
                merged[opt] = blob[opt]
        out[str(sym).upper()] = merged
    return out


def _enrich_ranges_saved_at(
    ranges: Dict[str, Dict[str, Any]],
    saved_at: Optional[str],
) -> Dict[str, Dict[str, Any]]:
    """Stamp ``saved_at`` / inferred ``session_date`` when the snapshot lacks them."""
    if not ranges:
        return ranges
    out: Dict[str, Dict[str, Any]] = {}
    for sym, info in ranges.items():
        merged = dict(info)
        if saved_at and not merged.get("session_date"):
            merged["session_date"] = str(saved_at)[:10]
        if saved_at:
            merged["saved_at"] = saved_at
        out[str(sym).upper()] = merged
    return out


def _range_history_from_settings(
    settings: Optional[Dict[str, Any]],
    settings_key: str,
) -> List[Dict[str, Any]]:
    """Read normalized ``{key}_history`` entries from strategy_states.settings."""
    if not isinstance(settings, dict):
        return []
    hist_key = f"{settings_key}_history"
    raw = settings.get(hist_key)
    if not isinstance(raw, list):
        return []
    out: List[Dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        ranges = _normalize_symbol_ranges(item.get("ranges"))
        if not ranges:
            continue
        saved_at = item.get("saved_at")
        out.append({
            "session_date": item.get("session_date"),
            "saved_at": saved_at,
            "ranges": _enrich_ranges_saved_at(ranges, saved_at if isinstance(saved_at, str) else None),
        })
    out.sort(key=lambda e: str(e.get("session_date") or ""), reverse=True)
    return out


_range_history_backfill_attempted: set = set()
_range_api_bars_cache: Dict[str, Tuple[float, List[Dict[str, Any]]]] = {}
_RANGE_API_BARS_TTL_SEC = 90.0
_range_overlays_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_RANGE_OVERLAYS_TTL_SEC = 30.0


def _range_overlays_cache_key(
    account_id: Optional[str],
    max_sessions: int,
    strategy_names: Tuple[str, ...],
) -> str:
    acct = str(account_id or "")
    strat = ",".join(sorted(strategy_names))
    return f"{acct}|{max_sessions}|{strat}"


def _parse_range_overlay_strategies(request) -> List[str]:
    raw = (request.query.get("strategies") or "").strip()
    if raw:
        wanted = {s.strip() for s in raw.split(",") if s.strip()}
        return [s for s in _STRATEGY_RANGE_SETTINGS_KEYS if s in wanted]
    return list(_STRATEGY_RANGE_SETTINGS_KEYS.keys())


async def _get_cached_api_1m_bars(trading_bot: Any, symbol: str) -> List[Dict[str, Any]]:
    """TTL cache so overlay refresh does not fan out 9+ History/retrieveBars per poll."""
    import time
    from core.range_history_backfill import fetch_api_1m_bars_for_backfill

    sym = str(symbol or "").upper()
    if not sym:
        return []
    now = time.time()
    hit = _range_api_bars_cache.get(sym)
    if hit and hit[0] > now:
        return hit[1]
    try:
        bars = await fetch_api_1m_bars_for_backfill(
            trading_bot, sym, lookback_days=10,
        )
    except Exception:
        logger.debug("api 1m cache fetch failed for %s", sym, exc_info=True)
        bars = hit[1] if hit else []
    _range_api_bars_cache[sym] = (now + _RANGE_API_BARS_TTL_SEC, list(bars or []))
    return list(bars or [])


def _range_history_stale(
    settings: Dict[str, Any],
    metadata: Dict[str, Any],
    settings_key: str,
) -> bool:
    """True when persisted history is missing or newest session is before yesterday."""
    hist_key = f"{settings_key}_history"
    hist = settings.get(hist_key) if isinstance(settings, dict) else None
    if not isinstance(hist, list) or len(hist) < 2:
        return True
    dates: List[str] = []
    for entry in hist:
        if not isinstance(entry, dict):
            continue
        sd = entry.get("session_date")
        if sd:
            dates.append(str(sd)[:10])
        ranges = entry.get("ranges")
        if isinstance(ranges, dict):
            for blob in ranges.values():
                if isinstance(blob, dict) and blob.get("session_date"):
                    dates.append(str(blob["session_date"])[:10])
    if not dates:
        return True
    newest = max(dates)
    yday = (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()
    return newest < yday


def _should_backfill_range_history(
    account_id: Optional[str],
    strategy_name: str,
    settings: Dict[str, Any],
    metadata: Dict[str, Any],
    settings_key: str,
) -> bool:
    if not account_id:
        return False
    tok = (str(account_id), strategy_name)
    if tok in _range_history_backfill_attempted and not _range_history_stale(settings, metadata, settings_key):
        return False
    if isinstance(metadata, dict) and metadata.get(f"{settings_key}_history_backfilled_at"):
        if not _range_history_stale(settings, metadata, settings_key):
            return False
    return _range_history_stale(settings, metadata, settings_key)


def _maybe_backfill_range_history(
    trading_bot: Any,
    account_id: Optional[str],
    strategy_name: str,
    settings: Dict[str, Any],
    metadata: Dict[str, Any],
    settings_key: str,
) -> None:
    """Schedule async backfill when history is missing/stale (chart-server event loop)."""
    if not _should_backfill_range_history(account_id, strategy_name, settings, metadata, settings_key):
        return
    tok = (str(account_id), strategy_name)
    _range_history_backfill_attempted.add(tok)
    db = getattr(trading_bot, "db", None)
    if not db:
        return

    async def _run() -> None:
        try:
            from core.range_history_backfill import backfill_strategy_range_history_async

            await backfill_strategy_range_history_async(
                db, str(account_id), strategy_name, [], trading_bot=trading_bot, max_sessions=20,
            )
        except Exception:
            logger.debug("lazy range history backfill failed for %s", strategy_name, exc_info=True)

    try:
        import asyncio
        loop = asyncio.get_running_loop()
        loop.create_task(_run())
    except RuntimeError:
        import asyncio
        asyncio.run(_run())


async def _await_range_history_backfill(
    trading_bot: Any,
    account_id: Optional[str],
    strategy_name: str,
) -> None:
    """Blocking (for overlay API): refresh history with Databento + API bars before read."""
    if not account_id:
        return
    db = getattr(trading_bot, "db", None)
    if not db:
        return
    try:
        st = db.get_strategy_state(str(account_id), strategy_name) or {}
    except Exception:
        return
    settings = _parse_strategy_settings_blob(st.get("settings"))
    metadata = st.get("metadata") or {}
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except (json.JSONDecodeError, TypeError, ValueError):
            metadata = {}
    settings_key = _STRATEGY_RANGE_SETTINGS_KEYS.get(strategy_name)
    if not settings_key:
        return
    if not _should_backfill_range_history(account_id, strategy_name, settings, metadata, settings_key):
        return
    tok = (str(account_id), strategy_name)
    _range_history_backfill_attempted.add(tok)
    try:
        from core.range_history_backfill import backfill_strategy_range_history_async

        await backfill_strategy_range_history_async(
            db, str(account_id), strategy_name, [], trading_bot=trading_bot, max_sessions=20,
        )
    except Exception:
        logger.debug("range history backfill failed for %s", strategy_name, exc_info=True)


async def _augment_range_bundle_with_live_bars(
    trading_bot: Any,
    account_id: Optional[str],
    strategy_name: str,
    bundle: Dict[str, Any],
    *,
    max_sessions: int = 20,
    api_bars_by_symbol: Optional[Dict[str, List[Dict[str, Any]]]] = None,
) -> Dict[str, Any]:
    """Merge persisted history with live Databento+API bar reconstruction (no DB write)."""
    try:
        from core.range_history_backfill import (
            _BAR_BACKFILL_SYMBOLS,
            attach_range_window_et,
            merge_session_maps,
            ranges_from_bars,
            ranges_from_trade_snapshots,
        )

        sym_set = set(str(s).upper() for s in (bundle.get("ranges") or {}).keys())
        for s in _BAR_BACKFILL_SYMBOLS:
            sym_set.add(s)
        if not sym_set:
            return bundle

        api_by_sym = api_bars_by_symbol or {}
        bar_maps: Dict[str, Dict[str, Dict[str, Any]]] = {}
        for sym in sorted(sym_set):
            try:
                api_bars = api_by_sym.get(sym)
                if api_bars is None:
                    api_bars = await _get_cached_api_1m_bars(trading_bot, sym)
                for sd, blob in ranges_from_bars(
                    sym, strategy_name, max_sessions=max_sessions, api_bars=api_bars,
                ).items():
                    bar_maps.setdefault(sd, {}).update(blob)
            except Exception:
                logger.debug("range bar map failed %s %s", strategy_name, sym, exc_info=True)

        snap_map: Dict[str, Dict[str, Dict[str, Any]]] = {}
        db = getattr(trading_bot, "db", None)
        if db and account_id:
            try:
                snap_map = ranges_from_trade_snapshots(db, str(account_id), strategy_name)
            except Exception:
                logger.debug("range snap map failed %s", strategy_name, exc_info=True)

        db_map: Dict[str, Dict[str, Dict[str, Any]]] = {}
        for item in bundle.get("range_history") or []:
            if not isinstance(item, dict):
                continue
            sd = str(item.get("session_date") or "")[:10]
            raw_ranges = item.get("ranges") if isinstance(item.get("ranges"), dict) else {}
            if sd and raw_ranges:
                db_map[sd] = dict(raw_ranges)

        sessions = merge_session_maps(db_map, bar_maps, snap_map)[:max_sessions]
        if not sessions:
            return bundle

        history_out: List[Dict[str, Any]] = []
        for sd, sym_blobs in sessions:
            history_out.append({
                "session_date": sd,
                "saved_at": f"{sd}T16:00:00+00:00",
                "ranges": {
                    sym: attach_range_window_et(blob, strategy_name, session_date=sd)
                    for sym, blob in sym_blobs.items()
                },
            })
        bundle["range_history"] = history_out
        latest_sd, latest_blobs = sessions[0]
        bundle["ranges"] = {
            sym: attach_range_window_et(blob, strategy_name, session_date=latest_sd)
            for sym, blob in latest_blobs.items()
        }
        return bundle
    except Exception:
        logger.debug("range overlay augment failed for %s", strategy_name, exc_info=True)
        return bundle


def _finalize_strategy_range_details(
    trading_bot: Any,
    account_id: Optional[str],
    strategy_name: str,
    details: Dict[str, Any],
) -> None:
    """Normalize current ranges + attach persisted multi-session history for chart overlays."""
    settings_key = _STRATEGY_RANGE_SETTINGS_KEYS.get(strategy_name)
    if not settings_key:
        return
    raw_ranges = details.get("ranges") if isinstance(details.get("ranges"), dict) else {}
    details["ranges"] = _normalize_symbol_ranges(raw_ranges)
    details["range_history"] = []
    details["range_saved_at"] = None
    db = getattr(trading_bot, "db", None)
    if not db or not account_id:
        return
    try:
        st = db.get_strategy_state(str(account_id), strategy_name) or {}
    except Exception:
        logger.debug("get_strategy_state for range finalize failed", exc_info=True)
        return
    settings = _parse_strategy_settings_blob(st.get("settings"))
    metadata = st.get("metadata") or {}
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except (json.JSONDecodeError, TypeError, ValueError):
            metadata = {}
    saved_at = metadata.get(f"{settings_key}_saved_at") if isinstance(metadata, dict) else None
    _maybe_backfill_range_history(
        trading_bot, account_id, strategy_name, settings, metadata, settings_key,
    )
    try:
        st = db.get_strategy_state(str(account_id), strategy_name) or {}
        settings = _parse_strategy_settings_blob(st.get("settings"))
        metadata = st.get("metadata") or {}
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except (json.JSONDecodeError, TypeError, ValueError):
                metadata = {}
        saved_at = metadata.get(f"{settings_key}_saved_at") if isinstance(metadata, dict) else saved_at
    except Exception:
        logger.debug("re-read strategy_state after backfill failed", exc_info=True)
    if isinstance(saved_at, str):
        details["range_saved_at"] = saved_at
    details["ranges"] = _enrich_ranges_saved_at(details["ranges"], saved_at if isinstance(saved_at, str) else None)
    details["range_history"] = _range_history_from_settings(settings, settings_key)


def _external_strategy_details_from_db(
    trading_bot: Any,
    account_id: Optional[str],
    strategy_name: str,
) -> Dict[str, Any]:
    """Headless GUI: serve persisted ranges when no in-process strategy instance."""
    if strategy_name == "overnight_range":
        raw = _overnight_or_ranges_with_executor_fallback(trading_bot, account_id)
        ranges = _normalize_symbol_ranges(raw)
    else:
        settings_key = _DB_RANGE_SETTINGS_KEYS.get(strategy_name)
        if settings_key:
            raw = _strategy_ranges_from_db(
                trading_bot, account_id, strategy_name, settings_key
            )
            ranges = _normalize_symbol_ranges(raw)
        else:
            ranges = {}
    details = {
        "name": strategy_name,
        "status": "external",
        "external": True,
        "symbols": sorted(ranges.keys()),
        "timeframe": "N/A",
        "ranges": ranges,
        "breakout_levels": {},
        "atr_data": {},
        "risk_profile": {},
        "active_orders": 0,
        "start_time": None,
        "runtime_seconds": None,
        "runtime_str": None,
    }
    _finalize_strategy_range_details(trading_bot, account_id, strategy_name, details)
    return details


def _range_overlay_bundle_for_strategy(
    trading_bot: Any,
    account_id: Optional[str],
    strategy_name: str,
) -> Dict[str, Any]:
    """DB-first range overlay payload for the chart (current + history)."""
    from core.range_history_backfill import attach_range_window_et

    if strategy_name == "overnight_range":
        raw = _overnight_or_ranges_with_executor_fallback(trading_bot, account_id)
        details: Dict[str, Any] = {
            "name": strategy_name,
            "ranges": _normalize_symbol_ranges(raw),
        }
    else:
        settings_key = _DB_RANGE_SETTINGS_KEYS.get(strategy_name)
        if not settings_key:
            return {"ranges": {}, "range_history": [], "range_saved_at": None}
        raw = _strategy_ranges_from_db(
            trading_bot, account_id, strategy_name, settings_key,
        )
        details = {
            "name": strategy_name,
            "ranges": _normalize_symbol_ranges(raw),
        }
    _finalize_strategy_range_details(trading_bot, account_id, strategy_name, details)
    ranges = {
        sym: attach_range_window_et(info, strategy_name)
        for sym, info in (details.get("ranges") or {}).items()
        if isinstance(info, dict)
    }
    history_out: List[Dict[str, Any]] = []
    for item in details.get("range_history") or []:
        if not isinstance(item, dict):
            continue
        raw_ranges = item.get("ranges") if isinstance(item.get("ranges"), dict) else {}
        history_out.append({
            "session_date": item.get("session_date"),
            "saved_at": item.get("saved_at"),
            "ranges": {
                sym: attach_range_window_et(info, strategy_name, session_date=item.get("session_date"))
                for sym, info in raw_ranges.items()
                if isinstance(info, dict)
            },
        })
    return {
        "ranges": ranges,
        "range_history": history_out,
        "range_saved_at": details.get("range_saved_at"),
    }


def _morning_range_anchor_from_df(
    entry_dt: datetime,
    df_5m: Any,
    *,
    range_start_et: Any = None,
    range_end_et: Any = None,
    tz_name: str = "America/New_York",
) -> Optional[Dict[str, Any]]:
    """Compute 7–8am ET MRR anchor for the trade's session (walkforward parity)."""
    try:
        from datetime import time as dtime
        from zoneinfo import ZoneInfo
        import pandas as pd
    except ImportError:
        return None
    if df_5m is None or getattr(df_5m, "empty", True):
        return None
    rs = range_start_et if range_start_et is not None else dtime(7, 0)
    re = range_end_et if range_end_et is not None else dtime(8, 0)
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        return None
    if entry_dt.tzinfo is None:
        entry_dt = entry_dt.replace(tzinfo=timezone.utc)
    session_date_et = entry_dt.astimezone(tz).date()
    idx = df_5m.index
    if getattr(idx, "tz", None) is None:
        idx_et_dt = pd.DatetimeIndex(idx).tz_localize("UTC").tz_convert(tz)
    else:
        idx_et_dt = idx.tz_convert(tz)
    date_arr = idx_et_dt.date
    time_arr = idx_et_dt.time
    range_mask = (date_arr == session_date_et) & (time_arr >= rs) & (time_arr < re)
    range_slice = df_5m[range_mask]
    if range_slice.empty:
        return None
    try:
        range_high = float(range_slice["high"].astype(float).max())
        range_low = float(range_slice["low"].astype(float).min())
    except (TypeError, ValueError, KeyError):
        return None
    if range_high <= range_low:
        return None
    from datetime import datetime as dt_combine
    return {
        "high": range_high,
        "low": range_low,
        "mid": (range_high + range_low) / 2.0,
        "size": range_high - range_low,
        "session_date": session_date_et.isoformat(),
        "session_start_et": dt_combine.combine(session_date_et, rs).isoformat(),
        "session_end_et": dt_combine.combine(session_date_et, re).isoformat(),
        "strategy_name": "morning_range_reversion",
        "derived": "databento_5m_anchor",
    }


def _range_from_history_for_session(
    trading_bot: Any,
    account_id: Optional[str],
    symbol: str,
    session_date: str,
    strategy_name: str = "morning_range_reversion",
) -> Optional[Dict[str, Any]]:
    """Lookup persisted range_history row for a specific ET session date."""
    sym_u = str(symbol or "").upper()
    sd = str(session_date or "").strip()[:10]
    if not sym_u or len(sd) < 8:
        return None
    key = _DB_RANGE_SETTINGS_KEYS.get(strategy_name)
    if not key:
        return None
    db = getattr(trading_bot, "db", None)
    if not db or not account_id:
        return None
    row = db.get_strategy_state(str(account_id), strategy_name)
    settings = (row or {}).get("settings") or {}
    for item in _range_history_from_settings(settings, key):
        if str(item.get("session_date") or "")[:10] != sd:
            continue
        info = (item.get("ranges") or {}).get(sym_u)
        if not isinstance(info, dict):
            for k, v in (item.get("ranges") or {}).items():
                if str(k).upper().split(".")[-1] == sym_u.split(".")[-1]:
                    info = v
                    break
        if isinstance(info, dict) and info.get("high") is not None and info.get("low") is not None:
            out = dict(info)
            out["strategy_name"] = strategy_name
            out["derived"] = "range_history"
            return out
    return None


def _strategy_candidates_for_recap(strategy_hint: Optional[str]) -> List[str]:
    """Order strategies to try when resolving a trade recap range overlay."""
    known = ["morning_range_reversion", "opening_range_breakout", "overnight_range"]
    raw = str(strategy_hint or "").strip().lower().replace("-", "_")
    if raw in known:
        return [raw] + [s for s in known if s != raw]
    if "morning" in raw or raw == "mrr":
        return ["morning_range_reversion"] + [s for s in known if s != "morning_range_reversion"]
    if "opening" in raw or raw == "orb":
        return ["opening_range_breakout"] + [s for s in known if s != "opening_range_breakout"]
    if "overnight" in raw or raw in ("or", "overnight_range"):
        return ["overnight_range"] + [s for s in known if s != "overnight_range"]
    return list(known)


def _range_snapshot_for_trade_recap(
    trading_bot: Any,
    account_id: Optional[str],
    symbol: str,
    entry_dt: datetime,
    repo_root: Path,
    root: str,
    *,
    strategy_hint: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Session-accurate range for trade recap (Databento anchor → range_history)."""
    from core.range_history_backfill import attach_range_window_et, range_anchor_for_trade_session

    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        ZoneInfo = None  # type: ignore[misc, assignment]
    if entry_dt.tzinfo is None:
        entry_dt = entry_dt.replace(tzinfo=timezone.utc)
    if ZoneInfo is not None:
        try:
            session_date = entry_dt.astimezone(ZoneInfo("America/New_York")).date().isoformat()
        except Exception:
            session_date = entry_dt.date().isoformat()
    else:
        session_date = entry_dt.date().isoformat()

    for strat in _strategy_candidates_for_recap(strategy_hint):
        anchor = range_anchor_for_trade_session(entry_dt, symbol, strat)
        if anchor:
            return anchor

    for strat in _strategy_candidates_for_recap(strategy_hint):
        hit = _range_from_history_for_session(
            trading_bot, account_id, symbol, session_date, strat,
        )
        if hit:
            return attach_range_window_et(hit, strat, session_date=session_date)

    return None


def _mrr_live_from_executor_metadata(trading_bot: Any) -> Tuple[List[str], Dict[str, Any]]:
    """Read MRR live brief + per-symbol state from a fresh strategy_executor heartbeat."""
    db = getattr(trading_bot, "db", None)
    if not db:
        return [], {}
    try:
        process_states = db.get_process_states("strategy_executor") or []
    except Exception:
        return [], {}
    for process in process_states:
        if process.get("status") != "running":
            continue
        try:
            from datetime import datetime as dt_hb, timezone as tz_hb
            last_heartbeat = process.get("last_heartbeat")
            last_dt = None
            if isinstance(last_heartbeat, dt_hb):
                last_dt = last_heartbeat
            elif isinstance(last_heartbeat, str) and last_heartbeat:
                last_dt = dt_hb.fromisoformat(last_heartbeat.replace("Z", "+00:00"))
            if last_dt:
                last_dt = last_dt if last_dt.tzinfo else last_dt.replace(tzinfo=tz_hb.utc)
                if (dt_hb.now(tz_hb.utc) - last_dt).total_seconds() > 120:
                    continue
        except Exception:
            continue
        metadata = process.get("metadata") or {}
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except (json.JSONDecodeError, TypeError, ValueError):
                metadata = {}
        strategies_in_process = metadata.get("strategies") or []
        if "morning_range_reversion" not in strategies_in_process:
            continue
        brief = metadata.get("mrr_live_brief")
        sym_state = metadata.get("mrr_symbol_state")
        lines = list(brief) if isinstance(brief, list) else []
        state = sym_state if isinstance(sym_state, dict) else {}
        if lines or state:
            return lines, state
    return [], {}


def _best_range_for_symbol(
    trading_bot: Any,
    account_id: Optional[str],
    symbol: str,
) -> Optional[Dict[str, Any]]:
    """Pick the freshest strategy range overlay for a symbol (recap fallback)."""
    sym_u = str(symbol or "").upper()
    if not sym_u:
        return None
    for strategy_name in ("morning_range_reversion", "opening_range_breakout", "overnight_range"):
        if strategy_name == "overnight_range":
            ranges = _overnight_or_ranges_with_executor_fallback(trading_bot, account_id)
        else:
            key = _DB_RANGE_SETTINGS_KEYS.get(strategy_name)
            if not key:
                continue
            raw = _strategy_ranges_from_db(trading_bot, account_id, strategy_name, key)
            ranges = _normalize_symbol_ranges(raw)
        info = ranges.get(sym_u) if isinstance(ranges, dict) else None
        if isinstance(info, dict) and info.get("high") is not None and info.get("low") is not None:
            out = dict(info)
            out["strategy_name"] = strategy_name
            return out
    return None


def _overnight_or_ranges_with_executor_fallback(
    trading_bot: Any, account_id: Optional[str]
) -> Dict[str, Any]:
    """
    OR snapshot for chart: selected account first, then any fresh strategy_executor
    with overnight_range (so GUI account can differ from executor account).
    """
    db = getattr(trading_bot, "db", None)
    if not db or not account_id:
        return {}
    st_primary = db.get_strategy_state(str(account_id), "overnight_range")
    r = _or_ranges_from_strategy_state_row(st_primary)
    if r:
        return r
    try:
        process_states = db.get_process_states("strategy_executor") or []
    except Exception:
        logger.debug("get_process_states(strategy_executor) failed", exc_info=True)
        return {}
    for process in process_states:
        if process.get("status") != "running":
            continue
        metadata = process.get("metadata") or {}
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except (json.JSONDecodeError, TypeError, ValueError):
                metadata = {}
        strategies_in_process = metadata.get("strategies") or []
        if "overnight_range" not in strategies_in_process:
            continue
        # Fresh OR snapshot from executor heartbeat (preferred — avoids strategy_states lag).
        or_meta = metadata.get("or_ranges") if isinstance(metadata, dict) else None
        if isinstance(or_meta, dict) and or_meta:
            return or_meta
        try:
            last_heartbeat = process.get("last_heartbeat")
            last_dt = None
            if isinstance(last_heartbeat, datetime):
                last_dt = last_heartbeat
            elif isinstance(last_heartbeat, str) and last_heartbeat:
                last_dt = datetime.fromisoformat(last_heartbeat.replace("Z", "+00:00"))
            if last_dt:
                last_dt = last_dt if last_dt.tzinfo else last_dt.replace(tzinfo=timezone.utc)
                if (datetime.now(timezone.utc) - last_dt).total_seconds() > 120:
                    continue
        except Exception:
            logger.debug(
                "OR fallback: heartbeat TTL check failed for %s",
                process.get("process_id"),
                exc_info=True,
            )
        aid2 = process.get("account_id")
        if not aid2:
            continue
        st2 = db.get_strategy_state(str(aid2), "overnight_range")
        r2 = _or_ranges_from_strategy_state_row(st2)
        if r2:
            return r2
    return {}


def json_serialize_safe(obj: Any) -> Any:
    """
    Recursively serialize object to JSON-safe format.
    Replaces Infinity/NaN with null or large numbers.
    """
    if isinstance(obj, float):
        if math.isinf(obj):
            return None if obj > 0 else -999999.0  # Positive infinity -> null, negative -> large negative
        if math.isnan(obj):
            return None
        return obj
    elif isinstance(obj, dict):
        return {k: json_serialize_safe(v) for k, v in obj.items()}
    elif isinstance(obj, (list, tuple)):
        return [json_serialize_safe(item) for item in obj]
    else:
        return obj


def _ws_snapshot_fingerprint_orders(orders: Optional[List[Any]]) -> str:
    """Compact stable fingerprint for order lists (skip redundant WS payloads)."""
    if not orders:
        return "o:0"
    parts: List[str] = []
    for o in orders:
        if not isinstance(o, dict):
            continue
        oid = str(o.get("order_id") or o.get("id") or o.get("orderId") or "")
        st = str(o.get("status") or "")
        q = str(o.get("quantity") or o.get("size") or "")
        px = (
            o.get("price")
            or o.get("stopPrice")
            or o.get("limitPrice")
            or o.get("stop_price")
            or o.get("limit_price")
            or ""
        )
        parts.append(f"{oid}|{st}|{q}|{px}")
    payload = "\n".join(sorted(parts))
    if not payload:
        return "o:0"
    return "o:" + hashlib.sha256(payload.encode()).hexdigest()[:24]


def _ws_snapshot_fingerprint_positions(positions: Optional[List[Any]]) -> str:
    """Compact stable fingerprint for position lists."""
    if not positions:
        return "p:0"
    parts: List[str] = []
    for p in positions:
        if not isinstance(p, dict):
            continue
        pid = str(p.get("position_id") or p.get("id") or "")
        sym = str(p.get("symbol") or "")
        qty = str(p.get("quantity") or p.get("size") or "")
        ep = str(p.get("entryPrice") or p.get("entry_price") or "")
        parts.append(f"{pid}|{sym}|{qty}|{ep}")
    payload = "\n".join(sorted(parts))
    if not payload:
        return "p:0"
    return "p:" + hashlib.sha256(payload.encode()).hexdigest()[:24]


def _extract_symbol_from_contract(contract_str: Optional[str]) -> Optional[str]:
    """Extract symbol from contractId or symbolId (e.g. ``CON.F.US.MNQ.Z25`` → ``MNQ``)."""
    if not contract_str:
        return None
    contract_str = str(contract_str).rstrip(".")
    parts = contract_str.split(".")
    if len(parts) >= 2:
        if len(parts) >= 4:
            candidate = parts[-2]
            if candidate and candidate.isalpha() and candidate.isupper():
                return candidate
        candidate = parts[-1]
        if candidate and candidate.isalpha() and candidate.isupper():
            return candidate
    return None


def _order_terminal_for_chart(od: dict) -> bool:
    """True if order should not appear as a working order on the chart / activity strip."""
    st = od.get("status")
    u = str(st).strip().upper() if st is not None else ""
    if u in (
        "FILLED",
        "CANCELLED",
        "CANCELED",
        "REJECTED",
        "EXPIRED",
        "DONE",
        "COMPLETE",
        "REPLACED",
    ):
        return True
    try:
        fv = float(od.get("fillVolume") or od.get("fill_volume") or 0)
        qty = float(od.get("quantity") or od.get("size") or 0)
        if qty > 0 and fv >= qty and u not in ("OPEN", "PENDING", "SUSPENDED"):
            return True
    except (TypeError, ValueError):
        pass
    return False


def normalize_chart_order_dict(order: Any) -> Optional[Dict[str, Any]]:
    """Normalize a single broker order dict for chart GUI + WebSocket."""
    if not isinstance(order, dict):
        return None
    order_dict = order.copy()

    symbol = order_dict.get("symbol")
    if not symbol:
        contract_id = order_dict.get("contractId") or order_dict.get("contract_id")
        symbol_id = order_dict.get("symbolId") or order_dict.get("symbol_id")
        symbol = _extract_symbol_from_contract(symbol_id) or _extract_symbol_from_contract(contract_id)
        if symbol:
            order_dict["symbol"] = symbol

    side = order_dict.get("side")
    if not isinstance(side, str):
        order_dict["side"] = "BUY" if (side == 0 or side is None) else "SELL"

    status = order_dict.get("status")
    if isinstance(status, int):
        status_map = {0: "PENDING", 1: "OPEN", 2: "FILLED", 3: "CANCELLED", 4: "REJECTED"}
        order_dict["status"] = status_map.get(status, "UNKNOWN")
    elif isinstance(status, str):
        su = status.strip().upper()
        if su == "PENDING":
            order_dict["status"] = "OPEN"
        elif su == "SUSPENDED":
            order_dict["status"] = "SUSPENDED"
        elif su in ("FILLED", "CANCELLED", "CANCELED", "REJECTED"):
            order_dict["status"] = "CANCELLED" if su == "CANCELED" else su

    raw_type = order_dict.get("type", 0)
    type_num = raw_type if isinstance(raw_type, int) else None
    if isinstance(raw_type, int):
        type_map = {1: "LIMIT", 2: "MARKET", 4: "STOP"}
        order_dict["type"] = type_map.get(raw_type, str(raw_type))
    elif not isinstance(order_dict.get("type"), str):
        order_dict["type"] = str(raw_type)

    limit_price = order_dict.get("limitPrice") or order_dict.get("limit_price")
    stop_price = order_dict.get("stopPrice") or order_dict.get("stop_price")
    if limit_price is not None:
        order_dict["price"] = float(limit_price)
        order_dict.setdefault("limitPrice", float(limit_price))
    if stop_price is not None:
        order_dict["stop_price"] = float(stop_price)
        order_dict.setdefault("stopPrice", float(stop_price))
    if order_dict.get("type") == "STOP" or type_num == 4:
        if stop_price is not None:
            order_dict["price"] = float(stop_price)
        elif not order_dict.get("price"):
            trigger_price = order_dict.get("triggerPrice") or order_dict.get("trigger_price")
            order_dict["price"] = float(trigger_price) if trigger_price is not None else 0.0

    quantity = order_dict.get("quantity") or order_dict.get("size") or 0
    order_dict["quantity"] = quantity
    if not order_dict.get("id"):
        order_dict["id"] = order_dict.get("orderId") or order_dict.get("order_id")
    if not order_dict.get("orderId") and order_dict.get("id"):
        order_dict["orderId"] = order_dict["id"]
    return order_dict


def normalize_orders_for_chart(
    orders: Optional[List[Any]],
    *,
    drop_terminal: bool = True,
) -> List[Dict[str, Any]]:
    """Normalize broker order rows for chart table, overlays, and WebSocket."""
    out: List[Dict[str, Any]] = []
    for order in orders or []:
        normalized = normalize_chart_order_dict(order)
        if normalized is not None:
            out.append(normalized)
    if drop_terminal:
        out = [o for o in out if not _order_terminal_for_chart(o)]
    return out


def normalize_positions_for_chart(positions: Optional[List[Any]]) -> List[Dict[str, Any]]:
    """Lightweight position normalization for chart table + WebSocket (no quote fetch)."""
    out: List[Dict[str, Any]] = []
    for pos in positions or []:
        if not isinstance(pos, dict):
            continue
        pos_dict = pos.copy()
        symbol = pos_dict.get("symbol")
        if not symbol:
            contract_id = pos_dict.get("contractId") or pos_dict.get("contract_id")
            symbol_id = pos_dict.get("symbolId") or pos_dict.get("symbol_id")
            symbol = _extract_symbol_from_contract(symbol_id) or _extract_symbol_from_contract(contract_id)
            if symbol:
                pos_dict["symbol"] = symbol
        side = pos_dict.get("side")
        pos_type = pos_dict.get("type")
        if not isinstance(side, str):
            if pos_type == 1:
                pos_dict["side"] = "LONG"
            elif pos_type == 2:
                pos_dict["side"] = "SHORT"
            else:
                pos_dict["side"] = "LONG" if side in (0, None) else "SHORT"
        entry_price = pos_dict.get("entry_price") or pos_dict.get("entryPrice") or pos_dict.get("averagePrice")
        if entry_price is not None:
            pos_dict["entry_price"] = float(entry_price)
            pos_dict["entryPrice"] = float(entry_price)
        quantity_raw = pos_dict.get("quantity") or pos_dict.get("size") or 0
        quantity = abs(float(quantity_raw)) if quantity_raw else 0
        pos_dict["quantity"] = quantity
        pos_dict["size"] = quantity
        pid = pos_dict.get("id") or pos_dict.get("position_id")
        if pid is not None:
            pos_dict["id"] = pid
            pos_dict["position_id"] = pid
        for a, b in (
            ("unrealizedPnL", "unrealized_pnl"),
            ("stopLoss", "stop_loss"),
            ("takeProfit", "take_profit"),
        ):
            if a not in pos_dict and b in pos_dict:
                pos_dict[a] = pos_dict[b]
            if b not in pos_dict and a in pos_dict:
                pos_dict[b] = pos_dict[a]
        out.append(pos_dict)
    return out


# Global server instance for real-time charts
_chart_server = None
_chart_server_port = None
_chart_server_trading_bot = None
_chart_server_symbol = None
_chart_server_timeframe = None

# Track refresh tasks to avoid concurrent redundant refreshes (Global scope)
_refresh_locks = defaultdict(asyncio.Lock)
_last_refresh_time = defaultdict(float)

_PAGE_THEME_ROOT = Path(__file__).resolve().parents[1]
_PAGE_THEME_TEMPLATE = _PAGE_THEME_ROOT / "config" / "page_theme.template.json"
_PAGE_THEME_USER = _PAGE_THEME_ROOT / "config" / "page_theme.json"


def load_merged_page_theme_vars() -> Dict[str, Any]:
    """
    Merge CSS custom properties for the Master dashboard.
    ``page_theme.template.json`` is committed defaults; optional ``config/page_theme.json``
    overrides (gitignored) for local skins.
    """
    merged: Dict[str, Any] = {}
    for path in (_PAGE_THEME_TEMPLATE, _PAGE_THEME_USER):
        if not path.exists():
            continue
        try:
            with open(path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
        except Exception as exc:
            logger.debug("page theme read %s: %s", path, exc)
            continue
        chunk = raw.get("vars") if isinstance(raw.get("vars"), dict) else raw
        if not isinstance(chunk, dict):
            continue
        for k, v in chunk.items():
            if isinstance(k, str) and k.startswith("--") and not k.startswith("---"):
                merged[k] = v
    return merged


async def handle_page_theme(request):
    """GET JSON of ``--*`` CSS variables for Master GUI theming."""
    try:
        vars_out = load_merged_page_theme_vars()
        resp = web.json_response({"vars": vars_out})
        resp.headers["Access-Control-Allow-Origin"] = "*"
        return resp
    except Exception as e:
        logger.warning("page theme endpoint: %s", e)
        resp = web.json_response({"vars": {}, "error": str(e)}, status=500)
        resp.headers["Access-Control-Allow-Origin"] = "*"
        return resp


async def _start_chart_server(trading_bot, symbol: str, timeframe: str = '5m') -> int:
    """Start a simple HTTP server for real-time chart updates."""
    global _chart_server, _chart_server_port, _chart_server_trading_bot, _chart_server_symbol, _chart_server_timeframe
    
    if _chart_server is not None:
        # Server already running
        _chart_server_trading_bot = trading_bot
        _chart_server_symbol = symbol
        _chart_server_timeframe = timeframe
        return _chart_server_port
    
    app = web.Application()
    
    # Reduce aiohttp access log verbosity (only log warnings/errors, not every request)
    import logging
    import os
    if os.getenv("ACCESS_LOG_VERBOSE", "false").lower() not in ("1", "true", "yes", "on"):
        logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
    
    # Cache for latest bar to avoid rate limiting
    _latest_bar_cache = {}
    _last_bar_fetch_time = {}
    
    # Cache for account state to reduce slow API calls
    _account_state_cache = {}
    _account_state_cache_time = {}
    _account_state_cache_ttl = 10.0  # seconds
    
    async def handle_account_state(request):
        """Get account state - balance, P&L, compliance - fast path via AccountTracker."""
        try:
            account_id = None
            if hasattr(trading_bot, 'selected_account') and trading_bot.selected_account:
                if isinstance(trading_bot.selected_account, dict):
                    account_id = trading_bot.selected_account.get('id')
                else:
                    account_id = str(trading_bot.selected_account)
            
            if not account_id:
                return web.json_response({'error': 'No account selected'}, status=200)

            import time
            cache_key = str(account_id)
            cached = _account_state_cache.get(cache_key)
            cached_ts = _account_state_cache_time.get(cache_key, 0.0)
            now = time.monotonic()
            if cached and (now - cached_ts) < _account_state_cache_ttl:
                response = web.json_response(cached)
                response.headers['Access-Control-Allow-Origin'] = '*'
                return response

            # Ensure AccountTracker knows this account so DLL/MLL limits resolve.
            if hasattr(trading_bot, 'account_tracker') and trading_bot.account_tracker:
                aid = str(account_id)
                if aid not in trading_bot.account_tracker.accounts:
                    try:
                        acct_name = (
                            (trading_bot.selected_account or {}).get('name')
                            if isinstance(trading_bot.selected_account, dict)
                            else f"Account-{aid}"
                        ) or f"Account-{aid}"
                        acct_type = (
                            (trading_bot.selected_account or {}).get('type')
                            or (trading_bot.selected_account or {}).get('account_type')
                            if isinstance(trading_bot.selected_account, dict)
                            else 'unknown'
                        ) or 'unknown'
                        start_bal = float(
                            (trading_bot.selected_account or {}).get('balance', 0)
                            if isinstance(trading_bot.selected_account, dict)
                            else 0
                        )
                        trading_bot.account_tracker.initialize_account(
                            account_id=aid,
                            account_name=acct_name,
                            account_type=acct_type,
                            starting_balance=start_bal if start_bal > 0 else 0.0,
                        )
                        trading_bot.account_tracker.current_account_id = aid
                    except Exception:
                        logger.debug("lazy account_tracker init failed", exc_info=True)

            # FAST PATH: Try AccountTracker first (it's memory-only, no API calls)
            tracker_state = None
            if hasattr(trading_bot, 'account_tracker') and trading_bot.account_tracker:
                tracker_state = trading_bot.account_tracker.get_state(account_id=str(account_id))

            # Basic info from bot if tracker not available
            account_name = None
            account_type = None
            balance = 0.0
            if hasattr(trading_bot, 'selected_account') and trading_bot.selected_account:
                if isinstance(trading_bot.selected_account, dict):
                    account_name = trading_bot.selected_account.get('name') or trading_bot.selected_account.get('accountName')
                    account_type = trading_bot.selected_account.get('accountType') or trading_bot.selected_account.get('type')
                    balance = float(trading_bot.selected_account.get('balance', trading_bot.selected_account.get('currentBalance', 0.0)))

            starting_balance = float(tracker_state.get('starting_balance', 0.0) if tracker_state else 0.0)
            highest_eod_balance = float(tracker_state.get('highest_eod_balance', 0.0) if tracker_state else 0.0)
            realized_pnl = float(tracker_state.get('realized_pnl', 0.0) if tracker_state else 0.0)
            unrealized_pnl = float(tracker_state.get('unrealized_pnl', 0.0) if tracker_state else 0.0)
            total_pnl = float(tracker_state.get('total_pnl', realized_pnl + unrealized_pnl) if tracker_state else (realized_pnl + unrealized_pnl))
            current_balance = float(tracker_state.get('current_balance', 0.0) if tracker_state else 0.0)

            # DLL + MLL come from ``AccountTracker.get_compliance_status`` —
            # the canonical risk-state source. ``tracker.get_state`` does
            # NOT include the limit fields; reading
            # ``tracker_state['daily_loss_limit']`` always returned 0.0,
            # which silently hid the head-chip DLL pill in v2 Phase 3.6.
            # The compliance helper is also where the bot's risk gate
            # ultimately reads from, so the dashboard chip and the
            # consec-loss breaker now agree on the same numbers.
            compliance: Dict[str, Any] = {}
            if hasattr(trading_bot, 'account_tracker') and trading_bot.account_tracker:
                try:
                    compliance = trading_bot.account_tracker.get_compliance_status(
                        account_id=str(account_id)
                    ) or {}
                except Exception:
                    logger.debug("compliance_status fetch failed", exc_info=True)
                    compliance = {}
            daily_loss_limit = float(compliance.get('dll_limit') or 0.0)
            maximum_loss_limit = float(compliance.get('mll_limit') or 0.0)

            if not account_name and tracker_state:
                account_name = tracker_state.get('account_name')
            if not account_type and tracker_state:
                account_type = tracker_state.get('account_type')

            # Backfill PnL from trades only when AccountTracker has no state
            # for this account yet. Do NOT treat realized_pnl==0 as "empty" —
            # a flat session is valid zero. Session-only trade fetches also
            # caused alternating 0-trade / N-trade log spam when the selected
            # account had no fills in the current session.
            tracker_uninitialized = tracker_state is None or (
                starting_balance == 0.0 and current_balance == 0.0 and balance == 0.0
            )
            if tracker_uninitialized and account_id:
                try:
                    if not hasattr(handle_account_state, "_trade_stats_cache"):
                        handle_account_state._trade_stats_cache = {}
                    if not hasattr(handle_account_state, "_trade_stats_cache_time"):
                        handle_account_state._trade_stats_cache_time = {}
                    stats_cached = handle_account_state._trade_stats_cache.get(cache_key)
                    stats_cached_ts = handle_account_state._trade_stats_cache_time.get(cache_key, 0.0)
                    if not stats_cached or (now - stats_cached_ts) > 30.0:
                        from core.cli_command_parser import CLICommandParser
                        parser = CLICommandParser(trading_bot)
                        end_dt = datetime.now(timezone.utc)
                        start_dt = end_dt - timedelta(days=_GUI_PNL_BACKFILL_DAYS)
                        trades_result = await parser._handle_trades(
                            [start_dt.isoformat(), end_dt.isoformat()]
                        )
                        stats = trades_result.get('statistics', {}) if isinstance(trades_result, dict) else {}
                        trades_list = trades_result.get('trades', []) if isinstance(trades_result, dict) else []
                        total_pnl_stat = stats.get('total_pnl', None)
                        if total_pnl_stat is None:
                            total_pnl_stat = sum(float(t.get('pnl', 0) or 0) for t in trades_list)
                        stats_cached = {
                            'total_pnl': float(total_pnl_stat or 0.0),
                            'trades_count': len(trades_list)
                        }
                        handle_account_state._trade_stats_cache[cache_key] = stats_cached
                        handle_account_state._trade_stats_cache_time[cache_key] = now
                    if stats_cached:
                        realized_pnl = float(stats_cached.get('total_pnl', realized_pnl))
                except Exception as e:
                    logger.debug(f"Could not derive realized PnL from trades: {e}")

            # If unrealized PnL missing, compute from positions
            if unrealized_pnl == 0.0:
                try:
                    pos_resp = await handle_get_positions(None)
                    pos_data = {}
                    if hasattr(pos_resp, 'text') and pos_resp.text:
                        pos_data = json.loads(pos_resp.text)
                    elif hasattr(pos_resp, 'body') and pos_resp.body:
                        pos_data = json.loads(pos_resp.body.decode('utf-8'))
                    positions = pos_data.get('positions', [])
                    if positions:
                        unrealized_pnl = sum(
                            float(p.get('unrealized_pnl', p.get('unrealizedPnL', p.get('unrealizedPnl', 0))) or 0)
                            for p in positions
                        )
                except Exception as e:
                    logger.debug(f"Could not compute unrealized PnL from positions: {e}")

            total_pnl = realized_pnl + unrealized_pnl
            if current_balance == 0.0 and starting_balance > 0:
                current_balance = starting_balance + total_pnl
            if current_balance == 0.0 and balance:
                current_balance = balance
            if starting_balance == 0.0 and current_balance:
                starting_balance = current_balance - total_pnl
            if highest_eod_balance == 0.0 and starting_balance:
                highest_eod_balance = starting_balance

            # Risk chips for the head-summary (DLL + MLL). Values come
            # straight from ``AccountTracker.get_compliance_status`` so
            # the dashboard, the consec-loss breaker, and the risk-gate
            # logic all read identical numbers.
            #
            # DLL — daily loss limit. Resets at EOD; capped at the limit
            #   so profitable sessions report 100% buffer (not 130%).
            # MLL — maximum loss limit (trailing). Computed from
            #   ``current_balance - drawdown_threshold`` where
            #   ``drawdown_threshold = highest_eod_balance - mll_limit``.
            #   This is the more catastrophic of the two: breaching MLL
            #   permanently blows the prop account, no daily reset.
            dll_remaining = float(compliance.get('dll_remaining') or 0.0)
            mll_remaining = float(compliance.get('mll_remaining') or 0.0)
            trailing_loss = float(compliance.get('trailing_loss') or 0.0)
            dll_violated = bool(compliance.get('dll_violated', False))
            mll_violated = bool(compliance.get('mll_violated', False))

            dll_pct_remaining: Optional[float] = None
            if daily_loss_limit and daily_loss_limit > 0:
                dll_pct_remaining = max(0.0, min(1.0, dll_remaining / daily_loss_limit))

            mll_pct_remaining: Optional[float] = None
            if maximum_loss_limit and maximum_loss_limit > 0:
                mll_pct_remaining = max(0.0, min(1.0, mll_remaining / maximum_loss_limit))

            data = {
                'account_id': account_id,
                'account_name': account_name or 'Unknown',
                'account_type': account_type or 'unknown',
                'balance': current_balance,
                'starting_balance': starting_balance,
                'highest_eod_balance': highest_eod_balance,
                'realized_pnl': realized_pnl,
                'unrealized_pnl': unrealized_pnl,
                'total_pnl': total_pnl,
                'daily_loss_limit': daily_loss_limit,
                'maximum_loss_limit': maximum_loss_limit,
                'dll_remaining': round(dll_remaining, 2),
                'dll_pct_remaining': dll_pct_remaining,
                'dll_violated': dll_violated,
                'mll_remaining': round(mll_remaining, 2),
                'mll_pct_remaining': mll_pct_remaining,
                'mll_violated': mll_violated,
                'trailing_loss': round(trailing_loss, 2),
            }
            
            _account_state_cache[cache_key] = data
            _account_state_cache_time[cache_key] = now

            response = web.json_response(data)
            response.headers['Access-Control-Allow-Origin'] = '*'
            return response
        except Exception as e:
            logger.error(f"Error in handle_account_state: {e}")
            return web.json_response({'error': str(e)}, status=200)

    async def handle_quote(request):
        """Handle quote requests for real-time updates."""
        try:
            # Get symbol from query parameter, fallback to server's default symbol
            quote_symbol = request.query.get('symbol') or symbol
            tf = request.query.get('timeframe') or request.query.get('tf') or "1m"
            bar_period_sec = quote_timeframe_bucket_seconds(tf)
            quote = await trading_bot.get_market_quote(quote_symbol)
            if quote and "error" not in quote:
                # Use quote data to build bar instead of fetching historical data
                # This avoids rate limiting from too many historical data requests
                current_price = float(quote.get('last') or quote.get('lastPrice') or quote.get('bid') or 0)
                # Handle None volume (can be None even if key exists)
                volume_raw = quote.get('volume', 0)
                current_volume = int(volume_raw) if volume_raw is not None else 0
                current_time = datetime.now(timezone.utc)
                
                # Round timestamp down to the chart's bar period (1m, 5m, 1h, …)
                ts = int(current_time.timestamp())
                timestamp_sec = (ts // bar_period_sec) * bar_period_sec
                
                # Get or create latest bar from cache
                cache_key = f"{quote_symbol}_{tf}_latest"
                volume_cache_key = f"{quote_symbol}_{tf}_last_volume"
                
                if cache_key not in _latest_bar_cache:
                    # Initialize with current price
                    _latest_bar_cache[cache_key] = {
                        'time': timestamp_sec,
                        'open': current_price,
                        'high': current_price,
                        'low': current_price,
                        'close': current_price,
                        'volume': 0  # Start at 0, will be calculated from volume delta
                    }
                    _last_bar_fetch_time[cache_key] = current_time
                    # Initialize last known volume for delta calculation
                    _latest_bar_cache[volume_cache_key] = current_volume
                else:
                    # Update existing bar
                    cached_bar = _latest_bar_cache[cache_key]
                    last_volume = _latest_bar_cache.get(volume_cache_key, current_volume)
                    
                    # Check if we're in the same minute (for 1m bars) - if not, start new bar
                    if cached_bar['time'] != timestamp_sec:
                        # New bar - reset
                        cached_bar = {
                            'time': timestamp_sec,
                            'open': current_price,
                            'high': current_price,
                            'low': current_price,
                            'close': current_price,
                            'volume': max(0, current_volume - last_volume)  # Volume delta for this bar
                        }
                        # Update last volume reference
                        _latest_bar_cache[volume_cache_key] = current_volume
                    else:
                        # Update current bar
                        cached_bar['high'] = max(cached_bar['high'], current_price)
                        cached_bar['low'] = min(cached_bar['low'], current_price)
                        cached_bar['close'] = current_price
                        # Add volume delta since last update
                        volume_delta = max(0, current_volume - last_volume)
                        if volume_delta > 0:
                            cached_bar['volume'] = cached_bar.get('volume', 0) + volume_delta
                            _latest_bar_cache[volume_cache_key] = current_volume
                    
                    _latest_bar_cache[cache_key] = cached_bar
                
                latest_bar = _latest_bar_cache[cache_key]
                
                # Create response with CORS headers
                response = web.json_response({
                    'quote': quote,
                    'latest_bar': latest_bar,
                    'timestamp': datetime.now().isoformat()
                })
                response.headers['Access-Control-Allow-Origin'] = '*'
                response.headers['Access-Control-Allow-Methods'] = 'GET, OPTIONS'
                response.headers['Access-Control-Allow-Headers'] = '*'
                return response
            else:
                response = web.json_response({'error': 'No quote available'}, status=404)
                response.headers['Access-Control-Allow-Origin'] = '*'
                return response
        except Exception as e:
            logger.error(f"Error fetching quote: {e}")
            import traceback
            logger.error(traceback.format_exc())
            response = web.json_response({'error': str(e)}, status=500)
            response.headers['Access-Control-Allow-Origin'] = '*'
            return response
    
    async def handle_options(request):
        """Handle CORS preflight requests."""
        response = web.Response()
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Access-Control-Allow-Methods'] = 'GET, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = '*'
        return response
    
    async def handle_place_order(request):
        """Handle order placement requests."""
        try:
            data = await request.json()
            order_symbol = data.get('symbol', symbol)  # Use order_symbol to avoid scope conflict
            side = data.get('side')  # 'BUY' or 'SELL'
            quantity = int(data.get('quantity', 1))
            order_type = data.get('order_type', 'market')  # 'market', 'limit', 'stop'
            limit_price = data.get('limit_price')
            stop_price = data.get('stop_price')
            # Bracket inputs (support multiple key styles for backward compatibility)
            enable_bracket = data.get('enable_bracket', data.get('enableBracket', False))
            stop_loss_price = data.get('stop_loss_price', data.get('stopLossPrice'))
            take_profit_price = data.get('take_profit_price', data.get('takeProfitPrice'))
            stop_loss_ticks = data.get('stop_loss_ticks', data.get('stopLossTicks'))
            take_profit_ticks = data.get('take_profit_ticks', data.get('takeProfitTicks'))

            # Helpful debug log (kept INFO; very small payload)
            try:
                logger.info(
                    f"📥 /api/chart/order payload: symbol={order_symbol} side={side} qty={quantity} "
                    f"type={order_type} limit={limit_price} stop={stop_price} "
                    f"enable_bracket={enable_bracket} sl_price={stop_loss_price} tp_price={take_profit_price} "
                    f"sl_ticks={stop_loss_ticks} tp_ticks={take_profit_ticks}"
                )
            except Exception:
                logger.debug("chart order payload log failed", exc_info=True)

            # Place order via trading bot
            # Check if this is a bracket order (either order_type is 'bracket' or enable_bracket is True with prices)
            # Distinguish between bracket order type (uses ticks) vs orders with bracket enabled (uses prices)
            is_bracket_order_type = (order_type == 'bracket')
            has_bracket_enabled = (enable_bracket and stop_loss_price and take_profit_price)
            has_bracket_ticks = (enable_bracket and stop_loss_ticks is not None and take_profit_ticks is not None)
            
            if is_bracket_order_type:
                # Standalone bracket order - expects ticks, not prices
                # Convert prices to ticks if provided
                try:
                    quote = await trading_bot.get_market_quote(order_symbol)
                    if quote and 'last' in quote:
                        current_price = float(quote['last'])
                        tick_size = await trading_bot._get_tick_size(order_symbol)
                        
                        # Convert prices to ticks
                        if side.upper() == 'BUY':
                            stop_loss_ticks = int((current_price - float(stop_loss_price)) / tick_size) if stop_loss_price else None
                            take_profit_ticks = int((float(take_profit_price) - current_price) / tick_size) if take_profit_price else None
                            # Ensure correct signs: BUY stop loss should be negative, TP positive
                            if stop_loss_ticks and stop_loss_ticks > 0:
                                stop_loss_ticks = -stop_loss_ticks
                            if take_profit_ticks and take_profit_ticks < 0:
                                take_profit_ticks = -take_profit_ticks
                        else:  # SELL
                            stop_loss_ticks = int((float(stop_loss_price) - current_price) / tick_size) if stop_loss_price else None
                            take_profit_ticks = int((current_price - float(take_profit_price)) / tick_size) if take_profit_price else None
                            # Ensure correct signs: SELL stop loss should be positive, TP negative
                            if stop_loss_ticks and stop_loss_ticks < 0:
                                stop_loss_ticks = -stop_loss_ticks
                            if take_profit_ticks and take_profit_ticks > 0:
                                take_profit_ticks = -take_profit_ticks
                        
                        if not stop_loss_ticks or not take_profit_ticks:
                            result = {'error': 'Bracket orders require stop loss and take profit values'}
                        else:
                            # Use create_bracket_order with ticks
                            result = await trading_bot.create_bracket_order(
                                symbol=order_symbol,
                                side=side,
                                quantity=quantity,
                                stop_loss_ticks=stop_loss_ticks,
                                take_profit_ticks=take_profit_ticks
                            )
                    else:
                        result = {'error': 'Could not get market price for bracket order'}
                except Exception as e:
                    logger.error(f"Error creating bracket order: {e}")
                    import traceback
                    logger.error(traceback.format_exc())
                    result = {'error': f'Failed to create bracket order: {str(e)}'}
            elif has_bracket_ticks:
                # Bracket provided in ticks (common from older UI payloads)
                try:
                    sl = int(stop_loss_ticks)
                    tp = int(take_profit_ticks)
                    if sl <= 0 or tp <= 0:
                        result = {'error': 'Bracket ticks must be positive integers'}
                    else:
                        # Normalize sign conventions expected by TopStepX brackets
                        if side and side.upper() == 'BUY':
                            sl = -abs(sl)
                            tp = abs(tp)
                        else:
                            sl = abs(sl)
                            tp = -abs(tp)

                        result = await trading_bot.place_market_order(
                            symbol=order_symbol,
                            side=side,
                            quantity=quantity,
                            order_type='market',
                            stop_loss_ticks=sl,
                            take_profit_ticks=tp
                        )
                except Exception as e:
                    logger.error(f"Error placing market order with tick brackets: {e}")
                    import traceback
                    logger.error(traceback.format_exc())
                    result = {'error': f'Failed to place market order with tick brackets: {str(e)}'}
            elif has_bracket_enabled:
                # Order with bracket enabled - use prices directly
                if order_type == 'market':
                    # Market order with brackets - use prices
                    if not stop_loss_price or not take_profit_price:
                        result = {'error': 'Bracket orders require stop loss and take profit prices'}
                    else:
                        # Validate prices are positive (absolute prices, not relative)
                        if float(stop_loss_price) <= 0 or float(take_profit_price) <= 0:
                            result = {'error': 'Stop loss and take profit must be positive absolute prices'}
                        else:
                            try:
                                result = await trading_bot.create_bracket_order(
                                    symbol=order_symbol,
                                    side=side,
                                    quantity=quantity,
                                    stop_loss_price=float(stop_loss_price),
                                    take_profit_price=float(take_profit_price)
                                )
                            except Exception as e:
                                error_str = str(e)
                                # Track bracket errors for mode detection
                                if hasattr(trading_bot, '_last_bracket_error'):
                                    trading_bot._last_bracket_error = error_str
                                else:
                                    trading_bot._last_bracket_error = error_str
                                logger.error(f"Error creating market order with brackets: {e}")
                                import traceback
                                logger.error(traceback.format_exc())
                                result = {'error': f'Failed to create market order with brackets: {str(e)}'}
                elif order_type == 'stop' and enable_bracket:
                    # Stop order with brackets - use stop_price as entry, convert prices to ticks
                    if not stop_loss_price or not take_profit_price or not stop_price:
                        result = {'error': 'Stop bracket orders require stop price, stop loss price, and take profit price'}
                    else:
                        # Validate prices are positive (absolute prices, not relative)
                        if float(stop_price) <= 0 or float(stop_loss_price) <= 0 or float(take_profit_price) <= 0:
                            result = {'error': 'Stop price, stop loss, and take profit must be positive absolute prices'}
                        else:
                            try:
                                # Use stop_price as entry price, pass prices to place_oco_bracket_with_stop_entry
                                # It will convert prices to ticks using stop_price as entry
                                result = await trading_bot.place_oco_bracket_with_stop_entry(
                                    symbol=order_symbol,
                                    side=side,
                                    quantity=quantity,
                                    entry_price=float(stop_price),
                                    stop_loss_price=float(stop_loss_price),
                                    take_profit_price=float(take_profit_price)
                                )
                            except Exception as e:
                                logger.error(f"Error placing stop bracket order: {e}")
                                import traceback
                                logger.error(traceback.format_exc())
                                result = {'error': f'Failed to place stop bracket order: {str(e)}'}
                elif order_type == 'limit' and enable_bracket:
                    # Limit order with brackets - use limit_price as entry price, convert prices to ticks
                    if not stop_loss_price or not take_profit_price:
                        result = {'error': 'Limit orders with brackets require stop loss and take profit prices'}
                    else:
                        # Validate prices are positive (absolute prices, not relative)
                        if float(stop_loss_price) <= 0 or float(take_profit_price) <= 0:
                            result = {'error': 'Stop loss and take profit must be positive absolute prices'}
                        else:
                            try:
                                # Use limit_price as entry price for tick calculation
                                entry_price = float(limit_price)
                                tick_size = await trading_bot._get_tick_size(order_symbol)
                                
                                # Calculate ticks from prices using limit_price as entry
                                if side.upper() == 'BUY':
                                    # BUY limit: stop loss below entry, TP above entry
                                    stop_loss_ticks = int((entry_price - float(stop_loss_price)) / tick_size)
                                    take_profit_ticks = int((float(take_profit_price) - entry_price) / tick_size)
                                    # Ensure correct signs
                                    if stop_loss_ticks > 0:
                                        stop_loss_ticks = -stop_loss_ticks
                                    if take_profit_ticks < 0:
                                        take_profit_ticks = -take_profit_ticks
                                else:  # SELL
                                    # SELL limit: stop loss above entry, TP below entry
                                    stop_loss_ticks = int((float(stop_loss_price) - entry_price) / tick_size)
                                    take_profit_ticks = int((entry_price - float(take_profit_price)) / tick_size)
                                    # Ensure correct signs
                                    if stop_loss_ticks < 0:
                                        stop_loss_ticks = -stop_loss_ticks
                                    if take_profit_ticks > 0:
                                        take_profit_ticks = -take_profit_ticks
                                
                                result = await trading_bot.place_market_order(
                                    symbol=order_symbol,
                                    side=side,
                                    quantity=quantity,
                                    order_type='limit',
                                    limit_price=limit_price,
                                    stop_loss_ticks=stop_loss_ticks,
                                    take_profit_ticks=take_profit_ticks
                                )
                            except Exception as e:
                                logger.error(f"Error placing limit order with brackets: {e}")
                                import traceback
                                logger.error(traceback.format_exc())
                                result = {'error': f'Failed to place limit order with brackets: {str(e)}'}
                else:
                    result = {'error': 'Bracket orders require stop loss and take profit prices'}
            elif order_type == 'market':
                # Market order - if brackets enabled, use prices (handled above in has_bracket_enabled)
                if enable_bracket and stop_loss_price and take_profit_price:
                    # This should have been handled in the has_bracket_enabled block above
                    # But if we reach here, validate and use create_bracket_order
                    if float(stop_loss_price) <= 0 or float(take_profit_price) <= 0:
                        result = {'error': 'Stop loss and take profit must be positive absolute prices'}
                    else:
                        try:
                            result = await trading_bot.create_bracket_order(
                                symbol=order_symbol,
                                side=side,
                                quantity=quantity,
                                stop_loss_price=float(stop_loss_price),
                                take_profit_price=float(take_profit_price)
                            )
                        except Exception as e:
                            logger.error(f"Error placing market order with brackets: {e}")
                            import traceback
                            logger.error(traceback.format_exc())
                            result = {'error': f'Failed to place market order with brackets: {str(e)}'}
                else:
                    result = await trading_bot.place_market_order(
                        symbol=order_symbol,
                        side=side,
                        quantity=quantity,
                        order_type='market'
                    )
            elif order_type == 'limit':
                # Limit order - if brackets enabled, use limit_price as entry, convert prices to ticks
                if enable_bracket and stop_loss_price and take_profit_price:
                    # Validate prices are positive (absolute prices, not relative)
                    if float(stop_loss_price) <= 0 or float(take_profit_price) <= 0:
                        result = {'error': 'Stop loss and take profit must be positive absolute prices'}
                    else:
                        try:
                            # Use limit_price as entry price for tick calculation
                            entry_price = float(limit_price)
                            tick_size = await trading_bot._get_tick_size(order_symbol)
                            
                            # Calculate ticks from prices using limit_price as entry
                            if side.upper() == 'BUY':
                                # BUY limit: stop loss below entry, TP above entry
                                stop_loss_ticks = int((entry_price - float(stop_loss_price)) / tick_size)
                                take_profit_ticks = int((float(take_profit_price) - entry_price) / tick_size)
                                # Ensure correct signs
                                if stop_loss_ticks > 0:
                                    stop_loss_ticks = -stop_loss_ticks
                                if take_profit_ticks < 0:
                                    take_profit_ticks = -take_profit_ticks
                            else:  # SELL
                                # SELL limit: stop loss above entry, TP below entry
                                stop_loss_ticks = int((float(stop_loss_price) - entry_price) / tick_size)
                                take_profit_ticks = int((entry_price - float(take_profit_price)) / tick_size)
                                # Ensure correct signs
                                if stop_loss_ticks < 0:
                                    stop_loss_ticks = -stop_loss_ticks
                                if take_profit_ticks > 0:
                                    take_profit_ticks = -take_profit_ticks
                            
                            result = await trading_bot.place_market_order(
                                symbol=order_symbol,
                                side=side,
                                quantity=quantity,
                                order_type='limit',
                                limit_price=limit_price,
                                stop_loss_ticks=stop_loss_ticks,
                                take_profit_ticks=take_profit_ticks
                            )
                        except Exception as e:
                            logger.error(f"Error placing limit order with brackets: {e}")
                            import traceback
                            logger.error(traceback.format_exc())
                            result = {'error': f'Failed to place limit order with brackets: {str(e)}'}
                else:
                    result = await trading_bot.place_market_order(
                        symbol=order_symbol,
                        side=side,
                        quantity=quantity,
                        order_type='limit',
                        limit_price=limit_price
                    )
            elif order_type == 'stop' or order_type == 'stop_market':
                if enable_bracket and stop_loss_price and take_profit_price:
                    result = await trading_bot.place_oco_bracket_with_stop_entry(
                        symbol=order_symbol,
                        side=side,
                        quantity=quantity,
                        entry_price=float(stop_price),
                        stop_loss_price=float(stop_loss_price),
                        take_profit_price=float(take_profit_price)
                    )
                else:
                    if not stop_price:
                        result = {'error': 'Stop orders require stop_price'}
                    else:
                        result = await trading_bot.place_stop_order(
                            symbol=order_symbol,
                            side=side,
                            quantity=quantity,
                            stop_price=float(stop_price)
                        )
            elif order_type == 'stop-limit' or order_type == 'stop_limit':
                # Stop limit order: requires both stop_price and limit_price
                if not stop_price or not limit_price:
                    result = {'error': 'Stop limit orders require both stop_price and limit_price'}
                else:
                    try:
                        # Stop limit orders use type 5 in TopStepX API
                        # We need to call the broker adapter directly with type 5
                        target_account = trading_bot.selected_account['id'] if trading_bot.selected_account else None
                        if not target_account:
                            result = {'error': 'No account selected'}
                        else:
                            # Get contract ID
                            contract_id = trading_bot._get_contract_id(order_symbol)
                            side_value = 0 if side.upper() == "BUY" else 1
                            
                            # Round prices to tick size
                            tick_size = await trading_bot._get_tick_size(order_symbol)
                            rounded_stop = trading_bot._round_to_tick_size(float(stop_price), tick_size)
                            rounded_limit = trading_bot._round_to_tick_size(float(limit_price), tick_size)
                            
                            # Create stop limit order (type 5)
                            order_data = {
                                "accountId": int(target_account),
                                "contractId": contract_id,
                                "type": 5,  # Stop Limit order type
                                "side": side_value,
                                "size": quantity,
                                "stopPrice": rounded_stop,
                                "limitPrice": rounded_limit,
                                "customTag": trading_bot._generate_unique_custom_tag("stop_limit")
                            }
                            
                            # Use broker adapter to place order
                            from brokers.topstepx_adapter import TopStepXAdapter
                            if hasattr(trading_bot, 'broker_adapter'):
                                adapter = trading_bot.broker_adapter
                            else:
                                adapter = TopStepXAdapter(trading_bot.auth_manager)
                            
                            # Place via adapter's _make_request
                            response = await adapter._make_request(
                                'POST',
                                '/api/Order',
                                json=order_data
                            )
                            
                            if response and 'id' in response:
                                result = {'success': True, 'orderId': response.get('id'), 'order_id': response.get('id')}
                            else:
                                result = {'error': f'Stop limit order failed: {response}'}
                    except Exception as e:
                        logger.error(f"Error placing stop limit order: {e}")
                        import traceback
                        logger.error(traceback.format_exc())
                        result = {'error': f'Failed to place stop limit order: {str(e)}'}
            elif order_type == 'trailing-stop' or order_type == 'trailing_stop':
                # Trailing stop order: requires trail_amount
                trail_amount = data.get('trail_amount') or data.get('trailAmount')
                if not trail_amount:
                    result = {'error': 'Trailing stop orders require trail_amount'}
                else:
                    try:
                        result = await trading_bot.place_trailing_stop_order(
                            symbol=order_symbol,
                            side=side,
                            quantity=quantity,
                            trail_amount=float(trail_amount)
                        )
                    except Exception as e:
                        logger.error(f"Error placing trailing stop order: {e}")
                        result = {'error': f'Failed to place trailing stop order: {str(e)}'}
            else:
                result = {'error': f'Unsupported order type: {order_type}'}
            
            if isinstance(result, dict) and not result.get("error"):
                if result.get("success") or result.get("orderId") or result.get("order_id"):
                    aid = None
                    if hasattr(trading_bot, "selected_account") and trading_bot.selected_account:
                        if isinstance(trading_bot.selected_account, dict):
                            aid = trading_bot.selected_account.get("id")
                        else:
                            aid = trading_bot.selected_account
                    if aid and getattr(trading_bot, "state_cache", None):
                        try:
                            trading_bot.state_cache.invalidate_orders(str(aid))
                        except Exception:
                            pass
                    if hasattr(handle_get_orders, "_cache"):
                        handle_get_orders._cache.clear()

            response = web.json_response(result)
            response.headers['Access-Control-Allow-Origin'] = '*'
            return response
        except Exception as e:
            logger.error(f"Error placing order: {e}")
            import traceback
            logger.error(traceback.format_exc())
            response = web.json_response({'error': str(e)}, status=500)
            response.headers['Access-Control-Allow-Origin'] = '*'
            return response
    
    async def handle_get_positions(request):
        """Handle position requests - canonicalize to match TradingChart.tsx format."""
        try:
            # OPTIMIZATION: Use StateCache for 99% faster access
            account_id = None
            if hasattr(trading_bot, 'selected_account') and trading_bot.selected_account:
                if isinstance(trading_bot.selected_account, dict):
                    account_id = trading_bot.selected_account.get('id')
                else:
                    account_id = str(trading_bot.selected_account)
            
            # Try StateCache first (sub-millisecond, event-driven)
            if hasattr(trading_bot, 'state_cache') and trading_bot.state_cache and account_id:
                positions = await trading_bot.state_cache.get_positions(account_id)
                if positions is None:
                    positions = []
                logger.debug(f"✅ Fetched {len(positions)} positions from StateCache (cached)")
            else:
                # Fallback to direct API call
                positions = await trading_bot.get_open_positions()
                logger.debug(f"✅ Fetched {len(positions) if positions else 0} positions from API (fallback)")
            
            def extract_symbol_from_contract(contract_str):
                """Extract symbol from contractId or symbolId (e.g., 'CON.F.US.MNQ.Z25' -> 'MNQ')."""
                if not contract_str:
                    return None
                # Strip any trailing dots first
                contract_str = str(contract_str).rstrip('.')
                parts = contract_str.split('.')
                # ContractManager uses parts[-2] for contractId like "CON.F.US.MNQ.Z25"
                # For symbolId like "F.US.MNQ", we want parts[-1]
                if len(parts) >= 2:
                    # Try parts[-2] first (for contractId format like "CON.F.US.MNQ.Z25")
                    if len(parts) >= 4:
                        candidate = parts[-2]
                        if candidate and candidate.isalpha() and candidate.isupper():
                            return candidate
                    # Fallback to parts[-1] (for symbolId format like "F.US.MNQ")
                    candidate = parts[-1]
                    if candidate and candidate.isalpha() and candidate.isupper():
                        return candidate
                return None
            
            positions_list = []
            for pos in positions or []:
                if isinstance(pos, dict):
                    pos_dict = pos.copy()
                    
                    # Extract symbol from contractId/symbolId if missing
                    symbol = pos_dict.get('symbol')
                    if not symbol:
                        contract_id = pos_dict.get('contractId') or pos_dict.get('contract_id')
                        symbol_id = pos_dict.get('symbolId') or pos_dict.get('symbol_id')
                        symbol = extract_symbol_from_contract(symbol_id) or extract_symbol_from_contract(contract_id)
                        if symbol:
                            pos_dict['symbol'] = symbol
                    
                    # Set side from type if missing (1 = LONG, 2 = SHORT in TopStepX)
                    side = pos_dict.get('side')
                    pos_type = pos_dict.get('type')
                    if not isinstance(side, str):
                        if pos_type == 1:
                            side = 'LONG'
                        elif pos_type == 2:
                            side = 'SHORT'
                        else:
                            side = 'LONG'  # Default
                        pos_dict['side'] = side
                    
                    # Set entry_price from averagePrice if missing
                    entry_price = pos_dict.get('entry_price') or pos_dict.get('entryPrice')
                    if not entry_price:
                        entry_price = pos_dict.get('averagePrice')
                    if entry_price is not None:
                        pos_dict['entry_price'] = float(entry_price)
                        pos_dict['entryPrice'] = float(entry_price)
                    
                    # Ensure quantity/size (always use absolute value for quantity)
                    quantity_raw = pos_dict.get('quantity') or pos_dict.get('size') or 0
                    quantity = abs(float(quantity_raw)) if quantity_raw else 0
                    pos_dict['quantity'] = quantity
                    pos_dict['size'] = quantity
                    
                    # Calculate unrealized PnL if not provided or is 0
                    unrealized_pnl = pos_dict.get('unrealizedPnL') or pos_dict.get('unrealized_pnl') or pos_dict.get('unrealizedPnl')
                    if unrealized_pnl is None or unrealized_pnl == 0:
                        # Try to calculate from current price and entry price
                        try:
                            entry_price = pos_dict.get('entryPrice') or pos_dict.get('entry_price')
                            current_price = pos_dict.get('currentPrice') or pos_dict.get('current_price') or pos_dict.get('markPrice')
                            
                            # If no current price, fetch it from market quote
                            if not current_price and symbol:
                                try:
                                    quote = await trading_bot.get_market_quote(symbol)
                                    if quote and "error" not in quote:
                                        current_price = quote.get('last') or quote.get('lastPrice') or quote.get('bid')
                                        if current_price:
                                            pos_dict['currentPrice'] = float(current_price)
                                            pos_dict['current_price'] = float(current_price)
                                except Exception as e:
                                    logger.debug(f"Could not fetch quote for {symbol}: {e}")
                            
                            if entry_price and current_price and quantity and symbol:
                                # Get tick size and point value for symbol
                                if hasattr(trading_bot, 'risk_manager'):
                                    tick_size = trading_bot.risk_manager.get_tick_size(symbol)
                                    point_value = trading_bot.risk_manager.get_point_value(symbol)
                                elif hasattr(trading_bot, '_get_tick_size') and hasattr(trading_bot, '_get_point_value'):
                                    tick_size = trading_bot._get_tick_size(symbol)
                                    point_value = trading_bot._get_point_value(symbol)
                                else:
                                    # Default values (correct point values for micro contracts)
                                    tick_size_map = {'MNQ': 0.25, 'MES': 0.25, 'NQ': 0.25, 'ES': 0.25, 'YM': 1.0}
                                    point_value_map = {'MNQ': 2.0, 'MES': 5.0, 'NQ': 20.0, 'ES': 50.0, 'YM': 5.0}
                                    tick_size = tick_size_map.get(symbol.upper(), 0.25)
                                    point_value = point_value_map.get(symbol.upper(), 2.0)
                                
                                # Calculate tick value: dollar value per tick
                                # tick_value = point_value * tick_size
                                # For MNQ: $5 per point * 0.25 points per tick = $1.25 per tick
                                tick_value = point_value * tick_size
                                
                                # Calculate price difference
                                entry_price_float = float(entry_price)
                                current_price_float = float(current_price)
                                
                                # Determine direction and calculate ticks
                                if side.upper() in ['LONG', 'BUY', '0']:
                                    # LONG: profit when current > entry
                                    price_diff = current_price_float - entry_price_float
                                    direction = 1 if price_diff >= 0 else -1  # 1 if favorable, -1 if unfavorable
                                else:  # SHORT
                                    # SHORT: profit when current < entry
                                    price_diff = entry_price_float - current_price_float
                                    direction = 1 if price_diff >= 0 else -1  # 1 if favorable, -1 if unfavorable
                                
                                # Calculate ticks from entry
                                ticks_from_entry = abs(price_diff) / tick_size
                                
                                # Calculate PnL: tick_value * ticks_from_entry * direction * quantity
                                unrealized_pnl = tick_value * ticks_from_entry * direction * float(quantity)
                                
                                pos_dict['unrealizedPnL'] = unrealized_pnl
                                pos_dict['unrealized_pnl'] = unrealized_pnl
                                pos_dict['unrealizedPnl'] = unrealized_pnl
                            else:
                                unrealized_pnl = 0.0
                        except Exception as e:
                            logger.debug(f"Could not calculate PnL for position {pos_dict.get('id')}: {e}")
                            unrealized_pnl = 0.0
                    
                    # Fetch linked orders to get stopLoss/takeProfit if not already present
                    # DISABLED BY DEFAULT - This causes slow Order/search calls for each position
                    # Only use linked orders that are explicitly bracket-linked (not standalone orders)
                    # Set include_linked=1 in query params to enable this (slower but more complete)
                    fetch_linked = False  # Disabled by default to avoid slow Order/search calls
                    if fetch_linked and not pos_dict.get('stopLoss') and not pos_dict.get('stop_loss'):
                        try:
                            pos_id = pos_dict.get('id') or pos_dict.get('position_id')
                            if pos_id:
                                linked_orders = await trading_bot.get_linked_orders(pos_id)
                                if linked_orders and not isinstance(linked_orders, dict):
                                    # Extract stop loss and take profit from linked orders
                                    # Only process orders that are explicitly bracket-linked (have AutoBracket tag)
                                    for order in linked_orders:
                                        custom_tag = order.get('customTag', '') or ''
                                        # Only process bracket-linked orders
                                        if 'AutoBracket' not in custom_tag:
                                            continue  # Skip standalone orders
                                        
                                        order_type = order.get('type', 0)
                                        order_side = order.get('side', 0)
                                        pos_side_val = 0 if side == 'LONG' else 1
                                        
                                        # For LONG positions: stop loss is SELL stop (type 4) with -SL tag, TP is SELL limit (type 1) with -TP tag
                                        # For SHORT positions: stop loss is BUY stop (type 4) with -SL tag, TP is BUY limit (type 1) with -TP tag
                                        if pos_side_val == 0:  # LONG
                                            if order_side == 1:  # SELL
                                                if order_type == 4 and ('-SL' in custom_tag or 'SL' in custom_tag):  # Stop loss
                                                    stop_price = order.get('stopPrice') or order.get('limitPrice')
                                                    if stop_price:
                                                        pos_dict['stopLoss'] = float(stop_price)
                                                        pos_dict['stop_loss'] = float(stop_price)
                                                elif order_type == 1 and ('-TP' in custom_tag or 'TP' in custom_tag):  # Take profit
                                                    tp_price = order.get('limitPrice')
                                                    if tp_price:
                                                        pos_dict['takeProfit'] = float(tp_price)
                                                        pos_dict['take_profit'] = float(tp_price)
                                        elif pos_side_val == 1:  # SHORT
                                            if order_side == 0:  # BUY
                                                if order_type == 4 and ('-SL' in custom_tag or 'SL' in custom_tag):  # Stop loss
                                                    stop_price = order.get('stopPrice') or order.get('limitPrice')
                                                    if stop_price:
                                                        pos_dict['stopLoss'] = float(stop_price)
                                                        pos_dict['stop_loss'] = float(stop_price)
                                                elif order_type == 1 and ('-TP' in custom_tag or 'TP' in custom_tag):  # Take profit
                                                    tp_price = order.get('limitPrice')
                                                    if tp_price:
                                                        pos_dict['takeProfit'] = float(tp_price)
                                                        pos_dict['take_profit'] = float(tp_price)
                        except Exception as e:
                            logger.debug(f"Could not fetch linked orders for position enrichment: {e}")
                    
                    # Ensure all field aliases exist (both camelCase and snake_case)
                    if 'entryPrice' not in pos_dict and 'entry_price' in pos_dict:
                        pos_dict['entryPrice'] = pos_dict['entry_price']
                    if 'entry_price' not in pos_dict and 'entryPrice' in pos_dict:
                        pos_dict['entry_price'] = pos_dict['entryPrice']
                    if 'stopLoss' not in pos_dict and 'stop_loss' in pos_dict:
                        pos_dict['stopLoss'] = pos_dict['stop_loss']
                    if 'stop_loss' not in pos_dict and 'stopLoss' in pos_dict:
                        pos_dict['stop_loss'] = pos_dict['stopLoss']
                    if 'takeProfit' not in pos_dict and 'take_profit' in pos_dict:
                        pos_dict['takeProfit'] = pos_dict['take_profit']
                    if 'take_profit' not in pos_dict and 'takeProfit' in pos_dict:
                        pos_dict['take_profit'] = pos_dict['takeProfit']
                    if 'unrealizedPnL' not in pos_dict and 'unrealized_pnl' in pos_dict:
                        pos_dict['unrealizedPnL'] = pos_dict['unrealized_pnl']
                    if 'unrealized_pnl' not in pos_dict and 'unrealizedPnL' in pos_dict:
                        pos_dict['unrealized_pnl'] = pos_dict['unrealizedPnL']
                    if 'unrealizedPnl' not in pos_dict:
                        pos_dict['unrealizedPnl'] = pos_dict.get('unrealizedPnL') or pos_dict.get('unrealized_pnl', 0)
                    
                    positions_list.append(pos_dict)
                else:
                    # Fallback: Convert Position object to dict (shouldn't happen with current implementation)
                    pos_dict = {
                        'position_id': getattr(pos, 'position_id', None),
                        'id': getattr(pos, 'position_id', None),
                        'symbol': getattr(pos, 'symbol', None),
                        'side': getattr(pos, 'side', None),
                        'quantity': getattr(pos, 'quantity', 0),
                        'entryPrice': getattr(pos, 'entry_price', None),
                        'entry_price': getattr(pos, 'entry_price', None),
                        'currentPrice': getattr(pos, 'current_price', None),
                        'current_price': getattr(pos, 'current_price', None),
                        'stopLoss': getattr(pos, 'stop_loss', None),
                        'stop_loss': getattr(pos, 'stop_loss', None),
                        'takeProfit': getattr(pos, 'take_profit', None),
                        'take_profit': getattr(pos, 'take_profit', None),
                        'unrealizedPnl': getattr(pos, 'unrealized_pnl', None),
                        'unrealized_pnl': getattr(pos, 'unrealized_pnl', None),
                        'account_id': getattr(pos, 'account_id', None),
                        'contractId': getattr(pos, 'contract_id', None),
                        'contract_id': getattr(pos, 'contract_id', None),
                    }
                    if hasattr(pos, 'raw_data') and pos.raw_data:
                        pos_dict.update(pos.raw_data)
                    positions_list.append(pos_dict)
            
            response = web.json_response({'positions': positions_list})
            response.headers['Access-Control-Allow-Origin'] = '*'
            return response
        except Exception as e:
            logger.error(f"❌ Error fetching positions for chart: {e}")
            import traceback
            logger.error(traceback.format_exc())
            # Return empty positions array instead of 500 error to keep chart functional
            response = web.json_response({'error': str(e), 'positions': []}, status=200)
            response.headers['Access-Control-Allow-Origin'] = '*'
            return response
    
    async def handle_get_orders(request):
        """Handle order requests - canonicalize to match TradingChart.tsx format."""
        try:
            # Get account ID from query or use default (handle None request for WebSocket broadcasts)
            account_id = request.query.get('account_id') if request else None
            
            # OPTIMIZATION: Use StateCache for 99% faster access
            if not account_id and hasattr(trading_bot, 'selected_account') and trading_bot.selected_account:
                if isinstance(trading_bot.selected_account, dict):
                    account_id = trading_bot.selected_account.get('id')
                else:
                    account_id = str(trading_bot.selected_account)
            if account_id is not None:
                account_id = str(account_id).strip()
            
            # include_linked=1 is opt-in because it can be very slow (per-position /api/Order/search calls)
            include_linked = False
            if request:
                include_linked_raw = request.query.get('include_linked')
                include_linked = str(include_linked_raw).lower() in ('1', 'true', 'yes', 'y', 'on')

            no_cache = False
            if request:
                no_cache = str(request.query.get("no_cache", "")).lower() in ("1", "true", "yes", "y", "on")

            # Prevent refresh storms (polling + multiple tabs) from stacking slow queries
            import time
            if not hasattr(handle_get_orders, "_lock"):
                handle_get_orders._lock = asyncio.Lock()
            if not hasattr(handle_get_orders, "_cache"):
                handle_get_orders._cache = {}
            cache_key = f"{account_id or 'default'}|include_linked={include_linked}"
            cached = handle_get_orders._cache.get(cache_key)
            now = time.monotonic()
            cache_ttl = 10.0 if include_linked else 8.0
            if not no_cache and cached and (now - cached.get("ts", 0.0)) < cache_ttl:
                response = web.json_response(cached["payload"])
                response.headers['Access-Control-Allow-Origin'] = '*'
                return response

            async with handle_get_orders._lock:
                cached = handle_get_orders._cache.get(cache_key)
                now = time.monotonic()
                if not no_cache and cached and (now - cached.get("ts", 0.0)) < cache_ttl:
                    response = web.json_response(cached["payload"])
                    response.headers['Access-Control-Allow-Origin'] = '*'
                    return response

                # OPTIMIZATION: Fetch orders using StateCache (99% faster)
                if hasattr(trading_bot, 'state_cache') and trading_bot.state_cache and account_id:
                    orders = await trading_bot.state_cache.get_orders(account_id)
                    if orders is None:
                        orders = []
                    logger.debug(f"✅ Fetched {len(orders)} orders from StateCache (cached)")
                else:
                    # Fallback to direct API call
                    orders = await trading_bot.get_open_orders(account_id=account_id)
                    logger.debug(f"✅ Fetched {len(orders) if orders else 0} standalone orders from API (fallback)")

                # Optional linked order enrichment (slow when re-fetching per position)
                if include_linked:
                    try:
                        positions = await trading_bot.get_open_positions(account_id=account_id)
                        all_orders_snapshot = list(orders or [])
                        linked_orders_all = []
                        adapter = getattr(trading_bot, "broker_adapter", None)
                        for pos in positions or []:
                            pos_id = pos.get('id') or pos.get('position_id')
                            if not pos_id:
                                continue
                            pos_data = pos if isinstance(pos, dict) else None
                            try:
                                if adapter and hasattr(adapter, "get_linked_orders"):
                                    linked_orders = await adapter.get_linked_orders(
                                        str(pos_id),
                                        account_id=account_id,
                                        all_orders=all_orders_snapshot,
                                        position_data=pos_data,
                                    )
                                else:
                                    linked_orders = await trading_bot.get_linked_orders(
                                        str(pos_id), account_id=account_id
                                    )
                                if linked_orders and isinstance(linked_orders, list):
                                    linked_orders_all.extend(linked_orders)
                            except Exception as e:
                                logger.debug(f"Could not fetch linked orders for position {pos_id}: {e}")

                        if linked_orders_all:
                            logger.debug(f"✅ Found {len(linked_orders_all)} linked orders (stop/take profit)")
                            if orders is None:
                                orders = []
                            existing_order_ids = {str(order.get('id') or order.get('orderId')) for order in orders}
                            for linked_order in linked_orders_all:
                                order_id = str(linked_order.get('id') or linked_order.get('orderId'))
                                if order_id and order_id not in existing_order_ids and order_id != 'None':
                                    orders.append(linked_order)
                                    existing_order_ids.add(order_id)
                            logger.debug(f"✅ Total orders (standalone + linked): {len(orders)}")
                    except Exception as e:
                        logger.debug(f"Could not fetch linked orders: {e}")
            
            def extract_symbol_from_contract(contract_str):
                return _extract_symbol_from_contract(contract_str)
            
            orders_list = normalize_orders_for_chart(orders, drop_terminal=True)
            
            # Group orders by parent/child relationships for bracket display
            order_groups = {}
            position_groups = {}  # Track position-linked brackets separately
            
            # First pass: Group orders by parentOrderId
            for order in orders_list:
                order_id = str(order.get('id') or order.get('orderId', ''))
                parent_id = order.get('parentOrderId') or order.get('parent_order_id')
                position_id = order.get('positionId') or order.get('position_id')
                
                # Check if this order is linked to a position (bracket order)
                if position_id and not parent_id:
                    # This is a bracket order linked to a position
                    pos_key = str(position_id)
                    if pos_key not in position_groups:
                        position_groups[pos_key] = []
                    position_groups[pos_key].append(order)
                elif parent_id:
                    # This is a child order (SL or TP) with a parent order
                    parent_key = str(parent_id)
                    if parent_key not in order_groups:
                        order_groups[parent_key] = {'parent': None, 'children': []}
                    order_groups[parent_key]['children'].append(order)
                else:
                    # This is a parent order (or standalone)
                    if order_id not in order_groups:
                        order_groups[order_id] = {'parent': None, 'children': []}
                    order_groups[order_id]['parent'] = order
            
            # Convert order_groups to list format for easier frontend rendering
            groups_list = []
            for group_id, group_data in order_groups.items():
                if group_data['parent']:
                    groups_list.append({
                        'id': group_id,
                        'type': 'order',
                        'parent': group_data['parent'],
                        'children': group_data['children']
                    })
            
            # Add position groups (brackets linked to positions)
            try:
                positions = []
                if position_groups:
                    positions = await trading_bot.get_open_positions(account_id=account_id)
                for pos in positions or []:
                    pos_id = str(pos.get('id') or pos.get('position_id') or '')
                    if pos_id in position_groups:
                        # Create a pseudo-order entry for the position
                        position_entry = {
                            'id': f'POS_{pos_id}',
                            'symbol': pos.get('symbol'),
                            'side': pos.get('side'),
                            'quantity': pos.get('quantity') or pos.get('size'),
                            'price': pos.get('entryPrice') or pos.get('entry_price') or pos.get('averagePrice'),
                            'type': 'POSITION',
                            'status': 'OPEN',
                            'position_id': pos_id
                        }
                        groups_list.append({
                            'id': f'POS_{pos_id}',
                            'type': 'position',
                            'parent': position_entry,
                            'children': position_groups[pos_id]
                        })
                        logger.debug(f"✅ Grouped {len(position_groups[pos_id])} bracket orders under position {pos_id}")
            except Exception as e:
                logger.debug(f"Could not group position brackets: {e}")
            
            response = web.json_response({
                'orders': orders_list,
                'groups': groups_list
            })
            response.headers['Access-Control-Allow-Origin'] = '*'
            # Cache response for a short TTL to avoid poll storms
            if not no_cache and hasattr(handle_get_orders, "_cache"):
                handle_get_orders._cache[cache_key] = {
                    "ts": time.monotonic(),
                    "payload": {'orders': orders_list, 'groups': groups_list}
                }
            return response
        except Exception as e:
            logger.error(f"❌ Error fetching orders for chart: {e}")
            import traceback
            logger.error(traceback.format_exc())
            response = web.json_response({'error': str(e), 'orders': []}, status=200)
            response.headers['Access-Control-Allow-Origin'] = '*'
            return response
    
    async def handle_get_contracts(request):
        """Handle contract list requests."""
        try:
            # Force fresh fetch to ensure we have latest contracts
            contracts = await trading_bot.get_available_contracts(use_cache=False)
            logger.info(f"📋 Fetched {len(contracts)} contracts for chart dropdown")
            
            if not contracts:
                logger.warning("⚠️ No contracts returned - attempting re-fetch...")
                # Try one more time with a slight delay
                import asyncio
                await asyncio.sleep(0.5)
                contracts = await trading_bot.get_available_contracts(use_cache=False)
                logger.info(f"📋 Re-fetched {len(contracts)} contracts")
            
            def extract_root_symbol(contract_id: str) -> Optional[str]:
                """
                TopStepX contract IDs often look like: CON.F.US.MNQ.H26
                We want MNQ as the dropdown symbol.
                """
                if not contract_id:
                    return None
                try:
                    parts = str(contract_id).split(".")
                    # CON.F.US.<SYM>.<EXP>
                    if len(parts) >= 5:
                        return parts[3].strip().upper()
                except Exception:
                    logger.debug("extract_root_symbol failed for %r", contract_id, exc_info=True)
                return None

            # Group by symbol
            by_symbol = {}
            for c in contracts:
                contract_id = c.get('contractId') or c.get('ContractId') or c.get('id') or c.get('contract_id') or ''
                
                # ALWAYS try to extract root symbol from contract ID FIRST (most reliable)
                sym = extract_root_symbol(contract_id)
                
                # Only if that fails, fall back to name/symbol fields
                if not sym:
                    sym = (
                        c.get('symbol')
                        or c.get('Symbol')
                        or c.get('name')
                        or 'Unknown'
                    )
                # Skip 'Unknown' symbols and empty symbols
                if sym and sym != 'Unknown' and sym.strip():
                    if sym not in by_symbol:
                        by_symbol[sym] = []
                    by_symbol[sym].append({'id': contract_id, 'description': c.get('description', '')})
            
            symbols_list = sorted(list(by_symbol.keys()))
            logger.info(f"✅ Grouped into {len(symbols_list)} unique symbols: {symbols_list[:10] if len(symbols_list) > 10 else symbols_list}")
            
            # Find the active MNQ contract (most recent or highest volume)
            default_symbol = None
            if 'MNQ' in by_symbol:
                mnq_contracts = by_symbol['MNQ']
                if mnq_contracts:
                    # Sort by contractId to get the most recent expiration (e.g., H26 > G26)
                    sorted_mnq = sorted(mnq_contracts, key=lambda x: x.get('id', ''), reverse=True)
                    default_symbol = sorted_mnq[0].get('id') if sorted_mnq else None
                    logger.info(f"📌 Default MNQ contract: {default_symbol}")
            
            response = web.json_response({
                'contracts': by_symbol, 
                'symbols': symbols_list,
                'default_symbol': default_symbol  # Return the active MNQ contract ID
            })
            response.headers['Access-Control-Allow-Origin'] = '*'
            return response
        except Exception as e:
            logger.error(f"❌ Error fetching contracts: {e}")
            import traceback
            logger.error(traceback.format_exc())
            response = web.json_response({'error': str(e), 'contracts': {}, 'symbols': []}, status=500)
            response.headers['Access-Control-Allow-Origin'] = '*'
            return response
    
    async def handle_reload_data(request):
        """Handle chart data reload requests (broker API and/or canonical Databento CSVs)."""
        try:
            reload_symbol = request.query.get("symbol") or symbol
            reload_timeframe = request.query.get("timeframe") or timeframe
            limit = int(request.query.get("limit", 100))
            source = (
                request.query.get("source") or os.environ.get("CHART_RELOAD_SOURCE") or "auto"
            ).strip().lower()
            if source not in ("api", "databento", "auto"):
                source = "auto"

            # Optional explicit time window (Unix seconds OR ISO 8601). When
            # provided, both the API path and the Databento path slice their
            # results to [start, end] so callers (e.g. the v2 trade-recap
            # modal) can fetch the exact window of an old trade.
            from datetime import datetime as _dt_in, timezone as _tz_in
            def _parse_ts(raw: Optional[str]) -> Optional[_dt_in]:
                if raw is None or raw == "":
                    return None
                s = str(raw).strip()
                # Unix seconds (or ms)
                try:
                    n = float(s)
                    if n > 1e12:
                        n = n / 1000.0
                    return _dt_in.fromtimestamp(n, tz=_tz_in.utc)
                except (TypeError, ValueError):
                    pass
                # ISO 8601
                try:
                    s2 = s.replace("Z", "+00:00")
                    dt0 = _dt_in.fromisoformat(s2)
                    if dt0.tzinfo is None:
                        dt0 = dt0.replace(tzinfo=_tz_in.utc)
                    return dt0.astimezone(_tz_in.utc)
                except (TypeError, ValueError):
                    return None
            window_start = _parse_ts(request.query.get("start"))
            window_end = _parse_ts(request.query.get("end"))

            chart_data: List[Dict[str, Any]] = []
            history_source: Optional[str] = None

            # AUTO: hybrid Databento + API. The broker API is current-to-the-tick
            # but only serves the most recent ~5 days of bars; Databento covers
            # multi-year history but lags by ~1 day. So we stitch:
            #   • window entirely older than api_horizon  →  Databento only
            #   • window entirely within api_horizon       →  API only
            #   • window straddles                         →  Databento up to
            #     api_horizon + API from api_horizon → now (deduped on time).
            # Tunable via CHART_API_HORIZON_DAYS env var.
            try:
                _api_horizon_days = float(os.environ.get("CHART_API_HORIZON_DAYS", "5"))
            except (TypeError, ValueError):
                _api_horizon_days = 5.0

            def _tf_seconds(tf_str: str) -> int:
                try:
                    from core.chart_databento_loader import _timeframe_seconds as _tfs
                    return int(_tfs(tf_str))
                except Exception:
                    m = {"1s": 1, "5s": 5, "15s": 15, "30s": 30,
                         "1m": 60, "5m": 300, "15m": 900, "30m": 1800,
                         "1h": 3600, "4h": 14400, "1d": 86400}
                    return m.get(str(tf_str).strip().lower(), 60)

            if source == "auto":
                _now = _dt_in.now(_tz_in.utc)
                from datetime import timedelta as _td_in
                _api_horizon = _now - _td_in(days=_api_horizon_days)

                # Resolve effective window. When the caller didn't pass an
                # explicit start/end, infer from limit × tf back from now.
                eff_lo = window_start
                eff_hi = window_end
                if eff_lo is None and eff_hi is None:
                    tf_sec = _tf_seconds(reload_timeframe)
                    eff_hi = _now
                    eff_lo = _now - _td_in(seconds=max(1, limit * tf_sec))

                # Decide which sources we need.
                needs_databento = (eff_lo is not None and eff_lo < _api_horizon)
                needs_api = (eff_hi is None or eff_hi > _api_horizon)

                db_bars: List[Dict[str, Any]] = []
                api_bars: List[Dict[str, Any]] = []

                if needs_databento:
                    try:
                        from core.chart_databento_loader import load_databento_window_for_chart
                        db_lo = eff_lo
                        db_hi = min(_api_horizon, eff_hi) if eff_hi is not None else _api_horizon
                        db_bars, db_err = load_databento_window_for_chart(
                            reload_symbol, reload_timeframe,
                            start_utc=db_lo, end_utc=db_hi, limit=limit,
                        )
                        if db_err and not db_bars:
                            logger.debug("auto: databento window err=%s", db_err)
                    except Exception as e:
                        logger.warning("auto: databento window failed: %s", e)

                if needs_api:
                    # When we DON'T need to stitch (window fits inside the API
                    # horizon), pass start_time=None/end_time=None so the broker
                    # returns its natural "latest `limit` bars" — same behavior
                    # as pre-Phase-2.9. Forcing tight start/end on small windows
                    # caused the broker to return zero bars (likely because
                    # ``end_time=now`` lands inside the still-open candle), which
                    # broke "Auto · last 200" / "1 day" / "3 days" presets.
                    # We only force a tight window when stitching is needed —
                    # in that case the API slice must START at api_horizon so
                    # we don't double-cover bars Databento already supplied.
                    if needs_databento:
                        api_lo = max(_api_horizon, eff_lo) if eff_lo is not None else _api_horizon
                        api_hi = eff_hi
                    else:
                        api_lo = None
                        api_hi = None
                    try:
                        bars = await trading_bot.get_historical_data(
                            symbol=reload_symbol,
                            timeframe=reload_timeframe,
                            limit=limit,
                            start_time=api_lo,
                            end_time=api_hi,
                        )
                        api_bars = broker_bars_to_chart_rows(bars)
                    except Exception as e:
                        logger.warning("auto: api path failed: %s", e)

                # Stitch — Databento (older) first, then API (recent), deduped.
                if db_bars and api_bars:
                    seen: Dict[int, Dict[str, Any]] = {}
                    for b in db_bars:
                        try:
                            t = int(b.get("time"))
                        except (TypeError, ValueError):
                            continue
                        seen[t] = b
                    # API bars win on overlap (current-to-the-tick).
                    for b in api_bars:
                        try:
                            t = int(b.get("time"))
                        except (TypeError, ValueError):
                            continue
                        seen[t] = b
                    chart_data = [seen[t] for t in sorted(seen.keys())]
                    history_source = "databento+api"
                elif db_bars:
                    chart_data = db_bars
                    history_source = "databento"
                elif api_bars:
                    chart_data = api_bars
                    history_source = "api"

                # Trim stitched output to the requested limit, biased toward the
                # recent edge (older bars drop first if we're over-budget).
                if limit and len(chart_data) > limit:
                    chart_data = chart_data[-limit:]
            elif source == "api":
                try:
                    bars = await trading_bot.get_historical_data(
                        symbol=reload_symbol,
                        timeframe=reload_timeframe,
                        limit=limit,
                        start_time=window_start,
                        end_time=window_end,
                    )
                    chart_data = broker_bars_to_chart_rows(bars)
                    if chart_data:
                        history_source = "api"
                except Exception:
                    raise
            else:  # source == "databento"
                # Prefer the windowed loader if a window was given; otherwise
                # fall back to the legacy "tail last N bars" behavior.
                if window_start is not None or window_end is not None:
                    from core.chart_databento_loader import load_databento_window_for_chart
                    db_bars, db_err = load_databento_window_for_chart(
                        reload_symbol, reload_timeframe,
                        start_utc=window_start, end_utc=window_end, limit=limit,
                    )
                else:
                    from core.chart_databento_loader import load_databento_bars_for_chart
                    db_bars, db_err = load_databento_bars_for_chart(
                        reload_symbol, reload_timeframe, limit
                    )
                if db_bars:
                    chart_data = db_bars
                    history_source = "databento"
                else:
                    response = web.json_response(
                        {
                            "error": db_err or "no_databento_csv",
                            "bars": [],
                            "symbol": reload_symbol,
                            "timeframe": reload_timeframe,
                            "history_source": "databento",
                        },
                        status=404,
                    )
                    response.headers["Access-Control-Allow-Origin"] = "*"
                    return response

            # Final guard: if the caller specified a time window but the
            # underlying loader didn't filter (Databento path doesn't natively
            # take start/end), slice client-side so the response is bounded.
            if (window_start is not None or window_end is not None) and chart_data:
                ws = int(window_start.timestamp()) if window_start else None
                we = int(window_end.timestamp()) if window_end else None
                def _row_t(row):
                    t = row.get("time")
                    if isinstance(t, (int, float)):
                        return int(t) if t < 1e12 else int(t / 1000)
                    return None
                chart_data = [
                    r for r in chart_data
                    if (_row_t(r) is not None) and
                       (ws is None or _row_t(r) >= ws) and
                       (we is None or _row_t(r) <= we)
                ]

            payload: Dict[str, Any] = {
                "bars": chart_data,
                "symbol": reload_symbol,
                "timeframe": reload_timeframe,
                "history_source": history_source,
            }
            response = web.json_response(payload)
            response.headers["Access-Control-Allow-Origin"] = "*"
            return response
        except Exception as e:
            logger.error(f"Error reloading chart data: {e}")
            import traceback
            logger.error(traceback.format_exc())
            response = web.json_response({"error": str(e), "bars": []}, status=500)
            response.headers["Access-Control-Allow-Origin"] = "*"
            return response
    
    async def handle_trade_recap(request):
        """Return Databento OHLCV around a specific trade for the recap modal.

        Mirrors :mod:`scripts.walkforward_trade_recap_report` exactly: load the
        canonical ``historical_data/price/{root}_{tf}_databento.csv`` via
        :func:`core.backtest.parquet_cache.load_ohlcv_cached`, slice
        ``[entry - pad, exit + pad]``, convert to LWC bars, and **snap** the
        entry/exit unix-times to the bar opens that actually contain them so
        markers always land on real candles (left-labeled bar convention).

        Query:
            symbol         (required)  e.g. ``MNQ``
            entry          (required)  ISO 8601 or unix seconds (or ms)
            exit           (required)  ISO 8601 or unix seconds (or ms)
            pad_minutes    (optional)  default 120, clamped to [5, 1440]
            timeframe      (optional)  ``1m`` (default) or ``5m``

        Response:
            {bars, bar_times, entry_snapped, exit_snapped, timeframe, csv}
        """
        try:
            from datetime import datetime as _dt_in, timezone as _tz_in
            from pathlib import Path as _PathIn

            symbol = (request.query.get('symbol') or '').strip().upper()
            if not symbol:
                return web.json_response({'error': 'symbol required', 'bars': []}, status=400)

            def _parse_ts(raw):
                if raw is None or raw == '':
                    return None
                s = str(raw).strip()
                try:
                    n = float(s)
                    if n > 1e12:
                        n = n / 1000.0
                    return _dt_in.fromtimestamp(n, tz=_tz_in.utc)
                except (TypeError, ValueError):
                    pass
                try:
                    s2 = s.replace('Z', '+00:00')
                    dt0 = _dt_in.fromisoformat(s2)
                    if dt0.tzinfo is None:
                        dt0 = dt0.replace(tzinfo=_tz_in.utc)
                    return dt0.astimezone(_tz_in.utc)
                except (TypeError, ValueError):
                    return None

            entry_dt = _parse_ts(request.query.get('entry'))
            exit_dt = _parse_ts(request.query.get('exit'))
            if entry_dt is None or exit_dt is None:
                return web.json_response(
                    {'error': 'entry/exit required (ISO 8601 or unix seconds)', 'bars': []},
                    status=400,
                )
            if exit_dt < entry_dt:
                entry_dt, exit_dt = exit_dt, entry_dt

            try:
                pad_minutes = int(request.query.get('pad_minutes') or 120)
            except (TypeError, ValueError):
                pad_minutes = 120
            pad_minutes = max(5, min(pad_minutes, 1440))

            tf_req = (request.query.get('timeframe') or '1m').strip().lower()
            if tf_req not in ('1m', '5m'):
                tf_req = '1m'
            range_only = str(request.query.get('range_only') or '').strip().lower() in (
                '1', 'true', 'yes',
            )
            strategy_hint = (request.query.get('strategy') or '').strip() or None

            from core.chart_databento_loader import chart_symbol_to_databento_root
            from core.backtest.parquet_cache import load_ohlcv_cached
            from core.backtest.ohlcv import (
                dataframe_to_chart_bars_unix,
                snap_trade_unix_to_chart_bar_open,
            )

            root = chart_symbol_to_databento_root(symbol)
            if not root:
                return web.json_response(
                    {'error': f'no Databento CSV for symbol {symbol}', 'bars': []},
                    status=404,
                )

            repo_root = _PathIn(__file__).resolve().parent.parent
            price_dir = repo_root / 'historical_data' / 'price'
            p1 = price_dir / f'{root}_1m_databento.csv'
            p5 = price_dir / f'{root}_5m_databento.csv'

            account_id = None
            if hasattr(trading_bot, 'selected_account') and trading_bot.selected_account:
                if isinstance(trading_bot.selected_account, dict):
                    account_id = trading_bot.selected_account.get('id')
                else:
                    account_id = str(trading_bot.selected_account)

            range_snap = _range_snapshot_for_trade_recap(
                trading_bot, account_id, symbol, entry_dt, repo_root, root,
                strategy_hint=strategy_hint,
            )

            if range_only:
                side_q = (request.query.get('side') or '').strip().upper()
                payload: Dict[str, Any] = {
                    'symbol': symbol,
                    'root': root,
                    'entry_unix': int(entry_dt.timestamp()),
                    'exit_unix': int(exit_dt.timestamp()),
                    'range_only': True,
                }
                if range_snap:
                    payload['range_snapshot'] = range_snap
                if range_snap and range_snap.get('strategy_name') == 'morning_range_reversion' and p5.is_file():
                    try:
                        from scripts.walkforward_trade_recap_report import _find_morning_range_signal_bar
                        from core.backtest.parquet_cache import load_ohlcv_cached
                        df5 = load_ohlcv_cached(p5)
                        trade_stub = {
                            'entry_time': entry_dt.isoformat(),
                            'side': side_q or 'BUY',
                        }
                        sig = _find_morning_range_signal_bar(
                            trade_stub,
                            df5,
                            require_reentry_close=False,
                            reentry_threshold_points=0.0,
                        )
                        if sig:
                            payload['signal_bar'] = {
                                'time': sig[0],
                                'price': sig[1],
                                'label': sig[2],
                            }
                    except Exception:
                        logger.debug("trade_recap signal_bar failed for %s", symbol, exc_info=True)
                response = web.json_response(payload)
                response.headers['Access-Control-Allow-Origin'] = '*'
                return response

            csv_path = None
            tf_used = None
            if tf_req == '1m' and p1.is_file():
                csv_path, tf_used = p1, '1m'
            elif tf_req == '5m' and p5.is_file():
                csv_path, tf_used = p5, '5m'
            elif p1.is_file():
                csv_path, tf_used = p1, '1m'
            elif p5.is_file():
                csv_path, tf_used = p5, '5m'
            else:
                return web.json_response(
                    {'error': f'no canonical CSV under {price_dir}', 'bars': []},
                    status=404,
                )

            df = load_ohlcv_cached(csv_path)

            # load_ohlcv_cached returns naive-UTC index; build timezone-naive
            # window bounds to match.
            from datetime import timedelta as _td_in
            pad = _td_in(minutes=pad_minutes)
            lo = (entry_dt - pad).astimezone(_tz_in.utc).replace(tzinfo=None)
            hi = (exit_dt + pad).astimezone(_tz_in.utc).replace(tzinfo=None)
            sub = df.loc[(df.index >= lo) & (df.index <= hi)]
            if sub is None or len(sub) == 0:
                return web.json_response({
                    'error': 'window outside CSV coverage',
                    'bars': [],
                    'csv': csv_path.name,
                    'timeframe': tf_used,
                    'window': {'lo': lo.isoformat(), 'hi': hi.isoformat()},
                    'csv_first': str(df.index[0]) if len(df) else None,
                    'csv_last': str(df.index[-1]) if len(df) else None,
                }, status=200)

            bars, bar_times = dataframe_to_chart_bars_unix(sub)
            # LWC expects {time, open, high, low, close, volume}; the helper
            # already produces "timestamp" — rename for the wire.
            wire_bars = [
                {
                    'time': b['timestamp'],
                    'open': b['open'],
                    'high': b['high'],
                    'low': b['low'],
                    'close': b['close'],
                    'volume': b.get('volume', 0),
                }
                for b in bars
            ]

            entry_unix = int(entry_dt.timestamp())
            exit_unix = int(exit_dt.timestamp())
            entry_snapped = snap_trade_unix_to_chart_bar_open(entry_unix, bar_times)
            exit_snapped = snap_trade_unix_to_chart_bar_open(exit_unix, bar_times)

            payload = {
                'symbol': symbol,
                'root': root,
                'timeframe': tf_used,
                'csv': csv_path.name,
                'bars': wire_bars,
                'bar_times': bar_times,
                'entry_unix': entry_unix,
                'exit_unix': exit_unix,
                'entry_snapped': entry_snapped,
                'exit_snapped': exit_snapped,
                'pad_minutes': pad_minutes,
            }
            range_snap = _range_snapshot_for_trade_recap(
                trading_bot, account_id, symbol, entry_dt, repo_root, root,
                strategy_hint=strategy_hint,
            )
            if range_snap:
                payload['range_snapshot'] = range_snap
            side_q = (request.query.get('side') or '').strip().upper()
            if range_snap and range_snap.get('strategy_name') == 'morning_range_reversion' and p5.is_file():
                try:
                    from scripts.walkforward_trade_recap_report import _find_morning_range_signal_bar
                    df5 = load_ohlcv_cached(p5)
                    trade_stub = {
                        'entry_time': entry_dt.isoformat(),
                        'side': side_q or 'BUY',
                    }
                    sig = _find_morning_range_signal_bar(
                        trade_stub,
                        df5,
                        require_reentry_close=False,
                        reentry_threshold_points=0.0,
                    )
                    if sig:
                        payload['signal_bar'] = {
                            'time': sig[0],
                            'price': sig[1],
                            'label': sig[2],
                        }
                except Exception:
                    logger.debug("trade_recap signal_bar failed for %s", symbol, exc_info=True)
            response = web.json_response(payload)
            response.headers['Access-Control-Allow-Origin'] = '*'
            return response
        except Exception as e:
            logger.error("trade_recap error: %s", e, exc_info=True)
            response = web.json_response({'error': str(e), 'bars': []}, status=500)
            response.headers['Access-Control-Allow-Origin'] = '*'
            return response

    async def handle_trade_snapshot(request):
        """Return the bars + metadata captured at trade-close time, if any.

        ``GET /api/chart/trade_snapshot/{trade_id}``

        This is the **canonical** path for the dashboard recap modal — the bars
        were captured the moment the trade closed (Databento or broker API,
        whichever had data) and locked into Postgres. The frontend should call
        this first and fall back to ``/api/chart/trade_recap`` (live Databento)
        only when the snapshot is missing (e.g. trade closed before the snapshot
        feature was deployed, or the capture failed).
        """
        try:
            trade_id = request.match_info.get('trade_id', '')
            if not trade_id:
                response = web.json_response(
                    {'error': 'missing_trade_id', 'bars': []}, status=400,
                )
                response.headers['Access-Control-Allow-Origin'] = '*'
                return response
            db = getattr(trading_bot, 'db', None)
            if not db:
                response = web.json_response(
                    {'error': 'db_unavailable', 'bars': []}, status=503,
                )
                response.headers['Access-Control-Allow-Origin'] = '*'
                return response
            snap = db.get_trade_snapshot(str(trade_id))
            if not snap:
                response = web.json_response(
                    {
                        'error': 'no_snapshot',
                        'trade_id': trade_id,
                        'bars': [],
                    },
                    status=404,
                )
                response.headers['Access-Control-Allow-Origin'] = '*'
                return response
            # bars_json may be a list (psycopg2 JSONB → list) or a string
            # (legacy / dumps_str). Normalize.
            raw_bars = snap.get('bars_json')
            if isinstance(raw_bars, str):
                try:
                    raw_bars = json.loads(raw_bars)
                except (TypeError, ValueError):
                    raw_bars = []
            if not isinstance(raw_bars, list):
                raw_bars = []
            raw_range = snap.get('range_snapshot_json')
            if isinstance(raw_range, str):
                try:
                    raw_range = json.loads(raw_range)
                except (TypeError, ValueError):
                    raw_range = None
            raw_meta = snap.get('metadata')
            if isinstance(raw_meta, str):
                try:
                    raw_meta = json.loads(raw_meta)
                except (TypeError, ValueError):
                    raw_meta = None

            # Recompute session range from Databento/TOML (walkforward parity).
            # Snapshots captured before this fix stored live strategy_states blobs
            # (often the wrong session); always prefer authoritative anchor on read.
            range_snap = None
            entry_raw = snap.get('entry_time')
            sym_u = str(snap.get('symbol') or '').upper()
            strat_hint = snap.get('strategy_name')
            if entry_raw and sym_u:
                try:
                    from core.chart_databento_loader import chart_symbol_to_databento_root
                    if isinstance(entry_raw, datetime):
                        entry_dt = entry_raw if entry_raw.tzinfo else entry_raw.replace(tzinfo=timezone.utc)
                    else:
                        s2 = str(entry_raw).replace('Z', '+00:00')
                        entry_dt = datetime.fromisoformat(s2)
                        if entry_dt.tzinfo is None:
                            entry_dt = entry_dt.replace(tzinfo=timezone.utc)
                    root = chart_symbol_to_databento_root(sym_u) or sym_u.split('.')[-1]
                    repo_root = Path(__file__).resolve().parent.parent
                    acct = snap.get('account_id')
                    range_snap = _range_snapshot_for_trade_recap(
                        trading_bot,
                        str(acct) if acct is not None else None,
                        sym_u,
                        entry_dt,
                        repo_root,
                        root,
                        strategy_hint=strat_hint,
                    )
                except Exception:
                    logger.debug("trade_snapshot range recompute failed", exc_info=True)
            if range_snap is None:
                range_snap = raw_range

            payload = {
                'trade_id': snap.get('trade_id'),
                'account_id': snap.get('account_id'),
                'strategy_name': snap.get('strategy_name'),
                'symbol': snap.get('symbol'),
                'side': snap.get('side'),
                'quantity': snap.get('quantity'),
                'entry_time': snap.get('entry_time'),
                'exit_time': snap.get('exit_time'),
                'entry_price': snap.get('entry_price'),
                'exit_price': snap.get('exit_price'),
                'pnl': snap.get('pnl'),
                'timeframe': snap.get('timeframe'),
                'bars': raw_bars,
                'range_snapshot': range_snap,
                'metadata': raw_meta,
                'created_at': snap.get('created_at'),
            }
            response = web.json_response(payload)
            response.headers['Access-Control-Allow-Origin'] = '*'
            return response
        except Exception as e:
            logger.error("trade_snapshot error: %s", e, exc_info=True)
            response = web.json_response({'error': str(e), 'bars': []}, status=500)
            response.headers['Access-Control-Allow-Origin'] = '*'
            return response

    async def handle_strategy_status(request):
        """Get strategy status - includes all available strategies, not just active ones."""
        try:
            if not hasattr(trading_bot, 'strategy_manager'):
                return web.json_response({'strategies': {}, 'available': [], 'error': 'Strategy manager not available'})
            
            # Get all loaded strategies
            statuses = {}
            active_names = set(getattr(trading_bot.strategy_manager, 'active_strategies', []) or [])
            
            # Get manager status which includes process information
            manager_status = trading_bot.strategy_manager.get_status() if hasattr(trading_bot.strategy_manager, 'get_status') else {}
            manager_strategy_statuses = manager_status.get('strategies', {}) if isinstance(manager_status, dict) else {}
            
            # Check for strategies running in external processes (via database)
            external_strategies = {}
            try:
                db = getattr(trading_bot, 'db', None)
                if db:
                    # Get all running strategy executor processes
                    process_states = db.get_process_states('strategy_executor')
                    logger.debug(f"Found {len(process_states)} strategy executor processes")
                    for process in process_states:
                        if process.get('status') == 'running':
                            # Ignore stale heartbeats to avoid false positives
                            try:
                                from datetime import datetime, timezone
                                heartbeat_ttl_seconds = 90
                                last_heartbeat = process.get('last_heartbeat')
                                last_dt = None
                                if isinstance(last_heartbeat, datetime):
                                    last_dt = last_heartbeat
                                elif isinstance(last_heartbeat, str) and last_heartbeat:
                                    last_dt = datetime.fromisoformat(last_heartbeat.replace('Z', '+00:00'))
                                if last_dt:
                                    last_dt = last_dt if last_dt.tzinfo else last_dt.replace(tzinfo=timezone.utc)
                                    if (datetime.now(timezone.utc) - last_dt).total_seconds() > heartbeat_ttl_seconds:
                                        continue
                            except Exception:
                                logger.debug(
                                    "heartbeat TTL check failed for process %s",
                                    process.get("process_id"),
                                    exc_info=True,
                                )
                            metadata = process.get('metadata', {})
                            if isinstance(metadata, str):
                                import json
                                try:
                                    metadata = json.loads(metadata)
                                except (json.JSONDecodeError, TypeError, ValueError):
                                    logger.debug(
                                        "Invalid process metadata JSON for %s",
                                        process.get("process_id"),
                                        exc_info=True,
                                    )
                                    metadata = {}
                            strategies_in_process = metadata.get('strategies', [])
                            logger.debug(f"Process {process.get('process_id')} has strategies: {strategies_in_process}")
                            for strategy_name in strategies_in_process:
                                if strategy_name not in external_strategies:
                                    external_strategies[strategy_name] = {
                                        'process_id': process.get('process_id'),
                                        'started_at': process.get('started_at'),
                                        'last_heartbeat': process.get('last_heartbeat')
                                    }
                                    logger.info(f"📡 Detected external strategy: {strategy_name} (process: {process.get('process_id')})")
            except Exception as e:
                logger.warning(f"Could not check external processes: {e}")
                import traceback
                logger.debug(traceback.format_exc())
            
            # Also check for strategies that might be running externally (have active positions or running tasks)
            # This ensures the GUI monitors the entire system, not just GUI-initiated actions
            for name, strategy in trading_bot.strategy_manager.strategies.items():
                if strategy:
                    # Get strategy config
                    config = getattr(strategy, 'config', None)
                    symbols = getattr(config, 'symbols', []) if config else []
                    timeframe = getattr(config, 'timeframe', None) or getattr(strategy, 'timeframe', None) or 'N/A'
                    
                    # Check if strategy is actually active (not just in active list)
                    # A strategy is active if:
                    # 1. It's in the active_strategies list, OR
                    # 2. It has active positions (started externally), OR
                    # 3. It has a running task (monitoring loop active)
                    active_positions = len(getattr(strategy, 'active_positions', [])) if hasattr(strategy, 'active_positions') else 0
                    strategy_status = getattr(strategy, 'status', None)
                    # Handle StrategyStatus enum properly
                    if hasattr(strategy_status, 'name'):
                        status_name = strategy_status.name
                    elif hasattr(strategy_status, 'value'):
                        status_name = strategy_status.value
                    else:
                        status_name = str(strategy_status) if strategy_status else 'unknown'
                    
                    # Check if strategy has running monitoring task
                    has_running_task = False
                    if hasattr(trading_bot.strategy_manager, '_tasks'):
                        for task in trading_bot.strategy_manager._tasks:
                            if not task.done() and hasattr(task, 'get_name'):
                                try:
                                    if task.get_name() == name:
                                        has_running_task = True
                                        break
                                except Exception:
                                    logger.debug("task.get_name failed while scanning strategy tasks", exc_info=True)
                    
                    # Strategy is considered active if:
                    # - In active list, OR
                    # - Has active positions (externally started), OR  
                    # - Status is 'active' or 'running' (check both enum name and value), OR
                    # - Has running task, OR
                    # - Has is_trading flag set (for strategies with custom run loops), OR
                    # - Is running in an external process (detected via database)
                    is_trading = getattr(strategy, 'is_trading', False)
                    status_lower = status_name.lower() if status_name else ''
                    is_external = name in external_strategies
                    is_actually_active = (
                        name in active_names or
                        active_positions > 0 or
                        status_lower in ['active', 'running'] or
                        has_running_task or
                        is_trading or
                        is_external
                    )
                    
                    # If strategy is active but not in active list, add it (monitoring externally started)
                    if is_actually_active and name not in active_names:
                        logger.info(f"📊 Detected externally started strategy: {name} (has {active_positions} positions, status: {status_name})")
                        # Don't modify active_strategies list, but mark as active for display
                    
                    # Get start time if available
                    start_time = None
                    if hasattr(strategy, 'start_time'):
                        start_time = strategy.start_time
                    elif hasattr(strategy, '_start_time'):
                        start_time = strategy._start_time
                    
                    # Format start time
                    start_time_iso = None
                    runtime_seconds = None
                    runtime_str = None
                    try:
                        from datetime import datetime, timezone
                        if isinstance(start_time, datetime):
                            # Make timezone-aware for consistent ISO parsing in the browser
                            dt = start_time if start_time.tzinfo else start_time.replace(tzinfo=timezone.utc)
                            start_time_iso = dt.isoformat()
                            if is_actually_active:
                                now = datetime.now(timezone.utc)
                                runtime_seconds = int((now - dt).total_seconds())
                                hours = runtime_seconds // 3600
                                minutes = (runtime_seconds % 3600) // 60
                                runtime_str = f"{hours}h {minutes}m"
                        elif isinstance(start_time, str) and start_time.strip():
                            # If a strategy stored a string, pass it through and let the UI show it
                            start_time_iso = start_time
                    except Exception:
                        logger.debug("strategy start_time normalize failed: %r", start_time, exc_info=True)
                        start_time_iso = str(start_time) if start_time else None
                    
                    # If strategy is actually active but not in active list, treat it as fully active
                    # This ensures externally started strategies get full GUI support
                    is_fully_active = is_actually_active
                    
                    # Ensure runtime_str is set if we have runtime_seconds
                    if not runtime_str and runtime_seconds is not None:
                        hours = runtime_seconds // 3600
                        minutes = (runtime_seconds % 3600) // 60
                        runtime_str = f"{hours}h {minutes}m"
                    
                    statuses[name] = {
                        'name': name,
                        'status': status_name,
                        'active': is_fully_active,  # Use detected active status - treat externally started as fully active
                        'symbols': symbols,
                        'timeframe': timeframe,
                        'start_time': start_time_iso or 'N/A',
                        'runtime_seconds': runtime_seconds,
                        'runtime_str': runtime_str or (f"{hours}h {minutes}m" if runtime_seconds is not None else None),
                        'positions': active_positions,
                        'monitoring': name in active_names or is_fully_active,  # Consider externally started as monitored
                    }
            
            # Add strategies that are running externally but not in local strategies dict
            for name, ext_info in external_strategies.items():
                if name not in statuses:
                    ext_started_at = ext_info.get("started_at")
                    # Try to get symbols from database
                    symbols = []
                    db = None
                    account_id = None
                    try:
                        db = getattr(trading_bot, 'db', None)
                        if db:
                            if hasattr(trading_bot, 'selected_account') and trading_bot.selected_account:
                                if isinstance(trading_bot.selected_account, dict):
                                    account_id = trading_bot.selected_account.get('id')
                                else:
                                    account_id = str(trading_bot.selected_account)
                            if account_id:
                                state = db.get_strategy_state(str(account_id), name)
                                if state and state.get('symbols'):
                                    symbols = state.get('symbols', [])
                    except Exception as e:
                        logger.debug(f"Could not get external strategy state: {e}")
                    
                    from datetime import datetime as dt_ext, timezone as tz_ext

                    started_dt = None
                    runtime_str = None
                    runtime_seconds = None
                    if ext_started_at:
                        try:
                            raw = ext_started_at
                            if isinstance(raw, str):
                                started_dt = dt_ext.fromisoformat(raw.replace('Z', '+00:00'))
                            elif isinstance(raw, dt_ext):
                                started_dt = raw
                            if started_dt is not None:
                                if started_dt.tzinfo is None:
                                    started_dt = started_dt.replace(tzinfo=tz_ext.utc)
                                now = dt_ext.now(tz_ext.utc)
                                runtime_seconds = int((now - started_dt).total_seconds())
                                hours = runtime_seconds // 3600
                                minutes = (runtime_seconds % 3600) // 60
                                runtime_str = f"{hours}h {minutes}m"
                        except Exception:
                            logger.debug(
                                "external strategy runtime from started_at failed: %r",
                                ext_info.get("started_at"),
                                exc_info=True,
                            )

                    start_time_str = 'N/A'
                    if started_dt is not None:
                        start_time_str = started_dt.isoformat()
                    elif ext_started_at:
                        start_time_str = str(ext_started_at)

                    # Get timeframe from state if available
                    timeframe = '5m'  # Default
                    try:
                        if db and account_id:
                            state = db.get_strategy_state(str(account_id), name)
                            if state and state.get('settings'):
                                settings = state.get('settings', {})
                                if isinstance(settings, str):
                                    import json
                                    try:
                                        settings = json.loads(settings)
                                    except Exception as json_exc:
                                        logger.debug("strategy settings JSON parse failed: %s", json_exc)
                                if isinstance(settings, dict):
                                    timeframe = settings.get('timeframe', '5m')
                    except Exception:
                        logger.debug("timeframe from DB state failed for %s", name, exc_info=True)
                    
                    statuses[name] = {
                        'name': name,
                        'status': 'active',
                        'active': True,  # External strategies are always active
                        'symbols': symbols,
                        'timeframe': timeframe,
                        'start_time': start_time_str,
                        'runtime_seconds': runtime_seconds,
                        'runtime_str': runtime_str,
                        'positions': 0,
                        'monitoring': True,  # External strategies are monitored
                    }

            # External process is source of truth for start/runtime when the same strategy is loaded locally but idle.
            from datetime import datetime as dt_merge, timezone as tz_merge

            for name, ext_info in external_strategies.items():
                if name not in statuses:
                    continue
                ext_started_at = ext_info.get("started_at")
                if not ext_started_at:
                    continue
                try:
                    raw = ext_started_at
                    if isinstance(raw, str):
                        started_m = dt_merge.fromisoformat(raw.replace('Z', '+00:00'))
                    elif isinstance(raw, dt_merge):
                        started_m = raw
                    else:
                        continue
                    if started_m.tzinfo is None:
                        started_m = started_m.replace(tzinfo=tz_merge.utc)
                    ent = statuses[name]
                    ent["start_time"] = started_m.isoformat()
                    now = dt_merge.now(tz_merge.utc)
                    rs = int((now - started_m).total_seconds())
                    ent["runtime_seconds"] = rs
                    ent["runtime_str"] = f"{rs // 3600}h {(rs % 3600) // 60}m"
                    ent["active"] = True
                    ent["monitoring"] = True
                except Exception:
                    logger.debug("merge external process times into %s failed", name, exc_info=True)
            
            # Get all available strategies (registered but not necessarily loaded)
            sm = trading_bot.strategy_manager
            available_strategies = (
                sm.catalog_strategy_names()
                if hasattr(sm, "catalog_strategy_names")
                else sm.registered_strategy_names()
            )
            
            # Add available strategies that aren't loaded yet
            for name in available_strategies:
                if name not in statuses:
                    statuses[name] = {
                        'name': name,
                        'status': 'idle',
                        'active': False,
                        'symbols': [],
                        'positions': 0,
                        'available': True
                    }
            
            # Include externally started strategies in active list for full GUI support
            active_list = list(trading_bot.strategy_manager.active_strategies) if hasattr(trading_bot.strategy_manager, 'active_strategies') else []
            for name, status in statuses.items():
                if status.get('active') and name not in active_list:
                    active_list.append(name)

            # Attach idle_state to every non-active strategy so the
            # dashboard's Ready/Sleeping panel can render schedule +
            # arms-in countdown + last persisted range without a
            # second round-trip. Active strategies skip this — the
            # live state chart already renders their range overlays.
            try:
                idle_db = getattr(trading_bot, 'db', None)
                idle_account_id: Optional[str] = None
                acct = getattr(trading_bot, 'selected_account', None)
                if isinstance(acct, dict):
                    idle_account_id = acct.get('id')
                elif acct is not None:
                    idle_account_id = str(acct)
                for name, status in statuses.items():
                    if status.get('active'):
                        continue
                    status['idle_state'] = _strategy_idle_state(
                        name, idle_db, idle_account_id
                    )
            except Exception:
                logger.debug("attach idle_state failed", exc_info=True)

            response = web.json_response({
                'strategies': statuses,
                'available': available_strategies,
                'active': active_list  # Include both GUI-started and externally started strategies
            })
            response.headers['Access-Control-Allow-Origin'] = '*'
            return response
        except Exception as e:
            logger.error(f"Error fetching strategy status: {e}")
            import traceback
            logger.error(traceback.format_exc())
            response = web.json_response({'error': str(e), 'strategies': {}, 'available': []}, status=500)
            response.headers['Access-Control-Allow-Origin'] = '*'
            return response
    
    async def handle_strategy_start(request):
        """Start a strategy."""
        try:
            data = await request.json()
            strategy_name = data.get('strategy')
            symbols = data.get('symbols', [])
            timeframe = data.get('timeframe')
            risk_config = data.get('risk_config')  # Optional per-instrument risk config
            
            if not hasattr(trading_bot, 'strategy_manager'):
                return web.json_response({'success': False, 'error': 'Strategy manager not available'})
            
            # Use CLI command parser for consistency (handles --timeframe, --symbols, and --risk-config)
            from core.cli_command_parser import CLICommandParser
            import json
            parser = CLICommandParser(trading_bot)
            
            # Build command string
            cmd_parts = ['strategies', 'start', strategy_name]
            if symbols:
                symbols_str = ','.join(symbols) if isinstance(symbols, list) else str(symbols)
                cmd_parts.append(f'--symbols={symbols_str}')
            if timeframe:
                cmd_parts.append(f'--timeframe={timeframe}')
            if risk_config:
                # Convert risk_config dict to JSON string for CLI
                risk_config_json = json.dumps(risk_config).replace("'", "\\'")
                cmd_parts.append(f"--risk-config='{risk_config_json}'")
            
            command = ' '.join(cmd_parts)
            logger.info(f"Executing strategy start command: {command}")

            # Execute via CLI parser (parse_and_execute returns handler result or raises)
            try:
                result = await parser.parse_and_execute(command)
                response = web.json_response({'success': True, 'result': result, 'message': 'Strategy started'})
            except Exception as cmd_err:
                response = web.json_response({'success': False, 'error': str(cmd_err)}, status=400)
            
            response.headers['Access-Control-Allow-Origin'] = '*'
            return response
        except Exception as e:
            logger.error(f"Error starting strategy: {e}")
            import traceback
            logger.error(traceback.format_exc())
            response = web.json_response({'success': False, 'error': str(e)}, status=500)
            response.headers['Access-Control-Allow-Origin'] = '*'
            return response
    
    async def handle_strategy_stop(request):
        """Stop a strategy."""
        try:
            data = await request.json()
            strategy_name = data.get('strategy')
            from strategies.strategy_manager import _normalize_strategy_id

            norm = _normalize_strategy_id(strategy_name or "")

            if not hasattr(trading_bot, 'strategy_manager'):
                return web.json_response({'success': False, 'error': 'Strategy manager not available'})

            sm = trading_bot.strategy_manager
            account_override = data.get("account_id")
            aid = account_override
            if not aid and hasattr(trading_bot, "selected_account") and trading_bot.selected_account:
                if isinstance(trading_bot.selected_account, dict):
                    aid = trading_bot.selected_account.get("id")
                else:
                    aid = str(trading_bot.selected_account)
            aid = str(aid).strip() if aid else None

            # Headless strategy_executor: nothing in-process — persist disabled so executor polls stop.
            if norm and norm not in sm.active_strategies:
                db = getattr(trading_bot, "db", None)
                if db and aid:
                    existing = db.get_strategy_state(aid, norm) or {}
                    settings = dict(_parse_strategy_settings_blob(existing.get("settings")))
                    symbols = existing.get("symbols")
                    if not isinstance(symbols, list):
                        symbols = None
                    meta = existing.get("metadata") or {}
                    meta_dict = dict(meta) if isinstance(meta, dict) else {}
                    now = datetime.now(timezone.utc)
                    saved = db.save_strategy_state(
                        account_id=aid,
                        strategy_name=norm,
                        enabled=False,
                        symbols=symbols,
                        settings=settings if settings else None,
                        metadata=meta_dict if meta_dict else None,
                        last_stopped=now,
                    )
                    if saved:
                        logger.info(
                            "Persisted strategy stop (external executor): %s account=%s",
                            norm,
                            aid,
                        )
                        response = web.json_response(
                            {
                                "success": True,
                                "external": True,
                                "message": f"Stop requested for {norm}; executor will stop on next sync",
                            }
                        )
                        response.headers["Access-Control-Allow-Origin"] = "*"
                        return response
                    logger.warning(
                        "Could not persist external stop for %s account=%s; trying CLI",
                        norm,
                        aid,
                    )

            # Use CLI command parser for consistency
            from core.cli_command_parser import CLICommandParser
            parser = CLICommandParser(trading_bot)

            command = f'strategies stop {strategy_name}'
            logger.info(f"Executing strategy stop command: {command}")

            # Execute via CLI parser (parse_and_execute returns handler result or raises)
            try:
                result = await parser.parse_and_execute(command)
                response = web.json_response({'success': True, 'result': result, 'message': 'Strategy stopped'})
            except Exception as cmd_err:
                response = web.json_response({'success': False, 'error': str(cmd_err)}, status=400)
            
            response.headers['Access-Control-Allow-Origin'] = '*'
            return response
        except Exception as e:
            logger.error(f"Error stopping strategy: {e}")
            import traceback
            logger.error(traceback.format_exc())
            response = web.json_response({'success': False, 'error': str(e)}, status=500)
            response.headers['Access-Control-Allow-Origin'] = '*'
            return response
    
    async def handle_flatten(request):
        """Handle flatten command - close all positions and cancel all orders. OPTIMIZED FOR SPEED."""
        try:
            # Return immediately, process in background for speed
            response = web.json_response({'success': True, 'message': 'Flatten initiated'})
            response.headers['Access-Control-Allow-Origin'] = '*'
            
            # Process flatten in background (don't wait)
            asyncio.create_task(_flatten_background())
            
            return response
        except Exception as e:
            logger.error(f"Error initiating flatten: {e}")
            response = web.json_response({'success': False, 'error': str(e)}, status=500)
            response.headers['Access-Control-Allow-Origin'] = '*'
            return response
    
    async def _flatten_background():
        """Background task to actually flatten positions."""
        try:
            result = await trading_bot.flatten_all_positions(interactive=False)
            aid = None
            if hasattr(trading_bot, "selected_account") and trading_bot.selected_account:
                if isinstance(trading_bot.selected_account, dict):
                    aid = trading_bot.selected_account.get("id")
                else:
                    aid = trading_bot.selected_account
            if aid and getattr(trading_bot, "state_cache", None):
                try:
                    trading_bot.state_cache.invalidate_orders(str(aid))
                except Exception:
                    pass
            if hasattr(handle_get_orders, "_cache"):
                handle_get_orders._cache.clear()
            # Broadcast update via WebSocket
            await broadcast_update({'type': 'flatten_complete', 'data': result}, immediate=True)
        except Exception as e:
            logger.error(f"Error in background flatten: {e}")
            await broadcast_update({'type': 'error', 'data': {'message': f'Flatten error: {str(e)}'}}, immediate=True)
    
    async def handle_cancel_all(request):
        """Handle cancel all orders command."""
        try:
            # Get selected account id (selected_account may be a dict)
            account_id = None
            if hasattr(trading_bot, "selected_account") and trading_bot.selected_account:
                if isinstance(trading_bot.selected_account, dict):
                    account_id = trading_bot.selected_account.get("id")
                else:
                    account_id = trading_bot.selected_account

            # Get all open orders
            orders = await trading_bot.get_open_orders(account_id=account_id)
            
            if not orders:
                if account_id and getattr(trading_bot, "state_cache", None):
                    try:
                        trading_bot.state_cache.invalidate_orders(str(account_id))
                    except Exception:
                        pass
                if hasattr(handle_get_orders, "_cache"):
                    handle_get_orders._cache.clear()
                return web.json_response({'success': True, 'message': 'No orders to cancel', 'canceled': 0})
            
            # Cancel each order
            canceled = []
            failed = []
            for order in orders:
                try:
                    order_id = order.get('orderId') or order.get('id')
                    if order_id:
                        await trading_bot.cancel_order(order_id=str(order_id), account_id=account_id)
                        canceled.append(order_id)
                except Exception as e:
                    logger.error(f"Failed to cancel order {order_id}: {e}")
                    failed.append({'order_id': order_id, 'error': str(e)})
            
            if account_id and getattr(trading_bot, "state_cache", None):
                try:
                    trading_bot.state_cache.invalidate_orders(str(account_id))
                except Exception:
                    pass
            if hasattr(handle_get_orders, "_cache"):
                handle_get_orders._cache.clear()

            response = web.json_response({
                'success': len(failed) == 0,
                'canceled': len(canceled),
                'failed': len(failed),
                'canceled_orders': canceled,
                'failed_orders': failed
            })
            response.headers['Access-Control-Allow-Origin'] = '*'
            return response
        except Exception as e:
            logger.error(f"Error canceling orders: {e}")
            response = web.json_response({'success': False, 'error': str(e)}, status=500)
            response.headers['Access-Control-Allow-Origin'] = '*'
            return response

    async def handle_close_position(request):
        """Handle closing a single position."""
        try:
            logger.info("📥 Received close_position request")
            data = await request.json()
            position_id = data.get('position_id')
            logger.info(f"Position ID to close: {position_id}")
            
            if not position_id:
                logger.warning("No position_id provided")
                response = web.json_response({'success': False, 'error': 'position_id required'}, status=400)
                response.headers['Access-Control-Allow-Origin'] = '*'
                return response
            
            # Get account_id
            account_id = None
            if hasattr(trading_bot, "selected_account") and trading_bot.selected_account:
                if isinstance(trading_bot.selected_account, dict):
                    account_id = trading_bot.selected_account.get("id")
                else:
                    account_id = trading_bot.selected_account
            logger.info(f"Using account_id: {account_id}")
            
            # Get position details BEFORE closing to calculate P&L
            position_details = None
            realized_pnl = 0.0
            try:
                position_details = await trading_bot.get_position_details(position_id, account_id=account_id)
                if position_details:
                    # Calculate realized P&L from position details
                    entry_price = float(position_details.get('entryPrice') or position_details.get('entry_price') or 0)
                    quantity = float(position_details.get('size') or position_details.get('quantity') or 0)
                    side = position_details.get('side', 0)  # 0 = LONG, 1 = SHORT
                    symbol = position_details.get('symbol', '')
                    
                    # Get current market price for exit
                    try:
                        quote = await trading_bot.get_market_quote(symbol) if symbol else None
                        if quote and 'error' not in quote:
                            if side == 0:  # LONG
                                exit_price = float(quote.get('bid') or quote.get('last') or entry_price)
                            else:  # SHORT
                                exit_price = float(quote.get('ask') or quote.get('last') or entry_price)
                            
                            # Get tick value for symbol
                            if hasattr(trading_bot, 'account_tracker') and trading_bot.account_tracker:
                                tick_value = trading_bot.account_tracker._get_tick_value(symbol)
                            else:
                                # Fallback tick values
                                tick_value = 2.0 if 'MNQ' in symbol.upper() else 5.0
                            
                            # Calculate P&L
                            if side == 0:  # LONG
                                price_diff = exit_price - entry_price
                            else:  # SHORT
                                price_diff = entry_price - exit_price
                            
                            realized_pnl = price_diff * tick_value * quantity
                            logger.info(f"📊 Calculated realized P&L for position {position_id}: ${realized_pnl:.2f} (entry: ${entry_price:.2f}, exit: ${exit_price:.2f}, qty: {quantity})")
                    except Exception as pnl_err:
                        logger.warning(f"Could not calculate P&L before close: {pnl_err}")
            except Exception as details_err:
                logger.warning(f"Could not get position details before close: {details_err}")
            
            # Close the position
            try:
                result = await trading_bot.close_position(
                    position_id=str(position_id),
                    account_id=account_id
                )
                
                # Handle result as dict or object
                if isinstance(result, dict):
                    success = result.get('success', False)
                    error_msg = result.get('error') or result.get('message', 'Unknown error')
                else:
                    success = getattr(result, 'success', False)
                    error_msg = getattr(result, 'message', 'Unknown error')
                
                if result and success:
                    logger.info(f"✅ Position {position_id} closed successfully")
                    
                    # Update account tracker with realized P&L if we calculated it
                    if realized_pnl != 0 and hasattr(trading_bot, 'account_tracker') and trading_bot.account_tracker and account_id:
                        try:
                            fill_data = {
                                'pnl': realized_pnl,
                                'commission': 0.0,  # Will be updated when trade data comes in
                                'fee': 0.0
                            }
                            trading_bot.account_tracker.update_from_fill(str(account_id), fill_data)
                            logger.info(f"✅ Updated account tracker with realized P&L: ${realized_pnl:.2f}")
                        except Exception as tracker_err:
                            logger.warning(f"Could not update account tracker: {tracker_err}")
                    
                    # Clear account state cache to force refresh
                    if account_id:
                        cache_key = f"account_state_{account_id}"
                        if cache_key in _account_state_cache:
                            del _account_state_cache[cache_key]
                            logger.debug(f"Cleared account state cache for {account_id}")
                    
                    # Broadcast account update immediately
                    try:
                        account_state_resp = await handle_account_state(None)
                        if hasattr(account_state_resp, 'text'):
                            account_data = json.loads(account_state_resp.text)
                            await broadcast_update({'type': 'account', 'data': account_data}, immediate=True)
                            logger.debug("📡 Broadcasted account state update after position close")
                    except Exception as broadcast_err:
                        logger.debug(f"Could not broadcast account update: {broadcast_err}")
                    
                    response = web.json_response({'success': True, 'message': f'Position {position_id} closed', 'realized_pnl': realized_pnl})
                else:
                    # Check if error is about missing position (404) - might already be closed
                    if '404' in str(error_msg) or 'not found' in str(error_msg).lower():
                        logger.warning(f"⚠️ Position {position_id} not found (might already be closed)")
                        response = web.json_response({
                            'success': True, 
                            'message': f'Position {position_id} not found (may already be closed)',
                            'warning': 'Position not found in API'
                        })
                    else:
                        logger.error(f"❌ Failed to close position {position_id}: {error_msg}")
                        response = web.json_response({'success': False, 'error': error_msg}, status=400)
            except Exception as close_error:
                # Handle specific error cases
                error_str = str(close_error)
                if '404' in error_str or 'Not Found' in error_str:
                    logger.warning(f"⚠️ Position {position_id} returned 404 (might already be closed)")
                    response = web.json_response({
                        'success': True,
                        'message': f'Position {position_id} not found in API (may already be closed)',
                        'warning': 'Position not found'
                    })
                else:
                    raise  # Re-raise if it's not a 404
            
            response.headers['Access-Control-Allow-Origin'] = '*'
            return response
        except Exception as e:
            logger.error(f"❌ Error closing position: {e}")
            import traceback
            logger.error(traceback.format_exc())
            response = web.json_response({'success': False, 'error': str(e)}, status=500)
            response.headers['Access-Control-Allow-Origin'] = '*'
            return response
    
    async def handle_cancel_order(request):
        """Handle cancelling a single order."""
        try:
            logger.info("📥 Received cancel_order request")
            data = await request.json()
            order_id = data.get('order_id')
            logger.info(f"Order ID to cancel: {order_id}")
            
            if not order_id:
                logger.warning("No order_id provided")
                response = web.json_response({'success': False, 'error': 'order_id required'}, status=400)
                response.headers['Access-Control-Allow-Origin'] = '*'
                return response
            
            # Get account_id
            account_id = None
            if hasattr(trading_bot, "selected_account") and trading_bot.selected_account:
                if isinstance(trading_bot.selected_account, dict):
                    account_id = trading_bot.selected_account.get("id")
                else:
                    account_id = trading_bot.selected_account
            logger.info(f"Using account_id: {account_id}")
            
            # Cancel the order
            try:
                result = await trading_bot.cancel_order(
                    order_id=str(order_id),
                    account_id=account_id
                )
                
                # Handle result as dict or object
                if isinstance(result, dict):
                    success = result.get('success', False)
                    error_msg = result.get('error') or result.get('message', 'Unknown error')
                else:
                    success = getattr(result, 'success', False)
                    error_msg = getattr(result, 'message', 'Unknown error')
                
                if result and success:
                    logger.info(f"✅ Order {order_id} cancelled successfully")
                    if account_id and getattr(trading_bot, "state_cache", None):
                        try:
                            trading_bot.state_cache.invalidate_orders(str(account_id))
                        except Exception:
                            pass
                    if hasattr(handle_get_orders, "_cache"):
                        handle_get_orders._cache.clear()
                    response = web.json_response({'success': True, 'message': f'Order {order_id} cancelled'})
                else:
                    # Check for rate limiting (429) or not found (404)
                    error_str = str(error_msg)
                    if '429' in error_str or 'Too Many Requests' in error_str:
                        logger.warning(f"⚠️ Rate limited when cancelling order {order_id}")
                        response = web.json_response({
                            'success': False, 
                            'error': 'Rate limited by API. Please wait a moment and try again.',
                            'rate_limited': True
                        }, status=429)
                    elif '404' in error_str or 'not found' in error_str.lower():
                        logger.warning(f"⚠️ Order {order_id} not found (might already be cancelled)")
                        if account_id and getattr(trading_bot, "state_cache", None):
                            try:
                                trading_bot.state_cache.invalidate_orders(str(account_id))
                            except Exception:
                                pass
                        if hasattr(handle_get_orders, "_cache"):
                            handle_get_orders._cache.clear()
                        response = web.json_response({
                            'success': True,
                            'message': f'Order {order_id} not found (may already be cancelled)',
                            'warning': 'Order not found'
                        })
                    else:
                        logger.error(f"❌ Failed to cancel order {order_id}: {error_msg}")
                        response = web.json_response({'success': False, 'error': error_msg}, status=400)
            except Exception as cancel_error:
                # Handle specific error cases
                error_str = str(cancel_error)
                if '429' in error_str or 'Too Many Requests' in error_str:
                    logger.warning(f"⚠️ Rate limited when cancelling order {order_id}: {error_str}")
                    response = web.json_response({
                        'success': False,
                        'error': 'API rate limit exceeded. Please wait 10-15 seconds and try again.',
                        'rate_limited': True
                    }, status=429)
                elif '404' in error_str or 'Not Found' in error_str:
                    logger.warning(f"⚠️ Order {order_id} returned 404 (might already be cancelled)")
                    response = web.json_response({
                        'success': True,
                        'message': f'Order {order_id} not found in API (may already be cancelled)',
                        'warning': 'Order not found'
                    })
                else:
                    raise  # Re-raise if it's not a known error
            
            response.headers['Access-Control-Allow-Origin'] = '*'
            return response
        except Exception as e:
            logger.error(f"❌ Error cancelling order: {e}")
            import traceback
            logger.error(traceback.format_exc())
            response = web.json_response({'success': False, 'error': str(e)}, status=500)
            response.headers['Access-Control-Allow-Origin'] = '*'
            return response

    app.router.add_get('/api/chart/quote', handle_quote)
    app.router.add_options('/api/chart/quote', handle_options)
    app.router.add_post('/api/chart/order', handle_place_order)
    app.router.add_options('/api/chart/order', handle_options)
    app.router.add_get('/api/chart/positions', handle_get_positions)
    app.router.add_options('/api/chart/positions', handle_options)
    app.router.add_get('/api/chart/orders', handle_get_orders)
    app.router.add_options('/api/chart/orders', handle_options)
    app.router.add_get('/api/chart/contracts', handle_get_contracts)
    app.router.add_options('/api/chart/contracts', handle_options)
    app.router.add_get('/api/chart/reload', handle_reload_data)
    app.router.add_options('/api/chart/reload', handle_options)
    app.router.add_get('/api/chart/trade_recap', handle_trade_recap)
    app.router.add_options('/api/chart/trade_recap', handle_options)
    app.router.add_get('/api/chart/trade_snapshot/{trade_id}', handle_trade_snapshot)
    app.router.add_options('/api/chart/trade_snapshot/{trade_id}', handle_options)
    # Strategy endpoints
    async def handle_strategy_details(request):
        """Get detailed information about a specific strategy."""
        try:
            strategy_name = request.match_info.get('name')
            
            if not hasattr(trading_bot, 'strategy_manager'):
                return web.json_response({'error': 'Strategy manager not available'}, status=404)
            
            account_id = request.query.get('account_id')
            if not account_id and hasattr(trading_bot, 'selected_account') and trading_bot.selected_account:
                if isinstance(trading_bot.selected_account, dict):
                    account_id = trading_bot.selected_account.get('id')
                else:
                    account_id = str(trading_bot.selected_account)
            
            # Get the strategy instance
            strategy = trading_bot.strategy_manager.strategies.get(strategy_name)
            if not strategy:
                # Headless strategy_executor: no in-process instance — serve DB snapshot when available.
                if strategy_name == 'overnight_range':
                    sym_list: List[str] = []
                    st_row: Optional[Dict[str, Any]] = None
                    db = getattr(trading_bot, 'db', None)
                    if db and account_id:
                        st_row = db.get_strategy_state(str(account_id), 'overnight_range')
                        if st_row:
                            sym_list = list(st_row.get('symbols') or [])
                    ranges = _overnight_or_ranges_with_executor_fallback(trading_bot, account_id)
                    if not sym_list and ranges:
                        sym_list = sorted(ranges.keys())
                    details: Dict[str, Any] = {
                        'name': strategy_name,
                        'status': 'external',
                        'external': True,
                        'symbols': sym_list,
                        'timeframe': 'N/A',
                        'ranges': ranges,
                        'breakout_levels': {},
                        'atr_data': {},
                        'risk_profile': {},
                        'active_orders': 0,
                        'start_time': None,
                        'runtime_seconds': None,
                        'runtime_str': None,
                    }
                    if st_row:
                        lst = st_row.get('last_started')
                        if lst:
                            if isinstance(lst, str):
                                details['start_time'] = lst
                            elif hasattr(lst, 'isoformat'):
                                details['start_time'] = lst.isoformat()
                            else:
                                details['start_time'] = str(lst)
                            try:
                                from datetime import datetime as dt_ls, timezone as tz_ls
                                if isinstance(lst, str):
                                    dt0 = dt_ls.fromisoformat(lst.replace('Z', '+00:00'))
                                elif isinstance(lst, dt_ls):
                                    dt0 = lst
                                else:
                                    dt0 = None
                                if dt0 is not None:
                                    if dt0.tzinfo is None:
                                        dt0 = dt0.replace(tzinfo=tz_ls.utc)
                                    now = dt_ls.now(tz_ls.utc)
                                    rs = int((now - dt0).total_seconds())
                                    details['runtime_seconds'] = rs
                                    details['runtime_str'] = f"{rs // 3600}h {(rs % 3600) // 60}m"
                            except Exception:
                                logger.debug('external overnight_range runtime from last_started failed', exc_info=True)
                    _finalize_strategy_range_details(
                        trading_bot, account_id, strategy_name, details,
                    )
                    response = web.json_response(details)
                    response.headers['Access-Control-Allow-Origin'] = '*'
                    return response
                if strategy_name in _DB_RANGE_SETTINGS_KEYS:
                    details = _external_strategy_details_from_db(
                        trading_bot, account_id, strategy_name
                    )
                    if strategy_name == "morning_range_reversion":
                        brief_lines, sym_state = _mrr_live_from_executor_metadata(trading_bot)
                        if brief_lines:
                            details["live_brief"] = brief_lines
                        if sym_state:
                            details["symbol_state"] = sym_state
                    response = web.json_response(details)
                    response.headers['Access-Control-Allow-Origin'] = '*'
                    return response
                response = web.json_response({
                    'name': strategy_name,
                    'external': True,
                    'status': 'not_loaded',
                    'ranges': {},
                })
                response.headers['Access-Control-Allow-Origin'] = '*'
                return response
            
            # Collect basic details
            config = getattr(strategy, 'config', None)
            symbols = getattr(config, 'symbols', []) if config else []
            timeframe = getattr(config, 'timeframe', None) or getattr(strategy, 'timeframe', None) or 'N/A'
            
            # Get start time
            start_time = None
            if hasattr(strategy, 'start_time'):
                start_time = strategy.start_time
            elif hasattr(strategy, '_start_time'):
                start_time = strategy._start_time
            
            # Format start time
            start_time_str = None
            runtime_seconds = None
            runtime_str = None
            if start_time:
                from datetime import datetime, timezone
                if isinstance(start_time, datetime):
                    start_time_str = start_time.isoformat()
                    # Calculate runtime
                    now = datetime.now(timezone.utc)
                    dt = start_time if start_time.tzinfo else start_time.replace(tzinfo=timezone.utc)
                    runtime_seconds = int((now - dt).total_seconds())
                    hours = runtime_seconds // 3600
                    minutes = (runtime_seconds % 3600) // 60
                    runtime_str = f"{hours}h {minutes}m"
                elif isinstance(start_time, str):
                    start_time_str = start_time
                    # Try to parse and calculate runtime
                    try:
                        dt = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
                        now = datetime.now(timezone.utc)
                        runtime_seconds = int((now - dt).total_seconds())
                        hours = runtime_seconds // 3600
                        minutes = (runtime_seconds % 3600) // 60
                        runtime_str = f"{hours}h {minutes}m"
                    except Exception:
                        logger.debug(
                            "strategy detail runtime from start_time str failed: %r",
                            start_time,
                            exc_info=True,
                        )

            details = {
                'name': strategy_name,
                'status': getattr(strategy, 'status', {}).name if hasattr(getattr(strategy, 'status', None), 'name') else str(getattr(strategy, 'status', 'unknown')),
                'start_time': start_time_str,
                'runtime_seconds': runtime_seconds,
                'runtime_str': runtime_str,
                'symbols': symbols,
                'timeframe': timeframe,
            }
            
            # Strategy-specific details (for overnight_range)
            if strategy_name == 'overnight_range':
                # Breakout levels
                details['breakout_levels'] = {}
                if hasattr(strategy, 'breakout_levels'):
                    for symbol, templates in strategy.breakout_levels.items():
                        level_info = {}
                        if 'BUY' in templates:
                            buy_template = templates['BUY']
                            level_info['long_entry'] = float(buy_template.entry_price) if hasattr(buy_template, 'entry_price') else None
                            level_info['long_stop'] = float(buy_template.stop_loss) if hasattr(buy_template, 'stop_loss') else None
                            level_info['long_tp'] = float(buy_template.take_profit) if hasattr(buy_template, 'take_profit') else None
                        if 'SELL' in templates:
                            sell_template = templates['SELL']
                            level_info['short_entry'] = float(sell_template.entry_price) if hasattr(sell_template, 'entry_price') else None
                            level_info['short_stop'] = float(sell_template.stop_loss) if hasattr(sell_template, 'stop_loss') else None
                            level_info['short_tp'] = float(sell_template.take_profit) if hasattr(sell_template, 'take_profit') else None
                        details['breakout_levels'][symbol] = level_info
                
                # ATR data
                details['atr_data'] = {}
                if hasattr(strategy, 'active_ranges'):
                    for symbol, range_data in strategy.active_ranges.items():
                        if hasattr(range_data, 'atr_data'):
                            atr = range_data.atr_data
                            details['atr_data'][symbol] = {
                                'current_atr': float(atr.current_atr) if hasattr(atr, 'current_atr') and atr.current_atr else None,
                                'daily_atr': float(atr.daily_atr) if hasattr(atr, 'daily_atr') and atr.daily_atr else None,
                                'day_bull_price': float(atr.day_bull_price) if hasattr(atr, 'day_bull_price') and atr.day_bull_price else None,
                                'day_bear_price': float(atr.day_bear_price) if hasattr(atr, 'day_bear_price') and atr.day_bear_price else None,
                            }
                
                # Overnight range data
                details['ranges'] = {}
                if hasattr(strategy, 'active_ranges'):
                    for symbol, range_data in strategy.active_ranges.items():
                        range_info = {}
                        if hasattr(range_data, 'high'):
                            range_info['high'] = float(range_data.high)
                        if hasattr(range_data, 'low'):
                            range_info['low'] = float(range_data.low)
                        if hasattr(range_data, 'range_size'):
                            range_info['size'] = float(range_data.range_size)
                        st = getattr(range_data, 'start_time', None)
                        et = getattr(range_data, 'end_time', None)
                        if st is not None and hasattr(st, 'isoformat'):
                            range_info['session_start_et'] = st.isoformat()
                        if et is not None and hasattr(et, 'isoformat'):
                            range_info['session_end_et'] = et.isoformat()
                            if hasattr(et, 'date'):
                                range_info['session_date'] = et.date().isoformat()
                        details['ranges'][symbol] = range_info
                
                # Executor (or other process) runs live OR logic; this chart process may only
                # hold an idle strategy instance with empty active_ranges — merge DB snapshot.
                sm = trading_bot.strategy_manager
                active_names = getattr(sm, "active_strategies", []) or []
                idle_here = strategy_name not in active_names
                db_ranges = _overnight_or_ranges_with_executor_fallback(trading_bot, account_id)
                if db_ranges:
                    for sym, blob in db_ranges.items():
                        if not isinstance(blob, dict):
                            continue
                        try:
                            hi_f = float(blob["high"]) if blob.get("high") is not None else None
                            lo_f = float(blob["low"]) if blob.get("low") is not None else None
                        except (TypeError, ValueError, KeyError):
                            continue
                        cur = details["ranges"].get(sym)
                        merge = idle_here
                        if not merge and cur:
                            ch, cl = cur.get("high"), cur.get("low")
                            merge = (ch is None or cl is None) and (
                                hi_f is not None or lo_f is not None
                            )
                        elif not cur:
                            merge = True
                        if merge and (hi_f is not None or lo_f is not None):
                            details["ranges"][sym] = {
                                "high": hi_f,
                                "low": lo_f,
                            }
                
                # Risk parameters
                details['risk_profile'] = {
                    'stop_atr_multiplier': float(strategy.stop_atr_multiplier) if hasattr(strategy, 'stop_atr_multiplier') else None,
                    'tp_atr_multiplier': float(strategy.tp_atr_multiplier) if hasattr(strategy, 'tp_atr_multiplier') else None,
                    'breakeven_threshold': float(strategy.breakeven_threshold_points) if hasattr(strategy, 'breakeven_threshold_points') else None,
                }
                
                # Active orders/positions count
                details['active_orders'] = len(strategy.breakout_active_orders) if hasattr(strategy, 'breakout_active_orders') else 0

            # Range-based strategy ranges (MRR, ORB) — same shape as overnight_range
            #   details['ranges'][symbol] = {high, low, mid, session_start_et, session_end_et}
            # so the v2 chart overlay can render shaded boxes + midlines uniformly.
            if strategy_name in ('morning_range_reversion', 'opening_range_breakout', 'overnight_reversion'):
                details.setdefault('ranges', {})
                # Window times in ET (used to render the shaded build-window box)
                rs_attr = getattr(strategy, 'range_start', None)
                re_attr = getattr(strategy, 'range_end_open', None)
                window_start_et = rs_attr.strftime('%H:%M') if rs_attr is not None and hasattr(rs_attr, 'strftime') else None
                window_end_et = re_attr.strftime('%H:%M') if re_attr is not None and hasattr(re_attr, 'strftime') else None
                if window_start_et and window_end_et:
                    details['range_window'] = {
                        'start_et': window_start_et,
                        'end_et': window_end_et,
                        'tz': 'America/New_York',
                    }
                # Per-symbol state — different shape per strategy
                state_dict = None
                if strategy_name == 'opening_range_breakout' and hasattr(strategy, '_sessions'):
                    state_dict = strategy._sessions
                elif strategy_name == 'morning_range_reversion' and hasattr(strategy, '_state'):
                    state_dict = strategy._state
                # overnight_reversion: same _state shape as MRR if present
                elif strategy_name == 'overnight_reversion' and hasattr(strategy, '_state'):
                    state_dict = strategy._state
                if state_dict:
                    from datetime import datetime as _dt_combine
                    for sym, st in state_dict.items():
                        # MRR/overnight_reversion store dict; ORB stores a dataclass.
                        if isinstance(st, dict):
                            hi = st.get('H') if 'H' in st else st.get('range_hi')
                            lo = st.get('L') if 'L' in st else st.get('range_lo')
                            sd = st.get('session_date')
                        else:
                            hi = getattr(st, 'range_hi', None)
                            lo = getattr(st, 'range_lo', None)
                            sd = getattr(st, 'session_date', None)
                        if hi is None or lo is None:
                            continue
                        try:
                            hi_f = float(hi)
                            lo_f = float(lo)
                        except (TypeError, ValueError):
                            continue
                        info = {
                            'high': hi_f,
                            'low': lo_f,
                            'mid': (hi_f + lo_f) / 2.0,
                            'size': hi_f - lo_f,
                        }
                        if sd is not None and hasattr(sd, 'isoformat'):
                            info['session_date'] = sd.isoformat()
                            if rs_attr is not None and re_attr is not None:
                                try:
                                    info['session_start_et'] = _dt_combine.combine(sd, rs_attr).isoformat()
                                    info['session_end_et'] = _dt_combine.combine(sd, re_attr).isoformat()
                                except Exception:
                                    logger.debug(
                                        "%s range session combine failed for %s",
                                        strategy_name, sym, exc_info=True,
                                    )
                        details['ranges'][str(sym).upper()] = info

                # DB fallback: when the strategy lives in another process
                # (production = strategy_executor), the chart-server's
                # in-memory ``_state``/``_sessions`` is empty. Read the
                # snapshot the executor wrote to ``strategy_states.settings``
                # via ``BaseStrategy.persist_range_snapshot``.
                _settings_key_by_strategy = {
                    'morning_range_reversion': 'mrr_ranges',
                    'opening_range_breakout': 'orb_ranges',
                    # overnight_reversion: not yet persisted; the in-process
                    # state path above is sufficient for the dashboard's host
                    # process. If/when an executor runs it standalone we can
                    # add ``ovr_ranges`` here.
                }
                _key = _settings_key_by_strategy.get(strategy_name)
                if _key:
                    db_ranges = _strategy_ranges_from_db(
                        trading_bot, account_id, strategy_name, _key
                    )
                    if db_ranges:
                        for sym, blob in db_ranges.items():
                            if not isinstance(blob, dict):
                                continue
                            try:
                                hi_f = float(blob['high']) if blob.get('high') is not None else None
                                lo_f = float(blob['low']) if blob.get('low') is not None else None
                            except (TypeError, ValueError, KeyError):
                                continue
                            sym_u = str(sym).upper()
                            cur = details['ranges'].get(sym_u)
                            # Take the DB row when the in-process state is
                            # missing/empty for this symbol (idle here =
                            # executor-owned), or when the in-process row
                            # has no usable bounds.
                            cur_has_bounds = (
                                isinstance(cur, dict)
                                and cur.get('high') is not None
                                and cur.get('low') is not None
                            )
                            if cur_has_bounds:
                                continue
                            if hi_f is None or lo_f is None:
                                continue
                            merged: Dict[str, Any] = {
                                'high': hi_f,
                                'low': lo_f,
                                'mid': blob.get('mid', (hi_f + lo_f) / 2.0),
                                'size': blob.get('width', hi_f - lo_f),
                            }
                            for opt in ('session_date', 'session_start_et', 'session_end_et', 'phase'):
                                if blob.get(opt) is not None:
                                    merged[opt] = blob[opt]
                            details['ranges'][sym_u] = merged

            if strategy_name in _STRATEGY_RANGE_SETTINGS_KEYS:
                _finalize_strategy_range_details(
                    trading_bot, account_id, strategy_name, details,
                )

            if strategy_name == "morning_range_reversion":
                brief_lines: List[str] = []
                sym_state: Dict[str, Any] = {}
                if hasattr(strategy, "discord_daily_brief"):
                    try:
                        brief_lines = list(strategy.discord_daily_brief() or [])
                    except Exception:
                        logger.debug("mrr discord_daily_brief failed", exc_info=True)
                if hasattr(strategy, "_state") and isinstance(strategy._state, dict):
                    for sym, st in strategy._state.items():
                        if not isinstance(st, dict):
                            continue
                        sym_state[str(sym).upper()] = {
                            "phase": st.get("phase"),
                            "H": st.get("H"),
                            "L": st.get("L"),
                            "width": st.get("width"),
                            "range_ready": st.get("range_ready"),
                            "sweep_fired_high": st.get("sweep_fired_high"),
                            "sweep_fired_low": st.get("sweep_fired_low"),
                            "fades_this_session": st.get("fades_this_session"),
                            "session_date": (
                                st.get("session_date").isoformat()
                                if hasattr(st.get("session_date"), "isoformat")
                                else st.get("session_date")
                            ),
                        }
                if not brief_lines and not sym_state:
                    brief_lines, sym_state = _mrr_live_from_executor_metadata(trading_bot)
                if brief_lines:
                    details["live_brief"] = brief_lines
                if sym_state:
                    details["symbol_state"] = sym_state

            response = web.json_response(details)
            response.headers['Access-Control-Allow-Origin'] = '*'
            return response
        except Exception as e:
            logger.error(f"Error getting strategy details: {e}")
            import traceback
            logger.error(traceback.format_exc())
            response = web.json_response({'error': str(e)}, status=500)
            response.headers['Access-Control-Allow-Origin'] = '*'
            return response
    
    async def handle_range_overlays(request):
        """All strategy range overlays in one response (chart range indicator)."""
        import copy
        import time

        account_id = request.query.get('account_id')
        if not account_id and hasattr(trading_bot, 'selected_account') and trading_bot.selected_account:
            if isinstance(trading_bot.selected_account, dict):
                account_id = trading_bot.selected_account.get('id')
            else:
                account_id = str(trading_bot.selected_account)
        try:
            max_sessions = int(request.query.get('max_sessions', '3'))
        except (TypeError, ValueError):
            max_sessions = 3
        max_sessions = max(1, min(14, max_sessions))
        strategy_names = _parse_range_overlay_strategies(request)
        if not strategy_names:
            response = web.json_response({})
            response.headers['Access-Control-Allow-Origin'] = '*'
            return response

        cache_key = _range_overlays_cache_key(account_id, max_sessions, tuple(strategy_names))
        now = time.time()
        hit = _range_overlays_cache.get(cache_key)
        if hit and hit[0] > now:
            response = web.json_response(copy.deepcopy(hit[1]))
            response.headers['Access-Control-Allow-Origin'] = '*'
            response.headers['X-Range-Overlays-Cache'] = 'hit'
            return response

        payload: Dict[str, Any] = {}
        try:
            from core.range_history_backfill import _BAR_BACKFILL_SYMBOLS

            # Non-blocking DB backfill — only for strategies the UI requested.
            if account_id:
                for strategy_name in strategy_names:
                    settings_key = _STRATEGY_RANGE_SETTINGS_KEYS.get(strategy_name)
                    if not settings_key:
                        continue
                    try:
                        db = getattr(trading_bot, "db", None)
                        st = db.get_strategy_state(str(account_id), strategy_name) or {} if db else {}
                        settings = _parse_strategy_settings_blob(st.get("settings"))
                        metadata = st.get("metadata") or {}
                        if isinstance(metadata, str):
                            try:
                                metadata = json.loads(metadata)
                            except (json.JSONDecodeError, TypeError, ValueError):
                                metadata = {}
                        _maybe_backfill_range_history(
                            trading_bot, account_id, strategy_name, settings, metadata, settings_key,
                        )
                    except Exception:
                        logger.debug("schedule range backfill failed for %s", strategy_name, exc_info=True)

            # One API fetch per symbol per TTL window (shared across requested strategies).
            api_by_sym: Dict[str, List[Dict[str, Any]]] = {}
            for sym in _BAR_BACKFILL_SYMBOLS:
                api_by_sym[sym] = await _get_cached_api_1m_bars(trading_bot, sym)

            for strategy_name in strategy_names:
                try:
                    bundle = _range_overlay_bundle_for_strategy(
                        trading_bot, account_id, strategy_name,
                    )
                    payload[strategy_name] = await _augment_range_bundle_with_live_bars(
                        trading_bot, account_id, strategy_name, bundle,
                        max_sessions=max_sessions, api_bars_by_symbol=api_by_sym,
                    )
                except Exception:
                    logger.warning("range_overlays strategy %s failed", strategy_name, exc_info=True)
                    payload[strategy_name] = payload.get(strategy_name) or {
                        "ranges": {}, "range_history": [], "range_saved_at": None,
                    }
            _range_overlays_cache[cache_key] = (now + _RANGE_OVERLAYS_TTL_SEC, copy.deepcopy(payload))
            response = web.json_response(payload)
            response.headers['Access-Control-Allow-Origin'] = '*'
            response.headers['X-Range-Overlays-Cache'] = 'miss'
            return response
        except Exception as e:
            logger.error("range_overlays error: %s", e, exc_info=True)
            if not payload:
                for strategy_name in strategy_names:
                    payload[strategy_name] = {"ranges": {}, "range_history": [], "range_saved_at": None}
            response = web.json_response(payload)
            response.headers['Access-Control-Allow-Origin'] = '*'
            return response

    _session_fib_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}
    _SESSION_FIB_TTL_SEC = 20.0

    async def handle_session_fib_sweep(request):
        """Session Fibonacci Sweep zone overlays for the master chart GUI."""
        import copy
        import time

        try:
            from strategies.session_fibonacci_sweep_math import (
                DEFAULT_ATR_LEN,
                DEFAULT_SESSION_ZONES,
                DEFAULT_TP_PULL_MULT,
                DEFAULT_ZONE_NAMES,
                build_session_fib_overlays,
                normalize_zone_names,
            )

            def _parse_zones(raw, fallback):
                if raw is None or not str(raw).strip():
                    return normalize_zone_names(fallback)
                return normalize_zone_names([z.strip() for z in str(raw).split(",") if z.strip()])

            sym = (request.query.get("symbol") or getattr(trading_bot, "symbol", None) or "MNQ")
            sym = str(sym).strip().upper()
            tf = (request.query.get("timeframe") or "5m").strip().lower()
            try:
                limit = int(request.query.get("limit", "3500"))
            except (TypeError, ValueError):
                limit = 3500
            limit = max(200, min(8000, limit))
            try:
                max_sessions = int(request.query.get("max_sessions", "5"))
            except (TypeError, ValueError):
                max_sessions = 5
            max_sessions = max(0, min(16, max_sessions))
            try:
                atr_len = int(request.query.get("atr_len", str(DEFAULT_ATR_LEN)))
            except (TypeError, ValueError):
                atr_len = DEFAULT_ATR_LEN
            atr_len = max(1, min(50, atr_len))
            try:
                ib_minutes = int(request.query.get("ib_minutes", "30"))
            except (TypeError, ValueError):
                ib_minutes = 30
            ib_minutes = max(1, min(240, ib_minutes))
            zone_names = list(_parse_zones(request.query.get("zones"), DEFAULT_ZONE_NAMES))
            session_zones = {
                "Tokyo": _parse_zones(request.query.get("zones_tokyo"), DEFAULT_SESSION_ZONES["Tokyo"]),
                "London": _parse_zones(request.query.get("zones_london"), DEFAULT_SESSION_ZONES["London"]),
                "NY AM": _parse_zones(
                    request.query.get("zones_nyam") or request.query.get("zones_ny_am"),
                    DEFAULT_SESSION_ZONES["NY AM"],
                ),
                "NY PM": _parse_zones(
                    request.query.get("zones_nypm") or request.query.get("zones_ny_pm"),
                    DEFAULT_SESSION_ZONES["NY PM"],
                ),
            }
            delay_until_ib = request.query.get("delay_until_ib", "1") not in ("0", "false", "False")
            range_mode = (request.query.get("range_mode") or "atr").strip().lower()
            if range_mode not in ("atr", "previous"):
                range_mode = "atr"
            try:
                tp_pull_mult = float(request.query.get("tp_pull_mult", str(DEFAULT_TP_PULL_MULT)))
            except (TypeError, ValueError):
                tp_pull_mult = DEFAULT_TP_PULL_MULT
            tp_pull_mult = max(0.0, min(5.0, tp_pull_mult))

            cache_key = "|".join(
                [
                    sym,
                    tf,
                    str(limit),
                    str(max_sessions),
                    str(atr_len),
                    str(ib_minutes),
                    ",".join(zone_names),
                    ";".join(f"{k}:{','.join(v)}" for k, v in session_zones.items()),
                    "1" if delay_until_ib else "0",
                    range_mode,
                    f"{tp_pull_mult:.4f}",
                ]
            )
            now = time.time()
            hit = _session_fib_cache.get(cache_key)
            if hit and hit[0] > now:
                response = web.json_response(copy.deepcopy(hit[1]))
                response.headers["Access-Control-Allow-Origin"] = "*"
                response.headers["X-Session-Fib-Cache"] = "hit"
                return response

            bars_raw = await trading_bot.get_historical_data(
                symbol=sym, timeframe=tf, limit=limit,
            )
            chart_bars = broker_bars_to_chart_rows(bars_raw or [])
            overlays = build_session_fib_overlays(
                chart_bars,
                atr_len=atr_len,
                ib_minutes=ib_minutes,
                delay_until_ib=delay_until_ib,
                range_mode=range_mode,  # type: ignore[arg-type]
                zone_names=zone_names,
                session_zones=session_zones,
                max_sessions=max_sessions,
                tp_pull_mult=tp_pull_mult,
            )
            payload = {
                "symbol": sym,
                "timeframe": tf,
                "bar_count": len(chart_bars),
                "atr_len": atr_len,
                "ib_minutes": ib_minutes,
                "tp_pull_mult": tp_pull_mult,
                "zones": zone_names,
                "session_zones": {k: list(v) for k, v in session_zones.items()},
                "sessions": overlays,
            }
            _session_fib_cache[cache_key] = (now + _SESSION_FIB_TTL_SEC, copy.deepcopy(payload))
            response = web.json_response(payload)
            response.headers["Access-Control-Allow-Origin"] = "*"
            response.headers["X-Session-Fib-Cache"] = "miss"
            return response
        except Exception as e:
            logger.error("session_fib_sweep error: %s", e, exc_info=True)
            response = web.json_response({"error": str(e), "sessions": []}, status=500)
            response.headers["Access-Control-Allow-Origin"] = "*"
            return response

    app.router.add_get('/api/chart/strategy/status', handle_strategy_status)
    app.router.add_options('/api/chart/strategy/status', handle_options)
    app.router.add_get('/api/chart/range_overlays', handle_range_overlays)
    app.router.add_options('/api/chart/range_overlays', handle_options)
    app.router.add_get('/api/chart/session_fib_sweep', handle_session_fib_sweep)
    app.router.add_options('/api/chart/session_fib_sweep', handle_options)
    app.router.add_get('/api/chart/strategy/details/{name}', handle_strategy_details)
    app.router.add_options('/api/chart/strategy/details/{name}', handle_options)
    app.router.add_post('/api/chart/strategy/start', handle_strategy_start)
    app.router.add_options('/api/chart/strategy/start', handle_options)
    app.router.add_post('/api/chart/strategy/stop', handle_strategy_stop)
    app.router.add_options('/api/chart/strategy/stop', handle_options)
    # Account state endpoint
    app.router.add_get('/api/chart/account/state', handle_account_state)
    app.router.add_options('/api/chart/account/state', handle_options)
    # Flatten and cancel endpoints
    app.router.add_post('/api/chart/flatten', handle_flatten)
    app.router.add_options('/api/chart/flatten', handle_options)
    app.router.add_post('/api/chart/cancel_all', handle_cancel_all)
    app.router.add_options('/api/chart/cancel_all', handle_options)
    app.router.add_post('/api/chart/close_position', handle_close_position)
    app.router.add_options('/api/chart/close_position', handle_options)
    app.router.add_post('/api/chart/cancel_order', handle_cancel_order)
    app.router.add_options('/api/chart/cancel_order', handle_options)

    async def _chart_round_price(symbol: Optional[str], price: float) -> float:
        """Snap chart drag prices to contract tick size before broker modify."""
        if not hasattr(trading_bot, '_get_tick_size') or not hasattr(trading_bot, '_round_to_tick_size'):
            return float(price)
        sym = str(symbol or '').strip().upper()
        if not sym:
            return float(price)
        try:
            tick = await trading_bot._get_tick_size(sym)
            return trading_bot._round_to_tick_size(float(price), tick)
        except Exception:
            return float(price)

    async def handle_modify_order(request):
        """Modify working order price/qty from the chart GUI."""
        try:
            data = await request.json()
            order_id = data.get('order_id')
            if not order_id:
                response = web.json_response({'success': False, 'error': 'order_id required'}, status=400)
                response.headers['Access-Control-Allow-Origin'] = '*'
                return response
            account_id = None
            if hasattr(trading_bot, 'selected_account') and trading_bot.selected_account:
                if isinstance(trading_bot.selected_account, dict):
                    account_id = trading_bot.selected_account.get('id')
                else:
                    account_id = trading_bot.selected_account
            new_price = data.get('price')
            new_qty = data.get('quantity')
            order_type = data.get('order_type')
            if new_price is not None:
                try:
                    new_price = float(new_price)
                except (TypeError, ValueError):
                    new_price = None
            if new_qty is not None:
                try:
                    new_qty = int(new_qty)
                except (TypeError, ValueError):
                    new_qty = None
            if order_type is not None:
                try:
                    order_type = int(order_type)
                except (TypeError, ValueError):
                    order_type = None
            symbol = data.get('symbol')
            if new_price is not None:
                new_price = await _chart_round_price(symbol, new_price)
            result = await trading_bot.modify_order(
                order_id=str(order_id),
                new_price=new_price,
                new_quantity=new_qty,
                account_id=account_id,
                order_type=order_type,
            )
            if isinstance(result, dict) and result.get('success'):
                if account_id and getattr(trading_bot, 'state_cache', None):
                    try:
                        trading_bot.state_cache.invalidate_orders(str(account_id))
                    except Exception:
                        pass
                if hasattr(handle_get_orders, '_cache'):
                    handle_get_orders._cache.clear()
                response = web.json_response({'success': True, 'orderId': result.get('orderId')})
            else:
                err = result.get('error', 'modify failed') if isinstance(result, dict) else 'modify failed'
                response = web.json_response({'success': False, 'error': err}, status=400)
            response.headers['Access-Control-Allow-Origin'] = '*'
            return response
        except Exception as e:
            logger.error('modify_order error: %s', e, exc_info=True)
            response = web.json_response({'success': False, 'error': str(e)}, status=500)
            response.headers['Access-Control-Allow-Origin'] = '*'
            return response

    app.router.add_post('/api/chart/modify_order', handle_modify_order)
    app.router.add_options('/api/chart/modify_order', handle_options)

    async def handle_modify_stop_loss(request):
        """Modify bracket stop-loss on an open position (chart drag)."""
        try:
            data = await request.json()
            position_id = data.get('position_id')
            price = data.get('price')
            if not position_id:
                response = web.json_response({'success': False, 'error': 'position_id required'}, status=400)
                response.headers['Access-Control-Allow-Origin'] = '*'
                return response
            try:
                new_price = float(price)
            except (TypeError, ValueError):
                response = web.json_response({'success': False, 'error': 'price required'}, status=400)
                response.headers['Access-Control-Allow-Origin'] = '*'
                return response
            symbol = data.get('symbol')
            new_price = await _chart_round_price(symbol, new_price)
            account_id = None
            if hasattr(trading_bot, 'selected_account') and trading_bot.selected_account:
                if isinstance(trading_bot.selected_account, dict):
                    account_id = trading_bot.selected_account.get('id')
                else:
                    account_id = trading_bot.selected_account
            result = await trading_bot.modify_stop_loss(
                position_id=str(position_id),
                new_stop_price=new_price,
                account_id=account_id,
            )
            ok = isinstance(result, dict) and result.get('success')
            if ok:
                if account_id and getattr(trading_bot, 'state_cache', None):
                    try:
                        trading_bot.state_cache.invalidate_orders(str(account_id))
                    except Exception:
                        pass
                if hasattr(handle_get_orders, '_cache'):
                    handle_get_orders._cache.clear()
                response = web.json_response({'success': True, 'result': result})
            else:
                err = result.get('error', 'modify stop loss failed') if isinstance(result, dict) else 'modify stop loss failed'
                response = web.json_response({'success': False, 'error': err}, status=400)
            response.headers['Access-Control-Allow-Origin'] = '*'
            return response
        except Exception as e:
            logger.error('modify_stop_loss error: %s', e, exc_info=True)
            response = web.json_response({'success': False, 'error': str(e)}, status=500)
            response.headers['Access-Control-Allow-Origin'] = '*'
            return response

    async def handle_modify_take_profit(request):
        """Modify bracket take-profit on an open position (chart drag)."""
        try:
            data = await request.json()
            position_id = data.get('position_id')
            price = data.get('price')
            if not position_id:
                response = web.json_response({'success': False, 'error': 'position_id required'}, status=400)
                response.headers['Access-Control-Allow-Origin'] = '*'
                return response
            try:
                new_price = float(price)
            except (TypeError, ValueError):
                response = web.json_response({'success': False, 'error': 'price required'}, status=400)
                response.headers['Access-Control-Allow-Origin'] = '*'
                return response
            symbol = data.get('symbol')
            new_price = await _chart_round_price(symbol, new_price)
            account_id = None
            if hasattr(trading_bot, 'selected_account') and trading_bot.selected_account:
                if isinstance(trading_bot.selected_account, dict):
                    account_id = trading_bot.selected_account.get('id')
                else:
                    account_id = trading_bot.selected_account
            result = await trading_bot.modify_take_profit(
                position_id=str(position_id),
                new_tp_price=new_price,
                account_id=account_id,
            )
            ok = isinstance(result, dict) and result.get('success')
            if ok:
                if account_id and getattr(trading_bot, 'state_cache', None):
                    try:
                        trading_bot.state_cache.invalidate_orders(str(account_id))
                    except Exception:
                        pass
                if hasattr(handle_get_orders, '_cache'):
                    handle_get_orders._cache.clear()
                response = web.json_response({'success': True, 'result': result})
            else:
                err = result.get('error', 'modify take profit failed') if isinstance(result, dict) else 'modify take profit failed'
                response = web.json_response({'success': False, 'error': err}, status=400)
            response.headers['Access-Control-Allow-Origin'] = '*'
            return response
        except Exception as e:
            logger.error('modify_take_profit error: %s', e, exc_info=True)
            response = web.json_response({'success': False, 'error': str(e)}, status=500)
            response.headers['Access-Control-Allow-Origin'] = '*'
            return response

    app.router.add_post('/api/chart/modify_stop_loss', handle_modify_stop_loss)
    app.router.add_options('/api/chart/modify_stop_loss', handle_options)
    app.router.add_post('/api/chart/modify_take_profit', handle_modify_take_profit)
    app.router.add_options('/api/chart/modify_take_profit', handle_options)
    logger.info("✅ Registered close_position, cancel_order, modify_order, and bracket modify routes")
    
    async def handle_get_accounts(request):
        """Handle get accounts request."""
        try:
            # Ensure accounts are loaded - use list_accounts() if not available
            if not hasattr(trading_bot, 'accounts') or not trading_bot.accounts:
                # Try to fetch accounts using list_accounts() method
                try:
                    accounts = await trading_bot.list_accounts()
                    if accounts:
                        trading_bot.accounts = accounts
                        logger.info(f"✅ Loaded {len(accounts)} accounts for GUI")
                    else:
                        logger.warning("No accounts returned from list_accounts()")
                except Exception as e:
                    logger.warning(f"Could not fetch accounts via list_accounts(): {e}")
                    # Try auth_manager as fallback
                    if hasattr(trading_bot, 'auth_manager') and hasattr(trading_bot.auth_manager, 'accounts'):
                        trading_bot.accounts = trading_bot.auth_manager.accounts
            
            accounts_list = []
            if hasattr(trading_bot, 'accounts') and trading_bot.accounts:
                eligible = _gui_eligible_accounts(trading_bot.accounts)
                if eligible:
                    trading_bot.accounts = eligible
                for idx, acc in enumerate(trading_bot.accounts):
                    account_data = {
                        'index': idx,
                        'name': acc.get('name', acc.get('accountName', 'Unknown')),
                        'id': acc.get('id', acc.get('accountId', '')),
                        'balance': float(acc.get('balance', acc.get('currentBalance', 0))),
                        'status': acc.get('status', 'unknown'),
                        'type': acc.get('accountType', 'unknown'),
                        'selected': False
                    }
                    # Check if this is the currently selected account
                    if hasattr(trading_bot, 'selected_account'):
                        if isinstance(trading_bot.selected_account, dict):
                            if trading_bot.selected_account.get('id') == account_data['id']:
                                account_data['selected'] = True
                        elif str(trading_bot.selected_account) == str(account_data['id']):
                            account_data['selected'] = True
                    accounts_list.append(account_data)
            
            return web.json_response({'accounts': accounts_list})
        except Exception as e:
            logger.error(f"❌ Error fetching accounts: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return web.json_response({'accounts': [], 'error': str(e)})
    
    async def handle_select_account(request):
        """Handle account selection request."""
        try:
            data = await request.json()
            account_index = data.get('account_index')
            
            if account_index is None:
                return web.json_response({'success': False, 'error': 'Invalid account index'})
            
            # Ensure accounts are loaded
            if not hasattr(trading_bot, 'accounts') or not trading_bot.accounts:
                # Fetch accounts if not loaded using list_accounts()
                try:
                    accounts = await trading_bot.list_accounts()
                    if accounts:
                        trading_bot.accounts = accounts
                        logger.info(f"✅ Loaded {len(accounts)} accounts for account selection")
                    else:
                        return web.json_response({'success': False, 'error': 'No accounts available'})
                except Exception as e:
                    logger.error(f"Error fetching accounts: {e}")
                    import traceback
                    logger.error(traceback.format_exc())
                    return web.json_response({'success': False, 'error': f'Failed to load accounts: {e}'})
            
            if account_index < 0 or account_index >= len(trading_bot.accounts):
                return web.json_response({'success': False, 'error': f'Account index out of range (0-{len(trading_bot.accounts)-1})'})
            
            # Select the account using the bot's own logic (ensures caches/trackers update consistently)
            selected_account = trading_bot.accounts[account_index]
            account_id = selected_account.get('id', selected_account.get('accountId'))
            if account_id is None:
                return web.json_response({'success': False, 'error': 'Selected account has no id'})
            await trading_bot.switch_account(str(account_id))
            
            # Update account tracker if available
            if hasattr(trading_bot, 'account_tracker'):
                if account_id:
                    account_name = selected_account.get('name', selected_account.get('accountName', f'Account-{account_id}'))
                    account_type = selected_account.get('type', selected_account.get('accountType', 'unknown'))
                    account_balance = float(selected_account.get('balance', selected_account.get('currentBalance', 0)))
                    
                    # Check if account is already tracked
                    if str(account_id) not in trading_bot.account_tracker.accounts:
                        logger.info(f"Initializing account tracker for {account_name} (ID: {account_id})")
                        trading_bot.account_tracker.initialize_account(
                            account_id=str(account_id),
                            account_name=account_name,
                            account_type=account_type,
                            starting_balance=account_balance
                        )
                    else:
                        # Update current account ID in tracker
                        trading_bot.account_tracker.current_account_id = str(account_id)
                        logger.info(f"Switched tracker to account {account_name} (ID: {account_id})")
            
            account_name = selected_account.get('name', selected_account.get('accountName', 'Unknown'))
            logger.info(f"✅ Switched to account: {account_name}")
            
            return web.json_response({
                'success': True,
                'account_name': account_name,
                'account_id': selected_account.get('id', selected_account.get('accountId'))
            })
        except Exception as e:
            logger.error(f"❌ Error selecting account: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return web.json_response({'success': False, 'error': str(e)})
    
    app.router.add_get('/api/accounts', handle_get_accounts)
    app.router.add_post('/api/select_account', handle_select_account)
    app.router.add_options('/api/select_account', handle_options)
    
    async def handle_execute_command(request):
        """Handle command execution from the GUI."""
        try:
            data = await request.json()
            command = data.get('command', '').strip()
            
            if not command:
                return web.json_response({'success': False, 'error': 'No command provided'})
            
            logger.info(f"🎮 GUI command execution: {command}")
            
            # Import CLI command parser
            from core.cli_command_parser import CLICommandParser
            
            # Create parser instance
            parser = CLICommandParser(trading_bot)
            
            # Execute command
            resp = await parser.execute_command(command)
            if resp.get("success"):
                result = resp.get("result")
                if result is None:
                    return web.json_response({'success': True, 'output': 'Command executed successfully'})
                if isinstance(result, (dict, list)):
                    import json
                    output = json.dumps(result, indent=2)
                    return web.json_response({'success': True, 'output': output, 'result': result})
                # Convert result to string, handling None and other types
                output = str(result) if result is not None else 'Command executed successfully'
                return web.json_response({'success': True, 'output': output, 'result': result})
            # Return error with helpful message
            error_msg = resp.get("error", "Command failed")
            available = resp.get("available_commands", [])
            if available:
                error_msg += f"\n\nAvailable commands: {', '.join(available[:20])}"  # Limit to first 20
            return web.json_response({'success': False, 'error': error_msg, 'result': resp})
        except Exception as e:
            logger.error(f"❌ Error executing command: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return web.json_response({'success': False, 'error': str(e)})
    
    app.router.add_post('/api/execute_command', handle_execute_command)
    app.router.add_options('/api/execute_command', handle_options)
    
    # Enhanced P&L tracking with historical data
    async def handle_pnl_history(request):
        """Get P&L history for equity curve visualization."""
        try:
            account_id = None
            if hasattr(trading_bot, 'selected_account') and trading_bot.selected_account:
                if isinstance(trading_bot.selected_account, dict):
                    account_id = trading_bot.selected_account.get('id')
                else:
                    account_id = str(trading_bot.selected_account)
            
            # Time window. Accepts:
            #   - period=N         (hours, legacy default)
            #   - period_days=N    (days; converted to hours)
            #   - since=ISO        (explicit start; overrides period*)
            # Handle None request (when called from WebSocket broadcast loop).
            period_hours = 24
            since_iso = None
            if request and hasattr(request, 'query'):
                try:
                    period_hours = int(request.query.get('period', 24))
                except (TypeError, ValueError):
                    period_hours = 24
                try:
                    period_days_q = int(request.query.get('period_days', 0))
                except (TypeError, ValueError):
                    period_days_q = 0
                if period_days_q > 0:
                    period_hours = period_days_q * 24
                since_iso = request.query.get('since') or None
            
            # Get current account state
            account_state_resp = await handle_account_state(None)
            account_state = {}
            if hasattr(account_state_resp, 'text') and account_state_resp.text:
                account_state = json.loads(account_state_resp.text)
            elif hasattr(account_state_resp, 'body') and account_state_resp.body:
                account_state = json.loads(account_state_resp.body.decode('utf-8'))
            
            current_balance = float(account_state.get('balance', 0))
            unrealized_pnl = float(account_state.get('unrealized_pnl', 0))
            realized_pnl = float(account_state.get('realized_pnl', 0))
            
            # Try to get historical data from database if available
            pnl_history = []
            if hasattr(trading_bot, 'database') and trading_bot.database:
                try:
                    from datetime import datetime, timedelta, timezone
                    if since_iso:
                        try:
                            start_time = datetime.fromisoformat(
                                since_iso.replace('Z', '+00:00')
                            )
                            if start_time.tzinfo is None:
                                start_time = start_time.replace(tzinfo=timezone.utc)
                        except (ValueError, TypeError):
                            start_time = datetime.now(timezone.utc) - timedelta(hours=period_hours)
                    else:
                        start_time = datetime.now(timezone.utc) - timedelta(hours=period_hours)
                    
                    # Query trade history for realized P&L over time
                    with trading_bot.database.get_connection() as conn:
                        cursor = conn.cursor()
                        cursor.execute("""
                            SELECT 
                                exit_time as timestamp,
                                SUM(pnl) OVER (ORDER BY exit_time) as cumulative_realized_pnl
                            FROM trade_history
                            WHERE account_id = %s 
                                AND exit_time >= %s
                                AND exit_time IS NOT NULL
                            ORDER BY exit_time
                        """, (str(account_id), start_time))
                        
                        rows = cursor.fetchall()
                        for row in rows:
                            pnl_history.append({
                                'timestamp': row[0].isoformat() if row[0] else None,
                                'realized_pnl': float(row[1] or 0),
                                'unrealized_pnl': 0.0,  # Historical unrealized not tracked
                                'total_pnl': float(row[1] or 0),
                                'balance': current_balance  # Approximate
                            })
                except Exception as e:
                    logger.debug(f"Could not fetch P&L history from database: {e}")
            
            # Add current state as latest point
            from datetime import datetime, timezone
            pnl_history.append({
                'timestamp': datetime.now(timezone.utc).isoformat(),
                'realized_pnl': realized_pnl,
                'unrealized_pnl': unrealized_pnl,
                'total_pnl': realized_pnl + unrealized_pnl,
                'balance': current_balance
            })
            
            # Calculate equity curve (balance over time)
            equity_curve = []
            starting_balance = current_balance - (realized_pnl + unrealized_pnl)
            for i, point in enumerate(pnl_history):
                equity_curve.append({
                    'timestamp': point['timestamp'],
                    'equity': starting_balance + point['total_pnl'],
                    'realized_pnl': point['realized_pnl'],
                    'unrealized_pnl': point.get('unrealized_pnl', 0),
                    'drawdown': 0.0  # Calculate if we have peak
                })
            
            # Calculate drawdown
            if equity_curve:
                peak_equity = equity_curve[0]['equity']
                for point in equity_curve:
                    if point['equity'] > peak_equity:
                        peak_equity = point['equity']
                    point['drawdown'] = peak_equity - point['equity']
            
            result = {
                'account_id': account_id,
                'current': {
                    'balance': current_balance,
                    'realized_pnl': realized_pnl,
                    'unrealized_pnl': unrealized_pnl,
                    'total_pnl': realized_pnl + unrealized_pnl
                },
                'equity_curve': equity_curve,
                'pnl_history': pnl_history,
                'period_hours': period_hours
            }
            
            response = web.json_response(result)
            response.headers['Access-Control-Allow-Origin'] = '*'
            return response
        except Exception as e:
            logger.error(f"Error fetching P&L history: {e}")
            import traceback
            logger.error(traceback.format_exc())
            response = web.json_response({'error': str(e)}, status=500)
            response.headers['Access-Control-Allow-Origin'] = '*'
            return response
    
    # Performance metrics endpoint
    async def handle_performance_metrics(request):
        """Get comprehensive performance metrics.

        Window selection (in priority order):
          - ?since=YYYY-MM-DD[&until=YYYY-MM-DD]   — explicit ISO range
          - ?period=N                              — last N days (0 = current trading session/today)
          - default                                — current trading session
        """
        try:
            account_id = None
            if hasattr(trading_bot, 'selected_account') and trading_bot.selected_account:
                if isinstance(trading_bot.selected_account, dict):
                    account_id = trading_bot.selected_account.get('id')
                else:
                    account_id = str(trading_bot.selected_account)

            # Resolve window. period=0 means "current trading session" (legacy default behavior).
            # Handle None request (called from WebSocket broadcast loop with no query).
            period_days = 0
            since_str = None
            until_str = None
            if request and hasattr(request, 'query'):
                try:
                    period_days = int(request.query.get('period', 0))
                except (TypeError, ValueError):
                    period_days = 0
                since_str = request.query.get('since') or None
                until_str = request.query.get('until') or None

            # Build trades_args for parser._handle_trades([start_date, end_date])
            trades_args: List[str] = []
            if since_str:
                trades_args.append(since_str)
                if until_str:
                    trades_args.append(until_str)
            elif period_days and period_days > 0:
                from datetime import datetime, timedelta, timezone
                start_dt = datetime.now(timezone.utc) - timedelta(days=period_days)
                trades_args.append(start_dt.isoformat())
            
            # Get current account state
            account_state_resp = await handle_account_state(None)
            account_state = {}
            if hasattr(account_state_resp, 'text') and account_state_resp.text:
                account_state = json.loads(account_state_resp.text)
            elif hasattr(account_state_resp, 'body') and account_state_resp.body:
                account_state = json.loads(account_state_resp.body.decode('utf-8'))
            
            # Get realized PnL from account tracker (manual calculation)
            realized_pnl = float(account_state.get('realized_pnl', 0))
            unrealized_pnl = float(account_state.get('unrealized_pnl', 0))
            
            # Get realized PnL from account tracker if not in account_state
            if realized_pnl == 0 and hasattr(trading_bot, 'account_tracker') and trading_bot.account_tracker and account_id:
                try:
                    tracker_state = trading_bot.account_tracker.get_state(account_id=str(account_id))
                    if isinstance(tracker_state, dict):
                        realized_pnl = float(tracker_state.get('realized_pnl', 0.0) or 0.0)
                        logger.debug(f"Performance metrics: Retrieved realized PnL from tracker: ${realized_pnl:.2f}")
                except Exception as e:
                    logger.debug(f"Could not get realized PnL from tracker in metrics: {e}")
            
            # Initialize metrics
            metrics = {
                'account_id': account_id,
                'period_days': period_days,
                'current': {
                    'balance': float(account_state.get('balance', 0)),
                    'realized_pnl': realized_pnl,
                    'unrealized_pnl': unrealized_pnl,
                    'total_pnl': realized_pnl + unrealized_pnl
                },
                'trades': {
                    'total': 0,
                    'winning': 0,
                    'losing': 0,
                    'win_rate': 0.0,
                    'avg_win': 0.0,
                    'avg_loss': 0.0,
                    'profit_factor': 0.0,
                    'largest_win': 0.0,
                    'largest_loss': 0.0
                },
                'performance': {
                    'sharpe_ratio': 0.0,
                    'sortino_ratio': 0.0,
                    'max_drawdown': 0.0,
                    'current_drawdown': 0.0,
                    'recovery_factor': 0.0
                },
                'by_strategy': {},
                'by_symbol': {},
                'by_hour': {}
            }
            
            # Get trade statistics using trading_bot's 'trades' command
            # This uses the same consolidation and calculation logic as the CLI command
            try:
                from core.cli_command_parser import CLICommandParser
                parser = CLICommandParser(trading_bot)
                
                # Execute 'trades' command. trades_args is empty for the current session,
                # or [start_iso] / [start_iso, end_iso] when the GUI requests a longer window.
                trades_result = await parser._handle_trades(trades_args)
                
                if trades_result and 'statistics' in trades_result:
                    stats = trades_result['statistics']
                    trades_list = trades_result.get('trades', [])
                    
                    # Map statistics from trades command to metrics format
                    metrics['trades']['total'] = stats.get('total_trades', 0)
                    metrics['trades']['winning'] = stats.get('winning_trades', 0)
                    metrics['trades']['losing'] = stats.get('losing_trades', 0)
                    # Win rate is already a percentage (0-100) from _calculate_trade_statistics
                    win_rate_pct = stats.get('win_rate', 0.0)
                    metrics['trades']['win_rate'] = win_rate_pct / 100.0 if win_rate_pct > 1 else win_rate_pct
                    metrics['trades']['avg_win'] = stats.get('average_win', 0.0)
                    metrics['trades']['avg_loss'] = abs(stats.get('average_loss', 0.0))  # Make positive for display
                    metrics['trades']['largest_win'] = stats.get('largest_win', 0.0)
                    metrics['trades']['largest_loss'] = stats.get('largest_loss', 0.0)
                    
                    # Use profit factor from statistics
                    metrics['trades']['profit_factor'] = stats.get('profit_factor', 0.0)
                    
                    metrics['performance']['max_drawdown'] = stats.get('max_drawdown', 0.0)
                    if starting_balance_calc > 0:
                        metrics['performance']['max_drawdown_pct'] = (metrics['performance']['max_drawdown'] / starting_balance_calc) * 100.0
                    else:
                        metrics['performance']['max_drawdown_pct'] = 0.0
                    metrics['performance']['current_drawdown'] = stats.get('current_drawdown_pct', 0.0)
                    metrics['performance']['recovery_factor'] = stats.get('recovery_factor', 0.0)
                    
                    # Process trades for additional breakdowns (by strategy, symbol, hour)
                    for trade in trades_list:
                        pnl = float(trade.get('pnl', 0) or 0)
                        symbol = trade.get('symbol', 'UNKNOWN')
                        strategy = trade.get('strategy') or 'manual'
                        
                        # Track by strategy
                        if strategy not in metrics['by_strategy']:
                            metrics['by_strategy'][strategy] = {'trades': 0, 'pnl': 0.0, 'wins': 0, 'losses': 0}
                        metrics['by_strategy'][strategy]['trades'] += 1
                        metrics['by_strategy'][strategy]['pnl'] += pnl
                        if pnl > 0:
                            metrics['by_strategy'][strategy]['wins'] += 1
                        else:
                            metrics['by_strategy'][strategy]['losses'] += 1
                        
                        # Track by symbol
                        if symbol not in metrics['by_symbol']:
                            metrics['by_symbol'][symbol] = {'trades': 0, 'pnl': 0.0, 'wins': 0, 'losses': 0}
                        metrics['by_symbol'][symbol]['trades'] += 1
                        metrics['by_symbol'][symbol]['pnl'] += pnl
                        if pnl > 0:
                            metrics['by_symbol'][symbol]['wins'] += 1
                        else:
                            metrics['by_symbol'][symbol]['losses'] += 1
                        
                        # Track by hour
                        exit_time = trade.get('exit_time')
                        if exit_time:
                            try:
                                from datetime import datetime
                                if isinstance(exit_time, str):
                                    dt = datetime.fromisoformat(exit_time.replace('Z', '+00:00'))
                                else:
                                    dt = exit_time
                                exit_hour = dt.hour
                                
                                if exit_hour not in metrics['by_hour']:
                                    metrics['by_hour'][exit_hour] = {'trades': 0, 'wins': 0, 'losses': 0}
                                metrics['by_hour'][exit_hour]['trades'] += 1
                                if pnl > 0:
                                    metrics['by_hour'][exit_hour]['wins'] += 1
                                else:
                                    metrics['by_hour'][exit_hour]['losses'] += 1
                            except Exception as e:
                                logger.debug(f"Could not parse exit_time for hour tracking: {e}")
                    
                    # Calculate win rates for strategies, symbols, and hours
                    for key in ['by_strategy', 'by_symbol']:
                        for name, data in metrics[key].items():
                            if data['trades'] > 0:
                                data['win_rate'] = data['wins'] / data['trades']
                    
                    for hour, data in metrics['by_hour'].items():
                        if data['trades'] > 0:
                            data['win_rate'] = data['wins'] / data['trades']
                    
                    # Calculate additional StrategIQ-style metrics
                    # Return %: (Net P&L / Starting Balance) * 100
                    starting_balance_calc = float(account_state.get('balance', 0)) - realized_pnl
                    if starting_balance_calc > 0:
                        metrics['performance']['return_pct'] = (realized_pnl / starting_balance_calc) * 100.0
                    else:
                        metrics['performance']['return_pct'] = 0.0
                    
                    # R:R Ratio (Risk:Reward): Average Win / Average Loss
                    if metrics['trades']['avg_loss'] > 0:
                        metrics['trades']['risk_reward_ratio'] = metrics['trades']['avg_win'] / metrics['trades']['avg_loss']
                    else:
                        metrics['trades']['risk_reward_ratio'] = 0.0
                    
                    # High Water Mark (HWM): Maximum cumulative P&L reached
                    if trades_list:
                        cumulative = 0.0
                        hwm = 0.0
                        for trade in sorted(trades_list, key=lambda x: x.get('exit_time', '') or x.get('entry_time', '')):
                            cumulative += float(trade.get('pnl', 0) or 0)
                            hwm = max(hwm, cumulative)
                        metrics['performance']['high_water_mark'] = hwm
                    else:
                        metrics['performance']['high_water_mark'] = 0.0
                    
                    # Fees: Estimate based on trades (TopStepX typically charges per contract)
                    # Rough estimate: $2.40 per round trip (entry + exit) for MNQ
                    total_contracts = sum(float(t.get('quantity', 0) or 0) for t in trades_list)
                    estimated_fees = total_contracts * 2.40  # $2.40 per round trip
                    metrics['performance']['fees'] = estimated_fees
                    
                    # Avg Trade: Net P&L / Total Trades
                    if metrics['trades']['total'] > 0:
                        metrics['trades']['avg_trade'] = realized_pnl / metrics['trades']['total']
                    else:
                        metrics['trades']['avg_trade'] = 0.0
                    
                    logger.debug(f"Performance metrics: Using trades command statistics - {metrics['trades']['total']} trades, {metrics['trades']['win_rate']*100:.1f}% win rate")
                    
            except Exception as trades_err:
                logger.debug(f"Could not get trades statistics from trading_bot command: {trades_err}")
                import traceback
                logger.debug(traceback.format_exc())
                # Fallback: try database if available
                if hasattr(trading_bot, 'database') and trading_bot.database:
                    try:
                        from datetime import datetime, timedelta, timezone
                        start_time = datetime.now(timezone.utc) - timedelta(days=period_days)
                        
                        with trading_bot.database.get_connection() as conn:
                            cursor = conn.cursor()
                            cursor.execute("""
                                SELECT 
                                    strategy_name,
                                    symbol,
                                    pnl,
                                    exit_time,
                                    EXTRACT(HOUR FROM exit_time) as exit_hour
                                FROM trade_history
                                WHERE account_id = %s 
                                    AND exit_time >= %s
                                    AND exit_time IS NOT NULL
                                    AND pnl IS NOT NULL
                                ORDER BY exit_time
                            """, (str(account_id), start_time))
                            
                            rows = cursor.fetchall()
                            wins = []
                            losses = []
                            
                            for row in rows:
                                strategy = row[0] or 'manual'
                                symbol = row[1] or 'unknown'
                                pnl = float(row[2] or 0)
                                exit_hour = int(row[4]) if row[4] else None
                                
                                metrics['trades']['total'] += 1
                                if pnl > 0:
                                    metrics['trades']['winning'] += 1
                                    wins.append(pnl)
                                elif pnl < 0:
                                    metrics['trades']['losing'] += 1
                                    losses.append(abs(pnl))
                                
                                # Track by strategy, symbol, hour (same as above)
                                if strategy not in metrics['by_strategy']:
                                    metrics['by_strategy'][strategy] = {'trades': 0, 'pnl': 0.0, 'wins': 0, 'losses': 0}
                                metrics['by_strategy'][strategy]['trades'] += 1
                                metrics['by_strategy'][strategy]['pnl'] += pnl
                                if pnl > 0:
                                    metrics['by_strategy'][strategy]['wins'] += 1
                                else:
                                    metrics['by_strategy'][strategy]['losses'] += 1
                                
                                if symbol not in metrics['by_symbol']:
                                    metrics['by_symbol'][symbol] = {'trades': 0, 'pnl': 0.0, 'wins': 0, 'losses': 0}
                                metrics['by_symbol'][symbol]['trades'] += 1
                                metrics['by_symbol'][symbol]['pnl'] += pnl
                                if pnl > 0:
                                    metrics['by_symbol'][symbol]['wins'] += 1
                                else:
                                    metrics['by_symbol'][symbol]['losses'] += 1
                                
                                if exit_hour is not None:
                                    if exit_hour not in metrics['by_hour']:
                                        metrics['by_hour'][exit_hour] = {'trades': 0, 'wins': 0, 'losses': 0}
                                    metrics['by_hour'][exit_hour]['trades'] += 1
                                    if pnl > 0:
                                        metrics['by_hour'][exit_hour]['wins'] += 1
                                    else:
                                        metrics['by_hour'][exit_hour]['losses'] += 1
                                
                                if pnl > metrics['trades']['largest_win']:
                                    metrics['trades']['largest_win'] = pnl
                                if pnl < metrics['trades']['largest_loss']:
                                    metrics['trades']['largest_loss'] = pnl
                            
                            if metrics['trades']['total'] > 0:
                                metrics['trades']['win_rate'] = metrics['trades']['winning'] / metrics['trades']['total']
                            
                            if wins:
                                metrics['trades']['avg_win'] = sum(wins) / len(wins)
                            if losses:
                                metrics['trades']['avg_loss'] = sum(losses) / len(losses)
                            
                            total_wins = sum(wins) if wins else 0
                            total_losses = sum(losses) if losses else 0
                            if total_losses > 0:
                                metrics['trades']['profit_factor'] = total_wins / total_losses
                            elif total_wins > 0:
                                # Use a large number instead of Infinity (JSON doesn't support Infinity)
                                metrics['trades']['profit_factor'] = 999999.0  # All wins, no losses
                            else:
                                metrics['trades']['profit_factor'] = 0.0
                            
                            for key in ['by_strategy', 'by_symbol']:
                                for name, data in metrics[key].items():
                                    if data['trades'] > 0:
                                        data['win_rate'] = data['wins'] / data['trades']
                            
                            for hour, data in metrics['by_hour'].items():
                                if data['trades'] > 0:
                                    data['win_rate'] = data['wins'] / data['trades']
                    except Exception as db_err:
                        logger.debug(f"Could not fetch performance metrics from database fallback: {db_err}")
            
            # Get strategy performance if available
            if hasattr(trading_bot, 'strategy_manager') and trading_bot.strategy_manager:
                try:
                    for strategy_name, strategy in trading_bot.strategy_manager.strategies.items():
                        if hasattr(strategy, 'metrics'):
                            metrics['by_strategy'][strategy_name] = {
                                'trades': strategy.metrics.total_trades,
                                'wins': strategy.metrics.winning_trades,
                                'losses': strategy.metrics.losing_trades,
                                'win_rate': strategy.metrics.win_rate,
                                'pnl': strategy.metrics.total_pnl,
                                'profit_factor': strategy.metrics.profit_factor,
                                'avg_win': strategy.metrics.avg_win,
                                'avg_loss': strategy.metrics.avg_loss
                            }
                except Exception as e:
                    logger.debug(f"Could not get strategy metrics: {e}")
            
            # Serialize metrics with Infinity/NaN handling
            safe_metrics = json_serialize_safe(metrics)
            response = web.json_response(safe_metrics)
            response.headers['Access-Control-Allow-Origin'] = '*'
            return response
        except Exception as e:
            logger.error(f"Error fetching performance metrics: {e}")
            import traceback
            logger.error(traceback.format_exc())
            response = web.json_response({'error': str(e)}, status=500)
            response.headers['Access-Control-Allow-Origin'] = '*'
            return response
    
    async def handle_risk_metrics(request):
        """Get real-time risk metrics - DLL, MLL, drawdown, position exposure."""
        try:
            account_id = None
            if hasattr(trading_bot, 'selected_account') and trading_bot.selected_account:
                if isinstance(trading_bot.selected_account, dict):
                    account_id = trading_bot.selected_account.get('id')
                else:
                    account_id = str(trading_bot.selected_account)
            
            if not account_id:
                response = web.json_response({'error': 'No account selected'}, status=400)
                response.headers['Access-Control-Allow-Origin'] = '*'
                return response
            
            # Get account state and compliance
            account_state = {}
            compliance = {}
            
            if hasattr(trading_bot, 'account_tracker') and trading_bot.account_tracker:
                try:
                    account_state = trading_bot.account_tracker.get_state(account_id=str(account_id))
                    compliance = trading_bot.account_tracker.check_compliance(account_id=str(account_id))
                except Exception as e:
                    logger.debug(f"Error getting risk metrics: {e}")

            # Prefer the unified account state (includes derived PnL)
            account_state_resp = None
            account_state_data = {}
            try:
                account_state_resp = await handle_account_state(None)
                if hasattr(account_state_resp, 'text') and account_state_resp.text:
                    account_state_data = json.loads(account_state_resp.text)
                elif hasattr(account_state_resp, 'body') and account_state_resp.body:
                    account_state_data = json.loads(account_state_resp.body.decode('utf-8'))
            except Exception as e:
                logger.debug(f"Error getting unified account state for risk metrics: {e}")
            
            # Get positions for exposure calculation
            positions = []
            try:
                pos_resp = await handle_get_positions(None)
                if hasattr(pos_resp, 'text'):
                    pos_data = json.loads(pos_resp.text)
                    positions = pos_data.get('positions', [])
            except Exception as e:
                logger.debug(f"Error getting positions for risk metrics: {e}")
            
            # Calculate position exposure by symbol
            position_exposure = {}
            for pos in positions:
                symbol = pos.get('symbol', 'UNKNOWN')
                qty = abs(float(pos.get('quantity', 0) or pos.get('size', 0)))
                if symbol not in position_exposure:
                    position_exposure[symbol] = 0
                position_exposure[symbol] += qty
            
            # Calculate current drawdown
            current_balance = float(
                account_state_data.get('balance', 0)
                or account_state.get('current_balance', 0)
                or account_state.get('balance', 0)
            )
            starting_balance = float(
                account_state_data.get('starting_balance', 0)
                or account_state.get('starting_balance', current_balance)
                or current_balance
            )
            highest_balance = float(
                account_state_data.get('highest_eod_balance', 0)
                or account_state.get('highest_eod_balance', 0)
                or account_state.get('highest_balance', current_balance)
                or starting_balance
            )
            current_drawdown = current_balance - highest_balance if highest_balance > 0 else 0
            max_drawdown = float(account_state.get('max_drawdown', 0))
            
            # Calculate percentages
            dll_limit = float(
                compliance.get('dll_limit', 0)
                or account_state_data.get('daily_loss_limit', 0)
                or 0
            )
            total_pnl = float(
                account_state_data.get('total_pnl', 0)
                or (account_state_data.get('realized_pnl', 0) + account_state_data.get('unrealized_pnl', 0))
            )
            dll_used = abs(min(0.0, total_pnl))
            dll_remaining = max(0.0, dll_limit + total_pnl) if dll_limit > 0 else 0.0
            dll_percentage = (dll_used / dll_limit * 100) if dll_limit > 0 else 0
            
            mll_limit = float(
                compliance.get('mll_limit', 0)
                or account_state_data.get('maximum_loss_limit', 0)
                or 0
            )
            trailing_loss = max(0.0, highest_balance - current_balance) if highest_balance > 0 else 0.0
            mll_used = trailing_loss
            mll_remaining = max(0.0, mll_limit - trailing_loss) if mll_limit > 0 else 0.0
            mll_percentage = (mll_used / mll_limit * 100) if mll_limit > 0 else 0
            
            # Risk per trade (average)
            risk_per_trade = 0.0
            if hasattr(trading_bot, 'risk_manager') and trading_bot.risk_manager:
                try:
                    # Get default risk per trade from risk manager
                    risk_per_trade = getattr(trading_bot.risk_manager, 'risk_per_trade', 0.0)
                except Exception:
                    logger.debug("risk_per_trade from risk_manager failed", exc_info=True)

            risk_metrics = {
                'account_id': account_id,
                'dll_limit': dll_limit,
                'dll_used': dll_used,
                'dll_remaining': dll_remaining,
                'dll_percentage': min(100, max(0, dll_percentage)),
                'dll_violated': total_pnl <= -dll_limit if dll_limit > 0 else False,
                'mll_limit': mll_limit,
                'mll_used': mll_used,
                'mll_remaining': mll_remaining,
                'mll_percentage': min(100, max(0, mll_percentage)),
                'mll_violated': (current_balance <= (highest_balance - mll_limit)) if mll_limit > 0 and highest_balance > 0 else False,
                'current_drawdown': current_drawdown,
                'max_drawdown': max_drawdown,
                'current_balance': current_balance,
                'starting_balance': starting_balance,
                'highest_balance': highest_balance,
                'position_exposure': position_exposure,
                'risk_per_trade': risk_per_trade,
                'is_compliant': compliance.get('is_compliant', True),
                'violations': compliance.get('violations', []),
                'warnings': []
            }
            
            # Add warnings for approaching limits
            if dll_percentage >= 80:
                risk_metrics['warnings'].append(f"⚠️ Approaching DLL: {dll_percentage:.1f}% used")
            if mll_percentage >= 80:
                risk_metrics['warnings'].append(f"⚠️ Approaching MLL: {mll_percentage:.1f}% used")
            if current_drawdown < -500:
                risk_metrics['warnings'].append(f"⚠️ Significant drawdown: ${abs(current_drawdown):.2f}")
            
            response = web.json_response(risk_metrics)
            response.headers['Access-Control-Allow-Origin'] = '*'
            return response
        except Exception as e:
            logger.error(f"Error fetching risk metrics: {e}")
            import traceback
            logger.error(traceback.format_exc())
            response = web.json_response({'error': str(e)}, status=500)
            response.headers['Access-Control-Allow-Origin'] = '*'
            return response
    
    # Enhanced trades endpoint with StrategIQ-style metrics
    async def handle_get_trades(request):
        """Get enhanced trades list with points, Max RU/DD, cumulative P&L."""
        try:
            account_id = None
            if hasattr(trading_bot, 'selected_account') and trading_bot.selected_account:
                if isinstance(trading_bot.selected_account, dict):
                    account_id = trading_bot.selected_account.get('id')
                else:
                    account_id = str(trading_bot.selected_account)
            
            if not account_id:
                return web.json_response({'trades': [], 'error': 'No account selected'})

            # Date range. Accepts:
            #   - start_date / end_date  (ISO or m/d/y, parsed by _handle_trades)
            #   - since / until          (alias for start_date / end_date)
            #   - period=N (days)        (last N days; ignored if since/start_date given)
            start_date = None
            end_date = None
            if request and hasattr(request, 'query'):
                start_date = request.query.get('start_date') or request.query.get('since')
                end_date = request.query.get('end_date') or request.query.get('until')
                if not start_date:
                    try:
                        period_days = int(request.query.get('period', 0))
                    except (TypeError, ValueError):
                        period_days = 0
                    if period_days > 0:
                        start_date = (
                            datetime.now(timezone.utc) - timedelta(days=period_days)
                        ).isoformat()
            
            # Get order history
            from core.cli_command_parser import CLICommandParser
            parser = CLICommandParser(trading_bot)
            trades_args = []
            if start_date:
                trades_args.append(start_date)
            if end_date:
                trades_args.append(end_date)
            trades_result = await parser._handle_trades(trades_args)
            
            if not trades_result or 'trades' not in trades_result:
                return web.json_response({'trades': [], 'statistics': {}})
            
            trades = trades_result.get('trades', [])

            # Pair half-turn opens with their matching closes so the recap
            # modal has real entry_time / entry_price (Trade/search returns
            # one record per fill — opens have profitAndLoss=null and the
            # CLI parser drops them, leaving us with close-only rows whose
            # creationTimestamp is the *exit* time). FIFO match within
            # symbol works for futures because positions are flat-or-one
            # per symbol on TopStepX prop accounts; for partial scale-outs
            # the oldest open keeps providing the entry until exhausted.
            pairings: Dict[str, Dict[str, Any]] = {}
            order_tag_by_id: Dict[str, str] = {}
            try:
                hist_orders = await trading_bot.get_order_history(
                    account_id=account_id,
                    limit=2000,
                    start_timestamp=start_date,
                    end_timestamp=end_date,
                )
                for o in hist_orders or []:
                    oid = str(o.get("id") or o.get("orderId") or "")
                    if oid:
                        order_tag_by_id[oid] = str(o.get("customTag") or o.get("custom_tag") or "")
            except Exception as hist_exc:
                logger.debug("order history for trade tags failed: %s", hist_exc, exc_info=True)
            try:
                raw_trades = await trading_bot.get_trades_from_api(
                    account_id=account_id,
                    start_date=start_date,
                    end_date=end_date,
                ) or []
                raw_sorted = sorted(
                    raw_trades,
                    key=lambda t: str(t.get('creationTimestamp') or t.get('timestamp') or ''),
                )
                open_q: Dict[str, List[Dict[str, Any]]] = {}
                for t in raw_sorted:
                    sym_t = (t.get('symbol') or '').upper()
                    if not sym_t:
                        continue
                    if t.get('is_half_turn', False):
                        open_q.setdefault(sym_t, []).append(t)
                    else:
                        queue = open_q.get(sym_t, [])
                        if queue:
                            opener = queue.pop(0)
                            opener_oid = str(opener.get("order_id") or opener.get("orderId") or "")
                            pairings[str(t.get('id') or '')] = {
                                'entry_time': opener.get('creationTimestamp') or opener.get('timestamp'),
                                'entry_price': opener.get('price'),
                                'entry_order_id': opener_oid or opener.get('order_id') or opener.get('orderId'),
                                'entry_order_tag': order_tag_by_id.get(opener_oid, ''),
                                # Trade/search "side" is the FILL side. The open
                                # fill's side IS the position direction (BUY=LONG,
                                # SELL=SHORT) — record it so we can correctly
                                # label closes whose fill-side is the *opposite*
                                # of the position.
                                'position_side': opener.get('side'),
                            }
            except Exception as pair_exc:
                logger.debug("trade pairing failed: %s", pair_exc, exc_info=True)
            
            # Helper function to calculate points
            def calculate_trade_points(trade: Dict) -> float:
                """Calculate points difference for a trade."""
                entry = float(trade.get('entry_price', 0) or 0)
                exit_price = float(trade.get('exit_price', 0) or 0)
                side = trade.get('side', '').upper()
                
                if side in ('LONG', 'BUY'):
                    return exit_price - entry
                else:  # SHORT or SELL
                    return entry - exit_price
            
            # Helper function to get point value for symbol
            def get_point_value(symbol: str) -> float:
                """Get point value for symbol."""
                try:
                    return trading_bot._get_point_value(symbol)
                except Exception:
                    logger.debug(
                        "_get_point_value failed for %s; using defaults", symbol, exc_info=True
                    )
                    # Default point values for common symbols
                    defaults = {'MNQ': 2.0, 'MES': 5.0, 'MYM': 1.0, 'M2K': 5.0, 'MGC': 10.0, 'GC': 10.0}
                    return defaults.get(symbol.upper(), 1.0)
            
            # Enhance trades with additional metrics. cumulative_pnl is
            # NOT initialised here — the running sum is computed *after*
            # leg aggregation (see _aggregate_trade_legs) so multi-leg
            # trades count once.
            enhanced_trades = []
            starting_balance = 0.0
            
            # Get starting balance from account state
            try:
                account_state_resp = await handle_account_state(None)
                account_state = {}
                if hasattr(account_state_resp, 'text') and account_state_resp.text:
                    account_state = json.loads(account_state_resp.text)
                elif hasattr(account_state_resp, 'body') and account_state_resp.body:
                    account_state = json.loads(account_state_resp.body.decode('utf-8'))
                
                current_balance = float(account_state.get('balance', 0))
                realized_pnl = float(account_state.get('realized_pnl', 0))
                starting_balance = current_balance - realized_pnl
            except Exception:
                logger.debug("drawdown helper: account_state parse failed", exc_info=True)

            # Helper to get sortable timestamp from trade
            def get_trade_timestamp(t):
                """Get a sortable timestamp from trade data."""
                ts = t.get('exit_time') or t.get('exitTime') or t.get('entry_time') or t.get('entryTime') or t.get('timestamp') or t.get('creationTimestamp') or ''
                if isinstance(ts, datetime):
                    return ts.isoformat()
                return str(ts) if ts else ''
            
            # Sort trades by exit time (oldest first for cumulative calculation)
            sorted_trades = sorted(trades, key=get_trade_timestamp)
            
            for idx, trade in enumerate(sorted_trades):
                # Normalize commonly used fields across API variants
                entry_time = trade.get('entry_time') or trade.get('entryTime') or trade.get('timestamp') or trade.get('creationTimestamp')
                exit_time = trade.get('exit_time') or trade.get('exitTime') or trade.get('timestamp') or trade.get('creationTimestamp')
                entry_price = trade.get('entry_price') or trade.get('entryPrice') or trade.get('price') or trade.get('avgPrice')
                exit_price = trade.get('exit_price') or trade.get('exitPrice') or trade.get('price') or trade.get('avgPrice')
                if entry_price is None:
                    entry_price = 0
                if exit_price is None:
                    exit_price = entry_price

                # Calculate points
                trade_with_prices = dict(trade)
                trade_with_prices['entry_price'] = entry_price
                trade_with_prices['exit_price'] = exit_price
                points = calculate_trade_points(trade_with_prices)

                # Get PnL from various possible field names in API response
                pnl = float(
                    trade.get('pnl') or 
                    trade.get('profitAndLoss') or 
                    trade.get('profit_and_loss') or 
                    trade.get('netPnl') or 
                    trade.get('net_pnl') or 
                    trade.get('realizedPnl') or
                    0
                )

                # First preference: real entry data from the matching half-turn
                # open fill (paired above). This gives us the actual entry
                # timestamp AND price from the broker, no derivation needed.
                derived_entry_from_exit = False
                paired_entry = pairings.get(str(trade.get('id') or ''))

                # Resolve TRUE position direction. Trade/search returns one
                # record per fill; the close-fill side is the *opposite* of
                # the position (sell-to-close LONG, buy-to-close SHORT). The
                # open fill's side IS the position direction. When pairing
                # found the open, use it; otherwise fall back to the close
                # fill side (best we can do).
                close_side_up = (trade.get('side') or '').upper()
                position_side_up = close_side_up
                if paired_entry:
                    if paired_entry.get('entry_time'):
                        entry_time = paired_entry['entry_time']
                    pep = paired_entry.get('entry_price')
                    if pep is not None:
                        try:
                            entry_price = float(pep)
                        except (TypeError, ValueError):
                            pass
                    pside = paired_entry.get('position_side')
                    if pside is not None:
                        # Adapter normalizes 0->BUY, 1->SELL on raw fills, but
                        # accept either form just in case.
                        if isinstance(pside, str):
                            position_side_up = pside.upper()
                        elif isinstance(pside, (int, float)):
                            position_side_up = 'BUY' if int(pside) == 0 else 'SELL'

                # Fallback: when the broker's Trade/search returns one fill per
                # record (entry_price == exit_price), points calculated from
                # prices alone is always 0. Derive points from
                #   pnl / (point_value * quantity)
                # so the UI shows the actual price movement of the trade. We
                # also reconstruct the missing entry price from the exit price
                # and signed points when no paired open was found, using the
                # POSITION direction (not the close-fill side):
                #   LONG  (BUY position): entry = exit - pts
                #   SHORT (SELL position): entry = exit + pts
                if abs(points) < 1e-9 and abs(pnl) > 1e-9:
                    try:
                        sym = (trade.get('symbol') or '').upper()
                        qty = float(trade.get('quantity', 0) or 0) or 1.0
                        pv = float(get_point_value(sym) or 0)
                        if pv > 0 and qty > 0:
                            points = pnl / (pv * qty)
                    except Exception:
                        logger.debug("derived-points fallback failed", exc_info=True)
                if abs(points) > 1e-9:
                    try:
                        ep_f = float(entry_price or 0)
                        xp_f = float(exit_price or 0)
                        if ep_f == xp_f:
                            # When pairing failed we don't know the position
                            # direction reliably from the close fill alone, so
                            # we use the SIGN of pnl to disambiguate:
                            # for a profit, LONG has exit>entry and SHORT has
                            # entry>exit. Trust pnl over points sign here.
                            if pnl >= 0:
                                # Profit: assume LONG when close-fill=SELL,
                                # SHORT when close-fill=BUY.
                                if close_side_up in ('SELL',):
                                    entry_price = round(xp_f - abs(float(points)), 2)
                                    position_side_up = 'BUY'
                                else:  # BUY close-fill -> SHORT
                                    entry_price = round(xp_f + abs(float(points)), 2)
                                    position_side_up = 'SELL'
                            else:
                                # Loss: LONG has exit<entry, SHORT has exit>entry
                                if close_side_up in ('SELL',):
                                    entry_price = round(xp_f + abs(float(points)), 2)
                                    position_side_up = 'BUY'
                                else:
                                    entry_price = round(xp_f - abs(float(points)), 2)
                                    position_side_up = 'SELL'
                            derived_entry_from_exit = True
                    except Exception:
                        logger.debug("derived-entry-from-exit failed", exc_info=True)
                # Recompute points using the resolved position direction so
                # the sign matches the displayed pnl (profit -> +pts, loss -> -pts).
                try:
                    ep_f = float(entry_price or 0)
                    xp_f = float(exit_price or 0)
                    if position_side_up in ('LONG', 'BUY'):
                        points = xp_f - ep_f
                    else:  # SHORT / SELL
                        points = ep_f - xp_f
                except Exception:
                    pass
                
                # cumulative_pnl is intentionally NOT computed per-leg —
                # it would double/triple-count multi-leg trades. The
                # running total is recomputed below over the aggregated
                # logical-trade list, so the equity curve and the
                # cumulative column in the trades table both match the
                # collapsed rows the operator sees.

                # Max RU/DD: use available API fields if present
                max_ru = (
                    trade.get('max_ru') or trade.get('maxRu') or trade.get('max_runup') or
                    trade.get('maxRunup') or trade.get('maxRunningUnrealized') or trade.get('max_unrealized')
                )
                max_dd = (
                    trade.get('max_dd') or trade.get('maxDd') or trade.get('max_drawdown') or
                    trade.get('maxDrawdown') or trade.get('maxAdverseExcursion')
                )
                
                # Convert datetime objects to ISO format strings for JSON serialization
                serialized_trade = {}
                for key, value in trade.items():
                    if isinstance(value, datetime):
                        serialized_trade[key] = value.isoformat()
                    else:
                        serialized_trade[key] = value

                # Ensure normalized fields are present for UI rendering. The
                # entry_price may have been reconstructed above from exit ± pts
                # when the broker returned a single fill record.
                serialized_trade['entry_time'] = entry_time or serialized_trade.get('entry_time') or ''
                serialized_trade['exit_time'] = exit_time or serialized_trade.get('exit_time') or ''
                serialized_trade['entry_price'] = float(entry_price) if entry_price is not None else 0.0
                serialized_trade['exit_price'] = float(exit_price) if exit_price is not None else 0.0
                # Always emit the POSITION direction in `side` (BUY=long, SELL=short)
                # so the trades table and recap modal label trades the way humans
                # think about them, not the way the broker labels the closing fill.
                serialized_trade['side'] = position_side_up
                serialized_trade['close_fill_side'] = close_side_up

                # Enhanced trade object. We deliberately do NOT compute
                # ``cumulative_pnl`` / ``trade_number`` here — both are
                # recomputed AFTER aggregating legs into logical trades
                # (otherwise a 3-fill exit would advance cumulative_pnl
                # 3× and the equity curve would inherit the same triple-
                # count that broke max-drawdown / win-rate / streaks).
                # ``entry_order_id`` is the parent-order id of the
                # opening fill (from the FIFO pairing dict above) and is
                # the primary group key for aggregation.
                entry_oid = str(
                    (paired_entry or {}).get("entry_order_id")
                    or trade.get("order_id")
                    or trade.get("orderId")
                    or ""
                )
                entry_tag = (
                    (paired_entry or {}).get("entry_order_tag")
                    or order_tag_by_id.get(entry_oid, "")
                    or trade.get("customTag")
                    or trade.get("custom_tag")
                    or ""
                )
                src_meta = _classify_order_tag(entry_tag)
                enhanced_trade = {
                    **serialized_trade,  # Include all original fields (with datetime converted to strings)
                    'points': round(points, 2),
                    'fees': round(float(trade.get('quantity', 0) or 0) * 2.40, 2), # $2.40 per round trip
                    'derived_entry': derived_entry_from_exit,
                    'entry_order_id': entry_oid or (paired_entry or {}).get('entry_order_id'),
                    'entry_unix': _trade_time_to_unix(entry_time),
                    'exit_unix': _trade_time_to_unix(exit_time),
                    'custom_tag': src_meta.get('custom_tag') or entry_tag,
                    'source': src_meta.get('source', 'manual'),
                    'source_label': src_meta.get('source_label', 'manual'),
                    'strategy': src_meta.get('strategy') or '',
                }
                enhanced_trades.append(enhanced_trade)

            # Collapse fill-level legs into logical trades. See
            # ``_aggregate_trade_legs`` docstring — this is what fixes
            # the inflated trade count, win rate, max-DD and equity
            # curve that operators were seeing in the v2 dashboard.
            aggregated = _aggregate_trade_legs(enhanced_trades)

            # Recompute cumulative_pnl, cumulative_equity, trade_number
            # over the *aggregated* sequence (still oldest first).
            agg_cum = 0.0
            for a_idx, a in enumerate(aggregated):
                agg_cum += float(a.get('pnl', 0) or 0)
                a['cumulative_pnl'] = round(agg_cum, 2)
                a['cumulative_equity'] = round(starting_balance + agg_cum, 2)
                a['trade_number'] = len(aggregated) - a_idx  # newest = 1

            total_legs = sum(int(a.get('legs_count', 1) or 1) for a in aggregated)

            # Recompute statistics from the logical-trade granularity so
            # win-rate / streaks / profit-factor / max-DD all match the
            # rows the operator sees in the table. Falls back to the
            # leg-level stats from _handle_trades if the helper isn't
            # available for some reason.
            statistics: Dict[str, Any] = {}
            try:
                if hasattr(trading_bot, '_calculate_trade_statistics'):
                    statistics = trading_bot._calculate_trade_statistics(aggregated) or {}
            except Exception:
                logger.debug("aggregated stats recompute failed", exc_info=True)
                statistics = {}
            if not statistics:
                statistics = trades_result.get('statistics', {}) or {}

            def _recompute_stats(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
                if not rows:
                    return {}
                try:
                    if hasattr(trading_bot, '_calculate_trade_statistics'):
                        return trading_bot._calculate_trade_statistics(rows) or {}
                except Exception:
                    logger.debug("stats recompute failed", exc_info=True)
                return {}

            statistics_by_source: Dict[str, Any] = {
                "all": statistics,
                "auto": _recompute_stats([a for a in aggregated if a.get("source") == "auto"]),
                "manual": _recompute_stats([a for a in aggregated if a.get("source") == "manual"]),
                "bracket": _recompute_stats([a for a in aggregated if a.get("source") == "bracket"]),
            }
            for bucket in statistics_by_source.values():
                if bucket and starting_balance > 0:
                    max_dd = bucket.get("max_drawdown", 0.0)
                    bucket["max_drawdown_pct"] = round((max_dd / starting_balance) * 100.0, 2)
                elif bucket:
                    bucket["max_drawdown_pct"] = 0.0

            # Add max_drawdown_pct if we have starting balance
            if statistics and starting_balance > 0:
                max_dd = statistics.get('max_drawdown', 0.0)
                statistics['max_drawdown_pct'] = round((max_dd / starting_balance) * 100.0, 2)
            elif statistics:
                statistics['max_drawdown_pct'] = 0.0

            # Reverse to show newest first
            aggregated.reverse()

            response = web.json_response({
                'trades': aggregated,
                'statistics': statistics,
                'statistics_by_source': statistics_by_source,
                'total_trades': len(aggregated),
                'total_legs': total_legs,
            })
            response.headers['Access-Control-Allow-Origin'] = '*'
            return response
        except Exception as e:
            logger.error(f"Error fetching enhanced trades: {e}")
            import traceback
            logger.error(traceback.format_exc())
            response = web.json_response({'trades': [], 'error': str(e)}, status=500)
            response.headers['Access-Control-Allow-Origin'] = '*'
            return response
    
    # Register new endpoints
    app.router.add_get('/api/chart/pnl/history', handle_pnl_history)
    app.router.add_options('/api/chart/pnl/history', handle_options)
    app.router.add_get('/api/chart/trades', handle_get_trades)
    app.router.add_options('/api/chart/trades', handle_options)
    app.router.add_get('/api/chart/performance/metrics', handle_performance_metrics)
    app.router.add_options('/api/chart/performance/metrics', handle_options)
    app.router.add_get('/api/chart/risk/metrics', handle_risk_metrics)
    app.router.add_options('/api/chart/risk/metrics', handle_options)
    app.router.add_get('/api/chart/theme/page', handle_page_theme)
    app.router.add_options('/api/chart/theme/page', handle_options)

    async def handle_metrics_glossary(request):
        from core.metrics_glossary import METRICS
        return web.json_response(METRICS)

    app.router.add_get('/api/metrics-glossary', handle_metrics_glossary)
    
    # Serve master control HTML (v2 Quiet Trader dashboard)
    async def _serve_master_v2_html():
        from pathlib import Path
        v2_path = Path(__file__).parent / 'master_control_v2.html'
        if not v2_path.exists():
            return None
        html_content = v2_path.read_text(encoding='utf-8')
        html_content = html_content.replace('{{SERVER_PORT}}', str(port))
        html_content = html_content.replace('{{SYMBOL}}', _chart_server_symbol or symbol)
        timeframe_value = _chart_server_timeframe or '5m'
        html_content = html_content.replace('{{TIMEFRAME}}', timeframe_value)
        try:
            from core.metrics_glossary import apply_tooltips_js

            script = f"<script>\n{apply_tooltips_js()}\n</script>"
            if "</body>" in html_content:
                html_content = html_content.replace("</body>", script + "\n</body>", 1)
        except Exception:
            logger.debug("metrics glossary inject skipped", exc_info=True)
        return html_content

    async def handle_master_control(request):
        """Serve the master control HTML page (v2)."""
        try:
            html_content = await _serve_master_v2_html()
            if html_content is None:
                html_content = """<!DOCTYPE html>
<html>
<head>
    <title>Master — Chart</title>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; margin: 0; padding: 20px; background: #1c1814; color: #faf6f0; }
        .error { color: #ff4444; padding: 20px; text-align: center; }
    </style>
</head>
<body>
    <div class="error">
        <h1>Master Control HTML Not Found</h1>
        <p>Please create gui/master_control_v2.html</p>
    </div>
</body>
</html>"""
            
            response = web.Response(text=html_content, content_type='text/html')
            response.headers['Access-Control-Allow-Origin'] = '*'
            response.headers['Cache-Control'] = 'no-cache'
            return response
        except Exception as e:
            logger.error(f"Error serving master control: {e}")
            response = web.Response(text=f"Error: {e}", status=500)
            return response

    async def handle_master_classic(request):
        """Legacy master_control.html (pre-v2)."""
        try:
            from pathlib import Path
            master_html_path = Path(__file__).parent / 'master_control.html'
            if not master_html_path.exists():
                return web.Response(status=404, text='master_control.html not found')
            html_content = master_html_path.read_text(encoding='utf-8')
            html_content = html_content.replace('{{SERVER_PORT}}', str(port))
            html_content = html_content.replace('{{SYMBOL}}', _chart_server_symbol or symbol)
            timeframe_value = _chart_server_timeframe or '5m'
            html_content = html_content.replace('{{TIMEFRAME}}', timeframe_value)
            import re
            html_content = re.sub(r'<option value="([^"]+)"([^>]*)\s+selected>', r'<option value="\1"\2>', html_content)
            html_content = re.sub(
                rf'<option value="{re.escape(timeframe_value)}"([^>]*)>',
                rf'<option value="{timeframe_value}"\1 selected>',
                html_content,
            )
            response = web.Response(text=html_content, content_type='text/html')
            response.headers['Access-Control-Allow-Origin'] = '*'
            return response
        except Exception as e:
            logger.error(f"Error serving master classic: {e}")
            return web.Response(status=500, text=f'Error: {e}')
    
    # WebSocket support for real-time updates
    _ws_clients = set()
    _ws_broadcast_task = None
    _ws_broadcast_queue = asyncio.Queue(maxsize=10)  # Batch updates queue
    _ws_batch_processor_task = None
    
    async def handle_websocket(request):
        """Handle WebSocket connections for real-time updates."""
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        
        _ws_clients.add(ws)
        logger.info(f"📡 WebSocket client connected (total: {len(_ws_clients)})")
        asyncio.create_task(_push_ws_initial_snapshot(ws))
        
        try:
            async for msg in ws:
                if msg.type == web.WSMsgType.TEXT:
                    try:
                        data = json.loads(msg.data)
                        # Handle client requests (subscribe/unsubscribe)
                        if data.get('type') == 'ping':
                            try:
                                await ws.send_json({'type': 'pong'})
                            except (ClientConnectionResetError, ConnectionResetError, OSError):
                                break
                    except json.JSONDecodeError:
                        logger.debug("chart WS ignored non-JSON message")
                elif msg.type == web.WSMsgType.ERROR:
                    logger.error(f'WebSocket error: {ws.exception()}')
        finally:
            _ws_clients.discard(ws)
            logger.info(f"📡 WebSocket client disconnected (remaining: {len(_ws_clients)})")
        
        return ws
    
    async def broadcast_update(data: dict, immediate: bool = False):
        """
        Broadcast update to all connected WebSocket clients.
        
        Args:
            data: Update data to broadcast
            immediate: If True, send immediately. If False, queue for batching (default: False)
        """
        if not _ws_clients:
            return
        
        # Check WebSocket connection state before sending
        if immediate:
            # Send immediately (for critical updates like order fills)
            dead_clients = set()
            for client in _ws_clients:
                try:
                    if hasattr(client, 'closed') and client.closed:
                        dead_clients.add(client)
                        continue
                    await client.send_json(data)
                except Exception as e:
                    logger.debug(f"Failed to send to client: {e}")
                    dead_clients.add(client)
            
            # Clean up dead connections
            for client in dead_clients:
                _ws_clients.discard(client)
        else:
            # Queue for batching (non-critical updates)
            try:
                _ws_broadcast_queue.put_nowait(data)
            except asyncio.QueueFull:
                # Queue full, drop oldest and add new
                try:
                    _ws_broadcast_queue.get_nowait()
                    _ws_broadcast_queue.put_nowait(data)
                except asyncio.QueueEmpty:
                    logger.debug("WS broadcast queue race: empty after full signal")

    async def _process_broadcast_batch():
        """Process batched WebSocket updates every 83ms (12x/sec) for fast updates."""
        while True:
            try:
                updates = []
                # Collect all queued updates
                while not _ws_broadcast_queue.empty():
                    try:
                        update = _ws_broadcast_queue.get_nowait()
                        updates.append(update)
                    except asyncio.QueueEmpty:
                        break
                
                # Send batched updates if any
                if updates and _ws_clients:
                    # Merge updates by type (keep latest of each type)
                    merged = {}
                    for update in updates:
                        update_type = update.get('type', 'unknown')
                        merged[update_type] = update
                    
                    # Send merged updates
                    dead_clients = set()
                    for client in _ws_clients:
                        try:
                            if client.closed:
                                dead_clients.add(client)
                                continue
                            # Send all merged updates
                            for update in merged.values():
                                await client.send_json(update)
                        except Exception as e:
                            logger.debug(f"Failed to send batch to client: {e}")
                            dead_clients.add(client)
                    
                    # Clean up dead connections
                    for client in dead_clients:
                        _ws_clients.discard(client)
                
                await asyncio.sleep(1.0 / 12.0)  # Batch every 83ms (12x/sec)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in broadcast batch processor: {e}")
                await asyncio.sleep(1)
    
    # Make broadcast_update globally accessible for strategy signals
    # Store it in the module namespace so it can be imported
    import sys
    current_module = sys.modules[__name__]
    current_module.broadcast_update = broadcast_update
    
    async def tail_log_file():
        """Tail the trading_bot.log file and broadcast new lines."""
        log_file_path = Path("trading_bot.log")

        def _open_log_at_safe_offset():
            with open(log_file_path, "rb") as fb:
                fb.seek(0, os.SEEK_END)
                file_size = fb.tell()
                if file_size > 10240:
                    fb.seek(file_size - 10240)
                    fb.readline()
                else:
                    fb.seek(0)
                raw = fb.read()
            return raw.decode("utf-8", errors="replace").splitlines()

        try:
            recent_lines = _open_log_at_safe_offset()
            for line in recent_lines[-50:]:
                log_data = parse_log_line(line)
                if log_data:
                    await broadcast_update({'type': 'log', 'data': log_data})

            with open(log_file_path, "r", encoding="utf-8", errors="replace") as f:
                f.seek(0, os.SEEK_END)
                while True:
                    line = f.readline()
                    if line:
                        log_data = parse_log_line(line)
                        if log_data:
                            await broadcast_update({'type': 'log', 'data': log_data})
                    else:
                        await asyncio.sleep(0.1)
        except FileNotFoundError:
            logger.warning("trading_bot.log not found, log streaming disabled")
        except Exception as e:
            logger.error(f"Error tailing log file: {e}")
    
    def parse_log_line(line: str) -> Optional[Dict]:
        """Parse a log line into structured data."""
        try:
            # Format: "2025-12-29 12:34:56,789 - module - LEVEL - message"
            parts = line.split(' - ', 3)
            if len(parts) >= 3:
                timestamp = parts[0].strip()
                level = parts[2].strip()
                message = parts[3].strip() if len(parts) >= 4 else parts[2].strip()
                
                return {
                    'timestamp': timestamp,
                    'level': level,
                    'message': message
                }
        except Exception:
            logger.debug("parse log line failed: %r", line, exc_info=True)
        return None
    
    # Track last sent data to avoid redundant broadcasts
    _last_broadcast_data = {
        'account': None,
        'positions': None,
        'orders': None,
        'strategies': None,
        'risk_metrics': None,
        'performance_metrics': None,
        'pnl_history': None
    }
    _last_ws_orders_positions_fp: Dict[str, str] = {}
    _ws_deferred_snapshot_tasks: Dict[str, asyncio.Task] = {}

    async def _schedule_deferred_snapshot(account_id_str: str, delay: float = 2.0) -> None:
        """Coalesce slow REST reconciles — instant WS patches arrive first from SignalR."""
        existing = _ws_deferred_snapshot_tasks.get(account_id_str)
        if existing and not existing.done():
            existing.cancel()

        async def _run() -> None:
            try:
                await asyncio.sleep(delay)
                await _broadcast_chart_snapshot(account_id_str, force_refresh=True)
            except asyncio.CancelledError:
                pass
            finally:
                _ws_deferred_snapshot_tasks.pop(account_id_str, None)

        _ws_deferred_snapshot_tasks[account_id_str] = asyncio.create_task(_run())

    async def _broadcast_chart_snapshot(account_id_str: str, *, force_refresh: bool = False) -> None:
        """Push normalized orders + positions to all WS clients (deduped by fingerprint)."""
        try:
            if force_refresh and hasattr(trading_bot, "state_cache") and trading_bot.state_cache:
                trading_bot.state_cache.invalidate_orders(account_id_str)
                trading_bot.state_cache.invalidate_positions(account_id_str)

            if hasattr(trading_bot, "state_cache") and trading_bot.state_cache:
                orders = await trading_bot.state_cache.get_orders(account_id_str)
                positions = await trading_bot.state_cache.get_positions(account_id_str)
            else:
                orders = await trading_bot.get_open_orders(account_id=account_id_str)
                positions = await trading_bot.get_open_positions(account_id=account_id_str)

            olist = normalize_orders_for_chart(orders or [], drop_terminal=True)
            plist = normalize_positions_for_chart(positions or [])

            ofp = _ws_snapshot_fingerprint_orders(olist)
            pfp = _ws_snapshot_fingerprint_positions(plist)
            ko, kp = f"orders:{account_id_str}", f"positions:{account_id_str}"

            if _last_ws_orders_positions_fp.get(ko) != ofp:
                _last_ws_orders_positions_fp[ko] = ofp
                await broadcast_update({"type": "orders", "data": {"orders": olist}}, immediate=True)
            if _last_ws_orders_positions_fp.get(kp) != pfp:
                _last_ws_orders_positions_fp[kp] = pfp
                await broadcast_update({"type": "positions", "data": {"positions": plist}}, immediate=True)
        except Exception as exc:
            logger.debug("WS chart snapshot broadcast failed: %s", exc)

    async def _push_ws_initial_snapshot(ws) -> None:
        """Send current orders/positions immediately when a client connects."""
        try:
            account_id = None
            if hasattr(trading_bot, "selected_account") and trading_bot.selected_account:
                if isinstance(trading_bot.selected_account, dict):
                    account_id = trading_bot.selected_account.get("id")
                else:
                    account_id = str(trading_bot.selected_account)
            if not account_id:
                return
            aid = str(account_id)
            if hasattr(trading_bot, "state_cache") and trading_bot.state_cache:
                orders = await trading_bot.state_cache.get_orders(aid) or []
                positions = await trading_bot.state_cache.get_positions(aid) or []
            else:
                orders = await trading_bot.get_open_orders(account_id=aid) or []
                positions = await trading_bot.get_open_positions(account_id=aid) or []
            olist = normalize_orders_for_chart(orders, drop_terminal=True)
            plist = normalize_positions_for_chart(positions)
            if not (hasattr(ws, "closed") and ws.closed):
                await ws.send_json({"type": "orders", "data": {"orders": olist}})
                await ws.send_json({"type": "positions", "data": {"positions": plist}})
        except Exception as exc:
            logger.debug("WS initial snapshot failed: %s", exc)

    # ============================================================================
    # EVENT-DRIVEN HANDLERS - React to events from the event bus
    # ============================================================================
    
    async def _on_order_event(event):
        """React to order events — defer slow REST reconcile; SignalR pushes instant patches."""
        try:
            from core.events import Event, EventType
            logger.debug(f"📡 Order event received: {event.type.value}")

            async def refresh_all_data():
                account_id = None
                if hasattr(trading_bot, 'selected_account') and trading_bot.selected_account:
                    if isinstance(trading_bot.selected_account, dict):
                        account_id = trading_bot.selected_account.get('id')
                    else:
                        account_id = str(trading_bot.selected_account)

                if not account_id:
                    return

                account_id_str = str(account_id)

                async with _refresh_locks[account_id_str]:
                    now = asyncio.get_event_loop().time()
                    if now - _last_refresh_time[account_id_str] < 0.25:
                        return
                    _last_refresh_time[account_id_str] = now

                # Fills/cancels affect positions — reconcile soon. Placed/updated rely on WS patch.
                if event.type in (
                    EventType.ORDER_FILLED,
                    EventType.ORDER_CANCELLED,
                    EventType.ORDER_REJECTED,
                ):
                    await _broadcast_chart_snapshot(account_id_str, force_refresh=True)
                elif event.type == EventType.ORDER_PLACED:
                    await _schedule_deferred_snapshot(account_id_str, delay=0.5)
                else:
                    await _schedule_deferred_snapshot(account_id_str, delay=2.0)

                try:
                    if hasattr(trading_bot, 'account_tracker') and trading_bot.account_tracker:
                        account_state = trading_bot.account_tracker.get_state(account_id=account_id_str)
                        await broadcast_update({'type': 'account', 'data': account_state}, immediate=True)
                except Exception as e:
                    logger.debug(f"Error refreshing account in event handler: {e}")

            asyncio.create_task(refresh_all_data())

        except Exception as e:
            logger.error(f"Error in order event handler: {e}")

    async def _on_position_event(event):
        """React to position events — defer slow REST reconcile; WS patches first."""
        try:
            from core.events import Event, EventType
            logger.debug(f"📡 Position event received: {event.type.value}")

            async def refresh_all_data():
                account_id = None
                if hasattr(trading_bot, 'selected_account') and trading_bot.selected_account:
                    if isinstance(trading_bot.selected_account, dict):
                        account_id = trading_bot.selected_account.get('id')
                    else:
                        account_id = str(trading_bot.selected_account)

                if not account_id:
                    return

                account_id_str = str(account_id)

                async with _refresh_locks[account_id_str]:
                    now = asyncio.get_event_loop().time()
                    if now - _last_refresh_time[account_id_str] < 0.25:
                        return
                    _last_refresh_time[account_id_str] = now

                if event.type in (EventType.POSITION_OPENED, EventType.POSITION_CLOSED):
                    await _broadcast_chart_snapshot(account_id_str, force_refresh=True)
                else:
                    await _schedule_deferred_snapshot(account_id_str, delay=1.5)

                try:
                    if hasattr(trading_bot, 'account_tracker') and trading_bot.account_tracker:
                        account_state = trading_bot.account_tracker.get_state(account_id=account_id_str)
                        await broadcast_update({'type': 'account', 'data': account_state}, immediate=True)
                except Exception as e:
                    logger.debug(f"Error refreshing account in event handler: {e}")

            asyncio.create_task(refresh_all_data())

        except Exception as e:
            logger.error(f"Error in position event handler: {e}")

    async def _on_account_event(event):
        """React to account events - can be batched."""
        try:
            from core.events import Event
            logger.debug(f"📡 Account event received: {event.type.value}")
            
            # Broadcast (can be batched, not critical)
            await broadcast_update({
                'type': 'account_update',
                'event_type': event.type.value,
                'data': event.data
            }, immediate=False)
        except Exception as e:
            logger.error(f"Error in account event handler: {e}")
    
    # ============================================================================

    async def websocket_broadcast_loop():
        """
        PURE EVENT-DRIVEN loop - Only heartbeats.
        Data updates are pushed immediately by event handlers (_on_order_event, etc.)
        This eliminates API spam and makes the UI much more responsive.
        """
        logger.info("📡 WebSocket broadcast loop started (PURE EVENT-DRIVEN MODE)")
        
        # Start log tailing in separate task
        log_task = asyncio.create_task(tail_log_file())
        last_reconcile = asyncio.get_event_loop().time()
        
        while True:
            try:
                if _ws_clients:
                    # Lightweight heartbeat every 30s to keep connection alive
                    from datetime import datetime, timezone
                    await broadcast_update({
                        'type': 'heartbeat',
                        'timestamp': datetime.now(timezone.utc).isoformat(),
                        'clients': len(_ws_clients)
                    }, immediate=False)

                    # Safety net: reconcile orders/positions periodically in case we miss SignalR invalidations.
                    # Keeps the Active Trading panel from drifting with stale/false orders.
                    now = asyncio.get_event_loop().time()
                    if now - last_reconcile >= 60.0:
                        last_reconcile = now
                        try:
                            account_id = None
                            if hasattr(trading_bot, 'selected_account') and trading_bot.selected_account:
                                if isinstance(trading_bot.selected_account, dict):
                                    account_id = trading_bot.selected_account.get('id')
                                else:
                                    account_id = str(trading_bot.selected_account)
                            if account_id:
                                await _broadcast_chart_snapshot(str(account_id), force_refresh=True)
                        except Exception as e:
                            logger.debug("Periodic reconcile failed: %s", e)
                
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                logger.info("📡 WebSocket broadcast loop cancelled")
                log_task.cancel()
                break
            except Exception as e:
                logger.error(f"Error in WebSocket broadcast loop: {e}")
                await asyncio.sleep(5)
    
    # Register WebSocket route (only if not already registered)
    # Check if route already exists to avoid duplicate registration
    ws_route_exists = any(
        route.method == 'GET' and str(route.resource) == '/ws'
        for route in app.router.routes()
    )
    if not ws_route_exists:
        app.router.add_get('/ws', handle_websocket)
    
    # Serve design-prototype variants (master_control_preview_{1..4}.html)
    # Used while iterating on a redesign; each variant is fully self-contained
    # (mock data, no live wiring) so they have zero impact on the live dashboard.
    async def handle_master_preview(request):
        """Serve gui/master_control_preview_{n}.html where n in {1..6}."""
        try:
            from pathlib import Path
            n = request.match_info.get('n', '1')
            if n not in {'1', '2', '3', '4', '5', '6'}:
                return web.Response(status=404, text=f'Unknown preview: {n}')
            preview_path = Path(__file__).parent / f'master_control_preview_{n}.html'
            if not preview_path.exists():
                return web.Response(status=404, text=f'Preview file not found: {preview_path.name}')
            with open(preview_path, 'r', encoding='utf-8') as f:
                html_content = f.read()
            response = web.Response(text=html_content, content_type='text/html')
            response.headers['Access-Control-Allow-Origin'] = '*'
            response.headers['Cache-Control'] = 'no-cache'
            return response
        except Exception as e:
            logger.error(f"Error serving master preview: {e}")
            return web.Response(status=500, text=f'Error: {e}')

    app.router.add_get('/master/preview/{n}', handle_master_preview)

    # Master v2 (P6 Quiet Trader) — parallel dashboard at /master/v2.
    async def handle_master_v2(request):
        """Serve the master_control_v2.html (alias of /master)."""
        try:
            html_content = await _serve_master_v2_html()
            if html_content is None:
                return web.Response(status=404, text='master_control_v2.html not found')
            response = web.Response(text=html_content, content_type='text/html')
            response.headers['Access-Control-Allow-Origin'] = '*'
            response.headers['Cache-Control'] = 'no-cache'
            return response
        except Exception as e:
            logger.error(f"Error serving master v2: {e}")
            return web.Response(status=500, text=f'Error: {e}')

    app.router.add_get('/master/v2', handle_master_v2)

    # Register main page routes
    app.router.add_get('/', handle_master_control)
    app.router.add_get('/master', handle_master_control)
    app.router.add_get('/master/classic', handle_master_classic)
    
    # Add favicon handler to prevent 404 errors
    async def handle_favicon(request):
        """Handle favicon requests."""
        return web.Response(status=204)  # No content
    
    app.router.add_get('/favicon.ico', handle_favicon)
    
    # Find available port
    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    sock.close()
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', port)
    await site.start()
    
    _chart_server = runner
    _chart_server_port = port
    _chart_server_trading_bot = trading_bot
    _chart_server_symbol = symbol
    _chart_server_timeframe = timeframe
    
    # Store WebSocket port in a file for external processes to discover
    try:
        port_file = Path('.gui_websocket_port')
        with open(port_file, 'w') as f:
            f.write(str(port))
        logger.debug(f"📝 Stored WebSocket port {port} in {port_file}")
    except Exception as e:
        logger.debug(f"Could not store WebSocket port file: {e}")
    
    # ============================================================================
    # SUBSCRIBE TO EVENT BUS - React to real-time events instead of polling!
    # ============================================================================
    if hasattr(trading_bot, 'event_bus') and trading_bot.event_bus:
        try:
            from core.events import EventType
            import warnings
            
            logger.info("📡 Subscribing GUI to trading events...")
            
            # Suppress false-positive RuntimeWarnings about coroutines
            # EventBus.subscribe() is synchronous and correctly handles async callbacks
            with warnings.catch_warnings():
                warnings.filterwarnings('ignore', category=RuntimeWarning, message='.*coroutine.*was never awaited.*')
                
                # Order events (CRITICAL - immediate GUI updates)
                trading_bot.event_bus.subscribe(EventType.ORDER_PLACED, _on_order_event)
                trading_bot.event_bus.subscribe(EventType.ORDER_FILLED, _on_order_event)
                trading_bot.event_bus.subscribe(EventType.ORDER_CANCELLED, _on_order_event)
                trading_bot.event_bus.subscribe(EventType.ORDER_REJECTED, _on_order_event)
                trading_bot.event_bus.subscribe(EventType.ORDER_UPDATED, _on_order_event)
                
                # Position events (CRITICAL - immediate GUI updates)
                trading_bot.event_bus.subscribe(EventType.POSITION_OPENED, _on_position_event)
                trading_bot.event_bus.subscribe(EventType.POSITION_CLOSED, _on_position_event)
                trading_bot.event_bus.subscribe(EventType.POSITION_UPDATED, _on_position_event)
                
                # Account events (can be batched)
                trading_bot.event_bus.subscribe(EventType.ACCOUNT_UPDATED, _on_account_event)
                trading_bot.event_bus.subscribe(EventType.BALANCE_CHANGED, _on_account_event)
                trading_bot.event_bus.subscribe(EventType.PNL_UPDATED, _on_account_event)
            
            logger.info("✅ GUI subscribed to 11 event types")
            logger.info("🚀 EVENT-DRIVEN MODE ACTIVE - 250-500x faster reactions!")
        except Exception as e:
            logger.error(f"❌ Error subscribing to events: {e}")
            logger.warning("⚠️  Falling back to polling mode")
    else:
        logger.warning("⚠️  Event bus not available, using polling mode")
    
    # ============================================================================
    
    # Start WebSocket broadcast loop and batch processor
    _ws_broadcast_task = asyncio.create_task(websocket_broadcast_loop())
    _ws_batch_processor_task = asyncio.create_task(_process_broadcast_batch())

    # Broker session warmth + live Market Hub (unaffected by ENABLE_SIGNALR=false)
    await _ensure_gui_session_warm(trading_bot, symbol, timeframe)
    
    logger.info(f"📡 Chart server started on http://127.0.0.1:{port}")
    logger.info(f"📡 WebSocket endpoint: ws://127.0.0.1:{port}/ws")
    return port


def generate_chart_html(
    symbol: str,
    timeframe: str,
    bars: List[Dict],
    output_path: Optional[str] = None,
    realtime: bool = False,
    backtest: bool = False,
    backtest_speed: float = 1.0,
    server_port: Optional[int] = None,
    trade_overlays: Optional[List[Dict[str, Any]]] = None,
    axis_time_zone: Optional[str] = None,
    morning_range_et_shade: bool = False,
    overnight_range_et_shade: bool = False,
    opening_range_et_shade: bool = False,
) -> str:
    """
    Generate standalone HTML file with TradingView Lightweight Charts.
    
    Args:
        symbol: Trading symbol (e.g., 'MNQ')
        timeframe: Timeframe (e.g., '5m')
        bars: List of bar dictionaries with keys: timestamp, open, high, low, close, volume
        output_path: Optional path to save HTML file
        realtime: Enable real-time updates
        backtest: Enable backtesting mode
        backtest_speed: Playback speed multiplier
        server_port: Port for real-time server (if realtime=True)
        trade_overlays: Optional list of trade dicts for static review charts (not live server).
            Each item may include: trade_id, side (BUY/SELL), entry_time, exit_time (Unix seconds
            or ISO strings), entry_price, exit_price, exit_reason (e.g. ``stop_loss``, ``take_profit``).
            Renders **blue** entry and **light grey** exit horizontal price lines, plus a **line
            series** from (entry_time, entry_price) to (exit_time, exit_price). Horizontal lines
            span the full chart width (they are not clipped to the trade window). No candle markers.
        axis_time_zone: Optional IANA zone for tick labels / crosshair (e.g. ``America/New_York``).
            Unix bar times are unchanged; only label formatting shifts. Recommended for US-session
            strategy review HTML so wall-clock matches ``signal.session_timezone`` / TOML docs.
        morning_range_et_shade: When True (and not ``backtest`` initial-empty mode), draws a
            semi-transparent **07:00–08:00** (exclusive of 08:00) **box** in ``axis_time_zone`` or
            ``America/New_York``: horizontal span = those bars; vertical span = **max(high)** /
            **min(low)** over the same bars (per ET calendar day), matching the morning anchor
            range geometry behind candles.
        overnight_range_et_shade: When True (and not ``backtest`` initial-empty mode), draws
            **overnight range** boxes from shipped ``overnight_range.toml`` timing (evening start
            through next-morning end, ET): session **high/low** over all bars in that window, with
            separate baseline fills for evening vs morning segments when the slice has gaps.
        opening_range_et_shade: When True, draws the **opening range** box from
            ``opening_range_breakout.toml`` ``range_start``–``range_end_open`` (ET) using session
            high/low over bars in that window.

    Returns:
        Path to generated HTML file
    """
    trade_overlays_json = json.dumps(trade_overlays or [], default=str)
    axis_tz_js = "null" if not axis_time_zone else json.dumps(axis_time_zone)
    morning_shade_js = "true" if morning_range_et_shade else "false"
    overnight_shade_js = "true" if overnight_range_et_shade else "false"
    opening_shade_js = "true" if opening_range_et_shade else "false"
    info_axis = f" | Axis: {axis_time_zone}" if axis_time_zone else ""
    info_shade = ""
    if morning_range_et_shade:
        info_shade += " | 7-8am ET range box"
    if opening_range_et_shade:
        info_shade += " | ORB ET range box"
    if overnight_range_et_shade:
        info_shade += " | overnight ET range box"
    from core.backtest.ohlcv import sanitize_ohlcv_ohlc

    # Prepare data for TradingView format
    chart_data = []
    for bar in bars:
        # Convert timestamp to seconds (TradingView expects Unix timestamp in seconds)
        if isinstance(bar.get('timestamp'), str):
            from datetime import datetime as dt
            try:
                ts = dt.fromisoformat(bar['timestamp'].replace('Z', '+00:00'))
                timestamp_sec = int(ts.timestamp())
            except (ValueError, TypeError, OSError):
                logger.debug("Skipping bar with unparseable timestamp", exc_info=True)
                continue
        elif isinstance(bar.get('timestamp'), (int, float)):
            # If in milliseconds, convert to seconds
            timestamp_sec = int(bar['timestamp'] / 1000) if bar['timestamp'] > 1e12 else int(bar['timestamp'])
        else:
            continue
        
        o0, h0, l0, c0 = (
            float(bar.get('open', 0)),
            float(bar.get('high', 0)),
            float(bar.get('low', 0)),
            float(bar.get('close', 0)),
        )
        o0, h0, l0, c0 = sanitize_ohlcv_ohlc(o0, h0, l0, c0)
        chart_data.append({
            'time': timestamp_sec,
            'open': o0,
            'high': h0,
            'low': l0,
            'close': c0,
            'volume': int(bar.get('volume', 0))
        })
    
    if not chart_data:
        raise ValueError("No valid bar data provided")
    
    # Sort chart_data by time to ensure chronological order
    chart_data.sort(key=lambda x: x['time'])
    # Duplicate Unix timestamps (e.g. stitched broker + canonical CSV) draw two candles at one x.
    _by_t: Dict[int, Dict[str, Any]] = {}
    for row in chart_data:
        t = int(row["time"])
        _by_t[t] = row
    chart_data = [_by_t[k] for k in sorted(_by_t.keys())]

    overnight_segments: List[Dict[str, Any]] = []
    if overnight_range_et_shade and chart_data:
        try:
            from core.backtest.session_shade import (
                load_overnight_range_timing_from_toml,
                overnight_range_baseline_segments,
                segments_to_jsonable,
            )

            ost, oen, oz = load_overnight_range_timing_from_toml()
            ref_unix: Optional[int] = None
            if trade_overlays:
                t0 = trade_overlays[0].get("entry_time")
                if isinstance(t0, (int, float)):
                    ref_unix = int(t0)
                elif isinstance(t0, str):
                    try:
                        from datetime import datetime as _dt

                        ref_unix = int(
                            _dt.fromisoformat(str(t0).replace("Z", "+00:00")).timestamp()
                        )
                    except (ValueError, TypeError, OSError):
                        ref_unix = None
            overnight_segments = segments_to_jsonable(
                overnight_range_baseline_segments(
                    chart_data,
                    overnight_start=ost,
                    overnight_end=oen,
                    zone=oz,
                    reference_unix=ref_unix,
                )
            )
        except Exception:
            logger.debug("overnight range shade skipped", exc_info=True)
            overnight_segments = []
    overnight_segments_json = json.dumps(overnight_segments)

    opening_segments: List[Dict[str, Any]] = []
    if opening_range_et_shade and chart_data:
        try:
            from core.backtest.session_shade import (
                load_orb_timing_from_toml,
                opening_range_baseline_segments,
                segments_to_jsonable,
            )

            rs, reo, oz = load_orb_timing_from_toml()
            ref_orb: Optional[int] = None
            if trade_overlays:
                t0 = trade_overlays[0].get("entry_time")
                if isinstance(t0, (int, float)):
                    ref_orb = int(t0)
                elif isinstance(t0, str):
                    try:
                        from datetime import datetime as _dt

                        ref_orb = int(
                            _dt.fromisoformat(str(t0).replace("Z", "+00:00")).timestamp()
                        )
                    except (ValueError, TypeError, OSError):
                        ref_orb = None
            opening_segments = segments_to_jsonable(
                opening_range_baseline_segments(
                    chart_data,
                    range_start=rs,
                    range_end_open=reo,
                    zone=oz,
                    reference_unix=ref_orb,
                )
            )
        except Exception:
            logger.debug("opening range shade skipped", exc_info=True)
            opening_segments = []
    opening_segments_json = json.dumps(opening_segments)

    # Generate HTML with TradingView Lightweight Charts
    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{symbol} {timeframe} Chart</title>
    <script src="https://unpkg.com/lightweight-charts@4.1.3/dist/lightweight-charts.standalone.production.js"></script>
    <style>
        body {{
            margin: 0;
            padding: 20px;
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
            background: #1a1a1a;
            color: #ffffff;
        }}
        .container {{
            max-width: 1400px;
            margin: 0 auto;
        }}
        .header {{
            margin-bottom: 20px;
        }}
        .header h1 {{
            margin: 0;
            font-size: 24px;
            color: #ffffff;
        }}
        .header .info {{
            margin-top: 5px;
            color: #888;
            font-size: 14px;
        }}
        #chart-container {{
            width: 100%;
            height: 600px;
            background: #1a1a1a;
            border-radius: 8px;
            overflow: hidden;
        }}
        .controls {{
            margin-top: 20px;
            padding: 15px;
            background: #2a2a2a;
            border-radius: 8px;
        }}
        .controls button {{
            background: #2962ff;
            color: white;
            border: none;
            padding: 10px 20px;
            border-radius: 4px;
            cursor: pointer;
            font-size: 14px;
            margin-right: 10px;
        }}
        .controls button:hover {{
            background: #1e53e5;
        }}
        .controls button.active {{
            background: #26a69a;
        }}
        .controls button:disabled {{
            background: #555;
            cursor: not-allowed;
        }}
        .status {{
            margin-top: 10px;
            color: #888;
            font-size: 12px;
        }}
        .toast {{
            position: fixed;
            top: 20px;
            right: 20px;
            background: #2a2a2a;
            color: #ffffff;
            padding: 16px 24px;
            border-radius: 8px;
            box-shadow: 0 4px 12px rgba(0, 0, 0, 0.5);
            z-index: 10000;
            min-width: 300px;
            max-width: 500px;
            border-left: 4px solid #2962ff;
            animation: slideIn 0.3s ease-out;
        }}
        .toast.success {{
            border-left-color: #26a69a;
        }}
        .toast.error {{
            border-left-color: #ef5350;
        }}
        .toast.warning {{
            border-left-color: #ffa726;
        }}
        @keyframes slideIn {{
            from {{
                transform: translateX(400px);
                opacity: 0;
            }}
            to {{
                transform: translateX(0);
                opacity: 1;
            }}
        }}
        .toast-title {{
            font-weight: bold;
            margin-bottom: 4px;
            font-size: 14px;
        }}
        .toast-message {{
            font-size: 12px;
            color: #d1d5db;
            line-height: 1.4;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>{symbol} {timeframe} Chart</h1>
            <div class="info">
                {len(chart_data)} bars | Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}{info_axis}{info_shade}
            </div>
        </div>
        <div id="chart-container"></div>
        {(f'''
        <div class="trading-panel" id="tradingPanel" style="background: #2a2a2a; padding: 10px; margin: 10px 0; border-radius: 8px; border: 1px solid #444;">
            <div style="display: flex; gap: 8px; align-items: center; flex-wrap: wrap;">
                <select id="symbolSelect" onchange="updateSymbol()" style="background: #1a1a1a; color: #d1d5db; border: 1px solid #444; padding: 6px; border-radius: 4px; font-size: 12px;">
                    <option value="{symbol.upper()}" selected>{symbol.upper()}</option>
                </select>
                <select id="timeframeSelect" onchange="updateTimeframe()" style="background: #1a1a1a; color: #d1d5db; border: 1px solid #444; padding: 6px; border-radius: 4px; font-size: 12px;">
                    <option value="30s"{' selected' if timeframe == '30s' else ''}>30s</option>
                    <option value="1m"{' selected' if timeframe == '1m' else ''}>1m</option>
                    <option value="5m"{' selected' if timeframe == '5m' else ''}>5m</option>
                    <option value="15m"{' selected' if timeframe == '15m' else ''}>15m</option>
                    <option value="1h"{' selected' if timeframe == '1h' else ''}>1h</option>
                    <option value="4h"{' selected' if timeframe == '4h' else ''}>4h</option>
                    <option value="1d"{' selected' if timeframe == '1d' else ''}>1d</option>
                </select>
                <select id="orderTypeSelect" onchange="updateOrderType()" style="background: #1a1a1a; color: #d1d5db; border: 1px solid #444; padding: 6px; border-radius: 4px; font-size: 12px; width: 80px;">
                    <option value="market" selected>Market</option>
                    <option value="limit">Limit</option>
                    <option value="stop">Stop</option>
                    <option value="bracket">Bracket</option>
                </select>
                <input type="number" id="quantityInput" value="1" min="1" placeholder="Qty" style="background: #1a1a1a; color: #d1d5db; border: 1px solid #444; padding: 6px; border-radius: 4px; font-size: 12px; width: 60px;">
                <input type="number" id="limitPriceInput" step="0.25" placeholder="Limit" style="background: #1a1a1a; color: #d1d5db; border: 1px solid #444; padding: 6px; border-radius: 4px; font-size: 12px; width: 80px; display: none;">
                <input type="number" id="stopPriceInput" step="0.25" placeholder="Stop" style="background: #1a1a1a; color: #d1d5db; border: 1px solid #444; padding: 6px; border-radius: 4px; font-size: 12px; width: 80px; display: none;">
                <label style="display: flex; align-items: center; gap: 4px; font-size: 11px; color: #888;">
                    <input type="checkbox" id="enableBracketCheck" onchange="updateBracket()" style="margin: 0;">
                    <span>Bracket</span>
                </label>
                <input type="number" id="stopLossPriceInput" step="0.25" placeholder="SL" style="background: #1a1a1a; color: #d1d5db; border: 1px solid #444; padding: 6px; border-radius: 4px; font-size: 12px; width: 70px; display: none;">
                <input type="number" id="takeProfitPriceInput" step="0.25" placeholder="TP" style="background: #1a1a1a; color: #d1d5db; border: 1px solid #444; padding: 6px; border-radius: 4px; font-size: 12px; width: 70px; display: none;">
                <button onclick="placeOrder('BUY')" style="background: #26a69a; color: white; border: none; padding: 6px 12px; border-radius: 4px; cursor: pointer; font-weight: bold; font-size: 12px;">BUY</button>
                <button onclick="placeOrder('SELL')" style="background: #ef5350; color: white; border: none; padding: 6px 12px; border-radius: 4px; cursor: pointer; font-weight: bold; font-size: 12px;">SELL</button>
            </div>
        </div>
        ''') if not backtest else ''}
        <div class="controls">
            <button onclick="refreshChart()">Refresh Data</button>
            <button onclick="exportData()">Export CSV</button>
            <button onclick="lastPositionUpdate=0;lastOrderUpdate=0;lastStrategyLinesUpdate=0;updatePositionLines();updateOrderLines();updateStrategyBreakoutLines();" style="background: #26a69a; color: white; border: none; padding: 6px 12px; border-radius: 4px; cursor: pointer; font-size: 12px;">Refresh Lines</button>
            {'<button id="realtimeBtn" onclick="toggleRealtime()">Start Real-Time</button>' if not backtest else ''}
            {'<div style="display: inline-block; margin-left: 10px;">Refresh: <select id="refreshRateSelect" onchange="updateRefreshRate()" style="background: #2a2a2a; color: #d1d5db; border: 1px solid #444; padding: 5px;"><option value="1" selected>1x/sec</option><option value="3">3x/sec</option><option value="6">6x/sec</option><option value="12">12x/sec</option></select></div>' if not backtest else ''}
            {'<button id="backtestBtn" onclick="toggleBacktest()">Start Backtest</button>' if backtest else ''}
            {'<button id="pauseBtn" onclick="togglePause()" style="background: #666; display: none;">Pause</button>' if backtest else ''}
            {'<button onclick="loadAllBars()" style="background: #26a69a;">Load All Bars (Test)</button>' if backtest else ''}
            {'<button onclick="exitTestMode()" style="background: #666;">Exit Test Mode</button>' if backtest else ''}
            {'<div style="display: inline-block; margin-left: 10px;">Speed: <select id="speedSelect" onchange="updateBacktestSpeed()" style="background: #2a2a2a; color: #d1d5db; border: 1px solid #444; padding: 5px;"><option value="1">1x</option><option value="2">2x</option><option value="5">5x</option><option value="10">10x</option><option value="25">25x</option><option value="50" selected>50x</option><option value="100">100x</option><option value="150">150x</option><option value="200">200x</option><option value="250">250x</option><option value="300">300x</option><option value="400">400x</option><option value="500">500x</option><option value="750">750x</option><option value="1000">1000x</option></select></div>' if backtest else ''}
            <div class="status" id="status">Chart loaded</div>
        </div>
    </div>

    <script>
        let chart = null;
        let candlestickSeries = null;
        let volumeSeries = null;
        let chartContainer = null; // Global scope for resize handler
        let positionPriceLines = []; // Position price lines (entry, stop loss, take profit)
        let orderPriceLines = []; // Order price lines (limit/stop orders)
        let strategyPriceLines = []; // overnight_range OR high/low + breakout template prices
        let lastPositionUpdate = 0; // Throttle position updates (max once per 5 seconds)
        let lastOrderUpdate = 0; // Throttle order updates (max once per 5 seconds)
        let lastStrategyLinesUpdate = 0;
        const POSITION_UPDATE_INTERVAL = 3000; // 3 seconds (reduced for faster updates)
        const ORDER_UPDATE_INTERVAL = 3000; // 3 seconds (reduced for faster updates)
        const STRATEGY_LINES_INTERVAL = 3000;
        let chartData = {json.dumps(chart_data)};
        (function dedupeChartDataByBarTime() {{
            const m = new Map();
            for (let i = 0; i < chartData.length; i++) {{
                const b = chartData[i];
                const t = Number(b.time);
                if (!isFinite(t)) continue;
                m.set(t, Object.assign({{}}, b, {{ time: t }}));
            }}
            chartData = Array.from(m.keys()).sort((a, b) => a - b).map((k) => m.get(k));
        }})();
        let tradeOverlays = {trade_overlays_json};
        let tradeRecapPriceLines = [];
        let tradeRecapConnectorSeries = [];
        let realtimeActive = {'true' if realtime else 'false'};
        let backtestMode = {'true' if backtest else 'false'}; // Mode enabled, not necessarily running
        let backtestActive = false; // Actually running
        let backtestPaused = false; // Paused state
        let backtestIndex = 0;
        let backtestInterval = null;
        let realtimeInterval = null;
        let realtimeRefreshRate = 1; // Updates per second (default: 1x/sec)
        let serverPort = {server_port if server_port else 'null'};
        let backtestSpeed = {backtest_speed}; // Speed multiplier (can be changed via dropdown)
        const baseBacktestIntervalMs = 150000; // Base interval (2.5 minutes for 5m bars)
        const symbol = '{symbol}';
        const timeframe = '{timeframe}';
        let testModeActive = false; // Flag to prevent backtest from interfering with test mode
        
        // Debug: Log initial state
        console.log('Chart initialized:', {{
            chartDataLength: chartData.length,
            backtestMode: backtestMode,
            backtestActive: backtestActive,
            firstBar: chartData[0],
            lastBar: chartData[chartData.length - 1]
        }});
        
        // Calculate interval based on timeframe (in seconds)
        function getTimeframeSeconds(tf) {{
            const match = tf.match(/(\\d+)([mhd])/);
            if (!match) return 60;
            const value = parseInt(match[1]);
            const unit = match[2];
            if (unit === 'm') return value * 60;
            if (unit === 'h') return value * 3600;
            if (unit === 'd') return value * 86400;
            return 60;
        }}
        
        const axisTimeZone = {axis_tz_js};
        const morningRangeEtShade = {morning_shade_js};
        const overnightRangeEtShade = {overnight_shade_js};
        const openingRangeEtShade = {opening_shade_js};
        const overnightRangeSegments = {overnight_segments_json};
        const openingRangeSegments = {opening_segments_json};
        function _fmtBarTime(d, opts) {{
            const o = Object.assign({{}}, opts || {{}});
            if (axisTimeZone) o.timeZone = axisTimeZone;
            return d.toLocaleString(undefined, o);
        }}
        
        const timeframeSeconds = getTimeframeSeconds(timeframe);
        const backtestIntervalMs = (timeframeSeconds * 1000) / backtestSpeed;

        function overlayUnixSeconds(v) {{
            if (v == null || v === '') return NaN;
            if (typeof v === 'number' && isFinite(v)) {{
                const n = Math.floor(v);
                return v > 1e12 ? Math.floor(v / 1000) : n;
            }}
            if (typeof v === 'string') {{
                const t = v.trim();
                if (/^-?\\d+(\\.\\d+)?$/.test(t)) {{
                    const num = parseFloat(t);
                    if (!isFinite(num)) return NaN;
                    return num > 1e12 ? Math.floor(num / 1000) : Math.floor(num);
                }}
                const ms = Date.parse(t);
                return isNaN(ms) ? NaN : Math.floor(ms / 1000);
            }}
            return NaN;
        }}

        function applyTradeOverlays() {{
            if (!tradeOverlays || tradeOverlays.length === 0) return;
            if (!chart || !candlestickSeries) return;
            try {{
                for (let i = 0; i < tradeRecapPriceLines.length; i++) {{
                    try {{ tradeRecapPriceLines[i].remove(); }} catch (e) {{}}
                }}
                tradeRecapPriceLines = [];
                for (let i = 0; i < tradeRecapConnectorSeries.length; i++) {{
                    try {{ chart.removeSeries(tradeRecapConnectorSeries[i]); }} catch (e) {{}}
                }}
                tradeRecapConnectorSeries = [];
                const overlayMarkers = [];
                if (typeof candlestickSeries.setMarkers === 'function') {{
                    candlestickSeries.setMarkers([]);
                }}
                for (const t of tradeOverlays) {{
                    const ep = t.entry_price != null ? parseFloat(t.entry_price) : NaN;
                    const xp = t.exit_price != null ? parseFloat(t.exit_price) : NaN;
                    const entryLineColor = '#2196f3';
                    const exitLineColor = '#cfd8dc';
                    if (!isNaN(ep) && typeof candlestickSeries.createPriceLine === 'function') {{
                        tradeRecapPriceLines.push(candlestickSeries.createPriceLine({{
                            price: ep,
                            title: '',
                            color: entryLineColor,
                            lineWidth: 1,
                            lineStyle: 0,
                            axisLabelVisible: true,
                        }}));
                    }}
                    if (!isNaN(xp) && typeof candlestickSeries.createPriceLine === 'function') {{
                        tradeRecapPriceLines.push(candlestickSeries.createPriceLine({{
                            price: xp,
                            title: '',
                            color: exitLineColor,
                            lineWidth: 1,
                            lineStyle: 0,
                            axisLabelVisible: true,
                        }}));
                    }}
                    const et = overlayUnixSeconds(t.entry_time);
                    let xt = overlayUnixSeconds(t.exit_time);
                    let xtDraw = xt;
                    if (isFinite(et) && isFinite(xt) && xt <= et) {{
                        xtDraw = et + timeframeSeconds;
                    }}
                    if (isFinite(et) && isFinite(xtDraw) && !isNaN(ep) && !isNaN(xp)
                            && typeof chart.addLineSeries === 'function') {{
                        try {{
                            const ln = chart.addLineSeries({{
                                color: 'rgba(100, 181, 246, 0.95)',
                                lineWidth: 2,
                                lineStyle: 0,
                                priceScaleId: 'right',
                                lastValueVisible: false,
                                priceLineVisible: false,
                                crosshairMarkerVisible: false,
                            }});
                            ln.setData([
                                {{ time: et, value: ep }},
                                {{ time: xtDraw, value: xp }},
                            ]);
                            tradeRecapConnectorSeries.push(ln);
                        }} catch (eLn) {{
                            console.warn('trade recap connector:', eLn);
                        }}
                    }}
                    const sideUp = (t.side || '').toString().toUpperCase();
                    const isLong = sideUp === 'BUY' || sideUp === 'LONG';
                    if (isFinite(et) && !isNaN(ep)) {{
                        overlayMarkers.push({{
                            time: et,
                            position: isLong ? 'belowBar' : 'aboveBar',
                            color: isLong ? '#42a5f5' : '#ef5350',
                            shape: isLong ? 'arrowUp' : 'arrowDown',
                            text: 'entry',
                        }});
                    }}
                    if (isFinite(xt) && !isNaN(xp)) {{
                        overlayMarkers.push({{
                            time: xt,
                            position: isLong ? 'aboveBar' : 'belowBar',
                            color: '#90a4ae',
                            shape: 'square',
                            text: t.exit_reason ? String(t.exit_reason) : 'exit',
                        }});
                    }}
                    const sig = overlayUnixSeconds(t.signal_time);
                    if (isFinite(sig)) {{
                        overlayMarkers.push({{
                            time: sig,
                            position: isLong ? 'belowBar' : 'aboveBar',
                            color: '#ffd54f',
                            shape: 'circle',
                            text: t.signal_label ? String(t.signal_label) : 'signal',
                        }});
                    }}
                }}
                if (overlayMarkers.length && typeof candlestickSeries.setMarkers === 'function') {{
                    overlayMarkers.sort((a, b) => a.time - b.time);
                    try {{ candlestickSeries.setMarkers(overlayMarkers); }} catch (eMk) {{ console.warn('overlay markers:', eMk); }}
                }}
            }} catch (e) {{
                console.warn('applyTradeOverlays:', e);
            }}
        }}
        
        function showToast(title, message, type = 'info') {{
            // Remove existing toasts
            const existingToasts = document.querySelectorAll('.toast');
            existingToasts.forEach(toast => toast.remove());
            
            const toast = document.createElement('div');
            toast.className = `toast ${{type}}`;
            toast.innerHTML = `
                <div class="toast-title">${{title}}</div>
                <div class="toast-message">${{message}}</div>
            `;
            document.body.appendChild(toast);
            
            // Auto-remove after 5 seconds
            setTimeout(() => {{
                toast.style.animation = 'slideIn 0.3s ease-out reverse';
                setTimeout(() => toast.remove(), 300);
            }}, 5000);
        }}
        
        function showError(message) {{
            const statusEl = document.getElementById('status');
            if (statusEl) {{
                statusEl.textContent = 'Error: ' + message;
                statusEl.style.color = '#ef5350';
            }}
            showToast('Error', message, 'error');
            console.error(message);
        }}
        
        function updateStatus(message) {{
            const statusEl = document.getElementById('status');
            if (statusEl) {{
                statusEl.textContent = message;
                statusEl.style.color = '#888';
            }}
        }}
        
        // Initialize chart after library loads
        function initChart() {{
            try {{
                // Prevent double initialization
                if (chart) {{
                    console.log('Chart already initialized, skipping');
                    return;
                }}
                
                // Check if library loaded
                if (typeof LightweightCharts === 'undefined') {{
                    showError('TradingView library failed to load');
                    return;
                }}
                
                if (!chartData || chartData.length === 0) {{
                    showError('No chart data available');
                    return;
                }}
                
                // Get chart container (assign to global variable)
                chartContainer = document.getElementById('chart-container');
                if (!chartContainer) {{
                    showError('Chart container not found');
                    return;
                }}
                
                // Ensure container has dimensions
                const containerWidth = chartContainer.clientWidth || chartContainer.offsetWidth || 1200;
                const containerHeight = 600;
                
                console.log('Chart container dimensions:', containerWidth, 'x', containerHeight);
                
                // Create chart
                chart = LightweightCharts.createChart(chartContainer, {{
                    layout: {{
                        background: {{ color: '#1a1a1a' }},
                        textColor: '#d1d5db',
                    }},
                    grid: {{
                        vertLines: {{ color: '#2a2a2a' }},
                        horzLines: {{ color: '#2a2a2a' }},
                    }},
                    crosshair: {{
                        mode: LightweightCharts.CrosshairMode.Normal,
                    }},
                    rightPriceScale: {{
                        borderColor: '#2a2a2a',
                        autoScale: true,
                    }},
                    localization: {{
                        locale: navigator.language || 'en-US',
                        timeFormatter: (timestamp) => {{
                            const date = new Date(timestamp * 1000);
                            return _fmtBarTime(date, {{
                                month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'
                            }});
                        }},
                    }},
                    timeScale: {{
                        borderColor: '#2a2a2a',
                        timeVisible: true,
                        secondsVisible: false,
                        tickMarkFormatter: (time, tickMarkType, locale) => {{
                            const T = LightweightCharts.TickMarkType;
                            const ts = (typeof time === 'number') ? time : (time?.timestamp ?? null);
                            if (typeof ts === 'number') {{
                                const d = new Date(ts * 1000);
                                if (typeof tickMarkType === 'number' && T && (
                                    tickMarkType === T.DayOfMonth || tickMarkType === T.Month || tickMarkType === T.Year
                                )) {{
                                    return _fmtBarTime(d, {{
                                        weekday: 'short', month: 'short', day: 'numeric'
                                    }});
                                }}
                                return _fmtBarTime(d, {{
                                    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'
                                }});
                            }}
                            if (time && typeof time === 'object' && 'year' in time && 'month' in time && 'day' in time) {{
                                const d = new Date(time.year, time.month - 1, time.day);
                                return d.toLocaleDateString(locale || undefined, {{
                                    weekday: 'short', month: 'short', day: 'numeric'
                                }});
                            }}
                            return '';
                        }},
                    }},
                    width: containerWidth,
                    height: containerHeight,
                }});
                
                console.log('Chart created successfully');
                
                let morningRangeSeriesList = [];
                if (morningRangeEtShade && !backtestMode && chartData.length > 0) {{
                    const tzShade = axisTimeZone || 'America/New_York';
                    const m7 = 7 * 60;
                    const m8 = 8 * 60;
                    function minutesSinceMidnightInZone(tsSec) {{
                        const d = new Date(tsSec * 1000);
                        const f = new Intl.DateTimeFormat('en-US', {{
                            timeZone: tzShade,
                            hour: '2-digit',
                            minute: '2-digit',
                            hour12: false,
                        }});
                        const s = f.format(d);
                        const m = s.match(/(\\d{{1,2}}):(\\d{{2}})/);
                        if (!m) return -1;
                        return parseInt(m[1], 10) * 60 + parseInt(m[2], 10);
                    }}
                    function etDateKey(tsSec) {{
                        const d = new Date(tsSec * 1000);
                        const f = new Intl.DateTimeFormat('en-CA', {{
                            timeZone: tzShade,
                            year: 'numeric',
                            month: '2-digit',
                            day: '2-digit',
                        }});
                        return f.format(d);
                    }}
                    const dayMap = new Map();
                    for (const b of chartData) {{
                        const mins = minutesSinceMidnightInZone(b.time);
                        if (mins < m7 || mins >= m8) continue;
                        const dk = etDateKey(b.time);
                        if (!dayMap.has(dk)) dayMap.set(dk, []);
                        dayMap.get(dk).push(b);
                    }}
                    if (typeof chart.addBaselineSeries === 'function') {{
                        for (const [dk, winBars] of dayMap.entries()) {{
                            if (!winBars || winBars.length === 0) continue;
                            let hi = -Infinity;
                            let lo = Infinity;
                            for (const b of winBars) {{
                                hi = Math.max(hi, parseFloat(b.high));
                                lo = Math.min(lo, parseFloat(b.low));
                            }}
                            if (!(hi > lo) || !isFinite(hi) || !isFinite(lo)) continue;
                            const sorted = winBars.slice().sort((a, b) => a.time - b.time);
                            const seg = sorted.map((b) => ({{ time: b.time, value: hi }}));
                            try {{
                                const s = chart.addBaselineSeries({{
                                    priceScaleId: 'right',
                                    baseValue: {{ type: 'price', price: lo }},
                                    topFillColor1: 'rgba(99, 102, 241, 0.28)',
                                    topFillColor2: 'rgba(129, 140, 248, 0.14)',
                                    topLineColor: 'rgba(0,0,0,0)',
                                    bottomFillColor1: 'rgba(0,0,0,0)',
                                    bottomFillColor2: 'rgba(0,0,0,0)',
                                    bottomLineColor: 'rgba(0,0,0,0)',
                                    lineWidth: 0,
                                    lineVisible: false,
                                    priceLineVisible: false,
                                    lastValueVisible: false,
                                    crosshairMarkerVisible: false,
                                }});
                                s.setData(seg);
                                morningRangeSeriesList.push(s);
                                const mid = (hi + lo) / 2;
                                const midSeg = sorted.map((b) => ({{ time: b.time, value: mid }}));
                                try {{
                                    const midLn = chart.addLineSeries({{
                                        color: 'rgba(226, 232, 240, 0.72)',
                                        lineWidth: 1,
                                        lineStyle: 2,
                                        priceScaleId: 'right',
                                        lastValueVisible: false,
                                        priceLineVisible: false,
                                        crosshairMarkerVisible: false,
                                    }});
                                    midLn.setData(midSeg);
                                    morningRangeSeriesList.push(midLn);
                                }} catch (e2) {{
                                    console.warn('morning range ET midline ' + dk + ':', e2);
                                }}
                            }} catch (e) {{
                                console.warn('morning range ET box ' + dk + ':', e);
                            }}
                        }}
                    }}
                }}

                let overnightRangeSeriesList = [];
                if (openingRangeEtShade && !backtestMode && chartData.length > 0
                        && openingRangeSegments && openingRangeSegments.length > 0) {{
                    if (typeof chart.addBaselineSeries === 'function') {{
                        for (let si = 0; si < openingRangeSegments.length; si++) {{
                            const seg = openingRangeSegments[si];
                            const hi = parseFloat(seg.hi);
                            const lo = parseFloat(seg.lo);
                            const ts = seg.times || [];
                            if (!(hi > lo) || !isFinite(hi) || !isFinite(lo) || ts.length === 0) continue;
                            const sorted = ts.slice().sort((a, b) => a - b);
                            const dataPts = sorted.map((t) => ({{ time: t, value: hi }}));
                            try {{
                                const s = chart.addBaselineSeries({{
                                    priceScaleId: 'right',
                                    baseValue: {{ type: 'price', price: lo }},
                                    topFillColor1: 'rgba(20, 184, 166, 0.28)',
                                    topFillColor2: 'rgba(45, 212, 191, 0.14)',
                                    topLineColor: 'rgba(0,0,0,0)',
                                    bottomFillColor1: 'rgba(0,0,0,0)',
                                    bottomFillColor2: 'rgba(0,0,0,0)',
                                    bottomLineColor: 'rgba(0,0,0,0)',
                                    lineWidth: 0,
                                    lineVisible: false,
                                    priceLineVisible: false,
                                    lastValueVisible: false,
                                    crosshairMarkerVisible: false,
                                }});
                                s.setData(dataPts);
                            }} catch (e) {{
                                console.warn('opening range ET box seg ' + si + ':', e);
                            }}
                        }}
                    }}
                }}
                if (overnightRangeEtShade && !backtestMode && chartData.length > 0
                        && overnightRangeSegments && overnightRangeSegments.length > 0) {{
                    if (typeof chart.addBaselineSeries === 'function') {{
                        for (let si = 0; si < overnightRangeSegments.length; si++) {{
                            const seg = overnightRangeSegments[si];
                            const hi = parseFloat(seg.hi);
                            const lo = parseFloat(seg.lo);
                            const ts = seg.times || [];
                            if (!(hi > lo) || !isFinite(hi) || !isFinite(lo) || ts.length === 0) continue;
                            const sorted = ts.slice().sort((a, b) => a - b);
                            const dataPts = sorted.map((t) => ({{ time: t, value: hi }}));
                            try {{
                                const s = chart.addBaselineSeries({{
                                    priceScaleId: 'right',
                                    baseValue: {{ type: 'price', price: lo }},
                                    topFillColor1: 'rgba(245, 158, 11, 0.28)',
                                    topFillColor2: 'rgba(251, 191, 36, 0.14)',
                                    topLineColor: 'rgba(0,0,0,0)',
                                    bottomFillColor1: 'rgba(0,0,0,0)',
                                    bottomFillColor2: 'rgba(0,0,0,0)',
                                    bottomLineColor: 'rgba(0,0,0,0)',
                                    lineWidth: 0,
                                    lineVisible: false,
                                    priceLineVisible: false,
                                    lastValueVisible: false,
                                    crosshairMarkerVisible: false,
                                }});
                                s.setData(dataPts);
                                overnightRangeSeriesList.push(s);
                            }} catch (e) {{
                                console.warn('overnight range ET box seg ' + si + ':', e);
                            }}
                        }}
                    }}
                }}

                // Volume first so candlesticks (added next) paint on top in the shared pane margin.
                volumeSeries = chart.addHistogramSeries({{
                    color: '#26a69a',
                    priceFormat: {{
                        type: 'volume',
                    }},
                    priceScaleId: 'volume',
                    scaleMargins: {{
                        top: 0.8,
                        bottom: 0,
                    }},
                }});
                chart.priceScale('volume').applyOptions({{
                    scaleMargins: {{
                        top: 0.8,
                        bottom: 0,
                    }},
                }});

                // Create candlestick series
                if (typeof chart.addCandlestickSeries !== 'function') {{
                    showError('addCandlestickSeries not available');
                    console.error('Available methods:', Object.getOwnPropertyNames(chart));
                    return;
                }}
                
                candlestickSeries = chart.addCandlestickSeries({{
                    upColor: '#26a69a',
                    downColor: '#ef5350',
                    borderVisible: false,
                    wickUpColor: '#26a69a',
                    wickDownColor: '#ef5350',
                    priceScaleId: 'right',
                    visible: true,
                }});
                
                // Prepare initial data
                if (!backtestMode) {{
                    // Normal mode: load all data immediately
                    const candlestickData = chartData.map(bar => ({{
                        time: bar.time,
                        open: parseFloat(bar.open),
                        high: parseFloat(bar.high),
                        low: parseFloat(bar.low),
                        close: parseFloat(bar.close),
                    }}));
                    
                    const volumeData = chartData.map(bar => ({{
                        time: bar.time,
                        value: parseInt(bar.volume) || 0,
                        color: parseFloat(bar.close) >= parseFloat(bar.open) ? '#26a69a80' : '#ef535080',
                    }}));
                    
                    // Set data
                    candlestickSeries.setData(candlestickData);
                    volumeSeries.setData(volumeData);
                    applyTradeOverlays();
                    
                    // Explicitly set visible range to show all data
                    // Use requestAnimationFrame to ensure chart has processed setData()
                    if (candlestickData.length > 0) {{
                        const firstBar = candlestickData[0];
                        const lastBar = candlestickData[candlestickData.length - 1];
                        
                        requestAnimationFrame(() => {{
                            try {{
                                chart.timeScale().setVisibleRange({{
                                    from: firstBar.time,
                                    to: lastBar.time
                                }});
                                console.log('Normal mode: Set visible range from', firstBar.time, 'to', lastBar.time);
                            }} catch (e) {{
                                console.error('Error setting visible range:', e);
                                chart.timeScale().fitContent();
                            }}
                        }});
                    }} else {{
                        chart.timeScale().fitContent();
                    }}
                    
                    updateStatus('Chart ready - ' + chartData.length + ' bars loaded');
                }} else {{
                    // Backtest mode: initialize with empty arrays, ready for replay
                    candlestickSeries.setData([]);
                    volumeSeries.setData([]);
                    
                    updateStatus('Chart ready - ' + chartData.length + ' bars loaded. Click "Start Backtest" to begin replay.');
                    console.log('Backtest mode: Chart initialized with empty series, ready for replay');
                }}
                
                // Load contracts and initialize UI
                if (serverPort) {{
                    loadContracts();
                    updateOrderType(); // Initialize order type visibility
                    updateBracket(); // Initialize bracket visibility
                    
                    // Initialize position/order lines after chart is ready
                    setTimeout(async () => {{
                        console.log('Chart initialized, updating position/order lines...');
                        lastPositionUpdate = 0; // Reset throttle to force update
                        lastOrderUpdate = 0; // Reset throttle to force update
                        lastStrategyLinesUpdate = 0;
                        await updatePositionLines();
                        await updateOrderLines();
                        await updateStrategyBreakoutLines();
                        
                        // Note: Position/order lines are now event-based only (refresh on order placement/fill)
                        // No periodic refresh interval - lines update when orders are placed or filled
                    }}, 1500);
                }}
                
                // Auto-start real-time if enabled (start immediately)
                if (realtimeActive && serverPort) {{
                    // Small delay to ensure everything is initialized
                    setTimeout(() => {{
                        toggleRealtime();
                    }}, 100);
                }}
            }} catch (error) {{
                showError('Chart error: ' + error.message);
                console.error('Full error:', error);
            }}
        }}
        
        // Real-time update function
        async function updateRealtime() {{
            if (!serverPort) {{
                updateStatus('Real-time server not available');
                return;
            }}
            
            try {{
                // Get current symbol from dropdown
                const currentSymbol = document.getElementById('symbolSelect')?.value || symbol;
                const currentTf = document.getElementById('timeframeSelect')?.value || timeframe;
                const response = await fetch(`http://127.0.0.1:${{serverPort}}/api/chart/quote?symbol=${{encodeURIComponent(currentSymbol)}}&timeframe=${{encodeURIComponent(currentTf)}}`);
                if (!response.ok) {{
                    throw new Error(`HTTP ${{response.status}}`);
                }}
                const data = await response.json();
                
                // Update position and order lines periodically (throttled)
                const now = Date.now();
                if (now - lastPositionUpdate > POSITION_UPDATE_INTERVAL) {{
                    lastPositionUpdate = now;
                    updatePositionLines().catch(err => console.debug('Position update error:', err));
                }}
                if (now - lastOrderUpdate > ORDER_UPDATE_INTERVAL) {{
                    lastOrderUpdate = now;
                    updateOrderLines().catch(err => console.debug('Order update error:', err));
                }}
                updateStrategyBreakoutLines().catch(err => console.debug('Strategy lines error:', err));
                
                if (data.latest_bar) {{
                    const bar = data.latest_bar;
                    if (!chartData || chartData.length === 0) {{
                        console.warn('No chart data available for real-time update');
                        return;
                    }}
                    
                    const lastBar = chartData[chartData.length - 1];
                    
                    // Normalize timestamps to numbers for comparison
                    // TradingView expects timestamps as Unix seconds (number)
                    const barTime = typeof bar.time === 'number' ? bar.time : (typeof bar.time === 'string' ? parseInt(bar.time) : null);
                    const lastBarTime = typeof lastBar.time === 'number' ? lastBar.time : (typeof lastBar.time === 'string' ? parseInt(lastBar.time) : null);
                    
                    if (barTime === null || lastBarTime === null) {{
                        console.warn('Invalid timestamp format in real-time update:', {{ barTime, lastBarTime, bar, lastBar }});
                        return;
                    }}
                    
                    // Check if this is a new bar or update to current bar
                    if (barTime === lastBarTime) {{
                        // Update current bar
                        lastBar.high = Math.max(lastBar.high, bar.close);
                        lastBar.low = Math.min(lastBar.low, bar.close);
                        lastBar.close = bar.close;
                        lastBar.volume = bar.volume;
                        
                        // Update chart
                        try {{
                            candlestickSeries.update({{
                                time: barTime,
                                open: lastBar.open,
                                high: lastBar.high,
                                low: lastBar.low,
                                close: lastBar.close,
                            }});
                            
                            volumeSeries.update({{
                                time: barTime,
                                value: bar.volume,
                                color: bar.close >= lastBar.open ? '#26a69a80' : '#ef535080',
                            }});
                        }} catch (updateError) {{
                            console.warn('Chart update error (same time):', updateError);
                        }}
                    }} else if (barTime > lastBarTime) {{
                        // New bar - ensure timestamp is normalized
                        const newBar = {{
                            ...bar,
                            time: barTime
                        }};
                        chartData.push(newBar);
                        
                        try {{
                            candlestickSeries.update({{
                                time: barTime,
                                open: bar.open,
                                high: bar.high,
                                low: bar.low,
                                close: bar.close,
                            }});
                            
                            volumeSeries.update({{
                                time: barTime,
                                value: bar.volume,
                                color: bar.close >= bar.open ? '#26a69a80' : '#ef535080',
                            }});
                        }} catch (updateError) {{
                            console.warn('Chart update error (new bar):', updateError);
                            // Remove the bar we just added if update failed
                            chartData.pop();
                        }}
                    }} else {{
                        // barTime < lastBarTime - this should not happen, but handle gracefully
                        console.warn(`Skipping real-time update: new bar time (${{barTime}}) is older than last bar time (${{lastBarTime}}). This may indicate clock skew or delayed data.`);
                        // Don't update the chart - TradingView doesn't allow updating older bars
                    }}
                    
                    updateStatus(`Real-time: ${{data.quote.last || data.quote.bid || 'N/A'}} | ${{new Date().toLocaleTimeString()}}`);
                }}
            }} catch (error) {{
                console.error('Real-time update error:', error);
                updateStatus('Real-time update failed: ' + error.message);
            }}
        }}
        
        // Toggle real-time updates
        function toggleRealtime() {{
            const btn = document.getElementById('realtimeBtn');
            if (!btn) return;
            
            if (realtimeActive) {{
                // Stop
                if (realtimeInterval) {{
                    clearInterval(realtimeInterval);
                    realtimeInterval = null;
                }}
                // Note: positionOrderRefreshInterval continues running even when real-time is stopped
                // This ensures lines update when positions/orders are closed
                realtimeActive = false;
                btn.textContent = 'Start Real-Time';
                btn.classList.remove('active');
                updateStatus('Real-time updates stopped');
            }} else {{
                // Start
                if (!serverPort) {{
                    showError('Real-time server not available. Start chart with --realtime flag.');
                    return;
                }}
                realtimeActive = true;
                btn.textContent = 'Stop Real-Time';
                btn.classList.add('active');
                updateRealtime(); // Immediate update
                
                // Calculate interval based on refresh rate (updates per second)
                const intervalMs = 1000 / realtimeRefreshRate;
                realtimeInterval = setInterval(updateRealtime, intervalMs);
                updateStatus(`Real-time updates started at ${{realtimeRefreshRate}}x/sec`);
            }}
        }}
        
        // Backtesting function
        function playBacktestBar() {{
            // Don't play if paused - keep all rendered candles visible
            if (backtestPaused) {{
                return;
            }}
            
            if (backtestIndex >= chartData.length) {{
                // Finished
                toggleBacktest();
                updateStatus('Backtest complete - ' + chartData.length + ' bars replayed');
                return;
            }}
            
            const bar = chartData[backtestIndex];
            
            try {{
                if (!candlestickSeries || !volumeSeries) {{
                    console.error('Chart series not initialized');
                    showError('Chart series not initialized');
                    return;
                }}
                
                // Prepare bar data
                const barData = {{
                    time: bar.time,
                    open: parseFloat(bar.open),
                    high: parseFloat(bar.high),
                    low: parseFloat(bar.low),
                    close: parseFloat(bar.close),
                }};
                
                const volumeData = {{
                    time: bar.time,
                    value: parseInt(bar.volume) || 0,
                    color: parseFloat(bar.close) >= parseFloat(bar.open) ? '#26a69a80' : '#ef535080',
                }};
                
                // Use update() to add/update bars
                candlestickSeries.update(barData);
                volumeSeries.update(volumeData);
                
                // Verify update worked
                const currentData = candlestickSeries.data();
                if (backtestIndex < 5) {{
                    console.log('Backtest bar', backtestIndex + 1, ':', barData);
                    console.log('Total bars in series:', currentData.length);
                }}
                
                // Don't auto-fit content - let user control zoom/pan manually
                // Removed fitContent() calls to allow manual zoom control
                
                const date = new Date(bar.time * 1000);
                updateStatus(`Backtest: ${{backtestIndex + 1}}/${{chartData.length}} | Speed: ${{backtestSpeed}}x | ${{date.toLocaleString()}}`);
            }} catch (error) {{
                console.error('Error playing backtest bar:', error);
                console.error('Bar data:', bar);
                showError('Error playing bar ' + (backtestIndex + 1) + ': ' + error.message);
            }}
            
            backtestIndex++;
        }}
        
        // Toggle backtesting
        function toggleBacktest() {{
            console.log('🔔 toggleBacktest() called');
            console.trace('Call stack:');
            
            // Don't allow backtest to start if we're in test mode
            if (testModeActive && !backtestActive) {{
                console.log('⚠️  Ignoring toggleBacktest() call - test mode is active');
                return;
            }}
            
            const btn = document.getElementById('backtestBtn');
            if (!btn) {{
                console.error('Backtest button not found');
                return;
            }}
            
            if (backtestActive) {{
                // Stop
                if (backtestInterval) {{
                    clearInterval(backtestInterval);
                    backtestInterval = null;
                }}
                backtestActive = false;
                backtestPaused = false; // Reset pause state when stopping
                btn.textContent = 'Start Backtest';
                btn.classList.remove('active');
                
                // Hide pause button
                const pauseBtn = document.getElementById('pauseBtn');
                if (pauseBtn) {{
                    pauseBtn.style.display = 'none';
                }}
                
                updateStatus('Backtest stopped at bar ' + backtestIndex + '/' + chartData.length);
            }} else {{
                // Start
                if (!chart || !candlestickSeries || !volumeSeries) {{
                    showError('Chart not initialized. Please wait...');
                    return;
                }}
                
                if (!chartData || chartData.length === 0) {{
                    showError('No chart data available for backtest');
                    return;
                }}
                
                backtestActive = true;
                backtestPaused = false; // Reset pause state when starting
                backtestIndex = 0;
                btn.textContent = 'Stop Backtest';
                btn.classList.add('active');
                
                // Show pause button
                const pauseBtn = document.getElementById('pauseBtn');
                if (pauseBtn) {{
                    pauseBtn.style.display = 'inline-block';
                    pauseBtn.textContent = 'Pause';
                    pauseBtn.style.background = '#666';
                }}
                
                // Clear existing data and start fresh
                try {{
                    console.log('Starting backtest with', chartData.length, 'bars');
                    console.log('Backtest interval:', backtestIntervalMs, 'ms');
                    console.log('First bar:', chartData[0]);
                    
                    // Reset index
                    backtestIndex = 0;
                    
                    // Clear series
                    candlestickSeries.setData([]);
                    volumeSeries.setData([]);
                    
                    // Small delay to ensure chart is ready, then start
                    setTimeout(() => {{
                        if (chartData.length > 0) {{
                            // Set first bar using setData() to initialize the series
                            const firstBar = chartData[0];
                            const firstBarData = {{
                                time: firstBar.time,
                                open: parseFloat(firstBar.open),
                                high: parseFloat(firstBar.high),
                                low: parseFloat(firstBar.low),
                                close: parseFloat(firstBar.close),
                            }};
                            
                            const firstVolumeData = {{
                                time: firstBar.time,
                                value: parseInt(firstBar.volume) || 0,
                                color: parseFloat(firstBar.close) >= parseFloat(firstBar.open) ? '#26a69a80' : '#ef535080',
                            }};
                            
                            console.log('Setting first bar:', firstBarData);
                            
                            try {{
                            candlestickSeries.setData([firstBarData]);
                            volumeSeries.setData([firstVolumeData]);
                            
                            console.log('First bar set, data count:', candlestickSeries.data().length);
                            
                            // Set visible range to show first ~100 bars (zoomed out for better overview)
                            const timeframeSeconds = getTimeframeSeconds(timeframe);
                            const visibleBars = 100; // Increased from 50 for better zoom out
                            const lastVisibleBar = chartData[Math.min(visibleBars - 1, chartData.length - 1)];
                            chart.timeScale().setVisibleRange({{
                                from: firstBar.time,
                                to: lastVisibleBar.time + (timeframeSeconds * 10) // Add some padding
                            }});
                            console.log('Set visible range to show first', visibleBars, 'bars (zoomed out)');
                                
                                // Verify data was set
                                const verifyData = candlestickSeries.data();
                                console.log('Verified data after setData:', verifyData.length, 'bars');
                                if (verifyData.length > 0) {{
                                    console.log('First bar in series:', verifyData[0]);
                                }}
                                
                                // Update index and status
                                backtestIndex = 1;
                                const date = new Date(firstBar.time * 1000);
                                const intervalMs = baseBacktestIntervalMs / backtestSpeed;
                                updateStatus(`Backtest: 1/${{chartData.length}} | Speed: ${{backtestSpeed}}x | ${{date.toLocaleString()}}`);
                                
                                // Start interval for remaining bars
                                if (chartData.length > 1) {{
                                    backtestInterval = setInterval(() => {{
                                        playBacktestBar();
                                    }}, intervalMs);
                                    console.log('Backtest interval started, next bar in', intervalMs, 'ms (speed:', backtestSpeed + 'x)');
                                }} else {{
                                    // Only one bar, already shown
                                    toggleBacktest();
                                    updateStatus('Backtest complete - 1 bar displayed');
                                }}
                                
                                updateStatus('Backtest started - ' + chartData.length + ' bars to replay');
                            }} catch (setError) {{
                                console.error('Error setting first bar:', setError);
                                showError('Error setting first bar: ' + setError.message);
                                backtestActive = false;
                                btn.textContent = 'Start Backtest';
                                btn.classList.remove('active');
                            }}
                        }} else {{
                            showError('No chart data available');
                        }}
                    }}, 100);
                }} catch (error) {{
                    showError('Error starting backtest: ' + error.message);
                    console.error('Backtest start error:', error);
                    console.error('Error stack:', error.stack);
                    backtestActive = false;
                    btn.textContent = 'Start Backtest';
                    btn.classList.remove('active');
                }}
            }}
        }}
        
        // Wait for DOM and library, ensure container is visible
        function tryInitChart() {{
            const container = document.getElementById('chart-container');
            if (container && (container.clientWidth > 0 || container.offsetWidth > 0)) {{
                initChart();
            }} else {{
                console.log('Container not ready, retrying...');
                setTimeout(tryInitChart, 100);
            }}
        }}
        
        if (document.readyState === 'loading') {{
            document.addEventListener('DOMContentLoaded', function() {{
                setTimeout(tryInitChart, 200);
            }});
        }} else {{
            setTimeout(tryInitChart, 200);
        }}
        
        window.addEventListener('load', function() {{
            if (!chart) {{
                setTimeout(tryInitChart, 200);
            }}
        }});
        
        // Refresh chart
        function refreshChart() {{
            updateStatus('Refresh not available. Re-run chart command to update.');
        }}
        
        // Exit test mode and allow backtest to run
        function exitTestMode() {{
            testModeActive = false;
            console.log('🚪 Test mode deactivated - backtest can now run');
            updateStatus('Test mode exited - you can now start backtest');
        }}
        
        // Update real-time refresh rate from dropdown
        function updateRefreshRate() {{
            const select = document.getElementById('refreshRateSelect');
            if (select) {{
                realtimeRefreshRate = parseInt(select.value);
                console.log('Real-time refresh rate updated to', realtimeRefreshRate, 'updates/sec');
                
                // If real-time is running, restart with new rate
                if (realtimeActive && realtimeInterval) {{
                    clearInterval(realtimeInterval);
                    const intervalMs = 1000 / realtimeRefreshRate;
                    realtimeInterval = setInterval(updateRealtime, intervalMs);
                    updateStatus(`Real-time refresh rate: ${{realtimeRefreshRate}}x/sec`);
                }}
            }}
        }}
        
        // Toggle pause/play for backtest
        function togglePause() {{
            const pauseBtn = document.getElementById('pauseBtn');
            if (!pauseBtn) {{
                console.error('Pause button not found');
                return;
            }}
            
            if (!backtestActive) {{
                console.log('Cannot pause - backtest is not running');
                return;
            }}
            
            if (backtestPaused) {{
                // Resume
                backtestPaused = false;
                pauseBtn.textContent = 'Pause';
                pauseBtn.style.background = '#666';
                updateStatus('Backtest resumed at bar ' + (backtestIndex + 1) + '/' + chartData.length);
                console.log('▶️  Backtest resumed at bar', backtestIndex + 1);
            }} else {{
                // Pause
                backtestPaused = true;
                pauseBtn.textContent = '▶ Play';
                pauseBtn.style.background = '#26a69a';
                updateStatus('Backtest paused at bar ' + backtestIndex + '/' + chartData.length + ' - All candles remain visible');
                console.log('⏸️  Backtest paused at bar', backtestIndex);
            }}
        }}
        
        // Update backtest speed from dropdown
        function updateBacktestSpeed() {{
            const select = document.getElementById('speedSelect');
            if (select) {{
                backtestSpeed = parseInt(select.value);
                console.log('Backtest speed updated to', backtestSpeed + 'x');
                
                // If backtest is running, restart with new speed
                if (backtestActive) {{
                    clearInterval(backtestInterval);
                    const intervalMs = baseBacktestIntervalMs / backtestSpeed;
                    backtestInterval = setInterval(() => {{
                        playBacktestBar();
                    }}, intervalMs);
                    console.log('Backtest interval updated to', intervalMs, 'ms');
                    updateStatus('Backtest speed: ' + backtestSpeed + 'x');
                }}
            }}
        }}
        
        // Test function: Load all bars at once to verify chart works
        function loadAllBars() {{
            if (!chart || !candlestickSeries || !volumeSeries) {{
                showError('Chart not initialized');
                return;
            }}
            
            // Set test mode flag to prevent backtest from interfering
            testModeActive = true;
            console.log('🧪 Test mode activated');
            
            try {{
                console.log('Loading all', chartData.length, 'bars at once (test)...');
                
                // Sort data by time to ensure chronological order
                const sortedChartData = [...chartData].sort((a, b) => a.time - b.time);
                const dedupMap = new Map();
                for (let i = 0; i < sortedChartData.length; i++) {{
                    const b = sortedChartData[i];
                    const t = Number(b.time);
                    if (!isFinite(t)) continue;
                    dedupMap.set(t, Object.assign({{}}, b, {{ time: t }}));
                }}
                const dedupedChartData = Array.from(dedupMap.keys()).sort((a, b) => a - b).map((k) => dedupMap.get(k));
                
                const candlestickData = dedupedChartData.map(bar => ({{
                    time: Number(bar.time), // Ensure it's a number (TradingView expects Unix timestamp in seconds)
                    open: parseFloat(bar.open),
                    high: parseFloat(bar.high),
                    low: parseFloat(bar.low),
                    close: parseFloat(bar.close),
                }}));
                
                const volumeData = dedupedChartData.map(bar => ({{
                    time: Number(bar.time), // Ensure it's a number
                    value: parseInt(bar.volume) || 0,
                    color: parseFloat(bar.close) >= parseFloat(bar.open) ? '#26a69a80' : '#ef535080',
                }}));
                
                // Verify time format - must be numbers, not strings
                const invalidTimes = candlestickData.filter(bar => typeof bar.time !== 'number' || isNaN(bar.time));
                if (invalidTimes.length > 0) {{
                    console.error('Invalid time format found:', invalidTimes.slice(0, 3));
                }}
                
                console.log('First 3 bars sample:', candlestickData.slice(0, 3));
                console.log('Last 3 bars sample:', candlestickData.slice(-3));
                console.log('Time range:', candlestickData[0].time, 'to', candlestickData[candlestickData.length - 1].time);
                console.log('All times are numbers:', candlestickData.every(bar => typeof bar.time === 'number' && !isNaN(bar.time)));
                
                // Log price range for debugging
                const minPrice = Math.min(...candlestickData.map(b => b.low));
                const maxPrice = Math.max(...candlestickData.map(b => b.high));
                console.log('Price range:', minPrice, 'to', maxPrice);
                
                candlestickSeries.setData(candlestickData);
                volumeSeries.setData(volumeData);
                applyTradeOverlays();
                
                console.log('Data set. Series data count:', candlestickSeries.data().length);
                
                // Force price axis to auto-scale
                candlestickSeries.priceScale().applyOptions({{
                    autoScale: true,
                }});
                volumeSeries.priceScale().applyOptions({{
                    autoScale: true,
                }});
                
                // Force resize and fit
                const container = document.getElementById('chart-container');
                if (container) {{
                    const width = container.clientWidth || container.offsetWidth || 1200;
                    chart.applyOptions({{ width: width, height: 600 }});
                    console.log('Chart resized to:', width, 'x 600');
                }}
                
                // Verify data before fitting
                const verifyCount = candlestickSeries.data().length;
                const firstBar = candlestickSeries.data()[0];
                const lastBar = candlestickSeries.data()[verifyCount - 1];
                console.log('All bars loaded. Series has', verifyCount, 'bars');
                console.log('First bar:', firstBar);
                console.log('Last bar:', lastBar);
                
                // Explicitly set visible range to show all data
                // The issue: fitContent() seems to fit to wrong range, so we MUST use setVisibleRange
                if (firstBar && lastBar) {{
                    const firstTime = Number(firstBar.time);
                    const lastTime = Number(lastBar.time);
                    
                    console.log('Attempting to set visible range from', firstTime, 'to', lastTime);
                    
                    // Use requestAnimationFrame to ensure chart has processed setData() first
                    requestAnimationFrame(() => {{
                        try {{
                            chart.timeScale().setVisibleRange({{
                                from: firstTime,
                                to: lastTime
                            }});
                            console.log('✅ setVisibleRange called');
                            
                            // Verify after a frame
                            requestAnimationFrame(() => {{
                                const range = chart.timeScale().getVisibleRange();
                                console.log('Visible range after setVisibleRange:', range);
                                if (range && (range.from > firstTime || range.to < lastTime)) {{
                                    console.error('❌ Range still wrong! Trying scrollToRealTime...');
                                    // Last resort: try scrolling to the first bar
                                    chart.timeScale().scrollToRealTime();
                                    // Then set range again
                                    setTimeout(() => {{
                                        chart.timeScale().setVisibleRange({{ from: firstTime, to: lastTime }});
                                    }}, 100);
                                }} else if (range) {{
                                    console.log('✅ Range is correct!');
                                    
                                    // Force chart to redraw by applying options
                                    chart.applyOptions({{
                                        timeScale: {{
                                            visible: true,
                                        }},
                                    }});
                                    
                                    // Force price scale to autoscale
                                    candlestickSeries.priceScale().applyOptions({{
                                        autoScale: true,
                                    }});
                                    
                                    console.log('Forced chart redraw and autoscale');
                                }}
                            }});
                        }} catch (e) {{
                            console.error('Error in setVisibleRange:', e);
                        }}
                    }});
                }}
                
                updateStatus('All ' + verifyCount + ' bars loaded (test mode)');
                console.log('✅ All bars loaded successfully in test mode');
            }} catch (error) {{
                console.error('Error loading all bars:', error);
                console.error('Error stack:', error.stack);
                showError('Error: ' + error.message);
            }} finally {{
                // Keep test mode active so backtest doesn't interfere
                console.log('🧪 Test mode remains active (bars should be visible)');
            }}
        }}
        
        // Place order from chart
        async function placeOrder(side) {{
            if (!serverPort) {{
                showError('Trading server not available');
                return;
            }}
            
            try {{
                const orderType = document.getElementById('orderTypeSelect').value;
                const quantity = parseInt(document.getElementById('quantityInput').value) || 1;
                const limitPrice = parseFloat(document.getElementById('limitPriceInput').value) || null;
                const stopPrice = parseFloat(document.getElementById('stopPriceInput').value) || null;
                const enableBracket = document.getElementById('enableBracketCheck').checked;
                const stopLossPrice = parseFloat(document.getElementById('stopLossPriceInput').value) || null;
                const takeProfitPrice = parseFloat(document.getElementById('takeProfitPriceInput').value) || null;
                
                // Validate required fields
                if (orderType === 'limit' && !limitPrice) {{
                    showError('Limit price required for limit orders');
                    return;
                }}
                if (orderType === 'stop' && !stopPrice) {{
                    showError('Stop price required for stop orders');
                    return;
                }}
                // Validate bracket prices - check both enableBracket checkbox and orderType === 'bracket'
                if ((enableBracket || orderType === 'bracket') && (!stopLossPrice || !takeProfitPrice)) {{
                    showError('Stop loss and take profit prices required for bracket orders');
                    return;
                }}
                
                const symbolSelect = document.getElementById('symbolSelect');
                const orderSymbol = symbolSelect?.value || symbol;
                
                if (!orderSymbol || orderSymbol.trim() === '' || orderSymbol === 'UNKNOWN') {{
                    showError('Please select a symbol first');
                    return;
                }}
                
                updateStatus(`Placing ${{side}} ${{orderType}} order...`);
                
                const response = await fetch(`http://127.0.0.1:${{serverPort}}/api/chart/order`, {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{
                        symbol: orderSymbol,
                        side: side,
                        quantity: quantity,
                        order_type: orderType,
                        limit_price: limitPrice,
                        stop_price: stopPrice,
                        stop_loss_price: stopLossPrice,
                        take_profit_price: takeProfitPrice,
                        enable_bracket: enableBracket
                    }})
                }});
                
                const result = await response.json();
                
                if (result.error) {{
                    showError(`Order failed: ${{result.error}}`);
                }} else {{
                    const orderId = result.get?.('order_id') || result.order_id || result.id || 'N/A';
                    const orderTypeDisplay = orderType === 'bracket' ? 'Bracket' : orderType.charAt(0).toUpperCase() + orderType.slice(1);
                    updateStatus(`✅ Order placed successfully!`);
                    showToast('Order Placed', `${{side}} ${{orderTypeDisplay}} order placed successfully${{orderId !== 'N/A' ? ' (ID: ' + orderId + ')' : ''}}`, 'success');
                    // Update position lines and order lines (force update, ignore throttle)
                    lastPositionUpdate = 0; // Reset throttle to force update
                    lastOrderUpdate = 0; // Reset throttle to force update
                    lastStrategyLinesUpdate = 0;
                    await updatePositionLines();
                    await updateOrderLines();
                    await updateStrategyBreakoutLines();
                }}
            }} catch (error) {{
                showError(`Order error: ${{error.message}}`);
                console.error('Order placement error:', error);
            }}
        }}
        
        // Pre-fill prices with last traded price
        async function prefillPrices() {{
            if (!serverPort) return;
            
            try {{
                const currentSymbol = document.getElementById('symbolSelect')?.value || symbol;
                const currentTf = document.getElementById('timeframeSelect')?.value || timeframe;
                const response = await fetch(`http://127.0.0.1:${{serverPort}}/api/chart/quote?symbol=${{encodeURIComponent(currentSymbol)}}&timeframe=${{encodeURIComponent(currentTf)}}`);
                if (!response.ok) return;
                
                const data = await response.json();
                const quote = data.quote;
                if (!quote) return;
                
                const lastPrice = parseFloat(quote.last || quote.lastPrice || quote.bid || quote.ask || 0);
                if (!lastPrice || lastPrice === 0) return;
                
                // Pre-fill all price inputs with last traded price
                const limitInput = document.getElementById('limitPriceInput');
                const stopInput = document.getElementById('stopPriceInput');
                const stopLossInput = document.getElementById('stopLossPriceInput');
                const takeProfitInput = document.getElementById('takeProfitPriceInput');
                
                // Only pre-fill if field is visible and empty
                if (limitInput && limitInput.style.display !== 'none' && !limitInput.value) {{
                    limitInput.value = lastPrice.toFixed(2);
                }}
                if (stopInput && stopInput.style.display !== 'none' && !stopInput.value) {{
                    stopInput.value = lastPrice.toFixed(2);
                }}
                if (stopLossInput && stopLossInput.style.display !== 'none' && !stopLossInput.value) {{
                    stopLossInput.value = lastPrice.toFixed(2);
                }}
                if (takeProfitInput && takeProfitInput.style.display !== 'none' && !takeProfitInput.value) {{
                    takeProfitInput.value = lastPrice.toFixed(2);
                }}
            }} catch (error) {{
                console.debug('Failed to pre-fill prices:', error);
            }}
        }}
        
        // Update order type UI visibility
        function updateOrderType() {{
            const orderType = document.getElementById('orderTypeSelect').value;
            const limitInput = document.getElementById('limitPriceInput');
            const stopInput = document.getElementById('stopPriceInput');
            const bracketCheck = document.getElementById('enableBracketCheck');
            
            if (orderType === 'limit') {{
                limitInput.style.display = 'inline-block';
                stopInput.style.display = 'none';
                bracketCheck.disabled = false;
            }} else if (orderType === 'stop') {{
                limitInput.style.display = 'none';
                stopInput.style.display = 'inline-block';
                bracketCheck.disabled = false;
            }} else if (orderType === 'bracket') {{
                limitInput.style.display = 'none';
                stopInput.style.display = 'none';
                bracketCheck.checked = true;
                bracketCheck.disabled = true; // Bracket type always has brackets enabled
            }} else {{
                limitInput.style.display = 'none';
                stopInput.style.display = 'none';
                bracketCheck.disabled = false;
            }}
            updateBracket(); // Also update bracket visibility
            prefillPrices(); // Pre-fill prices when order type changes
        }}
        
        // Update bracket UI visibility
        function updateBracket() {{
            const enableBracket = document.getElementById('enableBracketCheck').checked;
            const slInput = document.getElementById('stopLossPriceInput');
            const tpInput = document.getElementById('takeProfitPriceInput');
            
            if (enableBracket) {{
                slInput.style.display = 'inline-block';
                tpInput.style.display = 'inline-block';
            }} else {{
                slInput.style.display = 'none';
                tpInput.style.display = 'none';
            }}
            prefillPrices(); // Pre-fill prices when bracket visibility changes
        }}
        
        // Load available contracts and populate symbol dropdown
        async function loadContracts() {{
            if (!serverPort) {{
                console.log('No server port, skipping contract load');
                return;
            }}
            
            try {{
                console.log('Loading contracts from server...');
                const response = await fetch(`http://127.0.0.1:${{serverPort}}/api/chart/contracts`);
                if (!response.ok) {{
                    console.error('Failed to fetch contracts:', response.status);
                    return;
                }}
                const data = await response.json();
                console.log('Contracts response:', data);
                const symbols = data.symbols || [];
                
                if (symbols.length === 0) {{
                    console.warn('No symbols returned from contracts endpoint');
                    return;
                }}
                
                const select = document.getElementById('symbolSelect');
                if (!select) {{
                    console.error('Symbol select element not found');
                    return;
                }}
                
                const currentValue = select.value || '{symbol}';
                select.innerHTML = '';
                
                symbols.forEach(sym => {{
                    const option = document.createElement('option');
                    option.value = sym;
                    option.textContent = sym;
                    if (sym === currentValue || (currentValue === '{symbol}' && sym === '{symbol}')) {{
                        option.selected = true;
                    }}
                    select.appendChild(option);
                }});
                
                // If no symbol was selected and we have symbols, select the first one or the default
                if (select.selectedIndex === -1 && symbols.length > 0) {{
                    const defaultSymbol = '{symbol}';
                    const foundDefault = symbols.find(s => s === defaultSymbol);
                    if (foundDefault) {{
                        select.value = foundDefault;
                    }} else {{
                        select.selectedIndex = 0;
                    }}
                }}
                
                console.log('Contracts loaded:', symbols.length, 'symbols');
                // Pre-fill prices after contracts are loaded
                prefillPrices();
            }} catch (error) {{
                console.error('Error loading contracts:', error);
                updateStatus('Failed to load contracts: ' + error.message);
            }}
        }}
        
        // Pre-fill prices when symbol changes
        if (document.readyState === 'loading') {{
            document.addEventListener('DOMContentLoaded', function() {{
                const symbolSelect = document.getElementById('symbolSelect');
                if (symbolSelect) {{
                    symbolSelect.addEventListener('change', prefillPrices);
                }}
                // Initial pre-fill after a short delay
                setTimeout(prefillPrices, 1000);
            }});
        }} else {{
            // DOM already loaded
            const symbolSelect = document.getElementById('symbolSelect');
            if (symbolSelect) {{
                symbolSelect.addEventListener('change', prefillPrices);
            }}
            setTimeout(prefillPrices, 1000);
        }}
        
        // Reload chart data for new symbol/timeframe
        async function reloadChartData() {{
            if (!serverPort || !chart || !candlestickSeries) return;
            
            const symbolSelect = document.getElementById('symbolSelect');
            const newSymbol = symbolSelect?.value || '{symbol}';
            const newTimeframe = document.getElementById('timeframeSelect').value || '{timeframe}';
            
            if (!newSymbol || newSymbol.trim() === '') {{
                showError('Please select a symbol first');
                return;
            }}
            
            updateStatus(`Loading ${{newSymbol}} ${{newTimeframe}}...`);
            
            try {{
                const response = await fetch(`http://127.0.0.1:${{serverPort}}/api/chart/reload?symbol=${{encodeURIComponent(newSymbol)}}&timeframe=${{encodeURIComponent(newTimeframe)}}&limit=3500&source=auto`);
                const data = await response.json();
                
                if (data.error) {{
                    showError(`Failed to reload: ${{data.error}}`);
                    return;
                }}
                
                if (!data.bars || data.bars.length === 0) {{
                    showError('No data available for this symbol/timeframe');
                    return;
                }}
                
                const rawFull = data.bars.map(bar => ({{
                    time: Number(bar.time),
                    open: bar.open,
                    high: bar.high,
                    low: bar.low,
                    close: bar.close,
                    volume: bar.volume,
                }}));
                const byT = new Map();
                for (let i = 0; i < rawFull.length; i++) {{
                    const b = rawFull[i];
                    if (!isFinite(b.time)) continue;
                    byT.set(b.time, b);
                }}
                const times = Array.from(byT.keys()).sort((a, b) => a - b);
                const chartBars = times.map((k) => {{
                    const b = byT.get(k);
                    return {{
                        time: k,
                        open: b.open,
                        high: b.high,
                        low: b.low,
                        close: b.close,
                    }};
                }});
                
                candlestickSeries.setData(chartBars);
                
                // Update volume if available
                if (volumeSeries && data.bars[0].volume !== undefined) {{
                    const volumeBars = times.map((k) => {{
                        const b = byT.get(k);
                        return {{
                            time: k,
                            value: parseInt(b.volume, 10) || 0,
                            color: parseFloat(b.close) >= parseFloat(b.open) ? '#26a69a80' : '#ef535080'
                        }};
                    }});
                    volumeSeries.setData(volumeBars);
                }}
                
                // Update chart title
                document.querySelector('.header h1').textContent = `${{newSymbol}} ${{newTimeframe}} Chart`;
                
                // Fit content to show all bars
                chart.timeScale().fitContent();
                
                // Update position and order lines after chart is initialized
                setTimeout(async () => {{
                    console.log('Initializing position/order lines after chart load...');
                    lastPositionUpdate = 0; // Reset throttle to force update
                    lastOrderUpdate = 0; // Reset throttle to force update
                    lastStrategyLinesUpdate = 0;
                    await updatePositionLines();
                    await updateOrderLines();
                    await updateStrategyBreakoutLines();
                    
                    // Note: Position/order lines are event-based only (refresh on order placement/fill)
                }}, 1000);
                
                updateStatus(`✅ Chart updated: ${{data.bars.length}} bars`);
            }} catch (error) {{
                showError(`Error reloading chart: ${{error.message}}`);
                console.error('Reload error:', error);
            }}
        }}
        
        // Update symbol (reload chart data)
        function updateSymbol() {{
            reloadChartData();
        }}
        
        // Update timeframe (reload chart data)
        function updateTimeframe() {{
            reloadChartData();
        }}
        
        // Update position lines on chart (using price lines like TradingChart.tsx)
        async function updatePositionLines() {{
            if (!serverPort || !chart || !candlestickSeries) {{
                console.log('updatePositionLines: Missing requirements', {{
                    serverPort: !!serverPort,
                    chart: !!chart,
                    candlestickSeries: !!candlestickSeries
                }});
                return;
            }}
            
            // Throttle updates to prevent rate limiting
            const now = Date.now();
            if (now - lastPositionUpdate < POSITION_UPDATE_INTERVAL) {{
                console.debug('updatePositionLines: Throttled');
                return; // Skip if called too soon
            }}
            lastPositionUpdate = now;
            
            try {{
                // Remove existing position price lines
                positionPriceLines.forEach(line => {{
                    try {{
                        candlestickSeries.removePriceLine(line);
                    }} catch (e) {{
                        console.debug('Error removing position price line:', e);
                    }}
                }});
                positionPriceLines = [];
                
                console.log('Fetching positions from server...');
                const response = await fetch(`http://127.0.0.1:${{serverPort}}/api/chart/positions`);
                if (!response.ok) {{
                    console.error('Failed to fetch positions:', response.status, response.statusText);
                    return;
                }}
                const data = await response.json();
                const positions = data.positions || [];
                console.log('📋 Raw positions from API:', positions.length, positions);
                
                // Find position for current symbol (exact match like TradingChart.tsx)
                const currentSymbol = document.getElementById('symbolSelect')?.value || symbol;
                console.log('🔍 Looking for position with symbol:', currentSymbol);
                console.log('📊 Available positions:', positions.map(p => ({{
                    id: p.id,
                    symbol: p.symbol,
                    side: p.side,
                    quantity: p.quantity,
                    entry_price: p.entry_price,
                    contractId: p.contractId
                }})));
                
                // Simple exact match like TradingChart.tsx: pos.symbol === symbol
                const position = positions.find(p => {{
                    const posSymbol = p.symbol;
                    const matchesSymbol = posSymbol && posSymbol.toUpperCase() === currentSymbol.toUpperCase();
                    const hasQuantity = (p.quantity || p.size || 0) !== 0;
                    
                    if (matchesSymbol && hasQuantity) {{
                        console.log('Found matching position:', p);
                        return true;
                    }}
                    return false;
                }});
                
                if (!position) {{
                    console.log('No position found for symbol:', currentSymbol, 'Available positions:', positions.map(p => ({{
                        symbol: p.symbol,
                        contractId: p.contractId,
                        contract_id: p.contract_id,
                        quantity: p.quantity,
                        size: p.size
                    }})));
                    return;
                }}
                
                console.log('Processing position:', position);
                
                // Extract values exactly like TradingChart.tsx
                const entryPrice = Number(position.entry_price || position.entryPrice || 0);
                if (!entryPrice || !isFinite(entryPrice)) {{
                    console.warn('Entry price is invalid:', entryPrice, 'Position:', position);
                    return;
                }}
                
                const isLong = position.side === 'LONG';
                const quantity = Number(position.quantity ?? position.size ?? 0);
                
                console.log('Position values:', {{
                    entryPrice,
                    quantity,
                    isLong,
                    side: position.side
                }});
                
                // Extract stop loss and take profit if available
                const stopLoss = position.stop_loss || position.stopLoss;
                const takeProfit = position.take_profit || position.takeProfit;
                
                // Add entry price line (exactly like TradingChart.tsx)
                try {{
                    console.log('Creating entry price line at:', entryPrice);
                    const entryLine = candlestickSeries.createPriceLine({{
                        price: entryPrice,
                        color: isLong ? '#26A69A' : '#EF5350',
                        lineWidth: 2,
                        lineStyle: LightweightCharts.LineStyle.Solid,
                        axisLabelVisible: true,
                        title: `${{position.side}} ${{quantity}}@${{entryPrice.toFixed(2)}}`
                    }});
                    positionPriceLines.push(entryLine);
                    console.log('✅ Entry price line created successfully');
                }} catch (error) {{
                    console.error('❌ Error creating entry price line:', error);
                }}
                
                // Add stop loss price line if available
                if (stopLoss && Number(stopLoss) > 0) {{
                    try {{
                        const stopLine = candlestickSeries.createPriceLine({{
                            price: Number(stopLoss),
                            color: '#EF5350',
                            lineWidth: 2,
                            lineStyle: LightweightCharts.LineStyle.Dashed,
                            axisLabelVisible: true,
                            title: `Stop Loss: ${{Number(stopLoss).toFixed(2)}}`
                        }});
                        positionPriceLines.push(stopLine);
                        console.log('✅ Stop loss line created at:', stopLoss);
                    }} catch (error) {{
                        console.error('❌ Error creating stop loss price line:', error);
                    }}
                }}
                
                // Add take profit price line if available
                if (takeProfit && Number(takeProfit) > 0) {{
                    try {{
                        const tpLine = candlestickSeries.createPriceLine({{
                            price: Number(takeProfit),
                            color: '#26A69A',
                            lineWidth: 2,
                            lineStyle: LightweightCharts.LineStyle.Dashed,
                            axisLabelVisible: true,
                            title: `Take Profit: ${{Number(takeProfit).toFixed(2)}}`
                        }});
                        positionPriceLines.push(tpLine);
                        console.log('✅ Take profit line created at:', takeProfit);
                    }} catch (error) {{
                        console.error('❌ Error creating take profit price line:', error);
                    }}
                }}
            }} catch (error) {{
                console.error('Error updating position lines:', error);
            }}
        }}
        
        // Update order lines on chart
        async function updateOrderLines() {{
            if (!serverPort || !chart || !candlestickSeries) return;
            
            // Throttle updates to prevent rate limiting
            const now = Date.now();
            if (now - lastOrderUpdate < ORDER_UPDATE_INTERVAL) {{
                return; // Skip if called too soon
            }}
            lastOrderUpdate = now;
            
            try {{
                // Remove existing order price lines
                orderPriceLines.forEach(line => {{
                    try {{
                        candlestickSeries.removePriceLine(line);
                    }} catch (e) {{
                        console.debug('Error removing order price line:', e);
                    }}
                }});
                orderPriceLines = [];
                
                // Fetch open orders
                const response = await fetch(`http://127.0.0.1:${{serverPort}}/api/chart/orders`);
                if (!response.ok) {{
                    console.debug('Failed to fetch orders:', response.status);
                    return;
                }}
                
                const data = await response.json();
                const orders = data.orders || [];
                
                console.log('📋 Raw orders from API:', orders.length, orders);
                
                // Find orders for current symbol (exactly like TradingChart.tsx)
                const currentSymbol = document.getElementById('symbolSelect')?.value || symbol;
                console.log('🔍 Filtering orders for symbol:', currentSymbol);
                
                const relevantOrders = orders.filter(o => {{
                    // Extract symbol from multiple possible fields
                    const orderSymbol = o.symbol || o.contractId?.split('.')?.[2] || o.symbolId?.split('.')?.[2] || '';
                    const matchesSymbol = orderSymbol && orderSymbol.toUpperCase() === currentSymbol.toUpperCase();
                    
                    // Accept PENDING, OPEN, or status 1 (all mean open/pending)
                    // Also accept numeric status 1 or string '1'
                    const status = o.status;
                    const isPending = status === 'PENDING' || 
                                    status === 'OPEN' || 
                                    status === 'Open' || 
                                    status === 1 || 
                                    status === '1' ||
                                    String(status).toUpperCase() === 'OPEN' ||
                                    String(status).toUpperCase() === 'PENDING';
                    
                    // Check for any price field (price, stop_price, limitPrice, stopPrice)
                    // Also check for null/undefined explicitly
                    const price = o.price;
                    const stop_price = o.stop_price;
                    const limitPrice = o.limitPrice || o.limit_price;
                    const stopPrice = o.stopPrice;
                    const hasPrice = (price != null && price !== 0 && price !== '0') || 
                                   (stop_price != null && stop_price !== 0 && stop_price !== '0') || 
                                   (limitPrice != null && limitPrice !== 0 && limitPrice !== '0') || 
                                   (stopPrice != null && stopPrice !== 0 && stopPrice !== '0');
                    
                    const shouldInclude = matchesSymbol && isPending && hasPrice;
                    
                    if (!shouldInclude) {{
                        console.warn('❌ Filtered out order:', {{
                            id: o.id,
                            symbol: orderSymbol,
                            currentSymbol: currentSymbol,
                            matchesSymbol,
                            status: status,
                            isPending,
                            price: price,
                            stop_price: stop_price,
                            limitPrice: limitPrice,
                            stopPrice: stopPrice,
                            hasPrice,
                            fullOrder: o
                        }});
                    }} else {{
                        console.log('✅ Including order:', {{
                            id: o.id,
                            symbol: orderSymbol,
                            side: o.side,
                            type: o.type,
                            price: price || stop_price || limitPrice || stopPrice
                        }});
                    }}
                    return shouldInclude;
                }});
                
                console.log('📊 Relevant orders for', currentSymbol, ':', relevantOrders.length, 'out of', orders.length);
                console.log('📋 Relevant orders details:', relevantOrders.map(o => ({{
                    id: o.id,
                    symbol: o.symbol,
                    side: o.side,
                    type: o.type,
                    status: o.status,
                    price: o.price,
                    stop_price: o.stop_price,
                    limitPrice: o.limitPrice,
                    stopPrice: o.stopPrice
                }})));
                
                // Add price lines for each order (exactly like TradingChart.tsx)
                relevantOrders.forEach(order => {{
                    // Use stopPrice/stop_price for STOP orders, price/limitPrice for LIMIT orders
                    const orderTypeNum = order.type;
                    let orderPrice = null;
                    
                    console.log('🔍 Processing order:', {{
                        id: order.id,
                        type: orderTypeNum,
                        price: order.price,
                        stop_price: order.stop_price,
                        limitPrice: order.limitPrice,
                        stopPrice: order.stopPrice
                    }});
                    
                    if (orderTypeNum === 4 || orderTypeNum === 'STOP' || order.type === 'STOP') {{
                        // STOP order - use stopPrice/stop_price first
                        orderPrice = order.stopPrice || order.stop_price || order.price || order.limitPrice || order.limit_price;
                    }} else {{
                        // LIMIT or other order - use price/limitPrice first
                        orderPrice = order.price || order.limitPrice || order.limit_price || order.stopPrice || order.stop_price;
                    }}
                    
                    // Convert to number and validate
                    const priceNum = Number(orderPrice);
                    if (!orderPrice || priceNum === 0 || !isFinite(priceNum) || isNaN(priceNum)) {{
                        console.warn('❌ Skipping order with no valid price:', {{
                            id: order.id,
                            orderPrice: orderPrice,
                            priceNum: priceNum,
                            order: order
                        }});
                        return;
                    }}
                    
                    console.log('✅ Order has valid price:', {{
                        id: order.id,
                        orderPrice: priceNum,
                        type: orderTypeNum
                    }});
                    
                    const isLongOrder = order.side === 'BUY';
                    // Convert numeric order type to string (1=LIMIT, 4=STOP, etc.)
                    let orderType = order.type;
                    if (typeof orderType === 'number') {{
                        orderType = orderType == 1 ? 'LIMIT' : orderType == 4 ? 'STOP' : 'ORDER';
                    }} else {{
                        orderType = orderType || 'LIMIT';
                    }}
                    const isStopOrder = orderType === 'STOP' || order.stop_price || order.stopPrice;
                    
                    try {{
                        // Use the validated numeric price
                        const finalPrice = priceNum;
                        console.log('🎨 Creating order price line at:', finalPrice, 'Type:', orderType);
                        
                        // Ensure quantity is a number and format label correctly
                        const qty = Number(order.quantity || order.size || 1);
                        // orderType is already converted to string above, so just use it directly
                        const label = `${{order.side}} ${{qty}} ${{orderType}}`;
                        console.log('📝 Order label:', label, 'qty:', qty, 'orderType:', orderType, 'price:', finalPrice);
                        
                        const priceLine = candlestickSeries.createPriceLine({{
                            price: finalPrice,
                            color: isLongOrder ? '#10B981' : '#F59E0B',
                            lineWidth: 2,
                            lineStyle: isStopOrder ? LightweightCharts.LineStyle.Dotted : LightweightCharts.LineStyle.Dashed,
                            axisLabelVisible: true,
                            title: label
                        }});
                        orderPriceLines.push(priceLine);
                        console.log('✅ Order price line created successfully:', {{
                            id: order.id,
                            price: finalPrice,
                            label: label,
                            side: order.side,
                            type: orderType
                        }});
                    }} catch (error) {{
                        console.error('❌ Error creating order price line:', error, 'Order:', order);
                    }}
                }});
            }} catch (error) {{
                console.error('Error updating order lines:', error);
            }}
        }}
        
        async function updateStrategyBreakoutLines() {{
            if (!serverPort || !chart || !candlestickSeries) {{
                return;
            }}
            const nowMs = Date.now();
            if (nowMs - lastStrategyLinesUpdate < STRATEGY_LINES_INTERVAL) {{
                return;
            }}
            lastStrategyLinesUpdate = nowMs;
            try {{
                strategyPriceLines.forEach(line => {{
                    try {{
                        candlestickSeries.removePriceLine(line);
                    }} catch (e) {{
                        console.debug('Error removing strategy price line:', e);
                    }}
                }});
                strategyPriceLines = [];
                const currentSymbol = (document.getElementById('symbolSelect')?.value || symbol || '').toUpperCase();
                const stResp = await fetch(`http://127.0.0.1:${{serverPort}}/api/chart/strategy/status`);
                if (!stResp.ok) return;
                const st = await stResp.json();
                const activeNames = st.active || [];
                if (!Array.isArray(activeNames) || !activeNames.includes('overnight_range')) {{
                    return;
                }}
                const detResp = await fetch(`http://127.0.0.1:${{serverPort}}/api/chart/strategy/details/overnight_range`);
                if (!detResp.ok) return;
                const d = await detResp.json();
                if (d.error || !d.breakout_levels) return;
                const levels = d.breakout_levels[currentSymbol];
                const rg = (d.ranges && d.ranges[currentSymbol]) ? d.ranges[currentSymbol] : null;
                function addLine(price, color, title, styleName) {{
                    const p = Number(price);
                    if (!isFinite(p) || p <= 0) return;
                    let ls = LightweightCharts.LineStyle.Solid;
                    if (styleName === 'dotted') ls = LightweightCharts.LineStyle.Dotted;
                    else if (styleName === 'dashed') ls = LightweightCharts.LineStyle.Dashed;
                    const pl = candlestickSeries.createPriceLine({{
                        price: p,
                        color: color,
                        lineWidth: 2,
                        lineStyle: ls,
                        axisLabelVisible: true,
                        title: title
                    }});
                    strategyPriceLines.push(pl);
                }}
                if (rg) {{
                    if (rg.high != null) addLine(rg.high, '#AB47BC', 'OR High', 'solid');
                    if (rg.low != null) addLine(rg.low, '#AB47BC', 'OR Low', 'solid');
                }}
                if (levels) {{
                    if (levels.long_entry != null) addLine(levels.long_entry, '#22C55E', 'OR Long', 'solid');
                    if (levels.long_stop != null) addLine(levels.long_stop, '#EF4444', 'OR L SL', 'dotted');
                    if (levels.long_tp != null) addLine(levels.long_tp, '#86EFAC', 'OR L TP', 'dashed');
                    if (levels.short_entry != null) addLine(levels.short_entry, '#F59E0B', 'OR Short', 'solid');
                    if (levels.short_stop != null) addLine(levels.short_stop, '#F87171', 'OR S SL', 'dotted');
                    if (levels.short_tp != null) addLine(levels.short_tp, '#FBBF24', 'OR S TP', 'dashed');
                }}
            }} catch (err) {{
                console.debug('updateStrategyBreakoutLines:', err);
            }}
        }}
        
        // Export data to CSV
        function exportData() {{
            try {{
                const csv = [
                    ['Time', 'Open', 'High', 'Low', 'Close', 'Volume'].join(','),
                    ...chartData.map(bar => {{
                        const date = new Date(bar.time * 1000);
                        return [
                            date.toISOString(),
                            bar.open,
                            bar.high,
                            bar.low,
                            bar.close,
                            bar.volume
                        ].join(',');
                    }})
                ].join('\\n');
                
                const blob = new Blob([csv], {{ type: 'text/csv' }});
                const url = window.URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = '{symbol}_{timeframe}_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv';
                a.click();
                window.URL.revokeObjectURL(url);
                updateStatus('CSV exported');
            }} catch (error) {{
                showError('Export error: ' + error.message);
            }}
        }}
        
        // Handle window resize
        window.addEventListener('resize', () => {{
            if (chart && chartContainer) {{
                chart.applyOptions({{ width: chartContainer.clientWidth }});
            }} else if (chart) {{
                // Fallback: get container again if not in scope
                const container = document.getElementById('chart-container');
                if (container) {{
                    chart.applyOptions({{ width: container.clientWidth }});
                }}
            }}
        }});
    </script>
</body>
</html>"""
    
    # Determine output path
    if output_path is None:
        charts_dir = Path("charts")
        charts_dir.mkdir(exist_ok=True)
        output_path = str(charts_dir / f"{symbol}_{timeframe}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html")
    
    # Write HTML file
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html_content)
    
    return output_path


async def open_chart_html_async(
    trading_bot, 
    symbol: str = 'MNQ', 
    timeframe: str = '5m', 
    limit: int = 100,
    realtime: bool = False,
    backtest: bool = False,
    backtest_start: Optional[str] = None,
    backtest_end: Optional[str] = None,
    backtest_speed: float = 1.0
):
    """
    Generate and open TradingView Lightweight Charts HTML file (async version).
    
    Args:
        trading_bot: TopStepXTradingBot instance
        symbol: Trading symbol
        timeframe: Timeframe
        limit: Number of bars
        realtime: Enable real-time updates (polls for new quotes)
        backtest: Enable backtesting mode (replays historical bars)
        backtest_start: Start date for backtesting (YYYY-MM-DD or YYYY-MM-DD HH:MM:SS)
        backtest_end: End date for backtesting (YYYY-MM-DD or YYYY-MM-DD HH:MM:SS)
        backtest_speed: Playback speed multiplier (1.0 = real-time, 2.0 = 2x speed, etc.)
    
    Returns:
        Path to generated HTML file
    """
    # For backtesting, fetch all bars in the date range
    if backtest and backtest_start and backtest_end:
        from datetime import datetime
        try:
            start_dt = datetime.fromisoformat(backtest_start.replace('Z', '+00:00'))
            end_dt = datetime.fromisoformat(backtest_end.replace('Z', '+00:00'))
        except (ValueError, TypeError):
            try:
                start_dt = datetime.strptime(backtest_start, '%Y-%m-%d')
                end_dt = datetime.strptime(backtest_end, '%Y-%m-%d')
            except (ValueError, TypeError):
                raise ValueError(
                    "Invalid date format. Use YYYY-MM-DD or YYYY-MM-DD HH:MM:SS"
                ) from None
        
        bars = await trading_bot.get_historical_data(
            symbol=symbol,
            timeframe=timeframe,
            start_time=start_dt,
            end_time=end_dt
        )
    else:
        # Fetch historical data (already in async context)
        bars = await trading_bot.get_historical_data(
            symbol=symbol,
            timeframe=timeframe,
            limit=limit
        )
    
    if not bars:
        raise ValueError(f"No data available for {symbol} {timeframe}")
    
    # Convert to dict format if needed
    if bars and hasattr(bars[0], 'timestamp'):
        from datetime import timezone as tz
        bars = [
            {
                # Convert timestamp to Unix seconds directly to avoid timezone issues
                # If timestamp is naive, assume UTC
                'timestamp': int(bar.timestamp.replace(tzinfo=tz.utc).timestamp()) if bar.timestamp.tzinfo is None else int(bar.timestamp.timestamp()),
                'open': bar.open,
                'high': bar.high,
                'low': bar.low,
                'close': bar.close,
                'volume': bar.volume,
            }
            for bar in bars
        ]
    
    if not bars:
        raise ValueError(f"No data available for {symbol} {timeframe}")
    
    # Get trading bot reference for real-time updates (store in a way the HTML can access)
    # We'll create a simple HTTP server endpoint if realtime mode is enabled
    server_port = None
    if realtime:
        # Start a simple HTTP server for real-time updates
        server_port = await _start_chart_server(trading_bot, symbol)
    
    # Generate HTML
    html_path = generate_chart_html(
        symbol, 
        timeframe, 
        bars, 
        realtime=realtime,
        backtest=backtest,
        backtest_speed=backtest_speed,
        server_port=server_port
    )
    
    # Open in browser
    import webbrowser
    import os
    file_url = f"file://{os.path.abspath(html_path)}"
    webbrowser.open(file_url)
    
    return html_path


def open_chart_html(trading_bot, symbol: str = 'MNQ', timeframe: str = '5m', limit: int = 100):
    """
    Synchronous wrapper for open_chart_html_async.
    For use in non-async contexts (creates new event loop).
    
    Args:
        trading_bot: TopStepXTradingBot instance
        symbol: Trading symbol
        timeframe: Timeframe
        limit: Number of bars
    
    Returns:
        Path to generated HTML file
    """
    import asyncio
    
    # Check if we're in an async context
    try:
        loop = asyncio.get_running_loop()
        # If we get here, we're in an async context - use create_task
        # But this is a sync function, so we need to handle it differently
        raise RuntimeError("Use open_chart_html_async() in async contexts")
    except RuntimeError:
        # No running loop - create new one
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(
                open_chart_html_async(trading_bot, symbol, timeframe, limit)
            )
        finally:
            loop.close()
