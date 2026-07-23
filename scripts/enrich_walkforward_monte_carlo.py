#!/usr/bin/env python3
"""Add or refresh Monte Carlo section on an existing walk-forward report directory.

Reads ``metrics_insights.json``, runs shuffle + bootstrap MC, writes
``monte_carlo.json`` and patches ``metrics.html`` (or rebuilds MC block).

Usage::

    .venv/bin/python scripts/enrich_walkforward_monte_carlo.py \\
        docs/perf/regime_longspan_450d \\
        --simulations 2000
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.backtest.recap_metrics import format_monte_carlo_html
from core.backtest.walkforward_monte_carlo import run_walkforward_monte_carlo


def _group_trades(trades: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for t in trades:
        st = str(t.get("strategy") or "—")
        sy = str(t.get("symbol") or "—")
        key = f"{st}|{sy}"
        groups.setdefault(key, []).append(t)
    return groups


def _group_by_strategy(trades: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    out: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for t in trades:
        out[str(t.get("strategy") or "—")].append(t)
    return dict(out)


def _slug(s: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower() or "x"


def _inject_mc_html(metrics_html: str, mc_block: str) -> str:
    """Replace existing MC section or insert before Analysis — all legs pooled."""
    pattern = re.compile(
        r"<h2>Monte Carlo robustness</h2>.*?(?=<h2>Analysis — all legs pooled</h2>)",
        re.DOTALL,
    )
    replacement = f"<h2>Monte Carlo robustness</h2>\n  {mc_block}\n  "
    if pattern.search(metrics_html):
        return pattern.sub(replacement, metrics_html, count=1)
    anchor = "<h2>Analysis — all legs pooled</h2>"
    if anchor in metrics_html:
        return metrics_html.replace(
            anchor,
            f"<h2>Monte Carlo robustness</h2>\n  {mc_block}\n  {anchor}",
            1,
        )
    return metrics_html + f"\n<h2>Monte Carlo robustness</h2>\n{mc_block}\n"


def _patch_nav(html: str) -> str:
    if "monte_carlo.json" in html:
        return html
    if "metrics_insights.json" in html and "Monte Carlo" not in html:
        html = html.replace(
            "metrics_insights.json",
            'metrics_insights.json</a> · <a href="monte_carlo.json">monte_carlo.json</a> · '
            '<a href="#monte-carlo">Monte Carlo ↓</a> · <a href="metrics_insights.json',
            1,
        )
    return html


def _run_mc(
    trades: List[Dict[str, Any]],
    *,
    start: float,
    simulations: int,
    seed: int,
    key: str = "",
) -> Dict[str, Any]:
    if len(trades) < 5:
        return {}
    return run_walkforward_monte_carlo(
        trades,
        start_equity=start,
        num_simulations=simulations,
        seed=seed + (hash(key) % 10000 if key else 0),
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="Enrich walk-forward dir with Monte Carlo")
    ap.add_argument("report_dir", type=Path, help="Directory with metrics_insights.json")
    ap.add_argument("--simulations", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--sim-start-cash", type=float, default=0.0, help="0 = read from insights JSON")
    args = ap.parse_args()

    insights_path = args.report_dir / "metrics_insights.json"
    if not insights_path.is_file():
        print(f"Missing {insights_path}", file=sys.stderr)
        return 1

    doc = json.loads(insights_path.read_text(encoding="utf-8"))
    trades = doc.get("trades_flat") or []
    start = float(args.sim_start_cash or doc.get("sim_start_equity") or 2000.0)

    if not trades:
        print("No trades in metrics_insights.json", file=sys.stderr)
        return 1

    print(f"Running MC on {len(trades)} trades ({args.simulations} sims × 2 modes)...", file=sys.stderr)

    mc_grand = _run_mc(trades, start=start, simulations=args.simulations, seed=args.seed)

    mc_per: Dict[str, Any] = {}
    for key, tlist in sorted(_group_trades(trades).items()):
        r = _run_mc(tlist, start=start, simulations=args.simulations, seed=args.seed, key=key)
        if r:
            mc_per[key] = r

    mc_strategy: Dict[str, Any] = {}
    for st, tlist in sorted(_group_by_strategy(trades).items()):
        r = _run_mc(tlist, start=start, simulations=args.simulations, seed=args.seed, key=f"strat:{st}")
        if r:
            mc_strategy[st] = r

    mc_html = format_monte_carlo_html(
        mc_grand, title="Monte Carlo — all legs pooled", chart_id_prefix="mc-grand",
    )

    if mc_strategy:
        mc_html += "\n<h2>Monte Carlo — per strategy</h2>\n"
        for st in sorted(mc_strategy.keys()):
            mc_html += format_monte_carlo_html(
                mc_strategy[st],
                title=f"Monte Carlo — {st}",
                chart_id_prefix=f"mc-strat-{_slug(st)}",
            )

    if mc_per:
        mc_html += "\n<h2>Monte Carlo — per strategy × symbol</h2>\n"
        for key in sorted(mc_per.keys()):
            st, sy = key.split("|", 1)
            mc_html += format_monte_carlo_html(
                mc_per[key],
                title=f"Monte Carlo — {st} · {sy}",
                chart_id_prefix=f"mc-{_slug(st)}-{_slug(sy)}",
            )

    mc_doc = {
        "sim_start_equity": start,
        "num_simulations": args.simulations,
        "seed": args.seed,
        "grand": mc_grand,
        "per_strategy": mc_strategy,
        "per_strategy_symbol": mc_per,
    }
    (args.report_dir / "monte_carlo.json").write_text(json.dumps(mc_doc, indent=2), encoding="utf-8")

    metrics_path = args.report_dir / "metrics.html"
    if metrics_path.is_file():
        html = metrics_path.read_text(encoding="utf-8")
        html = _inject_mc_html(html, mc_html)
        html = _patch_nav(html)
        metrics_path.write_text(html, encoding="utf-8")
        print(f"patched {metrics_path}", file=sys.stderr)

    index_path = args.report_dir / "index.html"
    if index_path.is_file():
        idx = index_path.read_text(encoding="utf-8")
        if "monte_carlo.json" not in idx:
            idx = idx.replace(
                '<a href="metrics.html">Metrics / survivability</a>',
                '<a href="metrics.html">Metrics / survivability</a>\n'
                '    <a href="metrics.html#monte-carlo">Monte Carlo</a>\n'
                '    <a href="monte_carlo.json">monte_carlo.json</a>',
                1,
            )
            index_path.write_text(idx, encoding="utf-8")
            print(f"patched {index_path}", file=sys.stderr)

    v = mc_grand.get("endurance_verdict") or {}
    print(
        f"Monte Carlo: {args.simulations} sims · grade={v.get('grade')} · "
        f"shuffle P(profit)={100 * float((mc_grand.get('modes') or {}).get('shuffle', {}).get('total_pnl', {}).get('p_profit') or 0):.1f}%",
        file=sys.stderr,
    )
    print(f"wrote {args.report_dir / 'monte_carlo.json'}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
