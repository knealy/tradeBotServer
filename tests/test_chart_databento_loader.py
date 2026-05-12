"""Tests for canonical Databento → GUI chart bar loading."""

from pathlib import Path

import pytest

from core.chart_databento_loader import (
    chart_symbol_to_databento_root,
    load_databento_bars_for_chart,
)


@pytest.mark.parametrize(
    "sym,root",
    [
        ("MNQ", "MNQ"),
        ("MNQM5", "MNQ"),
        ("MESZ4", "MES"),
        ("MGC", "MGC"),
        ("ES", None),
    ],
)
def test_chart_symbol_to_databento_root(sym: str, root: str | None) -> None:
    assert chart_symbol_to_databento_root(sym) == root


def test_load_databento_bars_from_fixture_csv(tmp_path: Path) -> None:
    price = tmp_path / "historical_data" / "price"
    price.mkdir(parents=True)
    csv_path = price / "MNQ_1m_databento.csv"
    csv_path.write_text(
        "timestamp,open,high,low,close,volume\n"
        "2025-01-02 10:00:00,100,101,99,100.5,10\n"
        "2025-01-02 10:01:00,100.5,102,100,101,20\n"
        "2025-01-02 10:02:00,101,103,101,102.5,30\n",
        encoding="utf-8",
    )
    bars, err = load_databento_bars_for_chart("MNQ", "1m", 2, repo_root=tmp_path)
    assert err is None
    assert len(bars) == 2
    assert bars[-1]["close"] == 102.5
    assert all("time" in b for b in bars)
