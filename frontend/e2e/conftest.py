"""The browser check runs against servers that are already up: run.sh starts them on free ports."""

import os

import pytest


@pytest.fixture
def vouch_url() -> str:
    """Vouch on one origin: the built front end and the API, as on Cloud Run."""
    return os.environ["VOUCH_URL"]


@pytest.fixture
def failing_api_url() -> str:
    """An API on a broken fake script: every assessment ends in one `error` event."""
    return os.environ["FAILING_API_URL"]
