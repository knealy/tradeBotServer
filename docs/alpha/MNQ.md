# Alpha Discovery Report — MNQ

**Generated:** 2026-05-03 21:25 UTC  
**Window:** 127 calendar days  
**Sessions:** 68  
**Simulated trades:** 79  
**Simulation:** stop=1.25×ATR, TP=2.0×ATR  
**Stats correction:** Benjamini-Hochberg FDR, α = 0.05

> Features with ✅ are statistically significant after FDR correction.
> Features with ⚡ are not significant but have |IC| > 0.12 — worth monitoring.

---

## Overall Simulation Performance

| Metric | Value |
|--------|-------|
| Trades | 79 |
| Win rate | 46.8% |
| Avg R | +0.027 |
| Profit factor | 1.21x |
| Total P&L | $+2,987 |
| Max drawdown | $-2,987 |
| Annualised Sharpe | 1.19 |

---

## Signal Ranking

| # | Feature | IC | p-value | Sig | Best condition |
|---|---------|----|---------|----|----------------|
| 1 | `range_position` | +0.249 | 0.0167 * | ⚡ | Q4 [0.71–1.00] → avg +0.19R |
| 2 | `breakout_side` | +0.236 | 0.0193 * | ⚡ | LONG → avg +0.11R |
| 3 | `gap_atr_ratio` | -0.234 | 0.5609 | ⚡ | Q2 [0.14–0.24] → avg +0.12R |
| 4 | `gap_abs_pts` | -0.182 | 0.5250 | ⚡ | Q2 [56.62–118.25] → avg +0.12R |
| 5 | `prior_session_win` | +0.170 | 0.1225 | ⚡ | Prior Loss/Flat → avg +0.09R |
| 6 | `dow` | +0.169 | 0.5744 | ⚡ | Wed → avg +0.14R |
| 7 | `early_range_pct` | +0.134 | 0.3135 | ⚡ | Q3 [0.48–0.64] → avg +0.10R |
| 8 | `range_atr_ratio` | -0.122 | 0.4407 | ⚡ | Q1 [0.26–0.45] → avg +0.15R |
| 9 | `atr_pts` | +0.116 | 0.6750 | — | Q3 [430.39–507.76] → avg +0.18R |
| 10 | `atr_pct_rank` | +0.056 | 0.5050 | — | Q4 [0.87–0.97] → avg +0.05R |
| 11 | `range_pts` | -0.032 | 0.6554 | — | Q1 [123.75–187.38] → avg +0.09R |
| 12 | `trend_up` | +0.023 | 0.8469 | — | Uptrend → avg +0.04R |

---

## Statistically Significant Signals

_None found at α=0.05 after FDR correction. Increase the data window or check data quality._

---

## Exploratory Signals (not significant — monitor only)

_These did not survive FDR correction but show directional pattern (|IC| > 0.08).
Paper-trade the top filter for 20+ sessions before treating as confirmed._

### `range_position` [exploratory — p=0.0167]

- **Type:** continuous   **Test:** Mann-Whitney U (Q1 vs Q4)
- **IC:** +0.249   **Effect size:** 0.445   **p-value:** 0.0167 *   **N:** 79
- **Best-performing range:** [0.71, 1.00]

| Bucket | N | Win% | Avg R | PF | Avg P&L | Sharpe |
|--------|---|------|-------|----|---------|--------|
| Q1 [0.05–0.24] | 20 | 40% | -0.078 [············] | 0.49x | $-87 | -0.27 |
| Q2 [0.24–0.50] | 20 | 45% | +0.011 [███·········] | 1.07x | $+25 | 0.03 |
| Q3 [0.50–0.71] | 19 | 37% | -0.021 [██··········] | 0.80x | $-19 | -0.08 |
| Q4 [0.71–1.00] | 20 | 65% | +0.192 [████████████] | 3.03x | $+229 | 0.47 |


### `breakout_side` [exploratory — p=0.0193]

- **Type:** categorical   **Test:** Mann-Whitney U
- **IC:** +0.236   **Effect size:** 0.307   **p-value:** 0.0193 *   **N:** 79

| Bucket | N | Win% | Avg R | PF | Avg P&L | Sharpe |
|--------|---|------|-------|----|---------|--------|
| LONG | 38 | 55% | +0.113 [████████████] | 2.52x | $+130 | 0.34 |
| SHORT | 41 | 39% | -0.053 [············] | 0.69x | $-48 | -0.15 |


### `gap_atr_ratio` [exploratory — p=0.5609]

- **Type:** continuous   **Test:** Mann-Whitney U (Q1 vs Q4)
- **IC:** -0.234   **Effect size:** 0.110   **p-value:** 0.5609   **N:** 79
- **Best-performing range:** [0.14, 0.24]

| Bucket | N | Win% | Avg R | PF | Avg P&L | Sharpe |
|--------|---|------|-------|----|---------|--------|
| Q1 [0.00–0.14] | 20 | 50% | +0.018 [████········] | 1.16x | $+22 | 0.06 |
| Q2 [0.14–0.24] | 20 | 55% | +0.121 [████████████] | 2.25x | $+130 | 0.32 |
| Q3 [0.24–0.43] | 19 | 37% | +0.010 [███·········] | 1.09x | $+7 | 0.03 |
| Q4 [0.43–1.34] | 20 | 45% | -0.043 [············] | 0.76x | $-10 | -0.11 |


### `gap_abs_pts` [exploratory — p=0.5250]

- **Type:** continuous   **Test:** Mann-Whitney U (Q1 vs Q4)
- **IC:** -0.182   **Effect size:** 0.120   **p-value:** 0.5250   **N:** 79
- **Best-performing range:** [56.62, 118.25]

| Bucket | N | Win% | Avg R | PF | Avg P&L | Sharpe |
|--------|---|------|-------|----|---------|--------|
| Q1 [1.50–56.62] | 20 | 55% | +0.027 [████········] | 1.26x | $+19 | 0.09 |
| Q2 [56.62–118.25] | 20 | 50% | +0.116 [████████████] | 2.12x | $+135 | 0.30 |
| Q3 [118.25–193.25] | 19 | 42% | -0.006 [██··········] | 0.95x | $+1 | -0.02 |
| Q4 [193.25–486.25] | 20 | 40% | -0.033 [············] | 0.81x | $-6 | -0.08 |


### `prior_session_win` [exploratory — p=0.1225]

- **Type:** categorical   **Test:** Mann-Whitney U
- **IC:** +0.170   **Effect size:** 0.204   **p-value:** 0.1225   **N:** 78

| Bucket | N | Win% | Avg R | PF | Avg P&L | Sharpe |
|--------|---|------|-------|----|---------|--------|
| Prior Loss/Flat | 40 | 60% | +0.086 [████████████] | 1.85x | $+104 | 0.24 |
| Prior Win | 38 | 34% | -0.035 [············] | 0.78x | $-30 | -0.10 |


### `dow` [exploratory — p=0.5744]

- **Type:** categorical   **Test:** Kruskal-Wallis
- **IC:** +0.169   **Effect size:** 0.000   **p-value:** 0.5744   **N:** 79

| Bucket | N | Win% | Avg R | PF | Avg P&L | Sharpe |
|--------|---|------|-------|----|---------|--------|
| Wed | 16 | 56% | +0.145 [████████████] | 3.22x | $+140 | 0.47 |
| Mon | 15 | 47% | +0.067 [███████·····] | 1.71x | $+69 | 0.19 |
| Thu | 15 | 40% | +0.009 [███·········] | 1.07x | $+34 | 0.03 |
| Tue | 17 | 41% | -0.031 [█···········] | 0.84x | $-17 | -0.07 |
| Fri | 16 | 50% | -0.051 [············] | 0.65x | $-32 | -0.16 |


### `early_range_pct` [exploratory — p=0.3135]

- **Type:** continuous   **Test:** Mann-Whitney U (Q1 vs Q4)
- **IC:** +0.134   **Effect size:** 0.217   **p-value:** 0.3135   **N:** 64
- **Best-performing range:** [0.48, 0.64]

| Bucket | N | Win% | Avg R | PF | Avg P&L | Sharpe |
|--------|---|------|-------|----|---------|--------|
| Q1 [0.13–0.30] | 16 | 38% | -0.100 [············] | 0.47x | $-88 | -0.30 |
| Q2 [0.30–0.48] | 16 | 56% | -0.006 [█████·······] | 0.96x | $+21 | -0.02 |
| Q3 [0.48–0.64] | 17 | 47% | +0.096 [████████████] | 2.05x | $+107 | 0.28 |
| Q4 [0.64–1.00] | 15 | 47% | +0.078 [██████████··] | 1.76x | $+81 | 0.21 |


### `range_atr_ratio` [exploratory — p=0.4407]

- **Type:** continuous   **Test:** Mann-Whitney U (Q1 vs Q4)
- **IC:** -0.122   **Effect size:** 0.145   **p-value:** 0.4407   **N:** 79
- **Best-performing range:** [0.26, 0.45]

| Bucket | N | Win% | Avg R | PF | Avg P&L | Sharpe |
|--------|---|------|-------|----|---------|--------|
| Q1 [0.26–0.45] | 20 | 65% | +0.152 [████████████] | 2.87x | $+174 | 0.42 |
| Q2 [0.45–0.64] | 20 | 20% | -0.116 [············] | 0.20x | $-132 | -0.59 |
| Q3 [0.64–0.87] | 19 | 53% | +0.009 [█████·······] | 1.06x | $+12 | 0.02 |
| Q4 [0.87–1.51] | 20 | 50% | +0.061 [███████·····] | 1.50x | $+96 | 0.15 |


### `atr_pts` [exploratory — p=0.6750]

- **Type:** continuous   **Test:** Mann-Whitney U (Q1 vs Q4)
- **IC:** +0.116   **Effect size:** 0.080   **p-value:** 0.6750   **N:** 79
- **Best-performing range:** [430.39, 507.76]

| Bucket | N | Win% | Avg R | PF | Avg P&L | Sharpe |
|--------|---|------|-------|----|---------|--------|
| Q1 [218.98–379.76] | 20 | 50% | -0.058 [············] | 0.65x | $-45 | -0.17 |
| Q2 [379.76–430.39] | 20 | 45% | -0.024 [█···········] | 0.86x | $-28 | -0.06 |
| Q3 [430.39–507.76] | 19 | 58% | +0.184 [████████████] | 4.02x | $+222 | 0.52 |
| Q4 [507.76–587.40] | 20 | 35% | +0.011 [███·········] | 1.10x | $+12 | 0.04 |


---

## Implementation Guide

### How to apply significant findings to `overnight_range.toml`

_No significant signals to act on yet. Collect more data (aim for ≥ 200 trades)._
