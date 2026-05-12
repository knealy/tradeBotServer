# Body-reversion combo-trigger audit

**Ops — income cadence:** after any `body_reversion` replay JSON from `core/backtest_executor.py`, run **`scripts/print_weekly_income.py`** on that file for a **weekly PnL table** (needs `--include-trades` on the replay for true per-week buckets; without trades it prints **average $/week** from the summary). For the **Gate A/B/C × MNQ/MES/MGC** full-window grid, use **`bash scripts/resume_body_rev_gate_ab_full.sh`** (parallel subprocesses, **`GATE_AB_JOBS`** default 3). For **custom matrices** (many symbols / env overrides / date windows), add rows to a **JSONL** file and run **`scripts/run_backtest_manifest.py`** (`config/backtest_matrices/example_body_reversion_q1.jsonl`).

Pair the **big-body anchor** with each candidate co-trigger and report
**realized R** under two execution models. R unit = stop distance.

- **Stop-only / fixed-hold (`SO`)**: stop = `0.50×ATR`, hold = `6` bars (= 30 min)
- **Triple-barrier (`BK`)**: stop = `0.50×ATR`, TP = `3.0R`, max_bars = `6`

**delta_R / delta_PF** are vs the unconditional anchor (rows tagged
*baseline*). A positive delta_R means the co-trigger genuinely amplifies
the edge; a negative delta_R means the co-trigger over-restricts to a
less-edge subset. **delta_$/signal** approximates the $ improvement per
trade (`delta_R × stop_pts × $/pt`), using the median 14-bar ATR as a
proxy for stop-dollar magnitude.

_Co-triggers with **n < 200** are dropped (too noisy)._

Curated co-trigger list:

- **VWAP**: `vwap_dev<-5bp` (LONG-only), `vwap_dev>+5bp` (SHORT-only)
- **ATR regime**: `atr_high_q4`, `atr_low_q1` (14-bar ATR quartiles)
- **Volume**: `vol_spike_2x`, `vol_spike_3x` (vs 50-bar MA)
- **Range**: `range_expand_1.5x`, `range_contract_0.5x` (vs 20-bar MA)
- **Session phase**: `rth_open_60m`, `rth_mid`, `rth_close_120m`, `eth_overnight`
- **Trend**: `below_sma20` (LONG-only), `above_sma20` (SHORT-only)
- **Oscillator**: `rsi<30` (LONG-only), `rsi>70` (SHORT-only)
- **Gap**: `gap_dn_5bp` (LONG-only), `gap_up_5bp` (SHORT-only)
- **Bollinger**: `bb_lower_touch` (LONG-only), `bb_upper_touch` (SHORT-only)
- **Follow-through**: `2bear` (LONG-only), `2bull` (SHORT-only)

## MES — big_bear_body>0.9 (LONG)

_Baseline (anchor only, n=4,864): mean_R_SO=`+0.354`, PF_SO=`1.55`, mean_R_BK=`+0.298`, PF_BK=`1.47`._

| co_trigger | n | win% | mean_R_SO | PF_SO | mean_R_BK | PF_BK | delta_R | delta_PF | delta_$/sig |
|------------|---|------|-----------|-------|-----------|-------|---------|----------|-------------|
| `(baseline — anchor only)` | 4,864 | 31.1% | +0.354 | 1.55 | +0.298 | 1.47 | +0.000 | +0.00 | +0.00 |
| `atr_high_q4 & bb_lower_touch` | 468 | 58.5% | +3.239 | 9.96 | +2.230 | 7.26 | +2.885 | +8.41 | +21.51 |
| `atr_high_q4 & range_expand_1.5x` | 699 | 62.7% | +2.732 | 10.56 | +2.202 | 8.88 | +2.377 | +9.01 | +17.72 |
| `rsi<30` | 458 | 38.2% | +2.341 | 4.88 | +1.223 | 3.04 | +1.987 | +3.33 | +14.81 |
| `range_expand_1.5x & vwap_dev<-5bp` | 972 | 48.8% | +2.012 | 5.35 | +1.452 | 4.17 | +1.657 | +3.80 | +12.35 |
| `atr_high_q4 & vwap_dev<-5bp` | 1,240 | 60.2% | +1.863 | 7.35 | +1.517 | 6.22 | +1.509 | +5.80 | +11.25 |
| `atr_high_q4 & below_sma20` | 1,371 | 58.1% | +1.682 | 6.36 | +1.400 | 5.51 | +1.328 | +4.81 | +9.90 |
| `range_expand_1.5x & below_sma20` | 1,275 | 41.3% | +1.457 | 3.68 | +1.036 | 2.91 | +1.103 | +2.13 | +8.22 |
| `atr_high_q4` | 1,624 | 53.1% | +1.394 | 4.80 | +1.202 | 4.31 | +1.040 | +3.25 | +7.75 |
| `range_expand_1.5x` | 1,407 | 40.4% | +1.337 | 3.44 | +1.015 | 2.87 | +0.982 | +1.89 | +7.32 |
| `bb_lower_touch` | 1,160 | 34.6% | +1.271 | 3.03 | +0.793 | 2.28 | +0.917 | +1.48 | +6.83 |
| `vwap_dev<-5bp` | 2,276 | 42.8% | +1.005 | 2.97 | +0.791 | 2.56 | +0.651 | +1.42 | +4.85 |
| `below_sma20` | 3,671 | 34.1% | +0.525 | 1.86 | +0.417 | 1.69 | +0.171 | +0.31 | +1.27 |
| `2bear` | 771 | 32.3% | +0.437 | 1.69 | +0.351 | 1.55 | +0.083 | +0.13 | +0.62 |
| `eth_overnight` | 4,127 | 32.5% | +0.397 | 1.64 | +0.346 | 1.56 | +0.043 | +0.09 | +0.32 |
| `rth_close_120m` | 227 | 27.8% | +0.255 | 1.35 | +0.150 | 1.21 | -0.099 | -0.20 | -0.74 |
| `vol_spike_2x` | 690 | 23.6% | +0.142 | 1.19 | +0.069 | 1.09 | -0.213 | -0.36 | -1.59 |
| `vol_spike_3x` | 341 | 18.2% | +0.016 | 1.02 | -0.025 | 0.97 | -0.339 | -0.53 | -2.53 |
| `rth_mid` | 371 | 22.4% | -0.004 | 0.99 | -0.060 | 0.92 | -0.358 | -0.56 | -2.67 |
| `range_contract_0.5x` | 654 | 24.8% | -0.255 | 0.62 | -0.080 | 0.88 | -0.609 | -0.94 | -4.54 |
| `atr_low_q1` | 1,627 | 17.3% | -0.304 | 0.63 | -0.257 | 0.68 | -0.658 | -0.92 | -4.91 |

## MES — big_bull_body>0.9 (SHORT)

_Baseline (anchor only, n=5,250): mean_R_SO=`+0.122`, PF_SO=`1.18`, mean_R_BK=`+0.089`, PF_BK=`1.13`._

| co_trigger | n | win% | mean_R_SO | PF_SO | mean_R_BK | PF_BK | delta_R | delta_PF | delta_$/sig |
|------------|---|------|-----------|-------|-----------|-------|---------|----------|-------------|
| `(baseline — anchor only)` | 5,250 | 25.6% | +0.122 | 1.18 | +0.089 | 1.13 | +0.000 | +0.00 | +0.00 |
| `atr_high_q4 & bb_upper_touch` | 273 | 48.7% | +2.880 | 7.10 | +1.675 | 4.68 | +2.759 | +5.92 | +20.57 |
| `rsi>70` | 482 | 30.7% | +1.871 | 3.76 | +0.666 | 2.00 | +1.749 | +2.58 | +13.04 |
| `atr_high_q4 & range_expand_1.5x` | 604 | 47.5% | +1.656 | 6.65 | +1.554 | 6.75 | +1.534 | +5.47 | +11.44 |
| `range_expand_1.5x & vwap_dev>+5bp` | 892 | 36.7% | +1.309 | 3.63 | +0.888 | 2.83 | +1.187 | +2.45 | +8.85 |
| `range_expand_1.5x & above_sma20` | 1,127 | 33.6% | +1.027 | 2.90 | +0.725 | 2.37 | +0.905 | +1.72 | +6.75 |
| `atr_high_q4 & vwap_dev>+5bp` | 1,137 | 45.5% | +1.017 | 4.40 | +0.982 | 4.41 | +0.895 | +3.22 | +6.67 |
| `atr_high_q4 & above_sma20` | 1,346 | 43.7% | +0.881 | 3.69 | +0.919 | 3.91 | +0.760 | +2.51 | +5.66 |
| `range_expand_1.5x` | 1,292 | 32.0% | +0.868 | 2.54 | +0.650 | 2.18 | +0.746 | +1.36 | +5.56 |
| `bb_upper_touch` | 1,107 | 25.6% | +0.761 | 2.06 | +0.256 | 1.36 | +0.639 | +0.87 | +4.77 |
| `atr_high_q4` | 1,631 | 40.9% | +0.720 | 2.89 | +0.790 | 3.14 | +0.599 | +1.70 | +4.46 |
| `vwap_dev>+5bp` | 2,829 | 30.5% | +0.371 | 1.64 | +0.287 | 1.50 | +0.250 | +0.45 | +1.86 |
| `above_sma20` | 4,135 | 26.8% | +0.208 | 1.32 | +0.153 | 1.24 | +0.086 | +0.14 | +0.64 |
| `eth_overnight` | 4,335 | 26.9% | +0.185 | 1.29 | +0.139 | 1.22 | +0.063 | +0.11 | +0.47 |
| `2bull` | 896 | 27.0% | +0.130 | 1.20 | +0.160 | 1.25 | +0.008 | +0.02 | +0.06 |
| `rth_close_120m` | 255 | 22.4% | -0.050 | 0.93 | +0.014 | 1.02 | -0.171 | -0.25 | -1.28 |
| `range_contract_0.5x` | 702 | 27.1% | -0.084 | 0.86 | +0.057 | 1.09 | -0.206 | -0.32 | -1.53 |
| `vol_spike_2x` | 645 | 21.6% | -0.138 | 0.81 | -0.119 | 0.84 | -0.260 | -0.37 | -1.93 |
| `rth_mid` | 522 | 19.9% | -0.209 | 0.73 | -0.197 | 0.74 | -0.331 | -0.45 | -2.47 |
| `atr_low_q1` | 1,732 | 16.9% | -0.236 | 0.71 | -0.296 | 0.64 | -0.358 | -0.47 | -2.67 |
| `vol_spike_3x` | 294 | 18.0% | -0.260 | 0.67 | -0.261 | 0.66 | -0.381 | -0.52 | -2.84 |

## MGC — big_bear_body>0.9 (LONG)

_Baseline (anchor only, n=6,080): mean_R_SO=`+0.428`, PF_SO=`1.68`, mean_R_BK=`+0.322`, PF_BK=`1.51`._

| co_trigger | n | win% | mean_R_SO | PF_SO | mean_R_BK | PF_BK | delta_R | delta_PF | delta_$/sig |
|------------|---|------|-----------|-------|-----------|-------|---------|----------|-------------|
| `(baseline — anchor only)` | 6,080 | 31.3% | +0.428 | 1.68 | +0.322 | 1.51 | +0.000 | +0.00 | +0.00 |
| `rsi<30` | 525 | 48.0% | +4.122 | 9.14 | +1.928 | 4.84 | +3.694 | +7.46 | +40.37 |
| `range_expand_1.5x & vwap_dev<-5bp` | 976 | 45.3% | +2.645 | 6.27 | +1.533 | 4.09 | +2.217 | +4.59 | +24.23 |
| `atr_high_q4 & bb_lower_touch` | 339 | 56.9% | +2.606 | 7.22 | +2.172 | 6.23 | +2.178 | +5.55 | +23.80 |
| `range_expand_1.5x & below_sma20` | 1,287 | 41.5% | +2.024 | 4.71 | +1.230 | 3.28 | +1.596 | +3.03 | +17.44 |
| `atr_high_q4 & range_expand_1.5x` | 526 | 55.7% | +1.958 | 6.52 | +1.893 | 6.41 | +1.530 | +4.84 | +16.72 |
| `bb_lower_touch` | 1,280 | 38.4% | +1.953 | 4.25 | +1.079 | 2.81 | +1.526 | +2.58 | +16.67 |
| `range_expand_1.5x` | 1,494 | 39.4% | +1.756 | 4.13 | +1.108 | 3.00 | +1.328 | +2.46 | +14.51 |
| `atr_high_q4 & vwap_dev<-5bp` | 1,055 | 52.3% | +1.121 | 4.18 | +1.111 | 4.18 | +0.693 | +2.50 | +7.57 |
| `atr_high_q4 & below_sma20` | 1,230 | 52.6% | +1.042 | 4.02 | +1.050 | 4.06 | +0.615 | +2.34 | +6.72 |
| `vwap_dev<-5bp` | 3,110 | 36.1% | +0.877 | 2.52 | +0.590 | 2.03 | +0.450 | +0.84 | +4.92 |
| `atr_high_q4` | 1,553 | 47.0% | +0.789 | 2.97 | +0.850 | 3.14 | +0.362 | +1.29 | +3.95 |
| `2bear` | 1,103 | 35.5% | +0.781 | 2.30 | +0.513 | 1.86 | +0.353 | +0.63 | +3.86 |
| `rth_close_120m` | 513 | 33.9% | +0.707 | 2.16 | +0.385 | 1.64 | +0.279 | +0.49 | +3.05 |
| `below_sma20` | 4,487 | 33.8% | +0.616 | 2.02 | +0.447 | 1.75 | +0.188 | +0.34 | +2.05 |
| `eth_overnight` | 4,678 | 30.8% | +0.434 | 1.68 | +0.348 | 1.55 | +0.007 | +0.01 | +0.07 |
| `rth_mid` | 720 | 33.3% | +0.298 | 1.49 | +0.181 | 1.30 | -0.130 | -0.19 | -1.42 |
| `vol_spike_2x` | 713 | 26.6% | +0.135 | 1.19 | +0.075 | 1.11 | -0.293 | -0.49 | -3.20 |
| `vol_spike_3x` | 305 | 27.9% | +0.104 | 1.15 | +0.068 | 1.10 | -0.324 | -0.53 | -3.54 |
| `atr_low_q1` | 2,009 | 21.7% | -0.001 | 1.00 | -0.006 | 0.99 | -0.428 | -0.68 | -4.68 |
| `range_contract_0.5x` | 1,061 | 26.1% | -0.281 | 0.55 | +0.046 | 1.08 | -0.709 | -1.12 | -7.75 |

## MGC — big_bull_body>0.9 (SHORT)

_Baseline (anchor only, n=6,500): mean_R_SO=`+0.455`, PF_SO=`1.72`, mean_R_BK=`+0.378`, PF_BK=`1.61`._

| co_trigger | n | win% | mean_R_SO | PF_SO | mean_R_BK | PF_BK | delta_R | delta_PF | delta_$/sig |
|------------|---|------|-----------|-------|-----------|-------|---------|----------|-------------|
| `(baseline — anchor only)` | 6,500 | 31.0% | +0.455 | 1.72 | +0.378 | 1.61 | +0.000 | +0.00 | +0.00 |
| `rsi>70` | 682 | 44.3% | +3.945 | 8.21 | +1.731 | 4.23 | +3.489 | +6.49 | +38.14 |
| `atr_high_q4 & bb_upper_touch` | 394 | 57.4% | +3.035 | 8.81 | +2.071 | 6.36 | +2.579 | +7.09 | +28.19 |
| `range_expand_1.5x & vwap_dev>+5bp` | 1,083 | 41.3% | +2.774 | 6.08 | +1.445 | 3.72 | +2.319 | +4.36 | +25.34 |
| `atr_high_q4 & range_expand_1.5x` | 512 | 54.1% | +2.346 | 7.75 | +1.942 | 6.82 | +1.891 | +6.03 | +20.67 |
| `range_expand_1.5x & above_sma20` | 1,348 | 37.4% | +2.186 | 4.77 | +1.175 | 3.07 | +1.731 | +3.05 | +18.92 |
| `range_expand_1.5x` | 1,553 | 36.4% | +1.925 | 4.29 | +1.108 | 2.95 | +1.469 | +2.57 | +16.06 |
| `bb_upper_touch` | 1,507 | 35.5% | +1.920 | 4.07 | +0.929 | 2.51 | +1.464 | +2.35 | +16.00 |
| `atr_high_q4 & vwap_dev>+5bp` | 1,176 | 53.4% | +1.383 | 5.09 | +1.260 | 4.79 | +0.928 | +3.37 | +10.14 |
| `atr_high_q4 & above_sma20` | 1,400 | 51.0% | +1.172 | 4.30 | +1.196 | 4.45 | +0.717 | +2.58 | +7.83 |
| `atr_high_q4` | 1,720 | 46.7% | +0.936 | 3.33 | +1.018 | 3.60 | +0.480 | +1.61 | +5.25 |
| `vwap_dev>+5bp` | 3,614 | 35.0% | +0.882 | 2.48 | +0.555 | 1.95 | +0.427 | +0.77 | +4.66 |
| `rth_close_120m` | 536 | 33.6% | +0.791 | 2.33 | +0.456 | 1.78 | +0.335 | +0.61 | +3.67 |
| `above_sma20` | 4,980 | 32.9% | +0.608 | 1.99 | +0.480 | 1.79 | +0.153 | +0.27 | +1.67 |
| `eth_overnight` | 4,941 | 30.3% | +0.442 | 1.69 | +0.404 | 1.64 | -0.014 | -0.03 | -0.15 |
| `2bull` | 1,326 | 32.4% | +0.436 | 1.69 | +0.361 | 1.58 | -0.019 | -0.03 | -0.21 |
| `rth_mid` | 815 | 34.1% | +0.425 | 1.70 | +0.287 | 1.47 | -0.030 | -0.02 | -0.33 |
| `vol_spike_2x` | 740 | 25.1% | +0.096 | 1.13 | +0.123 | 1.17 | -0.359 | -0.59 | -3.92 |
| `rth_open_60m` | 208 | 27.4% | +0.033 | 1.05 | -0.071 | 0.90 | -0.422 | -0.67 | -4.62 |
| `vol_spike_3x` | 294 | 22.1% | -0.012 | 0.98 | +0.113 | 1.15 | -0.467 | -0.73 | -5.10 |
| `atr_low_q1` | 1,985 | 19.1% | -0.132 | 0.84 | -0.146 | 0.82 | -0.587 | -0.88 | -6.41 |
| `range_contract_0.5x` | 1,097 | 29.4% | -0.133 | 0.76 | +0.504 | 1.95 | -0.589 | -0.95 | -6.43 |

## MNQ — big_bear_body>0.9 (LONG)

_Baseline (anchor only, n=3,290): mean_R_SO=`+0.504`, PF_SO=`1.84`, mean_R_BK=`+0.424`, PF_BK=`1.71`._

| co_trigger | n | win% | mean_R_SO | PF_SO | mean_R_BK | PF_BK | delta_R | delta_PF | delta_$/sig |
|------------|---|------|-----------|-------|-----------|-------|---------|----------|-------------|
| `(baseline — anchor only)` | 3,290 | 36.0% | +0.504 | 1.84 | +0.424 | 1.71 | +0.000 | +0.00 | +0.00 |
| `atr_high_q4 & bb_lower_touch` | 393 | 48.9% | +2.200 | 5.48 | +1.732 | 4.60 | +1.696 | +3.64 | +23.44 |
| `atr_high_q4 & range_expand_1.5x` | 557 | 55.1% | +1.977 | 6.15 | +1.752 | 5.65 | +1.473 | +4.31 | +20.36 |
| `rsi<30` | 415 | 32.5% | +1.411 | 3.10 | +0.775 | 2.17 | +0.908 | +1.27 | +12.54 |
| `atr_high_q4 & vwap_dev<-5bp` | 1,097 | 57.3% | +1.391 | 5.04 | +1.242 | 4.65 | +0.887 | +3.21 | +12.26 |
| `atr_high_q4 & below_sma20` | 1,177 | 57.3% | +1.351 | 4.89 | +1.207 | 4.51 | +0.847 | +3.05 | +11.71 |
| `range_expand_1.5x & vwap_dev<-5bp` | 878 | 40.8% | +1.300 | 3.34 | +1.029 | 2.87 | +0.796 | +1.51 | +11.00 |
| `atr_high_q4` | 1,356 | 54.1% | +1.191 | 4.09 | +1.079 | 3.83 | +0.687 | +2.26 | +9.50 |
| `range_expand_1.5x & below_sma20` | 1,124 | 37.3% | +1.032 | 2.73 | +0.820 | 2.39 | +0.529 | +0.89 | +7.31 |
| `bb_lower_touch` | 986 | 31.6% | +0.948 | 2.41 | +0.662 | 1.99 | +0.445 | +0.57 | +6.14 |
| `range_expand_1.5x` | 1,237 | 36.2% | +0.928 | 2.53 | +0.761 | 2.26 | +0.425 | +0.70 | +5.87 |
| `vwap_dev<-5bp` | 1,930 | 42.3% | +0.820 | 2.56 | +0.690 | 2.32 | +0.316 | +0.72 | +4.37 |
| `eth_overnight` | 2,533 | 38.8% | +0.647 | 2.14 | +0.550 | 1.98 | +0.143 | +0.31 | +1.98 |
| `below_sma20` | 2,634 | 38.1% | +0.601 | 2.04 | +0.510 | 1.89 | +0.098 | +0.20 | +1.35 |
| `2bear` | 670 | 32.5% | +0.352 | 1.54 | +0.286 | 1.44 | -0.152 | -0.29 | -2.10 |
| `vol_spike_3x` | 274 | 20.1% | +0.113 | 1.14 | +0.054 | 1.07 | -0.391 | -0.69 | -5.40 |
| `vol_spike_2x` | 605 | 23.6% | +0.068 | 1.09 | +0.054 | 1.07 | -0.436 | -0.75 | -6.02 |
| `rth_mid` | 397 | 29.7% | +0.049 | 1.07 | +0.042 | 1.06 | -0.454 | -0.76 | -6.28 |
| `rth_close_120m` | 224 | 25.9% | +0.012 | 1.02 | -0.033 | 0.96 | -0.492 | -0.82 | -6.80 |
| `atr_low_q1` | 698 | 24.2% | -0.012 | 0.98 | -0.023 | 0.97 | -0.516 | -0.85 | -7.13 |

## MNQ — big_bull_body>0.9 (SHORT)

_Baseline (anchor only, n=3,762): mean_R_SO=`+0.212`, PF_SO=`1.35`, mean_R_BK=`+0.257`, PF_BK=`1.43`._

| co_trigger | n | win% | mean_R_SO | PF_SO | mean_R_BK | PF_BK | delta_R | delta_PF | delta_$/sig |
|------------|---|------|-----------|-------|-----------|-------|---------|----------|-------------|
| `(baseline — anchor only)` | 3,762 | 32.4% | +0.212 | 1.35 | +0.257 | 1.43 | +0.000 | +0.00 | +0.00 |
| `atr_high_q4 & range_expand_1.5x` | 501 | 45.3% | +0.772 | 2.99 | +1.141 | 4.11 | +0.561 | +1.64 | +7.75 |
| `atr_high_q4 & bb_upper_touch` | 288 | 38.9% | +0.756 | 2.36 | +0.819 | 2.53 | +0.544 | +1.01 | +7.52 |
| `atr_high_q4 & vwap_dev>+5bp` | 1,138 | 48.6% | +0.588 | 2.82 | +0.725 | 3.31 | +0.376 | +1.47 | +5.20 |
| `atr_high_q4 & above_sma20` | 1,278 | 47.3% | +0.549 | 2.55 | +0.698 | 3.02 | +0.338 | +1.20 | +4.67 |
| `atr_high_q4` | 1,516 | 44.5% | +0.481 | 2.20 | +0.625 | 2.59 | +0.270 | +0.85 | +3.73 |
| `range_expand_1.5x & vwap_dev>+5bp` | 824 | 33.0% | +0.416 | 1.72 | +0.552 | 1.98 | +0.204 | +0.37 | +2.82 |
| `range_expand_1.5x & above_sma20` | 989 | 32.5% | +0.392 | 1.66 | +0.533 | 1.91 | +0.180 | +0.31 | +2.49 |
| `range_expand_1.5x` | 1,128 | 31.4% | +0.335 | 1.55 | +0.493 | 1.82 | +0.123 | +0.20 | +1.70 |
| `eth_overnight` | 2,776 | 34.6% | +0.305 | 1.53 | +0.359 | 1.63 | +0.093 | +0.18 | +1.29 |
| `vwap_dev>+5bp` | 2,427 | 35.8% | +0.295 | 1.54 | +0.343 | 1.63 | +0.083 | +0.19 | +1.15 |
| `bb_upper_touch` | 951 | 28.3% | +0.282 | 1.41 | +0.271 | 1.39 | +0.070 | +0.06 | +0.97 |
| `above_sma20` | 3,083 | 33.8% | +0.251 | 1.43 | +0.305 | 1.53 | +0.040 | +0.08 | +0.55 |
| `2bull` | 868 | 33.6% | +0.224 | 1.38 | +0.226 | 1.38 | +0.012 | +0.03 | +0.17 |
| `rsi>70` | 444 | 25.2% | +0.201 | 1.28 | +0.102 | 1.14 | -0.011 | -0.07 | -0.15 |
| `atr_low_q1` | 743 | 22.6% | +0.080 | 1.10 | +0.027 | 1.04 | -0.132 | -0.24 | -1.83 |
| `rth_mid` | 566 | 28.6% | +0.001 | 1.00 | +0.003 | 1.00 | -0.211 | -0.35 | -2.92 |
| `vol_spike_3x` | 254 | 18.9% | -0.071 | 0.91 | -0.017 | 0.98 | -0.283 | -0.44 | -3.91 |
| `vol_spike_2x` | 612 | 22.9% | -0.091 | 0.88 | -0.048 | 0.93 | -0.303 | -0.47 | -4.18 |
| `rth_close_120m` | 255 | 23.9% | -0.155 | 0.78 | -0.139 | 0.80 | -0.366 | -0.57 | -5.06 |

## How to read

1. **Pick co-triggers with `delta_R > 0` AND `n ≥ 500`.** Those are the
   ones that both amplify the edge and have enough population to actually
   trade.
2. **Don't pick the highest delta_R blindly** — a co-trigger that drops `n`
   from 2,500 to 220 is statistical noise even if delta_R is +0.10.
3. **Cross-instrument robustness** matters: prefer a co-trigger whose
   delta_R is positive on **MNQ AND MES AND MGC**, even if any single
   symbol has a more aggressive option.
4. **`PF_BK` < `PF_SO`** is normal: the 0.5×ATR / 3R / 6-bar bracket
   forces an early time-stop on roll-out trades that would have made the
   stop-only/fixed-hold model.

## TopStepX live (PRAC)

The audited co-triggers inform **`config/strategies/body_reversion.toml`** (v3.1 hybrid). To run that stack on a practice account (same entrypoint family as overnight range):

```bash
bash scripts/run_reversion.sh <account_select_index>
```

Uses `core/strategy_executor.py --strategy=body_reversion --symbols=MNQ,MES,MGC` with tuned `max_pending`. Enable **Auto OCO Brackets** on the account. Time-based exit after `max_hold_bars × timeframe` is implemented on the live path via `close_position`.

