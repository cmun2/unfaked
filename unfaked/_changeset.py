"""What the agent changed, however it left it.

A coding agent does not reliably end its turn on a commit. The usual shape is
edit, edit, run tests, edit, "done" -- and a checker that only reads
``HEAD~1..HEAD`` reports nothing at all for that, which reads as approval. So
the unit of inspection is a ChangeSet rather than a commit range:

    CommitRange     an explicit base..head
    WorkingTree     a base against the files on disk, untracked ones included
    Session         a base recorded when the agent started, against disk now

All three answer the same three questions -- what files differ, what each looks
like now, and which commit messages are in scope -- so nothing downstream needs
to know which one it was given.
"""

from __future__ import annotations

import json
import os
from typing import List, Optional, Tuple

from ._git import (
    WORKTREE,
    FileDiff,
    GitError,
    commit_messages,
    diff_files,
    diff_worktree,
    has_parent,
    is_clean,
    rev_parse,
    short,
    show_file,
)

COMMIT_RANGE = "commit-range"
WORKING_TREE = "working-tree"
SESSION = "session"


class ChangeSet:
    __slots__ = ("repo", "base", "head", "kind", "session_started")

    def __init__(
        self,
        repo: str,
        base: str,
        head: str,
        kind: str,
        session_started: Optional[str] = None,
    ) -> None:
        self.repo = repo
        self.base = base
        self.head = head
        self.kind = kind
        self.session_started = session_started

    # --- what is in it ----------------------------------------------------

    @property
    def includes_uncommitted(self) -> bool:
        """Whether the files on disk are part of what is being inspected.

        When they are, `loose-ends` must not also report them as left behind:
        they are the subject, not a leftover.
        """
        return self.head == WORKTREE

    def diffs(self) -> List[FileDiff]:
        if self.head == WORKTREE:
            return diff_worktree(self.repo, self.base)
        return diff_files(self.repo, self.base, self.head)

    def read(self, path: str) -> Optional[str]:
        """The file as it stands at the head of this change set."""
        if self.head == WORKTREE:
            try:
                with open(os.path.join(self.repo, path), "r", encoding="utf-8") as fh:
                    return fh.read()
            except (OSError, UnicodeDecodeError):
                return None
        return show_file(self.repo, self.head, path)

    def messages(self) -> List[Tuple[str, str]]:
        """Commit messages in range. A working tree contributes none of its own."""
        tip = "HEAD" if self.head == WORKTREE else self.head
        try:
            return commit_messages(self.repo, self.base, tip)
        except GitError:
            return []

    # --- how to describe it ----------------------------------------------

    @property
    def label(self) -> str:
        if self.head == WORKTREE:
            where = short(self.repo, self.base)
            if self.kind == SESSION:
                return "working tree since the session began (%s)" % where
            return "working tree vs %s (%s)" % (self.base, where)
        return "%s..%s (%s)" % (self.base, self.head, short(self.repo, self.head))

    @property
    def evidence(self) -> str:
        """A command the reader can paste to see the same diff."""
        if self.head == WORKTREE:
            return "git diff %s" % self.base
        return "git diff %s..%s" % (self.base, self.head)

    def resolved(self) -> Tuple[str, Optional[str]]:
        head = None if self.head == WORKTREE else rev_parse(self.repo, self.head)
        return rev_parse(self.repo, self.base), head


# ---------------------------------------------------------------------------
# resolution
# ---------------------------------------------------------------------------


def resolve(
    repo: str,
    base: Optional[str],
    head: Optional[str],
    session_file: Optional[str],
) -> ChangeSet:
    """Pick the change set from what the caller asked for.

    Naming a `--head` means a commit range and nothing else -- that is a
    deliberate request to look at committed history. Otherwise the files on disk
    are the head whenever they differ from `HEAD`, because that is where an
    agent's work usually is when it stops.
    """
    if session_file:
        started = _read_session(repo, session_file)
        return ChangeSet(repo, started, WORKTREE, SESSION, session_started=started)

    if head is not None:
        chosen_base = base or _parent_of(repo, head)
        return ChangeSet(repo, chosen_base, head, COMMIT_RANGE)

    if not is_clean(repo):
        return ChangeSet(repo, base or "HEAD", WORKTREE, WORKING_TREE)

    if base is not None:
        return ChangeSet(repo, base, "HEAD", COMMIT_RANGE)

    return ChangeSet(repo, _parent_of(repo, "HEAD"), "HEAD", COMMIT_RANGE)


def _parent_of(repo: str, ref: str) -> str:
    if not has_parent(repo, ref):
        raise GitError(
            "%s has no parent commit and the working tree is clean; "
            "pass --base <ref> to pick a range." % ref
        )
    return "%s~1" % ref


# ---------------------------------------------------------------------------
# session markers
# ---------------------------------------------------------------------------


def write_session(repo: str, path: str) -> str:
    """Record where the agent is starting from. Returns the recorded revision."""
    head = rev_parse(repo, "HEAD")
    directory = os.path.dirname(os.path.abspath(path))
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"version": 1, "repo": repo, "head": head}, fh)
    return head


def _read_session(repo: str, path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, ValueError) as exc:
        raise GitError("cannot read the session marker at %s (%s)" % (path, exc)) from exc
    head = payload.get("head")
    if not isinstance(head, str) or not head:
        raise GitError("the session marker at %s has no recorded revision" % path)
    try:
        return rev_parse(repo, head)
    except GitError as exc:
        raise GitError(
            "the session marker points at %s, which is not in this repository" % head[:12]
        ) from exc


__all__ = [
    "COMMIT_RANGE",
    "SESSION",
    "WORKING_TREE",
    "ChangeSet",
    "resolve",
    "write_session",
]
