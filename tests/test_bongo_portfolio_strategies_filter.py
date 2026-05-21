"""CLI filter for ``scripts/bongo_portfolio_walkforward_equity.py``."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_bongo_portfolio_rejects_unknown_strategy_id():
    repo = Path(__file__).resolve().parent.parent
    script = repo / "scripts" / "bongo_portfolio_walkforward_equity.py"
    r = subprocess.run(
        [sys.executable, str(script), "--strategies", "not_a_real_strategy"],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode == 2
    assert "unknown" in r.stderr.lower()
