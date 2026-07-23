#!/usr/bin/env python3
"""Regime-aware performance breakdown from walk-forward trade recaps.

Reads ``metrics.json`` (per fold × strategy × symbol) and optional pooled trades
from ``metrics_insights.json`` or decision-cache payloads. Buckets realized PnL by
calendar month and flags the Jan-2026 regime shift called out in TODO notes.

Uses ``core.regime`` labels (trend / chop / mixed) when OHLCV CSVs are available;
otherwise reports month buckets only.

Example::

  .venv/bin/python scripts/regime_performance_report.py \\
    --walkforward-dir docs/perf/first_june_week \\
    --csv-dir historical_data/price \\
    --out docs/perf/first_june_week/regime_performance.html
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.metrics_glossary import th
from core.regime import RegimeConfig, classify


def _parse_iso(s: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _month_key(dt: datetime) -> str:
    return f"{dt.year:04d}-{dt.month:02d}"


def load_trades_from_insights(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    doc = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(doc.get("trades_flat"), list):
        return list(doc["trades_flat"])
    trades: List[Dict[str, Any]] = []
    for block in doc.values():
        if isinstance(block, dict) and isinstance(block.get("trades"), list):
            trades.extend(block["trades"])
    return trades


def bucket_trades_by_month_strategy(
    trades: List[Dict[str, Any]],
) -> Dict[str, Dict[str, Dict[str, float]]]:
    """``{strategy: {month: {n, pnl}}}``."""
    out: Dict[str, Dict[str, Dict[str, float]]] = defaultdict(
        lambda: defaultdict(lambda: {"n": 0.0, "pnl": 0.0})
    )
    for t in trades:
        xt = _parse_iso(str(t.get("exit_time") or t.get("entry_time") or ""))
        if not xt:
            continue
        strat = str(t.get("strategy") or t.get("_strategy") or "unknown")
        mk = _month_key(xt.astimezone(timezone.utc))
        out[strat][mk]["n"] += 1.0
        out[strat][mk]["pnl"] += float(t.get("pnl") or 0)
    return {s: dict(months) for s, months in out.items()}


def bucket_trades_by_month_strategy_symbol(
    trades: List[Dict[str, Any]],
) -> Dict[str, Dict[str, Dict[str, Dict[str, float]]]]:
    """``{strategy: {symbol: {month: {n, pnl}}}}``."""
    out: Dict[str, Dict[str, Dict[str, Dict[str, float]]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(lambda: {"n": 0.0, "pnl": 0.0}))
    )
    for t in trades:
        xt = _parse_iso(str(t.get("exit_time") or t.get("entry_time") or ""))
        if not xt:
            continue
        strat = str(t.get("strategy") or t.get("_strategy") or "unknown")
        sym = str(t.get("symbol") or t.get("_symbol") or "").upper()
        mk = _month_key(xt.astimezone(timezone.utc))
        out[strat][sym][mk]["n"] += 1.0
        out[strat][sym][mk]["pnl"] += float(t.get("pnl") or 0)
    return {s: {sym: dict(months) for sym, months in syms.items()} for s, syms in out.items()}


def bucket_trades_by_regime_strategy(
    trades: List[Dict[str, Any]],
    bars_by_symbol: Dict[str, List[Dict[str, Any]]],
    cfg: RegimeConfig,
) -> Dict[str, Dict[str, Dict[str, float]]]:
    """``{strategy: {regime: {n, pnl}}}``."""
    out: Dict[str, Dict[str, Dict[str, float]]] = defaultdict(
        lambda: defaultdict(lambda: {"n": 0.0, "pnl": 0.0})
    )
    for t in trades:
        strat = str(t.get("strategy") or t.get("_strategy") or "unknown")
        lbl = regime_label_for_trade(t, bars_by_symbol, cfg)
        out[strat][lbl]["n"] += 1.0
        out[strat][lbl]["pnl"] += float(t.get("pnl") or 0)
    return {s: dict(regs) for s, regs in out.items()}


def fold_rows_from_metrics(metrics_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows = []
    for m in metrics_rows:
        if not m.get("ok"):
            continue
        avg_b = m.get("avg_bars_held")
        rows.append({
            "fold": m.get("fold"),
            "fold_start": m.get("fold_start"),
            "fold_end": m.get("fold_end"),
            "strategy": m.get("strategy"),
            "symbol": m.get("symbol"),
            "n": int(m.get("n_trades") or 0),
            "pnl": float(m.get("total_pnl") or 0),
            "avg_bars": float(avg_b) if avg_b is not None else None,
            "win_rate": float(m.get("win_rate") or 0),
            "max_dd": float(m.get("max_drawdown") or 0),
        })
    return rows


def pnl_for_fold_boundary(
    metrics_rows: List[Dict[str, Any]],
    *,
    strategy: Optional[str] = None,
    symbol: Optional[str] = None,
    before: Optional[str] = None,
    after: Optional[str] = None,
) -> Tuple[float, int]:
    """Sum PnL for folds whose ``fold_start`` is before/after a YYYY-MM-DD cutoff."""
    pnl = 0.0
    n = 0
    for m in metrics_rows:
        if not m.get("ok"):
            continue
        if strategy and m.get("strategy") != strategy:
            continue
        if symbol and str(m.get("symbol", "")).upper() != symbol.upper():
            continue
        fs = str(m.get("fold_start") or "")
        if before and fs >= before:
            continue
        if after and fs < after:
            continue
        pnl += float(m.get("total_pnl") or 0)
        n += int(m.get("n_trades") or 0)
    return pnl, n


def bucket_metrics_by_month(metrics_rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, float]]:
    """Approximate month buckets from fold midpoints (when per-trade list unavailable)."""
    out: Dict[str, Dict[str, float]] = defaultdict(lambda: {"n": 0.0, "pnl": 0.0})
    for row in metrics_rows:
        if not row.get("ok"):
            continue
        fs = _parse_iso(str(row.get("fold_start", "")))
        fe = _parse_iso(str(row.get("fold_end", "")))
        if not fs or not fe:
            continue
        mid = fs + (fe - fs) / 2
        mk = _month_key(mid.astimezone(timezone.utc))
        out[mk]["n"] += float(row.get("n_trades") or 0)
        out[mk]["pnl"] += float(row.get("total_pnl") or 0)
    return dict(out)


def bucket_trades_by_month(trades: List[Dict[str, Any]]) -> Dict[str, Dict[str, float]]:
    out: Dict[str, Dict[str, float]] = defaultdict(lambda: {"n": 0.0, "pnl": 0.0})
    for t in trades:
        xt = _parse_iso(str(t.get("exit_time") or t.get("entry_time") or ""))
        if not xt:
            continue
        mk = _month_key(xt.astimezone(timezone.utc))
        out[mk]["n"] += 1.0
        out[mk]["pnl"] += float(t.get("pnl") or 0)
    return dict(out)


def regime_label_for_trade(
    trade: Dict[str, Any],
    bars_by_symbol: Dict[str, List[Dict[str, Any]]],
    cfg: RegimeConfig,
) -> str:
    sym = str(trade.get("symbol", "")).upper()
    bars = bars_by_symbol.get(sym) or []
    if len(bars) < 50:
        return "mixed"
    et = _parse_iso(str(trade.get("entry_time") or ""))
    if not et:
        return "mixed"
    ts = et.timestamp()
    window = [b for b in bars if float(b.get("_ts", 0)) <= ts][-cfg.ker_lookback :]
    if len(window) < cfg.ker_lookback:
        return "mixed"
    return classify(window, cfg).label


def load_bars_csv(csv_dir: Path, symbol: str, timeframe: str = "5m") -> List[Dict[str, Any]]:
    path = csv_dir / f"{symbol}_{timeframe}_databento.csv"
    if not path.exists():
        return []
    try:
        import pandas as pd

        df = pd.read_csv(path)
        if "timestamp" not in df.columns and "time" in df.columns:
            df = df.rename(columns={"time": "timestamp"})
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        df = df.sort_values("timestamp")
        out: List[Dict[str, Any]] = []
        for _, row in df.iterrows():
            out.append(
                {
                    "high": float(row.get("high", 0)),
                    "low": float(row.get("low", 0)),
                    "close": float(row.get("close", 0)),
                    "_ts": row["timestamp"].timestamp(),
                }
            )
        return out
    except Exception:
        return []


def _table(headers: List[str], rows: List[List[str]], *, numeric_cols: Optional[set] = None) -> str:
    num = numeric_cols or set()
    head = "".join(
        th("col", h, num=(i in num)) if i in num else f"<th>{h}</th>"
        for i, h in enumerate(headers)
    )
    body = ""
    for row in rows:
        tds = []
        for i, cell in enumerate(row):
            cls = ' class="num"' if i in num else ""
            tds.append(f"<td{cls}>{cell}</td>")
        body += "<tr>" + "".join(tds) + "</tr>"
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body or '<tr><td colspan=99>No data</td></tr>'}</tbody></table>"


def render_html(
    *,
    month_rows: List[Tuple[str, float, float]],
    regime_rows: List[Tuple[str, float, float]],
    pre26_pnl: float,
    post26_pnl: float,
    strategies: List[str],
    strat_month_rows: Dict[str, List[Tuple[str, float, float]]],
    strat_sym_month_rows: Dict[str, Dict[str, List[Tuple[str, float, float]]]],
    strat_regime_rows: Dict[str, List[Tuple[str, float, float]]],
    fold_rows: List[Dict[str, Any]],
    boundaries: Dict[str, Any],
    high_bars_neg_folds: List[Dict[str, Any]],
) -> str:
    month_tr = "".join(
        f"<tr><td>{mk}</td><td class='num'>{int(n)}</td><td class='num'>{pnl:.2f}</td></tr>"
        for mk, n, pnl in month_rows
    )
    regime_tr = "".join(
        f"<tr><td>{lbl}</td><td class='num'>{int(n)}</td><td class='num'>{pnl:.2f}</td></tr>"
        for lbl, n, pnl in regime_rows
    )
    strat_month_html = ""
    for strat in sorted(strat_month_rows.keys()):
        rows = strat_month_rows[strat]
        tr = "".join(
            f"<tr><td>{mk}</td><td class='num'>{int(n)}</td><td class='num'>{pnl:.2f}</td></tr>"
            for mk, n, pnl in rows
        )
        strat_month_html += f"<h3>{strat}</h3><table><thead><tr>{th('start', 'month')}{th('n', 'n', num=True)}{th('pnl', 'PnL', num=True)}</tr></thead><tbody>{tr}</tbody></table>"

    strat_sym_html = ""
    for strat in sorted(strat_sym_month_rows.keys()):
        strat_sym_html += f"<h3>{strat}</h3>"
        for sym in sorted(strat_sym_month_rows[strat].keys()):
            rows = strat_sym_month_rows[strat][sym]
            tr = "".join(
                f"<tr><td>{mk}</td><td class='num'>{int(n)}</td><td class='num'>{pnl:.2f}</td></tr>"
                for mk, n, pnl in rows
            )
            strat_sym_html += f"<h4>{sym}</h4><table><thead><tr>{th('start', 'month')}{th('n', 'n', num=True)}{th('pnl', 'PnL', num=True)}</tr></thead><tbody>{tr}</tbody></table>"

    strat_regime_html = ""
    for strat in sorted(strat_regime_rows.keys()):
        tr = "".join(
            f"<tr><td>{lbl}</td><td class='num'>{int(n)}</td><td class='num'>{pnl:.2f}</td></tr>"
            for lbl, n, pnl in strat_regime_rows[strat]
        )
        strat_regime_html += f"<h3>{strat}</h3><table><thead><tr>{th('strategy', 'regime')}{th('n', 'n', num=True)}{th('pnl', 'PnL', num=True)}</tr></thead><tbody>{tr}</tbody></table>"

    fold_tr = ""
    for fr in fold_rows:
        avg_s = f"{fr['avg_bars']:.1f}" if fr.get("avg_bars") is not None else "—"
        flag = ""
        if fr.get("avg_bars") is not None and fr["avg_bars"] >= 15 and fr["pnl"] < 0:
            flag = ' style="background:rgba(240,128,128,.12)"'
        fold_tr += (
            f"<tr{flag}><td>{fr['fold']}</td><td>{fr['fold_start']}</td><td>{fr['fold_end']}</td>"
            f"<td><code>{fr['strategy']}</code></td><td><code>{fr['symbol']}</code></td>"
            f"<td class='num'>{fr['n']}</td><td class='num'>{fr['pnl']:.2f}</td>"
            f"<td class='num'>{100*fr['win_rate']:.0f}%</td><td class='num'>{avg_s}</td>"
            f"<td class='num'>{fr['max_dd']:.2f}</td></tr>"
        )

    b = boundaries
    bound_html = (
        f"<li>All strategies — pre-2026: <strong>{b['pre_2026']['pnl']:.2f}</strong> "
        f"({b['pre_2026']['n']} trades) · 2026+: <strong>{b['post_2026']['pnl']:.2f}</strong> "
        f"({b['post_2026']['n']} trades)</li>"
        f"<li>All strategies — pre-Oct-2025: <strong>{b['pre_oct2025']['pnl']:.2f}</strong> "
        f"({b['pre_oct2025']['n']} trades) · Oct-2025+: <strong>{b['post_oct2025']['pnl']:.2f}</strong> "
        f"({b['post_oct2025']['n']} trades)</li>"
    )
    for key, label in (
        ("mrr_pre_jan14", "MRR pre-2026-01-14"),
        ("mrr_post_jan14", "MRR 2026-01-14+"),
        ("mrr_mgc_pre_jan14", "MRR·MGC pre-2026-01-14"),
        ("mrr_mgc_post_jan14", "MRR·MGC 2026-01-14+"),
        ("or_pre_oct2025", "OR pre-2025-10-01"),
        ("or_post_oct2025", "OR 2025-10-01+"),
    ):
        if key in b:
            bound_html += (
                f"<li>{label}: <strong>{b[key]['pnl']:.2f}</strong> ({b[key]['n']} trades)</li>"
            )

    warn_html = ""
    if high_bars_neg_folds:
        warn_html = "<h2>High avg-bars + negative PnL folds</h2><ul>"
        for fr in high_bars_neg_folds:
            warn_html += (
                f"<li>fold {fr['fold']} {fr['strategy']} {fr['symbol']}: "
                f"avg_bars={fr['avg_bars']:.1f} pnl={fr['pnl']:.2f}</li>"
            )
        warn_html += "</ul>"

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"/>
<title>Regime performance</title>
<style>
body {{ font-family: system-ui, sans-serif; background: #121218; color: #e8e8ef; padding: 1.5rem; max-width: 90rem; }}
table {{ border-collapse: collapse; width: 100%; margin: 1rem 0; font-size: 0.85rem; }}
th, td {{ border: 1px solid #333; padding: 0.45rem 0.6rem; }}
th {{ background: #1e1e28; }}
.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
.muted {{ color: #9ca3af; max-width: 52rem; }}
.banner {{ background: #1e1e28; border: 1px solid #333; padding: 0.75rem 1rem; border-radius: 6px; margin: 1rem 0; }}
h2 {{ margin-top: 2rem; color: #c9b87c; font-size: 1.05rem; }}
h3 {{ margin-top: 1.25rem; font-size: 0.95rem; color: #d8d8e0; }}
h4 {{ margin: 0.5rem 0; font-size: 0.85rem; color: #9ca3af; }}
code {{ background: #1e1e28; padding: 0.1rem 0.3rem; border-radius: 3px; }}
</style></head><body>
<h1>Regime &amp; calendar performance</h1>
<p class="muted">Strategies: {', '.join(strategies)}. Regime labels from <code>core/regime.py</code>
 (KER + ADX at trade entry). Boundaries: Jan-2026 and Oct-2025 OR shift from TODO notes;
 MGC Jan-2026 called out separately.</p>
<div class="banner"><strong>Boundary PnL (fold-start attribution)</strong><ul>{bound_html}</ul></div>
<div class="banner">
  <strong>Exit-month split:</strong> pre-2026 <strong>{pre26_pnl:.2f}</strong>
  · 2026+ <strong>{post26_pnl:.2f}</strong>
</div>
{warn_html}
<h2>Per fold (avg bars vs PnL)</h2>
<p class="muted">Rows highlighted when avg bars ≥ 15 and fold PnL &lt; 0 (timeout/chop drag pattern).</p>
<table><thead><tr>
{th('fold', 'fold')}{th('start', 'start')}{th('end', 'end')}{th('strategy', 'strategy')}
{th('sym', 'sym')}{th('n', 'n', num=True)}{th('pnl', 'PnL', num=True)}{th('wr', 'WR', num=True)}
{th('avg_bars', 'avg bars', num=True)}{th('maxdd', 'max DD', num=True)}
</tr></thead><tbody>{fold_tr or '<tr><td colspan=10>No data</td></tr>'}</tbody></table>
<h2>By exit month (all)</h2>
<table><thead><tr>{th('start', 'month')}{th('n', 'n', num=True)}{th('pnl', 'PnL', num=True)}</tr></thead>
<tbody>{month_tr or '<tr><td colspan=3>No data</td></tr>'}</tbody></table>
<h2>By strategy × month</h2>
{strat_month_html or '<p class="muted">No trade-level month data</p>'}
<h2>By strategy × symbol × month</h2>
{strat_sym_html or '<p class="muted">No trade-level data</p>'}
<h2>By regime at entry (all)</h2>
<table><thead><tr>{th('strategy', 'regime')}{th('n', 'n', num=True)}{th('pnl', 'PnL', num=True)}</tr></thead>
<tbody>{regime_tr or '<tr><td colspan=3>No trade-level regime data</td></tr>'}</tbody></table>
<h2>By strategy × regime</h2>
{strat_regime_html or '<p class="muted">No regime breakdown</p>'}
<p class="muted"><a href="metrics.html">metrics.html</a> · <a href="index.html">report hub</a></p>
</body></html>"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--walkforward-dir", type=Path, required=True)
    ap.add_argument("--csv-dir", type=Path, default=ROOT / "historical_data" / "price")
    ap.add_argument("--out", type=Path, default=None, help="HTML output (default: <dir>/regime_performance.html)")
    ap.add_argument(
        "--high-bars-threshold",
        type=float,
        default=15.0,
        help="Flag folds with avg_bars_held >= this and negative PnL",
    )
    args = ap.parse_args()

    wf = args.walkforward_dir if args.walkforward_dir.is_absolute() else ROOT / args.walkforward_dir
    metrics_path = wf / "metrics.json"
    if not metrics_path.exists():
        print(f"error: missing {metrics_path}", file=sys.stderr)
        return 2
    metrics_rows = json.loads(metrics_path.read_text(encoding="utf-8"))
    trades = load_trades_from_insights(wf / "metrics_insights.json")

    month_buckets = bucket_trades_by_month(trades) if trades else bucket_metrics_by_month(metrics_rows)
    month_rows = sorted(
        ((mk, v["n"], v["pnl"]) for mk, v in month_buckets.items()),
        key=lambda x: x[0],
    )

    pre26 = sum(v["pnl"] for mk, v in month_buckets.items() if mk < "2026-01")
    post26 = sum(v["pnl"] for mk, v in month_buckets.items() if mk >= "2026-01")

    regime_buckets: Dict[str, Dict[str, float]] = defaultdict(lambda: {"n": 0.0, "pnl": 0.0})
    cfg = RegimeConfig()
    symbols = sorted({str(r.get("symbol", "")).upper() for r in metrics_rows if r.get("ok")})
    bars_by_sym = {s: load_bars_csv(args.csv_dir, s) for s in symbols if s}

    strat_month_rows: Dict[str, List[Tuple[str, float, float]]] = {}
    strat_sym_month_rows: Dict[str, Dict[str, List[Tuple[str, float, float]]]] = {}
    strat_regime_rows: Dict[str, List[Tuple[str, float, float]]] = {}

    if trades:
        by_strat_month = bucket_trades_by_month_strategy(trades)
        for strat, months in by_strat_month.items():
            strat_month_rows[strat] = sorted(
                ((mk, v["n"], v["pnl"]) for mk, v in months.items()),
                key=lambda x: x[0],
            )
        by_ssm = bucket_trades_by_month_strategy_symbol(trades)
        for strat, syms in by_ssm.items():
            strat_sym_month_rows[strat] = {}
            for sym, months in syms.items():
                strat_sym_month_rows[strat][sym] = sorted(
                    ((mk, v["n"], v["pnl"]) for mk, v in months.items()),
                    key=lambda x: x[0],
                )
        if bars_by_sym:
            by_strat_reg = bucket_trades_by_regime_strategy(trades, bars_by_sym, cfg)
            for strat, regs in by_strat_reg.items():
                strat_regime_rows[strat] = sorted(
                    ((lbl, v["n"], v["pnl"]) for lbl, v in regs.items()),
                    key=lambda x: x[0],
                )
            for t in trades:
                lbl = regime_label_for_trade(t, bars_by_sym, cfg)
                regime_buckets[lbl]["n"] += 1.0
                regime_buckets[lbl]["pnl"] += float(t.get("pnl") or 0)

    regime_rows = sorted(
        ((lbl, v["n"], v["pnl"]) for lbl, v in regime_buckets.items()),
        key=lambda x: x[0],
    )
    strategies = sorted({str(r.get("strategy", "")) for r in metrics_rows if r.get("strategy")})

    fold_rows = fold_rows_from_metrics(metrics_rows)
    high_bars_neg = [
        fr for fr in fold_rows
        if fr.get("avg_bars") is not None
        and fr["avg_bars"] >= args.high_bars_threshold
        and fr["pnl"] < 0
        and fr["n"] > 0
    ]

    def _bnd(key_pnl: Tuple[float, int]) -> Dict[str, float]:
        return {"pnl": key_pnl[0], "n": key_pnl[1]}

    boundaries: Dict[str, Any] = {
        "pre_2026": _bnd(pnl_for_fold_boundary(metrics_rows, before="2026-01-01")),
        "post_2026": _bnd(pnl_for_fold_boundary(metrics_rows, after="2026-01-01")),
        "pre_oct2025": _bnd(pnl_for_fold_boundary(metrics_rows, before="2025-10-01")),
        "post_oct2025": _bnd(pnl_for_fold_boundary(metrics_rows, after="2025-10-01")),
        "mrr_pre_jan14": _bnd(pnl_for_fold_boundary(
            metrics_rows, strategy="morning_range_reversion", before="2026-01-14",
        )),
        "mrr_post_jan14": _bnd(pnl_for_fold_boundary(
            metrics_rows, strategy="morning_range_reversion", after="2026-01-14",
        )),
        "mrr_mgc_pre_jan14": _bnd(pnl_for_fold_boundary(
            metrics_rows, strategy="morning_range_reversion", symbol="MGC", before="2026-01-14",
        )),
        "mrr_mgc_post_jan14": _bnd(pnl_for_fold_boundary(
            metrics_rows, strategy="morning_range_reversion", symbol="MGC", after="2026-01-14",
        )),
        "or_pre_oct2025": _bnd(pnl_for_fold_boundary(
            metrics_rows, strategy="overnight_range", before="2025-10-01",
        )),
        "or_post_oct2025": _bnd(pnl_for_fold_boundary(
            metrics_rows, strategy="overnight_range", after="2025-10-01",
        )),
    }

    out_path = args.out or (wf / "regime_performance.html")
    if not out_path.is_absolute():
        out_path = ROOT / out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    html = render_html(
        month_rows=month_rows,
        regime_rows=regime_rows,
        pre26_pnl=pre26,
        post26_pnl=post26,
        strategies=strategies,
        strat_month_rows=strat_month_rows,
        strat_sym_month_rows=strat_sym_month_rows,
        strat_regime_rows=strat_regime_rows,
        fold_rows=fold_rows,
        boundaries=boundaries,
        high_bars_neg_folds=high_bars_neg,
    )
    out_path.write_text(html, encoding="utf-8")
    json_path = out_path.with_suffix(".json")
    json_path.write_text(
        json.dumps(
            {
                "month_buckets": month_buckets,
                "regime_buckets": regime_buckets,
                "strat_month": strat_month_rows,
                "strat_sym_month": strat_sym_month_rows,
                "strat_regime": strat_regime_rows,
                "fold_rows": fold_rows,
                "high_bars_neg_folds": high_bars_neg,
                "boundaries": boundaries,
                "pre_2026_pnl": pre26,
                "post_2026_pnl": post26,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"wrote {out_path}", file=sys.stderr)
    print(f"wrote {json_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
