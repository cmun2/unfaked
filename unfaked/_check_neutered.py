"""Check B: checks that were switched off instead of satisfied.

Everything here is anchored to lines the diff *added*, so a suppression that
predates the change is never reported.
"""

import ast
import re
from typing import Dict, List, Optional, Tuple

from ._finding import FAIL, INFO, WARN, CheckResult, Finding
from ._git import FileDiff
from ._lang import PYTHON, JSTS, language

NAME = "neutered-checks"
TITLE = "checks switched off"

# (regex, label, why, fix)
_SKIP_PATTERNS: List[Tuple[re.Pattern, str, str, str]] = [
    (re.compile(r"@\s*(?:pytest\.)?mark\.skip(?:if)?\b"), "@pytest.mark.skip",
     "A skipped test reports as not-run, but a green suite reads as if it ran.",
     "Delete the marker and make the test pass, or say in the reason why it can never run here."),
    (re.compile(r"@\s*(?:pytest\.)?mark\.xfail\b"), "@pytest.mark.xfail",
     "xfail turns a failing test into a passing suite.",
     "Fix the behaviour, or set `strict=True` so an unexpected pass is still an error."),
    (re.compile(r"\bpytest\.skip\s*\("), "pytest.skip(...)",
     "A runtime skip hides the test from the result count.",
     "Skip on a real capability check, not to get past a failure."),
    (re.compile(r"\bself\.skipTest\s*\("), "self.skipTest(...)",
     "A runtime skip hides the test from the result count.",
     "Skip on a real capability check, not to get past a failure."),
    (re.compile(r"@\s*unittest\.(?:skip|expectedFailure)\b"), "@unittest.skip",
     "A skipped test reports as not-run, but a green suite reads as if it ran.",
     "Delete the marker and make the test pass."),
    (re.compile(r"\b(?:it|test|describe)\s*\.\s*(?:skip|todo|failing)\s*\("), "it.skip(...)",
     "A skipped spec never runs, but the suite still goes green.",
     "Delete `.skip` and make the spec pass."),
    (re.compile(r"\b(?:xit|xdescribe|xtest)\s*\("), "xit(...)",
     "A skipped spec never runs, but the suite still goes green.",
     "Rename back to `it`/`describe` and make the spec pass."),
    (re.compile(r"\bt\.Skip(?:Now|f)?\s*\("), "t.Skip(...)",
     "A skipped Go test still reports success for the package.",
     "Skip on a real capability check, not to get past a failure."),
    (re.compile(r"#\[\s*ignore\b"), "#[ignore]",
     "An ignored Rust test is not run by `cargo test`.",
     "Remove `#[ignore]` and make the test pass."),
    (re.compile(r"@\s*(?:Disabled|Ignore)\b"), "@Disabled",
     "A disabled test never runs, but the build stays green.",
     "Re-enable it and make it pass."),
]

# (regex, label, why, fix, scoped-form regex or None, severity when blanket)
#
# A suppression that names the rule it turns off is a decision someone can
# review; a blanket one hides everything on the line, including errors nobody
# has seen yet. Only the blanket form is a WARN -- the scoped form is INFO, so
# a repo that pins its suppressions properly stays quiet.
_SUPPRESS_PATTERNS: List[Tuple[re.Pattern, str, str, str, Optional[re.Pattern]]] = [
    (re.compile(r"#\s*type:\s*ignore"), "# type: ignore",
     "The type error is still there; only the report of it was removed.",
     "Narrow the type or fix the call. If it is genuinely unavoidable, pin it: `# type: ignore[code]`.",
     re.compile(r"#\s*type:\s*ignore\[")),
    (re.compile(r"#\s*noqa\b"), "# noqa",
     "The lint finding is still there; only the report of it was removed.",
     "Fix the lint, or pin it to one rule with `# noqa: E501`.",
     re.compile(r"#\s*noqa\s*:\s*\w")),
    (re.compile(r"#\s*pylint:\s*disable"), "# pylint: disable",
     "The lint finding is still there; only the report of it was removed.",
     "Fix the lint rather than disabling the rule.",
     re.compile(r"#\s*pylint:\s*disable\s*=\s*\w")),
    (re.compile(r"#\s*mypy:\s*(?:ignore-errors|disable-error-code)"), "# mypy: ignore-errors",
     "This turns off type checking for the whole module, not one line.",
     "Scope the suppression to the one line that needs it.",
     None),
    (re.compile(r"(?://|/\*)\s*eslint-disable"), "eslint-disable",
     "The lint finding is still there; only the report of it was removed.",
     "Fix the lint, or disable the single rule on the single line.",
     re.compile(r"eslint-disable(?:-next-line|-line)?\s+[@\w][-@/\w]*")),
    (re.compile(r"(?://|/\*)\s*@ts-ignore\b"), "@ts-ignore",
     "The type error is still there; only the report of it was removed, and @ts-ignore hides every error on the next line.",
     "Fix the type, or use `@ts-expect-error` so it fails once the error goes away.",
     None),
    (re.compile(r"(?://|/\*)\s*@ts-nocheck\b"), "@ts-nocheck",
     "This turns off type checking for the whole file.",
     "Delete it and fix the types, or scope the suppression to one line.",
     None),
    (re.compile(r"(?://|/\*)\s*@ts-expect-error\b"), "@ts-expect-error",
     "This asserts the next line does not type-check.",
     "Confirm the line is *meant* to be a type error; otherwise fix the type.",
     re.compile(r"@ts-expect-error")),
    (re.compile(r"#\[\s*allow\s*\("), "#[allow(...)]",
     "The lint finding is still there; only the report of it was removed.",
     "Fix the lint rather than allowing the rule.",
     re.compile(r"#\[\s*allow\s*\(\s*[\w:]")),
    (re.compile(r"//\s*nolint\b"), "//nolint",
     "The lint finding is still there; only the report of it was removed.",
     "Fix the lint, or pin it: `//nolint:errcheck`.",
     re.compile(r"//\s*nolint\s*:\s*\w")),
    (re.compile(r"@\s*SuppressWarnings\s*\("), "@SuppressWarnings",
     "The warning is still there; only the report of it was removed.",
     "Fix the cause rather than suppressing the warning.",
     re.compile(r"@\s*SuppressWarnings\s*\(\s*[\"{\w]")),
]

# `catch {}` / `catch (e) {}` / `.catch(() => {})`
_JS_EMPTY_CATCH = re.compile(
    r"\bcatch\s*(?:\([^()]*\)\s*)?\{\s*\}"
    r"|\.\s*catch\s*\(\s*(?:\(\s*[^()]*\s*\)|[A-Za-z_$][\w$]*)\s*=>\s*\{\s*\}\s*\)"
    r"|\.\s*catch\s*\(\s*\(\s*\)\s*=>\s*(?:null|undefined|void 0)\s*\)"
)

# Strong assertion forms, and the weak forms that replace them.
_STRONG = re.compile(
    r"\b(assertEqual|assertEquals|assertIs|assertDictEqual|assertListEqual|assertSetEqual"
    r"|assertCountEqual|assertSequenceEqual|assertAlmostEqual|assertRegex)\s*\("
    r"|\.\s*(toBe|toEqual|toStrictEqual|toMatchObject|toHaveBeenCalledWith|toMatchInlineSnapshot)\s*\("
    r"|^\s*assert\s+[^\n]*?(?:==|!=)\s*\S"
)
_WEAK = re.compile(
    r"\b(assertTrue|assertFalse|assertIsNotNone|assertIsNone|assertIn|assertNotIn)\s*\("
    r"|\.\s*(toBeTruthy|toBeFalsy|toBeDefined|toBeUndefined|toBeNull|toHaveBeenCalled|toBeInstanceOf)\s*\("
    r"|^\s*assert\s+[A-Za-z_][\w.\[\]()]*\s*(?:#.*)?$"
    r"|^\s*assert\s+[A-Za-z_][\w.\[\]()]*\s+is\s+not\s+None\s*$"
)

_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")


def _blank_js_noncode(src: str) -> str:
    from ._lang import _blank_noncode

    return _blank_noncode(src)


def _line_of_offset(src: str, offset: int) -> int:
    return src.count("\n", 0, offset) + 1


def _snip(lines: List[str], line: int, span: int = 1) -> List[str]:
    out = []
    for i in range(line, min(line + span, len(lines) + 1)):
        if 1 <= i <= len(lines):
            out.append("%d\t%s" % (i, lines[i - 1].rstrip()))
    return out


def _strip_comment_only(text: str) -> str:
    """Drop leading/trailing whitespace for pattern matching."""
    return text.rstrip("\n\r")


def _py_empty_handlers(src: str) -> List[Tuple[int, bool]]:
    """[(lineno of `except`, is_broad)] for handlers whose body is just pass/..."""
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        body = list(node.body)
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
            if isinstance(body[0].value.value, str):
                body = body[1:]
        if not body:
            continue
        trivial = all(
            isinstance(s, ast.Pass)
            or (
                isinstance(s, ast.Expr)
                and isinstance(s.value, ast.Constant)
                and s.value.value is Ellipsis
            )
            for s in body
        )
        if not trivial:
            continue
        t = node.type
        broad = t is None or (isinstance(t, ast.Name) and t.id in ("Exception", "BaseException"))
        out.append((node.lineno, broad))
    return out


def run(
    diffs: List[FileDiff],
    sources: Dict[str, str],
    evidence_cmd: str,
) -> CheckResult:
    res = CheckResult(NAME, TITLE)

    for fd in diffs:
        if fd.binary or fd.status == "D":
            continue
        added = fd.added_lines
        if not added:
            continue
        src = sources.get(fd.path)
        lines = src.split("\n") if src is not None else []
        lang = language(fd.path)

        seen_lines = set()

        for lineno, text in sorted(added.items()):
            body = _strip_comment_only(text)
            for pat, label, why, fix in _SKIP_PATTERNS:
                if pat.search(body):
                    res.add(
                        Finding(
                            NAME, WARN, "test disabled: %s" % label, fd.path, lineno,
                            _snip(lines, lineno) or ["%d\t%s" % (lineno, body.rstrip())],
                            why=why, fix=fix, command=evidence_cmd,
                            extra={"kind": "skip", "marker": label},
                        )
                    )
                    seen_lines.add(lineno)
                    break
            else:
                for pat, label, why, fix, scoped_re in _SUPPRESS_PATTERNS:
                    if not pat.search(body):
                        continue
                    scoped = bool(scoped_re and scoped_re.search(body))
                    res.add(
                        Finding(
                            NAME,
                            INFO if scoped else WARN,
                            "suppression added: %s" % label,
                            fd.path, lineno,
                            _snip(lines, lineno) or ["%d\t%s" % (lineno, body.rstrip())],
                            why=why + (" It names the rule it turns off, so it is at least reviewable." if scoped else ""),
                            fix=fix, command=evidence_cmd,
                            extra={"kind": "suppression", "marker": label, "scoped": scoped},
                        )
                    )
                    seen_lines.add(lineno)
                    break

        # --- swallowed exceptions ------------------------------------------
        if lang == PYTHON and src is not None:
            for lineno, broad in _py_empty_handlers(src):
                if lineno not in added:
                    continue
                res.add(
                    Finding(
                        NAME, WARN, "exception swallowed silently", fd.path, lineno,
                        _snip(lines, lineno, 2),
                        why="The handler discards the error, so a failure here looks like success."
                        + (" It also catches everything, including bugs you did not anticipate." if broad else ""),
                        fix="Log it, re-raise it, or narrow the except to the one error you can actually handle.",
                        command=evidence_cmd,
                        extra={"kind": "swallow", "broad": broad},
                    )
                )
        elif lang == JSTS and src is not None:
            masked = _blank_js_noncode(src)
            for m in _JS_EMPTY_CATCH.finditer(masked):
                lineno = _line_of_offset(src, m.start())
                if lineno not in added:
                    continue
                res.add(
                    Finding(
                        NAME, WARN, "exception swallowed silently", fd.path, lineno,
                        _snip(lines, lineno, 2),
                        why="The catch discards the error, so a failure here looks like success.",
                        fix="Log it, rethrow it, or handle the one error you can actually handle.",
                        command=evidence_cmd,
                        extra={"kind": "swallow"},
                    )
                )

        # --- weakened assertions -------------------------------------------
        for hunk in fd.hunks:
            removed = [(n, t) for n, t in hunk.removed if _STRONG.search(t)]
            addeds = [(n, t) for n, t in hunk.added if _WEAK.search(t) and not _STRONG.search(t)]
            if not removed or not addeds:
                continue
            for anum, atext in addeds:
                best: Optional[Tuple[int, str, int]] = None
                a_ids = set(_IDENT.findall(atext))
                for rnum, rtext in removed:
                    shared = a_ids & set(_IDENT.findall(rtext))
                    shared -= {"assert", "self", "expect"}
                    if not shared:
                        continue
                    if best is None or len(shared) > best[2]:
                        best = (rnum, rtext, len(shared))
                if best is None:
                    continue
                res.add(
                    Finding(
                        NAME, FAIL, "assertion weakened", fd.path, anum,
                        ["-\t%s" % best[1].rstrip(), "+\t%s" % atext.rstrip()],
                        why="A specific assertion was replaced with one that accepts many more values, so the test now passes on outputs it used to reject.",
                        fix="Restore the specific assertion; if the expected value changed, change the expected value.",
                        command=evidence_cmd,
                        extra={"kind": "weakened", "before": best[1].strip(), "after": atext.strip()},
                    )
                )

    return res.finalize()


__all__ = ["run", "NAME", "TITLE"]
