"""A folder of compliance profiles for tests (#47): Nordlicht, a second test profile, and a
broken one. The second is Nordlicht's copy under another company, so the fake model's scripts
cite its documents as they do Nordlicht's. It answers in German for the API's tests; the browser
check's copy answers in English, since the server's fake model answers in English and a German
profile's answers must be German (the output check, #51).

    uv run python tests/profiles_dir.py <folder>    # the browser check's (frontend/e2e/run.sh)
"""

import shutil
import sys
from pathlib import Path

import yaml

from vouch.paths import FIXTURES_DIR
from vouch.profile import PROFILE_FILE

SECOND = "testwerk"
SECOND_COMPANY = "Testwerk Prüfstand GmbH"
BROKEN = "broken"


def make_profiles(folder: Path, *, broken: bool = True, language: str = "de") -> Path:
    """`folder` holding nordlicht, testwerk and (unless `broken` is False) a profile file that
    isn't valid YAML; `folder` itself."""
    shutil.copytree(FIXTURES_DIR / "nordlicht", folder / "nordlicht")
    shutil.copytree(FIXTURES_DIR / "nordlicht", folder / SECOND)
    second = folder / SECOND / PROFILE_FILE
    data = yaml.safe_load(second.read_text(encoding="utf-8"))
    data |= {"id": SECOND, "company": SECOND_COMPANY, "country": "Austria", "answer_language": language}
    second.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    if broken:
        (folder / BROKEN).mkdir()
        (folder / BROKEN / PROFILE_FILE).write_text("id: [unclosed\n", encoding="utf-8")
    return folder


if __name__ == "__main__":
    make_profiles(Path(sys.argv[1]), language="en")
