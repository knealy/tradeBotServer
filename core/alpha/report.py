"""
Alpha Discovery report generator.

Produces:
  - Markdown report (human readable, committable)
  - JSON summary (for automated pipelines / DB storage)
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from .hypothesis import BucketStats, HypothesisResult

logger = logging.getLogger(__name__)


def _pf(pf: float) -> str:
    return "∞" if pf == float("inf") else f"{pf:.2f}x"


def _stars(p: float) -> str:
    if p < 0.001: return " ***"
    if p < 0.01:  return " **"
    if p < 0.05:  return " *"
    return ""


def _bar(value: float, lo: float, hi: float, width: int = 20) -> str:
    """ASCII progress bar normalised between lo and hi."""
    span = hi - lo if hi > lo else 1.0
    filled = max(0, min(width, int(((value - lo) / span) * width)))
    return "[" + "█" * filled + "·" * (width - filled) + "]"


class AlphaReport:
    """
    Generates a comprehensive markdown + JSON alpha report.

    Parameters
    ----------
    symbol     : e.g. "MNQ"
    days       : number of calendar days of data used
    n_sessions : sessions with sufficient overnight coverage
    n_trades   : simulated trades analysed
    summary    : overall backtest stats dict from AlphaScanner.summary_stats()
    stop_atr   : stop ATR multiplier used in simulation
    tp_atr     : TP ATR multiplier used in simulation
    """

    def __init__(
        self,
        symbol: str,
        days: int,
        n_sessions: int,
        n_trades: int,
        summary: Dict[str, Any],
        stop_atr: float = 1.25,
        tp_atr: float = 2.0,
    ):
        self.symbol = symbol
        self.days = days
        self.n_sessions = n_sessions
        self.n_trades = n_trades
        self.summary = summary
        self.stop_atr = stop_atr
        self.tp_atr = tp_atr

    # ------------------------------------------------------------------
    # public
    # ------------------------------------------------------------------

    def to_markdown(self, results: List[HypothesisResult]) -> str:
        ranked = results  # caller should pass already-ranked list
        sig = [r for r in ranked if r.significant]
        nonsig = [r for r in ranked if not r.significant]
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

        lines: List[str] = []
        lines += self._header(now)
        lines += self._overall_stats()
        lines += self._signal_ranking_table(ranked)
        lines += self._significant_section(sig)
        lines += self._exploratory_section(nonsig)
        lines += self._action_guide(sig)
        return "\n".join(lines)

    def to_json(self, results: List[HypothesisResult]) -> Dict[str, Any]:
        return {
            "generated_at": datetime.utcnow().isoformat(),
            "symbol":        self.symbol,
            "days":          self.days,
            "n_sessions":    self.n_sessions,
            "n_trades":      self.n_trades,
            "sim_params":    {"stop_atr": self.stop_atr, "tp_atr": self.tp_atr},
            "overall":       self.summary,
            "signals": [
                {
                    "feature":      r.feature,
                    "type":         r.feature_type,
                    "significant":  r.significant,
                    "p_value":      r.p_value,
                    "ic":           r.ic,
                    "effect_size":  r.effect_size,
                    "n":            r.n_total,
                    "test":         r.test_name,
                    "buckets": [
                        {
                            "label":          b.label,
                            "n":              b.n,
                            "win_rate":       round(b.win_rate, 4),
                            "avg_r":          round(b.avg_r, 4),
                            "profit_factor":  round(b.profit_factor, 4) if b.profit_factor != float("inf") else None,
                            "avg_pnl_usd":    round(b.avg_pnl_usd, 2),
                        }
                        for b in r.buckets
                    ],
                    "best_quartile_lo": r.best_quartile_lo,
                    "best_quartile_hi": r.best_quartile_hi,
                }
                for r in results
            ],
        }

    # ------------------------------------------------------------------
    # private sections
    # ------------------------------------------------------------------

    def _header(self, now: str) -> List[str]:
        return [
            f"# Alpha Discovery Report — {self.symbol}",
            "",
            f"**Generated:** {now}  ",
            f"**Window:** {self.days} calendar days  ",
            f"**Sessions:** {self.n_sessions}  ",
            f"**Simulated trades:** {self.n_trades}  ",
            f"**Simulation:** stop={self.stop_atr}×ATR, TP={self.tp_atr}×ATR  ",
            f"**Stats correction:** Benjamini-Hochberg FDR, α = 0.05",
            "",
            "> Features with ✅ are statistically significant after FDR correction.",
            "> Features with ⚡ are not significant but have |IC| > 0.12 — worth monitoring.",
            "",
            "---",
            "",
        ]

    def _overall_stats(self) -> List[str]:
        s = self.summary
        if not s:
            return []
        pf_str = _pf(s.get("profit_factor", 0))
        wr = s.get("win_rate", 0)
        lines = [
            "## Overall Simulation Performance",
            "",
            f"| Metric | Value |",
            f"|--------|-------|",
            f"| Trades | {s.get('n_trades', '—')} |",
            f"| Win rate | {wr:.1%} |",
            f"| Avg R | {s.get('avg_r', 0):+.3f} |",
            f"| Profit factor | {pf_str} |",
            f"| Total P&L | ${s.get('total_pnl', 0):+,.0f} |",
            f"| Max drawdown | ${s.get('max_drawdown', 0):+,.0f} |",
            f"| Annualised Sharpe | {s.get('sharpe', 0):.2f} |",
            "",
            "---",
            "",
        ]
        return lines

    def _signal_ranking_table(self, results: List[HypothesisResult]) -> List[str]:
        lines = [
            "## Signal Ranking",
            "",
            "| # | Feature | IC | p-value | Sig | Best condition |",
            "|---|---------|----|---------|----|----------------|",
        ]
        for i, r in enumerate(results, 1):
            best = max(r.buckets, key=lambda b: b.avg_r) if r.buckets else None
            best_str = f"{best.label} → avg {best.avg_r:+.2f}R" if best else "—"
            sig = "✅" if r.significant else ("⚡" if abs(r.ic) > 0.12 else "—")
            lines.append(
                f"| {i} | `{r.feature}` | {r.ic:+.3f} | "
                f"{r.p_value:.4f}{_stars(r.p_value)} | {sig} | {best_str} |"
            )
        lines += ["", "---", ""]
        return lines

    def _significant_section(self, results: List[HypothesisResult]) -> List[str]:
        if not results:
            return [
                "## Statistically Significant Signals",
                "",
                "_None found at α=0.05 after FDR correction. Increase the data window or check data quality._",
                "",
                "---",
                "",
            ]
        lines = ["## Statistically Significant Signals", ""]
        for r in results:
            lines += self._render_result(r, highlight=True)
        lines += ["---", ""]
        return lines

    def _exploratory_section(self, results: List[HypothesisResult]) -> List[str]:
        if not results:
            return []
        # Only show those with |IC| > 0.08 — suppress pure noise
        notable = [r for r in results if abs(r.ic) > 0.08]
        if not notable:
            return []
        lines = [
            "## Exploratory Signals (not significant — monitor only)",
            "",
            "_These did not survive FDR correction but show directional pattern (|IC| > 0.08).",
            "Paper-trade the top filter for 20+ sessions before treating as confirmed._",
            "",
        ]
        for r in notable:
            lines += self._render_result(r, highlight=False)
        lines += ["---", ""]
        return lines

    def _render_result(self, r: HypothesisResult, highlight: bool) -> List[str]:
        sig_tag = "✅ SIGNIFICANT" if highlight else f"exploratory — p={r.p_value:.4f}"
        lines = [
            f"### `{r.feature}` [{sig_tag}]",
            "",
            f"- **Type:** {r.feature_type}   **Test:** {r.test_name}",
            f"- **IC:** {r.ic:+.3f}   **Effect size:** {r.effect_size:.3f}   "
            f"**p-value:** {r.p_value:.4f}{_stars(r.p_value)}   **N:** {r.n_total}",
        ]
        if r.best_quartile_lo is not None:
            lines.append(
                f"- **Best-performing range:** [{r.best_quartile_lo:.2f}, {r.best_quartile_hi:.2f}]"
            )
        lines += [
            "",
            "| Bucket | N | Win% | Avg R | PF | Avg P&L | Sharpe |",
            "|--------|---|------|-------|----|---------|--------|",
        ]
        r_vals = [b.avg_r for b in r.buckets]
        lo_r = min(r_vals) if r_vals else -1
        hi_r = max(r_vals) if r_vals else 1

        for b in r.buckets:
            bar = _bar(b.avg_r, lo_r, hi_r, width=12)
            lines.append(
                f"| {b.label} | {b.n} | {b.win_rate:.0%} | {b.avg_r:+.3f} {bar} | "
                f"{_pf(b.profit_factor)} | ${b.avg_pnl_usd:+.0f} | {b.sharpe:.2f} |"
            )
        lines += ["", ""]
        return lines

    def _action_guide(self, sig_results: List[HypothesisResult]) -> List[str]:
        lines = [
            "## Implementation Guide",
            "",
            "### How to apply significant findings to `overnight_range.toml`",
            "",
        ]
        if not sig_results:
            lines += [
                "_No significant signals to act on yet. Collect more data (aim for ≥ 200 trades)._",
                "",
            ]
            return lines

        lines += [
            "```toml",
            "[filters]",
        ]
        for r in sig_results:
            best = max(r.buckets, key=lambda b: b.avg_r)
            worst = min(r.buckets, key=lambda b: b.avg_r)
            if r.feature_type == "continuous" and r.best_quartile_lo is not None:
                lines.append(f"# {r.feature}: best Q [{r.best_quartile_lo:.2f}–{r.best_quartile_hi:.2f}], IC={r.ic:+.3f}")
                lines.append(f"# {r.feature}_min = {r.best_quartile_lo:.2f}  # uncomment to enable")
                lines.append(f"# {r.feature}_max = {r.best_quartile_hi:.2f}")
            elif r.feature_type == "categorical":
                lines.append(f"# {r.feature}: best='{best.label}' ({best.avg_r:+.2f}R), worst='{worst.label}' ({worst.avg_r:+.2f}R)")
                lines.append(f"# Consider skipping {r.feature} = '{worst.label}' sessions")
            lines.append("")
        lines += [
            "```",
            "",
            "**Checklist before enabling any filter:**",
            "1. Run `python scripts/alpha_discovery.py` with OOS window (`--oos-days 30`) — IC must remain positive",
            "2. Paper-trade the filtered strategy for ≥ 20 sessions",
            "3. Re-run this report monthly with fresh data",
            "4. Update `docs/alpha/` with the new report",
            "",
        ]
        return lines
