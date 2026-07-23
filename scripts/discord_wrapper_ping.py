#!/usr/bin/env python3
"""Sync Discord webhook ping for launchd wrapper lifecycle events.

The MRR wrapper (``run_morning_reversion.sh``) runs under bash without an
asyncio loop, so this helper uses ``urllib`` instead of
``core.discord_notifier`` (aiohttp).  No-op when ``DISCORD_WEBHOOK_URL`` is
unset.  Opt out entirely: ``DISCORD_WRAPPER_PING=0``.

Usage::

    scripts/discord_wrapper_ping.py start "account=1 wake=06:53 ET"
    scripts/discord_wrapper_ping.py idle_exit "next wake in 14h"
    scripts/discord_wrapper_ping.py executor_start "log=morning_range_....log"
    scripts/discord_wrapper_ping.py session_end "code=0 uptime=32400s"
    scripts/discord_wrapper_ping.py failed "syntax error / max restarts"
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone


def _enabled() -> bool:
    flag = os.getenv("DISCORD_WRAPPER_PING", "1") or "1"
    return str(flag).strip().strip('"').strip("'").lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def send_wrapper_ping(event: str, detail: str = "") -> bool:
    """Post a short plaintext message; returns True on success."""
    if not _enabled():
        return True
    url = (os.getenv("DISCORD_WEBHOOK_URL") or "").strip()
    if not url:
        return False

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    host = os.uname().nodename if hasattr(os, "uname") else "host"
    lines = [f"**MRR wrapper — {event}**", ts, f"host={host}"]
    if detail.strip():
        lines.append(detail.strip())
    content = "\n".join(lines)[:2000]

    req = urllib.request.Request(
        url,
        data=json.dumps({"content": content}).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "User-Agent": "tradeBotServer-wrapper-ping/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status in (200, 204)
    except urllib.error.HTTPError as exc:
        print(f"discord wrapper ping HTTP {exc.code}: {exc.reason}", file=sys.stderr)
        return False
    except Exception as exc:
        print(f"discord wrapper ping failed: {exc}", file=sys.stderr)
        return False


def main(argv: list[str] | None = None) -> int:
    args = list(argv or sys.argv[1:])
    if not args:
        print("usage: discord_wrapper_ping.py <event> [detail...]", file=sys.stderr)
        return 2
    event = args[0]
    detail = " ".join(args[1:]) if len(args) > 1 else ""
    return 0 if send_wrapper_ping(event, detail) else 1


if __name__ == "__main__":
    raise SystemExit(main())
