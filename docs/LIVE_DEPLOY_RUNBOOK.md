<!-- Last updated: 2026-06-09 — alongside MRR live-readiness audit (commit 5af1635d5). -->

# Live Deploy Runbook — MRR + overnight_range R24

**Audience**: operator deploying `morning_range_reversion` (R28) and `overnight_range` (R24) to two TopStepX prop accounts for the first time after the 2026-06-04 backtest-engine fixes + 2026-06-09 live-readiness audit.

This is the concrete, copy-paste-able execution path. The "why" lives in [`docs/STRATEGY_ARSENAL.md`](STRATEGY_ARSENAL.md) and [`docs/PORTFOLIO_BLUEPRINT.md`](PORTFOLIO_BLUEPRINT.md); this doc is just the "do".

---

## Pre-flight (do this once, today)

1. **Confirm committed configs are intact**:

   ```bash
   cd /Users/risu/tradeBotServer
   .venv/bin/python -m pytest \
       tests/test_morning_range_reversion_smoke.py \
       tests/test_overnight_range_symbol_risk_signal_overrides.py
   # Expect: 76 passed
   ```

2. **Confirm `.env` is current** — `TOPSTEPX_USERNAME` + `TOPSTEPX_API_KEY` (or `PROJECT_X_*` aliases) populated; `DATABASE_URL` reachable; `DAILY_LOSS_LIMIT` + `INITIAL_BALANCE` set; `PORTFOLIO_DAILY_LOSS_CAP` set (default $1000 if unset).

3. **Confirm "Auto OCO Brackets" enabled on BOTH accounts** in TopStepX dashboard. Without this, `place_oco_bracket_with_stop_entry` will fail silently with a phantom orderId. **This is the #1 cause of "strategy looks like it placed an order but nothing happened" in this codebase.**

4. **Identify the two accounts you'll use** — they need to be DIFFERENT TopStepX prop accounts (different account IDs). Pick the higher-balance one for MRR (it does most of the work).

   ```bash
   # List accounts to find the --account_select indices:
   .venv/bin/python -c "
   import asyncio
   from trading_bot import TopStepXTradingBot
   async def main():
       bot = TopStepXTradingBot()
       await bot._ensure_valid_token()
       for i, a in enumerate(await bot.list_accounts(), 1):
           print(f'{i}: {a.get(\"id\")} | {a.get(\"name\")} | bal=\${a.get(\"balance\", 0)}')
   asyncio.run(main())
   "
   ```

5. **Note the timing**:
   - `morning_range_reversion` builds its anchor between **07:00 – 08:00 ET** and fades after **08:00 ET** until `flat_before = 16:00 ET`. **You can launch the wrapper script the night before** — it sleeps with a countdown until 06:53 ET.
   - `overnight_range` builds its range between **19:00 ET (prior day) – 10:00 ET** and arms the breakout monitor after **10:00 ET**. The wrapper has NO countdown — it expects to be launched well before the range window (or it'll just sit until the next session).

---

## Launch — both strategies, both accounts

Assume you've picked **account 1 for MRR** and **account 2 for overnight_range** (per the recommendation in `docs/PORTFOLIO_BLUEPRINT.md` §3).

```bash
cd /Users/risu/tradeBotServer

# Account 1 — morning_range_reversion (the powerhouse).
# Wrapper computes schedule from TOML + waits with countdown.
# Override symbols with MORNING_RANGE_SYMBOLS=... if needed (default: MNQ + MGC).
nohup bash scripts/run_morning_reversion.sh 1 \
    >> logs/account1_mrr_$(date +%Y%m%d).out 2>&1 &
echo "MRR account1 PID: $!"

# Account 2 — overnight_range R24 (Tuesday-skip on MNQ committed).
# Wrapper does NOT countdown; runs immediately.
# Override symbols with OVERNIGHT_RANGE_SYMBOLS=... if needed (default: mnq + mgc).
nohup bash scripts/run_overnight.sh 2 \
    >> logs/account2_overnight_$(date +%Y%m%d).out 2>&1 &
echo "overnight_range account2 PID: $!"
```

**Expected log markers** (search the latest log per strategy):

| Strategy | Marker | When you should see it |
| --- | --- | --- |
| MRR | `🌅 ET morning_range_reversion session start` | Once per session at ~07:00 ET |
| MRR | `📐 ET anchor range built` | Once per session at ~08:00 ET |
| MRR | `🎯 ET SHORT/LONG` | When a fade signal fires (post-08:00) |
| MRR | `⏰ fade deadline reached` | Once per session at flat_before (16:00 ET) |
| overnight_range | `🚀 Overnight range tracker started` | Once at process startup |
| overnight_range | `📐 ET overnight range built` | Once per session at ~09:30 ET |
| overnight_range | `🎯 stop_bracket order placed` | When breakout fires (post-10:00 ET) |

If you don't see these markers within their expected windows, something is wrong — check the log tail for stack traces.

---

## Monitor — first sessions vs backtest expectation

After the first 5 ET sessions accumulate, run the comparison tool:

```bash
# MRR account 1 vs the 3m walk-forward truth:
.venv/bin/python scripts/compare_live_vs_backtest.py \
    --strategy morning_range_reversion --account 1 --window 3m

# overnight_range account 2:
.venv/bin/python scripts/compare_live_vs_backtest.py \
    --strategy overnight_range --account 2 \
    --truth docs/perf/overnight_range_r24_truth_3m
```

The output is a side-by-side table per (symbol, metric) with a verdict marker:

- **✓** within ±30 % of expectation → keep running
- **⚠** 30-60 % drift → investigate, but it's plausibly noise on n < 10 trades
- **✗** >60 % drift → STOP THE STRATEGY and investigate before re-deploy

**The comparison auto-scales the truth recap (3 m / ~62 trading days) down to the live window (e.g. 5 days)**, so the `n_trades` and `total_pnl` rows are apples-to-apples. WR / RF / avg_win / avg_loss are rate-independent — they don't scale.

### What's normal in the first 10 trades?

- **MRR**: 6-8 trades over 5 sessions is typical (Tuesday/Wed/Thu run; Mon/Fri filtered). WR should sit in the 65-75 % range. Total PnL ≈ +$500 to +$1200 on the recommended symbol weights. If WR < 50 % after 5 trades, **stop and investigate** — that's a >2σ departure from R28's 68.3 % truth.
- **overnight_range R24**: 5-7 trades over 5 sessions (Tue skipped for MNQ; MGC trades more weekdays). WR around 38-45 %. Total PnL ≈ +$300 to +$800. Higher variance per-trade because of the wider TP.
- **DD%** is the most volatile early metric — a single losing day can push it past truth temporarily. Only flag DD when it crosses 1.5× truth max-DD AND the trade count is ≥ 10.

---

## Stop / restart / hot-reload

- **Stop both strategies**:

  ```bash
  bash scripts/stop_all.sh
  # or per-process:
  pkill -f "strategy_executor.py --strategy=morning_range_reversion"
  pkill -f "strategy_executor.py --strategy=overnight_range"
  ```

- **Hot-reload TOML changes** (without restart): edit `config/strategies/<strategy>.toml`, the next bar tick will re-read via `maybe_reload()`. No process restart needed.  ⚠ **Exception**: changes to `meta.symbols` require a restart (the strategy's symbol manager is bound at boot).

- **Restart everything**:

  ```bash
  bash scripts/restart_all_strategies.sh
  ```

---

## Half-day awareness

`core.market_calendar` does NOT model half-days (the 2026-06-09 audit flagged this as a known limitation). On the following dates the futures session closes at 13:00 ET — **manually stop both strategies the prior evening** and restart the next morning:

- Day after Thanksgiving (4th Fri of November)
- Christmas Eve (Dec 24)
- New Year's Eve (Dec 31 — if a weekday)
- July 3 (if a weekday)
- Day before MLK Day (sometimes)

A future improvement is to add `skip_dates` to the TOMLs; out of scope for the initial deploy.

---

## Portfolio breaker behaviour

The `PortfolioDailyBreaker` (env `PORTFOLIO_DAILY_LOSS_CAP`, default $1000) subscribes per-bot-process to `EventType.TRADE_CLOSED`. **Critical detail**: since you're running TWO separate `core/strategy_executor.py` processes (one per account), the breaker is **per-process**.  Effective combined cap = 2 × $1000 = $2000.

If you want a portfolio-wide cap across BOTH accounts:
- Run both strategies inside a single dashboard process (`servers/start_async_webhook.py` boots `TopStepXTradingBot` and `StrategyManager` together); or
- Lower `PORTFOLIO_DAILY_LOSS_CAP` to $500 per process so 2 × $500 = $1000 combined ceiling.

Trip behaviour (on either / both accounts):
1. `flatten_all_positions(interactive=False)` flushes the open positions on the affected account
2. `EventType.PORTFOLIO_KILL` is published — `StrategyManager` disables every active strategy in that process
3. Strategies stay disabled until the next ET session rollover (18:00 ET CME futures globex day boundary)
4. Operator action: investigate the log for `PORTFOLIO BREAKER TRIPPED`; check whether the day's losses are consistent with a regime shift or a bug

---

## First-week checklist

- [ ] Day 1: 1 session each, smoke comparison passes (✓ on all rows)
- [ ] Day 3: ≥ 3 trades each, comparison still ✓
- [ ] Day 5: First full `compare_live_vs_backtest.py` run, all ✓
- [ ] Day 10: ≥ 10 trades per strategy, look at drift trends (run comparison daily)
- [ ] Week 2: Decide whether to scale up (`position_size` 2× → 3× MNQ, etc.) based on confidence

If anything trips ✗ at any checkpoint, stop. Re-deploy ONLY after the comparison comes back ✓ on a fresh paper / staging trial of the changed config.
