#!/usr/bin/env python3
"""Per-symbol consec-loss breaker sweep.

Per-symbol TOML overrides win over env vars in
``StrategyConfig.symbol_override`` precedence, so this wrapper writes the
breaker config directly into the ``[symbols.<SYM>.signal]`` block(s) of
``config/strategies/morning_range_reversion.toml``, runs the walk-forward,
and restores the TOML on exit.

Trials are described by a list of dicts like::

    {"label": "mgc_only_mcl2_cd10",
     "symbols": {"MGC": {"max_consecutive_losses": 2,
                          "loss_streak_cooldown_sessions": 10}},
     "env": {"MORNING_RANGE_REVERSION_SIGNAL_LOOKBACK_BARS": "2000"}}

The optional ``env`` dict is merged into the subprocess environment for
the trial (handy when sweeping ROOT-level knobs the regime-detection
layer needs, e.g. wider intraday bar buffer for KER computation).

Run sequentially to avoid races on the shared TOML file.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
TOML = ROOT / "config/strategies/morning_range_reversion.toml"


_SECTION_KEYS = {
    # Position-size + per-trade risk knobs live under ``[symbols.<SYM>.risk]``.
    "position_size": "risk",
    "risk_per_trade_pct": "risk",
    "max_positions": "risk",
    "max_daily_trades": "risk",
}


def _section_for(knob: str) -> str:
    """Return the per-symbol TOML section the knob belongs to.

    Defaults to ``signal`` (the historical default for this sweep harness);
    routes risk-management knobs to ``[symbols.<SYM>.risk]`` so the
    weighting sweep can drop ``position_size`` overrides into the right block.
    """
    return _SECTION_KEYS.get(knob, "signal")


def insert_per_symbol_overrides(symbol_overrides: dict[str, dict[str, int]]) -> str:
    """Patch the TOML by inserting/extending the appropriate
    ``[symbols.<SYM>.<section>]`` block for each knob. Returns the original
    TOML text for restoration.

    Scoped to the *exact* ``[symbols.<SYM>.<section>]`` block — earlier
    versions of this helper checked for key existence anywhere in the
    file, which silently clobbered the FIRST symbol's injection when
    multiple symbols were being patched in one trial.

    Round-24: now multi-section aware (knobs like ``position_size`` route
    to ``[symbols.<SYM>.risk]``; signal-layer knobs continue to land in
    ``[symbols.<SYM>.signal]``).
    """
    original = TOML.read_text()
    if not symbol_overrides:
        return original
    patched = original
    for sym, knobs in symbol_overrides.items():
        if not knobs:
            continue
        # Group knobs by destination section so each block is patched once.
        by_section: dict[str, dict[str, object]] = {}
        for k, v in knobs.items():
            by_section.setdefault(_section_for(k), {})[k] = v
        for section, section_knobs in by_section.items():
            patched = _patch_block(patched, sym, section, section_knobs)
    TOML.write_text(patched)
    return original


def _patch_block(patched: str, sym: str, section: str, knobs: dict[str, object]) -> str:
    """Helper: insert/update knobs in ``[symbols.<SYM>.<section>]``."""
    if not knobs:
        return patched
    block_header = f"[symbols.{sym}.{section}]"
    # Match the section header at start-of-line, then everything up to the next
    # section header (any ``[xxx]`` at start of line) or EOF.  ``.*?`` is lazy
    # so we stop at the very next section header — avoids the previous
    # ``[^\[]*`` pitfall where a ``[`` inside a TOML comment (e.g.
    # ``["Mon","Thu","Fri"]`` referenced in a comment line) prematurely
    # terminated the block.
    block_re = re.compile(
        rf"(^\[symbols\.{re.escape(sym)}\.{re.escape(section)}\].*?)(?=^\[|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    def _render(v: object) -> str:
        if isinstance(v, list):
            return "[" + ", ".join(
                f'"{item}"' if isinstance(item, str) else str(item) for item in v
            ) + "]"
        if isinstance(v, str):
            return f'"{v}"'
        return str(v)

    m = block_re.search(patched)
    if not m:
        kv_lines = "\n".join(f"{k} = {_render(v)}" for k, v in knobs.items())
        return patched.rstrip() + f"\n\n{block_header}\n{kv_lines}\n"
    block_text = m.group(1)
    for k, v in knobs.items():
        rendered = _render(v)
        key_re = re.compile(rf"^\s*{re.escape(k)}\s*=.*$", re.MULTILINE)
        if key_re.search(block_text):
            block_text = key_re.sub(f"{k} = {rendered}", block_text, count=1)
        else:
            block_text = block_text.rstrip() + f"\n{k} = {rendered}\n"
    block_text = block_text.rstrip() + "\n\n"
    return patched[: m.start()] + block_text + patched[m.end():]


async def run_one(label: str, symbols: dict[str, dict[str, int]],
                   env_overrides: dict[str, str], days: int, folds: int,
                   out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / label
    original = insert_per_symbol_overrides(symbols)
    try:
        env = os.environ.copy()
        env.setdefault("ENABLE_SIGNALR", "false")
        env.update(env_overrides or {})
        start = time.monotonic()
        cmd = [
            sys.executable, str(ROOT / "scripts/walkforward_trade_recap_report.py"),
            "--strategies", "morning_range_reversion",
            "--symbols", "MNQ,MES,MGC",
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


def summarize(results: list[dict], window_label: str) -> None:
    print(f"\n📊 {window_label} per-symbol breaker sweep")
    print(f"{'label':<28}{'ret%':>10}{'dd%':>8}{'rf':>7}{'wr':>9}"
          f"{'mnq_dd':>10}{'mes_dd':>10}{'mgc_dd':>10}{'mgc_ret':>10}{'mgc_n':>7}")
    print("-" * 110)
    for r in results:
        if not r["ok"]:
            continue
        m = r["metrics"]
        ov = m.get("overall_equity", {})
        rows = m.get("per_strategy_symbol", {})
        first_key = next(iter(rows))
        first_ext = rows[first_key]["extended"]
        sym_dd = {}
        sym_ret = {}
        sym_n = {}
        for sym in ("MNQ", "MES", "MGC"):
            k = next((kk for kk in rows if sym in kk), None)
            sym_dd[sym] = rows[k]["equity_summary"]["max_drawdown_pct"] if k else 0
            sym_ret[sym] = rows[k]["equity_summary"]["total_return_pct"] if k else 0
            sym_n[sym] = rows[k]["equity_summary"]["n_trades"] if k else 0
        print(
            f"{r['label']:<28}{ov.get('total_return_pct', 0):>+10.2f}{ov.get('max_drawdown_pct', 0):>8.2f}"
            f"{first_ext.get('recovery_factor_pnl_vs_seq_dd', 0):>7.2f}{first_ext.get('actual_win_rate', 0):>8.2%}"
            f"{sym_dd['MNQ']:>10.2f}{sym_dd['MES']:>10.2f}{sym_dd['MGC']:>10.2f}{sym_ret['MGC']:>+10.2f}{sym_n['MGC']:>7}"
        )


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=270)
    ap.add_argument("--folds", type=int, default=9)
    ap.add_argument("--out-dir", default="docs/perf/_opt_runs/per_symbol_breaker")
    ap.add_argument("--trials-json", required=True)
    ap.add_argument("--window-label", default="9m")
    args = ap.parse_args()

    trials = json.loads(Path(args.trials_json).read_text())
    out_root = ROOT / args.out_dir
    print(f"🚀 Per-symbol breaker sweep — {len(trials)} trials, {args.days}d / {args.folds} folds")
    results = []
    for i, t in enumerate(trials, 1):
        label = t["label"]
        sym_cfg = t.get("symbols", {})
        env_cfg = t.get("env", {})
        print(f"  [{i}/{len(trials)}] {label}")
        r = await run_one(label, sym_cfg, env_cfg, args.days, args.folds, out_root)
        results.append(r)
        if r["ok"]:
            ov = r["metrics"].get("overall_equity", {})
            print(f"    → ret={ov.get('total_return_pct', 0):+.2f}%  dd={ov.get('max_drawdown_pct', 0):.2f}%  ({r['duration_s']:.0f}s)")
    summarize(results, args.window_label)


if __name__ == "__main__":
    asyncio.run(main())
