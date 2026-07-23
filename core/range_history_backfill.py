"""Rebuild ``strategy_states.settings.{or,mrr,orb}_ranges_history`` from bars + trade snapshots.

Uses the same overnight window math as ``core/backtest/session_shade.py`` and
TOML range clocks for MRR/ORB — no full walk-forward replay required.

Bar timestamps: always normalize via ``core.backtest.ohlcv.ohlcv_index_naive_utc``
before merging CSV + API OHLCV (see ``docs/GOTCHAS.md``).
"""
from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from core.backtest.parquet_cache import load_ohlcv_cached
from core.backtest.ohlcv import ohlcv_index_naive_utc
from core.backtest.session_shade import (
    load_overnight_range_timing_from_toml,
    overnight_session_start_end_unix,
    parse_hh_mm,
)
from strategies.strategy_base import append_range_history

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]

_STRATEGY_SETTINGS_KEY = {
    "overnight_range": "or_ranges",
    "morning_range_reversion": "mrr_ranges",
    "opening_range_breakout": "orb_ranges",
}

# Always attempt bar reconstruction for canonical Databento symbols — not only
# the account's live strategy_state.symbols (e.g. MRR may run MGC+MNQ but
# state row only lists one symbol).
_BAR_BACKFILL_SYMBOLS = ("MNQ", "MES", "MGC")


def _load_timing(strategy_name: str) -> Tuple[str, str, str, str, str]:
    """Return (range_start, range_end_open, session_tz, overnight_start, overnight_end)."""
    import tomllib

    name_map = {
        "overnight_range": "overnight_range.toml",
        "morning_range_reversion": "morning_range_reversion.toml",
        "opening_range_breakout": "opening_range_breakout.toml",
    }
    fname = name_map.get(strategy_name)
    if not fname:
        raise ValueError(f"unsupported strategy: {strategy_name}")
    path = ROOT / "config" / "strategies" / fname
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    sig = data.get("signal") or {}
    timing = data.get("timing") or {}
    tz = str(sig.get("session_timezone") or timing.get("session_timezone") or "America/New_York")
    tz = tz.replace("US/Eastern", "America/New_York")
    rs = str(sig.get("range_start") or "09:30")
    re = str(sig.get("range_end_open") or "10:00")
    ost = str(timing.get("overnight_start") or "19:00")
    oen = str(timing.get("overnight_end") or "10:00")
    return rs, re, tz, ost, oen


def _csv_path(symbol: str) -> Optional[Path]:
    sym = str(symbol or "").upper()
    for name in (f"{sym}_1m_databento.csv", f"{sym.lower()}_1m_databento.csv"):
        p = ROOT / "historical_data" / "price" / name
        if p.is_file():
            return p
    return None


def _hl_from_df_window(df: pd.DataFrame, start_utc: pd.Timestamp, end_utc: pd.Timestamp) -> Optional[Dict[str, float]]:
    if df is None or df.empty:
        return None
    sl = df.loc[(df.index >= start_utc) & (df.index <= end_utc)]
    if sl.empty:
        return None
    hi = float(sl["high"].astype(float).max())
    lo = float(sl["low"].astype(float).min())
    if not (hi > lo):
        return None
    return {"high": hi, "low": lo, "mid": (hi + lo) / 2.0, "width": hi - lo}


def _session_window_et_bounds(
    strategy_name: str,
    session_date: date,
    rs: str,
    re: str,
    tz: str,
    ost: str,
    oen: str,
) -> Optional[Tuple[str, str]]:
    """Return ``(session_start_et, session_end_et)`` as timezone-aware US/Eastern ISO strings."""
    from zoneinfo import ZoneInfo

    zi = ZoneInfo(tz)
    if strategy_name == "overnight_range":
        ref = datetime.combine(session_date, time(10, 0), tzinfo=zi)
        ref_unix = int(ref.astimezone(timezone.utc).timestamp())
        su, eu = overnight_session_start_end_unix(ref_unix, ost, oen, tz)
        start_et = datetime.fromtimestamp(su, tz=timezone.utc).astimezone(zi)
        end_et = datetime.fromtimestamp(eu, tz=timezone.utc).astimezone(zi)
        return start_et.isoformat(), end_et.isoformat()
    sh, sm = parse_hh_mm(rs)
    eh, em = parse_hh_mm(re)
    start_local = datetime.combine(session_date, time(sh, sm), tzinfo=zi)
    end_open = datetime.combine(session_date, time(eh, em), tzinfo=zi)
    if end_open <= start_local:
        return None
    end_local = end_open - timedelta(microseconds=1)
    return start_local.isoformat(), end_local.isoformat()


def _session_window_utc_slice(
    strategy_name: str,
    session_date: date,
    rs: str,
    re: str,
    tz: str,
    ost: str,
    oen: str,
) -> Optional[Tuple[pd.Timestamp, pd.Timestamp]]:
    """Naive-UTC slice bounds for canonical Databento bar indexes."""
    bounds = _session_window_et_bounds(strategy_name, session_date, rs, re, tz, ost, oen)
    if not bounds:
        return None
    start_et = datetime.fromisoformat(bounds[0])
    end_et = datetime.fromisoformat(bounds[1])
    t0 = pd.Timestamp(start_et.astimezone(timezone.utc).replace(tzinfo=None))
    t1 = pd.Timestamp(end_et.astimezone(timezone.utc).replace(tzinfo=None))
    return t0, t1


def attach_range_window_et(
    blob: Dict[str, Any],
    strategy_name: str,
    *,
    session_date: Optional[str] = None,
) -> Dict[str, Any]:
    """Ensure ``session_{start,end}_et`` are real ET instants (not naive UTC mislabeled)."""
    if not isinstance(blob, dict):
        return blob
    out = dict(blob)
    sd_raw = session_date or out.get("session_date")
    if not sd_raw:
        return out
    try:
        sd = date.fromisoformat(str(sd_raw)[:10])
    except ValueError:
        return out
    rs, re, tz, ost, oen = _load_timing(strategy_name)
    bounds = _session_window_et_bounds(strategy_name, sd, rs, re, tz, ost, oen)
    if not bounds:
        return out
    out["session_start_et"], out["session_end_et"] = bounds
    out["session_date"] = sd.isoformat()
    return out


def _session_window_utc(
    strategy_name: str,
    session_date: date,
    rs: str,
    re: str,
    tz: str,
    ost: str,
    oen: str,
) -> Optional[Tuple[pd.Timestamp, pd.Timestamp, str]]:
    win = _session_window_utc_slice(strategy_name, session_date, rs, re, tz, ost, oen)
    if not win:
        return None
    return win[0], win[1], session_date.isoformat()


# Back-compat alias — prefer ``core.backtest.ohlcv.ohlcv_index_naive_utc`` in new code.
_ohlcv_index_naive_utc = ohlcv_index_naive_utc


def chart_bars_to_ohlcv_df(bars: Optional[List[Dict[str, Any]]]) -> Optional[pd.DataFrame]:
    """Normalize chart/API bar dicts to a naive-UTC OHLCV frame (Databento index style)."""
    if not bars:
        return None
    rows: List[Dict[str, Any]] = []
    for bar in bars:
        if not isinstance(bar, dict):
            continue
        ts_raw = bar.get("time")
        if ts_raw is None:
            ts_raw = bar.get("timestamp")
        try:
            if isinstance(ts_raw, (int, float)):
                tsec = int(ts_raw)
            elif isinstance(ts_raw, str) and ts_raw:
                tsec = int(pd.Timestamp(ts_raw).timestamp())
            elif isinstance(ts_raw, datetime):
                tsec = int(ts_raw.timestamp())
            else:
                continue
        except (TypeError, ValueError, OverflowError):
            continue
        try:
            rows.append(
                {
                    "timestamp": pd.to_datetime(tsec, unit="s", utc=True).tz_convert(None),
                    "open": float(bar["open"]),
                    "high": float(bar["high"]),
                    "low": float(bar["low"]),
                    "close": float(bar["close"]),
                    "volume": float(bar.get("volume") or 0),
                }
            )
        except (KeyError, TypeError, ValueError):
            continue
    if not rows:
        return None
    df = pd.DataFrame(rows).set_index("timestamp")
    df = ohlcv_index_naive_utc(df)
    if df is None or df.empty:
        return None
    return df[~df.index.duplicated(keep="last")]


def merge_ohlcv_dataframes(*frames: Optional[pd.DataFrame]) -> Optional[pd.DataFrame]:
    """Concat OHLCV frames; later frames win on duplicate timestamps (API over CSV)."""
    usable = [ohlcv_index_naive_utc(f) for f in frames if f is not None and not f.empty]
    usable = [f for f in usable if f is not None and not f.empty]
    if not usable:
        return None
    if len(usable) == 1:
        return usable[0]
    merged = pd.concat(usable).sort_index()
    return merged[~merged.index.duplicated(keep="last")]


def load_merged_1m_df(
    symbol: str,
    *,
    lookback_calendar_days: int = 60,
    api_bars: Optional[List[Dict[str, Any]]] = None,
) -> Optional[pd.DataFrame]:
    """Databento canonical 1m CSV stitched with optional recent API 1m bars."""
    csv = _csv_path(symbol)
    csv_df: Optional[pd.DataFrame] = None
    if csv:
        try:
            csv_df = ohlcv_index_naive_utc(load_ohlcv_cached(csv))
        except Exception:
            logger.debug("load_ohlcv_cached failed for %s", symbol, exc_info=True)
    api_df = chart_bars_to_ohlcv_df(api_bars)
    merged = merge_ohlcv_dataframes(csv_df, api_df)
    if merged is None or merged.empty:
        return None
    if lookback_calendar_days > 0:
        cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_calendar_days)
        cutoff_naive = cutoff.replace(tzinfo=None)
        merged = merged.loc[merged.index >= cutoff_naive]
    return merged if merged is not None and not merged.empty else None


def range_anchor_for_trade_session(
    entry_dt: datetime,
    symbol: str,
    strategy_name: str,
    *,
    df: Optional[pd.DataFrame] = None,
) -> Optional[Dict[str, Any]]:
    """Session H/L range for one trade from canonical OHLCV (walkforward / recap parity).

    Uses the same TOML windows and ``_hl_from_df_window`` as bar backfill — not
    live ``strategy_states`` current-range blobs (which are often the wrong session).
    """
    from zoneinfo import ZoneInfo

    strat = str(strategy_name or "").strip()
    if strat not in _STRATEGY_SETTINGS_KEY:
        return None
    if entry_dt.tzinfo is None:
        entry_dt = entry_dt.replace(tzinfo=timezone.utc)
    rs, re, tz, ost, oen = _load_timing(strat)
    session_date = entry_dt.astimezone(ZoneInfo(tz)).date()
    win = _session_window_utc(strat, session_date, rs, re, tz, ost, oen)
    if not win:
        return None
    t0, t1, sd = win
    if df is None:
        sym = str(symbol or "").upper()
        if strat == "overnight_range":
            df = load_merged_1m_df(sym, lookback_calendar_days=14)
        else:
            root = sym.split(".")[-1]
            p5 = ROOT / "historical_data" / "price" / f"{root.lower()}_5m_databento.csv"
            p1 = _csv_path(sym)
            if p5.is_file():
                df = load_ohlcv_cached(p5)
            elif p1 and p1.is_file():
                df = load_ohlcv_cached(p1)
            else:
                df = None
    blob = _hl_from_df_window(df, t0, t1) if df is not None else None
    if not blob:
        return None
    blob = dict(blob)
    blob["session_date"] = sd
    blob["strategy_name"] = strat
    blob["derived"] = "databento_ohlcv_anchor"
    return attach_range_window_et(blob, strat, session_date=sd)


async def fetch_api_1m_bars_for_backfill(
    trading_bot: Any,
    symbol: str,
    *,
    lookback_days: int = 14,
) -> List[Dict[str, Any]]:
    """Recent broker 1m bars for range reconstruction (fills the Databento lag gap)."""
    if not trading_bot or not hasattr(trading_bot, "get_historical_data"):
        return []
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=max(1, lookback_days))
    limit = min(max(1, lookback_days) * 24 * 60 + 240, 8000)
    try:
        bars = await trading_bot.get_historical_data(
            symbol=str(symbol),
            timeframe="1m",
            limit=limit,
            start_time=start,
            end_time=end,
        )
        return list(bars or [])
    except Exception:
        logger.debug("api 1m backfill fetch failed for %s", symbol, exc_info=True)
        return []


def _ranges_from_ohlcv_df(
    df: Optional[pd.DataFrame],
    symbol: str,
    strategy_name: str,
    *,
    max_sessions: int = 20,
    lookback_calendar_days: int = 60,
) -> Dict[str, Dict[str, Any]]:
    if df is None or df.empty:
        return {}
    rs, re, tz, ost, oen = _load_timing(strategy_name)
    sym_u = str(symbol).upper()
    out: Dict[str, Dict[str, Any]] = {}
    today = datetime.now(timezone.utc).date()
    found = 0
    for offset in range(lookback_calendar_days):
        if found >= max_sessions:
            break
        d = today - timedelta(days=offset)
        win = _session_window_utc(strategy_name, d, rs, re, tz, ost, oen)
        if not win:
            continue
        t0, t1, sd = win
        blob = _hl_from_df_window(df, t0, t1)
        if not blob:
            continue
        blob["session_date"] = sd
        blob = attach_range_window_et(blob, strategy_name, session_date=sd)
        out[sd] = {sym_u: blob}
        found += 1
    return out


def ranges_from_bars(
    symbol: str,
    strategy_name: str,
    *,
    max_sessions: int = 20,
    lookback_calendar_days: int = 60,
    api_bars: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Dict[str, Any]]:
    """Scan merged 1m bars (Databento CSV + optional API) for range windows."""
    df = load_merged_1m_df(
        symbol,
        lookback_calendar_days=lookback_calendar_days,
        api_bars=api_bars,
    )
    if df is None:
        logger.warning("no 1m bars for %s — skip bar backfill", symbol)
        return {}
    return _ranges_from_ohlcv_df(
        df, symbol, strategy_name,
        max_sessions=max_sessions,
        lookback_calendar_days=lookback_calendar_days,
    )


def ranges_from_trade_snapshots(
    db: Any,
    account_id: str,
    strategy_name: str,
    *,
    limit: int = 500,
) -> Dict[str, Dict[str, Dict[str, Any]]]:
    """``{session_date: {SYMBOL: range_blob}}`` from ``trade_snapshots``."""
    if not db or not hasattr(db, "list_trade_range_snapshots"):
        return {}
    rows = db.list_trade_range_snapshots(str(account_id), strategy_name=strategy_name, limit=limit)
    out: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for row in rows:
        sym = str(row.get("symbol") or "").upper()
        raw = row.get("range_snapshot_json")
        if not sym or not isinstance(raw, dict):
            continue
        hi, lo = raw.get("high"), raw.get("low")
        try:
            hi_f, lo_f = float(hi), float(lo)
        except (TypeError, ValueError):
            continue
        if not (hi_f > lo_f):
            continue
        sd = raw.get("session_date")
        if not sd:
            exit_time = row.get("exit_time")
            if isinstance(exit_time, datetime):
                sd = exit_time.date().isoformat()
            elif isinstance(exit_time, str) and exit_time:
                sd = exit_time[:10]
        if not sd:
            continue
        sd = str(sd)[:10]
        entry = {
            "high": hi_f,
            "low": lo_f,
            "mid": float(raw.get("mid") or (hi_f + lo_f) / 2.0),
            "width": hi_f - lo_f,
            "session_date": sd,
        }
        for opt in ("session_start_et", "session_end_et"):
            if raw.get(opt) is not None:
                entry[opt] = raw[opt]
        entry = attach_range_window_et(entry, strategy_name, session_date=sd)
        out.setdefault(sd, {})[sym] = entry
    return out


def merge_session_maps(
    *maps: Dict[str, Dict[str, Dict[str, Any]]],
) -> List[Tuple[str, Dict[str, Dict[str, Any]]]]:
    """Merge by session_date; later maps override earlier for same sym."""
    merged: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for m in maps:
        for sd, syms in (m or {}).items():
            merged.setdefault(sd, {}).update(syms)
    return sorted(merged.items(), key=lambda x: x[0], reverse=True)


def backfill_strategy_range_history(
    db: Any,
    account_id: str,
    strategy_name: str,
    symbols: List[str],
    *,
    max_sessions: int = 20,
    dry_run: bool = False,
    trading_bot: Any = None,
    api_bars_by_symbol: Optional[Dict[str, List[Dict[str, Any]]]] = None,
) -> int:
    """
    Populate ``settings[{key}_history]`` for one strategy/account.
    Returns number of history entries written.
    """
    settings_key = _STRATEGY_SETTINGS_KEY.get(strategy_name)
    if not settings_key or not db:
        return 0
    sym_list: List[str] = []
    seen_syms: set = set()
    for raw in list(symbols or []) + list(_BAR_BACKFILL_SYMBOLS):
        su = str(raw or "").upper().strip()
        if not su or su in seen_syms:
            continue
        seen_syms.add(su)
        sym_list.append(su)
    if not sym_list:
        st = db.get_strategy_state(str(account_id), strategy_name) or {}
        for raw in (st.get("symbols") or []):
            su = str(raw or "").upper().strip()
            if su and su not in seen_syms:
                seen_syms.add(su)
                sym_list.append(su)

    bar_maps: Dict[str, Dict[str, Dict[str, Any]]] = {}
    api_by_sym = api_bars_by_symbol or {}
    for sym in sym_list:
        for sd, blob in ranges_from_bars(
            sym,
            strategy_name,
            max_sessions=max_sessions,
            api_bars=api_by_sym.get(sym),
        ).items():
            bar_maps.setdefault(sd, {}).update(blob)

    snap_map = ranges_from_trade_snapshots(db, str(account_id), strategy_name)
    # CSV/API first, then trade snapshots override (freshest persisted ranges win).
    sessions = merge_session_maps(bar_maps, snap_map)[:max_sessions]

    if not sessions:
        logger.info("backfill %s/%s: no sessions found", account_id, strategy_name)
        return 0

    if dry_run:
        logger.info(
            "backfill dry-run %s %s: would write %d sessions (%s..%s)",
            account_id, strategy_name, len(sessions),
            sessions[-1][0], sessions[0][0],
        )
        return len(sessions)

    st = db.get_strategy_state(str(account_id), strategy_name) or {}
    settings = dict(st.get("settings") or {})
    metadata = dict(st.get("metadata") or {})
    hist_key = f"{settings_key}_history"
    settings[hist_key] = []

    for sd, sym_blobs in sessions:
        saved_at = f"{sd}T16:00:00+00:00"
        repaired = {
            sym: attach_range_window_et(blob, strategy_name, session_date=sd)
            for sym, blob in sym_blobs.items()
        }
        append_range_history(settings, settings_key, repaired, saved_at, max_entries=max_sessions)

    # Keep latest snapshot as current slot when present.
    latest_sd, latest_blobs = sessions[0]
    settings[settings_key] = {
        sym: attach_range_window_et(blob, strategy_name, session_date=latest_sd)
        for sym, blob in latest_blobs.items()
    }
    metadata[f"{settings_key}_saved_at"] = datetime.now(timezone.utc).isoformat()
    metadata[f"{settings_key}_history_backfilled_at"] = metadata[f"{settings_key}_saved_at"]

    db.save_strategy_state(
        account_id=str(account_id),
        strategy_name=strategy_name,
        enabled=bool(st.get("enabled", True)),
        symbols=sym_list,
        settings=settings,
        metadata=metadata,
        last_started=st.get("last_started"),
        last_stopped=st.get("last_stopped"),
    )
    logger.info(
        "backfilled %s %s: %d sessions into %s",
        account_id, strategy_name, len(sessions), hist_key,
    )
    return len(sessions)


async def backfill_strategy_range_history_async(
    db: Any,
    account_id: str,
    strategy_name: str,
    symbols: List[str],
    *,
    trading_bot: Any = None,
    max_sessions: int = 20,
    dry_run: bool = False,
    api_lookback_days: int = 14,
) -> int:
    """Async backfill: stitches Databento CSV with recent broker 1m bars when available."""
    sym_list: List[str] = []
    seen_syms: set = set()
    for raw in list(symbols or []) + list(_BAR_BACKFILL_SYMBOLS):
        su = str(raw or "").upper().strip()
        if not su or su in seen_syms:
            continue
        seen_syms.add(su)
        sym_list.append(su)
    api_by_sym: Dict[str, List[Dict[str, Any]]] = {}
    if trading_bot:
        for sym in sym_list:
            api_by_sym[sym] = await fetch_api_1m_bars_for_backfill(
                trading_bot, sym, lookback_days=api_lookback_days,
            )
    return backfill_strategy_range_history(
        db,
        account_id,
        strategy_name,
        sym_list,
        max_sessions=max_sessions,
        dry_run=dry_run,
        trading_bot=trading_bot,
        api_bars_by_symbol=api_by_sym,
    )


def backfill_all_range_strategies(
    db: Any,
    account_id: str,
    *,
    max_sessions: int = 20,
    dry_run: bool = False,
) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for name in _STRATEGY_SETTINGS_KEY:
        counts[name] = backfill_strategy_range_history(
            db, account_id, name, [],
            max_sessions=max_sessions,
            dry_run=dry_run,
        )
    return counts
