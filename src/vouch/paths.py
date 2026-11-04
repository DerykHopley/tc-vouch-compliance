"""Where Vouch's own data lives in the repository."""

from pathlib import Path

REPO_DIR = Path(__file__).resolve().parents[2]

# One folder per company: its compliance profile and its evidence (fixtures/nordlicht).
FIXTURES_DIR = REPO_DIR / "fixtures"
