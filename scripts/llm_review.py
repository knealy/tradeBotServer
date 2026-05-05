#!/usr/bin/env python3
"""Summarise / rank / critique research artifacts via a local Ollama model.

Examples
--------

  # Summarise one backtest JSON into a markdown brief next to it
  scripts/llm_review.py docs/perf/sweeps/overnight_range_MNQ_no_filters.json

  # Rank a sweep TSV (compare metrics across steps) using the bigger code model
  scripts/llm_review.py --task rank --model qwen2.5-coder:14b \\
      docs/perf/sweeps/overnight_range_MNQ_sweep_summary.tsv

  # Critique an alpha report
  scripts/llm_review.py --task critique docs/alpha/MNQ.md

  # Propose a TOML grid based on observed metrics
  scripts/llm_review.py --task propose-grid docs/perf/sweeps/strategy_matrix_summary.tsv

Hard rule: this script is research-only. Nothing it writes is consumed by
``trading_bot.py`` or anything in the live order path. See
``docs/LOCAL_LLM_RESEARCH.md`` for prompts, model menu, and security notes.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Iterable, List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.llm import OllamaClient, OllamaUnavailableError  # noqa: E402


SYSTEM_PROMPTS = {
    "summarize": (
        "You are a quantitative trading research assistant. Summarise the input "
        "artifact (backtest JSON, alpha-discovery markdown, or sweep TSV) in 8–14 "
        "concise bullet points. Cover: trade count, win rate, total PnL, max drawdown, "
        "Sharpe, period, any obvious tail risk, and the *next experiment* you'd run. "
        "Never invent numbers — quote what's in the artifact."
    ),
    "rank": (
        "You are a quantitative trading research assistant. The artifact is a sweep "
        "of strategy variants. Rank them best→worst on a Sharpe / DD / WR composite, "
        "explain the trade-offs, and call out any variant that looks overfit (extreme "
        "win rate on tiny n, or PnL driven by a single trade). Never invent numbers."
    ),
    "critique": (
        "You are a skeptical quant. Read the artifact and list the strongest reasons "
        "it might be wrong (selection bias, lookahead, small-n, regime fit, simulator "
        "vs broker mismatch). Suggest 2–3 cheap experiments to falsify the result."
    ),
    "propose-grid": (
        "You are a research engineer. Propose a parameter grid (filter bands, "
        "ATR multipliers, lookback windows, weekday gates) the operator should sweep "
        "next, given the artifact. Output a fenced TOML or shell snippet they can "
        "paste into config/strategies/*.toml or scripts/run_overnight_range_sweep.sh."
    ),
}

DEFAULT_MODEL = "qwen2.5:7b"


def _read_artifact(path: Path) -> str:
    text = path.read_text(errors="replace")
    if path.suffix == ".json":
        try:
            obj = json.loads(text.strip().splitlines()[-1])
        except Exception:
            return text
        if isinstance(obj, dict) and "result" in obj:
            r = obj["result"] or {}
            trades = r.get("trades") or r.get("all_trades") or []
            slim = {
                "ok": obj.get("ok"),
                "result": {
                    k: r.get(k)
                    for k in (
                        "symbol",
                        "strategy_name",
                        "period",
                        "total_trades",
                        "total_pnl",
                        "win_rate",
                        "sharpe_ratio",
                        "max_drawdown",
                        "profit_factor",
                        "avg_win",
                        "avg_loss",
                    )
                    if k in r
                },
            }
            if trades:
                slim["sample_trades"] = trades[:6]
                slim["trade_count"] = len(trades)
            return json.dumps(slim, indent=2, default=str)
        return json.dumps(obj, indent=2, default=str)
    return text


def _output_path(input_path: Path, task: str, override: Optional[Path]) -> Path:
    if override is not None:
        return override
    suffix = f".{task}.md"
    return input_path.with_suffix(input_path.suffix + suffix)


async def _review(
    inputs: List[Path],
    *,
    task: str,
    model: str,
    system: Optional[str],
    out: Optional[Path],
    temperature: float,
    num_ctx: Optional[int],
) -> int:
    sys_prompt = system or SYSTEM_PROMPTS.get(task)
    if sys_prompt is None:
        print(f"unknown task '{task}' (use one of {list(SYSTEM_PROMPTS)})", file=sys.stderr)
        return 2
    try:
        async with OllamaClient(model=model) as client:
            try:
                models = await client.list_models()
                if model not in models and not any(m.startswith(model.split(":", 1)[0]) for m in models):
                    print(
                        f"warn: '{model}' not in local Ollama tags ({', '.join(models[:8])}…)",
                        file=sys.stderr,
                    )
            except OllamaUnavailableError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 3

            for path in inputs:
                if not path.exists():
                    print(f"skip: {path} not found", file=sys.stderr)
                    continue
                artifact = _read_artifact(path)
                user_prompt = (
                    f"# {task.upper()} — {path.name}\n\n"
                    "Artifact follows between the markers; treat it as read-only "
                    "evidence, do not invent fields.\n\n"
                    "----- ARTIFACT BEGIN -----\n"
                    f"{artifact}\n"
                    "----- ARTIFACT END -----\n"
                )
                resp = await client.generate(
                    user_prompt,
                    system=sys_prompt,
                    temperature=temperature,
                    num_ctx=num_ctx,
                )
                target = _output_path(path, task, out if len(inputs) == 1 else None)
                target.parent.mkdir(parents=True, exist_ok=True)
                header = (
                    f"<!-- generated by scripts/llm_review.py task={task} "
                    f"model={resp.model} eval_count={resp.eval_count or 0} -->\n\n"
                )
                target.write_text(header + resp.text.rstrip() + "\n")
                print(f"wrote {target}")
    except OllamaUnavailableError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3
    return 0


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("inputs", nargs="+", type=Path)
    parser.add_argument(
        "--task",
        choices=sorted(SYSTEM_PROMPTS),
        default="summarize",
    )
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"Ollama model tag (default {DEFAULT_MODEL}).")
    parser.add_argument("--system", default=None, help="Override the system prompt.")
    parser.add_argument("--out", type=Path, default=None, help="Write to this single file (only with one input).")
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--num-ctx", type=int, default=None, help="Override Ollama num_ctx (model dependent).")
    args = parser.parse_args(list(argv) if argv is not None else None)
    return asyncio.run(
        _review(
            list(args.inputs),
            task=args.task,
            model=args.model,
            system=args.system,
            out=args.out,
            temperature=args.temperature,
            num_ctx=args.num_ctx,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
