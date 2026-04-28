<!-- Code conventions. Mirror of .cursor/rules/*.mdc in human-readable form. -->

# Conventions

Rules for new code and reviews. Each item: rule, one-line rationale, optional example.

### Logging

- **Rule:** `logger = logging.getLogger(__name__)`; call `configure_logging()` only through [core/logging_setup.py](../core/logging_setup.py); never `logging.basicConfig` outside that module.

- **Rationale:** One handler graph; avoids duplicate or conflicting sinks.

```python
import logging
logger = logging.getLogger(__name__)

# Good
logger.info("startup complete")
logger.debug("tick %s", symbol)

# Bad
logging.basicConfig(level=logging.DEBUG)
print("loaded")  # bypasses levels entirely
```

- **Rule:** File handler at INFO; console at WARNING; push chatty traces to `logger.debug`.

- **Rationale:** Terminal stays operable; disks hold audits.

### Configuration

- **Rule:** Strategies read via `StrategyConfig.get*` / `symbol_override`; avoid `os.getenv` in `strategies/*`.

- **Rationale:** Single precedence chain (see [core/strategy_config.py](../core/strategy_config.py#L7-L12)) and easy fixtures.

```python
# Good
size = cfg.get_int("risk.position_size", default=1)

# Bad
size = int(os.getenv("RISK_POSITION_SIZE", "1"))
```

- **Rule:** Document new keys in [config/strategies/_schema.toml](../config/strategies/_schema.toml). Secrets stay in `.env`; tunables in [config/strategies/](../config/strategies/).

- **Rationale:** Operators know where to look; secrets never hit git.

### Async I/O

- **Rule:** No `requests` or `time.sleep` in `async def`; use `aiohttp` and `await asyncio.sleep`. Long blocking sync work uses `await asyncio.to_thread(...)`.

- **Rationale:** The strategy stack shares one event loop with SignalR and websockets.

```python
# Good
await asyncio.sleep(1)

# Bad
time.sleep(1)
requests.get(url)
```

### Error handling

- **Rule:** Forbid bare `except:` and silent `except Exception: pass`. Log with `logger.exception` and re-raise unless the caller owns recovery.

- **Rationale:** Production failures must be visible and attributable.

```python
# Good
try:
    risky()
except Exception:
    logger.exception("risky failed")
    raise

# Bad
try:
    risky()
except Exception:
    pass
```

### Events

- **Rule:** `from core.event_bus import EventBus` and `from core.events import Event, EventType` only.

- **Rationale:** The old `events/` package is gone; `Event(type=…, data={…}, source="…")` matches [core/events.py](../core/events.py#L51-L65).

```python
# Good
await bus.publish(Event(type=EventType.ORDER_FILLED, data={"id": oid}, source="adapter"))

# Bad — wrong field names
await bus.publish(Event(event_type="filled", payload={}))
```

### Modules & imports

- **Rule:** Lazy-import heavy stacks (`pandas`, `numpy`, `matplotlib`, …) inside functions.

- **Rationale:** Import time stays low on trading entrypoints.

- **Rule:** New logic lives in focused modules; resist padding [trading_bot.py](../trading_bot.py).

- **Rationale:** The god-module is legacy surface area, not a template.

### Testing

- **Rule:** Ship at least one regression test under [tests/](../tests/) with meaningful Phase-3 fixes; benches under [tests/bench/](../tests/bench/) when needed.

- **Rationale:** Prevents repeat incidents without bloating unit suites.

- **Rule:** Prefer `pytest` markers; never commit `pytest.skip` as a TODO stub.

- **Rationale:** Skipped tests mask missing coverage forever.

### Documentation

- **Rule:** Long-lived prose in [docs/](../docs/); operational deltas in [docs/CHANGELOG.md](../docs/CHANGELOG.md). Avoid one-off markdown dumps in repo root.

- **Rationale:** One handbook tree plus chronological history.

- **Rule:** When these conventions or entrypoints move, update `.cursor/rules/*.mdc`, this file, and the “last verified” cues in [docs/HANDOFF.md](../docs/HANDOFF.md).

- **Rationale:** Humans and agents read different surfaces—keep them aligned.

### Files & VCS

- **Rule:** Never commit `.env` or secrets—only [`.env.example`](../.env.example). Ignore `rust/target/`, `__pycache__/`, `*.log`, `logs/` per [`.gitignore`](../.gitignore).

- **Rationale:** History leaks credentials; binaries clog diffs.

- **Rule:** Delete dead code outright; no `archive/` folders (see ADRs).

- **Rationale:** Git history already stores the past.
