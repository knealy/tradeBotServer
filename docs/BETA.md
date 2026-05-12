Faster operations and order execution (runtime)
* Profile before changing — Use the existing playbook (py-spy, Scalene, importtime in docs/perf/) on strategy_executor + live paths in topstepx_adapter so work targets real hotspots (SignalR callbacks, bar handling, REST placement), not guesses.
* Rust path — README already flags optional TOPSTEPX_USE_RUST. “Faster” here means widening what’s on the Rust side (or reducing Python↔Rust marshalling), measuring each step, and failing closed to Python if something’s wrong.
* Fewer round-trips — Batch or coalesce broker calls where the API allows (positions + orders in one logical “tick”), and avoid redundant get_available_contracts / history calls on the hot path (cache TTLs, in-memory contract map already partially there).
* Event loop hygiene — Keep the AGENTS rules (no blocking HTTP/sleep in async); ensure user/market hub handlers never do heavy work inline—delegate to bounded queues or asyncio.to_thread only where it’s truly CPU-bound.
* DB and logging — Async batch writers and avoiding hot-path INFO + large json.dumps (per AGENTS) are levers; tightening log levels in production and sampling metrics reduces I/O wait.
* uvloop — Already documented for Linux/macOS; on deploy targets that support it, confirm it’s actually enabled in prod images.

Backtesting: better at finding profitable strategies (not just “working” backtests)
* Research vs execution split — Treat core/backtest as the execution engine; add a thin “research” layer: parameter grids, walk-forward windows, train/test by calendar, and mandatory out-of-sample segments so “profitable” isn’t one curve-fit window.
* Costs and realism — Slippage, commission, partial fills, and session rules (RTH vs globex) dominate prop-style edges; making defaults conservative and sensitivity tables (slippage ±) part of the standard report reduces false positives.
* Robustness, not only PnL — Monte Carlo / bootstrap on trade sequences (you already have building blocks) as a default gate before promoting a config; require stability under reordering and parameter jitter.
* Alignment with live — Same StrategyConfig + same strategy code paths as live (you’re partly there with TOML + executor); explicit checks that backtest assumptions (bars, timezone, roll rules) match Bar/adapter behavior.
* Screening pipeline — Fast cheap filters (few symbols, short history) → full run only for survivors; store results in Postgres (strategy_performance etc.) with queryable tags so you can compare runs without rerunning everything.
* Overfitting discipline — Cap degrees of freedom (number of tuned params), use nested CV or simple holdouts, and log the exact TOML + git SHA for every run so “profitable” runs are reproducible.

Streamlining the repo
* trading_bot.py decomposition — README and AGENTS already call it out; continuing to peel vertical slices (already started with hubs, brackets, interactive UI) shrinks cognitive load and speeds reviews.
* Single entry for “how we run things” — Reduce overlap between README, HANDOFF, PLAYBOOK, ENV_VARS, and .cursor/plans so one path is canonical and others link once (you already hit verify_handoff.sh / broken links as a maintenance cost).
* Tests and CI scope — Expand smoke tests around extracted modules (you’ve started); keep default pytest fast, benches opt-in (docs/perf/README.md pattern).
* Dead code and duplicates — Periodic grep for duplicate helpers, unused scripts, and stale env keys vs slim_env allowlist keeps the tree honest.
* Generated vs hand-edited — docs/MAP.md via gen_map.sh is the right idea; anything else that drifts (env lists) could follow “one source generates docs” to avoid dual maintenance.

GUI: more polished without bloat
* Lean on design tokens — One small CSS layer (variables for spacing, type scale, neutrals, one accent) applied consistently across static/dashboard/ and any server-rendered pages (gui/chart_html.py) gives a “designed” feel with almost no new JS.
* Typography and density — Better font stack, line-height, and table alignment often read as “professional” faster than new widgets.
* Reuse components — Shared header, status badges, empty states, and error banners instead of one-off markup reduce duplication without adding a heavy framework.
* Avoid shipping a build toolchain in Docker — README is clear: pre-built SPA in static/dashboard/. Polish lives in a dev build step (scripts/build.sh); the runtime image stays thin.
* Progressive enhancement — Critical actions and readouts work without flashy charts; enhance with optional chart layers so the default payload stays small.
* Accessibility and motion — Focus states, contrast, reduced-motion preference—cheap wins that feel “product-grade.”


ALPHA STRATEGY


Alpha strategy research indicates that generating consistent, excess returns—alpha—requires exploiting market inefficiencies, often through high-turnover, short-term signals or specialized, long-term factor investing. While traditional alpha focuses on outperforming benchmarks, "trading for cashflow" often utilizes portable alpha strategies or income-generating instruments, such as covered calls, to produce liquidity. [1, 2, 3, 4, 5]
Here are key findings regarding alpha strategy research and its application for cashflow:
Alpha Research & Generation Techniques
* Short-Term Signals: Research shows that combining multiple, short-term, low-correlation signals can generate strong gross alpha (above 12% per year). However, these strategies often face high turnover (\(\approx 1000\%\)), which can erode two-thirds of the return due to transaction costs.
* Factor Investing (Quality & Value): Focusing on companies with high profitability (gross profit-to-assets) and high cash flow yields often outperforms traditional value metrics like book-to-market.
* Short-Duration Equities: Stocks that generate cash flow in the near term (short-duration) tend to have higher risk premia, making them effective for alpha generation, particularly when paired with low-risk/high-profitability factors.
* Eliminating Poor Performers: Screening out the worst quintile of momentum stocks can significantly boost returns across diverse portfolio types.
* Disciplined Alpha & Risk Control: Utilizing a Portfolio Impact (PI) framework allows managers to pinpoint specific risks and construct portfolios with limited, intentional exposure, aiming for higher information ratios. [1, 2, 3, 4, 5, 6]
Portable Alpha & Cashflow Strategies
Portable alpha allows investors to gain market exposure (beta) via derivatives while using the capital to run an "alpha engine" to generate cash flow. [1]
* Portable Alpha Mechanism: By using futures or swaps to get market exposure, the core cash is freed up for strategies like absolute return bond funds, providing a potential alpha source, as seen in techniques described by PIMCO.
* Income-Generating Alpha: Some strategies, like those from SteelPeak, use laddered covered callscombined with active stock selection to generate cash flow.
* Revaluation Alpha: Research suggests that about one-third of excess returns come from revaluation effects, which can dominate performance over shorter time frames (1–10 years). [1, 2, 3, 4, 5]
Key Considerations for Success
* Costs First: Successful alpha research must account for slippage, commission, and market impact before finalizing a strategy.
* Alpha Decay: Many strategies experience "alpha decay" once published and widely adopted.
* Diversification: Diversifying alpha sources is crucial; spreading active weights reduces the reliance on any single bet and keeps tracking error manageable.
* Manager Skill: For complex, high-turnover strategies or portable alpha, experienced management is critical to manage the associated risks


Short-term equity index futures trading for cash flow relies heavily on mean-reversion and high-frequency techniques, focusing on intraday volatility, particularly in liquid instruments like E-mini (ES/NQ) or Micro (MES/MNQ) contracts. For 2026, the most effective strategies include automated mean reversion, spread trading, and volatility harvesting, with Python-based tools and GitHub repositories being essential for development. [1, 2, 3]

Top Short-Term Equity Index Futures Strategies (2026)
* Mean Reversion (Intraday): Based on the principle that prices tend to return to their VWAP (Volume-Weighted Average Price) or a moving average after an extreme deviation.
* Volatility Harvesting: Profiting from high volatility by executing long and short trades during sharp price swings without holding a strong directional bias.
* Spread Trading: Buying and selling related contracts (e.g., calendar spreads like March ES vs. June ES) to profit from price differentials while reducing directional exposure.
* Quantitative Order Flow Analysis: Using machine learning to analyze liquidity imbalances and order book depth to predict price movement within milliseconds. [1, 2]

Performance Statistics & Cash Flow Focus
* Instrument Selection: In 2026, Micro contracts (MES/MNQ) are preferred for retail, providing liquidity that rivals full-sized contracts, lower margin requirements, and easier scaling for regular cash flow.
* Key Metrics: Successful short-term strategies in 2026 often target a Sharpe Ratio that balances profit against the high risks of intraday leverage.
* Cash Flow Consistency: Active scalping or intraday strategies can generate daily profits, but require very low latency and strict stop-loss management to handle high commission costs.
* Mean Reversion Example: A strategy that shorts when the price deviates \(>2\) standard deviations (Z-score \(>2\)) from the VWAP and covers at the mean can yield consistent, small gains. [1, 2, 3, 4, 5]

GitHub Repositories and Tools (2026)
Several repositories on GitHub offer frameworks for backtesting and implementing these strategies:
1. DhyeyMavani2003/Statistical-Trading-Strategy-in-Futures: Implements statistical strategies for futures markets using channel breakouts and trailing stops.
2. JerBouma/FinanceToolkit: A popular Python tool for calculating risk/performance metrics like Sharpe Ratios, useful for validating index futures strategies.
3. Auquan/Tutorials (Long-Short Strategies): Provides tutorials on building market-neutral, long-short strategies, which can be applied to index futures to reduce market exposure.
4. bradleyboyuyang/ML-HFT: A high-frequency trading framework using machine/deep learning designed for futures.
5. ianjure/mean-reversion-trading: A repository focused on developing algorithms to replicate mean reversion strategies on stock data, which can be adapted to index futures. [1, 2, 3, 4, 5]

2026 Implementation Tips
* Automation: Utilize platforms that support automated order flow to execute trades, as 2026 markets are heavily influenced by AI-driven analytics.
* Risk Management: Do not over-leverage; define dollar risk per trade rather than trading maximum size based on margin.
* Focus on Hours: Trade during high-volume periods (e.g., US market open) to minimize slippage, as low-volume periods lead to high "chop"



STATS RESEARCH

https://github.com/DhyeyMavani2003/Statistical-Trading-Strategy-in-Futures-Markets

https://github.com/JerBouma/FinanceToolkit

https://github.com/Auquan/Tutorials/blob/master/Long-Short%20Strategies%20using%20Ranking.ipynb

https://github.com/paperswithbacktest/awesome-systematic-trading

https://github.com/tradermonty/claude-trading-skills

mettalex-documentation/trading-strategies-with-derivatives. ...GitHubhttps://github.com › fetchai › blob › main › trading-strat...

wangzhe3224/awesome-systematic-tradingGitHubhttps://github.com › wangzhe3224 › awesome-systematic...

wilsonfreitas/awesome-quant: A curated list of insanely ...GitHubhttps://github.com › wilsonfreitas › awesome-quant

Lecture 07_ Strategy Testing.ipynbGitHubhttps://github.com › AliHabibnia › blob › main › Lectur...


HIST. DATA

https://github.com/TheSnowGuru/Stocks-Futures-Financial-Time-series-Tick-Bar-Data

https://github.com/jerbouma/FinanceDatabase

https://github.com/philipperemy/FX-1-Minute-Data

https://github.com/FutureSharks/financial-data

https://github.com/ropensci/rb3

https://github.com/CMEGroupPublic/datamine_python

https://github.com/wrighter/ib-scripts/tree/main

https://github.com/michaelsmusing/sources-for-intraday-historical-stock-data

https://github.com/ranaroussi/quantstats/issues/313



PROJECTS

https://github.com/vinicio-cortez/mes-trading-bot-showcase

https://github.com/Glatrix/TopstepX




Pathway 1 — overnight_range filter sweep (MNQ, 2025-12-24..2026-04-30)
Step	Description	Trades	PnL	WR	Sharpe	Max DD
P0	live TOML	4	+238	75%	11.11	85
P1	skip_weekdays=[]	7	+368	71%	9.98	125
P2	+ volatility off	45	+6 151	44%	2.93	11 924
P3	+ gap off	67	+6 511	52%	2.62	11 791
P4	all filters off	83	+6 133	50%	1.83	12 088
P5	filters on, widened bands	64	+776	51%	3.51	384
P5 is the most defensible cadence boost: 16× the trade count of live config, Sharpe 3.51, $384 DD vs $12 k DD for P4. Files in docs/perf/sweeps/overnight_range_MNQ_P*.json.
Pathway 2 — strategy × symbol matrix
* New strategy vwap_zscore_reversion (RTH-only intraday VWAP mean reversion, ATR-stopped, TP at VWAP) registered in both strategy_manager and backtest_executor. MNQ replay: 84 trades / +$1 172 / 46% WR / 2.09 Sharpe / $597 DD vs overnight_range P0 = 4 trades.
* MES / MGC pulled via scripts/walkback_history.sh (TopStepX retains ~6 weeks of 1m for MES, ~5 weeks for MGC). Walked back, merged, and re-run; vwap_zscore at default knobs is mildly negative on MES/MGC — needs per-symbol calibration before the LLM proposes a grid.
* Alpha-discovery reports for all 3 symbols at 5m → docs/alpha/{MNQ,MES,MGC}.{md,json}. None pass FDR over the short window — expected; serves as the objective filter dictionary CANDIDATES.md mentions.
Local-LLM research wiring
* core/llm/ollama_client.py — async generate / chat / embed over http://localhost:11434 (env-driven model & timeout, no new deps).
* scripts/llm_review.py — summarize / rank / critique / propose-grid over backtest JSON, alpha reports, sweep TSVs.
* scripts/llm_embed_logs.py — embed paragraphs into parquet via nomic-embed-text.
* docs/LOCAL_LLM_RESEARCH.md — codifies the research-only rule: nothing in core/llm/* may import from a live-trade path.
New / updated files (11 created, 9 edited)
* Strategies: strategies/vwap_zscore_reversion_strategy.py, config/strategies/vwap_zscore_reversion.toml, registry edits in strategies/strategy_manager.py + core/backtest_executor.py.
* Scripts: scripts/run_overnight_range_sweep.sh, scripts/run_strategy_matrix.sh, scripts/walkback_history.sh, scripts/llm_review.py, scripts/llm_embed_logs.py.
* LLM module: core/llm/__init__.py, core/llm/ollama_client.py.
* Docs: docs/LOCAL_LLM_RESEARCH.md, docs/perf/sweeps/CANDIDATES.md, refreshed docs/perf/researching.md, refreshed docs/RESEARCH_PATHWAYS.md, regenerated docs/MAP.md, docs/CHANGELOG.md [Unreleased].
* Tests: tests/test_vwap_zscore_reversion_smoke.py + added to pytest.ini (default suite now 14 ✅).
Verification
* .venv/bin/python -m pytest -q → 14 passed in 0.84 s.
* bash scripts/verify_handoff.sh → 13 errors / 1 warn, all pre-existing broken doc links (ROADMAP.md, 01-QUICK-START.md, FRAMEWORK_AND_ARCHITECTURE.md, etc.) and the HANDOFF.md "Last verified" header — no new errors introduced. The previously failing "stale MAP.md" check now passes after scripts/gen_map.sh.
Known issues observed during the run (not fixed — pre-existing)
* simple_momentum / simple_rth hang in replay (each bar emits a new stop-bracket and the mock-bot resorts the full bar list; CPU-bound). Mitigated for the matrix by the PER_RUN_TIMEOUT perl-alarm wrapper in run_strategy_matrix.sh. Worth a follow-up to fix the strategy or the mock bot.
* trend_scalping's __init__ builds StrategyConfig(name=…, symbols=…, enabled=True) directly — StrategyConfig is a dataclass with 13 required fields, so the call fails. Pre-existing bug; flagged in the matrix output and CANDIDATES.md.
* simple_candle emits multi-line JSON in replay (the run-summary parser only reads the last line — gives Extra data: line 1 column 5 (char 4)). Pre-existing.

Pathway 1 — overnight_range filter sweep (MNQ, 2025-12-24..2026-04-30)
Step	Description	Trades	PnL	WR	Sharpe	Max DD
P0	live TOML	4	+238	75%	11.11	85
P1	skip_weekdays=[]	7	+368	71%	9.98	125
P2	+ volatility off	45	+6 151	44%	2.93	11 924
P3	+ gap off	67	+6 511	52%	2.62	11 791
P4	all filters off	83	+6 133	50%	1.83	12 088
P5	filters on, widened bands	64	+776	51%	3.51	384
P5 is the most defensible cadence boost: 16× the trade count of live config, Sharpe 3.51, $384 DD vs $12 k DD for P4. Files in docs/perf/sweeps/overnight_range_MNQ_P*.json.
Pathway 2 — strategy × symbol matrix
* New strategy vwap_zscore_reversion (RTH-only intraday VWAP mean reversion, ATR-stopped, TP at VWAP) registered in both strategy_manager and backtest_executor. MNQ replay: 84 trades / +$1 172 / 46% WR / 2.09 Sharpe / $597 DD vs overnight_range P0 = 4 trades.
* MES / MGC pulled via scripts/walkback_history.sh (TopStepX retains ~6 weeks of 1m for MES, ~5 weeks for MGC). Walked back, merged, and re-run; vwap_zscore at default knobs is mildly negative on MES/MGC — needs per-symbol calibration before the LLM proposes a grid.
* Alpha-discovery reports for all 3 symbols at 5m → docs/alpha/{MNQ,MES,MGC}.{md,json}. None pass FDR over the short window — expected; serves as the objective filter dictionary CANDIDATES.md mentions.
Local-LLM research wiring
* core/llm/ollama_client.py — async generate / chat / embed over http://localhost:11434 (env-driven model & timeout, no new deps).
* scripts/llm_review.py — summarize / rank / critique / propose-grid over backtest JSON, alpha reports, sweep TSVs.
* scripts/llm_embed_logs.py — embed paragraphs into parquet via nomic-embed-text.
* docs/LOCAL_LLM_RESEARCH.md — codifies the research-only rule: nothing in core/llm/* may import from a live-trade path.
New / updated files (11 created, 9 edited)
* Strategies: strategies/vwap_zscore_reversion_strategy.py, config/strategies/vwap_zscore_reversion.toml, registry edits in strategies/strategy_manager.py + core/backtest_executor.py.
* Scripts: scripts/run_overnight_range_sweep.sh, scripts/run_strategy_matrix.sh, scripts/walkback_history.sh, scripts/llm_review.py, scripts/llm_embed_logs.py.
* LLM module: core/llm/__init__.py, core/llm/ollama_client.py.
* Docs: docs/LOCAL_LLM_RESEARCH.md, docs/perf/sweeps/CANDIDATES.md, refreshed docs/perf/researching.md, refreshed docs/RESEARCH_PATHWAYS.md, regenerated docs/MAP.md, docs/CHANGELOG.md [Unreleased].
* Tests: tests/test_vwap_zscore_reversion_smoke.py + added to pytest.ini (default suite now 14 ✅).
Verification
* .venv/bin/python -m pytest -q → 14 passed in 0.84 s.
* bash scripts/verify_handoff.sh → 13 errors / 1 warn, all pre-existing broken doc links (ROADMAP.md, 01-QUICK-START.md, FRAMEWORK_AND_ARCHITECTURE.md, etc.) and the HANDOFF.md "Last verified" header — no new errors introduced. The previously failing "stale MAP.md" check now passes after scripts/gen_map.sh.
Known issues observed during the run (not fixed — pre-existing)
* simple_momentum / simple_rth hang in replay (each bar emits a new stop-bracket and the mock-bot resorts the full bar list; CPU-bound). Mitigated for the matrix by the PER_RUN_TIMEOUT perl-alarm wrapper in run_strategy_matrix.sh. Worth a follow-up to fix the strategy or the mock bot.
* trend_scalping's __init__ builds StrategyConfig(name=…, symbols=…, enabled=True) directly — StrategyConfig is a dataclass with 13 required fields, so the call fails. Pre-existing bug; flagged in the matrix output and CANDIDATES.md.
* simple_candle emits multi-line JSON in replay (the run-summary parser only reads the last line — gives Extra data: line 1 column 5 (char 4)). Pre-existing.




Pathway 1 — overnight_range filter sweep (MNQ, 2025-12-24..2026-04-30)
Step	Description	Trades	PnL	WR	Sharpe	Max DD
P0	live TOML	4	+238	75%	11.11	85
P1	skip_weekdays=[]	7	+368	71%	9.98	125
P2	+ volatility off	45	+6 151	44%	2.93	11 924
P3	+ gap off	67	+6 511	52%	2.62	11 791
P4	all filters off	83	+6 133	50%	1.83	12 088
P5	filters on, widened bands	64	+776	51%	3.51	384
P5 is the most defensible cadence boost: 16× the trade count of live config, Sharpe 3.51, $384 DD vs $12 k DD for P4. Files in docs/perf/sweeps/overnight_range_MNQ_P*.json.
Pathway 2 — strategy × symbol matrix
* New strategy vwap_zscore_reversion (RTH-only intraday VWAP mean reversion, ATR-stopped, TP at VWAP) registered in both strategy_manager and backtest_executor. MNQ replay: 84 trades / +$1 172 / 46% WR / 2.09 Sharpe / $597 DD vs overnight_range P0 = 4 trades.
* MES / MGC pulled via scripts/walkback_history.sh (TopStepX retains ~6 weeks of 1m for MES, ~5 weeks for MGC). Walked back, merged, and re-run; vwap_zscore at default knobs is mildly negative on MES/MGC — needs per-symbol calibration before the LLM proposes a grid.
* Alpha-discovery reports for all 3 symbols at 5m → docs/alpha/{MNQ,MES,MGC}.{md,json}. None pass FDR over the short window — expected; serves as the objective filter dictionary CANDIDATES.md mentions.
Local-LLM research wiring
* core/llm/ollama_client.py — async generate / chat / embed over http://localhost:11434 (env-driven model & timeout, no new deps).
* scripts/llm_review.py — summarize / rank / critique / propose-grid over backtest JSON, alpha reports, sweep TSVs.
* scripts/llm_embed_logs.py — embed paragraphs into parquet via nomic-embed-text.
* docs/LOCAL_LLM_RESEARCH.md — codifies the research-only rule: nothing in core/llm/* may import from a live-trade path.
New / updated files (11 created, 9 edited)
* Strategies: strategies/vwap_zscore_reversion_strategy.py, config/strategies/vwap_zscore_reversion.toml, registry edits in strategies/strategy_manager.py + core/backtest_executor.py.
* Scripts: scripts/run_overnight_range_sweep.sh, scripts/run_strategy_matrix.sh, scripts/walkback_history.sh, scripts/llm_review.py, scripts/llm_embed_logs.py.
* LLM module: core/llm/__init__.py, core/llm/ollama_client.py.
* Docs: docs/LOCAL_LLM_RESEARCH.md, docs/perf/sweeps/CANDIDATES.md, refreshed docs/perf/researching.md, refreshed docs/RESEARCH_PATHWAYS.md, regenerated docs/MAP.md, docs/CHANGELOG.md [Unreleased].
* Tests: tests/test_vwap_zscore_reversion_smoke.py + added to pytest.ini (default suite now 14 ✅).
Verification
* .venv/bin/python -m pytest -q → 14 passed in 0.84 s.
* bash scripts/verify_handoff.sh → 13 errors / 1 warn, all pre-existing broken doc links (ROADMAP.md, 01-QUICK-START.md, FRAMEWORK_AND_ARCHITECTURE.md, etc.) and the HANDOFF.md "Last verified" header — no new errors introduced. The previously failing "stale MAP.md" check now passes after scripts/gen_map.sh.
Known issues observed during the run (not fixed — pre-existing)
* simple_momentum / simple_rth hang in replay (each bar emits a new stop-bracket and the mock-bot resorts the full bar list; CPU-bound). Mitigated for the matrix by the PER_RUN_TIMEOUT perl-alarm wrapper in run_strategy_matrix.sh. Worth a follow-up to fix the strategy or the mock bot.
* trend_scalping's __init__ builds StrategyConfig(name=…, symbols=…, enabled=True) directly — StrategyConfig is a dataclass with 13 required fields, so the call fails. Pre-existing bug; flagged in the matrix output and CANDIDATES.md.
* simple_candle emits multi-line JSON in replay (the run-summary parser only reads the last line — gives Extra data: line 1 column 5 (char 4)). Pre-existing.


Known non-ideal areas (documented / structural)	Large trading_bot.py, executor still has periodic polling alongside the event bus (docs/GOTCHAS.md), BarAggregator timer-driven partial bars, full legacy test suite under tests/ is not the default testpaths (comment in pytest.ini says many are stale).
