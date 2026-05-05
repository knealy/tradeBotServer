# Research starter — strategy hypotheses, libraries, and data sources

Living, opinionated index of upstream material we use to design, critique,
and validate trading edges in this repo. Pair with:

- [docs/RESEARCH_PATHWAYS.md](../RESEARCH_PATHWAYS.md) — operator map
  (pathway 1 / 2, multi-symbol export, sweep regen)
- [docs/OVERNIGHT_RANGE_RESEARCH.md](../OVERNIGHT_RANGE_RESEARCH.md) —
  per-strategy operator checklist for `overnight_range`
- [docs/ALPHA_DISCOVERY.md](../ALPHA_DISCOVERY.md) — session-feature scan + bar-level conditional scan (§ “Conditional short-horizon scan”)
- [docs/BACKTEST_RESEARCH.md](../BACKTEST_RESEARCH.md) — grid + OOS + MC
- [docs/LOCAL_LLM_RESEARCH.md](../LOCAL_LLM_RESEARCH.md) — Ollama review tools
- [docs/perf/sweeps/CANDIDATES.md](sweeps/CANDIDATES.md) — open hypothesis ledger

The notes below are *starting points*, not prescriptions. Anything that
sounds like a guarantee of returns is the prose, not the math.

---

## Mental model — alpha for cashflow

Generating excess returns ("alpha") consistently usually requires either:

1. **Many small, fast, low-correlation signals.** High gross alpha is
   plausible (10–15% / yr in some studies), but transaction costs eat
   most of it. Only viable with low-latency execution and disciplined
   stop-loss rules.
2. **A few high-quality factor exposures.** Quality (gross profit / assets)
   and high-cash-flow yield consistently beat naive value. Short-duration
   equities + low-risk / high-profitability factors stack well.
3. **Portable alpha.** Use derivatives for beta, free up capital for an
   "alpha engine" (covered calls, absolute-return bond strategies). Most
   of this is out of scope for a TopStepX micro-futures bot.

For micro-futures specifically, two practical levers stand out:

- **Intraday mean reversion** to VWAP / moving average after extreme
  z-score deviations (`vwap_zscore_reversion` strategy in this repo).
- **Volatility harvesting** — profit from large intraday swings without a
  strong directional view (`volatility_breakout` candidate).

Costs always come first: slippage, commission, market impact. Many
published strategies experience "alpha decay" once well known. Manage
risk before chasing trade count.

---

## Statistical / quant libraries

These are reference implementations or testing toolkits — none are wired
into the live bot.

- [DhyeyMavani2003/Statistical-Trading-Strategy-in-Futures-Markets](https://github.com/DhyeyMavani2003/Statistical-Trading-Strategy-in-Futures-Markets)
  — channel breakouts + trailing stops on futures (similar shape to
  `overnight_range`).
- [JerBouma/FinanceToolkit](https://github.com/JerBouma/FinanceToolkit)
  — Sharpe / Sortino / drawdown / risk metrics for validating runs.
- [Auquan/Tutorials — long/short ranking](https://github.com/Auquan/Tutorials/blob/master/Long-Short%20Strategies%20using%20Ranking.ipynb)
  — market-neutral ideas that occasionally translate to micro-index pairs.
- [paperswithbacktest/awesome-systematic-trading](https://github.com/paperswithbacktest/awesome-systematic-trading)
  — broad index of published strategies with code.
- [wangzhe3224/awesome-systematic-trading](https://github.com/wangzhe3224/awesome-systematic-trading)
  — alt index, often with stronger Asia / commodities content.
- [wilsonfreitas/awesome-quant](https://github.com/wilsonfreitas/awesome-quant)
  — community-maintained quant resource map.
- [AliHabibnia/Algorithmic_Trading_with_Python — Lecture 7 (strategy testing)](https://github.com/AliHabibnia/Algorithmic_Trading_with_Python/blob/main/Lecture%2007_%20Strategy%20Testing.ipynb)
  — pragmatic intro to backtesting framework design.
- [bradleyboyuyang/ML-HFT](https://github.com/bradleyboyuyang/ML-HFT)
  — ML/DL on order-book data; aspirational, not currently feasible
  without tick / book feeds.
- [ianjure/mean-reversion-trading](https://github.com/ianjure/mean-reversion-trading)
  — clean reference for mean-reversion algos worth porting to futures.
- [tradermonty/claude-trading-skills](https://github.com/tradermonty/claude-trading-skills)
  — agentic / LLM workflows in trading research (compare with our
  [docs/LOCAL_LLM_RESEARCH.md](../LOCAL_LLM_RESEARCH.md)).
- [fetchai/mettalex — derivatives strategies](https://github.com/fetchai/mettalex-documentation/blob/main/trading-strategies-with-derivatives.md)
  — high-level catalogue of structured-product / derivatives plays.

## Historical / tick / bar data sources

We currently bootstrap from TopStepX 1m via
[scripts/export_history.py](../../scripts/export_history.py) +
[scripts/walkback_history.sh](../../scripts/walkback_history.sh) (chunked).
For **Databento** GLBX batch folders (`split_symbols` → many `ohlcv-1m` CSVs), merge into one file per root with
[scripts/merge_databento_glbx_batch.py](../../scripts/merge_databento_glbx_batch.py) (see [BACKTESTING.md](../BACKTESTING.md) § Databento).
Other lines of attack:

- [TheSnowGuru/Stocks-Futures-Financial-Time-series-Tick-Bar-Data](https://github.com/TheSnowGuru/Stocks-Futures-Financial-Time-series-Tick-Bar-Data)
  — sample tick / bar archives for cross-checking our exports.
- [jerbouma/FinanceDatabase](https://github.com/jerbouma/FinanceDatabase)
  — symbol metadata; useful when we need to map TopStepX root → underlying
  index / commodity.
- [philipperemy/FX-1-Minute-Data](https://github.com/philipperemy/FX-1-Minute-Data)
  — example of clean public 1m archives; the layout informs our CSV
  conventions.
- [FutureSharks/financial-data](https://github.com/FutureSharks/financial-data)
  — long-history index / futures CSVs; check timezone before merging.
- [ropensci/rb3](https://github.com/ropensci/rb3)
  — Brazilian futures (mainly relevant if we ever expand beyond US micros).
- [CMEGroupPublic/datamine_python](https://github.com/CMEGroupPublic/datamine_python)
  — official CME data tooling; used as a tick / book reference.
- [wrighter/ib-scripts](https://github.com/wrighter/ib-scripts/tree/main)
  — IBKR API helpers; useful if we ever cross-validate a TopStepX export
  against IB.
- [michaelsmusing/sources-for-intraday-historical-stock-data](https://github.com/michaelsmusing/sources-for-intraday-historical-stock-data)
  — meta-list of bar-data providers, free + paid.
- [ranaroussi/quantstats #313](https://github.com/ranaroussi/quantstats/issues/313)
  — discussion thread on data normalization gotchas.

## Reference projects (TopStepX / micro-futures)

- [vinicio-cortez/mes-trading-bot-showcase](https://github.com/vinicio-cortez/mes-trading-bot-showcase)
  — simple MES bot; useful for comparing strategy logic shape.
- [Glatrix/TopstepX](https://github.com/Glatrix/TopstepX)
  — alt TopStepX integration; double-check API conventions when our
  adapter behaves unexpectedly.

---

## Implementation tips (carried over from prior notes)

- Trade during high-volume RTH to minimise slippage; low-volume periods
  are choppy. Most candidate strategies in
  [CANDIDATES.md](sweeps/CANDIDATES.md) are RTH-gated for that reason.
- Define risk in dollars per trade, not in margin. The bot already does
  this via `risk_per_trade_pct`.
- Validate before tightening filters: a strategy that "looks great" on
  in-sample 5–10 trades is statistically meaningless — wait for ≥ ~50
  sessions and run [scripts/alpha_discovery.py](../../scripts/alpha_discovery.py)
  with a session-based OOS split.
- For **conditional probabilities** on 5m bars (RSI / VWAP / MA / inside bars,
  prior-day high/low RTH re-touch rates), run
  [scripts/pattern_conditional_scan.py](../../scripts/pattern_conditional_scan.py)
  on merged Databento 1m CSVs; reports land under [docs/alpha/](../alpha/) (`pattern_scan_*.md`).
- For **forward-return effect sizes (in points)** at multiple horizons with
  IS/OOS sign stability + RTH phase stratification, run
  [scripts/deep_pattern_scan.py](../../scripts/deep_pattern_scan.py); reports
  land at [docs/alpha/deep_scan_INDEX.md](../alpha/deep_scan_INDEX.md). The
  first run produced the cross-instrument big-body reversion edge currently
  scaffolded as the [`body_reversion`](../../strategies/body_reversion_strategy.py)
  research strategy.
- Once you have an anchor, **audit candidate co-triggers** with
  [scripts/body_reversion_combo_audit.py](../../scripts/body_reversion_combo_audit.py)
  — pairs the anchor with each curated co-trigger (and 3-way combos) and
  reports realized R under both stop-only/fixed-hold and triple-barrier
  execution models. Output:
  [docs/alpha/body_reversion_combos.md](../alpha/body_reversion_combos.md).
  Pick co-triggers with positive `delta_R` AND `n ≥ 500` AND
  cross-instrument robustness; that filter found
  `atr_high_q4 & range_expand_1.5x` for the v3 body-reversion update,
  which lifted MES from a − $268 bleed to a + $1,003 winner on Q1 2026 OOS.
