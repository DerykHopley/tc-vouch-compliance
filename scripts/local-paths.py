#!/usr/bin/env python3
"""Machine-local paths, caught before they're published (#340): a home folder (Linux, Fedora
Atomic's /var/home, macOS) or Claude's scratch folders under /tmp. They reveal a username and a
folder layout, and no reader can open them: name a local file by its file name instead.

`--pre-push` (the pre-push hook): reads git's ref lines on stdin and refuses a push whose new
commits add such a path to a tracked file, naming the file and line. trial/ is exempt (kept as
evidence). ticket-lint-comment.py uses local_paths_in on issue bodies.

Standard library only."""

import re
import subprocess
import sys

LOCAL_PATH = re.compile(
    # Each slash before a name escaped, so this file holds no path the hook would refuse.
    r"(?:/var\/home/|/home/[A-Za-z0-9._-]+/|/Users/[A-Za-z0-9._-]+/|/tmp\/claude-)[^\s`'\")\]>]*"
)
EXEMPT = ("trial/",)  # kept as evidence, as they were written
NO_COMMIT = "0" * 40


def local_paths_in(text: str) -> list[str]:
    """The machine-local paths in `text`, in order."""
    return LOCAL_PATH.findall(text)


def in_added_lines(diff: str) -> list[tuple[str, int, str]]:
    """Each local path a unified diff adds, as (file, line in the new file, path); files under
    EXEMPT left out."""
    found: list[tuple[str, int, str]] = []
    file, line = "", 0
    for text in diff.splitlines():
        if text.startswith("+++ "):
            file = text[6:] if text.startswith("+++ b/") else ""
        elif text.startswith("@@"):
            hunk = re.search(r"\+(\d+)", text)
            line = int(hunk.group(1)) if hunk else 0
        elif text.startswith("+") and file and not file.startswith(EXEMPT):
            found += [(file, line, path) for path in local_paths_in(text[1:])]
            line += 1
        elif not text.startswith("-"):
            line += 1
    return found


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout


def _base(local: str, remote: str) -> str:
    """Where the pushed commits start: the remote's commit, or for a new branch where it leaves
    main (origin/main, else main)."""
    if remote != NO_COMMIT:
        return remote
    for main in ("origin/main", "main"):
        try:
            return _git("merge-base", local, main).strip()
        except subprocess.CalledProcessError:
            continue
    return _git("rev-list", "--max-parents=0", local).split()[0]


def pre_push(refs: str) -> int:
    found: list[tuple[str, int, str]] = []
    for ref_line in refs.splitlines():
        parts = ref_line.split()
        if len(parts) != 4 or parts[1] == NO_COMMIT:  # a deleted branch adds nothing
            continue
        local, remote = parts[1], parts[3]
        found += in_added_lines(_git("diff", "-U0", f"{_base(local, remote)}..{local}"))
    for file, line, path in found:
        print(f"pre-push: {file}:{line} adds a machine-local path ({path})", file=sys.stderr)
    if found:
        print(
            "pre-push: name a local file by its file name, never its full path "
            "(docs/agents/agent-rules.md, #340).",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    if sys.argv[1:] != ["--pre-push"]:
        sys.exit("usage: scripts/local-paths.py --pre-push < git's pre-push ref lines")
    sys.exit(pre_push(sys.stdin.read()))
