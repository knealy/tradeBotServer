# Conditional pattern scan — MNQ

**CSV:** `historical_data/price/MNQ_1m_databento_GLBX-20260504-UDPDE7PWXR.csv`
**Window filter:** start=2024-01-01 00:00:00 end=file max
**Bars:** 825,387 × 1m → 165,681 × 5m (America/New_York)

This report is **exploratory**. Many simultaneous tests inflate false positives; only rows with **FDR q ≤ 0.05** (Benjamini–Hochberg on Fisher p-values) are highlighted as significant. Economic significance (lift, costs, slippage) still requires OOS backtests per [BACKTEST_RESEARCH.md](../BACKTEST_RESEARCH.md).

## Prior calendar-day high / low (RTH breakout → same-day re-touch)

| Metric | Value |
|--------|-------|
| `rth_sessions_break_prev_day_high` | 325 |
| `rth_sessions_revert_touch_prev_day_high` | 275 |
| `conditional_revert_rate_given_break_high` | 0.8461538461538461 |
| `binomial_p_vs_50pct_revert_high` | 8.642643057074886e-39 |
| `rth_sessions_break_prev_day_low` | 237 |
| `rth_sessions_revert_touch_prev_day_low` | 207 |
| `conditional_revert_rate_given_break_low` | 0.8734177215189873 |
| `binomial_p_vs_50pct_revert_low` | 1.0244073217472244e-33 |
| `max_bars_after` | 78 |

_Note: Reversion = touch back through prior ETH-day level same RTH date, within max_bars_after 5m bars._

**Caveat (prior-day re-touch):** Intraday paths often **recross** a prior reference level without a clean economic edge; high conditional rates can reflect path continuity and the definition of “touch,” not a standalone fade strategy. Validate with tick-aware execution and net-of-costs backtests before promoting.

## 5m feature × next-bar outcomes (Fisher independence)

| name | n(feat) | rate(feat) | n(rest) | rate(rest) | lift | p_Fisher | FDR sig |
|------|---------|------------|---------|------------|------|----------|---------|
| `inside→next_break_dn` | 22021 | 0.251 | 143660 | 0.210 | 1.198 | 7.59e-43 | ✅ |
| `inside→next_break_up` | 22021 | 0.266 | 143660 | 0.233 | 1.138 | 3.791e-25 | ✅ |
| `close<SMA20→next_pos_ret` | 76843 | 0.514 | 88838 | 0.495 | 1.040 | 1.448e-15 | ✅ |
| `close>SMA20→next_neg_ret` | 88752 | 0.489 | 76929 | 0.472 | 1.038 | 6.598e-13 | ✅ |
| `close<VWAP-0.05pct→next_pos_ret` | 54038 | 0.516 | 111643 | 0.498 | 1.036 | 1.147e-11 | ✅ |
| `big_body>0.7_dn→next_bear` | 15300 | 0.459 | 150381 | 0.483 | 0.949 | 5.669e-09 | ✅ |
| `close>VWAP+0.05pct→next_neg_ret` | 73752 | 0.488 | 91929 | 0.475 | 1.028 | 7.353e-08 | ✅ |
| `touch_lower_BB→next_bull` | 24192 | 0.516 | 141489 | 0.501 | 1.030 | 1.132e-05 | ✅ |
| `big_body>0.7_up→next_bull` | 17133 | 0.490 | 148548 | 0.505 | 0.971 | 0.0002736 | ✅ |
| `RSI<30→next_bull` | 7319 | 0.523 | 158362 | 0.502 | 1.041 | 0.0005491 | ✅ |
| `2bull→next_bear` | 41258 | 0.488 | 124423 | 0.479 | 1.018 | 0.001909 | ✅ |
| `RSI>70→next_bear` | 8887 | 0.497 | 156794 | 0.480 | 1.035 | 0.00233 | ✅ |
| `2bull→next_bull` | 41258 | 0.497 | 124423 | 0.505 | 0.983 | 0.003193 | ✅ |
| `2bear→next_bull` | 37684 | 0.510 | 127997 | 0.502 | 1.016 | 0.00537 | ✅ |
| `RSI>70→next_bull` | 8887 | 0.491 | 156794 | 0.504 | 0.974 | 0.01693 | ✅ |
| `vol_spike→next_bull` | 19589 | 0.508 | 146092 | 0.503 | 1.011 | 0.1339 | — |
| `touch_upper_BB→next_bear` | 22686 | 0.484 | 142995 | 0.481 | 1.006 | 0.4108 | — |
| `vol_spike→next_bear` | 19589 | 0.482 | 146092 | 0.481 | 1.003 | 0.7204 | — |
| `RTH_h14→next_bull` | 6948 | 0.505 | 158733 | 0.503 | 1.003 | 0.7874 | — |
| `RTH_h9→next_bull` | 3607 | 0.503 | 162074 | 0.503 | 0.998 | 0.9329 | — |

### Interpretation

- **lift** = P(outcome|feature) / P(outcome|¬feature). With huge **n**, tiny lifts (e.g. 1.03) can still pass Fisher + FDR yet be **untradeable** after fees/slippage; treat |lift|−1 as effect size, not p-value alone.
- **Next steps:** promote patterns with ✅ into a dedicated strategy config + `core/backtest_executor.py --replay` grid; see [CANDIDATES.md](../perf/sweeps/CANDIDATES.md) and [ALPHA_DISCOVERY.md](../ALPHA_DISCOVERY.md).
