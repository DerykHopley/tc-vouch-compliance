"""The three-step review through the API (#372), for browser checks that seed a reviewed
assessment: Confirm findings, then each remediation item decided (Plan fixes), then Accept or
Reject. The page's own steps are test_review's."""

from collections.abc import Mapping, Sequence
from typing import Any, Literal

from click_burst import DOUBLE_CLICK_MS
from playwright.sync_api import APIRequestContext, Locator, Page, Response, expect

OWNER = "IT security officer"
"""The owner role an approval names, so a "not drafted" item (no owner) can be approved too."""

ItemDecision = Literal["approve", "discard"]


def _post(request: APIRequestContext, url: str, data: Any, headers: dict | None) -> dict:
    response = request.post(url, data=data, headers=headers or {})
    assert response.ok, (url, response.status, response.text())
    return response.json()


def confirmed(
    request: APIRequestContext,
    url: str,
    thread_id: str,
    overrides: Sequence[dict] = (),
    headers: dict | None = None,
) -> dict:
    """Confirm findings, with any overrides: the assessment at its next step."""
    found = f"{url}/api/assessments/{thread_id}/findings"
    return _post(request, found, {"overrides": list(overrides)}, headers)["assessment"]


def decided(
    request: APIRequestContext,
    url: str,
    thread_id: str,
    requirement_id: str,
    body: Mapping[str, Any],
    headers: dict | None = None,
) -> dict:
    """One remediation item decided: the item as kept."""
    item = f"{url}/api/assessments/{thread_id}/remediation/{requirement_id}"
    return _post(request, item, dict(body), headers)["item"]


def reviewed(
    request: APIRequestContext,
    url: str,
    thread_id: str,
    decision: Literal["accept", "reject"] = "accept",
    *,
    overrides: Sequence[dict] = (),
    decisions: Mapping[str, Mapping[str, Any]] | None = None,
    items: ItemDecision = "discard",
    headers: dict | None = None,
) -> dict:
    """The assessment reviewed: its findings confirmed with `overrides` (skipped for a Reject
    with none), the items in `decisions` decided (requirement id to body), and for an Accept
    every other draft decided as `items`; then the decision. The reviewed assessment."""
    if decision == "accept" or overrides or decisions:
        confirmed(request, url, thread_id, overrides, headers)
    for requirement_id, body in (decisions or {}).items():
        decided(request, url, thread_id, requirement_id, body, headers)
    if decision == "accept":
        shown = request.get(f"{url}/api/assessments/{thread_id}", headers=headers or {}).json()
        for item in shown["assessment"]["remediation"]:
            if item["status"] == "draft":
                body = {"decision": items} | ({"owner_role": OWNER} if items == "approve" else {})
                decided(request, url, thread_id, item["requirement_id"], body, headers)
    review = f"{url}/api/assessments/{thread_id}/review"
    return _post(request, review, {"decision": decision}, headers)["assessment"]


def decide(
    page: Page, bar: Locator, label: Literal["Accept verdict", "Reject verdict"]
) -> Response:
    """Presses a final decision in the review bar and confirms its ask (#634): the review route's
    response, once a double-click's time has passed, so the next click isn't swallowed."""
    bar.get_by_role("button", name=label).click()
    expect(bar.get_by_role("alert")).to_be_visible()
    with page.expect_response(lambda r: r.url.endswith("/review")) as answer:
        bar.get_by_role("button", name=label).click()
    # The next click comes after a double-click's time (#562).
    page.wait_for_timeout(DOUBLE_CLICK_MS)
    return answer.value
