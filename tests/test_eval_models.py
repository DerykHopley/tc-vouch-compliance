"""The eval's models by name: OpenRouter with a key, a local Ollama model, and their prices."""

import re

import httpx
import pytest
from pydantic import SecretStr

from vouch.eval_models import (
    FALLBACK_JUDGE,
    JUDGE,
    LOCAL_PRICE,
    MODELS,
    ModelUnavailable,
    OllamaNotRunning,
    chat_models,
    check_ollama,
    default_judge,
    price_of,
)
from vouch.model_choice import DEFAULT_MODEL, OpenRouter
from vouch.settings import Settings
from vouch.spending import FAKE, PRICES

KEYED = Settings(openrouter_api_key=SecretStr("k"))


def ollama(*pulled: str) -> httpx.Client:
    """A running Ollama, as its API answers, with these models pulled."""

    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.12.0"})
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": n, "model": n} for n in pulled]})
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(answer))


def test_a_real_model_without_a_key_is_refused_before_any_call():
    with pytest.raises(ModelUnavailable, match="OPENROUTER_API_KEY.*--model fake"):
        chat_models("anthropic/claude-sonnet-5.5", Settings())


def test_a_judge_without_a_key_says_to_leave_out_the_judge():
    with pytest.raises(ModelUnavailable) as raised:
        chat_models("anthropic/claude-sonnet-5.5", Settings(), role="judge")

    message = str(raised.value)
    assert "OPENROUTER_API_KEY" in message
    assert "--judge" in message
    assert "--model fake" not in message


def test_the_fake_model_cannot_judge_and_nothing_reaches_openrouter():
    with pytest.raises(ModelUnavailable, match="leave out --judge"):
        chat_models(FAKE, KEYED, role="judge")


def test_with_a_key_a_real_model_is_ready_and_nothing_is_sent_yet():
    make = chat_models("anthropic/claude-sonnet-5.5", KEYED)

    assert callable(make)  # the models are made per assessment, by vouch.model_choice


@pytest.mark.allow_hosts(["127.0.0.1"])
def test_ollama_not_running_says_how_to_start_it(closed_port):
    url = f"http://127.0.0.1:{closed_port}"

    with pytest.raises(OllamaNotRunning) as raised:
        chat_models("ollama/qwen3:14b", Settings(), ollama_url=url)

    message = str(raised.value)
    assert url in message
    assert "ollama serve" in message


def test_a_model_ollama_has_not_pulled_says_how_to_pull_it():
    with pytest.raises(ModelUnavailable, match="ollama pull qwen3:14b"):
        check_ollama("http://localhost:11434", "qwen3:14b", client=ollama("llama3.2:latest"))


@pytest.mark.parametrize("name", ["qwen3:14b", "qwen3"])
def test_a_pulled_model_passes_with_or_without_its_tag(name):
    check_ollama("http://localhost:11434", name, client=ollama("qwen3:14b", "qwen3:latest"))


@pytest.mark.parametrize("url", ["localhost:11434", "ftp://localhost:11434", "http://", "::"])
def test_a_malformed_ollama_url_is_a_clear_error_not_a_traceback(url):
    with pytest.raises(ModelUnavailable, match="--ollama-url"):
        chat_models("ollama/qwen3:14b", Settings(), ollama_url=url)


def test_a_local_model_gets_the_longer_timeout_given(monkeypatch):
    monkeypatch.setattr("vouch.eval_models.check_ollama", lambda url, model: None)

    asked = {}
    monkeypatch.setattr(
        OpenRouter, "chat_model", lambda self, model, callbacks, timeout: asked.update(t=timeout)
    )

    chat_models("ollama/qwen3:14b", Settings(), ollama_timeout=900)([])

    assert asked == {"t": 900}


def test_a_local_model_costs_nothing_and_a_listed_one_has_its_listed_price():
    assert price_of("ollama/qwen3:14b") == LOCAL_PRICE
    assert LOCAL_PRICE.cost(1_000_000, 1_000_000) == 0
    listed = MODELS[0]
    assert price_of(listed.id) == listed.price


def test_the_apps_default_model_is_in_the_list_at_the_apps_price():
    assert price_of(DEFAULT_MODEL) == PRICES[DEFAULT_MODEL]


def test_a_real_model_with_no_known_price_has_none_never_zero():
    assert price_of("someone/unlisted-model") is None


DATED_SOURCE = re.compile(r"https://\S+, \d{4}-\d{2}-\d{2}")


def test_the_list_is_what_ran():
    """#54's runs (#53's comment, 2026-11-05): Mistral Small 4 was blocked by the owner's
    OpenRouter guardrail and dropped; TypeSafe's Jev Router ran instead; then OpenAI's GPT-6
    Luna and GPT-6.1 Sol (#55's comment). GPT-6.1 Terra also ran, and is listed once, as the
    judge."""
    ids = [m.id for m in MODELS]

    assert ids == [
        DEFAULT_MODEL,
        "anthropic/claude-sonnet-5.5",
        "typesafe/jev-router",
        "openai/gpt-6-luna",
        "openai/gpt-6.1-sol",
        "ollama/gpt-oss:20b",
    ]


def test_each_model_and_the_judge_has_a_price_with_its_source_and_date_and_a_reason():
    for m in (*MODELS, JUDGE):
        assert m.price is not None or m.price_kind == "variable", m.id
        assert DATED_SOURCE.search(m.source), m.id
        assert m.why, m.id


def test_the_router_has_no_fixed_price_so_only_openrouters_reported_cost_counts():
    [router] = [m for m in MODELS if m.price_kind == "variable"]

    assert router.id == "typesafe/jev-router"
    assert router.price is None  # an unreported cost stays unknown, never $0
    assert router.id not in PRICES
    assert "openai" in router.why.lower()  # it may route to the judge's provider


def test_each_model_has_one_kind_of_price():
    kinds = {m.id: m.price_kind for m in (*MODELS, JUDGE)}

    assert kinds == {
        DEFAULT_MODEL: "listed",
        "anthropic/claude-sonnet-5.5": "listed",
        "typesafe/jev-router": "variable",
        "openai/gpt-6-luna": "listed",
        "openai/gpt-6.1-sol": "listed",
        "ollama/gpt-oss:20b": "local",
        JUDGE.id: "listed",
    }


def provider(model_id: str) -> str:
    return model_id.split("/", 1)[0]


def test_the_judge_is_named_and_priced_and_not_in_the_model_list():
    assert JUDGE.id not in {m.id for m in MODELS}
    assert JUDGE.price is not None
    assert price_of(JUDGE.id) == JUDGE.price


def test_no_listed_model_is_judged_by_default_by_its_own_provider():
    for m in MODELS:
        judge = default_judge(m.id)
        assert provider(judge) != provider(m.id), m.id
        assert price_of(judge) is not None, judge


def test_a_model_from_the_judges_provider_is_judged_by_the_fallback_judge():
    """GPT-6.1 Terra, the judge, ran as a model too (#55): it is never its own judge."""
    assert default_judge("openai/gpt-6.1-terra") == FALLBACK_JUDGE == "anthropic/claude-sonnet-5.5"
    assert default_judge("openai/gpt-6-luna") == FALLBACK_JUDGE
    assert default_judge(DEFAULT_MODEL) == JUDGE.id
    assert default_judge("ollama/gpt-oss:20b") == JUDGE.id


def test_no_model_with_a_variable_price_is_given_a_listed_one():
    """The usage meter prices a call from PRICES when OpenRouter reports no cost, so a router in
    PRICES would be priced as one fixed model: its unreported cost must stay unknown."""
    variable = [m.id for m in (*MODELS, JUDGE) if m.price_kind == "variable"]

    assert variable  # the Jev Router
    assert not set(variable) & PRICES.keys()


def test_the_app_knows_each_hosted_models_price_so_its_cost_is_never_unknown():
    for m in (*MODELS, JUDGE):
        if m.price_kind == "listed":
            assert PRICES[m.id] == m.price, m.id


def test_the_local_model_is_free():
    [local] = [m for m in MODELS if m.price_kind == "local"]

    assert local.price == LOCAL_PRICE
