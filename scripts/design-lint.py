#!/usr/bin/env python3
"""Flag colour values written outside a design token, so docs/design.md and the CSS stay the one source.
Usage: design-lint.py [path ...]   (default: src)
A colour (#hex, rgb(), hsl()) may appear only where a custom property is declared or set
(`--accent: #2563eb`, `el.style.setProperty('--slot', '#cfe3ff')`). Comments are ignored; in scripts,
only string literals are read. A line holding `design-lint: allow` is skipped (say why on that line). A colour function around a token
(`hsl(var(--border))`) passes. Tailwind palette classes (`text-amber-600`, `hover:bg-red-500/50`) count as colours;
theme classes (`bg-primary`) pass. Hidden folders, build output and installed packages are not read.
Exit 1 when anything is flagged. Standard library only. Colours only: sizes and spacing are left to review.
Copied from the rig (~/development/rig) by project-kickoff; a kickoff upgrade compares it with the rig's.
"""
import pathlib, re, sys

CSS = {".css", ".scss", ".sass", ".less"}
SCRIPT = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".vue", ".svelte", ".html"}
COLOUR = re.compile(r"#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3,4})\b|\b(?:rgba?|hsla?|oklch|oklab|lab|lch|hwb)\([^)]*\)")
# A Tailwind colour utility with a palette colour: a colour outside the tokens, written as a class.
PALETTE = re.compile(r"(?<![\w-])(?:bg|text|border|ring|fill|stroke|from|via|to|outline|decoration|divide|placeholder|caret|accent|shadow)"
                     r"-(?:slate|gray|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet"
                     r"|purple|fuchsia|pink|rose)-(?:50|[1-9]00|950)(?:/\d+)?(?![\w-])")
TOKEN = re.compile(r"--[\w-]+\s*:[^;{}]*|setProperty\(\s*['\"`]--[^)]*")
ALLOW = "design-lint: allow"
# Build output, tool state and installed packages: not the project's own code.
SKIP = {"node_modules", "dist", "build", "coverage", "playwright-report", "test-results", "site-packages"}


def split(text, script):
    """Return (code, strings): the text with comments blanked, and with everything but string contents blanked.
    Newlines are kept in both, so line numbers stay true."""
    code, strings, i, n = [], [], 0, len(text)
    quote = None
    while i < n:
        c = text[i]
        if quote:
            if c == "\\" and i + 1 < n:
                code.append(text[i:i + 2]); strings.append(text[i:i + 2]); i += 2; continue
            if c == quote or (c == "\n" and quote != "`"):  # ' and " end at a line's end: JSX text ("ntfy's") isn't a string
                quote = None
            code.append(c); strings.append(c if quote else " "); i += 1; continue
        if text.startswith("/*", i):
            end = text.find("*/", i + 2); end = n if end < 0 else end + 2
            blank = re.sub(r"[^\n]", " ", text[i:end]); code.append(blank); strings.append(blank); i = end; continue
        if script and text.startswith("//", i):
            end = text.find("\n", i); end = n if end < 0 else end
            blank = " " * (end - i); code.append(blank); strings.append(blank); i = end; continue
        if c in "'\"" or (script and c == "`"):
            quote = c
        code.append(c); strings.append("\n" if c == "\n" else " "); i += 1
    return "".join(code), "".join(strings)


def check(name, text):
    """Yield (line number, colour) for each colour outside a token."""
    script = pathlib.Path(name).suffix in SCRIPT
    code, strings = split(text, script)
    raw_lines = text.split("\n")
    token_lines = set()  # every line a token declaration spans, so a value over several lines counts whole
    for m in TOKEN.finditer(code):
        first = code.count("\n", 0, m.start()) + 1
        token_lines.update(range(first, first + m.group(0).count("\n") + 1))
    for number, line in enumerate((strings if script else code).split("\n"), 1):
        if ALLOW in raw_lines[number - 1] or number in token_lines:
            continue
        for m in COLOUR.finditer(line):
            if "var(--" not in m.group(0):  # hsl(var(--border)) wraps a token
                yield number, m.group(0)
        for m in PALETTE.finditer(line):
            yield number, m.group(0)


def message(value):
    if PALETTE.fullmatch(value):
        return f"{value} is a Tailwind palette class, not a design token; use a theme class from docs/design.md"
    return f"{value} is not a design token; use a var(--…) from docs/design.md"


def skipped(rel):
    """A folder the lint never reads: hidden ones (.astro, .claude, .venv) and SKIP."""
    return any(part in SKIP or part.startswith(".") for part in rel.parts[:-1])


def main(argv):
    roots = [pathlib.Path(a) for a in argv] or [pathlib.Path("src")]
    files = [p for r in roots for p in ([r] if r.is_file() else sorted(r.rglob("*")))
             if p.is_file() and p.suffix in CSS | SCRIPT and not skipped(p.relative_to(r) if r.is_dir() else p)]
    flagged = 0
    for path in files:
        for number, value in check(path.name, path.read_text(encoding="utf-8", errors="replace")):
            print(f"{path}:{number}: {message(value)}")
            flagged += 1
    print(f"{flagged} colour(s) outside tokens in {len(files)} file(s)", file=sys.stderr)
    return 1 if flagged else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
