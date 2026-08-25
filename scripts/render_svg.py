#!/usr/bin/env python3
"""Render a real `unfaked` run to SVG for the README.

    python scripts/render_svg.py             # writes docs/demo.svg and docs/finding.svg

`docs/demo.svg` animates: the commit an agent produced, the command, then the
report arriving line by line. `docs/finding.svg` is a still of one finding.

Neither is drawn. Both come from `examples/demo.py`, which builds a throwaway
repository and reports on it, so the images cannot drift from the tool and no
frame can be edited without re-running this.

SVG rather than a GIF: crisp at any width, selectable text, diffs as text in
review, a few kilobytes, and no recorder in the toolchain. CSS animation inside
an `<img>` is what the typing-SVG badges use, so GitHub renders it.

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

INK = {
    "fg": "#c9d1d9",
    "bg": "#0d1117",
    "chrome": "#161b22",
    "edge": "#30363d",
    "grey": "#8b949e",
    "dim": "#6e7681",
    "red": "#ff7b72",
    "green": "#3fb950",
    "yellow": "#d29922",
    "blue": "#58a6ff",
    "cyan": "#39c5cf",
}
SGR = {
    "31": ("red", False), "32": ("green", False), "33": ("yellow", False),
    "34": ("blue", False), "36": ("cyan", False), "90": ("dim", False),
    "1;31": ("red", True), "1;32": ("green", True), "1;33": ("yellow", True),
    "1;36": ("cyan", True), "1": ("fg", True), "2": ("dim", False),
}

CHAR_W = 7.7
LINE_H = 19.0
PAD_X = 26.0
PAD_TOP = 52.0
PAD_BOT = 24.0
BAR_H = 36.0

_ANSI = re.compile(r"\033\[([0-9;]*)m")

# The agent's own account of the commit, taken from the fixture rather than
# invented: examples/demo.py commits with this message.
CLAIM = "Adds comprehensive tests for the paginator."


def runs_of(line: str):
    out, colour, bold, pos = [], "fg", False, 0
    for m in _ANSI.finditer(line):
        if m.start() > pos:
            out.append((line[pos : m.start()], colour, bold))
        code = m.group(1) or "0"
        if code == "0":
            colour, bold = "fg", False
        elif code in SGR:
            colour, bold = SGR[code]
        pos = m.end()
    if pos < len(line):
        out.append((line[pos:], colour, bold))
    return [r for r in out if r[0]]


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _text_el(runs, x0: float, y: float, cls: str = "") -> str:
    if not runs:
        return ""
    c = f' class="{cls}"' if cls else ""
    parts = [f'<text{c} y="{y:.1f}" xml:space="preserve">']
    col = 0
    for text, colour, bold in runs:
        w = ' font-weight="600"' if bold else ""
        parts.append(
            f'<tspan x="{x0 + col * CHAR_W:.1f}" fill="{INK[colour]}"{w}>{esc(text)}</tspan>'
        )
        col += len(text)
    parts.append("</text>")
    return "".join(parts)


def frame(width: float, height: float, title: str, body: str, extra_css: str = "") -> str:
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}" height="{height:.0f}" \
viewBox="0 0 {width:.0f} {height:.0f}" role="img" \
font-family="ui-monospace,SFMono-Regular,'SF Mono',Menlo,Consolas,'Liberation Mono',monospace" \
font-size="13">
<style>
  text {{ dominant-baseline: middle; }}
{extra_css}
</style>
<rect x="0.5" y="0.5" width="{width - 1:.0f}" height="{height - 1:.0f}" rx="12"
      fill="{INK['bg']}" stroke="{INK['edge']}"/>
<path d="M0.5 12.5a12 12 0 0 1 12-12h{width - 25:.0f}a12 12 0 0 1 12 12v{BAR_H - 12:.0f}h-{width - 1:.0f}z"
      fill="{INK['chrome']}"/>
<line x1="0.5" y1="{BAR_H:.1f}" x2="{width - 0.5:.0f}" y2="{BAR_H:.1f}" stroke="{INK['edge']}"/>
<circle cx="{PAD_X:.0f}" cy="{BAR_H / 2:.0f}" r="5.5" fill="#ff5f57"/>
<circle cx="{PAD_X + 19:.0f}" cy="{BAR_H / 2:.0f}" r="5.5" fill="#febc2e"/>
<circle cx="{PAD_X + 38:.0f}" cy="{BAR_H / 2:.0f}" r="5.5" fill="#28c840"/>
<text x="{width / 2:.0f}" y="{BAR_H / 2:.0f}" fill="{INK['dim']}" font-size="11.5"
      text-anchor="middle">{esc(title)}</text>
{body}
</svg>
"""


def animated(lines, out_path: str) -> None:
    """The story: what the agent said, the command, then what actually happened."""
    prologue = [
        [("$ ", "green", True), ("git log -1 --format=%B | tail -2", "fg", False)],
        [(CLAIM, "grey", False)],
        [],
        [("$ ", "green", True), ("uvx unfaked --deep", "fg", False)],
    ]
    all_rows = prologue + [runs_of(l) for l in lines]

    # timing, in seconds
    t_claim, t_cmd, t_report, step = 0.35, 1.5, 2.6, 0.075
    hold = 4.0
    last = t_report + step * len(lines)
    total = last + hold

    delays = [t_claim, t_claim + 0.25, t_cmd, t_cmd]
    delays += [t_report + i * step for i in range(len(lines))]

    cols = max([len(_ANSI.sub("", l)) for l in lines] + [46])
    width = cols * CHAR_W + PAD_X * 2
    height = len(all_rows) * LINE_H + PAD_TOP + PAD_BOT

    body = []
    y = PAD_TOP
    for i, row in enumerate(all_rows):
        el = _text_el(row, PAD_X, y, cls=f"r r{i}")
        if el:
            body.append(el)
        y += LINE_H

    # a cursor that blinks until the command is "entered"
    cur_y = PAD_TOP + 3 * LINE_H
    cur_x = PAD_X + len("$ uvx unfaked --deep") * CHAR_W + 2
    body.append(
        f'<rect class="cur" x="{cur_x:.1f}" y="{cur_y - 7:.1f}" width="7.5" height="14" '
        f'fill="{INK["fg"]}"/>'
    )

    # One keyframe set per row. A shared animation with per-row `animation-delay`
    # would be shorter, but the rows also have to stay visible until the loop
    # restarts, and that hold has to sit inside each row's own timeline.
    # Base state is *visible*. Every row is drawn even if the stylesheet or the
    # animation is dropped somewhere between here and the reader -- a viewer that
    # kept `opacity: 0` but discarded the keyframes would otherwise render an
    # empty box. The animation supplies the hidden state from its own 0% frame.
    css = ["  .r { opacity: 1; }"]
    for i, d in enumerate(delays):
        p = max(0.01, d / total * 100)
        css.append(
            f"  .r{i} {{ animation: a{i} {total:.2f}s linear infinite; }}"
            f" @keyframes a{i} {{ 0%,{p:.2f}% {{ opacity:0 }} {min(p + 0.6, 99.9):.2f}%,100% {{ opacity:1 }} }}"
        )
    p_cmd = t_cmd / total * 100
    # Same reasoning as the rows: hidden by default, shown by the animation, so a
    # stripped stylesheet leaves no stray cursor parked in the middle of the frame.
    css.append(
        f"  .cur {{ visibility: hidden;"
        f" animation: blink 1s step-end infinite, gone {total:.2f}s linear infinite; }}"
    )
    css.append("  @keyframes blink { 50% { opacity: 0 } }")
    css.append(
        f"  @keyframes gone {{ 0%,{p_cmd:.2f}% {{ visibility:visible }}"
        f" {p_cmd + 0.1:.2f}%,100% {{ visibility:hidden }} }}"
    )
    css.append("  @media (prefers-reduced-motion: reduce) {")
    css.append("    .r { opacity: 1 !important; animation: none !important }")
    css.append("    .cur { visibility: hidden; animation: none !important }")
    css.append("  }")

    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(frame(width, height, "unfaked --deep", "\n".join(body), "\n".join(css)))


def still(lines, out_path: str, title: str) -> None:
    cols = max([len(_ANSI.sub("", l)) for l in lines] + [46])
    width = cols * CHAR_W + PAD_X * 2
    height = len(lines) * LINE_H + PAD_TOP + PAD_BOT
    body, y = [], PAD_TOP
    for line in lines:
        el = _text_el(runs_of(line), PAD_X, y)
        if el:
            body.append(el)
        y += LINE_H
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(frame(width, height, title, "\n".join(body)))


def demo_output() -> list:
    env = dict(os.environ, COLUMNS="84")
    env.pop("FORCE_COLOR", None)
    env["CLICOLOR_FORCE"] = "1"  # the demo writes to a pipe; ask it for colour
    proc = subprocess.run(
        [sys.executable, os.path.join(ROOT, "examples", "demo.py")],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    if not proc.stdout.strip():
        sys.stderr.write(proc.stderr or "demo produced no output\n")
        raise SystemExit(1)
    lines = proc.stdout.rstrip("\n").split("\n")
    while lines and not lines[0].strip():
        lines.pop(0)
    return lines


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--outdir", default=os.path.join(ROOT, "docs"))
    args = ap.parse_args()
    os.makedirs(args.outdir, exist_ok=True)

    lines = demo_output()
    summary = lines[:12]

    # One complete finding, from its header to the `run` line that reproduces it.
    # Sliced at the block boundaries the renderer emits rather than by index, so
    # it stays a whole finding when the report changes shape.
    plain = [_ANSI.sub("", l) for l in lines]
    starts = [i for i, l in enumerate(plain) if re.match(r"^\s{2}[✗▲?]\s{2}\S", l)]
    if not starts:
        raise SystemExit("no finding block in the demo output")
    begin = starts[0]
    end = starts[1] if len(starts) > 1 else len(lines)
    detail = [l for l in lines[begin:end] if l.strip()]

    animated(summary, os.path.join(args.outdir, "demo.svg"))
    still(detail, os.path.join(args.outdir, "finding.svg"), "one finding")
    sys.stderr.write(
        "demo.svg: %d lines animated · finding.svg: %d lines\n" % (len(summary), len(detail))
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
