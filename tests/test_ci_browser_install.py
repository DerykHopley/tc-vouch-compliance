"""CI's browser jobs install Playwright's browser quickly and boundedly (#270): on 2026-11-06 the
Firefox job was cancelled three times during `playwright install --with-deps`, leaving main red
with no test failed. The browsers are cached, the install step is bounded, and it is tried twice."""

from pathlib import Path

import yaml

CI = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml"


def browser_steps() -> list[dict]:
    return yaml.safe_load(CI.read_text())["jobs"]["browser"]["steps"]


def install_step() -> dict:
    [step] = [s for s in browser_steps() if "playwright install" in s.get("run", "")]
    return step


def test_the_browsers_are_cached_by_browser_and_the_e2e_lockfile():
    steps = browser_steps()
    [cache] = [s for s in steps if s.get("uses", "").startswith("actions/cache@")]
    assert cache["with"]["path"] == "~/.cache/ms-playwright"
    assert "matrix.browser" in cache["with"]["key"]
    assert "frontend/e2e/uv.lock" in cache["with"]["key"]
    assert steps.index(cache) < steps.index(install_step())


def test_the_install_is_bounded_and_tried_twice():
    step = install_step()
    assert 0 < step["timeout-minutes"] <= 8
    run = step["run"]
    assert "for attempt in 1 2" in run
    assert "timeout " in run  # each attempt bounded, so a hung one leaves time for the retry
