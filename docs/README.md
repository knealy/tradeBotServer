# Documentation index

**Read first:** [AGENTS.md](../AGENTS.md) → [HANDOFF.md](HANDOFF.md) → [MAP.md](MAP.md) → [PLAYBOOK.md](PLAYBOOK.md).

This page lists the **canonical** topics. Deeper notes live under [reference/](reference/) and [perf/](perf/). Superseded docs were removed; use `git log` / history for old write-ups.

---

## Canonical topics

| Doc | Purpose |
|-----|---------|
| [ARCHITECTURE.md](ARCHITECTURE.md) | System shape, data flow, main modules |
| [DEPLOYMENT.md](DEPLOYMENT.md) | Railway, Docker, process supervision |
| [DATABASE.md](DATABASE.md) | Postgres tables, batch writers, retention |
| [ENV_VARS.md](ENV_VARS.md) | Environment variables and `.env` |
| [STRATEGIES.md](STRATEGIES.md) | Strategy configs, registration, Pine notes |
| [STRATEGY_DEVELOPMENT.md](STRATEGY_DEVELOPMENT.md) | Decision tree: sample vs API, replay, research runner, ship path |
| [BACKTESTING.md](BACKTESTING.md) | Offline / backtest entrypoints |
| [DASHBOARD.md](DASHBOARD.md) | Webhook server, static dashboard, GUI |
| [RUST.md](RUST.md) | Optional Rust hot path |
| [TESTING.md](TESTING.md) | pytest layout and commands |
| [CHANGELOG.md](CHANGELOG.md) | Release-style change log |
| [ROADMAP.md](ROADMAP.md) | What is planned next |
| [DECISIONS.md](DECISIONS.md) | ADRs |
| [GOTCHAS.md](GOTCHAS.md) | Footguns |
| [CONVENTIONS.md](CONVENTIONS.md) | Style and async rules |
| [HANDOFF_INDEX.md](HANDOFF_INDEX.md) | One-line index of the handoff kit |

---

## Legacy and reference

- **[reference/](reference/)** — long-form deployment, env, Rust, and API notes.
- **[01-QUICK-START.md](01-QUICK-START.md)** — optional fast path (may be stale; prefer HANDOFF + PLAYBOOK).

---

## Regenerate

- **Module map:** `scripts/gen_map.sh` → [MAP.md](MAP.md)
- **Doc / link check:** `scripts/verify_handoff.sh`
