#!/usr/bin/env python3
"""
Merge a Databento GLBX batch into **canonical** OHLCV files used across the repo:

  historical_data/price/{MNQ,MES,MGC}_1m_databento.csv
  historical_data/price/{MNQ,MES,MGC}_5m_databento.csv   (--resample-5m, default on)

Flow:
1. Run ``merge_databento_glbx_batch.py`` on the batch dir → per-symbol
   ``{SYM}_1m_databento_<batch_tag>.csv`` in a temp directory.
2. For each root symbol, **stitch** into the canonical 1m file: if the canonical
   file already exists, merge with **archive first, new batch second** so
   duplicate timestamps keep the **newer** row (same rule as ``csv_merger.py``).
3. Optionally resample each updated canonical 1m → 5m.

**Batch directory:** pass ``--batch-dir``, or set ``DATABENTO_BATCH_DIR``, or
omit both to auto-pick the **lexicographically newest** ``historical_data/price/GLBX-*``
directory (works when job ids sort newer = later).

**Weekly cron (example)** — after you download a new GLBX folder into
``historical_data/price/``::

    5 4 * * 1 cd /path/to/tradeBotServer && .venv/bin/python scripts/databento_stitch_canonical.py >>logs/databento_stitch.log 2>&1

This script does **not** call the Databento HTTP API; it only merges local
CSVs. Use Databento’s CLI / portal / separate job to fetch new batches.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

ROOT = Path(__file__).resolve().parent.parent
PRICE = ROOT / "historical_data" / "price"
MERGE_SCRIPT = ROOT / "scripts" / "merge_databento_glbx_batch.py"
RESAMPLE = ROOT / "historical_data" / "resample_ohlcv_csv.py"


def _discover_batch_dir(explicit: Optional[Path]) -> Path:
    if explicit is not None:
        return explicit.resolve()
    env = os.environ.get("DATABENTO_BATCH_DIR", "").strip()
    if env:
        return Path(env).resolve()
    if not PRICE.is_dir():
        raise FileNotFoundError(f"Missing price dir: {PRICE}")
    candidates: List[Path] = sorted(PRICE.glob("GLBX-*"), key=lambda p: p.name)
    if not candidates:
        raise FileNotFoundError(
            f"No GLBX-* batch directory under {PRICE}. "
            "Download a batch or pass --batch-dir / set DATABENTO_BATCH_DIR."
        )
    return candidates[-1].resolve()


def _backup(path: Path) -> None:
    if not path.is_file():
        return
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    bak = path.parent / f"{path.name}.bak.{ts}"
    shutil.copy2(path, bak)
    print(f"backup: {path.name} -> {bak.name}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        "--batch-dir",
        type=Path,
        default=None,
        help="GLBX-* folder (default: DATABENTO_BATCH_DIR or newest GLBX-* under historical_data/price)",
    )
    ap.add_argument(
        "--roots",
        type=str,
        default="MNQ,MES,MGC",
        help="Comma-separated roots (default MNQ,MES,MGC)",
    )
    ap.add_argument(
        "--no-backup",
        action="store_true",
        help="Do not copy .bak before overwriting canonical files",
    )
    ap.add_argument(
        "--skip-5m",
        action="store_true",
        help="Skip 5m resample step",
    )
    args = ap.parse_args()

    try:
        batch_dir = _discover_batch_dir(args.batch_dir)
    except FileNotFoundError as e:
        print(e, file=sys.stderr)
        return 1

    if not batch_dir.is_dir():
        print(f"Not a directory: {batch_dir}", file=sys.stderr)
        return 1

    roots = tuple(r.strip().upper() for r in args.roots.split(",") if r.strip())
    tag = batch_dir.name
    py = sys.executable

    print(f"batch_dir={batch_dir}")
    print(f"tag={tag}  roots={roots}")
    sys.stdout.flush()

    with tempfile.TemporaryDirectory(prefix="databento_stitch_") as td:
        tdir = Path(td)
        cmd = [
            py,
            str(MERGE_SCRIPT),
            str(batch_dir),
            "--output-dir",
            str(tdir),
            "--roots",
            ",".join(roots),
        ]
        print("merge batch →", " ".join(cmd))
        sys.stdout.flush()
        subprocess.run(cmd, cwd=str(ROOT), check=True)

        for sym in roots:
            new_csv = tdir / f"{sym}_1m_databento_{tag}.csv"
            if not new_csv.is_file():
                print(f"skip {sym}: missing merged file {new_csv.name}", file=sys.stderr)
                continue
            canon = PRICE / f"{sym}_1m_databento.csv"
            PRICE.mkdir(parents=True, exist_ok=True)
            if canon.is_file():
                if not args.no_backup:
                    _backup(canon)
                # Later file wins on duplicate timestamps
                merger = ROOT / "historical_data" / "csv_merger.py"
                tmp_out = tdir / f"{sym}_1m_canonical_merged.csv"
                mcmd = [py, str(merger), str(canon), str(new_csv), "-o", str(tmp_out)]
                print(" ", " ".join(mcmd))
                subprocess.run(mcmd, cwd=str(ROOT), check=True)
                shutil.move(str(tmp_out), str(canon))
                print(f"  {sym}: stitched -> {canon.relative_to(ROOT)}")
            else:
                shutil.copy2(new_csv, canon)
                print(f"  {sym}: created canonical -> {canon.relative_to(ROOT)}")

            if not args.skip_5m:
                out5 = PRICE / f"{sym}_5m_databento.csv"
                if out5.is_file() and not args.no_backup:
                    _backup(out5)
                rcmd = [
                    py,
                    str(RESAMPLE),
                    "-i",
                    str(canon),
                    "--to",
                    "5m",
                    "-o",
                    str(out5),
                ]
                print(" ", " ".join(rcmd))
                subprocess.run(rcmd, cwd=str(ROOT), check=True)
                print(f"  {sym}: resampled -> {out5.relative_to(ROOT)}")

    print("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
