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


def cell(text, n=90):
    t = " ".join(str(text or "").split()).replace("|", "\\|")
    return t if len(t) <= n else t[:n - 1] + "…"


def loc(o):
    if not o.get("file"):
        return ""
    return "`%s%s`" % (o["file"], (":%s" % o["line"]) if o.get("line") else "")


class SummaryWriter(object):
    """Markdown summary: verdict, scenario table, bugs, reproduction, insights, report link."""

    def __init__(self, inv, report_path, traces=None):
        self.inv, self.report_path, self.traces = inv, report_path, traces or {}

    def write(self):
        L = self._header()
        for section in (self._scenarios, self._bugs, self._reproduction, self._insights, self._recommendations, self._coverage):
            L.extend(section())
        L += ["", "Full report (data timeline per scenario, flow map, code): [%s](%s)" % (self.report_path, file_url(self.report_path))]
        return "\n".join(L)

    def _header(self):
        inv = self.inv
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
        dev = sum(1 for x in scs if x.get("evidence") == "executed" and x.get("environment") == "development")
        bits.append("Scenarios: %d (%d executed with the tracer%s; %d simulated)" % (
            len(scs), executed, (" — %d of them on development data in a rolled-back sandbox" % dev) if dev else "", len(scs) - executed))
        if s.get("stack"):
            bits.append("Stack: %s" % s["stack"])
        L.append(" · ".join(bits))
        refs = inv.get("reference_data") or []
        if refs:
            L.append("Reference data: " + ", ".join("`%s`" % (r.get("ref") or r.get("name")) for r in refs))
        for sid, t in sorted(self.traces.items()):
            for sb in t.get("sandboxes") or []:
                if sb.get("warning"):
                    L.append("⚠️ %s (sandbox %s): %s" % (sid, sb.get("name"), sb["warning"]))
        if s.get("overview"):
            L += ["", s["overview"].strip()]
        return L

    def _scenarios(self):
        scs = self.inv.get("scenarios") or []
        if not scs:
            return []
        L = ["", "### Scenarios", "", "| # | Scenario | Outcome | Status |", "|---|---|---|---|"]
        for sc in scs:
            L.append("| %s | %s | %s | %s %s |" % (sc.get("id"), cell(sc.get("title"), 70),
                                                  cell((sc.get("outcome") or {}).get("result"), 80),
                                                  STATUS_ICON.get(sc.get("status"), ""), sc.get("status", "")))
        return L

    def _bugs(self):
        bugs = self.inv.get("bugs") or []
        if not bugs:
            return []
        L = ["", "### Bugs"]
        order = {"critical": 0, "major": 1, "minor": 2}
        for i, b in enumerate(sorted(bugs, key=lambda b: order.get(b.get("severity"), 9)), 1):
            L.append("%d. **[%s] %s** %s" % (i, b.get("severity"), b.get("title"), loc(b)))
            if b.get("root_cause"):
                L.append("   Root cause: %s" % cell(b["root_cause"], 300))
            if (b.get("fix") or {}).get("description"):
                L.append("   Fix: %s" % cell(b["fix"]["description"], 300))
            if b.get("scenarios"):
                L.append("   Seen in: %s" % ", ".join(b["scenarios"]))
        return L

    def _reproduction(self):
        rep = self.inv.get("reproduction")
        if not rep:
            return []
        L = ["", "### Reproduction"]
        err = rep.get("error") or {}
        if err:
            L.append("Error: `%s%s`" % (err.get("type", ""), (": " + cell(err.get("message"), 160)) if err.get("message") else ""))
        L.append("%s%s" % ("Confirmed by running it. " if rep.get("confirmed") else "Not confirmed by a run (simulated). ",
                           ("Fails at %s step %s." % (rep.get("scenario"), rep.get("failing_step"))) if rep.get("failing_step") else ""))
        if rep.get("root_cause"):
            L.append("Root cause: %s" % rep["root_cause"].strip())
        L.extend("%d. %s" % (i, st) for i, st in enumerate(rep.get("steps_to_reproduce") or [], 1))
        return L

    def _insights(self):
        ins = (self.inv.get("summary") or {}).get("insights") or []
        if not ins:
            return []
        return ["", "### Insights"] + ["- " + (("**%s** — %s" % (it.get("title"), it.get("body", ""))) if isinstance(it, dict) else str(it))
                                       for it in ins]

    def _recommendations(self):
        recs = (self.inv.get("summary") or {}).get("recommendations") or []
        return ["", "### Recommendations"] + ["- " + str(r) for r in recs] if recs else []

    def _coverage(self):
        nc = (self.inv.get("coverage") or {}).get("not_covered") or []
        return ["", "Not covered: " + "; ".join(str(x) for x in nc)] if nc else []
