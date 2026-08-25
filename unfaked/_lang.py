"""Language detection, test-file classification, and test discovery.

Python is handled with the stdlib `ast` module, so it is exact.
JavaScript/TypeScript is handled with a small brace-matching scanner, which is
approximate; anything the scanner is not sure about is simply not reported.
"""

import ast
import fnmatch
import os
import re
from typing import List, Optional, Tuple

PYTHON = "python"
JSTS = "jsts"
OTHER = "other"

_PY_EXT = (".py", ".pyi")
_JSTS_EXT = (".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts")


def language(path: str) -> str:
    low = path.lower()
    if low.endswith(_PY_EXT):
        return PYTHON
    if low.endswith(_JSTS_EXT):
        return JSTS
    return OTHER


_TEST_PATH_PATTERNS = (
    "test_*.py",
    "*_test.py",
    "conftest.py",
    "*.test.js",
    "*.test.jsx",
    "*.test.ts",
    "*.test.tsx",
    "*.test.mjs",
    "*.spec.js",
    "*.spec.jsx",
    "*.spec.ts",
    "*.spec.tsx",
    "*.spec.mjs",
    "*_test.go",
    "*_test.rb",
    "*Test.java",
    "*Tests.cs",
    "*.test.rs",
)

_TEST_DIR_NAMES = {"tests", "test", "__tests__", "spec", "specs", "testing", "e2e"}


def is_test_file(path: str) -> bool:
    base = os.path.basename(path)
    for pat in _TEST_PATH_PATTERNS:
        if fnmatch.fnmatch(base, pat):
            return True
    parts = set(p.lower() for p in path.replace("\\", "/").split("/")[:-1])
    if parts & _TEST_DIR_NAMES:
        # A file inside tests/ counts as a test file only if it looks like code.
        return language(path) in (PYTHON, JSTS) or base.endswith(
            (".go", ".rs", ".rb", ".java", ".cs")
        )
    return False


_ARTIFACT_PATTERNS = (
    "dist/*",
    "*/dist/*",
    "build/*",
    "*/build/*",
    "out/*",
    "*/out/*",
    "node_modules/*",
    "*/node_modules/*",
    "*.min.js",
    "*.min.css",
    "*.pyc",
    "*.pyo",
    "*.so",
    "*.egg-info/*",
    "__pycache__/*",
    "*/__pycache__/*",
    ".venv/*",
    "*/.venv/*",
    "coverage/*",
    "*/coverage/*",
    "target/debug/*",
    "target/release/*",
)

_LOCKFILES = {
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "bun.lockb",
    "poetry.lock",
    "uv.lock",
    "Pipfile.lock",
    "Cargo.lock",
    "composer.lock",
    "Gemfile.lock",
    "go.sum",
}


_ARTIFACT_DIRS = {
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".venv",
    "dist",
    "build",
    "coverage",
    ".next",
    ".turbo",
}


def is_build_artifact(path: str) -> bool:
    p = path.replace("\\", "/").rstrip("/")
    if set(p.split("/")) & _ARTIFACT_DIRS:
        return True
    return any(fnmatch.fnmatch(p, pat) for pat in _ARTIFACT_PATTERNS)


def is_lockfile(path: str) -> bool:
    return os.path.basename(path) in _LOCKFILES


# ---------------------------------------------------------------------------
# test discovery
# ---------------------------------------------------------------------------


class TestFn:
    """A test discovered in a file, with the line range of its body."""

    __slots__ = ("path", "name", "qualname", "lineno", "end_lineno", "body_start", "node", "kind")

    def __init__(
        self,
        path: str,
        name: str,
        qualname: str,
        lineno: int,
        end_lineno: int,
        body_start: int,
        node: object = None,
        kind: str = PYTHON,
    ) -> None:
        self.path = path
        self.name = name
        self.qualname = qualname  # "Class::name" for python, "describe > it" for js
        self.lineno = lineno  # line of `def` / `it(`
        self.end_lineno = end_lineno
        self.body_start = body_start
        self.node = node
        self.kind = kind

    @property
    def nodeid(self) -> str:
        if self.kind == PYTHON:
            return "%s::%s" % (self.path, self.qualname.replace(".", "::"))
        return "%s::%s" % (self.path, self.qualname)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "<TestFn %s:%d %s>" % (self.path, self.lineno, self.qualname)


def python_tests(path: str, source: str) -> List[TestFn]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    out: List[TestFn] = []

    def visit(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                visit(child, prefix + child.name + ".")
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if child.name.startswith("test") or prefix:
                    if not child.name.startswith("test"):
                        continue
                    end = getattr(child, "end_lineno", None) or child.lineno
                    body_start = child.body[0].lineno if child.body else child.lineno
                    out.append(
                        TestFn(
                            path,
                            child.name,
                            prefix + child.name,
                            child.lineno,
                            end,
                            body_start,
                            child,
                            PYTHON,
                        )
                    )

    visit(tree, "")
    return out


# --- JS/TS ------------------------------------------------------------------

_JS_TEST_CALL = re.compile(
    r"""\b(?P<fn>it|test)(?P<mod>\.(?:only|skip|todo|concurrent|failing|each))?\s*\(\s*"""
    r"""(?P<q>['"`])(?P<title>(?:\\.|(?!(?P=q))[\s\S])*)(?P=q)""",
)
_JS_DESCRIBE = re.compile(
    r"""\bdescribe(?:\.(?:only|skip|each))?\s*\(\s*(?P<q>['"`])(?P<title>(?:\\.|(?!(?P=q))[\s\S])*)(?P=q)"""
)


def _blank_noncode(src: str, comments: bool = True) -> str:
    """Replace string (and optionally comment) bodies with spaces.

    Used two ways: with comments blanked, so brace matching is sane; and with
    them kept, so a suppression comment is still visible while the same text
    inside a string literal is not.

    Newlines are preserved so every offset keeps its original line number.
    Regex literals are not handled; a file that trips the scanner just yields
    fewer tests, which is the safe direction.
    """
    out = list(src)
    i = 0
    n = len(src)
    while i < n:
        c = src[i]
        if comments and c == "/" and i + 1 < n and src[i + 1] == "/":
            j = src.find("\n", i)
            j = n if j == -1 else j
            for k in range(i, j):
                out[k] = " "
            i = j
        elif comments and c == "/" and i + 1 < n and src[i + 1] == "*":
            j = src.find("*/", i + 2)
            j = n if j == -1 else j + 2
            for k in range(i, j):
                if out[k] != "\n":
                    out[k] = " "
            i = j
        elif c in "'\"`":
            quote = c
            j = i + 1
            while j < n:
                if src[j] == "\\":
                    j += 2
                    continue
                if src[j] == quote:
                    j += 1
                    break
                if quote != "`" and src[j] == "\n":
                    break
                j += 1
            for k in range(i + 1, min(j - 1, n)):
                if out[k] != "\n":
                    out[k] = " "
            i = j
        else:
            i += 1
    return "".join(out)


def _match_block(masked: str, start: int) -> Optional[Tuple[int, int]]:
    """Given an index at/near `(`, return (body_open, body_close) of the callback."""
    depth = 0
    i = start
    n = len(masked)
    open_brace = -1
    while i < n:
        c = masked[i]
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth <= 0:
                return None
        elif c == "{" and depth >= 1:
            open_brace = i
            break
        i += 1
    if open_brace < 0:
        return None
    depth = 0
    i = open_brace
    while i < n:
        if masked[i] == "{":
            depth += 1
        elif masked[i] == "}":
            depth -= 1
            if depth == 0:
                return (open_brace, i)
        i += 1
    return None


def jsts_tests(path: str, source: str) -> List[TestFn]:
    masked = _blank_noncode(source)
    line_of = _line_index(source)

    describes: List[Tuple[int, int, str]] = []
    for m in _JS_DESCRIBE.finditer(masked):
        # titles were masked out; recover them from the original text
        title = source[m.start("title") : m.end("title")]
        block = _match_block(masked, m.start())
        if block:
            describes.append((block[0], block[1], title))

    out: List[TestFn] = []
    for m in _JS_TEST_CALL.finditer(masked):
        # skip `it` that is part of a longer identifier or a property access
        pre = masked[: m.start("fn")]
        if pre.rstrip().endswith("."):
            continue
        title = source[m.start("title") : m.end("title")]
        block = _match_block(masked, m.start("fn"))
        if not block:
            continue
        open_b, close_b = block
        crumbs = [t for s, e, t in describes if s < m.start() < e]
        qual = " > ".join(crumbs + [title])
        out.append(
            TestFn(
                path,
                title,
                qual,
                line_of(m.start("fn")),
                line_of(close_b),
                line_of(open_b),
                (open_b, close_b),
                JSTS,
            )
        )
    return out


def _line_index(src: str):
    starts = [0]
    for i, ch in enumerate(src):
        if ch == "\n":
            starts.append(i + 1)

    def line_of(offset: int) -> int:
        lo, hi = 0, len(starts) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if starts[mid] <= offset:
                lo = mid
            else:
                hi = mid - 1
        return lo + 1

    return line_of


_CODE_EXTS = _PY_EXT + _JSTS_EXT + (
    ".go", ".rs", ".rb", ".java", ".kt", ".kts", ".cs", ".swift", ".scala",
    ".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".m", ".mm",
    ".php", ".ex", ".exs", ".erl", ".dart", ".sh", ".bash", ".zsh",
)


def is_code(path: str) -> bool:
    """True for files where a suppression comment means something.

    Markdown, JSON and the like are excluded: `# noqa` in a README is prose
    about a suppression, not a suppression.
    """
    return path.lower().endswith(_CODE_EXTS)


def blank_string_literals(path: str, source: str) -> str:
    """Return `source` with string contents replaced by spaces, comments kept.

    A pattern that only matches inside a string literal is data -- a fixture, a
    regex, a message -- not a suppression someone added to their own code.
    Offsets and line numbers are preserved, so a match position in the result
    addresses the same place in the original.
    """
    lang = language(path)
    if lang == JSTS:
        return _blank_noncode(source, comments=False)
    if lang != PYTHON:
        return source
    try:
        import io
        import tokenize

        out = list(source)
        lines = source.split("\n")
        starts = [0]
        for ln in lines[:-1]:
            starts.append(starts[-1] + len(ln) + 1)
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type != tokenize.STRING and getattr(tokenize, "FSTRING_START", None) != tok.type:
                continue
            (r1, c1), (r2, c2) = tok.start, tok.end
            begin = starts[r1 - 1] + c1
            end = starts[r2 - 1] + c2
            for k in range(begin, min(end, len(out))):
                if out[k] != "\n":
                    out[k] = " "
        return "".join(out)
    except Exception:
        # A file we cannot tokenise is left alone; the checks then behave as
        # they did before, which is noisier but never wrong in the other
        # direction.
        return source


def discover_tests(path: str, source: str) -> List[TestFn]:
    lang = language(path)
    if lang == PYTHON:
        return python_tests(path, source)
    if lang == JSTS:
        return jsts_tests(path, source)
    return []
