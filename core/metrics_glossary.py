"""Shared tooltips for performance metrics (walkforward HTML + master GUI).

Single source of truth for column headers and KPI labels. HTML helpers escape
tooltip text for safe embedding in ``title`` attributes.
"""
from __future__ import annotations

import html
import json
from typing import Dict, Iterable, List, Optional, Tuple

# Keys are stable ids; values are short tooltip prose (plain text).
METRICS: Dict[str, str] = {
    "fold": "Walk-forward fold index (non-overlapping calendar windows).",
    "start": "First calendar date in this fold (inclusive).",
    "end": "Last calendar date in this fold (inclusive).",
    "strategy": "Strategy id replayed with its committed TOML config.",
    "sym": "Instrument symbol (e.g. MNQ, MGC).",
    "n": "Number of completed round-trip trades in the window.",
    "trades": "Count of completed round-trip trades in the selected window.",
    "pnl": "Sum of realized P&L ($) across all trades in the row.",
    "net_pnl": "Net realized P&L ($) after wins and losses in the window.",
    "wr": "Win rate — percentage of trades with positive realized P&L.",
    "win_rate": "Percentage of trades closed with positive realized P&L.",
    "expectancy": "Mean realized P&L per trade (total PnL ÷ n). Also called E/trade.",
    "e_trade": "Expectancy — average realized P&L per completed trade.",
    "pnl_stdev": "Standard deviation of per-trade P&L (σ). High σ with low expectancy suggests unstable edge.",
    "sigma_pnl": "Standard deviation of per-trade realized P&L.",
    "pf": "Profit factor — sum(winning $) ÷ |sum(losing $)|. >1 means gross wins exceed gross losses.",
    "profit_factor": "Gross winning dollars divided by absolute gross losing dollars.",
    "maxdd": "Max drawdown — peak-to-trough decline on cumulative P&L within the trade sequence.",
    "max_drawdown": "Largest peak-to-trough drop in cumulative P&L for the ordered trades.",
    "wins": "Count of trades with positive realized P&L.",
    "losses": "Count of trades with zero or negative realized P&L.",
    "max_consec_wins": "Longest streak of consecutive winning trades in the fold.",
    "max_consec_losses": "Longest streak of consecutive losing trades — survivability stress signal.",
    "avg_bars": "Average number of bars held from entry fill to exit fill.",
    "sum_r": "ΣR — sum of (trade PnL ÷ initial_risk_dollars) where initial risk was recorded at entry.",
    "margin": (
        "For take_profit: how much of the initial risk was consumed by MAE before TP "
        "(near 100% = almost stopped out). For stop_loss: how far price ran in the "
        "TP direction relative to initial risk."
    ),
    "qty": "Contract quantity (lots) for the round trip.",
    "quantity": "Number of contracts traded on entry (same for the full round trip unless scaled out).",
    "side": "Trade direction: BUY/LONG or SELL/SHORT at entry.",
    "entry": "Entry fill timestamp (UTC ISO in reports; ET on charts when axis is NY).",
    "exit": "Exit fill timestamp.",
    "exit_reason": "How the trade closed: stop_loss, take_profit, breakeven, replay_force_flat_et, etc.",
    "chart": "Link to the Lightweight Charts trade recap HTML for this leg.",
    "avg_trade": "Average realized P&L per trade in the selected performance window.",
    "best_streak": "Longest consecutive win or loss streak in the window (tagged W/L).",
    "current_streak": "Active win or loss streak at the end of the window.",
    "equity_curve": "Cumulative account equity after each trade exit (exit-time order).",
    "dll": "Daily loss limit buffer — room remaining before the prop daily loss cap.",
    "mll": "Maximum (trailing) loss limit buffer — room before account blow.",
    "regime_trend": "KER and ADX both high — directional, efficient price path (see core/regime.py).",
    "regime_chop": "KER and ADX both low — range-bound, mean-reverting conditions.",
    "regime_mixed": "Neither clean trend nor chop — default bucket when indicators disagree.",
}


def tooltip(key: str) -> str:
    """Return tooltip text for a metric key (empty string if unknown)."""
    return METRICS.get(str(key).strip(), "")


def th(
    key: str,
    label: str,
    *,
    num: bool = False,
    extra_attrs: str = "",
) -> str:
    """Render an HTML ``<th>`` with optional ``title`` tooltip."""
    tip = tooltip(key)
    cls = ' class="num"' if num else ""
    title = f' title="{html.escape(tip)}"' if tip else ""
    return f"<th{cls}{title}{extra_attrs}>{label}</th>"


def thead_cells(cells: Iterable[Tuple[str, str, bool]]) -> str:
    """Build a header row from ``(metric_key, label, is_numeric)`` tuples."""
    return "".join(th(k, lbl, num=num) for k, lbl, num in cells)


def glossary_json() -> str:
    """JSON object for embedding in GUI pages or API responses."""
    return json.dumps(METRICS, ensure_ascii=False)


def apply_tooltips_js() -> str:
    """Inline JS: set ``title`` on elements with ``data-metric-tip``."""
    return f"""const METRICS_GLOSSARY = {glossary_json()};
function applyMetricsTooltips(root) {{
  const scope = root || document;
  scope.querySelectorAll('[data-metric-tip]').forEach(el => {{
    const k = el.getAttribute('data-metric-tip');
    const tip = METRICS_GLOSSARY[k];
    if (tip) el.title = tip;
  }});
}}
document.addEventListener('DOMContentLoaded', () => applyMetricsTooltips());"""
