# TopStepX trading bot

Personal / operator-focused futures stack for **TopStepX (ProjectX)** accounts: REST + SignalR, Postgres, modular strategies, optional Railway dashboard.

## Read this first

| Order | Doc |
|--------|-----|
| 1 | [AGENTS.md](AGENTS.md) — golden rules, entrypoints, what not to do |
| 2 | [docs/HANDOFF.md](docs/HANDOFF.md) — lifecycle and mental model |
| 3 | [docs/MAP.md](docs/MAP.md) — annotated module map |
| 4 | [docs/PLAYBOOK.md](docs/PLAYBOOK.md) — start/stop, deploy, triage |

Full topic index: [docs/README.md](docs/README.md).

**Master dashboard** (`python trading_bot.py master` / `gui`): overnight-range lines for a **separate** `strategy_executor` process use `process_states.metadata.or_ranges` (embedded on each executor heartbeat) and/or `strategy_states.settings.or_ranges` (throttled DB snapshot from the strategy). The chart requests details with the **selected GUI account** so OR levels match the account you are viewing. Chart toolbar **Activity**, **Performance**, **Strategies**, **Terminal**, and **Tradeslog** open small account-scoped popout panels. **Strategy `max_pending`** (`core/risk_management.py`) counts **`stop_bracket`** entry legs that are still risk-relevant, including **`SUSPENDED`** when the broker reports brackets that way; it does **not** count terminal or fully filled legs. The chart shows **last close · bar countdown** on a price-aligned overlay (LWC does not allow custom text inside its native scale labels). **Theme** in the top bar opens session-only color/hex overrides plus **seven named presets** (reference palettes + **Lavender house**; includes **`--chart-canvas-bg`** for the chart pane / LWC background, default black; `sessionStorage`). Lightweight Charts tuning: [`docs/CHART_LIGHTWEIGHT_OPTIONS.md`](docs/CHART_LIGHTWEIGHT_OPTIONS.md); chart JSON: [`config/chart_theme.template.json`](config/chart_theme.template.json) (`python scripts/init_chart_theme_template.py`). **Master page** (CSS variables / colors): [`docs/PAGE_THEME_OPTIONS.md`](docs/PAGE_THEME_OPTIONS.md), [`config/page_theme.template.json`](config/page_theme.template.json), optional gitignored `config/page_theme.json`, `GET /api/chart/theme/page`, `python scripts/init_page_theme_template.py`.

## Backtesting — where the data comes from

Examples that use **`--sample`** never touch disk: they call `HistoricalDataLoader.get_sample_data()` in [`core/backtest/data_loader.py`](core/backtest/data_loader.py), which **generates a synthetic random-walk** OHLCV series in memory (useful for plumbing / CI, **not** real price action).

| Source | How | Real market data? |
|--------|-----|-------------------|
| **Synthetic** | `--sample` on `core/backtest_executor.py`, research runner synthetic stubs (`ma_crossover`, …), `MODE=sample` in scripts below | No |
| **Research replay** | `python -m core.research.runner --strategy overnight_range` (and other live strategy ids) uses the same strategy classes as live via replay — not the synthetic stubs | Yes if data source is API/CSV |
| **TopStepX API** | Omit `--sample` and `--csv`; executor authenticates and pulls history via the broker adapter (needs `.env` credentials). Or: `python scripts/export_history.py …` | Yes |
| **CSV file** | `bash scripts/fetch_history_csv.sh …` then `MODE=csv CSV=…/file.csv bash scripts/backtest_symbol.sh` | Yes, if you exported from the API (or compatible format) |

Streamlined scripts (repo root):

```bash
# Quick single run (synthetic)
SYMBOL=MNQ TIMEFRAME=5m DAYS=60 STRATEGY=ma_crossover bash scripts/backtest_symbol.sh

# List + single run + research grid / OOS / MC (synthetic by default)
SYMBOL=MNQ TIMEFRAME=5m DAYS=45 bash scripts/backtest_thorough_symbol.sh

# Export real bars, then backtest from file
bash scripts/fetch_history_csv.sh --symbol MNQ --timeframe 5m --days 90 --output historical_data/MNQ_5m.csv
MODE=csv CSV=historical_data/MNQ_5m.csv STRATEGY=ma_crossover bash scripts/backtest_symbol.sh

# Live API history directly (no CSV)
MODE=api SYMBOL=MNQ DAYS=30 STRATEGY=ma_crossover bash scripts/backtest_symbol.sh
```

Exported `*.csv` files are **gitignored** (see [.gitignore](.gitignore)); keep them locally or point `CSV=` at a path outside the repo.

**Calendar gaps** — export the missing window with the **same** `--timeframe` as your series, then merge (list files oldest → newest; duplicates keep the last file’s row):

```bash
bash scripts/fetch_history_csv.sh --symbol MNQ --timeframe 5m --start 2026-03-09 --end 2026-04-07 \
  --chunk-days 7 --output historical_data/price/MNQ_5m_gapfill.csv
python historical_data/csv_merger.py part_before.csv historical_data/price/MNQ_5m_gapfill.csv part_after.csv \
  -o historical_data/price/MNQ_5m_stitched.csv
```

**Mixed intervals (e.g. 1m archive + 5m recent)** — one CSV should be one bar size. Downsample the finer file, then merge:

```bash
python historical_data/resample_ohlcv_csv.py -i historical_data/price/merged.csv --to 5m \
  -o historical_data/price/merged_5m.csv
python historical_data/csv_merger.py historical_data/price/merged_5m.csv historical_data/price/MNQ_5m.csv \
  -o historical_data/price/MNQ_5m_unified.csv
```

[`historical_data/csv_merger.py`](historical_data/csv_merger.py) warns when median bar spacing differs sharply between inputs.

More flags, JSON output, and replay: [docs/BACKTESTING.md](docs/BACKTESTING.md), workflow tree: [docs/STRATEGY_DEVELOPMENT.md](docs/STRATEGY_DEVELOPMENT.md). **Trade review charts** (LWC HTML + optional matplotlib PNG from CSV + `--include-trades` JSON): [docs/OVERNIGHT_RANGE_RESEARCH.md](docs/OVERNIGHT_RANGE_RESEARCH.md) §2.2, `scripts/render_trade_review_charts.py`.

## Requirements

- **Python** 3.11+ (Dockerfile targets 3.12)
- **PostgreSQL** — `DATABASE_URL` (Railway or local)
- **TopStepX API key + username** — see [.env.example](.env.example)

## Quick start

1. `cp .env.example .env` and set `PROJECT_X_API_KEY`, `PROJECT_X_USERNAME`, `DATABASE_URL`.
2. **Strategy behavior** — edit `config/strategies/<name>.toml`, not long `OVERNIGHT_*` / `TREND_*` blocks in `.env`.
3. **Shrink an existing `.env`** — `python scripts/slim_env.py` keeps only infra keys allowed by that script; anything removed must live in TOML or be added back manually if you truly need it as env.
4. Run strategies: `python core/strategy_executor.py --strategy=<id> --account_id=...` (wrappers: [scripts/run_overnight.sh](scripts/run_overnight.sh), [scripts/start_all.sh](scripts/start_all.sh)).
5. Interactive CLI: `python trading_bot.py`.
6. Dashboard / webhook (e.g. Railway): `python servers/start_async_webhook.py` — see [Procfile](Procfile), [railway.json](railway.json).

## How processes fit together

```mermaid
flowchart LR
  subgraph local [Local / VPS]
    ex[core/strategy_executor.py]
    bot[TopStepXTradingBot]
    ad[brokers/topstepx_adapter]
    ex --> bot --> ad
  end
  subgraph cloud [Optional Railway]
    wh[servers/start_async_webhook.py]
    wh --> bot
  end
  ad --> API[TopStepX REST]
  ad --> SR[SignalR hubs]
  bot --> DB[(Postgres)]
```

## Current status

| Area | Status |
|------|--------|
| `trading_bot.py`, `core/strategy_executor.py` | Active |
| `brokers/topstepx_adapter.py`, market + user hubs | Active |
| Postgres + async batch writers | Active |
| Dashboard (`servers/start_async_webhook.py`) | Active |
| Strategy config | `config/strategies/*.toml` + [core/strategy_config.py](core/strategy_config.py); optional RTH companion: `simple_rth` |
| Browser UI | Pre-built SPA in `static/dashboard/` (no Node stage in Docker); optional unified chart UI [`gui/master_control.html`](gui/master_control.html) (served with chart server) — header checkboxes show/hide panels; chart fullscreen + next-bar countdown; OR high/low when `overnight_range` is active; bar bucket matches selected timeframe ([`docs/DASHBOARD.md`](docs/DASHBOARD.md)) |
| Rust hot path (`TOPSTEPX_USE_RUST`) | Optional / partial — profile first; see [docs/perf/OPERATIONS_TUNING.md](docs/perf/OPERATIONS_TUNING.md) |

## Configuration

- **Secrets + infra:** `.env` (never commit). Template: [.env.example](.env.example). Details: [docs/ENV_VARS.md](docs/ENV_VARS.md).
- **Per-strategy knobs:** `config/strategies/<strategy>.toml` — do not add new `os.getenv` in `strategies/*` ([AGENTS.md](AGENTS.md)).
- Credential aliases: `PROJECT_X_*` → `TOPSTEPX_*` → legacy typo `TOPSETPX_*`.
- **Emergency remote CLI** (Railway / automation): set `REMOTE_COMMAND_SECRET`, then `POST /api/remote_command` with header `X-Remote-Command-Secret` and JSON `{"command":"flatten"}` (same strings as [core/cli_command_parser.py](core/cli_command_parser.py)).
- **DB migrations (Alembic):** revisions under `migrations/`; baseline is a no-op because `DatabaseManager` still applies schema at startup — use Alembic for additive `ALTER`s (`alembic upgrade head` with `DATABASE_URL` set).

## Docker

[Dockerfile](Dockerfile) installs Python deps and runs `python3 servers/start_async_webhook.py`. The dashboard is **pre-built** under `static/dashboard/` ([scripts/build.sh](scripts/build.sh)).

```bash
docker build -t tradebot-local .
docker run --env-file .env -p 8080:8080 tradebot-local
```

## Tests & hygiene

```bash
pytest
bash scripts/verify_handoff.sh   # doc links + MAP drift
bash scripts/gen_map.sh         # refresh docs/MAP.md after layout changes
```

### Profiling the live hot path (strategy executor)

1. Start the executor, then capture its PID (e.g. `pgrep -f "strategy_executor.py"`).
2. Record ~60s CPU flame graph (requires [py-spy](https://github.com/benfred/py-spy) on the host):

```bash
bash scripts/profile_strategy_executor.sh <PID> 60
```

Output is written under `docs/perf/` (gitignored SVGs are fine to compare locally). Interpretation and Rust-vs-I/O guidance: [docs/perf/OPERATIONS_TUNING.md](docs/perf/OPERATIONS_TUNING.md).

**Verify locally:** with RTH or paper traffic so quotes fire, run the script above, then open the new `.svg` in a browser (flame width = time in stack). User Hub heavy work uses [core/hub_deferred_queue.py](core/hub_deferred_queue.py); the **market** hub still calls `register_quote_callback` targets **synchronously** inside `WebSocketManager.on_quote` (e.g. `trading_bot._on_websocket_quote` → bar aggregator) so tick-to-bar latency stays predictable—that path was intentionally not moved to the deferred queue.

## Security

- Never commit `.env` or tokens. If `.env` was ever shared or committed, **rotate** API keys, DB passwords, JWT, and Discord credentials.
- `scripts/slim_env.py` does not print values; it only rewrites the file.

## Disclaimer

Futures and prop evaluations involve substantial risk. This software is for educational and personal use; you are responsible for compliance, sizing, and losses.
