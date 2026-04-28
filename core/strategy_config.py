"""Per-strategy configuration loader.

Goal: keep .env focused on secrets + infra, and move strategy parameters
(timing, risk, signal, filters, position management, per-symbol overrides)
into TOML files under ``config/strategies/<name>.toml``.

Precedence (highest to lowest) when reading a parameter:

    1. CLI override (``cli_overrides`` dict)
    2. Environment variable (uppercased key, optional ``prefix_``)
    3. TOML file at ``config/strategies/<name>.toml``
    4. Strategy class default (``defaults`` dict)

Usage:
    from core.strategy_config import load_strategy_config

    cfg = load_strategy_config("overnight_range")
    cfg.get("risk.position_size", default=1)
    cfg.get_int("signal.atr_period")
    cfg.symbols()                 # list[str]
    cfg.symbol_override("MNQ", "risk.position_size", default=1)

Hot-reload: call ``cfg.maybe_reload()`` inside the strategy loop. It only
re-parses the TOML when ``mtime`` has changed.

Stdlib only (Python 3.11+). No new dependency.
"""

from __future__ import annotations

import logging
import os
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_CONFIG_DIR = _REPO_ROOT / "config" / "strategies"


def _coerce(value: Any, hint: type) -> Any:
    """Best-effort type coercion (env vars are always strings)."""
    if value is None or isinstance(value, hint):
        return value
    if hint is bool:
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)
    if hint is int:
        return int(value)
    if hint is float:
        return float(value)
    if hint is str:
        return str(value)
    if hint is list:
        if isinstance(value, str):
            return [p.strip() for p in value.split(",") if p.strip()]
        return list(value)
    return value


@dataclass
class StrategyConfig:
    """Resolved configuration for a single strategy.

    Stores the parsed TOML (under ``_data``), an optional CLI override map,
    and tracks the source file mtime so callers can opportunistically reload.
    """

    name: str
    path: Optional[Path]
    _data: Dict[str, Any] = field(default_factory=dict)
    _cli_overrides: Dict[str, Any] = field(default_factory=dict)
    _env_prefix: str = ""
    _mtime: float = 0.0

    # ------------------------------------------------------------------ I/O

    @classmethod
    def load(
        cls,
        name: str,
        *,
        path: Optional[Path] = None,
        env_prefix: Optional[str] = None,
        cli_overrides: Optional[Dict[str, Any]] = None,
    ) -> "StrategyConfig":
        """Load configuration for ``name`` from TOML.

        ``path`` defaults to ``config/strategies/<name>.toml`` under repo root.
        Missing files are non-fatal: an empty config is returned and only env
        vars + class defaults will apply (matches the legacy behavior).
        """
        if path is None:
            path = _DEFAULT_CONFIG_DIR / f"{name}.toml"
        env_prefix = env_prefix if env_prefix is not None else f"{name.upper()}_"

        data: Dict[str, Any] = {}
        mtime = 0.0
        if path.exists():
            try:
                with path.open("rb") as fh:
                    data = tomllib.load(fh)
                mtime = path.stat().st_mtime
                logger.info("Loaded strategy config: %s", path)
            except Exception as exc:
                logger.exception("Failed to parse %s: %s", path, exc)
                raise
        else:
            logger.debug("No config file at %s; relying on env + defaults", path)

        return cls(
            name=name,
            path=path if path.exists() else None,
            _data=data,
            _cli_overrides=dict(cli_overrides or {}),
            _env_prefix=env_prefix,
            _mtime=mtime,
        )

    def maybe_reload(self) -> bool:
        """Re-parse the TOML if its mtime changed. Returns True on reload."""
        if not self.path or not self.path.exists():
            return False
        try:
            new_mtime = self.path.stat().st_mtime
        except OSError:
            return False
        if new_mtime <= self._mtime:
            return False
        try:
            with self.path.open("rb") as fh:
                self._data = tomllib.load(fh)
            self._mtime = new_mtime
            logger.info("Reloaded strategy config %s (mtime=%s)", self.path, new_mtime)
            return True
        except Exception:
            logger.exception("Reload failed for %s", self.path)
            return False

    # ------------------------------------------------------------------ access

    def get(self, dotted_key: str, default: Any = None, *, hint: Optional[type] = None) -> Any:
        """Resolve ``dotted_key`` through CLI > env > TOML > default."""
        if dotted_key in self._cli_overrides:
            return _coerce(self._cli_overrides[dotted_key], hint) if hint else self._cli_overrides[dotted_key]

        env_key = (self._env_prefix + dotted_key.upper().replace(".", "_")).strip("_")
        env_val = os.getenv(env_key)
        if env_val is None and "." in dotted_key:
            env_val = os.getenv(dotted_key.split(".")[-1].upper())
        if env_val is not None:
            return _coerce(env_val, hint) if hint else env_val

        node: Any = self._data
        for part in dotted_key.split("."):
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                return default
        return _coerce(node, hint) if hint else node

    def get_int(self, key: str, default: Optional[int] = None) -> Optional[int]:
        v = self.get(key, default, hint=int)
        return v  # type: ignore[return-value]

    def get_float(self, key: str, default: Optional[float] = None) -> Optional[float]:
        return self.get(key, default, hint=float)  # type: ignore[return-value]

    def get_bool(self, key: str, default: bool = False) -> bool:
        return bool(self.get(key, default, hint=bool))

    def get_str(self, key: str, default: Optional[str] = None) -> Optional[str]:
        return self.get(key, default, hint=str)  # type: ignore[return-value]

    def get_list(self, key: str, default: Optional[List[Any]] = None) -> List[Any]:
        v = self.get(key, default if default is not None else [], hint=list)
        return list(v) if v is not None else []

    # ------------------------------------------------------------------ helpers

    def symbols(self) -> List[str]:
        return [s.upper() for s in self.get_list("meta.symbols")]

    def is_enabled(self) -> bool:
        return self.get_bool("meta.enabled", default=True)

    def symbol_override(self, symbol: str, dotted_key: str, default: Any = None, *, hint: Optional[type] = None) -> Any:
        """Look up ``[symbols.<SYM>].<key>`` first, falling back to ``key``."""
        node: Any = self._data.get("symbols", {}).get(symbol.upper(), {})
        for part in dotted_key.split("."):
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                node = None
                break
        if node is not None:
            return _coerce(node, hint) if hint else node
        return self.get(dotted_key, default, hint=hint)

    def as_dict(self) -> Dict[str, Any]:
        """Return a deep copy of the parsed TOML for inspection / logging."""
        import copy
        return copy.deepcopy(self._data)


def load_strategy_config(
    name: str,
    *,
    path: Optional[str | Path] = None,
    env_prefix: Optional[str] = None,
    cli_overrides: Optional[Dict[str, Any]] = None,
) -> StrategyConfig:
    """Module-level convenience wrapper around ``StrategyConfig.load``."""
    p = Path(path) if path else None
    return StrategyConfig.load(
        name,
        path=p,
        env_prefix=env_prefix,
        cli_overrides=cli_overrides,
    )


__all__ = ["StrategyConfig", "load_strategy_config"]
