"""Transient-error retry for AuthManager._make_request (2026-05-29 fix).

Background:
    The 2026-05-29 morning log shows 33 ``Request failed: ServerDisconnectedError``
    / ``ClientConnectionError`` ERRORs spread across the trading window. Each
    one cost the strategy a poll AND surfaced an empty / unhelpful log line. The
    pattern: the broker drops keepalive connections after ~75 s of idle, the
    pool returns a dead socket on the next request, and the request raises.
    On the same socket pool the NEXT request 5 s later usually has the same
    problem.

Fix: classify the exception. If it's an aiohttp transient AND the endpoint is
idempotent (not ``/Order/place``), close the current session to force a fresh
TCP+TLS handshake on the next request and retry once with a small backoff.

These tests verify:
    * a single transient → retry succeeds → return value reflects success
    * persistent transient (both attempts fail) → return ``{"error": ...,
      "transient": True}``
    * ``/Order/place`` is never retried (non-idempotent: silent retry could
      duplicate an order)
    * non-transient errors are NOT retried (would just burn a request)
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiohttp
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ─── Fake session machinery ──────────────────────────────────────────────────


class _FakeResponse:
    def __init__(self, status: int, body: str = "{}") -> None:
        self.status = status
        self._body = body

    async def text(self) -> str:
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _RaisingResponseCtx:
    """Context manager that raises on __aenter__ — simulates aiohttp transients."""

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    async def __aenter__(self):
        raise self._exc

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _ScriptedSession:
    """aiohttp ClientSession stand-in that returns scripted responses/exceptions in order.

    Each call to ``request()`` pops the next entry from ``responses``. Entries are
    either a ``_FakeResponse`` (returned directly) or an Exception instance
    (raised on ``__aenter__``).
    """

    def __init__(self, responses: List[Any]) -> None:
        self._responses = list(responses)
        self.closed = False
        self.close_calls = 0
        self.request_calls: List[Dict[str, Any]] = []

    def request(self, method: str, url: str, **kwargs) -> Any:
        self.request_calls.append({"method": method, "url": url, **kwargs})
        if not self._responses:
            raise AssertionError(
                f"_ScriptedSession exhausted at request #{len(self.request_calls)} "
                f"({method} {url})"
            )
        nxt = self._responses.pop(0)
        if isinstance(nxt, BaseException):
            return _RaisingResponseCtx(nxt)
        return nxt

    async def close(self) -> None:
        self.close_calls += 1
        self.closed = True


def _make_auth_with_sessions(session_queue: List[_ScriptedSession]):
    """Build AuthManager bypassing __init__ and inject a queue of sessions.

    The retry path closes the current session and asks ``_get_session()`` for a
    fresh one — we pop the next from the queue on demand so tests can assert
    exactly how many session rotations happened.
    """
    from core.auth import AuthManager

    auth = AuthManager.__new__(AuthManager)
    auth.api_key = "dummy"
    auth.username = "dummy"
    auth.base_url = "https://api.topstepx.example"
    auth.session_token = "dummy-token"
    auth.token_expiry = None
    auth._session = None
    auth._session_lock = None  # built lazily on the right loop
    auth.token_callbacks: List[Any] = []
    auth.rate_limiter = None  # exercises the no-rate-limiter code path

    pending = list(session_queue)
    sessions_used: List[_ScriptedSession] = []

    async def _fake_get_session():
        # Initial call OR after a close → return the next scripted session.
        if auth._session is not None and not auth._session.closed:
            return auth._session
        if not pending:
            raise AssertionError("Test ran out of pre-scripted sessions")
        auth._session = pending.pop(0)
        sessions_used.append(auth._session)
        return auth._session

    auth._get_session = _fake_get_session  # type: ignore[assignment]
    auth._sessions_used = sessions_used  # type: ignore[attr-defined]
    return auth


async def _init_lock_and_call(auth, method: str, endpoint: str, **kwargs):
    if auth._session_lock is None:
        auth._session_lock = asyncio.Lock()
    return await auth._make_request(method, endpoint, **kwargs)


# ─── Tests: success/failure of the retry ─────────────────────────────────────


def test_retry_on_server_disconnected_succeeds_on_second_attempt():
    """First request raises ServerDisconnectedError, second succeeds → caller sees success."""
    sess1 = _ScriptedSession([aiohttp.ServerDisconnectedError("conn dropped")])
    sess2 = _ScriptedSession([_FakeResponse(200, '{"orders": []}')])
    auth = _make_auth_with_sessions([sess1, sess2])

    result = asyncio.run(
        _init_lock_and_call(auth, "POST", "/api/Order/search", data={"accountId": 1})
    )

    assert result == {"orders": []}, f"retry must return the second-attempt body, got {result}"
    assert sess1.close_calls == 1, "the failing session must be closed before retry"
    assert len(auth._sessions_used) == 2, "two distinct sessions must have been used"


def test_retry_on_client_connection_error_succeeds():
    """ClientConnectionError (parent of many TCP failures) must also be retried."""
    sess1 = _ScriptedSession([aiohttp.ClientConnectionError("ECONNRESET")])
    sess2 = _ScriptedSession([_FakeResponse(200, '{"positions": []}')])
    auth = _make_auth_with_sessions([sess1, sess2])

    result = asyncio.run(
        _init_lock_and_call(auth, "POST", "/api/Position/searchOpen", data={"accountId": 1})
    )

    assert result == {"positions": []}
    assert sess1.close_calls == 1


def test_retry_on_timeout_error_succeeds():
    """asyncio.TimeoutError is a classic transient — retry must cover it."""
    sess1 = _ScriptedSession([asyncio.TimeoutError()])
    sess2 = _ScriptedSession([_FakeResponse(200, "{}")])
    auth = _make_auth_with_sessions([sess1, sess2])

    result = asyncio.run(
        _init_lock_and_call(auth, "POST", "/api/Order/search", data={})
    )

    assert "error" not in result, f"timeout retry should produce a normal success, got {result}"


def test_retry_exhausted_when_both_attempts_fail_marks_response_transient(caplog):
    """Two ServerDisconnectedError in a row → return {"error": ..., "transient": True}.
    Logged at ERROR with the exception class name so operators can grep for it."""
    sess1 = _ScriptedSession([aiohttp.ServerDisconnectedError("broker rotation")])
    sess2 = _ScriptedSession([aiohttp.ServerDisconnectedError("broker rotation #2")])
    auth = _make_auth_with_sessions([sess1, sess2])

    import logging
    caplog.set_level(logging.WARNING)

    result = asyncio.run(
        _init_lock_and_call(auth, "POST", "/api/Order/search", data={})
    )

    assert isinstance(result, dict)
    assert "error" in result, f"failure must surface as dict with error key, got {result}"
    assert result.get("transient") is True, "exhausted-retry must flag transient=True for caller"
    # Both attempts logged: 1× WARNING ("rotating session"), 1× ERROR ("after 2 attempts").
    warns = [r for r in caplog.records if r.levelname == "WARNING" and "rotating session" in r.message]
    errs = [r for r in caplog.records if r.levelname == "ERROR" and "after 2 attempts" in r.message]
    assert len(warns) == 1, f"exactly one retry-warning expected, got {len(warns)}"
    assert len(errs) == 1, f"exactly one final-error expected, got {len(errs)}"
    # The error message must include the exception class name (the 2026-05-27 fix).
    assert "ServerDisconnectedError" in errs[0].message


def test_order_place_is_never_retried():
    """``/Order/place`` is non-idempotent — a silent retry could place duplicate orders.
    Even a transient exception must surface the failure immediately so the strategy
    layer can decide whether to retry with idempotency safeguards."""
    sess1 = _ScriptedSession([aiohttp.ServerDisconnectedError("dropped before reply")])
    # Provide a second session so we'd notice if retry kicked in (it must not).
    sess2 = _ScriptedSession([_FakeResponse(200, '{"orderId": 999}')])
    auth = _make_auth_with_sessions([sess1, sess2])

    result = asyncio.run(
        _init_lock_and_call(auth, "POST", "/api/Order/place", data={"symbol": "MNQ", "qty": 1})
    )

    assert "error" in result, "must surface error, not silently retry"
    assert len(auth._sessions_used) == 1, (
        "must NOT rotate sessions for /Order/place even on transient — got "
        f"{len(auth._sessions_used)} sessions used"
    )
    assert sess1.close_calls == 0, "must not close the session for non-idempotent endpoints"


def test_non_transient_error_is_not_retried():
    """A ValueError or other non-aiohttp exception is a programmer error / unexpected
    failure mode — retrying it just wastes another request. Must surface immediately."""
    sess1 = _ScriptedSession([RuntimeError("totally unexpected")])
    sess2 = _ScriptedSession([_FakeResponse(200, "{}")])
    auth = _make_auth_with_sessions([sess1, sess2])

    result = asyncio.run(
        _init_lock_and_call(auth, "POST", "/api/Order/search", data={})
    )

    assert "error" in result
    assert result.get("transient") is not True, "non-transient must not be flagged transient"
    assert len(auth._sessions_used) == 1, "must not retry / rotate on non-transient"


def test_first_attempt_success_does_not_rotate_session():
    """Healthy request → no retry, no session rotation. Sanity check for the
    common path."""
    sess1 = _ScriptedSession([_FakeResponse(200, '{"ok": true}')])
    auth = _make_auth_with_sessions([sess1])

    result = asyncio.run(
        _init_lock_and_call(auth, "POST", "/api/Order/search", data={})
    )

    assert result == {"ok": True}
    assert sess1.close_calls == 0
    assert len(auth._sessions_used) == 1


# ── Transient-retry dedupe (2026-05-29) ──────────────────────────────────────


def test_transient_retry_skips_rotation_when_session_was_just_reset(caplog, monkeypatch):
    """2026-05-29 dedupe regression: when a sibling caller has rotated the
    session within ``_transient_dedupe_window_s``, the current transient retry
    must NOT close the (just-built) session again and must NOT emit its own
    "rotating session" WARNING. The 2026-05-29 14:00:32 log showed three
    identical WARNINGs 215 ms apart — this test pins the new behaviour where
    only the first caller surfaces the warning."""
    import logging
    from core import auth as auth_mod

    # The sibling has already rotated, so the current session has just been
    # rebuilt; the SAME session must be reused for the retry. Scripted with
    # [transient, success] so the second request through the same session succeeds.
    sess1 = _ScriptedSession([
        aiohttp.ClientConnectionError("blip"),
        _FakeResponse(200, '{"positions": []}'),
    ])
    auth = _make_auth_with_sessions([sess1])
    # Mark the session as having been rotated *just now* (sibling caller).
    auth._last_session_reset_at_mono = 100.0
    auth._transient_dedupe_window_s = 1.0
    monkeypatch.setattr(auth_mod, "_monotonic_now", lambda: 100.2)  # 200ms after rotation

    caplog.set_level(logging.DEBUG)
    result = asyncio.run(
        _init_lock_and_call(auth, "POST", "/api/Position/searchOpen", data={})
    )

    assert result == {"positions": []}, "request must still succeed via the same session"
    assert sess1.close_calls == 0, (
        "session must NOT be closed when within the dedupe window "
        "(a sibling has already rotated)"
    )
    assert len(auth._sessions_used) == 1, (
        "dedupe path must reuse the existing session — no new session built"
    )
    warns = [r for r in caplog.records if r.levelname == "WARNING" and "rotating session" in r.message]
    assert warns == [], f"must NOT emit the rotating-session WARNING; got {[r.message for r in warns]}"
    debugs = [r for r in caplog.records if "skipping duplicate session rotation" in r.message]
    assert len(debugs) == 1, "must surface ONE DEBUG line explaining the dedupe"


def test_transient_retry_still_rotates_when_outside_dedupe_window(caplog, monkeypatch):
    """If no recent sibling rotation, the transient retry MUST rotate the
    session (the original behaviour). Without this we'd lose the fix that
    bypasses the dead pool slot."""
    import logging
    from core import auth as auth_mod

    sess1 = _ScriptedSession([aiohttp.ServerDisconnectedError("dead socket")])
    sess2 = _ScriptedSession([_FakeResponse(200, '{"orders": []}')])
    auth = _make_auth_with_sessions([sess1, sess2])
    # Last rotation was a *long* time ago, well outside the dedupe window.
    auth._last_session_reset_at_mono = 100.0
    auth._transient_dedupe_window_s = 1.0
    monkeypatch.setattr(auth_mod, "_monotonic_now", lambda: 200.0)  # 100s after rotation

    caplog.set_level(logging.WARNING)
    result = asyncio.run(
        _init_lock_and_call(auth, "POST", "/api/Order/search", data={})
    )

    assert result == {"orders": []}
    assert sess1.close_calls == 1, "outside dedupe → must rotate"
    warns = [r for r in caplog.records if "rotating session" in r.message]
    assert len(warns) == 1, "must emit the rotating-session WARNING when not deduped"
