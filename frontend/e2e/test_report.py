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
from current_company import make_current
from fonts import FONTS_SETTLED, without_web_fonts
from hyphen_breaks import assert_hyphenated_in_german
from playwright.sync_api import APIRequestContext, Locator, Page, Route, expect
from reviewing import reviewed
from touch_targets import small_targets
from unbreakable import LONG_QUOTES, LONG_QUOTES_LEAST, UNBREAKABLE, with_long_quotes

LOCAL_USER = "Local user"
OWNER = "Information security officer"  # the demo script's owner for M1's item
NORDLICHT_REASON = "Nordlicht allows no remote access to its company network"
GERMAN_REASON = (  # an Alpenwerk override reason, with long compounds for the trail's Note (#530)
    "Die Protokollierungsrichtlinie regelt die Informationssicherheitsverantwortlichkeiten nicht."
)
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


def test_alpenwerks_report_is_german_and_the_page_around_it_english(
    page: Page, report_url, nordlicht_later
):
    accepted(page.request, report_url, "4.1.2", "alpenwerk")
    # Alpenwerk made current (#480) opens it; Nordlicht current again after.
    nordlicht_later(report_url)
    make_current(page.request, report_url, "alpenwerk")

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


def test_at_701px_alpenwerks_question_column_breaks_words_only_with_a_hyphen(
    page: Page, report_url, nordlicht_later
):
    """#521: just above the phone layout the controls table's Question column is narrow, and
    Alpenwerk's questions broke mid-word with no hyphen ("Information" / "ssicherheit"). They are
    marked in the answer language and hyphenate in it, as the quotes do since #511."""
    accepted(page.request, report_url, "4.1.2", "alpenwerk")  # the reviewed columns narrow it
    page.set_viewport_size({"width": 701, "height": 900})
    nordlicht_later(report_url)
    make_current(page.request, report_url, "alpenwerk")  # #480: the report opens under it

    report = open_report(page, report_url)

    expect(report).to_have_attribute("lang", "de")
    expect(report.locator("table.report-controls td.report-q").first).to_be_visible()
    assert_hyphenated_in_german(page, {"question": "table.report-controls td.report-q"})


@pytest.mark.parametrize("width", [701, 1024])
def test_alpenwerks_trail_note_column_breaks_words_only_with_a_hyphen(
    page: Page, report_url, width, nordlicht_later
):
    """#530: the trail's Note column is narrow at 701 and 1024, and Alpenwerk's override reasons
    split mid-word with no hyphen ("Protokollierungsrichtlinie"). A note is typed in the answer
    language, so it is marked in it and hyphenates, as the controls' questions do since #521."""
    override = [{"requirement_id": "S1", "verdict": "not-evidenced", "reason": GERMAN_REASON}]
    accepted(page.request, report_url, "4.1.2", "alpenwerk", override)
    page.set_viewport_size({"width": width, "height": 900})
    nordlicht_later(report_url)
    make_current(page.request, report_url, "alpenwerk")  # #480: the report opens under it

    report = open_report(page, report_url)

    notes = "table.report-trail td.report-q"
    expect(report.locator(notes).filter(has_text=GERMAN_REASON).first).to_be_visible()
    assert_hyphenated_in_german(page, {"note": notes})


# --- The controls and trail tables fit (#463) ---------------------------------------------------

TABLES = ("table.report-controls", "table.report-trail")
REMEDIATION = "section[aria-labelledby='report-remediation'] table"


def with_a_full_trail(page: Page) -> None:
    """The report as served, with the recorded Nordlicht trail and a long entry added, so the trail
    has every column filled (long notes, evidence ids, model names) whatever this server holds; and
    each UNBREAKABLE text as a trail note, a remediation item's Missing, a plan item's Missing and
    note, and a question's answer (#530)."""
    recorded = json.loads(NORDLICHT_REPORT.read_text())["trail"]["entries"]

    def patched(route: Route) -> None:
        served = route.fetch().json()
        served["trail"] = {
            "entries": [*recorded, long_entry(), *unbreakable_entries()],
            "next": None,
        }
        if "questions" in served:
            served["questions"] = [*served["questions"], *unbreakable_questions()]
            plan = served["plan"] or [{"priority": "low", "items": []}]
            plan[-1]["items"] += unbreakable_plan_items()
            served["plan"] = plan
        else:
            served["remediation"] = [*served["remediation"], unbreakable_remediation()]
        route.fulfill(json=served)

    page.route("**/api/report", patched)


def tables_overflowing(report: Locator, tables: tuple[str, ...] = TABLES) -> list[str]:
    """Each of the tables wider than its container, with its widths, once the fonts have
    settled (#512: measured while a late web font was still loading, in the fallback's widths)."""
    return report.evaluate(
        "async (el, selectors) => {"
        + FONTS_SETTLED
        + """
          return selectors.flatMap((s) => [...el.querySelectorAll(s)].map((t) => {
          const box = t.parentElement
          const wide = Math.max(t.scrollWidth, box.scrollWidth)
          return wide > box.clientWidth ? `${s}: ${wide} in ${box.clientWidth}` : null
        })).filter(Boolean)
        }""",
        list(tables),
    )


def set_goal(page: Page, url: str, profile: str, goal: str) -> None:
    """The profile's goal set through the API."""
    answer = page.request.put(
        f"{url}/api/goal", data={"goal": goal}, headers={"X-Vouch-Profile": profile}
    )
    assert answer.ok, answer.text()


@pytest.mark.parametrize("fonts", ["web", "fallback"])
@pytest.mark.parametrize("goal", ["audit-readiness", "internal-improvement"])
def test_at_1440_and_1024_neither_table_scrolls_sideways(
    page: Page, request, goal, fonts, nordlicht_later
):
    """#463: the trail was 1,231-1,250px in a 902px sheet at 1440, and the controls table carried
    empty columns. Nordlicht's report is English, Alpenwerk's German. The improvement report has a
    server of its own, as its goal would change what the readiness checks read. #512: measured once
    the web fonts have settled, and again without them, as on a slow network: on Ubuntu (CI) the
    fallback is DejaVu Sans, whose German headers were 963px of trail in the 902px sheet. #550: at
    701 the German verdict ("Teilweise nachgewiesen") made the controls table 619px in 603px; there
    the controls table is checked, as #463 owns the other tables' widths."""
    url = request.getfixturevalue("report_url" if goal == "audit-readiness" else "improvement_url")
    if fonts == "fallback":
        without_web_fonts(page)
    if goal != "audit-readiness":  # each profile's default
        for profile in ("nordlicht", "alpenwerk"):
            set_goal(page, url, profile, goal)
    accepted(page.request, url, "4.1.2", "alpenwerk")  # a German reviewed row: chip, reviewer, day
    with_a_full_trail(page)
    nordlicht_later(url)
    for profile in ("nordlicht", "alpenwerk"):
        make_current(page.request, url, profile)  # #480: the report opens under the current company
        for width in (1440, 1024, 701) if goal == "audit-readiness" else (1440, 1024):
            page.set_viewport_size({"width": width, "height": 900})
            report = open_report(page, url)
            expect(report.locator("table.report-trail tbody tr").first).to_be_visible()
            for text in UNBREAKABLE.values():
                expect(report.locator("table.report-trail")).to_contain_text(text)
            if goal == "audit-readiness":
                expect(report.locator("table.report-controls")).to_be_visible()
                expect(report.locator(REMEDIATION).last).to_contain_text(UNBREAKABLE["file name"])
                shown = (*TABLES, REMEDIATION) if width > 701 else ("table.report-controls",)
            else:
                expect(report.locator("table.report-questions")).to_contain_text(
                    UNBREAKABLE["compound"]
                )
                expect(report.locator("table.report-plan").last).to_contain_text(
                    UNBREAKABLE["signed link"]
                )
                shown = (*TABLES, "table.report-questions", "table.report-plan")
            assert tables_overflowing(report, shown) == [], (profile, width)
            if goal == "audit-readiness" and profile == "alpenwerk" and width > 1000:
                # a desktop's chips never wrap (#550): the German verdict is the widest
                spaces = report.locator("table.report-controls .chip").evaluate_all(
                    "chips => chips.map((c) => getComputedStyle(c).whiteSpace)"
                )
                assert spaces and set(spaces) == {"nowrap"}, (profile, width, spaces)
            if goal == "audit-readiness" and profile == "alpenwerk":
                # #463's verify: the German review day broke mid-date ("2026-11-" / "10")
                days = report.locator("table.report-controls td.report-day").evaluate_all(
                    "cells => cells.filter(c => c.textContent.trim())"
                    ".map(c => { const r = document.createRange(); r.selectNodeContents(c);"
                    " return new Set([...r.getClientRects()].map(x => Math.round(x.top))).size; })"
                )
                assert days and all(lines == 1 for lines in days), (profile, width, days)


def long_report_quotes(served: dict) -> None:
    """An accepted control's quotes hold LONG_QUOTES, one each, copying its first quote."""
    control = next(c for c in served["controls"] if c["status"] == "accepted" and c["quotes"])
    first = control["quotes"][0]
    control["quotes"] = [{**first, "quote": quote} for quote in LONG_QUOTES]


@pytest.mark.parametrize("width", [701, 390])
def test_a_quote_holding_a_long_link_or_path_does_not_widen_the_controls_table(
    page: Page, report_url, nordlicht_later, width
):
    """#538: a quote broke only at hyphens or spaces (#511), and the quote row spans the controls
    table, so a link, a signed link or a path widened the table to its own width (653 in 603 at
    701, 645 in 324 at 390). A token too long for the line breaks anywhere, as #530's columns do:
    each such quote's least width fits the table's box. Measured on the quote in one page load, so
    it holds whatever the Verdict column does (#550)."""
    page.set_viewport_size({"width": width, "height": 900})
    nordlicht_later(report_url)
    for profile in ("nordlicht", "alpenwerk"):
        accepted(page.request, report_url, "4.1.2", profile)
        page.unroute("**/api/report")  # the last company's route gives way to this one's
        make_current(page.request, report_url, profile)  # #480: the report opens under it
        with_long_quotes(page, "**/api/report", profile, long_report_quotes)

        report = open_report(page, report_url)
        quotes = report.locator("table.report-controls blockquote")
        for quote in LONG_QUOTES:
            expect(quotes.filter(has_text=quote)).to_have_count(1)
        measured = page.evaluate(LONG_QUOTES_LEAST, {"box": ".table-wrap", "hide": False})

        assert len(measured) == len(LONG_QUOTES), measured
        assert all(q["wide"] <= q["room"] for q in measured), (profile, width, measured)


def test_at_390_each_row_of_both_tables_reads_as_a_stacked_card(
    page: Page, report_url, nordlicht_report
):
    page.set_viewport_size({"width": 390, "height": 900})
    nordlicht_report["trail"]["entries"] += [long_entry(), *unbreakable_entries()]
    page.route("**/api/report", lambda route: route.fulfill(json=nordlicht_report))
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


# An owner role a reviewer types on approving an item: the standard German title, one word (#534's
# verify: it stuck out of its card by 25-31px).
GERMAN_OWNER = "Informationssicherheitsbeauftragter"
# The remediation table's column names, in each profile's report language (#534).
REMEDIATION_LABELS = {
    "nordlicht": ("Requirement", "Missing", "Owner role", "Due date"),
    "alpenwerk": ("Anforderung", "Fehlt", "Verantwortliche Rolle", "Fällig am"),
}


def with_a_german_remediation_item(page: Page) -> None:
    """Alpenwerk's report as served (made current: this route also sends Alpenwerk's header), with one
    more remediation group: an item whose Missing is GERMAN_REASON and one whose owner role is
    GERMAN_OWNER, long compounds that must break in a phone's card: they hyphenate in German or
    the check sees it."""

    def patched(route: Route) -> None:
        headers = {**route.request.headers, "x-vouch-profile": "alpenwerk"}
        served = route.fetch(headers=headers).json()
        group = unbreakable_remediation()
        group["items"] = [
            {**group["items"][0], "missing": GERMAN_REASON},
            {**group["items"][1], "missing": GERMAN_REASON, "owner_role": GERMAN_OWNER},
        ]
        served["remediation"] = [*served["remediation"], group]
        route.fulfill(json=served)

    page.route("**/api/report", patched)


@pytest.mark.parametrize("width", [390, 360, 1024])
def test_each_remediation_item_reads_as_a_stacked_card_on_a_phone(
    page: Page, report_url, width, nordlicht_later
):
    """#534: since #530 the remediation table was 332-440px in a 294-324px box under 700px, its
    Owner role and Due date off-screen with no cue that the box scrolls. Under 700px each item is a
    card, as the controls and trail rows are (#463): each cell a line of its column's name and its
    value, inside the box. At 1024 the table is unchanged: its rows are table rows."""
    approve = {"M1": {"decision": "approve", "owner_role": "IT-Leiter"}}
    for profile in ("nordlicht", "alpenwerk"):
        accepted(page.request, report_url, "4.1.2", profile, decisions=approve)
    page.set_viewport_size({"width": width, "height": 900})
    nordlicht_later(report_url)
    for profile, labels in REMEDIATION_LABELS.items():
        page.unroute("**/api/report")
        make_current(page.request, report_url, profile)  # #480: the report opens under it
        if profile == "alpenwerk":
            with_a_german_remediation_item(page)
        report = open_report(page, report_url)
        tables = report.locator(REMEDIATION)
        expect(tables.first).to_be_visible()
        if profile == "alpenwerk":
            expect(tables.last).to_contain_text(GERMAN_REASON)
            expect(tables.last).to_contain_text(GERMAN_OWNER)
        rows = tables.locator("tbody tr")
        displays = set(rows.evaluate_all("rows => rows.map((r) => getComputedStyle(r).display)"))
        if width > 700:
            assert displays == {"table-row"}, (profile, displays)
            continue
        assert displays == {"block"}, (profile, displays)
        assert tables_overflowing(report, (REMEDIATION,)) == [], (profile, width)
        # Every cell of every item, and its text, inside its box, named by its column in the
        # report's language (the header row still names it for a screen reader).
        items = rows.evaluate_all(
            """rows => rows.map((row) => [...row.querySelectorAll('th, td')].map((c) => {
              const box = c.closest('.table-wrap').getBoundingClientRect()
              const text = document.createRange()
              text.selectNodeContents(c)
              const within = (r) => r.left >= box.left - 0.5 && r.right <= box.right + 0.5
              return { label: c.dataset.label ?? null, text: c.textContent.slice(0, 40),
                inside: within(c.getBoundingClientRect()) && within(text.getBoundingClientRect()),
                shown: getComputedStyle(c, '::before').content }
            }))"""
        )
        named = [[c["label"] for c in cells] for cells in items]
        assert named and all(n == list(labels) for n in named), (profile, named)
        outside = [c for cells in items for c in cells if not c["inside"] or c["shown"] == "none"]
        assert outside == [], (profile, width, outside)
        if profile == "alpenwerk" and width == 390:
            assert_hyphenated_in_german(page, {"free text": f"{REMEDIATION} td.report-q"})


def long_entry() -> dict:
    """A trail entry with a long note and a long model name, filling every column."""
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
        "evidence": [{"doc_id": "authentication-standard", "version": "3.1"}],
        "model": "scripted-fake-model-with-a-long-name",
        "prompt_version": "assess-2026-11-01",
    }


def unbreakable_entries() -> list[dict]:
    """A trail entry for each UNBREAKABLE text, as its typed note (#530's verify)."""
    return [
        {**long_entry(), "id": 2 + i, "action": "override", "note": text}
        for i, text in enumerate(UNBREAKABLE.values())
    ]


def unbreakable_remediation() -> dict:
    """A remediation group with an item for each UNBREAKABLE text, as its drafted Missing."""
    return {
        "control_id": "1.6.2",
        "question": "To what extent are changes managed?",
        "items": [
            {
                "requirement_id": f"S{i + 1}",
                "missing": text,
                "owner_role": "IT-Leiter",
                "due_date": "2026-12-31",
            }
            for i, text in enumerate(UNBREAKABLE.values())
        ],
    }


def unbreakable_plan_items() -> list[dict]:
    """A done plan item for each UNBREAKABLE text, as its drafted Missing and its typed note."""
    return [
        {
            "control_id": "1.6.2",
            "requirement_id": f"S{i + 1}",
            "missing": text,
            "owner_role": "IT-Leiter",
            "due_date": "2026-12-31",
            "status": "done",
            "note": text,
        }
        for i, text in enumerate(UNBREAKABLE.values())
    ]


def unbreakable_questions() -> list[dict]:
    """An answered open question for each UNBREAKABLE text, as its typed answer."""
    return [
        {
            "id": 99 + i,
            "control_id": "1.1.1",
            "question": "Wo liegt die Protokollierungsrichtlinie?",
            "asked_of": "IT-Leiter",
            "due_date": "2026-12-01",
            "asked_at": "2026-11-06T14:05:00+00:00",
            "answer": text,
            "answered_at": "2026-11-07T09:00:00+00:00",
            "overdue": False,
        }
        for i, text in enumerate(UNBREAKABLE.values())
    ]


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


def test_export_pdf_downloads_alpenwerks_report_as_one_pdf_file(
    page: Page, report_url, nordlicht_later
):
    accepted(page.request, report_url, "4.1.2", "alpenwerk")
    nordlicht_later(report_url)
    make_current(page.request, report_url, "alpenwerk")  # #480
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
        served["trail"] = {"entries": [*served["trail"]["entries"], long_entry()], "next": 1}
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
# Alpenwerk's done note, question and answer, with long compounds for the free-text columns (#530).
GERMAN_DONE_NOTE = "Risikobewertungsprotokoll im Informationssicherheitsordner abgelegt."
GERMAN_QUESTION = (
    "Wer genehmigt den Geltungsbereich des Informationssicherheitsmanagementsystems und die "
    "Zugriffsberechtigungsüberprüfungen?"
)
GERMAN_ANSWER = "Die Geschäftsführung, in der jährlichen Managementbewertung."
# The plan's free-text columns (Missing, Note) and the open questions' (Question, Answer).
IMPROVEMENT_COLUMNS = {
    "plan": "table.report-plan tbody td.report-q",
    "questions": "table.report-questions tbody td.report-q",
}


def test_story_13_the_improvement_report_shows_the_plan_and_questions_and_exports_them(
    page: Page, improvement_url
):
    request = page.request
    set_goal(page, improvement_url, "nordlicht", "internal-improvement")
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


def test_at_701px_alpenwerks_plan_and_questions_break_words_only_with_a_hyphen(
    page: Page, improvement_url, nordlicht_later
):
    """#530: at 701px the plan's Missing and Note and the open questions' Question and Answer are
    narrow, and Alpenwerk's German split mid-word with no hyphen ("Risikobewertungsprotokoll").
    They are marked in the answer language and hyphenate in it, as the controls' questions do
    since #521: typed text too, as an Alpenwerk team writes in German."""
    request = page.request
    set_goal(page, improvement_url, "alpenwerk", "internal-improvement")
    thread_id = accepted(
        request,
        improvement_url,
        "4.1.2",
        "alpenwerk",
        decisions={"M1": {"decision": "approve", "owner_role": "IT-Leiter"}},
    )
    done = f"{improvement_url}/api/assessments/{thread_id}/remediation/M1/done"
    post(request, done, {"note": GERMAN_DONE_NOTE}, "alpenwerk")
    asked = {"question": GERMAN_QUESTION, "asked_of": "Geschäftsführung", "due_date": DUE}
    logged = post(request, f"{improvement_url}/api/controls/1.1.1/questions", asked, "alpenwerk")
    question_id = json.loads(logged)["question"]["id"]
    answer = f"{improvement_url}/api/controls/1.1.1/questions/{question_id}/answer"
    post(request, answer, {"answer": GERMAN_ANSWER}, "alpenwerk")
    page.set_viewport_size({"width": 701, "height": 900})
    nordlicht_later(improvement_url)
    make_current(page.request, improvement_url, "alpenwerk")  # #480: the report opens under it

    report = open_report(page, improvement_url)

    expect(report).to_have_attribute("lang", "de")
    expect(report.locator("table.report-plan").filter(has_text=GERMAN_DONE_NOTE)).to_be_visible()
    expect(report.locator("table.report-questions")).to_contain_text(GERMAN_ANSWER)
    assert_hyphenated_in_german(page, IMPROVEMENT_COLUMNS)
