# Overnight range — P5 / live TOML trade review (MNQ)

**Window:** 2025-12-24 → 2026-04-30 · **CSV:** `historical_data/price/MNQ_1m_complete.csv` · **Filters:** same as [`config/strategies/overnight_range.toml`](../../../../config/strategies/overnight_range.toml) `[filters]` (P5: widened range/gap/ATR bands, `skip_weekdays = []`, range/gap/volatility on).

## Performance snapshot

See [`SUMMARY.txt`](SUMMARY.txt) (JSON without the trades array). Headline from that run: **64 trades**, win rate **~51.6%**, total PnL **~$776** on $50k notional (simulated costs in executor).

## Files in this folder

| File | Purpose |
|------|---------|
| `backtest.json` | Full `--format=json --include-trades` blob (source: [`docs/perf/sweeps/overnight_range_MNQ_P5.json`](../../sweeps/overnight_range_MNQ_P5.json) — same P5 semantics as live TOML). |
| `trades.md` | GitHub-style markdown table of all trades. |
| `trades.csv` | Same trades as CSV (Excel / pandas). |
| `charts/MNQ_T000001.html` … `MNQ_T000064.html` | One Lightweight Charts HTML per trade (entry/exit markers, padded window). Open in a browser. |

## Shell: what `2>` does

In `bash`/`zsh`, **`2`** is the file descriptor for **stderr**. **`2>/dev/null`** sends stderr to the null device (discard). **`2>file.log`** sends stderr to a log file. That keeps **stdout** (the single JSON line from `--format=json`) clean when you redirect with `> out.json`.

When redirecting JSON to a file, Python may **block-buffer** stdout, so the file stays **empty until the process exits**. For long replays, either wait for completion or use **`PYTHONUNBUFFERED=1`** so bytes flush as they are written.

## Regenerate from live TOML (optional)

```bash
cd /path/to/tradeBotServer
export PYTHONUNBUFFERED=1
ENABLE_SIGNALR=false .venv/bin/python core/backtest_executor.py \
  --strategy=overnight_range --symbol=MNQ --timeframe=1m \
  --csv=historical_data/price/MNQ_1m_complete.csv \
  --start=2025-12-24 --end=2026-04-30 --replay --format=json --include-trades \
  > docs/perf/trade_review/overnight_range_p5_live_toml_mnq/backtest.json \
  2>docs/perf/trade_review/overnight_range_p5_live_toml_mnq/backtest.stderr.log

.venv/bin/python scripts/format_backtest_json.py --summary docs/perf/trade_review/overnight_range_p5_live_toml_mnq/backtest.json > docs/perf/trade_review/overnight_range_p5_live_toml_mnq/SUMMARY.txt
.venv/bin/python scripts/format_backtest_json.py --trades-md docs/perf/trade_review/overnight_range_p5_live_toml_mnq/backtest.json > docs/perf/trade_review/overnight_range_p5_live_toml_mnq/trades.md
.venv/bin/python scripts/format_backtest_json.py --trades-csv docs/perf/trade_review/overnight_range_p5_live_toml_mnq/backtest.json > docs/perf/trade_review/overnight_range_p5_live_toml_mnq/trades.csv

.venv/bin/python scripts/render_trade_review_charts.py \
  --json docs/perf/trade_review/overnight_range_p5_live_toml_mnq/backtest.json \
  --csv historical_data/price/MNQ_1m_complete.csv \
  --out-dir docs/perf/trade_review/overnight_range_p5_live_toml_mnq/charts
```

Cross-links: [RESEARCH_PATHWAYS.md](../../RESEARCH_PATHWAYS.md), [OVERNIGHT_RANGE_RESEARCH.md](../../OVERNIGHT_RANGE_RESEARCH.md) §2.
