#!/usr/bin/env python3
"""Walk-forward trade recap **report**: charts + hub index + metrics page.

Runs the same calendar folds as ``walkforward_last_trades_charts.py`` / ``walkforward_strategy_competition.py``,
then for each (strategy, symbol, fold) runs ``core/backtest_executor.py --replay --include-trades``.
Writes per-trade **Lightweight Charts** HTML as ``trades/<slug>_trade_chart.html`` (1m OHLCV when
``historical_data/price/{sym}_1m_databento.csv`` exists, else 5m).

Also writes:

- ``metrics_insights.json`` — simulated equity (default **$2,000** start), pooled + per strategy×symbol
  loss-pattern diagnostics (exit reason, side, weekday ET, exploratory Pearson vs loss indicator)
- ``strategy_configs.json`` + ``config/<strategy>.toml`` — verbatim snapshot of each strategy's
  ``config/strategies/<name>.toml`` plus any matching env-var overrides from ``--env``. Lets a reader
  reproduce the run by dropping the snapshot back into ``config/strategies/`` and replaying the env
  vars (mirrors ``StrategyConfig`` precedence: CLI &gt; env &gt; TOML &gt; default).

**Shading:** ``morning_range_reversion`` charts get the 7–8am ET anchor box; ``overnight_range`` /
``overnight_reversion`` charts get overnight session boxes from ``config/strategies/overnight_range.toml``
(same clock / ``overnight_reversion.toml`` mirrors timing).

Replay uses **TOML + executor defaults** (only ``ENABLE_SIGNALR=false`` in the subprocess env unless you pass ``--env``).

Example (3 months, MNQ + MGC, latest canonical Databento CSVs)::

  ENABLE_SIGNALR=false .venv/bin/python scripts/walkforward_trade_recap_report.py \\
    --days 90 --folds 6 --symbols MNQ,MGC \\
    --strategies overnight_range,morning_range_reversion \\
    --last-trades 8 \\
    --out-dir docs/perf/walkforward_mnq_mgc_3m_overnight_morning_trade_recaps
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from datetime import date, datetime, time as dtime, timedelta, timezone
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core.backtest.decision_cache import (
    CacheKeyInputs,
    cache_dir as _cache_dir,
    cache_enabled as _cache_enabled,
    lookup as _cache_lookup,
    store as _cache_store,
)
from core.backtest.inprocess_runner import run_backtest_inprocess, worker_init as _inprocess_worker_init
from core.backtest.ohlcv import dataframe_to_chart_bars_unix, snap_trade_unix_to_chart_bar_open
from core.backtest.parquet_cache import load_ohlcv_cached
from core.backtest.recap_metrics import (
    equity_chart_embed_js,
    equity_curve_from_trades,
    extended_performance_insights,
    format_insights_html,
    loss_pattern_analysis,
    sort_trades_by_exit_time,
)
from core.backtest.strategy_config_snapshot import (
    snapshot_strategy_configs,
    snapshots_to_html,
    snapshots_to_json,
)

# Executor loads StrategyConfig from TOML; keep subprocess headless only.
_REPLAY_BASE_ENV: Dict[str, str] = {
    "ENABLE_SIGNALR": "false",
    "PYTHONUNBUFFERED": "1",
}


def _csv_last_date(csv_path: Path) -> date:
    # ``load_ohlcv_cached`` returns a sorted, naive-UTC, normalized frame
    # — and reads through the parquet sidecar after first call, so this
    # "just check the last timestamp" probe stops re-parsing 100 MB of CSV.
    df = load_ohlcv_cached(csv_path)
    return pd.Timestamp(df.index[-1]).date()


def _fold_ranges(anchor_end: date, *, total_days: int, folds: int) -> List[Tuple[date, date, int]]:
    width = max(1, total_days // folds)
    ranges: List[Tuple[date, date, int]] = []
    global_start = anchor_end - timedelta(days=total_days)
    for i in range(folds):
        fs = global_start + timedelta(days=i * width)
        fe = fs + timedelta(days=width - 1)
        if i == folds - 1:
            fe = anchor_end
        ranges.append((fs, fe, i))
    return ranges


def _parse_iso_utc(s: str) -> datetime:
    s = str(s).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _overlay_for_trade(
    trade: Dict[str, Any],
    bar_times: List[int],
    *,
    signal_info: Optional[Tuple[int, float, str]] = None,
) -> Dict[str, Any]:
    et = _parse_iso_utc(str(trade["entry_time"]))
    xt = _parse_iso_utc(str(trade["exit_time"]))
    eu = int(et.timestamp())
    xu = int(xt.timestamp())
    out: Dict[str, Any] = {
        "trade_id": trade.get("trade_id", ""),
        "side": str(trade.get("side", "")).upper(),
        "entry_time": snap_trade_unix_to_chart_bar_open(eu, bar_times),
        "exit_time": snap_trade_unix_to_chart_bar_open(xu, bar_times),
        "entry_price": float(trade.get("entry_price", 0)),
        "exit_price": float(trade.get("exit_price", 0)),
        "exit_reason": str(trade.get("exit_reason") or ""),
    }
    if signal_info is not None:
        s_unix, s_close, s_label = signal_info
        out["signal_time"] = snap_trade_unix_to_chart_bar_open(int(s_unix), bar_times)
        out["signal_price"] = float(s_close)
        out["signal_label"] = str(s_label)
    return out


def _find_morning_range_signal_bar(
    trade: Dict[str, Any],
    df_5m_utc_index: pd.DataFrame,
    *,
    require_reentry_close: bool,
    reentry_threshold_points: float = 0.0,
    range_start_et: dtime = dtime(7, 0),
    range_end_et: dtime = dtime(8, 0),
    reentry_frac: float = 0.0,
    tz_name: str = "America/New_York",
) -> Optional[Tuple[int, float, str]]:
    """Return (unix_seconds, close_price, label) for the morning-range signal bar.

    Mirrors the strategy's actual decision tree (see
    ``MorningRangeReversionStrategy.analyze``):

    - **Threshold mode** (``reentry_threshold_points > 0``): mark the **first**
      5m close *outside* the anchor range — that's the bar where the strategy
      arms the resting STOP entry at ``L + threshold`` / ``H − threshold``.
      Label: ``"sweep_close (advance stop)"``. The entry fills later when price
      retraces back to the trigger; gap between marker and entry is *expected*.
    - **Mode A** (``require_reentry_close=True`` AND threshold == 0): mark the
      first close *back inside* ``[L+frac*W, H−frac*W]`` after a prior sweep
      (the literature ``reentry_close``). Label: ``"reentry_close"``.
    - **Mode B / immediate** (``require_reentry_close=False`` AND threshold == 0):
      mark the first close *outside* the anchor range — entry is at the range
      extreme on that same bar. Label: ``"sweep_close (immediate)"``.

    Prior to 2026-05-21 this helper hard-coded mode A logic regardless of the
    actual TOML, used the *last* sweep close instead of the *first*, and never
    annotated the trigger level — producing visibly wrong markers like a
    ``reentry_close`` bar three hours before the real entry.
    """
    if ZoneInfo is None or df_5m_utc_index is None or df_5m_utc_index.empty:
        return None
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        return None
    entry_utc = _parse_iso_utc(str(trade["entry_time"]))
    session_date_et = entry_utc.astimezone(tz).date()

    idx = df_5m_utc_index.index
    if getattr(idx, "tz", None) is None:
        idx_et_dt = pd.DatetimeIndex(idx).tz_localize("UTC").tz_convert(tz)
    else:
        idx_et_dt = idx.tz_convert(tz)

    date_arr = idx_et_dt.date
    time_arr = idx_et_dt.time
    same_session = date_arr == session_date_et

    range_mask = same_session & (time_arr >= range_start_et) & (time_arr < range_end_et)
    range_slice = df_5m_utc_index[range_mask]
    if range_slice.empty:
        return None
    range_high = float(range_slice["high"].astype(float).max())
    range_low = float(range_slice["low"].astype(float).min())
    width = range_high - range_low
    if width <= 0:
        return None
    inner_lo = range_low + reentry_frac * width
    inner_hi = range_high - reentry_frac * width

    after_mask = same_session & (time_arr >= range_end_et) & (idx < entry_utc)
    after = df_5m_utc_index[after_mask]
    if after.empty:
        return None

    side = str(trade.get("side", "")).upper()
    is_long = side in ("BUY", "LONG")

    closes = after["close"].astype(float).to_numpy()
    bar_times = list(after.index)

    threshold_pts = max(0.0, float(reentry_threshold_points or 0.0))

    # ── Threshold / advance-stop mode: arms on FIRST sweep close ──
    if threshold_pts > 0.0:
        for i, c in enumerate(closes):
            if is_long and c < range_low:
                ts = bar_times[i]
                return (int(pd.Timestamp(ts).tz_convert("UTC").timestamp()), float(c), "sweep_close (advance stop)")
            if (not is_long) and c > range_high:
                ts = bar_times[i]
                return (int(pd.Timestamp(ts).tz_convert("UTC").timestamp()), float(c), "sweep_close (advance stop)")
        return None

    # ── Immediate mode (no threshold, no re-entry wait): first sweep close ──
    if not require_reentry_close:
        for i, c in enumerate(closes):
            if is_long and c < range_low:
                ts = bar_times[i]
                return (int(pd.Timestamp(ts).tz_convert("UTC").timestamp()), float(c), "sweep_close (immediate)")
            if (not is_long) and c > range_high:
                ts = bar_times[i]
                return (int(pd.Timestamp(ts).tz_convert("UTC").timestamp()), float(c), "sweep_close (immediate)")
        return None

    # ── Mode A: sweep then close back inside inner band ──
    sweep_seen = False
    chosen: Optional[int] = None
    for i, c in enumerate(closes):
        if not sweep_seen:
            if is_long and c < range_low:
                sweep_seen = True
            elif (not is_long) and c > range_high:
                sweep_seen = True
            continue
        if inner_lo <= c <= inner_hi:
            chosen = i
            break
    if chosen is None:
        return None
    ts = bar_times[chosen]
    return (int(pd.Timestamp(ts).tz_convert("UTC").timestamp()), float(closes[chosen]), "reentry_close")


def _run_backtest_json(
    *,
    strategy: str,
    symbol: str,
    csv_path: Path,
    start: date,
    end: date,
    timeframe: str,
    extra_env: Dict[str, str],
) -> Dict[str, Any]:
    cmd = [
        sys.executable,
        str(ROOT / "core" / "backtest_executor.py"),
        f"--strategy={strategy}",
        f"--symbol={symbol}",
        f"--timeframe={timeframe}",
        f"--csv={csv_path}",
        f"--start={start.isoformat()}",
        f"--end={end.isoformat()}",
        "--replay",
        "--format=json",
        "--include-trades",
    ]
    env = os.environ.copy()
    env.update(_REPLAY_BASE_ENV)
    env.update(extra_env)
    proc = subprocess.run(cmd, cwd=str(ROOT), env=env, capture_output=True, text=True, check=False)
    text = (proc.stdout or "").strip()
    if proc.returncode != 0:
        return {"ok": False, "error": proc.stderr[-4000:] or text[-2000:], "strategy": strategy, "symbol": symbol}
    if not text:
        return {"ok": False, "error": "empty stdout", "strategy": strategy, "symbol": symbol}
    try:
        line = text.splitlines()[-1]
        out = json.loads(line)
        if not isinstance(out, dict):
            return {"ok": False, "error": "json root not object", "strategy": strategy, "symbol": symbol}
        return out
    except json.JSONDecodeError as e:
        return {"ok": False, "error": str(e), "strategy": strategy, "symbol": symbol, "tail": text[-1500:]}


def _resolve_csv(csv_dir: Path, csv_template: str, sym: str) -> Path:
    return Path(csv_template.format(csv_dir=str(csv_dir), sym=sym.lower(), SYM=sym))


def _ohlcv_path_for_chart(symbol: str) -> Tuple[Path, str]:
    p1 = ROOT / "historical_data" / "price" / f"{symbol.lower()}_1m_databento.csv"
    p1u = ROOT / "historical_data" / "price" / f"{symbol}_1m_databento.csv"
    for p in (p1, p1u):
        if p.is_file():
            return p, "1m"
    p5 = ROOT / "historical_data" / "price" / f"{symbol.lower()}_5m_databento.csv"
    p5u = ROOT / "historical_data" / "price" / f"{symbol}_5m_databento.csv"
    for p in (p5, p5u):
        if p.is_file():
            return p, "5m"
    return p5, "5m"


def _fold_metrics(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    n = len(trades)
    if n == 0:
        return {"n_trades": 0, "total_pnl": 0.0, "wins": 0, "losses": 0, "win_rate": 0.0}
    pnls = [float(t.get("pnl") or 0) for t in trades]
    wins = sum(1 for p in pnls if p > 0)
    losses = sum(1 for p in pnls if p < 0)
    return {
        "n_trades": n,
        "total_pnl": sum(pnls),
        "wins": wins,
        "losses": losses,
        "win_rate": wins / n if n else 0.0,
    }


def _fold_metrics_deep(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Per-fold or pooled trade list: survivability / distribution stats for recap metrics."""
    base = _fold_metrics(trades)
    n = int(base["n_trades"])
    if n == 0:
        return {
            **base,
            "expectancy": 0.0,
            "pnl_stdev": 0.0,
            "profit_factor": None,
            "avg_win": 0.0,
            "avg_loss": 0.0,
            "best_trade": 0.0,
            "worst_trade": 0.0,
            "max_consec_wins": 0,
            "max_consec_losses": 0,
            "max_drawdown": 0.0,
            "avg_bars_held": None,
            "sum_r_multiple": None,
            "n_trades_with_risk": 0,
        }
    pnls = [float(t.get("pnl") or 0) for t in trades]
    wins_p = [p for p in pnls if p > 0]
    losses_p = [p for p in pnls if p < 0]
    sum_w = float(sum(wins_p)) if wins_p else 0.0
    sum_l = float(sum(losses_p)) if losses_p else 0.0
    pf: Optional[float]
    if sum_l < 0:
        pf = sum_w / abs(sum_l)
    else:
        pf = None

    mean_pnl = statistics.fmean(pnls)
    stdev = statistics.stdev(pnls) if n > 1 else 0.0

    cw = cl = max_cw = max_cl = 0
    for p in pnls:
        if p > 0:
            cw += 1
            cl = 0
        elif p < 0:
            cl += 1
            cw = 0
        else:
            cw = cl = 0
        max_cw = max(max_cw, cw)
        max_cl = max(max_cl, cl)

    peak = cum = 0.0
    max_dd = 0.0
    for p in pnls:
        cum += p
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)

    bars = [t.get("bars_held") for t in trades]
    bars_i = [int(b) for b in bars if b is not None and str(b).strip() != ""]
    avg_bars = statistics.fmean(bars_i) if bars_i else None

    r_mults: List[float] = []
    for t, p in zip(trades, pnls):
        r = float(t.get("initial_risk_dollars") or 0)
        if r > 1e-9:
            r_mults.append(p / r)
    sum_r = float(sum(r_mults)) if r_mults else None

    return {
        **base,
        "expectancy": mean_pnl,
        "pnl_stdev": stdev,
        "profit_factor": pf,
        "avg_win": (sum_w / len(wins_p)) if wins_p else 0.0,
        "avg_loss": (sum_l / len(losses_p)) if losses_p else 0.0,
        "best_trade": max(pnls),
        "worst_trade": min(pnls),
        "max_consec_wins": max_cw,
        "max_consec_losses": max_cl,
        "max_drawdown": max_dd,
        "avg_bars_held": avg_bars,
        "sum_r_multiple": sum_r,
        "n_trades_with_risk": len(r_mults),
    }


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Writes index.html, metrics.html, metrics.json, metrics_insights.json, and trades/*.html under --out-dir.",
    )
    ap.add_argument("--days", type=int, default=90, help="Total calendar span ending at CSV last date")
    ap.add_argument("--folds", type=int, default=6, help="Number of non-overlapping walk-forward folds")
    ap.add_argument(
        "--strategies",
        type=str,
        default="overnight_range,morning_range_reversion",
        help="Comma-separated strategy ids",
    )
    ap.add_argument("--symbols", type=str, default="MNQ,MGC", help="Comma-separated symbols")
    ap.add_argument("--timeframe", type=str, default="5m", help="Replay bar interval (must match --csv)")
    ap.add_argument("--csv-dir", type=Path, default=ROOT / "historical_data" / "price")
    ap.add_argument(
        "--csv-template",
        type=str,
        default="{csv_dir}/{sym}_5m_databento.csv",
        help="Path template: {csv_dir}, {sym}, {SYM}",
    )
    ap.add_argument("--last-trades", type=int, default=8, help="Most recent trades per fold to chart")
    ap.add_argument("--padding-minutes", type=int, default=360, help="OHLC slice padding around entry/exit")
    ap.add_argument(
        "--out-dir",
        type=Path,
        default=ROOT / "docs" / "perf" / "walkforward_mnq_mgc_3m_overnight_morning_trade_recaps",
    )
    ap.add_argument(
        "--sim-start-cash",
        type=float,
        default=2000.0,
        help="Starting equity for simulated account curve (trades merged in exit-time order across folds)",
    )
    ap.add_argument("--dry-run", action="store_true", help="Print fold calendar and exit without running replays")
    ap.add_argument("--env", action="append", default=[], metavar="KEY=VAL", help="Extra env for replay subprocess")
    ap.add_argument(
        "--workers",
        type=int,
        default=None,
        help=(
            "Max concurrent replay subprocesses (default: min(n_tasks, os.cpu_count()); "
            "env override: WALKFORWARD_RECAP_WORKERS). Set to 1 to disable parallelism."
        ),
    )
    ap.add_argument(
        "--cache-decisions",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Default ON. Read/write per-(strategy,symbol,fold) replay payloads "
            "to docs/perf/_decision_cache/ so re-renders of the same TOML / "
            "strategy / window finish in seconds. Cache miss → fresh run. "
            "Cache key includes strategy code + TOML + engine code + env vars + "
            "CSV mtime, so it auto-invalidates on any input change. Disable "
            "with ``--no-cache-decisions`` when you need a clean cold run "
            "(e.g. validating a one-shot engine change). "
            "``BACKTEST_CACHE_DECISIONS=0`` env-disables for the same effect."
        ),
    )
    ap.add_argument(
        "--cache-dir",
        type=Path,
        default=None,
        help="Override decision-cache directory (default: docs/perf/_decision_cache).",
    )
    ap.add_argument(
        "--no-fast-loop",
        action="store_true",
        help=(
            "Opt out of the BACKTEST_FAST_LOOP replay engine (default ON for this "
            "script; switch off to fall back to the slow ``df.iterrows`` + "
            "linear-scan mock_get_historical_data path). Parity is regression-pinned "
            "by tests/test_backtest_fast_loop_parity.py; this flag is for "
            "debugging suspected fast-loop drift only."
        ),
    )
    ap.add_argument(
        "--in-process",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Default ON. Use a pre-warmed ``ProcessPoolExecutor`` of in-process "
            "backtest workers instead of fan-out via ``subprocess.run(python "
            "core/backtest_executor.py ...)``. Eliminates the ~250-400 ms "
            "Python + import startup tax per task (pandas + pyarrow + numpy + "
            "strategy bytecode load once per worker, not once per task). "
            "Trade counts and metrics are identical to the subprocess path "
            "(workers call the same ``run_backtest`` pipeline); env overrides "
            "(``--env``, ``BACKTEST_FAST_LOOP``) apply once at pool startup. "
            "On a typical 24-fold matrix this is ~2× faster wall-clock vs the "
            "subprocess pool. Disable with ``--no-in-process`` when you need "
            "per-task subprocess isolation (e.g. debugging a worker crash)."
        ),
    )
    args = ap.parse_args()

    extra_env: Dict[str, str] = {}
    # Fast loop enabled by default — pinned parity-equal to the slow path on
    # overnight_range and morning_range_reversion across 1- and 4-month windows
    # (see tests/test_backtest_fast_loop_parity.py). On a 3-month MNQ replay this
    # is ~60× faster than the slow path; the wall-clock for a typical 90-day,
    # 6-fold, 3-symbol matrix drops from minutes to seconds.
    if not args.no_fast_loop:
        extra_env["BACKTEST_FAST_LOOP"] = "1"
    for raw in args.env or []:
        if "=" not in raw:
            print(f"error: --env must be KEY=VAL, got {raw!r}", file=sys.stderr)
            return 2
        k, v = raw.split("=", 1)
        extra_env[k.strip()] = v.strip()

    strategies = [s.strip() for s in args.strategies.split(",") if s.strip()]
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    out_dir = args.out_dir if args.out_dir.is_absolute() else ROOT / args.out_dir
    trades_dir = out_dir / "trades"
    trades_dir.mkdir(parents=True, exist_ok=True)

    # Snapshot per-strategy TOMLs into ``<out_dir>/config/`` so this recap directory
    # carries the exact knobs that produced the metrics. Captures matching env-var
    # overrides too (filtered by ``<NAME_UPPER>_`` prefix, same scheme StrategyConfig uses).
    config_snapshots = snapshot_strategy_configs(
        strategies, out_dir=out_dir, env_overrides=extra_env
    )
    config_bundle = snapshots_to_json(
        config_snapshots,
        extra={
            "replay_env_base": dict(_REPLAY_BASE_ENV),
            "cli_extra_env": dict(extra_env),
            "args": {
                "days": args.days,
                "folds": args.folds,
                "strategies": strategies,
                "symbols": symbols,
                "timeframe": args.timeframe,
                "csv_template": args.csv_template,
                "last_trades": int(args.last_trades),
                "padding_minutes": int(args.padding_minutes),
                "sim_start_cash": float(args.sim_start_cash),
            },
        },
    )
    (out_dir / "strategy_configs.json").write_text(
        json.dumps(config_bundle, indent=2, default=str), encoding="utf-8"
    )
    config_snapshot_html = snapshots_to_html(
        config_snapshots, json_link="strategy_configs.json"
    )

    csv_paths: Dict[str, Path] = {}
    anchor_dates: Dict[str, date] = {}
    for sym in symbols:
        p = _resolve_csv(args.csv_dir, args.csv_template, sym)
        if not p.is_file():
            print(f"error: missing CSV for {sym}: {p}", file=sys.stderr)
            return 2
        csv_paths[sym] = p
        anchor_dates[sym] = _csv_last_date(p)

    anchor_end = min(anchor_dates.values())
    folds = _fold_ranges(anchor_end, total_days=args.days, folds=args.folds)
    n_keep = max(1, int(args.last_trades))

    print(
        f"anchor_end={anchor_end} folds={len(folds)} strategies={strategies} symbols={symbols} "
        f"timeframe={args.timeframe} last_trades={n_keep} out={out_dir}",
        file=sys.stderr,
    )
    for fs, fe, ix in folds:
        print(f"  fold {ix}: {fs} .. {fe}", file=sys.stderr)

    if args.dry_run:
        return 0

    sys.path.insert(0, str(ROOT))
    from core.backtest.session_shade import (
        load_overnight_range_timing_from_toml,
        overnight_recap_df_slice_bounds,
    )
    from gui.chart_html import generate_chart_html

    _or_ost, _or_oen, _or_oz = load_overnight_range_timing_from_toml()

    pad = timedelta(minutes=args.padding_minutes)
    ohlcv_cache: Dict[str, Tuple[pd.DataFrame, str]] = {}
    morning_5m_cache: Dict[str, pd.DataFrame] = {}
    for sym in symbols:
        csv_used, tf_chart = _ohlcv_path_for_chart(sym)
        if not csv_used.is_file():
            print(f"error: no 1m/5m chart CSV for {sym}", file=sys.stderr)
            return 2
        # Both chart-overlay slicing and morning-range signal-bar detection need
        # a tz-aware UTC index (downstream code asks ``getattr(idx, 'tz', None)``).
        # ``load_ohlcv_cached`` returns a naive-UTC index for compatibility with
        # ``backtest_executor``'s ``pd.Timestamp(date)`` slice math, so we
        # localize back to UTC here only for these chart-side reads.
        df = load_ohlcv_cached(csv_used)
        if getattr(df.index, "tz", None) is None:
            df = df.tz_localize("UTC")
        ohlcv_cache[sym] = (df, tf_chart)
        morning_5m_path = csv_paths[sym]
        df5 = load_ohlcv_cached(morning_5m_path)
        if getattr(df5.index, "tz", None) is None:
            df5 = df5.tz_localize("UTC")
        morning_5m_cache[sym] = df5

    # ── Read morning_range_reversion knobs from the live TOML so the chart marker
    # finder mirrors what the strategy actually does in replay. Env-var overrides
    # still win (mirrors ``StrategyConfig.get`` precedence: CLI > env > TOML > default).
    # Prior to 2026-05-21 this defaulted to ``require_reentry_close=true`` regardless
    # of the TOML — a bug that produced "reentry_close" markers 3 hours before the
    # actual stop-entry fill, even on TOMLs configured for threshold mode.
    try:
        from core.strategy_config import StrategyConfig as _StratCfg
        _mr_cfg = _StratCfg.load("morning_range_reversion")
        _toml_require_reentry = bool(_mr_cfg.get_bool("signal.require_reentry_close", True))
        _toml_threshold = float(_mr_cfg.get_float("signal.reentry_threshold_points", 0.0) or 0.0)
        _toml_reentry_frac = float(_mr_cfg.get_float("signal.reentry_frac", 0.0) or 0.0)
    except Exception:
        _toml_require_reentry = True
        _toml_threshold = 0.0
        _toml_reentry_frac = 0.0

    _env_require = extra_env.get("MORNING_RANGE_REVERSION_SIGNAL_REQUIRE_REENTRY_CLOSE")
    if _env_require is not None:
        morning_require_reentry_close = str(_env_require).strip().lower() not in ("0", "false", "no", "off")
    else:
        morning_require_reentry_close = _toml_require_reentry

    _env_threshold = extra_env.get("MORNING_RANGE_REVERSION_SIGNAL_REENTRY_THRESHOLD_POINTS")
    morning_reentry_threshold_points = float(_env_threshold) if _env_threshold is not None else _toml_threshold
    morning_reentry_frac = _toml_reentry_frac

    metrics_rows: List[Dict[str, Any]] = []
    chart_count = 0
    fold_sections: List[str] = []
    rollup_trades: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)

    # ── Phase A: fan replay subprocesses out with a ThreadPoolExecutor ────────
    # Each ``_run_backtest_json`` invocation spawns its own ``core/backtest_executor.py``
    # subprocess and blocks on ``subprocess.run`` — perfectly thread-safe from
    # the parent's perspective (no shared Python state crosses the boundary).
    # A thread pool sized to ``cpu_count()`` lets us saturate cores without
    # bumping into the GIL in this Python process. For 36 fold-tasks on an
    # 8-core Mac that's typically a ~4-6× wall-clock win vs the prior serial
    # loop. Phase B (chart rendering / metrics aggregation) stays serial to
    # preserve deterministic HTML ordering and shared cache writes.
    tasks: List[Tuple[date, date, int, str, str]] = [
        (fs, fe, fold_ix, strat, sym)
        for fs, fe, fold_ix in folds
        for strat in strategies
        for sym in symbols
    ]
    n_tasks = len(tasks)
    env_workers = os.environ.get("WALKFORWARD_RECAP_WORKERS")
    if args.workers is not None:
        workers = max(1, int(args.workers))
    elif env_workers:
        try:
            workers = max(1, int(env_workers))
        except ValueError:
            workers = min(n_tasks, os.cpu_count() or 4)
    else:
        workers = min(n_tasks, os.cpu_count() or 4)
    workers = min(workers, n_tasks)

    # ── Decision cache (opt-in) ───────────────────────────────────────────
    # When ``--cache-decisions`` is set, every (strategy, symbol, fold) task
    # consults ``docs/perf/_decision_cache/`` before spawning a subprocess.
    # Cache key includes strategy + TOML + engine + CSV stat — anything that
    # affects results triggers a miss. See ``core/backtest/decision_cache.py``
    # for the full key construction + invalidation strategy. The recap
    # report prints a hits/misses counter at end of run so a stale cache
    # doesn't go unnoticed.
    cache_on = _cache_enabled(cli_flag=args.cache_decisions)
    decision_cache_dir = _cache_dir(args.cache_dir) if cache_on else None
    cache_hits = 0
    cache_misses = 0

    # Pick the per-task runner: in-process avoids the per-subprocess Python +
    # imports startup tax (~250-400 ms each) at the cost of running tasks in
    # worker processes that share env with the parent. The subprocess path is
    # the historical default and still useful for debugging.
    use_inprocess = bool(getattr(args, "in_process", False))

    def _run_inprocess_task(
        strat: str, sym: str, fs_d: date, fe_d: date,
    ) -> Dict[str, Any]:
        """In-process backend equivalent of ``_run_backtest_json``.

        Returns the same dict shape (``{"ok": bool, "result": {...}, ...}``).
        env-var overrides are applied once at pool startup via
        ``_inprocess_worker_init`` — the walkforward script uses a single
        ``extra_env`` for the whole run so this matches subprocess semantics.
        """
        return run_backtest_inprocess(
            strategy=strat,
            symbol=sym,
            csv_path=str(csv_paths[sym]),
            start=fs_d.isoformat(),
            end=fe_d.isoformat(),
            timeframe=args.timeframe,
            include_trades=True,
        )

    def _resolve_payload(
        strat: str, sym: str, fs_d: date, fe_d: date,
    ) -> Tuple[Dict[str, Any], bool]:
        """Cache-aware wrapper around the per-task runner.

        Returns ``(payload, hit)`` where ``hit`` indicates a cache short-circuit.
        Cache disabled → always miss → always run.
        """
        if cache_on and decision_cache_dir is not None:
            ck = CacheKeyInputs(
                strategy=strat, symbol=sym, timeframe=args.timeframe,
                start=fs_d, end=fe_d, csv_path=csv_paths[sym],
                extra_env=dict(extra_env),
            )
            cached = _cache_lookup(ck, cache_dir_path=decision_cache_dir)
            if cached is not None:
                return cached, True
            if use_inprocess:
                payload = _run_inprocess_task(strat, sym, fs_d, fe_d)
            else:
                payload = _run_backtest_json(
                    strategy=strat, symbol=sym, csv_path=csv_paths[sym],
                    start=fs_d, end=fe_d, timeframe=args.timeframe,
                    extra_env=extra_env,
                )
            _cache_store(ck, payload, cache_dir_path=decision_cache_dir)
            return payload, False
        if use_inprocess:
            return _run_inprocess_task(strat, sym, fs_d, fe_d), False
        return _run_backtest_json(
            strategy=strat, symbol=sym, csv_path=csv_paths[sym],
            start=fs_d, end=fe_d, timeframe=args.timeframe,
            extra_env=extra_env,
        ), False

    runner_label = "in-process" if use_inprocess else "subprocess"
    print(
        f"replay phase: {n_tasks} {runner_label} tasks across {workers} worker(s)"
        + (f" (decision cache: {decision_cache_dir})" if cache_on else ""),
        file=sys.stderr,
    )

    # ``--in-process`` uses ``ProcessPoolExecutor`` with a warm-import initializer
    # — workers pre-load pandas/numpy/pyarrow/strategy modules once, then handle
    # multiple tasks each. Parent-side cache lookups always run before any
    # worker is involved so cache hits incur no Python startup at all.
    payloads: Dict[Tuple[int, str, str], Dict[str, Any]] = {}

    if use_inprocess:
        # In-process path: do parent-side cache lookups first (fast — a JSON
        # read per key), then dispatch only the misses to a ProcessPool.
        # Threads cannot run ``run_backtest_inprocess`` concurrently because
        # the strategy loop is pure Python (GIL-bound), so the cache-on +
        # in-process combination MUST use processes for the actual replays.
        pending_misses: List[Tuple[date, date, int, str, str, CacheKeyInputs]] = []
        for fs, fe, fold_ix, strat, sym in tasks:
            key = (fold_ix, strat, sym)
            if cache_on and decision_cache_dir is not None:
                ck = CacheKeyInputs(
                    strategy=strat, symbol=sym, timeframe=args.timeframe,
                    start=fs, end=fe, csv_path=csv_paths[sym],
                    extra_env=dict(extra_env),
                )
                cached = _cache_lookup(ck, cache_dir_path=decision_cache_dir)
                if cached is not None:
                    payloads[key] = cached
                    cache_hits += 1
                    continue
                pending_misses.append((fs, fe, fold_ix, strat, sym, ck))
            else:
                pending_misses.append((fs, fe, fold_ix, strat, sym, None))  # type: ignore[arg-type]

        if pending_misses:
            with ProcessPoolExecutor(
                max_workers=workers,
                initializer=_inprocess_worker_init,
                initargs=(dict(extra_env),),
            ) as pool:
                futs: Dict[Any, Tuple[int, str, str, Optional[CacheKeyInputs]]] = {}
                for fs, fe, fold_ix, strat, sym, ck in pending_misses:
                    fut = pool.submit(
                        run_backtest_inprocess,
                        strategy=strat, symbol=sym,
                        csv_path=str(csv_paths[sym]),
                        start=fs.isoformat(), end=fe.isoformat(),
                        timeframe=args.timeframe, include_trades=True,
                    )
                    futs[fut] = (fold_ix, strat, sym, ck)
                n_done = 0
                for fut in as_completed(futs):
                    fold_ix, strat, sym, ck = futs[fut]
                    key2 = (fold_ix, strat, sym)
                    try:
                        payload = fut.result()
                    except Exception as exc:  # noqa: BLE001
                        payload = {
                            "ok": False,
                            "error": f"worker exception: {exc!r}"[:2000],
                            "strategy": strat,
                            "symbol": sym,
                        }
                    payloads[key2] = payload
                    cache_misses += 1
                    if ck is not None and decision_cache_dir is not None:
                        _cache_store(ck, payload, cache_dir_path=decision_cache_dir)
                    n_done += 1
                    if n_done % max(1, n_tasks // 10) == 0 or n_done == n_tasks:
                        print(f"  replays {n_done}/{n_tasks}", file=sys.stderr)
    else:
        # Subprocess path: ThreadPool of ``_resolve_payload`` (each call forks
        # its own ``core/backtest_executor.py`` subprocess, blocking on the
        # ``subprocess.run`` — threads run concurrently because the GIL is
        # released while waiting on the child process I/O).
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futs2 = {
                pool.submit(_resolve_payload, strat, sym, fs, fe): (fold_ix, strat, sym)
                for fs, fe, fold_ix, strat, sym in tasks
            }
            n_done = 0
            for fut in as_completed(futs2):
                key = futs2[fut]
                try:
                    payload, hit = fut.result()
                    payloads[key] = payload
                    if hit:
                        cache_hits += 1
                    else:
                        cache_misses += 1
                except Exception as exc:  # noqa: BLE001
                    payloads[key] = {
                        "ok": False,
                        "error": f"worker exception: {exc!r}"[:2000],
                        "strategy": key[1],
                        "symbol": key[2],
                    }
                    cache_misses += 1
                n_done += 1
                if n_done % max(1, n_tasks // 10) == 0 or n_done == n_tasks:
                    print(f"  replays {n_done}/{n_tasks}", file=sys.stderr)
    if cache_on:
        print(
            f"decision cache: {cache_hits} hit(s), {cache_misses} miss(es) "
            f"across {n_tasks} task(s)",
            file=sys.stderr,
        )

    # ── Phase B: walk the original ordering, render charts, build metrics ────
    # Pulled payloads from the dict in the same loop shape the script has used
    # since inception so HTML row order, metrics_rows order, and slug naming
    # stay byte-identical.
    for fs, fe, fold_ix in folds:
        fold_rows: List[str] = []
        for strat in strategies:
            for sym in symbols:
                payload = payloads[(fold_ix, strat, sym)]
                if payload.get("ok") is False or "result" not in payload:
                    err = payload.get("error", "no result")
                    print(f"FAIL {strat} {sym} fold{fold_ix}: {err}", file=sys.stderr)
                    fold_rows.append(
                        f"<tr><td>{strat}</td><td>{sym}</td><td colspan='6'><code>{_html_escape(str(err)[:800])}</code></td></tr>"
                    )
                    metrics_rows.append(
                        {
                            "fold": fold_ix,
                            "fold_start": fs.isoformat(),
                            "fold_end": fe.isoformat(),
                            "strategy": strat,
                            "symbol": sym,
                            "ok": False,
                            "error": str(err)[:2000],
                        }
                    )
                    continue
                res = payload.get("result") or {}
                trades = [t for t in (res.get("trades") or []) if isinstance(t, dict)]
                trades.sort(key=lambda t: _parse_iso_utc(str(t.get("exit_time") or "")))
                for t in trades:
                    enriched = {
                        **t,
                        "_strategy": strat,
                        "_symbol": sym,
                        "_fold": fold_ix,
                    }
                    rollup_trades[(strat, sym)].append(enriched)
                fm = _fold_metrics_deep(trades)
                metrics_rows.append(
                    {
                        "fold": fold_ix,
                        "fold_start": fs.isoformat(),
                        "fold_end": fe.isoformat(),
                        "strategy": strat,
                        "symbol": sym,
                        "ok": True,
                        **fm,
                    }
                )
                picked = trades[-n_keep:] if len(trades) > n_keep else trades

                for ti, trade in enumerate(picked):
                    df_idx, tf = ohlcv_cache[sym]
                    et = _parse_iso_utc(str(trade["entry_time"]))
                    xt = _parse_iso_utc(str(trade["exit_time"]))
                    if strat in ("overnight_range", "overnight_reversion"):
                        lo_ix, hi_ix = overnight_recap_df_slice_bounds(
                            et, xt, pad, _or_ost, _or_oen, _or_oz
                        )
                        sub = df_idx.loc[(df_idx.index >= lo_ix) & (df_idx.index <= hi_ix)]
                    else:
                        sub = df_idx.loc[(df_idx.index >= et - pad) & (df_idx.index <= xt + pad)]
                    if sub.empty:
                        fold_rows.append(
                            f"<tr><td>{strat}</td><td>{sym}</td><td>—</td><td colspan='4'>empty OHLC slice</td></tr>"
                        )
                        continue
                    bars, bar_times = dataframe_to_chart_bars_unix(sub)
                    signal_info = None
                    if strat == "morning_range_reversion":
                        signal_info = _find_morning_range_signal_bar(
                            trade,
                            morning_5m_cache.get(sym),
                            require_reentry_close=morning_require_reentry_close,
                            reentry_threshold_points=morning_reentry_threshold_points,
                            reentry_frac=morning_reentry_frac,
                        )
                    overlay = _overlay_for_trade(trade, bar_times, signal_info=signal_info)
                    tid = str(trade.get("trade_id", f"T{ti}"))
                    slug = f"fold{fold_ix}_{strat}_{sym}_{tid}_{ti}".replace(" ", "_")
                    chart_name = f"{slug}_trade_chart.html"
                    html_path = trades_dir / chart_name
                    generate_chart_html(
                        symbol=sym,
                        timeframe=tf,
                        bars=bars,
                        output_path=str(html_path),
                        realtime=False,
                        backtest=False,
                        trade_overlays=[overlay],
                        axis_time_zone="America/New_York",
                        morning_range_et_shade=(strat == "morning_range_reversion"),
                        overnight_range_et_shade=(strat in ("overnight_range", "overnight_reversion")),
                    )
                    chart_count += 1
                    pnl = float(trade.get("pnl") or 0)
                    href = f"trades/{chart_name}"
                    fold_rows.append(
                        "<tr>"
                        f"<td><code>{_html_escape(strat)}</code></td><td><code>{_html_escape(sym)}</code></td>"
                        f"<td>{_html_escape(str(trade.get('side','')))}</td>"
                        f"<td>{_html_escape(str(trade.get('entry_time','')))}</td>"
                        f"<td>{_html_escape(str(trade.get('exit_time','')))}</td>"
                        f"<td>{pnl:.2f}</td>"
                        f"<td>{_html_escape(str(trade.get('exit_reason','')))}</td>"
                        f'<td><a href="{href}">trade_chart</a></td>'
                        "</tr>"
                    )

        fold_sections.append(
            f"<h2 id='fold{fold_ix}'>Fold {fold_ix}: {fs} → {fe}</h2>"
            "<table><thead><tr><th>strategy</th><th>sym</th><th>side</th><th>entry</th><th>exit</th>"
            "<th>pnl</th><th>exit_reason</th><th>chart</th></tr></thead><tbody>"
            f"{''.join(fold_rows) if fold_rows else '<tr><td colspan=8>No rows</td></tr>'}"
            "</tbody></table>"
        )

    (out_dir / "metrics.json").write_text(json.dumps(metrics_rows, indent=2), encoding="utf-8")

    def _pf_str(pf: Any) -> str:
        if pf is None:
            return "—"
        try:
            v = float(pf)
        except (TypeError, ValueError):
            return "—"
        if v > 1e6:
            return "∞"
        return f"{v:.2f}"

    metrics_table_rows: List[str] = []
    for m in metrics_rows:
        if not m.get("ok"):
            metrics_table_rows.append(
                "<tr>"
                f"<td>{m.get('fold')}</td><td>{m.get('fold_start','')}</td><td>{m.get('fold_end','')}</td>"
                f"<td><code>{_html_escape(m.get('strategy',''))}</code></td>"
                f"<td><code>{_html_escape(m.get('symbol',''))}</code></td>"
                f"<td colspan='12'><code>{_html_escape(str(m.get('error','')))}</code></td>"
                "</tr>"
            )
            continue
        wr = 100.0 * float(m.get("win_rate") or 0)
        exp = float(m.get("expectancy") or 0)
        std = float(m.get("pnl_stdev") or 0)
        mdd = float(m.get("max_drawdown") or 0)
        pf_s = _pf_str(m.get("profit_factor"))
        avg_b = m.get("avg_bars_held")
        avg_b_s = f"{float(avg_b):.1f}" if avg_b is not None else "—"
        sum_r = m.get("sum_r_multiple")
        sum_r_s = f"{float(sum_r):.2f}" if sum_r is not None else "—"
        metrics_table_rows.append(
            "<tr>"
            f"<td>{m['fold']}</td><td>{m['fold_start']}</td><td>{m['fold_end']}</td>"
            f"<td><code>{_html_escape(m['strategy'])}</code></td><td><code>{_html_escape(m['symbol'])}</code></td>"
            f"<td class='num'>{m['n_trades']}</td>"
            f"<td class='num'>{float(m['total_pnl']):.2f}</td>"
            f"<td class='num'>{wr:.1f}%</td>"
            f"<td class='num'>{exp:.3f}</td>"
            f"<td class='num'>{std:.3f}</td>"
            f"<td class='num'>{pf_s}</td>"
            f"<td class='num'>{mdd:.2f}</td>"
            f"<td>{m['wins']}</td><td>{m['losses']}</td>"
            f"<td>{m.get('max_consec_wins', 0)}</td><td>{m.get('max_consec_losses', 0)}</td>"
            f"<td class='num'>{avg_b_s}</td><td class='num'>{sum_r_s}</td>"
            "</tr>"
        )

    rollup_rows: List[str] = []
    for (st, sy) in sorted(rollup_trades.keys(), key=lambda k: (k[0], k[1])):
        tlist = rollup_trades[(st, sy)]
        rm = _fold_metrics_deep(tlist)
        if int(rm.get("n_trades") or 0) == 0:
            continue
        wr = 100.0 * float(rm.get("win_rate") or 0)
        exp = float(rm.get("expectancy") or 0)
        std = float(rm.get("pnl_stdev") or 0)
        pf_s = _pf_str(rm.get("profit_factor"))
        avg_b = rm.get("avg_bars_held")
        avg_b_s = f"{float(avg_b):.1f}" if avg_b is not None else "—"
        sum_r = rm.get("sum_r_multiple")
        sum_r_s = f"{float(sum_r):.2f}" if sum_r is not None else "—"
        rollup_rows.append(
            "<tr>"
            f"<td><code>{_html_escape(st)}</code></td><td><code>{_html_escape(sy)}</code></td>"
            f"<td class='num'>{rm['n_trades']}</td>"
            f"<td class='num'>{float(rm['total_pnl']):.2f}</td>"
            f"<td class='num'>{wr:.1f}%</td>"
            f"<td class='num'>{exp:.3f}</td>"
            f"<td class='num'>{std:.3f}</td>"
            f"<td class='num'>{pf_s}</td>"
            f"<td class='num'>{float(rm.get('max_drawdown') or 0):.2f}</td>"
            f"<td>{rm.get('max_consec_losses', 0)}</td>"
            f"<td class='num'>{avg_b_s}</td><td class='num'>{sum_r_s}</td>"
            "</tr>"
        )

    ok_rows = [m for m in metrics_rows if m.get("ok")]
    grand_n = sum(int(m.get("n_trades", 0)) for m in ok_rows)
    grand_pnl = sum(float(m.get("total_pnl", 0)) for m in ok_rows)
    all_trades_flat: List[Dict[str, Any]] = sort_trades_by_exit_time(
        [t for tlist in rollup_trades.values() for t in tlist]
    )
    grand_deep = _fold_metrics_deep(all_trades_flat)

    sim_cash = float(args.sim_start_cash)
    eq_pts_grand, eq_sum_grand = equity_curve_from_trades(all_trades_flat, start_equity=sim_cash)
    if not eq_pts_grand:
        eq_chart_block = "<p class='muted'>No trades — no equity curve.</p>"
    else:
        eq_chart_block = equity_chart_embed_js(eq_pts_grand)

    g_ext = extended_performance_insights(all_trades_flat)
    g_loss = loss_pattern_analysis(all_trades_flat)
    grand_insights_html = (
        format_insights_html(
            title="All legs pooled (exit-time order, all folds)",
            extended=g_ext,
            loss_patterns=g_loss,
        )
        if all_trades_flat
        else "<p class='muted'>No trades for pooled diagnostics.</p>"
    )

    per_rollup_insight_html: List[str] = []
    insights_per: Dict[str, Any] = {}
    for st, sy in sorted(rollup_trades.keys(), key=lambda k: (k[0], k[1])):
        tlist = rollup_trades[(st, sy)]
        if not tlist:
            continue
        ext_r = extended_performance_insights(tlist)
        lp_r = loss_pattern_analysis(tlist)
        _, eq_sr = equity_curve_from_trades(tlist, start_equity=sim_cash)
        insights_per[f"{st}|{sy}"] = {
            "equity_summary": eq_sr,
            "extended": ext_r,
            "loss_patterns": lp_r,
        }
        per_rollup_insight_html.append(
            format_insights_html(
                title=f"{st} · {sy} (merged folds)",
                extended=ext_r,
                loss_patterns=lp_r,
            )
        )

    insights_doc: Dict[str, Any] = {
        "sim_start_equity": sim_cash,
        "grand": {
            "equity_curve": eq_pts_grand,
            "equity_summary": eq_sum_grand,
            "extended": g_ext,
            "loss_patterns": g_loss,
        },
        "per_strategy_symbol": insights_per,
    }

    dark_css = """
:root {
  --bg: #0b0b0d;
  --panel: #131316;
  --panel2: #1a1a1f;
  --text: #e4e4ea;
  --muted: #9b9ba8;
  --border: #2c2c34;
  --link: #7eb8ff;
  --pos: #5ecf8e;
  --neg: #f08080;
  --accent: #c9b87c;
}
body { font-family: system-ui, -apple-system, Segoe UI, sans-serif; margin: 0; padding: 1.5rem 2rem 3rem;
  max-width: 128rem; background: var(--bg); color: var(--text); line-height: 1.45; }
h1 { font-size: 1.35rem; font-weight: 650; margin: 0 0 0.75rem; color: var(--text); }
h2 { font-size: 1.05rem; margin: 2rem 0 0.75rem; color: var(--accent); font-weight: 600; }
table { border-collapse: collapse; width: 100%; font-size: 0.78rem; margin-bottom: 1.75rem; background: var(--panel); border-radius: 8px; overflow: hidden; }
th, td { border: 1px solid var(--border); padding: 0.4rem 0.5rem; text-align: left; vertical-align: top; }
th { background: var(--panel2); color: #d8d8e0; font-weight: 600; }
tr:nth-child(even) td { background: rgba(255,255,255,0.02); }
a { color: var(--link); text-decoration: none; }
a:hover { text-decoration: underline; }
code { background: var(--panel2); padding: 0.12rem 0.35rem; border-radius: 4px; font-size: 0.88em; color: #dbeafe; }
.muted { color: var(--muted); font-size: 0.9rem; max-width: 72rem; }
.banner { background: var(--panel2); border: 1px solid var(--border); padding: 0.85rem 1.1rem; border-radius: 8px; margin: 1rem 0 1.25rem; }
.callout { border-left: 3px solid var(--accent); padding: 0.5rem 0 0.5rem 1rem; margin: 1rem 0; color: var(--muted); font-size: 0.88rem; }
nav { margin-bottom: 1.25rem; font-size: 1.02rem; }
nav a { margin-right: 1.25rem; font-weight: 600; }
.num { text-align: right; font-variant-numeric: tabular-nums; }
.pos { color: var(--pos); } .neg { color: var(--neg); }
pre { background: var(--panel2); padding: 0.75rem 1rem; border-radius: 6px; overflow: auto; font-size: 0.78rem; line-height: 1.45; color: #d8d8e0; }
pre code { background: transparent; padding: 0; color: inherit; font-size: inherit; }
details > summary { cursor: pointer; padding: 0.3rem 0; color: var(--link); font-weight: 500; user-select: none; }
details[open] > summary { margin-bottom: 0.5rem; }
h2#config-snapshot { margin-top: 2.5rem; border-top: 1px solid var(--border); padding-top: 1.25rem; }
h3 { font-size: 0.95rem; margin: 1.25rem 0 0.5rem; color: #d8d8e0; }
"""

    g_pf = _pf_str(grand_deep.get("profit_factor"))
    g_exp = float(grand_deep.get("expectancy") or 0)
    g_std = float(grand_deep.get("pnl_stdev") or 0)
    g_mdd = float(grand_deep.get("max_drawdown") or 0)
    g_mcl = int(grand_deep.get("max_consec_losses") or 0)

    metrics_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Walk-forward metrics — {', '.join(strategies)} / {', '.join(symbols)}</title>
  <style>{dark_css}</style>
</head>
<body>
  <p><a href="index.html">← Back to report hub</a></p>
  <h1>Walk-forward performance (replay)</h1>
  <p class="muted">Fold × strategy × symbol from <code>backtest_executor.py --replay --include-trades</code>.
    <strong>Expectancy</strong> = mean PnL per trade; <strong>PF</strong> = sum(winning $) / |sum(losing $)|;
    <strong>Max DD</strong> = peak-to-trough on cumulative PnL within that fold’s trade sequence;
    <strong>ΣR</strong> = sum of (trade PnL / <code>initial_risk_dollars</code>) where risk was recorded.</p>
  <nav style="margin-bottom:1rem"><a href="metrics.json">metrics.json</a> · <a href="metrics_insights.json">metrics_insights.json</a> · <a href="strategy_configs.json">strategy_configs.json</a> · <a href="#config-snapshot">replay config ↓</a></nav>
  <div class="banner">
    <strong>All folds pooled:</strong> {grand_n} trades · PnL <strong>{grand_pnl:.2f}</strong>
    · expectancy <strong>{g_exp:.3f}</strong> / trade · σ <strong>{g_std:.3f}</strong>
    · PF <strong>{g_pf}</strong> · max consecutive losses <strong>{g_mcl}</strong>
    · max DD (sequential) <strong>{g_mdd:.2f}</strong>
  </div>
  <div class="callout">
    <strong>Survivability read:</strong> high <em>max consecutive losses</em> inside a fold with negative expectancy
    usually means the edge is not stable across that window. Compare the <em>rollup</em> table (all folds merged
    per strategy×symbol) to per-fold rows — widening σ or falling PF in later folds is a simple walk-forward stress signal.
  </div>
  <h2>Per fold</h2>
  <table>
    <thead><tr><th>fold</th><th>start</th><th>end</th><th>strategy</th><th>sym</th><th class="num">n</th><th class="num">PnL</th><th class="num">WR%</th>
    <th class="num">E/trade</th><th class="num">σ PnL</th><th class="num">PF</th><th class="num">maxDD</th><th>win</th><th>loss</th><th>max win str</th><th>max loss str</th><th class="num">avg bars</th><th class="num">ΣR</th></tr></thead>
    <tbody>{''.join(metrics_table_rows) if metrics_table_rows else '<tr><td colspan="17">No data</td></tr>'}</tbody>
  </table>
  <h2>Rollup — all folds merged (per strategy × symbol)</h2>
  <table>
    <thead><tr><th>strategy</th><th>sym</th><th class="num">n</th><th class="num">PnL</th><th class="num">WR%</th><th class="num">E/trade</th><th class="num">σ PnL</th><th class="num">PF</th><th class="num">maxDD</th><th>max loss str</th><th class="num">avg bars</th><th class="num">ΣR</th></tr></thead>
    <tbody>{''.join(rollup_rows) if rollup_rows else '<tr><td colspan="12">No trades</td></tr>'}</tbody>
  </table>
  <h2>Simulated account equity</h2>
  <p class="muted">Starting balance <strong>${sim_cash:,.2f}</strong>. Every completed trade in this run is sorted by <strong>exit time</strong>
    and applied in sequence to one notional account (all legs, all folds). This shows combined PnL path stress;
    it does <em>not</em> model margin, cross-margin, or whether every leg would run on the same live account.</p>
  <div class="banner">
    <strong>Equity path:</strong> final <strong>{eq_sum_grand.get("final_equity", 0):,.2f}</strong>
    · return <strong>{eq_sum_grand.get("total_return_pct", 0):.2f}%</strong>
    · max drawdown <strong>{eq_sum_grand.get("max_drawdown_dollars", 0):,.2f}</strong> (<strong>{eq_sum_grand.get("max_drawdown_pct", 0):.2f}%</strong> from peak)
    · closes in path <strong>{eq_sum_grand.get("n_trades", 0)}</strong>
  </div>
  {eq_chart_block}
  <h2>Analysis — all legs pooled</h2>
  {grand_insights_html}
  <h2>Analysis — per strategy × symbol (merged folds)</h2>
  {(''.join(per_rollup_insight_html)) if per_rollup_insight_html else "<p class='muted'>—</p>"}
  {config_snapshot_html}
</body>
</html>
"""

    index_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Walk-forward trade recaps — {', '.join(strategies)} / {', '.join(symbols)}</title>
  <style>{dark_css}</style>
</head>
<body>
  <h1>Walk-forward trade recaps</h1>
  <nav>
    <a href="metrics.html">Metrics / survivability</a>
    <a href="metrics.html#config-snapshot">Replay config (exact TOML)</a>
    <a href="metrics.json">metrics.json</a>
    <a href="metrics_insights.json">metrics_insights.json</a>
    <a href="strategy_configs.json">strategy_configs.json</a>
  </nav>
  <p class="muted">Replay <code>{args.timeframe}</code> from <code>{args.csv_template}</code> · anchor end <strong>{anchor_end}</strong> ·
    <strong>{args.days}</strong> calendar days · <strong>{len(folds)}</strong> folds · last <strong>{n_keep}</strong> trades charted per fold.
    Charts: <code>trades/&lt;slug&gt;_trade_chart.html</code> (1m when CSV present).</p>
  <p class="muted">Shading: <strong>morning_range_reversion</strong> → 7–8am ET anchor;
    <strong>overnight_range</strong> / <strong>overnight_reversion</strong> → overnight box (timing from <code>overnight_range.toml</code>).</p>
  <ul>{''.join(f"<li><a href='#fold{ix}'>Fold {ix}: {fs} → {fe}</a></li>" for fs, fe, ix in folds)}</ul>
  {''.join(fold_sections)}
</body>
</html>
"""

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "metrics_insights.json").write_text(json.dumps(insights_doc, indent=2), encoding="utf-8")
    (out_dir / "metrics.html").write_text(metrics_html, encoding="utf-8")
    (out_dir / "index.html").write_text(index_html, encoding="utf-8")
    print(f"wrote {out_dir / 'index.html'}", file=sys.stderr)
    print(f"wrote {out_dir / 'metrics.html'}", file=sys.stderr)
    print(f"wrote {out_dir / 'metrics_insights.json'}", file=sys.stderr)
    print(f"wrote {out_dir / 'strategy_configs.json'}", file=sys.stderr)
    snapshot_count = sum(1 for s in config_snapshots if s.snapshot is not None)
    print(
        f"wrote {snapshot_count}/{len(config_snapshots)} strategy TOML snapshots under {out_dir / 'config'}",
        file=sys.stderr,
    )
    print(f"wrote {chart_count} charts under {trades_dir}", file=sys.stderr)
    return 0


def _html_escape(s: str) -> str:
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


if __name__ == "__main__":
    raise SystemExit(main())
