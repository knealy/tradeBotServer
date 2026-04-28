# Database

## Code of record

- **`infrastructure/database.py`** — connection pool, schema touches, `cleanup_old_data`, async batch writers for `api_metrics`, `strategy_executions`, `notifications` (env-tunable; see `.env.example`).

## Reference material

- [reference/DATABASE_ARCHITECTURE.md](reference/DATABASE_ARCHITECTURE.md) — table-oriented notes.

## Ops

- Nightly prune (~03:30 ET) when the webhook process runs [servers/scheduled_tasks.py](../servers/scheduled_tasks.py) (telemetry retention via `DB_TELEMETRY_RETENTION_DAYS`).
