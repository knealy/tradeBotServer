# Alpha Discovery Report — MES

**Generated:** 2026-05-03 21:25 UTC  
**Window:** 47 calendar days  
**Sessions:** 20  
**Simulated trades:** 22  
**Simulation:** stop=1.25×ATR, TP=2.0×ATR  
**Stats correction:** Benjamini-Hochberg FDR, α = 0.05

> Features with ✅ are statistically significant after FDR correction.
> Features with ⚡ are not significant but have |IC| > 0.12 — worth monitoring.

---

## Overall Simulation Performance

| Metric | Value |
|--------|-------|
| Trades | 22 |
| Win rate | 45.5% |
| Avg R | +0.036 |
| Profit factor | 1.42x |
| Total P&L | $+586 |
| Max drawdown | $-485 |
| Annualised Sharpe | 2.06 |

---

## Signal Ranking

| # | Feature | IC | p-value | Sig | Best condition |
|---|---------|----|---------|----|----------------|
| 1 | `breakout_side` | +0.431 | 0.0213 * | ⚡ | LONG → avg +0.12R |
| 2 | `prior_session_win` | +0.383 | 0.0817 | ⚡ | Prior Loss/Flat → avg +0.16R |
| 3 | `range_position` | +0.343 | 0.0519 | ⚡ | Q4 [0.73–1.00] → avg +0.28R |
| 4 | `atr_pts` | +0.242 | 0.0350 * | ⚡ | Q4 [101.70–120.29] → avg +0.15R |
| 5 | `atr_pct_rank` | +0.216 | 0.1014 | ⚡ | Q4 [0.37–0.88] → avg +0.15R |
| 6 | `range_atr_ratio` | -0.204 | 0.6623 | ⚡ | Q1 [0.23–0.33] → avg +0.11R |
| 7 | `range_pts` | -0.153 | 0.6991 | ⚡ | Q2 [29.81–46.88] → avg +0.15R |
| 8 | `gap_atr_ratio` | -0.122 | 0.8182 | ⚡ | Q2 [0.12–0.19] → avg +0.16R |
| 9 | `gap_abs_pts` | -0.087 | 0.8182 | — | Q1 [8.00–11.81] → avg +0.04R |

---

## Statistically Significant Signals

_None found at α=0.05 after FDR correction. Increase the data window or check data quality._

---

## Exploratory Signals (not significant — monitor only)

_These did not survive FDR correction but show directional pattern (|IC| > 0.08).
Paper-trade the top filter for 20+ sessions before treating as confirmed._

### `breakout_side` [exploratory — p=0.0213]

- **Type:** categorical   **Test:** Mann-Whitney U
- **IC:** +0.431   **Effect size:** 0.619   **p-value:** 0.0213 *   **N:** 22

| Bucket | N | Win% | Avg R | PF | Avg P&L | Sharpe |
|--------|---|------|-------|----|---------|--------|
| LONG | 15 | 60% | +0.117 [████████████] | 4.37x | $+74 | 0.48 |
| SHORT | 7 | 14% | -0.137 [············] | 0.31x | $-75 | -0.47 |


### `prior_session_win` [exploratory — p=0.0817]

- **Type:** categorical   **Test:** Mann-Whitney U
- **IC:** +0.383   **Effect size:** 0.463   **p-value:** 0.0817   **N:** 21

| Bucket | N | Win% | Avg R | PF | Avg P&L | Sharpe |
|--------|---|------|-------|----|---------|--------|
| Prior Loss/Flat | 9 | 78% | +0.165 [████████████] | 4.85x | $+102 | 0.56 |
| Prior Win | 12 | 25% | -0.052 [············] | 0.58x | $-24 | -0.20 |


### `range_position` [exploratory — p=0.0519]

- **Type:** continuous   **Test:** Mann-Whitney U (Q1 vs Q4)
- **IC:** +0.343   **Effect size:** 0.733   **p-value:** 0.0519   **N:** 22
- **Best-performing range:** [0.73, 1.00]

| Bucket | N | Win% | Avg R | PF | Avg P&L | Sharpe |
|--------|---|------|-------|----|---------|--------|
| Q1 [0.07–0.44] | 6 | 17% | -0.079 [█···········] | 0.48x | $-43 | -0.27 |
| Q2 [0.44–0.65] | 5 | 80% | +0.108 [██████······] | 13.70x | $+69 | 0.59 |
| Q3 [0.65–0.73] | 6 | 17% | -0.114 [············] | 0.29x | $-54 | -0.51 |
| Q4 [0.73–1.00] | 5 | 80% | +0.283 [████████████] | 330.71x | $+166 | 1.06 |


### `atr_pts` [exploratory — p=0.0350]

- **Type:** continuous   **Test:** Mann-Whitney U (Q1 vs Q3)
- **IC:** +0.242   **Effect size:** 0.714   **p-value:** 0.0350 *   **N:** 22
- **Best-performing range:** [101.70, 120.29]

| Bucket | N | Win% | Avg R | PF | Avg P&L | Sharpe |
|--------|---|------|-------|----|---------|--------|
| Q1 [71.22–83.90] | 7 | 14% | -0.125 [············] | 0.25x | $-64 | -0.58 |
| Q3 [93.23–101.70] | 5 | 20% | +0.013 [█████·······] | 1.10x | $+8 | 0.03 |
| Q4 [101.70–120.29] | 6 | 67% | +0.151 [████████████] | 9.45x | $+100 | 0.72 |


### `atr_pct_rank` [exploratory — p=0.1014]

- **Type:** continuous   **Test:** Mann-Whitney U (Q1 vs Q3)
- **IC:** +0.216   **Effect size:** 0.571   **p-value:** 0.1014   **N:** 22
- **Best-performing range:** [0.37, 0.88]

| Bucket | N | Win% | Avg R | PF | Avg P&L | Sharpe |
|--------|---|------|-------|----|---------|--------|
| Q1 [0.00–0.03] | 7 | 29% | -0.099 [············] | 0.33x | $-51 | -0.44 |
| Q3 [0.24–0.37] | 5 | 40% | +0.036 [██████······] | 1.29x | $+21 | 0.09 |
| Q4 [0.37–0.88] | 6 | 67% | +0.151 [████████████] | 9.45x | $+100 | 0.72 |


### `range_atr_ratio` [exploratory — p=0.6623]

- **Type:** continuous   **Test:** Mann-Whitney U (Q1 vs Q4)
- **IC:** -0.204   **Effect size:** 0.200   **p-value:** 0.6623   **N:** 22
- **Best-performing range:** [0.23, 0.33]

| Bucket | N | Win% | Avg R | PF | Avg P&L | Sharpe |
|--------|---|------|-------|----|---------|--------|
| Q1 [0.23–0.33] | 6 | 50% | +0.112 [████████████] | 2.99x | $+80 | 0.44 |
| Q2 [0.33–0.56] | 5 | 40% | +0.075 [█████████···] | 3.15x | $+44 | 0.34 |
| Q3 [0.56–0.76] | 6 | 33% | -0.054 [············] | 0.70x | $-24 | -0.13 |
| Q4 [0.76–1.09] | 5 | 60% | +0.015 [█████·······] | 1.23x | $+6 | 0.07 |


### `range_pts` [exploratory — p=0.6991]

- **Type:** continuous   **Test:** Mann-Whitney U (Q1 vs Q4)
- **IC:** -0.153   **Effect size:** 0.167   **p-value:** 0.6991   **N:** 22
- **Best-performing range:** [29.81, 46.88]

| Bucket | N | Win% | Avg R | PF | Avg P&L | Sharpe |
|--------|---|------|-------|----|---------|--------|
| Q1 [23.00–29.81] | 6 | 33% | +0.048 [████████····] | 1.64x | $+40 | 0.19 |
| Q2 [29.81–46.88] | 5 | 60% | +0.152 [████████████] | 12.92x | $+92 | 0.75 |
| Q3 [46.88–66.94] | 5 | 20% | -0.205 [············] | 0.04x | $-115 | -0.96 |
| Q4 [66.94–129.00] | 6 | 67% | +0.129 [███████████·] | 3.29x | $+77 | 0.38 |


### `gap_atr_ratio` [exploratory — p=0.8182]

- **Type:** continuous   **Test:** Mann-Whitney U (Q1 vs Q4)
- **IC:** -0.122   **Effect size:** 0.111   **p-value:** 0.8182   **N:** 22
- **Best-performing range:** [0.12, 0.19]

| Bucket | N | Win% | Avg R | PF | Avg P&L | Sharpe |
|--------|---|------|-------|----|---------|--------|
| Q1 [0.08–0.12] | 6 | 33% | +0.044 [█████·······] | 1.82x | $+36 | 0.22 |
| Q2 [0.12–0.19] | 5 | 60% | +0.164 [████████████] | 18.45x | $+99 | 0.67 |
| Q3 [0.19–0.31] | 5 | 40% | -0.064 [············] | 0.70x | $-28 | -0.14 |
| Q4 [0.31–0.76] | 6 | 50% | +0.006 [███·········] | 1.07x | $+3 | 0.03 |


### `gap_abs_pts` [exploratory — p=0.8182]

- **Type:** continuous   **Test:** Mann-Whitney U (Q1 vs Q3)
- **IC:** -0.087   **Effect size:** 0.111   **p-value:** 0.8182   **N:** 22
- **Best-performing range:** [8.00, 11.81]

| Bucket | N | Win% | Avg R | PF | Avg P&L | Sharpe |
|--------|---|------|-------|----|---------|--------|
| Q1 [8.00–11.81] | 6 | 33% | +0.044 [████████████] | 1.82x | $+36 | 0.22 |
| Q2 [11.81–18.00] | 7 | 43% | +0.029 [███████·····] | 1.30x | $+24 | 0.09 |
| Q4 [25.50–91.75] | 6 | 50% | +0.006 [············] | 1.07x | $+3 | 0.03 |


---

## Implementation Guide

### How to apply significant findings to `overnight_range.toml`

_No significant signals to act on yet. Collect more data (aim for ≥ 200 trades)._
