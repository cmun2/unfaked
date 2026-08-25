"""Check A (dynamic): the revert probe.

Put the source back the way it was, keep the tests the agent added, and run
them. Every test that still passes did not test the change.

This is the only check that runs your code. It edits the working tree in
place -- it checks out the base revision of the changed source files and then
puts them back -- so it refuses to start unless the tree is clean, and it
restores on every exit path including Ctrl-C.
"""

import atexit
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
from typing import Dict, List, Optional, Sequence, Tuple

from ._finding import FAIL, INFO, CheckResult, Finding
from ._git import GitError, git, rev_parse
from ._lang import JSTS, PYTHON, TestFn

NAME = "revert-probe"
TITLE = "do the new tests notice the change?"

PASS, FAILED, ABSENT, ERROR = "pass", "fail", "absent", "error"


class ProbeUnavailable(Exception):
    """Raised with a reason we can state plainly instead of guessing."""


# ---------------------------------------------------------------------------
# runners
# ---------------------------------------------------------------------------


class RunOutcome:
    __slots__ = ("results", "cases", "raw", "ok", "reason", "command")

    def __init__(self, command: str) -> None:
        self.results: Dict[str, str] = {}
        self.cases: Dict[str, str] = {}
        self.raw = ""
        self.ok = True
        self.reason = ""
        self.command = command


def _clean_env() -> Dict[str, str]:
    """Run the suite without letting it litter the tree we are about to restore.

    Colour is stripped as well. We read per-test verdicts out of the runner's
    own report, and `FORCE_COLOR` in the ambient environment makes it wrap those
    lines in escape sequences, which used to leave the probe with no results at
    all -- reported honestly as "not run", but silently losing the check that
    matters. It is set in plenty of CI images and in some people's shells.
    """
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.setdefault("CI", "1")
    env.pop("FORCE_COLOR", None)
    env.pop("CLICOLOR_FORCE", None)
    env["NO_COLOR"] = "1"
    return env


_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def _plain(text: str) -> str:
    """Drop any escape sequences a runner emitted despite being told not to."""
    return _ANSI_RE.sub("", text)


def _which_python(repo: str) -> Optional[str]:
    candidates = []
    venv = os.environ.get("VIRTUAL_ENV")
    if venv:
        candidates.append(os.path.join(venv, "bin", "python"))
    for name in (".venv", "venv", "env"):
        candidates.append(os.path.join(repo, name, "bin", "python"))
        candidates.append(os.path.join(repo, name, "Scripts", "python.exe"))
    candidates.append(sys.executable)
    for c in candidates:
        if not c or not os.path.exists(c):
            continue
        probe = subprocess.run(
            [c, "-c", "import pytest"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        if probe.returncode == 0:
            return c
    return None


_PYTEST_LINE = re.compile(
    r"^(PASSED|FAILED|ERROR|XFAIL|XPASS)\s+(\S+)", re.MULTILINE
)
_PYTEST_SKIP = re.compile(r"^SKIPPED\s+\[\d+\]\s+(\S+?):\d+", re.MULTILINE)


class PytestRunner:
    kind = PYTHON
    label = "pytest"

    def __init__(self, repo: str, python: str, extra: Sequence[str] = ()) -> None:
        self.repo = repo
        self.python = python
        self.extra = list(extra)

    @property
    def _shown_python(self) -> str:
        """A path a reader can paste: relative when the venv lives in the repo."""
        rel = os.path.relpath(self.python, self.repo)
        return "./" + rel if not rel.startswith("..") else self.python

    def command_for(self, tests: List[TestFn], shown: bool = False) -> List[str]:
        ids = sorted({"%s::%s" % (t.path, t.qualname.replace(".", "::")) for t in tests})
        return (
            [self._shown_python if shown else self.python, "-m", "pytest"]
            + ids
            + ["-q", "--no-header", "-rA", "--tb=no", "--color=no", "-p", "no:cacheprovider"]
            + self.extra
        )

    def run(self, tests: List[TestFn], timeout: int) -> RunOutcome:
        cmd = self.command_for(tests)
        out = RunOutcome(" ".join(_quote(c) for c in self.command_for(tests, shown=True)))
        try:
            proc = subprocess.run(
                cmd,
                cwd=self.repo,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=timeout,
                env=_clean_env(),
            )
        except subprocess.TimeoutExpired:
            out.ok = False
            out.reason = "pytest did not finish within %ds" % timeout
            return out
        out.raw = _plain(proc.stdout.decode("utf-8", "replace"))

        for verdict, nodeid in _PYTEST_LINE.findall(out.raw):
            key = nodeid.split("[", 1)[0]
            state = PASS if verdict in ("PASSED", "XFAIL") else FAILED
            out.cases[nodeid] = state
            prev = out.results.get(key)
            # any failing parametrisation makes the whole test id "fail"
            out.results[key] = FAILED if FAILED in (prev, state) else PASS
        for path in _PYTEST_SKIP.findall(out.raw):
            pass  # skipped tests are simply absent; handled by the caller

        if proc.returncode in (2, 3, 4):
            out.ok = False
            out.reason = "pytest exited %d (collection or usage error)" % proc.returncode
        elif proc.returncode == 5 and not out.results:
            out.ok = False
            out.reason = "pytest collected no tests"
        elif not out.results:
            out.ok = False
            out.reason = "pytest reported no per-test results"
        return out


class NodeRunner:
    kind = JSTS

    def __init__(self, repo: str, bin_path: str, label: str) -> None:
        self.repo = repo
        self.bin = bin_path
        self.label = label

    def command_for(self, tests: List[TestFn], outfile: str = "<report.json>") -> List[str]:
        files = sorted({t.path for t in tests})
        rel = os.path.relpath(self.bin, self.repo)
        if self.label == "jest":
            return ["./" + rel, "--silent", "--ci", "--json", "--outputFile=" + outfile] + files
        return ["./" + rel, "run", "--reporter=json", "--outputFile=" + outfile] + files

    def run(self, tests: List[TestFn], timeout: int) -> RunOutcome:
        fd, outfile = tempfile.mkstemp(prefix="unfaked-", suffix=".json")
        os.close(fd)
        cmd = self.command_for(tests, outfile)
        display = self.command_for(tests)
        out = RunOutcome(" ".join(_quote(c) for c in display))
        try:
            proc = subprocess.run(
                cmd,
                cwd=self.repo,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=timeout,
                env=_clean_env(),
            )
            out.raw = _plain(proc.stdout.decode("utf-8", "replace"))
            try:
                with open(outfile, "r") as fh:
                    payload = json.load(fh)
            except Exception:
                payload = None
        except subprocess.TimeoutExpired:
            out.ok = False
            out.reason = "%s did not finish within %ds" % (self.label, timeout)
            return out
        finally:
            try:
                os.unlink(outfile)
            except OSError:
                pass

        if not isinstance(payload, dict):
            out.ok = False
            out.reason = "%s produced no machine-readable report" % self.label
            return out

        for suite in payload.get("testResults", []) or []:
            for a in suite.get("assertionResults", []) or []:
                status = (a.get("status") or "").lower()
                if status in ("pending", "todo", "skipped", "disabled"):
                    continue
                title = a.get("title") or ""
                full = a.get("fullName") or title
                state = PASS if status == "passed" else FAILED
                out.cases[_norm(full)] = state
                for key in {_norm(full), _norm(title)}:
                    prev = out.results.get(key)
                    out.results[key] = FAILED if FAILED in (prev, state) else PASS

        if not out.results:
            out.ok = False
            out.reason = "%s reported no test results" % self.label
        return out


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _quote(s: str) -> str:
    return s if re.match(r"^[\w./:=@\[\]-]+$", s) else "'%s'" % s.replace("'", "'\\''")


def detect_runners(repo: str, tests: List[TestFn]) -> Tuple[Dict[str, object], List[str]]:
    """Return ({language: runner}, [notes about what could not be run])."""
    runners: Dict[str, object] = {}
    notes: List[str] = []
    kinds = {t.kind for t in tests}

    if PYTHON in kinds:
        py = _which_python(repo)
        if py:
            runners[PYTHON] = PytestRunner(repo, py)
        else:
            notes.append("no interpreter with pytest installed was found for this repo")

    if JSTS in kinds:
        for label in ("vitest", "jest"):
            cand = os.path.join(repo, "node_modules", ".bin", label)
            if os.path.exists(cand):
                runners[JSTS] = NodeRunner(repo, cand, label)
                break
        else:
            notes.append("neither jest nor vitest is installed in node_modules/.bin")

    return runners, notes


# ---------------------------------------------------------------------------
# the probe
# ---------------------------------------------------------------------------


def _key_for(t: TestFn) -> str:
    if t.kind == PYTHON:
        return "%s::%s" % (t.path, t.qualname.replace(".", "::"))
    return _norm(t.qualname.replace(" > ", " "))


class _Restorer:
    """Puts the working tree back, whatever happens."""

    def __init__(self, repo: str, paths: List[str]) -> None:
        self.repo = repo
        self.paths = paths
        self.armed = False
        self._prev_handlers = {}

    def arm(self) -> None:
        self.armed = True
        atexit.register(self.restore)
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                self._prev_handlers[sig] = signal.signal(sig, self._on_signal)
            except (ValueError, OSError):  # pragma: no cover - non-main thread
                pass

    def _on_signal(self, signum, frame):  # pragma: no cover - interactive only
        self.restore()
        sys.stderr.write("\nunfaked: working tree restored after signal %d\n" % signum)
        raise SystemExit(130)

    def restore(self) -> None:
        if not self.armed:
            return
        self.armed = False
        for attempt in (
            ("restore", "--source=HEAD", "--worktree", "--staged", "--"),
            ("checkout", "HEAD", "--"),
        ):
            try:
                git(self.repo, *(list(attempt) + self.paths))
                break
            except GitError:
                continue
        else:
            sys.stderr.write(
                "unfaked: FAILED to restore %s -- run `git checkout HEAD -- %s`\n"
                % (", ".join(self.paths), " ".join(self.paths))
            )
        for sig, handler in self._prev_handlers.items():
            try:
                signal.signal(sig, handler)
            except (ValueError, OSError):  # pragma: no cover
                pass


def run(
    repo: str,
    base: str,
    head: str,
    added_tests: List[TestFn],
    source_paths: List[str],
    source_status: Dict[str, str],
    sources: Dict[str, str],
    dirty_paths: Sequence[str],
    timeout: int,
    evidence_cmd: str,
) -> CheckResult:
    res = CheckResult(NAME, TITLE)

    def snippet(t: TestFn, span: int = 2) -> List[str]:
        src = sources.get(t.path)
        if src is None:
            return ["%s  (%s)" % (t.qualname, t.path)]
        lines = src.split("\n")
        out = []
        for i in range(t.lineno, min(t.lineno + span, len(lines) + 1)):
            out.append("%d\t%s" % (i, lines[i - 1].rstrip()))
        return out or ["%s  (%s)" % (t.qualname, t.path)]

    def inconclusive(reason: str, hint: str = "") -> CheckResult:
        res.status = "inconclusive"
        res.note = reason
        res.add(
            Finding(
                NAME, INFO, "revert probe did not run", None, None, [],
                why=reason,
                fix=hint or "Static checks above still apply.",
                command=evidence_cmd,
                extra={"kind": "inconclusive"},
            )
        )
        return res

    if not added_tests:
        return inconclusive(
            "no tests were added in this range, so there is nothing to re-run",
            "If the change was supposed to come with tests, that is the finding.",
        )
    if not source_paths:
        return inconclusive(
            "the change touched no non-test source files, so there is nothing to revert",
            "A test-only change cannot be probed this way.",
        )
    collide = sorted(set(dirty_paths) & set(source_paths))
    if collide:
        return inconclusive(
            "these files have uncommitted changes and the probe would have to overwrite them: %s"
            % ", ".join(collide),
            "Commit or stash them, then re-run. `--skip revert-probe` turns this off.",
        )
    if rev_parse(repo, "HEAD") != rev_parse(repo, head):
        return inconclusive(
            "HEAD is not %s; the probe runs the tests as they exist in the working tree" % head,
            "Check out %s and re-run." % head,
        )

    runners, notes = detect_runners(repo, added_tests)
    if not runners:
        return inconclusive(
            "; ".join(notes) or "no supported test runner was found",
            "Install the test runner in this repo, or use --skip revert-probe.",
        )

    runnable = [t for t in added_tests if t.kind in runners]
    unrunnable = [t for t in added_tests if t.kind not in runners]

    by_kind: Dict[str, List[TestFn]] = {}
    for t in runnable:
        by_kind.setdefault(t.kind, []).append(t)

    # 1. baseline -----------------------------------------------------------
    baseline: Dict[str, str] = {}
    baseline_cases: Dict[str, str] = {}
    baseline_cmds: List[str] = []
    for kind, tests in by_kind.items():
        outcome = runners[kind].run(tests, timeout)
        baseline_cmds.append(outcome.command)
        baseline_cases.update(outcome.cases)
        if not outcome.ok:
            return inconclusive(
                "the added tests do not run cleanly as committed (%s)" % outcome.reason,
                "Fix the suite first: %s" % outcome.command,
            )
        baseline.update(outcome.results)

    passing_now = [t for t in runnable if baseline.get(_key_for(t)) == PASS]
    unknown_now = [t for t in runnable if _key_for(t) not in baseline]
    failing_now = [t for t in runnable if baseline.get(_key_for(t)) == FAILED]

    if not passing_now:
        return inconclusive(
            "none of the added tests pass as committed, so reverting proves nothing",
            "Make the suite green first: %s" % (baseline_cmds[0] if baseline_cmds else ""),
        )

    # 2. revert the source, keep the tests -----------------------------------
    restorer = _Restorer(repo, source_paths)
    reverted: Dict[str, str] = {}
    reverted_cases: Dict[str, str] = {}
    revert_cmds: List[str] = []
    added_in_head = [p for p in source_paths if source_status.get(p) == "A"]
    checkout_paths = [p for p in source_paths if source_status.get(p) != "A"]

    revert_cmd_display = (
        "git restore --source=%s --worktree -- %s" % (base, " ".join(checkout_paths))
        if checkout_paths
        else ""
    )
    if added_in_head:
        revert_cmd_display = (
            (revert_cmd_display + " && " if revert_cmd_display else "")
            + "rm " + " ".join(added_in_head)
        )

    try:
        restorer.arm()
        if checkout_paths:
            try:
                git(repo, "restore", "--source=%s" % base, "--worktree", "--", *checkout_paths)
            except GitError:
                git(repo, "checkout", base, "--", *checkout_paths)
        for p in added_in_head:
            try:
                os.unlink(os.path.join(repo, p))
            except OSError:
                pass

        by_kind_pass: Dict[str, List[TestFn]] = {}
        for t in passing_now:
            by_kind_pass.setdefault(t.kind, []).append(t)
        for kind, tests in by_kind_pass.items():
            outcome = runners[kind].run(tests, timeout)
            revert_cmds.append(outcome.command)
            if not outcome.ok:
                res.status = "inconclusive"
                res.note = (
                    "with the change reverted the suite could not run (%s) -- most likely the "
                    "tests import something the change introduced, so this repo cannot be probed "
                    "by reverting alone" % outcome.reason
                )
                res.add(
                    Finding(
                        NAME, INFO, "revert probe inconclusive", None, None,
                        [ln for ln in outcome.raw.strip().split("\n")[-6:] if ln.strip()],
                        why=res.note,
                        fix="Split source and test changes into separate commits, or review these tests by hand.",
                        command="%s && %s" % (revert_cmd_display, outcome.command),
                        extra={"kind": "inconclusive"},
                    )
                )
                return res
            reverted.update(outcome.results)
            reverted_cases.update(outcome.cases)
    except GitError as exc:
        return inconclusive("could not revert the source files: %s" % exc)
    finally:
        restorer.restore()

    # 3. verdict -------------------------------------------------------------
    survivors = []
    for t in passing_now:
        if reverted.get(_key_for(t)) == PASS:
            survivors.append(t)

    def repro_for(t: TestFn) -> str:
        runner = runners[t.kind]
        one = " ".join(_quote(c) for c in runner.command_for([t], **({"shown": True} if isinstance(runner, PytestRunner) else {})))
        return "%s && %s" % (revert_cmd_display, one)

    for t in survivors:
        res.add(
            Finding(
                NAME,
                FAIL,
                "still passes with the change reverted: %s" % t.name,
                t.path,
                t.lineno,
                snippet(t, 3),
                why="This test passes both with and without the source change, so it does not "
                "demonstrate the change did anything.",
                fix="Make the assertion depend on the new behaviour, then confirm it fails on the old code.",
                command=repro_for(t),
                extra={"test": t.qualname, "nodeid": _key_for(t)},
            )
        )

    for t in unknown_now:
        res.add(
            Finding(
                NAME, INFO, "not evaluable: %s" % t.name, t.path, t.lineno,
                ["%s  (no result reported by the runner)" % t.qualname],
                why="The runner reported no result for this test, so the probe has no opinion on it.",
                fix="Check it by hand: %s" % (baseline_cmds[0] if baseline_cmds else ""),
                command=baseline_cmds[0] if baseline_cmds else evidence_cmd,
                extra={"kind": "unevaluable"},
            )
        )
    for t in failing_now:
        res.add(
            Finding(
                NAME, INFO, "already failing: %s" % t.name, t.path, t.lineno,
                ["%s  (fails as committed)" % t.qualname],
                why="It does not pass on the committed code, so reverting tells us nothing.",
                fix="Fix the test, then re-run unfaked.",
                command=baseline_cmds[0] if baseline_cmds else evidence_cmd,
                extra={"kind": "unevaluable"},
            )
        )
    for t in unrunnable:
        res.add(
            Finding(
                NAME, INFO, "no runner: %s" % t.name, t.path, t.lineno, [],
                why="; ".join(notes) or "no runner available for this file type",
                fix="Static checks still applied to it.",
                command=evidence_cmd,
                extra={"kind": "unevaluable"},
            )
        )

    probed_cases = sum(1 for k, v in baseline_cases.items() if v == PASS)
    survived_cases = sum(
        1 for k, v in baseline_cases.items() if v == PASS and reverted_cases.get(k) == PASS
    )
    res.stats = {
        "added": len(added_tests),
        "probed": len(passing_now),
        "survived": len(survivors),
        "cases_probed": probed_cases,
        "cases_survived": survived_cases,
        "runner": ", ".join(sorted(getattr(r, "label", "?") for r in runners.values())),
    }
    res.note = "%d of %d added tests re-run with the change reverted (%d cases); %d still passed" % (
        len(passing_now),
        len(added_tests),
        probed_cases,
        len(survivors),
    )
    return res.finalize()


__all__ = ["run", "NAME", "TITLE", "detect_runners"]
