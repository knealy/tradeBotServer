# Makefile — convenience targets for humans + Cursor agents.
#
# Design notes:
#   * Every target prints what it's doing on the first line so the agent can
#     confirm it ran the right one from a single tool roundtrip.
#   * The "agent loop" targets (backtest, summary, smoke) are tuned for the
#     "tweak a knob → re-verify" iteration, NOT for canonical reporting.
#     Canonical reports still go through ``scripts/walkforward_*`` directly.
#   * Defaults align with the round-3 backtest performance work:
#     ``--in-process`` + ``--cache-decisions`` are ON via the script defaults
#     (we don't pass anything; the scripts default these now).
#   * No PHONY duplication: list every PHONY target once at the top.
#   * Variables overridable per-invocation: e.g.
#         make backtest STRATEGY=overnight_range DAYS=180 FOLDS=6 SYMBOLS=MNQ,MES,MGC

.PHONY: help test verify map bench backtest summary smoke verify-fast \
        backtest-recap backtest-competition refresh-data refresh-data-check \
        allocate probe

# Defaults — override on the command line: ``make backtest STRATEGY=...``
PY            ?= .venv/bin/python
STRATEGY      ?= morning_range_reversion
DAYS          ?= 90
FOLDS         ?= 6
TIMEFRAME     ?= 5m
SYMBOLS       ?= MNQ,MES,MGC
OUT_DIR       ?= docs/perf/_agent_runs/$(STRATEGY)_$(DAYS)d_$(FOLDS)f
WORKERS       ?= 6
PARITY_WINDOW ?= 2026-04-01..2026-04-30

help: ## Show this help (lists every target with its one-line description).
	@printf '\nUsage: make <target> [VAR=value ...]\n\n'
	@printf 'Common variables: STRATEGY=%s DAYS=%s FOLDS=%s SYMBOLS=%s\n\n' \
		"$(STRATEGY)" "$(DAYS)" "$(FOLDS)" "$(SYMBOLS)"
	@grep -hE '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  %-22s %s\n", $$1, $$2}'
	@printf '\n'

test: ## Run the full pytest suite (~2 minutes; skips tests/test_hub_bracket_smoke.py for the pre-existing SyntaxError).
	pytest --ignore=tests/test_hub_bracket_smoke.py

verify: ## Run scripts/verify_handoff.sh (doc-link drift + MAP freshness).
	scripts/verify_handoff.sh

verify-fast: ## Backtest tests + parity tests only (~90 s — what you want after a backtest engine change).
	@printf '[verify-fast] backtest + parity tests (~90 s)\n'
	$(PY) -m pytest -q tests/ \
		--ignore=tests/test_hub_bracket_smoke.py \
		--ignore=tests/test_parquet_memory_cache.py \
		-k "backtest or replay or ohlcv or decision_cache or parquet_sidecar"

map: ## Regenerate docs/MAP.md from the current directory tree.
	scripts/gen_map.sh

bench: ## Run scripts/run_bench.sh (full backtest perf benchmark suite).
	scripts/run_bench.sh

# ──────────────────────────── Agent iteration loop ────────────────────────────
# These four targets are the fast-path for the "tweak → verify" loop. They use
# the in-process + decision-cache defaults that round 3 shipped, so a re-run
# with no TOML change finishes in a few seconds.

backtest: ## Run a walkforward strategy-competition matrix (STRATEGY, DAYS, FOLDS, SYMBOLS, OUT_DIR).
	@printf '[backtest] %s × %s symbols × %s folds (%s days) → %s\n' \
		"$(STRATEGY)" "$(SYMBOLS)" "$(FOLDS)" "$(DAYS)" "$(OUT_DIR)"
	@mkdir -p $(OUT_DIR)
	$(PY) scripts/walkforward_strategy_competition.py \
		--strategies $(STRATEGY) \
		--symbols $(SYMBOLS) \
		--days $(DAYS) --folds $(FOLDS) --timeframe $(TIMEFRAME) \
		--out-dir $(OUT_DIR) \
		--workers $(WORKERS)

backtest-recap: ## Run the trade-recap walkforward (trade charts + HTML index) for STRATEGY.
	@printf '[backtest-recap] %s recap → %s\n' "$(STRATEGY)" "$(OUT_DIR)"
	@mkdir -p $(OUT_DIR)
	$(PY) scripts/walkforward_trade_recap_report.py \
		--strategies $(STRATEGY) \
		--symbols $(SYMBOLS) \
		--days $(DAYS) --folds $(FOLDS) --timeframe $(TIMEFRAME) \
		--out-dir $(OUT_DIR) \
		--workers $(WORKERS)

backtest-competition: ## Run the full multi-strategy competition matrix on default knobs.
	@printf '[backtest-competition] %s × %s folds × %s symbols\n' \
		"morning_range_reversion,overnight_range,body_reversion" \
		"$(FOLDS)" "$(SYMBOLS)"
	@mkdir -p docs/perf/_agent_runs/competition_$(DAYS)d_$(FOLDS)f
	$(PY) scripts/walkforward_strategy_competition.py \
		--strategies morning_range_reversion,overnight_range,body_reversion \
		--symbols $(SYMBOLS) \
		--days $(DAYS) --folds $(FOLDS) --timeframe $(TIMEFRAME) \
		--out-dir docs/perf/_agent_runs/competition_$(DAYS)d_$(FOLDS)f \
		--workers $(WORKERS)

summary: ## Print a JSON summary of every docs/perf/ run for STRATEGY (use FORMAT=table for human view).
	@$(PY) scripts/agent_perf_summary.py $(STRATEGY) $(if $(WINDOWS),--windows $(WINDOWS)) $(if $(LIMIT),--limit $(LIMIT)) --format $(if $(FORMAT),$(FORMAT),json)

smoke: ## Run the byte-identical parity test for STRATEGY (4-month + 1-month windows; ~90 s).
	@printf '[smoke] %s fast-loop parity (1m + 4m windows)\n' "$(STRATEGY)"
	$(PY) -m pytest tests/test_backtest_fast_loop_parity.py -v \
		-k "$(STRATEGY)"

refresh-data: ## Pull fresh 1m + 5m bars from TopStepX onto canonical *_databento.csv files (MES/MNQ/MGC).
	@printf '[refresh-data] stitching fresh 1m + 5m bars onto canonical CSVs\n'
	bash scripts/refresh_historical.sh

refresh-data-check: ## Report current staleness of every *_databento.csv (no API calls; offline-safe).
	@bash scripts/refresh_historical.sh --check

allocate: ## Run portfolio allocator (rank production strategies + suggest per-account assignment).
	@$(PY) scripts/portfolio_allocator.py

probe: ## Truth-mode-always pattern edge probe. Usage: make probe PATTERN=dragonfly_doji BIAS=contrarian [OB=1]
	@$(PY) scripts/probe_pattern_edge.py \
		--pattern $(if $(PATTERN),$(PATTERN),dragonfly_doji) \
		--bias $(if $(BIAS),$(BIAS),contrarian) \
		$(if $(OB),--require-ob) \
		$(if $(SYMBOLS),--symbols $(SYMBOLS))
