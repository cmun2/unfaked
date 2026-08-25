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
            "command": "uvx unfaked --exit-zero"
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
- **quiet when clean** — one headline line, nothing else.
- **silent where it does not apply** — not a git repo, or an empty diff, and it
  exits 0 with no output.

## Reviewing the whole session, not the last commit

`Stop` fires once per turn, and the default range is `HEAD~1..HEAD`. If your
agent made several commits, point it at where the session started:

```json
"command": "uvx unfaked --base $(git rev-parse HEAD@{1}) --exit-zero"
```

If your agent has not committed at all, there is nothing in the diff to inspect
— `loose-ends` will say so.

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
