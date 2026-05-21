"""Ensure strategy TOML files parse and carry [meta].symbols (post–slim_env parity)."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

STRATEGIES_DIR = Path(__file__).resolve().parent.parent / "config" / "strategies"


@pytest.mark.parametrize(
    "toml_name",
    [
        "overnight_range.toml",
        "trend_following.toml",
        "simple_candle.toml",
        "mean_reversion.toml",
        "trend_scalping.toml",
        "simple_momentum.toml",
        "simple_rth.toml",
        "ema_stack_trend_15m.toml",
        "rsi_switch_15m.toml",
    ],
)
def test_strategy_toml_parses_and_has_meta_symbols(toml_name: str):
    path = STRATEGIES_DIR / toml_name
    assert path.is_file(), f"missing {path}"
    with path.open("rb") as fh:
        data = tomllib.load(fh)
    assert "meta" in data, f"{toml_name} missing [meta]"
    meta = data["meta"]
    assert "symbols" in meta and isinstance(meta["symbols"], list), f"{toml_name} meta.symbols"
    assert len(meta["symbols"]) >= 1, f"{toml_name} has empty symbols"


def test_overnight_range_toml_covers_schema_timing_signal_risk():
    """Legacy env knobs dropped by slim_env map to TOML (see config/strategies/_schema.toml)."""
    path = STRATEGIES_DIR / "overnight_range.toml"
    with path.open("rb") as fh:
        d = tomllib.load(fh)
    assert "timing" in d and "overnight_start" in d["timing"]
    assert "signal" in d and "atr_period" in d["signal"]
    assert "risk" in d and "position_size" in d["risk"]
    assert "position_management" in d
