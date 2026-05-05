#!/usr/bin/env python3
"""Embed strategy log lines / artifact paragraphs into a parquet file.

Useful for offline retrieval: ``rg --files``-style "what did the bot say
yesterday at 9:30 ET?" with cosine-similarity over `nomic-embed-text` vectors.

Hard rule: research-only — never imported or invoked from a live trading
process. See ``docs/LOCAL_LLM_RESEARCH.md``.

Examples
--------

  # Embed all sweep summaries into one parquet
  scripts/llm_embed_logs.py docs/perf/sweeps/*.json -o docs/perf/sweeps/embeddings.parquet

  # Embed a multi-day strategy log, splitting by line
  scripts/llm_embed_logs.py /tmp/strategy_2026-04-30.log --split-by line \\
      -o docs/perf/embeddings/strategy_2026-04-30.parquet
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Iterable, List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.llm import OllamaClient, OllamaUnavailableError  # noqa: E402


def _split(text: str, mode: str, max_chars: int) -> List[str]:
    text = text.strip()
    if not text:
        return []
    if mode == "line":
        chunks = [ln for ln in text.splitlines() if ln.strip()]
    elif mode == "para":
        chunks = [p.strip() for p in text.split("\n\n") if p.strip()]
    else:
        chunks = [text]
    out: List[str] = []
    for c in chunks:
        if len(c) <= max_chars:
            out.append(c)
        else:
            for i in range(0, len(c), max_chars):
                out.append(c[i : i + max_chars])
    return out


async def _embed_files(
    paths: Iterable[Path],
    *,
    out: Path,
    split_by: str,
    max_chars: int,
    model: str,
) -> int:
    try:
        import pandas as pd
    except ImportError:
        print("pandas is required (already in requirements.txt)", file=sys.stderr)
        return 4

    rows: list[dict] = []
    try:
        async with OllamaClient() as client:
            for path in paths:
                if not path.is_file():
                    print(f"skip: {path} not found", file=sys.stderr)
                    continue
                text = path.read_text(errors="replace")
                chunks = _split(text, split_by, max_chars)
                if not chunks:
                    continue
                vectors = await client.embed(chunks, model=model)
                for chunk, vec in zip(chunks, vectors):
                    rows.append(
                        {
                            "path": str(path),
                            "chunk": chunk,
                            "embedding": vec,
                        }
                    )
                print(f"  embedded {len(chunks):>4} chunks from {path}")
    except OllamaUnavailableError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3

    if not rows:
        print("no chunks embedded", file=sys.stderr)
        return 1
    out.parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(rows)
    if out.suffix.lower() in (".parquet", ".pq"):
        df.to_parquet(out, index=False)
    else:
        df.to_json(out, orient="records", lines=True)
    print(f"wrote {out} ({len(df)} rows)")
    return 0


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("-o", "--out", type=Path, required=True)
    parser.add_argument("--split-by", choices=("line", "para", "file"), default="para")
    parser.add_argument("--max-chars", type=int, default=2000)
    parser.add_argument("--model", default="nomic-embed-text")
    args = parser.parse_args(list(argv) if argv is not None else None)
    return asyncio.run(
        _embed_files(
            args.paths,
            out=args.out,
            split_by=args.split_by,
            max_chars=args.max_chars,
            model=args.model,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
