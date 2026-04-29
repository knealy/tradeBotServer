# Operations tuning (live trading hot path)

Use this after you have a **profile**, not before. Guessing bottlenecks on a network-bound stack usually wastes time.

## 1. Capture evidence

- **CPU / asyncio stalls:** [py-spy](https://github.com/benfred/py-spy) on the running `strategy_executor` PID (see [README.md](README.md) → `docs/perf/README.md`).
- **Python + memory:** [Scalene](https://github.com/plasma-umass/scalene) with `-m core.strategy_executor ...` as documented in `docs/perf/README.md`.
- **Import / startup:** `python3 -X importtime -c "import trading_bot"` → save log under `docs/perf/`.

Focus areas called out in reviews:

- `brokers/topstepx_adapter.py` — REST placement, history, contract cache.
- `core/websocket_manager.py` / `core/user_hub_manager.py` — SignalR callbacks (keep them non-blocking).
- `core/state_cache.py` — cache misses vs hits (batch snapshot refresh).

## 2. Rust (`TOPSTEPX_USE_RUST`) vs alternatives

| Work type | What actually limits speed | Best lever |
|-----------|---------------------------|------------|
| REST / WebSocket I/O | Network RTT, API rate limits, number of round-trips | Fewer calls, parallel `gather` where endpoints are independent, connection reuse (`aiohttp` session), caching |
| JSON parse / small Python overhead | CPU, but usually small next to RTT | Orjson-style fast JSON (already partially addressed via `core.json_fast`), avoid huge `json.dumps` on hot paths |
| Tight order-state loops | GIL + Python | Optional Rust (or Cython) **after** profiling proves CPU dominance |

**Recommendation:** For **network-bound** trading flows, **Rust rarely beats a well-async Python client** on wall-clock latency: the wire dominates. Rust is most justified when profiling shows **CPU-bound** work (serialization storms, heavy numeric paths) *after* you have reduced round-trips and logging I/O.

Always **fail closed to Python** when Rust raises (adapter already falls back on several paths).

## 3. Implemented patterns in this repo

- **Parallel positions + orders:** `TopStepXAdapter.get_positions_and_open_orders_parallel` overlaps two HTTP waits. `TopStepXTradingBot.get_positions_and_orders_batch` uses it; `StateCache` uses the batch on cache miss under a shared `snapshot_{account_id}` lock so one refresh fills **both** orders and positions caches when either side misses.
- **Logging:** Hot-path `INFO` logs that fire every poll were moved toward `DEBUG` in the adapter where safe—reduces disk/fsync pressure in production.
- **uvloop:** Installed from `configure_logging()` when the package is present (Linux/macOS Docker). Image sets `USE_UVLOOP=1` by default; disable with `USE_UVLOOP=0` or `DISABLE_UVLOOP=1` if you debug loop-specific issues.
- **User Hub deferred queue:** `core/hub_deferred_queue.HubDeferredWorkQueue` (bounded, one worker) sequences heavy work from `core/user_hub_handlers` (PnL, EventBus, GUI `broadcast_update`) after sync callbacks invalidate caches. Capacity: `HUB_DEFERRED_QUEUE_MAX` (default 128). Capture stacks with `scripts/profile_strategy_executor.sh` (see repo [README.md](../../README.md)).

## 4. Event loop hygiene (checklist)

- No `requests` / blocking DNS / `time.sleep` inside `async def` (see [AGENTS.md](../../AGENTS.md)).
- SignalR handlers: invalidate caches and schedule work; do not run long synchronous work inline.
- Prefer **bounded** queues if you offload CPU work; unbounded `create_task` fan-out can hide backpressure.

## 5. When to revisit Rust

Re-run py-spy after:

1. Batching / parallel snapshot refresh is in use.
2. Log volume on the hot path is trimmed.
3. DB/async writers are not saturating.

If the flame graph still shows Python hot spots *above* I/O wait, then extend the Rust executor for those call sites with benchmarks before/after.
