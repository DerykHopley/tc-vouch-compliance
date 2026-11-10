"""The Company page's facts (#505): what a compliance profile holds about its company, what its
protection level brings in from the base framework, and where the company is defined. Built
from the profile and the base framework alone, so it reads no table and keeps nothing."""

from dataclasses import asdict
from typing import Any, Literal

from vouch.isa import CONTROLS_FILE, BaseFrameworkError
from vouch.profile import ComplianceProfile, ProtectionScope, protection_scope

DefinedIn = Literal["files", "setup"]


def defined_in(profile: ComplianceProfile) -> DefinedIn:
    """Where the company is defined: "setup" when made in company setup (kept in the database),
    else "files"."""
    return "setup" if profile.stored else "files"


def level_scope(profile: ComplianceProfile) -> ProtectionScope:
    """What the profile's protection level brings in; BaseFrameworkError when the framework
    can't be read, or gives the level no scope (its columns changed)."""
    for scope in protection_scope():
        if scope.level == profile.protection_level:
            return scope
    raise BaseFrameworkError(CONTROLS_FILE, f"no scope for {profile.protection_level} protection")


def company_facts(profile: ComplianceProfile) -> dict[str, Any]:
    """The profile's company facts, its country overlays with the requirements each adds, its
    protection level's scope as GET /api/developer/protection-levels sends it, and its
    not-applicable entries in its order (`requirement_id` None for a whole control).
    BaseFrameworkError when the base framework can't be read (level_scope)."""
    scope = level_scope(profile)
    return {
        "company": profile.company,
        "country": profile.country,
        "answer_language": profile.answer_language,
        "base_framework": profile.base_framework,
        "protection_level": profile.protection_level,
        "overlays": [
            {"name": overlay.name, "requirements": len(overlay.requirements)}
            for overlay in profile.overlays
        ],
        "scope": asdict(scope),
        "not_applicable": [
            {"control_id": e.control, "requirement_id": e.requirement, "reason": e.reason}
            for e in profile.not_applicable
        ],
        "defined_in": defined_in(profile),
    }
