"""Tests for shared metrics glossary and walk-forward dynamic sizing."""
from core.backtest.dynamic_sizing import (
    apply_dynamic_sizing,
    dynamic_sizing_enabled,
    end_carry,
    initial_carry,
    max_qty,
    reference_equity,
)
from core.metrics_glossary import METRICS, th, tooltip


def test_metrics_glossary_has_core_keys():
    for key in ("pf", "expectancy", "maxdd", "qty", "margin"):
        assert key in METRICS
        assert tooltip(key)


def test_th_renders_title():
    html = th("pf", "PF", num=True)
    assert "title=" in html
    assert 'class="num"' in html
    assert ">PF</th>" in html


def test_dynamic_sizing_drawdown_penalty_uses_2k_base():
    ref = 2000.0
    # Peak 2400, current 2100 → $300 dd = 15% of $2k → −3 from base 4 → 1
    assert apply_dynamic_sizing(4, 2100.0, 2400.0, ref) == 1
    # Peak 2200, current 2100 → $100 dd = 5% of $2k → −1 from base 2 → 1
    assert apply_dynamic_sizing(2, 2100.0, 2200.0, ref) == 1


def test_dynamic_sizing_bonus_only_at_peak():
    ref = 2000.0
    # At peak 2200 (+10% vs ref) → +1
    assert apply_dynamic_sizing(1, 2200.0, 2200.0, ref) == 2
    # Same equity but below peak → no bonus, drawdown penalty instead
    assert apply_dynamic_sizing(1, 2200.0, 2400.0, ref) == 1


def test_dynamic_sizing_env_default_off():
    import os

    os.environ.pop("BACKTEST_DYNAMIC_SIZING", None)
    assert dynamic_sizing_enabled() is False


def test_reference_equity_default_2000():
    import os

    os.environ.pop("BACKTEST_DYNAMIC_SIZING_BASE", None)
    assert reference_equity() == 2000.0


def test_dynamic_sizing_qty_ceiling():
    ref = 2000.0
    # Would be base 1 + 20 bonus steps without cap
    assert apply_dynamic_sizing(1, 6200.0, 6200.0, ref, qty_ceiling=15) == 15
    assert apply_dynamic_sizing(1, 6200.0, 6200.0, ref, qty_ceiling=3) == 3


def test_max_qty_default_15():
    import os

    os.environ.pop("BACKTEST_DYNAMIC_SIZING_MAX", None)
    assert max_qty() == 15


def test_carry_chain_increases_bonus_at_later_fold():
    ref = 2000.0
    carry = initial_carry(ref)
    # Fold 1 ends +$250
    carry = end_carry(carry["equity"] + 250.0, carry["peak"] + 250.0)
    eq = carry["equity"]
    peak = carry["peak"]
    # +$250 on $2k → +1 size step at peak
    assert apply_dynamic_sizing(1, eq, peak, ref) == 2
