# `body_reversion` v3.1 hybrid — 10-day PRAC shadow plan

This is the operational runbook for running `body_reversion` v3.1 hybrid on a
**TopStepX practice (PRAC) account** while logging every fill via the new
:mod:`core.drift_monitor` so we can compare live R per trade against the
canonical Q1-2026 OOS replay before promoting to a real account.

The strategy is **already enabled in TOML** (`config/strategies/body_reversion.toml`,
`[meta] enabled = true`). The PRAC shadow does **not** require any code or
config changes — it is a process / observability change only.

## Pre-flight

1. Confirm replay reference exists:

   ```bash
   ls /tmp/body_rev_gate_ab/q1_2026/
   # Should contain body_rev_{MNQ,MES,MGC}_{A,B,C}.json with --include-trades.
   ```

   If empty / summary-only, regenerate with trades embedded:

   ```bash
   for s in MNQ MES MGC; do
     ENABLE_SIGNALR=false PYTHONUNBUFFERED=1 \
       .venv/bin/python core/backtest_executor.py --strategy=body_reversion \
         --symbol=$s --timeframe=5m \
         --csv=historical_data/price/${s}_5m_databento.csv \
         --start=2026-01-01 --end=2026-04-01 --replay \
         --format=json --include-trades \
         > /tmp/body_rev_gate_ab/q1_2026/body_rev_${s}_v31.json
   done
   ```

2. Verify income brain state file does not exist yet (clean slate per account):

   ```bash
   ls data/income_brain_<PRAC_ACCOUNT_ID>.json   # should not exist
   ```

3. Recommended `.env` overrides for the PRAC shadow:

   ```bash
   DRIFT_MONITOR=true
   DRIFT_MONITOR_LOG_DIR=logs/drift

   # Income brain — start small, sized for PRAC defaults.
   INCOME_BRAIN=true
   INCOME_BRAIN_DIVISOR=2500
   INCOME_BRAIN_CUSHION=2000
   INCOME_BRAIN_MIN_N=1
   INCOME_BRAIN_MAX_N=3            # cap N=3 during shadow
   INCOME_BRAIN_HWM_BREACH=2000
   INCOME_BRAIN_DAILY_TARGET=400   # halt new entries above +$400 today
   INCOME_BRAIN_DAILY_STOP=-300    # halt new entries below -$300 today
   ```

   `INCOME_BRAIN` is currently observability-only: the brain's `decide_size`
   is exposed via `python -m core.income_brain status --account ...` for
   sanity checking. Wiring it into the actual order placement path is the
   *next* PR — the goal here is to validate that the brain's recommendations
   match operator expectations before letting it size anything live.

## Running the shadow

```bash
# 1. Drift monitor + body_reversion on PRAC, three symbols
bash scripts/run_reversion.sh <PRAC_ACCOUNT_ID>
# (script honors DRIFT_MONITOR / INCOME_BRAIN from .env)

# 2. Confirm the monitor attached
grep "DriftMonitor attached" logs/strategy_executor*.log | tail -1

# 3. Verify fills land in the JSONL
tail -f logs/drift/body_reversion_<PRAC_ACCOUNT_ID>_*.jsonl
```

## Daily sanity checks (every session)

```bash
# 4. Income-brain snapshot
.venv/bin/python -m core.income_brain status --account <PRAC_ACCOUNT_ID>

# 5. Drift comparison vs replay (per symbol)
for s in MNQ MES MGC; do
  echo "=== $s ==="
  .venv/bin/python -m core.drift_monitor compare \
    --live  logs/drift/body_reversion_<PRAC_ACCOUNT_ID>_$(date +%Y%m%d).jsonl \
    --replay /tmp/body_rev_gate_ab/q1_2026/body_rev_${s}_v31.json \
    --tolerance-seconds 600
done
```

The comparison output shows:

- `n_matched` — how many live trades found a replay counterpart within ±10 min.
- `mean drift_R`, `median drift_R`, `p95 |drift_R|` — drift distribution.
- `% |drift_R| > 0.20` — the **kill threshold**.

## Promotion gate

After **10 trading days** (or 30 trades, whichever fires first):

- Concatenate all daily JSONLs and rerun `compare`:

  ```bash
  cat logs/drift/body_reversion_<PRAC_ACCOUNT_ID>_*.jsonl > /tmp/shadow_all.jsonl
  for s in MNQ MES MGC; do
    .venv/bin/python -m core.drift_monitor compare \
      --live /tmp/shadow_all.jsonl \
      --replay /tmp/body_rev_gate_ab/q1_2026/body_rev_${s}_v31.json
  done
  ```

- **Promote to live** only if **all** of:
  - `n_matched ≥ 20` per symbol (or aggregate ≥ 30 across symbols).
  - `% |drift_R| > 0.20` is **≤ 20 %** in aggregate.
  - `mean drift_R` is bounded in **[−0.30, +0.30]** — i.e. live is not a
    persistent loser vs replay.
  - No single trade with `drift_R < −1.0` that wasn't explained by a
    SignalR disconnect or known broker latency.

- **Halt and investigate** if any of:
  - `% |drift_R| > 0.20` exceeds 25 % — slippage / commission model wrong.
  - `mean drift_R < −0.30` — live is bleeding R the replay never showed.
  - More than half the trades are unmatched — order tagging or timestamp drift.

## Failure mode triage

| Symptom | First place to look |
|---|---|
| `n_unmatched_live` high | `customTag` does not include the strategy name (regex `^TB-<role>-<strategy>-`). Check `core.bracket_orders` `_format_order_tag`. |
| `n_unmatched_replay` high | Live missed signals that replay caught — broker rejected, risk manager blocked, or 1m intrabar fired before SignalR delivered the bar. |
| `mean drift_R < 0` | Slippage is biting more than the replay's 0.5-tick assumption. Inspect `entry_price` vs `bar.close` in the JSONL. |
| `mean drift_R > 0` (rare) | Replay is being conservative (e.g. `_resolve_both_hit_outcome` defaulting to stop). Worth confirming with `--1m-csv`. |
| Brain shows `halted_today` after 2 trades | `INCOME_BRAIN_DAILY_TARGET` / `_STOP` too tight for the strategy's daily R variance. Use `python -m core.income_brain reset` and widen. |

## What this gate does *not* validate

- **Slippage at scale** — PRAC fills are typically tighter than live retail
  fills. Once promoted, expect a 10–20 % R degradation; size accordingly.
- **Overnight session correctness** — `body_reversion` is a 5m intraday
  strategy on RTH-adjacent windows; this shadow does not exercise the
  overnight session boundary. Run `overnight_range` on the same account in
  parallel if you need that confidence.

## After promotion

1. Add a `[Unreleased]` entry in `docs/CHANGELOG.md` summarising the shadow
   results.
2. Bump `INCOME_BRAIN_MAX_N` slowly (3 → 5 → 8) only after each successive
   week's `compare` shows clean drift on the larger size.
3. Keep the shadow JSONL forever — it becomes the "drift baseline" for any
   future strategy revision (v3.2, v4) to compare against.
