#!/usr/bin/env python3
"""Print aggregate stats from walkforward ``summary.tsv`` (trade-weighted win rate, etc.)."""

from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from pathlib import Path
from typing import Any, DefaultDict, Dict, List, Tuple


def load_rows(path: Path) -> List[Dict[str, Any]]:
    with path.open(encoding="utf-8") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def summarize(rows: List[Dict[str, Any]]) -> List[Tuple[Tuple[str, str], Dict[str, float]]]:
    """Return sorted list of ((strategy, symbol), metrics) by sum_pnl desc."""
    agg: DefaultDict[Tuple[str, str], Dict[str, float]] = defaultdict(
        lambda: {
            "sum_pnl": 0.0,
            "sum_trades": 0.0,
            "wr_num": 0.0,  # sum(win_rate_pct * trades); win_rate in TSV is 0-100
            "sharpe_sum": 0.0,
            "folds": 0.0,
            "folds_pos": 0.0,
        }
    )
    for r in rows:
        k = (r["strategy"], r["symbol"])
        tr = float(r["total_trades"] or 0)
        pnl = float(r["total_pnl"] or 0)
        wr = float(r["win_rate"] or 0)
        sh = float(r["sharpe_ratio"] or 0)
        a = agg[k]
        a["sum_pnl"] += pnl
        a["sum_trades"] += tr
        a["wr_num"] += wr * tr
        a["sharpe_sum"] += sh
        a["folds"] += 1
        if pnl > 0:
            a["folds_pos"] += 1
    out: List[Tuple[Tuple[str, str], Dict[str, float]]] = []
    for k, a in agg.items():
        st = a["sum_trades"]
        w_avg = (a["wr_num"] / st) if st > 0 else 0.0
        mean_sh = a["sharpe_sum"] / max(1.0, a["folds"])
        out.append(
            (
                k,
                {
                    "sum_pnl": a["sum_pnl"],
                    "sum_trades": st,
                    "weighted_win_rate_pct": w_avg,
                    "mean_fold_sharpe": mean_sh,
                    "folds_green": a["folds_pos"],
                    "folds": a["folds"],
                },
            )
        )
    out.sort(key=lambda x: x[1]["sum_pnl"], reverse=True)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("tsv", type=Path, help="Path to summary.tsv")
    ap.add_argument("--label", type=str, default="", help="Prefix column for batch compare")
    args = ap.parse_args()
    rows = load_rows(args.tsv)
    label = args.label or args.tsv.parent.name
    print(f"# {label}\t{args.tsv}")
    print(
        "strategy\tsymbol\tsum_pnl\ttotal_trades\tweighted_wr_pct\tmean_fold_sharpe\tfolds_green"
    )
    for (st, sy), m in summarize(rows):
        fg = f"{int(m['folds_green'])}/{int(m['folds'])}"
        print(
            f"{st}\t{sy}\t{m['sum_pnl']:.2f}\t{int(m['sum_trades'])}\t"
            f"{m['weighted_win_rate_pct']:.2f}\t{m['mean_fold_sharpe']:.3f}\t{fg}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
