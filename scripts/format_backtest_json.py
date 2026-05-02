#!/usr/bin/env python3
"""
Pretty-print or tabulate ``core/backtest_executor.py --format=json`` output.

Reads a single JSON object from a file path, or from stdin when path is ``-``
or omitted (useful after piping from the backtest CLI).

Examples:

  # Pretty-print full response (default)
  .venv/bin/python core/backtest_executor.py ... --format=json --include-trades \\
    | .venv/bin/python scripts/format_backtest_json.py

  # Trades only, indented
  .venv/bin/python scripts/format_backtest_json.py --trades-only saved.json

  # CSV suitable for Excel / pandas
  .venv/bin/python scripts/format_backtest_json.py --trades-csv saved.json > trades.csv

  # GitHub-style markdown table (stdout)
  .venv/bin/python scripts/format_backtest_json.py --trades-md saved.json
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any, Dict, List


def _parse_json_loose(text: str) -> Dict[str, Any]:
    text = text.strip()
    if not text:
        raise ValueError("empty input")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        for line in reversed(text.splitlines()):
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                return json.loads(line)
        raise ValueError("no valid JSON object found") from None


def _load(path: str | None) -> Dict[str, Any]:
    if path is None or path == "-":
        return _parse_json_loose(sys.stdin.read())
    p = Path(path)
    return _parse_json_loose(p.read_text(encoding="utf-8"))


def _trades(doc: Dict[str, Any]) -> List[Dict[str, Any]]:
    r = doc.get("result") or {}
    t = r.get("trades")
    return t if isinstance(t, list) else []


def _summary_without_trades(doc: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(doc)
    res = out.get("result")
    if isinstance(res, dict):
        res = dict(res)
        res.pop("trades", None)
        out["result"] = res
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "path",
        nargs="?",
        default=None,
        help="JSON file path, or '-' / omit for stdin",
    )
    g = ap.add_mutually_exclusive_group()
    g.add_argument(
        "--trades-csv",
        action="store_true",
        help="Print result.trades as CSV (header + rows)",
    )
    g.add_argument(
        "--trades-md",
        action="store_true",
        help="Print result.trades as a markdown table",
    )
    g.add_argument(
        "--trades-only",
        action="store_true",
        help="Pretty-print only the trades array (JSON)",
    )
    g.add_argument(
        "--summary",
        action="store_true",
        help="Pretty-print JSON without the trades array (compact overview)",
    )
    args = ap.parse_args()

    try:
        doc = _load(args.path)
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    if args.trades_csv:
        trades = _trades(doc)
        if not trades:
            print("trade_id,symbol,side,entry_time,exit_time,entry_price,exit_price,quantity,pnl,exit_reason")
            return 0
        keys = list(trades[0].keys())
        w = csv.DictWriter(sys.stdout, fieldnames=keys, extrasaction="ignore")
        w.writeheader()
        w.writerows(trades)
        return 0

    if args.trades_md:
        trades = _trades(doc)
        if not trades:
            print("_no trades_")
            return 0
        keys = list(trades[0].keys())
        print("| " + " | ".join(keys) + " |")
        print("| " + " | ".join("---" for _ in keys) + " |")
        for t in trades:
            print("| " + " | ".join(str(t.get(k, "")) for k in keys) + " |")
        return 0

    if args.trades_only:
        print(json.dumps(_trades(doc), indent=2))
        return 0

    if args.summary:
        print(json.dumps(_summary_without_trades(doc), indent=2))
        return 0

    print(json.dumps(doc, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
