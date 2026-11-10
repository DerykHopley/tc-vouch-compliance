"""The Company page's facts (#505): what a compliance profile holds about its company, what its
protection level brings in from the base framework, and where the company is defined. Built
from the profile and the base framework alone, so it reads no table and keeps nothing."""

from dataclasses import asdict
from typing import Any, Literal

from vouch.profile import ComplianceProfile, protection_scope

DefinedIn = Literal["files", "setup"]


def defined_in(profile: ComplianceProfile) -> DefinedIn:
    """Where the company is defined: "setup" when made in company setup (kept in the database),
    else "files"."""
    return "setup" if profile.stored else "files"


def company_facts(profile: ComplianceProfile) -> dict[str, Any]:
    """The profile's company facts, its country overlays with the requirements each adds, its
    protection level's scope as GET /api/developer/protection-levels sends it, and its
    not-applicable entries in its order (`requirement_id` None for a whole control).
    BaseFrameworkError when the base framework can't be read."""
    [scope] = [s for s in protection_scope() if s.level == profile.protection_level]
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
