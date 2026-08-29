# Claude Code hook

Run `unfaked` the moment Claude says it's done, before you read the summary.

Add to `.claude/settings.json` in your project (or `~/.claude/settings.json` for
every project):

```json
{
  "hooks": {
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "uvx unfaked -q --exit-zero"
          }
        ]
      }
    ]
  }
}
```

That is the whole integration. Defaults are already what a hook needs:

- **the revert probe runs** — the check the other three cannot stand in for. It
  is given 20 seconds; if the suite needs longer the report says so and names
  the command to run it without a budget. Static checks alone are 222ms on
  `anthropic-sdk-python`; the probe adds one filtered run of the added tests
  against the current source and one against the base.
- **`--exit-zero`** — never fails the hook. A verification tool that can kill
  your session is worse than no verification tool.
- **`-q`** — prints nothing at all unless there is something to report. A line
  that appears on turns where nothing happened is how a hook gets muted.
- **silent where it does not apply** — not a git repo, or an empty diff, and it
  exits 0 with no output.

Add `--probe-budget 5` if 20 seconds is more than you will wait, or `--fast` to
go back to static checks only.

## Reviewing the whole session, not the last commit

An agent does not reliably finish on a commit. The usual shape is edit, edit,
run tests, edit, "done" — so record where the session started and read
everything since:

```json
{
  "hooks": {
    "SessionStart": [ { "hooks": [
      { "type": "command", "command": "uvx unfaked --session-start .git/unfaked-session.json" }
    ] } ],
    "Stop": [ { "hooks": [
      { "type": "command", "command": "uvx unfaked -q --session-file .git/unfaked-session.json --exit-zero" }
    ] } ]
  }
}
```

**Use the pair.** Without it, a turn that changed nothing leaves a clean tree, so
the range falls back to the last commit and every turn re-reports the same
commit until you make another one. With the pair, a turn that changed nothing
has an empty range and the hook stays silent.

The header always names which range was read.

## Reading it afterwards

The terminal report is for the moment the agent stops. When something is flagged
and you want to read the evidence without scrolling back through a session, add
a second output:

```json
{ "type": "command",
  "command": "uvx unfaked -q --exit-zero --html-file .unfaked/report.html" }
```

One self-contained file — no CDN, no font, no script — so it opens the same
offline, as a CI artifact, or attached to a review. It carries the revert
probe's ratio as a bar, then every finding with its snippet, its reasoning, and
the command to reproduce it. `--json-file` still writes the machine-readable
payload from the same run.

## When the probe does not fit

The probe runs the added tests twice: once as committed, once against the base
in a detached worktree. That is two filtered runs, not two full suites — 1.7s on
this repository. When it does not fit in the budget the report says which, and
you can lift it:

```console
$ uvx unfaked --deep
```

## Verifying the hook is wired up

```console
$ uvx unfaked --version
$ claude --debug        # hook fires are logged at session end
```
