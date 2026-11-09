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
def paper_url() -> str:
    """A fourth Vouch like vouch_url, for the working paper's checks (#41), for the same reason."""
    return os.environ["PAPER_URL"]


@pytest.fixture
def chat_url() -> str:
    """A fifth Vouch like vouch_url, for the chat's checks (#44): its own request-rate budget."""
    return os.environ["CHAT_URL"]


@pytest.fixture
def report_url() -> str:
    """A sixth Vouch like vouch_url, for the readiness report's checks (#234): Nordlicht and
    Alpenwerk on the demo fake, its own assessments and request-rate budget."""
    return os.environ["REPORT_URL"]


@pytest.fixture
def afresh_url() -> str:
    """A seventh Vouch like vouch_url, for the fresh start's check (#285): starting Nordlicht
    afresh there hides no other check's progress."""
    return os.environ["AFRESH_URL"]


@pytest.fixture
def pass_url() -> str:
    """An eighth Vouch like vouch_url, for the profile pass's checks (#390): a pass assesses all
    12 controls, which would change the counts other checks read."""
    return os.environ["PASS_URL"]


@pytest.fixture
def rounds_url() -> str:
    """A ninth Vouch like vouch_url, for the review rounds' checks (#402): closing a round changes
    the hero and the comparison other checks read, and accepting a control the counts."""
    return os.environ["ROUNDS_URL"]


@pytest.fixture
def failing_api_url() -> str:
    """An API on a broken fake script: every assessment ends in one `error` event."""
    return os.environ["FAILING_API_URL"]
