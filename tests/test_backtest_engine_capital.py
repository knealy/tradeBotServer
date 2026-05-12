"""BacktestEngine cash vs reported trade PnL must agree (no double commission)."""

from __future__ import annotations

from datetime import datetime, timezone

from core.backtest.engine import BacktestEngine
from core.backtest.models import BacktestOrder, OrderSide, OrderStatus, OrderType


def test_final_capital_matches_initial_plus_sum_trade_pnl_round_trip():
    e = BacktestEngine(
        initial_capital=50_000.0,
        commission_per_contract=2.5,
        slippage_ticks=0.0,
        point_value=2.0,
    )
    e.reset()
    ts = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    e.current_bar_index = 1

    o1 = BacktestOrder(
        order_id="1",
        timestamp=ts,
        symbol="MNQ",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=1,
        price=100.0,
    )
    o1.filled_price = 100.0
    o1.filled_timestamp = ts
    o1.status = OrderStatus.FILLED
    o1.slippage = 0.0
    e._update_position(o1, 100.0)
    assert e.capital == 50_000.0 - 2.5

    o2 = BacktestOrder(
        order_id="2",
        timestamp=ts,
        symbol="MNQ",
        side=OrderSide.SELL,
        order_type=OrderType.MARKET,
        quantity=1,
        price=101.0,
    )
    o2.filled_price = 101.0
    o2.filled_timestamp = ts
    o2.status = OrderStatus.FILLED
    o2.slippage = 0.0
    e._update_position(o2, 101.0)

    assert len(e.trades) == 1
    t = e.trades[0]
    # (101 - 100) * 1 * $2/pt - $2.5 entry - $2.5 exit
    assert abs(t.pnl - (-3.0)) < 1e-9
    assert abs(t.commission - 5.0) < 1e-9
    assert abs(e.capital - (50_000.0 + t.pnl)) < 1e-9


def test_build_result_final_capital_matches_total_pnl():
    e = BacktestEngine(
        initial_capital=50_000.0,
        commission_per_contract=2.5,
        slippage_ticks=0.0,
        point_value=2.0,
    )
    e.reset()
    ts = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    e.current_bar_index = 1
    for i, (side_close, px_open, px_close) in enumerate(
        (
            (OrderSide.BUY, 100.0, 101.0),
            (OrderSide.SELL, 101.0, 100.0),
        )
    ):
        o_in = BacktestOrder(
            order_id=f"in{i}",
            timestamp=ts,
            symbol="MNQ",
            side=side_close,
            order_type=OrderType.MARKET,
            quantity=1,
            price=px_open,
        )
        o_in.filled_price = px_open
        o_in.filled_timestamp = ts
        o_in.status = OrderStatus.FILLED
        o_in.slippage = 0.0
        e._update_position(o_in, px_open)
        o_out = BacktestOrder(
            order_id=f"out{i}",
            timestamp=ts,
            symbol="MNQ",
            side=OrderSide.SELL if side_close == OrderSide.BUY else OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=1,
            price=px_close,
        )
        o_out.filled_price = px_close
        o_out.filled_timestamp = ts
        o_out.status = OrderStatus.FILLED
        o_out.slippage = 0.0
        e._update_position(o_out, px_close)

    r = e._build_result("MNQ", "test", ts, ts)
    assert abs(r.final_capital - (r.initial_capital + r.total_pnl)) < 1e-6
    assert abs(r.total_pnl - sum(t.pnl for t in r.trades)) < 1e-9
