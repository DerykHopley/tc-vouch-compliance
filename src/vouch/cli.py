"""`assess <control-id>`: run the graph for one control and print its Assessment as JSON."""

import json
import sys

from langchain_core.language_models.chat_models import BaseChatModel

from vouch.controls import UnknownControlError, load_control
from vouch.exit_codes import EXIT_BAD_INPUT
from vouch.fake_model import ScriptError
from vouch.graph import assess
from vouch.model_choice import MemoryModelStore, ModelChoice
from vouch.settings import Settings, SettingsError, load_settings


def build_model(settings: Settings) -> BaseChatModel:
    """The API's model rule (vouch.model_choice), on ASSESS_MODEL or the default model: the fake
    when ASSESS_FAKE_SCRIPT is set, else OpenRouter. With neither set the CLI stops, rather
    than run the API's demo script. Raises SettingsError."""
    choice = ModelChoice(settings, MemoryModelStore())
    if choice.fake_reason == "no_key":
        raise SettingsError("set OPENROUTER_API_KEY, or ASSESS_FAKE_SCRIPT")
    return choice.for_assessment(choice.chosen())


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: assess <control-id>", file=sys.stderr)
        return EXIT_BAD_INPUT
    try:
        control = load_control(args[0])
        model = build_model(load_settings())
    except (UnknownControlError, ScriptError, SettingsError) as error:
        print(f"assess: {error}", file=sys.stderr)
        return EXIT_BAD_INPUT
    print(json.dumps(assess(control, model).to_dict(), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
