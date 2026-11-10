"""The readiness report's browser checks (#56) run on a Vouch of their own (#234): Nordlicht (the
default profile) and Alpenwerk on the demo fake, with assessments of their own.

Nordlicht's check seeds one standing through the API and reads all of it on the page: 4.1.2
accepted with S1 overridden to not evidenced (so M1's drafted item is approved and S1's
discarded), 1.1.1 accepted, 5.2.9 left proposed. Alpenwerk's assesses and accepts 4.1.2 on the
German demo script (demo-de.json). The page checks only read, so they run in any order."""

import json
import re
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.request import urlopen

import pdf_words
import pytest
from playwright.sync_api import APIRequestContext, Locator, Page, Route, expect
from reviewing import reviewed
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
    decisions: dict | None = None,
) -> str:
    """The control assessed and accepted through the three steps (#372): the items `decisions`
    names decided so, every other one discarded. Its thread id."""
    thread_id = assessed(request, report_url, control_id, profile)
    headers = {"X-Vouch-Profile": profile}
    reviewed(
        request,
        report_url,
        thread_id,
        overrides=overrides or [],
        decisions=decisions,
        headers=headers,
    )
    return thread_id


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
    accepted(
        request,
        report_url,
        "4.1.2",
        "nordlicht",
        s1_not_evidenced,
        {"S1": {"decision": "discard"}, "M1": {"decision": "approve"}},
    )
    accepted(request, report_url, "1.1.1", "nordlicht")  # its items discarded
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
    # Its status word alone (#463): no repeated sentence, its verdict cell empty.
    expect(proposed).not_to_contain_text("not yet accepted")
    expect(proposed.locator("[data-label=Verdict]")).to_have_text("")
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
    # Each review in its three steps (#372): the overrides and Findings confirmed (one time, the
    # override first kept), the items decided, then Accepted. 1.1.1's items are discarded.
    trail = report.get_by_role("table", name="Audit trail").locator("tbody tr")

    def entry(control: str, requirement: str, action: str) -> re.Pattern:
        """A row's who, control, requirement and action, with the cells between them."""
        cells = r"\s*".join([re.escape(LOCAL_USER), re.escape(control), requirement, action])
        return re.compile(cells)

    expect(trail).to_have_text(
        [
            entry("1.1.1", "", "Accepted"),
            entry("1.1.1", r"S\d", "Discarded"),
            entry("1.1.1", r"S\d", "Discarded"),
            entry("1.1.1", "", "Findings confirmed"),
            entry("4.1.2", "", "Accepted"),
            entry("4.1.2", "M1", "Approved"),
            entry("4.1.2", "S1", "Discarded"),
            entry("4.1.2", "", "Findings confirmed"),
            entry("4.1.2", "S1", "Overridden"),
        ]
    )
    for kept in (trail.nth(0), trail.nth(5), trail.filter(has_text="Accepted").last):
        expect(kept.locator("td[data-label=Evidence]")).to_have_text(re.compile(r"\S+ v\S+"))
    # Every trail cell stays a table cell, so a row's rule lines up across it.
    displays = trail.locator("td").evaluate_all(
        "cells => cells.map(c => getComputedStyle(c).display)"
    )
    assert set(displays) == {"table-cell"}, displays

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
    expect(page.get_by_role("button", name=re.compile(r"[Yy]our menu$"))).to_be_visible()
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


# --- The controls and trail tables fit (#463) ---------------------------------------------------

TABLES = ("table.report-controls", "table.report-trail")


def with_a_full_trail(page: Page) -> None:
    """The report as served, with the recorded Nordlicht trail and a long entry added, so the trail
    has every column filled (long notes, evidence ids, model names) whatever this server holds."""
    recorded = json.loads(NORDLICHT_REPORT.read_text())["trail"]["entries"]

    def patched(route: Route) -> None:
        served = route.fetch().json()
        served["trail"] = {"entries": [*recorded, long_entry()], "next": None}
        route.fulfill(json=served)

    page.route("**/api/report", patched)


def tables_overflowing(report: Locator) -> list[str]:
    """Each controls or trail table wider than its container, with its widths."""
    return report.evaluate(
        """(el, selectors) => selectors.flatMap((s) => [...el.querySelectorAll(s)].map((t) => {
          const box = t.parentElement
          const wide = Math.max(t.scrollWidth, box.scrollWidth)
          return wide > box.clientWidth ? `${s}: ${wide} in ${box.clientWidth}` : null
        })).filter(Boolean)""",
        list(TABLES),
    )


def set_goal(page: Page, url: str, profile: str, goal: str) -> None:
    answer = page.request.put(
        f"{url}/api/goal", data={"goal": goal}, headers={"X-Vouch-Profile": profile}
    )
    assert answer.ok, answer.text()


@pytest.mark.parametrize("goal", ["audit-readiness", "internal-improvement"])
def test_at_1440_and_1024_neither_table_scrolls_sideways(page: Page, request, goal):
    """#463: the trail was 1,231-1,250px in a 902px sheet at 1440, and the controls table carried
    empty columns. Nordlicht's report is English, Alpenwerk's German. The improvement report has a
    server of its own, as its goal would change what the readiness checks read."""
    url = request.getfixturevalue("report_url" if goal == "audit-readiness" else "improvement_url")
    if goal != "audit-readiness":  # each profile's default
        for profile in ("nordlicht", "alpenwerk"):
            set_goal(page, url, profile, goal)
    accepted(page.request, url, "4.1.2", "alpenwerk")  # a German reviewed row: chip, reviewer, day
    with_a_full_trail(page)
    for profile in ("nordlicht", "alpenwerk"):
        page.add_init_script(f"window.localStorage.setItem('vouch.profile', '{profile}')")
        for width in (1440, 1024):
            page.set_viewport_size({"width": width, "height": 900})
            report = open_report(page, url)
            expect(report.locator("table.report-trail tbody tr").first).to_be_visible()
            if goal == "audit-readiness":
                expect(report.locator("table.report-controls")).to_be_visible()
            assert tables_overflowing(report) == [], (profile, width)


def test_at_390_each_row_of_both_tables_reads_as_a_stacked_card(page: Page, report_url):
    page.set_viewport_size({"width": 390, "height": 900})
    page.route("**/api/report", lambda route: route.fulfill(json=nordlicht_with_long_entry()))
    for scheme in ("light", "dark"):
        page.emulate_media(color_scheme=scheme)
        report = open_report(page, report_url)
        expect(report.locator("table.report-controls")).to_be_visible()
        scrolls = page.evaluate(
            "document.documentElement.scrollWidth > document.documentElement.clientWidth"
        )
        assert not scrolls, f"the report scrolls sideways at 390px in {scheme}"
        assert tables_overflowing(report) == [], scheme

        # The first control (1.1.1, accepted): its Status, Verdict and Reviewed by on screen.
        first = report.locator("table.report-controls tbody tr").first
        # The shown column name is not read with the value: the header row still names it.
        expect(first.get_by_role("cell", name="Accepted", exact=True)).to_have_count(1)
        for label in ("Status", "Verdict", "Reviewed by"):
            cell = first.locator(f"[data-label='{label}']")
            cell.scroll_into_view_if_needed()
            expect(cell).to_be_in_viewport(ratio=1)
            expect(cell).not_to_have_text("")

        # Every filled cell of every row lies within the screen, stacked: one cell a line.
        for selector in TABLES:
            cells = report.locator(f"{selector} tbody tr:not(.report-quote-row) > :not(:empty)")
            boxes = cells.evaluate_all(
                """cells => cells.map((c) => {
                  const r = c.getBoundingClientRect()
                  return [c.dataset.label, r.left, r.right, getComputedStyle(c).display]
                })"""
            )
            outside = [b for b in boxes if b[1] < 0 or b[2] > 390.5]
            assert outside == [], (selector, outside[:5])
            assert {b[3] for b in boxes} == {"grid"}, (selector, {b[3] for b in boxes})
        assert small_targets(page) == []


def long_entry() -> dict:
    return {
        "id": 1,
        "at": "2026-11-06T14:05:00+00:00",
        "reviewer_name": LOCAL_USER,
        "control_id": "4.1.2",
        "requirement_id": "M1",
        "action": "accept",
        "note": LONG_NOTE,
        "verdict_before": "partly-evidenced",
        "verdict_after": "partly-evidenced",
        "evidence": [
            {"doc_id": "authentication-standard", "version": "3.1"},
            {"doc_id": "privileged-access-management-procedure", "version": "12.4"},
        ],
        "model": "anthropic/claude-sonnet-4.5-with-a-long-model-name",
        "prompt_version": "m4-answer-language-2",
    }


def nordlicht_with_long_entry() -> dict:
    served = json.loads(NORDLICHT_REPORT.read_text())
    served["trail"]["entries"].append(long_entry())
    return served


# --- Export (#57, story 9; #308, story 4 of spec #321) ---------------------------------------

EXPORT_FAILED = "The readiness report could not be exported just now. Please try again."


def test_export_markdown_downloads_one_markdown_file_under_the_servers_name(page: Page, report_url):
    report = open_report(page, report_url)
    title = report.get_by_role("heading", level=1).text_content()
    disclaimer = report.locator(".report-disclaimer").text_content()

    with page.expect_download() as download_info:
        page.get_by_role("button", name="Export Markdown").click()
    download = download_info.value

    name = download.suggested_filename
    assert re.fullmatch(r"readiness-report-nordlicht-\d{4}-\d{2}-\d{2}\.md", name), name
    text = Path(download.path()).read_text(encoding="utf-8")
    assert text.startswith(f"# {title}\n")
    assert text.rstrip().endswith(str(disclaimer))
    expect(page.get_by_role("button", name="Export Markdown")).to_be_enabled()


def test_export_pdf_downloads_alpenwerks_report_as_one_pdf_file(page: Page, report_url):
    accepted(page.request, report_url, "4.1.2", "alpenwerk")
    page.add_init_script("window.localStorage.setItem('vouch.profile', 'alpenwerk')")
    downloads = []
    page.on("download", lambda download: downloads.append(download))
    report = open_report(page, report_url)
    title = str(report.get_by_role("heading", level=1).text_content())
    disclaimer = str(report.locator(".report-disclaimer").text_content())

    with page.expect_download() as download_info:
        page.get_by_role("button", name="Export PDF").click()
    download = download_info.value

    name = download.suggested_filename
    assert re.fullmatch(r"readiness-report-alpenwerk-\d{4}-\d{2}-\d{2}\.pdf", name), name
    data = Path(download.path()).read_bytes()
    assert data.startswith(b"%PDF-")
    text = " ".join(word.text for word in pdf_words.read(data).words)
    assert title == "Bereitschaftsbericht"
    assert text.startswith("VOUCH")  # the letterhead (#352), then the title in capitals
    assert f"Alpenwerk Präzision GmbH {title.upper()} " in text
    assert " ".join(disclaimer.split()) in text
    expect(page.get_by_role("button", name="Export PDF")).to_be_enabled()
    assert len(downloads) == 1


def test_a_pdf_export_answered_503_says_so_and_downloads_nothing(page: Page, report_url):
    page.set_viewport_size({"width": 400, "height": 900})
    downloads = []
    page.on("download", lambda download: downloads.append(download))
    page.route(
        "**/api/report/pdf",
        lambda route: route.fulfill(status=503, json={"detail": "The database is unavailable."}),
    )
    report = open_report(page, report_url)

    with page.expect_response("**/api/report/pdf"):
        page.get_by_role("button", name="Export PDF").click()

    expect(page.get_by_role("alert")).to_have_text(EXPORT_FAILED)
    expect(report.get_by_role("heading", level=1)).to_be_visible()
    for name in ("Export Markdown", "Export PDF"):
        button = page.get_by_role("button", name=name)
        expect(button).to_be_enabled()
        box = button.bounding_box()
        assert box is not None and box["width"] >= 44 and box["height"] >= 44, (name, box)
    assert downloads == []
    assert small_targets(page) == []


@pytest.mark.parametrize(
    ("width", "scheme"),
    [(w, s) for w in (1280, 800, 400) for s in ("light", "dark")],
)
def test_the_export_error_sits_under_both_buttons_at_every_width(
    page: Page, report_url, width, scheme
):
    """#355: at 1280px the alert sat beside the buttons, as the row wrapped only when it had to;
    docs/design.md and #308 put it under them at every width."""
    page.set_viewport_size({"width": width, "height": 900})
    page.emulate_media(color_scheme=scheme)
    page.route("**/api/report/pdf", lambda route: route.fulfill(status=503, json={"detail": "x"}))
    open_report(page, report_url)

    with page.expect_response("**/api/report/pdf"):
        page.get_by_role("button", name="Export PDF").click()

    alert = page.get_by_role("alert")
    expect(alert).to_have_text(EXPORT_FAILED)
    alert_box = alert.bounding_box()
    assert alert_box is not None
    for name in ("Export Markdown", "Export PDF"):
        button_box = page.get_by_role("button", name=name).bounding_box()
        assert button_box is not None
        assert alert_box["y"] >= button_box["y"] + button_box["height"], (name, width)


# --- Print (#57, story 10) ----------------------------------------------------------------------

OLDER_ENTRIES = "Older entries are in the audit trail."
LONG_NOTE = "Checked against the rollout plan and the admin register. " * 6
PRINT_WIDTH = 794  # A4 at 96 dpi


def with_older_entries(page: Page) -> None:
    """The report as served, with a long trail entry added and `next` set, so the trail table and
    the older entries sentence are there whatever the other checks have recorded."""

    def patched(route: Route) -> None:
        served = route.fetch().json()
        added = {
            "id": 1,
            "at": "2026-11-06T14:05:00+00:00",
            "reviewer_name": LOCAL_USER,
            "control_id": "4.1.2",
            "requirement_id": "M1",
            "action": "accept",
            "note": LONG_NOTE,
            "verdict_before": "partly-evidenced",
            "verdict_after": "partly-evidenced",
            "evidence": [{"doc_id": "authentication-standard", "version": "3.1"}],
            "model": "scripted-fake-model-with-a-long-name",
            "prompt_version": "assess-2026-11-01",
        }
        served["trail"] = {"entries": [*served["trail"]["entries"], added], "next": 1}
        route.fulfill(json=served)

    page.route("**/api/report", patched)


def shown(locator: Locator) -> bool:
    return any(locator.nth(n).is_visible() for n in range(locator.count()))


def test_printed_only_the_report_shows_and_its_tables_fit_the_page(page: Page, report_url):
    page.set_viewport_size({"width": 1440, "height": 900})  # pinning needs 1402px (#465)
    with_older_entries(page)
    report = open_report(page, report_url)
    older = report.get_by_text(OLDER_ENTRIES)
    expect(page.get_by_role("button", name="Show older entries")).to_be_visible()
    expect(older).to_be_hidden()  # on screen the button stands for it (#56)
    page.get_by_role("button", name="Ask Vouch").click()
    page.get_by_role("button", name="Make larger").click()
    page.get_by_role("button", name="Pin beside the page").click()
    chat = page.locator("aside[aria-label='Chat with Vouch']")
    expect(chat).to_have_class("bubble pinned")

    page.set_viewport_size({"width": PRINT_WIDTH, "height": 1123})
    page.emulate_media(media="print", color_scheme="dark")

    # The app around the report is left out: only the article prints.
    chrome = {
        "top bar": page.locator("header.top"),
        "tabs": page.get_by_role("navigation", name="Main"),
        "chat button": page.locator(".fab"),
        "pinned chat": chat,
        "Export Markdown": page.get_by_role("button", name="Export Markdown"),
        "Export PDF": page.get_by_role("button", name="Export PDF"),
        "Show older entries": page.get_by_role("button", name="Show older entries"),
    }
    assert [name for name, part in chrome.items() if shown(part)] == []
    expect(report).to_be_visible()
    expect(older).to_be_visible()

    # Every table lies within the article, and no table container scrolls sideways.
    article = report.bounding_box()
    assert article is not None
    tables = report.locator("table")
    assert tables.count() >= 2
    for n in range(tables.count()):
        box = tables.nth(n).bounding_box()
        assert box is not None
        assert box["x"] + box["width"] <= article["x"] + article["width"] + 0.5, n
    scrolling = report.locator(".table-wrap").evaluate_all(
        "els => els.filter((el) => el.scrollWidth > el.clientWidth).length"
    )
    assert scrolling == 0

    # Rows stay whole, headers repeat on each page, and the trail's cells stay table cells (#275).
    styles = report.evaluate(
        """el => ({
          rows: [...el.querySelectorAll('tr')].map((r) => getComputedStyle(r).breakInside),
          heads: [...el.querySelectorAll('thead')].map((h) => getComputedStyle(h).display),
          cells: [...el.querySelectorAll('.report-trail td')].map((c) => getComputedStyle(c).display),
        })"""
    )
    assert set(styles["rows"]) == {"avoid"}
    assert set(styles["heads"]) == {"table-header-group"}
    assert styles["cells"] and set(styles["cells"]) == {"table-cell"}

    # The light theme's ink on white, though the screen is dark.
    colours = report.evaluate(
        """el => [getComputedStyle(document.body).backgroundColor,
          getComputedStyle(el).backgroundColor, getComputedStyle(el).color]"""
    )
    assert colours == ["rgb(255, 255, 255)", "rgb(255, 255, 255)", "rgb(27, 29, 33)"]

    # A link in the article prints its target after its text.
    after = report.evaluate(
        """el => {
          const a = document.createElement('a')
          a.href = 'https://example.com/policy'
          a.textContent = 'the policy'
          el.querySelector('.report-disclaimer').append(a)
          return getComputedStyle(a, '::after').content
        }"""
    )
    assert "https://example.com/policy" in after


def test_the_older_entries_sentence_is_not_there_when_the_trail_has_no_more(page: Page, report_url):
    report = open_report(page, report_url)
    page.emulate_media(media="print")
    expect(report.get_by_role("heading", level=1)).to_be_visible()
    expect(report.get_by_text(OLDER_ENTRIES)).to_have_count(0)


# A real A4 print (#296): page breaks move things that print emulation can't show. Chromium only,
# as only it prints to PDF; the report is the seeded Nordlicht one, as recorded (two controls
# accepted), so its page breaks fall where the bug was found.
NORDLICHT_REPORT = Path(__file__).parent / "nordlicht_report.json"
MM = 72 / 25.4  # points


@pytest.fixture
def nordlicht_report() -> dict:
    return json.loads(NORDLICHT_REPORT.read_text())


def printed_nordlicht(
    page: Page, report_url: str, served: dict
) -> tuple[pdf_words.Printed, dict[str, str], float]:
    """The recorded report printed to an A4 PDF, its section headings as printed (by id), and the
    stylesheet's @page margin in points."""
    page.route("**/api/report", lambda route: route.fulfill(json=served))
    report = open_report(page, report_url)
    expect(report.get_by_role("table", name="Audit trail")).to_be_visible()
    page.emulate_media(media="print")
    headings = report.locator("h2").evaluate_all(
        """hs => hs.map((h) => [h.id, getComputedStyle(h).textTransform === 'uppercase'
          ? h.textContent.toUpperCase() : h.textContent])"""
    )
    margin = page.evaluate(
        """[...document.styleSheets].flatMap((s) => {
          try { return [...s.cssRules] } catch { return [] }  // a font sheet from another origin
        }).find((r) => r instanceof CSSPageRule).style.margin"""
    )
    assert margin.endswith("mm"), margin
    return pdf_words.read(page.pdf(format="A4")), dict(headings), float(margin[:-2]) * MM


def same_line(a: pdf_words.Box, b: pdf_words.Box, tolerance: float = 1.0) -> bool:
    return a.page == b.page and abs(a.top - b.top) <= tolerance


def heading_words(
    found: list[pdf_words.Box], heading: str
) -> tuple[pdf_words.Box, list[pdf_words.Box]]:
    """The heading's words in the PDF and the box around them: the run of its words in one size on
    one line, with no other word of that size beside them (as in a longer heading)."""
    tokens = heading.split()
    for n in range(len(found) - len(tokens) + 1):
        run = found[n : n + len(tokens)]
        size = run[0].size
        if [w.text for w in run] != tokens or not all(
            same_line(run[0], w) and abs(w.size - size) < 0.1 for w in run
        ):
            continue
        if any(same_line(w, run[0]) and abs(w.size - size) < 0.1 for w in found if w not in run):
            continue
        top, bottom = min(w.top for w in run), max(w.bottom for w in run)
        return pdf_words.Box(run[0].page, heading, run[0].left, top, run[-1].right, bottom), run
    raise AssertionError(f"{heading!r} is not in the PDF")


def at_hyphens(word: str) -> list[str]:
    return [part for part in re.split(r"(?<=-)", word) if part]


def test_a_printed_a4_report_keeps_headings_clear_of_tables_and_notes_whole(
    page: Page, report_url, browser_name, nordlicht_report
):
    if browser_name != "chromium":
        pytest.skip("only Chromium prints to PDF")
    printed, headings, margin = printed_nordlicht(page, report_url, nordlicht_report)
    found = printed.words

    # The controls table breaks across a page before the next heading, as where the bug was
    # found; without that break the check below would prove nothing.
    controls, _ = heading_words(found, headings["report-controls"])
    remediation, _ = heading_words(found, headings["report-remediation"])
    assert controls.page < remediation.page, "the controls table no longer spans a page break"

    # No section heading shares its height with a table row's text or rule.
    clashes = []
    for heading in headings.values():
        box, own = heading_words(found, heading)
        clashes += [(heading, w.text) for w in found if w not in own and w.overlaps_vertically(box)]
        clashes += [(heading, "a rule") for r in printed.rules if r.overlaps_vertically(box)]
    assert clashes == [], clashes

    # Every word of the trail's notes prints whole (a line may still end after a hyphen, as anywhere in
    # prose), and nothing runs past the page's margin.
    trail, _ = heading_words(found, headings["report-trail"])
    printed_parts = Counter(
        part
        for w in found
        if w.page > trail.page or (w.page == trail.page and w.top > trail.top)
        for part in at_hyphens(w.text)
    )
    noted = Counter(
        part
        for e in nordlicht_report["trail"]["entries"]
        for word in e["note"].split()
        for part in at_hyphens(word)
    )
    assert noted - printed_parts == Counter()
    assert [w.text for w in found if w.right > printed.page_width - margin + 0.5] == []


# --- The improvement report (#420, story 13 of spec #350) ---------------------------------------

DONE_NOTE = "Exposure record kept in the ISMS folder."
ASKED = "Who signs off the ISMS scope each year?"
DUE = (datetime.now(UTC).date() + timedelta(days=30)).isoformat()  # within the horizon


def test_story_13_the_improvement_report_shows_the_plan_and_questions_and_exports_them(
    page: Page, improvement_url
):
    request = page.request
    goal = request.put(
        f"{improvement_url}/api/goal",
        data={"goal": "internal-improvement"},
        headers={"X-Vouch-Profile": "nordlicht"},
    )
    assert goal.ok, goal.text()
    thread_id = accepted(
        request,
        improvement_url,
        "4.1.2",
        "nordlicht",
        decisions={"M1": {"decision": "approve", "owner_role": OWNER}},
    )
    done = f"{improvement_url}/api/assessments/{thread_id}/remediation/M1/done"
    post(request, done, {"note": DONE_NOTE}, "nordlicht")
    post(request, f"{improvement_url}/api/rounds/close", {}, "nordlicht")
    question = {"question": ASKED, "asked_of": "CEO", "due_date": DUE}
    post(request, f"{improvement_url}/api/controls/1.1.1/questions", question, "nordlicht")

    for width in (1280, 400):
        page.set_viewport_size({"width": width, "height": 900})
        for scheme in ("light", "dark"):
            page.emulate_media(color_scheme=scheme)
            report = open_report(page, improvement_url)
            expect(report.get_by_role("heading", level=1)).to_have_text("Improvement report")
            # M1, partly evidenced on the fake: Medium by default, Done once marked.
            plan = report.get_by_role("region", name="Improvement plan")
            row = plan.get_by_role("table", name="Medium").locator("tbody tr")
            expect(row).to_have_count(1)
            expect(row).to_contain_text("M1")
            expect(row).to_contain_text("Done")
            expect(row).to_contain_text(DONE_NOTE)
            asked = report.get_by_role("table", name="Open questions").locator("tbody tr")
            expect(asked).to_have_count(1)
            expect(asked).to_contain_text(ASKED)
            expect(asked).to_contain_text("Open")
            compared = report.get_by_role("region", name="Compared with the last round")
            expect(compared).to_contain_text("Compared with First round, closed on")
            expect(report.locator("table.report-controls")).to_have_count(0)
            scrolls = page.evaluate(
                "document.documentElement.scrollWidth > document.documentElement.clientWidth"
            )
            assert not scrolls, f"the improvement report scrolls sideways at {width}px in {scheme}"
            assert small_targets(page) == []

    with page.expect_download() as download_info:
        page.get_by_role("button", name="Export Markdown").click()
    download = download_info.value
    name = download.suggested_filename
    assert re.fullmatch(r"improvement-report-nordlicht-\d{4}-\d{2}-\d{2}\.md", name), name
    text = Path(download.path()).read_text(encoding="utf-8")
    assert text.startswith("# Improvement report\n")
    assert f"| Done | {DONE_NOTE} |" in text
    assert f"| 1.1.1 | {ASKED} | CEO | {DUE} | Open |" in text
