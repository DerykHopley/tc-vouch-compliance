"""The browser check runs against servers that are already up: run.sh starts them on free ports."""

import os

import pytest


@pytest.fixture
def ui_url() -> str:
    return os.environ["UI_URL"]


@pytest.fixture
def failing_api_url() -> str:
    """An API on a broken fake script: every assessment ends in one `error` event."""
    return os.environ["FAILING_API_URL"]
