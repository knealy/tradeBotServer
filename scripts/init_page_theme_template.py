#!/usr/bin/env python3
"""
Write ``config/page_theme.template.json`` with default Master dashboard CSS variables.

Usage:
  python scripts/init_page_theme_template.py
  python scripts/init_page_theme_template.py --dry-run

To customize live:
  cp config/page_theme.template.json config/page_theme.json
  # edit config/page_theme.json (tracked only if you remove it from .gitignore)
  # reload http://127.0.0.1:<port>/master — theme is fetched from /api/chart/theme/page
"""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "config" / "page_theme.template.json"

DEFAULT = {
    "_readme": (
        "Master dashboard (:root) CSS variables. Optional overrides: "
        "config/page_theme.json (gitignored). Docs: docs/PAGE_THEME_OPTIONS.md"
    ),
    "vars": {
        "--bg-primary": "#1c1814",
        "--bg-secondary": "#2a241d",
        "--bg-tertiary": "#3d352c",
        "--text-primary": "#faf6f0",
        "--text-secondary": "#c9b8a4",
        "--border-color": "#6f5e4f",
        "--accent-color": "#6a9fb8",
        "--positive-color": "#7aab7f",
        "--negative-color": "#c4877a",
        "--warning-color": "#d4a84b",
        "--chart-canvas-bg": "#000000",
        "--font-size-base": "12px",
    },
}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if args.dry_run:
        print(TARGET)
        return
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    if TARGET.exists():
        shutil.copy2(TARGET, TARGET.with_suffix(".template.json.bak"))
    TARGET.write_text(json.dumps(DEFAULT, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {TARGET}")


if __name__ == "__main__":
    main()
