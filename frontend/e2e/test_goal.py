"""The profile's Goal (#369, spec #350 story 1; #441, spec #448 story 5), on the Dossier's server
(dossier_url): the lead changes the goal to Internal improvement from the hero through the in-app
ask (#441: no browser dialog opens), sees it after a reload and as the newest entry of the
report's audit trail, then changes it back and sees Audit readiness and a second entry. The ask
takes focus on Cancel, and Escape or Cancel close it with focus back on Change goal, sending
nothing; confirming keeps focus on Change goal, busy, and after. At 1280px and 400px, light and dark, with the ask open too: nothing scrolls sideways and
every control is at least 44px. Each run ends on Audit readiness, as it began."""

import pytest
from browser_dialogs import dialogs_of, no_dialog
from playwright.sync_api import Page, expect
from touch_targets import small_targets

TO_IMPROVEMENT = "Goal changed from Audit readiness to Internal improvement"
TO_READINESS = "Goal changed from Internal improvement to Audit readiness"


def open_dossier(page: Page, url: str) -> None:
    with page.expect_response("**/api/overview"):
        page.goto(url)


def ask(page: Page, dialogs: list[str], to: str) -> None:
    """Change goal on the hero: no browser dialog; the in-app ask opens with today's question,
    focus on Cancel."""
    page.locator(".hero .goal").get_by_role("button", name="Change goal").click()
    no_dialog(dialogs)
    expect(page.locator(".hero .inline-confirm [role=alert]")).to_have_text(
        f"Change this profile's goal to {to}? The change is recorded in the audit trail."
    )
    expect(page.get_by_role("button", name="Cancel")).to_be_focused()


def change_goal(page: Page, dialogs: list[str], to: str) -> None:
    """Change goal, then Change goal in the ask, waiting for the overview read after it; focus
    stays on Change goal."""
    ask(page, dialogs, to)
    with (
        page.expect_response("**/api/goal") as changed,
        page.expect_response("**/api/overview"),
    ):
        page.locator(".hero .inline-confirm").get_by_role("button", name="Change goal").click()
    assert changed.value.ok, changed.value.status
    expect(page.locator(".hero .goal").get_by_role("button", name="Change goal")).to_be_focused()


def newest_trail_actions(page: Page, url: str, n: int) -> list[str]:
    """The Action cells of the report's newest `n` audit trail rows: found by their column's name,
    as a column empty in every row is left out (#463)."""
    with page.expect_response("**/api/report"):
        page.goto(f"{url}/report")
    rows = page.get_by_role("table", name="Audit trail").locator("tbody tr")
    expect(rows.first).to_be_visible()
    return [rows.nth(i).locator("td[data-label=Action]").inner_text() for i in range(n)]


def fits(page: Page, where: str) -> None:
    assert not page.evaluate(
        "document.documentElement.scrollWidth > document.documentElement.clientWidth"
    ), f"the Dossier scrolls sideways, {where}"
    assert small_targets(page) == [], where


@pytest.mark.parametrize("width", [1280, 400])
@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_the_lead_changes_the_goal_and_back_and_the_trail_shows_both(
    page: Page, dossier_url, width, scheme
):
    dialogs = dialogs_of(page)
    page.set_viewport_size({"width": width, "height": 900})
    page.emulate_media(color_scheme=scheme)
    open_dossier(page, dossier_url)
    goal = page.locator(".hero .goal")
    expect(goal).to_contain_text("Goal: Audit readiness")
    fits(page, scheme)

    # Escape and Cancel close the ask, send nothing, and put focus back on Change goal.
    sent: list[str] = []
    page.on("request", lambda r: sent.append(r.url) if "/api/goal" in r.url else None)
    ask(page, dialogs, "Internal improvement")
    fits(page, f"{scheme}, the ask open")
    page.keyboard.press("Escape")
    expect(page.locator(".inline-confirm")).to_have_count(0)
    expect(goal.get_by_role("button", name="Change goal")).to_be_focused()
    ask(page, dialogs, "Internal improvement")
    page.get_by_role("button", name="Cancel").click()
    expect(goal.get_by_role("button", name="Change goal")).to_be_focused()
    assert sent == []

    change_goal(page, dialogs, "Internal improvement")
    expect(goal).to_contain_text("Goal: Internal improvement")
    with page.expect_response("**/api/overview"):
        page.reload()
    expect(goal).to_contain_text("Goal: Internal improvement")
    fits(page, scheme)
    assert newest_trail_actions(page, dossier_url, 1) == [TO_IMPROVEMENT]

    open_dossier(page, dossier_url)
    change_goal(page, dialogs, "Audit readiness")
    expect(goal).to_contain_text("Goal: Audit readiness")
    assert newest_trail_actions(page, dossier_url, 2) == [TO_READINESS, TO_IMPROVEMENT]
    no_dialog(dialogs)
