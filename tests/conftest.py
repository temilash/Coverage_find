"""Shared test setup: query logs go to a temporary folder, never the project's logs/."""

import pytest

from coverage_detective.data import db


@pytest.fixture(autouse=True)
def _query_log(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "LOG_PATH", tmp_path / "logs" / "queries.jsonl")
