"""Per-symbol ``[symbols.SYM.risk]`` / ``[symbols.SYM.signal]`` for overnight_range brackets."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from core.strategy_config import StrategyConfig
from strategies.strategy_base import StrategyConfig as SBStrategyConfig


def _sb_config() -> SBStrategyConfig:
    return SBStrategyConfig(
        name="overnight_range",
        enabled=True,
        symbols=["MNQ", "MGC"],
        max_positions=2,
        position_size=2,
        risk_per_trade_percent=0.5,
        max_daily_trades=4,
        preferred_conditions=[],
        avoid_conditions=[],
        trading_start_time="00:00",
        trading_end_time="23:59",
        no_trade_start="",
        no_trade_end="",
    )


def test_overnight_symbol_risk_signal_overrides_for_brackets(tmp_path, monkeypatch):
    p = Path(tmp_path) / "overnight_range.toml"
    p.write_text(
        """
[risk]
position_size = 2

[signal]
stop_atr_multiplier = 1.5

[symbols.MGC.risk]
position_size = 1

[symbols.MGC.signal]
stop_atr_multiplier = 1.0
""",
        encoding="utf-8",
    )
    cfg = StrategyConfig.load("overnight_range", path=p)
    assert cfg.symbol_override("MGC", "risk.position_size", cfg.get_int("risk.position_size", 1), hint=int) == 1
    assert cfg.symbol_override("MNQ", "risk.position_size", cfg.get_int("risk.position_size", 1), hint=int) == 2
    assert cfg.symbol_override("MGC", "signal.stop_atr_multiplier", 1.5, hint=float) == 1.0
    assert abs(cfg.symbol_override("MNQ", "signal.stop_atr_multiplier", 1.5, hint=float) - 1.5) < 1e-9

    monkeypatch.setattr(
        "strategies.overnight_range_strategy.load_strategy_config",
        lambda *_a, **_k: cfg,
    )
    from strategies.overnight_range_strategy import OvernightRangeStrategy

    strat = OvernightRangeStrategy(MagicMock(), _sb_config())
    assert strat.default_quantity == 2
    assert strat.stop_atr_multiplier == 1.5
    assert strat._overnight_symbol_position_size("MGC") == 1
    assert strat._overnight_symbol_position_size("MNQ") == 2
    assert strat._overnight_symbol_stop_atr_multiplier("MGC") == 1.0
    assert strat._overnight_symbol_stop_atr_multiplier("MNQ") == 1.5


def test_overnight_symbol_position_size_clamped_to_at_least_one(tmp_path, monkeypatch):
    p = Path(tmp_path) / "overnight_range.toml"
    p.write_text(
        """
[risk]
position_size = 2
[signal]
stop_atr_multiplier = 1.5
[symbols.MGC.risk]
position_size = 0
""",
        encoding="utf-8",
    )
    cfg = StrategyConfig.load("overnight_range", path=p)
    monkeypatch.setattr(
        "strategies.overnight_range_strategy.load_strategy_config",
        lambda *_a, **_k: cfg,
    )
    from strategies.overnight_range_strategy import OvernightRangeStrategy

    strat = OvernightRangeStrategy(MagicMock(), _sb_config())
    assert strat._overnight_symbol_position_size("MGC") == 1
