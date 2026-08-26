"""The corpus: for every check, one repo that must trip it and one that must not.

Run with:  python -m unittest discover -s tests -v
"""

import os
import subprocess
import unittest

from harness import NO_PROBE, Repo, check_status, fails, findings, warns

SRC_BEFORE = '''\
def add(a, b):
    return a + b
'''

SRC_AFTER = '''\
def add(a, b):
    if a is None or b is None:
        raise ValueError("add() needs two numbers")
    return a + b
'''


def base_repo(name):
    r = Repo(name)
    r.write("src/calc.py", SRC_BEFORE)
    r.write("tests/test_calc.py", "from src.calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n")
    r.commit("initial")
    return r


# ---------------------------------------------------------------------------
# A (static) -- tests that cannot fail
# ---------------------------------------------------------------------------


class TestHollowNoAssertion(unittest.TestCase):
    def test_caught(self):
        with base_repo("a1-catch") as r:
            r.write("src/calc.py", SRC_AFTER)
            r.write(
                "tests/test_none.py",
                "from src.calc import add\n\n\ndef test_rejects_none():\n    try:\n        add(None, 1)\n    except ValueError:\n        return\n",
            )
            r.commit("reject None")
            payload, _ = r.run(*NO_PROBE)
            hits = warns(payload, "hollow-tests")
            self.assertTrue(any("no assertion" in f["title"] for f in hits), payload["checks"])

    def test_control(self):
        with base_repo("a1-control") as r:
            r.write("src/calc.py", SRC_AFTER)
            r.write(
                "tests/test_none.py",
                "import pytest\nfrom src.calc import add\n\n\ndef test_rejects_none():\n    with pytest.raises(ValueError):\n        add(None, 1)\n",
            )
            r.commit("reject None")
            payload, _ = r.run(*NO_PROBE)
            self.assertEqual([], findings(payload, "hollow-tests"), payload["checks"])


class TestHollowEmptyBody(unittest.TestCase):
    def test_caught(self):
        with base_repo("a1b-catch") as r:
            r.write("tests/test_empty.py", "def test_todo():\n    pass\n")
            r.commit("add coverage")
            payload, code = r.run(*NO_PROBE)
            self.assertTrue(any("empty" in f["title"] for f in fails(payload, "hollow-tests")))
            self.assertEqual(1, code)

    def test_control(self):
        with base_repo("a1b-control") as r:
            r.write("tests/test_empty.py", "def test_todo():\n    assert 2 + 2 == 4\n")
            r.commit("add coverage")
            payload, code = r.run(*NO_PROBE)
            self.assertEqual([], fails(payload, "hollow-tests"))
            self.assertEqual(0, code)


class TestHollowTautology(unittest.TestCase):
    def test_caught_python(self):
        with base_repo("a2-catch") as r:
            r.write("src/calc.py", SRC_AFTER)
            r.write(
                "tests/test_taut.py",
                "from src.calc import add\n\n\n"
                "def test_add_is_stable():\n"
                "    result = add(1, 2)\n"
                "    assert result == result\n\n\n"
                "def test_always():\n"
                "    assert True\n",
            )
            r.commit("more tests")
            payload, code = r.run(*NO_PROBE)
            titles = [f["title"] for f in fails(payload, "hollow-tests")]
            self.assertTrue(any("itself" in t for t in titles), titles)
            self.assertTrue(any("Tautological" in t or "tautological" in t for t in titles), titles)
            self.assertEqual(1, code)

    def test_control_python(self):
        with base_repo("a2-control") as r:
            r.write("src/calc.py", SRC_AFTER)
            r.write(
                "tests/test_taut.py",
                "from src.calc import add\n\n\n"
                "def test_add_is_stable():\n"
                "    result = add(1, 2)\n"
                "    assert result == 3\n\n\n"
                "def test_negative():\n"
                "    assert add(-1, -1) == -2\n",
            )
            r.commit("more tests")
            payload, code = r.run(*NO_PROBE)
            self.assertEqual([], fails(payload, "hollow-tests"))
            self.assertEqual(0, code)

    def test_caught_js(self):
        with base_repo("a2js-catch") as r:
            r.write(
                "tests/calc.test.ts",
                "import { add } from '../src/calc';\n\n"
                "describe('add', () => {\n"
                "  it('is stable', () => {\n"
                "    const result = add(1, 2);\n"
                "    expect(result).toBe(result);\n"
                "  });\n"
                "});\n",
            )
            r.commit("ts tests")
            payload, code = r.run(*NO_PROBE)
            self.assertTrue(fails(payload, "hollow-tests"), payload["checks"])
            self.assertEqual(1, code)

    def test_control_js(self):
        with base_repo("a2js-control") as r:
            r.write(
                "tests/calc.test.ts",
                "import { add } from '../src/calc';\n\n"
                "describe('add', () => {\n"
                "  it('adds', () => {\n"
                "    const result = add(1, 2);\n"
                "    expect(result).toBe(3);\n"
                "  });\n"
                "});\n",
            )
            r.commit("ts tests")
            payload, code = r.run(*NO_PROBE)
            self.assertEqual([], fails(payload, "hollow-tests"))
            self.assertEqual(0, code)


class TestHollowMockSelfAssert(unittest.TestCase):
    def test_caught(self):
        with base_repo("a3-catch") as r:
            r.write(
                "tests/test_mock.py",
                "from unittest.mock import Mock\n\n\n"
                "def test_fetches_user():\n"
                "    client = Mock()\n"
                "    client.get_user.return_value = {'id': 7}\n"
                "    assert client.get_user() == {'id': 7}\n",
            )
            r.commit("mock test")
            payload, _ = r.run(*NO_PROBE)
            titles = [f["title"] for f in warns(payload, "hollow-tests")]
            self.assertTrue(any("mock" in t for t in titles), titles)

    def test_control(self):
        with base_repo("a3-control") as r:
            r.write(
                "tests/test_mock.py",
                "from unittest.mock import Mock\n\nfrom src.calc import add\n\n\n"
                "def test_fetches_user():\n"
                "    client = Mock()\n"
                "    client.get_user.return_value = {'id': 7}\n"
                "    assert add(client.get_user()['id'], 1) == 8\n",
            )
            r.commit("mock test")
            payload, _ = r.run(*NO_PROBE)
            titles = [f["title"] for f in warns(payload, "hollow-tests")]
            self.assertFalse(any("mock" in t for t in titles), titles)


# ---------------------------------------------------------------------------
# A (dynamic) -- the revert probe
# ---------------------------------------------------------------------------


class TestRevertProbe(unittest.TestCase):
    """Needs pytest on the interpreter running this suite."""

    def setUp(self):
        try:
            import pytest  # noqa: F401
        except ImportError:  # pragma: no cover
            self.skipTest("pytest is not installed; the probe cannot run")

    def test_caught(self):
        # The test the agent added checks a property the old code already had.
        with base_repo("a4-catch") as r:
            r.write("src/calc.py", SRC_AFTER)
            r.write(
                "tests/test_guard.py",
                "from src.calc import add\n\n\n"
                "def test_guard_rejects_none():\n"
                "    assert add(2, 3) == 5\n",
            )
            r.commit("guard against None, with a test")
            payload, code = r.run("--deep")
            hits = fails(payload, "revert-probe")
            self.assertEqual(1, len(hits), payload["checks"])
            self.assertIn("still passes with the change reverted", hits[0]["title"])
            self.assertEqual(1, code)
            self.assertIn("still pass with the change reverted", payload["headline"])

    def test_control(self):
        # The same shape of commit, but the test actually exercises the guard.
        with base_repo("a4-control") as r:
            r.write("src/calc.py", SRC_AFTER)
            r.write(
                "tests/test_guard.py",
                "import pytest\n\nfrom src.calc import add\n\n\n"
                "def test_guard_rejects_none():\n"
                "    with pytest.raises(ValueError):\n"
                "        add(None, 1)\n",
            )
            r.commit("guard against None, with a test")
            payload, code = r.run("--deep")
            self.assertEqual([], fails(payload, "revert-probe"), payload["checks"])
            self.assertEqual("ok", check_status(payload, "revert-probe"))
            self.assertEqual(0, code)

    def test_restores_the_working_tree(self):
        with base_repo("a4-restore") as r:
            r.write("src/calc.py", SRC_AFTER)
            r.write("tests/test_guard.py", "from src.calc import add\n\n\ndef test_x():\n    assert add(2, 3) == 5\n")
            r.commit("guard")
            import subprocess

            def status():
                out = subprocess.run(
                    ["git", "-C", r.path, "status", "--porcelain"], capture_output=True
                ).stdout.decode()
                return sorted(l for l in out.splitlines() if "__pycache__" not in l)

            before = status()
            r.run("--deep")
            self.assertEqual(before, status())
            with open(r.path + "/src/calc.py") as fh:
                self.assertEqual(SRC_AFTER, fh.read())

    def test_inconclusive_when_tree_is_dirty(self):
        with base_repo("a4-dirty") as r:
            r.write("src/calc.py", SRC_AFTER)
            r.write("tests/test_guard.py", "from src.calc import add\n\n\ndef test_x():\n    assert add(2, 3) == 5\n")
            r.commit("guard")
            r.write("src/calc.py", SRC_AFTER + "\n# scratch\n")
            payload, code = r.run("--deep")
            self.assertEqual("inconclusive", check_status(payload, "revert-probe"))
            self.assertEqual([], fails(payload, "revert-probe"))


def _build_report(cli, path):
    """Run the checks the way main() does, but hand back the Report."""
    from unfaked._changeset import resolve as resolve_changeset

    args = cli.build_parser().parse_args([path])
    ctx = cli.Context()
    ctx.repo = cli.toplevel(path)
    ctx.changeset = resolve_changeset(ctx.repo, args.base, args.head, args.session_file)
    ctx.base = ctx.changeset.base
    ctx.head = ctx.changeset.head
    ctx.repo_label = cli.repo_name(ctx.repo)
    ctx.range_label = ctx.changeset.label
    import sys as _sys

    return cli._run_checks(args, ctx, _sys.stdout)


class TestExpectationMovedToTheCode(unittest.TestCase):
    """The shape an agent's concession takes in a diff.

    Pressed hard enough, an agent will agree rather than check, and what that
    looks like in code is the expected value being edited to whatever the code
    already produced. The assertion does not get weaker, so the weakened-check
    does not see it; what gives it away is that nothing outside the tests moved.
    """

    def test_caught(self):
        with base_repo("re-aimed-catch") as r:
            r.write(
                "tests/test_calc.py",
                "from src.calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 4\n",
            )
            r.commit("expectation")
            r.write(
                "tests/test_calc.py",
                "from src.calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n",
            )
            r.commit("align the test with actual behaviour")
            payload, _ = r.run(*NO_PROBE)
            hits = [f for f in findings(payload, "neutered-checks")
                    if "expected value changed" in f["title"]]
            self.assertEqual(1, len(hits), payload["checks"])
            self.assertIn("moved to the code", hits[0]["why"])

    def test_control_source_changed_too(self):
        # The same test edit, but the production code moved in the same commit.
        # That is ordinary work: the expectation follows a real behaviour change.
        with base_repo("re-aimed-control") as r:
            # base_repo already committed this expectation; go straight to the
            # commit that changes both sides.
            r.write("src/calc.py", SRC_AFTER)
            r.write(
                "tests/test_calc.py",
                "from src.calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 4\n",
            )
            r.commit("change the behaviour and the test together")
            payload, _ = r.run(*NO_PROBE)
            hits = [f for f in findings(payload, "neutered-checks")
                    if "expected value changed" in f["title"]]
            self.assertEqual([], hits, payload["checks"])

    def test_control_renamed_only(self):
        # Renaming a variable is not a changed expectation.
        with base_repo("re-aimed-rename") as r:
            r.write(
                "tests/test_calc.py",
                "from src.calc import add\n\n\ndef test_add():\n    total = add(1, 2)\n    assert total == 3\n",
            )
            r.commit("expectation")
            r.write(
                "tests/test_calc.py",
                "from src.calc import add\n\n\ndef test_add():\n    result = add(1, 2)\n    assert result == 3\n",
            )
            r.commit("rename a local")
            payload, _ = r.run(*NO_PROBE)
            hits = [f for f in findings(payload, "neutered-checks")
                    if "expected value changed" in f["title"]]
            self.assertEqual([], hits, payload["checks"])


class TestProbeLeavesTheTreeAlone(unittest.TestCase):
    """The checker must not be the one thing in the room that mutates the repo.

    It also has to work while the tree is dirty, which is the normal state when
    an agent has just stopped.
    """

    def setUp(self):
        try:
            import pytest  # noqa: F401
        except ImportError:  # pragma: no cover
            self.skipTest("pytest is not installed; the probe cannot run")

    def _status(self, path):
        out = subprocess.run(
            ["git", "-C", path, "status", "--porcelain"], capture_output=True
        ).stdout.decode()
        return sorted(l for l in out.splitlines() if "__pycache__" not in l)

    def test_runs_with_uncommitted_changes_and_changes_nothing(self):
        with base_repo("shadow-dirty") as r:
            # The agent worked and stopped without committing.
            r.write("src/calc.py", SRC_AFTER)
            r.write(
                "tests/test_guard.py",
                "from src.calc import add\n\n\ndef test_guard():\n    assert add(2, 3) == 5\n",
            )
            before = self._status(r.path)
            payload, code = r.run("--deep")

            self.assertTrue(payload["includes_uncommitted"])
            # The probe reached a verdict rather than refusing on a dirty tree.
            self.assertEqual(1, len(fails(payload, "revert-probe")), payload["checks"])
            self.assertEqual(1, code)
            self.assertEqual(before, self._status(r.path))

    def test_leaves_no_worktree_behind(self):
        with base_repo("shadow-cleanup") as r:
            r.write("src/calc.py", SRC_AFTER)
            r.write(
                "tests/test_guard.py",
                "from src.calc import add\n\n\ndef test_guard():\n    assert add(2, 3) == 5\n",
            )
            r.run("--deep")
            out = subprocess.run(
                ["git", "-C", r.path, "worktree", "list"], capture_output=True
            ).stdout.decode()
            self.assertEqual(1, len([l for l in out.splitlines() if l.strip()]), out)


class TestSaysWhichChangeSetItRead(unittest.TestCase):
    def test_points_at_the_commit_when_the_tree_carries_no_tests(self):
        # The agent committed its work and left a scratch file. Rather than
        # guessing which was meant, say what was read and how to ask for the
        # other.
        with base_repo("hint-commit") as r:
            r.write("src/calc.py", SRC_AFTER)
            r.write(
                "tests/test_guard.py",
                "from src.calc import add\n\n\ndef test_guard():\n    assert add(2, 3) == 5\n",
            )
            r.commit("guard against None, with a test")
            r.write("notes.md", "TODO\n")
            payload, _ = r.run(*NO_PROBE)

            self.assertEqual("working-tree", payload["changeset"])
            self.assertIn("--head HEAD", payload["hint"])

    def test_no_hint_when_the_tree_is_the_whole_story(self):
        with base_repo("hint-none") as r:
            r.write("src/calc.py", SRC_AFTER)
            r.write(
                "tests/test_guard.py",
                "from src.calc import add\n\n\ndef test_guard():\n    assert add(2, 3) == 5\n",
            )
            payload, _ = r.run(*NO_PROBE)
            self.assertEqual("", payload.get("hint", ""))


class TestProbeSurvivesForcedColour(unittest.TestCase):
    """`FORCE_COLOR` in the environment used to silently disable the probe.

    The runner wrapped its per-test report in escape sequences, the parser found
    no results, and the check degraded to "not run" -- honest, but the one check
    that matters was gone. It is set in CI images and in some shells, so this
    pins it.
    """

    def setUp(self):
        try:
            import pytest  # noqa: F401
        except ImportError:  # pragma: no cover
            self.skipTest("pytest is not installed; the probe cannot run")
        self._saved = {k: os.environ.get(k) for k in ("FORCE_COLOR", "CLICOLOR_FORCE")}
        os.environ["FORCE_COLOR"] = "1"
        os.environ["CLICOLOR_FORCE"] = "1"

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_probe_still_reaches_a_verdict(self):
        with base_repo("colour-forced") as r:
            r.write("src/calc.py", SRC_AFTER)
            r.write(
                "tests/test_guard.py",
                "from src.calc import add\n\n\ndef test_guard():\n    assert add(2, 3) == 5\n",
            )
            r.commit("guard against None, with a test")
            payload, code = r.run("--deep")
            self.assertEqual(1, len(fails(payload, "revert-probe")), payload["checks"])
            self.assertEqual(1, code)

    def test_clean_env_strips_the_colour_forcing_variables(self):
        from unfaked import _probe

        env = _probe._clean_env()
        self.assertNotIn("FORCE_COLOR", env)
        self.assertNotIn("CLICOLOR_FORCE", env)
        self.assertEqual("1", env["NO_COLOR"])


class TestFastIsTheDefault(unittest.TestCase):
    """The probe re-runs the suite once per added test, so it is opt-in.

    Fast mode is what makes this usable on every agent hand-off; if the probe
    ever creeps back into the default these tests fail.
    """

    def _vacuous(self, name):
        r = base_repo(name)
        ctx = r.__enter__()
        ctx.write("src/calc.py", SRC_AFTER)
        ctx.write(
            "tests/test_guard.py",
            "from src.calc import add\n\n\ndef test_guard_rejects_none():\n    assert add(2, 3) == 5\n",
        )
        ctx.commit("guard against None, with a test")
        return r, ctx

    def test_default_does_not_run_the_probe(self):
        r, ctx = self._vacuous("mode-default")
        try:
            payload, code = ctx.run()
            self.assertEqual("inconclusive", check_status(payload, "revert-probe"))
            self.assertIn("--deep", payload["checks"][1]["note"])
            # the vacuous test goes unnoticed, and nothing claims otherwise
            self.assertEqual([], fails(payload, "revert-probe"))
            self.assertNotIn("with the change reverted", payload["headline"])
        finally:
            r.__exit__(None, None, None)

    def test_explicit_fast_matches_the_default(self):
        r, ctx = self._vacuous("mode-fast")
        try:
            payload, _ = ctx.run("--fast")
            self.assertEqual("inconclusive", check_status(payload, "revert-probe"))
        finally:
            r.__exit__(None, None, None)

    def test_deep_runs_it(self):
        r, ctx = self._vacuous("mode-deep")
        try:
            payload, code = ctx.run("--deep")
            self.assertEqual(1, len(fails(payload, "revert-probe")))
            self.assertEqual(1, code)
        finally:
            r.__exit__(None, None, None)

    def test_quiet_collapses_to_one_line_when_there_is_nothing_to_say(self):
        # The hook integration promises this. A hook that prints a table after
        # every turn gets muted, and a muted hook reports nothing at all.
        from unfaked import _render, cli

        with base_repo("mode-quiet-clean") as r:
            r.write("src/calc.py", SRC_AFTER)
            r.write(
                "tests/test_guard.py",
                "import pytest\n\nfrom src.calc import add\n\n\n"
                "def test_guard_rejects_none():\n"
                "    with pytest.raises(ValueError):\n"
                "        add(None, 1)\n",
            )
            r.commit("guard against None, with a test")
            report = _build_report(cli, r.path)
            st = _render.Style(False)
            self.assertEqual(1, len(_render.render(report, st, quiet=True).strip().split("\n")))
            self.assertGreater(len(_render.render(report, st).strip().split("\n")), 5)

    def test_quiet_still_expands_when_there_is_a_finding(self):
        from unfaked import _render, cli

        with base_repo("mode-quiet-dirty") as r:
            r.write("src/calc.py", SRC_AFTER)
            r.write(
                "tests/test_guard.py",
                "from src.calc import add\n\n\ndef test_x():\n    assert add(2, 3) == add(2, 3)\n",
            )
            r.commit("guard against None, with a test")
            out = _render.render(_build_report(cli, r.path), _render.Style(False), quiet=True)
            self.assertIn("hollow-tests", out)

    def test_only_revert_probe_asks_for_it_by_name(self):
        # Naming the check is an explicit request; requiring --deep as well
        # would just be a second way to say the same thing.
        r, ctx = self._vacuous("mode-only")
        try:
            payload, _ = ctx.run("--only", "revert-probe")
            self.assertEqual(1, len(fails(payload, "revert-probe")))
        finally:
            r.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# B -- checks switched off
# ---------------------------------------------------------------------------


class TestNeuteredSkips(unittest.TestCase):
    def test_caught(self):
        with base_repo("b1-catch") as r:
            r.write(
                "tests/test_calc.py",
                "import pytest\n\nfrom src.calc import add\n\n\n"
                "@pytest.mark.skip(reason='flaky')\n"
                "def test_add():\n    assert add(1, 2) == 3\n",
            )
            r.commit("stabilise the suite")
            payload, _ = r.run(*NO_PROBE)
            titles = [f["title"] for f in warns(payload, "neutered-checks")]
            self.assertTrue(any("skip" in t for t in titles), titles)

    def test_control_preexisting_skip_is_not_reported(self):
        r = Repo("b1-control")
        r.write("src/calc.py", SRC_BEFORE)
        r.write(
            "tests/test_calc.py",
            "import pytest\n\nfrom src.calc import add\n\n\n"
            "@pytest.mark.skip(reason='needs a network')\n"
            "def test_add():\n    assert add(1, 2) == 3\n",
        )
        r.commit("initial, already skipped")
        try:
            r.write("src/calc.py", SRC_AFTER)
            r.commit("guard against None")
            payload, _ = r.run(*NO_PROBE)
            self.assertEqual([], findings(payload, "neutered-checks"), payload["checks"])
        finally:
            r.cleanup()

    def test_caught_js(self):
        with base_repo("b1js-catch") as r:
            r.write(
                "tests/calc.test.ts",
                "describe('add', () => {\n  it.skip('adds', () => {\n    expect(1).toBe(1);\n  });\n});\n",
            )
            r.commit("skip it for now")
            payload, _ = r.run(*NO_PROBE)
            titles = [f["title"] for f in warns(payload, "neutered-checks")]
            self.assertTrue(any("skip" in t for t in titles), titles)


class TestNeuteredSuppressions(unittest.TestCase):
    def test_caught_blanket(self):
        with base_repo("b2-catch") as r:
            r.write("src/calc.py", SRC_AFTER.replace("return a + b", "return a + b  # type: ignore"))
            r.commit("fix types")
            payload, _ = r.run(*NO_PROBE)
            titles = [f["title"] for f in warns(payload, "neutered-checks")]
            self.assertTrue(any("type: ignore" in t for t in titles), titles)

    def test_caught_ts_ignore(self):
        with base_repo("b2ts-catch") as r:
            r.write("src/calc.ts", "// @ts-ignore\nexport const add = (a, b) => a + b;\n")
            r.commit("port to ts")
            payload, _ = r.run(*NO_PROBE)
            titles = [f["title"] for f in warns(payload, "neutered-checks")]
            self.assertTrue(any("@ts-ignore" in t for t in titles), titles)

    def test_control_no_suppression(self):
        with base_repo("b2-control") as r:
            r.write("src/calc.py", SRC_AFTER)
            r.commit("fix types properly")
            payload, _ = r.run(*NO_PROBE)
            self.assertEqual([], findings(payload, "neutered-checks"), payload["checks"])

    def test_scoped_suppression_is_only_info(self):
        with base_repo("b2-scoped") as r:
            r.write(
                "src/calc.py",
                SRC_AFTER.replace("return a + b", "return a + b  # type: ignore[no-any-return]"),
            )
            r.commit("fix types")
            payload, code = r.run(*NO_PROBE)
            self.assertEqual([], warns(payload, "neutered-checks"))
            self.assertTrue(findings(payload, "neutered-checks", "INFO"))
            self.assertEqual(0, code)


class TestNeuteredSwallowedExceptions(unittest.TestCase):
    def test_caught_python(self):
        with base_repo("b3-catch") as r:
            r.write(
                "src/calc.py",
                "def add(a, b):\n    try:\n        return a + b\n    except Exception:\n        pass\n",
            )
            r.commit("harden add()")
            payload, _ = r.run(*NO_PROBE)
            titles = [f["title"] for f in warns(payload, "neutered-checks")]
            self.assertTrue(any("swallowed" in t for t in titles), titles)

    def test_control_python(self):
        with base_repo("b3-control") as r:
            r.write(
                "src/calc.py",
                "import logging\n\n\ndef add(a, b):\n    try:\n        return a + b\n"
                "    except TypeError:\n        logging.exception('bad operands')\n        raise\n",
            )
            r.commit("harden add()")
            payload, _ = r.run(*NO_PROBE)
            self.assertEqual([], findings(payload, "neutered-checks"), payload["checks"])

    def test_caught_js(self):
        with base_repo("b3js-catch") as r:
            r.write(
                "src/calc.ts",
                "export function add(a: number, b: number) {\n  try {\n    return a + b;\n  } catch (e) {}\n}\n",
            )
            r.commit("harden add()")
            payload, _ = r.run(*NO_PROBE)
            titles = [f["title"] for f in warns(payload, "neutered-checks")]
            self.assertTrue(any("swallowed" in t for t in titles), titles)

    def test_control_js(self):
        with base_repo("b3js-control") as r:
            r.write(
                "src/calc.ts",
                "export function add(a: number, b: number) {\n  try {\n    return a + b;\n"
                "  } catch (e) {\n    console.error(e);\n    throw e;\n  }\n}\n",
            )
            r.commit("harden add()")
            payload, _ = r.run(*NO_PROBE)
            self.assertEqual([], findings(payload, "neutered-checks"), payload["checks"])


class TestNeuteredPrecision(unittest.TestCase):
    """The patterns must not fire on text that merely mentions them."""

    def test_pattern_inside_a_python_string_is_not_a_suppression(self):
        with base_repo("b5-string") as r:
            r.write(
                "src/lint_rules.py",
                'RULES = [\n'
                '    ("# type: ignore", "blanket type suppression"),\n'
                '    ("@pytest.mark.skip", "disabled test"),\n'
                ']\n',
            )
            r.commit("describe the rules")
            payload, _ = r.run(*NO_PROBE)
            self.assertEqual([], findings(payload, "neutered-checks"), payload["checks"])

    def test_pattern_inside_a_js_string_is_not_a_suppression(self):
        with base_repo("b5js-string") as r:
            r.write(
                "src/rules.ts",
                "export const RULES = ['// @ts-ignore', '// eslint-disable'];\n",
            )
            r.commit("describe the rules")
            payload, _ = r.run(*NO_PROBE)
            self.assertEqual([], findings(payload, "neutered-checks"), payload["checks"])

    def test_a_real_comment_is_still_a_suppression(self):
        with base_repo("b5-comment") as r:
            r.write("src/rules.ts", "// @ts-ignore\nexport const x = 1;\n")
            r.commit("port")
            self.assertTrue(warns(r.run(*NO_PROBE)[0], "neutered-checks"))

    def test_markdown_is_not_scanned(self):
        with base_repo("b5-md") as r:
            r.write(
                "docs/style.md",
                "Never add a bare `# type: ignore`, and never `@pytest.mark.skip` a test.\n",
            )
            r.commit("write down the rules")
            payload, _ = r.run(*NO_PROBE)
            self.assertEqual([], findings(payload, "neutered-checks"), payload["checks"])

    def test_narrow_except_pass_is_only_info(self):
        with base_repo("b5-narrow") as r:
            r.write(
                "src/calc.py",
                "def add(a, b):\n    try:\n        return a + b\n    except OverflowError:\n        pass\n",
            )
            r.commit("tolerate overflow")
            payload, code = r.run(*NO_PROBE)
            self.assertEqual([], warns(payload, "neutered-checks"))
            self.assertTrue(findings(payload, "neutered-checks", "INFO"))
            self.assertEqual(0, code)


class TestNeuteredWeakenedAssertions(unittest.TestCase):
    def test_caught_python(self):
        r = Repo("b4-catch")
        r.write("src/calc.py", SRC_BEFORE)
        r.write(
            "tests/test_calc.py",
            "import unittest\n\nfrom src.calc import add\n\n\n"
            "class AddTest(unittest.TestCase):\n"
            "    def test_add(self):\n"
            "        self.assertEqual(add(1, 2), 3)\n",
        )
        r.commit("initial")
        try:
            r.write(
                "tests/test_calc.py",
                "import unittest\n\nfrom src.calc import add\n\n\n"
                "class AddTest(unittest.TestCase):\n"
                "    def test_add(self):\n"
                "        self.assertTrue(add(1, 2))\n",
            )
            r.commit("make the suite green")
            payload, code = r.run(*NO_PROBE)
            hits = fails(payload, "neutered-checks")
            self.assertTrue(any("weakened" in f["title"] for f in hits), payload["checks"])
            self.assertEqual(1, code)
        finally:
            r.cleanup()

    def test_caught_js(self):
        r = Repo("b4js-catch")
        r.write(
            "tests/calc.test.ts",
            "describe('add', () => {\n  it('adds', () => {\n    expect(add(1, 2)).toEqual(3);\n  });\n});\n",
        )
        r.commit("initial")
        try:
            r.write(
                "tests/calc.test.ts",
                "describe('add', () => {\n  it('adds', () => {\n    expect(add(1, 2)).toBeTruthy();\n  });\n});\n",
            )
            r.commit("make the suite green")
            payload, code = r.run(*NO_PROBE)
            self.assertTrue(fails(payload, "neutered-checks"), payload["checks"])
            self.assertEqual(1, code)
        finally:
            r.cleanup()

    def test_control_changed_but_still_specific(self):
        r = Repo("b4-control")
        r.write("src/calc.py", SRC_BEFORE)
        r.write(
            "tests/test_calc.py",
            "import unittest\n\nfrom src.calc import add\n\n\n"
            "class AddTest(unittest.TestCase):\n"
            "    def test_add(self):\n"
            "        self.assertEqual(add(1, 2), 3)\n",
        )
        r.commit("initial")
        try:
            r.write(
                "tests/test_calc.py",
                "import unittest\n\nfrom src.calc import add\n\n\n"
                "class AddTest(unittest.TestCase):\n"
                "    def test_add(self):\n"
                "        self.assertEqual(add(2, 2), 4)\n",
            )
            r.commit("update the expected value")
            payload, code = r.run(*NO_PROBE)
            self.assertEqual([], fails(payload, "neutered-checks"), payload["checks"])
            self.assertEqual(0, code)
        finally:
            r.cleanup()


# ---------------------------------------------------------------------------
# C -- what the change left behind
# ---------------------------------------------------------------------------


class TestTracesUncommitted(unittest.TestCase):
    def test_caught(self):
        # Reviewing a commit: a file on disk that is not in it means what was
        # reviewed is not what is there. Asking for a commit range explicitly,
        # since the default would take the working tree as the subject instead.
        with base_repo("c1-catch") as r:
            r.write("src/calc.py", SRC_AFTER)
            r.commit("guard")
            r.write("src/scratch.py", "# left behind by the agent\n")
            payload, _ = r.run("--head", "HEAD", *NO_PROBE)
            titles = [f["title"] for f in warns(payload, "loose-ends")]
            self.assertTrue(any("scratch.py" in t for t in titles), titles)

    def test_not_reported_when_it_is_the_subject(self):
        # With no --head the working tree is what is inspected, so the same file
        # is the change rather than something left beside it.
        with base_repo("c1-subject") as r:
            r.write("src/calc.py", SRC_AFTER)
            r.commit("guard")
            r.write("src/scratch.py", "# the agent stopped without committing\n")
            payload, _ = r.run(*NO_PROBE)
            titles = [f["title"] for f in findings(payload, "loose-ends")]
            self.assertFalse(any("scratch.py" in t for t in titles), titles)
            self.assertTrue(payload["includes_uncommitted"])
            self.assertEqual("working-tree", payload["changeset"])

    def test_control(self):
        with base_repo("c1-control") as r:
            r.write("src/calc.py", SRC_AFTER)
            r.commit("guard")
            payload, _ = r.run(*NO_PROBE)
            self.assertEqual([], findings(payload, "loose-ends"), payload["checks"])


class TestTracesScope(unittest.TestCase):
    def test_caught(self):
        with base_repo("c2-catch") as r:
            r.write("src/calc.py", SRC_AFTER)
            r.write("docs/roadmap.md", "# unrelated\n")
            r.commit("guard, and some notes")
            payload, _ = r.run("--scope", "src/*", *NO_PROBE)
            titles = [f["title"] for f in warns(payload, "loose-ends")]
            self.assertTrue(any("roadmap.md" in t for t in titles), titles)

    def test_control(self):
        with base_repo("c2-control") as r:
            r.write("src/calc.py", SRC_AFTER)
            r.commit("guard")
            payload, _ = r.run("--scope", "src/*", *NO_PROBE)
            self.assertEqual([], findings(payload, "loose-ends"), payload["checks"])


class TestTracesTestClaim(unittest.TestCase):
    def test_caught(self):
        with base_repo("c3-catch") as r:
            r.write("src/calc.py", SRC_AFTER)
            r.commit("Guard against None\n\nIncludes comprehensive tests for the new behaviour.")
            payload, _ = r.run(*NO_PROBE)
            titles = [f["title"] for f in warns(payload, "loose-ends")]
            self.assertTrue(any("claims tests" in t for t in titles), titles)

    def test_control_claim_backed_by_tests(self):
        with base_repo("c3-control") as r:
            r.write("src/calc.py", SRC_AFTER)
            r.write(
                "tests/test_guard.py",
                "import pytest\n\nfrom src.calc import add\n\n\n"
                "def test_rejects_none():\n    with pytest.raises(ValueError):\n        add(None, 1)\n",
            )
            r.commit("Guard against None\n\nIncludes comprehensive tests for the new behaviour.")
            payload, _ = r.run(*NO_PROBE)
            titles = [f["title"] for f in warns(payload, "loose-ends")]
            self.assertFalse(any("claims tests" in t for t in titles), titles)

    def test_control_message_without_a_claim(self):
        with base_repo("c3-control2") as r:
            r.write("src/calc.py", SRC_AFTER)
            r.commit("Guard against None inputs in add()")
            payload, _ = r.run(*NO_PROBE)
            self.assertEqual([], findings(payload, "loose-ends"), payload["checks"])


class TestTracesArtifacts(unittest.TestCase):
    def test_caught(self):
        with base_repo("c4-catch") as r:
            r.write("src/calc.py", SRC_AFTER)
            r.write("dist/bundle.js", "// generated\n")
            r.commit("guard")
            payload, _ = r.run(*NO_PROBE)
            titles = [f["title"] for f in warns(payload, "loose-ends")]
            self.assertTrue(any("bundle.js" in t for t in titles), titles)

    def test_lockfile_is_only_info(self):
        with base_repo("c4-lock") as r:
            r.write("src/calc.py", SRC_AFTER)
            r.write("package-lock.json", '{"lockfileVersion": 3}\n')
            r.commit("guard")
            payload, code = r.run(*NO_PROBE)
            infos = [f["title"] for f in findings(payload, "loose-ends", "INFO")]
            self.assertTrue(any("package-lock.json" in t for t in infos), infos)
            self.assertEqual(0, code)


# ---------------------------------------------------------------------------
# plumbing
# ---------------------------------------------------------------------------


class TestCli(unittest.TestCase):
    def test_skip_disables_a_check(self):
        with base_repo("cli-skip") as r:
            r.write("tests/test_empty.py", "def test_todo():\n    pass\n")
            r.commit("add coverage")
            payload, code = r.run("--skip", "hollow-tests,revert-probe")
            self.assertEqual("skipped", check_status(payload, "hollow-tests"))
            self.assertEqual(0, code)

    def test_only_runs_one_check(self):
        with base_repo("cli-only") as r:
            r.write("tests/test_empty.py", "def test_todo():\n    pass\n")
            r.commit("add coverage")
            payload, _ = r.run("--only", "hollow-tests")
            self.assertEqual("skipped", check_status(payload, "loose-ends"))
            self.assertEqual("fail", check_status(payload, "hollow-tests"))

    def test_exit_zero(self):
        with base_repo("cli-exitzero") as r:
            r.write("tests/test_empty.py", "def test_todo():\n    pass\n")
            r.commit("add coverage")
            payload, code = r.run("--exit-zero", *NO_PROBE)
            self.assertEqual(0, code)
            self.assertTrue(fails(payload))

    def test_unknown_check_is_rejected(self):
        from unfaked import cli

        self.assertEqual(2, cli.main(["--skip", "nonsense"]))

    def test_json_file_writes_the_payload_and_keeps_the_report_on_stdout(self):
        # CI wants both from one run: the readable report in the log, the
        # payload as an artefact. Re-running to get the second format would
        # mean running the probe twice.
        import contextlib
        import io
        import json as json_module
        import tempfile

        from unfaked import cli

        with base_repo("cli-jsonfile") as r:
            r.write("tests/test_empty.py", "def test_todo():\n    pass\n")
            r.commit("add coverage")
            target = os.path.join(tempfile.mkdtemp(), "report.json")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                code = cli.main([r.path, "--json-file", target, "--no-color"] + list(NO_PROBE))

            self.assertEqual(1, code)
            printed = buf.getvalue()
            self.assertIn("hollow-tests", printed)
            self.assertNotIn('"counts"', printed)

            with open(target) as fh:
                payload = json_module.load(fh)
            self.assertTrue(fails(payload, "hollow-tests"))

    def test_json_file_alongside_json_writes_both(self):
        import contextlib
        import io
        import json as json_module
        import tempfile

        from unfaked import cli

        with base_repo("cli-jsonboth") as r:
            r.write("tests/test_empty.py", "def test_todo():\n    pass\n")
            r.commit("add coverage")
            target = os.path.join(tempfile.mkdtemp(), "report.json")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                cli.main([r.path, "--json", "--json-file", target] + list(NO_PROBE))

            self.assertEqual(json_module.loads(buf.getvalue()), json_module.load(open(target)))

    def test_json_file_creates_the_directory_it_was_given(self):
        # `--json-file reports/unfaked.json` should not need an mkdir in front
        # of it.
        import contextlib
        import io
        import json as json_module

        from unfaked import cli

        with base_repo("cli-jsonmkdir") as r:
            r.write("tests/test_ok.py", "def test_ok():\n    assert True\n")
            r.commit("add coverage")
            target = os.path.join(r.path, "reports", "nested", "unfaked.json")
            with contextlib.redirect_stdout(io.StringIO()):
                cli.main([r.path, "--json-file", target] + list(NO_PROBE))

            # Before this, the missing `reports/nested` was an error and no
            # file was written at all.
            with open(target) as fh:
                self.assertIn("counts", json_module.load(fh))

    def test_json_file_that_cannot_be_written_reports_rather_than_crashes(self):
        import contextlib
        import io

        from unfaked import cli

        with base_repo("cli-jsonbad") as r:
            r.write("tests/test_ok.py", "def test_ok():\n    assert True\n")
            r.commit("add coverage")
            # A file where a directory would have to be: no mkdir can fix this
            # one, so it still has to be reported rather than raised.
            blocker = os.path.join(r.path, "blocker")
            with open(blocker, "w") as fh:
                fh.write("not a directory\n")
            with contextlib.redirect_stdout(io.StringIO()):
                with contextlib.redirect_stderr(io.StringIO()) as err:
                    code = cli.main(
                        [r.path, "--json-file", os.path.join(blocker, "d.json")] + list(NO_PROBE)
                    )
            self.assertEqual(2, code)
            self.assertIn("cannot write", err.getvalue())


if __name__ == "__main__":
    unittest.main()
