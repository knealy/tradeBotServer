#!/usr/bin/env python3
"""Generic optimization harness for ``config/strategies/overnight_range.toml``.

Mirrors ``scripts/sweep_per_symbol_breaker.py`` (which is morning-range
specific) but for the overnight breakout strategy.  Each trial is a dict::

    {
      "label": "lift_skipdays",
      "root":   {"filters.skip_weekdays": [], "signal.stop_atr_multiplier": 1.0},
      "symbols": {"MGC": {"signal.stop_atr_multiplier": 0.75,
                          "filters.skip_weekdays": [4]}},
      "env":    {"OVERNIGHT_RANGE_SIGNAL_ATR_TIMEFRAME": "5m"},
    }

Per-symbol overrides land in ``[symbols.<SYM>.<section>]`` blocks (section
inferred from the dotted key); root overrides land in the matching
``[section]`` block.  Skip-weekdays lists render as TOML arrays.

Trials are run sequentially because they share the same TOML file
(parallel writes would race).  Each trial restores the original TOML on
exit, so a Ctrl-C between trials leaves the working tree clean.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
TOML = ROOT / "config/strategies/overnight_range.toml"


def _render_value(v: object) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, list):
        return "[" + ", ".join(
            f'"{item}"' if isinstance(item, str) else str(item) for item in v
        ) + "]"
    if isinstance(v, str):
        return f'"{v}"'
    return str(v)


def _split_dotted(key: str) -> tuple[str, str]:
    """``"signal.stop_atr_multiplier"`` -> ``("signal", "stop_atr_multiplier")``."""
    if "." not in key:
        raise ValueError(f"key must be dotted (e.g. signal.foo): {key!r}")
    section, leaf = key.split(".", 1)
    return section, leaf


def _patch_block_in_text(text: str, block_header: str, knobs: dict[str, object]) -> str:
    """Insert/extend a TOML ``[block_header]`` block with the given knobs.

    Same lazy-match regex as the morning-range sweep harness — stops at
    the next start-of-line ``[`` header so brackets inside comments do
    not prematurely terminate the block.
    """
    if not knobs:
        return text
    block_re = re.compile(
        rf"(^\[{re.escape(block_header)}\].*?)(?=^\[|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    m = block_re.search(text)
    if not m:
        kv = "\n".join(f"{k} = {_render_value(v)}" for k, v in knobs.items())
        return text.rstrip() + f"\n\n[{block_header}]\n{kv}\n"
    block_text = m.group(1)
    for k, v in knobs.items():
        rendered = _render_value(v)
        key_re = re.compile(rf"^\s*{re.escape(k)}\s*=.*$", re.MULTILINE)
        if key_re.search(block_text):
            block_text = key_re.sub(f"{k} = {rendered}", block_text, count=1)
        else:
            block_text = block_text.rstrip() + f"\n{k} = {rendered}\n"
    block_text = block_text.rstrip() + "\n\n"
    return text[: m.start()] + block_text + text[m.end():]


def write_overrides(
    root: dict[str, object] | None,
    symbols: dict[str, dict[str, object]] | None,
) -> str:
    """Apply root + per-symbol overrides; return the ORIGINAL text for restore."""
    original = TOML.read_text()
    patched = original
    if root:
        by_section: dict[str, dict[str, object]] = {}
        for k, v in root.items():
            section, leaf = _split_dotted(k)
            by_section.setdefault(section, {})[leaf] = v
        for section, knobs in by_section.items():
            patched = _patch_block_in_text(patched, section, knobs)
    if symbols:
        for sym, kvs in symbols.items():
            if not kvs:
                continue
            by_section = {}
            for k, v in kvs.items():
                section, leaf = _split_dotted(k)
                by_section.setdefault(section, {})[leaf] = v
            for section, knobs in by_section.items():
                patched = _patch_block_in_text(patched, f"symbols.{sym}.{section}", knobs)
    TOML.write_text(patched)
    return original


async def run_trial(
    label: str,
    root: dict[str, object] | None,
    symbols: dict[str, dict[str, object]] | None,
    env_overrides: dict[str, str],
    days: int,
    folds: int,
    out_root: Path,
    sym_csv: str,
) -> dict:
    target = out_root / label
    target.mkdir(parents=True, exist_ok=True)
    original = write_overrides(root, symbols)
    try:
        env = os.environ.copy()
        env.setdefault("ENABLE_SIGNALR", "false")
        env.update(env_overrides or {})
        start = time.monotonic()
        cmd = [
            sys.executable,
            str(ROOT / "scripts/walkforward_trade_recap_report.py"),
            "--strategies", "overnight_range",
            "--symbols", sym_csv,
            "--days", str(days),
            "--folds", str(folds),
            "--out-dir", str(target),
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=str(ROOT), env=env,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        duration = time.monotonic() - start
        if proc.returncode != 0:
            print(f"  ❌ {label} failed:\n{stderr.decode()[:500]}")
            return {"label": label, "ok": False, "duration_s": duration}
        metrics = json.loads((target / "metrics_insights.json").read_text())
        return {"label": label, "ok": True, "duration_s": duration, "metrics": metrics}
    finally:
        TOML.write_text(original)


def _row(g: dict) -> tuple[float, float, float, float, int]:
    eq = g.get("equity_summary", {})
    ex = g.get("extended", {})
    return (
        float(eq.get("total_return_pct", 0) or 0),
        float(eq.get("max_drawdown_pct", 0) or 0),
        float(ex.get("recovery_factor_pnl_vs_seq_dd", 0) or 0),
        float(ex.get("actual_win_rate", 0) or 0),
        int(eq.get("n_trades", 0) or 0),
    )


def summarize(results: list[dict], window_label: str) -> None:
    print(f"\n📊 {window_label} overnight_range sweep")
    print(f"{'label':<34}{'n':>5}{'ret%':>10}{'dd%':>9}{'rf':>7}{'wr':>9}"
          f"{'mnq_n':>7}{'mnq_ret':>10}{'mes_n':>7}{'mes_ret':>10}{'mgc_n':>7}{'mgc_ret':>10}{'t(s)':>7}")
    print("-" * 130)
    rows = []
    for r in results:
        if not r.get("ok"):
            continue
        m = r["metrics"]
        g = m.get("grand", {})
        ret, dd, rf, wr, n = _row(g)
        sym_stats = {}
        for sym in ("MNQ", "MES", "MGC"):
            k = f"overnight_range|{sym}"
            if k in m.get("per_strategy_symbol", {}):
                es = m["per_strategy_symbol"][k]["equity_summary"]
                sym_stats[sym] = (int(es.get("n_trades", 0) or 0),
                                  float(es.get("total_return_pct", 0) or 0))
            else:
                sym_stats[sym] = (0, 0.0)
        rows.append({
            "label": r["label"], "n": n, "ret": ret, "dd": dd, "rf": rf, "wr": wr,
            "sym": sym_stats, "dur": r["duration_s"],
        })
        print(
            f"{r['label']:<34}{n:>5}{ret:>+10.2f}{dd:>9.2f}{rf:>7.2f}{wr:>8.2%}"
            f"{sym_stats['MNQ'][0]:>7}{sym_stats['MNQ'][1]:>+10.2f}"
            f"{sym_stats['MES'][0]:>7}{sym_stats['MES'][1]:>+10.2f}"
            f"{sym_stats['MGC'][0]:>7}{sym_stats['MGC'][1]:>+10.2f}"
            f"{r['duration_s']:>7.0f}"
        )
    if rows:
        rows.sort(key=lambda r: (r["ret"] * max(r["rf"], 0), r["ret"]), reverse=True)
        print(f"\n🥇 Top by ret×max(rf,0):")
        for r in rows[:5]:
            print(f"   {r['label']:<34} ret={r['ret']:+7.2f}%  dd={r['dd']:5.2f}%  rf={r['rf']:5.2f}  wr={r['wr']:5.2%}  n={r['n']}")


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=270)
    ap.add_argument("--folds", type=int, default=9)
    ap.add_argument("--out-dir", default="docs/perf/_opt_runs/overnight")
    ap.add_argument("--trials-json", required=True)
    ap.add_argument("--window-label", default="9m")
    ap.add_argument("--symbols", default="MNQ,MES,MGC")
    args = ap.parse_args()

    trials = json.loads(Path(args.trials_json).read_text())
    out_root = ROOT / args.out_dir
    print(f"🚀 overnight_range sweep — {len(trials)} trials, {args.days}d / {args.folds} folds, symbols={args.symbols}")
    results = []
    for i, t in enumerate(trials, 1):
        label = t["label"]
        root = t.get("root", {})
        sym_cfg = t.get("symbols", {})
        env_cfg = t.get("env", {})
        print(f"  [{i}/{len(trials)}] {label}")
        r = await run_trial(label, root, sym_cfg, env_cfg, args.days, args.folds, out_root, args.symbols)
        results.append(r)
        if r["ok"]:
            g = r["metrics"].get("grand", {})
            ret, dd, rf, wr, n = _row(g)
            print(f"    → ret={ret:+.2f}%  dd={dd:.2f}%  rf={rf:.2f}  wr={wr:.2%}  n={n}  ({r['duration_s']:.0f}s)")
    summarize(results, args.window_label)


if __name__ == "__main__":
    asyncio.run(main())
