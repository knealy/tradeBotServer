#!/usr/bin/env python3
"""
Run many ``core/backtest_executor.py`` replay jobs **in parallel** from a **JSONL manifest**.

Each line is one JSON object::

  {
    "strategy": "body_reversion",
    "symbol": "MNQ",
    "csv": "historical_data/price/MNQ_5m_databento.csv",
    "start": "2026-01-01",
    "end": "2026-03-01",
    "output": "/tmp/manifest_mnq_q1.json",
    "include_trades": false,
    "env": {"BODY_REVERSION_SIGNAL_REQUIRE_HIGH_ATR": "true"}
  }

Paths are resolved relative to the **repo root** unless absolute. ``env`` is merged
into the subprocess environment (optional).

Use this to sweep parameters / symbols **in unison** with strategy iteration: edit
``config/backtest_matrices/*.jsonl``, run once, then ``scripts/print_weekly_income.py``
on outputs that used ``"include_trades": true``.

Examples::

  .venv/bin/python scripts/run_backtest_manifest.py config/backtest_matrices/example_body_reversion_q1.jsonl
  BACKTEST_MANIFEST_JOBS=4 .venv/bin/python scripts/run_backtest_manifest.py my_jobs.jsonl
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT = Path(__file__).resolve().parent.parent


def _resolve_path(p: str) -> Path:
    x = Path(p)
    if x.is_absolute():
        return x
    return (ROOT / x).resolve()


def _one_job(job: Dict[str, Any]) -> Tuple[str, int, str]:
    jid = str(job.get("id", job.get("strategy", "?") + "_" + job.get("symbol", "?")))
    out = _resolve_path(job["output"])
    csv_path = _resolve_path(job["csv"])
    if not csv_path.is_file():
        return jid, 2, f"missing csv {csv_path}"

    env = os.environ.copy()
    for k, v in (job.get("env") or {}).items():
        env[str(k)] = str(v)
    env.setdefault("ENABLE_SIGNALR", "false")
    env.setdefault("PYTHONUNBUFFERED", "1")

    cmd = [
        sys.executable,
        str(ROOT / "core" / "backtest_executor.py"),
        f"--strategy={job['strategy']}",
        f"--symbol={job['symbol']}",
        "--replay",
        "--format=json",
        f"--csv={csv_path}",
        f"--start={job['start']}",
        f"--end={job['end']}",
    ]
    tf = job.get("timeframe")
    if tf:
        cmd.append(f"--timeframe={tf}")
    if job.get("include_trades"):
        cmd.append("--include-trades")

    out.parent.mkdir(parents=True, exist_ok=True)
    log_path = out.with_suffix(out.suffix + ".run.log")
    with out.open("w", encoding="utf-8") as jf, log_path.open("w", encoding="utf-8") as lf:
        p = subprocess.run(cmd, cwd=str(ROOT), env=env, stdout=jf, stderr=lf, text=True)
    if p.returncode != 0:
        tail = log_path.read_text(encoding="utf-8", errors="replace")[-4000:]
        return jid, p.returncode, tail or f"log {log_path}"
    return jid, 0, str(out)


def _load_jobs(path: Path) -> List[Dict[str, Any]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    jobs: List[Dict[str, Any]] = []
    for i, line in enumerate(lines, 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            jobs.append(json.loads(line))
        except json.JSONDecodeError as e:
            raise ValueError(f"{path}:{i}: invalid JSON: {e}") from e
    return jobs


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("manifest", type=Path, help="JSONL manifest path")
    ap.add_argument(
        "--jobs",
        type=int,
        default=None,
        help="Max concurrent subprocesses (default: env BACKTEST_MANIFEST_JOBS or 3)",
    )
    args = ap.parse_args()
    manifest = args.manifest.resolve()
    if not manifest.is_file():
        print(f"error: manifest not found: {manifest}", file=sys.stderr)
        return 1

    try:
        n_workers = int(
            args.jobs
            if args.jobs is not None
            else os.environ.get("BACKTEST_MANIFEST_JOBS", "3")
        )
    except ValueError:
        n_workers = 3
    n_workers = max(1, n_workers)

    jobs = _load_jobs(manifest)
    if not jobs:
        print("error: no jobs in manifest", file=sys.stderr)
        return 1

    print(f"manifest={manifest}  jobs={len(jobs)}  workers={n_workers}", file=sys.stderr)
    failures = 0
    with ThreadPoolExecutor(max_workers=n_workers) as ex:
        futs = {ex.submit(_one_job, j): j for j in jobs}
        for fut in as_completed(futs):
            jid, code, msg = fut.result()
            print(f"[{jid}] exit={code} {msg}", file=sys.stderr)
            if code != 0:
                failures += 1
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
