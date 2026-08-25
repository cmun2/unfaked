"""The one thing every check produces."""

from typing import Any, Dict, List, Optional

FAIL = "FAIL"
WARN = "WARN"
INFO = "INFO"

_ORDER = {FAIL: 0, WARN: 1, INFO: 2}


class Finding:
    """A single observation, with the evidence that backs it.

    Every field except `severity`/`check`/`title` is optional, but a finding
    with no `file` and no `command` is a finding we cannot defend, so checks
    are expected to always supply one of the two.
    """

    __slots__ = (
        "check",
        "severity",
        "title",
        "file",
        "line",
        "snippet",
        "why",
        "fix",
        "command",
        "extra",
    )

    def __init__(
        self,
        check: str,
        severity: str,
        title: str,
        file: Optional[str] = None,
        line: Optional[int] = None,
        snippet: Optional[List[str]] = None,
        why: str = "",
        fix: str = "",
        command: Optional[str] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.check = check
        self.severity = severity
        self.title = title
        self.file = file
        self.line = line
        self.snippet = snippet or []
        self.why = why
        self.fix = fix
        self.command = command
        self.extra = extra or {}

    @property
    def location(self) -> str:
        if self.file and self.line:
            return "%s:%d" % (self.file, self.line)
        return self.file or ""

    def sort_key(self):
        return (_ORDER.get(self.severity, 9), self.check, self.file or "", self.line or 0)

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "check": self.check,
            "severity": self.severity,
            "title": self.title,
            "file": self.file,
            "line": self.line,
            "snippet": self.snippet,
            "why": self.why,
            "fix": self.fix,
            "command": self.command,
        }
        if self.extra:
            d["extra"] = self.extra
        return d


class CheckResult:
    """What one check has to say, whether or not it found anything."""

    __slots__ = ("name", "title", "findings", "status", "note", "skipped", "stats")

    def __init__(self, name: str, title: str) -> None:
        self.name = name
        self.title = title
        self.findings: List[Finding] = []
        self.status = "ok"  # ok | fail | warn | inconclusive | skipped
        self.note = ""
        self.skipped = False
        self.stats: Dict[str, Any] = {}

    def add(self, f: Finding) -> None:
        self.findings.append(f)

    def finalize(self) -> "CheckResult":
        if self.skipped:
            self.status = "skipped"
        elif self.status == "inconclusive":
            pass
        elif any(f.severity == FAIL for f in self.findings):
            self.status = "fail"
        elif any(f.severity == WARN for f in self.findings):
            self.status = "warn"
        else:
            self.status = "ok"
        return self

    def to_dict(self) -> Dict[str, Any]:
        return {
            "check": self.name,
            "title": self.title,
            "status": self.status,
            "note": self.note,
            "stats": self.stats,
            "findings": [f.to_dict() for f in self.findings],
        }
