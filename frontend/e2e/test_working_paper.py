"""A control's working paper (#41): requirement by requirement for an assessed control, the
requirements and Assess for one not yet assessed, not-found for an unknown control, the verdict
kept over a reload, and the index stacked or beside the paper by width.

On its own server (paper_url): local mode has one user and 60 requests a minute, and the main
server's checks already spend most of that."""

import re

import pytest
from playwright.sync_api import APIRequestContext, Locator, Page, expect

ASSESSED = "4.1.2"  # the demo script's control: five requirements, each with a quote
UNASSESSED = "5.1.2"  # assessed by one check only; Nordlicht marks its S3 not applicable


def assessed(request: APIRequestContext, paper_url: str, control_id: str) -> None:
    """Assesses a control on the server's fake model, up to the review."""
    response = request.post(f"{paper_url}/api/assessments", data={"control_id": control_id})
    assert response.ok, response.status
    assert "event: result" in response.text()


def open_paper(page: Page, paper_url: str, control_id: str, *, assessed: bool) -> Locator:
    """The control's paper, once what it lists is read: the overview (the requirements before
    an assessment), and for an assessed control its newest stored assessment. A control the
    overview says is not assessed reads none."""
    if assessed:
        with (
            page.expect_response("**/api/overview"),
            page.expect_response(re.compile(r"/api/assessments/[^/]+$")),
        ):
            page.goto(f"{paper_url}/controls/{control_id}")
    else:
        with page.expect_response("**/api/overview"):
            page.goto(f"{paper_url}/controls/{control_id}")
    return page.get_by_role("region", name="Working paper")


def row(paper: Locator, requirement_id: str) -> Locator:
    return paper.locator("tr").filter(
        has=paper.page.locator("th[scope=row] .mono").get_by_text(requirement_id, exact=True)
    )


def box(locator: Locator) -> dict:
    """The element's bounding box, which it must have."""
    found = locator.bounding_box()
    assert found, locator
    return found


def test_an_assessed_controls_rows_show_verdict_kind_stale_flag_and_quotes(page: Page, paper_url):
    assessed(page.request, paper_url, ASSESSED)

    paper = open_paper(page, paper_url, ASSESSED, assessed=True)

    expect(paper.get_by_text("Protection level: normal")).to_be_visible()
    expect(paper.get_by_text("Answer language: English")).to_be_visible()
    rows = paper.get_by_role("table", name="Requirements").locator("tbody th[scope=row]")
    expect(rows).to_have_count(5)
    for requirement_id in ("M1", "M2", "S1", "S2", "S3"):
        shown = row(paper, requirement_id)
        expect(shown.locator(".chip")).to_have_text(re.compile(r"^(Partly e|E)videnced$"))
        expect(shown.locator(".kind").first).to_have_text(re.compile(r"^(documented|implemented)$"))
        expect(shown.get_by_text(re.compile(r"^(Current|Stale)$"))).to_be_visible()
        expect(shown.locator("blockquote").first).to_be_visible()
    expect(row(paper, "M1").locator("blockquote")).to_have_text(
        "We decide how people sign in to a system by looking at what could go wrong with it."
    )
    expect(row(paper, "M1").get_by_text("Must", exact=True)).to_be_visible()
    # M1 partly, M2 evidenced: the control is partly evidenced, and the paper says why (#212).
    expect(paper.locator(".paper-head .roll-up")).to_have_text(
        "Partly evidenced: 1 of 2 must requirements is fully evidenced."
    )


def test_an_unassessed_control_lists_its_requirements_and_assess_streams_steps(
    page: Page, paper_url
):
    paper = open_paper(page, paper_url, UNASSESSED, assessed=False)

    table = paper.get_by_role("table", name="Requirements")
    expect(row(paper, "M1")).to_be_visible()
    expect(row(paper, "S3")).to_contain_text("Not applicable: Nordlicht allows no remote access")
    expect(table.locator(".chip")).to_have_count(0)  # no verdicts before an assessment

    paper.get_by_role("button", name=f"Assess {UNASSESSED}", exact=True).click()

    steps = paper.get_by_role("list", name="Agent steps").get_by_role("listitem")
    expect(steps.first).to_contain_text(f"Loaded control {UNASSESSED}: ")
    expect(paper.get_by_role("region", name="Verdict and draft answer")).to_be_visible()
    expect(row(paper, "S3").locator(".chip")).to_have_text("Not applicable")
    expect(row(paper, "S3")).to_contain_text("Nordlicht allows no remote access")
    index = page.get_by_role("navigation", name="Controls")
    expect(
        index.get_by_role("link", name=re.compile(rf"^{re.escape(UNASSESSED)}"))
    ).to_contain_text("Proposed")


def test_an_unknown_controls_link_says_not_found_with_a_way_back(page: Page, paper_url):
    with page.expect_response("**/api/overview"):
        page.goto(f"{paper_url}/controls/9.9.9")

    not_found = page.get_by_role("region", name="Control not found")
    expect(not_found.get_by_role("heading")).to_have_text("No control 9.9.9")
    with page.expect_response("**/api/overview"):
        not_found.get_by_role("link", name="← Overview").click()
    expect(page).to_have_url(f"{paper_url}/")
    expect(page.get_by_role("article", name="Control ")).to_have_count(12)


def test_a_finished_assessment_is_still_shown_after_a_reload(page: Page, paper_url):
    paper = open_paper(page, paper_url, "1.1.1", assessed=False)
    paper.get_by_role("button", name="Assess 1.1.1", exact=True).click()
    result = paper.get_by_role("region", name="Verdict and draft answer")
    expect(result).to_be_visible()
    verdict = result.locator(".chip").inner_text()
    answer = result.locator(".answer").inner_text()
    assert answer

    with page.expect_response(re.compile(r"/api/assessments/[^/]+$")):  # read from the database
        page.reload()

    expect(result.locator(".chip")).to_have_text(verdict)
    expect(result.locator(".answer")).to_have_text(answer)
    expect(paper.get_by_text("Proposed", exact=True)).to_be_visible()
    expect(row(paper, "M1").locator(".chip")).to_be_visible()


def scrolls_sideways(page: Page) -> bool:
    return page.evaluate(
        "document.documentElement.scrollWidth > document.documentElement.clientWidth"
    )


def test_a_click_on_an_index_rows_question_opens_that_control(page: Page, paper_url):
    """#231: the control index keeps opening a control from anywhere in its row."""
    page.set_viewport_size({"width": 1400, "height": 900})
    with page.expect_response("**/api/overview"):
        page.goto(f"{paper_url}/controls/1.1.1")
    index = page.get_by_role("navigation", name="Controls")
    index.get_by_role("link").filter(has_text="5.2.4").locator(".wp-q").click()
    expect(page).to_have_url(f"{paper_url}/controls/5.2.4")
    paper = page.get_by_role("region", name="Working paper")
    expect(paper.get_by_role("heading", level=2)).to_contain_text("5.2.4")


def test_the_index_is_beside_the_paper_wide_stacks_above_it_below_1000px_and_400px_fits(
    page: Page, paper_url
):
    assessed(page.request, paper_url, ASSESSED)  # the wide table of an assessed control
    page.emulate_media(color_scheme="light")
    for width, beside in ((1400, True), (900, False), (400, False)):
        page.set_viewport_size({"width": width, "height": 900})
        paper = open_paper(page, paper_url, ASSESSED, assessed=True)
        expect(row(paper, "M1")).to_be_visible()
        index = page.get_by_role("navigation", name="Controls").bounding_box()
        shown = paper.bounding_box()
        assert index and shown
        if beside:
            assert index["x"] + index["width"] <= shown["x"], f"at {width}px the index isn't beside"
        else:
            assert index["y"] + index["height"] <= shown["y"], f"at {width}px the index isn't above"
        assert not scrolls_sideways(page), f"the working paper scrolls sideways at {width}px"

    page.emulate_media(color_scheme="dark")  # still 400px
    paper = open_paper(page, paper_url, ASSESSED, assessed=True)
    expect(row(paper, "M1")).to_be_visible()
    expect(page.locator("body")).to_have_css("background-color", "rgb(13, 13, 14)")  # dark --paper
    assert not scrolls_sideways(page), "the working paper scrolls sideways at 400px in dark"


# The assessment graph's nodes, as its steps name them.
NODE_NAME = re.compile(r"^[a-z_]+$")  # a graph node name, as src/vouch/graph.py names them


def test_after_assess_each_step_names_its_node_in_a_chip_and_the_answer_shows_at_every_width(
    page: Page, paper_url
):
    """Story 18 (#232): how Vouch reached this, a node-name chip per step, and the draft answer,
    with no sideways scroll at 400, 900 and 1400px."""
    control_id = "2.1.3"  # assessed by this check only
    page.set_viewport_size({"width": 1400, "height": 900})
    paper = open_paper(page, paper_url, control_id, assessed=False)
    paper.get_by_role("button", name=f"Assess {control_id}", exact=True).click()

    answer = paper.get_by_role("region", name="Verdict and draft answer")
    expect(answer.locator(".answer")).to_be_visible()
    steps = paper.get_by_role("list", name="Agent steps").get_by_role("listitem")
    expect(steps.first.locator(".step-node")).to_have_text("load_control")
    expect(steps.locator(".step-node")).to_have_count(steps.count())  # one chip in every step
    names = steps.locator(".step-node").all_inner_texts()
    assert names and all(NODE_NAME.match(n) for n in names), names
    for width in (400, 900, 1400):
        page.set_viewport_size({"width": width, "height": 900})
        expect(answer.locator(".answer")).to_be_visible()
        assert not scrolls_sideways(page), f"the working paper scrolls sideways at {width}px"


# Every card's parts, read in one go once the fonts have loaded and layout has settled (two frames):
# measured one call at a time, a late font or a toggle showing or hiding moved the rows between
# reads on CI (#211). Page coordinates, so scrolling can't skew them either.
CARD_PARTS = """async () => {
  await document.fonts.ready
  await new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done)))
  const rect = (el) => {
    const r = el.getBoundingClientRect()
    return { top: r.top + scrollY, bottom: r.bottom + scrollY, left: r.left, width: r.width }
  }
  const cards = {}
  for (const th of document.querySelectorAll('table[aria-label="Requirements"] tbody th[scope=row]')) {
    const tr = th.closest('tr') // a requirement's row: not its override or remediation item's (#464)
    cards[tr.querySelector('th[scope=row] .mono').textContent] = {
      card: rect(tr), head: rect(tr.querySelector('th[scope=row]')),
      verdict: rect(tr.querySelector('.verdict-cell .chip')), text: rect(tr.querySelector('.rq-text')),
      evidence: rect(tr.querySelector('.evidence')), reason: rect(tr.querySelector('.reason')),
    }
  }
  return cards
}"""


def settled_cards(page: Page) -> dict:
    return page.evaluate(CARD_PARTS)


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_on_a_phone_each_requirement_is_a_card_in_order(page: Page, paper_url, scheme):
    """#211 (the owner's option A): at 400px each row is a card: id, column and verdict on one
    line; the text; the evidence kinds and stale flag; the reason and quotes."""
    assessed(page.request, paper_url, ASSESSED)
    page.set_viewport_size({"width": 400, "height": 900})
    page.emulate_media(color_scheme=scheme)
    paper = open_paper(page, paper_url, ASSESSED, assessed=True)

    assert not scrolls_sideways(page)
    expect(paper.locator("tbody th[scope=row]")).to_have_count(5)  # not the items' rows (#464)
    for requirement_id, card in settled_cards(page).items():
        head, verdict, text = card["head"], card["verdict"], card["text"]
        evidence, reason = card["evidence"], card["reason"]
        # One line: the id and the chip share a band, the chip to the right.
        assert verdict["top"] < head["bottom"] and head["top"] < verdict["bottom"], requirement_id
        assert verdict["left"] > head["left"], requirement_id
        assert head["bottom"] <= text["top"] + 1, requirement_id
        assert text["bottom"] <= evidence["top"] + 1, requirement_id
        assert evidence["bottom"] <= reason["top"] + 1, requirement_id
        assert card["card"]["width"] <= 400, requirement_id


def test_on_a_phone_show_full_requirement_works_from_the_keyboard(page: Page, paper_url):
    assessed(page.request, paper_url, ASSESSED)
    page.set_viewport_size({"width": 400, "height": 900})
    paper = open_paper(page, paper_url, ASSESSED, assessed=True)
    card = row(paper, "M1")
    toggle = card.locator(".rq-toggle")
    expect(toggle).to_have_text("Show full requirement")
    text = card.locator(".rq-text")
    clamped = box(text)["height"]

    toggle.focus()
    page.keyboard.press("Enter")
    expect(toggle).to_have_attribute("aria-expanded", "true")
    expect(toggle).to_have_text("Show less")
    assert box(text)["height"] > clamped  # M1's text runs past 2 lines at this width
    page.keyboard.press("Enter")
    expect(toggle).to_have_attribute("aria-expanded", "false")
    assert box(text)["height"] == clamped


def test_on_a_phone_a_requirement_that_fits_in_2_lines_offers_no_toggle(page: Page, paper_url):
    # #211's verifier: a toggle that expands nothing is noise. A long text gets one, a short none.
    assessed(page.request, paper_url, ASSESSED)
    page.set_viewport_size({"width": 400, "height": 900})
    paper = open_paper(page, paper_url, ASSESSED, assessed=True)
    for requirement_id in ("M1", "M2", "S1", "S2", "S3"):
        text = row(paper, requirement_id).locator(".rq-text")
        cut = text.evaluate("el => el.scrollHeight > el.clientHeight + 1")
        expect(row(paper, requirement_id).locator(".rq-toggle")).to_have_count(1 if cut else 0)
    expect(row(paper, "M1").locator(".rq-toggle")).to_have_count(1)


def test_on_a_desktop_the_requirements_stay_a_table_with_no_toggle(page: Page, paper_url):
    assessed(page.request, paper_url, ASSESSED)
    page.set_viewport_size({"width": 1280, "height": 900})
    paper = open_paper(page, paper_url, ASSESSED, assessed=True)

    expect(paper.get_by_role("columnheader", name="Requirement", exact=True)).to_be_visible()
    expect(paper.get_by_role("button", name="Show full requirement")).to_have_count(0)


WIDE = "5.2.4"  # #464's audit: Alpenwerk's long German words ("Informationssicherheit")
REQUIREMENT_MIN = 200
"""The Requirement column's floor at 1024 and 1440 (#464), in px: it was 100-143px."""

# The judged table, read in one go once the fonts have loaded and layout has settled (as
# CARD_PARTS): its width against its wrapper's, the narrowest Requirement cell, each text's
# hyphenation and language, and where each remediation item sits against its requirement's row.
TABLE_LAYOUT = """async () => {
  await document.fonts.ready
  await new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done)))
  const table = document.querySelector('table.reqs.judged')
  const rect = (el) => el.getBoundingClientRect()
  return {
    scroll: table.scrollWidth, room: table.parentElement.clientWidth, table: rect(table).width,
    requirement: Math.min(...[...table.querySelectorAll('td.rq')].map((td) => rect(td).width)),
    texts: [...table.querySelectorAll('.rq-text')].map((t) => ({ hyphens: getComputedStyle(t).hyphens, lang: t.lang })),
    items: [...table.querySelectorAll('section.remediation')].map((item) => {
      const own = item.closest('tr')
      const id = item.getAttribute('aria-label').replace('Remediation item ', '')
      const requirement = [...table.querySelectorAll('tbody tr')]
        .find((tr) => tr.querySelector('th[scope=row] .mono')?.textContent === id)
      return { id, ownRow: own !== requirement, inQuotes: !!item.closest('.quotes-cell'),
        next: requirement.nextElementSibling === own, below: rect(own).top >= rect(requirement).bottom - 1,
        width: rect(own).width }
    }),
  }
}"""


def as_company(page: Page, profile: str) -> None:
    """Every API call the page makes goes under `profile`, by the API's own header: not by the
    'vouch.profile' key, which M9 (#448, #480) removes."""
    page.route(
        "**/api/**",
        lambda route: route.continue_(
            headers={**route.request.headers, "x-vouch-profile": profile}
        ),
    )


@pytest.mark.parametrize("width", [1440, 1024, 390])
def test_the_requirements_table_fits_hyphenates_and_puts_each_fix_on_its_own_row(
    page: Page, paper_url, width
):
    """#464: the working paper uses the full width (#448 decision 6). At 1440 and 1024 the judged
    table doesn't scroll sideways on Nordlicht or Alpenwerk, and the Requirement column keeps at
    least REQUIREMENT_MIN; the requirement text hyphenates in its own language; each remediation
    item is its own row under its requirement, spanning the table on a desktop and inside the
    card's width on a phone (#211)."""
    page.set_viewport_size({"width": width, "height": 900})
    for profile, language in (("nordlicht", "en"), ("alpenwerk", "de")):
        response = page.request.post(
            f"{paper_url}/api/assessments",
            data={"control_id": WIDE},
            headers={"X-Vouch-Profile": profile},
        )
        assert response.ok, response.status
        assert "event: result" in response.text()
        page.unroute("**/api/**")
        as_company(page, profile)
        paper = open_paper(page, paper_url, WIDE, assessed=True)
        name = "German" if language == "de" else "English"
        expect(paper.get_by_text(f"Answer language: {name}")).to_be_visible()
        layout = page.evaluate(TABLE_LAYOUT)

        assert not scrolls_sideways(page), f"{profile} at {width}px"
        assert layout["scroll"] <= layout["room"], f"{profile} at {width}px: {layout}"
        if width > 1000:
            assert layout["requirement"] >= REQUIREMENT_MIN, f"{profile} at {width}px: {layout}"
        assert layout["texts"] and all(
            t == {"hyphens": "auto", "lang": language} for t in layout["texts"]
        ), layout["texts"]
        assert layout["items"], f"{profile}'s {WIDE} has no remediation item to place"
        for item in layout["items"]:
            assert item["ownRow"] and not item["inQuotes"], item
            assert item["next"] and item["below"], item
            if width > 700:
                assert abs(item["width"] - layout["table"]) <= 1, item  # spans the table
            else:
                assert item["width"] <= width, item


def test_on_a_german_profile_a_stale_citations_flag_is_german(page: Page, report_url):
    # #359: Alpenwerk's 1.1.1 on the German demo script cites the logging policy, approved
    # 2023-05-10 for a yearly review, so stale; the API serves its reason in German. On the
    # report's server (Alpenwerk on the demo fake): the paper's server spends its whole daily
    # assessment limit on its own checks, and one more there was refused with a 429.
    response = page.request.post(
        f"{report_url}/api/assessments",
        data={"control_id": "1.1.1"},
        headers={"X-Vouch-Profile": "alpenwerk"},
    )
    assert response.ok, response.status
    assert "event: result" in response.text()
    page.add_init_script("window.localStorage.setItem('vouch.profile', 'alpenwerk')")

    paper = open_paper(page, report_url, "1.1.1", assessed=True)

    flag = row(paper, "S4").locator("figcaption .flag")  # the quote's, not the evidence column's
    expect(flag).to_have_text(
        "Prüfung überfällig: freigegeben am 2023-05-10, jährlich zu prüfen, fällig am 2024-05-10"
    )
    expect(flag).to_have_attribute("lang", "de")
    expect(paper.get_by_text(re.compile(r"^Stale: "))).to_have_count(0)
    # #484: the evidence column's labels are the served words too, stale and current.
    stale = row(paper, "S4").locator(".evidence-col .flag")
    expect(stale).to_have_text("Prüfung überfällig")
    expect(stale).to_have_attribute("lang", "de")
    current = row(paper, "M1").locator(".evidence-col .why")
    expect(current).to_have_text("Aktuell")
    expect(current).to_have_attribute("lang", "de")
