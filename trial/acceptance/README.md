# Hidden acceptance suite (sprint3 process trial)

Kept outside both trial repos, so neither arm's builders see it. It tests only behaviour that `docs/trial-spec.md`'s tickets state, through the interfaces they name:
- the `assess` CLI
- `ASSESS_FAKE_SCRIPT` and `ASSESS_POLICY_DIR`
- `from audit_agent import search_policies`

It is black-box: every run is a subprocess, with the API keys unset and HTTP sent to a dead proxy. Sockets are disabled in-process.

## Run it against an arm

From the trial repo's root, after its queue has stopped:

```bash
A=~/development/TuringCollege/sprint3-project/trial/acceptance
env -u OPENROUTER_API_KEY uv run --with pytest --with pytest-socket \
  pytest -c $A/pytest.ini --rootdir $A -p no:cacheprovider --disable-socket --allow-unix-socket \
  $A --junitxml=$A/results/$(basename $PWD).xml
```

Test names start with `test_t1_`, `test_t2_` or `test_t3_`, so failures count per ticket. Keep the XML files: the comparison re-renders from them.

## Checked before the trial (2026-11-03)

- 31 tests pass against a throwaway plain-Python reference implementation (no LangGraph).
- Each of three deliberate breaks turns the suite red, and only where it should:
  - folder checked before input validation: the 5 missing-dir bad-input cases fail
  - citations not filtered to searched docs: 2 tests fail
  - verdict taken from the answer turn: 5 tests fail

## After each arm

Check that no builder read this folder. Search that arm's session transcripts (`~/.claude/projects/*sprint3-trail-<arm>*/`; the local folders are spelled `trail`) for `trial/acceptance`. A hit means that arm's suite result doesn't count.
