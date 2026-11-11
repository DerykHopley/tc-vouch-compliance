"""Tests for scripts/merge-on-green.sh's size report, with a fake `gh` on PATH. Run: python3 -I -m unittest discover -s tests"""

import os
import pathlib
import subprocess
import tempfile
import textwrap
import unittest

GATE = pathlib.Path(__file__).parent.parent / "scripts" / "merge-on-green.sh"

FAKE_GH = textwrap.dedent("""\
    #!/usr/bin/env bash
    # Records each call, and answers the few questions the gate asks.
    echo "$*" >> "$FAKE_LOG"
    case "$*" in
      *"--json headRefOid"*) echo abc1234def ;;
      *"--json files"*) printf '%s\\n' "$FAKE_FILES" ;;
      *"--json mergeable"*) echo "${FAKE_MERGEABLE:-MERGEABLE}" ;;
      *"pr checks"*"--json name"*) echo "${FAKE_CHECKS:-1}" ;;
      *"pr checks"*) exit 0 ;;
      *"pr merge"*) exit 0 ;;
    esac
    """)


def run_gate(files, target=None, extra_env=None):
    """files: lines of '<changed lines> <path>', as the gate's size query returns them."""
    with tempfile.TemporaryDirectory() as d:
        gh = pathlib.Path(d) / "gh"
        gh.write_text(FAKE_GH)
        gh.chmod(0o755)
        sleep = pathlib.Path(d) / "sleep"  # the wait for checks, made instant
        sleep.write_text("#!/bin/sh\n")
        sleep.chmod(0o755)
        log = pathlib.Path(d) / "calls.log"
        log.touch()
        env = {
            **os.environ,
            "PATH": f"{d}:{os.environ['PATH']}",
            "FAKE_LOG": str(log),
            "FAKE_FILES": "\n".join(files),
        }
        env.pop("SIZE_TARGET", None)
        env.update(extra_env or {})
        if target is not None:
            env["SIZE_TARGET"] = str(target)
        p = subprocess.run(
            ["bash", str(GATE), "7"],
            check=False,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return p, log.read_text()


class SizeTarget(unittest.TestCase):
    def test_over_the_target_still_merges_and_says_so(self):
        # 400 is a target, not a wall: the ticket loop reviews a bigger PR more deeply instead.
        p, calls = run_gate(["520 src/app.py"])
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("pr merge", calls)
        self.assertIn("520", p.stdout)
        self.assertIn("over the 400 target", p.stdout)

    def test_at_the_target_merges_without_a_note(self):
        p, calls = run_gate(["250 src/app.py", "150 tests/test_app.py"])
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("pr merge", calls)
        self.assertIn("400 changed lines", p.stdout)
        self.assertNotIn("over the", p.stdout)

    def test_lockfiles_do_not_count(self):
        p, _calls = run_gate(
            ["300 src/app.py", "5000 uv.lock", "900 web/package-lock.json", "40 go.sum"]
        )
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("300 changed lines", p.stdout)
        self.assertNotIn("over the", p.stdout)

    def test_size_target_can_be_set(self):
        p, _calls = run_gate(["413 src/app.py"], target=300)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("over the 300 target", p.stdout)

    def test_a_spec_pr_merges_whatever_its_size(self):
        # A spec's one PR into main carries every ticket.
        p, calls = run_gate(["2400 src/app.py"])
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("pr merge", calls)


class NoChecks(unittest.TestCase):
    def test_a_conflicting_pr_is_told_to_merge_its_base(self):
        # GitHub runs no pull_request checks on a PR with a conflict, so waiting longer can't help (rig #70).
        p, calls = run_gate(
            ["10 src/app.py"], extra_env={"FAKE_CHECKS": "0", "FAKE_MERGEABLE": "CONFLICTING"}
        )
        self.assertEqual(p.returncode, 1)
        self.assertNotIn("pr merge", calls)
        self.assertIn("after 5 minutes (mergeable: CONFLICTING)", p.stderr)
        self.assertIn("merge its base in and push", p.stderr)

    def test_a_mergeable_pr_is_run_once_more(self):
        p, calls = run_gate(["10 src/app.py"], extra_env={"FAKE_CHECKS": "0"})
        self.assertEqual(p.returncode, 1)
        self.assertNotIn("pr merge", calls)
        self.assertIn("Run this once more", p.stderr)


if __name__ == "__main__":
    unittest.main()
