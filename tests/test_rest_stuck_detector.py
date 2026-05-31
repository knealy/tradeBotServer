"""Stuck-REST detector + auth session reset (2026-05-27 / 2026-05-29 fixes).

Background:
    On 2026-05-27 the broker's ``/api/History/retrieveBars`` endpoint served
    the **same response (last_bar_ts = T12:05:00+00:00) for 1,077 consecutive
    fetches across 60 minutes** while wall-clock time kept advancing. The
    moment an unrelated ``/api/Auth/...`` call forced a new TCP handshake,
    REST advanced on the very next fetch — proving the broker's edge layer
    was serving a cached response pinned to a specific keepalive route.

These tests pin the three halves of the fix:

  1. ``core.auth.AuthManager.force_session_reset`` — closes the shared
     aiohttp session so the next ``_get_session()`` call builds a fresh
     TCP+TLS connection.
  2. ``trading_bot.TopStepXTradingBot._detect_and_handle_stuck_rest`` —
     **steady-state path**: trips ``force_session_reset`` when the same
     ``last_ts`` has been returned ≥ 3 times AND has been pinned for > 2×
     the timeframe of wall-clock dwell.
  3. **Cold-start path (2026-05-29 fix)** — trips ``force_session_reset``
     IMMEDIATELY on the first poll where the returned bar's absolute age
     (``now - bar_ts``) exceeds 2× the timeframe. Recovers in seconds
     instead of waiting 10 minutes for the steady-state dwell trigger.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _fresh_ts(seconds_old: float = 0.0) -> str:
    """Return an ISO-8601 timestamp ``seconds_old`` in the past from real now.

    Used to keep the steady-state detector tests in their original code path
    (bar is recent enough that the cold-start fast-path stays quiet). The
    cold-start tests deliberately pass a larger ``seconds_old`` to trip the
    new immediate-reset behaviour.
    """
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds_old)).strftime(
        "%Y-%m-%dT%H:%M:%S+00:00"
    )


# ─── core.auth.TopStepXAuth.force_session_reset ──────────────────────────────


class _FakeSession:
    """Stand-in for ``aiohttp.ClientSession`` — we only care about close semantics."""

    def __init__(self) -> None:
        self.closed = False
        self.close_calls = 0

    async def close(self) -> None:
        self.close_calls += 1
        self.closed = True


def _make_auth_stub():
    """Build an ``AuthManager`` instance bypassing __init__ (avoids env/network).

    NOTE: the ``_session_lock`` is created lazily on the event loop that runs the
    coroutine so we don't bind it to the import-time loop. We initialise it to
    None and let the test driver call ``_init_lock`` inside ``asyncio.run``.
    """
    from core.auth import AuthManager

    auth = AuthManager.__new__(AuthManager)
    auth._session = None
    auth._session_lock = None  # will be set inside the asyncio.run coroutine
    return auth


async def _force_reset_in_loop(auth, reason: str):
    """Helper: builds the lock on the CURRENT event loop so it binds correctly."""
    if auth._session_lock is None:
        auth._session_lock = asyncio.Lock()
    return await auth.force_session_reset(reason=reason)


def test_force_session_reset_closes_open_session_and_returns_true():
    auth = _make_auth_stub()
    fake = _FakeSession()
    auth._session = fake

    result = asyncio.run(_force_reset_in_loop(auth, "unit-test"))

    assert result is True, "must report reset succeeded when session was open"
    assert fake.close_calls == 1, "underlying aiohttp session must be closed exactly once"
    assert auth._session is None, "session handle must be cleared so _get_session() builds a fresh one"


def test_force_session_reset_idempotent_when_session_already_closed():
    auth = _make_auth_stub()
    fake = _FakeSession()
    fake.closed = True  # simulate already-closed
    auth._session = fake

    result = asyncio.run(_force_reset_in_loop(auth, "duplicate-call"))

    assert result is False, "no-op when session already closed"
    assert fake.close_calls == 0, "must not double-close"


def test_force_session_reset_noop_when_no_session_exists():
    auth = _make_auth_stub()
    assert auth._session is None
    result = asyncio.run(_force_reset_in_loop(auth, "cold-start"))
    assert result is False


def test_force_session_reset_swallows_close_exceptions_but_still_clears_handle():
    """A close() that raises (e.g., transport already broken) must not crash the bot."""
    auth = _make_auth_stub()

    class _BrokenSession:
        closed = False

        async def close(self):
            raise RuntimeError("simulated transport error during close")

    auth._session = _BrokenSession()
    # Must NOT raise — production caller is in a hot path and this is a best-effort cleanup
    result = asyncio.run(_force_reset_in_loop(auth, "broken-close"))
    assert result is True
    assert auth._session is None


# ─── trading_bot._detect_and_handle_stuck_rest ────────────────────────────────


class _FakeAuth:
    """Captures force_session_reset() calls so tests can assert on them."""

    def __init__(self) -> None:
        self.reset_calls: List[str] = []

    async def force_session_reset(self, reason: str = "manual") -> bool:
        self.reset_calls.append(reason)
        return True


class _FakeBroker:
    def __init__(self) -> None:
        self.auth = _FakeAuth()


def _make_bot():
    """Minimal TopStepXTradingBot stub with just the bits the detector touches."""
    from trading_bot import TopStepXTradingBot

    bot = TopStepXTradingBot.__new__(TopStepXTradingBot)
    bot.broker_adapter = _FakeBroker()
    return bot


def test_first_fetch_does_not_trip_detector():
    """A single new timestamp seeds the tracker — must not trip the detector."""
    from trading_bot import TopStepXTradingBot

    bot = _make_bot()
    triggered = asyncio.run(
        TopStepXTradingBot._detect_and_handle_stuck_rest(
            bot, "MNQ", "5m", _fresh_ts()
        )
    )
    assert triggered is False
    assert bot.broker_adapter.auth.reset_calls == []


def test_two_identical_fetches_do_not_trip_detector():
    """≤ 2 identical timestamps → still under the count threshold."""
    from trading_bot import TopStepXTradingBot

    bot = _make_bot()
    ts = _fresh_ts()
    asyncio.run(TopStepXTradingBot._detect_and_handle_stuck_rest(bot, "MNQ", "5m", ts))
    triggered = asyncio.run(
        TopStepXTradingBot._detect_and_handle_stuck_rest(bot, "MNQ", "5m", ts)
    )
    assert triggered is False
    assert bot.broker_adapter.auth.reset_calls == []


def test_three_identical_fetches_within_2x_timeframe_do_not_trip_detector(monkeypatch):
    """3 identical timestamps in < 2× timeframe wall-clock dwell → tracker holds fire.

    A short quiet stretch in the middle of a session can legitimately return the same
    bar timestamp 3 times in rapid succession (e.g. 3 polls inside the same 5m bar).
    Detector must only fire when BOTH count AND wall-clock dwell exceed thresholds.
    """
    from trading_bot import TopStepXTradingBot
    import trading_bot as tb

    bot = _make_bot()
    ts = _fresh_ts()  # fresh — cold-start path must stay quiet

    # Freeze the monotonic clock; advance by 30s between calls (3 calls = 60s total,
    # under the 2 × 5m = 600s threshold).
    now = [1000.0]
    monkeypatch.setattr(tb.time, "monotonic", lambda: now[0])

    for i in range(3):
        triggered = asyncio.run(
            TopStepXTradingBot._detect_and_handle_stuck_rest(bot, "MNQ", "5m", ts)
        )
        assert triggered is False, f"fetch #{i+1} should not have tripped at {i*30}s dwell"
        now[0] += 30.0

    assert bot.broker_adapter.auth.reset_calls == [], (
        "no reset must fire within 2× timeframe dwell"
    )


def test_repro_2026_05_27_stuck_rest_triggers_session_reset(monkeypatch):
    """End-to-end repro: 5m timeframe, same bar_ts returned across 11+ minutes of wall-clock.

    On 2026-05-27 the bot received the same ``T12:05:00+00:00`` payload 1,077 times in
    60 minutes. This test reproduces the steady-state with 5 polls 3 min apart so the
    third+ poll is at > 10 min wall-clock dwell (the 2 × 5m threshold). Detector must
    trip exactly once and call ``force_session_reset``.
    """
    from trading_bot import TopStepXTradingBot
    import trading_bot as tb

    bot = _make_bot()
    ts = _fresh_ts()  # fresh — only steady-state path should fire

    now = [0.0]
    monkeypatch.setattr(tb.time, "monotonic", lambda: now[0])

    trips: List[bool] = []
    for i in range(5):
        triggered = asyncio.run(
            TopStepXTradingBot._detect_and_handle_stuck_rest(bot, "MNQ", "5m", ts)
        )
        trips.append(triggered)
        now[0] += 180.0  # 3 minutes between each poll

    # Polls at dwell 0, 180, 360, 540, 720 s. 2× 5m = 600s → first trip at the 720s poll.
    assert trips == [False, False, False, False, True], f"expected exactly one trip on the last poll, got {trips}"
    assert len(bot.broker_adapter.auth.reset_calls) == 1
    assert bot.broker_adapter.auth.reset_calls[0].startswith("rest-stuck:MNQ:5m")


def test_advancing_timestamp_resets_tracker(monkeypatch):
    """A fresh REST timestamp (broker caught up) wipes the dwell counter."""
    from trading_bot import TopStepXTradingBot
    import trading_bot as tb

    bot = _make_bot()
    now = [0.0]
    monkeypatch.setattr(tb.time, "monotonic", lambda: now[0])

    ts1 = _fresh_ts(seconds_old=60)  # fresh enough to skip cold-start
    ts2 = _fresh_ts(seconds_old=0)

    asyncio.run(TopStepXTradingBot._detect_and_handle_stuck_rest(bot, "MNQ", "5m", ts1))
    now[0] += 60.0
    asyncio.run(TopStepXTradingBot._detect_and_handle_stuck_rest(bot, "MNQ", "5m", ts1))
    # Bar advances — tracker should reset and the count goes back to 1.
    now[0] += 60.0
    asyncio.run(TopStepXTradingBot._detect_and_handle_stuck_rest(bot, "MNQ", "5m", ts2))

    rec = bot._rest_freshness_track["MNQ|5m"]
    assert rec["last_ts"] == ts2
    assert rec["consecutive"] == 1
    assert bot.broker_adapter.auth.reset_calls == [], "no reset on healthy advance"


def test_detector_isolates_per_symbol_and_timeframe(monkeypatch):
    """MNQ stuck must not trip a reset for MGC (separate symbol/tf trackers)."""
    from trading_bot import TopStepXTradingBot
    import trading_bot as tb

    bot = _make_bot()
    now = [0.0]
    monkeypatch.setattr(tb.time, "monotonic", lambda: now[0])

    mnq_ts = _fresh_ts()
    for _ in range(5):
        asyncio.run(TopStepXTradingBot._detect_and_handle_stuck_rest(bot, "MNQ", "5m", mnq_ts))
        now[0] += 180.0
    # MNQ should have tripped exactly once by now.
    assert len(bot.broker_adapter.auth.reset_calls) == 1

    # MGC, also at the same dwell window, but only seen once → must NOT trip.
    triggered = asyncio.run(
        TopStepXTradingBot._detect_and_handle_stuck_rest(
            bot, "MGC", "5m", _fresh_ts()
        )
    )
    assert triggered is False
    assert len(bot.broker_adapter.auth.reset_calls) == 1, "MGC must not piggy-back on MNQ trip"


def test_detector_does_not_loop_after_trip(monkeypatch):
    """After a trip the tracker resets — even if REST is STILL stuck, no second reset until the
    new dwell window expires. Prevents 'reset every poll' loops during a multi-hour outage."""
    from trading_bot import TopStepXTradingBot
    import trading_bot as tb

    bot = _make_bot()
    now = [0.0]
    monkeypatch.setattr(tb.time, "monotonic", lambda: now[0])

    ts = _fresh_ts()
    for _ in range(5):
        asyncio.run(TopStepXTradingBot._detect_and_handle_stuck_rest(bot, "MNQ", "5m", ts))
        now[0] += 180.0
    assert len(bot.broker_adapter.auth.reset_calls) == 1

    # Immediately re-poll twice more (1 min apart) with the same stuck ts.
    now[0] += 60.0
    asyncio.run(TopStepXTradingBot._detect_and_handle_stuck_rest(bot, "MNQ", "5m", ts))
    now[0] += 60.0
    asyncio.run(TopStepXTradingBot._detect_and_handle_stuck_rest(bot, "MNQ", "5m", ts))
    # Still only one reset — the tracker rearmed with dwell=0 after the trip.
    assert len(bot.broker_adapter.auth.reset_calls) == 1


def test_detector_skips_empty_or_none_timestamps():
    """Defensive: a bar with no timestamp must not crash the detector."""
    from trading_bot import TopStepXTradingBot

    bot = _make_bot()
    triggered = asyncio.run(
        TopStepXTradingBot._detect_and_handle_stuck_rest(bot, "MNQ", "5m", None)
    )
    assert triggered is False
    triggered = asyncio.run(
        TopStepXTradingBot._detect_and_handle_stuck_rest(bot, "MNQ", "5m", "")
    )
    assert triggered is False


def test_detector_swallows_reset_exception_and_does_not_crash_caller(monkeypatch):
    """If auth.force_session_reset() itself raises, the detector must log and continue."""
    from trading_bot import TopStepXTradingBot
    import trading_bot as tb

    bot = _make_bot()

    class _ExplodingAuth:
        async def force_session_reset(self, reason: str = "manual") -> bool:
            raise RuntimeError("simulated reset failure")

    bot.broker_adapter.auth = _ExplodingAuth()
    now = [0.0]
    monkeypatch.setattr(tb.time, "monotonic", lambda: now[0])

    ts = _fresh_ts()
    for _ in range(5):
        # Must not propagate the RuntimeError.
        asyncio.run(TopStepXTradingBot._detect_and_handle_stuck_rest(bot, "MNQ", "5m", ts))
        now[0] += 180.0


# ─── Cold-start fast path (2026-05-29 fix) ───────────────────────────────────


def test_coldstart_immediate_trip_when_first_fetch_returns_stale_bar(monkeypatch):
    """2026-05-29 repro: cold-start where the FIRST REST response is already
    ``> 2× timeframe`` old → detector trips immediately, no 10-minute wait.

    Log evidence: 08:35:30 bot start → 08:35:32 first REST returned bar from
    08:15 (1233s old vs 2× 5m = 600s threshold) → previously waited until
    08:45 for the steady-state dwell to expire. With the cold-start path the
    reset fires on call #1.
    """
    from trading_bot import TopStepXTradingBot
    import trading_bot as tb

    bot = _make_bot()
    monkeypatch.setattr(tb.time, "monotonic", lambda: 0.0)

    # bar is 1233 s old at the moment of fetch — matches the 2026-05-29 log.
    stale_ts = _fresh_ts(seconds_old=1233.0)
    triggered = asyncio.run(
        TopStepXTradingBot._detect_and_handle_stuck_rest(bot, "MNQ", "5m", stale_ts)
    )
    assert triggered is True, "stale-on-arrival bar must trip the cold-start fast path on call #1"
    assert len(bot.broker_adapter.auth.reset_calls) == 1
    assert bot.broker_adapter.auth.reset_calls[0].startswith("rest-coldstart:MNQ:5m")


def test_coldstart_does_not_double_trip_on_same_stuck_timestamp(monkeypatch):
    """After the cold-start trip we rearm the tracker — subsequent polls
    returning the SAME stale ts inside the new dwell window must NOT trip again.
    Prevents a reset storm during a multi-minute broker outage."""
    from trading_bot import TopStepXTradingBot
    import trading_bot as tb

    bot = _make_bot()
    now = [0.0]
    monkeypatch.setattr(tb.time, "monotonic", lambda: now[0])

    stale_ts = _fresh_ts(seconds_old=1500.0)
    # Call 1 → cold-start trip
    triggered = asyncio.run(TopStepXTradingBot._detect_and_handle_stuck_rest(bot, "MNQ", "5m", stale_ts))
    assert triggered is True
    # Calls 2-3 inside the new dwell window with same ts → no additional resets.
    now[0] += 30.0
    triggered = asyncio.run(TopStepXTradingBot._detect_and_handle_stuck_rest(bot, "MNQ", "5m", stale_ts))
    assert triggered is False
    now[0] += 30.0
    triggered = asyncio.run(TopStepXTradingBot._detect_and_handle_stuck_rest(bot, "MNQ", "5m", stale_ts))
    assert triggered is False
    assert len(bot.broker_adapter.auth.reset_calls) == 1, (
        "exactly ONE cold-start reset for this stuck-ts episode"
    )


def test_coldstart_threshold_at_2x_timeframe_boundary(monkeypatch):
    """Bar exactly at the 2× timeframe boundary should NOT trigger cold-start
    (strict ``>`` comparison) but a bar 1 s past should."""
    from trading_bot import TopStepXTradingBot
    import trading_bot as tb

    monkeypatch.setattr(tb.time, "monotonic", lambda: 0.0)

    bot_under = _make_bot()
    under_ts = _fresh_ts(seconds_old=599.0)  # 1 s under 2× 5m
    triggered = asyncio.run(
        TopStepXTradingBot._detect_and_handle_stuck_rest(bot_under, "MNQ", "5m", under_ts)
    )
    assert triggered is False, "bar < 2× tf must not trip cold-start"
    assert bot_under.broker_adapter.auth.reset_calls == []

    bot_over = _make_bot()
    over_ts = _fresh_ts(seconds_old=602.0)  # 2 s past the 600 s threshold
    triggered = asyncio.run(
        TopStepXTradingBot._detect_and_handle_stuck_rest(bot_over, "MNQ", "5m", over_ts)
    )
    assert triggered is True, "bar > 2× tf must trip cold-start on first sight"
    assert len(bot_over.broker_adapter.auth.reset_calls) == 1


def test_coldstart_scales_with_timeframe(monkeypatch):
    """Cold-start threshold = 2× timeframe so it scales correctly across timeframes.
    A bar 150 s old:
      - on 1m timeframe (threshold=120s)  → TRIP
      - on 5m timeframe (threshold=600s)  → no trip
      - on 15m timeframe (threshold=1800s)→ no trip
    """
    from trading_bot import TopStepXTradingBot
    import trading_bot as tb

    monkeypatch.setattr(tb.time, "monotonic", lambda: 0.0)

    ts_150 = _fresh_ts(seconds_old=150.0)

    bot1 = _make_bot()
    assert asyncio.run(TopStepXTradingBot._detect_and_handle_stuck_rest(bot1, "MNQ", "1m", ts_150)) is True

    bot2 = _make_bot()
    assert asyncio.run(TopStepXTradingBot._detect_and_handle_stuck_rest(bot2, "MNQ", "5m", ts_150)) is False

    bot3 = _make_bot()
    assert asyncio.run(TopStepXTradingBot._detect_and_handle_stuck_rest(bot3, "MNQ", "15m", ts_150)) is False


def test_coldstart_coalesces_multi_symbol_rotations(monkeypatch):
    """2026-05-29 follow-up: when all three symbols see stale data within ~500ms
    on cold-start, the FIRST rotation alone is enough — symbols #2 and #3 must
    NOT trigger their own ``force_session_reset`` (it's wasted work AND it
    catches in-flight requests mid-rotation, surfacing extra ``ServerDisconnectedError``
    ERRORs that aren't real failures).
    """
    from trading_bot import TopStepXTradingBot
    import trading_bot as tb

    bot = _make_bot()
    now = [0.0]
    monkeypatch.setattr(tb.time, "monotonic", lambda: now[0])

    stale_ts = _fresh_ts(seconds_old=1233.0)

    # Symbol 1: first observation → ACTUAL rotation.
    triggered_1 = asyncio.run(
        TopStepXTradingBot._detect_and_handle_stuck_rest(bot, "MNQ", "5m", stale_ts)
    )
    assert triggered_1 is True
    assert len(bot.broker_adapter.auth.reset_calls) == 1, "MNQ must rotate"

    # Symbols 2 + 3: 200ms and 250ms later, inside the coalesce window.
    # _trip_reset should return True (so the caller can downgrade STALE DATA
    # to WARNING) but NOT call force_session_reset again.
    now[0] += 0.2
    triggered_2 = asyncio.run(
        TopStepXTradingBot._detect_and_handle_stuck_rest(bot, "MES", "5m", _fresh_ts(seconds_old=1230.0))
    )
    now[0] += 0.05
    triggered_3 = asyncio.run(
        TopStepXTradingBot._detect_and_handle_stuck_rest(bot, "MGC", "5m", _fresh_ts(seconds_old=1235.0))
    )

    assert triggered_2 is True, "must still report 'recovery in progress' so caller can soften STALE DATA"
    assert triggered_3 is True
    assert len(bot.broker_adapter.auth.reset_calls) == 1, (
        f"only ONE actual session reset for the three-symbol cold-start storm; got "
        f"{len(bot.broker_adapter.auth.reset_calls)} resets: {bot.broker_adapter.auth.reset_calls}"
    )


def test_coldstart_allows_second_rotation_after_cooldown(monkeypatch):
    """The coalesce window must EXPIRE — if the route stays stuck past the
    cooldown, a fresh rotation is allowed (otherwise a multi-minute outage
    would be impossible to recover from after the first attempt)."""
    from trading_bot import TopStepXTradingBot
    import trading_bot as tb

    bot = _make_bot()
    now = [0.0]
    monkeypatch.setattr(tb.time, "monotonic", lambda: now[0])

    ts1 = _fresh_ts(seconds_old=1500.0)
    asyncio.run(TopStepXTradingBot._detect_and_handle_stuck_rest(bot, "MNQ", "5m", ts1))
    assert len(bot.broker_adapter.auth.reset_calls) == 1

    # Wait past the 10s coalesce window.
    now[0] += 15.0
    ts2 = _fresh_ts(seconds_old=1800.0)  # different stale ts, still old
    triggered = asyncio.run(
        TopStepXTradingBot._detect_and_handle_stuck_rest(bot, "MES", "5m", ts2)
    )
    assert triggered is True
    assert len(bot.broker_adapter.auth.reset_calls) == 2, (
        "second rotation should fire after coalesce window expires"
    )


def test_concurrent_trips_coalesce_to_one_actual_rotation():
    """Race-condition regression (2026-05-29 14:00:21):
    when ``asyncio.gather`` runs ``_detect_and_handle_stuck_rest`` for MES and MNQ
    in the same event loop, BOTH tasks used to read ``_last_session_reset_at_mono``
    as the same old value before either had written back ⇒ both fired
    ``force_session_reset`` 5ms apart. With the timestamp-claim-before-await
    fix, the second task sees the fresh stamp and takes the coalesce path.

    Uses real monotonic clock (no monkeypatch) because ``asyncio.sleep`` depends on
    ``time.monotonic`` and would hang if we froze it — the slow_reset wrap below
    needs the loop to actually advance.
    """
    from trading_bot import TopStepXTradingBot

    bot = _make_bot()

    # Slow down the auth.force_session_reset await so concurrent task #2 gets
    # a real chance to enter ``_trip_reset`` while task #1 is mid-await. The
    # delay is small enough (50ms) that it stays inside the 10s coalesce window.
    original = bot.broker_adapter.auth.force_session_reset

    async def _slow_reset(reason: str = "manual") -> bool:
        await asyncio.sleep(0.05)
        return await original(reason=reason)

    bot.broker_adapter.auth.force_session_reset = _slow_reset  # type: ignore[assignment]

    stale_ts = _fresh_ts(seconds_old=1500.0)

    async def _race():
        return await asyncio.gather(
            TopStepXTradingBot._detect_and_handle_stuck_rest(bot, "MNQ", "5m", stale_ts),
            TopStepXTradingBot._detect_and_handle_stuck_rest(bot, "MES", "5m", stale_ts),
        )

    results = asyncio.run(_race())
    # Both tasks should report "trip is in flight or just completed" (True),
    # but only ONE force_session_reset call should have actually fired.
    assert results == [True, True]
    assert len(bot.broker_adapter.auth.reset_calls) == 1, (
        f"concurrent gather must coalesce; got {len(bot.broker_adapter.auth.reset_calls)} "
        f"resets ({bot.broker_adapter.auth.reset_calls})"
    )


def test_coldstart_recovers_in_seconds_not_minutes(monkeypatch):
    """The whole point of the cold-start fix: recovery time drops from ~10 min
    (steady-state path) to a single poll. This test asserts the timeline."""
    from trading_bot import TopStepXTradingBot
    import trading_bot as tb

    bot = _make_bot()
    now = [0.0]
    monkeypatch.setattr(tb.time, "monotonic", lambda: now[0])

    # Simulate the 2026-05-29 scenario: bot starts, polls every 5s, first poll
    # returns a 1233 s old bar (the broker was pinned to 08:15 while bot started
    # at 08:35:30).
    stale_ts = _fresh_ts(seconds_old=1233.0)
    triggered_at_poll = None
    for i in range(120):  # up to 600s of polling
        if asyncio.run(TopStepXTradingBot._detect_and_handle_stuck_rest(bot, "MNQ", "5m", stale_ts)):
            triggered_at_poll = i
            break
        now[0] += 5.0

    assert triggered_at_poll == 0, (
        f"cold-start must trip on poll #0, not poll #{triggered_at_poll} — "
        "otherwise the broker outage burns minutes of trading window"
    )
