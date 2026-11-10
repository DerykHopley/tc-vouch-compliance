"""A developer removes a company made in setup (#418, story 6 of #351): it leaves every list, a
reload doesn't bring it back, and making it again gives a new id, as the removed one's audit trail
stays under the old. Removing the current company makes Nordlicht current for everyone (#480,
story 3 of #448, decision 8).

On a server of its own (removal_url): the companies made there stay in that process, so no other
check lists them, and each create runs a profile pass (#447), whose daily limit (3) the setup
server's creates share. A create makes its company current (#480): each check puts Nordlicht back.
"""

import re

from current_company import nordlicht_after
from playwright.sync_api import Browser, Page, expect
from test_company_setup import create_company, open_setup, to_step_7

COMPANY = "Musterfirma Süd GmbH"
COMPANY_ID = "musterfirma-sud-gmbh"
NORDLICHT = "Nordlicht Komponenten GmbH"
NEVER_AFTER_REMOVE = re.compile(
    "Vouch could not be loaded|Vouch could not show this page|Open to course members"
    "|Please sign in again"
)
"""#455's failure, for a removal: the app's error page or the sign-in gate in place of the page."""


def kept_the_developer_page(page: Page) -> None:
    """The removal's answer rendered: the Developer page shows, never the gate or an error (#455)."""
    expect(page.get_by_role("region", name="Developer settings")).to_be_visible()
    expect(page.get_by_text(NEVER_AFTER_REMOVE)).to_have_count(0)


def create(page: Page, url: str, company: str = COMPANY) -> str:
    """Makes `company` through company setup; the id it was given. It is made current, and the app
    lands on it, its profile pass answered."""
    open_setup(page, url)
    to_step_7(page, company)
    return create_company(page).json()["id"]


def listed(page: Page) -> list[str]:
    """The companies the Companies section lists, by name."""
    return page.get_by_role("region", name="Companies").locator(".company-name").all_text_contents()


def removed(page: Page, company: str) -> None:
    """Presses Remove on `company`'s row, accepting the confirm, and waits for the removal and the
    list read again."""
    sheet = page.get_by_role("region", name="Companies")
    page.once("dialog", lambda dialog: dialog.accept())
    with (
        page.expect_response(lambda r: r.request.method == "DELETE") as answer,
        page.expect_response(lambda r: r.url.endswith("/api/profiles")),
    ):
        sheet.get_by_role("button", name=f"Remove {company}").click()
    assert answer.value.status == 200


def test_story_3_removing_the_current_company_makes_nordlicht_current_for_everyone(
    page: Page, browser: Browser, removal_url
):
    page.set_viewport_size({"width": 1280, "height": 900})
    with nordlicht_after(page.request, removal_url):
        assert create(page, removal_url) == COMPANY_ID
        expect(page.locator(".pill").first).to_contain_text(COMPANY)

        page.get_by_role("link", name="Developer").click()  # the settings read as the app opened
        sheet = page.get_by_role("region", name="Companies")
        row = sheet.get_by_role("listitem").filter(has_text=COMPANY_ID)
        expect(row.get_by_text("Current", exact=True)).to_be_visible()
        asked: list[str] = []

        def accept(dialog) -> None:
            asked.append(dialog.message)
            dialog.accept()

        page.once("dialog", accept)
        with (
            page.expect_response(lambda r: r.request.method == "DELETE") as answer,
            page.expect_response(lambda r: r.url.endswith("/api/profiles")),
            page.expect_response("**/api/developer/settings"),  # the app opened afresh
        ):
            row.get_by_role("button", name=f"Remove {COMPANY}").click()
        assert answer.value.status == 200
        question = (
            f"Remove {COMPANY}? It leaves the profile switcher for everyone and can't be opened "
            "again. Its audit trail is kept."
        )
        assert asked == [question]

        # The app opened afresh under Nordlicht, on the developer page, which says so; focus is on
        # that note, as the button pressed is gone (#418 review).
        note = sheet.get_by_role("status")
        expect(note).to_have_text(f"Removed {COMPANY}. {NORDLICHT} is now the current company.")
        expect(note).to_be_focused()
        expect(page).to_have_url(f"{removal_url}/developer")
        kept_the_developer_page(page)
        expect(
            sheet.get_by_role("listitem").filter(has_text=NORDLICHT).get_by_text("Current")
        ).to_be_visible()
        assert COMPANY not in listed(page)
        under = page.request.get(
            f"{removal_url}/api/overview", headers={"X-Vouch-Profile": COMPANY_ID}
        )
        assert under.status == 404

        other = browser.new_context()
        try:
            second = other.new_page()
            with second.expect_response(lambda r: r.url.endswith("/api/overview")) as read:
                second.goto(removal_url)
            assert read.value.request.header_value("X-Vouch-Profile") == "nordlicht"
            expect(second.locator(".pill").first).to_contain_text(NORDLICHT)
        finally:
            other.close()

        with page.expect_response("**/api/developer/settings"):
            page.reload()
        expect(sheet.get_by_role("listitem")).to_have_count(2)  # Alpenwerk and Nordlicht
        assert COMPANY not in listed(page)

        assert create(page, removal_url) == f"{COMPANY_ID}-2"
        page.get_by_role("link", name="Developer").click()
        expect(sheet.get_by_text(f"{COMPANY_ID}-2", exact=True)).to_be_visible()
        expect(sheet.get_by_text(COMPANY_ID, exact=True)).to_have_count(0)


def test_removing_a_company_not_current_keeps_the_current_one_and_focuses_the_note(
    page: Page, removal_url
):
    company = "Abriss Werk GmbH"
    page.set_viewport_size({"width": 1280, "height": 900})
    with nordlicht_after(page.request, removal_url):  # made current by the create, then not
        made = create(page, removal_url, company)
    with page.expect_response("**/api/developer/settings"):
        page.goto(f"{removal_url}/developer")  # a page open under Nordlicht
    sheet = page.get_by_role("region", name="Companies")
    expect(sheet.get_by_role("button", name=f"Make {company} current")).to_be_visible()
    removed(page, company)

    note = sheet.get_by_role("status")
    expect(note).to_have_text(f"Removed {company}.")
    expect(note).to_be_focused()
    kept_the_developer_page(page)
    expect(sheet.get_by_text(made, exact=True)).to_have_count(0)
    expect(
        sheet.get_by_role("listitem").filter(has_text=NORDLICHT).get_by_text("Current")
    ).to_be_visible()
    with page.expect_response(lambda r: r.url.endswith("/api/overview")) as read:
        page.get_by_role("navigation", name="Main").get_by_role("link", name="Overview").click()
    assert read.value.request.header_value("X-Vouch-Profile") == "nordlicht"
