from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_stitch_broker_history_to_databento_5m_help():
    repo = Path(__file__).resolve().parent.parent
    script = repo / "scripts" / "stitch_broker_history_to_databento_5m.py"
    r = subprocess.run(
        [sys.executable, str(script), "--help"],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode == 0
    assert "--dry-run" in r.stdout
