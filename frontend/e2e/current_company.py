"""The current company (#480) for the browser checks: every user's company, set through the
developer API (local mode's one user is a developer). A check that changes a server's current
company puts Nordlicht back after, so the other checks on that server still open Nordlicht."""

from collections.abc import Iterator
from contextlib import contextmanager

from playwright.sync_api import APIRequestContext, Page

DEFAULT = "nordlicht"


def make_current(request: APIRequestContext, url: str, company: str) -> None:
    response = request.put(f"{url}/api/developer/current-company", data={"company": company})
    assert response.ok, (response.status, response.text())


def reopened_under(page: Page, url: str, company: str) -> None:
    """Makes `company` current on the server at `url`, then reloads the page open there (a page
    follows a new current company on its next open), waiting for its overview. Where a check once
    switched profile in the top bar; the check puts Nordlicht back (nordlicht_later)."""
    make_current(page.request, url, company)
    with page.expect_response(lambda r: r.url.endswith("/api/overview")):
        page.reload()


@contextmanager
def current_company(request: APIRequestContext, url: str, company: str) -> Iterator[None]:
    """`company` is the server's current company for the block, then Nordlicht again."""
    make_current(request, url, company)
    try:
        yield
    finally:
        make_current(request, url, DEFAULT)


@contextmanager
def nordlicht_after(request: APIRequestContext, url: str) -> Iterator[None]:
    """Nordlicht is the server's current company after the block, whatever it made current."""
    try:
        yield
    finally:
        make_current(request, url, DEFAULT)
