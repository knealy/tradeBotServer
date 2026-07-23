# Exit-policy / false-breakout R1 — results (2026-07-23)

## MRR time-stop / scratch (450d / 9f, MNQ+MGC)

| rank | label | ret% | dd% | RF | WR | n |
|------|-------|------|-----|----|----|---|
| 1 | **baseline_mh46** | **+299.5** | 42.9 | **3.41** | 66% | 270 |
| 2 | mh15 | +154.9 | 48.5 | 2.53 | 60% | 252 |
| 3 | scratch12_r05 | +130.8 | 38.7 | 1.95 | 60% | 245 |
| … | mh8 / scratch8_r05 | **negative** | ~50 | <0 | 55% | 223 |

**Verdict: do not tighten.** Hard `max_hold` / “need +0.5R by bar 8” clips slow winners
that resolve after the 16+ loser bucket. Leave `max_hold_bars=46`, `scratch_*=0`.
Use **holding-time monitor + fast gate** for longevity instead of exit surgery.

Artifacts: `docs/perf/_opt_runs/morning_range_reversion/exit_policy_r1/`

## OR false-breakout (450d / 9f)

| label | ret% | dd% | RF | WR | n |
|-------|------|-----|----|----|---|
| **atr_min_012** | **+90.2** | **23.0** | **2.08** | 40% | 152 |
| atr_min_010 | +80.0 | 37.3 | 1.84 | 37% | 171 |
| baseline | +79.3 | 39.0 | 1.83 | 36% | 172 |
| scratch3_r0 | +72.6 | 45.1 | 1.57 | 34% | 170 |

**Scratch underwater by bar 2–3: hurts** (cuts building breakouts).  
**`filters.atr_min_pct = 0.12`**: best on **450d** — fewer dead-range whipsaws,
DD −16pp, RF↑. Cross-check on **90d/270d ending 2026-07-23**: noop (identical to
baseline) while OR itself is deeply negative on those windows — the filter
won't revive a faded recent regime. Optional TOML commit for longspan quality
only; do not treat as a fix for Jun–Jul OR bleed.

## Tooling shipped

- `scripts/conditional_expectancy_report.py`
- `scripts/holding_time_monitor.py` (MGC MRR already **ALERT** at end of Jun 2026, rolling avg ~22 bars)
- Richer `trades_flat` in walkforward insights
