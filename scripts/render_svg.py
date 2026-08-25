#!/usr/bin/env python3
"""Render real `unfaked` output to an SVG for the README.

    python scripts/render_svg.py docs/hero.svg

The point is that the image cannot drift from the tool. It runs
`examples/demo.py`, which builds a throwaway repository and reports on it, then
draws that output verbatim. No frame is typed by hand and none can be edited
without re-running this.

SVG rather than a GIF or a screenshot: it stays crisp at any width, the text is
selectable, it diffs as text in review, and it is a few kilobytes.

Requires pytest on the interpreter you run it with, because the demo runs the
revert probe.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# The terminal palette. Tuned so the same file is legible on GitHub's light and
# dark themes, which is why the background is drawn rather than left transparent.
PALETTE = {
    "fg": "#c9d1d9",
    "bg": "#0d1117",
    "grey": "#6e7681",
    "red": "#ff7b72",
    "green": "#3fb950",
    "yellow": "#d29922",
    "blue": "#58a6ff",
    "cyan": "#39c5cf",
}
SGR = {
    "31": ("red", False),
    "32": ("green", False),
    "33": ("yellow", False),
    "34": ("blue", False),
    "36": ("cyan", False),
    "90": ("grey", False),
    "1;31": ("red", True),
    "1;32": ("green", True),
    "1;33": ("yellow", True),
    "1;36": ("cyan", True),
    "1": ("fg", True),
    "2": ("grey", False),
}

CHAR_W = 8.4  # advance width of the 14px monospace stack below
LINE_H = 20.0
PAD_X = 22.0
PAD_Y = 20.0
TITLEBAR = 34.0

_ANSI = re.compile(r"\033\[([0-9;]*)m")


def parse_ansi(line: str):
    """Split one line into (text, colour, bold) runs."""
    runs = []
    colour, bold, pos = "fg", False, 0
    for m in _ANSI.finditer(line):
        if m.start() > pos:
            runs.append((line[pos : m.start()], colour, bold))
        code = m.group(1) or "0"
        if code == "0":
            colour, bold = "fg", False
        elif code in SGR:
            colour, bold = SGR[code]
        pos = m.end()
    if pos < len(line):
        runs.append((line[pos:], colour, bold))
    return [r for r in runs if r[0]]


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def render(lines, title: str) -> str:
    cols = max([len(_ANSI.sub("", l)) for l in lines] + [40])
    width = cols * CHAR_W + PAD_X * 2
    height = len(lines) * LINE_H + PAD_Y * 2 + TITLEBAR

    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}" '
        f'height="{height:.0f}" viewBox="0 0 {width:.0f} {height:.0f}" '
        f'font-family="ui-monospace,SFMono-Regular,Menlo,Consolas,monospace" font-size="14">',
        f'<rect width="{width:.0f}" height="{height:.0f}" rx="10" fill="{PALETTE["bg"]}"/>',
        # window chrome, so it reads as a terminal at a glance
        f'<circle cx="{PAD_X:.0f}" cy="18" r="6" fill="#ff5f57"/>',
        f'<circle cx="{PAD_X + 20:.0f}" cy="18" r="6" fill="#febc2e"/>',
        f'<circle cx="{PAD_X + 40:.0f}" cy="18" r="6" fill="#28c840"/>',
        f'<text x="{width / 2:.0f}" y="23" fill="{PALETTE["grey"]}" font-size="12" '
        f'text-anchor="middle">{esc(title)}</text>',
    ]

    y = TITLEBAR + PAD_Y
    for line in lines:
        runs = parse_ansi(line)
        if runs:
            out.append(f'<text x="{PAD_X:.1f}" y="{y:.1f}" xml:space="preserve">')
            col = 0
            for text, colour, bold in runs:
                x = PAD_X + col * CHAR_W
                weight = ' font-weight="bold"' if bold else ""
                out.append(
                    f'<tspan x="{x:.1f}" fill="{PALETTE[colour]}"{weight}>{esc(text)}</tspan>'
                )
                col += len(text)
            out.append("</text>")
        y += LINE_H
    out.append("</svg>")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("output")
    ap.add_argument("--title", default="unfaked --deep")
    ap.add_argument(
        "--lines", type=int, default=0, help="keep only the first N output lines (0 = all)"
    )
    args = ap.parse_args()

    env = dict(os.environ, COLUMNS="86", FORCE_COLOR="1")
    proc = subprocess.run(
        [sys.executable, os.path.join(ROOT, "examples", "demo.py")],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    if not proc.stdout.strip():
        sys.stderr.write(proc.stderr or "demo produced no output\n")
        return 1

    lines = proc.stdout.rstrip("\n").split("\n")
    while lines and not lines[0].strip():
        lines.pop(0)
    if args.lines:
        lines = lines[: args.lines]

    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as fh:
        fh.write(render(lines, args.title))
    sys.stderr.write(f"{args.output}: {len(lines)} lines\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
