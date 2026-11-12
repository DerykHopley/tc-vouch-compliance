"""CI's browser jobs install Playwright's browser quickly and boundedly (#270): on 2026-11-06 the
Firefox job was cancelled three times during `playwright install --with-deps`, leaving main red
with no test failed. The browsers are cached, the install step is bounded, and it is tried twice."""

from pathlib import Path

import yaml

CI = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml"


def browser_steps() -> list[dict]:
    return yaml.safe_load(CI.read_text())["jobs"]["browser"]["steps"]


def step_running(command: str) -> dict:
    [step] = [s for s in browser_steps() if command in s.get("run", "")]
    return step


def test_the_browsers_are_cached_by_browser_and_the_e2e_lockfile():
    steps = browser_steps()
    [cache] = [s for s in steps if s.get("uses", "").startswith("actions/cache@")]
    assert cache["with"]["path"] == "~/.cache/ms-playwright"
    assert "matrix.browser" in cache["with"]["key"]
    assert "frontend/e2e/uv.lock" in cache["with"]["key"]
    assert steps.index(cache) < steps.index(step_running("playwright install "))


def test_the_system_packages_install_bounded_by_the_step_never_killed_midway():
    # #290: a `timeout` around apt killed only uv; apt kept the dpkg lock and the retry failed.
    # #611 retries the download once, safe because no `timeout` kills apt midway: apt exits on
    # its own and frees the lock first.
    [step] = [s for s in browser_steps() if s.get("id") == "apt-install"]
    assert 0 < step["timeout-minutes"] <= 8
    assert "timeout " not in step["run"]
    assert "for attempt in 1 2" in step["run"]
    assert "--with-deps" not in step_running("playwright install ")["run"]


def test_the_system_packages_come_from_a_cache_keyed_on_the_runner_image_and_playwright():
    # #611: a mirror DNS outage timed the step out with no test run; the .debs are cached.
    steps = browser_steps()
    [key] = [s for s in steps if s.get("id") == "apt-key"]
    assert "ImageOS" in key["run"] and "ImageVersion" in key["run"] and "playwright" in key["run"]
    [restore] = [s for s in steps if s.get("uses", "").startswith("actions/cache/restore@")]
    [save] = [s for s in steps if s.get("uses", "").startswith("actions/cache/save@")]
    assert restore["with"]["path"] == save["with"]["path"] == "~/.cache/apt-debs"
    [install] = [s for s in steps if s.get("id") == "apt-install"]
    assert steps.index(restore) < steps.index(install) < steps.index(save)
    assert "--no-download" in install["run"]


def test_the_browser_download_is_bounded_and_tried_twice():
    step = step_running("playwright install ")
    assert 0 < step["timeout-minutes"] <= 8
    run = step["run"]
    assert "for attempt in 1 2" in run
    assert "timeout " in run  # each attempt bounded, so a hung one leaves time for the retry
