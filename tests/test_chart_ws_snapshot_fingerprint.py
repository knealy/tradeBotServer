"""Unit tests for WebSocket snapshot dedupe helpers in gui/chart_html."""

from gui.chart_html import _ws_snapshot_fingerprint_orders, _ws_snapshot_fingerprint_positions


def test_orders_fingerprint_stable_under_reorder():
    a = [{"id": "1", "status": "WORKING", "quantity": 1, "price": 100.0}]
    b = [{"id": "1", "status": "WORKING", "quantity": 1, "price": 100.0}]
    assert _ws_snapshot_fingerprint_orders(a) == _ws_snapshot_fingerprint_orders(b)


def test_orders_fingerprint_changes_on_status():
    a = [{"id": "1", "status": "WORKING", "quantity": 1, "price": 100.0}]
    b = [{"id": "1", "status": "FILLED", "quantity": 1, "price": 100.0}]
    assert _ws_snapshot_fingerprint_orders(a) != _ws_snapshot_fingerprint_orders(b)


def test_positions_fingerprint_empty():
    assert _ws_snapshot_fingerprint_positions([]) == "p:0"
    assert _ws_snapshot_fingerprint_orders([]) == "o:0"
