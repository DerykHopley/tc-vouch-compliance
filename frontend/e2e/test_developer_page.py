"""The developer page (#34): a developer sees it; a compliance lead never does.

The servers run in local mode, whose one user is a developer, with no OpenRouter key (the fake
model, nothing paid). A compliance lead is the same app with the developer API answering as it
does for one, 403 (tests/test_model_choice.py proves the API's 403 in clerk mode).

They run on PROFILES_URL: #47 moved them off the main server while the request rate was capped at
60 a minute. Fix #138 lifted that cap for the suite, so they could move back.
"""

import re

import pytest
from playwright.sync_api import Page, Route, expect

DEVELOPERS_ONLY = '{"detail": "This is available to developers only."}'


def as_compliance_lead(page: Page) -> None:
    def refuse(route: Route) -> None:
        route.fulfill(status=403, content_type="application/json", body=DEVELOPERS_ONLY)

    page.route("**/api/developer/**", refuse)


def test_a_developer_opens_the_page_and_sees_the_model_the_prompt_version_and_the_fake_model(
    page: Page, profiles_url
):
    page.goto(profiles_url)
    page.get_by_role("link", name="Developer").click()

    expect(page).to_have_url(re.compile(r"/developer$"))
    settings = page.get_by_role("region", name="Developer settings")
    expect(settings).to_contain_text("anthropic/claude-sonnet-5.5")
    expect(settings).to_contain_text("Prompt version")
    expect(settings).to_contain_text("No OpenRouter key is set: assessments run on the fake model.")
    usage = settings.get_by_role("link", name="Usage and costs on OpenRouter")
    expect(usage).to_have_attribute("href", "https://openrouter.ai/activity")


def test_a_developer_sees_the_daily_limit_and_every_assessments_usage_totalled(
    page: Page, profiles_url
):
    """#35. The servers are shared by the tests, so the totals hold whatever ran before."""
    page.request.post(f"{profiles_url}/api/assessments", data={"control_id": "4.1.2"})
    page.goto(f"{profiles_url}/developer")

    settings = page.get_by_role("region", name="Developer settings")
    expect(settings).to_contain_text("30 assessments per user per day (UTC)")
    expect(settings.get_by_role("region", name="Usage")).to_contain_text(
        re.compile(
            r"Fake model: [\d,]+ assessments? · [\d,]+ tokens in · [\d,]+ tokens out · \$[\d.]+ estimated, not spent"
        )
    )
    expect(settings).to_contain_text("kept until the server restarts")


def test_without_a_key_a_change_is_refused_with_the_apis_message(page: Page, profiles_url):
    page.goto(f"{profiles_url}/developer")
    settings = page.get_by_role("region", name="Developer settings")

    field = settings.get_by_role("textbox", name="Model id")
    field.fill("openai/gpt-5.5")
    field.press("Enter")

    expect(settings.get_by_role("alert")).to_contain_text("No OpenRouter key is set")
    expect(field).to_have_attribute("aria-invalid", "true")
    expect(settings).to_contain_text("anthropic/claude-sonnet-5.5")


def test_a_compliance_lead_has_no_developer_link_and_gets_the_dossier_at_its_address(
    page: Page, profiles_url
):
    as_compliance_lead(page)

    # The Dossier shows before the developer API answers, so wait for that answer first;
    # otherwise the checks below can pass before the page has decided (a race, #121's CI).
    with page.expect_response(lambda r: "/api/developer/" in r.url) as answer:
        page.goto(f"{profiles_url}/developer")
    assert answer.value.status == 403

    expect(page.get_by_role("heading", level=1)).to_contain_text("Prepared for audit")
    expect(page.get_by_role("navigation", name="Main").get_by_role("link")).to_have_text(
        ["Overview", "Report"]  # no Developer; the Report tab is everyone's (#56)
    )
    expect(page.get_by_role("region", name="Developer settings")).to_have_count(0)


def overlaps(a: dict, b: dict) -> bool:
    """Whether two bounding boxes share any area."""
    return (
        a["x"] < b["x"] + b["width"]
        and b["x"] < a["x"] + a["width"]
        and a["y"] < b["y"] + b["height"]
        and b["y"] < a["y"] + a["height"]
    )


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_at_phone_width_the_daily_limit_field_is_never_under_the_ask_vouch_button(
    page: Page, profiles_url, scheme
):
    """#198: the page leaves room for the floating button, so the field brought into view, by
    scrolling to it or by focusing it (a tap, Tab), stops above the button."""
    page.set_viewport_size({"width": 375, "height": 812})
    page.emulate_media(color_scheme=scheme)
    page.goto(f"{profiles_url}/developer")
    field = page.get_by_role("textbox", name="Assessments per day")
    button = page.get_by_role("button", name="Ask Vouch")
    expect(field).to_be_visible()

    for bring in (field.scroll_into_view_if_needed, field.focus):
        page.evaluate("window.scrollTo(0, 0)")
        bring()
        assert not overlaps(field.bounding_box(), button.bounding_box()), bring


@pytest.mark.parametrize(("width", "scheme"), [(375, "light"), (375, "dark"), (1280, "light")])
def test_the_daily_limit_field_focused_from_the_bottom_stops_below_the_sticky_top_bar(
    page: Page, profiles_url, width, scheme
):
    """#205: the page's scroll padding follows the sticky top bar's height (taller at phone
    width), so a field brought into view by focus stops below the bar, not under it."""
    page.set_viewport_size(
        {"width": width, "height": 500}
    )  # short, so the page scrolls at any width
    page.emulate_media(color_scheme=scheme)
    page.goto(f"{profiles_url}/developer")
    field = page.get_by_role("textbox", name="Assessments per day")
    expect(field).to_be_visible()
    bar = page.locator("header.top")

    # The field scrolled to the window's top edge: under the bar, yet "in view" without padding.
    field.evaluate("el => window.scrollTo(0, el.getBoundingClientRect().top + window.scrollY)")
    assert overlaps(field.bounding_box(), bar.bounding_box())  # the case the fix is for
    field.focus()

    assert not overlaps(field.bounding_box(), bar.bounding_box())


# --- A change keeps the page (#455) -------------------------------------------------------------
# A change's answer once lacked the broken profiles, the page threw rendering it, and the live app
# showed the Discord gate with "Vouch could not be loaded" until a reload.


def test_a_daily_limit_change_keeps_the_developer_page_showing_the_new_limit(
    page: Page, profiles_url
):
    page.goto(f"{profiles_url}/developer")
    settings = page.get_by_role("region", name="Developer settings")
    try:
        field = settings.get_by_role("textbox", name="Assessments per day")
        field.fill("29")
        with page.expect_response("**/api/developer/daily-limit") as answered:
            field.press("Enter")
        assert answered.value.status == 200

        expect(settings.get_by_role("status")).to_contain_text(
            "Each user may now start 29 assessments a day."
        )
        expect(settings).to_contain_text("29 assessments per user per day (UTC)")
        expect(page.get_by_text("Vouch could not")).to_have_count(0)
    finally:  # the servers are shared: the next test reads the default again
        reset = page.request.put(
            f"{profiles_url}/api/developer/daily-limit", data={"daily_limit": 30}
        )
        assert reset.status == 200, reset.text()


def test_a_model_change_keeps_the_developer_page_showing_the_new_model(page: Page, profiles_url):
    # No key here, so the API refuses a change: its answer is the settings read, the new model in it, as the API's own
    # test (tests/test_model_choice.py) shows a change answers.
    read = page.request.get(f"{profiles_url}/api/developer/settings").json()

    def changed(route: Route) -> None:
        route.fulfill(
            status=200,
            json=read | {"model": "openai/gpt-5.5", "runs_on": "openrouter", "fake_reason": None},
        )

    page.route("**/api/developer/model", changed)
    page.goto(f"{profiles_url}/developer")
    settings = page.get_by_role("region", name="Developer settings")

    field = settings.get_by_role("textbox", name="Model id")
    field.fill("openai/gpt-5.5")
    with page.expect_response("**/api/developer/model"):
        field.press("Enter")

    expect(settings.get_by_role("status")).to_contain_text(
        "The next assessment runs on openai/gpt-5.5."
    )
    expect(settings.locator(".sheet .mono").first).to_have_text("openai/gpt-5.5")
    expect(page.get_by_text("Vouch could not")).to_have_count(0)
