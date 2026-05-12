"""OHLCV helpers for replay-style backtests (vectorized where possible)."""

from __future__ import annotations

import math
from typing import Any, Dict, List

import pandas as pd


def replay_bars_from_ohlcv_df(data: pd.DataFrame) -> List[Dict[str, Any]]:
    """
    Convert an indexed OHLCV DataFrame to the list[dict] shape ``StrategyReplayEngine`` expects.

    Faster than ``iterrows`` for large frames (numpy-backed scan).
    """
    if data is None or len(data) == 0:
        return []
    idx = data.index
    o = data["open"].to_numpy(dtype=float, copy=False)
    h = data["high"].to_numpy(dtype=float, copy=False)
    lo = data["low"].to_numpy(dtype=float, copy=False)
    c = data["close"].to_numpy(dtype=float, copy=False)
    v = data["volume"].to_numpy(copy=False)
    n = len(data)
    out: List[Dict[str, Any]] = []
    for i in range(n):
        ts = pd.Timestamp(idx[i]).to_pydatetime()
        vol = float(v[i])
        ivol = 0 if math.isnan(vol) else int(vol)
        out.append(
            {
                "timestamp": ts,
                "open": float(o[i]),
                "high": float(h[i]),
                "low": float(lo[i]),
                "close": float(c[i]),
                "volume": ivol,
            }
        )
    return out
