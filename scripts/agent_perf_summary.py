#!/usr/bin/env python3
"""agent_perf_summary.py — Single-shot performance summary for Cursor agents.

The walkforward scripts (``walkforward_trade_recap_report.py``,
``walkforward_strategy_competition.py``, ``walkforward_last_trades_charts.py``)
each leave behind a folder under ``docs/perf/<run_id>/`` containing the same
three artefacts:

  * ``metrics.json``           per-fold rows (n_trades, pnl, win_rate, ...)
  * ``metrics_insights.json``  aggregated ``grand.equity_summary`` +
                               ``grand.extended`` headline numbers
  * ``strategy_configs.json``  the TOML snapshot used for the run

For agents iterating on a strategy this layout means "how is round N
performing on the 9m window?" requires opening **at least three files** per
run (and usually more, to diff against the prior round). That's the friction
this tool removes: one invocation, one JSON blob, every headline number for
every run that matches a strategy filter, plus an optional latest-vs-previous
delta when more than one matching run exists per window.

Typical agent usage::

    # JSON (default — pipe to ``jq`` or eat from a tool call)
    .venv/bin/python scripts/agent_perf_summary.py morning_range_reversion

    # Just the windows you care about
    .venv/bin/python scripts/agent_perf_summary.py morning_range_reversion --windows 9m

    # Compact table for human inspection
    .venv/bin/python scripts/agent_perf_summary.py --format=table

    # Compare against an old baseline directory by name
    .venv/bin/python scripts/agent_perf_summary.py morning_range_reversion \\
        --compare-baseline morning_range_final_9m

Schema (stable; safe to parse):

    {
      "generated_at_utc": "<iso>",
      "scanned_root": "docs/perf",
      "n_runs_scanned": 56,
      "n_runs_matched": 3,
      "filters": {"strategy": "morning_range_reversion", "windows": null},
      "runs": [
        {
          "run_id": "morning_range_round25_9m",
          "path": "docs/perf/morning_range_round25_9m",
          "window": "9m",                  // inferred from suffix
          "mtime_utc": "2026-05-30T...",
          "strategies": ["morning_range_reversion"],
          "symbols": ["MNQ","MGC"],
          "headline": {
            "return_pct": 466.65,
            "max_drawdown_pct": 14.564,
            "recovery_factor": 10.654,
            "win_rate_pct": 74.51,
            "n_trades": 51,
            "final_equity": 11333.0,
            "start_equity": 2000.0
          },
          "per_symbol": [...],             // grand.equity_summary per symbol
          "vs_previous": {                 // null if no previous match
            "previous_run_id": "...",
            "return_pct_delta": +18.4,
            ...
          }
        },
        ...
      ]
    }

Failure mode: any run with a malformed or missing ``metrics_insights.json``
is included with ``"ok": false`` + an ``"error"`` field rather than skipped,
so the agent sees the dropout instead of silently missing a run.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PERF_ROOT = REPO_ROOT / "docs" / "perf"

# Suffix patterns we treat as a "window" tag (3m / 6m / 9m / 12m / 270d / ...)
_WINDOW_RE = re.compile(r"_(\d+[a-zA-Z])$")


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="seconds")


def _infer_window(run_id: str) -> Optional[str]:
    """Pull the trailing window suffix (``_9m`` → ``9m``) from a perf run id.

    Returns ``None`` when the id has no recognisable suffix. The walkforward
    runners stamp this consistently (``<strategy>_<round>_<window>``), but
    older ad-hoc runs may not.
    """
    m = _WINDOW_RE.search(run_id)
    return m.group(1).lower() if m else None


def _load_json(path: Path) -> Optional[Any]:
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def _strategies_in_run(run_path: Path) -> List[str]:
    """Return the strategy ids referenced by this run (from per-fold metrics.json).

    Falls back to ``strategy_configs.json`` when ``metrics.json`` is missing.
    Returns ``[]`` when neither file exists or neither lists strategies.
    """
    found: set[str] = set()
    metrics = _load_json(run_path / "metrics.json")
    if isinstance(metrics, list):
        for row in metrics:
            if isinstance(row, dict) and isinstance(row.get("strategy"), str):
                found.add(row["strategy"])
    if not found:
        cfg = _load_json(run_path / "strategy_configs.json")
        if isinstance(cfg, dict):
            for entry in cfg.get("strategies", []) or []:
                if isinstance(entry, dict) and isinstance(entry.get("name"), str):
                    found.add(entry["name"])
    return sorted(found)


def _symbols_in_run(run_path: Path) -> List[str]:
    """Distinct symbols referenced by the run's per-fold metrics."""
    found: set[str] = set()
    metrics = _load_json(run_path / "metrics.json")
    if isinstance(metrics, list):
        for row in metrics:
            if isinstance(row, dict) and isinstance(row.get("symbol"), str):
                found.add(row["symbol"])
    return sorted(found)


def _headline_from_insights(insights: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Extract the canonical headline numbers from a ``metrics_insights.json``.

    Returns ``None`` when the document is missing the ``grand.equity_summary``
    block — that's the signal that the run wasn't aggregated successfully (the
    walkforward scripts produce it at the end of phase B; an aborted run
    leaves the per-fold ``metrics.json`` but no insights).
    """
    if not isinstance(insights, dict):
        return None
    grand = insights.get("grand") or {}
    eq = grand.get("equity_summary") or {}
    ext = grand.get("extended") or {}
    if not eq:
        return None
    win_rate = ext.get("actual_win_rate")
    if isinstance(win_rate, (int, float)):
        win_rate_pct = round(win_rate * 100.0, 4)
    else:
        win_rate_pct = None
    return {
        "return_pct": eq.get("total_return_pct"),
        "max_drawdown_pct": eq.get("max_drawdown_pct"),
        "max_drawdown_dollars": eq.get("max_drawdown_dollars"),
        "recovery_factor": ext.get("recovery_factor_pnl_vs_seq_dd"),
        "win_rate_pct": win_rate_pct,
        "n_trades": eq.get("n_trades"),
        "final_equity": eq.get("final_equity"),
        "start_equity": eq.get("start_equity"),
    }


def _per_symbol_from_insights(insights: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Per-symbol breakdown if the insights doc carries one.

    The walkforward scripts attach ``grand.per_symbol`` as a list of dicts
    keyed by ``symbol`` with their own ``equity_summary`` / ``extended`` sub-
    sections. Older or smaller runs may omit it — return ``[]`` when missing.
    """
    if not isinstance(insights, dict):
        return []
    grand = insights.get("grand") or {}
    per_sym = grand.get("per_symbol")
    rows: List[Dict[str, Any]] = []
    if isinstance(per_sym, list):
        for entry in per_sym:
            if not isinstance(entry, dict):
                continue
            eq = entry.get("equity_summary") or {}
            ext = entry.get("extended") or {}
            wr = ext.get("actual_win_rate")
            rows.append(
                {
                    "symbol": entry.get("symbol"),
                    "return_pct": eq.get("total_return_pct"),
                    "max_drawdown_pct": eq.get("max_drawdown_pct"),
                    "recovery_factor": ext.get("recovery_factor_pnl_vs_seq_dd"),
                    "win_rate_pct": round(wr * 100.0, 4) if isinstance(wr, (int, float)) else None,
                    "n_trades": eq.get("n_trades"),
                }
            )
    return rows


def _scan_runs(perf_root: Path) -> List[Path]:
    """Return every directory under ``perf_root`` that has a metrics_insights.json.

    Walkforward outputs always include the insights file when the run
    completed; folders without it are either aborted runs or non-perf
    subdirectories (e.g. ``_opt_runs/``, ``_decision_cache/``) and are
    excluded.
    """
    if not perf_root.exists() or not perf_root.is_dir():
        return []
    out: List[Path] = []
    for child in sorted(perf_root.iterdir()):
        if not child.is_dir():
            continue
        if child.name.startswith("_"):
            continue  # _opt_runs, _decision_cache, etc.
        if (child / "metrics_insights.json").exists():
            out.append(child)
    return out


def _delta(curr: Optional[float], prev: Optional[float]) -> Optional[float]:
    """Return ``curr - prev`` rounded to 4 dp, or ``None`` if either is missing."""
    if curr is None or prev is None:
        return None
    try:
        return round(float(curr) - float(prev), 4)
    except (TypeError, ValueError):
        return None


def _compute_vs_previous(
    current: Dict[str, Any],
    candidates: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Pick the most recent same-window run older than ``current`` and diff."""
    if not isinstance(current, dict):
        return None
    current_window = current.get("window")
    current_mtime = current.get("mtime_unix")
    if current_window is None or current_mtime is None:
        return None
    previous: Optional[Dict[str, Any]] = None
    for c in candidates:
        if c is current or not isinstance(c, dict):
            continue
        if c.get("window") != current_window:
            continue
        c_mtime = c.get("mtime_unix")
        if c_mtime is None or c_mtime >= current_mtime:
            continue
        if previous is None or (c_mtime or 0) > (previous.get("mtime_unix") or 0):
            previous = c
    if previous is None:
        return None
    a = current.get("headline") or {}
    b = previous.get("headline") or {}
    return {
        "previous_run_id": previous.get("run_id"),
        "previous_path": previous.get("path"),
        "return_pct_delta": _delta(a.get("return_pct"), b.get("return_pct")),
        "max_drawdown_pct_delta": _delta(a.get("max_drawdown_pct"), b.get("max_drawdown_pct")),
        "recovery_factor_delta": _delta(a.get("recovery_factor"), b.get("recovery_factor")),
        "win_rate_pct_delta": _delta(a.get("win_rate_pct"), b.get("win_rate_pct")),
        "n_trades_delta": _delta(a.get("n_trades"), b.get("n_trades")),
    }


def _row_for_run(
    run_path: Path,
    strategy_filter: Optional[str],
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Build one summary row for a run.

    Returns ``(row, None)`` on success, ``(skip_reason_row, reason)`` when the
    run doesn't match the filter, and ``(error_row, reason)`` when the file
    exists but is malformed. ``skip_reason_row`` is ``None``; ``error_row`` is
    a partial dict so the caller can include it in output.
    """
    strategies = _strategies_in_run(run_path)
    if strategy_filter and strategy_filter not in strategies:
        return None, "strategy_filter"

    insights = _load_json(run_path / "metrics_insights.json")
    headline = _headline_from_insights(insights) if isinstance(insights, dict) else None
    if headline is None:
        return (
            {
                "run_id": run_path.name,
                "path": str(run_path.relative_to(REPO_ROOT)),
                "window": _infer_window(run_path.name),
                "mtime_unix": run_path.stat().st_mtime,
                "mtime_utc": _iso(run_path.stat().st_mtime),
                "strategies": strategies,
                "symbols": _symbols_in_run(run_path),
                "ok": False,
                "error": "metrics_insights.json missing or malformed",
            },
            None,
        )

    return (
        {
            "run_id": run_path.name,
            "path": str(run_path.relative_to(REPO_ROOT)),
            "window": _infer_window(run_path.name),
            "mtime_unix": run_path.stat().st_mtime,
            "mtime_utc": _iso(run_path.stat().st_mtime),
            "strategies": strategies,
            "symbols": _symbols_in_run(run_path),
            "ok": True,
            "headline": headline,
            "per_symbol": _per_symbol_from_insights(insights),
        },
        None,
    )


def _render_table(payload: Dict[str, Any]) -> str:
    """Human-friendly table for the ``--format=table`` output path.

    The JSON output is the canonical agent contract; this rendering is for
    when an operator pipes the tool to a terminal. We keep it deliberately
    minimal — no Rich / Pandas — so the script has zero new dependencies.
    """
    runs = payload.get("runs") or []
    if not runs:
        return "(no runs matched)"
    headers = [
        "run_id",
        "win",
        "mtime",
        "ret%",
        "DD%",
        "RF",
        "WR%",
        "n",
        "Δret%",
        "Δdd%",
    ]
    rows: List[List[str]] = []
    for r in runs:
        h = r.get("headline") or {}
        vs = r.get("vs_previous") or {}
        if not r.get("ok", True):
            rows.append(
                [
                    r.get("run_id", "?"),
                    r.get("window") or "?",
                    (r.get("mtime_utc") or "")[:10],
                    "ERR",
                    "ERR",
                    "ERR",
                    "ERR",
                    "ERR",
                    "",
                    "",
                ]
            )
            continue
        rows.append(
            [
                r.get("run_id", "?"),
                r.get("window") or "?",
                (r.get("mtime_utc") or "")[:10],
                _fmt_num(h.get("return_pct")),
                _fmt_num(h.get("max_drawdown_pct")),
                _fmt_num(h.get("recovery_factor")),
                _fmt_num(h.get("win_rate_pct")),
                _fmt_num(h.get("n_trades"), 0),
                _fmt_signed(vs.get("return_pct_delta")),
                _fmt_signed(vs.get("max_drawdown_pct_delta")),
            ]
        )
    col_widths = [
        max(len(headers[i]), *(len(r[i]) for r in rows)) for i in range(len(headers))
    ]
    sep = "  "
    out = [sep.join(h.ljust(w) for h, w in zip(headers, col_widths))]
    out.append(sep.join("-" * w for w in col_widths))
    for r in rows:
        out.append(sep.join(c.ljust(w) for c, w in zip(r, col_widths)))
    return "\n".join(out)


def _fmt_num(v: Any, digits: int = 2) -> str:
    if v is None:
        return "—"
    if isinstance(v, (int, float)):
        return f"{float(v):.{digits}f}" if digits else f"{int(v)}"
    return str(v)


def _fmt_signed(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, (int, float)):
        return f"{v:+.2f}"
    return str(v)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description=(
            "Single-shot performance summary across ``docs/perf/`` runs. "
            "Built for Cursor agents — emits a stable JSON contract by default "
            "so a single tool call replaces 4-6 file reads."
        )
    )
    ap.add_argument(
        "strategy",
        nargs="?",
        default=None,
        help=(
            "Filter to runs that included this strategy id (e.g. "
            "``morning_range_reversion``, ``overnight_range``, ``body_reversion``). "
            "Omit to include every run under ``docs/perf/``."
        ),
    )
    ap.add_argument(
        "--windows",
        type=str,
        default=None,
        help=(
            "Comma-separated window suffixes to include (e.g. ``3m,6m,9m``). "
            "Compared against the trailing ``_<NUMBER><LETTER>`` suffix of the "
            "run id. Omit to include all windows."
        ),
    )
    ap.add_argument(
        "--perf-root",
        type=Path,
        default=DEFAULT_PERF_ROOT,
        help=f"Override the perf root (default: {DEFAULT_PERF_ROOT.relative_to(REPO_ROOT)}).",
    )
    ap.add_argument(
        "--limit",
        type=int,
        default=None,
        help=(
            "Return only the N most-recent matching runs (sorted by mtime "
            "descending). Helpful when an agent only needs the latest round."
        ),
    )
    ap.add_argument(
        "--format",
        choices=["json", "table"],
        default="json",
        help=(
            "Output shape. ``json`` (default) is the canonical agent contract; "
            "``table`` renders a compact human-friendly summary."
        ),
    )
    ap.add_argument(
        "--include-per-symbol",
        action=argparse.BooleanOptionalAction,
        default=False,
        help=(
            "Include the per-symbol breakdown in the JSON output. Off by "
            "default to keep the payload small; flip ON when comparing how "
            "a strategy split across MNQ / MES / MGC."
        ),
    )
    ap.add_argument(
        "--compare-baseline",
        type=str,
        default=None,
        help=(
            "Run id (folder name under ``docs/perf/``) to use as a forced "
            "comparison baseline for every matched run. Overrides the "
            "default 'most recent same-window run' selector."
        ),
    )
    args = ap.parse_args(argv)

    perf_root: Path = args.perf_root.resolve()
    runs_paths = _scan_runs(perf_root)

    windows_filter: Optional[List[str]] = None
    if args.windows:
        windows_filter = [w.strip().lower() for w in args.windows.split(",") if w.strip()]

    rows: List[Dict[str, Any]] = []
    n_scanned = len(runs_paths)
    for path in runs_paths:
        row, _ = _row_for_run(path, args.strategy)
        if row is None:
            continue
        if windows_filter and (row.get("window") or "").lower() not in windows_filter:
            continue
        rows.append(row)

    rows.sort(key=lambda r: r.get("mtime_unix") or 0.0, reverse=True)

    if args.compare_baseline:
        baseline_path = perf_root / args.compare_baseline
        if baseline_path.exists():
            baseline_row, _ = _row_for_run(baseline_path, None)
            if baseline_row and baseline_row.get("ok"):
                for r in rows:
                    if not r.get("ok"):
                        continue
                    a = r.get("headline") or {}
                    b = baseline_row.get("headline") or {}
                    r["vs_previous"] = {
                        "previous_run_id": baseline_row.get("run_id"),
                        "previous_path": baseline_row.get("path"),
                        "return_pct_delta": _delta(a.get("return_pct"), b.get("return_pct")),
                        "max_drawdown_pct_delta": _delta(a.get("max_drawdown_pct"), b.get("max_drawdown_pct")),
                        "recovery_factor_delta": _delta(a.get("recovery_factor"), b.get("recovery_factor")),
                        "win_rate_pct_delta": _delta(a.get("win_rate_pct"), b.get("win_rate_pct")),
                        "n_trades_delta": _delta(a.get("n_trades"), b.get("n_trades")),
                    }
    else:
        for r in rows:
            if not r.get("ok"):
                continue
            r["vs_previous"] = _compute_vs_previous(r, rows)

    if args.limit is not None and args.limit >= 0:
        rows = rows[: args.limit]

    if not args.include_per_symbol:
        for r in rows:
            r.pop("per_symbol", None)

    payload: Dict[str, Any] = {
        "generated_at_utc": datetime.now(tz=timezone.utc).isoformat(timespec="seconds"),
        "scanned_root": str(perf_root.relative_to(REPO_ROOT)) if perf_root.is_relative_to(REPO_ROOT) else str(perf_root),
        "n_runs_scanned": n_scanned,
        "n_runs_matched": len(rows),
        "filters": {
            "strategy": args.strategy,
            "windows": windows_filter,
            "compare_baseline": args.compare_baseline,
        },
        "runs": rows,
    }

    if args.format == "table":
        print(_render_table(payload))
    else:
        json.dump(payload, sys.stdout, indent=2, default=str)
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
