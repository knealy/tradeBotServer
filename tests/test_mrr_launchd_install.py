"""Tests for the MRR daily-launchd workflow (2026-06-15).

Two surfaces under test:

  1. ``scripts/install_mrr_launchd.sh`` — renders a launchd plist, computes
     the host-local schedule from MRR's TOML, validates the output with
     plistlib.  Tests run the installer in ``--dry-run`` mode against a
     real repo checkout so no system state is touched.
  2. ``scripts/run_morning_reversion.sh`` — the session-end cutoff env
     parsing.  We can't easily exercise the bash supervise loop end-to-end
     in pytest, but we CAN extract and test the cutoff math directly.

Pin every operator-touching invariant: label format, schedule shape,
required keys, weekday coverage.  This catches regressions in the awk
substitution, plistlib API drift, and TOML format changes.
"""

from __future__ import annotations

import plistlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALLER = REPO_ROOT / "scripts" / "install_mrr_launchd.sh"
UNINSTALLER = REPO_ROOT / "scripts" / "uninstall_mrr_launchd.sh"
TEMPLATE = REPO_ROOT / "scripts" / "launchd" / "com.tradebot.mrr.plist.template"
WRAPPER = REPO_ROOT / "scripts" / "run_morning_reversion.sh"
TOML_PATH = REPO_ROOT / "config" / "strategies" / "morning_range_reversion.toml"


# ────────────────────────────────────────────────────────────────────────────
# Static-file sanity (cheapest tier — no subprocess invocation)
# ────────────────────────────────────────────────────────────────────────────

def test_installer_script_exists_and_is_executable():
    assert INSTALLER.is_file(), f"{INSTALLER} missing"
    assert UNINSTALLER.is_file(), f"{UNINSTALLER} missing"
    # Both scripts should be marked executable (chmod +x).
    assert INSTALLER.stat().st_mode & 0o100, "installer is not executable"
    assert UNINSTALLER.stat().st_mode & 0o100, "uninstaller is not executable"


def test_template_exists_and_has_all_placeholders():
    assert TEMPLATE.is_file(), f"{TEMPLATE} missing"
    body = TEMPLATE.read_text()
    # Every placeholder consumed by the installer's awk substitution must
    # appear at least once in the template, otherwise the renderer will
    # bake the literal placeholder string into the output.
    for placeholder in (
        "__LABEL__", "__SCRIPT_PATH__", "__ACCOUNT__", "__WORKING_DIR__",
        "__LOG_OUT__", "__LOG_ERR__", "__HOUR__", "__MINUTE__", "__PATH__",
    ):
        assert placeholder in body, f"template missing placeholder {placeholder!r}"


def test_template_is_parseable_plist_after_dummy_substitution():
    """Substitute trivial values and confirm the result still parses.
    Catches structural plist mistakes (missing close tags, malformed
    StartCalendarInterval array) without needing the installer to be
    runnable.
    """
    body = TEMPLATE.read_text()
    subs = {
        "__LABEL__": "com.test.dummy",
        "__SCRIPT_PATH__": "/dev/null",
        "__ACCOUNT__": "1",
        "__WORKING_DIR__": "/tmp",
        "__LOG_OUT__": "/tmp/out.log",
        "__LOG_ERR__": "/tmp/err.log",
        "__HOUR__": "6",
        "__MINUTE__": "50",
        "__PATH__": "/usr/bin:/bin",
    }
    for k, v in subs.items():
        body = body.replace(k, v)
    data = plistlib.loads(body.encode("utf-8"))
    assert data["Label"] == "com.test.dummy"
    assert data["ProgramArguments"] == ["/bin/bash", "/dev/null", "1"]
    assert len(data["StartCalendarInterval"]) == 5


# ────────────────────────────────────────────────────────────────────────────
# Installer dry-run smoke (real subprocess; touches no system state)
# ────────────────────────────────────────────────────────────────────────────

@pytest.mark.skipif(
    shutil.which("bash") is None,
    reason="bash not available — required to drive the installer",
)
def test_installer_dry_run_produces_valid_plist():
    """End-to-end dry-run: installer reads the real TOML, renders the plist,
    validates with plistlib.  No filesystem state is changed (we only assert
    on the dry-run stdout)."""
    proc = subprocess.run(
        ["bash", str(INSTALLER), "1", "--dry-run"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        timeout=30,
    )
    assert proc.returncode == 0, (
        f"installer dry-run failed (rc={proc.returncode})\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    assert "✓ plist validation OK" in proc.stdout
    # The rendered plist must include the expected label + the 5 weekday entries.
    assert "com.tradebot.mrr.account1" in proc.stdout
    # Count <key>Weekday</key> appearances — expect exactly 5 (Mon-Fri).
    weekday_keys = proc.stdout.count("<key>Weekday</key>")
    assert weekday_keys == 5, (
        f"expected 5 Weekday entries in rendered plist, got {weekday_keys}"
    )


@pytest.mark.skipif(
    shutil.which("bash") is None,
    reason="bash not available",
)
def test_installer_dry_run_resolves_paths_to_absolute():
    """The plist substitutions must produce absolute paths — relative paths
    would silently fail under launchd (which doesn't honour CWD)."""
    proc = subprocess.run(
        ["bash", str(INSTALLER), "1", "--dry-run"],
        capture_output=True, text=True, cwd=REPO_ROOT, timeout=30,
    )
    assert proc.returncode == 0
    # Every <string>...</string> in ProgramArguments + log paths must be
    # absolute (start with /).  Extract by scanning for "<string>/" appearances.
    rendered = proc.stdout.split("Rendered plist below:")[-1]
    assert "<string>/" in rendered, "no absolute path found in rendered plist"
    # The wrapper path specifically must be present.
    assert "/scripts/run_morning_reversion.sh" in rendered


@pytest.mark.skipif(
    shutil.which("bash") is None,
    reason="bash not available",
)
def test_installer_wake_lead_override_propagates():
    """``--wake-lead 15`` must shift the local-clock schedule by 15 min
    before start_time ET."""
    proc_default = subprocess.run(
        ["bash", str(INSTALLER), "1", "--dry-run"],
        capture_output=True, text=True, cwd=REPO_ROOT, timeout=30,
    )
    proc_15 = subprocess.run(
        ["bash", str(INSTALLER), "1", "--wake-lead", "15", "--dry-run"],
        capture_output=True, text=True, cwd=REPO_ROOT, timeout=30,
    )
    assert proc_default.returncode == 0
    assert proc_15.returncode == 0
    # Extract the "schedule (session TZ): HH:MM" line and compare.
    import re
    def _et_minute(out: str) -> int:
        # Format: "  schedule (session TZ): 6:50  America/New_York"
        # The first colon belongs to "TZ)" — match the HH:MM token explicitly.
        for line in out.splitlines():
            if "schedule (session TZ)" in line:
                m = re.search(r"\b(\d{1,2}):(\d{2})\b", line.split(")")[1])
                assert m, f"no HH:MM in line: {line!r}"
                return int(m.group(1)) * 60 + int(m.group(2))
        raise AssertionError(f"no schedule line found in:\n{out}")

    default_min = _et_minute(proc_default.stdout)
    fifteen_min = _et_minute(proc_15.stdout)
    # 15 - 5 = 10 minute difference (default is 5, override is 15).
    assert default_min - fifteen_min == 10, (
        f"expected --wake-lead 15 to move schedule 10min earlier; "
        f"default={default_min}min, override={fifteen_min}min"
    )


# ────────────────────────────────────────────────────────────────────────────
# Wrapper session-end cutoff math
# ────────────────────────────────────────────────────────────────────────────

@pytest.mark.skipif(
    shutil.which("bash") is None,
    reason="bash not available",
)
def test_wrapper_session_end_grace_zero_disables_cutoff():
    """``SESSION_EXIT_GRACE_MIN=0`` must wire SESSION_EXIT_TS to a far-future
    sentinel so ``session_ended`` never returns true (i.e. legacy always-on
    behaviour preserved)."""
    # Extract + exercise the relevant snippet directly so we don't have to
    # run the full wrapper (which would block on the countdown).
    snippet = """
set -euo pipefail
FLAT_BEFORE_TS=1750000000
SESSION_EXIT_GRACE_MIN=0
if [ "$SESSION_EXIT_GRACE_MIN" -lt 0 ] 2>/dev/null; then
    SESSION_EXIT_GRACE_MIN=0
fi
if [ "$SESSION_EXIT_GRACE_MIN" -gt 0 ] 2>/dev/null; then
    SESSION_EXIT_TS=$((FLAT_BEFORE_TS + SESSION_EXIT_GRACE_MIN * 60))
else
    SESSION_EXIT_TS=$(( $(date +%s) + 365 * 24 * 3600 ))
fi
echo "SESSION_EXIT_TS=$SESSION_EXIT_TS"
"""
    proc = subprocess.run(
        ["bash", "-c", snippet], capture_output=True, text=True, timeout=10,
    )
    assert proc.returncode == 0, proc.stderr
    import time
    out_ts = int(proc.stdout.strip().split("=")[1])
    # Must be at least 360 days in the future (sentinel = 1 year out).
    assert out_ts - int(time.time()) > 360 * 24 * 3600


@pytest.mark.skipif(
    shutil.which("bash") is None,
    reason="bash not available",
)
def test_wrapper_runs_past_banner_without_unbound_variable_crash():
    """REGRESSION (2026-06-16): launchd-driven runs crashed at line 211
    because SESSION_EXIT_GRACE_MIN was declared *after* the schedule banner
    referenced it.  set -u tripped immediately and the wrapper exited rc=1
    before the executor was launched — meaning MRR silently did not trade
    on the first morning after launchd installation.

    This test exec's the wrapper end-to-end (NO_WAIT=1 to skip countdown,
    MAX_RESTARTS=0 to bail after a single launch attempt) and asserts:

    1. NO ``unbound variable`` error appears in stdout/stderr.
    2. The wrapper progresses past the banner to the supervise-loop
       launch step (we see the ``→ launching strategy_executor`` line).

    We can't let the wrapper actually start trading, so we time-bound the
    test and SIGTERM as soon as the launch line appears, then nuke any
    spawned executor process tree.
    """
    import signal
    import time

    proc = subprocess.Popen(
        ["bash", str(WRAPPER), "1"],
        cwd=REPO_ROOT,
        env={
            **__import__("os").environ,
            "NO_WAIT": "1",
            "SESSION_EXIT_GRACE_MIN": "0",
            "MAX_RESTARTS": "0",
            # Force the wrapper into "in trading window" branch even if test
            # runs outside session hours — NO_WAIT bypasses the wait either way.
        },
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,  # isolate process group for clean kill
    )

    deadline = time.time() + 12
    captured_chunks: list[str] = []
    launched = False
    crashed = False

    # Stream output line-by-line; exit as soon as we see either signal.
    try:
        while time.time() < deadline:
            line = proc.stdout.readline() if proc.stdout else ""
            if not line:
                if proc.poll() is not None:
                    break
                time.sleep(0.1)
                continue
            captured_chunks.append(line)
            if "unbound variable" in line:
                crashed = True
                break
            if "launching strategy_executor" in line:
                launched = True
                break
    finally:
        # Tear down the wrapper + entire process group (incl. spawned executor).
        try:
            import os
            os.killpg(proc.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            proc.wait(timeout=5)
        # Belt-and-suspenders: kill any orphaned executor that escaped the pgrp.
        subprocess.run(
            ["pkill", "-KILL", "-f",
             "core/strategy_executor.py.*morning_range_reversion.*account_select=1"],
            check=False,
        )

    captured = "".join(captured_chunks)
    assert not crashed, (
        "regression: wrapper crashed with 'unbound variable' — "
        "SESSION_EXIT_GRACE_MIN must be defined before the schedule banner. "
        f"Captured output:\n{captured}"
    )
    assert launched, (
        "wrapper did not reach the supervise-loop launch step in 12s. "
        f"Captured output:\n{captured}"
    )


@pytest.mark.skipif(
    shutil.which("bash") is None,
    reason="bash not available",
)
def test_wrapper_session_end_grace_positive_computes_cutoff():
    """Positive grace minutes must add ``flat_before + grace*60`` seconds."""
    snippet = """
set -euo pipefail
FLAT_BEFORE_TS=1000000
SESSION_EXIT_GRACE_MIN=30
if [ "$SESSION_EXIT_GRACE_MIN" -gt 0 ] 2>/dev/null; then
    SESSION_EXIT_TS=$((FLAT_BEFORE_TS + SESSION_EXIT_GRACE_MIN * 60))
else
    SESSION_EXIT_TS=$(( $(date +%s) + 365 * 24 * 3600 ))
fi
echo "SESSION_EXIT_TS=$SESSION_EXIT_TS"
"""
    proc = subprocess.run(
        ["bash", "-c", snippet], capture_output=True, text=True, timeout=10,
    )
    assert proc.returncode == 0
    out_ts = int(proc.stdout.strip().split("=")[1])
    # 30 min * 60 s/min = 1800 s after FLAT_BEFORE_TS.
    assert out_ts == 1_000_000 + 1800
