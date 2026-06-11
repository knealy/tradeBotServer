"""Pinning tests for ``scripts/portfolio_allocator.py``.

The allocator's contract:

  1. PRODUCTION_TIER is the single source of truth for which
     strategies are in the live arsenal.  Adding a strategy here
     should be the ONLY change needed to surface it in the allocator.
  2. The CLI emits JSON with stable top-level keys
     (``reports``, ``correlation``, ``conflicts``, ``allocation``,
     ``params``).
  3. Each ``reports`` entry has stable per-strategy keys.
  4. Allocation respects ``--accounts`` bound and never assigns the
     same strategy twice.
  5. Pearson correlation is symmetric (corr(a, b) == corr(b, a)).
  6. ``_pearson`` returns 0 for n < 3 or constant series.
"""
from __future__ import annotations

import json
import math
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "portfolio_allocator.py"


def _run(*args: str, timeout: int = 60) -> subprocess.CompletedProcess:
    cmd = [sys.executable, str(SCRIPT), *args]
    return subprocess.run(cmd, cwd=str(ROOT), capture_output=True,
                          text=True, timeout=timeout)


# ─────────────────── unit tests on the math helpers ──────────────────


def test_pearson_returns_zero_for_short_series() -> None:
    from scripts.portfolio_allocator import _pearson
    assert _pearson([], []) == 0.0
    assert _pearson([1.0], [2.0]) == 0.0
    assert _pearson([1.0, 2.0], [3.0, 4.0]) == 0.0


def test_pearson_returns_one_for_identical_series() -> None:
    from scripts.portfolio_allocator import _pearson
    xs = [1.0, 2.0, 3.0, 4.0, 5.0]
    assert abs(_pearson(xs, xs) - 1.0) < 1e-9


def test_pearson_returns_negative_one_for_perfectly_anti_correlated() -> None:
    from scripts.portfolio_allocator import _pearson
    xs = [1.0, 2.0, 3.0, 4.0, 5.0]
    ys = [5.0, 4.0, 3.0, 2.0, 1.0]
    assert abs(_pearson(xs, ys) - (-1.0)) < 1e-9


def test_pearson_returns_zero_for_constant_series() -> None:
    """Constant series → zero variance → corr undefined → return 0."""
    from scripts.portfolio_allocator import _pearson
    assert _pearson([1.0, 1.0, 1.0], [2.0, 3.0, 4.0]) == 0.0


# ─────────────────── integration tests on the CLI ────────────────────


def test_production_tier_has_at_least_one_strategy() -> None:
    """The arsenal source of truth must not be empty."""
    from scripts.portfolio_allocator import PRODUCTION_TIER
    assert len(PRODUCTION_TIER) >= 1
    for spec in PRODUCTION_TIER:
        assert "id" in spec and "truth_recap_dir" in spec and "toml" in spec, spec


def test_unknown_candidate_errors() -> None:
    result = _run("--candidates", "not_a_real_strategy", "--accounts", "1")
    assert result.returncode != 0
    msg = (result.stderr + result.stdout).lower()
    assert "unknown strategy id" in msg, f"stdout={result.stdout} stderr={result.stderr}"


def test_json_schema_pins_top_level_keys() -> None:
    """JSON output structure is stable for downstream consumers."""
    result = _run("--json")
    assert result.returncode == 0, f"stderr={result.stderr}"
    # Skip warning lines before JSON (script prints "⚠ missing truth recap" for
    # strategies whose dir doesn't exist locally).
    out = result.stdout
    json_start = out.index('{\n  "reports"')
    doc = json.loads(out[json_start:])
    expected_top = {"reports", "correlation", "conflicts", "allocation", "params"}
    missing = expected_top - set(doc.keys())
    assert not missing, f"missing top-level keys: {missing}"
    assert isinstance(doc["reports"], list)
    assert isinstance(doc["correlation"], dict)
    assert isinstance(doc["conflicts"], list)
    assert isinstance(doc["allocation"], list)
    if doc["reports"]:
        rep_keys = {"strategy_id", "n_trades", "total_pnl", "max_drawdown_pct",
                    "recovery_factor", "sharpe_ish", "score", "symbols",
                    "direction"}
        missing_rep = rep_keys - set(doc["reports"][0].keys())
        assert not missing_rep, f"missing per-report keys: {missing_rep}"


def test_allocation_respects_account_limit() -> None:
    """``--accounts N`` must produce at most N assignments."""
    for n in (1, 2, 3):
        result = _run("--json", "--accounts", str(n))
        assert result.returncode == 0, f"stderr={result.stderr}"
        out = result.stdout
        json_start = out.index('{\n  "reports"')
        doc = json.loads(out[json_start:])
        assert len(doc["allocation"]) <= n, (
            f"--accounts {n} produced {len(doc['allocation'])} assignments"
        )


def test_allocation_no_duplicate_strategies() -> None:
    """Each strategy can appear in at most one account assignment."""
    result = _run("--json")
    assert result.returncode == 0
    out = result.stdout
    json_start = out.index('{\n  "reports"')
    doc = json.loads(out[json_start:])
    assigned = [a["strategy"] for a in doc["allocation"]]
    assert len(assigned) == len(set(assigned)), (
        f"duplicate strategy in allocation: {assigned}"
    )


def test_correlation_keys_are_symmetric_pairs() -> None:
    """Correlation dict uses ``a__vs__b`` keys where a < b lexically;
    no duplicate pair entries (b__vs__a)."""
    result = _run("--json")
    assert result.returncode == 0
    out = result.stdout
    json_start = out.index('{\n  "reports"')
    doc = json.loads(out[json_start:])
    seen = set()
    for key in doc["correlation"]:
        a, b = key.split("__vs__")
        # Pair must be lexically ordered (a <= b) so each pair appears
        # ONCE in the dict.
        assert a <= b, f"pair {key} not lexically ordered"
        pair = frozenset([a, b])
        assert pair not in seen, f"duplicate pair {pair}"
        seen.add(pair)
