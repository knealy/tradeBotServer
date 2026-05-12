# `morning_range_reversion` — full-window validation (MNQ Databento, 2024-2026)

**TL;DR:** the upstream **92.2 % WR / +0.844 R / ~$2.7 k/mo** headline (`docs/alpha/morning_reversion_info.md`) **does not replicate** on stitched Databento MNQ history. The strategy *is* positive-expectancy in the **legacy reentry path with `max_fades_per_session = 1`**, but at ~5–6× lower income per contract than the headline claims.

Run on 2026-05-09 against `historical_data/price/MNQ_5m_databento.csv` + `MNQ_1m_databento.csv`, window 2024-01-01 → 2026-05-01 (164,826 5m bars / 824,127 1m bars; 1m intrabar TP-vs-SL resolution active).

## Sieve sweep

Net R per trade at 1:1 = `2 × WR − 1`. Approx PnL = `n × netR × $95` (typical MNQ width). Commissions/slippage **not** subtracted yet — knock off ~$3–4 / round-trip × n for a realistic figure.

| Mode | Cap | Trades | Sessions | Trades/sess | WR | Net R | ~PnL (28 mo) |
|---|---:|---:|---:|---:|---:|---:|---:|
| **immediate fade (default)** | 0 | 2,853 | 598 | 4.77 | 0.4444 | −0.1112 | **−$30,115** |
| immediate fade | 1 | 598 | 598 | 1.00 | 0.4365 | −0.1271 | −$7,220 |
| immediate fade | 2 | 1,100 | 598 | 1.84 | 0.4555 | −0.0891 | −$9,310 |
| immediate fade | 4 | 1,843 | 598 | 3.08 | 0.4623 | −0.0754 | −$13,205 |
| **legacy reentry** | 0 | 2,417 | 529 | 4.57 | 0.5536 | +0.1072 | **+$24,605** |
| **legacy reentry** | **1** | **529** | **529** | **1.00** | **0.6295** | **+0.2590** | **+$13,015** |
| legacy reentry | 2 | 962 | 529 | 1.82 | 0.5821 | +0.1642 | +$15,010 |
| legacy reentry | 4 | 1,569 | 529 | 2.97 | 0.5666 | +0.1332 | +$19,855 |

After approx costs (`$3.50 per round trip × n`):

| Mode | n × netR × $95 | − costs | Net |
|---|---:|---:|---:|
| legacy + cap=1 | +$13,015 | −$1,852 | **+$11,163** (~$398 /mo) |
| legacy + cap=0 | +$24,605 | −$8,460 | +$16,145 (~$577 /mo) |
| legacy + cap=2 | +$15,010 | −$3,367 | +$11,643 (~$416 /mo) |

## Decision

1. **Reject the immediate-fade default** — every cap value is net-negative on this dataset. Either the upstream backtest used different rules (limit-on-pullback?) or different data (vendor with different overnight session, different roll), or both.
2. **Adopt `signal.require_reentry_close = true` + `signal.max_fades_per_session = 1`** as the live/replay default. This is the cleanest read on the edge: 1 trade per traded session, **62.95 % WR**, +0.26 R per trade, **~+$400 /month per contract on MNQ after costs**.
3. **Keep `meta.enabled = false` for now.** The edge is real but ~5× weaker than `body_reversion` v3.1 hybrid (MES Sharpe 5.67 / DD 1.23 % full-window). Promote `morning_range_reversion` only after a 30-trade live-shadow vs replay matches within 20 % R drift.
4. The third-party 92.2 % claim should be treated as a **theoretical ceiling** (or as evidence of dataset / definition drift), **not a target**.

## Repro

```bash
.venv/bin/python scripts/validate_morning_range_reversion.py \
  --csv historical_data/price/MNQ_5m_databento.csv \
  --1m-csv historical_data/price/MNQ_1m_databento.csv \
  --start 2024-01-01 --end 2026-05-01 --legacy-reentry
```

The cap sweep was a one-off `/tmp/test_max_fades.py`; equivalent control is `--legacy-reentry` plus editing `signal.max_fades_per_session` in `config/strategies/morning_range_reversion.toml`.

## Symbol portability (legacy reentry, uncapped, same window)

| Symbol | Trades | Sessions | WR | Net R | High-sweep WR | Low-sweep WR |
|---|---:|---:|---:|---:|---:|---:|
| **MNQ** | 2,417 | 529 | 0.5536 | +0.107 | 0.583 | 0.524 |
| **MES** | 2,415 | 537 | 0.5673 | +0.135 | 0.590 | 0.541 |
| **MGC** | 2,106 | 515 | 0.5954 | +0.191 | 0.574 | 0.619 |

Edge **is** portable across all three micros. **MGC is the strongest** in legacy mode (0.595 WR / +0.191 R) — the opposite of `body_reversion` where MES was the standout. This makes the two strategies complementary on a portfolio basis.

## Followups

- Run cap=1 sieve on MES + MGC to compare cleanest-read income vs MNQ (already done at cap=0; cap=1 trade-by-trade audit would tighten the WR ceiling).
- **Per-direction asymmetry on MNQ**: high-sweep WR 0.583 > low-sweep 0.524. Test `allow_long=false` (only fade upside sweeps) — likely small lift in WR at half the cadence.
- **MGC asymmetry inverts**: low-sweep 0.619 > high-sweep 0.574. Per-symbol direction filters could be a real win.
- Walk-forward: split sessions 70/30 IS/OOS via `core.research.runner` once a strategy class wraps this sieve.
- Compare `tp_mult = 0.5` (target halfway to mid) vs `tp_mult = 1.0` (mid) — half-target may push WR back toward the headline at the cost of R/trade.
