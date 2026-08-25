#!/usr/bin/env python3
"""Build the demo repository the README screenshots, then run unfaked on it.

    python examples/demo.py            # build it and print the report
    python examples/demo.py --keep     # leave the repo on disk and print its path

The repository is a plausible agent commit: a real fix, five new tests of
which two do not exercise the fix, a skip added to an existing test, a blanket
`# type: ignore`, and a scratch file left uncommitted. Nothing in the README
is typed by hand -- it is this script's output.

Requires pytest on the interpreter you run it with, because the revert probe
actually runs the suite.
"""

import argparse
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

BEFORE = '''\
"""Slice a list into pages."""


def paginate(items, page_size, page):
    start = page * page_size
    return items[start : start + page_size]


def page_count(items, page_size):
    return len(items) // page_size
'''

AFTER = '''\
"""Slice a list into pages."""


def paginate(items, page_size, page):
    if page_size <= 0:
        raise ValueError("page_size must be positive")
    start = page * page_size
    return items[start : start + page_size]


def page_count(items, page_size):
    return -(-len(items) // page_size)  # type: ignore
'''

TESTS_BEFORE = '''\
from src.paginator import page_count, paginate

ITEMS = list(range(9))


def test_paginate_last_page():
    assert paginate(ITEMS, 4, 2) == [8]


def test_page_count_exact_fit():
    assert page_count(list(range(8)), 4) == 2
'''

TESTS_AFTER = '''\
import pytest

from src.paginator import page_count, paginate

ITEMS = list(range(9))


@pytest.mark.skip(reason="flaky on CI")
def test_paginate_last_page():
    assert paginate(ITEMS, 4, 2) == [8]


def test_page_count_exact_fit():
    assert page_count(list(range(8)), 4) == 2


def test_page_count_rounds_up():
    assert page_count(ITEMS, 4) == 3


def test_paginate_first_page():
    assert paginate(ITEMS, 4, 0) == [0, 1, 2, 3]


def test_page_count_of_an_empty_list():
    assert page_count([], 4) == 0


def test_rejects_a_zero_page_size():
    with pytest.raises(ValueError):
        paginate(ITEMS, 0, 0)


def test_paginate_smoke():
    paginate(ITEMS, 4, 1)
'''

MESSAGE = """Fix page_count rounding and reject a zero page size

page_count() truncated instead of rounding up, so the last partial page was
unreachable. Adds comprehensive tests for the paginator.
"""


def git(repo, *args):
    subprocess.run(
        ["git", "-C", repo] + list(args),
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def write(repo, rel, content):
    full = os.path.join(repo, rel)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w") as fh:
        fh.write(content)


def _has_pytest(python):
    return (
        subprocess.run(
            [python, "-c", "import pytest"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode
        == 0
    )


def _make_venv(path):
    """Give the demo repo a real ./.venv, the way a Python project has one.

    pytest comes from wherever this script found it, linked in with a .pth file
    so no download is needed. Without it the report still works, it just names
    whatever interpreter is running instead of `./.venv/bin/python`.
    """
    if not _has_pytest(sys.executable):
        return
    venv = os.path.join(path, ".venv")
    r = subprocess.run(
        [sys.executable, "-m", "venv", "--without-pip", venv],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if r.returncode != 0:
        return
    import sysconfig

    parent_sp = sysconfig.get_paths()["purelib"]
    for lib in os.listdir(os.path.join(venv, "lib")):
        sp = os.path.join(venv, "lib", lib, "site-packages")
        if os.path.isdir(sp):
            with open(os.path.join(sp, "_borrowed.pth"), "w") as fh:
                fh.write(parent_sp + "\n")
    if not _has_pytest(os.path.join(venv, "bin", "python")):
        import shutil

        shutil.rmtree(venv, ignore_errors=True)


def build(path):
    git(path, "init", "-q", "-b", "main")
    git(path, "config", "user.email", "agent@example.com")
    git(path, "config", "user.name", "a coding agent")
    git(path, "config", "commit.gpgsign", "false")

    write(path, "src/paginator.py", BEFORE)
    write(path, "tests/test_paginator.py", TESTS_BEFORE)
    write(path, ".gitignore", "__pycache__/\n.venv/\n")
    _make_venv(path)
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", "Add a paginator", "--no-verify")

    write(path, "src/paginator.py", AFTER)
    write(path, "tests/test_paginator.py", TESTS_AFTER)
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", MESSAGE, "--no-verify")

    # the agent's scratch file, never committed
    write(path, "notes_to_self.md", "TODO: check whether page_count is used elsewhere\n")
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true", help="do not delete the repo afterwards")
    ap.add_argument("--no-color", action="store_true")
    args = ap.parse_args()

    path = os.path.join(tempfile.mkdtemp(prefix="unfaked-demo-"), "paginator")
    os.makedirs(path)
    build(path)
    argv = [path]
    if args.no_color:
        argv.append("--no-color")

    from unfaked import cli

    code = cli.main(argv)
    if args.keep:
        sys.stderr.write("demo repo: %s\n" % path)
    else:
        import shutil

        shutil.rmtree(os.path.dirname(path), ignore_errors=True)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
