# TODO — Strategy

Audited **2026-07-23** (pass 4 — exit-policy R1 complete).  
Live: MRR + OR · MNQ/MGC. Regime gates coded, **all OFF**.

**Thesis:** Conditional expectancy > geometry sweeps. Holding time is the
edge signal — but **hard time-stops on MRR destroy slow winners**. Longevity
comes from *detecting* hold inflation + DLL gates, not clipping every long trade.

---

## Do next

### 1. Enable **fast gate** on live MRR only

```bash
export REGIME_FAST_GATE_ENABLED=1
export REGIME_FAST_DLL_USD=1000
```

Document in `scripts/launchd/README.md`. Leave KPI/calendar off. **Never** copy
MRR fast-gate params onto OR without an OR-specific sweep.

### 2. Optional OR commit: `filters.atr_min_pct = 0.12`

450d R1: ret +79→**+90%**, DD 39→**23%**, RF 1.83→**2.08**, cut 20 dead 0–3-bar
trades. Recent 90d/270d: **noop** (already above floor) and OR is bleeding
anyway (−182% / −335% on those windows) — filter won't revive a faded regime.
Commit only if you want the longspan quality filter locked in; otherwise leave
at 0.08 and treat OR as **secondary** to MGC-MRR.

### 3. Watch MGC MRR holding-time alert

```bash
.venv/bin/python scripts/holding_time_monitor.py \
  --insights docs/perf/<run>/metrics_insights.json \
  --symbol MGC --window 20 --alert-bars 12
```

End of Jun 2026 longspan already **ALERT** (rolling avg ~22 bars). Rising hold =
reversion slowing — size down / pause before PnL collapses.

---

## R1 evidence (done — do not re-litigate)

### MRR exit-policy sweep (450d/9f) — **baseline wins**

| label | ret% | RF | note |
|-------|------|-----|------|
| **baseline mh=46** | **+300** | **3.41** | keep |
| mh15 | +155 | 2.53 | clips slow TPs |
| scratch8 need +0.5R | **−14** | <0 | kills builders |
| mh8 | **−18** | <0 | too tight |

→ Leave `max_hold_bars=46`, `scratch_check_bars=0`. Scratch code stays for
future research; not for live TOML.

### OR false-breakout (450d/9f)

| label | ret% | dd% | RF |
|-------|------|-----|-----|
| **atr_min_012** | **+90** | **23** | **2.08** |
| baseline | +79 | 39 | 1.83 |
| scratch3 R&lt;0 | +73 | 45 | 1.57 |

→ Scratch hurts OR (winners need time). Entry ATR floor helps longspan.
Recent windows: OR edge faded — demote priority vs MGC-MRR.

Full write-up: [`docs/perf/_opt_runs/EXIT_POLICY_R1_SUMMARY.md`](docs/perf/_opt_runs/EXIT_POLICY_R1_SUMMARY.md)

---

## Tooling (shipped)

| Script | Purpose |
|--------|---------|
| `scripts/conditional_expectancy_report.py` | Expectancy by bars/exit/hour/weekday |
| `scripts/holding_time_monitor.py` | Rolling avg hold → edge-decay alarm |
| Richer `trades_flat` | risk, R inputs, MAE/MFE, fold id |

Example:
```bash
.venv/bin/python scripts/conditional_expectancy_report.py \
  --insights docs/perf/regime_longspan_450d/metrics_insights.json
```

**Best criteria going forward:** per-trade expectancy conditioned on a feature,
positive across folds — not headline return. Use the scanner before any TOML
commit.

---

## Parked

| Item | Why |
|------|-----|
| MRR max_hold / aggressive scratch | R1: baseline dominates |
| OR scratch / geometry R2 | Scratch hurts; geometry already lost to baseline |
| MRR fast-gate → OR | Untuned; OR has consec-loss breaker |
| MES revive | BE-WR trap |
| KPI + calendar live | Redundant / noop in 2026 |
| Exit-policy TOML section | Knobs exist under `signal.*`; formal section later if needed |

---

## Shipped index

Regime stack · OR thin-margin R1 · recap parity · dynamic sizing (max 15) ·
ORB close-breakout · Monte Carlo · 450d longspan · exit scratch plumbing ·
expectancy scanner · holding-time monitor.
