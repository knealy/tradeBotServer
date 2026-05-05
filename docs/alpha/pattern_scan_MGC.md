# Conditional pattern scan — MGC

**CSV:** `historical_data/price/MGC_1m_databento_GLBX-20260504-UDPDE7PWXR.csv`
**Window filter:** start=2024-01-01 00:00:00 end=file max
**Bars:** 825,389 × 1m → 165,977 × 5m (America/New_York)

This report is **exploratory**. Many simultaneous tests inflate false positives; only rows with **FDR q ≤ 0.05** (Benjamini–Hochberg on Fisher p-values) are highlighted as significant. Economic significance (lift, costs, slippage) still requires OOS backtests per [BACKTEST_RESEARCH.md](../BACKTEST_RESEARCH.md).

## Prior calendar-day high / low (RTH breakout → same-day re-touch)

| Metric | Value |
|--------|-------|
| `rth_sessions_break_prev_day_high` | 266 |
| `rth_sessions_revert_touch_prev_day_high` | 201 |
| `conditional_revert_rate_given_break_high` | 0.7556390977443609 |
| `binomial_p_vs_50pct_revert_high` | 2.42775175192697e-17 |
| `rth_sessions_break_prev_day_low` | 181 |
| `rth_sessions_revert_touch_prev_day_low` | 135 |
| `conditional_revert_rate_given_break_low` | 0.7458563535911602 |
| `binomial_p_vs_50pct_revert_low` | 2.4045764645185017e-11 |
| `max_bars_after` | 78 |

_Note: Reversion = touch back through prior ETH-day level same RTH date, within max_bars_after 5m bars._

**Caveat (prior-day re-touch):** Intraday paths often **recross** a prior reference level without a clean economic edge; high conditional rates can reflect path continuity and the definition of “touch,” not a standalone fade strategy. Validate with tick-aware execution and net-of-costs backtests before promoting.

## 5m feature × next-bar outcomes (Fisher independence)

| name | n(feat) | rate(feat) | n(rest) | rate(rest) | lift | p_Fisher | FDR sig |
|------|---------|------------|---------|------------|------|----------|---------|
| `inside→next_break_up` | 19321 | 0.281 | 146656 | 0.232 | 1.209 | 2.076e-48 | ✅ |
| `inside→next_break_dn` | 19321 | 0.258 | 146656 | 0.214 | 1.204 | 9.102e-42 | ✅ |
| `close<SMA20→next_pos_ret` | 78495 | 0.502 | 87482 | 0.480 | 1.045 | 8.262e-19 | ✅ |
| `close>SMA20→next_neg_ret` | 87331 | 0.485 | 78646 | 0.465 | 1.043 | 2.721e-16 | ✅ |
| `close<VWAP-0.05pct→next_pos_ret` | 59267 | 0.502 | 106710 | 0.483 | 1.039 | 3.184e-13 | ✅ |
| `big_body>0.7_dn→next_bear` | 17665 | 0.451 | 148312 | 0.478 | 0.944 | 1.115e-11 | ✅ |
| `close>VWAP+0.05pct→next_neg_ret` | 76248 | 0.483 | 89729 | 0.469 | 1.031 | 3.169e-09 | ✅ |
| `touch_lower_BB→next_bull` | 22624 | 0.506 | 143353 | 0.487 | 1.038 | 3e-07 | ✅ |
| `RSI<30→next_bull` | 5617 | 0.522 | 160360 | 0.489 | 1.068 | 1.085e-06 | ✅ |
| `2bear→next_bull` | 36617 | 0.501 | 129360 | 0.487 | 1.029 | 1.397e-06 | ✅ |
| `big_body>0.7_up→next_bull` | 19546 | 0.474 | 146431 | 0.492 | 0.963 | 1.424e-06 | ✅ |
| `vol_spike→next_bull` | 18172 | 0.505 | 147805 | 0.488 | 1.034 | 1.958e-05 | ✅ |
| `RSI>70→next_bear` | 7967 | 0.496 | 158010 | 0.474 | 1.045 | 0.0001781 | ✅ |
| `2bull→next_bear` | 39018 | 0.482 | 126959 | 0.473 | 1.019 | 0.001969 | ✅ |
| `touch_upper_BB→next_bear` | 23492 | 0.484 | 142485 | 0.474 | 1.022 | 0.002548 | ✅ |
| `2bull→next_bull` | 39018 | 0.484 | 126959 | 0.492 | 0.985 | 0.013 | ✅ |
| `RSI>70→next_bull` | 7967 | 0.478 | 158010 | 0.491 | 0.974 | 0.02826 | ✅ |
| `RTH_h14→next_bull` | 7100 | 0.479 | 158877 | 0.490 | 0.977 | 0.06003 | — |
| `vol_spike→next_bear` | 18172 | 0.472 | 147805 | 0.476 | 0.993 | 0.3738 | — |
| `RTH_h9→next_bull` | 3612 | 0.497 | 162365 | 0.490 | 1.015 | 0.4002 | — |

### Interpretation

- **lift** = P(outcome|feature) / P(outcome|¬feature). With huge **n**, tiny lifts (e.g. 1.03) can still pass Fisher + FDR yet be **untradeable** after fees/slippage; treat |lift|−1 as effect size, not p-value alone.
- **Next steps:** promote patterns with ✅ into a dedicated strategy config + `core/backtest_executor.py --replay` grid; see [CANDIDATES.md](../perf/sweeps/CANDIDATES.md) and [ALPHA_DISCOVERY.md](../ALPHA_DISCOVERY.md).
