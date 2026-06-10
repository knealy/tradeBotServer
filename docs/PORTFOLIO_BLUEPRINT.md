<!-- Last updated: 2026-06-09 — created alongside #1 overnight_reversion revival + #4 NR7/Globex retirement. -->

# Portfolio Blueprint — Multi-Strategy Production Deployment

**Audience**: operator deploying the current production-tier strategies to live TopStepX prop accounts.

This is the answer to the long-standing "how do I run my top strategies together?" question. The arsenal has 4 production-tier strategies, each with positive truth metrics on the corrected backtest engine. They cannot all share a single TopStepX prop account because prop accounts cannot be long and short the same instrument simultaneously. The deployment story is **one strategy per account**, with the conflict matrix and per-account risk math documented below.

For strategy-level evidence (sweep recaps, walk-forward truth, TOML rationale), see [`docs/STRATEGY_ARSENAL.md`](STRATEGY_ARSENAL.md). For day-to-day runbooks (start/stop/log drain), see [`docs/PLAYBOOK.md`](PLAYBOOK.md). For the engine-fix history that produced these numbers, see [`docs/CHANGELOG.md`](CHANGELOG.md).

---

## 1. Production tier (truth on corrected engine, 2026-06-09)

| # | Strategy | Symbols | Window (ET) | Direction | Bias | 9m Truth (ret / RF / DD / WR) | Per-day max loss¹ |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | `morning_range_reversion` | MNQ + MGC | 07:00 – 16:00 (anchor 07:00 – 08:00, fade after 08:00) | both | REVERSION | +914.6 % / 10.32 / 41.9 % / 68.3 % | $415 |
| 2 | `overnight_range` | MNQ + MGC | 09:30 + breakout monitor all session | both (post-breakout) | CONTINUATION | +133.3 % / 6.43 / 12.9 % / 40.4 % | $300 |
| 3 | `vwap_zscore_reversion` | MGC only | 09:35 – 15:30 | both | REVERSION (intraday) | +56.0 % / 0.99 / 39.0 % / 40.9 % | $180 |
| 4 | `overnight_reversion` | MNQ + MGC | 09:29 + (first 1m close outside H/L) | **long-only** | REVERSION (failed-breakout fade) | +103.1 % / 1.72 / 37.3 % / 32.6 % | $250 |

¹ Per-day max loss is the worst observed single-session realised PnL drawdown across the 9 m truth fold. These are EXISTING per-strategy `core.consec_loss_breaker` caps + position sizing; they don't include the new portfolio-level safety net (see §5).

Combined worst-case-day arithmetic (all 4 strategies hit their per-day max-loss on the same session): **≈ $1,145 / day**. This sits below the $1,200 TopStepX daily-loss-limit on a 50K Express account and well below the $2,500 cap on a 100K account, but the portfolio-level breaker at $1,000 (see §5) trips before any individual strategy can push the aggregate past that line.

---

## 2. The conflict matrix — why per-account separation is required

Strategies trade overlapping symbols in overlapping windows in opposite directions. The cells below mark whether two strategies can simultaneously go LONG vs SHORT on the SAME symbol:

| ↓ vs → | morning_range_reversion | overnight_range | vwap_zscore_reversion | overnight_reversion |
| --- | --- | --- | --- | --- |
| `morning_range_reversion` | — | **MNQ/MGC near 09:30** (REVERSION vs CONTINUATION) | MGC during RTH (both reversion, occasional direction mismatch) | **MNQ/MGC near 09:30** (sweep fade vs failed-breakout fade can disagree) |
| `overnight_range` | — | — | MGC during RTH (continuation vs reversion) | **MNQ/MGC near 09:30** (CONTINUATION vs failed-breakout REVERSION — direct opposite) |
| `vwap_zscore_reversion` | — | — | — | MGC after 09:29 (both reversion, usually aligned) |

**Hard conflicts** (bolded above) are pairs that can produce opposite directions on the SAME symbol within the SAME minute. A single TopStepX prop account cannot hold both. **Solution: one strategy per account.**

The soft conflicts (italics-equivalent — MGC during RTH where both are reversion) usually align directionally but they still consume the same per-symbol position-size slot. Running them in the same account would silently throttle one of them via `core/risk_management.StrategyRiskManager` (max_pending per symbol). Per-account separation is the same answer for these too.

---

## 3. Recommended account allocation

The user runs TopStepX with `--account_select <N>` per process (see `scripts/run_overnight.sh`, `scripts/run_morning_reversion.sh`). Each prop account is its own DLL/MLL boundary.

```
Account 1  →  morning_range_reversion          (workhorse — highest expected $)
Account 2  →  overnight_range                  (continuation diversifier)
Account 3  →  vwap_zscore_reversion (MGC only) (intraday fade, low correlation)
Account 4  →  overnight_reversion              (failed-breakout fade)
```

Rationale for that ordering:

- **#1 morning_range_reversion** is by far the highest-expected-PnL strategy (RF 10.32 on 9 m). Pair it with the most reliable / liquid account.
- **#2 overnight_range** complements #1 by capturing the OPPOSITE regime (continuation) on the same symbol set; running it on a separate account guarantees no flatten-the-other-strategy collision.
- **#3 vwap_zscore_reversion MGC-only** is the lowest position-size and lowest per-trade risk; great fit for a smaller account.
- **#4 overnight_reversion** is the newest revival (RF 1.72 on 9 m, 6 m / 3 m RF 3.38 / 2.47); structurally directionally OPPOSITE to overnight_range. **MUST be on its own account.**

If only 2 accounts are available, run **#1 + #2** (highest combined expected value, complementary regimes — both REVERSION + CONTINUATION on the same symbols guarantees the bot is positioned to profit from either regime).

If 3 accounts: add **#4** (overnight_reversion) third. It's the cleanest non-conflicting strategy after #1 + #2 on a fresh account.

The 4th slot (`vwap_zscore_reversion`) can also share the `#3 overnight_reversion` account in pinch — both are MGC-friendly and MOSTLY reversion-direction-aligned — but you accept the soft-conflict throttling risk.

---

## 4. Per-account configuration — what to change vs the defaults

The committed TOML configs (`config/strategies/<strategy>.toml`) are the **canonical truth** for each strategy. Per-account adjustments are typically restricted to `--risk-config` JSON on the `core/strategy_executor.py` command line (already done in each `scripts/run_*.sh` wrapper).

| Strategy | Wrapper script | Override that account | Symbol override |
| --- | --- | --- | --- |
| `morning_range_reversion` | `scripts/run_morning_reversion.sh <N>` | command-line arg | `MORNING_RANGE_SYMBOLS=MNQ,MGC` (default) |
| `overnight_range` | `scripts/run_overnight.sh <N>` | command-line arg | `OVERNIGHT_RANGE_SYMBOLS=mnq,mgc` (default) |
| `vwap_zscore_reversion` | None yet (use `core/strategy_executor.py` directly) | `--account_select=<N>` | `--symbols=MGC` |
| `overnight_reversion` | None yet (use `core/strategy_executor.py` directly) | `--account_select=<N>` | `--symbols=MNQ,MGC` |

Direct invocation example for the strategies without a dedicated wrapper:

```bash
# vwap_zscore_reversion on account 3 (MGC-only as committed)
nohup caffeinate -dimsu .venv/bin/python core/strategy_executor.py \
    --strategy=vwap_zscore_reversion \
    --symbols=MGC \
    --timeframe=1m \
    --account_select=3 \
    >> logs/vwap_zscore_account3_$(date +%Y%m%d).log 2>&1 &

# overnight_reversion on account 4 (MNQ + MGC, long-only + skip Mon/Fri per committed TOML)
nohup caffeinate -dimsu .venv/bin/python core/strategy_executor.py \
    --strategy=overnight_reversion \
    --symbols=MNQ,MGC \
    --timeframe=5m \
    --account_select=4 \
    >> logs/overnight_reversion_account4_$(date +%Y%m%d).log 2>&1 &
```

**Future TODO**: add dedicated `scripts/run_vwap_zscore.sh` and `scripts/run_overnight_reversion.sh` wrappers (with `caffeinate`, log paths, schedule countdown) once these strategies are deployed and the operational patterns stabilise.

---

## 5. Portfolio-level safety net

The **portfolio daily-loss breaker** (`core/portfolio_daily_breaker.py`) is the hard-stop that no single strategy can defeat. It subscribes to `EventType.TRADE_CLOSED` on the bot's event bus, aggregates realised PnL across ALL strategies / symbols / accounts on the bot process, and trips when the absolute realised daily PnL crosses the configured cap.

- **Env var**: `PORTFOLIO_DAILY_LOSS_CAP` (USD, positive number). Default `1000`. Zero disables.
- **Trip action**: calls `trading_bot.flatten_all_positions(interactive=False)` + publishes `EventType.PORTFOLIO_KILL` (which `StrategyManager` subscribes to, disabling every active strategy until the next ET session rollover at 18:00).
- **Per-account vs per-bot scope**: the breaker is **per-bot-process** (i.e. per `core/strategy_executor.py` PID). If you run 4 separate processes (one per account, as recommended in §3), each has its own breaker with its own $1000 cap by default. **For a combined portfolio cap across all 4 accounts, deploy via the master GUI / dashboard process that hosts all 4 strategies in one bot.** Otherwise the 4 × $1000 = $4000 aggregate is the de-facto ceiling.

Caveat: the per-strategy `core.consec_loss_breaker` (built-in to every strategy in the production tier) gates trade entry per (strategy, symbol, account) tuple and is independent of the portfolio breaker. Both are active in production.

---

## 6. Regime publisher — optional gate, off by default

`core.regime.RegimePublisherService` (added 2026-06-09) can publish `EventType.REGIME_UPDATE` events on every reference-symbol bar close. Strategies that want to gate on regime can subscribe.

**Status**: off by default. **No production-tier strategy currently consumes regime events.** The polling-style `classify(bars)` API in `core/regime.py` is the recommended path for backtest-friendly regime gating (the production-tier strategies all carry their own internal regime gates that are pinned by walk-forward tests; retrofitting them onto the publisher would invalidate the R28 / R24 / R2-revival tunes).

Operator opt-in:

```bash
# Enable publisher with MNQ 5m as reference; emit only on label change:
export REGIME_PUBLISHER_ENABLED=1
export REGIME_PUBLISHER_SYMBOL=MNQ
export REGIME_PUBLISHER_TIMEFRAME=5m
# Optional: emit on every bar (subscribers can dedupe themselves):
# export REGIME_PUBLISHER_ALWAYS_EMIT=1
```

Then start the bot normally — `trading_bot.ensure_regime_publisher` will lazy-construct on first strategy start (same lazy-boot path as the portfolio breaker).

---

## 7. Risk sizer — available, NOT auto-wired

`core.risk_sizer.size_for(...)` provides a fixed-dollar-risk position sizer (replaces hard-coded `position_size = N`).

**Status**: NOT wired into any production strategy.

The production-tier strategies all currently size via the committed TOML `[risk].position_size` integer (or per-symbol `[symbols.<SYM>.risk].position_size`). Switching to risk-based sizing would change the per-trade dollar exposure and **invalidate every walk-forward tune behind the current production-tier numbers** (R28 for morning_range_reversion, R24 for overnight_range, the revivals for vwap_zscore + overnight_reversion all assumed fixed-contract sizing).

The right time to enable `core.risk_sizer` is during the next round of walk-forward tuning, when the sweep harness can be configured to use the new sizer end-to-end. **Not now.** This is documented as a known limitation in `docs/CHANGELOG.md` 2026-06-09 entry; the helper is available but no strategy opts in.

---

## 8. Live deploy checklist (for any of the 4 strategies)

1. **TOML check** — `config/strategies/<strategy>.toml` has `[meta].enabled = true` and the right `meta.symbols` list. Pinning tests in `tests/test_<strategy>_smoke.py` lock the committed values; run `pytest tests/test_<strategy>_smoke.py` before deploy.
2. **Account isolation** — pick a TopStepX prop account that no other strategy is targeting (per §3). Verify the `--account_select` index resolves to that account via the master GUI account list.
3. **Risk caps** — confirm per-symbol `max_quantity` / `cooldown` / `max_pending` in the wrapper script match the TOML's `position_size` (otherwise the executor silently throttles or over-sizes).
4. **Bracket order capability** — TopStepX account must have "Auto OCO Brackets" enabled (each wrapper script reminder header documents this).
5. **Portfolio breaker** — `PORTFOLIO_DAILY_LOSS_CAP` env should be set (default $1000). For per-bot-process scope clarity, document which value is active in `logs/<process>.log` headers.
6. **First trading day** — monitor `logs/<strategy>_account<N>_<TS>.log` for `🎯 SHORT/LONG` (signal) and `📐` (anchor build) lifecycle markers. The first session should produce the expected number of trades per the strategy's TOML rationale block.
7. **Drawdown gate** — if the first 3 trading sessions cumulatively under-perform the 90 % CI of the strategy's 3 m walk-forward truth (recap dir under `docs/perf/<strategy>_*_truth_3m/`), **stop the strategy and investigate**. The truth recaps establish the expected performance band; sustained departure means either a regime shift or a bug.

---

## 9. Multi-account orchestration scripts

There's no dedicated multi-strategy-multi-account launcher script yet. The closest pattern is `scripts/start_all.sh` (which is currently a 2-process tmux template — `trading_bot.py` + `core/strategy_executor.py --all`). For the per-account-per-strategy deployment from §3, the manual pattern is one `scripts/run_*.sh <N>` per strategy:

```bash
# Production: 4 strategies, 4 accounts, 4 backgrounded processes:
nohup bash scripts/run_morning_reversion.sh 1 >> logs/account1.out 2>&1 &
nohup bash scripts/run_overnight.sh 2          >> logs/account2.out 2>&1 &
nohup .venv/bin/python core/strategy_executor.py \
    --strategy=vwap_zscore_reversion --symbols=MGC --timeframe=1m \
    --account_select=3 >> logs/vwap_zscore_account3.out 2>&1 &
nohup .venv/bin/python core/strategy_executor.py \
    --strategy=overnight_reversion --symbols=MNQ,MGC --timeframe=5m \
    --account_select=4 >> logs/overnight_reversion_account4.out 2>&1 &
```

Future operational improvement: a `scripts/start_portfolio.sh` that loops over a `(account, strategy, symbols, timeframe)` matrix and starts each as a managed background process. Out of scope for this session — the manual pattern above is fully sufficient for an operator-driven deploy.

---

## 10. Maintenance cadence

- **Quarterly**: re-run truth recaps for all 4 strategies via `scripts/walkforward_trade_recap_report.py --no-cache-decisions` and compare to the committed numbers in §1. Material drift = re-tune candidate.
- **After every engine change** to `core/backtest/{engine,strategy_replay}.py`: refresh truth recaps + audit per-strategy deltas. The 2026-06-04 engine-fix sweep is the canonical example — it flipped `body_reversion` from production to retired, revived 2 strategies, and reshaped the R28 / R24 commits.
- **Annual**: re-audit the stagnant + retired tiers. Engine changes / data refreshes can flip verdicts (the 2026-06-08 stagnant-tier audit revived two strategies that were dismissed on the buggy engine).
- **On `morning_range_reversion` or `overnight_range` TOML edit**: rerun the corresponding `_smoke.py` pinning tests + a fresh `walkforward_trade_recap_report.py --no-cache-decisions` truth recap before deploy.
