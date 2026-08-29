"""A single self-contained HTML file for the report.

The terminal output is for the moment the agent stops. This is for the moment
after: something was flagged, and you want to read the evidence rather than
scroll back through a session. It is one file with no external requests -- no
CDN, no font, no script that phones anywhere -- so it opens the same offline, in
a CI artifact, or attached to a review.

Zero dependencies applies here too, so the markup is written by hand and
everything user-supplied goes through `escape()`.
"""

from html import escape
from typing import Any, Dict, List

# The probe is why this tool exists, so it gets the picture and everything else
# gets a row. `added` splits into the tests that noticed the change and the ones
# that pass either way, and that ratio is the only number worth a graphic.
_CSS = """
*,*::before,*::after{box-sizing:border-box}
body{margin:0;padding:2.5rem 1.25rem 4rem;font:15px/1.6 ui-sans-serif,-apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;
 background:var(--bg);color:var(--fg);-webkit-text-size-adjust:100%}
.wrap{max-width:64rem;margin:0 auto}
:root{--bg:#fbfbfa;--fg:#1c1b1a;--dim:#6d6a66;--line:#e3e0dc;--card:#fff;
 --fail:#b3261e;--warn:#8a6100;--ok:#1c6b3f;--accent:#2f5fd0;--code:#f4f2ef}
@media (prefers-color-scheme:dark){:root{--bg:#131211;--fg:#eceae7;--dim:#9b9691;--line:#2c2a28;--card:#1a1918;
 --fail:#ff8a80;--warn:#e8b339;--ok:#6ed09a;--accent:#8fb0ff;--code:#211f1d}}
:root[data-theme="light"]{--bg:#fbfbfa;--fg:#1c1b1a;--dim:#6d6a66;--line:#e3e0dc;--card:#fff;
 --fail:#b3261e;--warn:#8a6100;--ok:#1c6b3f;--accent:#2f5fd0;--code:#f4f2ef}
:root[data-theme="dark"]{--bg:#131211;--fg:#eceae7;--dim:#9b9691;--line:#2c2a28;--card:#1a1918;
 --fail:#ff8a80;--warn:#e8b339;--ok:#6ed09a;--accent:#8fb0ff;--code:#211f1d}
h1{font-size:1rem;font-weight:650;margin:0;letter-spacing:-.01em}
.sub{color:var(--dim);font-size:.8rem;margin:.3rem 0 0}
.mono{font-family:ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,monospace}
.head{display:flex;flex-wrap:wrap;gap:.5rem 1rem;align-items:baseline;
 border-bottom:1px solid var(--line);padding-bottom:1rem;margin-bottom:2rem}
.verdict{border-left:3px solid var(--ok);padding:.1rem 0 .1rem 1rem;margin:0 0 2rem;
 font-size:1.45rem;line-height:1.35;font-weight:600;letter-spacing:-.02em}
.verdict.fail{border-color:var(--fail)}.verdict.warn{border-color:var(--warn)}
.hint{color:var(--dim);font-size:.85rem;margin:-1.25rem 0 2rem;padding-left:1.05rem}
.probe{border:1px solid var(--line);border-radius:10px;padding:1.1rem 1.25rem;margin:0 0 2rem;background:var(--card)}
.probe h2{font-size:.72rem;text-transform:uppercase;letter-spacing:.09em;color:var(--dim);margin:0 0 .85rem;font-weight:600}
.bar{display:flex;height:.55rem;border-radius:99px;overflow:hidden;background:var(--line)}
.bar i{display:block}
.bar .d{background:var(--ok)}.bar .s{background:var(--warn)}
.key{display:flex;flex-wrap:wrap;gap:1.25rem;margin-top:.75rem;font-size:.82rem;color:var(--dim)}
.key b{color:var(--fg);font-weight:600}
.key span::before{content:"";display:inline-block;width:.5rem;height:.5rem;border-radius:2px;margin-right:.45rem}
.key .d::before{background:var(--ok)}.key .s::before{background:var(--warn)}
table.checks{width:100%;border-collapse:collapse;margin:0 0 2.5rem;font-size:.88rem}
table.checks td{padding:.55rem .5rem;border-bottom:1px solid var(--line);vertical-align:top}
table.checks td:first-child{width:1.5rem;text-align:center}
table.checks .name{font-weight:600;white-space:nowrap}
table.checks .desc{color:var(--dim)}
table.checks .tally{text-align:right;white-space:nowrap;color:var(--dim)}
table.checks .note{color:var(--dim);font-size:.82rem;padding-top:0}
.f-fail{color:var(--fail)}.f-warn{color:var(--warn)}.f-ok{color:var(--ok)}.f-dim{color:var(--dim)}
.finding{border:1px solid var(--line);border-left-width:3px;border-radius:10px;background:var(--card);
 padding:1.1rem 1.25rem;margin:0 0 1rem}
.finding.FAIL{border-left-color:var(--fail)}.finding.WARN{border-left-color:var(--warn)}
.finding.INFO{border-left-color:var(--line)}
.finding .loc{font-size:.78rem;color:var(--accent);word-break:break-all}
.finding .t{font-weight:600;margin:.3rem 0 0;letter-spacing:-.01em}
pre{margin:.85rem 0 0;padding:.75rem .9rem;background:var(--code);border-radius:7px;
 overflow-x:auto;font-size:.8rem;line-height:1.55}
pre.snippet .ln{color:var(--dim);user-select:none}
dl{margin:.9rem 0 0;display:grid;grid-template-columns:auto 1fr;gap:.35rem .85rem;font-size:.87rem}
dt{color:var(--dim);font-size:.72rem;text-transform:uppercase;letter-spacing:.07em;padding-top:.18rem}
dd{margin:0}
footer{margin-top:2.5rem;padding-top:1rem;border-top:1px solid var(--line);
 color:var(--dim);font-size:.78rem;display:flex;flex-wrap:wrap;gap:.5rem 1rem;justify-content:space-between}
"""

_GLYPH = {"fail": "&#10007;", "warn": "&#9650;", "ok": "&#10003;",
          "inconclusive": "?", "skipped": "&ndash;"}
_KLASS = {"fail": "f-fail", "warn": "f-warn", "ok": "f-ok"}


def _probe_panel(check: Dict[str, Any]) -> str:
    """The one number the terminal report cannot draw.

    Only shown when the probe actually ran: an inconclusive probe has no ratio,
    and a bar drawn from zeroes would imply it measured something.
    """
    stats = check.get("stats") or {}
    added = stats.get("probed") or 0
    if check.get("status") not in ("ok", "warn", "fail") or not added:
        return ""
    survived = stats.get("survived") or 0
    distinguishing = max(added - survived, 0)
    pct = (distinguishing * 100.0) / added
    runner = escape(str(stats.get("runner") or ""))
    return (
        '<section class="probe">'
        '<h2>Revert probe%s</h2>'
        '<div class="bar"><i class="d" style="width:%.4f%%"></i>'
        '<i class="s" style="width:%.4f%%"></i></div>'
        '<div class="key">'
        '<span class="d"><b>%d</b> fail with the change reverted</span>'
        '<span class="s"><b>%d</b> pass either way</span>'
        "</div></section>"
        % (
            " &middot; %s" % runner if runner else "",
            pct,
            100.0 - pct,
            distinguishing,
            survived,
        )
    )


def _snippet(lines: List[str]) -> str:
    if not lines:
        return ""
    rows = []
    for raw in lines[:12]:
        num, _, code = raw.partition("\t")
        if not _:
            num, code = "", raw
        rows.append(
            '<span class="ln">%s</span>  %s' % (escape(num.rjust(5)), escape(code.rstrip()))
        )
    return '<pre class="snippet mono">%s</pre>' % "\n".join(rows)


def _finding(f: Dict[str, Any]) -> str:
    sev = str(f.get("severity") or "INFO")
    loc = f.get("file") or f.get("check") or ""
    if f.get("file") and f.get("line"):
        loc = "%s:%s" % (f["file"], f["line"])
    parts = [
        '<article class="finding %s">' % escape(sev),
        '<div class="loc mono">%s</div>' % escape(str(loc)),
        '<p class="t">%s</p>' % escape(str(f.get("title") or "")),
        _snippet(list(f.get("snippet") or [])),
    ]
    rows = []
    for label, key in (("why", "why"), ("fix", "fix")):
        if f.get(key):
            rows.append("<dt>%s</dt><dd>%s</dd>" % (label, escape(str(f[key]))))
    if f.get("command"):
        # Never wrapped or shortened: the point of this line is that it can be
        # pasted, and a command that has been prettied up cannot be.
        rows.append(
            '<dt>run</dt><dd><pre class="mono">%s</pre></dd>' % escape(str(f["command"]))
        )
    if rows:
        parts.append("<dl>%s</dl>" % "".join(rows))
    parts.append("</article>")
    return "".join(parts)


def _checks_table(checks: List[Dict[str, Any]]) -> str:
    rows = []
    for c in checks:
        status = str(c.get("status") or "")
        n_fail = sum(1 for f in c.get("findings") or [] if f.get("severity") == "FAIL")
        n_warn = sum(1 for f in c.get("findings") or [] if f.get("severity") == "WARN")
        if status == "fail":
            tally = "%d fail" % n_fail
        elif status == "warn":
            tally = "%d warn" % n_warn
        elif status == "ok":
            tally = "clean"
        elif status == "skipped":
            tally = "skipped"
        else:
            tally = "not run"
        rows.append(
            '<tr><td class="%s">%s</td><td class="name mono">%s</td>'
            '<td class="desc">%s</td><td class="tally">%s</td></tr>'
            % (
                _KLASS.get(status, "f-dim"),
                _GLYPH.get(status, "?"),
                escape(str(c.get("check") or "")),
                escape(str(c.get("title") or "")),
                escape(tally),
            )
        )
        if c.get("note") and status in ("inconclusive", "skipped"):
            rows.append('<tr><td></td><td colspan="3" class="note">%s</td></tr>' % escape(str(c["note"])))
    return '<table class="checks">%s</table>' % "".join(rows)


def render(payload: Dict[str, Any]) -> str:
    """The whole report as one HTML document."""
    counts = payload.get("counts") or {}
    n_fail = counts.get("fail") or 0
    n_warn = counts.get("warn") or 0
    tone = "fail" if n_fail else ("warn" if n_warn else "ok")

    repo = str(payload.get("repo") or "")
    label = repo.rstrip("/").rsplit("/", 1)[-1] or repo

    facts = [
        "%d file%s changed" % (payload.get("files_changed") or 0,
                               "" if payload.get("files_changed") == 1 else "s"),
        "%d test%s added" % (payload.get("tests_added") or 0,
                             "" if payload.get("tests_added") == 1 else "s"),
    ]
    if payload.get("runner"):
        facts.append(str(payload["runner"]))

    checks = list(payload.get("checks") or [])
    probe = next((c for c in checks if c.get("check") == "revert-probe"), None)

    findings = [f for c in checks for f in (c.get("findings") or [])
                if f.get("severity") in ("FAIL", "WARN")]
    order = {"FAIL": 0, "WARN": 1}
    findings.sort(key=lambda f: (order.get(str(f.get("severity")), 2),
                                 str(f.get("file") or ""), f.get("line") or 0))

    tallies = []
    if n_fail:
        tallies.append("%d fail" % n_fail)
    if n_warn:
        tallies.append("%d warn" % n_warn)

    body = [
        '<div class="wrap">',
        '<div class="head"><h1 class="mono">unfaked &middot; %s</h1>'
        '<p class="sub mono">%s</p><p class="sub">%s</p></div>'
        % (escape(label), escape(str(payload.get("range") or "")),
           escape("  ·  ".join(facts))),
        '<p class="verdict %s">%s</p>' % (tone, escape(str(payload.get("headline") or ""))),
    ]
    if payload.get("hint"):
        body.append('<p class="hint">%s</p>' % escape(str(payload["hint"])))
    if probe:
        body.append(_probe_panel(probe))
    body.append(_checks_table(checks))
    body.extend(_finding(f) for f in findings)
    body.append(
        "<footer><span>%s</span><span class=\"mono\">unfaked %s &middot; exit %s</span></footer>"
        % (
            escape(" · ".join(tallies) or "0 findings"),
            escape(str(payload.get("version") or "")),
            escape(str(payload.get("exit_code", 0))),
        )
    )
    body.append("</div>")

    return (
        "<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>unfaked &middot; %s</title><style>%s</style></head><body>%s</body></html>\n"
        % (escape(label), _CSS, "".join(body))
    )
