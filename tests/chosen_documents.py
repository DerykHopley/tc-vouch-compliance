"""A compliance profile limited to chosen documents (#384): the sample library's reference
profile copied into a test folder, its `policies` source choosing two of the library's
policies. Shared by the profile, search, quote-check, graph, chat and profile-pass tests."""

import shutil
from pathlib import Path

import yaml

from vouch.paths import FIXTURES_DIR
from vouch.profile import PROFILE_FILE, SAMPLE_LIBRARY, ComplianceProfile, load_profile

CHOSEN = ("backup-policy", "logging-standard")
UNCHOSEN = "information-security-policy"  # a policy of the same folder, not chosen
# Words found in a chosen policy and in the unchosen one.
SHARED_WORDS = "review approved"
CHOSEN_QUOTE = "IT reviews this policy and the recovery guides every year."  # backup-policy
UNCHOSEN_QUOTE = "Any change to this policy needs their approval again."  # information-security


def chosen_profile_path(tmp_path: Path, documents: object = CHOSEN) -> Path:
    """The profile file of a copy of the sample library in `tmp_path`, its policies source
    choosing `documents` (written as given, so a test can write a bad list)."""
    folder = tmp_path / SAMPLE_LIBRARY
    shutil.copytree(FIXTURES_DIR / SAMPLE_LIBRARY, folder)
    path = folder / PROFILE_FILE
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["evidence_sources"]["policies"]["documents"] = (
        list(documents) if isinstance(documents, tuple) else documents
    )
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def chosen_profile(tmp_path: Path) -> ComplianceProfile:
    """The sample library's profile with its policies limited to CHOSEN."""
    return load_profile(chosen_profile_path(tmp_path))
