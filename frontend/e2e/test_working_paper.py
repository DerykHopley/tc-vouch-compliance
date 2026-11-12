"""A control's working paper (#41): requirement by requirement for an assessed control, the
requirements and Assess for one not yet assessed, not-found for an unknown control, the verdict
kept over a reload, and the index stacked or beside the paper by width.

On its own server (paper_url): local mode has one user and 60 requests a minute, and the main
server's checks already spend most of that."""

import json
import re

import pdf_words
import pytest
from current_company import make_current
from dossier_cards import card as overview_card
from fonts import FONTS_SETTLED, without_web_fonts
from hyphen_breaks import assert_hyphenated_in_german
from playwright.sync_api import APIRequestContext, Locator, Page, Route, expect
from reviewing import confirmed
from touch_targets import small_targets
from unbreakable import LONG_QUOTES, LONG_QUOTES_LEAST, with_long_quotes
from verdict_chips import (
    CHIPS,
    ONE_LINE_FROM,
    SWEEP,
    WRAP_WIDTH,
    assert_dot_on_first_line,
)

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
    answer = result.locator(".answer").text_content()  # its headings and paragraphs as one text
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


@pytest.mark.parametrize("width", [1440, 1024, WRAP_WIDTH, 390])
def test_the_requirements_table_fits_hyphenates_and_puts_each_fix_on_its_own_row(
    page: Page, paper_url, width, nordlicht_later
):
    """#464: the working paper uses the full width (#448 decision 6). At 1440 and 1024 the judged
    table doesn't scroll sideways on Nordlicht or Alpenwerk, and the Requirement column keeps at
    least REQUIREMENT_MIN; the requirement text hyphenates in its own language; each remediation
    item is its own row under its requirement, spanning the table on a desktop and inside the
    card's width on a phone (#211). #550: at WRAP_WIDTH the German verdict widened the table by
    about 16px."""
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


def test_in_the_judged_table_a_verdict_chip_wraps_only_under_800(
    page: Page, paper_url, nordlicht_later
):
    """#550: under ONE_LINE_FROM a verdict chip in the judged table may wrap at its space, its dot
    on its first line, and the table still fits; from ONE_LINE_FROM every chip is one line. The
    Overview's card chip stays on one line at WRAP_WIDTH."""
    nordlicht_later(paper_url)
    for profile in ("nordlicht", "alpenwerk"):
        assessed(page.request, paper_url, WIDE, profile)
        make_current(page.request, paper_url, profile)
        page.set_viewport_size({"width": WRAP_WIDTH, "height": 900})
        open_paper(page, paper_url, WIDE, assessed=True)
        for width in (WRAP_WIDTH, *SWEEP):
            page.set_viewport_size({"width": width, "height": 900})
            chips = page.evaluate(CHIPS, "table.reqs.judged .verdict-col .chip")
            if width >= ONE_LINE_FROM:
                assert chips and all(c["lines"] == 1 for c in chips), (
                    f"{profile} at {width}: {chips}"
                )
            else:
                assert_dot_on_first_line(chips)
            if width != WRAP_WIDTH:  # the fit at WRAP_WIDTH is the table test's
                fit = page.evaluate(TABLE_LAYOUT)
                assert fit["scroll"] <= fit["room"], f"{profile} at {width}px: {fit}"

    page.set_viewport_size({"width": WRAP_WIDTH, "height": 900})
    assessed(page.request, paper_url, ASSESSED, "alpenwerk")
    with page.expect_response("**/api/overview"):
        page.goto(f"{paper_url}/")
    expect(overview_card(page, ASSESSED).locator(".chip")).to_have_text("Teilweise nachgewiesen")
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


# Where the review bar sits against the viewport, the top bar, the Ask Vouch button and the last
# requirement row, scrolled to `at` of the page (0 the top, 0.5 the middle, 1 the bottom; #467).
# `bar` is the part of the bar that floats over the page (the nearest fixed or sticky box holding
# its primary button), or the whole bar when nothing of it floats; `strip` is whether what shows
# 10px under a floating bar is the bar's own (the page's content doesn't slide under it there).
REVIEW_BAR_AT = (
    "async (at) => {"
    + FONTS_SETTLED
    + """
  window.scrollTo(0, (document.documentElement.scrollHeight - innerHeight) * at)
  await new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done)))
  const region = document.querySelector('section.review-bar')
  const button = region.querySelector(':is(.btn-primary, .btn-sign)')
  let part = button
  while (part !== region && !['fixed', 'sticky'].includes(getComputedStyle(part).position)) part = part.parentElement
  const box = (el) => el.getBoundingClientRect().toJSON()
  const bar = box(part)
  const under = bar.bottom + 10 < innerHeight ? document.elementFromPoint(bar.left + 4, bar.bottom + 10) : null
  const rows = [...document.querySelectorAll('table.reqs.judged tbody tr')]
  return {
    viewport: innerHeight, top: box(document.querySelector('header.top')).bottom, bar, button: box(button),
    fab: box(document.querySelector('.fab')), last: box(rows[rows.length - 1]),
    floating: part !== region || getComputedStyle(region).position === 'sticky',
    strip: under === null ? null : region.contains(under),
    sideways: document.documentElement.scrollWidth > document.documentElement.clientWidth,
  }
}"""
)


def meets(a: dict, b: dict) -> bool:
    """Whether two boxes overlap, with 1px of slack for sub-pixel rounding."""
    return (
        a["left"] < b["right"] - 1
        and a["right"] > b["left"] + 1
        and a["top"] < b["bottom"] - 1
        and a["bottom"] > b["top"] + 1
    )


def proposed_wide_paper(
    page: Page, paper_url: str, size: tuple[int, int], *, confirm: bool = False
) -> tuple[Locator, str]:
    """Alpenwerk's 5.2.4 (a long paper) assessed afresh, opened at `size`: at Confirm findings,
    or with `confirm` at Plan fixes (its drafts not yet decided); the paper and its thread id."""
    page.set_viewport_size({"width": size[0], "height": size[1]})
    response = page.request.post(
        f"{paper_url}/api/assessments",
        data={"control_id": WIDE},
        headers={"X-Vouch-Profile": "alpenwerk"},
    )
    assert response.ok, response.status
    data = re.search(r"event: result\r?\ndata: (.*)", response.text())
    assert data, response.text()
    thread_id = json.loads(data.group(1))["assessment"]["thread_id"]
    make_current(page.request, paper_url, "alpenwerk")
    if confirm:
        confirmed(page.request, paper_url, thread_id)
    paper = open_paper(page, paper_url, WIDE, assessed=True)
    button = "Accept verdict" if confirm else "Confirm findings"
    expect(paper.get_by_role("button", name=button)).to_be_visible()
    return paper, thread_id


BAR_SIZES = [(1440, 900), (1024, 768), (701, 900), (390, 844), (360, 740)]


@pytest.mark.parametrize("size", BAR_SIZES, ids=lambda size: str(size[0]))
def test_the_review_bars_buttons_stay_in_view_and_cover_neither_the_last_row_nor_ask_vouch(
    page: Page, paper_url, size, nordlicht_later
):
    """#467: on a proposed 5.2.4, scrolled to the top, the middle and the bottom, the review
    bar's primary button is in the viewport below the top bar; the bar never meets the Ask Vouch
    button; nothing scrolls sideways; under a floating bar the page's content doesn't show (the
    strip down to Ask Vouch is the bar's); at the bottom the last requirement row ends above it."""
    nordlicht_later(paper_url)
    proposed_wide_paper(page, paper_url, size)

    for at in (0, 0.5, 1):
        seen = page.evaluate(REVIEW_BAR_AT, at)
        where = f"{size[0]}px, scrolled to {at}: {seen}"
        assert seen["button"]["top"] >= seen["top"] - 1, where
        assert seen["button"]["bottom"] <= seen["viewport"] + 1, where
        assert not meets(seen["bar"], seen["fab"]), where
        assert not seen["sideways"], where
        if seen["floating"] and seen["strip"] is not None:
            assert seen["strip"], where
        if at == 1:
            assert seen["last"]["bottom"] <= seen["bar"]["top"] + 1, where


# The focused field, when the review bar covers any of its foot or its focus ring (the points just
# inside its bottom edge and 3px under it), or it ends off screen: where. Null when it is clear,
# is in the bar itself, or is taller than the window (a scrolling table, which can't be in view
# whole). After two frames, so a scroll the page makes on focus is done.
COVERED = """async () => {
  await new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done)))
  const el = document.activeElement
  const region = document.querySelector('section.review-bar')
  if (!el || el === document.body || region.contains(el)) return null
  const r = el.getBoundingClientRect()
  if (r.height > innerHeight / 2) return null
  const hits = []
  for (const y of [r.bottom - 1, r.bottom + 3]) {
    for (const x of [r.left + 2, (r.left + r.right) / 2, r.right - 2]) {
      if (y >= innerHeight) { hits.push(`off screen at y=${Math.round(y)}`); continue }
      const at = document.elementFromPoint(x, y)
      if (at && region.contains(at)) hits.push(`under the bar at ${Math.round(x)},${Math.round(y)}`)
    }
  }
  const name = (el.getAttribute('aria-label') || el.textContent || el.tagName).trim().slice(0, 40)
  return hits.length ? { name, rect: r.toJSON(), hits } : null
}"""


@pytest.mark.parametrize("size", BAR_SIZES, ids=lambda size: str(size[0]))
def test_a_field_tabbed_to_or_opened_with_change_is_never_under_the_review_bar(
    page: Page, paper_url, size, nordlicht_later
):
    """#467: on a proposed 5.2.4, Tab from the breadcrumb through the paper to the review bar:
    each field focused on the way ends clear of the bar, its focus ring included; each Change
    pressed with Enter opens its override with the Verdict field clear of the bar, and Tab to its
    Reason too (the bar may change as the override opens). Cancel closes it, and tabbing goes on
    from that Change."""
    nordlicht_later(paper_url)
    paper, _ = proposed_wide_paper(page, paper_url, size)
    page.evaluate("window.scrollTo(0, 0)")
    paper.get_by_role("navigation", name="Breadcrumb").get_by_role("link").first.focus()
    region = paper.get_by_role("region", name="Review")
    covered: list = []
    changes = 0
    for _ in range(200):
        page.keyboard.press("Tab")
        if region.evaluate("(bar) => bar.contains(document.activeElement)"):
            break
        if (hit := page.evaluate(COVERED)) is not None:
            covered.append(hit)
        name = page.evaluate("document.activeElement.getAttribute('aria-label') || ''")
        if not name.startswith("Change "):
            continue
        changes += 1
        editor = paper.get_by_role("form", name=f"Override {name.removeprefix('Change ')}")
        page.keyboard.press("Enter")
        expect(editor.get_by_label("Verdict")).to_be_focused()
        if (hit := page.evaluate(COVERED)) is not None:
            covered.append(hit)
        page.keyboard.press("Tab")
        expect(editor.get_by_label("Reason (required)")).to_be_focused()
        if (hit := page.evaluate(COVERED)) is not None:
            covered.append(hit)
        editor.get_by_role("button", name="Cancel").click()
        paper.get_by_role("button", name=name, exact=True).focus()
    else:
        raise AssertionError("Tab never reached the review bar")
    assert changes > 0
    assert covered == [], f"{size[0]}px: {covered}"


# The docked buttons, their place in the bar (the slot) and the bar, as boxes; whether docked.
DOCK = """() => {
  const box = (sel) => document.querySelector(sel).getBoundingClientRect().toJSON()
  const dock = document.querySelector('.review-dock')
  const style = getComputedStyle(dock)
  const content = dock.getBoundingClientRect().height - parseFloat(style.paddingTop) - parseFloat(style.paddingBottom)
    - parseFloat(style.borderTopWidth) - parseFloat(style.borderBottomWidth)
  return { docked: style.position === 'fixed', dock: box('.review-dock'), content,
    slot: dock.parentElement.getBoundingClientRect().toJSON(),
    bar: box('section.review-bar'), top: box('header.top').bottom,
    page: document.documentElement.clientWidth }
}"""


def test_a_409_in_the_docked_buttons_keeps_their_room_and_focus_clear(
    page: Page, paper_url, nordlicht_later
):
    """#467: pressing the docked Confirm findings after another tab confirmed them shows "moved
    on" with Refresh in the docked row; the bar keeps that grown row's room in its place, and a
    field tabbed to after it still ends clear of the docked buttons."""
    nordlicht_later(paper_url)
    paper, thread_id = proposed_wide_paper(page, paper_url, (390, 844))
    confirmed(page.request, paper_url, thread_id)  # another tab, first
    page.evaluate("window.scrollTo(0, 0)")
    assert page.evaluate(DOCK)["docked"]
    bar = paper.get_by_role("region", name="Review")
    with page.expect_response(re.compile(r"/findings$")) as answer:
        bar.get_by_role("button", name="Confirm findings").click()
    assert answer.value.status == 409
    expect(bar.get_by_role("button", name="Refresh")).to_be_visible()
    page.evaluate(
        "() => new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done)))"
    )
    seen = page.evaluate(DOCK)
    assert seen["docked"], seen
    assert abs(seen["slot"]["height"] - seen["content"]) <= 1, seen

    page.evaluate("window.scrollTo(0, 0)")
    paper.get_by_role("navigation", name="Breadcrumb").get_by_role("link").first.focus()
    covered = []
    for _ in range(200):
        page.keyboard.press("Tab")
        if bar.evaluate("(bar) => bar.contains(document.activeElement)"):
            break
        if (hit := page.evaluate(COVERED)) is not None:
            covered.append(hit)
    assert covered == [], covered


def test_the_docked_reject_asks_in_the_docked_row_and_is_answered_by_keyboard(
    page: Page, paper_url, nordlicht_later
):
    """#634: on a proposed 5.2.4 at 360px, Enter on the docked Reject verdict sends nothing: the
    ask opens in the docked row, focus on Cancel, the row still docked under the top bar and above
    Ask Vouch, its room kept in the bar, nothing sideways or under 44px. Esc closes it with focus
    back on Reject verdict; asked again, Shift+Tab and Enter on its Reject verdict records it."""
    nordlicht_later(paper_url)
    paper, _ = proposed_wide_paper(page, paper_url, (360, 740))
    page.evaluate("window.scrollTo(0, 0)")
    assert page.evaluate(DOCK)["docked"]
    sent: list[str] = []
    page.on("request", lambda r: sent.append(r.url) if r.url.endswith("/review") else None)
    bar = paper.get_by_role("region", name="Review")
    reject = bar.get_by_role("button", name="Reject verdict")
    reject.focus()
    page.keyboard.press("Enter")
    expect(bar.get_by_role("button", name="Cancel")).to_be_focused()
    expect(bar.get_by_role("alert")).to_contain_text("with no note?")
    page.evaluate(
        "() => new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done)))"
    )
    seen = page.evaluate(DOCK)
    assert seen["docked"], seen
    assert seen["dock"]["top"] >= seen["top"] - 1, seen  # the whole ask below the top bar
    assert seen["dock"]["bottom"] <= 740 - 88 + 1, seen  # and above Ask Vouch's clearance
    assert abs(seen["slot"]["height"] - seen["content"]) <= 1, seen
    assert not page.evaluate(
        "document.documentElement.scrollWidth > document.documentElement.clientWidth"
    )
    assert small_targets(page) == []

    page.keyboard.press("Escape")
    expect(bar.get_by_role("alert")).to_have_count(0)
    expect(reject).to_be_focused()
    assert sent == []

    page.keyboard.press("Enter")
    expect(bar.get_by_role("button", name="Cancel")).to_be_focused()
    page.keyboard.press("Shift+Tab")
    expect(bar.get_by_role("button", name="Reject verdict")).to_be_focused()
    with page.expect_response(re.compile(r"/review$")) as answer:
        page.keyboard.press("Enter")
    assert answer.value.ok, answer.value.status
    expect(bar.get_by_text("Rejected", exact=True)).to_be_visible()


def test_the_docked_buttons_stay_docked_while_a_remediation_decision_lands(
    page: Page, paper_url, nordlicht_later
):
    """#467: at Plan fixes, discarding an item from the rows reads the paper again (the bar is
    shown afresh); the docked buttons stay docked in every frame drawn, never blinking back into
    the bar's place."""
    nordlicht_later(paper_url)
    paper, _ = proposed_wide_paper(page, paper_url, (390, 844), confirm=True)
    page.evaluate("window.scrollTo(0, 0)")
    assert page.evaluate(DOCK)["docked"]
    discard = paper.get_by_role("button", name=re.compile(r"^Discard ")).first
    discard.scroll_into_view_if_needed()
    page.evaluate("""() => {
      window.__docked = []
      const tick = () => {
        const dock = document.querySelector('.review-dock')
        window.__docked.push(!!dock && getComputedStyle(dock).position === 'fixed')
        if (window.__docked.length < 90) requestAnimationFrame(tick)
      }
      requestAnimationFrame(tick)
    }""")
    with page.expect_response(re.compile(r"/remediation/[^/]+$")) as answer:
        discard.click()
    assert answer.value.ok, answer.value.status
    page.wait_for_function("window.__docked.length >= 90")
    frames = page.evaluate("window.__docked")
    assert all(frames), frames


def test_printed_the_docked_buttons_fit_the_bar(page: Page, paper_url, nordlicht_later):
    """#467: printed while docked, the buttons are back in the bar's place, inside its padding, and
    the bar keeps to the page: the width they were docked at doesn't carry over to paper."""
    nordlicht_later(paper_url)
    proposed_wide_paper(page, paper_url, (1440, 900))
    page.evaluate("window.scrollTo(0, 0)")
    assert page.evaluate(DOCK)["docked"]
    page.emulate_media(media="print")
    seen = page.evaluate(DOCK)
    assert not seen["docked"], seen
    assert seen["bar"]["right"] <= seen["page"] + 1, seen  # the bar isn't widened past the page
    assert seen["dock"]["left"] >= seen["bar"]["left"] + 12 - 1, seen  # inside the bar's padding
    assert seen["dock"]["right"] <= seen["bar"]["right"] - 12 + 1, seen


def overlapping_words(printed: pdf_words.Printed) -> list[tuple[str, str, int]]:
    """Each pair of printed words whose boxes cross on one page, by text and page."""
    words = printed.words
    return [
        (a.text, b.text, a.page)
        for i, a in enumerate(words)
        for b in words[i + 1 :]
        if a.overlaps(b)
    ]


@pytest.mark.parametrize("size", [(1440, 900), (390, 844)], ids=lambda size: str(size[0]))
def test_a_printed_paper_at_plan_fixes_has_no_buttons_and_no_overlap(
    page: Page, paper_url, browser_name, nordlicht_later, size
):
    """#591: printed to A4 at Plan fixes, from a wide or a phone window, no printed word overlaps
    another (the review bar printed over the last remediation item, its Approve pill over "Your
    note"), and the remediation items' Approve and Discard are left off the paper. The bar's own
    buttons print in its place (#467)."""
    nordlicht_later(paper_url)
    paper, _ = proposed_wide_paper(page, paper_url, size, confirm=True)
    expect(paper.get_by_role("button", name=re.compile(r"^Approve ")).first).to_be_visible()
    page.emulate_media(media="print")
    if browser_name == "chromium":  # only Chromium prints to PDF
        printed = pdf_words.read(page.pdf(format="A4"))
        assert "decided." in [w.text for w in printed.words]  # the bar printed
        assert overlapping_words(printed) == []
    for name in (r"^Approve ", r"^Discard "):
        expect(paper.get_by_role("button", name=re.compile(name))).to_have_count(0)


def test_a_field_taller_than_the_room_keeps_its_top_below_the_top_bar(
    page: Page, paper_url, nordlicht_later
):
    """#467: at 360x740, a Reason field the lead has drawn taller than the room between the top
    bar and the docked buttons, tabbed to, keeps its top below the top bar: clearing the docked
    buttons never scrolls a field's top under the top bar."""
    nordlicht_later(paper_url)
    paper, _ = proposed_wide_paper(page, paper_url, (360, 740))
    page.evaluate("window.scrollTo(0, 0)")
    change = paper.get_by_role("button", name=re.compile(r"^Change ")).last
    change.focus()
    page.keyboard.press("Enter")
    reason = paper.get_by_label("Reason (required)")
    seen = page.evaluate(DOCK)
    room = seen["dock"]["top"] - seen["top"]
    reason.evaluate("(el, height) => { el.style.height = `${height}px` }", room + 30)
    page.keyboard.press("Tab")
    expect(reason).to_be_focused()
    page.evaluate(
        "() => new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done)))"
    )
    top_bar = page.evaluate("document.querySelector('header.top').getBoundingClientRect().bottom")
    assert box(reason)["y"] >= top_bar - 1, (box(reason), top_bar)


# The stacked control index's scroll hint (#555): at 1000px or less the index sits above the paper
# in a box of its own that scrolls ("Policies and incidents" is its first area). Its foot fades
# while more rows lie below, the next row cut by the edge, and the fade goes at the list's end.
INDEX_FADES = """(() => {
  const after = getComputedStyle(document.querySelector('.wp-index'), '::after')
  return after.display !== 'none' && after.backgroundImage.includes('gradient')
})()"""


def as_never_assessed(page: Page) -> None:
    """The overview with every card not assessed, as a fresh profile's index shows it: the rows'
    heights then don't hang on the review tags other checks on this server leave (a tag makes a
    row taller, and then an area heading, not a row, can meet the box's foot)."""

    def fresh(route: Route) -> None:
        served = route.fetch().json()
        for card in served["controls"]:
            card |= {"status": "not-assessed", "verdict": None}
        route.fulfill(json=served)

    page.route("**/api/overview", fresh)


# The stacked index's edge and the first row it cuts (not wholly shown), read in one go once the
# fonts have settled. Read one call at a time, a late IBM Plex Mono moved the index up 19px between
# the edge and the rows (the top bar shrinks when it swaps in), so the row the fade covers was
# missed and the next one measured: `383 < 354 - 8` (#656).
CUT_ROW = (
    "async (nav) => {"
    + FONTS_SETTLED
    + """  const edge = nav.getBoundingClientRect().top + nav.clientTop + nav.clientHeight
  const row = [...nav.querySelectorAll('.wp-row')]
    .find((r) => r.getBoundingClientRect().bottom > edge + 0.5)
  const { x, y, width, height } = row.getBoundingClientRect()
  return { edge, control: row.querySelector('.mono').textContent, x, y, width, height }
}"""
)


def test_on_a_phone_the_stacked_indexs_foot_fades_until_scrolled_to_its_end(page: Page, paper_url):
    as_never_assessed(page)
    page.set_viewport_size({"width": 390, "height": 844})
    open_paper(page, paper_url, "1.1.1", assessed=False)
    nav = page.get_by_role("navigation", name="Controls")
    expect(nav.get_by_role("link")).to_have_count(12)
    assert nav.evaluate("n => n.scrollHeight > n.clientHeight"), "the index doesn't scroll"

    page.wait_for_function(INDEX_FADES, timeout=5000)
    # The next row is cut by the edge: the first row not wholly shown starts inside the box.
    cut = nav.evaluate(CUT_ROW)
    assert cut["y"] < cut["edge"] - 8, cut  # at least 8px of it shows

    nav.evaluate("n => { n.scrollTop = n.scrollHeight }")
    page.wait_for_function(f"!{INDEX_FADES}", timeout=5000)


def test_on_a_phone_the_stacked_index_cuts_a_row_at_its_foot_when_an_area_ends_there(
    page: Page, paper_url
):
    """#617: opening a control below the box's foot brought its row into view with its foot on
    the box's edge; when the row ends its area (4.2.1, the last of its three), the next area's
    heading met the edge and no row was half-shown under the fade. The index now scrolls on until
    a row is cut there, the open row still wholly shown. The rows carry review tags."""

    def tagged(route: Route) -> None:
        served = route.fetch().json()
        for i, card in enumerate(served["controls"]):
            card |= {
                "status": ["waiting-for-review", "accepted"][i % 2],
                "verdict": "partly-evidenced",
            }
        route.fulfill(json=served)

    page.route("**/api/overview", tagged)
    page.set_viewport_size({"width": 390, "height": 844})
    open_paper(page, paper_url, "4.2.1", assessed=False)
    nav = page.get_by_role("navigation", name="Controls")
    expect(nav.get_by_role("link")).to_have_count(12)
    expect(nav.locator(".tag-accepted").first).to_be_visible()
    expect(nav.locator(".tag-waiting-for-review").first).to_be_visible()
    page.wait_for_function(INDEX_FADES, timeout=5000)

    cut = nav.evaluate(CUT_ROW)
    assert cut["y"] < cut["edge"] - 8, cut  # a row, not a heading, meets the edge
    open_row = box(nav.locator('[aria-current="page"]'))
    top = nav.evaluate("n => n.getBoundingClientRect().top + n.clientTop")
    assert top - 0.5 <= open_row["y"], (open_row, top)
    assert open_row["y"] + open_row["height"] <= cut["edge"] + 0.5, (open_row, cut)


@pytest.mark.parametrize("how", ["tap", "click"])
def test_on_a_phone_the_half_shown_row_under_the_fade_opens_on_the_first_press(
    browser, paper_url, how
):
    """#555, the owner's answer B: a tap or click on the row the fade covers opens it on the
    first press, as on main. Nothing scrolls the row away between press and release (a row
    reached with Tab may sit partly under the fade)."""
    context = browser.new_context(has_touch=how == "tap", viewport={"width": 390, "height": 844})
    page = context.new_page()
    as_never_assessed(page)
    try:
        open_paper(page, paper_url, "1.1.1", assessed=False)
        nav = page.get_by_role("navigation", name="Controls")
        expect(nav.get_by_role("link")).to_have_count(12)
        page.wait_for_function(INDEX_FADES, timeout=5000)
        cut = nav.evaluate(CUT_ROW)
        assert cut["y"] < cut["edge"] - 8, cut  # a part of it shows to press
        x, y = cut["x"] + cut["width"] / 2, (cut["y"] + cut["edge"]) / 2  # its visible part
        if how == "tap":
            page.touchscreen.tap(x, y)
        else:
            page.mouse.click(x, y)
        expect(page).to_have_url(f"{paper_url}/controls/{cut['control']}")
    finally:
        context.close()


def test_a_stacked_index_that_fits_shows_no_fade_nor_does_the_index_beside_the_paper(
    page: Page, paper_url
):
    """#555: a short list (the overview's first two controls) fits its box and shows no fade;
    at 1440 the index beside the paper is unchanged, with none."""

    def two_controls(route: Route) -> None:
        served = route.fetch().json()
        served["controls"] = served["controls"][:2]
        route.fulfill(json=served)

    page.route("**/api/overview", two_controls)
    page.set_viewport_size({"width": 390, "height": 844})
    open_paper(page, paper_url, "1.1.1", assessed=False)
    nav = page.get_by_role("navigation", name="Controls")
    expect(nav.get_by_role("link")).to_have_count(2)
    assert nav.evaluate("n => n.scrollHeight <= n.clientHeight"), "two rows overflow the index"
    assert not page.evaluate(INDEX_FADES)

    page.unroute("**/api/overview")
    page.set_viewport_size({"width": 1440, "height": 900})
    open_paper(page, paper_url, "1.1.1", assessed=False)
    expect(nav.get_by_role("link")).to_have_count(12)
    assert not page.evaluate(INDEX_FADES)
