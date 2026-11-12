"""The lead approves or discards a remediation item on the working paper (#43): approved and kept,
with its audit entry; discarded and gone from the overview's open count; a past due date refused;
a double click approving once; one profile's items not shown under another.

On the working paper's server (paper_url), as #42's review checks. Each check assesses 4.1.2
afresh (the demo script: M1 partly evidenced, the rest evidenced, so M1's item is drafted,
#212) and confirms its findings through the API with M1 overridden to evidenced (its draft
dropped) and S1 to not evidenced, which gives S1 a "not drafted" item for the lead to fill in at
Plan fixes (#372). The per-profile check runs on profiles_url (#47)."""

import json
import re
from datetime import date, timedelta

from current_company import reopened_under
from playwright.sync_api import APIRequestContext, Locator, Page, expect
from reviewing import confirmed, decide
from test_review import CONTROL, assessed, open_paper
from top_bar import back_to_overview

OWNER = "IT security officer"
ITEM = "S1"
OVERRIDES = [
    {"requirement_id": "M1", "verdict": "evidenced", "reason": "The exposure record exists."},
    {"requirement_id": ITEM, "verdict": "not-evidenced", "reason": "No per-system list."},
]


def confirmed_with_an_item(
    request: APIRequestContext, url: str, headers: dict | None = None
) -> str:
    """4.1.2 assessed, its findings confirmed with M1 overridden to evidenced and S1 to not
    evidenced: at Plan fixes, its thread id."""
    thread_id = assessed_on(request, url, headers)
    confirmed(request, url, thread_id, OVERRIDES, headers)
    return thread_id


def assessed_on(request: APIRequestContext, url: str, headers: dict | None) -> str:
    if headers is None:
        return assessed(request, url)["assessment"]["thread_id"]
    response = request.post(f"{url}/api/assessments", data={"control_id": CONTROL}, headers=headers)
    assert response.ok, response.status
    data = re.search(r"event: result\r?\ndata: (.*)", response.text())
    assert data, response.text()
    return json.loads(data.group(1))["assessment"]["thread_id"]


def item(paper: Locator) -> Locator:
    return paper.get_by_role("region", name=f"Remediation item {ITEM}")  # its own row (#464)


def stored_items(request: APIRequestContext, url: str, thread_id: str) -> list[dict]:
    return request.get(f"{url}/api/assessments/{thread_id}").json()["assessment"]["remediation"]


def decisions(request: APIRequestContext, url: str, thread_id: str, action: str) -> int:
    entries = request.get(f"{url}/api/audit-trail?limit=500").json()["entries"]
    return sum(1 for e in entries if (e["thread_id"], e["action"]) == (thread_id, action))


def remediation_tile(page: Page) -> Locator:
    band = page.get_by_role("region", name="Readiness")
    tile = band.locator(".tile").filter(has=page.get_by_text("remediation items", exact=True))
    return tile.locator("b")


def open_count(request: APIRequestContext, url: str) -> int:
    """The overview's open remediation items, from the API: the number the tile shows once read."""
    return request.get(f"{url}/api/overview").json()["readiness"]["remediation_items"]


def back_on_the_overview(page: Page) -> Locator:
    """Back to the overview: its remediation tile. The response event fires on the answer's
    headers, before the app has read the body and shown the new count, so assert on the tile
    with expect (which waits), never a one-time read (#240)."""
    with page.expect_response("**/api/overview"):
        back_to_overview(page)
    return remediation_tile(page)


def test_approve_keeps_the_item_with_the_leads_owner_and_writes_an_audit_entry(
    page: Page, paper_url
):
    thread_id = confirmed_with_an_item(page.request, paper_url)
    paper = open_paper(page, paper_url)
    draft = item(paper)
    expect(draft).to_contain_text("Draft")
    # Vouch suggested no owner for this item: Approve waits for one.
    expect(draft.get_by_role("button", name=f"Approve {ITEM}")).to_be_disabled()
    draft.get_by_label("Owner role").fill(OWNER)
    with page.expect_response(re.compile(rf"/remediation/{ITEM}$")) as answer:
        draft.get_by_role("button", name=f"Approve {ITEM}").click()
    assert answer.value.ok, answer.value.status

    expect(draft).to_contain_text("Approved")
    expect(draft).to_contain_text(f"Owner: {OWNER}")
    expect(draft.get_by_role("button")).to_have_count(0)
    # Then the API: the item is kept as approved, and the trail has its entry.
    kept = stored_items(page.request, paper_url, thread_id)
    assert [(i["status"], i["owner_role"]) for i in kept] == [("approved", OWNER)]
    assert decisions(page.request, paper_url, thread_id, "approve") == 1


def test_a_discarded_item_stays_out_of_the_open_count_once_accepted(page: Page, paper_url):
    confirmed_with_an_item(page.request, paper_url)
    before = open_count(page.request, paper_url)
    paper = open_paper(page, paper_url)

    with page.expect_response(re.compile(rf"/remediation/{ITEM}$")):
        item(paper).get_by_role("button", name=f"Discard {ITEM}").click()
    expect(item(paper)).to_contain_text("Discarded")
    bar = paper.get_by_role("region", name="Review")
    decide(page, bar, "Accept verdict")
    expect(bar.get_by_text("Accepted", exact=True)).to_be_visible()

    expect(back_on_the_overview(page)).to_have_text(str(before))  # accepted, its item discarded


def test_a_past_due_date_is_refused_with_a_hint(page: Page, paper_url):
    thread_id = confirmed_with_an_item(page.request, paper_url)
    paper = open_paper(page, paper_url)
    past = (date.today() - timedelta(days=2)).isoformat()  # past in every time zone

    draft = item(paper)
    draft.get_by_label("Owner role").fill(OWNER)
    draft.get_by_label("Due date").fill(past)
    with page.expect_response(re.compile(rf"/remediation/{ITEM}$")) as answer:
        draft.get_by_role("button", name=f"Approve {ITEM}").click()
    assert answer.value.status == 422

    expect(draft.get_by_role("alert")).to_have_text(
        f"The due date {past} has passed. Choose today or a later date."
    )
    expect(draft.get_by_label("Due date")).to_have_attribute("aria-invalid", "true")
    expect(draft).to_contain_text("Draft")
    assert [i["status"] for i in stored_items(page.request, paper_url, thread_id)] == ["draft"]


def test_a_double_click_approves_once(page: Page, paper_url):
    thread_id = confirmed_with_an_item(page.request, paper_url)
    paper = open_paper(page, paper_url)

    item(paper).get_by_label("Owner role").fill(OWNER)
    with page.expect_response(re.compile(rf"/remediation/{ITEM}$")):
        item(paper).get_by_role("button", name=f"Approve {ITEM}").dblclick()
    expect(item(paper)).to_contain_text("Approved")

    kept = stored_items(page.request, paper_url, thread_id)
    assert [i["status"] for i in kept] == ["approved"]
    assert decisions(page.request, paper_url, thread_id, "approve") == 1


def scrolls_sideways(page: Page) -> bool:
    return page.evaluate(
        "document.documentElement.scrollWidth > document.documentElement.clientWidth"
    )


def test_the_draft_fits_at_400px_in_light_and_dark(page: Page, paper_url):
    confirmed_with_an_item(page.request, paper_url)
    page.set_viewport_size({"width": 400, "height": 900})
    for scheme in ("light", "dark"):
        page.emulate_media(color_scheme=scheme)
        paper = open_paper(page, paper_url)
        expect(item(paper).get_by_role("button", name=f"Approve {ITEM}")).to_be_visible()
        assert not scrolls_sideways(page), f"the draft scrolls sideways at 400px in {scheme}"


def test_one_profiles_approved_items_do_not_show_under_another(
    page: Page, profiles_url, nordlicht_later
):
    nordlicht_later(profiles_url)
    nordlicht = {"X-Vouch-Profile": "nordlicht"}
    thread_id = confirmed_with_an_item(page.request, profiles_url, nordlicht)
    approved = page.request.post(
        f"{profiles_url}/api/assessments/{thread_id}/remediation/{ITEM}",
        data={"decision": "approve", "owner_role": OWNER},
        headers=nordlicht,
    )
    assert approved.ok, approved.status
    accepted = page.request.post(
        f"{profiles_url}/api/assessments/{thread_id}/review",
        data={"decision": "accept"},
        headers=nordlicht,
    )
    assert accepted.ok, accepted.status

    with page.expect_response("**/api/profiles"), page.expect_response("**/api/overview"):
        page.goto(profiles_url)
    expect(remediation_tile(page)).to_have_text("1")

    reopened_under(page, profiles_url, "testwerk")
    expect(remediation_tile(page)).to_have_text("0")
    elsewhere = page.request.get(
        f"{profiles_url}/api/assessments/{thread_id}", headers={"X-Vouch-Profile": "testwerk"}
    )
    assert elsewhere.status == 404
