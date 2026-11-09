"""A developer removes a company made in setup (#418, story 6 of #351): it leaves the profile
switcher, a reload doesn't bring it back, and making it again gives a new id, as the removed
one's audit trail stays under the old.

On the setup server (setup_url), as company setup's create is: the companies made there stay in
that process, so no other check's switcher lists them. Its name is its own, so the create check
there is unaffected.
"""

import re

from playwright.sync_api import Page, expect
from test_company_setup import PROFILES, open_setup, press, to_step_7

COMPANY = "Musterfirma Süd GmbH"
COMPANY_ID = "musterfirma-sud-gmbh"
FELL_BACK = "Your last profile is no longer available: showing Nordlicht Komponenten GmbH."


def create(page: Page, setup_url: str, company: str = COMPANY) -> str:
    """Makes `company` through company setup; the id it was given. The app lands on it."""
    open_setup(page, setup_url)
    to_step_7(page, company)
    with (
        page.expect_response(lambda r: r.url.endswith(PROFILES)) as created,
        page.expect_response(lambda r: r.url.endswith("/api/overview")),
    ):
        press(page, "Create company")
    assert created.value.status == 201
    return created.value.json()["id"]


def switcher_companies(page: Page) -> list[str]:
    banner = page.get_by_role("banner")
    banner.get_by_role("button", name=re.compile(r"^Profile: ")).click()
    names = banner.get_by_role("menuitemradio").all_text_contents()
    page.keyboard.press("Escape")
    return names


def test_a_developer_removes_the_company_shown_and_it_never_comes_back(page: Page, setup_url):
    page.set_viewport_size({"width": 1280, "height": 900})
    assert create(page, setup_url) == COMPANY_ID
    banner = page.get_by_role("banner")
    expect(banner.get_by_role("button", name=re.compile(rf"^Profile: {COMPANY}"))).to_be_visible()

    page.get_by_role("link", name="Developer").click()
    sheet = page.get_by_role("region", name="Company setup")
    remove = sheet.get_by_role("button", name=f"Remove {COMPANY}")
    expect(sheet.get_by_text(COMPANY_ID, exact=True)).to_be_visible()
    asked: list[str] = []

    def accept(dialog) -> None:
        asked.append(dialog.message)
        dialog.accept()

    page.once("dialog", accept)
    with (
        page.expect_response(lambda r: r.request.method == "DELETE") as removed,
        page.expect_response(lambda r: r.url.endswith("/api/profiles")),
    ):
        remove.click()
    assert removed.value.status == 200
    question = (
        f"Remove {COMPANY}? It leaves the profile switcher for everyone and can't be opened "
        "again. Its audit trail is kept."
    )
    assert asked == [question]

    # The app opened afresh under Nordlicht; the developer page still says what was removed, and
    # focus is on that note, as the button pressed is gone (#418 review).
    note = sheet.get_by_role("status")
    expect(note).to_have_text(f"Removed {COMPANY}.")
    expect(note).to_be_focused()
    expect(banner.get_by_role("status")).to_have_text(FELL_BACK)
    expect(
        banner.get_by_role("button", name=re.compile(r"^Profile: Nordlicht Komponenten GmbH"))
    ).to_be_visible()
    expect(page).to_have_url(f"{setup_url}/developer")
    expect(page.get_by_role("region", name="Developer settings")).to_be_visible()
    assert not any(name.startswith(COMPANY) for name in switcher_companies(page))
    expect(sheet.get_by_role("button", name=f"Remove {COMPANY}")).to_have_count(0)
    under = page.request.get(f"{setup_url}/api/overview", headers={"X-Vouch-Profile": COMPANY_ID})
    assert under.status == 404

    with page.expect_response(lambda r: r.url.endswith("/api/profiles")):
        page.reload()
    expect(
        banner.get_by_role("button", name=re.compile(r"^Profile: Nordlicht Komponenten GmbH"))
    ).to_be_visible()
    assert not any(name.startswith(COMPANY) for name in switcher_companies(page))

    assert create(page, setup_url) == f"{COMPANY_ID}-2"
    page.get_by_role("link", name="Developer").click()
    expect(sheet.get_by_text(f"{COMPANY_ID}-2", exact=True)).to_be_visible()
    expect(sheet.get_by_text(COMPANY_ID, exact=True)).to_have_count(0)


def test_removing_a_company_not_shown_keeps_the_profile_and_focuses_the_note(page: Page, setup_url):
    company = "Abriss Werk GmbH"
    page.set_viewport_size({"width": 1280, "height": 900})
    made = create(page, setup_url, company)
    banner = page.get_by_role("banner")
    banner.get_by_role("button", name=re.compile(rf"^Profile: {company}")).click()
    with page.expect_response(lambda r: r.url.endswith("/api/overview")):
        banner.get_by_role("menuitemradio", name=re.compile(r"^Nordlicht Komponenten GmbH")).click()

    page.get_by_role("link", name="Developer").click()
    sheet = page.get_by_role("region", name="Company setup")
    page.once("dialog", lambda dialog: dialog.accept())
    with (
        page.expect_response(lambda r: r.request.method == "DELETE") as removed,
        page.expect_response(lambda r: r.url.endswith("/api/profiles")),
    ):
        sheet.get_by_role("button", name=f"Remove {company}").click()
    assert removed.value.status == 200

    note = sheet.get_by_role("status")
    expect(note).to_have_text(f"Removed {company}.")
    expect(note).to_be_focused()
    expect(sheet.get_by_text(made, exact=True)).to_have_count(0)
    expect(banner.get_by_role("status")).to_have_count(0)
    expect(
        banner.get_by_role("button", name=re.compile(r"^Profile: Nordlicht Komponenten GmbH"))
    ).to_be_visible()
    assert not any(name.startswith(company) for name in switcher_companies(page))
