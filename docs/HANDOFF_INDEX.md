# HANDOFF_INDEX.md — Documentation Kit

One-line description of every file in the handoff kit.

---

## Kit files

| File | Description |
|------|-------------|
| [`AGENTS.md`](../AGENTS.md) | Entry point for any LLM agent or new dev: project summary, golden rules, entrypoints, change checklist. |
| [`.cursor/rules/*.mdc`](../.cursor/rules/) | Cursor-specific coding constraints enforced at agent invocation; authoritative if present, otherwise defer to AGENTS.md golden rules. |
| [`docs/HANDOFF.md`](HANDOFF.md) | Deep-dive mental model: tick lifecycle, process layout, config flow, operating notes, what was cleaned up, what is deferred. |
| [`docs/MAP.md`](MAP.md) | Annotated directory tree — every non-trivial file and module explained in one line; regenerate with `scripts/gen_map.sh`. |
| [`docs/PLAYBOOK.md`](PLAYBOOK.md) | Operational runbooks: start/stop procedures, Railway deploy, account switch, log drain, credential rotation. |
| [`docs/DECISIONS.md`](DECISIONS.md) | Architecture Decision Records (ADRs): why SignalR over REST polling, why psycopg2 over an ORM, why Rust is opt-in, why god-module decomposition is deferred. |
| [`docs/GOTCHAS.md`](GOTCHAS.md) | Known footguns and subtle bugs: TOPSETPX_* typo aliases (trading_bot.py:180–181), logs/ file-vs-dir collision, Rust hotpath coverage gaps, caffeinate dependency, JWT race on reconnect. |
| [`docs/CONVENTIONS.md`](CONVENTIONS.md) | Code style, async patterns, naming conventions, test approach, import rules (no `requests` in async, no `os.getenv` in strategies). |
| [`docs/CHANGELOG.md`](CHANGELOG.md) | Running record of all notable changes; add to `[Unreleased]` on every PR. |
| [`docs/COMPREHENSIVE_ROADMAP.md`](COMPREHENSIVE_ROADMAP.md) | Long-horizon roadmap: trading_bot.py decomposition, full Rust migration or removal, WebSocket dashboard push, strategy TOML completion. |
| [`docs/perf/`](perf/) | Performance analysis documents: Rust vs Python benchmarks, hot-path profiling notes, optimization summaries. |

---

## Recommended read order

### (a) New agent / dev picking up the codebase

1. [`AGENTS.md`](../AGENTS.md) — orientation and golden rules (5 min)
2. [`docs/HANDOFF.md`](HANDOFF.md) — mental model, lifecycle, process layout (15 min)
3. [`docs/MAP.md`](MAP.md) — locate the module you need to touch (5 min)
4. [`docs/CONVENTIONS.md`](CONVENTIONS.md) — coding rules before writing a single line (5 min)
5. [`docs/DECISIONS.md`](DECISIONS.md) — understand why things are shaped the way they are before proposing a redesign (10 min)
6. [`docs/GOTCHAS.md`](GOTCHAS.md) — scan for landmines in the area you are working (5 min)

### (b) Operator firefighting a live issue

1. [`docs/PLAYBOOK.md`](PLAYBOOK.md) — start/stop commands, log locations, account switch
2. [`docs/GOTCHAS.md`](GOTCHAS.md) — known failure modes and workarounds
3. [`docs/HANDOFF.md`](HANDOFF.md) § "Where to start when …" — issue-specific entry points
4. [`docs/CHANGELOG.md`](CHANGELOG.md) — check if a recent change is the cause

### (c) Maintainer adding a new strategy

1. [`docs/HANDOFF.md`](HANDOFF.md) § "Adding a new strategy" — step-by-step procedure
2. [`docs/CONVENTIONS.md`](CONVENTIONS.md) — naming and async patterns
3. `config/strategies/_schema.toml` — required TOML keys
4. `strategies/strategy_base.py` — base class API
5. `strategies/strategy_manager.py` — registration
6. [`docs/CHANGELOG.md`](CHANGELOG.md) — record the addition under `[Unreleased]`
