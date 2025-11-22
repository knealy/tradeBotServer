# Project Status vs Strategic Roadmaps (2025-11-22)

This note summarizes how the current codebase lines up with the guidance in:

- `docs/CURRENT_ARCHITECTURE.md`
- `docs/COMPREHENSIVE_ROADMAP.md`
- `docs/TECH_STACK_ANALYSIS.md`
- discord forum insights (`discord_chat.txt`)

## 1. Snapshot vs. Source Documents

| Reference doc | Expected state | Current reality | Gap / next action |
| --- | --- | --- | --- |
| `CURRENT_ARCHITECTURE.md` | Clear data flow from SignalR → aggregator → event bus → dashboard | SignalR + aggregator now working, but there is still no explicit event bus; components communicate via direct method calls | Introduce internal bus (see §3) so services emit/consume typed events rather than manipulating shared state |
| `COMPREHENSIVE_ROADMAP.md` Phase 3 | React dashboard parity (strategy control, analytics, risk) | UI implements core cards but still hits API edge cases (recent `strategies/null/verify`), analytics are partially powered by CLI | Harden API contracts, add schema validation, finish analytics endpoints before adding more UI surface |
| `COMPREHENSIVE_ROADMAP.md` Phase 4 | Split trading bot into modules, prep Go/Rust migration | `trading_bot.py` (>6000 LOC) still acts as monolith; no module boundaries for auth/orders/risk | Carve out modules per §2 to shrink the blast radius and make future language migration tractable |
| `TECH_STACK_ANALYSIS.md` | Hybrid stack: Python strategies + Go execution + Redis | All services still Python-only, no cache tier, but DB-backed tracker now in place | Start by defining clear execution API + event contracts before rewriting hot paths in Go |
| Discord forum themes | Professional-grade platform: event bus, translation layer, advanced analytics, AI-assisted ops | We have translation-layer groundwork (TopStep focus) but lack the 24/7 service fabric or advanced analytics metrics the community expects | Treat “pro-grade” as a set of measurable controls: event-driven core, workspace observability, analytics APIs, translation-friendly adapters |

## 2. Immediate Simplification Targets

| Area | Pain point | Suggested change |
| --- | --- | --- |
| `trading_bot.py` | 6k+ lines, hard to test, mixes auth/order/risk/state | Split into `core/session.py`, `orders/execution.py`, `risk/limits.py`, `data/feeds.py`, and a thin CLI harness. Export typed interfaces so strategies depend on abstractions instead of the massive bot object. |
| Strategy definitions | Each strategy loads env vars ad-hoc, persists custom state | Create `strategies/base_config.py` with Pydantic schemas + persistence helper; remove duplicated env parsing; unify status reporting for dashboard. |
| Logging & telemetry | Multiple modules configure logging independently | Move logging bootstrap into `infrastructure/logging.py` with env toggles (e.g., `ACCESS_LOG_VERBOSE`, `STRUCTURED_LOGS`). Hook metrics tracker into that module so logs + metrics share correlation IDs. |
| Async servers | Webhook + dashboard server share similar startup logic | Extract `servers/common/startup.py` that authenticates bot, selects account, applies persisted states, and starts bar aggregator once. Keeps both entry points lean and reduces divergence. |
| Frontend data layer | Components query APIs manually, no cache/key typing | Introduce a small “data client” in `frontend/src/services/client.ts` that exports typed hooks (`useStrategies`, `useAccounts`, etc.). This unifies error handling and prepares for GraphQL/Go gateway migration. |

## 3. Event-Bus & Hybrid Architecture Path

1. **Define event types** now, even before introducing a full bus: e.g., `AccountSelected`, `StrategyStateChanged`, `BarUpdated`, `OrderLifecycleEvent`. Represent them as dataclasses and publish through a central emitter.
2. **Implement a lightweight in-process bus** (async queue + subscribers). Start by having the webhook server publish order/strategy events and the dashboard subscribe to update WebSocket clients. This mirrors the “event bus” described in `TECH_STACK_ANALYSIS.md` and Discord threads about professional-grade translation layers.
3. **Extract execution API**: Create a narrow interface (`ExecutionService.place_order`, `cancel`, `replace`) that today forwards to Python handlers but tomorrow can proxy to a Go microservice.
4. **Introduce Redis (or even `aiocache`)** as a shared cache for hot market/strategy state once the bus is in place. This aligns with the roadmap’s Tier-2 caching plan and prepares for multi-instance deployments.
5. **Go/Rust migration (Phase 4)**: once event contracts and execution API exist, you can incrementally swap the executor with a Go service (per `TECH_STACK_ANALYSIS.md`), leaving strategies + dashboard untouched.

## 4. Professional-Grade Checklist & Next Steps

| Pillar | Action items |
| --- | --- |
| **Observability** | Add structured logging, per-request IDs, and lightweight tracing. Export key metrics (latency, order attempts, queue depth) to the existing `infrastructure.performance_metrics` tracker and surface them on the dashboard. |
| **Testing discipline** | Expand pytest coverage around the new modules (auth/orders/risk). Introduce snapshot/API contract tests for dashboard endpoints to prevent regressions like the `strategies/null/verify` call. |
| **Configuration hygiene** | Replace ad-hoc env parsing with Pydantic settings, document overrides, and ship a `config doctor` command that validates envs before deploy. |
| **Strategy + analytics UX** | From the discord feedback, users expect chart overlays, advanced analytics, and translation layers. Prioritize exposing strategy metrics (PF, WR, DD, MAE/MFE) via `/api/performance` so the dashboard can compete with the “professional-grade” stacks described in the chat. |
| **Translation / multi-broker readiness** | Abstract account selection and order routing behind interfaces even if TopStep remains the only backend today. This keeps us aligned with the “ONE BOT TO RULE THEM ALL” mindset in the community and the roadmap’s future broker goals. |

---

Deliverables from this note feed directly into the remaining plan items:

1. Use the snapshot + checklist above to report status to stakeholders.
2. Execute the module splits and logging refactors listed in §2.
3. Prototype the event bus + execution interface from §3 before investing in Go/Rust rewrites.
4. Track progress against the roadmap by updating this file (or a Notion/issue tracker) weekly.

