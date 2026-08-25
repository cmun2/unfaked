"""Check C: what the change left lying around.

None of these prove anything was faked. They are the cheap, unambiguous facts
about the repository that a "done" report should have matched.
"""

import fnmatch
import re
from typing import List, Optional

from ._finding import INFO, WARN, CheckResult, Finding
from ._git import FileDiff
from ._lang import is_build_artifact, is_lockfile, is_test_file

NAME = "loose-ends"
TITLE = "what the change left behind"

_STATUS_WORDS = {
    "??": "untracked",
    " M": "modified, not staged",
    "M ": "staged, not committed",
    "MM": "staged and then modified again",
    " D": "deleted, not staged",
    "D ": "deletion staged, not committed",
    "A ": "added, not committed",
    "AM": "added and then modified",
    " A": "added, not staged",
}

# A commit message claiming test work. Deliberately narrow: it must actually
# say tests were added or written, not merely mention the word.
_TEST_CLAIM = re.compile(
    r"\b(?:"
    r"add(?:ed|s|ing)?\s+(?:\w+\s+){0,3}?tests?"
    r"|tests?\s+add(?:ed)?"
    r"|(?:wrote|writing|write)\s+(?:\w+\s+){0,3}?tests?"
    r"|(?:comprehensive|extensive|thorough|full|unit|integration|regression)\s+tests?"
    r"|tests?\s+(?:coverage|included|are included)"
    r"|with\s+tests?\b"
    r"|covered\s+by\s+tests?"
    r"|test\s+coverage"
    r")\b",
    re.IGNORECASE,
)


def run(
    repo: str,
    diffs: List[FileDiff],
    dirty: List,
    messages: List,
    scope: Optional[List[str]],
    evidence_cmd: str,
) -> CheckResult:
    res = CheckResult(NAME, TITLE)

    # --- uncommitted work -------------------------------------------------
    interesting = [
        (xy, path)
        for xy, path in dirty
        if not is_build_artifact(path)
    ]
    if interesting:
        for xy, path in interesting[:20]:
            label = _STATUS_WORDS.get(xy, xy.strip() or "changed")
            res.add(
                Finding(
                    NAME,
                    WARN,
                    "left uncommitted: %s" % path,
                    path,
                    None,
                    ["%s\t%s" % (xy, path)],
                    why="This file differs from the commit under review (%s), so what was reviewed is not what is on disk."
                    % label,
                    fix="`git add %s` if it belongs to the change, or discard it." % path,
                    command="git status --porcelain",
                    extra={"status": xy},
                )
            )
        if len(interesting) > 20:
            res.note = "%d more uncommitted paths not listed" % (len(interesting) - 20)

    # --- artifacts and lockfiles in the diff ------------------------------
    for fd in diffs:
        if fd.status == "D":
            continue
        if is_build_artifact(fd.path):
            res.add(
                Finding(
                    NAME,
                    WARN,
                    "build output committed: %s" % fd.path,
                    fd.path,
                    None,
                    ["%s\t+%d -%d" % (fd.path, fd.n_added, fd.n_removed)],
                    why="Generated files in a diff hide the real change and go stale immediately.",
                    fix="Remove it from the commit and add the path to .gitignore.",
                    command=evidence_cmd,
                    extra={"kind": "artifact"},
                )
            )
        elif is_lockfile(fd.path):
            res.add(
                Finding(
                    NAME,
                    INFO,
                    "lockfile changed: %s" % fd.path,
                    fd.path,
                    None,
                    ["%s\t+%d -%d" % (fd.path, fd.n_added, fd.n_removed)],
                    why="A dependency moved. That is often intended, and often a side effect nobody asked for.",
                    fix="Confirm the dependency change was part of the task.",
                    command=evidence_cmd,
                    extra={"kind": "lockfile"},
                )
            )

    # --- out of scope -----------------------------------------------------
    if scope:
        for fd in diffs:
            if any(fnmatch.fnmatch(fd.path, pat) for pat in scope):
                continue
            res.add(
                Finding(
                    NAME,
                    WARN,
                    "outside the requested scope: %s" % fd.path,
                    fd.path,
                    None,
                    ["%s\t+%d -%d" % (fd.path, fd.n_added, fd.n_removed)],
                    why="The task was scoped to %s and this file is not in it." % ", ".join(scope),
                    fix="Split the unrelated edit into its own change, or widen --scope.",
                    command=evidence_cmd,
                    extra={"kind": "scope"},
                )
            )

    # --- a commit that claims tests it did not write ----------------------
    changed_tests = [fd for fd in diffs if is_test_file(fd.path) and fd.status != "D"]
    if not changed_tests:
        for sha, msg in messages:
            m = _TEST_CLAIM.search(msg)
            if not m:
                continue
            subject = msg.split("\n", 1)[0]
            res.add(
                Finding(
                    NAME,
                    WARN,
                    "commit claims tests, none were touched",
                    None,
                    None,
                    ["%s\t%s" % (sha[:9], subject), '  claim: "%s"' % m.group(0)],
                    why="No file in this range is a test file, so the claim is not backed by the diff.",
                    fix="Add the tests, or reword the commit so the next reader is not misled.",
                    command="git log --format=%%B -1 %s" % sha,
                    extra={"kind": "claim", "sha": sha},
                )
            )

    return res.finalize()


__all__ = ["run", "NAME", "TITLE"]
