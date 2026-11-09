"""Where Vouch's own data lives in the repository."""

from pathlib import Path

REPO_DIR = Path(__file__).resolve().parents[2]

# One folder per company: its compliance profile and its evidence (fixtures/nordlicht).
FIXTURES_DIR = REPO_DIR / "fixtures"

# The sample library (#370): invented evidence documents any company made in setup can use,
# with the hidden reference profile the eval judges them under. Shipped with the app, so it
# stays here whatever profiles folder VOUCH_PROFILES_DIR names.
SAMPLE_LIBRARY_DIR = FIXTURES_DIR / "sample-library"
