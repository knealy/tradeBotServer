from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_replay_morning_reversion_trade_charts_from_1m_csv_help():
    repo = Path(__file__).resolve().parent.parent
    script = repo / "scripts" / "replay_morning_reversion_trade_charts_from_1m_csv.py"
    r = subprocess.run(
        [sys.executable, str(script), "--help"],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode == 0
    assert "--reuse-json" in r.stdout
    assert "--full-span" in r.stdout
