"""
Thin research runner: parameter grid, mandatory OOS split, Monte Carlo gate, optional DB row.

Uses existing BacktestEngine + HistoricalDataLoader; does not replace core/backtest_executor.py.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import itertools
import logging
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from core.backtest import BacktestEngine
from core.backtest.monte_carlo import MonteCarloSimulator
from core.backtest.models import BacktestResult

logger = logging.getLogger(__name__)

# Function-based strategies only for dataframe + engine path (class strategies use replay elsewhere)
_GRID_STRATEGIES = frozenset({"ma_crossover", "rsi_mean_reversion", "ema_trend"})

MAX_GRID_COMBOS_DEFAULT = 50
MIN_OOS_BARS_DEFAULT = 50


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
    git_sha: Optional[str] = None
    toml_path: Optional[Path] = None
    run_tag: str = "research"
    persist_db: bool = True
    mc_seed: Optional[int] = 42
    # v2
    walk_forward_folds: int = 0
    slippage_sensitivity_ticks: Optional[List[float]] = None
    screen_days: int = 0
    full_days: int = 0


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
    return params


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
    if cfg.strategy not in _GRID_STRATEGIES:
        raise ValueError(f"research runner supports {sorted(_GRID_STRATEGIES)}; got {cfg.strategy!r}")

    git_sha = cfg.git_sha or _git_sha()
    toml_hash = _toml_hash(cfg.toml_path)

    from core.backtest_executor import BacktestExecutor

    executor = BacktestExecutor()

    days_screen = cfg.screen_days or 0
    days_full = cfg.full_days or cfg.days
    effective_days = days_screen if days_screen > 0 else days_full

    raw = executor.loader.get_sample_data(
        symbol=cfg.symbol,
        days=effective_days,
        timeframe=cfg.timeframe,
    )
    if not isinstance(raw, pd.DataFrame) or len(raw) < cfg.min_oos_bars + 20:
        raise ValueError("Insufficient sample bars for IS+OOS; increase --days")

    # Optional second-stage full window (same path; operator compares screen vs full artifacts)
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

    ticks_sens: List[float] = list(cfg.slippage_sensitivity_ticks or [])

    for gi, combo in enumerate(combos):
        params = _coerce_param_dict_for_strategy(cfg.strategy, dict(zip(names, combo)))
        is_res = await _run_engine_on_df(
            executor,
            train_df,
            cfg.strategy,
            cfg.symbol,
            params,
            cfg.slippage_ticks,
            cfg.initial_capital,
        )
        oos_res = await _run_engine_on_df(
            executor,
            test_df,
            cfg.strategy,
            cfg.symbol,
            params,
            cfg.slippage_ticks,
            cfg.initial_capital,
        )

        mc: Dict[str, Any] = {}
        if len(oos_res.trades) > 0:
            mc_sim = MonteCarloSimulator(initial_capital=cfg.initial_capital)
            mc = mc_sim.run_simulations(
                oos_res.trades,
                num_simulations=cfg.mc_simulations,
                seed=cfg.mc_seed,
            )
        passed, reason = _mc_gate(mc, cfg.mc_min_profit_prob, cfg.mc_max_mean_dd_pct)

        row = {
            "grid_id": gi,
            "params": params,
            "is_sharpe": round(is_res.sharpe_ratio, 4),
            "is_trades": is_res.total_trades,
            "oos_sharpe": round(oos_res.sharpe_ratio, 4),
            "oos_trades": oos_res.total_trades,
            "oos_return_pct": round(oos_res.total_return_pct, 4),
            "mc_pass": passed,
            "mc_reason": reason,
            "mc_profit_prob": mc.get("probability_of_profit"),
            "mc_mean_max_dd_pct": mc.get("mean_max_drawdown"),
        }
        results.append(row)

        if gi == 0 and ticks_sens:
            sens_rows: List[Dict[str, Any]] = []
            for tick in ticks_sens:
                r = await _run_engine_on_df(
                    executor,
                    train_df,
                    cfg.strategy,
                    cfg.symbol,
                    params,
                    tick,
                    cfg.initial_capital,
                )
                sens_rows.append(
                    {
                        "slippage_ticks": tick,
                        "total_trades": r.total_trades,
                        "total_return_pct": round(r.total_return_pct, 4),
                        "sharpe_ratio": round(r.sharpe_ratio, 4),
                        "max_drawdown_pct": round(r.max_drawdown_pct, 4),
                    }
                )
            sens_global = sens_rows

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
                    },
                    "walk_forward_folds": cfg.walk_forward_folds,
                    "slippage_sensitivity": sens_global if gi == 0 else [],
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

    wf_rows: List[Dict[str, Any]] = []
    if cfg.walk_forward_folds and cfg.walk_forward_folds > 1:
        n = len(raw)
        folds = int(cfg.walk_forward_folds)
        base_params = _coerce_param_dict_for_strategy(cfg.strategy, dict(zip(names, combos[0])) if combos else {})
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
            tr_r = await _run_engine_on_df(
                executor,
                tr,
                cfg.strategy,
                cfg.symbol,
                base_params,
                cfg.slippage_ticks,
                cfg.initial_capital,
            )
            te_r = await _run_engine_on_df(
                executor,
                te,
                cfg.strategy,
                cfg.symbol,
                base_params,
                cfg.slippage_ticks,
                cfg.initial_capital,
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

    return {
        "strategy": cfg.strategy,
        "symbol": cfg.symbol,
        "git_sha": git_sha,
        "toml_hash": toml_hash,
        "grid_results": results,
        "slippage_sensitivity": sens_global,
        "walk_forward": wf_rows,
    }


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Research grid + OOS + MC gate (sample data by default)")
    p.add_argument("--strategy", default="ma_crossover", choices=sorted(_GRID_STRATEGIES))
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
    p.add_argument("--git-sha", type=str, default="")
    p.add_argument("--toml", type=str, default="", help="Strategy TOML path for metadata hash")
    p.add_argument("--run-tag", type=str, default="research")
    p.add_argument("--no-db", action="store_true")
    p.add_argument("--walk-forward", type=int, default=0, metavar="FOLDS")
    p.add_argument(
        "--slippage-sensitivity",
        type=str,
        default="",
        help="Comma-separated ticks e.g. 0.25,0.5,1.0 (enables table for first grid combo)",
    )
    p.add_argument("--screen-days", type=int, default=0, help="If >0, use this many days for screening stage")
    p.add_argument("--full-days", type=int, default=0, help="Hint for full window when screening (logging only)")
    return p


async def _async_main() -> None:
    from core.logging_setup import configure_logging

    configure_logging(console_level="INFO")
    args = _build_arg_parser().parse_args()
    grid = parse_param_grid(args.grid)
    sens: Optional[List[float]] = None
    if args.slippage_sensitivity.strip():
        sens = [float(x.strip()) for x in args.slippage_sensitivity.split(",") if x.strip()]

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
        git_sha=args.git_sha or None,
        toml_path=Path(args.toml) if args.toml else None,
        run_tag=args.run_tag,
        persist_db=not args.no_db,
        walk_forward_folds=max(0, int(args.walk_forward)),
        slippage_sensitivity_ticks=sens,
        screen_days=max(0, int(args.screen_days)),
        full_days=max(0, int(args.full_days)),
    )
    out = await run_research(cfg)
    logger.info("Research complete: %s", {k: out[k] for k in ("strategy", "symbol", "git_sha")})
    for r in out["grid_results"]:
        logger.info("%s", r)


def main() -> None:
    asyncio.run(_async_main())


if __name__ == "__main__":
    main()
