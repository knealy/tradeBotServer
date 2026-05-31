"""Unit tests for ``core.backtest.strategy_config_snapshot``.

Verifies that the walk-forward recap snapshot helper:

- Copies the source TOML verbatim into ``<out_dir>/config/<name>.toml``.
- Records a parseable ``data`` dict alongside the raw text.
- Filters env-var overrides by ``<NAME_UPPER>_`` prefix (matches
  ``StrategyConfig`` precedence).
- Surfaces missing TOMLs as ``error`` rows without crashing the run.
- Emits HTML containing snapshot links + env override rows + verbatim TOML
  in a ``<details>`` block.
- Emits JSON with ``captured_at_utc`` + per-strategy ``data`` body.
"""
from __future__ import annotations

import json
from pathlib import Path

from core.backtest.strategy_config_snapshot import (
    snapshot_strategy_configs,
    snapshots_to_html,
    snapshots_to_json,
)


def _write_strategy_toml(cfg_dir: Path, name: str, body: str) -> Path:
    cfg_dir.mkdir(parents=True, exist_ok=True)
    p = cfg_dir / f"{name}.toml"
    p.write_text(body, encoding="utf-8")
    return p


def test_snapshot_copies_toml_and_parses(tmp_path: Path) -> None:
    cfg = tmp_path / "config_strategies"
    body = """
[meta]
enabled = true
symbols = ["MNQ"]

[signal]
tp_mult = 1.8
reentry_threshold_points = 1
""".strip() + "\n"
    src = _write_strategy_toml(cfg, "morning_range_reversion", body)

    out_dir = tmp_path / "report"
    snaps = snapshot_strategy_configs(
        ["morning_range_reversion"],
        out_dir=out_dir,
        env_overrides={},
        config_dir=cfg,
    )

    assert len(snaps) == 1
    s = snaps[0]
    assert s.name == "morning_range_reversion"
    assert s.error is None
    assert s.source == src
    assert s.snapshot == out_dir / "config" / "morning_range_reversion.toml"
    assert s.snapshot.is_file()
    assert s.snapshot.read_text(encoding="utf-8") == body
    assert s.raw_text == body
    assert s.data["meta"]["enabled"] is True
    assert s.data["meta"]["symbols"] == ["MNQ"]
    assert s.data["signal"]["tp_mult"] == 1.8
    assert s.data["signal"]["reentry_threshold_points"] == 1


def test_snapshot_filters_env_overrides_by_strategy_prefix(tmp_path: Path) -> None:
    cfg = tmp_path / "config_strategies"
    _write_strategy_toml(cfg, "morning_range_reversion", "[meta]\nenabled = true\n")
    _write_strategy_toml(cfg, "overnight_range", "[meta]\nenabled = true\n")

    env = {
        "MORNING_RANGE_REVERSION_SIGNAL_TP_MULT": "0.7",
        "MORNING_RANGE_REVERSION_RISK_POSITION_SIZE": "1",
        "OVERNIGHT_RANGE_SIGNAL_PARTIAL_TP_ENABLED": "true",
        "ENABLE_SIGNALR": "false",
        "PYTHONUNBUFFERED": "1",
    }

    snaps = snapshot_strategy_configs(
        ["morning_range_reversion", "overnight_range"],
        out_dir=tmp_path / "report",
        env_overrides=env,
        config_dir=cfg,
    )

    by_name = {s.name: s for s in snaps}
    assert set(by_name["morning_range_reversion"].env_overrides.keys()) == {
        "MORNING_RANGE_REVERSION_SIGNAL_TP_MULT",
        "MORNING_RANGE_REVERSION_RISK_POSITION_SIZE",
    }
    assert by_name["morning_range_reversion"].env_overrides[
        "MORNING_RANGE_REVERSION_SIGNAL_TP_MULT"
    ] == "0.7"
    assert set(by_name["overnight_range"].env_overrides.keys()) == {
        "OVERNIGHT_RANGE_SIGNAL_PARTIAL_TP_ENABLED",
    }


def test_snapshot_missing_toml_returns_error_row(tmp_path: Path) -> None:
    cfg = tmp_path / "config_strategies"
    cfg.mkdir(parents=True, exist_ok=True)

    snaps = snapshot_strategy_configs(
        ["does_not_exist"],
        out_dir=tmp_path / "report",
        env_overrides={"DOES_NOT_EXIST_SIGNAL_X": "1"},
        config_dir=cfg,
    )

    assert len(snaps) == 1
    s = snaps[0]
    assert s.source is None
    assert s.snapshot is None
    assert s.error and "No TOML" in s.error
    assert s.env_overrides == {"DOES_NOT_EXIST_SIGNAL_X": "1"}


def test_snapshots_to_json_includes_data_and_metadata(tmp_path: Path) -> None:
    cfg = tmp_path / "config_strategies"
    _write_strategy_toml(cfg, "morning_range_reversion", "[meta]\nenabled = true\n")
    snaps = snapshot_strategy_configs(
        ["morning_range_reversion"],
        out_dir=tmp_path / "report",
        env_overrides={"MORNING_RANGE_REVERSION_SIGNAL_TP_MULT": "0.7"},
        config_dir=cfg,
    )

    bundle = snapshots_to_json(snaps, extra={"args": {"days": 30}})
    text = json.dumps(bundle, default=str)
    assert "captured_at_utc" in bundle
    assert "strategies" in bundle
    assert "morning_range_reversion" in bundle["strategies"]
    leg = bundle["strategies"]["morning_range_reversion"]
    assert leg["data"]["meta"]["enabled"] is True
    assert leg["env_overrides"]["MORNING_RANGE_REVERSION_SIGNAL_TP_MULT"] == "0.7"
    assert leg["error"] is None
    assert bundle["args"]["days"] == 30
    json.loads(text)  # round-trip safety


def test_snapshots_to_html_contains_links_and_overrides(tmp_path: Path) -> None:
    cfg = tmp_path / "config_strategies"
    body = "[meta]\nenabled = true\nsymbols = [\"MNQ\"]\n"
    _write_strategy_toml(cfg, "morning_range_reversion", body)
    snaps = snapshot_strategy_configs(
        ["morning_range_reversion"],
        out_dir=tmp_path / "report",
        env_overrides={"MORNING_RANGE_REVERSION_SIGNAL_TP_MULT": "0.7"},
        config_dir=cfg,
    )

    html = snapshots_to_html(snaps, json_link="strategy_configs.json")
    assert 'id="config-snapshot"' in html
    assert "config/morning_range_reversion.toml" in html
    assert "strategy_configs.json" in html
    assert "MORNING_RANGE_REVERSION_SIGNAL_TP_MULT" in html
    assert "0.7" in html
    assert "<details>" in html
    assert "[meta]" in html
    assert "symbols" in html
    assert "&quot;MNQ&quot;" in html


def test_snapshots_to_html_renders_missing_toml(tmp_path: Path) -> None:
    cfg = tmp_path / "config_strategies"
    cfg.mkdir(parents=True, exist_ok=True)

    snaps = snapshot_strategy_configs(
        ["mystery_strategy"],
        out_dir=tmp_path / "report",
        env_overrides={},
        config_dir=cfg,
    )
    html = snapshots_to_html(snaps)
    assert "mystery_strategy" in html
    assert "not snapshotted" in html
    assert "No TOML" in html


def test_snapshots_to_html_empty_returns_empty_string(tmp_path: Path) -> None:
    assert snapshots_to_html([]) == ""
