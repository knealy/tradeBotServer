"""Walk-forward recap helpers: simulated equity curves and loss diagnostics.

Used by ``scripts/walkforward_trade_recap_report.py`` to enrich ``metrics.html``
without pulling in GUI / LWC from Python (chart is embedded as JSON + JS).
"""

from __future__ import annotations

import json
import math
import statistics
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

_ET = ZoneInfo("America/New_York")


def _parse_iso_utc(s: str) -> datetime:
    s = str(s).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def sort_trades_by_exit_time(trades: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(trades, key=lambda t: _parse_iso_utc(str(t.get("exit_time") or "1970-01-01")))


def equity_curve_from_trades(
    trades: List[Dict[str, Any]],
    *,
    start_equity: float = 2000.0,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Build a step equity series at each exit (for LWC ``time`` / ``value``).

    Inserts a baseline point at the earliest ``entry_time`` so the curve starts at
    ``start_equity`` before the first close. Uses UTCTimestamp seconds for LWC.

    Returns:
        ``points``: ``[{time, equity, trade_pnl, cumulative_pnl}, ...]`` sorted by ``time``
        ``summary``: final equity, max DD $, max DD %, return %
    """
    if start_equity <= 0:
        start_equity = 2000.0
    ordered = sort_trades_by_exit_time([t for t in trades if isinstance(t, dict)])
    if not ordered:
        return (
            [],
            {
                "start_equity": start_equity,
                "final_equity": start_equity,
                "total_return_pct": 0.0,
                "max_drawdown_dollars": 0.0,
                "max_drawdown_pct": 0.0,
                "n_trades": 0,
            },
        )

    points: List[Dict[str, Any]] = []
    equity = float(start_equity)
    peak = equity
    max_dd = 0.0
    max_dd_pct = 0.0
    cum_pnl = 0.0

    earliest_entry = min(_parse_iso_utc(str(t.get("entry_time") or t.get("exit_time"))) for t in ordered)
    t0 = int(earliest_entry.timestamp())
    points.append(
        {
            "time": t0,
            "equity": round(equity, 2),
            "trade_pnl": 0.0,
            "cumulative_pnl": 0.0,
        }
    )

    last_ts = t0
    for t in ordered:
        pnl = float(t.get("pnl") or 0)
        xt = _parse_iso_utc(str(t.get("exit_time")))
        ts = int(xt.timestamp())
        if ts <= last_ts:
            ts = last_ts + 1
        cum_pnl += pnl
        equity += pnl
        peak = max(peak, equity)
        dd = peak - equity
        max_dd = max(max_dd, dd)
        if peak > 1e-9:
            max_dd_pct = max(max_dd_pct, (dd / peak) * 100.0)
        points.append(
            {
                "time": ts,
                "equity": round(equity, 2),
                "trade_pnl": round(pnl, 4),
                "cumulative_pnl": round(cum_pnl, 4),
            }
        )
        last_ts = ts

    summary = {
        "start_equity": start_equity,
        "final_equity": round(equity, 2),
        "total_return_pct": round((equity - start_equity) / start_equity * 100.0, 3)
        if start_equity
        else 0.0,
        "max_drawdown_dollars": round(max_dd, 2),
        "max_drawdown_pct": round(max_dd_pct, 3),
        "n_trades": len(ordered),
    }
    return points, summary


def _pearson(xs: List[float], ys: List[float]) -> Optional[float]:
    n = len(xs)
    if n < 3 or n != len(ys):
        return None
    mx = statistics.fmean(xs)
    my = statistics.fmean(ys)
    num = sum((xi - mx) * (yi - my) for xi, yi in zip(xs, ys))
    denx = math.sqrt(sum((xi - mx) ** 2 for xi in xs))
    deny = math.sqrt(sum((yi - my) ** 2 for yi in ys))
    if denx < 1e-12 or deny < 1e-12:
        return None
    return num / (denx * deny)


def _trade_pnl(t: Dict[str, Any]) -> float:
    return float(t.get("pnl") or 0)


def _trade_side(t: Dict[str, Any]) -> str:
    return str(t.get("side") or "").upper() or "—"


def _exit_reason(t: Dict[str, Any]) -> str:
    return str(t.get("exit_reason") or "unknown")


def entry_weekday_et_label(t: Dict[str, Any]) -> str:
    """Mon..Sun label for entry bar (ET)."""
    try:
        et = _parse_iso_utc(str(t.get("entry_time")))
        wd = et.astimezone(_ET).weekday()
        return ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")[wd]
    except (ValueError, TypeError, OSError):
        return "—"


def entry_hour_et(t: Dict[str, Any]) -> int:
    try:
        et = _parse_iso_utc(str(t.get("entry_time")))
        return int(et.astimezone(_ET).hour)
    except (ValueError, TypeError, OSError):
        return -1


def extended_performance_insights(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Extra aggregates beyond fold_metrics (recovery factor, breakeven WR, hold skew)."""
    if not trades:
        return {}

    pnls = [_trade_pnl(t) for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    n = len(pnls)
    sum_w = float(sum(wins)) if wins else 0.0
    sum_l_abs = float(abs(sum(losses))) if losses else 0.0

    # sequential DD on PnL (same as fold_metrics_deep)
    peak = cum = 0.0
    max_dd_seq = 0.0
    for p in pnls:
        cum += p
        peak = max(peak, cum)
        max_dd_seq = max(max_dd_seq, peak - cum)

    avg_win = sum_w / len(wins) if wins else 0.0
    avg_loss_mag = sum_l_abs / len(losses) if losses else 0.0
    # Breakeven win rate: W * avg_win - (1-W) * avg_loss_mag = 0 => W = L / (A + L)
    breakeven_wr: Optional[float]
    if avg_win + avg_loss_mag > 1e-9:
        breakeven_wr = avg_loss_mag / (avg_win + avg_loss_mag)
    else:
        breakeven_wr = None

    recovery = float(sum(pnls) / max_dd_seq) if max_dd_seq > 1e-9 else None

    bars_all = [int(t.get("bars_held") or 0) for t in trades if t.get("bars_held") is not None]
    bars_w = [
        int(t.get("bars_held") or 0) for t in trades if _trade_pnl(t) > 0 and t.get("bars_held") is not None
    ]
    bars_l = [
        int(t.get("bars_held") or 0) for t in trades if _trade_pnl(t) < 0 and t.get("bars_held") is not None
    ]

    # Per-trade R-multiples = pnl / initial_risk_dollars.  For
    # variable-stop strategies (ATR-scaled stops on overnight gaps,
    # MGC vs MNQ position-size differences), trades with very small
    # initial_risk_dollars can dominate the mean (overnight_reversion
    # 2026-06-11 audit found avg_r_winners=+850 because one trade
    # had near-zero recorded risk).  Cap each trade's R at ±MAX_R_CAP
    # before averaging so the mean is robust; surface BOTH the clipped
    # mean (the "fair" measurement) AND the unclipped raw mean (so
    # the variable-stop pathology is visible in the recap), plus
    # MEDIAN as a non-parametric central tendency.  See CHANGELOG
    # 2026-06-11 "Arsenal truth-effects sanity check" for context.
    MAX_R_CAP = 10.0
    r_w: List[float] = []
    r_l: List[float] = []
    for t in trades:
        p = _trade_pnl(t)
        r = float(t.get("initial_risk_dollars") or 0)
        if r > 1e-9:
            if p > 0:
                r_w.append(p / r)
            elif p < 0:
                r_l.append(p / r)
    r_w_clipped = [min(MAX_R_CAP, v) for v in r_w]
    r_l_clipped = [max(-MAX_R_CAP, v) for v in r_l]
    n_w_clip = sum(1 for v in r_w if v > MAX_R_CAP)
    n_l_clip = sum(1 for v in r_l if v < -MAX_R_CAP)

    def _safe_mean(xs: List[float]) -> Optional[float]:
        if not xs:
            return None
        if len(xs) == 1:
            return round(xs[0], 3)
        return round(statistics.fmean(xs), 3)

    def _safe_median(xs: List[float]) -> Optional[float]:
        if not xs:
            return None
        return round(statistics.median(xs), 3)

    return {
        "n_trades": n,
        "recovery_factor_pnl_vs_seq_dd": round(recovery, 3) if recovery is not None else None,
        "breakeven_win_rate": round(breakeven_wr, 4) if breakeven_wr is not None else None,
        "actual_win_rate": round(len(wins) / n, 4) if n else 0.0,
        "median_bars_held_win": float(statistics.median(bars_w)) if len(bars_w) > 0 else None,
        "median_bars_held_loss": float(statistics.median(bars_l)) if len(bars_l) > 0 else None,
        "mean_mae_losers": round(
            statistics.fmean(
                [float(t.get("max_adverse_excursion") or 0) for t in trades if _trade_pnl(t) < 0]
            ),
            4,
        )
        if losses
        else None,
        "mean_mfe_winners": round(
            statistics.fmean(
                [float(t.get("max_favorable_excursion") or 0) for t in trades if _trade_pnl(t) > 0]
            ),
            4,
        )
        if wins
        else None,
        # PRIMARY: clipped mean — robust to variable-stop pathology.
        "avg_r_winners": _safe_mean(r_w_clipped),
        "avg_r_losers": _safe_mean(r_l_clipped),
        # DIAGNOSTICS: median (robust central tendency) + unclipped
        # raw mean (surfaces variable-stop pathology) + clip counts.
        "median_r_winners": _safe_median(r_w),
        "median_r_losers": _safe_median(r_l),
        "avg_r_winners_unclipped": _safe_mean(r_w),
        "avg_r_losers_unclipped": _safe_mean(r_l),
        "n_clipped_r_winners": n_w_clip,
        "n_clipped_r_losers": n_l_clip,
        "r_cap": MAX_R_CAP,
    }


def loss_pattern_analysis(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Structured comparison winners vs losers for categorical + correlation hints."""
    if not trades:
        return {"n": 0}

    losses = [t for t in trades if _trade_pnl(t) < 0]
    wins = [t for t in trades if _trade_pnl(t) > 0]
    n_l, n_w = len(losses), len(wins)

    # exit_reason x outcome
    by_reason: Dict[str, Dict[str, int]] = {}
    for t in trades:
        r = _exit_reason(t)
        by_reason.setdefault(r, {"wins": 0, "losses": 0, "n": 0})
        by_reason[r]["n"] += 1
        if _trade_pnl(t) > 0:
            by_reason[r]["wins"] += 1
        elif _trade_pnl(t) < 0:
            by_reason[r]["losses"] += 1

    # side: loss rate
    by_side: Dict[str, Dict[str, Any]] = {}
    for t in trades:
        s = _trade_side(t)
        by_side.setdefault(s, {"n": 0, "losses": 0})
        by_side[s]["n"] += 1
        if _trade_pnl(t) < 0:
            by_side[s]["losses"] += 1
    for s in by_side:
        nn = by_side[s]["n"]
        by_side[s]["loss_rate_pct"] = round(100.0 * by_side[s]["losses"] / nn, 1) if nn else 0.0

    # weekday: where do losses cluster?
    by_dow: Dict[str, Dict[str, int]] = {}
    for t in losses:
        lbl = entry_weekday_et_label(t)
        by_dow.setdefault(lbl, {"losses": 0})
        by_dow[lbl]["losses"] += 1
    dow_all: Dict[str, int] = {}
    for t in trades:
        lbl = entry_weekday_et_label(t)
        dow_all[lbl] = dow_all.get(lbl, 0) + 1
    dow_order = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
    dow_summary: Dict[str, Dict[str, Any]] = {}
    for d in dow_order:
        if d not in dow_all:
            continue
        nlost = by_dow.get(d, {}).get("losses", 0)
        dow_summary[d] = {
            "losses": nlost,
            "trades": dow_all.get(d, 0),
            "loss_share_of_losses_pct": round(100.0 * nlost / n_l, 1) if n_l else 0.0,
        }

    # Pearson: indicator 1=loss vs numeric factors
    y_loss = [1.0 if _trade_pnl(t) < 0 else 0.0 for t in trades]
    bars = [float(t.get("bars_held") or 0) for t in trades]
    risks = [float(t.get("initial_risk_dollars") or 0) for t in trades]
    hours = [float(entry_hour_et(t)) for t in trades if entry_hour_et(t) >= 0]

    correlations: List[Dict[str, Any]] = []
    r_bars = _pearson(bars, y_loss)
    if r_bars is not None:
        correlations.append(
            {
                "factor": "bars_held",
                "pearson_vs_loss_indicator": round(r_bars, 4),
                "n": len(trades),
                "hint": _correlation_hint("bars_held", r_bars),
            }
        )
    # risk: only where risk > 0
    idx_r = [i for i, t in enumerate(trades) if float(t.get("initial_risk_dollars") or 0) > 1e-9]
    if len(idx_r) > 4:
        rs = [risks[i] for i in idx_r]
        ys = [y_loss[i] for i in idx_r]
        r_risk = _pearson(rs, ys)
        if r_risk is not None:
            correlations.append(
                {
                    "factor": "initial_risk_dollars",
                    "pearson_vs_loss_indicator": round(r_risk, 4),
                    "n": len(idx_r),
                    "hint": _correlation_hint("initial_risk_dollars", r_risk),
                }
            )
    if len(hours) == len(trades) and len(trades) > 4:
        r_h = _pearson([float(entry_hour_et(t)) for t in trades], y_loss)
        if r_h is not None:
            correlations.append(
                {
                    "factor": "entry_hour_et",
                    "pearson_vs_loss_indicator": round(r_h, 4),
                    "n": len(trades),
                    "hint": _correlation_hint("entry_hour_et", r_h),
                }
            )

    loss_reason_pct = {
        r: round(100.0 * v["losses"] / n_l, 1) if n_l else 0.0 for r, v in by_reason.items() if v["losses"] > 0
    }

    by_strategy_mix: Dict[str, Dict[str, Any]] = {}
    if any(t.get("_strategy") for t in trades):
        strat_loss: Dict[str, int] = {}
        strat_all: Dict[str, int] = {}
        for t in trades:
            st = str(t.get("_strategy") or "—")
            strat_all[st] = strat_all.get(st, 0) + 1
        for t in losses:
            st = str(t.get("_strategy") or "—")
            strat_loss[st] = strat_loss.get(st, 0) + 1
        for st in sorted(strat_all.keys()):
            nl_ = strat_loss.get(st, 0)
            by_strategy_mix[st] = {
                "losses": nl_,
                "trades": strat_all[st],
                "pct_of_all_losses": round(100.0 * nl_ / n_l, 1) if n_l else 0.0,
            }

    return {
        "n_trades": len(trades),
        "n_losses": n_l,
        "n_wins": n_w,
        "by_exit_reason": by_reason,
        "losses_by_exit_reason_pct": loss_reason_pct,
        "by_side_loss_rate": by_side,
        "by_strategy_loss_mix": by_strategy_mix or None,
        "losses_by_weekday": dow_summary,
        "correlations_with_loss": correlations,
    }


def _correlation_hint(factor: str, r: float) -> str:
    if abs(r) < 0.08:
        return f"Weak linear association between {factor} and losing trades (|r|≈{abs(r):.2f})."
    direction = "higher" if r > 0 else "lower"
    return (
        f"When {factor} is {direction}, losing trades are slightly more common "
        f"(Pearson r≈{r:.2f}); treat as exploratory—causation not implied."
    )


def format_insights_html(
    *,
    title: str,
    extended: Dict[str, Any],
    loss_patterns: Dict[str, Any],
) -> str:
    """Single collapsible-style section for metrics.html."""
    if not extended and int(loss_patterns.get("n_trades") or 0) == 0:
        return ""

    rows_ext: List[str] = []
    if extended:
        be = extended.get("breakeven_win_rate")
        be_s = f"{100.0 * float(be):.1f}%" if be is not None else "—"
        rf = extended.get("recovery_factor_pnl_vs_seq_dd")
        m_w = extended.get("median_bars_held_win")
        m_l = extended.get("median_bars_held_loss")
        rows_ext.append(
            f"<tr><td>Breakeven win rate (from avg win / avg loss)</td><td class='num'>{be_s}</td>"
            f"<td>Actual WR {100.0 * float(extended.get('actual_win_rate') or 0):.1f}%</td></tr>"
        )
        rows_ext.append(
            f"<tr><td>Recovery factor (total PnL / sequential max DD)</td>"
            f"<td class='num'>{rf if rf is not None else '—'}</td><td>Higher is better; uses fold-style cumulative PnL DD</td></tr>"
        )
        rows_ext.append(
            f"<tr><td>Median bars held</td><td class='num'>W:{m_w if m_w is not None else '—'} "
            f"/ L:{m_l if m_l is not None else '—'}</td><td>Win vs loss holding time</td></tr>"
        )
        arw = extended.get("avg_r_winners")
        arl = extended.get("avg_r_losers")
        cap = extended.get("r_cap")
        mrw = extended.get("median_r_winners")
        mrl = extended.get("median_r_losers")
        nwc = extended.get("n_clipped_r_winners") or 0
        nlc = extended.get("n_clipped_r_losers") or 0
        clip_note = (
            f" ({nwc + nlc} trade(s) clipped at ±{cap}R)" if (nwc + nlc) > 0 else ""
        )
        rows_ext.append(
            f"<tr><td>Avg R multiples (clipped ±{cap}R)</td>"
            f"<td class='num'>win:{arw if arw is not None else '—'} "
            f"loss:{arl if arl is not None else '—'}</td>"
            f"<td>Clipped mean is robust to variable-stop outliers{clip_note}</td></tr>"
        )
        rows_ext.append(
            f"<tr><td>Median R multiples</td>"
            f"<td class='num'>win:{mrw if mrw is not None else '—'} "
            f"loss:{mrl if mrl is not None else '—'}</td>"
            f"<td>Non-parametric central tendency; use when n_clipped &gt; 0</td></tr>"
        )

    reason_rows: List[str] = []
    for reason, pct in sorted(
        (loss_patterns.get("losses_by_exit_reason_pct") or {}).items(), key=lambda x: -x[1]
    ):
        br = (loss_patterns.get("by_exit_reason") or {}).get(reason, {})
        reason_rows.append(
            f"<tr><td><code>{reason}</code></td><td class='num'>{pct}%</td>"
            f"<td>losses {br.get('losses',0)} / events {br.get('n',0)}</td></tr>"
        )

    side_rows: List[str] = []
    for side, info in sorted((loss_patterns.get("by_side_loss_rate") or {}).items()):
        side_rows.append(
            f"<tr><td><code>{side}</code></td><td class='num'>{info.get('loss_rate_pct',0)}%</td>"
            f"<td>{info.get('losses',0)} losses / {info.get('n',0)} trades</td></tr>"
        )

    strat_rows: List[str] = []
    for st, info in sorted((loss_patterns.get("by_strategy_loss_mix") or {}).items()):
        strat_rows.append(
            f"<tr><td><code>{_html_escape(st)}</code></td><td class='num'>{info.get('losses',0)}</td>"
            f"<td>{info.get('pct_of_all_losses',0)}% of all losses · {info.get('trades',0)} trades in leg</td></tr>"
        )

    corr_rows: List[str] = []
    for c in loss_patterns.get("correlations_with_loss") or []:
        corr_rows.append(
            f"<tr><td><code>{c.get('factor')}</code></td><td class='num'>{c.get('pearson_vs_loss_indicator')}</td>"
            f"<td>{_html_escape(str(c.get('hint', '')))}</td></tr>"
        )

    dow_rows: List[str] = []
    for d, info in (loss_patterns.get("losses_by_weekday") or {}).items():
        dow_rows.append(
            f"<tr><td>{d}</td><td class='num'>{info.get('losses',0)}</td>"
            f"<td>{info.get('loss_share_of_losses_pct',0)}% of all losses; "
            f"{info.get('trades',0)} entries this weekday</td></tr>"
        )

    return f"""
<h3>{_html_escape(title)}</h3>
<h4>Performance insights</h4>
<table><thead><tr><th>Metric</th><th class="num">Value</th><th>Note</th></tr></thead>
<tbody>{''.join(rows_ext) if rows_ext else '<tr><td colspan="3">—</td></tr>'}</tbody></table>
<h4>Losses by exit reason</h4>
<table><thead><tr><th>exit_reason</th><th class="num">% of losses</th><th>detail</th></tr></thead>
<tbody>{''.join(reason_rows) if reason_rows else '<tr><td colspan="3">—</td></tr>'}</tbody></table>
<h4>Loss rate by side</h4>
<table><thead><tr><th>side</th><th class="num">Loss rate</th><th>detail</th></tr></thead>
<tbody>{''.join(side_rows) if side_rows else '<tr><td colspan="3">—</td></tr>'}</tbody></table>
<h4>Loss mix by strategy leg</h4>
<p class="muted">Populated when trades carry <code>_strategy</code> from the recap report (multi-strategy runs).</p>
<table><thead><tr><th>strategy</th><th class="num">Losses</th><th>Context</th></tr></thead>
<tbody>{''.join(strat_rows) if strat_rows else '<tr><td colspan="3">—</td></tr>'}</tbody></table>
<h4>Loss count by entry weekday (ET)</h4>
<table><thead><tr><th>Weekday</th><th class="num">Losses</th><th>Context</th></tr></thead>
<tbody>{''.join(dow_rows) if dow_rows else '<tr><td colspan="3">—</td></tr>'}</tbody></table>
<h4>Linear association with losses (exploratory)</h4>
<p class="muted">Pearson correlation between a 0/1 <em>loss indicator</em> and each factor. Values near 0 mean
no obvious linear pattern in this sample; |r|&gt;0.15 is a loose rule-of-thumb for “worth eyeballing” only.</p>
<table><thead><tr><th>Factor</th><th class="num">r vs loss</th><th>Interpretation</th></tr></thead>
<tbody>{''.join(corr_rows) if corr_rows else '<tr><td colspan="3">—</td></tr>'}</tbody></table>
"""


def _html_escape(s: str) -> str:
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def equity_chart_embed_js(equity_points: List[Dict[str, Any]], *, chart_id: str = "equityChart") -> str:
    """Return HTML fragment: container + script for Lightweight Charts v4."""
    data = [{"time": p["time"], "value": p["equity"]} for p in equity_points]
    payload = json.dumps(data)
    div = f'<div id="{chart_id}" style="width:100%;height:340px;position:relative;"></div>'
    cdn = (
        '<script src="https://unpkg.com/lightweight-charts@4.1.3/'
        'dist/lightweight-charts.standalone.production.js"></script>\n'
    )
    script = (
        "<script>\n"
        "(function() {\n"
        f'  const el = document.getElementById("{chart_id}");\n'
        "  if (!el || typeof LightweightCharts === 'undefined') return;\n"
        "  const chart = LightweightCharts.createChart(el, {\n"
        "    layout: { background: { color: '#131316' }, textColor: '#c8c8d0' },\n"
        "    grid: { vertLines: { color: '#2c2c34' }, horzLines: { color: '#2c2c34' } },\n"
        "    rightPriceScale: { borderColor: '#2c2c34' },\n"
        "    timeScale: { borderColor: '#2c2c34', timeVisible: true, secondsVisible: false },\n"
        "  });\n"
        "  const series = chart.addLineSeries({ color: '#5ecf8e', lineWidth: 2 });\n"
        f"  const raw = {payload};\n"
        "  series.setData(raw);\n"
        "  chart.timeScale().fitContent();\n"
        "  window.addEventListener('resize', () => {\n"
        "    chart.applyOptions({ width: el.clientWidth });\n"
        "  });\n"
        "})();\n"
        "</script>\n"
    )
    return div + "\n" + cdn + script


def monte_carlo_histogram_svg(
    bins: Dict[str, Any],
    *,
    chart_id: str,
    title: str,
    actual: Optional[float] = None,
    unit_prefix: str = "$",
    width: int = 520,
    height: int = 180,
) -> str:
    """Inline SVG histogram for Monte Carlo distributions (works on file://)."""
    edges = bins.get("edges") or []
    counts = bins.get("counts") or []
    if not edges or not counts or len(edges) != len(counts) + 1:
        return f"<p class='muted'>No histogram data for {_html_escape(title)}.</p>"

    max_c = max(counts) or 1
    pad_l, pad_r, pad_t, pad_b = 48, 16, 28, 36
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b
    n = len(counts)
    bar_w = plot_w / max(n, 1)

    bars: List[str] = []
    lo, hi = float(edges[0]), float(edges[-1])
    zero_x: Optional[float] = None
    if lo < 0 < hi:
        zero_x = pad_l + (0 - lo) / (hi - lo) * plot_w

    for i, c in enumerate(counts):
        if c <= 0:
            continue
        x = pad_l + i * bar_w + 1
        bh = (c / max_c) * plot_h
        y = pad_t + plot_h - bh
        cx = (float(edges[i]) + float(edges[i + 1])) / 2
        color = "#5ecf8e" if cx >= 0 else "#f08080"
        bars.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{max(bar_w - 2, 1):.1f}" '
            f'height="{bh:.1f}" fill="{color}" opacity="0.85" rx="1"/>'
        )

    actual_line = ""
    if actual is not None and hi > lo:
        ax = pad_l + (float(actual) - lo) / (hi - lo) * plot_w
        if pad_l <= ax <= pad_l + plot_w:
            actual_line = (
                f'<line x1="{ax:.1f}" y1="{pad_t}" x2="{ax:.1f}" y2="{pad_t + plot_h}" '
                f'stroke="#c9b87c" stroke-width="2" stroke-dasharray="4,3"/>'
                f'<text x="{ax:.1f}" y="{pad_t - 6}" text-anchor="middle" '
                f'fill="#c9b87c" font-size="10">actual</text>'
            )

    zero_line = ""
    if zero_x is not None:
        zero_line = (
            f'<line x1="{zero_x:.1f}" y1="{pad_t}" x2="{zero_x:.1f}" y2="{pad_t + plot_h}" '
            f'stroke="#666" stroke-width="1" opacity="0.5"/>'
        )

    def _fmt(v: float) -> str:
        if abs(v) >= 1000:
            return f"{unit_prefix}{v:,.0f}"
        return f"{unit_prefix}{v:.0f}"

    x_labels = (
        f'<text x="{pad_l}" y="{height - 8}" fill="#9b9ba8" font-size="10">{_fmt(lo)}</text>'
        f'<text x="{pad_l + plot_w}" y="{height - 8}" text-anchor="end" fill="#9b9ba8" font-size="10">{_fmt(hi)}</text>'
    )

    return f"""
<div class="mc-hist" id="{_html_escape(chart_id)}">
  <div class="muted" style="font-size:0.82rem;margin-bottom:0.25rem">{_html_escape(title)}</div>
  <svg viewBox="0 0 {width} {height}" width="100%" height="{height}" role="img"
       aria-label="{_html_escape(title)} histogram" style="max-width:{width}px">
    {zero_line}
    {''.join(bars)}
    {actual_line}
    <line x1="{pad_l}" y1="{pad_t + plot_h}" x2="{pad_l + plot_w}" y2="{pad_t + plot_h}" stroke="#444" stroke-width="1"/>
    {x_labels}
  </svg>
</div>"""


def format_monte_carlo_html(
    mc: Dict[str, Any],
    *,
    title: str = "Monte Carlo robustness",
    chart_id_prefix: str = "mc",
) -> str:
    """HTML section for walk-forward metrics.html."""
    if not mc or int(mc.get("n_trades") or 0) == 0:
        return "<p class='muted'>No trades for Monte Carlo.</p>"

    verdict = mc.get("endurance_verdict") or {}
    grade = str(verdict.get("grade") or "—")
    grade_class = {"strong": "pos", "adequate": "pos", "caution": "", "weak": "neg"}.get(grade, "")
    checks = verdict.get("checks") or []
    check_rows = []
    for c in checks:
        ok = c.get("pass")
        mark = "✓" if ok else "✗"
        cls = "pos" if ok else "neg"
        check_rows.append(
            f"<tr><td class='{cls}'>{mark}</td><td>{_html_escape(str(c.get('check', '')))}</td>"
            f"<td>{_html_escape(str(c.get('detail', '')))}</td></tr>"
        )

    def _mode_table(mode_key: str, label: str) -> str:
        m = (mc.get("modes") or {}).get(mode_key) or {}
        if not m:
            return ""
        tp = m.get("total_pnl") or {}
        dd = m.get("max_drawdown_dollars") or {}
        mcl = m.get("max_consecutive_losses") or {}
        hists = m.get("histograms") or {}
        cid = f"{chart_id_prefix}-{mode_key}".replace("|", "-").replace(" ", "-")
        hist_html = ""
        if hists.get("total_pnl"):
            hist_html += monte_carlo_histogram_svg(
                hists["total_pnl"],
                chart_id=f"{cid}-pnl",
                title=f"{label} — total PnL distribution",
                actual=float(tp.get("actual") or 0),
            )
        if hists.get("max_drawdown_dollars"):
            hist_html += monte_carlo_histogram_svg(
                hists["max_drawdown_dollars"],
                chart_id=f"{cid}-dd",
                title=f"{label} — max drawdown ($) distribution",
                actual=float(dd.get("actual") or 0),
                unit_prefix="$",
            )
        rows = [
            ("Total PnL p5 / p50 / p95", f"${tp.get('p5', 0):,.0f} / ${tp.get('p50', 0):,.0f} / ${tp.get('p95', 0):,.0f}"),
            ("P(profit)", f"{100 * float(tp.get('p_profit') or 0):.1f}%"),
            ("Actual total PnL (sequential)", f"${tp.get('actual', 0):,.0f} ({tp.get('actual_percentile', 0):.0f}th pct)"),
            ("Max DD $ p50 / p95", f"${dd.get('p50', 0):,.0f} / ${dd.get('p95', 0):,.0f}"),
            ("Actual max DD $", f"${dd.get('actual', 0):,.0f} ({dd.get('actual_percentile', 0):.0f}th pct)"),
            ("Max consec losses p90", f"{mcl.get('p90', 0):.0f} (actual {mcl.get('actual', 0)})"),
        ]
        body = "".join(
            f"<tr><td>{_html_escape(k)}</td><td class='num'>{_html_escape(v)}</td></tr>"
            for k, v in rows
        )
        return f"""
<h4>{_html_escape(label)}</h4>
<div class="mc-hist-grid" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:1rem;margin:0.75rem 0 1rem">
{hist_html}
</div>
<table><thead><tr><th>Metric</th><th class="num">Distribution</th></tr></thead>
<tbody>{body}</tbody></table>"""

    actual = mc.get("sequential_actual") or {}
    n_sims = mc.get("num_simulations", 0)
    n_tr = mc.get("n_trades", 0)
    start = mc.get("start_equity", 2000)

    anchor = ' id="monte-carlo"' if chart_id_prefix == "mc-grand" else ""
    return f"""
<h3{anchor}>{_html_escape(title)}</h3>
<p class="muted"><strong>{n_sims:,}</strong> simulations on <strong>{n_tr}</strong> trades
  (start ${start:,.0f}). <strong>Shuffle</strong> = permute trade order (tests path / drawdown luck).
  <strong>Bootstrap</strong> = resample P&Ls with replacement (tests distribution uncertainty).
  Sequential actual: total PnL <strong>${actual.get('total_pnl', 0):,.0f}</strong>,
  max DD <strong>${actual.get('max_drawdown_dollars', 0):,.0f}</strong>.</p>
<div class="banner">
  <strong>Endurance grade:</strong> <span class="{grade_class}">{grade.upper()}</span>
  — {_html_escape(str(verdict.get('summary', '')))}
  ({verdict.get('checks_passed', 0)}/{verdict.get('checks_total', 0)} checks passed)
</div>
<table><thead><tr><th></th><th>Check</th><th>Detail</th></tr></thead>
<tbody>{''.join(check_rows)}</tbody></table>
{_mode_table('shuffle', 'Trade-order shuffle')}
{_mode_table('bootstrap', 'P&L bootstrap (iid resample)')}
"""
