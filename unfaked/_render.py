"""Terminal output.

Zero dependencies, so the escape codes are written out by hand. Colour is off
whenever stdout is not a tty, when NO_COLOR is set, or when --no-color is
passed.
"""

import os
import shutil
import sys
from typing import List, Optional

from ._finding import FAIL, INFO, WARN, CheckResult, Finding

RESET = "\033[0m"


class Style:
    def __init__(self, enabled: bool) -> None:
        self.on = enabled

    def _w(self, code: str, text: str) -> str:
        return "%s%s%s" % (code, text, RESET) if self.on else text

    def bold(self, t):
        return self._w("\033[1m", t)

    def dim(self, t):
        return self._w("\033[2m", t)

    def red(self, t):
        return self._w("\033[31m", t)

    def bred(self, t):
        return self._w("\033[1;31m", t)

    def green(self, t):
        return self._w("\033[32m", t)

    def bgreen(self, t):
        return self._w("\033[1;32m", t)

    def yellow(self, t):
        return self._w("\033[33m", t)

    def byellow(self, t):
        return self._w("\033[1;33m", t)

    def blue(self, t):
        return self._w("\033[34m", t)

    def cyan(self, t):
        return self._w("\033[36m", t)

    def bcyan(self, t):
        return self._w("\033[1;36m", t)

    def grey(self, t):
        return self._w("\033[90m", t)


def color_enabled(no_color: bool, stream=None) -> bool:
    stream = stream or sys.stdout
    if no_color or os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    if os.environ.get("TERM") == "dumb":
        return False
    return bool(getattr(stream, "isatty", lambda: False)())


def width() -> int:
    try:
        w = shutil.get_terminal_size((80, 24)).columns
    except Exception:  # pragma: no cover
        w = 80
    return max(62, min(w, 96))


_GLYPH = {FAIL: "✗", WARN: "▲", INFO: "·"}
_STATUS_GLYPH = {
    "fail": "✗",
    "warn": "▲",
    "ok": "✓",
    "inconclusive": "?",
    "skipped": "–",
}


def _paint_sev(st: Style, sev: str, text: str) -> str:
    if sev == FAIL:
        return st.bred(text)
    if sev == WARN:
        return st.byellow(text)
    return st.grey(text)


def _wrap(text: str, w: int) -> List[str]:
    words = text.split()
    out, cur = [], ""
    for word in words:
        if cur and len(cur) + 1 + len(word) > w:
            out.append(cur)
            cur = word
        else:
            cur = (cur + " " + word) if cur else word
    if cur:
        out.append(cur)
    return out or [""]


def _truncate(text: str, w: int) -> str:
    return text if len(text) <= w else text[: max(0, w - 1)] + "…"


class Report:
    def __init__(self, ctx, checks: List[CheckResult]) -> None:
        self.ctx = ctx
        self.checks = checks

    @property
    def findings(self) -> List[Finding]:
        out: List[Finding] = []
        for c in self.checks:
            out.extend(c.findings)
        out.sort(key=lambda f: f.sort_key())
        return out

    def count(self, sev: str) -> int:
        return sum(1 for f in self.findings if f.severity == sev)

    @property
    def exit_code(self) -> int:
        return 1 if self.count(FAIL) else 0


def headline(report: Report) -> str:
    """The one sentence that has to survive being read at a glance."""
    ctx = report.ctx
    probe = next((c for c in report.checks if c.name == "revert-probe"), None)
    if probe is not None and probe.stats:
        survived = probe.stats.get("survived", 0)
        added = probe.stats.get("added", 0)
        distinguishing = probe.stats.get("distinguishing", 0)
        if survived and distinguishing:
            # Both halves matter. Leading with the survivors alone reads as an
            # accusation when the change is, in fact, demonstrably tested.
            return (
                "%d of the %d tests it added also pass with the change reverted; "
                "the other %d fail without it." % (survived, added, distinguishing)
            )
        if survived:
            return "%d of the %d tests it added still pass with the change reverted." % (
                survived,
                added,
            )

    hollow = next((c for c in report.checks if c.name == "hollow-tests"), None)
    if hollow is not None:
        n = sum(1 for f in hollow.findings if f.severity == FAIL)
        if n:
            tests = {f.extra.get("test") for f in hollow.findings if f.severity == FAIL}
            return "%d of the %d tests it added cannot fail." % (len(tests), ctx.added_test_count)

    weakened = [
        f
        for c in report.checks
        for f in c.findings
        if f.severity == FAIL and f.extra.get("kind") == "weakened"
    ]
    if weakened:
        return "%d assertion%s in existing tests were replaced with weaker ones." % (
            len(weakened),
            "" if len(weakened) == 1 else "s",
        )

    n_fail = report.count(FAIL)
    if n_fail:
        return "%d thing%s here was made to pass rather than made to work." % (
            n_fail,
            "" if n_fail == 1 else "s",
        )

    if probe is not None and probe.stats.get("probed"):
        n = probe.stats["probed"]
        if n == 1:
            return "The one test it added fails with the change reverted. It tests the change."
        return "All %d tests it added fail with the change reverted. They test the change." % n
    n_warn = report.count(WARN)
    if n_warn:
        return "Nothing faked found. %d thing%s worth a look." % (
            n_warn,
            "" if n_warn == 1 else "s",
        )
    return "Nothing faked found."


def render(report: Report, st: Style, w: Optional[int] = None, quiet: bool = False) -> str:
    w = w or width()
    ctx = report.ctx
    L: List[str] = []
    pad = "  "
    inner = w - len(pad)

    if quiet and not report.count(FAIL) and not report.count(WARN):
        # Hook mode with nothing to say, so it says nothing. This runs after
        # every turn, including the ones that changed no code, and a line that
        # appears when nothing happened is the thing that gets a hook muted.
        # `-v` brings the confirmation back for anyone wiring one up.
        if not ctx.verbose:
            return ""
        return "  %s %s\n" % (st.bgreen("▎"), st.grey(headline(report)))

    # --- header -----------------------------------------------------------
    L.append("")
    title = "%s  %s" % (st.bold("unfaked"), st.cyan(ctx.repo_label))
    L.append(pad + title + st.grey("  ·  %s" % ctx.range_label))

    facts = []
    facts.append("%d file%s changed" % (ctx.file_count, "" if ctx.file_count == 1 else "s"))
    facts.append(
        "%d test%s added" % (ctx.added_test_count, "" if ctx.added_test_count == 1 else "s")
    )
    if ctx.runner_label:
        facts.append(ctx.runner_label)
    if ctx.unsupported_langs:
        facts.append("static-only: " + ", ".join(sorted(ctx.unsupported_langs)))
    L.append(pad + st.grey("  ·  ".join(facts)))
    L.append("")

    # --- headline ---------------------------------------------------------
    head = headline(report)
    n_fail = report.count(FAIL)
    bar = "▎"
    paint = st.bred if n_fail else st.bgreen
    for i, line in enumerate(_wrap(head, inner - 2)):
        L.append(pad + paint(bar) + " " + (st.bold(line) if i == 0 else line))
    L.append("")

    if getattr(ctx, "hint", ""):
        for line in _wrap(ctx.hint, inner - 2):
            L.append(pad + st.grey("  " + line))
        L.append("")

    # --- per-check summary ------------------------------------------------
    name_w = max([len(c.name) for c in report.checks] + [12])
    for c in report.checks:
        glyph = _STATUS_GLYPH.get(c.status, "?")
        if c.status == "fail":
            g, tail = st.red(glyph), st.red(_tally(c, FAIL))
        elif c.status == "warn":
            g, tail = st.yellow(glyph), st.yellow(_tally(c, WARN))
        elif c.status == "ok":
            g, tail = st.green(glyph), st.grey("clean")
        elif c.status == "skipped":
            g, tail = st.grey(glyph), st.grey("skipped")
        else:
            g, tail = st.grey(glyph), st.grey("not run")
        left = "%s %s" % (g, st.bold(c.name.ljust(name_w)))
        mid = st.grey(c.title)
        raw_len = 2 + name_w + 2 + len(c.title)
        tail_raw = _tally(c, FAIL) if c.status == "fail" else (
            _tally(c, WARN) if c.status == "warn" else
            ("clean" if c.status == "ok" else ("skipped" if c.status == "skipped" else "not run"))
        )
        gap = max(1, inner - raw_len - len(tail_raw))
        L.append(pad + left + "  " + mid + " " * gap + tail)
        if c.status in ("inconclusive", "skipped") and c.note:
            for line in _wrap(c.note, inner - 4):
                L.append(pad + "  " + st.grey(line))
    L.append("")

    # --- findings ---------------------------------------------------------
    findings = [f for f in report.findings if f.severity != INFO or ctx.verbose]
    if findings:
        L.append(pad + st.grey("─" * inner))
        L.append("")
    for f in findings:
        L.extend(_render_finding(f, st, pad, inner))

    # --- footer -----------------------------------------------------------
    L.append(pad + st.grey("─" * inner))
    counts = []
    if report.count(FAIL):
        counts.append(st.red("%d fail" % report.count(FAIL)))
    if report.count(WARN):
        counts.append(st.yellow("%d warn" % report.count(WARN)))
    n_info = report.count(INFO)
    if n_info:
        counts.append(st.grey("%d info" % n_info))
    if not counts:
        counts.append(st.green("0 findings"))
    raw_counts = " · ".join(
        x
        for x in (
            "%d fail" % report.count(FAIL) if report.count(FAIL) else "",
            "%d warn" % report.count(WARN) if report.count(WARN) else "",
            "%d info" % n_info if n_info else "",
        )
        if x
    ) or "0 findings"
    hint = "exit %d" % report.exit_code
    if n_info and not ctx.verbose:
        hint = "%d info hidden · -v  ·  exit %d" % (n_info, report.exit_code)
        raw_hint = hint
    else:
        raw_hint = hint
    gap = max(1, inner - len(raw_counts) - len(raw_hint))
    L.append(pad + " · ".join(counts) + " " * gap + st.grey(hint))
    L.append("")
    return "\n".join(L)


def _tally(c: CheckResult, sev: str) -> str:
    n = sum(1 for f in c.findings if f.severity == sev)
    return "%d %s" % (n, "fail" if sev == FAIL else "warn")


def _render_finding(f: Finding, st: Style, pad: str, inner: int) -> List[str]:
    L: List[str] = []
    glyph = _GLYPH.get(f.severity, "·")
    loc = f.location or f.check
    head_left = "%s  %s" % (glyph, loc)
    gap = max(1, inner - len(head_left) - len(f.check))
    L.append(
        pad
        + _paint_sev(st, f.severity, glyph)
        + "  "
        + st.bcyan(loc)
        + " " * gap
        + st.grey(f.check)
    )
    for i, line in enumerate(_wrap(f.title, inner - 3)):
        L.append(pad + "   " + (st.bold(line) if i == 0 else line))

    snippet = list(f.snippet)
    while snippet and not snippet[-1].split("\t", 1)[-1].strip():
        snippet.pop()
    if snippet:
        L.append("")
        for raw in snippet[:8]:
            if "\t" in raw:
                num, code = raw.split("\t", 1)
            else:
                num, code = "", raw
            L.append(
                pad
                + "     "
                + st.grey("%5s │ " % num)
                + _truncate(code, max(10, inner - 13))
            )
        L.append("")

    for label, text in (("why", f.why), ("fix", f.fix)):
        if not text:
            continue
        lines = _wrap(text, inner - 9)
        L.append(pad + "   " + st.grey(label) + "  " + lines[0])
        for extra in lines[1:]:
            L.append(pad + "        " + extra)
    if f.command:
        # never truncate a command; a reader has to be able to paste it
        cmd_lines = _wrap(f.command, inner - 9)
        L.append(pad + "   " + st.grey("run") + "  " + st.dim(cmd_lines[0]))
        for extra in cmd_lines[1:]:
            L.append(pad + "        " + st.dim(extra))
    L.append("")
    return L
