"""InvestigationValidator: checks investigation.json before it is rendered.

Errors block rendering (broken file/line anchors, unknown ids, invalid enums). Warnings are
printed as ACTION REQUIRED so the investigator fixes gaps (missing edge cases, bugs without a
fix or a reference, a stack trace never mapped to a step) before replying.
"""
import json
import os

from .sourcefile import SourceCache
from .vocabulary import (CATEGORIES, CHANGE_OPS, EVIDENCE, NODE_KINDS, NO_CHANGE_OK, SCENARIO_STATUSES, SEVERITIES,
                         STEP_KINDS, VERDICTS)


class InvestigationValidator(object):
    def __init__(self, inv, repo, out_dir):
        self.inv, self.repo, self.out_dir = inv, repo, out_dir
        self.sources = SourceCache(repo)
        self.errors, self.warnings = [], []
        self.node_ids = self.cond_ids = self.bug_ids = self.scenario_ids = set()
        self.used_conditions = set()

    def validate(self):
        """-> (errors, warnings). Also numbers the steps (n) in place."""
        self._header()
        self._flow()
        self._conditions()
        self.bug_ids = self._ids(self.inv.get("bugs") or [], "bug")
        self._scenarios()
        self._coverage()
        self._bugs()
        self._reproduction()
        return self.errors, self.warnings

    # ---- helpers ---------------------------------------------------------------------------
    def _ids(self, items, label):
        seen = set()
        for it in items:
            i = it.get("id")
            if not i:
                self.errors.append("%s without an id: %s" % (label, json.dumps(it)[:100]))
            elif i in seen:
                self.errors.append("duplicate %s id %s" % (label, i))
            seen.add(i)
        return seen

    def _anchor(self, obj, where, required=False):
        """file must be repo-relative and exist; line / end_line must be inside it."""
        f = obj.get("file")
        if not f:
            if required:
                self.errors.append("%s: missing file" % where)
            return
        if os.path.isabs(f):
            self.errors.append("%s: file must be repo-relative, got %s" % (where, f))
            return
        src = self.sources.get(f)
        if not src.exists:
            self.errors.append("%s: file %s does not exist in the repo" % (where, f))
            return
        n = len(src)
        for key in ("line", "end_line"):
            v = obj.get(key)
            if v is not None and (not isinstance(v, int) or v < 1 or v > n):
                self.errors.append("%s: %s %r is outside %s (1-%d)" % (where, key, v, f, n))
        if obj.get("line") is None and required:
            self.errors.append("%s: missing line" % where)

    def _enum(self, value, allowed, message):
        if value not in allowed:
            self.errors.append(message + " must be one of %s" % ", ".join(allowed))

    # ---- sections --------------------------------------------------------------------------
    def _header(self):
        if not self.inv.get("title"):
            self.errors.append("title is required")
        s = self.inv.get("summary") or {}
        if not s.get("headline"):
            self.errors.append("summary.headline is required")
        self._enum(s.get("verdict"), VERDICTS, "summary.verdict")

    def _flow(self):
        flow = self.inv.get("flow") or {}
        nodes = flow.get("nodes") or []
        self.node_ids = self._ids(nodes, "flow node")
        for nd in nodes:
            if nd.get("kind") and nd["kind"] not in NODE_KINDS:
                self.errors.append("node %s: kind %r must be one of %s" % (nd.get("id"), nd["kind"], ", ".join(NODE_KINDS)))
            self._anchor(nd, "node %s" % nd.get("id"))
        for e in flow.get("edges") or []:
            for k in ("from", "to"):
                if e.get(k) not in self.node_ids:
                    self.errors.append("flow edge %s→%s: unknown node %r" % (e.get("from"), e.get("to"), e.get(k)))

    def _conditions(self):
        conds = self.inv.get("conditions") or []
        self.cond_ids = self._ids(conds, "condition")
        for c in conds:
            self._anchor(c, "condition %s" % c.get("id"))

    def _scenarios(self):
        scenarios = self.inv.get("scenarios") or []
        if not scenarios:
            self.errors.append("at least one scenario is required")
        self.scenario_ids = self._ids(scenarios, "scenario")
        for sc in scenarios:
            self._scenario(sc)

    def _scenario(self, sc):
        w = "scenario %s" % sc.get("id")
        if not sc.get("title"):
            self.errors.append(w + ": title is required")
        self._enum(sc.get("status"), SCENARIO_STATUSES, w + ": status")
        if sc.get("category") and sc["category"] not in CATEGORIES:
            self.errors.append(w + ": category %r must be one of %s" % (sc["category"], ", ".join(CATEGORIES)))
        if sc.get("evidence", "simulated") not in EVIDENCE:
            self.errors.append(w + ": evidence must be simulated or executed")
        for p in sc.get("path") or []:
            if p not in self.node_ids:
                self.errors.append(w + ": path references unknown node %r" % p)
        for d in sc.get("decisions") or []:
            if d.get("condition") not in self.cond_ids:
                self.errors.append(w + ": decision references unknown condition %r" % d.get("condition"))
            self.used_conditions.add(d.get("condition"))
        for b in sc.get("bugs") or []:
            if b not in self.bug_ids:
                self.errors.append(w + ": unknown bug %r" % b)
        if sc.get("trace"):
            if not os.path.exists(os.path.join(self.out_dir, sc["trace"])):
                self.errors.append(w + ": trace file %s not found in the output folder" % sc["trace"])
        elif sc.get("evidence") == "executed":
            self.warnings.append(w + " is marked executed but has no `trace` log; link the tracer output or mark it simulated")
        steps = sc.get("steps") or []
        if not steps:
            self.warnings.append(w + " has no steps: record how the data moves from input to outcome")
        changed = False
        for i, st in enumerate(steps, 1):
            st["n"] = i
            changed = self._step(st, "%s step %d" % (w, i)) or changed
        if steps and not changed and sc.get("status") not in NO_CHANGE_OK:
            self.warnings.append(w + " records no data changes (changes / db / external / state) in any step")
        if not (sc.get("outcome") or {}).get("result"):
            self.warnings.append(w + " has no outcome.result")

    def _step(self, st, sw):
        """Validates one step; True when it records a data change."""
        if st.get("kind") not in STEP_KINDS:
            self.errors.append(sw + ": kind %r must be one of %s" % (st.get("kind"), ", ".join(STEP_KINDS)))
        if not st.get("title"):
            self.errors.append(sw + ": title is required")
        if st.get("node") and st["node"] not in self.node_ids:
            self.errors.append(sw + ": unknown node %r" % st["node"])
        self._anchor(st, sw)
        for ch in st.get("changes") or []:
            if not ch.get("path"):
                self.errors.append(sw + ": every change needs a path (e.g. params.email, user.status)")
            if ch.get("op", "set") not in CHANGE_OPS:
                self.errors.append(sw + ": change op %r must be one of %s" % (ch.get("op"), ", ".join(CHANGE_OPS)))
        fl = st.get("flag")
        if fl and fl.get("bug") and fl["bug"] not in self.bug_ids:
            self.errors.append(sw + ": flag references unknown bug %r" % fl["bug"])
        return bool(st.get("changes") or st.get("db") or st.get("external") or st.get("state"))

    def _coverage(self):
        scenarios, conds = self.inv.get("scenarios") or [], self.inv.get("conditions") or []
        if scenarios and len(scenarios) < 3 and conds:
            self.warnings.append("only %d scenario(s) for a flow with %d condition(s): cover each branch outcome" % (len(scenarios), len(conds)))
        if scenarios and all(sc.get("category") in (None, "happy_path") for sc in scenarios):
            self.warnings.append("no edge-case / error scenarios: add invalid input, boundaries, missing records, failures")
        for c in conds:
            if c.get("id") not in self.used_conditions:
                self.warnings.append("condition %s (%s) is not exercised by any scenario's decisions" % (c.get("id"), c.get("expression", "")[:60]))

    def _bugs(self):
        for b in self.inv.get("bugs") or []:
            w = "bug %s" % b.get("id")
            self._enum(b.get("severity"), SEVERITIES, w + ": severity")
            if not b.get("title"):
                self.errors.append(w + ": title is required")
            self._anchor(b, w)
            if not b.get("file"):
                self.warnings.append(w + " has no file/line: pin point where it happens")
            for x in b.get("scenarios") or []:
                if x not in self.scenario_ids:
                    self.errors.append(w + ": unknown scenario %r" % x)
            if not (b.get("fix") or {}).get("description"):
                self.warnings.append(w + " has no fix.description")
            if b.get("severity") in ("critical", "major") and not b.get("references"):
                self.warnings.append(w + " (%s) cites no reference: add docs / CWE / issue links that support it" % b.get("severity"))

    def _reproduction(self):
        rep = self.inv.get("reproduction")
        if not rep:
            if os.path.exists(os.path.join(self.out_dir, "stacktrace.json")):
                self.warnings.append("a stack trace was supplied but there is no `reproduction` section mapping it to a scenario step")
            return
        if rep.get("scenario") and rep["scenario"] not in self.scenario_ids:
            self.errors.append("reproduction.scenario %r is not a scenario" % rep["scenario"])
        elif rep.get("scenario") and rep.get("failing_step"):
            sc = next(x for x in self.inv["scenarios"] if x.get("id") == rep["scenario"])
            if not 1 <= int(rep["failing_step"]) <= len(sc.get("steps") or []):
                self.errors.append("reproduction.failing_step %r is not a step of %s" % (rep["failing_step"], rep["scenario"]))
        for f in rep.get("frames") or []:
            if f.get("path"):
                self._anchor({"file": f["path"], "line": f.get("line")}, "reproduction frame #%s" % f.get("n"))
        if not rep.get("root_cause"):
            self.warnings.append("reproduction.root_cause is empty")
