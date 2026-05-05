"""Local-LLM helpers for *research-only* tasks.

Hard rule: nothing in ``core.llm`` may be imported from a hot-path module
(``trading_bot``, ``strategies/*``, ``brokers/*``, ``core/event_bus.py``,
``core/order_execution.py``, etc.). Keep usage in ``scripts/llm_*.py``
and notebooks. See ``docs/LOCAL_LLM_RESEARCH.md``.
"""

from .ollama_client import OllamaClient, OllamaError, OllamaUnavailableError

__all__ = ["OllamaClient", "OllamaError", "OllamaUnavailableError"]
