"""Serialize/deserialize throughput: json_fast vs stdlib json (Phase 2.11)."""

from __future__ import annotations

import json

import pytest

from core.json_fast import dumps_str, loads as fast_loads, is_fast_backend

pytestmark = pytest.mark.bench

# Order-sized payload (similar to REST place/search bodies)
_ORDER_LIKE = {
    "contractId": 123456,
    "accountId": "acct-1",
    "side": "BUY",
    "quantity": 2,
    "orderType": "MARKET",
    "meta": {"tag": "bench", "idx": 42},
}


def test_dumps_str_order_payload(benchmark):
    benchmark(dumps_str, _ORDER_LIKE)


def test_stdlib_json_dumps_order_payload(benchmark):
    benchmark(json.dumps, _ORDER_LIKE)


def test_loads_roundtrip(benchmark):
    raw = dumps_str(_ORDER_LIKE)

    def _roundtrip():
        return fast_loads(raw)

    benchmark(_roundtrip)


def test_reports_fast_json_backend():
    """orjson is required in production requirements; skip on interpreters without wheels."""
    if not is_fast_backend():
        pytest.skip("orjson not available for this Python build (json_fast falls back to stdlib)")
    assert is_fast_backend() is True
