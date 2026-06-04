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


def test_overnight_symbol_tp_atr_multiplier_overrides(tmp_path, monkeypatch):
    """Round-13: ``[symbols.<SYM>.signal].tp_atr_multiplier`` must override root.

    Pins the resolver added in round-13 so a future TOML edit can't silently
    revert MNQ TP from the asymmetric 4× back to the root 2.5× default.
    """
    p = Path(tmp_path) / "overnight_range.toml"
    p.write_text(
        """
[risk]
position_size = 1
[signal]
stop_atr_multiplier = 1.0
tp_atr_multiplier = 2.5
[symbols.MNQ.signal]
tp_atr_multiplier = 4.0
[symbols.MGC.signal]
tp_atr_multiplier = 2.0
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
    assert strat.tp_atr_multiplier == 2.5  # root value
    assert strat._overnight_symbol_tp_atr_multiplier("MNQ") == 4.0
    assert strat._overnight_symbol_tp_atr_multiplier("MGC") == 2.0
    # Unknown symbol falls back to root
    assert strat._overnight_symbol_tp_atr_multiplier("ES") == 2.5


def test_overnight_symbol_skip_weekdays_overrides(tmp_path, monkeypatch):
    """Round-13: ``[symbols.<SYM>.filters].skip_weekdays`` must override root.

    Pins the resolver added in round-13 (MNQ skips Mon+Fri, MGC skips Wed,
    no root-level skip).
    """
    p = Path(tmp_path) / "overnight_range.toml"
    p.write_text(
        """
[risk]
position_size = 1
[signal]
stop_atr_multiplier = 1.0
[filters]
skip_weekdays = []
[symbols.MNQ.filters]
skip_weekdays = [0, 4]
[symbols.MGC.filters]
skip_weekdays = [2]
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
    assert strat._overnight_symbol_skip_weekdays("MNQ") == frozenset({0, 4})
    assert strat._overnight_symbol_skip_weekdays("MGC") == frozenset({2})
    # Unknown symbol falls back to root (empty here)
    assert strat._overnight_symbol_skip_weekdays("ES") == frozenset()


def test_overnight_committed_round23_defaults_resolve_correctly():
    """Pins the live ``config/strategies/overnight_range.toml`` round-23 values.

    Round-23 (2026-06-03 PM, step-trail addition on top of R22):
      • Root: stop=1.0, tp=2.5, offset=1.5, skip_weekdays=[],
              ``trail_steps_r = [[2.0, 1.0]]`` (NEW — single-stage step trail in
              R-multiples: trigger at 2R MFE, lock 1R)
      • MNQ:  stop=0.5, tp=4.0, skip_weekdays=[0, 4]  — unchanged
      • MGC:  stop=0.5, tp=2.0, skip_weekdays=[2]     — unchanged
    Trail is global (per-symbol override not yet supported in ``_read_trail_steps_r``).
    Sweep evidence: R24/R25 (270d / 9 folds) ranked `[[2.0, 1.0]]` first by
    RF×ret on the corrected baseline; cross-validated 3m (+16% ret) and
    9m (+2.3% ret) — see overnight_range.toml step-trail comment block.
    """
    from core.strategy_config import load_strategy_config

    cfg = load_strategy_config("overnight_range")
    assert cfg.get("meta.symbols") == ["MNQ", "MGC"]
    assert cfg.get_float("signal.stop_atr_multiplier") == 1.0
    assert cfg.get_float("signal.tp_atr_multiplier") == 2.5
    assert cfg.get_float("position_management.range_break_offset") == 1.5
    assert cfg.get_list("filters.skip_weekdays", []) == []
    assert cfg.get_list("position_management.trail_steps_r", []) == [[2.0, 1.0]]
    assert cfg.symbol_override("MNQ", "signal.stop_atr_multiplier", hint=float) == 0.5
    assert cfg.symbol_override("MNQ", "signal.tp_atr_multiplier", hint=float) == 4.0
    assert list(cfg.symbol_override("MNQ", "filters.skip_weekdays")) == [0, 4]
    assert cfg.symbol_override("MGC", "signal.stop_atr_multiplier", hint=float) == 0.5
    assert cfg.symbol_override("MGC", "signal.tp_atr_multiplier", hint=float) == 2.0
    assert list(cfg.symbol_override("MGC", "filters.skip_weekdays")) == [2]


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


def test_overnight_read_trail_steps_r_sorts_and_drops_bad_rows(tmp_path, monkeypatch):
    """R23: ``_read_trail_steps_r`` parses TOML rows, drops trigger<=0, sorts ascending."""
    p = Path(tmp_path) / "overnight_range.toml"
    p.write_text(
        """
[signal]
stop_atr_multiplier = 1.0
[position_management]
trail_steps_r = [[4.0, 2.5], [2.0, 1.0], [0.0, 0.5], [-1.0, 0.3]]
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
    # Negative/zero triggers dropped, remaining stages sorted ascending by trigger.
    assert strat.trail_steps_r == [(2.0, 1.0), (4.0, 2.5)]


def test_overnight_read_trail_steps_r_empty_when_missing(tmp_path, monkeypatch):
    """R23: ``trail_steps_r`` absent from TOML → no trail wiring (R22 behaviour)."""
    p = Path(tmp_path) / "overnight_range.toml"
    p.write_text(
        """
[signal]
stop_atr_multiplier = 1.0
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
    assert strat.trail_steps_r == []


def test_overnight_register_trail_watches_scales_to_r_multiples(tmp_path, monkeypatch):
    """R23: per-trade ``R_pts = |entry - stop|`` scales each (trigger_R, lock_R)
    row to absolute points before registering with ``register_generic_breakeven_watch``.
    """
    p = Path(tmp_path) / "overnight_range.toml"
    p.write_text(
        """
[signal]
stop_atr_multiplier = 1.0
[position_management]
trail_steps_r = [[2.0, 1.0], [4.0, 2.5]]
""",
        encoding="utf-8",
    )
    cfg = StrategyConfig.load("overnight_range", path=p)
    monkeypatch.setattr(
        "strategies.overnight_range_strategy.load_strategy_config",
        lambda *_a, **_k: cfg,
    )
    from strategies.overnight_range_strategy import OvernightRangeStrategy

    bot = MagicMock()
    bot.register_generic_breakeven_watch = MagicMock()
    strat = OvernightRangeStrategy(bot, _sb_config())
    strat.trading_bot = bot
    # Long MNQ-style: entry=100, stop=90 → R=10pt. Stage-0: trigger 20pt / lock 10pt.
    # Stage-1: trigger 40pt / lock 25pt.
    strat._register_trail_watches(
        order_id="ENT_X", symbol="MNQ", side="BUY",
        entry_price=100.0, stop_loss_price=90.0,
    )
    calls = bot.register_generic_breakeven_watch.call_args_list
    assert len(calls) == 2
    args0, kw0 = calls[0]
    assert args0[0] == "ENT_X_trail0"
    assert kw0["profit_threshold"] == 20.0
    assert kw0["breakeven_offset"] == 10.0
    args1, kw1 = calls[1]
    assert args1[0] == "ENT_X_trail1"
    assert kw1["profit_threshold"] == 40.0
    assert kw1["breakeven_offset"] == 25.0


def test_overnight_register_trail_watches_noop_when_R_is_zero(tmp_path, monkeypatch):
    """R23 guard: |entry - stop| == 0 (no risk) skips registration entirely."""
    p = Path(tmp_path) / "overnight_range.toml"
    p.write_text(
        """
[signal]
stop_atr_multiplier = 1.0
[position_management]
trail_steps_r = [[2.0, 1.0]]
""",
        encoding="utf-8",
    )
    cfg = StrategyConfig.load("overnight_range", path=p)
    monkeypatch.setattr(
        "strategies.overnight_range_strategy.load_strategy_config",
        lambda *_a, **_k: cfg,
    )
    from strategies.overnight_range_strategy import OvernightRangeStrategy

    bot = MagicMock()
    bot.register_generic_breakeven_watch = MagicMock()
    strat = OvernightRangeStrategy(bot, _sb_config())
    strat.trading_bot = bot
    strat._register_trail_watches(
        order_id="ENT_X", symbol="MNQ", side="BUY",
        entry_price=100.0, stop_loss_price=100.0,
    )
    bot.register_generic_breakeven_watch.assert_not_called()
