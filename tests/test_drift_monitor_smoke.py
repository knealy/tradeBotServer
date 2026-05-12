"""Smoke tests for core.drift_monitor — round-trip, matching, and CLI."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import List

import pytest

from core.drift_monitor import (
    DriftMonitor,
    _live_round_trips,
    _match_round_trips,
    _replay_round_trips,
    compare_logs,
    fill_record_from_event,
)
from core.events import Event, EventType


REPO = Path(__file__).resolve().parent.parent


def _make_order(
    *,
    order_id: str,
    side: int,
    price: float,
    custom_tag: str = "TB-stop_bracket-body_reversion-26050913",
    stop_price: float = None,
    fill_volume: int = 1,
    filled_at: str = "2026-05-09T13:30:00+00:00",
    contract_id: str = "MNQ.M26",
):
    return {
        "id": order_id,
        "side": side,
        "filledPrice": price,
        "stopPrice": stop_price,
        "fillVolume": fill_volume,
        "size": fill_volume,
        "customTag": custom_tag,
        "filledAt": filled_at,
        "contractId": contract_id,
        "status": 2,
    }


def test_fill_record_extracts_strategy_from_tag():
    rec = fill_record_from_event(
        _make_order(
            order_id="1",
            side=0,
            price=20000.0,
            custom_tag="TB-stop_bracket-body_reversion-26050913",
            stop_price=19990.0,
        ),
        account_id="9",
    )
    assert rec is not None
    assert rec.strategy == "body_reversion"
    assert rec.role == "stop_bracket"
    assert rec.side == "BUY"
    assert rec.fill_price == 20000.0
    assert rec.stop_price == 19990.0
    assert rec.symbol == "MNQ"
    assert rec.account_id == "9"


def test_fill_record_returns_none_for_no_fill_price():
    assert fill_record_from_event({"side": 0, "fillVolume": 1, "customTag": "TB-x-y-z"}) is None


def test_live_round_trips_pairs_buy_then_sell_on_same_symbol():
    fills = [
        {
            "timestamp_utc": "2026-05-09T13:30:00+00:00",
            "symbol": "MNQ",
            "side": "BUY",
            "size": 1,
            "fill_price": 20000.0,
            "stop_price": 19990.0,
        },
        {
            "timestamp_utc": "2026-05-09T13:45:00+00:00",
            "symbol": "MNQ",
            "side": "SELL",
            "size": 1,
            "fill_price": 20020.0,
            "stop_price": None,
        },
    ]
    trips = _live_round_trips(fills)
    assert len(trips) == 1
    t = trips[0]
    assert t["side"] == "BUY"
    assert t["pnl_points"] == pytest.approx(20.0)
    # risk = 10 points → R = +2.0
    assert t["R"] == pytest.approx(2.0)


def test_replay_round_trips_understands_long_short_strings():
    blob = {
        "result": {
            "trades": [
                {
                    "symbol": "MNQ",
                    "side": "LONG",
                    "entry_time": "2026-05-09T13:30:00+00:00",
                    "exit_time": "2026-05-09T13:45:00+00:00",
                    "entry_price": 20000.0,
                    "exit_price": 20020.0,
                    "stop_price": 19990.0,
                }
            ]
        }
    }
    trips = _replay_round_trips(blob)
    assert len(trips) == 1
    assert trips[0]["side"] == "BUY"
    assert trips[0]["R"] == pytest.approx(2.0)


def test_match_within_tolerance_pairs_trades():
    live = [
        {
            "symbol": "MNQ",
            "side": "BUY",
            "entry_ts": "2026-05-09T13:30:00+00:00",
            "exit_ts": "2026-05-09T13:45:00+00:00",
            "pnl_points": 18.0,
            "R": 1.8,
        }
    ]
    replay = [
        {
            "symbol": "MNQ",
            "side": "BUY",
            "entry_ts": "2026-05-09T13:31:00+00:00",
            "exit_ts": "2026-05-09T13:45:00+00:00",
            "pnl_points": 20.0,
            "R": 2.0,
        }
    ]
    pairs, ul, ur = _match_round_trips(live, replay, tolerance_seconds=120)
    assert len(pairs) == 1
    assert ul == [] and ur == []
    assert pairs[0].drift_R == pytest.approx(-0.2)


def test_compare_logs_end_to_end(tmp_path: Path):
    live_path = tmp_path / "live.jsonl"
    replay_path = tmp_path / "replay.json"

    live_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "timestamp_utc": "2026-05-09T13:30:00+00:00",
                        "bar_minute_utc": "2026-05-09T13:30:00+00:00",
                        "strategy": "body_reversion",
                        "symbol": "MNQ",
                        "side": "BUY",
                        "size": 1,
                        "fill_price": 20000.0,
                        "stop_price": 19990.0,
                        "limit_price": 20020.0,
                        "role": "stop_bracket",
                        "order_id": "A",
                        "custom_tag": "TB-stop_bracket-body_reversion-26050913",
                        "account_id": "9",
                    }
                ),
                json.dumps(
                    {
                        "timestamp_utc": "2026-05-09T13:45:00+00:00",
                        "bar_minute_utc": "2026-05-09T13:45:00+00:00",
                        "strategy": "body_reversion",
                        "symbol": "MNQ",
                        "side": "SELL",
                        "size": 1,
                        "fill_price": 20018.0,
                        "stop_price": None,
                        "limit_price": None,
                        "role": "stop_bracket",
                        "order_id": "B",
                        "custom_tag": "TB-stop_bracket-body_reversion-26050913",
                        "account_id": "9",
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )

    replay_path.write_text(
        json.dumps(
            {
                "result": {
                    "trades": [
                        {
                            "symbol": "MNQ",
                            "side": "LONG",
                            "entry_time": "2026-05-09T13:30:00+00:00",
                            "exit_time": "2026-05-09T13:45:00+00:00",
                            "entry_price": 20000.0,
                            "exit_price": 20020.0,
                            "stop_price": 19990.0,
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    summary = compare_logs(live_path, replay_path)
    assert summary.n_matched == 1
    assert summary.n_unmatched_live == 0
    assert summary.n_unmatched_replay == 0
    pair = summary.pairs[0]
    # live R = 18/10 = 1.8; replay R = 20/10 = 2.0; drift = -0.2
    assert pair.live_R == pytest.approx(1.8)
    assert pair.replay_R == pytest.approx(2.0)
    assert pair.drift_R == pytest.approx(-0.2)


@pytest.mark.asyncio
async def test_drift_monitor_writes_jsonl(tmp_path: Path):
    monitor = DriftMonitor(log_dir=tmp_path)

    class _Bus:
        def __init__(self):
            self.subs = {}

        def subscribe(self, etype, cb):
            self.subs.setdefault(etype, []).append(cb)

        def unsubscribe(self, etype, cb):
            if etype in self.subs and cb in self.subs[etype]:
                self.subs[etype].remove(cb)

    bus = _Bus()
    await monitor.attach(bus)
    assert EventType.ORDER_FILLED in bus.subs

    event = Event(
        type=EventType.ORDER_FILLED,
        data={
            "order": _make_order(
                order_id="42",
                side=1,
                price=20100.0,
                stop_price=20110.0,
                custom_tag="TB-stop_bracket-morning_range_reversion-26050913",
            ),
            "account_id": "12345",
        },
        source="test",
    )
    await monitor._on_order_filled(event)

    matched = list(tmp_path.glob("morning_range_reversion_12345_*.jsonl"))
    assert len(matched) == 1
    lines = matched[0].read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["strategy"] == "morning_range_reversion"
    assert rec["side"] == "SELL"
    assert rec["fill_price"] == 20100.0


def test_cli_compare_smoke(tmp_path: Path):
    # Reuse the end-to-end fixtures via subprocess to make sure CLI argv parses.
    live_path = tmp_path / "live.jsonl"
    replay_path = tmp_path / "replay.json"
    live_path.write_text(
        json.dumps(
            {
                "timestamp_utc": "2026-05-09T13:30:00+00:00",
                "bar_minute_utc": "2026-05-09T13:30:00+00:00",
                "strategy": "x",
                "symbol": "MNQ",
                "side": "BUY",
                "size": 1,
                "fill_price": 20000.0,
                "stop_price": 19990.0,
                "limit_price": None,
                "role": "stop_bracket",
                "order_id": "A",
                "custom_tag": "TB-stop_bracket-x-y",
                "account_id": "9",
            }
        )
        + "\n"
        + json.dumps(
            {
                "timestamp_utc": "2026-05-09T13:45:00+00:00",
                "bar_minute_utc": "2026-05-09T13:45:00+00:00",
                "strategy": "x",
                "symbol": "MNQ",
                "side": "SELL",
                "size": 1,
                "fill_price": 20020.0,
                "stop_price": None,
                "limit_price": None,
                "role": "stop_bracket",
                "order_id": "B",
                "custom_tag": "TB-stop_bracket-x-y",
                "account_id": "9",
            }
        ),
        encoding="utf-8",
    )
    replay_path.write_text(
        json.dumps(
            {
                "result": {
                    "trades": [
                        {
                            "symbol": "MNQ",
                            "side": "LONG",
                            "entry_time": "2026-05-09T13:30:00+00:00",
                            "exit_time": "2026-05-09T13:45:00+00:00",
                            "entry_price": 20000.0,
                            "exit_price": 20020.0,
                            "stop_price": 19990.0,
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    out = subprocess.check_output(
        [
            sys.executable,
            "-m",
            "core.drift_monitor",
            "compare",
            "--live",
            str(live_path),
            "--replay",
            str(replay_path),
            "--json",
        ],
        cwd=REPO,
        text=True,
    )
    blob = json.loads(out.strip())
    assert blob["n_matched"] == 1
    assert blob["n_unmatched_live"] == 0
