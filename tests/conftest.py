"""Shared test setup: run traces go to a temporary folder, never the project's logs/."""

import pytest

from coverage_detective.agents import trace_log


@pytest.fixture(autouse=True)
def _trace_logs(monkeypatch, tmp_path):
    monkeypatch.setattr(trace_log, "LOGS", tmp_path / "logs")
