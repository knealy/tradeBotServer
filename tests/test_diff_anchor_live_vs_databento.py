"""Tests for ``scripts/diff_anchor_live_vs_databento.py``.

The diff tool is the operator-facing half of the 2026-06-11 fix: it consumes
a JSON anchor written by ``core.anchor_persistence`` and recomputes the same
anchor from a local databento 5m CSV, surfacing divergences that would
otherwise silently flip backtest-vs-live trade outcomes.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from datetime import date, time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "diff_anchor_live_vs_databento.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("diff_anchor_live_vs_databento", SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["diff_anchor_live_vs_databento"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def mod():
    return _load_module()


# ──────────────────── helper: write a synthetic 5m CSV ─────────────────────────

def _write_5m_csv(path: Path, rows: list) -> None:
    """rows: list of dicts with keys ts, open, high, low, close, volume."""
    with path.open("w") as f:
        f.write("timestamp,open,high,low,close,volume\n")
        for r in rows:
            f.write(
                f"{r['ts']},{r['open']},{r['high']},{r['low']},{r['close']},{r.get('volume', 0)}\n"
            )


def _databento_2026_06_11_rows():
    """The actual 12-bar anchor window from MGC_5m_databento.csv on 2026-06-11.
    Bars cover 11:00:00 → 11:55:00 UTC (07:00 → 07:55 ET; 08:00 ET is exclusive)."""
    return [
        {"ts": "2026-06-11 11:00:00", "open": 4110.30, "high": 4113.90, "low": 4107.00, "close": 4112.20},
        {"ts": "2026-06-11 11:05:00", "open": 4112.40, "high": 4113.90, "low": 4108.20, "close": 4111.60},
        {"ts": "2026-06-11 11:10:00", "open": 4111.50, "high": 4113.60, "low": 4106.50, "close": 4106.50},
        {"ts": "2026-06-11 11:15:00", "open": 4106.40, "high": 4110.00, "low": 4104.40, "close": 4106.80},
        {"ts": "2026-06-11 11:20:00", "open": 4106.90, "high": 4108.90, "low": 4103.50, "close": 4106.60},
        {"ts": "2026-06-11 11:25:00", "open": 4106.80, "high": 4111.80, "low": 4104.70, "close": 4110.70},
        {"ts": "2026-06-11 11:30:00", "open": 4110.60, "high": 4116.80, "low": 4108.30, "close": 4108.50},
        {"ts": "2026-06-11 11:35:00", "open": 4108.40, "high": 4109.40, "low": 4102.80, "close": 4103.80},
        {"ts": "2026-06-11 11:40:00", "open": 4104.20, "high": 4107.30, "low": 4101.70, "close": 4105.40},
        {"ts": "2026-06-11 11:45:00", "open": 4104.90, "high": 4107.00, "low": 4102.90, "close": 4105.70},
        {"ts": "2026-06-11 11:50:00", "open": 4105.70, "high": 4108.30, "low": 4104.70, "close": 4106.60},
        {"ts": "2026-06-11 11:55:00", "open": 4106.40, "high": 4108.00, "low": 4100.10, "close": 4100.50},
    ]


# ──────────────────── compute_databento_anchor ────────────────────────────────

def test_compute_databento_anchor_matches_real_2026_06_11(tmp_path: Path, mod):
    csv_path = tmp_path / "MGC_5m_databento.csv"
    _write_5m_csv(csv_path, _databento_2026_06_11_rows())
    result = mod.compute_databento_anchor(
        csv_path=csv_path,
        session_date_et=date(2026, 6, 11),
        window_start_et=time(7, 0),
        window_end_et=time(8, 0),
    )
    assert result is not None
    assert result["high"] == pytest.approx(4116.80)
    assert result["low"] == pytest.approx(4100.10)
    assert result["width"] == pytest.approx(16.70)
    assert result["n_bars_used"] == 12


def test_compute_databento_anchor_window_is_half_open(tmp_path: Path, mod):
    """A bar EXACTLY at ``window_end_et`` must be excluded — anchor windows
    are half-open ``[start, end)`` so the 08:00 ET bar is the first signal-side bar."""
    rows = [
        # 07:55 UTC-flip: included
        {"ts": "2026-06-11 11:55:00", "open": 100, "high": 200, "low": 50, "close": 150},
        # 08:00 ET = 12:00 UTC: MUST be excluded
        {"ts": "2026-06-11 12:00:00", "open": 100, "high": 999, "low": 1, "close": 150},
    ]
    csv_path = tmp_path / "MGC_5m_databento.csv"
    _write_5m_csv(csv_path, rows)
    result = mod.compute_databento_anchor(
        csv_path=csv_path,
        session_date_et=date(2026, 6, 11),
        window_start_et=time(7, 0),
        window_end_et=time(8, 0),
    )
    assert result is not None
    assert result["n_bars_used"] == 1
    assert result["high"] == 200
    assert result["low"] == 50


def test_compute_databento_anchor_returns_none_when_no_bars(tmp_path: Path, mod):
    csv_path = tmp_path / "MGC_5m_databento.csv"
    _write_5m_csv(csv_path, [])
    result = mod.compute_databento_anchor(
        csv_path=csv_path,
        session_date_et=date(2026, 6, 11),
        window_start_et=time(7, 0),
        window_end_et=time(8, 0),
    )
    assert result is None


def test_compute_databento_anchor_returns_none_for_missing_csv(tmp_path: Path, mod):
    result = mod.compute_databento_anchor(
        csv_path=tmp_path / "does_not_exist.csv",
        session_date_et=date(2026, 6, 11),
        window_start_et=time(7, 0),
        window_end_et=time(8, 0),
    )
    assert result is None


# ──────────────────── diff_one_anchor ─────────────────────────────────────────

def _write_live_anchor(tmp_path: Path, **overrides) -> Path:
    payload = {
        "strategy": "morning_range_reversion",
        "symbol": "MGC",
        "session_date_et": "2026-06-11",
        "computed_at_utc": "2026-06-11T12:00:00+00:00",
        "anchor": {"high": 4116.80, "low": 4100.10, "width": 16.70, "mid": 4108.45},
        "window": {
            "start_et": "07:00", "end_et": "08:00", "n_bars_used": 12,
            "first_bar_ts_utc": "2026-06-11T11:00:00+00:00",
            "last_bar_ts_utc": "2026-06-11T11:55:00+00:00",
        },
        "data_feed_health": {"available": True, "safe": True, "reason": None},
    }
    # Apply overrides via simple nested-merge
    for k, v in overrides.items():
        if isinstance(v, dict) and isinstance(payload.get(k), dict):
            payload[k] = {**payload[k], **v}
        else:
            payload[k] = v
    p = tmp_path / "morning_range_reversion_MGC_2026-06-11.json"
    p.write_text(json.dumps(payload, indent=2))
    return p


def test_diff_one_anchor_no_divergence_when_live_matches_databento(tmp_path: Path, mod):
    csv_path = tmp_path / "MGC_5m_databento.csv"
    _write_5m_csv(csv_path, _databento_2026_06_11_rows())
    anchor_path = _write_live_anchor(tmp_path)
    result = mod.diff_one_anchor(anchor_path, csv_dir=tmp_path)
    assert result.diverged is False
    assert result.delta_high == pytest.approx(0.0)
    assert result.delta_low == pytest.approx(0.0)
    assert result.delta_width == pytest.approx(0.0)
    assert result.db_n_bars == 12
    assert result.error is None


def test_diff_one_anchor_flags_the_2026_06_11_scenario(tmp_path: Path, mod):
    """The actual 2026-06-11 incident: live width 16.18 vs databento 16.70."""
    csv_path = tmp_path / "MGC_5m_databento.csv"
    _write_5m_csv(csv_path, _databento_2026_06_11_rows())
    # Live got the same low but a 0.52 pt narrower high
    anchor_path = _write_live_anchor(
        tmp_path,
        anchor={"high": 4116.28, "low": 4100.10, "width": 16.18, "mid": 4108.19},
        data_feed_health={"available": True, "safe": False, "reason": "stale_market_hub"},
    )
    result = mod.diff_one_anchor(anchor_path, csv_dir=tmp_path)
    assert result.diverged is True
    assert result.delta_high == pytest.approx(-0.52)
    assert result.delta_low == pytest.approx(0.0)
    assert result.delta_width == pytest.approx(-0.52)
    assert result.data_feed_safe is False
    assert result.data_feed_reason == "stale_market_hub"


def test_diff_one_anchor_respects_threshold(tmp_path: Path, mod):
    """A 0.09 pt difference must NOT flag as DIVERGED with the default 0.10 threshold."""
    rows = _databento_2026_06_11_rows()
    rows[6] = {**rows[6], "high": 4116.80 - 0.09}
    csv_path = tmp_path / "MGC_5m_databento.csv"
    _write_5m_csv(csv_path, rows)
    # Live still says 4116.80 (above databento's reduced 4116.71)
    anchor_path = _write_live_anchor(tmp_path)
    result = mod.diff_one_anchor(anchor_path, csv_dir=tmp_path, threshold_pts=0.10)
    assert result.diverged is False
    # And the same numbers should flag with a tighter threshold
    result2 = mod.diff_one_anchor(anchor_path, csv_dir=tmp_path, threshold_pts=0.05)
    assert result2.diverged is True


def test_diff_one_anchor_handles_missing_csv(tmp_path: Path, mod):
    anchor_path = _write_live_anchor(tmp_path)
    # csv_dir has no databento file
    result = mod.diff_one_anchor(anchor_path, csv_dir=tmp_path)
    assert result.diverged is True
    assert "no databento 5m CSV found" in (result.error or "")


def test_diff_one_anchor_handles_corrupt_json(tmp_path: Path, mod):
    csv_path = tmp_path / "MGC_5m_databento.csv"
    _write_5m_csv(csv_path, _databento_2026_06_11_rows())
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    result = mod.diff_one_anchor(bad, csv_dir=tmp_path)
    assert result.diverged is True
    assert "unreadable" in (result.error or "")


def test_diff_one_anchor_handles_no_databento_bars_for_date(tmp_path: Path, mod):
    csv_path = tmp_path / "MGC_5m_databento.csv"
    # 5m CSV covering a different date entirely
    _write_5m_csv(csv_path, [
        {"ts": "2026-05-15 11:00:00", "open": 100, "high": 110, "low": 90, "close": 100},
    ])
    anchor_path = _write_live_anchor(tmp_path)
    result = mod.diff_one_anchor(anchor_path, csv_dir=tmp_path)
    assert result.diverged is True
    assert "has no bars" in (result.error or "")


# ──────────────────── CLI surface ─────────────────────────────────────────────

def test_cli_help():
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        cwd=str(REPO), capture_output=True, text=True, check=False,
    )
    assert r.returncode == 0, r.stderr
    assert "--threshold-pts" in r.stdout
    assert "--all" in r.stdout


def test_cli_anchor_and_all_are_mutually_exclusive(tmp_path: Path):
    """Either ``--anchor`` or ``--all`` must be specified, but not both."""
    r = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=str(REPO), capture_output=True, text=True, check=False,
    )
    assert r.returncode != 0
    assert "exactly one of --anchor or --all" in r.stderr


def test_cli_json_output_exit_code_1_on_divergence(tmp_path: Path):
    csv_path = tmp_path / "MGC_5m_databento.csv"
    _write_5m_csv(csv_path, _databento_2026_06_11_rows())
    p = _write_live_anchor(
        tmp_path,
        anchor={"high": 4116.28, "low": 4100.10, "width": 16.18, "mid": 4108.19},
    )
    r = subprocess.run(
        [sys.executable, str(SCRIPT),
         "--anchor", str(p),
         "--csv-dir", str(tmp_path),
         "--json"],
        cwd=str(REPO), capture_output=True, text=True, check=False,
    )
    assert r.returncode == 1
    rows = json.loads(r.stdout)
    assert len(rows) == 1
    assert rows[0]["diverged"] is True
    assert rows[0]["delta_width"] == pytest.approx(-0.52)


def test_cli_exit_0_on_clean_diff(tmp_path: Path):
    csv_path = tmp_path / "MGC_5m_databento.csv"
    _write_5m_csv(csv_path, _databento_2026_06_11_rows())
    p = _write_live_anchor(tmp_path)
    r = subprocess.run(
        [sys.executable, str(SCRIPT),
         "--anchor", str(p),
         "--csv-dir", str(tmp_path)],
        cwd=str(REPO), capture_output=True, text=True, check=False,
    )
    assert r.returncode == 0, r.stdout + r.stderr


def test_cli_all_flag_walks_anchor_dir(tmp_path: Path):
    csv_path = tmp_path / "MGC_5m_databento.csv"
    _write_5m_csv(csv_path, _databento_2026_06_11_rows())
    anchor_dir = tmp_path / "anchors"
    anchor_dir.mkdir()
    payload = {
        "strategy": "morning_range_reversion",
        "symbol": "MGC",
        "session_date_et": "2026-06-11",
        "anchor": {"high": 4116.80, "low": 4100.10, "width": 16.70, "mid": 4108.45},
        "window": {"start_et": "07:00", "end_et": "08:00", "n_bars_used": 12},
    }
    (anchor_dir / "a.json").write_text(json.dumps(payload))
    r = subprocess.run(
        [sys.executable, str(SCRIPT),
         "--all",
         "--anchor-dir", str(anchor_dir),
         "--csv-dir", str(tmp_path)],
        cwd=str(REPO), capture_output=True, text=True, check=False,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert "DIVERGED" not in r.stdout
