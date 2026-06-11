"""Pinning tests for ``scripts/validate_engine_vs_live.py``.

The script is a per-trade engine-vs-live PnL diff that we expect to
remain ZERO when fed a trade whose live-fill semantics exactly match
the engine's truth-mode model (entry at next-bar open + slip, stop
gap-through clamp, force-flat at session boundary, commission).

These tests pin the contract by feeding a hand-crafted JSON
of synthetic trades:

  * ``test_engine_matching_trade_diffs_to_zero`` — when the synthetic
    trade is built so the live fill semantics MATCH the engine model
    exactly (entry slip 0.5 ticks, clean TP), ``ΔR`` must be < 0.001 R
    (i.e. floating-point noise only).  This guards against accidental
    drift where the engine model in ``_simulate_trade_truth`` diverges
    from the diff tool's expectations.

  * ``test_missing_metadata_skips_cleanly`` — trades without
    ``stop_price`` / ``tp_price`` in their metadata are skipped with a
    clear reason, NOT crashed.  This guards the live deploy path:
    very early live trades won't have all metadata fields populated.

  * ``test_engine_alarm_for_no_force_flat_held_overnight`` — the
    inverse: a live trade that "magically" held overnight (impossible
    in real life because TopStepX flats positions at 16:00 ET) and
    won an absurd 5R must be flagged ``alarm`` by the engine because
    the engine would have force-flat'd it.  This is the canary for
    "the engine is correctly modelling force-flat".

These tests use a tiny on-disk fixture rather than the canonical
~800 k-bar 5m CSV; ``simulate_price_action_trades._read_csv`` is the
loader so a 24-bar synthetic CSV is enough.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "validate_engine_vs_live.py"


def _write_synthetic_5m_csv(target: Path, *, n_bars: int = 96,
                             start: str = "2026-05-28 13:30:00",
                             base_px: float = 7500.0,
                             step: float = 0.50) -> None:
    """Write a deterministic up-trending 5m bar series.

    Each bar is exactly ``step`` higher than the previous open, with a
    tight (close - open) ± 0.25 range — enough to trigger TP within a
    few bars on a 1-pt geometry but not so volatile that SL/TP collide.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    t0 = datetime.fromisoformat(start)
    lines = ["timestamp,open,high,low,close,volume"]
    for i in range(n_bars):
        o = base_px + i * step
        h = o + 0.50
        l = o - 0.25
        c = o + step
        ts = (t0 + timedelta(minutes=5 * i)).strftime("%Y-%m-%d %H:%M:%S")
        lines.append(f"{ts},{o:.2f},{h:.2f},{l:.2f},{c:.2f},100")
    target.write_text("\n".join(lines) + "\n")


def _run(*args: str) -> subprocess.CompletedProcess:
    cmd = [sys.executable, str(SCRIPT), *args]
    return subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, timeout=60)


# ─────────────────────────── tests ───────────────────────────────────


def test_engine_matching_trade_diffs_to_zero(tmp_path: Path) -> None:
    """Synthetic live trade whose fill perfectly mirrors engine model
    must produce ΔR ≈ 0 (float noise) and verdict ``ok``."""
    csv_path = tmp_path / "MES_5m_databento.csv"
    _write_synthetic_5m_csv(csv_path)

    # Entry bar @ index 5 (timestamp t0 + 25 min). Engine fills at
    # next-bar open = base + 6 * 0.5 = 7503.0. With slip 0.5 ticks
    # (0.125), entry_px = 7503.125.  SL 4pt below, TP 2pt above.
    entry_idx = 5
    base_px = 7500.0
    step = 0.50
    next_open = base_px + (entry_idx + 1) * step  # 7503.0
    actual_entry = next_open + 0.125  # 0.5 tick slip
    sl_px = next_open - 4.0
    tp_px = next_open + 2.0
    actual_exit = tp_px
    # PnL: (TP - actual_entry) * MES_pv - $5 commission
    pnl = (actual_exit - actual_entry) * 5.0 - 5.0

    t0 = datetime(2026, 5, 28, 13, 30, 0, tzinfo=timezone.utc)
    entry_time = (t0 + timedelta(minutes=5 * entry_idx)).isoformat().replace("+00:00", "Z")
    exit_time = (t0 + timedelta(minutes=5 * (entry_idx + 5))).isoformat().replace("+00:00", "Z")
    trade = {
        "symbol": "MES", "side": "BUY", "quantity": 1,
        "entry_price": actual_entry, "exit_price": actual_exit,
        "pnl": pnl,
        "entry_time": entry_time, "exit_time": exit_time,
        "metadata": {"stop_price": sl_px, "tp_price": tp_px},
    }
    json_path = tmp_path / "trade.json"
    json_path.write_text(json.dumps([trade]))

    # Invoke the script with the synthetic CSV.  ``--symbol`` is
    # required so the script knows tick/point conventions.
    result = _run(
        "--trades-json", str(json_path),
        "--csv-5m", str(csv_path),
        "--symbol", "MES",
        "--no-1m",  # synthetic 5m must not be mixed with canonical 1m
        "--force-flat-et", "",  # disable force-flat for this test
        "--json",
    )
    assert result.returncode == 0, f"stderr: {result.stderr}\nstdout: {result.stdout}"
    # The JSON is preceded by a "loaded N trades" line; parse the LAST
    # JSON-shaped block (script prints both a status line and the JSON).
    out = result.stdout
    json_start = out.index("[")
    rows = json.loads(out[json_start:])
    assert len(rows) == 1
    r = rows[0]
    assert r["verdict"] == "ok", r
    assert abs(r["diff_r"]) < 0.001, f"expected ΔR ≈ 0, got {r['diff_r']}"
    assert r["engine_exit_reason"] == "take_profit"


def test_missing_metadata_skips_cleanly(tmp_path: Path) -> None:
    """Trades without stop_price / tp_price in metadata must be skipped
    with a non-empty ``reason``, NOT crashed."""
    csv_path = tmp_path / "MES_5m_databento.csv"
    _write_synthetic_5m_csv(csv_path)
    t0 = datetime(2026, 5, 28, 13, 55, 0, tzinfo=timezone.utc)
    trade = {
        "symbol": "MES", "side": "BUY", "quantity": 1,
        "entry_price": 7503.0, "exit_price": 7505.0, "pnl": 5.0,
        "entry_time": t0.isoformat().replace("+00:00", "Z"),
        "exit_time": (t0 + timedelta(minutes=15)).isoformat().replace("+00:00", "Z"),
        "metadata": {},  # MISSING stop_price / tp_price
    }
    json_path = tmp_path / "trade.json"
    json_path.write_text(json.dumps([trade]))
    result = _run(
        "--trades-json", str(json_path),
        "--csv-5m", str(csv_path),
        "--symbol", "MES",
        "--no-1m",
        "--json",
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    rows = json.loads(result.stdout[result.stdout.index("["):])
    assert len(rows) == 1
    r = rows[0]
    assert r["verdict"] == "skip"
    assert "stop" in r.get("reason", "").lower() or "tp" in r.get("reason", "").lower()


def test_engine_alarm_for_no_force_flat_held_overnight(tmp_path: Path) -> None:
    """Live trade that "won" 5R by holding overnight — engine has
    ``--force-flat-et 16:00`` enabled so it would have force-flat'd
    at a smaller R.  ``ΔR`` must be large enough to trip the
    ``alarm`` verdict (|ΔR| >= 1.0).  This is the canary for engine
    force-flat modelling being on for the live-validation tool.
    """
    csv_path = tmp_path / "MES_5m_databento.csv"
    # Cross-session series: bars 0..5 are pre-16:00 ET (entry window),
    # 6..30 push past 16:00 ET force-flat cutoff, and final bars are
    # WAY above entry so the live "claimed 5R win" looks plausible.
    # 13:55 UTC = 09:55 ET (within RTH for entry); 20:00 UTC = 16:00 ET.
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["timestamp,open,high,low,close,volume"]
    base = 7500.0
    t0 = datetime(2026, 5, 28, 13, 55, 0)  # 09:55 ET
    for i in range(60):
        ts = (t0 + timedelta(minutes=5 * i)).strftime("%Y-%m-%d %H:%M:%S")
        o = base + i * 0.10  # tiny drift up
        h = o + 0.25
        l = o - 0.25
        c = o + 0.10
        lines.append(f"{ts},{o:.2f},{h:.2f},{l:.2f},{c:.2f},100")
    # Push a giant gap up AFTER 16:00 ET so the "live held overnight"
    # claim of +5R looks real but the engine would have force-flat'd.
    # 16:05 ET = 20:05 UTC, which is bar index ~(20:05 - 13:55) / 5 = ~73 → adjust
    csv_path.write_text("\n".join(lines) + "\n")

    # SL 2pt below, TP 10pt above — TP only hit AFTER force-flat cutoff.
    next_open = base + 1 * 0.10
    sl_px = next_open - 2.0
    tp_px = next_open + 10.0  # never hit before 16:00 ET in this series
    actual_entry = next_open + 0.125
    actual_exit = tp_px  # pretend live magically got TP
    # Live PnL claim (5R): 10pt × $5/pt - $5 = $45
    pnl = (actual_exit - actual_entry) * 5.0 - 5.0

    t0_utc = t0.replace(tzinfo=timezone.utc)
    trade = {
        "symbol": "MES", "side": "BUY", "quantity": 1,
        "entry_price": actual_entry, "exit_price": actual_exit, "pnl": pnl,
        "entry_time": t0_utc.isoformat().replace("+00:00", "Z"),
        "exit_time": (t0_utc + timedelta(hours=12)).isoformat().replace("+00:00", "Z"),
        "metadata": {"stop_price": sl_px, "tp_price": tp_px},
    }
    json_path = tmp_path / "trade.json"
    json_path.write_text(json.dumps([trade]))
    result = _run(
        "--trades-json", str(json_path),
        "--csv-5m", str(csv_path),
        "--symbol", "MES",
        "--no-1m",
        "--force-flat-et", "16:00",
        "--max-bars", "300",
        "--json",
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    rows = json.loads(result.stdout[result.stdout.index("["):])
    assert len(rows) == 1
    r = rows[0]
    # Engine must have force-flat'd; the live claim of +5R is mismatched.
    assert r["engine_exit_reason"] == "replay_force_flat_et", r
    assert r["verdict"] == "alarm", (
        f"expected alarm verdict; got {r['verdict']} with ΔR={r['diff_r']}"
    )
    assert abs(r["diff_r"]) >= 1.0, r
