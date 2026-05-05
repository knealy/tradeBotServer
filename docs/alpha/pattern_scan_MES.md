# Conditional pattern scan — MES

**CSV:** `historical_data/price/MES_1m_databento_GLBX-20260504-UDPDE7PWXR.csv`
**Window filter:** start=2024-01-01 00:00:00 end=file max
**Bars:** 825,197 × 1m → 165,682 × 5m (America/New_York)

This report is **exploratory**. Many simultaneous tests inflate false positives; only rows with **FDR q ≤ 0.05** (Benjamini–Hochberg on Fisher p-values) are highlighted as significant. Economic significance (lift, costs, slippage) still requires OOS backtests per [BACKTEST_RESEARCH.md](../BACKTEST_RESEARCH.md).

## Prior calendar-day high / low (RTH breakout → same-day re-touch)

| Metric | Value |
|--------|-------|
| `rth_sessions_break_prev_day_high` | 310 |
| `rth_sessions_revert_touch_prev_day_high` | 268 |
| `conditional_revert_rate_given_break_high` | 0.864516129032258 |
| `binomial_p_vs_50pct_revert_high` | 1.9081148996642373e-41 |
| `rth_sessions_break_prev_day_low` | 239 |
| `rth_sessions_revert_touch_prev_day_low` | 208 |
| `conditional_revert_rate_given_break_low` | 0.8702928870292888 |
| `binomial_p_vs_50pct_revert_low` | 2.2700137413451257e-33 |
| `max_bars_after` | 78 |

_Note: Reversion = touch back through prior ETH-day level same RTH date, within max_bars_after 5m bars._

**Caveat (prior-day re-touch):** Intraday paths often **recross** a prior reference level without a clean economic edge; high conditional rates can reflect path continuity and the definition of “touch,” not a standalone fade strategy. Validate with tick-aware execution and net-of-costs backtests before promoting.

## 5m feature × next-bar outcomes (Fisher independence)

| name | n(feat) | rate(feat) | n(rest) | rate(rest) | lift | p_Fisher | FDR sig |
|------|---------|------------|---------|------------|------|----------|---------|
| `inside→next_break_dn` | 16436 | 0.240 | 149246 | 0.196 | 1.223 | 9.272e-39 | ✅ |
| `inside→next_break_up` | 16436 | 0.252 | 149246 | 0.215 | 1.175 | 1.637e-27 | ✅ |
| `close<VWAP-0.05pct→next_pos_ret` | 48738 | 0.492 | 116944 | 0.466 | 1.055 | 8.428e-22 | ✅ |
| `close<SMA20→next_pos_ret` | 76513 | 0.486 | 89169 | 0.464 | 1.049 | 5.24e-20 | ✅ |
| `touch_lower_BB→next_bull` | 24194 | 0.491 | 141488 | 0.470 | 1.045 | 9.918e-10 | ✅ |
| `RSI<30→next_bull` | 6818 | 0.507 | 158864 | 0.472 | 1.076 | 8.34e-09 | ✅ |
| `close>SMA20→next_neg_ret` | 88938 | 0.460 | 76744 | 0.446 | 1.031 | 1.196e-08 | ✅ |
| `big_body>0.7_dn→next_bear` | 16283 | 0.434 | 149399 | 0.457 | 0.950 | 2.679e-08 | ✅ |
| `big_body>0.7_up→next_bull` | 17901 | 0.455 | 147781 | 0.475 | 0.958 | 3.183e-07 | ✅ |
| `vol_spike→next_bull` | 22730 | 0.488 | 142952 | 0.471 | 1.038 | 5.554e-07 | ✅ |
| `close>VWAP+0.05pct→next_neg_ret` | 68163 | 0.461 | 97519 | 0.449 | 1.026 | 2.264e-06 | ✅ |
| `vol_spike→next_bear` | 22730 | 0.468 | 142952 | 0.452 | 1.035 | 6.469e-06 | ✅ |
| `RSI>70→next_bear` | 8301 | 0.475 | 157381 | 0.453 | 1.049 | 9.109e-05 | ✅ |
| `RTH_h14→next_bull` | 6948 | 0.495 | 158734 | 0.472 | 1.048 | 0.0002539 | ✅ |
| `RTH_h9→next_bull` | 3607 | 0.498 | 162075 | 0.472 | 1.054 | 0.002273 | ✅ |
| `2bull→next_bear` | 36295 | 0.461 | 129387 | 0.453 | 1.019 | 0.003152 | ✅ |
| `2bear→next_bull` | 33559 | 0.480 | 132123 | 0.471 | 1.018 | 0.004504 | ✅ |
| `2bull→next_bull` | 36295 | 0.469 | 129387 | 0.474 | 0.988 | 0.05395 | — |
| `RSI>70→next_bull` | 8301 | 0.463 | 157381 | 0.474 | 0.978 | 0.07115 | — |
| `touch_upper_BB→next_bear` | 22382 | 0.460 | 143300 | 0.454 | 1.014 | 0.08197 | — |

### Interpretation

- **lift** = P(outcome|feature) / P(outcome|¬feature). With huge **n**, tiny lifts (e.g. 1.03) can still pass Fisher + FDR yet be **untradeable** after fees/slippage; treat |lift|−1 as effect size, not p-value alone.
- **Next steps:** promote patterns with ✅ into a dedicated strategy config + `core/backtest_executor.py --replay` grid; see [CANDIDATES.md](../perf/sweeps/CANDIDATES.md) and [ALPHA_DISCOVERY.md](../ALPHA_DISCOVERY.md).
