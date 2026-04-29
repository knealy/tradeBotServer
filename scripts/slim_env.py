#!/usr/bin/env python3
"""Rewrite .env to allowed infra keys only (strategy knobs belong in config/strategies/*.toml)."""
from __future__ import annotations

import sys
from pathlib import Path

# Keys the runtime still reads from the environment (see docs/ENV_VARS.md).
ALLOWED = frozenset(
    {
        "PROJECT_X_API_KEY",
        "PROJECT_X_USERNAME",
        "PROJECT_X_ACCOUNT_ID",
        "TOPSTEPX_API_KEY",
        "TOPSTEPX_USERNAME",
        "TOPSTEPX_ACCOUNT_ID",
        "TOPSETPX_API_KEY",
        "TOPSETPX_USERNAME",
        "DATABASE_URL",
        "PUBLIC_DATABASE_URL",
        "POSTGRES_HOST",
        "POSTGRES_PORT",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "POSTGRES_DB",
        "JWT_TOKEN",
        "DISCORD_WEBHOOK_URL",
        "DISCORD_BOT_TOKEN",
        "DISCORD_COMMAND_CHANNEL_ID",
        "DISCORD_COMMAND_ALLOWED_USER_IDS",
        "LOG_LEVEL",
        "ENABLE_FILE_LOGGING",
        "API_TIMEOUT",
        "MAX_RETRIES",
        "API_RATE_LIMIT_MAX",
        "API_RATE_LIMIT_PERIOD",
        "ENABLE_SIGNALR",
        "STRATEGY_TIMEZONE",
        "HEALTH_CHECK_ENABLED",
        "HEALTH_CHECK_INTERVAL",
        "WEBHOOK_TIMEOUT",
        "MAX_WEBHOOK_SIZE",
        "REMOTE_COMMAND_SECRET",
        "INITIAL_BALANCE",
        "DAILY_LOSS_LIMIT",
        "MAXIMUM_LOSS_LIMIT",
        "GLOBAL_MAX_POSITIONS",
        "MAX_CONCURRENT_STRATEGIES",
        "AUTO_SELECT_STRATEGIES",
        "REGISTER_STRATEGIES",
        "STRATEGY_CONFIG_RELOAD",
        "MARKET_CONDITION_CHECK_INTERVAL",
        "TOPSTEPX_USE_RUST",
        "RUST_HTTP_TIMEOUT_SECONDS",
        "PREFETCH_ENABLED",
        "PREFETCH_SYMBOLS",
        "PREFETCH_TIMEFRAMES",
        "USE_PROJECTX_SDK",
        "ENABLE_TESTING",
        "DEBUG_MODE",
        "DB_TELEMETRY_RETENTION_DAYS",
        "DB_ASYNC_API_METRICS",
        "DB_API_METRICS_BATCH",
        "DB_API_METRICS_FLUSH_S",
        "DB_API_METRICS_QUEUE_MAX",
        "DB_ASYNC_STRATEGY_EXEC",
        "DB_STRATEGY_EXEC_BATCH",
        "DB_STRATEGY_EXEC_FLUSH_S",
        "DB_STRATEGY_EXEC_QUEUE_MAX",
        "DB_ASYNC_NOTIFICATIONS",
        "DB_NOTIFICATIONS_BATCH",
        "DB_NOTIFICATIONS_FLUSH_S",
        "DB_NOTIFICATIONS_QUEUE_MAX",
        "HISTORICAL_PARQUET_CACHE",
        "HISTORICAL_PARQUET_DIR",
        "HISTORICAL_PARQUET_TTL_MINUTES",
        "BENCH_ACCOUNT_ID",
        "BENCH_SYMBOL",
        "BENCH_ITERS",
        "BENCH_LIVE",
        "USE_UVLOOP",
        "DISABLE_UVLOOP",
        "HUB_DEFERRED_QUEUE_MAX",
        "LOG_FILE",
        "RAILWAY_ENVIRONMENT",
    }
)


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    env_path = root / ".env"
    if not env_path.is_file():
        print("No .env found", file=sys.stderr)
        return 1
    lines = env_path.read_text().splitlines()
    out: list[str] = [
        "# Slimmed by scripts/slim_env.py — strategy parameters live in config/strategies/*.toml",
        "# See .env.example and docs/ENV_VARS.md",
        "",
    ]
    dropped = 0
    for line in lines:
        s = line.strip()
        if not s:
            continue
        if s.startswith("#"):
            continue
        if "=" not in line:
            continue
        key = line.split("=", 1)[0].strip()
        if key in ALLOWED:
            out.append(line)
        else:
            dropped += 1
    env_path.write_text("\n".join(out) + "\n")
    print(f"Wrote {env_path} ({len(out)} lines, dropped {dropped} keys)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
