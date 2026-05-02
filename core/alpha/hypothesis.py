"""
Statistical hypothesis tests for alpha signal discovery.

Each test returns a HypothesisResult containing per-bucket statistics
and a p-value from the most appropriate non-parametric test.

Multiple-comparison correction (Benjamini-Hochberg FDR) is applied
by the scanner after all features have been tested.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
from scipy import stats

logger = logging.getLogger(__name__)


@dataclass
class BucketStats:
    label: str
    n: int
    win_rate: float       # fraction of positive-R trades
    avg_r: float          # mean realized-R
    profit_factor: float  # gross_profit / gross_loss
    avg_pnl_usd: float
    std_r: float
    sharpe: float         # simple in-bucket Sharpe (annualization not applied)


@dataclass
class HypothesisResult:
    feature: str
    feature_type: str          # "categorical" | "continuous"
    test_name: str
    buckets: List[BucketStats]
    p_value: float
    effect_size: float         # eta-squared (categorical) or rank-biserial (continuous)
    ic: float                  # information coefficient = Pearson(feature, realized_r)
    n_total: int
    significant: bool = False  # set by apply_fdr_correction
    note: str = ""
    # Actionable cut-points for continuous features
    best_quartile_lo: Optional[float] = None
    best_quartile_hi: Optional[float] = None


# ---------------------------------------------------------------------------
# internal helpers
# ---------------------------------------------------------------------------

def _bucket_stats(label: str, grp: pd.DataFrame, r_col: str) -> Optional[BucketStats]:
    r = grp[r_col].dropna()
    if len(r) < 3:
        return None
    wins   = r[r > 0]
    losses = r[r < 0]
    gross_profit = float(wins.sum()) if len(wins) > 0 else 0.0
    gross_loss   = abs(float(losses.sum())) if len(losses) > 0 else 1e-9
    pf = gross_profit / gross_loss if gross_loss > 1e-9 else (float("inf") if gross_profit > 0 else 0.0)
    std_r  = float(r.std()) if len(r) > 1 else 0.0
    sharpe = float(r.mean() / std_r) if std_r > 0 else 0.0
    return BucketStats(
        label=label,
        n=len(r),
        win_rate=float(len(wins) / len(r)),
        avg_r=float(r.mean()),
        profit_factor=pf,
        avg_pnl_usd=float(grp["pnl"].mean()),
        std_r=std_r,
        sharpe=sharpe,
    )


# ---------------------------------------------------------------------------
# public test functions
# ---------------------------------------------------------------------------

def test_categorical(
    df: pd.DataFrame,
    feature_col: str,
    r_col: str = "realized_r",
    label_map: Optional[Dict] = None,
    min_n_per_bucket: int = 5,
) -> Optional[HypothesisResult]:
    """
    Kruskal-Wallis test across categories.
    Falls back to Mann-Whitney U when there are only 2 groups.
    """
    if feature_col not in df.columns:
        return None
    needed = [feature_col, r_col, "pnl"]
    sub = df[needed].dropna()
    if len(sub) < 20:
        return None

    groups = sub.groupby(feature_col)
    buckets: List[BucketStats] = []
    arrays: List[np.ndarray] = []

    for key, grp in groups:
        if len(grp) < min_n_per_bucket:
            continue
        label = label_map.get(key, str(key)) if label_map else str(key)
        b = _bucket_stats(label, grp, r_col)
        if b:
            buckets.append(b)
            arrays.append(grp[r_col].dropna().values)

    if len(arrays) < 2:
        return None

    # Statistical test
    if len(arrays) == 2:
        stat, p = stats.mannwhitneyu(arrays[0], arrays[1], alternative="two-sided")
        test_name = "Mann-Whitney U"
        n1, n2 = len(arrays[0]), len(arrays[1])
        effect = abs(1 - (2 * stat) / (n1 * n2)) if n1 * n2 > 0 else 0.0
    else:
        stat, p = stats.kruskal(*arrays)
        test_name = "Kruskal-Wallis"
        n_total = sum(len(a) for a in arrays)
        k = len(arrays)
        effect = max(0.0, (stat - k + 1) / (n_total - k)) if n_total > k else 0.0  # eta-squared

    # IC: max absolute Pearson correlation of one-hot dummies with outcome
    dummies = pd.get_dummies(sub[feature_col], drop_first=False)
    ic_vals = [abs(sub[r_col].corr(dummies[c])) for c in dummies.columns]
    ic = float(max(ic_vals)) if ic_vals else 0.0

    return HypothesisResult(
        feature=feature_col,
        feature_type="categorical",
        test_name=test_name,
        buckets=sorted(buckets, key=lambda b: b.avg_r, reverse=True),
        p_value=float(p),
        effect_size=float(effect),
        ic=ic,
        n_total=len(sub),
    )


def test_continuous(
    df: pd.DataFrame,
    feature_col: str,
    r_col: str = "realized_r",
    n_quantiles: int = 4,
    min_n_per_bucket: int = 5,
) -> Optional[HypothesisResult]:
    """
    Quartile-bucket analysis + Mann-Whitney U (Q1 vs Q4).
    Returns cut-points for the best-performing quartile.
    """
    if feature_col not in df.columns:
        return None
    needed = [feature_col, r_col, "pnl"]
    sub = df[needed].dropna()
    if len(sub) < 20:
        return None

    ic = float(sub[feature_col].corr(sub[r_col]))

    # Quantile buckets
    try:
        sub = sub.copy()
        sub["_q"], qbins = pd.qcut(
            sub[feature_col], q=n_quantiles, labels=False, duplicates="drop", retbins=True
        )
    except Exception as exc:
        logger.debug(f"qcut failed for {feature_col}: {exc}")
        return None

    buckets: List[BucketStats] = []
    arrays: List[np.ndarray] = []
    q_bounds: list = []

    for q_idx in sorted(sub["_q"].dropna().unique()):
        q_idx = int(q_idx)
        grp = sub[sub["_q"] == q_idx]
        if len(grp) < min_n_per_bucket:
            continue
        lo = float(qbins[q_idx])
        hi = float(qbins[q_idx + 1]) if q_idx + 1 < len(qbins) else float(sub[feature_col].max())
        label = f"Q{q_idx + 1} [{lo:.2f}–{hi:.2f}]"
        b = _bucket_stats(label, grp, r_col)
        if b:
            buckets.append(b)
            arrays.append(grp[r_col].dropna().values)
            q_bounds.append((lo, hi))

    if len(arrays) < 2:
        return None

    # Mann-Whitney U: bottom vs top quartile
    stat, p = stats.mannwhitneyu(arrays[0], arrays[-1], alternative="two-sided")
    n1, n2 = len(arrays[0]), len(arrays[-1])
    effect = abs(1 - (2 * stat) / (n1 * n2)) if n1 * n2 > 0 else 0.0

    # Best quartile (highest avg R)
    best_idx = int(np.argmax([b.avg_r for b in buckets]))
    best_lo, best_hi = q_bounds[best_idx]

    return HypothesisResult(
        feature=feature_col,
        feature_type="continuous",
        test_name=f"Mann-Whitney U (Q1 vs Q{len(arrays)})",
        buckets=buckets,  # kept in quantile order for the table
        p_value=float(p),
        effect_size=float(effect),
        ic=ic,
        n_total=len(sub),
        best_quartile_lo=best_lo,
        best_quartile_hi=best_hi,
    )


def apply_fdr_correction(
    results: List[HypothesisResult], alpha: float = 0.05
) -> List[HypothesisResult]:
    """
    Benjamini-Hochberg FDR correction.
    Sets HypothesisResult.significant = True for features that survive.
    """
    if not results:
        return results

    n = len(results)
    pvals = np.array([r.p_value for r in results])
    sorted_idx = np.argsort(pvals)
    thresholds = (np.arange(1, n + 1) / n) * alpha

    sig_mask = np.zeros(n, dtype=bool)
    for rank, idx in enumerate(sorted_idx):
        if pvals[idx] <= thresholds[rank]:
            sig_mask[sorted_idx[: rank + 1]] = True

    for i, r in enumerate(results):
        r.significant = bool(sig_mask[i])

    n_sig = int(sig_mask.sum())
    logger.info(f"FDR correction: {n_sig}/{n} features significant at α={alpha}")
    return results
