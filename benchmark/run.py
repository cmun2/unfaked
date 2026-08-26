#!/usr/bin/env python3
"""How much of a real change set this can actually judge, and how often it is wrong.

    python benchmark/run.py                 # both corpora, table to stdout
    python benchmark/run.py --json out.json # same numbers, machine-readable

Two corpora, because the two failure modes are different:

  fixtures   Repositories built here, each containing one thing that should be
             reported and, next to it, the honest version of the same edit that
             should not be. Measures whether a check fires, and whether it fires
             when it should not.

  history    This repository's own commits. Real work, so a static check that
             fires here is wrong by construction. Measures how often a verdict
             can be reached at all -- a checker that says "inconclusive" to
             everything is never wrong and never useful.

             The probe is counted separately and not as an error. Saying a test
             passes both ways is a true statement about a control test as much
             as about a hollow one, and this repository writes a lot of
             controls; calling that a false alarm would score the tool down for
             being right.

Nothing is downloaded and nothing outside a temporary directory is written, so
the numbers are reproducible from a clone.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from typing import Dict, List, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

SRC_BEFORE = "def rate(x):\n    return x * 0.1\n"
SRC_AFTER = "def rate(x):\n    return round(x * 0.1, 2)\n"
TEST_BEFORE = "from src.m import rate\n\n\ndef test_rate():\n    assert rate(50) == 5.0\n"


def git(repo: str, *args: str) -> None:
    subprocess.run(
        ["git", "-C", repo, *args], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )


def write(repo: str, rel: str, text: str) -> None:
    full = os.path.join(repo, rel)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as fh:
        fh.write(text)


def build(parent: str, name: str, after: Dict[str, str], message: str, commit: bool) -> str:
    """A repo with one baseline commit, then `after` applied on top."""
    repo = os.path.join(parent, name)
    os.makedirs(repo)
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "agent@example.com")
    git(repo, "config", "user.name", "a coding agent")
    git(repo, "config", "commit.gpgsign", "false")
    write(repo, "src/m.py", SRC_BEFORE)
    write(repo, "tests/test_m.py", TEST_BEFORE)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "initial", "--no-verify")
    for rel, text in after.items():
        write(repo, rel, text)
    if commit:
        git(repo, "add", "-A")
        git(repo, "commit", "-qm", message, "--no-verify")
    return repo


# ---------------------------------------------------------------------------
# fixtures: (name, expected check or None, files, commit?)
# ---------------------------------------------------------------------------

T = "tests/test_m.py"
S = "src/m.py"


def fixtures() -> List[Tuple[str, Optional[str], Dict[str, str], bool]]:
    keep = TEST_BEFORE
    return [
        # --- things that should be reported ---------------------------------
        ("no assertion", "hollow-tests",
         {S: SRC_AFTER, T: keep + "\n\ndef test_smoke():\n    rate(1)\n"}, True),
        ("tautology", "hollow-tests",
         {S: SRC_AFTER, T: keep + "\n\ndef test_t():\n    r = rate(1)\n    assert r == r\n"}, True),
        ("empty test body", "hollow-tests",
         {S: SRC_AFTER, T: keep + "\n\ndef test_todo():\n    pass\n"}, True),
        ("mock asserts itself", "hollow-tests",
         {S: SRC_AFTER, T: keep + "\n\nfrom unittest.mock import Mock\n\n\ndef test_m():\n"
                                  "    c = Mock()\n    c.get.return_value = 7\n    assert c.get() == 7\n"}, True),
        ("skip added", "neutered-checks",
         {S: SRC_AFTER,
          T: "import pytest\nfrom src.m import rate\n\n\n@pytest.mark.skip(reason='flaky')\n"
             "def test_rate():\n    assert rate(50) == 5.0\n"}, True),
        ("suppression added", "neutered-checks",
         {S: "def rate(x):\n    return round(x * 0.1, 2)  # type: ignore\n", T: keep}, True),
        ("exception swallowed", "neutered-checks",
         {S: "def rate(x):\n    try:\n        return round(x * 0.1, 2)\n    except Exception:\n        pass\n",
          T: keep}, True),
        ("assertion weakened", "neutered-checks",
         {S: SRC_AFTER,
          T: "import unittest\nfrom src.m import rate\n\n\nclass T(unittest.TestCase):\n"
             "    def test_rate(self):\n        self.assertTrue(rate(50))\n"}, True),
        ("expectation re-aimed", "neutered-checks",
         {T: "from src.m import rate\n\n\ndef test_rate():\n    assert rate(50) == 5.000001\n"}, True),
        # Reviewing a commit: a file on disk that is not in it means what was
        # reviewed is not what is there. Needs --head, since the default takes
        # the working tree as the subject instead.
        ("left uncommitted", "loose-ends",
         {S: SRC_AFTER, "scratch.md": "TODO\n"}, "commit-then-leave"),

        # --- controls: the honest version of the same edit -------------------
        ("control: real assertion", None,
         {S: SRC_AFTER, T: keep + "\n\ndef test_rounds():\n    assert rate(3) == 0.3\n"}, True),
        ("control: specific assertion kept", None,
         {S: SRC_AFTER,
          T: "import unittest\nfrom src.m import rate\n\n\nclass T(unittest.TestCase):\n"
             "    def test_rate(self):\n        self.assertEqual(rate(50), 5.0)\n"}, True),
        ("control: source moved with the test", None,
         {S: "def rate(x):\n    return round(x * 0.2, 2)\n",
          T: "from src.m import rate\n\n\ndef test_rate():\n    assert rate(50) == 10.0\n"}, True),
        ("control: docs only", None, {"README.md": "# notes\n"}, True),
    ]


# ---------------------------------------------------------------------------


def inspect(repo: str, extra: List[str]) -> Dict:
    """Run the CLI over `repo` and hand back its JSON.

    The report is captured outside the repository. Writing it inside would make
    the tree dirty, and a dirty tree is exactly what the tool now reads -- the
    measurement would be of the measurement.
    """
    from unfaked import cli

    handle, out = tempfile.mkstemp(prefix="unfaked-bench-", suffix=".json")
    os.close(handle)
    argv = [repo, "--json", "--exit-zero"] + extra
    stdout = sys.stdout
    try:
        with open(out, "w", encoding="utf-8") as fh:
            sys.stdout = fh
            try:
                cli.main(argv)
            finally:
                sys.stdout = stdout
        with open(out, "r", encoding="utf-8") as fh:
            return json.load(fh)
    finally:
        os.unlink(out)


def reported(payload: Dict) -> Dict[str, List[str]]:
    by_check: Dict[str, List[str]] = {}
    for check in payload.get("checks", []):
        for finding in check.get("findings", []):
            if str(finding.get("severity", "")).lower() in ("fail", "warn"):
                by_check.setdefault(check["check"], []).append(finding["title"])
    return by_check


def run_fixtures(parent: str) -> Dict:
    rows, caught, missed, false_alarms = [], 0, 0, 0
    for index, (name, expected, files, commit) in enumerate(fixtures()):
        leave_behind = commit == "commit-then-leave"
        repo = build(
            parent,
            "fx%02d" % index,
            {} if leave_behind else files,
            "the agent's change",
            commit is True,
        )
        extra = ["--skip", "revert-probe"]
        if leave_behind:
            # Commit the work, then drop the scratch file beside it.
            for rel, text in files.items():
                if rel == "scratch.md":
                    continue
                write(repo, rel, text)
            git(repo, "add", "-A")
            git(repo, "commit", "-qm", "the agent's change", "--no-verify")
            write(repo, "scratch.md", files["scratch.md"])
            extra += ["--head", "HEAD"]
        payload = inspect(repo, extra)
        by_check = reported(payload)
        if expected is None:
            ok = not by_check
            false_alarms += 0 if ok else 1
        else:
            ok = expected in by_check
            caught += 1 if ok else 0
            missed += 0 if ok else 1
        rows.append(
            {
                "name": name,
                "expects": expected,
                "reported": sorted(by_check),
                "ok": ok,
            }
        )
    total_expected = sum(1 for _n, e, _f, _c in fixtures() if e is not None)
    controls = len(fixtures()) - total_expected
    return {
        "rows": rows,
        "expected": total_expected,
        "caught": caught,
        "missed": missed,
        "controls": controls,
        "false_alarms": false_alarms,
    }


def run_history(limit: int, deep: bool) -> Dict:
    revs = (
        subprocess.run(
            ["git", "-C", ROOT, "rev-list", "--max-count=%d" % limit, "HEAD"],
            capture_output=True,
            check=True,
        )
        .stdout.decode()
        .split()
    )
    rows, fails, evaluable, inconclusive, no_tests, nondist = [], 0, 0, 0, 0, 0
    for rev in revs:
        # The root commit has no parent, so there is no change to read.
        if subprocess.run(
            ["git", "-C", ROOT, "rev-parse", "--verify", "--quiet", rev + "^"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode:
            continue
        payload = inspect(ROOT, ["--head", rev] + (["--deep"] if deep else ["--skip", "revert-probe"]))
        probe = next((c for c in payload["checks"] if c["check"] == "revert-probe"), {})
        status = probe.get("status", "skipped")
        # Only the static checks can be wrong here. A probe FAIL says the added
        # test does not distinguish the change from the baseline, which is a
        # true statement about a control test as much as about a hollow one --
        # counting it as an error would punish the tool for being right.
        n_fail = sum(
            1
            for check in payload["checks"]
            if check["check"] != "revert-probe"
            for finding in check.get("findings", [])
            if str(finding.get("severity", "")).lower() == "fail"
        )
        n_nondistinguishing = sum(
            1
            for finding in probe.get("findings", [])
            if str(finding.get("severity", "")).lower() == "fail"
        )
        if payload["tests_added"] == 0:
            no_tests += 1
        elif status in ("ok", "fail", "warn"):
            evaluable += 1
        elif status == "inconclusive":
            inconclusive += 1
        fails += n_fail
        nondist += n_nondistinguishing
        rows.append(
            {
                "rev": rev[:12],
                "tests_added": payload["tests_added"],
                "fail": n_fail,
                "not_distinguishing": n_nondistinguishing,
                "probe": status,
            }
        )
    return {
        "rows": rows,
        "commits": len(rows),
        "false_positive_fails": fails,
        "not_distinguishing": nondist,
        "evaluable": evaluable,
        "inconclusive": inconclusive,
        "no_tests_added": no_tests,
        "deep": deep,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--history", type=int, default=25, help="how many of this repo's commits")
    ap.add_argument("--deep", action="store_true", help="run the revert probe over history too")
    ap.add_argument("--json", metavar="PATH", help="also write the numbers here")
    args = ap.parse_args()

    parent = tempfile.mkdtemp(prefix="unfaked-bench-")
    try:
        fx = run_fixtures(parent)
    finally:
        shutil.rmtree(parent, ignore_errors=True)
    hist = run_history(args.history, args.deep)

    print()
    print("  fixtures — one planted problem each, and the honest version beside it")
    for row in fx["rows"]:
        mark = "ok  " if row["ok"] else "MISS" if row["expects"] else "FALSE"
        print(
            "    %-5s %-32s %s"
            % (mark, row["name"], ",".join(row["reported"]) or "nothing reported")
        )
    print()
    print("    caught %d/%d · false alarms %d/%d controls"
          % (fx["caught"], fx["expected"], fx["false_alarms"], fx["controls"]))

    print()
    print("  history — %d real commits of this repository%s"
          % (hist["commits"], ", probe included" if hist["deep"] else ""))
    print("    false-positive FAILs   %d   (static checks only)" % hist["false_positive_fails"])
    print("    added tests            %d of %d commits"
          % (hist["commits"] - hist["no_tests_added"], hist["commits"]))
    if hist["deep"]:
        judged = hist["evaluable"] + hist["inconclusive"]
        share = (100.0 * hist["evaluable"] / judged) if judged else 0.0
        print("    probe reached a verdict %d of %d (%.0f%%)"
              % (hist["evaluable"], judged, share))
        print("    added tests that do not distinguish their change   %d"
              % hist["not_distinguishing"])
    print()

    payload = {"fixtures": fx, "history": hist}
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=1)
        sys.stderr.write("%s written\n" % args.json)

    return 1 if (fx["missed"] or fx["false_alarms"] or hist["false_positive_fails"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
