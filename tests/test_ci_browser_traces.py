"""CI's browser jobs keep a Playwright trace for each failed test (#697): three of #664's one-off
failures (#694, #695, #696) couldn't be read from the log alone. The browser check records a trace
per test and keeps it only when the test failed; the traces are uploaded only when the job failed,
so a green run uploads nothing extra."""

from pathlib import Path

import yaml

CI = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml"


def browser_steps() -> list[dict]:
    return yaml.safe_load(CI.read_text())["jobs"]["browser"]["steps"]


def test_the_browser_check_keeps_a_trace_for_each_failed_test_in_the_runner_temp():
    [check] = [s for s in browser_steps() if "frontend/e2e/run.sh" in s.get("run", "")]
    run = check["run"]
    assert "--tracing retain-on-failure" in run
    assert "--output ${{ runner.temp }}/playwright-traces" in run


def test_the_traces_are_uploaded_only_when_the_job_failed():
    steps = browser_steps()
    [check] = [s for s in steps if "frontend/e2e/run.sh" in s.get("run", "")]
    [upload] = [
        s
        for s in steps
        if s.get("uses", "").startswith("actions/upload-artifact@")
        and "playwright-traces" in s["with"]["path"]
    ]
    assert upload["if"] == "failure()"
    assert upload["with"]["path"] == "${{ runner.temp }}/playwright-traces"
    assert "matrix.browser" in upload["with"]["name"]
    assert steps.index(check) < steps.index(upload)
