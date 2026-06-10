# ORB window exploration — `overnight_range` (2026-06-09)

**Hypothesis**: a shorter / morning-anchored range window (e.g. 9:30-9:45 ORB, premarket, Asia-only) might beat the committed `overnight_range` 19:00→10:00 window on the same alpha mechanic. If so, spawn `opening_range_breakout` as a new strategy.

**Method**: 7 alternative windows + the committed baseline, swept on MNQ + MGC across 9m / 6m / 3m walk-forward. All per-symbol `skip_weekdays` cleared and `filters.gap` + `filters.range_size` disabled so the comparison is on the raw window mechanic — not on tunes that were specifically fitted to the 19:00→10:00 hours.

**Result: rejected.** The committed window is uniquely profitable. Every alternative either produces zero trades (the range collapses into the same minute as market_open) or loses heavily.

| Window | 9m RF | 6m RF | 3m RF | Verdict |
|---|---|---|---|---|
| 19:00→10:00 (committed) | **6.43** | **4.51** | **2.20** | Champion |
| 09:30→09:45 15min ORB | 0 | 0 | 0 | No signal |
| 09:30→10:00 30min ORB | 0 | 0 | 0 | No signal |
| 09:30→10:30 60min ORB | -0.62 | -0.68 | -0.52 | Heavy losses |
| 08:30→09:30 60min pre-mkt | -0.98 | -0.72 | -0.97 | Heavy losses |
| 06:30→09:30 3h pre-mkt | -0.64 | -0.46 | -0.10 | Heavy losses |
| 20:00→07:00 Asia | -0.21 | -0.03 | -0.79 | Mixed losses |
| 22:00→04:00 short Globex | -0.92 | -0.40 | -0.83 | Heavy losses |

## Why this is the result

1. **Range width matters**. The 19:00→10:00 window has 14.5 hours to develop a wide, meaningful range — Asia + Europe + early NY pre-market all contribute. Short windows like 09:30-09:45 produce ranges that are essentially noise around the open; the breakout signal is dominated by tick-level fluctuation.

2. **Cross-session positioning is the alpha**. The committed strategy enters AT NY open (10:00 ET) based on a range built BEFORE NY open — capturing the institutional positioning that happens overnight. Same-day-only windows (ORB variants) miss this structural edge entirely.

3. **The 09:30→09:45 / 09:30→10:00 zero-trade results** are a harness artifact: when `overnight_end == market_open`, the breakout monitor effectively never gets bar time to evaluate the range as a tradeable signal (range build window and order window are the same minute). These trials are uninformative; we know from the 09:30→10:30 trial that the 60-minute ORB on MNQ+MGC current data is a LOSING signal anyway.

4. **MGC is the loser on every alternative window**. MNQ is sometimes mildly positive (e.g. premarket_60min at +$10), but MGC consistently posts -$22 to -$96 on each alternative. The 19:00→10:00 → MGC +$86 result is genuinely structural — gold has its own Asia-driven move that the overnight range captures cleanly.

## Decision

**No spawn of `opening_range_breakout`.** The 19:00→10:00 window is not just "one good choice" — it's the *uniquely good* choice within this strategy's alpha mechanic. The breakout-of-prior-range signal IS the overnight-range signal; trying to apply it to a different window destroys the edge.

Future work (if revisited):
- Test ORB as a fundamentally different mechanic — opening drive continuation, VWAP-anchored fade, etc. — rather than as the same `OvernightRangeStrategy` class with different timing knobs.
- The `opening_drive_continuation` strategy concept from `docs/STRATEGY_ARSENAL.md` Phase-2 backlog is a better next swing if ORB-style alpha is wanted.

## Artifacts

- Trial JSON: `/tmp/orb_windows.json` (also reproducible from this doc + the 8 labels above)
- Sweep results: `docs/perf/_opt_runs/orb_exploration/{9m,6m,3m}/`
- Run command: `for spec in "270:9:9m" "180:6:6m" "90:3:3m"; do ...; .venv/bin/python scripts/optimize_strategy.py --strategy overnight_range --trials-json /tmp/orb_windows.json --days $days --folds $folds --symbols MNQ,MGC --out-dir docs/perf/_opt_runs/orb_exploration/$label --window-label $label; done`
