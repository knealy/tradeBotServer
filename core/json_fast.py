"""Fast JSON helpers: orjson when installed, else stdlib json."""

from __future__ import annotations

import json
from typing import Any, Callable, Union

try:
    import orjson

    _HAS_ORJSON = True
except ImportError:
    orjson = None  # type: ignore[assignment]
    _HAS_ORJSON = False

_DEFAULT: Callable[[Any], Any] = str


def dumps_str(obj: Any, default: Callable[[Any], Any] = _DEFAULT) -> str:
    """Serialize to UTF-8 string (for logs, JSONB text, debug)."""
    if _HAS_ORJSON:
        return orjson.dumps(obj, default=default).decode("utf-8")
    return json.dumps(obj, default=default)


def dumps_bytes(obj: Any, default: Callable[[Any], Any] = _DEFAULT) -> bytes:
    """Serialize to bytes (HTTP bodies)."""
    if _HAS_ORJSON:
        return orjson.dumps(obj, default=default)
    return json.dumps(obj, default=default).encode("utf-8")


def loads(data: Union[str, bytes]) -> Any:
    if _HAS_ORJSON:
        if isinstance(data, str):
            data = data.encode("utf-8")
        return orjson.loads(data)
    if isinstance(data, bytes):
        data = data.decode("utf-8")
    return json.loads(data)


def is_fast_backend() -> bool:
    return _HAS_ORJSON
