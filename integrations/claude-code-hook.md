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

- **fast mode** — static checks only, no test run. Measured at 222ms on
  `anthropic-sdk-python` and 572ms on a 617-server dataset repo.
- **`--exit-zero`** — never fails the hook. A verification tool that can kill
  your session is worse than no verification tool.
- **`-q`** — one headline line when there is nothing to report. Without it you
  get the full table after every turn, which is how a hook gets muted.
- **silent where it does not apply** — not a git repo, or an empty diff, and it
  exits 0 with no output.

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

Without the pair, the default still covers the common case: if the working tree
differs from `HEAD` those changes are what gets read, and if it is clean the last
commit is. The header always names which of the two it was.

## Adding the probe back

The revert probe re-runs the suite once per added test, which is too slow for
this hook. Run it yourself when the summary looks too good:

```console
$ uvx unfaked --deep
```

## Verifying the hook is wired up

```console
$ uvx unfaked --version
$ claude --debug        # hook fires are logged at session end
```
