"""Lightweight TopStepX history client for offline stitch / export scripts.

Avoids constructing ``TopStepXTradingBot`` (Postgres pool, Discord, hubs,
SignalR). Same auth + adapter stack as ``core/backtest_executor.py``.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


async def open_broker_history_adapter(
    *,
    api_key: Optional[str] = None,
    username: Optional[str] = None,
):
    """Authenticate and return ``(auth_manager, adapter)``.

    Sets ``ENABLE_SIGNALR=false`` and ``DISABLE_DATABASE=1`` when unset so
    casual imports of shared bot helpers cannot open Railway Postgres or
    start SignalR during a CSV stitch.

    Returns ``(None, None)`` when authentication fails (caller should exit
    non-zero). Always close via ``close_broker_history_session(auth)``.
    """
    os.environ.setdefault("ENABLE_SIGNALR", "false")
    os.environ.setdefault("DISABLE_DATABASE", "1")
    # History pulls don't need a 30s total hang on bad DNS.
    os.environ.setdefault("API_TIMEOUT", os.getenv("API_TIMEOUT") or "10")

    from core.auth import AuthManager
    from core.rate_limiter import RateLimiter
    from brokers.topstepx_adapter import TopStepXAdapter

    auth = AuthManager(api_key=api_key, username=username)
    ok = await auth.authenticate()
    if not ok:
        logger.error("Broker history authentication failed")
        try:
            await auth.close()
        except Exception:
            logger.debug("auth.close after failed login failed", exc_info=True)
        return None, None

    adapter = TopStepXAdapter(
        auth_manager=auth,
        rate_limiter=RateLimiter(max_calls=60, period=60),
    )
    # Prefetch so the first get_historical_data does not ERROR-log an empty
    # contract cache before auto-refresh (noisy but otherwise harmless).
    try:
        await adapter.get_available_contracts(use_cache=False)
    except Exception:
        logger.debug("contract prefetch after history auth failed", exc_info=True)
    return auth, adapter


async def close_broker_history_session(auth) -> None:
    """Best-effort close of the shared aiohttp session (Ctrl-C safe)."""
    if auth is None:
        return
    try:
        await auth.close()
    except Exception:
        logger.debug("broker history session close failed", exc_info=True)
