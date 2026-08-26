"""Git plumbing and unified-diff parsing.

Everything unfaked knows about a change comes from here. We never read the
agent's prose; we read the diff and the repository state.
"""

import os
import re
import subprocess
from typing import Dict, List, Optional, Tuple


class GitError(RuntimeError):
    pass


def git(repo: str, *args: str, check: bool = True) -> str:
    proc = subprocess.run(
        ["git", "-C", repo] + list(args),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if check and proc.returncode != 0:
        raise GitError(
            "git %s failed (%d): %s"
            % (" ".join(args), proc.returncode, proc.stderr.decode("utf-8", "replace").strip())
        )
    return proc.stdout.decode("utf-8", "replace")


def toplevel(path: str) -> str:
    out = git(path, "rev-parse", "--show-toplevel").strip()
    if not out:
        raise GitError("not a git repository: %s" % path)
    return out


def rev_parse(repo: str, ref: str) -> str:
    return git(repo, "rev-parse", ref).strip()


def short(repo: str, ref: str) -> str:
    return git(repo, "rev-parse", "--short", ref).strip()


def repo_name(repo: str) -> str:
    return os.path.basename(os.path.abspath(repo))


def has_parent(repo: str, ref: str = "HEAD") -> bool:
    try:
        git(repo, "rev-parse", "--verify", "%s^" % ref)
        return True
    except GitError:
        return False


def merge_base(repo: str, a: str, b: str) -> Optional[str]:
    try:
        return git(repo, "merge-base", a, b).strip()
    except GitError:
        return None


def commit_messages(repo: str, base: str, head: str) -> List[Tuple[str, str]]:
    """[(sha, full message)] for base..head, newest first."""
    sep = "\x1e"
    out = git(repo, "log", "--format=%%H%s%%B%s" % (sep, "\x1d"), "%s..%s" % (base, head))
    entries = []
    for chunk in out.split("\x1d"):
        chunk = chunk.strip("\n")
        if not chunk.strip():
            continue
        sha, _, body = chunk.partition(sep)
        entries.append((sha.strip(), body.strip()))
    return entries


def show_file(repo: str, ref: str, path: str) -> Optional[str]:
    proc = subprocess.run(
        ["git", "-C", repo, "show", "%s:%s" % (ref, path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    if proc.returncode != 0:
        return None
    return proc.stdout.decode("utf-8", "replace")


def porcelain_status(repo: str) -> List[Tuple[str, str]]:
    """[(xy, path)] from `git status --porcelain`, excluding ignored files."""
    out = git(repo, "status", "--porcelain")
    rows = []
    for line in out.splitlines():
        if not line.strip():
            continue
        xy, path = line[:2], line[3:]
        # Renames render as "old -> new"; the destination is what matters.
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        rows.append((xy, path.strip().strip('"')))
    return rows


def is_clean(repo: str) -> bool:
    return not porcelain_status(repo)


# ---------------------------------------------------------------------------
# unified diff
# ---------------------------------------------------------------------------

_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


class Hunk:
    """One @@ block. `lines` is [(kind, old_lineno, new_lineno, text)]."""

    __slots__ = ("old_start", "new_start", "lines")

    def __init__(self, old_start: int, new_start: int) -> None:
        self.old_start = old_start
        self.new_start = new_start
        self.lines: List[Tuple[str, Optional[int], Optional[int], str]] = []

    @property
    def added(self) -> List[Tuple[int, str]]:
        return [(n, t) for k, _o, n, t in self.lines if k == "+" and n is not None]

    @property
    def removed(self) -> List[Tuple[int, str]]:
        return [(o, t) for k, o, _n, t in self.lines if k == "-" and o is not None]


class FileDiff:
    __slots__ = ("path", "old_path", "status", "binary", "hunks")

    def __init__(self, path: str, old_path: Optional[str], status: str) -> None:
        self.path = path
        self.old_path = old_path
        self.status = status  # A / M / D / R
        self.binary = False
        self.hunks: List[Hunk] = []

    @property
    def added_lines(self) -> Dict[int, str]:
        out: Dict[int, str] = {}
        for h in self.hunks:
            for n, t in h.added:
                out[n] = t
        return out

    @property
    def removed_lines(self) -> Dict[int, str]:
        out: Dict[int, str] = {}
        for h in self.hunks:
            for n, t in h.removed:
                out[n] = t
        return out

    @property
    def n_added(self) -> int:
        return sum(len(h.added) for h in self.hunks)

    @property
    def n_removed(self) -> int:
        return sum(len(h.removed) for h in self.hunks)


def parse_diff(text: str) -> List[FileDiff]:
    files: List[FileDiff] = []
    cur: Optional[FileDiff] = None
    hunk: Optional[Hunk] = None
    old_no = new_no = 0
    pending_old: Optional[str] = None
    pending_status = "M"

    def flush() -> None:
        nonlocal cur, hunk
        if cur is not None:
            files.append(cur)
        cur = None
        hunk = None

    for line in text.split("\n"):
        if line.startswith("diff --git "):
            flush()
            pending_old = None
            pending_status = "M"
            m = re.match(r"^diff --git a/(.*) b/(.*)$", line)
            path = m.group(2) if m else line
            cur = FileDiff(path, None, "M")
            continue
        if cur is None:
            continue
        if line.startswith("new file mode"):
            cur.status = "A"
            continue
        if line.startswith("deleted file mode"):
            cur.status = "D"
            continue
        if line.startswith("rename from "):
            cur.old_path = line[len("rename from ") :]
            cur.status = "R"
            continue
        if line.startswith("Binary files") or line.startswith("GIT binary patch"):
            cur.binary = True
            continue
        m = _HUNK_RE.match(line)
        if m:
            old_no = int(m.group(1))
            new_no = int(m.group(3))
            hunk = Hunk(old_no, new_no)
            cur.hunks.append(hunk)
            continue
        if hunk is None:
            continue
        if line.startswith("+"):
            hunk.lines.append(("+", None, new_no, line[1:]))
            new_no += 1
        elif line.startswith("-"):
            hunk.lines.append(("-", old_no, None, line[1:]))
            old_no += 1
        elif line.startswith(" "):
            hunk.lines.append((" ", old_no, new_no, line[1:]))
            old_no += 1
            new_no += 1
        elif line.startswith("\\"):
            continue

    flush()
    del pending_old, pending_status
    return files


WORKTREE = "<worktree>"
"""Stand-in for a revision, meaning the files as they are on disk right now."""


def diff_worktree(repo: str, base: str, context: int = 0) -> List[FileDiff]:
    """`base` against the working tree, untracked files included.

    An agent that has not committed still changed something, and a new test file
    it has not staged is exactly the thing worth looking at. `git diff` alone
    never mentions those, so they are added here as whole-file additions.
    """
    tracked = parse_diff(
        git(
            repo,
            "diff",
            "-U%d" % context,
            "--no-color",
            "--no-ext-diff",
            "--find-renames",
            base,
        )
    )
    seen = {fd.path for fd in tracked}
    untracked = []
    for xy, path in porcelain_status(repo):
        if xy != "??" or path in seen or path.endswith("/"):
            continue
        full = os.path.join(repo, path)
        try:
            with open(full, "r", encoding="utf-8") as fh:
                text = fh.read()
        except (OSError, UnicodeDecodeError):
            continue
        fd = FileDiff(path, None, "A")
        hunk = Hunk(0, 1)
        for i, line in enumerate(text.split("\n"), start=1):
            hunk.lines.append(("+", None, i, line))
        fd.hunks.append(hunk)
        untracked.append(fd)
    return tracked + untracked


def diff_files(repo: str, base: str, head: str, context: int = 0) -> List[FileDiff]:
    text = git(
        repo,
        "diff",
        "-U%d" % context,
        "--no-color",
        "--no-ext-diff",
        "--find-renames",
        "%s..%s" % (base, head),
    )
    return parse_diff(text)
