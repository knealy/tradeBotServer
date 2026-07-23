"""Rolling live KPI gate — throttle size when recent sessions underperform replay.

Compares the last ``window_sessions`` (default 20) **completed** ET session
PnL summaries against replay percentiles in ``config/regime_kpi_baselines.json``
(built via ``scripts/gen_regime_kpi_baselines.py`` from walk-forward trades).

Endurance-first policy (default):

- Warmup: first ``min_sessions_warmup`` completed sessions → multiplier **1.0**
- Rolling mean session PnL **≥ replay p50** → **1.0**
- **< replay p25** (session mean or trade expectancy) → **0.5×**
- **< replay p10** → **0.25×** (or **block** when ``REGIME_KPI_HALT_BELOW_P10=1``)

Strategies record trades through ``StrategyBase.record_trade_outcome``; sizing
is applied in ``place_bracket_order`` via ``trading_bot.regime_kpi_entry_quantity``.

Opt-in: ``REGIME_KPI_GATE_ENABLED=1`` (off by default).
"""

from __future__ import annotations

import json
import logging
import os
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_DEFAULT_STATE_DIR = Path("data")
_DEFAULT_BASELINES = Path("config/regime_kpi_baselines.json")


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


def regime_kpi_gate_enabled() -> bool:
    return _env_flag("REGIME_KPI_GATE_ENABLED", False)


def _load_tz(name: str = "America/New_York"):
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(name)
    except Exception:
        return timezone.utc


@dataclass(frozen=True)
class PercentileBand:
    p10: float
    p25: float
    p50: float


@dataclass(frozen=True)
class KpiBaseline:
    session_pnl: PercentileBand
    expectancy: PercentileBand

    @classmethod
    def from_dict(cls, blob: Dict[str, Any]) -> "KpiBaseline":
        sp = blob.get("session_pnl") or {}
        ex = blob.get("expectancy") or {}
        return cls(
            session_pnl=PercentileBand(
                p10=float(sp.get("p10", 0)),
                p25=float(sp.get("p25", 0)),
                p50=float(sp.get("p50", 0)),
            ),
            expectancy=PercentileBand(
                p10=float(ex.get("p10", 0)),
                p25=float(ex.get("p25", 0)),
                p50=float(ex.get("p50", 0)),
            ),
        )


@dataclass
class SessionSummary:
    session_date: str
    net_pnl: float = 0.0
    trade_count: int = 0

    @property
    def expectancy(self) -> float:
        if self.trade_count <= 0:
            return 0.0
        return self.net_pnl / self.trade_count


@dataclass
class StrategyKpiState:
    current_session_date: str = ""
    current: SessionSummary = field(default_factory=lambda: SessionSummary(session_date=""))
    completed: List[SessionSummary] = field(default_factory=list)


@dataclass
class RegimeKpiGateState:
    account_id: str = ""
    strategies: Dict[str, StrategyKpiState] = field(default_factory=dict)
    last_updated_utc: str = ""


def load_baselines(path: Optional[Path] = None) -> Dict[str, KpiBaseline]:
    p = path or Path(os.getenv("REGIME_KPI_BASELINES_PATH", str(_DEFAULT_BASELINES)))
    if not p.is_file():
        logger.warning("regime_kpi_gate: baselines missing at %s", p)
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("regime_kpi_gate: could not load baselines: %s", exc)
        return {}
    out: Dict[str, KpiBaseline] = {}
    for key, val in raw.items():
        if key == "meta" or not isinstance(val, dict):
            continue
        try:
            out[str(key)] = KpiBaseline.from_dict(val)
        except (TypeError, ValueError):
            continue
    return out


def _baseline_key(strategy: str, symbol: str) -> Tuple[str, str]:
    sym = str(symbol or "").upper()
    strat = str(strategy or "").strip()
    specific = f"{strat}:{sym}" if sym else strat
    return specific, strat


class RegimeKpiGate:
    """Per-account rolling session KPI tracker + size multiplier."""

    def __init__(
        self,
        account_id: str,
        *,
        baselines: Optional[Dict[str, KpiBaseline]] = None,
        state_dir: Path = _DEFAULT_STATE_DIR,
        window_sessions: Optional[int] = None,
        min_sessions_warmup: Optional[int] = None,
        throttle_mult: Optional[float] = None,
        severe_mult: Optional[float] = None,
    ):
        self.account_id = str(account_id)
        self.baselines = baselines if baselines is not None else load_baselines()
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.window_sessions = window_sessions or _env_int("REGIME_KPI_WINDOW_SESSIONS", 20)
        self.min_sessions_warmup = min_sessions_warmup or _env_int("REGIME_KPI_MIN_SESSIONS", 5)
        self.throttle_mult = throttle_mult if throttle_mult is not None else _env_float(
            "REGIME_KPI_THROTTLE_MULT", 0.5,
        )
        self.severe_mult = severe_mult if severe_mult is not None else _env_float(
            "REGIME_KPI_SEVERE_MULT", 0.25,
        )
        self.halt_below_p10 = _env_flag("REGIME_KPI_HALT_BELOW_P10", False)
        self.state = self._load_state()

    def _path(self) -> Path:
        return self.state_dir / f"regime_kpi_{self.account_id}.json"

    def _load_state(self) -> RegimeKpiGateState:
        path = self._path()
        if not path.is_file():
            return RegimeKpiGateState(account_id=self.account_id)
        try:
            blob = json.loads(path.read_text(encoding="utf-8"))
            strategies: Dict[str, StrategyKpiState] = {}
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
                    session_date=str(cur_raw.get("session_date") or sval.get("current_session_date") or ""),
                    net_pnl=float(cur_raw.get("net_pnl") or 0),
                    trade_count=int(cur_raw.get("trade_count") or 0),
                )
                strategies[str(name)] = StrategyKpiState(
                    current_session_date=str(sval.get("current_session_date") or cur.session_date),
                    current=cur,
                    completed=completed[-self.window_sessions * 2 :],
                )
            return RegimeKpiGateState(
                account_id=str(blob.get("account_id") or self.account_id),
                strategies=strategies,
                last_updated_utc=str(blob.get("last_updated_utc") or ""),
            )
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            logger.exception("regime_kpi_gate: load failed — fresh state")
            return RegimeKpiGateState(account_id=self.account_id)

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
                "completed": [asdict(c) for c in st.completed[-self.window_sessions * 2 :]],
            }
        path = self._path()
        tmp = path.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(blob, indent=2), encoding="utf-8")
            tmp.replace(path)
        except OSError:
            logger.exception("regime_kpi_gate: persist failed")

    def _strategy_state(self, strategy: str) -> StrategyKpiState:
        name = str(strategy or "").strip()
        if name not in self.state.strategies:
            self.state.strategies[name] = StrategyKpiState()
        return self.state.strategies[name]

    def _finalize_session(self, st: StrategyKpiState) -> None:
        if st.current.trade_count <= 0:
            return
        st.completed.append(st.current)
        if len(st.completed) > self.window_sessions:
            st.completed = st.completed[-self.window_sessions :]

    def record_trade(
        self,
        strategy: str,
        symbol: str,
        pnl: float,
        *,
        exit_time: Optional[datetime] = None,
    ) -> None:
        """Ingest a closed trade (called from ``StrategyBase.record_trade_outcome``)."""
        if not regime_kpi_gate_enabled():
            return
        strat = str(strategy or "").strip()
        if not strat:
            return
        ts = exit_time or datetime.now(timezone.utc)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        session_d = ts.astimezone(_load_tz()).date().isoformat()

        st = self._strategy_state(strat)
        if st.current_session_date and session_d != st.current_session_date:
            self._finalize_session(st)
            st.current = SessionSummary(session_date=session_d)
        elif not st.current_session_date:
            st.current = SessionSummary(session_date=session_d)

        st.current_session_date = session_d
        if st.current.session_date != session_d:
            st.current = SessionSummary(session_date=session_d)
        st.current.net_pnl += float(pnl)
        st.current.trade_count += 1
        self._persist()

    def _rolling_metrics(self, strategy: str) -> Tuple[int, float, float]:
        st = self._strategy_state(strategy)
        window = list(st.completed)[-self.window_sessions :]
        n = len(window)
        if n == 0:
            return 0, 0.0, 0.0
        total_pnl = sum(s.net_pnl for s in window)
        total_trades = sum(s.trade_count for s in window)
        mean_session = total_pnl / n
        expectancy = total_pnl / total_trades if total_trades > 0 else 0.0
        return n, mean_session, expectancy

    def _lookup_baseline(self, strategy: str, symbol: str) -> Optional[KpiBaseline]:
        specific, strat = _baseline_key(strategy, symbol)
        if specific in self.baselines:
            return self.baselines[specific]
        return self.baselines.get(strat)

    def resolve_multiplier(self, strategy: str, symbol: str) -> Tuple[float, str]:
        """Return ``(multiplier, reason)`` for the next entry."""
        if not regime_kpi_gate_enabled():
            return 1.0, "disabled"

        baseline = self._lookup_baseline(strategy, symbol)
        if baseline is None:
            return 1.0, "no baseline"

        n, mean_sess, expectancy = self._rolling_metrics(strategy)
        if n < self.min_sessions_warmup:
            return 1.0, f"warmup {n}/{self.min_sessions_warmup}"

        mult = 1.0
        parts: List[str] = [f"roll{n} sess_mean={mean_sess:.0f} exp={expectancy:.0f}"]

        sp = baseline.session_pnl
        ex = baseline.expectancy

        if mean_sess >= sp.p50 and expectancy >= ex.p50:
            return 1.0, f"above p50 ({parts[0]})"

        if mean_sess < sp.p10 or expectancy < ex.p10:
            if self.halt_below_p10:
                return 0.0, f"halt below p10 ({parts[0]})"
            mult = min(mult, self.severe_mult)
            parts.append("below_p10")
        elif mean_sess < sp.p25 or expectancy < ex.p25:
            mult = min(mult, self.throttle_mult)
            parts.append("below_p25")

        if mult >= 0.999:
            return 1.0, f"ok ({parts[0]})"
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


def maybe_create_regime_kpi_gate(account_id: str) -> Optional[RegimeKpiGate]:
    if not regime_kpi_gate_enabled():
        return None
    if not account_id:
        return None
    return RegimeKpiGate(str(account_id))


def regime_kpi_account_id_from_bot(bot: Any) -> Optional[str]:
    sel = getattr(bot, "selected_account", None)
    if isinstance(sel, dict):
        aid = sel.get("id")
        return str(aid) if aid is not None else None
    if sel is not None:
        return str(sel)
    return None


def regime_kpi_entry_quantity_for_bot(
    bot: Any,
    strategy_name: str,
    symbol: str,
    requested: int,
) -> int:
    if not regime_kpi_gate_enabled():
        try:
            return max(0, int(requested))
        except (TypeError, ValueError):
            return 0

    gate = getattr(bot, "regime_kpi_gate", None)
    if gate is None:
        aid = regime_kpi_account_id_from_bot(bot)
        if not aid:
            return max(0, int(requested))
        gate = maybe_create_regime_kpi_gate(aid)
        if gate is None:
            return max(0, int(requested))
        bot.regime_kpi_gate = gate

    qty, reason = gate.apply_quantity(strategy_name, symbol, requested)
    if qty != requested:
        logger.info(
            "📉 regime KPI %s %s: %s",
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
    if not regime_kpi_gate_enabled():
        return
    gate = getattr(bot, "regime_kpi_gate", None)
    if gate is None:
        aid = regime_kpi_account_id_from_bot(bot)
        if not aid:
            return
        gate = maybe_create_regime_kpi_gate(aid)
        if gate is None:
            return
        bot.regime_kpi_gate = gate
    gate.record_trade(strategy_name, symbol, pnl, exit_time=exit_time)
