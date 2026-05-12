"""Smoke tests for scripts/print_weekly_income.py helpers."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "print_weekly_income.py"


def test_summary_only_prints_avg_weekly(tmp_path: Path) -> None:
    doc = {
        "ok": True,
        "result": {
            "symbol": "MNQ",
            "strategy": "body_reversion",
            "period": "2024-01-01 to 2024-01-29",
            "total_pnl": 700.0,
            "total_trades": 10,
        },
    }
    p = tmp_path / "x.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    out = subprocess.check_output([sys.executable, str(SCRIPT), str(p)], text=True)
    assert "avg_pnl_per_week_usd" in out
    assert "700.00" in out.replace(",", "")


def test_trades_weekly_bucket(tmp_path: Path) -> None:
    doc = {
        "ok": True,
        "result": {
            "symbol": "MNQ",
            "strategy": "body_reversion",
            "period": "2024-01-01 to 2024-01-14",
            "total_pnl": 150.0,
            "trades": [
                {"exit_time": "2024-01-02T16:00:00+00:00", "pnl": 100.0},
                {"exit_time": "2024-01-09T16:00:00+00:00", "pnl": 50.0},
            ],
        },
    }
    p = tmp_path / "t.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    out = subprocess.check_output([sys.executable, str(SCRIPT), str(p)], text=True)
    assert "2024-W01" in out or "2024-W02" in out
    assert "iso_week" in out
