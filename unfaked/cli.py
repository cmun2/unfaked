"""unfaked — your agent said it's done; this checks whether it made that true."""

import argparse
import json
import os
import sys
from typing import Dict, List, Optional

from . import __version__
from . import _check_hollow, _check_neutered, _check_traces, _probe
from ._finding import FAIL, INFO, WARN, CheckResult
from ._git import (
    GitError,
    commit_messages,
    diff_files,
    has_parent,
    is_clean,
    porcelain_status,
    repo_name,
    rev_parse,
    short,
    show_file,
    toplevel,
)
from ._lang import (
    JSTS,
    OTHER,
    PYTHON,
    TestFn,
    discover_tests,
    is_build_artifact,
    is_lockfile,
    is_test_file,
    language,
)
from ._render import Report, Style, color_enabled, headline, render

CHECKS = [
    (_check_hollow.NAME, _check_hollow.TITLE),
    (_probe.NAME, _probe.TITLE),
    (_check_neutered.NAME, _check_neutered.TITLE),
    (_check_traces.NAME, _check_traces.TITLE),
]
CHECK_NAMES = [n for n, _ in CHECKS]

_CODE_EXT = {
    ".go": "Go",
    ".rs": "Rust",
    ".rb": "Ruby",
    ".java": "Java",
    ".kt": "Kotlin",
    ".cs": "C#",
    ".c": "C",
    ".cc": "C++",
    ".cpp": "C++",
    ".h": "C",
    ".hpp": "C++",
    ".swift": "Swift",
    ".php": "PHP",
    ".scala": "Scala",
    ".ex": "Elixir",
    ".exs": "Elixir",
}


class Context:
    """Everything the renderer needs to describe what was inspected."""

    def __init__(self) -> None:
        self.repo = ""
        self.repo_label = ""
        self.range_label = ""
        self.base = ""
        self.head = ""
        self.file_count = 0
        self.added_test_count = 0
        self.runner_label = ""
        self.unsupported_langs: List[str] = []
        self.verbose = False


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="unfaked",
        description=(
            "Your agent said it's done. unfaked checks whether it made that true, "
            "or just made the check pass."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "checks:\n"
            + "\n".join("  %-16s %s" % (n, t) for n, t in CHECKS)
            + "\n\nexamples:\n"
            "  unfaked                       # inspect HEAD~1..HEAD here (fast)\n"
            "  unfaked --deep                # also re-run the tests with the change reverted\n"
            "  unfaked --base main           # inspect everything since main\n"
            "  unfaked --scope 'src/**'      # flag edits outside the task\n"
            "\n"
            "fast is the default because the moment this is for -- an agent has just\n"
            "said it is done -- cannot afford to wait. --deep runs your test suite\n"
            "several times, so keep it for review and CI.\n"
        ),
    )
    p.add_argument("path", nargs="?", default=".", help="repository to inspect (default: .)")
    p.add_argument("--base", help="compare against this ref (default: HEAD~1)")
    p.add_argument("--head", default="HEAD", help="the reviewed revision (default: HEAD)")
    p.add_argument(
        "--scope",
        action="append",
        default=[],
        metavar="GLOB",
        help="files the task was allowed to touch; repeatable",
    )
    p.add_argument(
        "--skip", action="append", default=[], metavar="CHECK", help="disable a check; repeatable"
    )
    p.add_argument("--only", action="append", default=[], metavar="CHECK", help="run only this check")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument(
        "--fast",
        dest="deep",
        action="store_false",
        default=False,
        help="static checks only, never runs your code (default)",
    )
    mode.add_argument(
        "--deep",
        dest="deep",
        action="store_true",
        help="also revert the change and re-run the added tests",
    )
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument("--no-color", action="store_true", help="disable ANSI colour")
    p.add_argument("--exit-zero", action="store_true", help="always exit 0")
    p.add_argument("-v", "--verbose", action="store_true", help="show INFO findings too")
    p.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="print one line when there is nothing to report (for hooks)",
    )
    p.add_argument(
        "--timeout", type=int, default=600, metavar="SEC", help="per test run (default: 600)"
    )
    p.add_argument("--list-checks", action="store_true", help="print check names and exit")
    p.add_argument("--version", action="version", version="unfaked %s" % __version__)
    return p


def _enabled(name: str, args) -> bool:
    skips = {s.strip() for item in args.skip for s in item.split(",") if s.strip()}
    onlys = {s.strip() for item in args.only for s in item.split(",") if s.strip()}
    if onlys:
        return name in onlys
    return name not in skips


def _collect_added_tests(repo: str, head: str, diffs, sources: Dict[str, str]) -> List[TestFn]:
    out: List[TestFn] = []
    for fd in diffs:
        if fd.binary or fd.status == "D" or not is_test_file(fd.path):
            continue
        src = sources.get(fd.path)
        if src is None:
            continue
        added = fd.added_lines
        for t in discover_tests(fd.path, src):
            span = range(t.lineno, t.end_lineno + 1)
            if fd.status == "A" or t.lineno in added or any(n in added for n in span):
                # An edited test counts as "added" only if its signature line is
                # new; otherwise we would re-probe tests the change merely moved.
                if fd.status == "A" or t.lineno in added:
                    out.append(t)
    return out


def _run_checks(args, ctx: Context, out_stream) -> Report:
    repo = ctx.repo
    diffs = [fd for fd in diff_files(repo, ctx.base, ctx.head)]
    ctx.file_count = len(diffs)

    sources: Dict[str, str] = {}
    for fd in diffs:
        if fd.binary or fd.status == "D":
            continue
        src = show_file(repo, ctx.head, fd.path)
        if src is not None:
            sources[fd.path] = src

    added_tests = _collect_added_tests(repo, ctx.head, diffs, sources)
    ctx.added_test_count = len(added_tests)

    unsupported = set()
    for fd in diffs:
        ext = os.path.splitext(fd.path)[1].lower()
        if language(fd.path) == OTHER and ext in _CODE_EXT:
            unsupported.add(_CODE_EXT[ext])
    ctx.unsupported_langs = sorted(unsupported)

    evidence = "git diff %s..%s" % (ctx.base, ctx.head)

    results: List[CheckResult] = []

    # A static ---------------------------------------------------------------
    if _enabled(_check_hollow.NAME, args):
        results.append(_check_hollow.run(added_tests, sources, evidence))
    else:
        results.append(_skipped(_check_hollow.NAME, _check_hollow.TITLE))

    # A dynamic --------------------------------------------------------------
    source_paths = sorted(
        {
            fd.path
            for fd in diffs
            if not is_test_file(fd.path)
            and not is_build_artifact(fd.path)
            and not is_lockfile(fd.path)
            and not fd.binary
        }
    )
    source_status = {fd.path: fd.status for fd in diffs}
    if not _enabled(_probe.NAME, args):
        results.append(_skipped(_probe.NAME, _probe.TITLE))
    elif not _deep_requested(args):
        results.append(_deferred(_probe.NAME, _probe.TITLE, bool(added_tests)))
    else:
        runners, _notes = _probe.detect_runners(repo, added_tests)
        ctx.runner_label = ", ".join(sorted(getattr(r, "label", "?") for r in runners.values()))
        results.append(
            _probe.run(
                repo,
                ctx.base,
                ctx.head,
                added_tests,
                source_paths,
                source_status,
                sources,
                [path for _xy, path in porcelain_status(repo)],
                args.timeout,
                evidence,
            )
        )

    # B ----------------------------------------------------------------------
    if _enabled(_check_neutered.NAME, args):
        results.append(_check_neutered.run(diffs, sources, evidence))
    else:
        results.append(_skipped(_check_neutered.NAME, _check_neutered.TITLE))

    # C ----------------------------------------------------------------------
    if _enabled(_check_traces.NAME, args):
        results.append(
            _check_traces.run(
                repo,
                diffs,
                porcelain_status(repo),
                commit_messages(repo, ctx.base, ctx.head),
                args.scope or None,
                evidence,
            )
        )
    else:
        results.append(_skipped(_check_traces.NAME, _check_traces.TITLE))

    # point every generic "run" line at the specific file
    for res in results:
        for f in res.findings:
            if f.command == evidence and f.file:
                f.command = "%s -- %s" % (evidence, f.file)

    order = {n: i for i, (n, _) in enumerate(CHECKS)}
    results.sort(key=lambda r: order.get(r.name, 99))
    return Report(ctx, results)


def _skipped(name: str, title: str) -> CheckResult:
    r = CheckResult(name, title)
    r.skipped = True
    r.note = "disabled with --skip"
    return r.finalize()


def _deep_requested(args) -> bool:
    """--deep, or --only revert-probe, which asks for it by name."""
    if args.deep:
        return True
    onlys = {s.strip() for item in args.only for s in item.split(",") if s.strip()}
    return _probe.NAME in onlys


def _deferred(name: str, title: str, has_tests: bool) -> CheckResult:
    """Not run because this is fast mode -- the default.

    Distinct from _skipped(): the user did not turn this off, so say what it
    would have done and how to ask for it. Staying under a couple of seconds is
    what makes it usable on every agent hand-off, and this check re-runs the
    suite once per added test.
    """
    r = CheckResult(name, title)
    # inconclusive, not skipped: nobody turned this off, it simply has no
    # verdict yet. "skipped" would read as the user's choice.
    r.status = "inconclusive"
    r.note = (
        "not run in fast mode -- `unfaked --deep` reverts the change and re-runs "
        "the added tests"
        if has_tests
        else "no tests were added in this range, so --deep would have nothing to re-run"
    )
    return r.finalize()


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    if args.list_checks:
        for n, t in CHECKS:
            print("%-16s %s" % (n, t))
        return 0

    unknown = [
        s
        for item in (args.skip + args.only)
        for s in item.split(",")
        if s.strip() and s.strip() not in CHECK_NAMES
    ]
    if unknown:
        sys.stderr.write(
            "unfaked: unknown check(s): %s\n  known: %s\n"
            % (", ".join(unknown), ", ".join(CHECK_NAMES))
        )
        return 2

    ctx = Context()
    ctx.verbose = args.verbose
    try:
        ctx.repo = toplevel(os.path.abspath(args.path))
        ctx.head = args.head
        if args.base:
            ctx.base = args.base
        else:
            if not has_parent(ctx.repo, args.head):
                sys.stderr.write(
                    "unfaked: %s has no parent commit; pass --base <ref> to pick a range.\n"
                    % args.head
                )
                return 2
            ctx.base = "%s~1" % args.head
        rev_parse(ctx.repo, ctx.base)
        rev_parse(ctx.repo, ctx.head)
    except GitError as exc:
        sys.stderr.write("unfaked: %s\n" % exc)
        return 2

    ctx.repo_label = repo_name(ctx.repo)
    ctx.range_label = "%s..%s (%s)" % (ctx.base, ctx.head, short(ctx.repo, ctx.head))

    try:
        report = _run_checks(args, ctx, sys.stdout)
    except GitError as exc:
        sys.stderr.write("unfaked: %s\n" % exc)
        return 2

    if args.json:
        payload = {
            "version": __version__,
            "repo": ctx.repo,
            "base": rev_parse(ctx.repo, ctx.base),
            "head": rev_parse(ctx.repo, ctx.head),
            "range": "%s..%s" % (ctx.base, ctx.head),
            "headline": headline(report),
            "files_changed": ctx.file_count,
            "tests_added": ctx.added_test_count,
            "runner": ctx.runner_label,
            "static_only_languages": ctx.unsupported_langs,
            "counts": {
                "fail": report.count(FAIL),
                "warn": report.count(WARN),
                "info": report.count(INFO),
            },
            "exit_code": 0 if args.exit_zero else report.exit_code,
            "checks": [c.to_dict() for c in report.checks],
        }
        json.dump(payload, sys.stdout, indent=2, sort_keys=False)
        sys.stdout.write("\n")
    else:
        st = Style(color_enabled(args.no_color))
        sys.stdout.write(render(report, st, quiet=args.quiet))

    return 0 if args.exit_zero else report.exit_code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
