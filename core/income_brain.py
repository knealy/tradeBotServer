"""Income Brain — equity-tier sizing + daily target/stop on top of risk_management.

This module sits **between** the strategy and ``core/risk_management.py``: a
strategy still asks the risk manager whether a new entry is allowed, but the
*size* (number of contracts) and the *daily halt* decision come from this
brain instead of the strategy's static ``risk.position_size``.

Two cooperating pieces:

1. :class:`EquityTierSizer` — deterministic sizing rule
   ``N = clamp(min_n, max_n, floor((equity − cushion) / divisor))``.
   The defaults (`divisor=2500`, `cushion=2000`, `min_n=1`, `max_n=20`) match
   the conservative tier table in
   ``docs/alpha/morning_reversion_info.md``. A trailing high-water mark
   (HWM) damps re-entry sizing after a drawdown — when current equity drops
   more than ``hwm_breach_dollars`` below the recorded HWM, the sizer falls
   back to ``min_n`` until equity sets a new HWM.

2. :class:`DailySessionGoal` — once realized PnL on a session **reaches**
   ``daily_target_dollars`` (gain) or ``daily_stop_dollars`` (loss),
   :meth:`should_halt` returns ``True``. The strategy executor is expected
   to flatten and pause until the next ET session date when this fires.

Both pieces share a single :class:`IncomeBrain` that persists state to a
JSON file (``data/income_brain_<account>.json``) so a restart resumes the
HWM and the per-session realized-PnL counters cleanly.

Configuration is plain env-var-driven (no new TOML schema); strategies do
**not** import this module — use :func:`income_brain_entry_quantity_for_bot` from
``trading_bot`` (``income_brain_entry_quantity``), ``strategy_executor``, and
tooling. This keeps strategies free of direct env/infrastructure imports (AGENTS.md).

Wiring example (kept opt-in):

    INCOME_BRAIN=true \\
    INCOME_BRAIN_DIVISOR=2500 \\
    INCOME_BRAIN_CUSHION=2000 \\
    INCOME_BRAIN_DAILY_TARGET=400 \\
    INCOME_BRAIN_DAILY_STOP=-300 \\
    python core/strategy_executor.py --strategy=body_reversion ...

CLI helpers::

    python -m core.income_brain status --account 12694476
    python -m core.income_brain size --account 12694476 --equity 5400
    python -m core.income_brain reset --account 12694476  # archive + zero
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
from dataclasses import dataclass, asdict, field
from datetime import datetime, date, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


_DEFAULT_STATE_DIR = Path("data")


def _load_tz(name: str = "America/New_York"):
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(name)
    except Exception:
        try:
            import pytz

            return pytz.timezone(name)
        except Exception:
            return timezone.utc


def _today_session_date(tz_name: str = "America/New_York") -> str:
    tz = _load_tz(tz_name)
    return datetime.now(tz=timezone.utc).astimezone(tz).date().isoformat()


def _safe_float_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning("Invalid float for %s=%r — using default %s", name, raw, default)
        return default


def _safe_int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("Invalid int for %s=%r — using default %s", name, raw, default)
        return default


# -- Sizing ----------------------------------------------------------------


@dataclass
class EquityTierSizer:
    """Equity → contract count with HWM-aware drawdown brake.

    Args:
        divisor: dollars of equity per additional contract (default 2,500).
        cushion: dollars subtracted from equity before division (default 2,000).
        min_n: never size below this (default 1).
        max_n: hard cap regardless of equity (default 20).
        hwm_breach_dollars: when ``hwm − equity > hwm_breach_dollars``, fall
            back to ``min_n`` until a new HWM is set (default 2,000).
    """

    divisor: float = 2500.0
    cushion: float = 2000.0
    min_n: int = 1
    max_n: int = 20
    hwm_breach_dollars: float = 2000.0

    def size(self, equity: float, hwm: Optional[float] = None) -> int:
        """Return contracts to trade given current equity and trailing HWM."""
        if hwm is not None and hwm - equity > self.hwm_breach_dollars:
            return self.min_n
        if self.divisor <= 0:
            return self.min_n
        usable = max(0.0, equity - self.cushion)
        raw = int(usable // self.divisor)
        return max(self.min_n, min(self.max_n, raw))


# -- Daily target / stop ---------------------------------------------------


@dataclass
class DailySessionGoal:
    """Halt new trading once a daily realized-PnL target or stop is reached.

    ``daily_target_dollars`` and ``daily_stop_dollars`` are absolute USD
    amounts on a single ET session date. Pass ``0`` to disable either side.
    """

    daily_target_dollars: float = 0.0
    daily_stop_dollars: float = 0.0

    def should_halt(self, realized_pnl_today: float) -> Tuple[bool, str]:
        if self.daily_stop_dollars and realized_pnl_today <= self.daily_stop_dollars:
            return True, f"daily_stop_hit pnl={realized_pnl_today:.2f}"
        if self.daily_target_dollars and realized_pnl_today >= self.daily_target_dollars:
            return True, f"daily_target_hit pnl={realized_pnl_today:.2f}"
        return False, ""


# -- Brain (composition + persistence) -------------------------------------


@dataclass
class IncomeBrainState:
    """Persisted on-disk state."""

    account_id: str
    hwm: float = 0.0
    last_equity: float = 0.0
    session_date: str = ""
    realized_pnl_today: float = 0.0
    halted_today: bool = False
    halt_reason: str = ""
    last_updated_utc: str = ""
    history: Dict[str, float] = field(default_factory=dict)
    """Mapping of ``YYYY-MM-DD`` → realized PnL closed on that session date."""

    def to_json(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"), default=str)


@dataclass
class IncomeBrain:
    """Stateful coordinator: sizing + halt + persistence per account."""

    account_id: str
    sizer: EquityTierSizer = field(default_factory=EquityTierSizer)
    goal: DailySessionGoal = field(default_factory=DailySessionGoal)
    state_dir: Path = field(default_factory=lambda: _DEFAULT_STATE_DIR)
    session_tz: str = "America/New_York"
    state: IncomeBrainState = field(init=False)

    def __post_init__(self) -> None:
        self.state_dir = Path(self.state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.state = self._load_state()

    # -- persistence ------------------------------------------------------

    def _path(self) -> Path:
        return self.state_dir / f"income_brain_{self.account_id}.json"

    def _load_state(self) -> IncomeBrainState:
        path = self._path()
        if not path.is_file():
            return IncomeBrainState(account_id=self.account_id)
        try:
            with path.open("r", encoding="utf-8") as fh:
                blob = json.load(fh)
            history = blob.get("history") or {}
            return IncomeBrainState(
                account_id=str(blob.get("account_id") or self.account_id),
                hwm=float(blob.get("hwm") or 0.0),
                last_equity=float(blob.get("last_equity") or 0.0),
                session_date=str(blob.get("session_date") or ""),
                realized_pnl_today=float(blob.get("realized_pnl_today") or 0.0),
                halted_today=bool(blob.get("halted_today")),
                halt_reason=str(blob.get("halt_reason") or ""),
                last_updated_utc=str(blob.get("last_updated_utc") or ""),
                history={k: float(v) for k, v in history.items()} if isinstance(history, dict) else {},
            )
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            logger.exception("income_brain: could not load %s — starting fresh", path)
            return IncomeBrainState(account_id=self.account_id)

    def _persist(self) -> None:
        self.state.last_updated_utc = datetime.now(timezone.utc).isoformat()
        path = self._path()
        tmp = path.with_suffix(".tmp")
        try:
            with tmp.open("w", encoding="utf-8") as fh:
                fh.write(self.state.to_json())
            tmp.replace(path)
        except OSError:
            logger.exception("income_brain: could not persist state to %s", path)

    # -- session bookkeeping ---------------------------------------------

    def _ensure_session(self) -> None:
        today = _today_session_date(self.session_tz)
        if self.state.session_date != today:
            # roll: archive prior session, reset counters
            if self.state.session_date:
                self.state.history[self.state.session_date] = self.state.realized_pnl_today
            self.state.session_date = today
            self.state.realized_pnl_today = 0.0
            self.state.halted_today = False
            self.state.halt_reason = ""
            self._persist()

    def update_equity(self, equity: float) -> int:
        """Record a fresh equity reading, update HWM, and return current size N."""
        self._ensure_session()
        equity = float(equity)
        self.state.last_equity = equity
        if equity > self.state.hwm:
            self.state.hwm = equity
        self._persist()
        return self.sizer.size(equity, hwm=self.state.hwm)

    def record_realized_pnl(self, delta_pnl: float) -> Tuple[bool, str]:
        """Add ``delta_pnl`` to today's realized PnL; return ``(halt, reason)``."""
        self._ensure_session()
        self.state.realized_pnl_today += float(delta_pnl)
        halt, reason = self.goal.should_halt(self.state.realized_pnl_today)
        if halt and not self.state.halted_today:
            self.state.halted_today = True
            self.state.halt_reason = reason
            logger.warning(
                "income_brain: halting account %s — %s",
                self.account_id,
                reason,
            )
        self._persist()
        return self.state.halted_today, self.state.halt_reason

    def sync_session_pnl_from_broker(self, net_pnl_usd: float) -> Tuple[bool, str]:
        """Set today's realized PnL from broker-tracked session net PnL (source of truth).

        Called before sizing so daily target/stop reflect fills from all strategies,
        not only paths that invoked :meth:`record_realized_pnl`.
        """
        self._ensure_session()
        self.state.realized_pnl_today = float(net_pnl_usd)
        halt, reason = self.goal.should_halt(self.state.realized_pnl_today)
        if halt and not self.state.halted_today:
            self.state.halted_today = True
            self.state.halt_reason = reason
            logger.warning(
                "income_brain: halting account %s (broker PnL sync) — %s",
                self.account_id,
                reason,
            )
        self._persist()
        return self.state.halted_today, self.state.halt_reason or reason

    # -- decision API -----------------------------------------------------

    def decide_size(self, equity: Optional[float] = None) -> int:
        """Return contracts to trade now (respecting HWM brake + halts)."""
        self._ensure_session()
        if self.state.halted_today:
            return 0
        if equity is not None:
            return self.update_equity(equity)
        return self.sizer.size(self.state.last_equity, hwm=self.state.hwm)

    def should_halt_today(self) -> Tuple[bool, str]:
        self._ensure_session()
        if self.state.halted_today:
            return True, self.state.halt_reason
        return self.goal.should_halt(self.state.realized_pnl_today)

    def reset_for_new_session(self) -> None:
        """Force a session roll (useful from a CLI / cron)."""
        if self.state.session_date:
            self.state.history[self.state.session_date] = self.state.realized_pnl_today
        self.state.session_date = _today_session_date(self.session_tz)
        self.state.realized_pnl_today = 0.0
        self.state.halted_today = False
        self.state.halt_reason = ""
        self._persist()

    # -- snapshots --------------------------------------------------------

    def snapshot(self) -> Dict[str, Any]:
        self._ensure_session()
        return {
            "account_id": self.account_id,
            "session_date": self.state.session_date,
            "last_equity": self.state.last_equity,
            "hwm": self.state.hwm,
            "drawdown_from_hwm": max(0.0, self.state.hwm - self.state.last_equity),
            "realized_pnl_today": self.state.realized_pnl_today,
            "halted_today": self.state.halted_today,
            "halt_reason": self.state.halt_reason,
            "current_size_n": self.decide_size(),
            "sizer": asdict(self.sizer),
            "goal": asdict(self.goal),
            "history_sessions": len(self.state.history),
            "last_updated_utc": self.state.last_updated_utc,
        }


# -- factory from environment ---------------------------------------------


def build_from_env(account_id: str) -> IncomeBrain:
    """Construct an :class:`IncomeBrain` using ``INCOME_BRAIN_*`` env vars.

    All values are optional; defaults match the conservative tier doc. The
    factory does *not* read the env if ``INCOME_BRAIN`` is unset / falsy —
    callers should check the gate themselves and skip wiring if so.
    """
    sizer = EquityTierSizer(
        divisor=_safe_float_env("INCOME_BRAIN_DIVISOR", 2500.0),
        cushion=_safe_float_env("INCOME_BRAIN_CUSHION", 2000.0),
        min_n=_safe_int_env("INCOME_BRAIN_MIN_N", 1),
        max_n=_safe_int_env("INCOME_BRAIN_MAX_N", 20),
        hwm_breach_dollars=_safe_float_env("INCOME_BRAIN_HWM_BREACH", 2000.0),
    )
    goal = DailySessionGoal(
        daily_target_dollars=_safe_float_env("INCOME_BRAIN_DAILY_TARGET", 0.0),
        daily_stop_dollars=_safe_float_env("INCOME_BRAIN_DAILY_STOP", 0.0),
    )
    state_dir = Path(os.getenv("INCOME_BRAIN_STATE_DIR") or _DEFAULT_STATE_DIR)
    return IncomeBrain(
        account_id=str(account_id),
        sizer=sizer,
        goal=goal,
        state_dir=state_dir,
        session_tz=os.getenv("INCOME_BRAIN_TZ", "America/New_York"),
    )


def is_enabled() -> bool:
    """Return ``True`` when ``INCOME_BRAIN`` env var is truthy."""
    raw = os.getenv("INCOME_BRAIN", "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


# -- trading_bot wiring (unit-tested with a mock *bot*) --------------------


def income_brain_account_id_from_bot(bot: Any) -> Optional[str]:
    """Resolve account id string from ``bot.selected_account`` (dict-shaped)."""
    acct = getattr(bot, "selected_account", None)
    if not isinstance(acct, dict):
        return None
    raw = acct.get("id") or acct.get("accountId")
    if raw is None:
        return None
    s = str(raw).strip()
    return s or None


def income_brain_entry_quantity_for_bot(bot: Any, strategy_name: str, requested: int) -> int:
    """Clamp entry size when ``INCOME_BRAIN`` is enabled; else passthrough.

    Expects ``bot`` to expose ``selected_account``, optional ``account_tracker``
    with ``get_daily_pnl(account_id)``, and a mutable ``_income_brain_cache`` dict
    (``TopStepXTradingBot`` sets this in ``__init__``).
    """
    try:
        rq = max(0, int(requested))
    except (TypeError, ValueError):
        rq = 0
    if not is_enabled():
        return rq
    aid = income_brain_account_id_from_bot(bot)
    if not aid:
        return rq
    cache = getattr(bot, "_income_brain_cache", None)
    if cache is None:
        setattr(bot, "_income_brain_cache", {})
        cache = bot._income_brain_cache  # type: ignore[union-attr]
    if aid not in cache:
        cache[aid] = build_from_env(aid)
    brain = cache[aid]
    daily = 0.0
    tracker = getattr(bot, "account_tracker", None)
    if tracker is not None:
        try:
            daily = float(tracker.get_daily_pnl(aid) or 0.0)
        except Exception:
            daily = 0.0
    brain.sync_session_pnl_from_broker(daily)
    halt, reason = brain.should_halt_today()
    if halt:
        logger.warning(
            "income_brain: blocking %s entry for account %s — %s",
            strategy_name,
            aid,
            reason,
        )
        return 0
    bal = 0.0
    acct = getattr(bot, "selected_account", None)
    if isinstance(acct, dict):
        try:
            bal = float(acct.get("balance") or 0.0)
        except (TypeError, ValueError):
            bal = 0.0
    n = brain.update_equity(bal)
    return max(0, min(rq, int(n)))


# -- CLI ------------------------------------------------------------------


def _print_status(brain: IncomeBrain) -> None:
    snap = brain.snapshot()
    print(f"income_brain status — account {snap['account_id']}")
    print(f"  session_date:        {snap['session_date']}")
    print(f"  last_equity:         ${snap['last_equity']:,.2f}")
    print(f"  hwm:                 ${snap['hwm']:,.2f}")
    print(f"  drawdown_from_hwm:   ${snap['drawdown_from_hwm']:,.2f}")
    print(f"  realized_pnl_today:  ${snap['realized_pnl_today']:,.2f}")
    print(f"  halted_today:        {snap['halted_today']}  ({snap['halt_reason']})")
    print(f"  current_size_n:      {snap['current_size_n']}")
    print(
        f"  sizer:               divisor=${snap['sizer']['divisor']:.0f} "
        f"cushion=${snap['sizer']['cushion']:.0f} "
        f"min={snap['sizer']['min_n']} max={snap['sizer']['max_n']} "
        f"hwm_breach=${snap['sizer']['hwm_breach_dollars']:.0f}"
    )
    print(
        f"  goal:                target=${snap['goal']['daily_target_dollars']:.0f} "
        f"stop=${snap['goal']['daily_stop_dollars']:.0f}"
    )
    print(f"  history_sessions:    {snap['history_sessions']}")


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m core.income_brain",
        description="Equity-tier sizing + daily target/stop helpers.",
    )
    ap.add_argument("--account", required=True, help="Account ID for state file")
    ap.add_argument(
        "--state-dir",
        default=str(_DEFAULT_STATE_DIR),
        help="Directory holding income_brain_<account>.json",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="Print current state")
    s = sub.add_parser("size", help="Compute contracts for a hypothetical equity")
    s.add_argument("--equity", type=float, required=True)
    p = sub.add_parser("record-pnl", help="Add to today's realized PnL")
    p.add_argument("--delta", type=float, required=True)
    sub.add_parser("reset", help="Roll session and zero today's realized PnL")
    a = sub.add_parser("archive", help="Copy current state file with a timestamp suffix")
    a.add_argument("--label", default="manual", help="Suffix label (default: manual)")

    args = ap.parse_args(argv)
    state_dir = Path(args.state_dir)
    brain = IncomeBrain(account_id=str(args.account), state_dir=state_dir)
    # CLI honors env-driven defaults for sizer/goal:
    if is_enabled() or any(
        os.getenv(k)
        for k in (
            "INCOME_BRAIN_DIVISOR",
            "INCOME_BRAIN_CUSHION",
            "INCOME_BRAIN_DAILY_TARGET",
            "INCOME_BRAIN_DAILY_STOP",
        )
    ):
        env_brain = build_from_env(str(args.account))
        brain.sizer = env_brain.sizer
        brain.goal = env_brain.goal

    if args.cmd == "status":
        _print_status(brain)
        return 0
    if args.cmd == "size":
        n = brain.update_equity(args.equity)
        print(f"size at equity ${args.equity:,.2f}: N={n}")
        return 0
    if args.cmd == "record-pnl":
        halt, reason = brain.record_realized_pnl(args.delta)
        print(f"realized_pnl_today=${brain.state.realized_pnl_today:,.2f} halt={halt} {reason}")
        return 0
    if args.cmd == "reset":
        brain.reset_for_new_session()
        print(f"reset complete — session_date={brain.state.session_date}")
        return 0
    if args.cmd == "archive":
        path = brain._path()
        if path.is_file():
            ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            dest = path.with_name(f"{path.stem}.{args.label}.{ts}.json")
            shutil.copy(path, dest)
            print(f"archived: {dest}")
        else:
            print("no state file to archive")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
