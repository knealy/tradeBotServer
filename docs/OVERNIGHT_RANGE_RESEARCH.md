# Overnight range — research and backtest workflow

Use this page as the operator checklist when tuning **`overnight_range`** for profitability. The blueprint lives in [`STRATEGY_IMPROVEMENT_PLAN.md`](STRATEGY_IMPROVEMENT_PLAN.md) (Phase 1 filters, weekday skip, Phase 2 ATR grid, etc.).

## 0. Trade frequency — why a few trades in months can be “normal”

**`overnight_range` is not a high‑frequency system.** In CSV replay (and in live near open), you get **at most one bracket placement attempt per symbol per session** in the configured open window — not one trade per bar. Rough upper bound on *sessions where a signal can exist*:

- **Calendar days** in the window × **symbols** you run (often 1 in replay).
- **`skip_weekdays`** — live TOML is currently **P5**-aligned: **`[]`** (all weekdays). If you set **`[0, 4]`** again, only **Tue / Wed / Thu** count → multiply by **~3/7** (about **40%** fewer days than “every day”).
- **`[filters]`** (range size, gap, volatility) → each **enabled** filter removes a slice of sessions in **`check_market_conditions`** before any order is sent. With all three on and tight bands, it is common for **well under half** of remaining sessions to pass.
- **Holidays / thin data / range too small or too large** → more skips.

So **single‑digit trades over a few months** with the current Phase‑1‑style TOML is **often expected**, not a bug in the replay engine. **Win rate and Sharpe on 4 completed trades are not statistically meaningful** for prop sizing — treat them as smoke tests until you have **dozens of sessions** (or use [`ALPHA_DISCOVERY.md`](ALPHA_DISCOVERY.md) session splits and OOS rules).

**If your goal is more “monthly cashflow” frequency:**

1. **Relax or A/B filters** — turn **`range_size` / `gap` / `volatility`** off one at a time, or widen **`range_*`**, **`gap_max_pts`**, **`atr_*`**; compare trade count and *out‑of‑sample* stats (see [`BACKTEST_RESEARCH.md`](BACKTEST_RESEARCH.md)).
2. **Weekdays** — set **`skip_weekdays = []`** for research runs (Mon/Fri back in); keep Mon/Fri off for live only if data supports it.
3. **Different product** — add or weight another strategy for mid‑week intraday edges; OR alone will rarely match “daily” scalping frequency.
4. **Session scan** — run [`scripts/alpha_discovery.py`](../scripts/alpha_discovery.py) on 5m CSV to see which features correlate with R *before* tightening filters again.

Commented “looser research” toggles live in [`config/strategies/overnight_range.toml`](../config/strategies/overnight_range.toml) under **`[filters]`**.

**Full operator map** (full-range vs chunk JSON, multi-symbol export, Pathway 1 / 2 checklists): [`RESEARCH_PATHWAYS.md`](RESEARCH_PATHWAYS.md).

## 1. Data

1. Export or merge 1m (or native) OHLCV for the symbol; timestamps should be **naive UTC** in stitched archives (see [`BACKTESTING.md`](BACKTESTING.md) “Timestamps”).
2. Optional: resample to 5m for lighter grids and session-style research:

   ```bash
   .venv/bin/python historical_data/resample_ohlcv_csv.py \
     --input historical_data/price/MNQ_1m_complete.csv \
     --output historical_data/price/MNQ_5m.csv \
     --rule 5min
   ```

## 2. Strategy CSV replay (sanity + regime checks)

[`scripts/backtest_overnight_csv.sh`](../scripts/backtest_overnight_csv.sh) wraps **`core/backtest_executor.py`** with **`--strategy=overnight_range`** and **`--csv=…`**.

```bash
START=2026-01-01 END=2026-01-14 bash scripts/backtest_overnight_csv.sh
```

**Full-archive chunked batch** (same calendar idea as chunked export; default **30** inclusive days per run, JSON summary):

```bash
.venv/bin/python scripts/run_overnight_range_csv_chunks.py \
  --csv historical_data/price/MNQ_1m_complete.csv --chunk-days 30 --timeframe 1m --no-print-trades
```

Output: **`docs/perf/overnight_range_MNQ_1m_chunks.json`** (each chunk may include a **`trades`** array; top-level **`all_trades`** lists every round-trip with a **`chunk`** span). Stdout prints per-chunk **`trades=`** / **`pnl=`** summary lines; omit **`--no-print-trades`** if you want full trade tables on the terminal. Use **`--omit-trades`** for a slimmer JSON only.

**Regenerated 2026-04-29** with current replay logic; rollup **`sum_total_trades` = 4**, **`sum_total_pnl` ≈ 238.30** (matches a single full-span `backtest_executor` CSV run for the same archive — see [RESEARCH_PATHWAYS.md](RESEARCH_PATHWAYS.md) §A). Re-run the command above after strategy or replay changes so this file stays comparable to [response.json](../response.json)-style runs.

The backtest CLI flag **`--include-trades`** (with **`--format=json`**) is implemented in **`core/backtest_executor.py`** for any replay run.

### 2.1 Formatting JSON output

One-line JSON is hard to read. Options:

**Built-in Python** (whole document, indented):

```bash
python3 -m json.tool < response.json
# or pipe directly from the backtest (stderr may still show logs unless redirected)
.venv/bin/python core/backtest_executor.py ... --format=json --include-trades 2>/dev/null | python3 -m json.tool
```

**`jq`** (if installed) — summary without the large `trades` array:

```bash
jq 'del(.result.trades)' response.json
```

**Repo helper** [`scripts/format_backtest_json.py`](../scripts/format_backtest_json.py) — reads stdin, a path, or `-`; tolerates extra noise by taking the last `{...}` line if needed.

```bash
# Pretty full document (default)
.venv/bin/python scripts/format_backtest_json.py docs/perf/overnight_range_MNQ_1m_chunks.json

# Overview only (drops trades for readability)
.venv/bin/python scripts/format_backtest_json.py --summary response.json

# Trades only, pretty JSON array
.venv/bin/python scripts/format_backtest_json.py --trades-only response.json

# CSV → open in Excel / `pandas.read_csv`
.venv/bin/python scripts/format_backtest_json.py --trades-csv response.json > trades.csv

# Markdown table (e.g. paste into GitHub / Notion)
.venv/bin/python scripts/format_backtest_json.py --trades-md response.json
```

For **`overnight_range_MNQ_1m_chunks.json`**, each *chunk* has its own `trades` list; use `jq '.chunks[] | {start, end, trades}' file.json` or a short Python snippet if you need one CSV across all chunks (the chunk runner also writes **`all_trades`** with a **`chunk`** field).

### 2.2 Trade review charts (historic candles + entry/exit)

Use real 1m (or other) OHLCV from your export together with a backtest JSON that includes **`trades`** (or **`all_trades`** from the chunk runner).

**Generate one Lightweight Charts HTML per trade** (markers at entry/exit + horizontal entry/exit price lines) and optional **matplotlib** PNGs (close price + vertical lines — quick snapshots, not a pixel-perfect LWC capture):

```bash
.venv/bin/python scripts/render_trade_review_charts.py \
  --json response.json \
  --csv historical_data/price/MNQ_1m_complete.csv \
  --out-dir docs/perf/trade_review \
  --padding-minutes 240 \
  --png
```

From the chunked perf file (note repeating **`trade_id`** across chunks — output filenames include the **`chunk`** span when present):

```bash
.venv/bin/python scripts/render_trade_review_charts.py \
  --json docs/perf/overnight_range_MNQ_1m_chunks.json \
  --trade-id T000001 \
  --png
```

Open each **`.html`** in Chrome/Safari/Firefox. The page uses the same **`generate_chart_html`** stack as [`gui/chart_html.py`](../gui/chart_html.py): full candle window is loaded immediately (**`backtest=False`** on these exports). **Bar-by-bar replay** for arbitrary embedded HTML is still the chart’s **“Start Backtest”** mode when `generate_chart_html(..., backtest=True)` is used elsewhere (e.g. live bot chart); trade overlays are also applied after **“Load All Bars”** in that mode so markers appear once the series is filled.

Replay mode sets **`trading_bot._is_strategy_replay`**. The strategy then:

- Evaluates **`analyze()`** for bracket placement in CSV replay only on/after **`timing.market_open`** (US/Eastern for that session date). When **`timing.replay_order_window_minutes > 0`**, only that many minutes after open are considered (legacy sparse replay). When **`<= 0`**, there is **no post-open minute cap** — bars from **open through the end of the CSV slice** may still trigger the one-shot session attempt (via **`_replay_sessions_signaled`**), but **never before** `market_open`.
- Honors **`filters.skip_weekdays`** (Python weekday: Monday **0**, Friday **4**) using the **current bar** clock in replay.

Knobs live in **`config/strategies/overnight_range.toml`** (hot-reload under the executor). Compare metrics before/after each filter change and record a dated row in [`BACKTEST_RESEARCH.md`](BACKTEST_RESEARCH.md) if you keep a lab notebook there.

## 3. Session-level modeling (filters / hypotheses)

For feature scans and IS/OOS splits by **session** (not raw bar count), use [`scripts/alpha_discovery.py`](../scripts/alpha_discovery.py) and [`docs/ALPHA_DISCOVERY.md`](ALPHA_DISCOVERY.md). Prefer **5m** CSV for speed when the harness does not require 1m precision.

## 4. Parameter grids (stop / TP ATR multipliers)

Use **`python -m core.research.runner --help`** — optional **`--csv`**, **`--csv-start`**, **`--csv-end`** for exported bars (see [`BACKTEST_RESEARCH.md`](BACKTEST_RESEARCH.md)).

## 5. Live vs replay

CSV replay still uses a simplified fill and bracket model versus TopStepX live behavior. Use replay for **relative** comparisons (same CSV, same date window) and **`alpha_discovery`** for session-level structure; promote changes only after a small **paper** or **reduced-size** live window if possible.

**Session range in replay** — each day’s **`track_overnight_range()`** must drive brackets (``active_ranges`` alone is not enough across calendar days). The replay engine also cancels the opposite staged **entry** stop when one side fills so long+short breakout stops do not stack across sessions.
