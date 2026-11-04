"""`assess <control-id> [--profile <name>]`: run the graph for one VDA ISA control (`4.1.2`)
under a compliance profile (nordlicht unless named) and print its Assessment as JSON.

`assess --requirements <isa-control>`: list a VDA ISA control's requirements (`5.1.1`).
`assess --requirements <isa-control> --profile <name>`: only those the compliance profile
applies, then those it marks not applicable, with the reason. Flags go in any order.
"""

import argparse
import json
import os
import sys

from langchain_core.language_models.chat_models import BaseChatModel

from vouch.fake_model import ScriptError
from vouch.graph import OutputCheckError, assess
from vouch.isa import (
    NOTICE_FILE,
    BaseFrameworkError,
    IsaControl,
    Requirement,
    UnknownControlError,
    load_isa_control,
)
from vouch.profile import ComplianceProfile, ProfileError, UnknownProfileError, load_named_profile
from vouch.settings import fake_model_from_env

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
EXIT_FAILED = 1
EXIT_BAD_INPUT = 2
DEFAULT_PROFILE = "nordlicht"
USAGE = (
    "assess <control-id> [--profile <name>] | "
    "assess --requirements <isa-control> [--profile <name>]"
)


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
    """One line per requirement, its sub-items indented below it; an overlay's names its source."""
    first, *rest = req.text.split("\n")
    label = f"overlay, {req.source}" if req.column == "overlay" else req.column
    return [f"{req.id} ({label}): {first}", *(f"  {line}" for line in rest)]


def format_requirements(control: IsaControl, profile: ComplianceProfile | None = None) -> str:
    """The control's question, one line per requirement (sub-items indented), and the credit.

    With a profile: only the requirements it applies, then those it marks not applicable,
    each with its reason.
    """
    lines = [f"{control.id} {control.question}"]
    overlay_requirements: list[Requirement] = []
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
        everything = requirements.applicable + tuple(
            n.requirement for n in requirements.not_applicable
        )
        overlay_requirements = [r for r in everything if r.column == "overlay"]
    lines.append(
        f"Source of the ISA text: {control.source}, (c) ENX Association, CC BY-ND 4.0; "
        f"see {NOTICE_FILE}"
    )
    lines.extend(f"{r.id} is {r.source}'s, not ISA text." for r in overlay_requirements)
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


class _OneLineParser(argparse.ArgumentParser):
    """argparse, but a misuse prints one stderr line (the usage and what's wrong) and exits 2."""

    def error(self, message: str):
        self.exit(EXIT_BAD_INPUT, f"usage: {USAGE} ({message})\n")


def _parser() -> argparse.ArgumentParser:
    parser = _OneLineParser(
        prog="assess",
        usage=USAGE,
        allow_abbrev=False,
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("control_id", nargs="?", metavar="<control-id>")
    parser.add_argument("--requirements", metavar="<isa-control>")
    parser.add_argument("--profile", metavar="<name>")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    try:
        args = parser.parse_args(sys.argv[1:] if argv is None else argv)
        if (args.control_id is None) == (args.requirements is None):
            parser.error("give a control id or --requirements, each with an optional --profile")
    except SystemExit as stop:  # --help (0) or a misuse (2), already printed
        return stop.code if isinstance(stop.code, int) else EXIT_BAD_INPUT
    if args.requirements is not None:
        return list_requirements(args.requirements, args.profile)
    try:
        load_isa_control(args.control_id)
        profile = load_named_profile(args.profile or DEFAULT_PROFILE)
        model = build_model()
    except (
        UnknownControlError,
        BaseFrameworkError,
        UnknownProfileError,
        ProfileError,
        ConfigError,
        ScriptError,
    ) as error:
        print(f"assess: {error}", file=sys.stderr)
        return EXIT_BAD_INPUT
    try:
        assessment = assess(args.control_id, profile, model)
    except OutputCheckError as error:
        print(f"assess: {error}", file=sys.stderr)
        return EXIT_FAILED
    print(json.dumps(assessment.to_dict(), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
