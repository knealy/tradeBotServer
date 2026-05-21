from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_generate_signal_walkthrough_report_help():
    repo = Path(__file__).resolve().parent.parent
    script = repo / "scripts" / "generate_signal_walkthrough_report.py"
    r = subprocess.run(
        [sys.executable, str(script), "--help"],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode == 0
    assert "--start" in r.stdout
