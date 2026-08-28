"""Build throwaway git repositories and run unfaked over them.

Every case in the corpus is a pair: one repo where the check must fire and one
control repo, as close to identical as possible, where it must not. A check
that cannot tell the pair apart is not worth shipping.
"""

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unfaked import cli  # noqa: E402


def _git(repo, *args):
    subprocess.run(
        ["git", "-C", repo] + list(args),
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


class Repo:
    """A temp git repo with a base commit and a change commit on top."""

    def __init__(self, name="case"):
        self.path = tempfile.mkdtemp(prefix="unfaked-%s-" % name)
        _git(self.path, "init", "-q", "-b", "main")
        _git(self.path, "config", "user.email", "corpus@unfaked.test")
        _git(self.path, "config", "user.name", "unfaked corpus")
        _git(self.path, "config", "commit.gpgsign", "false")

    def write(self, rel, content):
        full = os.path.join(self.path, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w") as fh:
            fh.write(content)
        return self

    def remove(self, rel):
        os.unlink(os.path.join(self.path, rel))
        return self

    def commit(self, message):
        _git(self.path, "add", "-A")
        _git(self.path, "commit", "-q", "-m", message, "--no-verify")
        return self

    def run(self, *argv):
        """Run unfaked --json and return (payload, exit_code)."""
        buf = io.StringIO()
        args = [self.path, "--json"] + list(argv)
        with contextlib.redirect_stdout(buf):
            code = cli.main(args)
        return json.loads(buf.getvalue()), code

    def cleanup(self):
        shutil.rmtree(self.path, ignore_errors=True)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.cleanup()


def findings(payload, check=None, severity=None):
    out = []
    for c in payload["checks"]:
        if check and c["check"] != check:
            continue
        for f in c["findings"]:
            if severity and f["severity"] != severity:
                continue
            out.append(f)
    return out


def fails(payload, check=None):
    return findings(payload, check, "FAIL")


def warns(payload, check=None):
    return findings(payload, check, "WARN")


def check_status(payload, name):
    for c in payload["checks"]:
        if c["check"] == name:
            return c["status"]
    raise KeyError(name)


def check_note(payload, name):
    for c in payload["checks"]:
        if c["check"] == name:
            return c.get("note") or ""
    raise KeyError(name)


NO_PROBE = ("--skip", "revert-probe")
