"""The developer page (#34): a developer sees it; a compliance lead never does.

The servers run in local mode, whose one user is a developer, with no OpenRouter key (the fake
model, nothing paid). A compliance lead is the same app with the developer API answering as it
does for one, 403 (tests/test_model_choice.py proves the API's 403 in clerk mode).
"""

import re

from playwright.sync_api import Page, Route, expect

DEVELOPERS_ONLY = '{"detail": "This is available to developers only."}'


def as_compliance_lead(page: Page) -> None:
    def refuse(route: Route) -> None:
        route.fulfill(status=403, content_type="application/json", body=DEVELOPERS_ONLY)

    page.route("**/api/developer/**", refuse)


def test_a_developer_opens_the_page_and_sees_the_model_the_prompt_version_and_the_fake_model(
    page: Page, vouch_url
):
    page.goto(vouch_url)
    page.get_by_role("link", name="Developer").click()

    expect(page).to_have_url(re.compile(r"/developer$"))
    settings = page.get_by_role("region", name="Developer settings")
    expect(settings).to_contain_text("anthropic/claude-haiku-5.5")
    expect(settings).to_contain_text("Prompt version")
    expect(settings).to_contain_text("No OpenRouter key is set: assessments run on the fake model.")
    usage = settings.get_by_role("link", name="Usage and costs on OpenRouter")
    expect(usage).to_have_attribute("href", "https://openrouter.ai/activity")


def test_without_a_key_a_change_is_refused_with_the_apis_message(page: Page, vouch_url):
    page.goto(f"{vouch_url}/developer")
    settings = page.get_by_role("region", name="Developer settings")

    field = settings.get_by_role("textbox", name="Model id")
    field.fill("openai/gpt-5.5")
    field.press("Enter")

    expect(settings.get_by_role("alert")).to_contain_text("No OpenRouter key is set")
    expect(field).to_have_attribute("aria-invalid", "true")
    expect(settings).to_contain_text("anthropic/claude-haiku-5.5")


def test_a_compliance_lead_has_no_developer_link_and_gets_the_dossier_at_its_address(
    page: Page, vouch_url
):
    as_compliance_lead(page)
    answered = []
    page.on("response", lambda r: "/api/developer/" in r.url and answered.append(r.status))

    page.goto(f"{vouch_url}/developer")

    expect(page.get_by_role("heading", level=1)).to_contain_text("Prepared for audit")
    expect(page.get_by_role("navigation", name="Main").get_by_role("link")).to_have_text(["Dossier"])
    expect(page.get_by_role("region", name="Developer settings")).to_have_count(0)
    assert answered == [403]
