"""Smoke tests for scripts/strategy_litmus.py."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_litmus_morning_range_why_exits_zero():
    repo = Path(__file__).resolve().parent.parent
    r = subprocess.run(
        [sys.executable, str(repo / "scripts" / "strategy_litmus.py"), "morning_range", "--why"],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode == 0
    assert "Intrabar" in r.stdout or "intrabar" in r.stdout.lower()


def test_litmus_morning_range_on_csv_when_present():
    repo = Path(__file__).resolve().parent.parent
    csv_path = repo / "historical_data" / "price" / "MNQ_5m_databento.csv"
    if not csv_path.is_file():
        return
    r = subprocess.run(
        [
            sys.executable,
            str(repo / "scripts" / "strategy_litmus.py"),
            "morning_range",
            "--csv",
            str(csv_path),
            "--last-days",
            "45",
            "--compare-intrabar",
        ],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode == 0, r.stderr
    assert "conservative:" in r.stdout
    assert "optimistic:" in r.stdout
