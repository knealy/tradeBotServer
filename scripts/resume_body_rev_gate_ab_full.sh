#!/usr/bin/env bash
# Resume / run the full-window Gate A/B/C body_reversion grid (MNQ, MES, MGC).
# **Default: parallel** (3 workers) via ``scripts/run_body_rev_gate_ab_parallel.py``
# — same JSON outputs as the old sequential loop, ~3× wall-clock faster on a
# multi-core machine when all cells need running.
#
# Env:
#   GATE_AB_JOBS=3          max concurrent backtests (default 3)
#   BODY_REV_GATE_OUT=…     output dir (default /tmp/body_rev_gate_ab/full_2024_2026)
#   BODY_REV_SKIP_EXISTING=0  force re-run all cells
#
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
exec .venv/bin/python scripts/run_body_rev_gate_ab_parallel.py "$@"
