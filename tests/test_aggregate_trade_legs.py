"""Regression tests for ``gui.chart_html._aggregate_trade_legs``.

The TopStepX ``Trade/search`` API returns one record per filled
contract leg, which historically appeared in the dashboard as one row
per leg — turning a 3-contract atomic close into 3 "trades" and a
6-contract scale-out into 6 "trades". That broke trade count, win
rate, streaks, max-drawdown, profit factor and the equity curve.

These tests pin the aggregation contract:

* Legs that share a parent ``entry_order_id`` collapse into one
  logical trade regardless of exit timing (covers scale-outs).
* Legs without a paired opener fall back to ``(symbol, side,
  entry_time bucket)`` so atomic same-instant entries still merge.
* Single-leg trades pass through untouched.
* Aggregated rows carry weighted-average prices, summed quantity /
  pnl / fees, earliest entry / latest exit, sign-correct points, and
  the ``legs[]`` drilldown list.
"""

from __future__ import annotations

from gui.chart_html import _aggregate_trade_legs


def _leg(**overrides):
    """Build a minimally-populated leg dict like ``handle_get_trades``
    produces just before aggregation."""
    base = {
        "id": "L0",
        "symbol": "MNQ",
        "side": "BUY",
        "quantity": 1,
        "entry_price": 100.0,
        "exit_price": 101.0,
        "entry_time": "2026-05-19T14:14:00+00:00",
        "exit_time": "2026-05-19T14:23:00+00:00",
        "pnl": 2.0,
        "fees": 1.40,
        "points": 1.0,
        "entry_order_id": None,
        "order_id": "X",
    }
    base.update(overrides)
    return base


def test_single_leg_passes_through():
    legs = [_leg(id="L1", entry_order_id="O1")]
    out = _aggregate_trade_legs(legs)
    assert len(out) == 1
    assert out[0]["legs_count"] == 1
    assert out[0]["legs"] == []
    assert out[0]["pnl"] == 2.0


def test_three_atomic_close_legs_collapse_into_one_trade():
    """Three close-fill records of the same 3-contract position share an
    ``entry_order_id`` (parent open). They must collapse to a single
    logical trade with qty=3 and pnl=sum."""
    legs = [
        _leg(id="L1", entry_order_id="O42", quantity=1, pnl=-1520.00,
             entry_price=29008.50, exit_price=28932.50,
             exit_time="2026-05-18T18:10:00+00:00"),
        _leg(id="L2", entry_order_id="O42", quantity=1, pnl=-1520.00,
             entry_price=29008.50, exit_price=28932.50,
             exit_time="2026-05-18T18:10:00+00:00"),
        _leg(id="L3", entry_order_id="O42", quantity=1, pnl=-1525.00,
             entry_price=29008.75, exit_price=28932.50,
             exit_time="2026-05-18T18:10:00+00:00"),
    ]
    out = _aggregate_trade_legs(legs)
    assert len(out) == 1, f"expected 1 logical trade, got {len(out)}"
    t = out[0]
    assert t["legs_count"] == 3
    assert t["quantity"] == 3
    assert t["pnl"] == round(-1520 - 1520 - 1525, 2)
    # Weighted-average entry price: (29008.50*1 + 29008.50*1 + 29008.75*1) / 3
    assert t["entry_price"] == round((29008.50 + 29008.50 + 29008.75) / 3, 2)
    assert t["exit_price"] == 28932.50
    # Drilldown carries each individual leg id
    assert {l["id"] for l in t["legs"]} == {"L1", "L2", "L3"}


def test_six_leg_scale_out_collapses_by_entry_order():
    """A 6-contract long with staggered exits across 3 minutes still
    shares the parent ``entry_order_id``. Aggregation must yield one
    trade with qty=6, summed pnl, weighted-avg exit, and exit_time =
    last leg."""
    legs = [
        _leg(id=f"L{i}", entry_order_id="O99", quantity=1, pnl=p,
             entry_price=28747.00, exit_price=xp,
             entry_time="2026-05-19T14:14:00+00:00",
             exit_time=et)
        for i, (p, xp, et) in enumerate([
            (379.50, 28683.75, "2026-05-19T14:20:00+00:00"),
            (408.00, 28677.75, "2026-05-19T14:21:00+00:00"),
            (171.00, 28713.75, "2026-05-19T14:23:00+00:00"),
            (172.50, 28713.75, "2026-05-19T14:23:00+00:00"),
            (259.50, 28713.75, "2026-05-19T14:23:00+00:00"),
            (256.50, 28713.75, "2026-05-19T14:23:00+00:00"),
        ], start=1)
    ]
    # The above sets side=BUY everywhere via _leg defaults, but original
    # data was SHORT (entry > exit, positive pnl). Fix it for realism:
    for l in legs:
        l["side"] = "SELL"
    out = _aggregate_trade_legs(legs)
    assert len(out) == 1
    t = out[0]
    assert t["legs_count"] == 6
    assert t["quantity"] == 6
    assert t["pnl"] == round(379.50 + 408.00 + 171.00 + 172.50 + 259.50 + 256.50, 2)
    assert t["entry_time"] == "2026-05-19T14:14:00+00:00"
    # latest exit_time wins after sorting legs by exit_time
    assert t["exit_time"] == "2026-05-19T14:23:00+00:00"
    # Position SELL → points = entry - exit; with all entry=28747 and
    # weighted-avg exit, points must be positive (winning short).
    assert t["points"] > 0


def test_falls_back_to_entry_time_bucket_when_no_pairing():
    """Two legs with ``entry_order_id=None`` but identical entry_time
    still collapse via the time-bucket fallback."""
    legs = [
        _leg(id="L1", entry_order_id=None, quantity=1, pnl=10.0,
             entry_time="2026-05-18T18:00:00+00:00"),
        _leg(id="L2", entry_order_id=None, quantity=1, pnl=12.0,
             entry_time="2026-05-18T18:00:00+00:00"),
    ]
    out = _aggregate_trade_legs(legs)
    assert len(out) == 1
    assert out[0]["legs_count"] == 2
    assert out[0]["pnl"] == 22.0


def test_different_entry_orders_stay_separate():
    """Two trades from different parent entry orders must remain
    separate even if their entry times happen to collide."""
    legs = [
        _leg(id="L1", entry_order_id="O1", entry_time="2026-05-18T18:00:00+00:00", pnl=5.0),
        _leg(id="L2", entry_order_id="O2", entry_time="2026-05-18T18:00:00+00:00", pnl=-3.0),
    ]
    out = _aggregate_trade_legs(legs)
    assert len(out) == 2


def test_empty_input():
    assert _aggregate_trade_legs([]) == []


def test_short_position_points_sign():
    """For a SELL-side trade, points = entry - exit (signed by P&L
    direction). A losing short (exit > entry) must report negative
    points."""
    legs = [
        _leg(id="L1", side="SELL", entry_order_id="OS1",
             entry_price=29000.0, exit_price=29050.0, pnl=-50.0),
        _leg(id="L2", side="SELL", entry_order_id="OS1",
             entry_price=29000.0, exit_price=29050.0, pnl=-50.0),
    ]
    out = _aggregate_trade_legs(legs)
    assert len(out) == 1
    assert out[0]["side"] == "SELL"
    assert out[0]["points"] == -50.0
    assert out[0]["pnl"] == -100.0
