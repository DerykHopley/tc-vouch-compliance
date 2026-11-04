"""`assess <control-id>`: run the graph for one control and print its Assessment as JSON."""

import json
import os
import sys

from langchain_core.language_models.chat_models import BaseChatModel

from vouch.controls import UnknownControlError, load_control
from vouch.fake_model import ScriptError
from vouch.graph import assess
from vouch.settings import fake_model_from_env

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
EXIT_BAD_INPUT = 2


class ConfigError(RuntimeError):
    """The environment doesn't say which model to use."""


def build_model() -> BaseChatModel:
    """The fake when ASSESS_FAKE_SCRIPT is set; otherwise the real model from the environment."""
    fake = fake_model_from_env()
    if fake is not None:
        return fake
    api_key = os.environ.get("OPENROUTER_API_KEY")
    model = os.environ.get("ASSESS_MODEL")
    if not api_key or not model:
        raise ConfigError("set OPENROUTER_API_KEY and ASSESS_MODEL, or ASSESS_FAKE_SCRIPT")
    from langchain_openai import ChatOpenAI  # only the real path needs the provider

    return ChatOpenAI(model=model, api_key=api_key, base_url=OPENROUTER_BASE_URL)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: assess <control-id>", file=sys.stderr)
        return EXIT_BAD_INPUT
    try:
        control = load_control(args[0])
        model = build_model()
    except (UnknownControlError, ConfigError, ScriptError) as error:
        print(f"assess: {error}", file=sys.stderr)
        return EXIT_BAD_INPUT
    print(json.dumps(assess(control, model).to_dict(), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
