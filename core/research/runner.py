"""
Thin research runner: parameter grid, mandatory OOS split, Monte Carlo gate, optional DB row.

Uses existing BacktestEngine + HistoricalDataLoader; class strategies use
``BacktestExecutor._run_strategy_replay`` (same path as ``--replay`` CLI).
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import itertools
import json
import logging
import os
import subprocess
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from core.backtest import BacktestEngine
from core.backtest.monte_carlo import MonteCarloSimulator
from core.backtest.models import BacktestResult

logger = logging.getLogger(__name__)

_GRID_STRATEGIES = frozenset({"ma_crossover", "rsi_mean_reversion", "ema_trend"})

# Live strategy classes — grid-searched via StrategyReplayEngine + mock bot (see backtest_executor).
_REPLAY_STRATEGIES = frozenset(
    {
        "simple_candle",
        "overnight_range",
        "mean_reversion",
        "trend_following",
        "trend_scalping",
        "simple_momentum",
        "simple_rth",
        "vwap_zscore_reversion",
        "body_reversion",
        "morning_range_reversion",
        "hourly_anchor_retrace",
    }
)

_ALL_RESEARCH_STRATEGIES = frozenset(_GRID_STRATEGIES | _REPLAY_STRATEGIES)

MAX_GRID_COMBOS_DEFAULT = 50
MIN_OOS_BARS_DEFAULT = 50

# Env keys that may override ``overnight_range`` filter TOML (see ``run_overnight_range_sweep.sh``).
# ``P0`` research profile clears all of these so live TOML values apply.
OVERNIGHT_RANGE_FILTER_ENV_KEYS: Tuple[str, ...] = (
    "OVERNIGHT_RANGE_FILTERS_SKIP_WEEKDAYS",
    "OVERNIGHT_RANGE_FILTERS_RANGE_MIN_PTS",
    "OVERNIGHT_RANGE_FILTERS_RANGE_MAX_PTS",
    "OVERNIGHT_RANGE_FILTERS_GAP_MAX_PTS",
    "OVERNIGHT_RANGE_FILTERS_ATR_MIN",
    "OVERNIGHT_RANGE_FILTERS_ATR_MAX",
    "OVERNIGHT_RANGE_FILTERS_RANGE_SIZE",
    "OVERNIGHT_RANGE_FILTERS_GAP",
    "OVERNIGHT_RANGE_FILTERS_VOLATILITY",
    "OVERNIGHT_RANGE_FILTERS_DLL_PROXIMITY",
)

# Pathway 1 sweep step P5 — filters on, widened bands (matches ``run_overnight_range_sweep.sh``).
OVERNIGHT_RANGE_P5_RESEARCH_ENV: Dict[str, str] = {
    "OVERNIGHT_RANGE_FILTERS_SKIP_WEEKDAYS": "",
    "OVERNIGHT_RANGE_FILTERS_RANGE_MIN_PTS": "30",
    "OVERNIGHT_RANGE_FILTERS_RANGE_MAX_PTS": "600",
    "OVERNIGHT_RANGE_FILTERS_GAP_MAX_PTS": "250",
    "OVERNIGHT_RANGE_FILTERS_ATR_MIN": "15",
    "OVERNIGHT_RANGE_FILTERS_ATR_MAX": "220",
}


def parse_replay_env(spec: str) -> Dict[str, str]:
    """
    Parse ``KEY=VALUE`` pairs separated by commas. VALUE may be empty (e.g. clear skip list).
    """
    if not (spec or "").strip():
        return {}
    out: Dict[str, str] = {}
    for part in spec.split(","):
        part = part.strip()
        if not part or "=" not in part:
            continue
        key, _, val = part.partition("=")
        out[key.strip()] = val
    return out


def parse_replay_env_clear(spec: str) -> Tuple[str, ...]:
    """Comma-separated env var names to delete before applying ``replay_env``."""
    if not (spec or "").strip():
        return ()
    return tuple(k.strip() for k in spec.split(",") if k.strip())


@contextmanager
def _temp_replay_environ(
    clear_keys: Tuple[str, ...],
    set_vars: Dict[str, str],
) -> Any:
    """Temporarily clear/set process env for strategy ``StrategyConfig`` resolution."""
    keys: Tuple[str, ...] = tuple({*clear_keys, *set_vars.keys()})
    saved: Dict[str, Optional[str]] = {k: os.environ.get(k) for k in keys}
    try:
        for k in clear_keys:
            os.environ.pop(k, None)
        for k, v in set_vars.items():
            os.environ[k] = v
        yield
    finally:
        for k, old in saved.items():
            if old is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = old


@dataclass
class ResearchRunConfig:
    strategy: str
    symbol: str = "MNQ"
    timeframe: str = "5m"
    days: int = 14
    initial_capital: float = 50_000.0
    slippage_ticks: float = 0.5
    oos_fraction: float = 0.2
    min_oos_bars: int = MIN_OOS_BARS_DEFAULT
    param_grid: Dict[str, List[Any]] = field(default_factory=dict)
    max_grid_combos: int = MAX_GRID_COMBOS_DEFAULT
    mc_simulations: int = 200
    mc_min_profit_prob: float = 0.45
    mc_max_mean_dd_pct: float = 35.0
    """Monte Carlo path: ``shuffle`` (permute trade order) or ``bootstrap`` (iid resample P&Ls)."""
    mc_simulation_mode: str = "shuffle"
    git_sha: Optional[str] = None
    toml_path: Optional[Path] = None
    run_tag: str = "research"
    persist_db: bool = True
    mc_seed: Optional[int] = 42
    walk_forward_folds: int = 0
    slippage_sensitivity_ticks: Optional[List[float]] = None
    screen_days: int = 0
    full_days: int = 0
    """Load bars from CSV instead of synthetic sample (same columns as export_history)."""
    csv_path: Optional[str] = None
    """Optional 1m CSV for intrabar fills in replay strategies."""
    csv_1m_path: Optional[str] = None
    """Preloaded 1m bars (dicts) for replay intrabar fills (avoid reloading per combo)."""
    bars_1m: Optional[List[Dict[str, Any]]] = None
    csv_start: Optional[str] = None
    csv_end: Optional[str] = None
    # Delete these env keys before each replay backtest (TOML baseline for overnight_range).
    replay_env_clear: Tuple[str, ...] = ()
    # Extra env for each replay backtest (e.g. OVERNIGHT_RANGE_FILTERS_* overrides).
    replay_env: Dict[str, str] = field(default_factory=dict)


def parse_param_grid(spec: str) -> Dict[str, List[float]]:
    """
    Parse ``key=min:max:step`` segments (comma-separated). Values are float ranges.
    Example: ``fast_period=8:12:2,slow_period=40:80:20``
    """
    if not spec.strip():
        return {}
    out: Dict[str, List[float]] = {}
    for part in spec.split(","):
        part = part.strip()
        if not part or "=" not in part:
            continue
        key, rhs = part.split("=", 1)
        key = key.strip()
        segs = rhs.split(":")
        if len(segs) != 3:
            raise ValueError(f"Bad grid segment (need min:max:step): {part!r}")
        lo, hi, step = (float(segs[0]), float(segs[1]), float(segs[2]))
        if step <= 0:
            raise ValueError(f"step must be > 0 in {part!r}")
        vals: List[float] = []
        x = lo
        while x <= hi + 1e-9:
            vals.append(round(x, 6))
            x += step
        out[key] = vals
    return out


def _git_sha() -> str:
    try:
        return (
            subprocess.check_output(
                ["git", "rev-parse", "HEAD"],
                cwd=Path(__file__).resolve().parent.parent.parent,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
            .decode()
            .strip()
        )
    except Exception:
        return os.getenv("GIT_SHA", "unknown")


def _toml_hash(path: Optional[Path]) -> str:
    if not path or not path.is_file():
        return ""
    h = hashlib.sha256(path.read_bytes()).hexdigest()
    return h[:16]


def _coerce_param_dict_for_strategy(strategy: str, params: Dict[str, Any]) -> Dict[str, Any]:
    if strategy == "ma_crossover":
        out = {}
        if "fast_period" in params:
            out["fast_period"] = int(params["fast_period"])
        if "slow_period" in params:
            out["slow_period"] = int(params["slow_period"])
        return out
    if strategy == "rsi_mean_reversion":
        out = {}
        if "rsi_oversold" in params:
            out["rsi_oversold"] = int(params["rsi_oversold"])
        if "rsi_overbought" in params:
            out["rsi_overbought"] = int(params["rsi_overbought"])
        return out
    if strategy == "ema_trend":
        out = {}
        if "ema_short" in params:
            out["ema_short"] = int(params["ema_short"])
        if "ema_long" in params:
            out["ema_long"] = int(params["ema_long"])
        return out
    if strategy == "morning_range_reversion":
        out: Dict[str, Any] = {}
        if "max_hold_bars" in params:
            out["max_hold_bars"] = int(params["max_hold_bars"])
        if "require_reentry_close" in params:
            v = params["require_reentry_close"]
            if isinstance(v, str):
                out["require_reentry_close"] = v.strip().lower() in ("1", "true", "yes")
            else:
                out["require_reentry_close"] = bool(v)
        for k in ("sl_mult", "tp_mult", "reentry_frac"):
            if k in params:
                out[k] = float(params[k])
        for k, v in params.items():
            if k not in out:
                out[k] = v
        return out
    if strategy == "hourly_anchor_retrace":
        # No special coercions yet; keep floats and strings as-is.
        return params
    return params


def _df_to_bar_dicts(df: pd.DataFrame) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for idx, row in df.iterrows():
        ts = idx if isinstance(idx, datetime) else pd.to_datetime(idx).to_pydatetime()
        out.append(
            {
                "timestamp": ts,
                "open": float(row.get("open", row.get("Open", 0))),
                "high": float(row.get("high", row.get("High", 0))),
                "low": float(row.get("low", row.get("Low", 0))),
                "close": float(row.get("close", row.get("Close", 0))),
                "volume": int(row.get("volume", row.get("Volume", 0))),
            }
        )
    return out


async def _run_engine_on_df(
    executor,
    df: pd.DataFrame,
    strategy_name: str,
    symbol: str,
    strategy_params: Dict[str, Any],
    slippage_ticks: float,
    initial_capital: float,
) -> BacktestResult:
    strategy_func = executor._get_strategy_function(strategy_name, **strategy_params)
    if strategy_func is None:
        raise ValueError(f"Strategy {strategy_name!r} needs function-based replay; use {_GRID_STRATEGIES}")
    data = df.copy()
    if not executor.loader.validate_data(data):
        raise ValueError("Data validation failed")
    data = executor.loader.add_indicators(
        data, indicators=["ema_89", "ema_233", "atr_14", "rsi_14", "sma_20"]
    )
    engine = BacktestEngine(
        initial_capital=initial_capital,
        commission_per_contract=2.50,
        slippage_ticks=slippage_ticks,
        point_value=executor._get_point_value(symbol),
    )
    return await engine.run(
        strategy_func=strategy_func,
        data=data,
        symbol=symbol,
        strategy_name=strategy_name,
        tick_size=executor._get_tick_size(symbol),
    )


async def _run_research_backtest(
    executor,
    df: pd.DataFrame,
    cfg: ResearchRunConfig,
    strategy_params: Dict[str, Any],
    slippage_ticks: float,
) -> BacktestResult:
    """Function-based engine or class replay, depending on ``cfg.strategy``."""
    with _temp_replay_environ(cfg.replay_env_clear, cfg.replay_env):
        if cfg.strategy in _GRID_STRATEGIES:
            return await _run_engine_on_df(
                executor,
                df,
                cfg.strategy,
                cfg.symbol,
                strategy_params,
                slippage_ticks,
                cfg.initial_capital,
            )
        if cfg.strategy in _REPLAY_STRATEGIES:
            bars = _df_to_bar_dicts(df)
            bars_1m = cfg.bars_1m
            bundle = await executor._run_strategy_replay(
                strategy_name=cfg.strategy,
                symbol=cfg.symbol,
                bars=bars,
                bars_1m=bars_1m,
                initial_capital=cfg.initial_capital,
                slippage_ticks=slippage_ticks,
                replay_timeframe=cfg.timeframe,
                quiet=True,
                **strategy_params,
            )
            if not bundle or not bundle.get("result"):
                raise ValueError(f"Replay failed for strategy {cfg.strategy!r}")
            return bundle["result"]
    raise ValueError(
        f"Unknown strategy {cfg.strategy!r}; use one of {sorted(_ALL_RESEARCH_STRATEGIES)}"
    )


async def _oos_slippage_break_even_ticks(
    executor,
    test_df: pd.DataFrame,
    cfg: ResearchRunConfig,
    params: Dict[str, Any],
    ticks: List[float],
) -> Optional[float]:
    """
    Rough slippage (ticks) at which OOS total return crosses from >=0 to <0, using sorted ``ticks``.
    """
    if len(ticks) < 2:
        return None
    ordered = sorted(float(t) for t in ticks)
    prev_t, prev_r = ordered[0], None
    for t in ordered:
        r = await _run_research_backtest(executor, test_df, cfg, params, t)
        ret = float(r.total_return_pct)
        if prev_r is not None and prev_r >= 0.0 > ret:
            denom = prev_r - ret
            if abs(denom) < 1e-12:
                return float(t)
            frac = prev_r / denom
            frac = max(0.0, min(1.0, frac))
            return round(prev_t + frac * (t - prev_t), 4)
        prev_t, prev_r = t, ret
    if prev_r is not None and prev_r < 0:
        return float(ordered[0])
    return None


def _mc_gate(mc: Dict[str, Any], min_prob: float, max_mean_dd: float) -> Tuple[bool, str]:
    if not mc:
        return False, "no_monte_carlo"
    p = float(mc.get("probability_of_profit", 0.0))
    dd = float(mc.get("mean_max_drawdown", 999.0))
    if p < min_prob:
        return False, f"profit_prob {p:.3f} < {min_prob}"
    if dd > max_mean_dd:
        return False, f"mean_max_dd {dd:.2f}% > {max_mean_dd}%"
    return True, "ok"


async def run_research(cfg: ResearchRunConfig) -> Dict[str, Any]:
    if cfg.strategy not in _ALL_RESEARCH_STRATEGIES:
        raise ValueError(
            f"research runner supports {sorted(_ALL_RESEARCH_STRATEGIES)}; got {cfg.strategy!r}"
        )

    git_sha = cfg.git_sha or _git_sha()
    toml_hash = _toml_hash(cfg.toml_path)

    from core.backtest_executor import BacktestExecutor

    executor = BacktestExecutor()

    days_screen = cfg.screen_days or 0
    days_full = cfg.full_days or cfg.days
    effective_days = days_screen if days_screen > 0 else days_full

    if cfg.csv_path:
        raw = executor.loader.load_from_csv(cfg.csv_path, cfg.symbol)
        if not isinstance(raw, pd.DataFrame) or raw.empty:
            raise ValueError(f"CSV load failed or empty: {cfg.csv_path!r}")
        if cfg.csv_start:
            raw = raw[raw.index >= pd.Timestamp(cfg.csv_start)]
        if cfg.csv_end:
            raw = raw[raw.index < pd.Timestamp(cfg.csv_end) + pd.Timedelta(days=1)]
        if cfg.strategy in _REPLAY_STRATEGIES and len(raw) > 50_000:
            logger.warning(
                "CSV has %d bars with replay strategy %s — expect long runtime; "
                "narrow with --csv-start / --csv-end",
                len(raw),
                cfg.strategy,
            )
    else:
        raw = executor.loader.get_sample_data(
            symbol=cfg.symbol,
            days=effective_days,
            timeframe=cfg.timeframe,
        )

    # Preload intrabar 1m bars once for replay strategies (huge speedup vs per-combo loads).
    if cfg.csv_1m_path and cfg.strategy in _REPLAY_STRATEGIES:
        raw_1m = executor.loader.load_from_csv(cfg.csv_1m_path, cfg.symbol)
        if isinstance(raw_1m, pd.DataFrame) and not raw_1m.empty:
            if cfg.csv_start:
                raw_1m = raw_1m[raw_1m.index >= pd.Timestamp(cfg.csv_start)]
            if cfg.csv_end:
                raw_1m = raw_1m[raw_1m.index < pd.Timestamp(cfg.csv_end) + pd.Timedelta(days=1)]
            cfg.bars_1m = _df_to_bar_dicts(raw_1m)
            logger.info("Loaded %d 1m bars for intrabar replay", len(cfg.bars_1m))
    if not isinstance(raw, pd.DataFrame) or len(raw) < cfg.min_oos_bars + 20:
        raise ValueError(
            "Insufficient bars for IS+OOS; increase --days, widen --csv-start/--csv-end, "
            "or lower --min-oos-bars"
        )

    if cfg.screen_days and cfg.full_days and cfg.full_days != cfg.screen_days:
        logger.info(
            "Screen stage uses %d days; re-run with --screen-days 0 --days %d for full window",
            cfg.screen_days,
            cfg.full_days,
        )

    split_idx = int(len(raw) * (1.0 - cfg.oos_fraction))
    split_idx = max(split_idx, 1)
    train_df = raw.iloc[:split_idx].copy()
    test_df = raw.iloc[split_idx:].copy()
    if len(test_df) < cfg.min_oos_bars:
        raise ValueError(
            f"OOS window too small ({len(test_df)} bars); need >= {cfg.min_oos_bars} "
            "(lower --oos-fraction or increase --days)"
        )

    names = list(cfg.param_grid.keys())
    value_lists = [cfg.param_grid[k] for k in names]
    combos = list(itertools.product(*value_lists)) if names else [tuple()]
    if len(combos) > cfg.max_grid_combos:
        raise ValueError(f"Grid size {len(combos)} exceeds cap {cfg.max_grid_combos}")

    results: List[Dict[str, Any]] = []
    sens_global: List[Dict[str, Any]] = []
    sens_oos_global: List[Dict[str, Any]] = []

    ticks_sens: List[float] = list(cfg.slippage_sensitivity_ticks or [])
    mc_mode = (cfg.mc_simulation_mode or "shuffle").strip().lower()
    if mc_mode not in ("shuffle", "bootstrap"):
        mc_mode = "shuffle"

    for gi, combo in enumerate(combos):
        params = _coerce_param_dict_for_strategy(cfg.strategy, dict(zip(names, combo)))
        is_res = await _run_research_backtest(
            executor, train_df, cfg, params, cfg.slippage_ticks
        )
        oos_res = await _run_research_backtest(
            executor, test_df, cfg, params, cfg.slippage_ticks
        )

        mc: Dict[str, Any] = {}
        if len(oos_res.trades) > 0:
            mc_sim = MonteCarloSimulator(initial_capital=cfg.initial_capital)
            mc = mc_sim.run_simulations(
                oos_res.trades,
                num_simulations=cfg.mc_simulations,
                seed=cfg.mc_seed,
                simulation_mode=mc_mode,
            )
        passed, reason = _mc_gate(mc, cfg.mc_min_profit_prob, cfg.mc_max_mean_dd_pct)

        oos_be_ticks: Optional[float] = None
        if len(ticks_sens) >= 2:
            oos_be_ticks = await _oos_slippage_break_even_ticks(
                executor, test_df, cfg, params, ticks_sens
            )

        row = {
            "grid_id": gi,
            "params": params,
            "is_sharpe": round(is_res.sharpe_ratio, 4),
            "is_trades": is_res.total_trades,
            "is_return_pct": round(is_res.total_return_pct, 4),
            "is_total_pnl": round(is_res.total_pnl, 2),
            "oos_sharpe": round(oos_res.sharpe_ratio, 4),
            "oos_trades": oos_res.total_trades,
            "oos_return_pct": round(oos_res.total_return_pct, 4),
            "oos_total_pnl": round(oos_res.total_pnl, 2),
            "oos_slippage_break_even_ticks": oos_be_ticks,
            "mc_pass": passed,
            "mc_reason": reason,
            "mc_profit_prob": mc.get("probability_of_profit"),
            "mc_mean_max_dd_pct": mc.get("mean_max_drawdown"),
            "mc_mode": mc_mode,
        }
        results.append(row)

        if gi == 0 and ticks_sens:
            sens_rows: List[Dict[str, Any]] = []
            sens_oos_rows: List[Dict[str, Any]] = []
            for tick in ticks_sens:
                r_is = await _run_research_backtest(
                    executor, train_df, cfg, params, tick
                )
                sens_rows.append(
                    {
                        "slippage_ticks": tick,
                        "total_trades": r_is.total_trades,
                        "total_return_pct": round(r_is.total_return_pct, 4),
                        "sharpe_ratio": round(r_is.sharpe_ratio, 4),
                        "max_drawdown_pct": round(r_is.max_drawdown_pct, 4),
                    }
                )
                r_oos = await _run_research_backtest(
                    executor, test_df, cfg, params, tick
                )
                sens_oos_rows.append(
                    {
                        "slippage_ticks": tick,
                        "total_trades": r_oos.total_trades,
                        "total_return_pct": round(r_oos.total_return_pct, 4),
                        "sharpe_ratio": round(r_oos.sharpe_ratio, 4),
                        "max_drawdown_pct": round(r_oos.max_drawdown_pct, 4),
                    }
                )
            sens_global = sens_rows
            sens_oos_global = sens_oos_rows

        if cfg.persist_db and passed:
            try:
                from infrastructure.database import get_database

                db = get_database()
                meta = {
                    "git_sha": git_sha,
                    "toml_hash": toml_hash,
                    "run_tag": cfg.run_tag,
                    "is_oos": True,
                    "grid_id": gi,
                    "params": params,
                    "mc_summary": {
                        "probability_of_profit": mc.get("probability_of_profit"),
                        "mean_max_drawdown": mc.get("mean_max_drawdown"),
                        "num_simulations": mc.get("num_simulations"),
                        "simulation_mode": mc_mode,
                    },
                    "walk_forward_folds": cfg.walk_forward_folds,
                    "slippage_sensitivity_is": sens_global if gi == 0 else [],
                    "slippage_sensitivity_oos": sens_oos_global if gi == 0 else [],
                    "oos_slippage_break_even_ticks": oos_be_ticks,
                }
                metrics = {
                    "symbol": cfg.symbol,
                    "total_trades": oos_res.total_trades,
                    "winning_trades": oos_res.winning_trades,
                    "losing_trades": oos_res.losing_trades,
                    "total_pnl": getattr(oos_res, "total_pnl", 0) or 0,
                    "win_rate": oos_res.win_rate,
                    "profit_factor": getattr(oos_res, "profit_factor", None),
                    "max_drawdown": oos_res.max_drawdown_pct,
                    "sharpe_ratio": oos_res.sharpe_ratio,
                    "avg_win": oos_res.average_win,
                    "avg_loss": oos_res.average_loss,
                    "best_trade": oos_res.largest_win,
                    "worst_trade": oos_res.largest_loss,
                    **meta,
                }
                db.save_strategy_metrics(f"{cfg.strategy}_research", metrics)
            except Exception as exc:
                logger.warning("DB persist skipped: %s", exc)

    wf_by_combo: List[Dict[str, Any]] = []
    if cfg.walk_forward_folds and cfg.walk_forward_folds > 1:
        n = len(raw)
        folds = int(cfg.walk_forward_folds)
        for gi, combo in enumerate(combos):
            combo_params = _coerce_param_dict_for_strategy(
                cfg.strategy, dict(zip(names, combo))
            )
            wf_rows: List[Dict[str, Any]] = []
            for k in range(folds):
                a = int(n * k / folds)
                b = int(n * (k + 1) / folds)
                c = int(n * min(k + 2, folds) / folds)
                if b <= a or c <= b:
                    continue
                tr = raw.iloc[a:b].copy()
                te = raw.iloc[b:c].copy()
                if len(te) < 10:
                    continue
                tr_r = await _run_research_backtest(
                    executor, tr, cfg, combo_params, cfg.slippage_ticks
                )
                te_r = await _run_research_backtest(
                    executor, te, cfg, combo_params, cfg.slippage_ticks
                )
                wf_rows.append(
                    {
                        "fold": k,
                        "train_bars": len(tr),
                        "test_bars": len(te),
                        "train_sharpe": round(tr_r.sharpe_ratio, 4),
                        "test_sharpe": round(te_r.sharpe_ratio, 4),
                        "test_trades": te_r.total_trades,
                    }
                )
            wf_by_combo.append({"grid_id": gi, "params": combo_params, "folds": wf_rows})

    return {
        "strategy": cfg.strategy,
        "symbol": cfg.symbol,
        "git_sha": git_sha,
        "toml_hash": toml_hash,
        "grid_results": results,
        "slippage_sensitivity_is": sens_global,
        "slippage_sensitivity_oos": sens_oos_global,
        "walk_forward_by_combo": wf_by_combo,
    }


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Research grid + OOS + MC gate (sample data by default)")
    p.add_argument(
        "--strategy",
        default="ma_crossover",
        choices=sorted(_ALL_RESEARCH_STRATEGIES),
    )
    p.add_argument("--symbol", default="MNQ")
    p.add_argument("--timeframe", default="5m")
    p.add_argument("--days", type=int, default=14)
    p.add_argument("--oos-fraction", type=float, default=0.2)
    p.add_argument("--min-oos-bars", type=int, default=MIN_OOS_BARS_DEFAULT)
    p.add_argument("--grid", type=str, default="", help="key=min:max:step,...")
    p.add_argument("--max-grid", type=int, default=MAX_GRID_COMBOS_DEFAULT)
    p.add_argument("--mc", type=int, default=200)
    p.add_argument("--mc-min-profit-prob", type=float, default=0.45)
    p.add_argument("--mc-max-mean-dd", type=float, default=35.0)
    p.add_argument(
        "--mc-mode",
        type=str,
        default="shuffle",
        choices=("shuffle", "bootstrap"),
        help="Monte Carlo: shuffle trade order vs bootstrap resample P&Ls with replacement",
    )
    p.add_argument("--git-sha", type=str, default="")
    p.add_argument("--toml", type=str, default="", help="Strategy TOML path for metadata hash")
    p.add_argument("--run-tag", type=str, default="research")
    p.add_argument("--no-db", action="store_true")
    p.add_argument("--walk-forward", type=int, default=0, metavar="FOLDS")
    p.add_argument(
        "--slippage-sensitivity",
        type=str,
        default="",
        help="Comma-separated ticks e.g. 0.25,0.5,1.0 (IS+OOS tables for first grid combo)",
    )
    p.add_argument("--screen-days", type=int, default=0, help="If >0, use this many days for screening stage")
    p.add_argument("--full-days", type=int, default=0, help="Hint for full window when screening (logging only)")
    p.add_argument(
        "--csv",
        type=str,
        default="",
        help="OHLCV CSV path (uses HistoricalDataLoader); omit for synthetic sample",
    )
    p.add_argument(
        "--csv-1m",
        type=str,
        default="",
        help="Optional 1m OHLCV CSV (replay strategies only): intrabar order fills inside each aggregate bar",
    )
    p.add_argument(
        "--csv-start",
        type=str,
        default="",
        help="Inclusive YYYY-MM-DD lower bound on CSV index (UTC-naive timestamps)",
    )
    p.add_argument(
        "--csv-end",
        type=str,
        default="",
        help="Inclusive YYYY-MM-DD upper bound on CSV index",
    )
    p.add_argument(
        "--replay-env",
        type=str,
        default="",
        help="Comma-separated KEY=VAL for class-strategy replay (empty VAL allowed). "
        "Applied around each backtest so StrategyConfig picks up overrides.",
    )
    p.add_argument(
        "--replay-env-clear",
        type=str,
        default="",
        help="Comma-separated env var names to unset before --replay-env (TOML wins).",
    )
    p.add_argument(
        "--overnight-research-profile",
        type=str,
        default="",
        metavar="PROFILE",
        help="overnight_range only: p0 = clear all OVERNIGHT_RANGE_FILTERS_* overrides (TOML); "
        "p5 = widened-band P5 env (see run_overnight_range_sweep.sh). Leave empty to skip.",
    )
    p.add_argument(
        "--output",
        type=str,
        default="",
        help="Write full research JSON (grid_results, walk_forward_by_combo, …) to this path.",
    )
    return p


async def _async_main() -> None:
    from core.logging_setup import configure_logging

    configure_logging(console_level="INFO")
    args = _build_arg_parser().parse_args()
    grid = parse_param_grid(args.grid)
    sens: Optional[List[float]] = None
    if args.slippage_sensitivity.strip():
        sens = [float(x.strip()) for x in args.slippage_sensitivity.split(",") if x.strip()]

    replay_clear = parse_replay_env_clear(args.replay_env_clear)
    replay_set = parse_replay_env(args.replay_env)
    prof = (args.overnight_research_profile or "").strip().lower()
    if prof:
        if args.strategy != "overnight_range":
            raise SystemExit(
                "--overnight-research-profile only applies with --strategy overnight_range"
            )
        if prof not in ("p0", "p5"):
            raise SystemExit("--overnight-research-profile must be p0 or p5")
        merged_clear = {*replay_clear, *OVERNIGHT_RANGE_FILTER_ENV_KEYS}
        replay_clear = tuple(sorted(merged_clear))
        if prof == "p5":
            replay_set = {**OVERNIGHT_RANGE_P5_RESEARCH_ENV, **replay_set}

    cfg = ResearchRunConfig(
        strategy=args.strategy,
        symbol=args.symbol,
        timeframe=args.timeframe,
        days=args.days,
        param_grid=grid,
        max_grid_combos=args.max_grid,
        oos_fraction=args.oos_fraction,
        min_oos_bars=args.min_oos_bars,
        mc_simulations=args.mc,
        mc_min_profit_prob=args.mc_min_profit_prob,
        mc_max_mean_dd_pct=args.mc_max_mean_dd,
        mc_simulation_mode=args.mc_mode,
        git_sha=args.git_sha or None,
        toml_path=Path(args.toml) if args.toml else None,
        run_tag=args.run_tag,
        persist_db=not args.no_db,
        walk_forward_folds=max(0, int(args.walk_forward)),
        slippage_sensitivity_ticks=sens,
        screen_days=max(0, int(args.screen_days)),
        full_days=max(0, int(args.full_days)),
        csv_path=args.csv.strip() or None,
        csv_1m_path=args.csv_1m.strip() or None,
        csv_start=args.csv_start.strip() or None,
        csv_end=args.csv_end.strip() or None,
        replay_env_clear=replay_clear,
        replay_env=replay_set,
    )
    out = await run_research(cfg)
    logger.info("Research complete: %s", {k: out[k] for k in ("strategy", "symbol", "git_sha")})
    for r in out["grid_results"]:
        logger.info("%s", r)
    out_path = (args.output or "").strip()
    if out_path:
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_text(json.dumps(out, indent=2, default=str) + "\n")
        logger.info("Wrote research JSON → %s", out_path)


def main() -> None:
    asyncio.run(_async_main())


if __name__ == "__main__":
    main()
