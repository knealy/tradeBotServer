#!/usr/bin/env python3
"""One-shot cleanup: drop stale time-window keys from ``strategy_states.settings``.

TOML (``config/strategies/<name>.toml``) is the source of truth for
``trading_start_time`` / ``trading_end_time`` / ``no_trade_start`` /
``no_trade_end``. They used to be echoed into ``strategy_states.settings`` on
every save, which silently overrode TOML edits on the next executor restart
(see strategies/strategy_manager.py ``_TOML_AUTHORITATIVE_TIME_KEYS``).

Running this script is **optional** — the executor now ignores those keys on
read and stops writing them on save, so they will be dropped from any row on
its next save. This script just clears them eagerly to keep
``strategy_states.settings`` tidy and to silence the
``Ignoring stale persisted time-window settings`` INFO log.

Usage:
    python scripts/scrub_strategy_state_time_keys.py            # dry run
    python scripts/scrub_strategy_state_time_keys.py --apply    # write changes
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.logging_setup import configure_logging
import load_env  # noqa: F401  -- side effect: load .env

# Force console at INFO so the dry-run report is visible in the operator's terminal
# (configure_logging defaults the console to WARNING).
configure_logging(console_level="INFO")
logger = logging.getLogger("scrub_strategy_state_time_keys")

STALE_KEYS = (
    "trading_start_time",
    "trading_end_time",
    "no_trade_start",
    "no_trade_end",
)


def scrub(apply: bool) -> int:
    from infrastructure.database import get_database

    db = get_database()
    if not db or not db.pool:
        logger.error("Database not available; aborting.")
        return 2

    select_sql = """
        SELECT account_id, strategy_name,
               settings ?| array['trading_start_time','trading_end_time',
                                 'no_trade_start','no_trade_end'] AS has_stale,
               settings
        FROM strategy_states
        WHERE settings ?| array['trading_start_time','trading_end_time',
                                'no_trade_start','no_trade_end']
        ORDER BY strategy_name, account_id
    """

    update_sql = """
        UPDATE strategy_states
        SET settings = settings - %s::text[],
            updated_at = NOW()
        WHERE account_id = %s AND strategy_name = %s
    """

    with db.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(select_sql)
            rows = cur.fetchall()
            if not rows:
                logger.info("✅ No rows carry stale time-window keys.")
                return 0
            logger.info("Found %d row(s) with stale time-window keys.", len(rows))
            for account_id, strategy_name, has_stale, settings in rows:
                stale_present = sorted(k for k in STALE_KEYS if k in (settings or {}))
                logger.info(
                    "  acct=%s  strat=%-30s  stale=%s",
                    str(account_id)[:8] + "…",
                    strategy_name,
                    stale_present,
                )
            if not apply:
                logger.info(
                    "Dry run only. Re-run with --apply to drop these keys "
                    "(the SQL is a JSONB minus: settings = settings - array[...])."
                )
                return 0

            updated = 0
            for account_id, strategy_name, _, _ in rows:
                cur.execute(update_sql, (list(STALE_KEYS), account_id, strategy_name))
                updated += cur.rowcount
            logger.info("🧹 Cleared stale keys from %d row(s).", updated)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually drop the keys (default: dry run / report only).",
    )
    args = parser.parse_args()
    return scrub(apply=args.apply)


if __name__ == "__main__":
    sys.exit(main())
