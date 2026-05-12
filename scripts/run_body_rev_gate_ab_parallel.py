#!/usr/bin/env python3
"""
Run the full-window **Gate A / B / C** ``body_reversion`` replay grid (MNQ, MES, MGC)
in **parallel** where safe (each job is its own process + output file).

Same semantics as ``scripts/resume_body_rev_gate_ab_full.sh``, but uses
``concurrent.futures.ThreadPoolExecutor`` so up to **N** backtest subprocesses run at once
(default **3**, one per symbol).

Environment:

- ``GATE_AB_JOBS`` — max concurrent workers (default **3**).
- ``BODY_REV_GATE_OUT`` — output directory (default ``/tmp/body_rev_gate_ab/full_2024_2026``).
- ``BODY_REV_SKIP_EXISTING`` — if ``0``, re-run even when JSON > 200 bytes (default **1**).

Examples::

  .venv/bin/python scripts/run_body_rev_gate_ab_parallel.py
  GATE_AB_JOBS=2 .venv/bin/python scripts/run_body_rev_gate_ab_parallel.py
"""
from __future__ import annotations

import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, Tuple

ROOT = Path(__file__).resolve().parent.parent
START = "2024-01-01"
END = "2026-05-01"

_GATES: Dict[str, Tuple[str, str]] = {
    "A": (
        "BODY_REVERSION_SIGNAL_REQUIRE_HIGH_ATR=true",
        "BODY_REVERSION_SIGNAL_REQUIRE_RANGE_EXPAND=false",
    ),
    "B": (
        "BODY_REVERSION_SIGNAL_REQUIRE_HIGH_ATR=false",
        "BODY_REVERSION_SIGNAL_REQUIRE_RANGE_EXPAND=true",
    ),
    "C": (
        "BODY_REVERSION_SIGNAL_REQUIRE_HIGH_ATR=true",
        "BODY_REVERSION_SIGNAL_REQUIRE_RANGE_EXPAND=true",
    ),
}


def _need_run(json_path: Path, skip_existing: bool) -> bool:
    if not skip_existing:
        return True
    if not json_path.is_file():
        return True
    return json_path.stat().st_size < 200


def _run_cell(args: Tuple[str, str, Path, bool]) -> Tuple[str, str, int, str]:
    sym, gate, out_dir, skip_existing = args
    out_dir = Path(out_dir)
    csv = ROOT / "historical_data" / "price" / f"{sym}_5m_databento.csv"
    json_path = out_dir / f"body_rev_{sym}_{gate}.json"
    log_path = out_dir / f"body_rev_{sym}_{gate}.log"
    if not csv.is_file():
        return sym, gate, 2, f"missing {csv}"
    if not _need_run(json_path, skip_existing):
        return sym, gate, 0, f"skip existing {json_path.name}"

    env = os.environ.copy()
    a, b = _GATES[gate]
    env["ENABLE_SIGNALR"] = "false"
    env["PYTHONUNBUFFERED"] = "1"
    # Assign KEY=value pairs for StrategyConfig env override
    for piece in (a, b):
        key, _, val = piece.partition("=")
        env[key.strip()] = val.strip()

    cmd = [
        sys.executable,
        str(ROOT / "core" / "backtest_executor.py"),
        "--strategy=body_reversion",
        f"--symbol={sym}",
        "--timeframe=5m",
        f"--csv={csv}",
        f"--start={START}",
        f"--end={END}",
        "--replay",
        "--format=json",
    ]
    out_dir.mkdir(parents=True, exist_ok=True)
    with json_path.open("w", encoding="utf-8") as jf, log_path.open("w", encoding="utf-8") as lf:
        p = subprocess.run(cmd, cwd=str(ROOT), env=env, stdout=jf, stderr=lf)
    return sym, gate, p.returncode, f"wrote {json_path}" if p.returncode == 0 else f"exit {p.returncode}"


def main() -> int:
    out_dir = Path(os.environ.get("BODY_REV_GATE_OUT", "/tmp/body_rev_gate_ab/full_2024_2026"))
    skip = os.environ.get("BODY_REV_SKIP_EXISTING", "1").strip() not in ("0", "false", "no")
    try:
        n_workers = max(1, int(os.environ.get("GATE_AB_JOBS", "3")))
    except ValueError:
        n_workers = 3

    jobs: list[Tuple[str, str, Path, bool]] = []
    for sym in ("MNQ", "MES", "MGC"):
        for gate in ("A", "B", "C"):
            jobs.append((sym, gate, out_dir, skip))

    print(f"out_dir={out_dir}  workers={n_workers}  skip_existing={skip}", file=sys.stderr)
    failures = 0
    with ThreadPoolExecutor(max_workers=n_workers) as ex:
        futs = {ex.submit(_run_cell, j): j for j in jobs}
        for fut in as_completed(futs):
            sym, gate, code, msg = fut.result()
            print(f"[{sym}][{gate}] code={code} {msg}", file=sys.stderr)
            if code != 0:
                failures += 1
    print(f"DONE {out_dir} failures={failures}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
