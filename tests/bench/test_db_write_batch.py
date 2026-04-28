"""Batch row prep for DB-style inserts (JSONB metadata) — no live Postgres (Phase 2.11)."""

from __future__ import annotations

import json

import pytest

from core.json_fast import dumps_str

pytestmark = pytest.mark.bench

_BATCH = 400


def _build_api_metric_rows():
    rows = []
    for i in range(_BATCH):
        meta = {"endpoint": "/api/History/retrieveBars", "i": i, "ok": True}
        rows.append(
            (
                "/api/History/retrieveBars",
                "POST",
                float(12 + (i % 50)),
                200,
                True,
                None,
                dumps_str(meta),
            )
        )
    return rows


def test_prepare_metric_batch_strings(benchmark):
    benchmark(_build_api_metric_rows)


def test_stdlib_json_meta_only(benchmark):
    def _run():
        for i in range(_BATCH):
            json.dumps({"endpoint": "/api/Order/place", "i": i})

    benchmark(_run)
