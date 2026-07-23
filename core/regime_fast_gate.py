"""Fast reactive regime gate — prop-DLL-aligned session halt + short rolling throttle.

Designed for typical **$1k daily loss limit** prop accounts (``REGIME_FAST_DLL_USD``).
Acts on **days and weeks**, not 20-session replay percentiles (see ``regime_kpi_gate``).

Default policy (opt-in ``REGIME_FAST_GATE_ENABLED=1``):

1. **Session halt** — no further entries same ET day when strategy session PnL
   ≤ ``-session_halt_usd`` (default 40% of DLL → **−$400** on $1k DLL).
2. **Rolling session throttle** — last ``rolling_window`` (default **3**) completed
   sessions have mean PnL **< rolling_mean_max** (default **0**) → **0.5×** size for
   the next ``throttle_duration`` (default **5**) sessions.  Use e.g. ``-150`` to
   require deeper losing patches before throttling.
3. **Weekly MGC throttle** — prior ISO week MGC PnL ≤ ``-weekly_mgc_loss_usd``
   (default **30% of DLL → −$300**) → **0.5×** on MGC only this week.

Wiring: ``StrategyBase.record_trade_outcome`` + ``place_bracket_order`` via
``trading_bot.regime_fast_entry_quantity``.  Replay:
``scripts/sim_regime_fast_gate.py``.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_DEFAULT_STATE_DIR = Path("data")


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().strip('"').strip("'").lower() in ("1", "true", "yes", "on")


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return float(str(raw).strip().strip('"').strip("'"))
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return int(float(str(raw).strip().strip('"').strip("'")))
    except ValueError:
        return default


def rolling_mean_max_from_env() -> float:
    return _env_float("REGIME_FAST_ROLLING_MEAN_MAX", -300.0)


def regime_fast_gate_enabled() -> bool:
    return _env_flag("REGIME_FAST_GATE_ENABLED", False)


def _load_tz(name: str = "America/New_York"):
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(name)
    except Exception:
        return timezone.utc


def session_halt_usd_from_env() -> float:
    """Absolute session halt; falls back to pct × DLL when unset/zero."""
    explicit = _env_float("REGIME_FAST_SESSION_HALT_USD", 0.0)
    if explicit > 0:
        return explicit
    dll = _env_float("REGIME_FAST_DLL_USD", 1000.0)
    pct = _env_float("REGIME_FAST_SESSION_HALT_PCT", 0.50)
    return max(1.0, dll * pct)


def weekly_mgc_loss_usd_from_env() -> float:
    explicit = _env_float("REGIME_FAST_WEEKLY_MGC_LOSS_USD", 0.0)
    if explicit > 0:
        return explicit
    dll = _env_float("REGIME_FAST_DLL_USD", 1000.0)
    pct = _env_float("REGIME_FAST_WEEKLY_MGC_LOSS_PCT", 0.30)
    return max(1.0, dll * pct)


@dataclass
class SessionSummary:
    session_date: str
    net_pnl: float = 0.0
    trade_count: int = 0


@dataclass
class StrategyFastState:
    current_session_date: str = ""
    current: SessionSummary = field(default_factory=lambda: SessionSummary(session_date=""))
    completed: List[SessionSummary] = field(default_factory=list)
    session_halt_today: bool = False
    throttle_sessions_left: int = 0
    mgc_week_throttle: bool = False
    current_iso_week: str = ""
    current_week_mgc_pnl: float = 0.0
    prior_week_mgc_pnl: float = 0.0


@dataclass
class RegimeFastGateState:
    account_id: str = ""
    strategies: Dict[str, StrategyFastState] = field(default_factory=dict)
    last_updated_utc: str = ""


class RegimeFastGate:
    """Per-account fast reactive throttle + session halt."""

    def __init__(
        self,
        account_id: str,
        *,
        state_dir: Path = _DEFAULT_STATE_DIR,
        dll_usd: Optional[float] = None,
        session_halt_usd: Optional[float] = None,
        rolling_window: Optional[int] = None,
        rolling_warmup: Optional[int] = None,
        throttle_mult: Optional[float] = None,
        throttle_duration: Optional[int] = None,
        weekly_mgc_loss_usd: Optional[float] = None,
        rolling_mean_max: Optional[float] = None,
        session_halt_enabled: Optional[bool] = None,
        weekly_mgc_enabled: Optional[bool] = None,
    ):
        self.account_id = str(account_id)
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.dll_usd = dll_usd if dll_usd is not None else _env_float("REGIME_FAST_DLL_USD", 1000.0)
        self.session_halt_usd = (
            session_halt_usd if session_halt_usd is not None else session_halt_usd_from_env()
        )
        self.rolling_window = rolling_window or _env_int("REGIME_FAST_ROLLING_WINDOW", 3)
        self.rolling_warmup = rolling_warmup or _env_int("REGIME_FAST_ROLLING_WARMUP", 3)
        self.throttle_mult = throttle_mult if throttle_mult is not None else _env_float(
            "REGIME_FAST_THROTTLE_MULT", 0.5,
        )
        self.throttle_duration = throttle_duration or _env_int(
            "REGIME_FAST_THROTTLE_DURATION", 3,
        )
        self.weekly_mgc_loss_usd = (
            weekly_mgc_loss_usd if weekly_mgc_loss_usd is not None else weekly_mgc_loss_usd_from_env()
        )
        self.rolling_mean_max = (
            rolling_mean_max if rolling_mean_max is not None else rolling_mean_max_from_env()
        )
        self.session_halt_enabled = (
            session_halt_enabled
            if session_halt_enabled is not None
            else _env_flag("REGIME_FAST_SESSION_HALT_ENABLED", True)
        )
        self.weekly_mgc_enabled = (
            weekly_mgc_enabled
            if weekly_mgc_enabled is not None
            else _env_flag("REGIME_FAST_WEEKLY_MGC_ENABLED", False)
        )
        self.state = self._load_state()

    def _path(self) -> Path:
        return self.state_dir / f"regime_fast_{self.account_id}.json"

    def _load_state(self) -> RegimeFastGateState:
        path = self._path()
        if not path.is_file():
            return RegimeFastGateState(account_id=self.account_id)
        try:
            blob = json.loads(path.read_text(encoding="utf-8"))
            strategies: Dict[str, StrategyFastState] = {}
            for name, sval in (blob.get("strategies") or {}).items():
                if not isinstance(sval, dict):
                    continue
                completed = []
                for c in sval.get("completed") or []:
                    if isinstance(c, dict):
                        completed.append(
                            SessionSummary(
                                session_date=str(c.get("session_date") or ""),
                                net_pnl=float(c.get("net_pnl") or 0),
                                trade_count=int(c.get("trade_count") or 0),
                            )
                        )
                cur_raw = sval.get("current") or {}
                cur = SessionSummary(
                    session_date=str(cur_raw.get("session_date") or ""),
                    net_pnl=float(cur_raw.get("net_pnl") or 0),
                    trade_count=int(cur_raw.get("trade_count") or 0),
                )
                strategies[str(name)] = StrategyFastState(
                    current_session_date=str(sval.get("current_session_date") or cur.session_date),
                    current=cur,
                    completed=completed[-self.rolling_window * 4 :],
                    session_halt_today=bool(sval.get("session_halt_today")),
                    throttle_sessions_left=int(sval.get("throttle_sessions_left") or 0),
                    mgc_week_throttle=bool(sval.get("mgc_week_throttle")),
                    current_iso_week=str(sval.get("current_iso_week") or ""),
                    current_week_mgc_pnl=float(sval.get("current_week_mgc_pnl") or 0),
                    prior_week_mgc_pnl=float(sval.get("prior_week_mgc_pnl") or 0),
                )
            return RegimeFastGateState(
                account_id=str(blob.get("account_id") or self.account_id),
                strategies=strategies,
                last_updated_utc=str(blob.get("last_updated_utc") or ""),
            )
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            logger.exception("regime_fast_gate: load failed — fresh state")
            return RegimeFastGateState(account_id=self.account_id)

    def _persist(self) -> None:
        self.state.last_updated_utc = datetime.now(timezone.utc).isoformat()
        blob = {
            "account_id": self.account_id,
            "last_updated_utc": self.state.last_updated_utc,
            "strategies": {},
        }
        for name, st in self.state.strategies.items():
            blob["strategies"][name] = {
                "current_session_date": st.current_session_date,
                "current": asdict(st.current),
                "completed": [asdict(c) for c in st.completed[-self.rolling_window * 4 :]],
                "session_halt_today": st.session_halt_today,
                "throttle_sessions_left": st.throttle_sessions_left,
                "mgc_week_throttle": st.mgc_week_throttle,
                "current_iso_week": st.current_iso_week,
                "current_week_mgc_pnl": st.current_week_mgc_pnl,
                "prior_week_mgc_pnl": st.prior_week_mgc_pnl,
            }
        path = self._path()
        tmp = path.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(blob, indent=2), encoding="utf-8")
            tmp.replace(path)
        except OSError:
            logger.exception("regime_fast_gate: persist failed")

    def _strategy_state(self, strategy: str) -> StrategyFastState:
        name = str(strategy or "").strip()
        if name not in self.state.strategies:
            self.state.strategies[name] = StrategyFastState()
        return self.state.strategies[name]

    @staticmethod
    def _iso_week(ts: datetime) -> str:
        et = ts.astimezone(_load_tz())
        iso = et.isocalendar()
        return f"{iso.year}-W{iso.week:02d}"

    def _finalize_session(self, st: StrategyFastState) -> None:
        if st.current.trade_count <= 0:
            return
        st.completed.append(st.current)
        keep = max(self.rolling_window * 4, 12)
        if len(st.completed) > keep:
            st.completed = st.completed[-keep:]
        if st.throttle_sessions_left > 0:
            st.throttle_sessions_left -= 1
        window = st.completed[-self.rolling_window :]
        if len(window) >= self.rolling_warmup:
            mean_sess = sum(s.net_pnl for s in window) / len(window)
            if mean_sess < self.rolling_mean_max:
                st.throttle_sessions_left = max(
                    st.throttle_sessions_left,
                    self.throttle_duration,
                )

    def _roll_week(self, st: StrategyFastState, ts: datetime, symbol: str) -> None:
        week = self._iso_week(ts)
        if not st.current_iso_week:
            st.current_iso_week = week
            return
        if week == st.current_iso_week:
            return
        st.prior_week_mgc_pnl = st.current_week_mgc_pnl
        st.current_week_mgc_pnl = 0.0
        st.current_iso_week = week
        st.mgc_week_throttle = (
            self.weekly_mgc_enabled
            and st.prior_week_mgc_pnl <= -abs(self.weekly_mgc_loss_usd)
        )

    def record_trade(
        self,
        strategy: str,
        symbol: str,
        pnl: float,
        *,
        exit_time: Optional[datetime] = None,
    ) -> None:
        if not regime_fast_gate_enabled():
            return
        strat = str(strategy or "").strip()
        if not strat:
            return
        ts = exit_time or datetime.now(timezone.utc)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        session_d = ts.astimezone(_load_tz()).date().isoformat()
        sym = str(symbol or "").upper()

        st = self._strategy_state(strat)
        self._roll_week(st, ts, sym)

        if st.current_session_date and session_d != st.current_session_date:
            self._finalize_session(st)
            st.current = SessionSummary(session_date=session_d)
            st.session_halt_today = False
        elif not st.current_session_date:
            st.current = SessionSummary(session_date=session_d)

        st.current_session_date = session_d
        if st.current.session_date != session_d:
            st.current = SessionSummary(session_date=session_d)
            st.session_halt_today = False

        st.current.net_pnl += float(pnl)
        st.current.trade_count += 1
        if sym == "MGC":
            st.current_week_mgc_pnl += float(pnl)

        if (
            self.session_halt_enabled
            and st.current.net_pnl <= -abs(self.session_halt_usd)
        ):
            st.session_halt_today = True

        self._persist()

    def resolve_multiplier(self, strategy: str, symbol: str) -> Tuple[float, str]:
        if not regime_fast_gate_enabled():
            return 1.0, "disabled"

        strat = str(strategy or "").strip()
        sym = str(symbol or "").upper()
        st = self._strategy_state(strat)

        if st.session_halt_today:
            return 0.0, f"session halt (day PnL ≤ −${self.session_halt_usd:.0f})"

        mult = 1.0
        parts: List[str] = []

        if st.throttle_sessions_left > 0:
            mult = min(mult, self.throttle_mult)
            parts.append(
                f"roll{self.rolling_window}<{self.rolling_mean_max:.0f} "
                f"({st.throttle_sessions_left} sess left)"
            )

        if sym == "MGC" and st.mgc_week_throttle:
            mult = min(mult, self.throttle_mult)
            parts.append(
                f"mgc week throttle (prior wk ${st.prior_week_mgc_pnl:.0f})"
            )

        if mult >= 0.999:
            return 1.0, "ok"
        if mult <= 0:
            return 0.0, " ".join(parts)
        return mult, " ".join(parts)

    def apply_quantity(self, strategy: str, symbol: str, requested: int) -> Tuple[int, str]:
        try:
            req = max(0, int(requested))
        except (TypeError, ValueError):
            return 0, "invalid qty"

        mult, reason = self.resolve_multiplier(strategy, symbol)
        if mult <= 0:
            return 0, f"blocked ({reason})"
        if mult >= 0.999:
            return req, reason

        adj = max(0, int(req * mult))
        if adj < 1 and req >= 1 and mult > 0:
            adj = 1
        return adj, f"{reason} → {req}×{mult:.2f}={adj}"


def maybe_create_regime_fast_gate(account_id: str) -> Optional[RegimeFastGate]:
    if not regime_fast_gate_enabled():
        return None
    if not account_id:
        return None
    return RegimeFastGate(str(account_id))


def regime_fast_account_id_from_bot(bot: Any) -> Optional[str]:
    sel = getattr(bot, "selected_account", None)
    if isinstance(sel, dict):
        aid = sel.get("id")
        return str(aid) if aid is not None else None
    if sel is not None:
        return str(sel)
    return None


def regime_fast_entry_quantity_for_bot(
    bot: Any,
    strategy_name: str,
    symbol: str,
    requested: int,
) -> int:
    if not regime_fast_gate_enabled():
        try:
            return max(0, int(requested))
        except (TypeError, ValueError):
            return 0

    gate = getattr(bot, "regime_fast_gate", None)
    if gate is None:
        aid = regime_fast_account_id_from_bot(bot)
        if not aid:
            return max(0, int(requested))
        gate = maybe_create_regime_fast_gate(aid)
        if gate is None:
            return max(0, int(requested))
        bot.regime_fast_gate = gate

    qty, reason = gate.apply_quantity(strategy_name, symbol, requested)
    if qty != requested:
        logger.info(
            "⚡ regime fast %s %s: %s",
            strategy_name,
            symbol,
            reason,
        )
    return qty


def record_trade_for_bot(
    bot: Any,
    strategy_name: str,
    symbol: str,
    pnl: float,
    *,
    exit_time: Optional[datetime] = None,
) -> None:
    if not regime_fast_gate_enabled():
        return
    gate = getattr(bot, "regime_fast_gate", None)
    if gate is None:
        aid = regime_fast_account_id_from_bot(bot)
        if not aid:
            return
        gate = maybe_create_regime_fast_gate(aid)
        if gate is None:
            return
        bot.regime_fast_gate = gate
    gate.record_trade(strategy_name, symbol, pnl, exit_time=exit_time)
