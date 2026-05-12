"""Live-vs-replay drift monitor.

Subscribes to ``EventType.ORDER_FILLED`` on the running bot's ``EventBus`` and
records every fill to an append-only JSONL log under
``logs/drift/<strategy>_<account>_<YYYYMMDD>.jsonl``. Each line captures the
information needed to reproduce the trade in replay terms: the bar timestamp
(rounded down to the nearest minute), the symbol, side, size, fill price, stop
price (if known from the bracket), and the strategy name (extracted from the
order ``customTag`` — see ``CLAUDE.md`` "Order tag format").

A separate offline command compares a live JSONL log to a backtest JSON
produced by ``core/backtest_executor.py --include-trades``. For each round
trip it computes the per-trade realized R difference

    drift_R = (live_R - replay_R)

and surfaces summary statistics (mean / median / p95 absolute drift, % of
trades with |drift_R| > 0.20). The 20% bar matches the documented "kill"
threshold in ``docs/perf/morning_range_reversion_validation.md`` and the
PRAC-shadow plan for ``body_reversion`` v3.1.

Wiring (kept opt-in so existing executors are unchanged):

    DRIFT_MONITOR=true python core/strategy_executor.py --strategy=body_reversion ...

The module imports nothing from a strategy hot path and is safe to import in
research-only contexts.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)


_TAG_RE = re.compile(
    r"^TB-(?P<role>[A-Za-z0-9_]+)-(?P<strategy>[A-Za-z0-9_]+)-(?P<suffix>.+)?$"
)


def _parse_strategy_from_tag(custom_tag: Any) -> Optional[str]:
    """Extract the strategy name embedded in an order ``customTag``.

    Format documented in ``CLAUDE.md``::

        TB-<role>-<strategy>-<YYMMDDHH...>

    Returns ``None`` for tags that do not match (manual orders, GUI fills).
    """
    if not isinstance(custom_tag, str) or not custom_tag.startswith("TB-"):
        return None
    m = _TAG_RE.match(custom_tag.strip())
    if not m:
        return None
    return m.group("strategy") or None


def _parse_iso_utc(value: Any) -> Optional[datetime]:
    """Parse an ISO 8601 timestamp into a tz-aware UTC datetime."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not isinstance(value, str):
        return None
    s = value.strip()
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _floor_to_minute(dt: datetime) -> datetime:
    return dt.replace(second=0, microsecond=0)


def _normalize_side(value: Any) -> Optional[str]:
    """Normalize a TopStepX side field into ``"BUY"`` / ``"SELL"``.

    The user hub uses ``0 = BUY`` / ``1 = SELL``; webhook payloads sometimes
    surface the string form. Anything else returns ``None``.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        if int(value) == 0:
            return "BUY"
        if int(value) == 1:
            return "SELL"
        return None
    s = str(value).strip().upper()
    if s in {"BUY", "LONG", "0"}:
        return "BUY"
    if s in {"SELL", "SHORT", "1"}:
        return "SELL"
    return None


def _normalize_symbol(order: Dict[str, Any]) -> Optional[str]:
    """Pull a short symbol (``MNQ``) from the order's contract / symbol fields.

    Mirrors ``gui/chart_html.extract_symbol_from_contract``:
    ``CON.F.US.MNQ.Z25`` → ``MNQ`` (parts[-2]); ``F.US.MNQ`` → ``MNQ`` (parts[-1]);
    ``MNQ.M26`` → ``MNQ`` (parts[0]); plain ``MNQ`` → ``MNQ``.
    """
    raw = (
        order.get("symbol")
        or order.get("symbolId")
        or order.get("contractId")
        or order.get("contract")
    )
    if raw is None:
        return None
    s = str(raw).strip().rstrip(".").upper()
    if not s:
        return None
    parts = s.split(".") if "." in s else [s]
    if len(parts) >= 4:
        candidate = parts[-2]
        if candidate.isalpha():
            return candidate
    if len(parts) >= 2:
        # Try last alphabetic, then first alphabetic.
        for cand in (parts[-1], parts[0]):
            if cand.isalpha():
                return cand
    return parts[0] if parts[0].isalpha() else None


@dataclass
class FillRecord:
    """Single line in the live drift JSONL."""

    timestamp_utc: str
    bar_minute_utc: str
    strategy: Optional[str]
    symbol: Optional[str]
    side: Optional[str]
    size: int
    fill_price: Optional[float]
    stop_price: Optional[float]
    limit_price: Optional[float]
    role: Optional[str]
    order_id: Optional[str]
    custom_tag: Optional[str]
    account_id: Optional[str]

    def to_jsonl(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"))


def _safe_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def fill_record_from_event(
    order: Dict[str, Any],
    *,
    account_id: Optional[str] = None,
    now: Optional[datetime] = None,
) -> Optional[FillRecord]:
    """Build a ``FillRecord`` from a TopStepX user-hub order payload.

    Returns ``None`` if the payload does not look like a fill (no fill price,
    no fill volume, no recognizable side).
    """
    fill_price = _safe_float(order.get("filledPrice"))
    if fill_price is None:
        # Some payloads put the price under ``avgFillPrice`` / ``price``.
        fill_price = _safe_float(order.get("avgFillPrice")) or _safe_float(order.get("price"))
    if fill_price is None:
        return None

    side = _normalize_side(order.get("side"))
    if side is None:
        return None

    # Determine size: prefer ``fillVolume`` (cumulative), fall back to ``size``.
    size = _safe_int(order.get("fillVolume")) or _safe_int(order.get("size"))
    if size <= 0:
        return None

    custom_tag = order.get("customTag")
    strategy = _parse_strategy_from_tag(custom_tag)
    role: Optional[str] = None
    if isinstance(custom_tag, str):
        m = _TAG_RE.match(custom_tag.strip())
        if m:
            role = m.group("role")

    ts_raw = (
        order.get("filledAt")
        or order.get("fillTime")
        or order.get("updateTimestamp")
        or order.get("creationTimestamp")
    )
    ts = _parse_iso_utc(ts_raw) or (now or datetime.now(timezone.utc))
    return FillRecord(
        timestamp_utc=ts.isoformat(),
        bar_minute_utc=_floor_to_minute(ts).isoformat(),
        strategy=strategy,
        symbol=_normalize_symbol(order),
        side=side,
        size=size,
        fill_price=fill_price,
        stop_price=_safe_float(order.get("stopPrice")),
        limit_price=_safe_float(order.get("limitPrice")),
        role=role,
        order_id=str(order.get("id")) if order.get("id") is not None else None,
        custom_tag=custom_tag if isinstance(custom_tag, str) else None,
        account_id=str(account_id) if account_id is not None else None,
    )


class DriftMonitor:
    """Subscribe to ``ORDER_FILLED`` and append each fill to a JSONL log.

    The monitor is intentionally **passive** — it never sends events or
    talks to the broker. All comparison logic lives in :func:`compare_logs`
    and runs offline.
    """

    def __init__(
        self,
        log_dir: str | Path = "logs/drift",
        *,
        clock: Optional[Any] = None,
    ) -> None:
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._bus = None
        self._subscribed = False
        self._records_logged = 0

    # -- event-bus integration -------------------------------------------------

    async def attach(self, event_bus: Any) -> None:
        """Subscribe to the running bot's ``EventBus``.

        Safe to call multiple times — extra subscribes are skipped.
        """
        if self._subscribed:
            return
        from core.events import EventType  # local import — keeps research path clean

        event_bus.subscribe(EventType.ORDER_FILLED, self._on_order_filled)
        self._bus = event_bus
        self._subscribed = True
        logger.info("Drift monitor attached: log_dir=%s", self.log_dir)

    async def detach(self) -> None:
        if not self._subscribed or self._bus is None:
            return
        try:
            from core.events import EventType

            self._bus.unsubscribe(EventType.ORDER_FILLED, self._on_order_filled)
        except Exception as exc:
            logger.debug("Drift monitor detach error: %s", exc)
        self._subscribed = False
        self._bus = None

    async def _on_order_filled(self, event: Any) -> None:
        try:
            data = getattr(event, "data", None) or {}
            order = data.get("order") or {}
            account_id = data.get("account_id")
            rec = fill_record_from_event(order, account_id=account_id, now=self._clock())
            if rec is None:
                return
            self._write(rec)
        except Exception:
            logger.exception("Drift monitor: failed to handle ORDER_FILLED")

    # -- file IO --------------------------------------------------------------

    def _path_for(self, rec: FillRecord) -> Path:
        ts = _parse_iso_utc(rec.timestamp_utc) or self._clock()
        date_str = ts.strftime("%Y%m%d")
        strategy = rec.strategy or "unknown"
        account = rec.account_id or "unknown"
        return self.log_dir / f"{strategy}_{account}_{date_str}.jsonl"

    def _write(self, rec: FillRecord) -> None:
        path = self._path_for(rec)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(rec.to_jsonl())
            fh.write("\n")
        self._records_logged += 1
        logger.debug(
            "drift fill logged: strategy=%s symbol=%s side=%s price=%s tag=%s",
            rec.strategy,
            rec.symbol,
            rec.side,
            rec.fill_price,
            rec.custom_tag,
        )

    @property
    def records_logged(self) -> int:
        return self._records_logged


# -- offline comparison ------------------------------------------------------


@dataclass
class TradePair:
    """One live round-trip matched against a replay round-trip."""

    symbol: str
    side: str
    live_entry_ts: str
    replay_entry_ts: str
    live_pnl: float
    replay_pnl: float
    live_R: Optional[float]
    replay_R: Optional[float]
    drift_R: Optional[float]


@dataclass
class DriftSummary:
    """Aggregate statistics for a comparison run."""

    n_live: int
    n_replay: int
    n_matched: int
    n_unmatched_live: int
    n_unmatched_replay: int
    mean_drift_R: float = 0.0
    median_drift_R: float = 0.0
    p95_abs_drift_R: float = 0.0
    pct_breach_20: float = 0.0
    pairs: List[TradePair] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        return d


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if not path.is_file():
        return out
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def _live_round_trips(fills: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Pair each entry-tagged fill with the next opposite-side fill on same symbol.

    A "round trip" is the simplest aggregation that doesn't require knowledge
    of the bracket structure: open with first fill, close with the next
    opposite-side fill on the same symbol that fully or partially offsets.
    """
    by_symbol: Dict[str, List[Dict[str, Any]]] = {}
    for f in fills:
        sym = f.get("symbol")
        if not sym or not f.get("side") or f.get("fill_price") is None:
            continue
        by_symbol.setdefault(sym, []).append(f)

    trips: List[Dict[str, Any]] = []
    for sym, group in by_symbol.items():
        group.sort(key=lambda r: r.get("timestamp_utc") or "")
        position_side: Optional[str] = None
        entry: Optional[Dict[str, Any]] = None
        for f in group:
            side = f["side"]
            if entry is None:
                entry = f
                position_side = side
                continue
            if side != position_side:
                exit_ = f
                size = min(int(entry.get("size", 0)), int(exit_.get("size", 0))) or 1
                ep = float(entry["fill_price"])
                xp = float(exit_["fill_price"])
                direction = 1 if position_side == "BUY" else -1
                pnl_pts = (xp - ep) * direction
                stop = entry.get("stop_price")
                R = None
                if stop is not None and ep is not None:
                    risk_pts = abs(ep - float(stop))
                    if risk_pts > 0:
                        R = pnl_pts / risk_pts
                trips.append(
                    {
                        "symbol": sym,
                        "side": position_side,
                        "entry_ts": entry["timestamp_utc"],
                        "exit_ts": exit_["timestamp_utc"],
                        "entry_price": ep,
                        "exit_price": xp,
                        "size": size,
                        "pnl_points": pnl_pts,
                        "R": R,
                    }
                )
                entry = None
                position_side = None
        # Anything left open is dropped (no exit yet).
    return trips


def _replay_round_trips(replay_json: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract round trips from a ``backtest_executor --include-trades`` JSON."""
    result = replay_json.get("result") if isinstance(replay_json, dict) else None
    if not isinstance(result, dict):
        result = replay_json or {}
    trades = result.get("trades") or replay_json.get("trades") or replay_json.get("all_trades") or []
    out: List[Dict[str, Any]] = []
    for t in trades:
        if not isinstance(t, dict):
            continue
        side = _normalize_side(t.get("side") or t.get("direction") or t.get("position_side"))
        if side is None:
            # ``BacktestTrade.to_json_dict`` uses ``LONG`` / ``SHORT``.
            raw = str(t.get("side") or t.get("direction") or "").upper()
            if raw in {"LONG"}:
                side = "BUY"
            elif raw in {"SHORT"}:
                side = "SELL"
        ep = _safe_float(t.get("entry_price"))
        xp = _safe_float(t.get("exit_price"))
        sl = _safe_float(t.get("stop_price") or t.get("stop"))
        if ep is None or xp is None or side is None:
            continue
        direction = 1 if side == "BUY" else -1
        pnl_pts = (xp - ep) * direction
        R = None
        if sl is not None:
            risk_pts = abs(ep - sl)
            if risk_pts > 0:
                R = pnl_pts / risk_pts
        out.append(
            {
                "symbol": str(t.get("symbol") or "").upper() or None,
                "side": side,
                "entry_ts": t.get("entry_time") or t.get("entry_ts"),
                "exit_ts": t.get("exit_time") or t.get("exit_ts"),
                "entry_price": ep,
                "exit_price": xp,
                "pnl_points": pnl_pts,
                "R": R,
            }
        )
    return out


def _match_round_trips(
    live: List[Dict[str, Any]],
    replay: List[Dict[str, Any]],
    *,
    tolerance_seconds: int = 300,
) -> Tuple[List[TradePair], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Match live to replay by (symbol, side, entry_ts ± tolerance)."""

    used_replay: set = set()
    pairs: List[TradePair] = []

    for lt in live:
        lt_ts = _parse_iso_utc(lt.get("entry_ts"))
        if lt_ts is None:
            continue
        best_idx = None
        best_delta = None
        for i, rt in enumerate(replay):
            if i in used_replay:
                continue
            if rt.get("symbol") and lt.get("symbol") and rt["symbol"] != lt["symbol"]:
                continue
            if rt.get("side") != lt.get("side"):
                continue
            rt_ts = _parse_iso_utc(rt.get("entry_ts"))
            if rt_ts is None:
                continue
            delta = abs((rt_ts - lt_ts).total_seconds())
            if delta > tolerance_seconds:
                continue
            if best_delta is None or delta < best_delta:
                best_delta = delta
                best_idx = i
        if best_idx is None:
            continue
        used_replay.add(best_idx)
        rt = replay[best_idx]
        live_R = lt.get("R")
        replay_R = rt.get("R")
        drift = None
        if live_R is not None and replay_R is not None:
            drift = float(live_R) - float(replay_R)
        pairs.append(
            TradePair(
                symbol=lt.get("symbol") or "?",
                side=lt.get("side") or "?",
                live_entry_ts=lt.get("entry_ts") or "",
                replay_entry_ts=rt.get("entry_ts") or "",
                live_pnl=float(lt.get("pnl_points") or 0.0),
                replay_pnl=float(rt.get("pnl_points") or 0.0),
                live_R=live_R,
                replay_R=replay_R,
                drift_R=drift,
            )
        )

    matched_live_idx = {pair.live_entry_ts for pair in pairs}
    unmatched_live = [lt for lt in live if lt.get("entry_ts") not in matched_live_idx]
    unmatched_replay = [rt for i, rt in enumerate(replay) if i not in used_replay]
    return pairs, unmatched_live, unmatched_replay


def _percentile(values: List[float], pct: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(round((pct / 100.0) * (len(s) - 1)))))
    return s[k]


def _summarize(
    pairs: List[TradePair],
    n_live: int,
    n_replay: int,
    unmatched_live: int,
    unmatched_replay: int,
) -> DriftSummary:
    drifts = [p.drift_R for p in pairs if p.drift_R is not None]
    summary = DriftSummary(
        n_live=n_live,
        n_replay=n_replay,
        n_matched=len(pairs),
        n_unmatched_live=unmatched_live,
        n_unmatched_replay=unmatched_replay,
        pairs=pairs,
    )
    if drifts:
        drifts_sorted = sorted(drifts)
        summary.mean_drift_R = sum(drifts) / len(drifts)
        summary.median_drift_R = drifts_sorted[len(drifts_sorted) // 2]
        summary.p95_abs_drift_R = _percentile([abs(d) for d in drifts], 95.0)
        breaches = sum(1 for d in drifts if abs(d) > 0.20)
        summary.pct_breach_20 = breaches / len(drifts)
    return summary


def compare_logs(
    live_jsonl: Path,
    replay_json: Path,
    *,
    tolerance_seconds: int = 300,
) -> DriftSummary:
    """Compare a live drift JSONL against a replay backtest JSON.

    Returns a :class:`DriftSummary` with per-trade pairs and aggregate drift
    statistics. Both arguments are path-like.
    """
    live_fills = _read_jsonl(Path(live_jsonl))
    live_trips = _live_round_trips(live_fills)
    with Path(replay_json).open("r", encoding="utf-8") as fh:
        replay_blob = json.load(fh)
    replay_trips = _replay_round_trips(replay_blob)
    pairs, ul, ur = _match_round_trips(live_trips, replay_trips, tolerance_seconds=tolerance_seconds)
    return _summarize(pairs, len(live_trips), len(replay_trips), len(ul), len(ur))


# -- CLI -------------------------------------------------------------------


def _print_summary(summary: DriftSummary, verbose: bool = False) -> None:
    print("drift_monitor compare")
    print(f"  live round-trips:    {summary.n_live}")
    print(f"  replay round-trips:  {summary.n_replay}")
    print(f"  matched:             {summary.n_matched}")
    print(f"  unmatched (live):    {summary.n_unmatched_live}")
    print(f"  unmatched (replay):  {summary.n_unmatched_replay}")
    if summary.n_matched:
        print(f"  mean drift_R:        {summary.mean_drift_R:+.4f}")
        print(f"  median drift_R:      {summary.median_drift_R:+.4f}")
        print(f"  p95 |drift_R|:       {summary.p95_abs_drift_R:.4f}")
        print(f"  % |drift_R| > 0.20:  {summary.pct_breach_20 * 100:.1f}%")
        if summary.pct_breach_20 > 0.20:
            print(
                "  ⚠️  More than 20% of trades exceed the 0.20 R drift threshold — "
                "investigate before scaling capital."
            )
    if verbose:
        for p in summary.pairs:
            print(
                f"    {p.symbol} {p.side} live_R={p.live_R} replay_R={p.replay_R} drift={p.drift_R}"
            )


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m core.drift_monitor",
        description="Live-vs-replay drift monitor utilities.",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    cmp_p = sub.add_parser("compare", help="Compare a live drift JSONL to a replay JSON")
    cmp_p.add_argument("--live", type=Path, required=True, help="Live JSONL log")
    cmp_p.add_argument("--replay", type=Path, required=True, help="backtest_executor JSON")
    cmp_p.add_argument(
        "--tolerance-seconds",
        type=int,
        default=300,
        help="Match window for entry timestamp (default: 300s = 5 minutes)",
    )
    cmp_p.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    cmp_p.add_argument("--verbose", action="store_true", help="Print every matched trade")

    args = ap.parse_args(argv)
    if args.cmd == "compare":
        summary = compare_logs(
            args.live,
            args.replay,
            tolerance_seconds=args.tolerance_seconds,
        )
        if args.json:
            print(json.dumps(summary.to_dict(), default=str))
        else:
            _print_summary(summary, verbose=args.verbose)
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
