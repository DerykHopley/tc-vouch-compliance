"""The browser check runs against servers that are already up: run.sh starts them on free ports."""

import json
import os
import urllib.request
from collections.abc import Iterator
from typing import Any

import pytest

SERVERS = (
    "VOUCH_URL",
    "DOSSIER_URL",
    "PROFILES_URL",
    "PAPER_URL",
    "CHAT_URL",
    "REPORT_URL",
    "AFRESH_URL",
    "PASS_URL",
    "ROUNDS_URL",
    "PLAN_URL",
    "QUESTIONS_URL",
    "IMPROVEMENT_URL",
    "SETUP_URL",
    "FIRST_PASS_URL",
    "REMOVAL_URL",
    "DOCUMENTS_URL",
    "RESET_URL",
    "FAILING_API_URL",
)
"""Every test server's URL variable, as run.sh exports them."""
DEFAULT_LIMIT_SERVERS = ("PROFILES_URL",)
"""Servers left on the app's default daily assessment limit (30): the developer page's checks
there read it ("30 assessments per user per day") and change it and set it back to 30."""
E2E_DAILY_LIMIT = 1_000
"""The daily assessment limit every other test server starts with (#489): far above what any
server's checks start (the busiest, the working paper's, starts 30), so no check is refused with a
429 for the assessments its neighbours on the server started. Set through the app's own setting
(PUT /api/developer/daily-limit; local mode's one user is a developer): the limit is a stored
setting, not an environment variable."""


def _developer_settings(url: str, limit: int | None = None) -> dict[str, Any]:
    """The developer page's settings, as read, or as answered after the daily limit is changed
    to `limit`."""
    if limit is None:
        request = urllib.request.Request(f"{url}/api/developer/settings")
    else:
        request = urllib.request.Request(
            f"{url}/api/developer/daily-limit",
            data=json.dumps({"daily_limit": limit}).encode(),
            method="PUT",
            headers={"Content-Type": "application/json"},
        )
    with urllib.request.urlopen(request, timeout=30) as answer:
        return json.load(answer)


def _raise_chat_limit(url: str) -> None:
    """The chat's own daily limit (#628) to E2E_DAILY_LIMIT: every chat turn counts, the
    greeting too, and a server's checks together send more than the default 50."""
    request = urllib.request.Request(
        f"{url}/api/developer/chat-daily-limit",
        data=json.dumps({"chat_daily_limit": E2E_DAILY_LIMIT}).encode(),
        method="PUT",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=30):
        pass


@pytest.fixture(scope="session", autouse=True)
def daily_limit_above_the_suite() -> Iterator[None]:
    """Every test server but DEFAULT_LIMIT_SERVERS starts with E2E_DAILY_LIMIT (#489). After the
    suite, each still has room for one more assessment: a server whose checks used its whole daily
    limit answers the next one 429, which a check added there would meet."""
    for server in SERVERS:
        _raise_chat_limit(os.environ[server])
        if server not in DEFAULT_LIMIT_SERVERS:
            _developer_settings(os.environ[server], E2E_DAILY_LIMIT)
    yield
    full = []
    for server in SERVERS:
        settings = _developer_settings(os.environ[server])
        used, limit = settings["usage"]["fake"]["assessments"], settings["daily_limit"]
        if used >= limit:
            full.append(f"{server} used {used} assessments of its daily limit {limit}")
    assert not full, f"test servers at their daily assessment limit: {full}"


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
    the hero and the comparison other checks read, and accepting a control the counts. Rounds are
    kept per company: Nordlicht's there are test_rounds' alone (its first check needs the first
    round still open), and a check from another file closes Alpenwerk's instead (#525)."""
    return os.environ["ROUNDS_URL"]


@pytest.fixture
def plan_url() -> str:
    """A tenth Vouch like vouch_url, for the improvement plan's checks (#410): its goal is Internal
    improvement and its control ends Closed, which other checks would read."""
    return os.environ["PLAN_URL"]


@pytest.fixture
def questions_url() -> str:
    """An eleventh Vouch like vouch_url, for the open questions' checks (#414): its control is
    accepted, questioned and assessed again, which would change the counts and cards other checks
    read."""
    return os.environ["QUESTIONS_URL"]


@pytest.fixture
def improvement_url() -> str:
    """A twelfth Vouch like vouch_url, for the improvement report's check (#420): its goal is
    Internal improvement and a round is closed, which would change the report other checks read."""
    return os.environ["IMPROVEMENT_URL"]


@pytest.fixture
def setup_url() -> str:
    """A thirteenth Vouch like vouch_url, for company setup's create (#409): the company it makes
    stays in that process (local mode), so no other check lists it; a create makes it current
    there (#480), so each create check puts Nordlicht back (nordlicht_later). Its three
    creates (#409, #424, #454) each run a profile pass: the passes' daily limit (3 a user), so a
    fourth create needs a server of its own."""
    return os.environ["SETUP_URL"]


@pytest.fixture
def first_pass_url() -> str:
    """A fourteenth Vouch like setup_url, for the profile pass a create starts (#447): every create
    on setup_url runs one, and the passes' daily limit (3 a user) would refuse story 5's."""
    return os.environ["FIRST_PASS_URL"]


@pytest.fixture
def removal_url() -> str:
    """A fifteenth Vouch like setup_url, for removing a company made in setup (#418): its three
    creates each run a profile pass (#447), and with setup_url's three they would pass the
    passes' daily limit (3 a user)."""
    return os.environ["REMOVAL_URL"]


@pytest.fixture
def documents_url() -> str:
    """A sixteenth Vouch like vouch_url, for adding documents (#585): an added document is kept
    in that process for its company and found by every search there, which would change the
    documents and the evidence other servers' checks read."""
    return os.environ["DOCUMENTS_URL"]


@pytest.fixture
def reset_url() -> str:
    """A seventeenth Vouch like vouch_url, for Reset demo's check (#605): it deletes every user's
    progress under Nordlicht and seeds the demo round, which the other servers' checks would read."""
    return os.environ["RESET_URL"]


@pytest.fixture
def nordlicht_later(page):
    """Registers a server whose current company this check changes (#480: a create makes its
    company current); Nordlicht is made current there again once the check ends, pass or fail."""
    from current_company import make_current

    urls: list[str] = []
    yield urls.append
    for url in urls:
        make_current(page.request, url, "nordlicht")


@pytest.fixture
def failing_api_url() -> str:
    """An API on a broken fake script: every assessment ends in one `error` event."""
    return os.environ["FAILING_API_URL"]
