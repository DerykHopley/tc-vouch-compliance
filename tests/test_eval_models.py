"""The eval's models by name: OpenRouter with a key, a local Ollama model, and their prices."""

import socket

import pytest
from pydantic import SecretStr

from vouch.eval_models import (
    LOCAL_PRICE,
    MODELS,
    ModelUnavailable,
    chat_models,
    price_of,
)
from vouch.model_choice import DEFAULT_MODEL
from vouch.settings import Settings
from vouch.spending import PRICES


def closed_port() -> int:
    """A local port nothing listens on: bound by the system, then let go."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_a_real_model_without_a_key_is_refused_before_any_call():
    with pytest.raises(ModelUnavailable, match="OPENROUTER_API_KEY"):
        chat_models("anthropic/claude-sonnet-5.5", Settings())


def test_with_a_key_a_real_model_is_ready_and_nothing_is_sent_yet():
    make = chat_models("anthropic/claude-sonnet-5.5", Settings(openrouter_api_key=SecretStr("k")))

    assert callable(make)  # the models are made per assessment, by vouch.model_choice


@pytest.mark.allow_hosts(["127.0.0.1"])
def test_ollama_not_running_says_how_to_start_it():
    url = f"http://127.0.0.1:{closed_port()}"

    with pytest.raises(ModelUnavailable) as raised:
        chat_models("ollama/qwen3:14b", Settings(), ollama_url=url)

    message = str(raised.value)
    assert url in message
    assert "ollama serve" in message


def test_a_local_model_costs_nothing_and_a_listed_one_has_its_listed_price():
    assert price_of("ollama/qwen3:14b") == LOCAL_PRICE
    assert LOCAL_PRICE.cost(1_000_000, 1_000_000) == 0
    listed = MODELS[0]
    assert price_of(listed.id) == listed.price


def test_the_apps_default_model_is_in_the_list_at_the_apps_price():
    assert price_of(DEFAULT_MODEL) == PRICES[DEFAULT_MODEL]


def test_a_real_model_with_no_known_price_has_none_never_zero():
    assert price_of("someone/unlisted-model") is None
