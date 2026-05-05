"""Thin async client for a local Ollama server (research-only).

Why: we have a stack of local models from ``ollama list``
(``qwen2.5-coder:14b/32b``, ``qwen2.5:7b``, ``glm-4.7-flash``,
``nomic-embed-text``, ``qwen3-embedding:0.6b``) and want to use them for
*research-stage* automation only — summarising backtest JSON, ranking
filter sweeps, embedding logs for retrieval, etc.

This client deliberately implements only what the research scripts need:

- ``generate``: single-prompt completion via ``/api/generate``
- ``chat``:     multi-message chat via ``/api/chat``
- ``embed``:    one or many texts via ``/api/embeddings`` / ``/api/embed``

Hot-path safety
---------------
- No global state, no daemons.
- Default 10-minute hard timeout per call (override with ``OLLAMA_TIMEOUT``).
- Only ``aiohttp`` (already in ``requirements.txt``) — no new deps.
- Raises ``OllamaUnavailableError`` if the server is not reachable so callers
  never silently degrade.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence

import aiohttp

logger = logging.getLogger(__name__)


class OllamaError(RuntimeError):
    """Generic client error (HTTP non-2xx, malformed JSON, etc.)."""


class OllamaUnavailableError(OllamaError):
    """Raised when the Ollama server can't be reached."""


@dataclass
class OllamaResponse:
    text: str
    raw: Dict[str, Any]
    model: str
    eval_count: Optional[int] = None
    prompt_eval_count: Optional[int] = None


class OllamaClient:
    """Async wrapper around the Ollama HTTP API."""

    def __init__(
        self,
        host: Optional[str] = None,
        model: Optional[str] = None,
        timeout: Optional[float] = None,
        session: Optional[aiohttp.ClientSession] = None,
    ) -> None:
        self.host = (host or os.getenv("OLLAMA_HOST", "http://localhost:11434")).rstrip("/")
        self.model = model or os.getenv("OLLAMA_MODEL", "qwen2.5:7b")
        try:
            self.timeout = float(timeout if timeout is not None else os.getenv("OLLAMA_TIMEOUT", "600"))
        except ValueError:
            self.timeout = 600.0
        self._session = session
        self._owns_session = session is None

    async def __aenter__(self) -> "OllamaClient":
        if self._session is None:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self.timeout)
            )
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    async def close(self) -> None:
        if self._owns_session and self._session is not None:
            await self._session.close()
            self._session = None

    # ------------------------------------------------------------------ helpers

    def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self.timeout)
            )
            self._owns_session = True
        return self._session

    async def _post_json(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        sess = self._ensure_session()
        url = f"{self.host}{path}"
        try:
            async with sess.post(url, json=payload) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    raise OllamaError(f"{path} → HTTP {resp.status}: {body[:400]}")
                return await resp.json()
        except aiohttp.ClientConnectorError as exc:
            raise OllamaUnavailableError(f"Ollama not reachable at {self.host}: {exc}") from exc
        except aiohttp.ClientError as exc:
            raise OllamaError(f"{path} client error: {exc}") from exc

    async def list_models(self) -> List[str]:
        sess = self._ensure_session()
        try:
            async with sess.get(f"{self.host}/api/tags") as resp:
                if resp.status != 200:
                    raise OllamaError(f"/api/tags HTTP {resp.status}")
                data = await resp.json()
                return [m.get("name") for m in (data.get("models") or []) if m.get("name")]
        except aiohttp.ClientConnectorError as exc:
            raise OllamaUnavailableError(f"Ollama not reachable at {self.host}: {exc}") from exc

    # ------------------------------------------------------------------ inference

    async def generate(
        self,
        prompt: str,
        *,
        model: Optional[str] = None,
        system: Optional[str] = None,
        temperature: float = 0.2,
        num_ctx: Optional[int] = None,
        stop: Optional[Sequence[str]] = None,
        format_json: bool = False,
    ) -> OllamaResponse:
        payload: Dict[str, Any] = {
            "model": model or self.model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": float(temperature)},
        }
        if num_ctx is not None:
            payload["options"]["num_ctx"] = int(num_ctx)
        if stop:
            payload["options"]["stop"] = list(stop)
        if system:
            payload["system"] = system
        if format_json:
            payload["format"] = "json"
        data = await self._post_json("/api/generate", payload)
        return OllamaResponse(
            text=data.get("response", ""),
            raw=data,
            model=data.get("model", payload["model"]),
            eval_count=data.get("eval_count"),
            prompt_eval_count=data.get("prompt_eval_count"),
        )

    async def chat(
        self,
        messages: Sequence[Dict[str, str]],
        *,
        model: Optional[str] = None,
        temperature: float = 0.2,
        num_ctx: Optional[int] = None,
        format_json: bool = False,
    ) -> OllamaResponse:
        payload: Dict[str, Any] = {
            "model": model or self.model,
            "messages": list(messages),
            "stream": False,
            "options": {"temperature": float(temperature)},
        }
        if num_ctx is not None:
            payload["options"]["num_ctx"] = int(num_ctx)
        if format_json:
            payload["format"] = "json"
        data = await self._post_json("/api/chat", payload)
        msg = data.get("message") or {}
        return OllamaResponse(
            text=msg.get("content", ""),
            raw=data,
            model=data.get("model", payload["model"]),
            eval_count=data.get("eval_count"),
            prompt_eval_count=data.get("prompt_eval_count"),
        )

    async def embed(
        self,
        texts: Iterable[str],
        *,
        model: Optional[str] = None,
    ) -> List[List[float]]:
        out: List[List[float]] = []
        sess = self._ensure_session()
        embed_model = model or os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text")
        for text in texts:
            payload = {"model": embed_model, "prompt": text}
            try:
                async with sess.post(f"{self.host}/api/embeddings", json=payload) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        raise OllamaError(f"/api/embeddings HTTP {resp.status}: {body[:200]}")
                    data = await resp.json()
                    vec = data.get("embedding") or (data.get("embeddings") or [None])[0]
                    if not isinstance(vec, list):
                        raise OllamaError("malformed embedding response (no 'embedding' field)")
                    out.append([float(x) for x in vec])
            except aiohttp.ClientConnectorError as exc:
                raise OllamaUnavailableError(
                    f"Ollama not reachable at {self.host}: {exc}"
                ) from exc
        return out


__all__ = ["OllamaClient", "OllamaResponse", "OllamaError", "OllamaUnavailableError"]
