"""The browser check runs against servers that are already up: run.sh starts them on free ports."""

import os

import pytest


@pytest.fixture
def vouch_url() -> str:
    """Vouch on one origin: the built front end and the API, as on Cloud Run."""
    return os.environ["VOUCH_URL"]


@pytest.fixture
def dossier_url() -> str:
    """A second Vouch like vouch_url, for the Dossier's checks: its own assessments and its own
    request-rate budget (local mode has one user, and every check spends from it)."""
    return os.environ["DOSSIER_URL"]


@pytest.fixture
def profiles_url() -> str:
    """A third Vouch like vouch_url on a profiles folder of its own (#47): nordlicht, testwerk
    and a broken profile; its own request-rate budget too."""
    return os.environ["PROFILES_URL"]


@pytest.fixture
def failing_api_url() -> str:
    """An API on a broken fake script: every assessment ends in one `error` event."""
    return os.environ["FAILING_API_URL"]
