# Walk-forward strategy competition

Head-to-head **replay** matrix for **`body_reversion`**, **`morning_range_reversion`**, and **`overnight_range`** on the same calendar folds.

## Prerequisites

- Canonical 5m CSVs: `historical_data/price/MNQ_5m_databento.csv`, `MES_5m_databento.csv`, `MGC_5m_databento.csv` (naive UTC bar open; see repo **BACKTESTING** doc for resample/stitch).
- `ENABLE_SIGNALR=false` (the script sets this for subprocesses).

## Run

```bash
cd /path/to/tradeBotServer
ENABLE_SIGNALR=false .venv/bin/python scripts/walkforward_strategy_competition.py \
  --days 100 --folds 5 --timeframe 5m \
  --strategies body_reversion,morning_range_reversion,overnight_range \
  --symbols MNQ,MES,MGC
```

- **`--dry-run`**: print fold date ranges only (no backtests).
- **`--out-dir`**: override output directory (default `docs/perf/walkforward_competition/`).
- **`--csv-template`**: default `{csv_dir}/{sym}_5m_databento.csv` with `{sym}` lowercased.

## Outputs

| Path | Purpose |
|------|---------|
| `summary.tsv` | One row per (strategy, symbol, fold): `total_pnl`, `total_trades`, `win_rate`, `sharpe_ratio`, `max_drawdown`. |
| `leaderboard.md` | Ranked by **sum of fold `total_pnl`** per (strategy, symbol); includes folds with positive PnL count. |
| `runs/*.json` | Raw `backtest_executor --format=json` output per job. |

## Interpretation

- **Different trade counts** across strategies are expected (e.g. **overnight_range** is sparse by design; **body_reversion** is event-gated; **morning_range_reversion** needs sweep + re-entry).
- **Live timing fixes** (`_in_trading_window` in session TZ) do **not** change replay when **`_is_strategy_replay`** is true; they align the **strategy executor** with the same clocks used in bar logic so live vs CSV replay diverge less.
