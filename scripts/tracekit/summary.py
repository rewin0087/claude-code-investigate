"""The markdown summary relayed in the chat (and saved as summary.md)."""
from urllib.parse import quote

VERDICT_LABEL = {
    "bug_found": "🐞 Bug found", "risks_found": "⚠️ Risks found", "works_as_expected": "✅ Works as expected",
    "reproduced": "🎯 Reproduced", "not_reproduced": "❔ Not reproduced", "inconclusive": "❔ Inconclusive",
}
STATUS_ICON = {"pass": "✅", "fail": "❌", "bug": "🐞", "risk": "⚠️", "expected_error": "🛑", "reproduced": "🎯",
               "inconclusive": "❔"}


def file_url(path):
    return "file://" + quote(path)


def _cell(text, n=90):
    t = " ".join(str(text or "").split()).replace("|", "\\|")
    return t if len(t) <= n else t[:n - 1] + "…"


def _loc(o):
    if not o.get("file"):
        return ""
    return "`%s%s`" % (o["file"], (":%s" % o["line"]) if o.get("line") else "")


def terminal_summary(inv, meta, report_path):
    s = inv.get("summary") or {}
    L = ["## Investigation: %s" % inv.get("title", ""), ""]
    L.append("**%s** — %s" % (VERDICT_LABEL.get(s.get("verdict"), s.get("verdict")), s.get("headline", "")))
    L.append("")
    tgt = inv.get("target") or {}
    scs = inv.get("scenarios") or []
    executed = sum(1 for x in scs if x.get("evidence") == "executed")
    bits = []
    if tgt.get("label"):
        bits.append("Target: `%s`" % tgt["label"])
    bits.append("Scenarios: %d (%d executed with the tracer, %d simulated)" % (len(scs), executed, len(scs) - executed))
    if s.get("stack"):
        bits.append("Stack: %s" % s["stack"])
    L.append(" · ".join(bits))
    if s.get("overview"):
        L += ["", s["overview"].strip()]
    if scs:
        L += ["", "### Scenarios", "", "| # | Scenario | Outcome | Status |", "|---|---|---|---|"]
        for sc in scs:
            L.append("| %s | %s | %s | %s %s |" % (sc.get("id"), _cell(sc.get("title"), 70),
                                                  _cell((sc.get("outcome") or {}).get("result"), 80),
                                                  STATUS_ICON.get(sc.get("status"), ""), sc.get("status", "")))
    bugs = inv.get("bugs") or []
    if bugs:
        L += ["", "### Bugs"]
        order = {"critical": 0, "major": 1, "minor": 2}
        for i, b in enumerate(sorted(bugs, key=lambda b: order.get(b.get("severity"), 9)), 1):
            L.append("%d. **[%s] %s** %s" % (i, b.get("severity"), b.get("title"), _loc(b)))
            if b.get("root_cause"):
                L.append("   Root cause: %s" % _cell(b["root_cause"], 300))
            if (b.get("fix") or {}).get("description"):
                L.append("   Fix: %s" % _cell(b["fix"]["description"], 300))
            if b.get("scenarios"):
                L.append("   Seen in: %s" % ", ".join(b["scenarios"]))
    rep = inv.get("reproduction")
    if rep:
        L += ["", "### Reproduction"]
        err = rep.get("error") or {}
        if err:
            L.append("Error: `%s%s`" % (err.get("type", ""), (": " + _cell(err.get("message"), 160)) if err.get("message") else ""))
        L.append("%s%s" % ("Confirmed by running it. " if rep.get("confirmed") else "Not confirmed by a run (simulated). ",
                           ("Fails at %s step %s." % (rep.get("scenario"), rep.get("failing_step"))) if rep.get("failing_step") else ""))
        if rep.get("root_cause"):
            L.append("Root cause: %s" % rep["root_cause"].strip())
        for i, st in enumerate(rep.get("steps_to_reproduce") or [], 1):
            L.append("%d. %s" % (i, st))
    ins = s.get("insights") or []
    if ins:
        L += ["", "### Insights"]
        for it in ins:
            L.append("- " + (("**%s** — %s" % (it.get("title"), it.get("body", ""))) if isinstance(it, dict) else str(it)))
    recs = s.get("recommendations") or []
    if recs:
        L += ["", "### Recommendations"]
        L.extend("- " + str(r) for r in recs)
    nc = (inv.get("coverage") or {}).get("not_covered") or []
    if nc:
        L += ["", "Not covered: " + "; ".join(str(x) for x in nc)]
    L += ["", "Full report (data timeline per scenario, flow map, code): [%s](%s)" % (report_path, file_url(report_path))]
    return "\n".join(L)
