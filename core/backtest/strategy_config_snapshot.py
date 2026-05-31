"""Snapshot strategy TOML configs into walk-forward report directories.

Used by ``scripts/walkforward_trade_recap_report.py`` and
``scripts/walkforward_last_trades_charts.py`` so every report directory carries
the **exact** TOML(s) that produced the metrics. Lets a reader replicate the
performance months later without having to remember what was in
``config/strategies/<name>.toml`` at run time.

Two layers:

1. **Snapshot file** — verbatim copy of each strategy's TOML at
   ``<out_dir>/config/<name>.toml``. Drop this back into
   ``config/strategies/`` to reproduce the run.
2. **Sidecar JSON / HTML** — parsed TOML + matching env-var overrides
   (filtered by ``<NAME_UPPER>_`` prefix, mirroring ``StrategyConfig`` env
   precedence) for at-a-glance diffing in the metrics page.

Stdlib only (Python 3.11+).
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tomllib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_DEFAULT_CONFIG_DIR = _REPO_ROOT / "config" / "strategies"


@dataclass
class StrategyConfigSnapshot:
    """One strategy's snapshotted TOML config + env-var overrides."""

    name: str
    source: Optional[Path]
    snapshot: Optional[Path]
    data: Dict[str, Any] = field(default_factory=dict)
    raw_text: str = ""
    env_overrides: Dict[str, str] = field(default_factory=dict)
    error: Optional[str] = None


def _env_prefix(strategy: str) -> str:
    return strategy.upper().replace("-", "_") + "_"


def _matching_env_overrides(
    strategy: str,
    env: Mapping[str, str],
) -> Dict[str, str]:
    """Return env vars whose key starts with the strategy's ``StrategyConfig`` prefix."""
    pfx = _env_prefix(strategy)
    return {k: str(v) for k, v in env.items() if str(k).startswith(pfx)}


def snapshot_strategy_configs(
    strategies: Iterable[str],
    *,
    out_dir: Path,
    env_overrides: Optional[Mapping[str, str]] = None,
    config_dir: Optional[Path] = None,
) -> List[StrategyConfigSnapshot]:
    """Copy each strategy's TOML to ``<out_dir>/config/<name>.toml`` and parse it.

    Missing TOMLs are non-fatal: a snapshot row with ``error`` is returned so the
    report still renders an explicit "no config file" marker.
    """
    cfg_dir = config_dir or _DEFAULT_CONFIG_DIR
    snap_dir = out_dir / "config"
    snap_dir.mkdir(parents=True, exist_ok=True)
    env = dict(env_overrides or {})

    results: List[StrategyConfigSnapshot] = []
    for name in strategies:
        per_env = _matching_env_overrides(name, env)
        src = cfg_dir / f"{name}.toml"
        if not src.is_file():
            results.append(
                StrategyConfigSnapshot(
                    name=name,
                    source=None,
                    snapshot=None,
                    env_overrides=per_env,
                    error=f"No TOML at {src}",
                )
            )
            continue
        dst = snap_dir / f"{name}.toml"
        try:
            shutil.copyfile(src, dst)
            raw = src.read_text(encoding="utf-8")
            data = tomllib.loads(raw)
        except Exception as exc:
            logger.exception("snapshot_strategy_configs: %s -> %s", src, dst)
            results.append(
                StrategyConfigSnapshot(
                    name=name,
                    source=src,
                    snapshot=dst if dst.is_file() else None,
                    env_overrides=per_env,
                    error=str(exc),
                )
            )
            continue
        results.append(
            StrategyConfigSnapshot(
                name=name,
                source=src,
                snapshot=dst,
                data=data,
                raw_text=raw,
                env_overrides=per_env,
            )
        )
    return results


def _git_head_sha(repo_root: Optional[Path] = None) -> Optional[str]:
    """Best-effort short HEAD SHA. Returns None outside a git checkout."""
    root = repo_root or _REPO_ROOT
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(root),
            capture_output=True,
            text=True,
            check=False,
            timeout=2.0,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    sha = (out.stdout or "").strip()
    return sha or None


def snapshots_to_json(
    snapshots: Iterable[StrategyConfigSnapshot],
    *,
    extra: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Machine-readable bundle for ``strategy_configs.json``."""
    by_name: Dict[str, Any] = {}
    for s in snapshots:
        by_name[s.name] = {
            "source_path": str(s.source) if s.source else None,
            "snapshot_path": str(s.snapshot) if s.snapshot else None,
            "data": s.data,
            "env_overrides": s.env_overrides,
            "error": s.error,
        }
    bundle: Dict[str, Any] = {
        "captured_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "git_head": _git_head_sha(),
        "strategies": by_name,
    }
    if extra:
        bundle.update(dict(extra))
    return bundle


def _html_escape(s: str) -> str:
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def snapshots_to_html(
    snapshots: Iterable[StrategyConfigSnapshot],
    *,
    section_id: str = "config-snapshot",
    title: str = "Replay configuration (exact TOML)",
    json_link: Optional[str] = None,
) -> str:
    """HTML block listing per-strategy snapshot links + verbatim TOML contents.

    The TOML body is wrapped in a ``<details>`` element so the section stays
    compact by default.  ``json_link`` is an optional href to the machine-
    readable bundle written via :func:`snapshots_to_json`.
    """
    snaps = list(snapshots)
    if not snaps:
        return ""

    parts: List[str] = []
    parts.append(f'<h2 id="{section_id}">{_html_escape(title)}</h2>')
    intro = (
        "Each strategy's <code>config/strategies/&lt;name&gt;.toml</code> is "
        "copied verbatim into <code>config/&lt;name&gt;.toml</code> next to this "
        "report. Reproduce the run by dropping the snapshot back into "
        "<code>config/strategies/</code> and re-applying any environment "
        "overrides listed below (mirrors <code>StrategyConfig</code> precedence: "
        "CLI &gt; env &gt; TOML &gt; default)."
    )
    parts.append(f'<p class="muted">{intro}</p>')
    if json_link:
        parts.append(
            f'<p class="muted">Machine-readable bundle: '
            f'<a href="{_html_escape(json_link)}"><code>{_html_escape(json_link)}</code></a>.</p>'
        )

    for s in snaps:
        snap_link = (
            f'<a href="config/{_html_escape(s.name)}.toml"><code>config/{_html_escape(s.name)}.toml</code></a>'
            if s.snapshot is not None
            else "<em>not snapshotted</em>"
        )
        src_html = (
            f"<code>{_html_escape(str(s.source))}</code>"
            if s.source is not None
            else "<em>missing</em>"
        )
        err_html = (
            f'<p class="muted neg">error: <code>{_html_escape(str(s.error))}</code></p>'
            if s.error
            else ""
        )
        if s.env_overrides:
            env_body = "".join(
                f"<tr><td><code>{_html_escape(k)}</code></td>"
                f"<td><code>{_html_escape(v)}</code></td></tr>"
                for k, v in sorted(s.env_overrides.items())
            )
        else:
            env_body = "<tr><td colspan='2' class='muted'>— none —</td></tr>"
        toml_block = (
            f"<details><summary>Show TOML ({len(s.raw_text)} chars)</summary>"
            f"<pre><code>{_html_escape(s.raw_text)}</code></pre>"
            f"</details>"
            if s.raw_text
            else "<p class='muted'>(no parsed TOML)</p>"
        )
        parts.append(
            f"<h3>{_html_escape(s.name)}</h3>"
            f"<p class='muted'>source: {src_html} · snapshot: {snap_link}</p>"
            f"{err_html}"
            "<table><thead><tr><th>env override</th><th>value</th></tr></thead>"
            f"<tbody>{env_body}</tbody></table>"
            f"{toml_block}"
        )
    return "\n".join(parts)


__all__ = [
    "StrategyConfigSnapshot",
    "snapshot_strategy_configs",
    "snapshots_to_html",
    "snapshots_to_json",
]
