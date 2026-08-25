---
name: unfaked
description: >-
  Check whether a change actually did the work or just made the checks pass.
  Use before reporting a task complete, when reviewing a commit or PR written
  by an agent, or when a test suite went green suspiciously fast. Catches
  tests that cannot fail, tests that pass with the change reverted, checks
  switched off instead of satisfied, and edits left outside the task.
---

# unfaked

You are about to report that a task is done. Before you do, check that it is.

This is not a review of whether the code is good. It is a check for four
specific ways a change can look finished without being finished — the ways
that survive a passing test suite.

## Run it

```bash
uvx unfaked
```

Nothing to install, no API key, no network. It reads `HEAD~1..HEAD` by default.
If the work spans several commits, pass the ref you started from:

```bash
uvx unfaked --base <ref-before-your-work>
```

If nothing was committed, commit first — this reads the diff, not the
worktree.

## Then run the probe

The default is static and takes well under a second. The check that matters
most is not static:

```bash
uvx unfaked --deep
```

`--deep` reverts your source change, re-runs only the tests you added, and
reports any that still pass. A test that passes with the change reverted did
not test the change. This runs the suite once per added test, so it takes
minutes on a large repo — run it anyway before claiming a fix is verified.

## Reading the output

Every finding carries the file and line, the code, why it is a problem, and a
`run` line that reproduces it. Take the `run` line seriously: it is how you
confirm the tool is right rather than assuming it.

- **FAIL** — evidence the change was made to pass rather than to work. Fix
  before reporting done.
- **WARN** — legitimate sometimes. Decide, and say which you decided.
- **not run** — the check reached no verdict. Do not read this as a pass.
  The note says what it needed.

## What to do with what it finds

Fix it, then re-run. Do not report the finding to the user as a caveat and
move on — a hollow test is a task that is not finished.

If you disagree with a finding, run its `run` line and say what it showed. A
disagreement backed by the reproduction is useful; one without it is not.

## Do not

- Do not delete or weaken the tests it flags to make it quiet. That is the
  behaviour it exists to catch, and `neutered-checks` will catch that too.
- Do not skip `--deep` on a bug fix because the suite is green. Green is what
  a hollow test looks like.
- Do not treat `not run` as `clean` in your summary to the user.
