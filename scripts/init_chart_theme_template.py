#!/usr/bin/env python3
"""
Write or refresh ``config/chart_theme.template.json`` from built-in defaults.

Usage:
  python scripts/init_chart_theme_template.py
  python scripts/init_chart_theme_template.py --dry-run   # print path only

After editing the template, mirror options into ``gui/master_control.html`` (search
``createChart`` / ``addCandlestickSeries``) or extend ``gui/chart_html.py`` to load JSON.
"""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "config" / "chart_theme.template.json"

DEFAULT = {
    "_readme": (
        "Defaults for Lightweight Charts v4 (master + standalone). "
        "Copy keys into gui/master_control.html initChart() or merge programmatically. "
        "Remove _readme before machine-parse."
    ),
    "layout": {
        "background": {"color": "#000000"},
        "textColor": "#f2ebe3",
        "fontSize": 13,
    },
    "grid": {
        "vertLines": {"visible": False},
        "horzLines": {"visible": False},
    },
    "crosshair": {
        "mode": "Normal",
        "vertLine": {"labelVisible": True, "color": "#a89988"},
        "horzLine": {"labelVisible": True, "color": "#a89988"},
    },
    "rightPriceScale": {
        "borderColor": "#8b7765",
        "autoScale": True,
        "visible": True,
    },
    "timeScale": {
        "borderColor": "#8b7765",
        "borderVisible": True,
        "timeVisible": True,
        "secondsVisible": True,
        "visible": True,
    },
    "candlestickSeries": {
        "upColor": "#7da882",
        "downColor": "#bf7a6e",
        "borderVisible": False,
        "wickUpColor": "#7da882",
        "wickDownColor": "#bf7a6e",
    },
    "volumeHistogramSeries": {
        "color": "#5a6d78",
        "priceFormat": {"type": "volume"},
        "priceScaleId": "",
    },
}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="Do not write; print target path")
    args = ap.parse_args()
    if args.dry_run:
        print(TEMPLATE)
        return
    TEMPLATE.parent.mkdir(parents=True, exist_ok=True)
    if TEMPLATE.exists():
        bak = TEMPLATE.with_suffix(".template.json.bak")
        shutil.copy2(TEMPLATE, bak)
    text = json.dumps(DEFAULT, indent=2) + "\n"
    TEMPLATE.write_text(text, encoding="utf-8")
    print(f"Wrote {TEMPLATE}")


if __name__ == "__main__":
    main()
