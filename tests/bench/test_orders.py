"""Order-shaped payload handling (serialization / dict churn; Phase 2.11)."""

from __future__ import annotations

import pytest

from core.json_fast import dumps_str, loads

pytestmark = pytest.mark.bench

_ORDER = {
    "contractId": 987654,
    "accountId": "12345",
    "side": "SELL",
    "quantity": 1,
    "orderType": "LIMIT",
    "limitPrice": 18500.25,
    "stopPrice": None,
    "customTag": "bench-order",
}


def test_order_payload_dumps(benchmark):
    benchmark(dumps_str, _ORDER)


def test_order_payload_roundtrip(benchmark):
    raw = dumps_str(_ORDER)

    def _go():
        return loads(raw)

    benchmark(_go)
