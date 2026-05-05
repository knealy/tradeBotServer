# body_reversion — v3.1 hybrid trade review (MGC, Q1 2026)

**Window:** 2026-01-01 → 2026-05-01 · **CSV:** `historical_data/price/MGC_5m_databento.csv` · **Timeframe:** 5m · **Config:** [`config/strategies/body_reversion.toml`](../../../../config/strategies/body_reversion.toml) — global **ATR** gate on, **range expand** off for MGC (v3.1 hybrid).

## Performance snapshot

See [`SUMMARY.txt`](SUMMARY.txt). Headline: **145 trades**, win rate **~32.4%**, total PnL **~$3,321**, Sharpe **~2.21**, expectancy **~$22.90/tr** on $50k notional (simulated costs in executor).

**Sibling reviews:** [MNQ](../body_reversion_v31_hybrid_q1_2026_MNQ/README.md) · [MES](../body_reversion_v31_hybrid_q1_2026_MES/README.md). Context: [`docs/perf/sweeps/CANDIDATES.md`](../../sweeps/CANDIDATES.md) §0.

## Files in this folder

| File | Purpose |
|------|---------|
| `backtest.json` | Full `--format=json --include-trades` response. |
| `backtest.stderr.log` | Executor stderr (if any). |
| `SUMMARY.txt` | Summary without `trades`. |
| `trades.md` / `trades.csv` | All round-trips. |
| `charts/MGC_T000001.html` … `MGC_T000145.html` | Per-trade LWC HTML (±120 min of 5m bars). |

## Regenerate

```bash
cd /path/to/tradeBotServer
export PYTHONUNBUFFERED=1
OUT=docs/perf/trade_review/body_reversion_v31_hybrid_q1_2026_MGC
mkdir -p "$OUT/charts"
ENABLE_SIGNALR=false .venv/bin/python core/backtest_executor.py \
  --strategy=body_reversion --symbol=MGC --timeframe=5m \
  --csv=historical_data/price/MGC_5m_databento.csv \
  --start=2026-01-01 --end=2026-05-01 --replay --format=json --include-trades \
  > "$OUT/backtest.json" 2> "$OUT/backtest.stderr.log"

.venv/bin/python scripts/format_backtest_json.py --summary "$OUT/backtest.json" > "$OUT/SUMMARY.txt"
.venv/bin/python scripts/format_backtest_json.py --trades-md "$OUT/backtest.json" > "$OUT/trades.md"
.venv/bin/python scripts/format_backtest_json.py --trades-csv "$OUT/backtest.json" > "$OUT/trades.csv"

.venv/bin/python scripts/render_trade_review_charts.py \
  --json "$OUT/backtest.json" \
  --csv historical_data/price/MGC_5m_databento.csv \
  --out-dir "$OUT/charts" \
  --symbol MGC --timeframe 5m --padding-minutes 120
```
