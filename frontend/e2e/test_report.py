"""The readiness report's browser checks (#56) run on a Vouch of their own (#234): Nordlicht (the
default profile) and Alpenwerk on the demo fake, with assessments of their own.

Nordlicht's check seeds one standing through the API and reads all of it on the page: 4.1.2
accepted with S1 overridden to not evidenced (so M1's drafted item is approved and S1's
discarded), 1.1.1 accepted, 5.2.9 left proposed. Alpenwerk's assesses and accepts 4.1.2 on the
German demo script (demo-de.json). The page checks only read, so they run in any order."""

import json
import re
from urllib.request import urlopen

from playwright.sync_api import APIRequestContext, Locator, Page, expect
from touch_targets import small_targets

LOCAL_USER = "Local user"
OWNER = "Information security officer"  # the demo script's owner for M1's item
NORDLICHT_REASON = "Nordlicht allows no remote access to its company network"
DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def test_the_report_server_answers_health(report_url):
    with urlopen(f"{report_url}/api/health", timeout=10) as response:
        assert response.status == 200
        assert json.load(response)["status"] == "ok"


def post(request: APIRequestContext, url: str, data: dict, profile: str) -> str:
    response = request.post(url, data=data, headers={"X-Vouch-Profile": profile})
    assert response.ok, (url, response.status, response.text())
    return response.text()


def assessed(request: APIRequestContext, report_url: str, control_id: str, profile: str) -> str:
    """Assesses a control under the profile on the server's fake model, up to the review: its
    thread id."""
    text = post(request, f"{report_url}/api/assessments", {"control_id": control_id}, profile)
    data = re.search(r"event: result\r?\ndata: (.*)", text)
    assert data, text
    return json.loads(data.group(1))["assessment"]["thread_id"]


def accepted(
    request: APIRequestContext,
    report_url: str,
    control_id: str,
    profile: str,
    overrides: list | None = None,
) -> str:
    thread_id = assessed(request, report_url, control_id, profile)
    review = {"decision": "accept", "overrides": overrides or []}
    post(request, f"{report_url}/api/assessments/{thread_id}/review", review, profile)
    return thread_id


def decided(request, report_url, thread_id: str, requirement_id: str, decision: str) -> None:
    url = f"{report_url}/api/assessments/{thread_id}/remediation/{requirement_id}"
    post(request, url, {"decision": decision}, "nordlicht")


def open_report(page: Page, report_url: str) -> Locator:
    with page.expect_response("**/api/report"):
        page.goto(f"{report_url}/report")
    return page.locator("article.report-doc")


def control_row(report: Locator, control_id: str) -> Locator:
    table = report.locator("table.report-controls")  # named in the report's language
    return table.locator("tbody tr").filter(
        has=report.page.locator("th[scope=row]").get_by_text(control_id, exact=True)
    )


def test_nordlichts_report_shows_its_reviewed_standing(page: Page, report_url):
    request = page.request
    s1_not_evidenced = [
        {"requirement_id": "S1", "verdict": "not-evidenced", "reason": "No per-system list."}
    ]
    thread = accepted(request, report_url, "4.1.2", "nordlicht", s1_not_evidenced)
    decided(request, report_url, thread, "S1", "discard")
    accepted(request, report_url, "1.1.1", "nordlicht")
    decided(request, report_url, thread, "M1", "approve")
    assessed(request, report_url, "5.2.9", "nordlicht")  # proposed: still open

    report = open_report(page, report_url)

    # Readiness (stories 1, 3): accepted verdicts only.
    readiness = report.get_by_role("region", name="Readiness")
    expect(readiness.locator(".report-lead")).to_have_text("2 of 12 controls accepted")
    expect(readiness).to_contain_text("10 Open")

    # The controls (story 2): each accepted one's verdict, reviewer and day; the proposed one open.
    for control_id in ("4.1.2", "1.1.1"):
        row = control_row(report, control_id)
        expect(row).to_contain_text("Accepted")
        expect(row.locator(".chip")).to_have_count(1)
        expect(row).to_contain_text(LOCAL_USER)
        expect(row.locator("td.mono")).to_have_text(DAY)
    expect(control_row(report, "4.1.2").locator(".chip")).to_have_text("Partly evidenced")
    proposed = control_row(report, "5.2.9")
    expect(proposed).to_contain_text("Open")
    expect(proposed).to_contain_text("Open: not yet accepted, so it has no reviewed verdict.")
    expect(proposed.locator(".chip")).to_have_count(0)
    expect(proposed).not_to_contain_text(LOCAL_USER)

    # The approved item under its control, the discarded one not (story 4).
    items = report.get_by_role("region", name="Remediation items")
    expect(items.locator(".report-group-head")).to_have_text(re.compile(r"^4\.1\.2 "))
    item_rows = items.locator("tbody tr")
    expect(item_rows).to_have_count(1)
    expect(item_rows.locator("th")).to_have_text("M1")
    expect(item_rows).to_contain_text(OWNER)
    expect(item_rows.locator("td").last).to_have_text(DAY)

    # Not applicable with the profile's reason (story 5).
    excluded = report.get_by_role("region", name="Not applicable")
    expect(excluded).to_contain_text("5.1.2 S3")
    expect(excluded).to_contain_text(NORDLICHT_REASON)

    # The trail, newest first, each with who, action and evidence versions (story 6).
    # Asserted with expect, which retries: a response's event fires before the page renders (#240).
    # 4.1.2's accept and its S1 override are one review, kept in either order.
    trail = report.get_by_role("table", name="Audit trail").locator("tbody tr")

    def entry(control: str, requirement: str, action: str) -> re.Pattern:
        """A row's who, control, requirement and action, with the cells between them."""
        cells = r"\s*".join([re.escape(LOCAL_USER), re.escape(control), requirement, action])
        return re.compile(cells)

    one_review = entry("4.1.2", "(S1)?", "(Overridden|Accepted)")
    expect(trail).to_have_text(
        [
            entry("4.1.2", "M1", "Approved"),
            entry("1.1.1", "", "Accepted"),
            entry("4.1.2", "S1", "Discarded"),
            one_review,
            one_review,
        ]
    )
    for kept in (trail.nth(0), trail.nth(1), trail.filter(has_text="Accepted").last):
        expect(kept.locator("td").nth(8)).to_have_text(re.compile(r"\S+ v\S+"))

    # The disclaimer, as served (story 7).
    expect(report.locator(".report-disclaimer")).to_have_text(
        "Drafted by an AI assistant, reviewed by Local user; not an audit result."
    )

    # A refresh opens the report again (story 1).
    report = open_report(page, report_url)
    expect(page.get_by_role("link", name="Report")).to_have_attribute("aria-current", "page")
    expect(report.locator(".report-lead")).to_have_text("2 of 12 controls accepted")


def test_alpenwerks_report_is_german_and_the_page_around_it_english(page: Page, report_url):
    accepted(page.request, report_url, "4.1.2", "alpenwerk")
    # The remembered profile (#201) opens Alpenwerk.
    page.add_init_script("window.localStorage.setItem('vouch.profile', 'alpenwerk')")

    report = open_report(page, report_url)

    expect(report).to_have_attribute("lang", "de")
    expect(report.get_by_role("heading", level=1)).to_have_text("Bereitschaftsbericht")
    expect(control_row(report, "4.1.2").locator(".chip")).to_have_text(
        re.compile(r"^(Teilweise nachgewiesen|Nachgewiesen)$")
    )
    expect(report.locator(".report-disclaimer")).to_have_text(
        "Erstellt von einem KI-Assistenten, geprüft von Local user; kein Auditergebnis."
    )
    # The interface stays English.
    nav = page.get_by_role("navigation", name="Main")
    expect(nav.get_by_role("link", name="Report", exact=True)).to_be_visible()
    expect(page.get_by_role("button", name=re.compile(r"your menu$", re.I))).to_be_visible()
    expect(page.get_by_role("button", name="Ask Vouch")).to_be_visible()


def test_at_400px_the_report_does_not_scroll_sideways_and_its_controls_are_44px(
    page: Page, report_url
):
    page.set_viewport_size({"width": 400, "height": 900})
    for scheme in ("light", "dark"):
        page.emulate_media(color_scheme=scheme)
        report = open_report(page, report_url)
        expect(report.get_by_role("heading", level=1)).to_be_visible()
        scrolls = page.evaluate(
            "document.documentElement.scrollWidth > document.documentElement.clientWidth"
        )
        assert not scrolls, f"the report scrolls sideways at 400px in {scheme}"
        assert small_targets(page) == []
