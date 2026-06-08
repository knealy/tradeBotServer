"""Decision-cache key invariants and round-trip semantics.

These tests pin the behaviour ``walkforward_trade_recap_report.py`` relies
on when ``--cache-decisions`` is set: same inputs hit the same key, any
change to a tracked input forces a miss, and corrupted cache files are
gracefully treated as misses (not raised).
"""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path

import pytest

from core.backtest.decision_cache import (
    CacheKeyInputs,
    cache_dir,
    cache_enabled,
    lookup,
    store,
)


def _csv(tmp_path: Path, name: str = "demo.csv") -> Path:
    p = tmp_path / name
    p.write_text("timestamp,open,high,low,close,volume\n2024-01-02 10:00:00,1,2,0.5,1.5,10\n")
    return p


def _inputs(csv: Path, **kw) -> CacheKeyInputs:
    base = dict(
        strategy="overnight_range",
        symbol="MNQ",
        timeframe="5m",
        start=date(2024, 1, 1),
        end=date(2024, 1, 31),
        csv_path=csv,
        extra_env={"FOO": "bar", "BAZ": "qux"},
    )
    base.update(kw)
    return CacheKeyInputs(**base)


def test_fingerprint_is_deterministic(tmp_path: Path) -> None:
    csv = _csv(tmp_path)
    a = _inputs(csv).fingerprint()
    b = _inputs(csv).fingerprint()
    assert a == b
    # _CACHE_KEY_VERSION was bumped 1 → 2 on 2026-06-08 when the parquet
    # sidecar size+mtime joined the cache key. Loosely assert the prefix
    # so future bumps don't ripple to every test in this file.
    from core.backtest.decision_cache import _CACHE_KEY_VERSION
    assert a.startswith(f"v{_CACHE_KEY_VERSION}_")
    assert len(a) > 20  # v{N}_ + 64-char sha256


def test_fingerprint_changes_on_symbol(tmp_path: Path) -> None:
    csv = _csv(tmp_path)
    a = _inputs(csv).fingerprint()
    b = _inputs(csv, symbol="MES").fingerprint()
    assert a != b


def test_fingerprint_changes_on_date_window(tmp_path: Path) -> None:
    csv = _csv(tmp_path)
    a = _inputs(csv).fingerprint()
    b = _inputs(csv, end=date(2024, 2, 28)).fingerprint()
    assert a != b


def test_fingerprint_changes_on_env(tmp_path: Path) -> None:
    csv = _csv(tmp_path)
    a = _inputs(csv).fingerprint()
    b = _inputs(csv, extra_env={"FOO": "different"}).fingerprint()
    assert a != b


def test_fingerprint_env_order_does_not_matter(tmp_path: Path) -> None:
    """Sorted-key canonicalization: ``{a:1,b:2}`` keys to same fingerprint as ``{b:2,a:1}``."""
    csv = _csv(tmp_path)
    a = _inputs(csv, extra_env={"X": "1", "Y": "2"}).fingerprint()
    b = _inputs(csv, extra_env={"Y": "2", "X": "1"}).fingerprint()
    assert a == b


def test_fingerprint_invalidates_on_csv_mtime(tmp_path: Path) -> None:
    """File modification touches mtime_ns → key changes → cached entry becomes a miss."""
    csv = _csv(tmp_path)
    a = _inputs(csv).fingerprint()
    # Bump mtime
    csv.write_text(csv.read_text() + "2024-01-02 10:05:00,1.5,2.5,1,2,11\n")
    os.utime(csv, None)
    b = _inputs(csv).fingerprint()
    assert a != b


def test_fingerprint_invalidates_on_parquet_sidecar_mtime(tmp_path: Path) -> None:
    """Sidecar rewritten in place → cache key changes even if CSV mtime is unchanged.

    Regression for the 2026-06-08 R28-follow-up fix.  The contract-roll
    quarantine path mutates ``<csv>.parquet`` without touching the source
    CSV; the prior v1 key only hashed the CSV descriptor, so stale results
    could be served after a sidecar rewrite.  v2 folds the sidecar
    size+mtime into the key.
    """
    csv = _csv(tmp_path)
    parquet = csv.with_suffix(csv.suffix + ".parquet")
    parquet.write_bytes(b"fake-parquet-payload-v1")
    a = _inputs(csv).fingerprint()
    # Re-write the sidecar with different content (and let the FS bump mtime).
    import time
    time.sleep(0.01)  # mtime_ns resolution guard on coarse filesystems
    parquet.write_bytes(b"fake-parquet-payload-v2-after-contract-roll-quarantine")
    os.utime(parquet, None)
    b = _inputs(csv).fingerprint()
    assert a != b, (
        "parquet sidecar rewrite must invalidate the cache key — otherwise "
        "the in-process runner can serve stale rows after a sidecar mutation"
    )


def test_fingerprint_unaffected_when_no_sidecar_present(tmp_path: Path) -> None:
    """When no ``<csv>.parquet`` exists, the descriptor degrades to a stable
    ``MISSING`` sentinel so the key is reproducible across runs that all
    lack a sidecar (typical for transient test fixtures)."""
    csv = _csv(tmp_path)
    parquet = csv.with_suffix(csv.suffix + ".parquet")
    assert not parquet.exists()
    a = _inputs(csv).fingerprint()
    b = _inputs(csv).fingerprint()
    assert a == b


def test_store_and_lookup_round_trip(tmp_path: Path) -> None:
    csv = _csv(tmp_path)
    cdir = cache_dir(tmp_path / "_cache")
    ck = _inputs(csv)
    payload = {"ok": True, "result": {"total_trades": 7, "trades": [{"pnl": 100.0}]}}
    store(ck, payload, cache_dir_path=cdir)
    out = lookup(ck, cache_dir_path=cdir)
    assert out == payload


def test_lookup_miss_returns_none(tmp_path: Path) -> None:
    csv = _csv(tmp_path)
    cdir = cache_dir(tmp_path / "_cache")
    assert lookup(_inputs(csv), cache_dir_path=cdir) is None


def test_store_skips_failed_payload(tmp_path: Path) -> None:
    """Failures (``ok=False`` / no result key) should not be cached."""
    csv = _csv(tmp_path)
    cdir = cache_dir(tmp_path / "_cache")
    ck = _inputs(csv)
    store(ck, {"ok": False, "error": "boom"}, cache_dir_path=cdir)
    assert lookup(ck, cache_dir_path=cdir) is None


def test_corrupted_cache_file_is_miss(tmp_path: Path) -> None:
    """A truncated/garbage cache file is logged + treated as miss (not raised)."""
    csv = _csv(tmp_path)
    cdir = cache_dir(tmp_path / "_cache")
    ck = _inputs(csv)
    p = cdir / f"{ck.fingerprint()}.json"
    p.write_text("{ this is not valid json", encoding="utf-8")
    assert lookup(ck, cache_dir_path=cdir) is None


def test_cache_enabled_env_var() -> None:
    """``BACKTEST_CACHE_DECISIONS=1`` enables the cache without ``--cache-decisions``."""
    os.environ.pop("BACKTEST_CACHE_DECISIONS", None)
    assert cache_enabled(cli_flag=False) is False
    os.environ["BACKTEST_CACHE_DECISIONS"] = "1"
    try:
        assert cache_enabled(cli_flag=False) is True
    finally:
        os.environ.pop("BACKTEST_CACHE_DECISIONS", None)
    # CLI flag wins regardless
    assert cache_enabled(cli_flag=True) is True
