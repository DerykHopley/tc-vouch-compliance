#!/usr/bin/env python3
"""Flag a verdict token used outside the roles docs/design.md gives it.
Usage: token-roles.py [path ...]   (default: frontend/src)
docs/design.md (Tokens) keeps --evidenced, --partly-evidenced, --not-evidenced, --not-applicable and
their -bg pairs for verdict chips, the readiness bar and card edges: "semantic, not accents". So in CSS
a rule using one must have a selector in ALLOWED below; in scripts (inline styles) any use is flagged.
Declaring the tokens (`--evidenced: #...`) is not a use. Exit 1 when anything is flagged.
Project check from the M0 retro (2026-11-04); design-lint.py checks that colours are tokens at all.
"""
import pathlib
import re
import sys

VERDICT = re.compile(r"var\(\s*--(?P<t>(?:partly-evidenced|not-evidenced|not-applicable|evidenced)(?:-bg)?)\s*[,)]")
# Selector prefixes for docs/design.md's three roles: verdict chips, card edges, the readiness bar.
ALLOWED = (".v-", ".card-head", ".readiness")
CSS = {".css"}
SCRIPT = {".ts", ".tsx", ".js", ".jsx"}
SKIP = {"node_modules", "dist", "build"}
RULE = re.compile(r"([^{}]+)\{([^{}]*)\}")


def blank_comments(text):
    return re.sub(r"/\*.*?\*/", lambda m: re.sub(r"[^\n]", " ", m.group(0)), text, flags=re.DOTALL)


def check_css(text):
    """Yield (line, selector, token) for each verdict token in a rule whose selector isn't allowed."""
    text = blank_comments(text)
    for rule in RULE.finditer(text):
        selectors = [s.strip() for s in rule.group(1).split(",")]
        selector = " ".join(rule.group(1).split())
        allowed = all(any(part.startswith(a) for a in ALLOWED) for part in selectors)
        if allowed:
            continue
        for m in VERDICT.finditer(rule.group(2)):
            line = text.count("\n", 0, rule.start(2) + m.start()) + 1
            yield line, selector, "--" + m.group("t")


def check_script(text):
    for n, line in enumerate(text.split("\n"), 1):
        for m in VERDICT.finditer(line):
            yield n, "inline style", "--" + m.group("t")


def main(paths):
    found = 0
    for root in paths:
        root = pathlib.Path(root)
        files = [root] if root.is_file() else sorted(p for p in root.rglob("*") if p.is_file())
        for f in files:
            if SKIP & set(f.parts) or f.suffix not in CSS | SCRIPT:
                continue
            text = f.read_text(encoding="utf-8", errors="replace")
            hits = check_css(text) if f.suffix in CSS else check_script(text)
            for line, selector, token in hits:
                print(f"{f}:{line} {selector} uses {token} (docs/design.md: verdict chips, card edges, readiness bar only)")
                found += 1
    print(f"{found} verdict token use(s) outside their roles")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or ["frontend/src"]))
