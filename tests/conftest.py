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
