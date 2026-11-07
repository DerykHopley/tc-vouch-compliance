"""Tests start from no Vouch settings, whatever the developer's shell exports.

`vouch.api` builds its app from the environment when imported, so this runs first.
"""

import os
import socket

import pytest

from vouch.faithfulness import DEEPEVAL_SWITCHES
from vouch.settings import ENV_NAMES

for name in ENV_NAMES:
    os.environ.pop(name, None)


@pytest.fixture
def closed_port() -> int:
    """A local port nothing listens on: bound by the system, then let go."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(autouse=True)
def _deepeval_switches_stay_in_their_test(monkeypatch):
    """vouch.faithfulness sets DeepEval's switches in the process environment before importing
    it; each test starts without them and ends with them gone again."""
    for name in DEEPEVAL_SWITCHES:
        monkeypatch.delenv(name, raising=False)


POSTGRES_FIXTURES = {"empty_database", "postgres"}
"""The fixtures that give a test a Postgres database (test_database, test_memory)."""


def _uses_postgres(item: pytest.Item) -> bool:
    """Whether the test runs against Postgres: it asks for a Postgres fixture, or it is a
    parameter marked needs_postgres (database_url's and any_store's "postgres", which reach the
    fixture only when they run)."""
    if POSTGRES_FIXTURES & set(getattr(item, "fixturenames", ())):
        return True
    return any(
        "VOUCH_TEST_DATABASE_URL" in str(mark.kwargs.get("reason", ""))
        for mark in item.iter_markers("skipif")
    )


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Every Postgres-backed test carries the `postgres` marker (#259): CI's database job runs
    `pytest -m postgres`, so a new one is picked up with no list to keep."""
    for item in items:
        if _uses_postgres(item):
            item.add_marker(pytest.mark.postgres)
