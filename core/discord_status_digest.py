"""Build Discord periodic status / daily strategy briefing lines."""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timezone
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from core.market_calendar import equity_futures_session_note

if TYPE_CHECKING:
    from trading_bot import TopStepXTradingBot

logger = logging.getLogger(__name__)

_WD = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def discord_status_interval_seconds() -> int:
    """Parse ``DISCORD_STATUS_INTERVAL_SECONDS`` (supports quoted values)."""
    import os

    raw = os.getenv("DISCORD_STATUS_INTERVAL_SECONDS", "0") or "0"
    v = str(raw).strip().strip('"').strip("'")
    try:
        return max(0, int(float(v)))
    except (TypeError, ValueError):
        return 0


async def build_discord_status_lines(
    bot: "TopStepXTradingBot",
) -> tuple[str, List[str]]:
    """Return ``(title, lines)`` for ``DiscordNotifier.send_status_digest``."""
    now_utc = datetime.now(timezone.utc)
    lines: List[str] = [now_utc.strftime("%Y-%m-%d %H:%M:%S UTC")]

    acc_name = ""
    aid: Optional[str] = None
    if bot.selected_account:
        if isinstance(bot.selected_account, dict):
            aid = bot.selected_account.get("id")
            acc_name = str(bot.selected_account.get("name") or "")
        else:
            aid = str(bot.selected_account)

    if acc_name:
        lines.append(f"account={acc_name}")
    if aid:
        aid_s = str(aid)
        if getattr(bot, "state_cache", None):
            try:
                pos = await bot.state_cache.get_positions(aid_s)
                ord_ = await bot.state_cache.get_orders(aid_s)
                lines.append(f"positions={len(pos or [])} orders={len(ord_ or [])}")
            except Exception:
                logger.debug("Discord digest: positions/orders fetch failed", exc_info=True)
        if getattr(bot, "account_tracker", None):
            try:
                st = bot.account_tracker.get_state(aid_s)
                if isinstance(st, dict) and st:
                    bal = st.get("balance")
                    ru = st.get("realized_pnl")
                    uu = st.get("unrealized_pnl")
                    if bal is not None:
                        lines.append(f"balance={bal}")
                    if ru is not None or uu is not None:
                        lines.append(f"realized_pnl={ru} unrealized_pnl={uu}")
            except Exception:
                logger.debug("Discord digest: account_tracker failed", exc_info=True)
        tracker = getattr(bot, "session_trade_tracker", None)
        if tracker and hasattr(tracker, "get_session_pnl"):
            try:
                sp = tracker.get_session_pnl(aid_s)
                if isinstance(sp, dict) and sp:
                    tc = sp.get("total_trades") or sp.get("trade_count") or sp.get("trades")
                    spnl = sp.get("net_pnl") or sp.get("session_pnl") or sp.get("realized_pnl")
                    if tc is not None:
                        lines.append(f"session_trades={tc}")
                    if spnl is not None:
                        lines.append(f"session_pnl={spnl}")
            except Exception:
                logger.debug("Discord digest: session_pnl failed", exc_info=True)

    daily = _build_daily_briefing(bot)
    if daily:
        lines.append("")
        lines.extend(daily)

    return "Trade bot status", lines


def _build_daily_briefing(bot: "TopStepXTradingBot") -> List[str]:
    """ET calendar + per-strategy trading plan for today."""
    import os

    flag = os.getenv("DISCORD_STATUS_INCLUDE_DAILY", "1") or "1"
    if str(flag).strip().strip('"').strip("'").lower() in ("0", "false", "no", "off"):
        return []

    try:
        from zoneinfo import ZoneInfo

        tz = ZoneInfo("America/New_York")
    except Exception:
        tz = timezone.utc  # type: ignore[assignment]

    now_et = datetime.now(tz)
    d = now_et.date()
    lines: List[str] = [f"Today (ET): {d.isoformat()} {_WD[d.weekday()]}"]

    cal = equity_futures_session_note(d)
    if cal.get("trade_recommended", True):
        lines.append("calendar: normal session")
    else:
        lines.append(f"calendar: closed — {cal.get('reason', 'holiday/weekend')}")

    sm = getattr(bot, "strategy_manager", None)
    if not sm:
        lines.append("strategies: (manager not loaded)")
        return lines

    active = list(getattr(sm, "active_strategies", []) or [])
    if not active:
        lines.append("strategies: none running")
        return lines

    for name in active:
        strat = sm.strategies.get(name) if hasattr(sm, "strategies") else None
        if strat is None:
            lines.append(f"• {name}: running (no instance)")
            continue
        brief_fn = getattr(strat, "discord_daily_brief", None)
        if callable(brief_fn):
            try:
                chunk = brief_fn(now_et=now_et) or []
                if chunk:
                    lines.append(f"• {name}:")
                    lines.extend(f"  {ln}" if not ln.startswith("  ") else ln for ln in chunk)
                else:
                    lines.append(f"• {name}: active")
            except Exception as exc:
                lines.append(f"• {name}: brief error ({exc})")
                logger.debug("discord_daily_brief failed for %s", name, exc_info=True)
        else:
            syms = ", ".join(getattr(strat.config, "symbols", []) or [])
            lines.append(f"• {name}: active symbols={syms or '?'}")

    return lines


def _fmt_time(t: time) -> str:
    return t.strftime("%H:%M")
