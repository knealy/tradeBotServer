# Local LLM (Ollama) — research-only helper

**Hard rule.** Anything in [`core/llm/`](../core/llm/) and `scripts/llm_*.py`
is for *research workflows* only. None of it may be imported by, or called
from, any module on the live trading hot path:

- `trading_bot.py`
- `strategies/*` (the live strategy classes)
- `brokers/*`
- `core/event_bus.py`, `core/order_execution.py`, `core/risk_management.py`
- `servers/*` request handlers that touch the broker

Concretely: an LLM never decides to place, modify, or cancel a real order.
It can summarise backtest results, propose grids, critique reports, embed
log lines for retrieval — and that is it.

---

## Why we have it

Inference work that does **not** need a frontier model:

- Convert sweep TSV / backtest JSON into a labelled markdown brief.
- Critique an alpha-discovery report (find what it might have missed).
- Propose a small parameter grid for `core.research.runner` based on
  observed metrics.
- Embed strategy logs / sweep notes for retrieval.

These tasks are well within the capability of the local Qwen models the
operator already has installed:

```
qwen2.5-coder:32b           # heavy code summaries
qwen2.5-coder:14b           # default code summaries
qwen2.5:7b                  # fast general summaries (default for llm_review.py)
glm-4.7-flash:latest        # alt general model
qwen3:32b                   # heavier general model
nomic-embed-text:latest     # default embeddings
qwen3-embedding:0.6b        # alt embeddings
```

Run `ollama list` to see what's currently pulled.

---

## Components

| Path | Purpose |
|------|---------|
| [`core/llm/__init__.py`](../core/llm/__init__.py) | Public re-exports + the “research-only” banner. |
| [`core/llm/ollama_client.py`](../core/llm/ollama_client.py) | `OllamaClient` — async `generate` / `chat` / `embed` over `http://localhost:11434`. |
| [`scripts/llm_review.py`](../scripts/llm_review.py) | Summarise / rank / critique / propose-grid for a backtest JSON, alpha report, or sweep TSV. |
| [`scripts/llm_embed_logs.py`](../scripts/llm_embed_logs.py) | Embed log / artifact paragraphs into a parquet file (offline retrieval). |

The client only depends on `aiohttp`, which is already in
[`requirements.txt`](../requirements.txt). No new dependencies.

---

## Environment

| Var | Default | Notes |
|-----|---------|-------|
| `OLLAMA_HOST` | `http://localhost:11434` | Must include scheme + port. |
| `OLLAMA_MODEL` | `qwen2.5:7b` | Default chat / generate model. |
| `OLLAMA_EMBED_MODEL` | `nomic-embed-text` | Embedding model. |
| `OLLAMA_TIMEOUT` | `600` (seconds) | Per-call hard timeout. |

---

## Quick start

```bash
# Summarise the no-filters overnight_range backtest into a sibling .md
.venv/bin/python scripts/llm_review.py \
  docs/perf/sweeps/overnight_range_MNQ_no_filters.json

# Compare the full P0..P5 sweep with the bigger code model
OLLAMA_MODEL=qwen2.5-coder:14b .venv/bin/python scripts/llm_review.py \
  --task rank \
  docs/perf/sweeps/overnight_range_MNQ_sweep_summary.tsv

# Critique an alpha report (write to a custom path)
.venv/bin/python scripts/llm_review.py --task critique \
  --out docs/alpha/MNQ.critique.md docs/alpha/MNQ.md

# Propose a grid for a new sweep
.venv/bin/python scripts/llm_review.py --task propose-grid \
  docs/perf/sweeps/strategy_matrix_MNQ_summary.tsv

# Embed sweep summaries for offline retrieval
.venv/bin/python scripts/llm_embed_logs.py docs/perf/sweeps/*.json \
  -o docs/perf/sweeps/embeddings.parquet
```

---

## Prompts

`scripts/llm_review.py` ships with system prompts for each task; override
with `--system "…"` if you want to A/B a different framing.

Prompt rules:

- Never paste secrets (API keys, account numbers, full P&L histories) into
  the prompt — the artifact-loader purposely keeps only public metric keys
  and a small `sample_trades` slice.
- Mention "do not invent numbers" in the system prompt; we follow that
  convention in `SYSTEM_PROMPTS` already.
- Set a low temperature (default `0.2`) for deterministic summaries; bump
  to `0.6+` only when explicitly brainstorming.

---

## Why this is *not* in the hot path

- **Latency**. Even the fastest local model adds 100s+ of milliseconds; live
  ticks need to settle in < 50 ms.
- **Variance**. Same prompt + temperature 0 still drifts across model
  releases; live order decisions must be reproducible from code + config.
- **Auditability**. Trade decisions need a deterministic, code-reviewed
  rule. LLM rationales are a "post-hoc" artifact, not the rule.
- **Failure mode**. Ollama can be down, slow, or paged out. None of those
  conditions can be allowed to gate live order routing.

If you ever feel tempted to wire an LLM into a strategy, write the rule it
suggested into `strategies/<name>_strategy.py`, A/B-test the rule, then
delete the LLM call.

---

## See also

- [docs/RESEARCH_PATHWAYS.md](RESEARCH_PATHWAYS.md) — pathway map for
  research vs production.
- [docs/perf/researching.md](perf/researching.md) — open research links and
  starter ideas.
- [docs/ALPHA_DISCOVERY.md](ALPHA_DISCOVERY.md) — feature-level alpha
  discovery process the LLM helpers consume.
