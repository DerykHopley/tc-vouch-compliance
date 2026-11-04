"""Where Vouch's own data lives in the repository."""

from pathlib import Path

# One folder per company: its compliance profile and its evidence (fixtures/nordlicht).
FIXTURES_DIR = Path(__file__).resolve().parents[2] / "fixtures"
