"""Decision cache for walk-forward replay outputs (opt-in via ``--cache-decisions``).

When a recap report re-runs the *same* strategy on the *same* fold with the
*same* TOML and the *same* code, the resulting trade list is purely a
function of those inputs. ``walkforward_trade_recap_report.py`` calls into
this module to short-circuit the (expensive) subprocess + replay engine
roundtrip on cache hits, letting the chart-rendering + metrics-aggregation
phase finish in seconds.

Default is **off**. Pass ``--cache-decisions`` (or set
``BACKTEST_CACHE_DECISIONS=1``) when you're iterating on report layout /
chart shading / new metrics — anything where the underlying strategy
behaviour is unchanged. The recap report prints a hit/miss counter at the
end of every run so cache thrash never goes unnoticed.

Cache layout (under ``--cache-dir`` / default ``docs/perf/_decision_cache``)::

    <cache_dir>/
        v1_<sha256>.json    # one file per (strategy, symbol, fold, …) input bundle

Each file is the raw payload that ``_run_backtest_json`` returns —
``{"ok": True, "result": {...trade list + metrics...}}``. We hash the
following into the cache key (any change → cache miss):

- Strategy name, symbol, timeframe, start/end dates
- TOML config blob for the strategy
- Strategy ``.py`` file content (catches in-source logic changes)
- ``core/backtest/strategy_replay.py`` content (replay-engine drift)
- ``core/backtest/engine.py`` content (fill / position bookkeeping drift)
- ``extra_env`` dict (canonical-sorted; mirrors ``StrategyConfig`` env precedence)
- CSV path + size + mtime_ns (catches data refreshes / edits within range)
- Parquet sidecar (``<csv>.parquet``) size + mtime_ns when present
  (catches in-place sidecar rewrites the CSV mtime alone does not detect —
  e.g. the 2026-06-03 contract-roll quarantine that mutates the sidecar
  without touching the source CSV; see ``core/backtest/parquet_cache.py``).
- ``_CACHE_KEY_VERSION`` (bump to invalidate every cached entry at once)

This deliberately does NOT hash transitive imports beyond the two engine
files. If you change an indirect dependency that affects results (e.g.
``core/backtest/ohlcv.py`` or a shared bracket-plan helper), bump
``_CACHE_KEY_VERSION`` to wipe the cache. The cost of a false hit (stale
trade list rendered as if fresh) is bad enough that this version-key
escape hatch is worth the small operational tax.

2026-06-08 (R28 follow-up): ``_CACHE_KEY_VERSION`` bumped 1 → 2 to wipe
every entry from before the parquet-sidecar mtime fix landed. The failure
mode that motivated this: the R26 → R27 → R28 sweep series produced
materially different baseline metrics across consecutive runs on the SAME
committed TOML because the in-process runner served stale cache entries
after the contract-roll quarantine had rewritten the parquet sidecars
in place (CSV mtime unchanged → key unchanged → stale hit). See
``docs/CHANGELOG.md`` "Decision-cache stale-data invalidation gap"
known-issue entry.

Safety note: the cache intentionally lives under ``docs/perf/`` (already
in ``.gitignore`` for that subtree) so cached files don't leak into PRs.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Tuple

logger = logging.getLogger(__name__)

# Bump this whenever you intentionally invalidate every cached entry
# (e.g. changed a shared bracket helper not covered by the engine hash).
#
# v2 (2026-06-08, R28 follow-up): parquet sidecar size+mtime now folded
#     into the cache key.  The earlier v1 entries were keyed only on CSV
#     descriptor, which let an in-place sidecar rewrite (contract-roll
#     quarantine path) serve stale results when the CSV mtime hadn't
#     changed.  Bumping wipes every pre-fix entry in one shot.
_CACHE_KEY_VERSION = 2

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_DEFAULT_CACHE_DIR = _REPO_ROOT / "docs" / "perf" / "_decision_cache"

# Engine files whose content participates in the cache key. Hashed once at
# module import — recap reports are short-lived, hashing once is fine.
_ENGINE_FILES_FOR_HASH = (
    _REPO_ROOT / "core" / "backtest" / "strategy_replay.py",
    _REPO_ROOT / "core" / "backtest" / "engine.py",
)


def _sha256_of_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha256_of_file(path: Path) -> str:
    """Stream-hash a file; tolerate missing files by returning a sentinel."""
    if not path.is_file():
        return f"MISSING:{path.name}"
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _engine_fingerprint() -> str:
    parts = [_sha256_of_file(p) for p in _ENGINE_FILES_FOR_HASH]
    return _sha256_of_text("|".join(parts))


# Cached at module load so we don't re-stat the engine files per task.
_ENGINE_FP = _engine_fingerprint()


def _strategy_files(strategy: str) -> Tuple[Path, Path]:
    """Return (strategy ``.py`` path, TOML path) for ``strategy``.

    Strategy module follows the ``strategies/<name>_strategy.py`` convention
    (validated against the registered set in ``core/backtest_executor.py``).
    TOML lives at ``config/strategies/<name>.toml``. Missing files are
    permitted at hash time — they'll just hash to a sentinel and any later
    on-disk change will trigger a miss.
    """
    py = _REPO_ROOT / "strategies" / f"{strategy}_strategy.py"
    toml = _REPO_ROOT / "config" / "strategies" / f"{strategy}.toml"
    return py, toml


def _canonical_env_blob(extra_env: Mapping[str, str]) -> str:
    """Stable serialization of env overrides — key-sorted, ``KEY=VALUE`` per line."""
    return "\n".join(f"{k}={extra_env[k]}" for k in sorted(extra_env or {}))


@dataclass(frozen=True)
class CacheKeyInputs:
    """Bundle of inputs that fully determines a replay result. See module docstring."""

    strategy: str
    symbol: str
    timeframe: str
    start: date
    end: date
    csv_path: Path
    extra_env: Mapping[str, str]

    def fingerprint(self) -> str:
        """Compute the deterministic ``v{N}_<sha256>`` cache key for these inputs."""
        py, toml = _strategy_files(self.strategy)
        try:
            csv_stat = self.csv_path.stat()
            csv_descriptor = f"{self.csv_path}|{csv_stat.st_size}|{csv_stat.st_mtime_ns}"
        except OSError:
            csv_descriptor = f"{self.csv_path}|MISSING"
        # 2026-06-08 R28-follow-up: also fold the parquet sidecar's
        # size+mtime into the key.  The in-process runner reads the
        # sidecar — not the CSV — when present, so a sidecar that was
        # rewritten in place (contract-roll quarantine path) AFTER the
        # cache entry was written can otherwise serve stale rows.
        parquet_path = self.csv_path.with_suffix(self.csv_path.suffix + ".parquet")
        try:
            pq_stat = parquet_path.stat()
            parquet_descriptor = (
                f"{parquet_path}|{pq_stat.st_size}|{pq_stat.st_mtime_ns}"
            )
        except OSError:
            parquet_descriptor = f"{parquet_path}|MISSING"
        material = "|".join((
            f"v={_CACHE_KEY_VERSION}",
            f"strat={self.strategy}",
            f"sym={self.symbol}",
            f"tf={self.timeframe}",
            f"start={self.start.isoformat()}",
            f"end={self.end.isoformat()}",
            f"csv={csv_descriptor}",
            f"parquet={parquet_descriptor}",
            f"py={_sha256_of_file(py)}",
            f"toml={_sha256_of_file(toml)}",
            f"engine={_ENGINE_FP}",
            f"env={_sha256_of_text(_canonical_env_blob(self.extra_env))}",
        ))
        return f"v{_CACHE_KEY_VERSION}_{_sha256_of_text(material)}"


def cache_enabled(*, cli_flag: bool) -> bool:
    """True when the user passed ``--cache-decisions`` OR set ``BACKTEST_CACHE_DECISIONS=1``."""
    if cli_flag:
        return True
    return os.environ.get("BACKTEST_CACHE_DECISIONS", "0").strip().lower() in (
        "1", "true", "yes", "on",
    )


def cache_dir(custom: Optional[Path] = None) -> Path:
    """Resolve the on-disk cache directory; create if missing."""
    p = Path(custom) if custom is not None else _DEFAULT_CACHE_DIR
    p.mkdir(parents=True, exist_ok=True)
    return p


def lookup(inputs: CacheKeyInputs, *, cache_dir_path: Path) -> Optional[Dict[str, Any]]:
    """Return cached payload if present + readable; else None.

    A corrupted cache file (bad JSON, missing required fields) is logged at
    WARNING and treated as a miss — the caller falls through to a fresh
    replay and the cache file is overwritten on the next ``store`` call.
    """
    key = inputs.fingerprint()
    p = cache_dir_path / f"{key}.json"
    if not p.is_file():
        return None
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("decision cache: unreadable entry %s (%s); treating as miss", p.name, exc)
        return None
    if not isinstance(payload, dict) or "result" not in payload:
        logger.warning("decision cache: malformed entry %s; treating as miss", p.name)
        return None
    return payload


def store(inputs: CacheKeyInputs, payload: Dict[str, Any], *, cache_dir_path: Path) -> None:
    """Persist a successful replay result. Failures are non-fatal."""
    if not isinstance(payload, dict) or "result" not in payload:
        # Don't cache failures — a future run might succeed; the cache is
        # for stable trade-list reproductions only.
        return
    key = inputs.fingerprint()
    p = cache_dir_path / f"{key}.json"
    tmp = p.with_suffix(p.suffix + ".tmp")
    try:
        tmp.write_text(json.dumps(payload, default=str), encoding="utf-8")
        tmp.replace(p)
    except OSError as exc:
        logger.warning("decision cache: failed to write %s: %s", p.name, exc)


__all__ = [
    "CacheKeyInputs",
    "cache_dir",
    "cache_enabled",
    "lookup",
    "store",
]
