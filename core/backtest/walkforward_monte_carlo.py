"""Monte Carlo robustness tests for walk-forward trade recaps.

Tests whether pooled replay PnL is an **enduring edge** vs **lucky trade order**
by re-sampling the empirical trade PnL distribution:

- **shuffle** — permute historical trades (path / drawdown sensitivity)
- **bootstrap** — iid resample P&Ls with replacement (distribution uncertainty)

Used by ``scripts/walkforward_trade_recap_report.py`` and
``scripts/enrich_walkforward_monte_carlo.py``.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np


def _trade_pnls(trades: Sequence[Dict[str, Any]]) -> List[float]:
    return [float(t.get("pnl") or 0) for t in trades if isinstance(t, dict)]


def _max_consecutive_losses(pnls: Sequence[float]) -> int:
    streak = best = 0
    for p in pnls:
        if p < 0:
            streak += 1
            best = max(best, streak)
        else:
            streak = 0
    return best


def _sequential_metrics(pnls: Sequence[float], start_equity: float) -> Dict[str, float]:
    equity = float(start_equity)
    peak = equity
    max_dd = 0.0
    max_dd_pct = 0.0
    for p in pnls:
        equity += p
        peak = max(peak, equity)
        dd = peak - equity
        max_dd = max(max_dd, dd)
        if peak > 1e-9:
            max_dd_pct = max(max_dd_pct, (dd / peak) * 100.0)
    total = equity - start_equity
    return {
        "total_pnl": total,
        "final_equity": equity,
        "max_drawdown_dollars": max_dd,
        "max_drawdown_pct": max_dd_pct,
        "max_consecutive_losses": float(_max_consecutive_losses(pnls)),
    }


def _percentile_summary(arr: np.ndarray) -> Dict[str, float]:
    if arr.size == 0:
        return {}
    return {
        "p5": float(np.percentile(arr, 5)),
        "p10": float(np.percentile(arr, 10)),
        "p25": float(np.percentile(arr, 25)),
        "p50": float(np.percentile(arr, 50)),
        "p75": float(np.percentile(arr, 75)),
        "p90": float(np.percentile(arr, 90)),
        "p95": float(np.percentile(arr, 95)),
        "mean": float(np.mean(arr)),
        "std": float(np.std(arr)),
        "min": float(np.min(arr)),
        "max": float(np.max(arr)),
    }


def _histogram_bins(arr: np.ndarray, n_bins: int = 32) -> Dict[str, Any]:
    """Compact histogram for JSON + SVG rendering."""
    if arr.size == 0:
        return {"edges": [], "counts": [], "n_bins": 0}
    lo, hi = float(np.min(arr)), float(np.max(arr))
    if abs(hi - lo) < 1e-9:
        pad = max(abs(lo) * 0.01, 1.0)
        lo, hi = lo - pad, hi + pad
    edges = np.linspace(lo, hi, n_bins + 1)
    counts, _ = np.histogram(arr, bins=edges)
    return {
        "edges": [round(float(e), 2) for e in edges],
        "counts": [int(c) for c in counts],
        "n_bins": n_bins,
    }


def run_walkforward_monte_carlo(
    trades: Sequence[Dict[str, Any]],
    *,
    start_equity: float = 2000.0,
    num_simulations: int = 2000,
    seed: Optional[int] = 42,
    modes: Tuple[str, ...] = ("shuffle", "bootstrap"),
) -> Dict[str, Any]:
    """Run Monte Carlo on recap trade dicts (``pnl`` field required).

    Returns JSON-serializable summary per mode plus sequential baseline and
    endurance verdict heuristics.
    """
    pnls = _trade_pnls(trades)
    n = len(pnls)
    if n == 0:
        return {"n_trades": 0, "modes": {}}

    rng = np.random.default_rng(seed)
    pnl_arr = np.asarray(pnls, dtype=np.float64)
    actual = _sequential_metrics(pnls, start_equity)

    mode_results: Dict[str, Any] = {}
    for mode in modes:
        m = (mode or "shuffle").strip().lower()
        if m not in ("shuffle", "bootstrap"):
            continue
        mode_results[m] = _run_mode(
            pnl_arr,
            start_equity=start_equity,
            num_simulations=num_simulations,
            mode=m,
            rng=rng,
            actual=actual,
            histogram_bins=32,
        )

    verdict = _endurance_verdict(actual, mode_results, n_trades=n)
    return {
        "n_trades": n,
        "start_equity": start_equity,
        "num_simulations": num_simulations,
        "seed": seed,
        "sequential_actual": actual,
        "modes": mode_results,
        "endurance_verdict": verdict,
    }


def _run_mode(
    pnl_arr: np.ndarray,
    *,
    start_equity: float,
    num_simulations: int = 2000,
    mode: str,
    rng: np.random.Generator,
    actual: Dict[str, float],
    histogram_bins: int = 32,
) -> Dict[str, Any]:
    n = len(pnl_arr)
    total_pnls = np.empty(num_simulations, dtype=np.float64)
    final_equities = np.empty(num_simulations, dtype=np.float64)
    max_dds = np.empty(num_simulations, dtype=np.float64)
    max_dd_pcts = np.empty(num_simulations, dtype=np.float64)
    max_consec = np.empty(num_simulations, dtype=np.int32)

    for i in range(num_simulations):
        if mode == "bootstrap":
            sample = rng.choice(pnl_arr, size=n, replace=True)
        else:
            sample = rng.permutation(pnl_arr)
        m = _sequential_metrics(sample, start_equity)
        total_pnls[i] = m["total_pnl"]
        final_equities[i] = m["final_equity"]
        max_dds[i] = m["max_drawdown_dollars"]
        max_dd_pcts[i] = m["max_drawdown_pct"]
        max_consec[i] = int(m["max_consecutive_losses"])

    p_profit = float(np.mean(total_pnls > 0))
    p_final_above_start = float(np.mean(final_equities > start_equity))
    actual_pnl = actual["total_pnl"]
    pct_luckier_than_actual = float(np.mean(total_pnls >= actual_pnl))

    return {
        "simulation_mode": mode,
        "total_pnl": {
            **_percentile_summary(total_pnls),
            "p_profit": round(p_profit, 4),
            "actual": round(actual_pnl, 2),
            "actual_percentile": round(float(np.mean(total_pnls <= actual_pnl)) * 100, 1),
            "pct_sims_gte_actual": round(pct_luckier_than_actual * 100, 1),
        },
        "final_equity": _percentile_summary(final_equities),
        "max_drawdown_dollars": {
            **_percentile_summary(max_dds),
            "actual": round(actual["max_drawdown_dollars"], 2),
            "actual_percentile": round(float(np.mean(max_dds <= actual["max_drawdown_dollars"])) * 100, 1),
        },
        "max_drawdown_pct": {
            **_percentile_summary(max_dd_pcts),
            "actual": round(actual["max_drawdown_pct"], 2),
        },
        "max_consecutive_losses": {
            **_percentile_summary(max_consec.astype(np.float64)),
            "actual": int(actual["max_consecutive_losses"]),
            "p90_or_worse": round(float(np.mean(max_consec >= np.percentile(max_consec, 90))) * 100, 1),
        },
        "probability_final_above_start": round(p_final_above_start, 4),
        "histograms": {
            "total_pnl": _histogram_bins(total_pnls, n_bins=histogram_bins),
            "max_drawdown_dollars": _histogram_bins(max_dds, n_bins=histogram_bins),
        },
    }


def _endurance_verdict(
    actual: Dict[str, float],
    modes: Dict[str, Any],
    *,
    n_trades: int,
) -> Dict[str, Any]:
    """Heuristic pass/fail for walk-forward endurance (not a guarantee)."""
    checks: List[Dict[str, Any]] = []
    shuffle = modes.get("shuffle") or {}
    boot = modes.get("bootstrap") or {}

    def _add(name: str, ok: bool, detail: str) -> None:
        checks.append({"check": name, "pass": ok, "detail": detail})

    sh_pnl = shuffle.get("total_pnl") or {}
    bs_pnl = boot.get("total_pnl") or {}
    sh_order_invariant = (
        float(sh_pnl.get("min") or 0) == float(sh_pnl.get("max") or 0)
        if sh_pnl.get("min") is not None
        else False
    )

    _add(
        "shuffle_p_profit_ge_90pct",
        float(sh_pnl.get("p_profit") or 0) >= 0.90,
        f"shuffle P(profit)={100 * float(sh_pnl.get('p_profit') or 0):.1f}%",
    )
    _add(
        "bootstrap_p_profit_ge_85pct",
        float(bs_pnl.get("p_profit") or 0) >= 0.85,
        f"bootstrap P(profit)={100 * float(bs_pnl.get('p_profit') or 0):.1f}%",
    )
    _add(
        "bootstrap_p5_total_pnl_positive",
        float(bs_pnl.get("p5") or 0) > 0,
        f"bootstrap p5 total PnL=${float(bs_pnl.get('p5') or 0):,.0f}",
    )
    _add(
        "actual_not_order_lucky_shuffle",
        sh_order_invariant or float(sh_pnl.get("actual_percentile") or 100) <= 90,
        "order-invariant (all shuffles identical)"
        if sh_order_invariant
        else (
            f"actual PnL at shuffle {float(sh_pnl.get('actual_percentile') or 0):.0f}th pct "
            f"({float(sh_pnl.get('pct_sims_gte_actual') or 0):.0f}% of shuffles ≥ actual)"
        ),
    )
    sh_dd = shuffle.get("max_drawdown_dollars") or {}
    _add(
        "actual_dd_not_worst_case_shuffle",
        float(sh_dd.get("actual_percentile") or 0) <= 85,
        f"actual max DD at shuffle {float(sh_dd.get('actual_percentile') or 0):.0f}th pct",
    )
    _add(
        "min_sample_size",
        n_trades >= 20,
        f"n_trades={n_trades} (≥20 recommended for MC stability)",
    )

    passed = sum(1 for c in checks if c["pass"])
    n_checks = len(checks)
    if passed == n_checks:
        grade = "strong"
        summary = "Edge appears robust to trade-order reshuffling and bootstrap resampling."
    elif passed >= n_checks - 1:
        grade = "adequate"
        summary = "Mostly robust; review failed check before live sizing."
    elif passed >= n_checks - 2:
        grade = "caution"
        summary = "Mixed signals — edge may depend on sequence or sample size."
    else:
        grade = "weak"
        summary = "Monte Carlo suggests luck or thin edge; do not size up."

    return {
        "grade": grade,
        "summary": summary,
        "checks_passed": passed,
        "checks_total": n_checks,
        "checks": checks,
    }
