"""Centralized logging configuration for the trading bot.

Every entry point (trading_bot, strategy_executor, webhook server, scheduled
tasks, scripts) calls configure_logging() exactly once before any other
logger.* calls. This guarantees:

  * one rotating file handler (10MB x 5 backups, default 50MB total)
  * one stdout handler (default WARNING level so the terminal stays quiet)
  * uvloop installed for asyncio on POSIX when the package is present (opt-out via env)
  * SignalRCoreClient and websocket logs muted to WARNING

LOG_FILE env var picks the destination. LOG_LEVEL env var sets the file
handler level (default INFO). Pass force=True only on first call.

LIFECYCLE_LOGGERS env var (or the ``lifecycle_logger_names`` kwarg) names
loggers whose **INFO** records should bypass the default console threshold and
appear in the terminal. Used by the morning_range_reversion executor so its
``📐 anchor range built`` / ``🌅 new session`` / ``🎯 SHORT/LONG`` lifecycle
beacons show in the foreground while the per-poll chatter from other modules
stays at file-only verbosity.
"""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Iterable, List, Optional

_CONFIGURED = False


class _LifecycleConsoleFilter(logging.Filter):
    """Console handler filter: allow INFO+ from named loggers, WARNING+ otherwise.

    A logger name matches if it is the named logger OR a child of it
    (``"strategies.foo"`` covers ``"strategies.foo.bar"``).
    """

    def __init__(self, names: Iterable[str], default_level: int) -> None:
        super().__init__()
        self._prefixes: tuple = tuple(sorted({str(n).strip() for n in names if n}))
        self._default_level = int(default_level)

    def _matches_lifecycle(self, logger_name: str) -> bool:
        for pref in self._prefixes:
            if logger_name == pref or logger_name.startswith(pref + "."):
                return True
        return False

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: D401
        if self._matches_lifecycle(record.name) and record.levelno >= logging.INFO:
            return True
        return record.levelno >= self._default_level


class _LifecycleConsoleFormatter(logging.Formatter):
    """Dual-format formatter for the lifecycle-aware console handler.

    The 2026-05-29 ask: when the morning_range_reversion executor prints
    ``📐 anchor range built`` to the terminal, the operator wants to see the
    range data — not the timestamp / logger-name / level prefix that's already
    in the file log. But the SAME console handler still has to print WARNING
    and ERROR lines with full context (timestamp matters when diagnosing a
    fault). So we format records based on origin + level:

      * INFO from a lifecycle logger     → message only (``%(message)s``)
      * everything else (WARN/ERROR/etc) → full format with timestamp+name+level

    Falls back to the full format if no lifecycle names are configured (kept
    for symmetry with the legacy single-formatter behaviour).
    """

    def __init__(self, lifecycle_names: Iterable[str], full_fmt: logging.Formatter) -> None:
        super().__init__(fmt="%(message)s")
        # Reuse the existing full-format Formatter so we don't drift from the
        # rest of the logging config.
        self._full_fmt = full_fmt
        self._prefixes: tuple = tuple(sorted({str(n).strip() for n in lifecycle_names if n}))

    def _is_lifecycle(self, logger_name: str) -> bool:
        for pref in self._prefixes:
            if logger_name == pref or logger_name.startswith(pref + "."):
                return True
        return False

    def format(self, record: logging.LogRecord) -> str:  # noqa: D401
        if record.levelno == logging.INFO and self._is_lifecycle(record.name):
            # Bare message for clean alignment of column-formatted lifecycle lines.
            return record.getMessage()
        return self._full_fmt.format(record)


def _parse_lifecycle_env() -> List[str]:
    """Read ``LIFECYCLE_LOGGERS`` env var (comma-separated names)."""
    raw = os.getenv("LIFECYCLE_LOGGERS", "").strip()
    if not raw:
        return []
    return [s.strip() for s in raw.split(",") if s.strip()]


def configure_logging(
    *,
    log_file: Optional[str] = None,
    level: Optional[str] = None,
    console_level: str = "WARNING",
    max_bytes: int = 10 * 1024 * 1024,
    backup_count: int = 5,
    install_uvloop: bool = True,
    lifecycle_logger_names: Optional[Iterable[str]] = None,
) -> Path:
    """Configure logging once for the whole process.

    Returns the absolute Path of the resolved log file.
    Subsequent calls are no-ops unless ``force_reconfigure`` env is set.

    ``lifecycle_logger_names`` (or the ``LIFECYCLE_LOGGERS`` env var, comma-sep)
    names loggers whose **INFO** records should bypass the default console
    threshold and appear in the terminal.
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

    # Merge explicit list + env so caller-passed names win without losing operator overrides.
    merged_lifecycle = list(lifecycle_logger_names or []) + _parse_lifecycle_env()
    seen: set = set()
    lifecycle_names: List[str] = []
    for name in merged_lifecycle:
        if name and name not in seen:
            lifecycle_names.append(name)
            seen.add(name)

    if lifecycle_names:
        # Drop the console threshold so the filter alone gates records.
        # Non-lifecycle records still need ``>= console_level_value`` (enforced by
        # the filter), so non-strategy chatter stays quiet.
        console_handler.setLevel(logging.DEBUG)
        console_handler.addFilter(_LifecycleConsoleFilter(lifecycle_names, console_level_value))
        # Use the dual formatter so lifecycle INFO lines print bare (no
        # timestamp/logger/level prefix) while WARNING/ERROR keep the full
        # context. This lets the 📐 anchor-range-built lines align cleanly in
        # the terminal — that's the explicit operator-experience ask in the
        # 2026-05-29 follow-up.
        console_handler.setFormatter(_LifecycleConsoleFormatter(lifecycle_names, fmt))

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

    log = logging.getLogger(__name__)
    if install_uvloop:
        use_uv = os.getenv("USE_UVLOOP", "1").lower() not in ("0", "false", "no")
        disabled = os.getenv("DISABLE_UVLOOP", "").lower() in ("1", "true", "yes")
        if not use_uv or disabled:
            log.debug("uvloop skipped (USE_UVLOOP/DISABLE_UVLOOP)")
        else:
            try:
                import uvloop  # type: ignore[import-untyped]

                uvloop.install()
                log.info("uvloop event loop policy installed")
            except ImportError:
                log.debug("uvloop not available (optional; install on Linux/macOS)")
            except Exception as exc:
                log.debug("uvloop.install skipped: %s", exc)

    _CONFIGURED = True
    if lifecycle_names:
        log.info(
            "Logging configured: file=%s level=%s console=%s (+ INFO console for: %s)",
            log_path, file_level_name, console_level, ", ".join(lifecycle_names),
        )
    else:
        log.info(
            "Logging configured: file=%s level=%s console=%s",
            log_path, file_level_name, console_level,
        )
    return log_path


def get_log_path() -> Path:
    """Return the path of the active rotating log file."""
    root = logging.getLogger()
    for h in root.handlers:
        if isinstance(h, RotatingFileHandler):
            return Path(h.baseFilename)
    return Path("trading_bot.log").resolve()
