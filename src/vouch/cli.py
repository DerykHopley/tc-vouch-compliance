"""`assess <control-id>`: run the graph for one control and print its Assessment as JSON.

`assess --requirements <isa-control>`: list a VDA ISA control's requirements (`5.1.1`).
`assess --requirements <isa-control> --profile <name>`: only those the compliance profile
applies, then those it marks not applicable, with the reason.
"""

import json
import os
import sys

from langchain_core.language_models.chat_models import BaseChatModel

from vouch.controls import UnknownControlError, load_control
from vouch.fake_model import ScriptError
from vouch.graph import assess
from vouch.isa import NOTICE_FILE, BaseFrameworkError, IsaControl, Requirement, load_isa_control
from vouch.profile import ComplianceProfile, ProfileError, UnknownProfileError, load_named_profile
from vouch.settings import fake_model_from_env

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
EXIT_BAD_INPUT = 2
USAGE = "usage: assess <control-id> | assess --requirements <isa-control> [--profile <name>]"


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


def _requirement_lines(req: Requirement) -> list[str]:
    """One line per requirement, its sub-items indented below it."""
    first, *rest = req.text.split("\n")
    return [f"{req.id} ({req.column}): {first}", *(f"  {line}" for line in rest)]


def format_requirements(control: IsaControl, profile: ComplianceProfile | None = None) -> str:
    """The control's question, one line per requirement (sub-items indented), and the credit.

    With a profile: only the requirements it applies, then those it marks not applicable,
    each with its reason.
    """
    lines = [f"{control.id} {control.question}"]
    if profile is None:
        for req in control.requirements:
            lines.extend(_requirement_lines(req))
    else:
        lines.append(
            f"Profile: {profile.company} ({profile.id}), {profile.protection_level} protection"
        )
        requirements = profile.requirements_for(control)
        for req in requirements.applicable:
            lines.extend(_requirement_lines(req))
        if requirements.not_applicable:
            lines.append("Not applicable:")
            for excluded in requirements.not_applicable:
                lines.extend(_requirement_lines(excluded.requirement))
                lines.append(f"  Reason: {excluded.reason}")
    lines.append(f"Source: {control.source}, (c) ENX Association, CC BY-ND 4.0; see {NOTICE_FILE}")
    return "\n".join(lines)


def list_requirements(control_id: str, profile_name: str | None = None) -> int:
    try:
        control = load_isa_control(control_id)
        profile = None if profile_name is None else load_named_profile(profile_name)
    except (UnknownControlError, BaseFrameworkError, UnknownProfileError, ProfileError) as error:
        print(f"assess: {error}", file=sys.stderr)
        return EXIT_BAD_INPUT
    print(format_requirements(control, profile))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) == 2 and args[0] == "--requirements":
        return list_requirements(args[1])
    if len(args) == 4 and args[0] == "--requirements" and args[2] == "--profile":
        return list_requirements(args[1], args[3])
    if len(args) != 1 or args[0].startswith("--requirements"):
        print(USAGE, file=sys.stderr)
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
