#!/usr/bin/env python3
"""Print the WCAG 2 contrast ratio of two hex colours, so a ratio is computed, never estimated.
Usage: contrast.py <fg> <bg> [--min 4.5]   e.g. contrast.py '#3F7A55' '#E2EDE5' --min 4.5
With --min it exits 1 when the ratio is below the floor (docs/design.md: 4.5:1 text, 3:1 large text
and shapes). Colours are #rgb or #rrggbb; anything else exits 2. Standard library only.
"""
import argparse
import re
import sys

HEX = re.compile(r"^#?([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


def luminance(colour):
    m = HEX.match(colour)
    if not m:
        sys.exit(print(f"contrast.py: {colour!r} isn't a hex colour (#rgb or #rrggbb)", file=sys.stderr) or 2)
    h = m.group(1)
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    channels = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    r, g, b = (c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def ratio(fg, bg):
    a, b = sorted((luminance(fg), luminance(bg)), reverse=True)
    return (a + 0.05) / (b + 0.05)


def main(argv):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("fg"); p.add_argument("bg"); p.add_argument("--min", type=float)
    args = p.parse_args(argv)
    r = ratio(args.fg, args.bg)
    line = f"{r:.2f}:1  {args.fg} on {args.bg}"
    if args.min is not None and r < args.min:
        print(f"{line}  below {args.min:g}:1")
        return 1
    print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
