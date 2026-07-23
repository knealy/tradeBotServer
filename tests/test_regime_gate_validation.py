"""Smoke tests for regime gate validation harness."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _enable_regime_sizing(monkeypatch):
    monkeypatch.setenv("REGIME_SIZING_ENABLED", "1")


def test_run_validation_on_longspan(tmp_path):
    wf = ROOT / "docs/perf/regime_longspan_450d"
    if not (wf / "metrics_insights.json").exists():
        pytest.skip("regime_longspan_450d artifacts missing")

    from scripts.regime_gate_validation import run_validation

    csv_dir = ROOT / "historical_data/price"
    doc = run_validation(wf, csv_dir)

    assert doc["n_trades"] > 100
    cal = doc["calendar_gate_all"]
    assert cal["n_blocked"] >= 0
    assert "kept_pnl" in cal
    assert "regime_label_breakdown" in doc
    assert "MNQ" in doc["label_flip_stats_5m"]


def test_validation_json_roundtrip():
    path = ROOT / "docs/perf/regime_longspan_450d/regime_gate_validation.json"
    if not path.exists():
        pytest.skip("validation json not generated")
    doc = json.loads(path.read_text())
    assert doc["calendar_gate_by_strategy"]["overnight_range"]["n_blocked"] == 64
