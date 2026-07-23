#!/usr/bin/env python3
"""Append the **latest broker 1m bars** onto canonical ``*_1m_databento.csv`` files.

Same lightweight history stack as ``scripts/stitch_broker_history_to_databento_5m.py``
(``core/broker_history_session`` — no full ``TopStepXTradingBot`` / Postgres):

- ``open_broker_history_adapter()`` + ``authenticate()``
- ``adapter.get_historical_data(..., timeframe='1m', …, limit=20000, _use_cache=False)``

For each symbol:

1. Read the last ``timestamp`` from ``historical_data/price/<SYM>_1m_databento.csv`` (case-insensitive root on macOS).
2. Pull **1m** bars from **one minute after** that timestamp through **now** (UTC), in ``--chunk-days`` windows
   (default **10** calendar days so each chunk stays under the ~20k bar cap: 10×1440 = 14400).
3. Write a temp CSV with ``timestamp,open,high,low,close,volume``.
4. Merge with ``historical_data/csv_merger.py`` (**canonical first, broker temp second** so newer timestamps win).

**Merge order tip:** ``csv_merger`` keeps the **last** file’s row on duplicate timestamps. To extend history with a
broker export, always pass **archive/canonical first**, **new pull second** — the opposite of listing the small
export first.

Examples::

  ENABLE_SIGNALR=false .venv/bin/python scripts/stitch_broker_history_to_databento_1m.py --symbols MGC --dry-run
  .venv/bin/python scripts/stitch_broker_history_to_databento_1m.py

Env: ``PROJECT_X_API_KEY`` + ``PROJECT_X_USERNAME`` (or TOPSTEPX_* aliases).
"""
from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, List

ROOT = Path(__file__).resolve().parent.parent
PRICE = ROOT / "historical_data" / "price"
MERGER = ROOT / "historical_data" / "csv_merger.py"


def _bar_ts_utc(bar: Any) -> datetime:
    ts = bar.timestamp
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def _dedupe_sort_bars(bars: list) -> list:
    by_ts: dict = {}
    for b in bars:
        by_ts[_bar_ts_utc(b)] = b
    return sorted(by_ts.values(), key=lambda x: _bar_ts_utc(x))


def _backup(path: Path) -> None:
    if not path.is_file():
        return
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    bak = path.parent / f"{path.name}.bak.{ts}"
    shutil.copy2(path, bak)
    print(f"backup: {path.name} -> {bak.name}")


def _resolve_canon_1m(sym: str) -> Path | None:
    """Prefer lowercase template path; fall back to uppercase filename if needed."""
    low = PRICE / f"{sym.lower()}_1m_databento.csv"
    if low.is_file():
        return low
    up = PRICE / f"{sym}_1m_databento.csv"
    return up if up.is_file() else None


def _last_csv_timestamp_utc(path: Path) -> datetime | None:
    import pandas as pd

    df = pd.read_csv(path, usecols=[0])
    col = df.columns[0]
    ts = pd.to_datetime(df[col].iloc[-1], utc=True)
    return ts.to_pydatetime()


def _write_broker_1m_csv(path: Path, bars: list) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w", encoding="utf-8") as f:
        f.write("timestamp,open,high,low,close,volume\n")
        for bar in bars:
            ts = _bar_ts_utc(bar).replace(tzinfo=None)
            f.write(
                f"{ts.strftime('%Y-%m-%d %H:%M:%S')},"
                f"{bar.open},{bar.high},{bar.low},{bar.close},{int(bar.volume or 0)}\n"
            )
            n += 1
    return n


async def _fetch_bars_chunked(
    adapter: Any,
    *,
    symbol: str,
    timeframe: str,
    start: datetime,
    end: datetime,
    chunk_days: int,
) -> list:
    all_raw: list = []
    cursor = start
    if cursor >= end:
        return []
    chunk_days = max(1, int(chunk_days))
    n_chunk = 0
    while cursor < end:
        n_chunk += 1
        chunk_end = min(end, cursor + timedelta(days=chunk_days))
        print(f"   {symbol} chunk {n_chunk}: {cursor.isoformat()} → {chunk_end.isoformat()} …")
        chunk = await adapter.get_historical_data(
            symbol=symbol,
            timeframe=timeframe,
            start_time=cursor,
            end_time=chunk_end,
            limit=20000,
            _use_cache=False,
        )
        print(f"      → {len(chunk)} bars")
        if len(chunk) >= 20000:
            print(
                "      ⚠️  Hit 20k bar cap — reduce --chunk-days and re-run from the same canonical file.",
                file=sys.stderr,
            )
        all_raw.extend(chunk or [])
        cursor = chunk_end
    return _dedupe_sort_bars(all_raw)


async def _run(args: argparse.Namespace) -> int:
    sys.path.insert(0, str(ROOT))
    from core.broker_history_session import (
        close_broker_history_session,
        open_broker_history_adapter,
    )

    api_key = os.getenv("PROJECT_X_API_KEY") or os.getenv("TOPSTEPX_API_KEY") or os.getenv("TOPSETPX_API_KEY")
    username = os.getenv("PROJECT_X_USERNAME") or os.getenv("TOPSTEPX_USERNAME") or os.getenv("TOPSETPX_USERNAME")
    if not api_key or not username:
        print("error: set PROJECT_X_API_KEY and PROJECT_X_USERNAME (or TOPSTEPX_* aliases)", file=sys.stderr)
        return 2

    roots = tuple(r.strip().upper() for r in args.symbols.split(",") if r.strip())
    tf = str(args.timeframe or "1m")
    end = datetime.now(timezone.utc)
    py = sys.executable

    print("Authenticating (broker history session)…")
    auth, adapter = await open_broker_history_adapter(api_key=api_key, username=username)
    if auth is None or adapter is None:
        print("error: authentication failed — check network/DNS and PROJECT_X_* credentials", file=sys.stderr)
        return 1
    print("✅ ok\n")

    try:
        for sym in roots:
            canon = _resolve_canon_1m(sym)
            if canon is None:
                print(f"skip {sym}: missing canonical *1m_databento.csv under {PRICE}", file=sys.stderr)
                continue

            last = _last_csv_timestamp_utc(canon)
            start = last + timedelta(minutes=1)
            if start >= end:
                print(f"{sym}: already up to date (last bar {last.isoformat()})")
                continue

            print(f"{sym}: pull {tf} {start.isoformat()} → {end.isoformat()} (after last file bar)")

            bars = await _fetch_bars_chunked(
                adapter,
                symbol=sym,
                timeframe=tf,
                start=start,
                end=end,
                chunk_days=args.chunk_days,
            )
            if not bars:
                print(f"   no new bars returned for {sym}")
                continue

            with tempfile.TemporaryDirectory(prefix=f"stitch1m_{sym}_") as td:
                tdir = Path(td)
                fresh = tdir / f"{sym.lower()}_1m_broker_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.csv"
                n = _write_broker_1m_csv(fresh, bars)
                print(f"   wrote temp {n} rows -> {fresh.name}")

                if args.dry_run:
                    print(f"   dry-run: not merging into {canon.relative_to(ROOT)}")
                    continue

                if not args.no_backup:
                    _backup(canon)
                out_m = tdir / "merged.csv"
                cmd = [py, str(MERGER), str(canon), str(fresh), "-o", str(out_m)]
                print("  ", " ".join(cmd))
                subprocess.run(cmd, cwd=str(ROOT), check=True)
                shutil.move(str(out_m), str(canon))
                print(f"   merged -> {canon.relative_to(ROOT)}")
    finally:
        await close_broker_history_session(auth)

    print("done")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbols", type=str, default="MNQ,MES,MGC", help="Comma-separated roots")
    ap.add_argument("--timeframe", type=str, default="1m", help="Must match canonical cadence (default 1m)")
    ap.add_argument(
        "--chunk-days",
        type=int,
        default=10,
        help="Max calendar days per broker get_historical_data window (default 10 for 1m under 20k cap)",
    )
    ap.add_argument("--dry-run", action="store_true", help="Fetch + temp CSV only; do not merge")
    ap.add_argument("--no-backup", action="store_true", help="Skip .bak copy before overwriting canonical")
    args = ap.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
