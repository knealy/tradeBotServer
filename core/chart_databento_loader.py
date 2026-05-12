"""
Load recent OHLCV bars from canonical Databento CSVs for the Master GUI chart.

Paths (see ``scripts/databento_stitch_canonical.py``):

  historical_data/price/{MNQ,MES,MGC}_1m_databento.csv
  historical_data/price/{MNQ,MES,MGC}_5m_databento.csv

Only **root** symbols MNQ / MES / MGC (including contract-style prefixes like ``MNQM5``).
"""

from __future__ import annotations

import io
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_ROOTS = ("MNQ", "MES", "MGC")
_TF_RE = re.compile(r"^(\d+)\s*([smhd])$", re.I)


def chart_symbol_to_databento_root(symbol: str) -> Optional[str]:
    """Map chart / contract symbol to MNQ, MES, or MGC if a canonical file exists."""
    s = (symbol or "").strip().upper()
    if not s:
        return None
    for r in _ROOTS:
        if s == r or s.startswith(r):
            return r
    return None


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _timeframe_seconds(tf: str) -> int:
    m = _TF_RE.match((tf or "1m").strip().lower())
    if not m:
        return 60
    n, u = int(m.group(1)), m.group(2).lower()
    mult = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    return max(1, n * mult[u])


def _read_csv_header_line(path: Path) -> str:
    with path.open("r", encoding="utf-8", errors="replace") as f:
        return f.readline().rstrip("\r\n")


def _tail_csv_as_dataframe(path: Path, n_data_rows: int) -> "Any":
    """Read the last ``n_data_rows`` data rows plus header (no full-file scan)."""
    import pandas as pd

    if n_data_rows <= 0:
        return pd.DataFrame()
    header = _read_csv_header_line(path)
    size = path.stat().st_size
    if size <= 0:
        return pd.DataFrame()
    # Grow a binary buffer from EOF until we likely have enough complete lines.
    read_sz = min(size, max(64 * 1024, n_data_rows * 96 + 4096))
    pos = size
    buf = b""
    with path.open("rb") as f:
        while pos > 0:
            step = min(read_sz, pos)
            pos -= step
            f.seek(pos)
            buf = f.read(step) + buf
            if buf.count(b"\n") >= n_data_rows + 2 or pos == 0:
                break
            read_sz = min(read_sz * 2, size)
        if pos > 0:
            first_nl = buf.find(b"\n")
            if first_nl != -1:
                buf = buf[first_nl + 1 :]
    text = buf.decode("utf-8", errors="replace")
    lines = text.splitlines()
    tail = lines[-n_data_rows:] if len(lines) >= n_data_rows else lines
    body = header + "\n" + "\n".join(tail)
    return pd.read_csv(io.StringIO(body))


def _df_to_chart_rows(df: Any) -> List[Dict[str, Any]]:
    """DataFrame indexed by timestamp (naive UTC) -> list for ``/api/chart/reload``."""
    import pandas as pd

    out: List[Dict[str, Any]] = []
    if df is None or len(df) == 0:
        return out
    for ts, row in df.iterrows():
        if isinstance(ts, pd.Timestamp):
            tsec = int(ts.timestamp())
        else:
            tsec = int(pd.Timestamp(ts).timestamp())
        out.append(
            {
                "time": tsec,
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row.get("volume", 0) or 0),
            }
        )
    return out


def _normalize_ohlcv_index(df: Any) -> Any:
    import pandas as pd

    if df is None or len(df) == 0:
        return df
    if "timestamp" in df.columns:
        df = df.copy()
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True).dt.tz_convert(None)
        df = df.set_index("timestamp").sort_index()
    return df


def load_databento_bars_for_chart(
    symbol: str,
    timeframe: str,
    limit: int,
    repo_root: Optional[Path] = None,
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """
    Return ``(bars, error_message)``. ``bars`` is empty if unavailable.

    Prefers ``*_5m_databento.csv`` when the chart timeframe is **5m**; otherwise
    tails ``*_1m_databento.csv`` and resamples (or uses 5m then resamples up if
    1m is missing).
    """
    from core.backtest.data_loader import HistoricalDataLoader

    root = chart_symbol_to_databento_root(symbol)
    if not root:
        return [], "not_mnq_mes_mgc"
    root_path = repo_root or _repo_root()
    price = root_path / "historical_data" / "price"
    p1 = price / f"{root}_1m_databento.csv"
    p5 = price / f"{root}_5m_databento.csv"
    tf = (timeframe or "5m").strip().lower()
    limit = max(1, min(int(limit), 5000))
    loader = HistoricalDataLoader()

    try:
        if tf == "5m" and p5.is_file():
            df = _normalize_ohlcv_index(_tail_csv_as_dataframe(p5, limit))
        elif p1.is_file():
            bar_sec = _timeframe_seconds(tf)
            mult = max(1, (bar_sec + 59) // 60)
            need_1m = min(limit * mult + mult * 4 + 120, 2_000_000)
            n_tail = limit if tf == "1m" else need_1m
            df = _normalize_ohlcv_index(_tail_csv_as_dataframe(p1, n_tail))
            if tf != "1m" and len(df) > 0:
                df = loader.resample(df, tf)
            df = df.iloc[-limit:]
        elif p5.is_file():
            tf_sec = _timeframe_seconds(tf)
            need_5m = min(max(limit * max(1, tf_sec // 300 + 2), limit + 50), 120_000)
            df = _normalize_ohlcv_index(_tail_csv_as_dataframe(p5, need_5m))
            if tf != "5m" and len(df) > 0:
                df = loader.resample(df, tf)
            df = df.iloc[-limit:]
        else:
            return [], "missing_csv"
    except Exception as e:
        logger.warning("Databento chart load failed for %s %s: %s", symbol, timeframe, e)
        return [], str(e)

    if df is None or len(df) == 0:
        return [], "empty"

    rows = _df_to_chart_rows(df)
    logger.info(
        "Databento chart: %s %s -> %d bars (root=%s)",
        symbol,
        timeframe,
        len(rows),
        root,
    )
    return rows, None
