"""Regression: LOG_FILE parent path must not be a regular file (logs-startup-file-vs-dir)."""

from __future__ import annotations

import pytest


@pytest.fixture
def reset_logging_configure():
    import core.logging_setup as ls

    ls._CONFIGURED = False
    yield
    ls._CONFIGURED = False


def test_renames_file_that_shadows_log_parent_dir(tmp_path, reset_logging_configure, monkeypatch):
    """If ``LOG_FILE``'s parent exists as a file, it is renamed so a real directory can be created."""
    monkeypatch.setenv("LOGGING_FORCE_RECONFIGURE", "1")

    import core.logging_setup as ls

    shadow = tmp_path / "nest"
    shadow.write_text("not-a-directory\n")
    log_target = shadow / "bot.log"

    out = ls.configure_logging(log_file=str(log_target), install_uvloop=False)

    assert out.resolve() == log_target.resolve()
    assert shadow.is_dir()
    backups = list(tmp_path.glob("nest.file_backup_*"))
    assert len(backups) == 1
