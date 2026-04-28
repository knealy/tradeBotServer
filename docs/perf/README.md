# Performance artifacts

Profiling snapshots and bench history live here.

- `profile-before.svg`, `profile-after.svg` — `py-spy record` snapshots
  captured during live market hours. Add a new pair when running a
  Phase 2 microopt experiment.
- `import-times.log` — output of `python -X importtime -c 'import trading_bot'`.
  Cap total < 2 s once Phase 2.8 lazy-imports land.
- `nightly.md` — auto-generated nightly bench results from `tests/bench/`.

Generate the baseline:

```bash
py-spy record -o docs/perf/profile-before.svg --pid "$(pgrep -f strategy_executor.py | head -1)" -d 600
python -X importtime -c "import trading_bot" 2> docs/perf/import-times.log
```
