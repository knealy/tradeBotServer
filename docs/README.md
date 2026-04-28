# Documentation index

**Read first:** [AGENTS.md](../AGENTS.md) → [HANDOFF.md](HANDOFF.md) → [MAP.md](MAP.md) → [PLAYBOOK.md](PLAYBOOK.md).

This page lists the **canonical** topics. Deeper or historical material lives under [reference/](reference/), [archive/](archive/), and [perf/](perf/).

---

## Canonical topics

| Doc | Purpose |
|-----|---------|
| [ARCHITECTURE.md](ARCHITECTURE.md) | System shape, data flow, main modules |
| [DEPLOYMENT.md](DEPLOYMENT.md) | Railway, Docker, process supervision |
| [DATABASE.md](DATABASE.md) | Postgres tables, batch writers, retention |
| [ENV_VARS.md](ENV_VARS.md) | Environment variables and `.env` |
| [STRATEGIES.md](STRATEGIES.md) | Strategy configs, registration, Pine notes |
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
- **[archive/](archive/)** — dated fix write-ups and old versions; not kept in sync with code.
- **[01-QUICK-START.md](01-QUICK-START.md)** — optional fast path (may be stale; prefer HANDOFF + PLAYBOOK).

---

## Regenerate

- **Module map:** `scripts/gen_map.sh` → [MAP.md](MAP.md)
- **Doc / link check:** `scripts/verify_handoff.sh`
