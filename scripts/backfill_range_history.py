#!/usr/bin/env python3
"""Backfill ``{or,mrr,orb}_ranges_history`` from Databento 1m bars + trade_snapshots.

Reuses overnight session math from ``core/backtest/session_shade.py`` and TOML
range clocks — lighter than a full walk-forward replay.

Example::

  ENABLE_SIGNALR=false .venv/bin/python scripts/backfill_range_history.py \\
    --account-id 22182502 --sessions 20
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

from core.logging_setup import configure_logging
from core.range_history_backfill import backfill_all_range_strategies
from infrastructure.database import DatabaseManager

logger = logging.getLogger(__name__)


def main() -> int:
    configure_logging()
    p = argparse.ArgumentParser(description="Backfill strategy range history for the master chart")
    p.add_argument("--account-id", required=True, help="TopStepX account id")
    p.add_argument("--sessions", type=int, default=20, help="Max sessions per strategy (default 20)")
    p.add_argument("--dry-run", action="store_true", help="Log only; do not write DB")
    args = p.parse_args()

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        logger.error("DATABASE_URL not set")
        return 1
    db = DatabaseManager()
    if not db.pool:
        logger.error("database pool not available (check DATABASE_URL)")
        return 1

    counts = backfill_all_range_strategies(
        db, args.account_id, max_sessions=max(1, args.sessions), dry_run=args.dry_run,
    )
    for name, n in counts.items():
        print(f"{name}: {n} sessions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
