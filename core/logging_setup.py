"""Centralized logging configuration for the trading bot.

Every entry point (trading_bot, strategy_executor, webhook server, scheduled
tasks, scripts) calls configure_logging() exactly once before any other
logger.* calls. This guarantees:

  * one rotating file handler (10MB x 5 backups, default 50MB total)
  * one stdout handler (default WARNING level so the terminal stays quiet)
  * uvloop installed for asyncio (best-effort; falls back silently)
  * SignalRCoreClient and websocket logs muted to WARNING

LOG_FILE env var picks the destination. LOG_LEVEL env var sets the file
handler level (default INFO). Pass force=True only on first call.
"""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

_CONFIGURED = False


def configure_logging(
    *,
    log_file: Optional[str] = None,
    level: Optional[str] = None,
    console_level: str = "WARNING",
    max_bytes: int = 10 * 1024 * 1024,
    backup_count: int = 5,
    install_uvloop: bool = True,
) -> Path:
    """Configure logging once for the whole process.

    Returns the absolute Path of the resolved log file.
    Subsequent calls are no-ops unless ``force_reconfigure`` env is set.
    """
    global _CONFIGURED
    if _CONFIGURED and not os.getenv("LOGGING_FORCE_RECONFIGURE"):
        return Path(logging.getLogger().handlers[0].baseFilename)  # type: ignore[attr-defined]

    log_file = log_file or os.getenv("LOG_FILE", "trading_bot.log")
    file_level_name = (level or os.getenv("LOG_LEVEL", "INFO")).upper()
    file_level = getattr(logging, file_level_name, logging.INFO)
    console_level_value = getattr(logging, console_level.upper(), logging.WARNING)

    log_path = Path(log_file)
    if not log_path.is_absolute():
        log_path = Path(__file__).resolve().parent.parent / log_path
    if log_path.parent.exists() and log_path.parent.is_file():
        from datetime import datetime, timezone
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        log_path.parent.rename(log_path.parent.with_name(f"{log_path.parent.name}.file_backup_{ts}"))
    log_path.parent.mkdir(parents=True, exist_ok=True)

    fmt = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")

    file_handler = RotatingFileHandler(
        str(log_path),
        mode="a",
        encoding="utf-8",
        maxBytes=max_bytes,
        backupCount=backup_count,
    )
    file_handler.setLevel(file_level)
    file_handler.setFormatter(fmt)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(console_level_value)
    console_handler.setFormatter(fmt)

    logging.basicConfig(
        level=file_level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[file_handler, console_handler],
        force=True,
    )

    logging.getLogger("SignalRCoreClient").setLevel(logging.WARNING)
    logging.getLogger("websocket").setLevel(logging.WARNING)
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)

    if install_uvloop:
        try:
            import uvloop  # type: ignore
            uvloop.install()
        except Exception:
            pass

    _CONFIGURED = True
    logging.getLogger(__name__).info(
        "Logging configured: file=%s level=%s console=%s", log_path, file_level_name, console_level
    )
    return log_path


def get_log_path() -> Path:
    """Return the path of the active rotating log file."""
    root = logging.getLogger()
    for h in root.handlers:
        if isinstance(h, RotatingFileHandler):
            return Path(h.baseFilename)
    return Path("trading_bot.log").resolve()
