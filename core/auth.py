"""
Authentication Module - Handles broker authentication and token management.

This module abstracts authentication logic, making it easy to swap
authentication methods or add new brokers.
"""

import os
import logging
import base64
import asyncio
import time as _time
from typing import Optional, List, Dict, Any, Callable
from datetime import datetime, timezone, timedelta
import aiohttp


def _monotonic_now() -> float:
    """Wrapper that lets tests monkeypatch ``core.auth._monotonic_now`` without
    touching the stdlib's ``time.monotonic`` (which other modules use too)."""
    return _time.monotonic()

from core.json_fast import dumps_str, loads as json_fast_loads

# ─── Transient-failure classification (2026-05-29 fix) ────────────────────────
# These exception types fire when the broker's edge rotates a TCP route, drops a
# keepalive connection, or the network blips — none of them indicate the request
# was actually delivered to the broker. Retrying them with a freshly-built
# session usually succeeds on the next attempt and lets us avoid surfacing
# misleading "Request failed: ServerDisconnectedError" ERRORs on every poll.
# Anything not in this tuple is treated as terminal (logic error, HTTP 4xx
# parsed elsewhere, JSON decode failure) and surfaces immediately.
_TRANSIENT_EXC: tuple = (
    aiohttp.ServerDisconnectedError,
    aiohttp.ClientConnectionError,
    aiohttp.ClientPayloadError,
    asyncio.TimeoutError,
)

# Endpoints that we MUST NOT auto-retry — non-idempotent / state-mutating where
# a silent retry could duplicate the action (e.g. placing two orders for one
# user-intended click). We still surface the transient ERROR to the caller so
# the strategy layer can decide what to do.
_NON_IDEMPOTENT_PREFIXES: tuple = (
    "/api/Order/place",
)

# Try to import jwt (PyJWT), fallback to base64 if not available
try:
    import jwt
    HAS_JWT = True
except ImportError:
    HAS_JWT = False
    jwt = None

logger = logging.getLogger(__name__)


class AuthenticationError(Exception):
    """Raised when authentication fails."""
    pass


class AuthManager:
    """
    Manages authentication and session tokens.
    
    Handles token refresh, expiration checking, and authentication requests.
    """
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        username: Optional[str] = None,
        base_url: str = "https://api.topstepx.com"
    ):
        """
        Initialize authentication manager.
        
        Args:
            api_key: API key (or from environment)
            username: Username (or from environment)
            base_url: Base API URL
        """
        self.api_key = api_key or os.getenv('PROJECT_X_API_KEY') or os.getenv('TOPSTEPX_API_KEY') or os.getenv('TOPSETPX_API_KEY')
        self.username = username or os.getenv('PROJECT_X_USERNAME') or os.getenv('TOPSTEPX_USERNAME') or os.getenv('TOPSETPX_USERNAME')
        self.base_url = base_url
        
        # Try to load JWT token from environment (useful for Railway deployment)
        env_jwt = os.getenv('JWT_TOKEN')
        if env_jwt:
            self.session_token = env_jwt
            # Parse JWT to extract expiration time
            try:
                if HAS_JWT and jwt:
                    decoded = jwt.decode(env_jwt, options={"verify_signature": False})
                    exp_timestamp = decoded.get('exp')
                    if exp_timestamp:
                        self.token_expiry = datetime.fromtimestamp(exp_timestamp, tz=timezone.utc)
                        logger.info(f"Loaded JWT from environment (expires: {self.token_expiry})")
                    else:
                        self.token_expiry = None
                        logger.warning("JWT loaded from environment but has no expiration claim")
                else:
                    # Fallback to base64 decoding
                    parts = env_jwt.split('.')
                    if len(parts) >= 2:
                        payload = parts[1]
                        payload += '=' * (4 - len(payload) % 4)
                        decoded = json_fast_loads(base64.urlsafe_b64decode(payload))
                        exp_timestamp = decoded.get('exp')
                        if exp_timestamp:
                            self.token_expiry = datetime.fromtimestamp(exp_timestamp, tz=timezone.utc)
                            logger.info(f"Loaded JWT from environment (expires: {self.token_expiry})")
                        else:
                            self.token_expiry = None
            except Exception as parse_err:
                logger.warning(f"Failed to parse JWT from environment: {parse_err}")
                self.token_expiry = None
        else:
            self.session_token = None
            self.token_expiry = None

        # Shared aiohttp session (created lazily on first request)
        self._session: Optional[aiohttp.ClientSession] = None
        self._session_lock = asyncio.Lock()
        # ── 2026-05-29: shared rotation tracker for transient-retry coalescing ──
        # When several concurrent in-flight requests blow up on the same dead
        # keepalive socket (e.g. three Position/searchOpen polls firing
        # simultaneously after a session rotation), each one used to log its own
        # ``🔁 Request transient X for /Y/Z (attempt 1/2) — rotating session``
        # WARNING and close the (already-closed-by-a-sibling) session a second/
        # third time. Both the noise and the redundant rotations were wasted work.
        # The tracker (monotonic timestamp of the most recent close, ``None`` ⇒
        # never rotated) lets a concurrent transient-error handler observe
        # "another caller rotated the session ${elapsed}s ago, the new pool slot
        # hasn't even been opened yet" and skip the rotate-and-warn — just sleep
        # the backoff and let the retry attempt use the fresh session.
        self._last_session_reset_at_mono: Optional[float] = None
        # 1s is long enough to cover a TLS handshake on the new socket but short
        # enough that a route that goes stale again within seconds still gets
        # rotated. Tunable via ``AIOHTTP_TRANSIENT_DEDUPE_S`` for ops.
        try:
            self._transient_dedupe_window_s: float = max(
                0.0, float(os.getenv("AIOHTTP_TRANSIENT_DEDUPE_S", "1.0"))
            )
        except (TypeError, ValueError):
            self._transient_dedupe_window_s = 1.0
    
    def _is_token_expired(self) -> bool:
        """
        Check if current token is expired.
        
        Returns:
            True if token is expired or missing
        """
        if not self.session_token:
            return True
        
        if not self.token_expiry:
            # If we don't know expiration, assume expired for safety
            return True
        
        # Add 5 minute buffer before actual expiration
        buffer = datetime.now(timezone.utc) + timedelta(minutes=5)
        return buffer >= self.token_expiry
    
    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session and not self._session.closed:
            return self._session
        async with self._session_lock:
            if self._session and not self._session.closed:
                return self._session
            timeout = aiohttp.ClientTimeout(
                total=float(os.getenv("API_TIMEOUT", "30")),
                connect=3,
            )
            # ── G: TLS keep-alive + connection-pool tuning ──────────────────────────────
            # ``keepalive_timeout`` was 30 s; the broker's idle-close is ~60 s, so 75 s
            # gave us at least one chance to reuse the TCP+TLS session before a new
            # handshake. ``limit_per_host=16`` allows multi-symbol parallel POSTs (E1)
            # without blocking on a single-host pool of 1.
            try:
                ka = float(os.getenv("AIOHTTP_KEEPALIVE_TIMEOUT", "75") or 75.0)
            except ValueError:
                ka = 75.0
            connector = aiohttp.TCPConnector(
                limit=64,
                limit_per_host=16,
                keepalive_timeout=ka,
                enable_cleanup_closed=True,
                # Force connection reuse — aiohttp will pool across requests by default,
                # but we set this explicitly so a regression elsewhere doesn't disable it.
                force_close=False,
            )
            # ── C: orjson serializer for outgoing POST bodies (3–5× faster than stdlib) ──
            # We pass dumps_str (string out) because aiohttp expects a ``Callable[[Any], str]``.
            self._session = aiohttp.ClientSession(
                timeout=timeout,
                connector=connector,
                json_serialize=dumps_str,
            )
            # 2026-05-29: demoted from INFO → DEBUG. This line fires on every
            # ``force_session_reset`` (post-rotation rebuild) AND at startup. With
            # the transient-retry coalesce + the stuck-REST coalesce, rotations
            # are rare in the steady state — but startup + occasional reset still
            # produced 5+ INFO lines in a 25-min healthy session, drowning out
            # actual lifecycle events. Operators who care about session creation
            # can flip the level via ``configure_logging(default_level=DEBUG)``
            # or enable DEBUG for ``core.auth`` selectively.
            logger.debug(
                "HTTP session ready: keepalive=%.0fs, limit=64, limit_per_host=16, "
                "json_serializer=%s",
                ka, "orjson" if dumps_str.__module__.endswith("json_fast") else "stdlib",
            )
            return self._session

    async def close(self) -> None:
        """Close the shared HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()

    async def force_session_reset(self, reason: str = "manual") -> bool:
        """Close the shared aiohttp session so the next ``_get_session()`` builds
        a brand-new TCP+TLS connection (fresh route, fresh keepalive slot).

        Why this exists: the broker's ``/api/History/retrieveBars`` has been
        observed serving a **cached response pinned to a specific keepalive
        connection** — on 2026-05-27 the bot received the same ``last_bar_ts =
        2026-05-27T12:05:00+00:00`` payload 1,077 times across a 60-minute
        window, then immediately advanced the moment an unrelated
        ``/api/Auth/...`` call forced a new TCP handshake. Closing the session
        rotates the route so we stop hitting whatever cache layer is pinned to
        it, without forcing a full re-auth (the bearer token stays valid).

        Idempotent: returns ``False`` when there's nothing to reset, ``True``
        when an open session was actually closed.

        2026-05-29: stamps ``_last_session_reset_at_mono`` so the transient
        retry handler in ``_make_request`` can coalesce concurrent in-flight
        rotation attempts (see ``_transient_dedupe_window_s``).
        """
        async with self._session_lock:
            if self._session is None or self._session.closed:
                # Update the timestamp anyway: any concurrent transient handler
                # racing to rotate the session should see "yes, just rotated"
                # and skip its redundant close. Safe-set so test mocks that
                # bypass ``__init__`` don't crash.
                try:
                    self._last_session_reset_at_mono = _monotonic_now()
                except Exception:
                    pass
                return False
            try:
                await self._session.close()
            except Exception as e:
                logger.debug("force_session_reset: error closing session (%s): %s", reason, e)
            finally:
                self._session = None
                try:
                    self._last_session_reset_at_mono = _monotonic_now()
                except Exception:
                    pass
        logger.warning(
            "🔁 HTTP session reset (reason=%s) — next request will build a fresh TCP+TLS connection",
            reason,
        )
        return True

    async def _make_request(
        self,
        method: str,
        endpoint: str,
        data: Optional[Dict] = None,
        headers: Optional[Dict] = None,
        timeout: int = 30,
        *,
        quiet_client_errors: bool = False,
    ) -> Dict[str, Any]:
        """
        Make HTTP request to TopStepX API.
        
        Args:
            method: HTTP method (GET, POST, etc.)
            endpoint: API endpoint
            data: Request data (for POST requests)
            headers: Request headers
            timeout: Request timeout in seconds
            
        Returns:
            Response dictionary
        """
        url = f"{self.base_url}{endpoint}"
        request_headers = headers or {}
        
        # Add auth header if we have a token (never for unauthenticated auth endpoints)
        skip_bearer = endpoint.startswith("/api/Auth/")
        if self.session_token and "Authorization" not in request_headers and not skip_bearer:
            request_headers["Authorization"] = f"Bearer {self.session_token}"

        # ── J: only run the None-strip pass when we know None values are present ──
        # Adapter callers (post-2026-05-21) skip None at source, so most payloads
        # arrive clean. Avoid the dict-comprehension cost when not needed.
        if not data:
            cleaned_data = None
        elif any(v is None for v in data.values()):
            cleaned_data = {k: v for k, v in data.items() if v is not None}
        else:
            cleaned_data = data

        if endpoint == "/api/History/retrieveBars":
            try:
                logger.debug(f"📦 retrieveBars payload: {dumps_str(cleaned_data)}")
            except Exception:
                logger.debug(f"📦 retrieveBars payload (non-JSON): {cleaned_data}")

        # ── Transient-error retry policy (2026-05-29 fix) ────────────────────────
        # The 2026-05-29 log showed 33 ``Request failed: ServerDisconnectedError``
        # / ``ClientConnectionError`` ERRORs spread across the morning — each one
        # surfaced an empty "Request failed:" line, each one cost the strategy a
        # poll, and each one usually would have succeeded on a fresh TCP route.
        # The pattern: keepalive connection got torn down by the broker between
        # polls (~75s idle close), the first request after that gets the dead
        # socket back from the pool, raises, and the next poll 5s later has the
        # exact same problem because the SAME stale pool slot may still be there.
        #
        # Fix: classify the exception. If it's in ``_TRANSIENT_EXC`` and the
        # endpoint is idempotent (not /Order/place), close the current session
        # to force a fresh TCP+TLS handshake on the next ``_get_session()`` call
        # and retry once with a short backoff. Two attempts total. The retry
        # log line uses WARNING so an operator can see the recovery happening
        # without an ERROR storm cluttering the file.
        is_non_idempotent = any(endpoint.startswith(p) for p in _NON_IDEMPOTENT_PREFIXES)
        max_retries = 0 if is_non_idempotent else 1

        last_transient_exc: Optional[Exception] = None
        for attempt in range(max_retries + 1):
            try:
                session = await self._get_session()
                # Preserve connect/sock_connect so a bad DNS host fails in ~3s
                # instead of burning the full ``API_TIMEOUT`` on getaddrinfo.
                try:
                    connect_s = float(os.getenv("API_CONNECT_TIMEOUT", "3") or 3)
                except ValueError:
                    connect_s = 3.0
                req_timeout = aiohttp.ClientTimeout(
                    total=timeout,
                    connect=connect_s,
                    sock_connect=connect_s,
                )

                async with session.request(
                    method=method.upper(),
                    url=url,
                    json=cleaned_data if method.upper() == "POST" else None,
                    headers=request_headers,
                    timeout=req_timeout,
                ) as resp:
                    status_code = resp.status
                    body_text = (await resp.text()) or ""
                    body_text = body_text.strip()

                    if status_code == 429:
                        endpoint_key = endpoint.split("?")[0]
                        backoff_delay = 1.0
                        if hasattr(self, "rate_limiter") and self.rate_limiter:
                            self.rate_limiter.record_429_error(endpoint_key)
                            backoff_delay = float(self.rate_limiter.get_backoff_delay(endpoint_key))
                        logger.warning(f"⏳ HTTP 429 Too Many Requests for {endpoint}. Waiting {backoff_delay:.2f}s before retry...")
                        await asyncio.sleep(backoff_delay)
                        # Retry once
                        async with session.request(
                            method=method.upper(),
                            url=url,
                            json=cleaned_data if method.upper() == "POST" else None,
                            headers=request_headers,
                            timeout=req_timeout,
                        ) as retry_resp:
                            status_code = retry_resp.status
                            body_text = ((await retry_resp.text()) or "").strip()
                            if status_code == 429 and hasattr(self, "rate_limiter") and self.rate_limiter:
                                self.rate_limiter.reset_429_backoff(endpoint_key)

                    if status_code >= 400:
                        snippet = body_text.replace("\n", " ")
                        if len(snippet) > 600:
                            snippet = snippet[:600] + "…"
                        error_msg = f"HTTP {status_code} | body: {snippet}" if snippet else f"HTTP {status_code}"
                        if status_code == 404 and (
                            "/api/Fill/search" in endpoint or endpoint.startswith("/api/Position/")
                        ):
                            logger.debug(
                                "Endpoint returned 404 (expected in some cases): %s",
                                endpoint,
                            )
                            return {"error": error_msg, "status_code": status_code, "response_text": body_text}
                        if quiet_client_errors:
                            logger.debug(error_msg)
                        else:
                            logger.error(error_msg)
                        return {"error": error_msg, "status_code": status_code, "response_text": body_text}

                    if not body_text:
                        return {"success": True, "message": "Operation completed successfully"}
                    try:
                        json_response = json_fast_loads(body_text)
                        if isinstance(json_response, dict) and json_response.get("error") is None:
                            json_response = {k: v for k, v in json_response.items() if k != "error" or v is not None}
                        return json_response
                    except ValueError as e:
                        logger.error(f"Failed to parse JSON response: {e}")
                        return {"error": f"Invalid JSON response: {e}", "response_text": body_text}
            except _TRANSIENT_EXC as e:
                last_transient_exc = e
                if attempt < max_retries:
                    backoff_s = 0.3 + 0.2 * attempt
                    # ── Coalesce concurrent in-flight transient rotations (2026-05-29) ─
                    # When N concurrent requests hit the same dead socket pool slot at
                    # once, each one used to fire its own ``🔁 Request transient X ...
                    # rotating session`` WARNING and close the (already-closed-by-
                    # a-sibling) session in turn. The 2026-05-29 14:00:32 log shows
                    # three identical lines for /Position/searchOpen 215ms apart.
                    # If another caller has already rotated within the dedupe window,
                    # the new pool slot is *just* being established — close()'ing
                    # again wastes the rebuild AND emits a duplicate WARNING. Just
                    # sleep and retry on the fresh session.
                    #
                    # ``getattr`` defaults keep this code working when ``AuthManager``
                    # is instantiated via ``__new__`` (test mocks) without calling
                    # ``__init__`` — the dedupe simply becomes a no-op for them.
                    last_reset = getattr(self, "_last_session_reset_at_mono", None)
                    dedupe_window = float(getattr(self, "_transient_dedupe_window_s", 0.0) or 0.0)
                    now_mono = _monotonic_now()
                    skip_rotate = (
                        dedupe_window > 0.0
                        and last_reset is not None
                        and (now_mono - float(last_reset)) < dedupe_window
                    )
                    if skip_rotate:
                        elapsed = now_mono - float(last_reset)
                        logger.debug(
                            "🔁 Request transient %s for %s — skipping duplicate session "
                            "rotation (sibling rotated %.2fs ago, inside %.1fs dedupe window). "
                            "Retrying on fresh session in %.1fs.",
                            type(e).__name__, endpoint,
                            elapsed, dedupe_window, backoff_s,
                        )
                    else:
                        logger.warning(
                            "🔁 Request transient %s for %s (attempt %d/%d) — "
                            "rotating session and retrying in %.1fs",
                            type(e).__name__, endpoint, attempt + 1, max_retries + 1, backoff_s,
                        )
                        try:
                            async with self._session_lock:
                                if self._session is not None and not self._session.closed:
                                    await self._session.close()
                                self._session = None
                                # Stamp the rotation timestamp INSIDE the lock so
                                # concurrent waiters take the dedupe path even if
                                # they wake before our outer ``last_reset`` read.
                                self._last_session_reset_at_mono = _monotonic_now()
                        except Exception as close_exc:
                            logger.debug(
                                "Mid-retry session close swallowed (%s): %s",
                                type(close_exc).__name__, close_exc,
                            )
                    await asyncio.sleep(backoff_s)
                    continue
                # All retries exhausted — surface the transient ERROR.
                # str(e) is empty for many aiohttp transients (ServerDisconnectedError,
                # ClientPayloadError, ConnectionResetError, asyncio.TimeoutError, ...).
                # Falling back to the exception class name turns "Request failed: " into
                # "Request failed: ServerDisconnectedError" so operators can tell a
                # benign keepalive blip from a real error at a glance.
                error_msg = str(e) or repr(e) or type(e).__name__
                logger.error(
                    "Request failed (after %d attempts): %s: %s",
                    max_retries + 1, type(e).__name__, error_msg,
                )
                return {"error": error_msg, "transient": True}
            except Exception as e:
                # Non-transient (logic error, unexpected exception type) — surface
                # immediately with the same class-name fallback so empty-message
                # exceptions still produce a useful log line. No retry: retrying a
                # programming error or unknown failure mode just wastes a poll.
                error_msg = str(e) or repr(e) or type(e).__name__
                logger.error(f"Request failed: {type(e).__name__}: {error_msg}")
                if "too many 500" in error_msg.lower() or "ResponseError" in str(type(e)):
                    logger.warning("⚠️  Multiple 500 errors received. This might indicate:")
                    logger.warning("   1. Server is temporarily unavailable")
                    logger.warning("   2. Token may have expired (try refreshing)")
                    logger.warning("   3. Account settings issue (check 'Auto OCO Brackets' setting)")
                    return {"error": error_msg, "retry_exhausted": True, "status_code": 500}
                return {"error": error_msg}
        # Defensive fallback: for-loop completed without return (shouldn't happen because
        # success returns from inside the try and failure returns from the except blocks).
        # Surface a clear diagnostic if we ever land here.
        if last_transient_exc is not None:
            return {"error": type(last_transient_exc).__name__, "transient": True}
        return {"error": "internal: _make_request retry loop exited unexpectedly"}
    
    async def authenticate(self) -> bool:
        """
        Authenticate with the TopStepX API using username and API key.
        
        Returns:
            True if authentication successful, False otherwise
        """
        if not self.api_key or not self.username:
            logger.error("API key and username are required")
            return False
        
        try:
            logger.info("Authenticating with TopStepX API...")
            
            # Prepare login data (TopStepX uses userName and apiKey)
            login_data = {
                "userName": self.username,
                "apiKey": self.api_key
            }
            
            # Set headers for login request
            headers = {
                "accept": "text/plain",
                "Content-Type": "application/json"
            }
            
            # Make login request
            response = await self._make_request("POST", "/api/Auth/loginKey", data=login_data, headers=headers)
            
            # Check for actual errors (not just presence of "error" key with None/empty value)
            # Only treat as error if error key exists AND has a truthy, non-None value
            if isinstance(response, dict):
                error_value = response.get("error")
                # Only log error if it's a real error (not None, not empty string)
                if error_value is not None and error_value:
                    logger.error(f"Authentication failed: {error_value}")
                    return False
            
            # Check if login was successful
            # TopStepX API might return token directly as string, or in a "token" field
            token = None
            if isinstance(response, str):
                # Response is a token string directly
                token = response
            elif isinstance(response, dict):
                # Response is a dict - check for token field
                token = response.get("token")
                # Also check if response itself is the token (some APIs return just the token)
                if not token and len(response) == 1 and "token" not in response:
                    # Might be a different structure
                    pass
            
            if token:
                self.session_token = token if isinstance(token, str) else str(token)
            else:
                # No token found - check if there's an error message
                error_msg = None
                if isinstance(response, dict):
                    # Check for error messages, but skip if value is None or empty string
                    error_msg = response.get("errorMessage") or response.get("message")
                    # Only use "error" field if it's a real error (not None, not empty, not string "None")
                    error_field = response.get("error")
                    if error_field and error_field != "None" and str(error_field).strip():
                        error_msg = error_field
                
                if error_msg and str(error_msg).strip() and str(error_msg) != "None":
                    logger.error(f"Authentication failed: {error_msg}")
                else:
                    logger.error("Authentication failed: No token received from API")
                return False
                
            # Parse JWT to extract expiration time
            try:
                if HAS_JWT and jwt:
                    # Decode without verification (we trust the server's token)
                    decoded = jwt.decode(self.session_token, options={"verify_signature": False})
                    exp_timestamp = decoded.get("exp")
                    if exp_timestamp:
                        self.token_expiry = datetime.fromtimestamp(exp_timestamp, tz=timezone.utc)
                        logger.info(f"Token expires at: {self.token_expiry}")
                    else:
                        # Default to 30 minutes if no expiry in token
                        self.token_expiry = datetime.now(timezone.utc) + timedelta(minutes=30)
                        logger.warning("No expiry in JWT, assuming 30 minute lifetime")
                else:
                    # Fall back to base64 decoding if PyJWT not available
                    raise ImportError("PyJWT not available")
            except (ImportError, AttributeError):
                # If PyJWT not installed, fall back to base64 decoding
                try:
                    # JWT format: header.payload.signature
                    parts = self.session_token.split('.')
                    if len(parts) >= 2:
                        # Decode payload (add padding if needed)
                        payload = parts[1]
                        payload += '=' * (4 - len(payload) % 4)
                        decoded = json_fast_loads(base64.urlsafe_b64decode(payload))
                        exp_timestamp = decoded.get("exp")
                        if exp_timestamp:
                            self.token_expiry = datetime.fromtimestamp(exp_timestamp, tz=timezone.utc)
                            logger.info(f"Token expires at: {self.token_expiry}")
                        else:
                            self.token_expiry = datetime.now(timezone.utc) + timedelta(minutes=30)
                    else:
                        self.token_expiry = datetime.now(timezone.utc) + timedelta(minutes=30)
                except Exception as parse_err:
                    # If parsing fails, assume 30 minute lifetime
                    self.token_expiry = datetime.now(timezone.utc) + timedelta(minutes=30)
                    logger.warning(f"Failed to parse token expiry: {parse_err}, assuming 30 minute lifetime")
            except Exception as decode_err:
                # If decoding fails, assume 30 minute lifetime
                self.token_expiry = datetime.now(timezone.utc) + timedelta(minutes=30)
                logger.warning(f"Failed to decode token: {decode_err}, assuming 30 minute lifetime")
            
            # If we got here, token was successfully set and parsed
            logger.info(f"Successfully authenticated as: {self.username}")
            logger.info(f"Session token obtained: {self.session_token[:20]}...")
            return True
            
        except Exception as e:
            logger.error(f"Authentication failed: {type(e).__name__}: {str(e) or repr(e)}")
            return False
    
    async def ensure_valid_token(self, force_refresh: bool = False) -> bool:
        """
        Ensure we have a valid token, refreshing if necessary.
        
        Args:
            force_refresh: If True, force token refresh even if token appears valid
        
        Returns:
            True if token is valid
            
        Raises:
            AuthenticationError: If token refresh fails
        """
        if not force_refresh and not self._is_token_expired():
            return True
        
        if force_refresh:
            logger.info("Force refreshing token...")
        else:
            logger.info("Token expired or missing, authenticating...")
        return await self.authenticate()
    
    def get_token(self) -> Optional[str]:
        """
        Get current session token.
        
        Returns:
            Session token or None if not authenticated
        """
        return self.session_token
    
    def get_auth_headers(self) -> dict:
        """
        Get authentication headers for API requests.
        
        Returns:
            Dictionary with Authorization header
        """
        if not self.session_token:
            return {}
        return {"Authorization": f"Bearer {self.session_token}"}
    
    async def list_accounts(self) -> List[Dict[str, Any]]:
        """
        List all active accounts for the authenticated user.
        
        Returns:
            List of account information dictionaries
        """
        try:
            # Ensure valid token before making request
            await self.ensure_valid_token()

            # 2026-05-29: demoted from INFO → DEBUG.
            # ``list_accounts()`` is called by the broker keep-alive heartbeat
            # every 120s as a cheap "is the session still alive?" probe. Logging
            # it at INFO produced ~30 lines/hour of noise in the file with no
            # operator value — the heartbeat success is already implied by the
            # absence of error logs and the bot continuing to trade. Real
            # account-fetch contexts (startup, account switch) get their own
            # INFO logs from the caller ("Found N active accounts", etc.).
            logger.debug("Fetching active accounts from TopStepX API...")

            if not self.session_token:
                logger.error("No session token available. Please authenticate first.")
                return []
            
            # Make real API call to get accounts using session token
            headers = {
                "accept": "text/plain",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.session_token}"
            }
            
            # Search for active accounts
            search_data = {
                "onlyActiveAccounts": True
            }
            
            response = await self._make_request("POST", "/api/Account/search", data=search_data, headers=headers)
            
            # If we get 401/403, try refreshing token and retry once
            if response.get("status_code") in (401, 403):
                logger.info("Token expired during request, refreshing...")
                if await self.authenticate():
                    # Retry the request with new token
                    headers["Authorization"] = f"Bearer {self.session_token}"
                    response = await self._make_request("POST", "/api/Account/search", data=search_data, headers=headers)
            
            if "error" in response:
                logger.error(f"Failed to fetch accounts: {response['error']}")
                return []
            
            # Parse the response - adjust based on actual API response structure
            if isinstance(response, list):
                accounts = response
            elif isinstance(response, dict) and "accounts" in response:
                accounts = response["accounts"]
            elif isinstance(response, dict) and "data" in response:
                accounts = response["data"]
            elif isinstance(response, dict) and "result" in response:
                accounts = response["result"]
            else:
                logger.warning(f"Unexpected API response format: {response}")
                accounts = []
            
            # Normalize account data structure
            normalized_accounts = []
            for account in accounts:
                # Determine account type from name or other fields
                account_name = account.get("name") or account.get("accountName", "Unknown Account")
                account_type = "unknown"
                
                if "PRAC" in account_name.upper():
                    account_type = "practice"
                elif "50KTC" in account_name.upper() or "100KTC" in account_name.upper() or "150KTC" in account_name.upper():
                    account_type = "eval"
                elif "EXPRESS" in account_name.upper():
                    account_type = "funded"
                elif "EVAL" in account_name.upper():
                    account_type = "evaluation"
                
                normalized_account = {
                    "id": account.get("id") or account.get("accountId"),
                    "name": account_name,
                    "status": account.get("status", "active"),
                    "balance": account.get("balance", 0.0),
                    "currency": account.get("currency", "USD"),
                    "account_type": account_type
                }
                normalized_accounts.append(normalized_account)
            
            logger.debug(f"Found {len(normalized_accounts)} active accounts")  # Reduced to DEBUG
            return normalized_accounts
            
        except Exception as e:
            logger.error(f"Failed to fetch accounts: {type(e).__name__}: {str(e) or repr(e)}")
            return []

