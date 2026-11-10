"""The profile's Goal (#369, spec #350 story 1), on the Dossier's server (dossier_url): the lead
changes the goal to Internal improvement from the hero, OKs the browser's confirm, sees it after
a reload and as the newest entry of the report's audit trail, then changes it back and sees
Audit readiness and a second entry. At 1280px and 400px, light and dark: nothing scrolls
sideways and every control is at least 44px. Each run ends on Audit readiness, as it began."""

import pytest
from playwright.sync_api import Dialog, Page, expect
from touch_targets import small_targets

TO_IMPROVEMENT = "Goal changed from Audit readiness to Internal improvement"
TO_READINESS = "Goal changed from Internal improvement to Audit readiness"


def open_dossier(page: Page, url: str) -> None:
    with page.expect_response("**/api/overview"):
        page.goto(url)


def change_goal(page: Page, to: str) -> list[str]:
    """Change goal, OK on the confirm, and wait for the overview read after it: what was asked."""
    asked: list[str] = []

    def ok(dialog: Dialog) -> None:
        asked.append(dialog.message)
        dialog.accept()

    page.once("dialog", ok)
    with (
        page.expect_response("**/api/goal") as changed,
        page.expect_response("**/api/overview"),
    ):
        page.get_by_role("button", name="Change goal").click()
    assert changed.value.ok, changed.value.status
    assert asked == [
        f"Change this profile's goal to {to}? The change is recorded in the audit trail."
    ]
    return asked


def newest_trail_actions(page: Page, url: str, n: int) -> list[str]:
    """The Action cells of the report's newest `n` audit trail rows."""
    with page.expect_response("**/api/report"):
        page.goto(f"{url}/report")
    rows = page.get_by_role("table", name="Audit trail").locator("tbody tr")
    expect(rows.first).to_be_visible()
    return [rows.nth(i).locator("td").nth(4).inner_text() for i in range(n)]


def fits(page: Page, scheme: str) -> None:
    assert not page.evaluate(
        "document.documentElement.scrollWidth > document.documentElement.clientWidth"
    ), f"the Dossier scrolls sideways, {scheme}"
    assert small_targets(page) == []


@pytest.mark.parametrize("width", [1280, 400])
@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_the_lead_changes_the_goal_and_back_and_the_trail_shows_both(
    page: Page, dossier_url, width, scheme
):
    page.set_viewport_size({"width": width, "height": 900})
    page.emulate_media(color_scheme=scheme)
    open_dossier(page, dossier_url)
    goal = page.locator(".hero .goal")
    expect(goal).to_contain_text("Goal: Audit readiness")
    fits(page, scheme)

    change_goal(page, "Internal improvement")
    expect(goal).to_contain_text("Goal: Internal improvement")
    with page.expect_response("**/api/overview"):
        page.reload()
    expect(goal).to_contain_text("Goal: Internal improvement")
    fits(page, scheme)
    assert newest_trail_actions(page, dossier_url, 1) == [TO_IMPROVEMENT]

    open_dossier(page, dossier_url)
    change_goal(page, "Audit readiness")
    expect(goal).to_contain_text("Goal: Audit readiness")
    assert newest_trail_actions(page, dossier_url, 2) == [TO_READINESS, TO_IMPROVEMENT]
