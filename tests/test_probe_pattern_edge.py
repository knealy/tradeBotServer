"""Smoke + pinning tests for ``scripts/probe_pattern_edge.py``.

The probe is the truth-mode-always pattern screening tool.  These
tests pin the CLI contract and the JSON output schema so downstream
tooling (capital allocator, automated promotion pipeline) can rely on
the shape.

Tests:
  * ``test_unknown_pattern_errors`` — typo'd pattern names must exit
    non-zero with a clear "unknown pattern" error.
  * ``test_unknown_symbol_errors`` — typo'd symbols must exit non-zero.
  * ``test_json_schema_pins_top_level_keys`` — the JSON output must
    have the canonical top-level keys so the capital-allocator
    downstream can rely on the shape.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "probe_pattern_edge.py"


def _run(*args: str, timeout: int = 30) -> subprocess.CompletedProcess:
    cmd = [sys.executable, str(SCRIPT), *args]
    return subprocess.run(cmd, cwd=str(ROOT), capture_output=True,
                          text=True, timeout=timeout)


def test_unknown_pattern_errors() -> None:
    """Typo'd pattern name must fail loudly before any bars are loaded."""
    result = _run("--pattern", "not_a_real_pattern_xyz")
    assert result.returncode != 0
    assert "unknown pattern" in result.stderr.lower() or "unknown pattern" in result.stdout.lower(), (
        f"expected 'unknown pattern' error; got stderr={result.stderr} stdout={result.stdout}"
    )


def test_unknown_symbol_errors() -> None:
    """Typo'd symbol name must fail loudly."""
    result = _run("--pattern", "dragonfly_doji", "--symbols", "FAKE")
    assert result.returncode != 0
    assert "unknown symbol" in result.stderr.lower() or "unknown symbol" in result.stdout.lower(), (
        f"stderr={result.stderr} stdout={result.stdout}"
    )


def test_json_schema_pins_top_level_keys(tmp_path: Path) -> None:
    """JSON output must have stable top-level keys for downstream tools.

    Uses a tiny --since/--until window on MES (whichever bars exist)
    so the run completes fast; we don't care about the actual stats,
    only that the structure is right.
    """
    csv = ROOT / "historical_data" / "price" / "MES_5m_databento.csv"
    if not csv.exists():
        pytest.skip("MES 5m CSV missing; cannot smoke-test")

    # Tiny window — 7 days of data is enough for a few signals.
    result = _run(
        "--pattern", "dragonfly_doji", "--bias", "contrarian",
        "--symbols", "MES",
        "--since", "2026-05-01", "--until", "2026-05-07",
        "--no-1m",  # skip 1m to speed things up
        "--min-n", "0",  # don't fail on low signal count
        "--json",
        timeout=90,
    )
    assert result.returncode == 0, (
        f"non-zero exit ({result.returncode}); stderr={result.stderr}"
    )
    # Output may contain status lines before the JSON; find the start
    # of the outermost JSON object by looking for the script's
    # canonical first key.
    out = result.stdout
    json_start = out.index('{\n  "pattern"')
    doc = json.loads(out[json_start:])
    expected = {"pattern", "bias", "since", "until", "filters",
                "geometry", "engine", "per_symbol", "verdict"}
    missing = expected - set(doc.keys())
    assert not missing, f"JSON missing keys: {missing}"
    assert doc["pattern"] == "dragonfly_doji"
    assert doc["bias"] == "contrarian"
    assert doc["filters"]["require_ob"] is False
    assert isinstance(doc["per_symbol"], list)
    assert len(doc["per_symbol"]) == 1
    sym = doc["per_symbol"][0]
    sym_keys = {"symbol", "n_signals", "n_trades", "n_wins", "n_losses",
                "wr_pct", "mean_r", "std_r"}
    missing_sym = sym_keys - set(sym.keys())
    assert not missing_sym, f"per_symbol missing keys: {missing_sym}"
    assert sym["symbol"] == "MES"
    # Verdict must be one of three values.
    assert doc["verdict"] in ("PRODUCTIONABLE", "MARGINAL", "NO EDGE"), doc["verdict"]


