"""A throwaway checkout to run the counterfactual in.

The probe asks what the added tests do against the old source. Answering that by
editing the repository and putting it back works, but it makes the checker the
one thing in the room that mutates what it is checking -- and it cannot run at
all while the tree is dirty, which is most of the time when an agent has just
stopped.

So the old source is checked out somewhere else instead. `git worktree` gives a
real working copy of the base revision sharing the same object store: cheap, and
nothing about the original is touched. The tests being probed are copied in on
top, because they are the one thing that must come from the change rather than
from the baseline.

Tooling stays where it is. The interpreter or test binary is used by absolute
path from the original checkout, and `node_modules` is linked rather than
installed again -- resolution walks up from the working directory, which is now
the shadow.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from typing import Iterator, List, Optional

from ._git import GitError, git

LINKED_DIRECTORIES = ("node_modules",)
"""Directories borrowed from the original rather than recreated.

Read-only during a test run, and reinstalling them would cost more than the
probe itself.
"""


class Shadow:
    """A checkout of `base` with the changed tests laid over it."""

    __slots__ = ("path", "_repo", "_linked")

    def __init__(self, path: str, repo: str) -> None:
        self.path = path
        self._repo = repo
        self._linked: List[str] = []

    def lay_over(self, files: dict) -> None:
        """Write `{relative path: contents}`, creating parents as needed."""
        for relative, text in files.items():
            if text is None:
                continue
            destination = os.path.join(self.path, relative)
            os.makedirs(os.path.dirname(destination) or self.path, exist_ok=True)
            with open(destination, "w", encoding="utf-8") as fh:
                fh.write(text)

    def link_tooling(self) -> None:
        for name in LINKED_DIRECTORIES:
            source = os.path.join(self._repo, name)
            if not os.path.isdir(source):
                continue
            destination = os.path.join(self.path, name)
            if os.path.exists(destination):
                continue
            try:
                os.symlink(source, destination)
                self._linked.append(destination)
            except OSError:
                # Not fatal: the runner may not need it, and if it does the
                # failure surfaces as a runner error rather than a wrong verdict.
                pass


def _remove(repo: str, path: str) -> None:
    try:
        git(repo, "worktree", "remove", "--force", path)
    except GitError:
        shutil.rmtree(path, ignore_errors=True)
    try:
        git(repo, "worktree", "prune")
    except GitError:
        pass


class shadow_workspace:  # noqa: N801 - used as a context manager, reads as one
    """Context manager yielding a `Shadow`, or raising `GitError` if unavailable."""

    def __init__(self, repo: str, base: str) -> None:
        self._repo = repo
        self._base = base
        self._path: Optional[str] = None

    def __enter__(self) -> Shadow:
        parent = tempfile.mkdtemp(prefix="unfaked-shadow-")
        self._path = os.path.join(parent, "tree")
        try:
            git(self._repo, "worktree", "add", "--detach", "--quiet", self._path, self._base)
        except GitError:
            shutil.rmtree(parent, ignore_errors=True)
            self._path = None
            raise
        return Shadow(self._path, self._repo)

    def __exit__(self, *_exc: object) -> None:
        if self._path is None:
            return
        _remove(self._repo, self._path)
        shutil.rmtree(os.path.dirname(self._path), ignore_errors=True)
        self._path = None


def iter_test_paths(tests) -> Iterator[str]:
    seen = set()
    for test in tests:
        if test.path not in seen:
            seen.add(test.path)
            yield test.path


__all__ = ["Shadow", "iter_test_paths", "shadow_workspace"]
