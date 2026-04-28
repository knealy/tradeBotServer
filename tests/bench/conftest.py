"""Shared fixtures for performance benchmarks."""

import pytest


@pytest.fixture
def benchmark_rounds():
    """Tight rounds for local dev; override via scripts/run_bench.sh."""
    return 5
