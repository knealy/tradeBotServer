# Performance baseline (operator-run)

`uvloop` is installed from [requirements.txt](../../requirements.txt) and enabled from
[core/logging_setup.py](../../core/logging_setup.py) when the package is present (POSIX).

## Capturing profiles (not committed automatically)

During live or paper sessions, capture stacks and archive paths + commit SHA in
[docs/CHANGELOG.md](../CHANGELOG.md):

```bash
# macOS / Linux — attach to running bot PID
py-spy record -o docs/perf/profile-<date>.svg --pid <PID> --duration 60

# or import time
python -X importtime -c "import trading_bot" 2> docs/perf/importtimes-<date>.log
```

Commit resulting SVG/log files only after reviewing (they can be large). Nightly bench placeholders:
[docs/perf/nightly.md](nightly.md).
