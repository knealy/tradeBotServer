"""Tests for order-tag → trade source classification."""
from gui.chart_html import _classify_order_tag


def test_manual_when_no_tag():
    r = _classify_order_tag("")
    assert r["source"] == "manual"


def test_auto_tb_overnight_tag():
    tag = "TB-stop_bracket-overnight_range-26061609"
    r = _classify_order_tag(tag)
    assert r["source"] == "auto"
    assert r["strategy"] == "overnight_range"
    assert r["custom_tag"] == tag


def test_auto_mrr_tag():
    tag = "TB-morning_range_reversion-MNQ-26061607"
    r = _classify_order_tag(tag)
    assert r["source"] == "auto"
    assert r["strategy"] == "morning_range_reversion"


def test_bracket_sl_tag():
    r = _classify_order_tag("AutoBracket-SL-12345")
    assert r["source"] == "bracket"


def test_manual_gui_order():
    r = _classify_order_tag("manual-click-123")
    assert r["source"] == "manual"
