#!/usr/bin/env python3
"""BONGO portfolio replay: merged equity curve from multiple strategy×symbol legs.

Runs ``core/backtest_executor.py --replay --format=json --include-trades`` per leg
on the **same** calendar folds (walk-forward segments), merges all completed trades
by ``exit_time``, and builds a **single** cumulative PnL / equity series (one shared
``initial_capital`` pool — independent replays summed in time order; not a margin sim).

Default legs (partial TP **only** on ``overnight_range`` + ``body_reversion``; morning
uses 1-lot, no partial; symbols per BONGO portfolio slice):

  - overnight_range: MNQ, MES, MGC
  - body_reversion: MNQ, MGC
  - morning_range_reversion: MNQ, MGC

Use ``--strategies overnight_range,morning_range_reversion`` to run **overnight + morning only**
(five legs: MNQ/MES/MGC overnight + MNQ/MGC morning), e.g. to skip **body_reversion** in a walkthrough.
Use ``--strategies body_reversion,morning_range_reversion`` for **body + morning only** (four legs).

Morning signal stress matches the high-WR replay recipe:
``REQUIRE_HIGH_ATR=true``, ``TP_MULT=0.7``.

Outputs under ``--out-dir`` (default ``docs/perf/bongo_portfolio_walkforward_equity``):

  - ``portfolio_summary.json`` — legs, folds, merged trade count, final equity, WR
  - ``merged_trades.json`` — sorted trades with ``strategy`` tag
  - ``portfolio_equity.html`` — Chart.js equity + trade table

Example (full seven-leg book)::

  ENABLE_SIGNALR=false .venv/bin/python scripts/bongo_portfolio_walkforward_equity.py \\
    --days 100 --folds 5 --timeframe 5m

Body + morning only (no ``overnight_range``) — same MNQ/MGC body/morning legs::

  ENABLE_SIGNALR=false .venv/bin/python scripts/bongo_portfolio_walkforward_equity.py \\
    --strategies body_reversion,morning_range_reversion \\
    --days 100 --folds 5 --timeframe 5m \\
    --out-dir docs/perf/bongo_portfolio_body_morning_walkthrough

Overnight + morning only (no ``body_reversion``) — overnight MNQ/MES/MGC + morning MNQ/MGC::

  ENABLE_SIGNALR=false .venv/bin/python scripts/bongo_portfolio_walkforward_equity.py \\
    --strategies overnight_range,morning_range_reversion \\
    --days 100 --folds 5 --timeframe 5m \\
    --out-dir docs/perf/bongo_portfolio_overnight_morning_walkthrough
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent


def _overnight_range_mgc_toml_snapshot() -> Dict[str, Any]:
    """Record ``[symbols.MGC.*]`` from shipped TOML so summaries stay reproducible."""
    p = ROOT / "config" / "strategies" / "overnight_range.toml"
    out: Dict[str, Any] = {"config_path": str(p.relative_to(ROOT)) if p.is_file() else None}
    if not p.is_file():
        return out
    import tomllib

    with p.open("rb") as fh:
        data = tomllib.load(fh)
    sym = data.get("symbols") or {}
    mgc = sym.get("MGC") or sym.get("mgc")
    if isinstance(mgc, dict):
        out["MGC"] = mgc
    return out


# (strategy_id, symbol) — top book from BONGO competition slice; body/morning MNQ+MGC only.
DEFAULT_LEGS: Tuple[Tuple[str, str], ...] = (
    ("overnight_range", "MNQ"),
    ("overnight_range", "MES"),
    ("overnight_range", "MGC"),
    ("body_reversion", "MNQ"),
    ("body_reversion", "MGC"),
    ("morning_range_reversion", "MNQ"),
    ("morning_range_reversion", "MGC"),
)

_VALID_STRATEGY_IDS = frozenset({s for s, _ in DEFAULT_LEGS})


def _legs_for_strategies_arg(strategies_csv: str) -> List[Tuple[str, str]]:
    """Return legs to run. Empty ``strategies_csv`` = full ``DEFAULT_LEGS``."""
    raw = (strategies_csv or "").strip()
    if not raw:
        return list(DEFAULT_LEGS)
    want_lower = {p.strip().lower() for p in raw.split(",") if p.strip()}
    if not want_lower:
        return list(DEFAULT_LEGS)
    id_by_lower = {s.lower(): s for s in _VALID_STRATEGY_IDS}
    unknown = sorted(w for w in want_lower if w not in id_by_lower)
    if unknown:
        print(
            f"error: unknown --strategies id(s): {unknown}. "
            f"Valid: {', '.join(sorted(_VALID_STRATEGY_IDS))}",
            file=sys.stderr,
        )
        raise SystemExit(2)
    resolved = {id_by_lower[w] for w in want_lower}
    out = [(s, y) for s, y in DEFAULT_LEGS if s in resolved]
    if not out:
        print("error: --strategies produced no legs", file=sys.stderr)
        raise SystemExit(2)
    return out


def _csv_last_date(csv_path: Path) -> date:
    import pandas as pd

    df = pd.read_csv(csv_path)
    ts_col = next(
        c for c in df.columns if str(c).lower() in ("timestamp", "time", "date", "datetime")
    )
    ts = pd.to_datetime(df[ts_col].iloc[-1])
    if getattr(ts, "tz", None) is not None:
        ts = ts.tz_convert("UTC").tz_localize(None)
    return pd.Timestamp(ts).date()


def _fold_ranges(
    anchor_end: date, *, total_days: int, folds: int
) -> List[Tuple[date, date, int]]:
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


def _parse_ts(raw: Any) -> datetime:
    if isinstance(raw, datetime):
        dt = raw
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    s = str(raw or "").strip()
    if not s:
        return datetime.min.replace(tzinfo=timezone.utc)
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _portfolio_env() -> Dict[str, str]:
    """Env passed to every subprocess; strategy-specific keys only affect matching strategy."""
    return {
        "ENABLE_SIGNALR": "false",
        "PYTHONUNBUFFERED": "1",
        # Morning — high WR recipe; **no** partial TP, 1 lot
        "MORNING_RANGE_REVERSION_SIGNAL_REQUIRE_HIGH_ATR": "true",
        "MORNING_RANGE_REVERSION_SIGNAL_TP_MULT": "0.7",
        "MORNING_RANGE_REVERSION_SIGNAL_PARTIAL_TP_ENABLED": "false",
        "MORNING_RANGE_REVERSION_RISK_POSITION_SIZE": "1",
        # Body + overnight — partial TP replay path (qty >= 2)
        "BODY_REVERSION_SIGNAL_PARTIAL_TP_ENABLED": "true",
        "BODY_REVERSION_RISK_POSITION_SIZE": "2",
        "OVERNIGHT_RANGE_SIGNAL_PARTIAL_TP_ENABLED": "true",
        "OVERNIGHT_RANGE_RISK_POSITION_SIZE": "2",
    }


async def _run_leg_json(
    *,
    strategy: str,
    symbol: str,
    csv_path: Path,
    start: date,
    end: date,
    timeframe: str,
    env: Dict[str, str],
) -> Dict[str, Any]:
    full_env = os.environ.copy()
    full_env.update(env)
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
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(ROOT),
        env=full_env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    text = (stdout or b"").decode("utf-8", errors="replace").strip()
    if not text:
        return {
            "ok": False,
            "error": "empty stdout",
            "strategy": strategy,
            "symbol": symbol,
            "stderr": (stderr or b"").decode("utf-8", errors="replace")[-2000:],
        }
    try:
        line = text.splitlines()[-1]
        d = json.loads(line)
    except json.JSONDecodeError as e:
        return {
            "ok": False,
            "error": str(e),
            "strategy": strategy,
            "symbol": symbol,
            "tail": text[-1500:],
        }
    d["_strategy"] = strategy
    d["_symbol"] = symbol
    return d


def _extract_trades(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    if not payload.get("ok", True):
        return []
    res = payload.get("result") or {}
    trades = res.get("trades") or []
    out: List[Dict[str, Any]] = []
    for t in trades:
        if not isinstance(t, dict):
            continue
        out.append(t)
    return out


def _max_drawdown(equity: List[float]) -> Tuple[float, float]:
    peak = float("-inf")
    max_dd = 0.0
    max_dd_pct = 0.0
    for eq in equity:
        peak = max(peak, eq)
        dd = peak - eq
        max_dd = max(max_dd, dd)
        if peak > 0:
            max_dd_pct = max(max_dd_pct, 100.0 * dd / peak)
    return max_dd, max_dd_pct


def _html_risk_note(overnight_risk_note: bool) -> str:
    if overnight_risk_note:
        return (
            '<p class="note"><strong>Overnight risk $:</strong> bracket SL = entry ± (15m ATR × '
            '<code>stop_atr_multiplier</code>); MGC = <strong>$10/pt/contract</strong>. Per-symbol TOML '
            '<code>[symbols.MGC]</code> can cap qty / tighten stop vs MNQ/MES; body legs may still use env '
            '2-lot + partial TP. Use the <strong>qty</strong> / <strong>risk $</strong> columns and '
            '<code>docs/BONGO.md</code> (overnight stop / risk $ section).</p>'
        )
    return (
        '<p class="note">This run <strong>excludes overnight_range</strong> (body + morning only). '
        'Body uses partial TP at 2 lots per env; morning is 1 lot with the high-ATR + <code>TP_MULT=0.7</code> '
        'recipe — see <code>docs/BONGO.md</code> (morning-range section).</p>'
    )


def _write_html(
    out_path: Path,
    *,
    initial_capital: float,
    equity_points: List[Tuple[str, float]],
    trades_sorted: List[Dict[str, Any]],
    summary: Dict[str, Any],
    page_heading: str = "BONGO portfolio — merged equity (trade-by-trade)",
    overnight_risk_note: bool = True,
) -> None:
    eq_labels = [p[0] for p in equity_points]
    eq_vals = [p[1] for p in equity_points]
    chart_json = json.dumps({"labels": eq_labels, "values": eq_vals})
    trades_json = json.dumps(trades_sorted, indent=0)
    summary_json = json.dumps(summary, indent=2)
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <title>{page_heading.replace("—", "-")}</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
  <style>
    body {{ font-family: system-ui, sans-serif; margin: 1rem 1.5rem; color: #1a1a1a; }}
    h1 {{ font-size: 1.25rem; }}
    pre.summary {{ background: #f4f4f5; padding: 1rem; overflow: auto; max-height: 20rem; }}
    #twrap {{ max-height: 28rem; overflow: auto; border: 1px solid #ddd; }}
    table {{ border-collapse: collapse; width: 100%; font-size: 0.8rem; }}
    th, td {{ border-bottom: 1px solid #eee; padding: 0.35rem 0.5rem; text-align: left; }}
    th {{ position: sticky; top: 0; background: #fafafa; }}
    .pnl-pos {{ color: #0a0; }}
    .pnl-neg {{ color: #a00; }}
    canvas {{ max-width: 100%; height: 320px !important; }}
    p.note {{ font-size: 0.85rem; max-width: 52rem; background: #fffbeb; border: 1px solid #fcd34d; padding: 0.6rem 0.75rem; }}
  </style>
</head>
<body>
  <h1>{page_heading}</h1>
  <p>Cumulative equity after each completed trade (chronological merge). Initial capital: {initial_capital:,.0f}.</p>
  {_html_risk_note(overnight_risk_note)}
  <canvas id="eq"></canvas>
  <h2>Summary</h2>
  <pre class="summary" id="sum"></pre>
  <h2>Trades ({len(trades_sorted)})</h2>
  <div id="twrap">
  <table>
    <thead><tr>
      <th>#</th><th>exit (UTC)</th><th>strategy</th><th>sym</th><th>side</th><th>qty</th>
      <th>risk $</th><th>≈pts</th><th>pnl</th><th>exit_reason</th>
    </tr></thead>
    <tbody id="tb"></tbody>
  </table>
  </div>
  <script>
    const chartData = {chart_json};
    const trades = {trades_json};
    const summary = {summary_json};
    const PTVAL = {{ MNQ: 2, MES: 5, MGC: 10, NQ: 20, ES: 50, MYM: 0.5, M2K: 5, RTY: 50, GC: 100 }};
    function riskPts(t) {{
      const ir = Number(t.initial_risk_dollars || 0);
      const q = Number(t.quantity || 1) || 1;
      const pv = PTVAL[t.symbol] || 2;
      if (ir <= 0 || q <= 0 || pv <= 0) return '—';
      return (ir / (q * pv)).toFixed(1);
    }}
    function riskUsd(t) {{
      const ir = Number(t.initial_risk_dollars || 0);
      return ir > 0 ? ir.toFixed(0) : '—';
    }}
    document.getElementById('sum').textContent = JSON.stringify(summary, null, 2);
    const ctx = document.getElementById('eq');
    new Chart(ctx, {{
      type: 'line',
      data: {{
        labels: chartData.labels,
        datasets: [{{
          label: 'Equity',
          data: chartData.values,
          borderColor: '#2563eb',
          tension: 0.05,
          pointRadius: 0,
        }}]
      }},
      options: {{
        responsive: true,
        scales: {{ x: {{ display: true }}, y: {{ display: true }} }},
        plugins: {{ legend: {{ display: false }} }}
      }}
    }});
    const tb = document.getElementById('tb');
    trades.forEach((t, i) => {{
      const tr = document.createElement('tr');
      const pnl = Number(t.pnl || 0);
      const cls = pnl >= 0 ? 'pnl-pos' : 'pnl-neg';
      tr.innerHTML = `<td>${{i+1}}</td><td>${{t.exit_time || ''}}</td><td>${{t.strategy}}</td><td>${{t.symbol}}</td><td>${{t.side || ''}}</td><td>${{t.quantity ?? ''}}</td><td>${{riskUsd(t)}}</td><td>${{riskPts(t)}}</td><td class="${{cls}}">${{pnl.toFixed(2)}}</td><td>${{t.exit_reason || ''}}</td>`;
      tb.appendChild(tr);
    }});
  </script>
</body>
</html>
"""
    out_path.write_text(html, encoding="utf-8")


async def _async_main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=100)
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--timeframe", type=str, default="5m")
    ap.add_argument("--csv-dir", type=Path, default=ROOT / "historical_data" / "price")
    ap.add_argument(
        "--out-dir",
        type=Path,
        default=ROOT / "docs" / "perf" / "bongo_portfolio_walkforward_equity",
    )
    ap.add_argument("--capital", type=float, default=50_000.0)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument(
        "--strategies",
        type=str,
        default="",
        metavar="IDS",
        help=(
            "Comma-separated strategy ids to include (default: all legs). "
            "Examples: overnight_range,morning_range_reversion (no body); "
            "body_reversion,morning_range_reversion (no overnight). "
            f"Valid: {', '.join(sorted(_VALID_STRATEGY_IDS))}."
        ),
    )
    args = ap.parse_args()

    legs = _legs_for_strategies_arg(args.strategies)
    has_overnight = any(s == "overnight_range" for s, _ in legs)
    has_body = any(s == "body_reversion" for s, _ in legs)
    has_morning = any(s == "morning_range_reversion" for s, _ in legs)
    env = _portfolio_env()
    out_dir: Path = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    symbols_needed = sorted({sym for _, sym in legs})
    csv_paths: Dict[str, Path] = {}
    anchors: Dict[str, date] = {}
    for sym in symbols_needed:
        p = args.csv_dir / f"{sym.lower()}_5m_databento.csv"
        if not p.is_file():
            print(f"error: missing CSV {p}", file=sys.stderr)
            return 2
        csv_paths[sym] = p
        anchors[sym] = _csv_last_date(p)
    anchor_end = min(anchors.values())
    folds = _fold_ranges(anchor_end, total_days=args.days, folds=args.folds)

    strategies_filter = (args.strategies or "").strip() or "all"
    meta = {
        "anchor_end": str(anchor_end),
        "days": args.days,
        "folds": args.folds,
        "timeframe": args.timeframe,
        "legs": [{"strategy": s, "symbol": y} for s, y in legs],
        "strategies_filter": strategies_filter,
        "env": dict(sorted(env.items())),
    }
    if has_overnight:
        meta["overnight_range_toml_symbols_mgc"] = _overnight_range_mgc_toml_snapshot()
    print(json.dumps({"portfolio_setup": meta}, indent=2), file=sys.stderr)

    if args.dry_run:
        return 0

    all_trades: List[Dict[str, Any]] = []
    leg_fold_pnl: Dict[str, Dict[int, float]] = defaultdict(lambda: defaultdict(float))
    leg_fold_trades: Dict[str, Dict[int, int]] = defaultdict(lambda: defaultdict(int))

    for fs, fe, fold_ix in folds:
        tasks = [
            _run_leg_json(
                strategy=st,
                symbol=sy,
                csv_path=csv_paths[sy],
                start=fs,
                end=fe,
                timeframe=args.timeframe,
                env=env,
            )
            for st, sy in legs
        ]
        results = await asyncio.gather(*tasks)
        for (st, sy), payload in zip(legs, results):
            key = f"{st}:{sy}"
            if not payload.get("ok", True):
                print(f"FAIL fold{fold_ix} {key}: {payload.get('error', payload)}", file=sys.stderr)
                continue
            res = payload.get("result") or {}
            pnl = float(res.get("total_pnl") or 0.0)
            nt = int(res.get("total_trades") or 0)
            leg_fold_pnl[key][fold_ix] += pnl
            leg_fold_trades[key][fold_ix] += nt
            for t in _extract_trades(payload):
                tt = dict(t)
                tt["strategy"] = st
                tt["symbol"] = sy
                tt["walkforward_fold"] = fold_ix
                all_trades.append(tt)

    all_trades.sort(key=lambda x: (_parse_ts(x.get("exit_time")), str(x.get("strategy")), str(x.get("symbol"))))

    initial = float(args.capital)
    equity = initial
    equity_series: List[Tuple[str, float]] = [("start", initial)]
    wins = 0
    for i, t in enumerate(all_trades, start=1):
        pnl = float(t.get("pnl") or 0.0)
        if pnl > 0:
            wins += 1
        equity += pnl
        et = str(t.get("exit_time") or "")
        short = et[:19] if len(et) >= 19 else et
        equity_series.append((f"{i}:{short}", equity))

    max_dd, max_dd_pct = _max_drawdown([p[1] for p in equity_series])
    n = len(all_trades)
    wr = 100.0 * wins / n if n else 0.0

    summary = {
        "initial_capital": initial,
        "final_equity": equity,
        "total_pnl": equity - initial,
        "merged_trades": n,
        "win_rate_pct": round(wr, 4),
        "max_drawdown": max_dd,
        "max_drawdown_pct": round(max_dd_pct, 4),
        "per_leg_fold_pnl": {k: dict(v) for k, v in sorted(leg_fold_pnl.items())},
        "meta": meta,
    }
    ov_risk = [
        float(t.get("initial_risk_dollars") or 0)
        for t in all_trades
        if t.get("strategy") == "overnight_range" and float(t.get("initial_risk_dollars") or 0) > 0
    ]
    if ov_risk:
        ov_sorted = sorted(ov_risk)
        mid = len(ov_sorted) // 2
        summary["overnight_initial_risk_usd_median"] = float(ov_sorted[mid])
        idx95 = min(len(ov_sorted) - 1, int(0.95 * (len(ov_sorted) - 1)))
        summary["overnight_initial_risk_usd_p95"] = float(ov_sorted[idx95])
        summary["overnight_initial_risk_usd_max"] = float(ov_sorted[-1])

    (out_dir / "portfolio_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (out_dir / "merged_trades.json").write_text(json.dumps(all_trades, indent=2), encoding="utf-8")
    page_heading = "BONGO portfolio — merged equity (trade-by-trade)"
    if has_overnight and has_morning and not has_body:
        page_heading = "BONGO portfolio — overnight_range + morning_range_reversion (merged equity)"
    elif has_body and has_morning and not has_overnight:
        page_heading = "BONGO portfolio — body_reversion + morning_range_reversion (merged equity)"
    _write_html(
        out_dir / "portfolio_equity.html",
        initial_capital=initial,
        equity_points=equity_series,
        trades_sorted=all_trades,
        summary=summary,
        page_heading=page_heading,
        overnight_risk_note=has_overnight,
    )
    print(f"wrote {out_dir / 'portfolio_summary.json'}", file=sys.stderr)
    print(f"wrote {out_dir / 'merged_trades.json'}", file=sys.stderr)
    print(f"wrote {out_dir / 'portfolio_equity.html'}", file=sys.stderr)
    return 0


def main() -> int:
    return asyncio.run(_async_main())


if __name__ == "__main__":
    raise SystemExit(main())
