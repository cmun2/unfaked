"""Check A (static): tests that cannot fail.

A test that has no assertion, asserts a tautology, or asserts a mock's own
return value will pass no matter what the source does. Counting green dots
never catches these.
"""

import ast
import re
from typing import Dict, List, Optional, Set, Tuple

from ._finding import FAIL, WARN, CheckResult, Finding
from ._lang import JSTS, PYTHON, TestFn

NAME = "hollow-tests"
TITLE = "tests that cannot fail"

# Anything whose call target contains one of these is treated as an assertion.
_ASSERT_HINTS = ("assert", "raises", "expect", "should", "verify", "check_that", "fail")

_PY_ASSERT_METHODS = re.compile(r"^(assert|fail)[A-Za-z_]*$")


def _dump(node: ast.AST) -> str:
    return ast.dump(node, annotate_fields=False)


def _is_pure(node: ast.AST) -> bool:
    """True if re-evaluating the expression obviously yields the same thing."""
    if isinstance(node, (ast.Name, ast.Constant)):
        return True
    if isinstance(node, ast.Attribute):
        return _is_pure(node.value)
    if isinstance(node, ast.Subscript):
        return _is_pure(node.value)
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return all(_is_pure(e) for e in node.elts)
    if isinstance(node, ast.Dict):
        return all(k is not None and _is_pure(k) for k in node.keys) and all(
            _is_pure(v) for v in node.values
        )
    if isinstance(node, ast.UnaryOp):
        return _is_pure(node.operand)
    return False


def _func_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _dotted(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return (base + "." if base else "") + node.attr
    if isinstance(node, ast.Call):
        return _dotted(node.func)
    return ""


def _root_name(node: ast.AST) -> str:
    while isinstance(node, (ast.Attribute, ast.Subscript, ast.Call)):
        node = node.value if not isinstance(node, ast.Call) else node.func
    return node.id if isinstance(node, ast.Name) else ""


def _mentions(node: ast.AST, name: str) -> bool:
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name) and sub.id == name:
            return True
    return False


def _py_has_assertion(fn: ast.AST) -> bool:
    for node in ast.walk(fn):
        if isinstance(node, ast.Assert):
            return True
        if isinstance(node, ast.Raise):
            return True
        if isinstance(node, ast.Call):
            name = _func_name(node.func).lower()
            if _PY_ASSERT_METHODS.match(_func_name(node.func) or ""):
                return True
            if any(h in name for h in _ASSERT_HINTS):
                return True
            dotted = _dotted(node.func).lower()
            if any(h in dotted for h in _ASSERT_HINTS):
                return True
        if isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                if any(h in _dotted(item.context_expr).lower() for h in _ASSERT_HINTS):
                    return True
    return False


def _py_body_is_trivial(fn: ast.AST) -> bool:
    """`pass`, `...`, or only a docstring."""
    body = list(getattr(fn, "body", []))
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        if isinstance(body[0].value.value, str):
            body = body[1:]
    if not body:
        return True
    return all(
        isinstance(s, ast.Pass)
        or (
            isinstance(s, ast.Expr)
            and isinstance(s.value, ast.Constant)
            and s.value.value is Ellipsis
        )
        for s in body
    )


def _collect_mock_returns(fn: ast.AST) -> List[Tuple[str, str, str]]:
    """[(root_name, dumped return value, source-ish label)] set inside the test."""
    out: List[Tuple[str, str, str]] = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Attribute) and tgt.attr == "return_value":
                    root = _root_name(tgt.value)
                    if root:
                        out.append((root, _dump(node.value), _dotted(tgt)))
        elif isinstance(node, ast.Call):
            fname = _dotted(node.func)
            if not (
                "Mock" in fname
                or fname.endswith("patch")
                or fname.endswith("patch.object")
                or "stub" in fname.lower()
            ):
                continue
            for kw in node.keywords:
                if kw.arg == "return_value":
                    out.append(("", _dump(kw.value), fname))
    return out


class _PyAsserts:
    """Every assertion in a python test, normalised to (left, right, kind, line)."""

    def __init__(self, fn: ast.AST) -> None:
        self.items: List[Tuple[Optional[ast.AST], Optional[ast.AST], str, int, str]] = []
        for node in ast.walk(fn):
            if isinstance(node, ast.Assert):
                t = node.test
                if isinstance(t, ast.Compare) and len(t.ops) == 1:
                    kind = type(t.ops[0]).__name__
                    self.items.append((t.left, t.comparators[0], kind, node.lineno, "assert"))
                else:
                    self.items.append((t, None, "truthy", node.lineno, "assert"))
            elif isinstance(node, ast.Call):
                name = _func_name(node.func)
                if not _PY_ASSERT_METHODS.match(name or ""):
                    continue
                args = [a for a in node.args if not isinstance(a, ast.Starred)]
                if name in ("assertEqual", "assertEquals", "assertIs", "assertNotEqual") and len(args) >= 2:
                    self.items.append((args[0], args[1], name, node.lineno, name))
                elif name in ("assertTrue", "assertFalse") and len(args) >= 1:
                    self.items.append((args[0], None, name, node.lineno, name))


def _check_python_test(
    t: TestFn, lines: List[str], res: CheckResult, evidence_cmd: str
) -> None:
    fn = t.node
    assert isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))

    def snip(line: int, span: int = 1) -> List[str]:
        return _snippet(lines, line, span)

    if _py_body_is_trivial(fn):
        res.add(
            Finding(
                NAME,
                FAIL,
                "test body is empty: %s" % t.name,
                t.path,
                t.lineno,
                snip(t.lineno, (t.end_lineno - t.lineno) + 1),
                why="An empty test passes unconditionally; it proves nothing about the change.",
                fix="Give it a real assertion, or delete it so the count stops lying.",
                command=evidence_cmd,
                extra={"test": t.qualname},
            )
        )
        return

    if not _py_has_assertion(fn):
        res.add(
            Finding(
                NAME,
                WARN,
                "no assertion in added test: %s" % t.name,
                t.path,
                t.lineno,
                snip(t.lineno, min(6, (t.end_lineno - t.lineno) + 1)),
                why="It can only fail if something raises, so it is a smoke test, not a check of behaviour.",
                fix="Assert the value the change is supposed to produce, or rename it so nobody counts it as coverage.",
                command=evidence_cmd,
                extra={"test": t.qualname},
            )
        )

    mock_returns = _collect_mock_returns(fn)
    mock_dumps = {d: (root, label) for root, d, label in mock_returns}

    for left, right, kind, line, _src in _PyAsserts(fn).items:
        if left is None:
            continue
        if kind == "truthy":
            if isinstance(left, ast.Constant) and bool(left.value):
                res.add(
                    Finding(
                        NAME,
                        FAIL,
                        "tautological assertion in %s" % t.name,
                        t.path,
                        line,
                        snip(line),
                        why="`assert <truthy constant>` can never fail.",
                        fix="Assert the value under test instead of a literal.",
                        command=evidence_cmd,
                        extra={"test": t.qualname},
                    )
                )
            continue
        if kind in ("assertTrue", "assertFalse"):
            if isinstance(left, ast.Constant):
                res.add(
                    Finding(
                        NAME,
                        FAIL,
                        "tautological assertion in %s" % t.name,
                        t.path,
                        line,
                        snip(line),
                        why="`%s(<constant>)` has a fixed outcome regardless of the code under test."
                        % kind,
                        fix="Assert the value under test instead of a literal.",
                        command=evidence_cmd,
                        extra={"test": t.qualname},
                    )
                )
            continue
        if right is None:
            continue

        same = _dump(left) == _dump(right)
        if same and kind in ("Eq", "Is", "assertEqual", "assertEquals", "assertIs"):
            severity = FAIL if (_is_pure(left) and _is_pure(right)) else WARN
            res.add(
                Finding(
                    NAME,
                    severity,
                    "assertion compares an expression to itself in %s" % t.name,
                    t.path,
                    line,
                    snip(line),
                    why="Both sides of the comparison are the same expression, so the assertion holds no matter what the code does."
                    if severity == FAIL
                    else "Both sides are the same call, so this only proves the call is deterministic, not that it is correct.",
                    fix="Compare the result against the value you expect, spelled out literally.",
                    command=evidence_cmd,
                    extra={"test": t.qualname},
                )
            )
            continue

        if kind in ("Eq", "assertEqual", "assertEquals"):
            for side, other in ((left, right), (right, left)):
                hit = mock_dumps.get(_dump(side))
                if hit is None:
                    continue
                root, label = hit
                if root and not _mentions(other, root):
                    continue
                res.add(
                    Finding(
                        NAME,
                        WARN,
                        "assertion checks a mock's own return value in %s" % t.name,
                        t.path,
                        line,
                        snip(line),
                        why="`%s` was configured in this test, so the assertion re-reads the value the test just wrote."
                        % label,
                        fix="Assert against the real collaborator, or assert the call arguments the mock received.",
                        command=evidence_cmd,
                        extra={"test": t.qualname},
                    )
                )
                break


# --- JS/TS ------------------------------------------------------------------

_JS_EXPECT = re.compile(
    r"""expect\s*\(\s*(?P<subj>(?:[^()]|\([^()]*\))*?)\s*\)\s*(?:\.\s*(?:resolves|rejects)\s*)?"""
    r"""(?:\.\s*not\s*)?\.\s*(?P<matcher>toBe|toEqual|toStrictEqual)\s*\(\s*(?P<exp>(?:[^()]|\([^()]*\))*?)\s*\)"""
)
_JS_ASSERTISH = re.compile(
    r"\b(expect|assert|should|chai|\.rejects|\.resolves|fail\()\b|\bexpect\s*\("
)


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s)


def _check_jsts_test(t: TestFn, lines: List[str], res: CheckResult, evidence_cmd: str) -> None:
    open_b, close_b = t.node  # offsets into the source
    body_lines = lines[t.body_start - 1 : t.end_lineno]
    body = "\n".join(body_lines)
    stripped = re.sub(r"//[^\n]*", "", body)
    stripped = re.sub(r"/\*[\s\S]*?\*/", "", stripped)

    if not stripped.strip().strip("{}").strip():
        res.add(
            Finding(
                NAME,
                FAIL,
                "test body is empty: %s" % t.name,
                t.path,
                t.lineno,
                _snippet(lines, t.lineno, (t.end_lineno - t.lineno) + 1),
                why="An empty test passes unconditionally; it proves nothing about the change.",
                fix="Give it a real expectation, or delete it so the count stops lying.",
                command=evidence_cmd,
                extra={"test": t.qualname},
            )
        )
        return

    if not _JS_ASSERTISH.search(stripped):
        res.add(
            Finding(
                NAME,
                WARN,
                "no expectation in added test: %s" % t.name,
                t.path,
                t.lineno,
                _snippet(lines, t.lineno, min(6, (t.end_lineno - t.lineno) + 1)),
                why="It can only fail if something throws, so it is a smoke test, not a check of behaviour.",
                fix="Add an `expect(...)` for the value the change is supposed to produce.",
                command=evidence_cmd,
                extra={"test": t.qualname},
            )
        )
        return

    for m in _JS_EXPECT.finditer(stripped):
        subj, exp = m.group("subj"), m.group("exp")
        line = t.body_start + stripped[: m.start()].count("\n")
        if _norm(subj) and _norm(subj) == _norm(exp):
            res.add(
                Finding(
                    NAME,
                    FAIL,
                    "expectation compares an expression to itself in %s" % t.name,
                    t.path,
                    line,
                    _snippet(lines, line),
                    why="Both sides of `%s` are the same expression, so it holds no matter what the code does."
                    % m.group("matcher"),
                    fix="Compare against the value you expect, spelled out literally.",
                    command=evidence_cmd,
                    extra={"test": t.qualname},
                )
            )
        elif _norm(subj) in ("true", "false", "1", "0") and _norm(subj) == _norm(exp):
            res.add(
                Finding(
                    NAME,
                    FAIL,
                    "tautological expectation in %s" % t.name,
                    t.path,
                    line,
                    _snippet(lines, line),
                    why="`expect(%s).%s(%s)` has a fixed outcome." % (subj, m.group("matcher"), exp),
                    fix="Assert the value under test instead of a literal.",
                    command=evidence_cmd,
                    extra={"test": t.qualname},
                )
            )


def _snippet(lines: List[str], line: int, span: int = 1) -> List[str]:
    out = []
    for i in range(line, min(line + span, len(lines) + 1)):
        if 1 <= i <= len(lines):
            out.append("%d\t%s" % (i, lines[i - 1].rstrip()))
    return out


def run(added_tests: List[TestFn], sources: Dict[str, str], evidence_cmd: str) -> CheckResult:
    res = CheckResult(NAME, TITLE)
    for t in added_tests:
        src = sources.get(t.path)
        if src is None:
            continue
        lines = src.split("\n")
        if t.kind == PYTHON:
            _check_python_test(t, lines, res, evidence_cmd)
        elif t.kind == JSTS:
            _check_jsts_test(t, lines, res, evidence_cmd)
    if not added_tests:
        res.note = "no tests were added in this range"
    return res.finalize()


__all__ = ["run", "NAME", "TITLE"]
