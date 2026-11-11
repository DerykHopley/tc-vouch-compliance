"""A control's working paper (#41): requirement by requirement for an assessed control, the
requirements and Assess for one not yet assessed, not-found for an unknown control, the verdict
kept over a reload, and the index stacked or beside the paper by width.

On its own server (paper_url): local mode has one user and 60 requests a minute, and the main
server's checks already spend most of that."""

import re

import pytest
from current_company import make_current
from dossier_cards import card as overview_card
from fonts import FONTS_SETTLED, without_web_fonts
from hyphen_breaks import assert_hyphenated_in_german
from playwright.sync_api import APIRequestContext, Locator, Page, expect
from unbreakable import LONG_QUOTES, LONG_QUOTES_LEAST, with_long_quotes

ASSESSED = "4.1.2"  # the demo script's control: five requirements, each with a quote
UNASSESSED = "5.1.2"  # assessed by one check only; Nordlicht marks its S3 not applicable


def assessed(
    request: APIRequestContext, paper_url: str, control_id: str, profile: str | None = None
) -> None:
    """Assesses a control on the server's fake model, up to the review: under the profile when
    one is named, else under the current company."""
    headers = {"X-Vouch-Profile": profile} if profile else {}
    response = request.post(
        f"{paper_url}/api/assessments", data={"control_id": control_id}, headers=headers
    )
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


# Each requirement's id and its row, as a JS array: not its override's or remediation item's
# row (#464).
REQUIREMENT_ROWS = """[...document.querySelectorAll('table[aria-label="Requirements"] tbody th[scope=row]')]
    .map((th) => [th.querySelector('.mono').textContent, th.closest('tr')])"""

# Every card's parts, read in one go once the fonts have loaded and layout has settled (two frames):
# measured one call at a time, a late font or a toggle showing or hiding moved the rows between
# reads on CI (#211). Page coordinates, so scrolling can't skew them either.
CARD_PARTS = (
    "async () => {"
    + FONTS_SETTLED
    + """  const rect = (el) => {
    const r = el.getBoundingClientRect()
    return { top: r.top + scrollY, bottom: r.bottom + scrollY, left: r.left, width: r.width }
  }
  const cards = {}
  for (const [id, tr] of """
    + REQUIREMENT_ROWS
    + """) {
    cards[id] = {
      card: rect(tr), head: rect(tr.querySelector('th[scope=row]')),
      verdict: rect(tr.querySelector('.verdict-cell .chip')), text: rect(tr.querySelector('.rq-text')),
      evidence: rect(tr.querySelector('.evidence')), reason: rect(tr.querySelector('.reason')),
    }
  }
  return cards
}"""
)


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


# Whether the clamp hides some of each requirement's text, read once the fonts have settled (as
# CARD_PARTS): the measure the toggle must agree with.
CUT_TEXTS = (
    "async () => {"
    + FONTS_SETTLED
    + """  return Object.fromEntries("""
    + REQUIREMENT_ROWS
    + """.map(([id, tr]) => {
      const text = tr.querySelector('.rq-text')
      return [id, text.scrollHeight > text.clientHeight + 1]
    }))
}"""
)


@pytest.mark.parametrize("fonts", ["web", "fallback"])
def test_on_a_phone_a_requirement_that_fits_in_2_lines_offers_no_toggle(
    page: Page, paper_url, fonts
):
    """#211's verifier: a toggle that expands nothing is noise. A long text gets one, a short
    none. #516: the web font arrived after the first measure and took S2 to 3 lines in a box the
    clamp kept 2 lines high, so no toggle, and its third line hidden. Again without the web fonts,
    as on a slow network (#512's case). Each cut text, expanded, shows all its lines."""
    if fonts == "fallback":
        without_web_fonts(page)
    assessed(page.request, paper_url, ASSESSED)
    page.set_viewport_size({"width": 400, "height": 900})
    paper = open_paper(page, paper_url, ASSESSED, assessed=True)
    cut = page.evaluate(CUT_TEXTS)

    assert sorted(cut) == ["M1", "M2", "S1", "S2", "S3"], cut
    assert cut["M1"], cut
    for requirement_id, hidden in cut.items():
        toggle = row(paper, requirement_id).locator(".rq-toggle")
        expect(toggle).to_have_count(1 if hidden else 0)
        if hidden:
            toggle.click()
            text = row(paper, requirement_id).locator(".rq-text")
            assert text.evaluate("el => el.scrollHeight <= el.clientHeight + 1"), requirement_id


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
TABLE_LAYOUT = (
    "async () => {"
    + FONTS_SETTLED
    + """  const table = document.querySelector('table.reqs.judged')
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
)


# Each verdict chip a selector finds, once the fonts have settled (as CARD_PARTS): its white-space,
# its text's line boxes, and its dot. A pseudo-element has no box to read, so a probe takes the
# dot's place for the measure: a span with the `::before`'s size, margins and alignment, the
# `::before` itself switched off meanwhile.
CHIPS = (
    "async (selector) => {"
    + FONTS_SETTLED
    + """  const off = document.createElement('style')
  off.textContent = '.dot-probing::before { display: none !important }'
  document.head.append(off)
  const chips = [...document.querySelectorAll(selector)].map((chip) => {
    const dot = getComputedStyle(chip, '::before')
    const probe = document.createElement('span')
    for (const p of ['display', 'width', 'height', 'margin-top', 'margin-right', 'margin-bottom',
      'margin-left', 'align-self', 'flex-shrink', 'vertical-align']) probe.style.setProperty(p, dot.getPropertyValue(p))
    chip.classList.add('dot-probing')
    chip.prepend(probe)
    const range = document.createRange()
    range.selectNodeContents(chip.lastChild)
    const rects = [...range.getClientRects()].map((r) => ({ top: r.top, bottom: r.bottom }))
    const box = probe.getBoundingClientRect()
    probe.remove()
    chip.classList.remove('dot-probing')
    return { text: chip.textContent, whiteSpace: getComputedStyle(chip).whiteSpace,
      lines: new Set(rects.map((r) => Math.round(r.top))).size, first: rects[0],
      dot: { top: box.top, bottom: box.bottom } }
  })
  off.remove()
  return chips
}"""
)
WRAP_WIDTH = 701
"""The width at which the German verdict widened the judged table by about 16px (#550)."""
ONE_LINE_FROM = 800
"""From this width a verdict chip never wraps (#550's verify): with every chip on one line the
tables fit from 718px in the web fonts, and from 770px with DejaVu Sans (CI's fallback)."""
SWEEP = (720, ONE_LINE_FROM, 900, 1000)
"""The widths between 701 and 1001 the fit checks also measure, resized in place."""


def assert_dot_on_first_line(chips: list[dict]) -> None:
    """Each chip's dot sits within its text's first line box, wrapped or not (#550)."""
    assert chips, "no verdict chip to measure"
    for chip in chips:
        first, dot = chip["first"], chip["dot"]
        assert dot["top"] >= first["top"] - 1 and dot["bottom"] <= first["bottom"] + 1, chip


@pytest.mark.parametrize("width", [1440, 1024, WRAP_WIDTH, 390])
def test_the_requirements_table_fits_hyphenates_and_puts_each_fix_on_its_own_row(
    page: Page, paper_url, width, nordlicht_later
):
    """#464: the working paper uses the full width (#448 decision 6). At 1440 and 1024 the judged
    table doesn't scroll sideways on Nordlicht or Alpenwerk, and the Requirement column keeps at
    least REQUIREMENT_MIN; the requirement text hyphenates in its own language; each remediation
    item is its own row under its requirement, spanning the table on a desktop and inside the
    card's width on a phone (#211). #550: at 701 the German verdict ("Teilweise nachgewiesen")
    widened the table by about 16px; there a chip may wrap at its space, its dot on its first line,
    while the Overview's card chip stays on one line."""
    page.set_viewport_size({"width": width, "height": 900})
    nordlicht_later(paper_url)
    for profile, language in (("nordlicht", "en"), ("alpenwerk", "de")):
        response = page.request.post(
            f"{paper_url}/api/assessments",
            data={"control_id": WIDE},
            headers={"X-Vouch-Profile": profile},
        )
        assert response.ok, response.status
        assert "event: result" in response.text()
        make_current(page.request, paper_url, profile)  # #480: the paper opens under it
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
        chips = page.evaluate(CHIPS, "table.reqs.judged .verdict-col .chip")
        if width > 1000:  # a desktop's chips stay on one line (#550)
            assert chips and all(c["lines"] == 1 for c in chips), f"{profile} at {width}px: {chips}"
        if width == WRAP_WIDTH and profile == "alpenwerk":
            assert_dot_on_first_line(chips)
        if width == WRAP_WIDTH:  # #550's verify: a chip wraps only where the table needs it
            for swept in SWEEP:
                page.set_viewport_size({"width": swept, "height": 900})
                fit = page.evaluate(TABLE_LAYOUT)
                assert fit["scroll"] <= fit["room"], f"{profile} at {swept}px: {fit}"
                chips = page.evaluate(CHIPS, "table.reqs.judged .verdict-col .chip")
                if swept >= ONE_LINE_FROM:
                    assert all(c["lines"] == 1 for c in chips), f"{profile} at {swept}px: {chips}"
                else:
                    assert_dot_on_first_line(chips)
            page.set_viewport_size({"width": width, "height": 900})

    if width == WRAP_WIDTH:  # outside the table a chip stays on one line
        assessed(page.request, paper_url, ASSESSED, "alpenwerk")
        with page.expect_response("**/api/overview"):
            page.goto(f"{paper_url}/")
        expect(overview_card(page, ASSESSED).locator(".chip")).to_have_text(
            "Teilweise nachgewiesen"
        )
        (chip,) = page.evaluate(CHIPS, f"article[aria-label='Control {ASSESSED}'] .chip")
        assert chip["whiteSpace"] == "nowrap" and chip["lines"] == 1, chip


INDEX_ROWS = (
    """async () => {"""
    + FONTS_SETTLED
    + """
  const rect = (el) => el && el.getBoundingClientRect()
  return [...document.querySelectorAll('.wp-index .wp-row')].map((row) => {
    const q = row.querySelector('.wp-q')
    const state = row.querySelector('.wp-state')
    return {
      id: row.querySelector('.mono').textContent, row: rect(row).height, idBox: rect(row.querySelector('.mono')),
      state: rect(state), gap: parseFloat(getComputedStyle(state).columnGap),
      chip: rect(row.querySelector('.chip')), tag: rect(row.querySelector('.tag')), q: rect(q),
      line: parseFloat(getComputedStyle(q).lineHeight),
    }
  })
}"""
)

ROW_MAX = 72
"""An index row's height at 1440 (#466), in px, when its id, chip and any tag share one line: that
line, two lines of question and the row's padding."""
ROW_WRAPPED_MAX = 96
"""The same when the review tag doesn't fit beside the id and chip in the 250px index (beside
"Partly evidenced", or any German verdict word) and wraps to a line of its own below them: the
owner's answer A on #466 (2026-11-10)."""


LINE_SHARED = 3
"""How far apart, in px, the vertical centres of two things on one line may be (#466)."""


def centre(rect: dict) -> float:
    """A DOMRect's vertical centre, as INDEX_ROWS reads them."""
    return rect["top"] + rect["height"] / 2


@pytest.mark.parametrize("profile", ["nordlicht", "alpenwerk"])
def test_at_1440_each_index_row_is_its_id_and_chip_then_at_most_two_lines_of_question(
    page: Page, paper_url, profile, nordlicht_later
):
    """#466 (answer A): the id and chip share a line; the review tag sits on it when it fits and
    wraps below it otherwise; then the question, clamped to two lines."""
    response = page.request.post(
        f"{paper_url}/api/assessments",
        data={"control_id": ASSESSED},  # a chip and a review tag: the row's widest state
        headers={"X-Vouch-Profile": profile},
    )
    assert response.ok, response.status
    page.set_viewport_size({"width": 1440, "height": 900})
    nordlicht_later(paper_url)
    make_current(page.request, paper_url, profile)  # #480: the paper opens under it
    open_paper(page, paper_url, ASSESSED, assessed=True)

    rows = page.evaluate(INDEX_ROWS)

    assert len(rows) == 12, rows
    assert any(r["q"]["height"] > 1.5 * r["line"] for r in rows), "no question uses a second line"
    assert any(r["tag"] for r in rows), "no row has a review tag to place"
    for r in rows:
        id_line = r["idBox"]
        # The id and chip share a line: their vertical centres within LINE_SHARED, also when the
        # tag wraps below them (centred across both lines, the id sat half a line low).
        assert abs(centre(id_line) - centre(r["chip"])) <= LINE_SHARED, r
        assert r["q"]["height"] <= 2 * r["line"] + 1, r  # two lines of question at most
        assert r["q"]["top"] >= r["state"]["bottom"] - 1, r  # the question under the state
        if r["tag"] is None:
            assert r["row"] <= ROW_MAX, r
            continue
        fits = r["chip"]["width"] + r["gap"] + r["tag"]["width"] <= r["state"]["width"] + 0.5
        if fits:  # on the id and chip's line
            assert r["tag"]["top"] < id_line["bottom"], r
            assert r["row"] <= ROW_MAX, r
        else:  # on a line of its own, below them
            assert r["tag"]["top"] >= r["chip"]["bottom"] - 1, r
            assert r["row"] <= ROW_WRAPPED_MAX, r


def test_at_1440_the_index_stays_in_view_when_the_paper_scrolls_to_its_foot(page: Page, paper_url):
    """#466: the index's list is sticky beside the paper, below the top bar, and scrolls on its
    own; it ends above the room main keeps for Ask Vouch, so the paper's foot doesn't push it up."""
    assessed(page.request, paper_url, ASSESSED)  # a long paper
    page.set_viewport_size({"width": 1440, "height": 900})
    open_paper(page, paper_url, ASSESSED, assessed=True)
    index = page.get_by_role("navigation", name="Controls").locator(".wp-index-list")
    top_bar = box(page.locator(".top"))

    page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
    page.wait_for_function("scrollY > 0")

    shown = box(index)
    assert abs(shown["y"] - (top_bar["y"] + top_bar["height"])) <= 1, (shown, top_bar)
    assert shown["y"] + shown["height"] <= 900 + 1, shown
    last = index.get_by_role("link").filter(has_text="5.2.9")  # the index scrolls on its own
    last.scroll_into_view_if_needed()
    assert box(last)["y"] + box(last)["height"] <= 900 + 1, box(last)
    moved = abs(box(index)["y"] - shown["y"])  # the top bar's height is fractional (62.67px)
    assert moved <= 1, "bringing the last row into view moved the index"


FOCUSED_IN_LIST = (
    """async () => {"""
    + FONTS_SETTLED
    + """
  const row = document.activeElement
  const list = document.querySelector('.wp-index-list')
  if (!row || !list.contains(row)) return null
  const r = row.getBoundingClientRect(), l = list.getBoundingClientRect()
  return { id: row.querySelector('.mono').textContent, top: r.top, bottom: r.bottom,
    listTop: l.top, listBottom: l.bottom }
}"""
)


def test_at_1440_a_focused_index_row_is_scrolled_into_the_lists_view(page: Page, paper_url):
    """#466: the index's list scrolls on its own, so a row Tab reaches lies inside it, its focus
    ring whole. Firefox left 5.1.2 sticking out of the list's foot (#466's verifier)."""
    page.set_viewport_size({"width": 1440, "height": 900})
    open_paper(page, paper_url, "1.1.1", assessed=False)
    links = page.get_by_role("navigation", name="Controls").get_by_role("link")
    links.first.focus()
    seen = []
    for _ in range(links.count()):
        focused = page.evaluate(FOCUSED_IN_LIST)
        assert focused, f"focus left the index after {seen}"
        seen.append(focused["id"])
        assert focused["top"] >= focused["listTop"] - 1, focused
        assert focused["bottom"] <= focused["listBottom"] + 1, focused
        page.keyboard.press("Tab")
    assert seen[-1] == "5.2.9", seen


def test_at_701px_a_german_quotes_long_words_break_only_with_a_hyphen(
    page: Page, paper_url, nordlicht_later
):
    """#511: just above the phone layout the quotes column is narrow (#464), and Alpenwerk's
    quotes ("Administratorkonten") broke mid-word with no hyphen. They hyphenate in the answer
    language (German, as Alpenwerk's documents are), or don't break."""
    response = page.request.post(
        f"{paper_url}/api/assessments",
        data={"control_id": WIDE},
        headers={"X-Vouch-Profile": "alpenwerk"},
    )
    assert response.ok, response.status
    assert "event: result" in response.text()
    page.set_viewport_size({"width": 701, "height": 900})
    nordlicht_later(paper_url)
    make_current(page.request, paper_url, "alpenwerk")  # #480: the paper opens under it
    paper = open_paper(page, paper_url, WIDE, assessed=True)
    expect(paper.get_by_text("Answer language: German")).to_be_visible()
    quotes = "table.reqs.judged .quotes-cell blockquote"
    expect(paper.locator(quotes).first).to_be_visible()

    # Chromium's English rules also find breaks in these words, so the quote must say it's German.
    assert_hyphenated_in_german(page, {"quotes": quotes})


STORED = re.compile(r"/api/assessments/[^/]+$")


def long_stored_quotes(served: dict) -> None:
    """The stored assessment's first quotes hold LONG_QUOTES, one each."""
    citations = [c for r in served["assessment"]["requirements"] for c in r["citations"]]
    assert len(citations) >= len(LONG_QUOTES), citations
    for citation, quote in zip(citations, LONG_QUOTES, strict=False):
        citation["quote"] = quote


@pytest.mark.parametrize("width", [701, 1024, 390])
def test_a_quote_holding_a_long_link_or_path_does_not_widen_the_quotes_column(
    page: Page, paper_url, nordlicht_later, width
):
    """#538: a quote broke only at hyphens or spaces (#511), and the quotes column sizes to its
    content's least width, so a link, a signed link or a path widened the judged table (1144 in 669
    at 701). A token too long for the line breaks anywhere, as #530's report columns do: each such
    quote's least width fits the quotes column as it is without them. Measured on the quote in one
    page load, so it holds whatever the Verdict column does (#550)."""
    page.set_viewport_size({"width": width, "height": 900})
    nordlicht_later(paper_url)
    for profile in ("nordlicht", "alpenwerk"):
        response = page.request.post(
            f"{paper_url}/api/assessments",
            data={"control_id": ASSESSED},
            headers={"X-Vouch-Profile": profile},
        )
        assert response.ok, response.status
        assert "event: result" in response.text()
        page.unroute(STORED)  # the last company's route gives way to this one's
        make_current(page.request, paper_url, profile)  # #480: the paper opens under it
        with_long_quotes(page, STORED, profile, long_stored_quotes)

        paper = open_paper(page, paper_url, ASSESSED, assessed=True)
        quotes = paper.locator("table.reqs.judged blockquote")
        for quote in LONG_QUOTES:
            expect(quotes.filter(has_text=quote)).to_have_count(1)
        measured = page.evaluate(LONG_QUOTES_LEAST, {"box": ".quotes-cell", "hide": True})

        assert len(measured) == len(LONG_QUOTES), measured
        assert all(q["wide"] <= q["room"] for q in measured), (profile, width, measured)


def test_on_a_german_profile_a_stale_citations_flag_is_german(
    page: Page, report_url, nordlicht_later
):
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
    nordlicht_later(report_url)
    make_current(
        page.request, report_url, "alpenwerk"
    )  # #480: Alpenwerk opens as the current company

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
