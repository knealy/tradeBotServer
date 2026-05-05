# body_reversion — v3.1 hybrid trade review (MNQ, Q1 2026)

**Window:** 2026-01-01 → 2026-05-01 · **CSV:** `historical_data/price/MNQ_5m_databento.csv` · **Timeframe:** 5m · **Config:** [`config/strategies/body_reversion.toml`](../../../../config/strategies/body_reversion.toml) — global **ATR** gate on, **range expand** off for MNQ (v3.1 hybrid).

## Performance snapshot

See [`SUMMARY.txt`](SUMMARY.txt) (JSON without the trades array). Headline: **108 trades**, win rate **~31.5%**, total PnL **~$992**, Sharpe **~1.86**, expectancy **~$9.18/tr** on $50k notional (simulated costs in executor).

**Sibling reviews:** [MES](../body_reversion_v31_hybrid_q1_2026_MES/README.md) · [MGC](../body_reversion_v31_hybrid_q1_2026_MGC/README.md). Context: [`docs/perf/sweeps/CANDIDATES.md`](../../sweeps/CANDIDATES.md) §0.

## Files in this folder

| File | Purpose |
|------|---------|
| `backtest.json` | Full `--format=json --include-trades` response from `core/backtest_executor.py`. |
| `backtest.stderr.log` | Executor stderr (if any). |
| `SUMMARY.txt` | Same blob with `trades` stripped (`scripts/format_backtest_json.py --summary`). |
| `trades.md` | Markdown table of all trades. |
| `trades.csv` | Same trades as CSV. |
| `charts/MNQ_T000001.html` … `MNQ_T000108.html` | One **Lightweight Charts** HTML per trade (entry/exit markers, ±120 minutes of 5m bars). Open in a browser. |

## Regenerate

```bash
cd /path/to/tradeBotServer
export PYTHONUNBUFFERED=1
OUT=docs/perf/trade_review/body_reversion_v31_hybrid_q1_2026_MNQ
mkdir -p "$OUT/charts"
ENABLE_SIGNALR=false .venv/bin/python core/backtest_executor.py \
  --strategy=body_reversion --symbol=MNQ --timeframe=5m \
  --csv=historical_data/price/MNQ_5m_databento.csv \
  --start=2026-01-01 --end=2026-05-01 --replay --format=json --include-trades \
  > "$OUT/backtest.json" 2> "$OUT/backtest.stderr.log"

.venv/bin/python scripts/format_backtest_json.py --summary "$OUT/backtest.json" > "$OUT/SUMMARY.txt"
.venv/bin/python scripts/format_backtest_json.py --trades-md "$OUT/backtest.json" > "$OUT/trades.md"
.venv/bin/python scripts/format_backtest_json.py --trades-csv "$OUT/backtest.json" > "$OUT/trades.csv"

.venv/bin/python scripts/render_trade_review_charts.py \
  --json "$OUT/backtest.json" \
  --csv historical_data/price/MNQ_5m_databento.csv \
  --out-dir "$OUT/charts" \
  --symbol MNQ --timeframe 5m --padding-minutes 120
```
