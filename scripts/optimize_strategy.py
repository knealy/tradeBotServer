#!/usr/bin/env python3
"""Generic TOML-sweep harness for any strategy.

Thin wrapper around ``scripts/optimize_overnight_range.py`` that lets the
caller point at a different strategy id + TOML path.  The trial schema is
unchanged::

    {
      "label": "tighten_tp",
      "root":   {"signal.tp_atr_multiplier": 1.5},
      "symbols": {"MGC": {"signal.atr_regime_quantile": 0.65}},
      "env":    {"BODY_REVERSION_SIGNAL_BODY_PCT_MIN": "0.92"},
    }

Per-symbol overrides land in ``[symbols.<SYM>.<section>]`` blocks; root
overrides land in the matching ``[section]`` block.  Trials are run
sequentially because they share the same TOML file (parallel writes
would race).

Usage::

    .venv/bin/python scripts/optimize_strategy.py \
        --strategy body_reversion \
        --trials-json /tmp/body_rev_dd.json \
        --days 270 --folds 9 --symbols MNQ,MGC \
        --out-dir docs/perf/_opt_runs/body_reversion/r1_9m \
        --window-label 9m

Set ``BACKTEST_FAST_LOOP=1`` (default) for the ~10× fast-loop replay
engine; default-on in the underlying walkforward script.
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


# ── TOML patching (lifted verbatim from optimize_overnight_range.py) ────────


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
    """Split ``section.leaf`` → (section, leaf); ``leaf`` alone → ("", "leaf").

    Empty section is the ROOT (top-of-TOML) bucket — used by strategies
    that read flat keys (``cfg.get_int("max_hold_bars")``) rather than the
    section-scoped convention (``cfg.get_int("signal.max_hold_bars")``).
    """
    if "." not in key:
        return "", key
    section, leaf = key.split(".", 1)
    return section, leaf


def _patch_root_keys(text: str, knobs: dict[str, object]) -> str:
    """Edit top-of-file root keys (before any ``[section]`` header).

    Insert as a new line at the very top if the key doesn't exist, or
    replace the value in-place if it does. Root-key edits never cross a
    ``[...]`` header — that's the bucket boundary the section patcher
    handles separately.
    """
    if not knobs:
        return text
    # Slice off everything up to (but not including) the first [section]
    # header so we only operate on the root bucket.
    header_re = re.compile(r"^\[", re.MULTILINE)
    m = header_re.search(text)
    root_block = text[: m.start()] if m else text
    rest = text[m.start():] if m else ""
    for k, v in knobs.items():
        rendered = _render_value(v)
        key_re = re.compile(rf"^\s*{re.escape(k)}\s*=.*$", re.MULTILINE)
        if key_re.search(root_block):
            root_block = key_re.sub(f"{k} = {rendered}", root_block, count=1)
        else:
            root_block = root_block.rstrip() + f"\n{k} = {rendered}\n"
    if not root_block.endswith("\n"):
        root_block += "\n"
    return root_block + rest


def _patch_block_in_text(text: str, block_header: str, knobs: dict[str, object]) -> str:
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
    toml_path: Path,
    root: dict[str, object] | None,
    symbols: dict[str, dict[str, object]] | None,
) -> str:
    original = toml_path.read_text()
    patched = original
    if root:
        by_section: dict[str, dict[str, object]] = {}
        root_keys: dict[str, object] = {}
        for k, v in root.items():
            section, leaf = _split_dotted(k)
            if section == "":
                root_keys[leaf] = v
            else:
                by_section.setdefault(section, {})[leaf] = v
        # Flat root keys first so subsequent [section] inserts don't push
        # the file past them with the wrong relative ordering.
        if root_keys:
            patched = _patch_root_keys(patched, root_keys)
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
    toml_path.write_text(patched)
    return original


async def run_trial(
    label: str,
    strategy: str,
    toml_path: Path,
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
    # Snapshot the TOML to a sibling sentinel BEFORE any write, so a hard
    # interrupt (Ctrl-C, OOM kill, etc.) can be recovered by hand — the
    # ``.optimize_strategy.bak`` file is the safety copy of the last-known
    # original.  ``write_overrides`` itself also returns the original text
    # for the in-process ``finally`` restore.
    backup_path = toml_path.with_suffix(toml_path.suffix + ".optimize_strategy.bak")
    pre_text = toml_path.read_text()
    backup_path.write_text(pre_text)
    original = write_overrides(toml_path, root, symbols)
    try:
        env = os.environ.copy()
        env.setdefault("ENABLE_SIGNALR", "false")
        env.setdefault("BACKTEST_FAST_LOOP", "1")
        env.update(env_overrides or {})
        start = time.monotonic()
        cmd = [
            sys.executable,
            str(ROOT / "scripts/walkforward_trade_recap_report.py"),
            "--strategies", strategy,
            "--symbols", sym_csv,
            "--days", str(days),
            "--folds", str(folds),
            "--last-trades", "0",
            "--out-dir", str(target),
            # Tier 2.1 default: route walk-forward folds through a pre-warmed
            # ``ProcessPoolExecutor`` of in-process backtest workers
            # (``core/backtest/inprocess_runner.py``) instead of fan-out via
            # ``subprocess.run([python, core/backtest_executor.py, ...])``.
            # Each worker pre-loads pandas + numpy + pyarrow + strategy
            # bytecode once and amortizes those ~250-400 ms import costs
            # across every fold it handles. Opt-out for parity debugging by
            # setting env ``OPTIMIZE_STRATEGY_INPROCESS=0``.
            *(["--in-process"] if os.environ.get("OPTIMIZE_STRATEGY_INPROCESS", "1") != "0" else []),
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
        metrics_path = target / "metrics_insights.json"
        if not metrics_path.exists():
            print(f"  ❌ {label}: no metrics_insights.json")
            return {"label": label, "ok": False, "duration_s": duration}
        metrics = json.loads(metrics_path.read_text())
        return {"label": label, "ok": True, "duration_s": duration, "metrics": metrics}
    finally:
        # Best-effort restore.  Even if write fails, ``.optimize_strategy.bak``
        # is the manual recovery copy committed before the patch.
        try:
            toml_path.write_text(original)
        finally:
            try:
                backup_path.unlink()
            except FileNotFoundError:
                pass


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


def summarize(results: list[dict], window_label: str, strategy: str, symbols_csv: str) -> None:
    sym_list = [s.strip().upper() for s in symbols_csv.split(",") if s.strip()]
    print(f"\n📊 {window_label} {strategy} sweep")
    header = f"{'label':<34}{'n':>5}{'ret%':>10}{'dd%':>9}{'rf':>7}{'wr':>9}"
    for sym in sym_list:
        header += f"{sym.lower()+'_n':>7}{sym.lower()+'_ret':>10}"
    header += f"{'t(s)':>7}"
    print(header)
    print("-" * len(header))
    rows = []
    for r in results:
        if not r.get("ok"):
            continue
        m = r["metrics"]
        g = m.get("grand", {})
        ret, dd, rf, wr, n = _row(g)
        sym_stats = {}
        for sym in sym_list:
            k = f"{strategy}|{sym}"
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
        line = f"{r['label']:<34}{n:>5}{ret:>+10.2f}{dd:>9.2f}{rf:>7.2f}{wr:>8.2%}"
        for sym in sym_list:
            line += f"{sym_stats[sym][0]:>7}{sym_stats[sym][1]:>+10.2f}"
        line += f"{r['duration_s']:>7.0f}"
        print(line)
    if rows:
        rows.sort(key=lambda r: (r["ret"] * max(r["rf"], 0), r["ret"]), reverse=True)
        print(f"\n🥇 Top by ret×max(rf,0):")
        for r in rows[:5]:
            print(f"   {r['label']:<34} ret={r['ret']:+.2f}%  dd={r['dd']:.2f}%  rf={r['rf']:5.2f}  wr={r['wr']:.2%}  n={r['n']}")


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", required=True, help="Strategy id (matches config/strategies/<id>.toml)")
    ap.add_argument("--toml-path", default=None, help="Override TOML path (default: config/strategies/<strategy>.toml)")
    ap.add_argument("--days", type=int, default=270)
    ap.add_argument("--folds", type=int, default=9)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--trials-json", required=True)
    ap.add_argument("--window-label", default="9m")
    ap.add_argument("--symbols", default="MNQ,MGC")
    args = ap.parse_args()

    toml_path = Path(args.toml_path) if args.toml_path else ROOT / f"config/strategies/{args.strategy}.toml"
    if not toml_path.exists():
        print(f"❌ TOML not found: {toml_path}")
        sys.exit(2)
    trials = json.loads(Path(args.trials_json).read_text())
    out_root = ROOT / args.out_dir if not Path(args.out_dir).is_absolute() else Path(args.out_dir)
    print(f"🚀 {args.strategy} sweep — {len(trials)} trials, {args.days}d / {args.folds} folds, symbols={args.symbols}")
    results = []
    for i, t in enumerate(trials, 1):
        label = t["label"]
        root = t.get("root", {})
        sym_cfg = t.get("symbols", {})
        env_cfg = t.get("env", {})
        print(f"  [{i}/{len(trials)}] {label}")
        r = await run_trial(
            label, args.strategy, toml_path, root, sym_cfg, env_cfg,
            args.days, args.folds, out_root, args.symbols,
        )
        results.append(r)
        if r["ok"]:
            g = r["metrics"].get("grand", {})
            ret, dd, rf, wr, n = _row(g)
            print(f"    → ret={ret:+.2f}%  dd={dd:.2f}%  rf={rf:.2f}  wr={wr:.2%}  n={n}  ({r['duration_s']:.0f}s)")
    summarize(results, args.window_label, args.strategy, args.symbols)


if __name__ == "__main__":
    asyncio.run(main())
