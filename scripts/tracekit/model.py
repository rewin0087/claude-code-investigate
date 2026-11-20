"""investigation.json: vocabulary, loading and validation.

Errors block rendering (broken anchors, unknown ids, invalid enums). Warnings are printed as
ACTION REQUIRED so the investigator fixes gaps (missing edge cases, bugs without a fix or a
reference, a stack trace that was never mapped to a step) before replying.
"""
import json
import os

from .errors import InvestigateError

VERDICTS = ("bug_found", "risks_found", "works_as_expected", "reproduced", "not_reproduced", "inconclusive")
SCENARIO_STATUSES = ("pass", "fail", "bug", "risk", "expected_error", "reproduced", "inconclusive")
CATEGORIES = ("happy_path", "validation", "auth", "permission", "not_found", "conflict", "boundary", "edge_case",
              "error_handling", "external_failure", "concurrency", "security", "performance", "reproduction", "regression")
STEP_KINDS = ("input", "parse", "validation", "branch", "transform", "lookup", "db_read", "db_write", "external_call",
              "state_change", "side_effect", "auth", "cache", "event", "job", "response", "error", "return")
NODE_KINDS = STEP_KINDS + ("entry", "middleware", "callback", "output")
CHANGE_OPS = ("set", "update", "delete", "create", "append", "remove", "read")
SEVERITIES = ("critical", "major", "minor")
EVIDENCE = ("simulated", "executed")


def load_json(path, label):
    if not os.path.exists(path):
        raise InvestigateError("%s not found at %s" % (label, path))
    try:
        with open(path) as fh:
            return json.load(fh)
    except ValueError as e:
        raise InvestigateError("%s is not valid JSON: %s" % (label, e))


class _Lines(object):
    """Line counts of repo files, cached."""

    def __init__(self, repo):
        self.repo, self.cache = repo, {}

    def count(self, rel):
        if rel not in self.cache:
            p = os.path.join(self.repo, rel)
            try:
                with open(p, "rb") as fh:
                    self.cache[rel] = fh.read().count(b"\n") + 1
            except OSError:
                self.cache[rel] = None
        return self.cache[rel]


def _ids(items, label, errors):
    seen = set()
    for it in items:
        i = it.get("id")
        if not i:
            errors.append("%s without an id: %s" % (label, json.dumps(it)[:100]))
        elif i in seen:
            errors.append("duplicate %s id %s" % (label, i))
        seen.add(i)
    return seen


def _anchor(obj, where, lines, errors, required=False):
    f, ln = obj.get("file"), obj.get("line")
    if not f:
        if required:
            errors.append("%s: missing file" % where)
        return
    if os.path.isabs(f):
        errors.append("%s: file must be repo-relative, got %s" % (where, f))
        return
    n = lines.count(f)
    if n is None:
        errors.append("%s: file %s does not exist in the repo" % (where, f))
        return
    for key in ("line", "end_line"):
        v = obj.get(key)
        if v is None:
            continue
        if not isinstance(v, int) or v < 1 or v > n:
            errors.append("%s: %s %r is outside %s (1-%d)" % (where, key, v, f, n))
    if ln is None and required:
        errors.append("%s: missing line" % where)


def validate(inv, repo, out_dir):
    """-> (errors, warnings). Also numbers steps (n) in place."""
    errors, warnings = [], []
    lines = _Lines(repo)
    if not inv.get("title"):
        errors.append("title is required")
    s = inv.get("summary") or {}
    if not s.get("headline"):
        errors.append("summary.headline is required")
    if s.get("verdict") not in VERDICTS:
        errors.append("summary.verdict must be one of %s" % ", ".join(VERDICTS))
    flow = inv.get("flow") or {}
    nodes = flow.get("nodes") or []
    node_ids = _ids(nodes, "flow node", errors)
    for nd in nodes:
        if nd.get("kind") and nd["kind"] not in NODE_KINDS:
            errors.append("node %s: kind %r must be one of %s" % (nd.get("id"), nd["kind"], ", ".join(NODE_KINDS)))
        _anchor(nd, "node %s" % nd.get("id"), lines, errors)
    for e in flow.get("edges") or []:
        for k in ("from", "to"):
            if e.get(k) not in node_ids:
                errors.append("flow edge %s→%s: unknown node %r" % (e.get("from"), e.get("to"), e.get(k)))
    conds = inv.get("conditions") or []
    cond_ids = _ids(conds, "condition", errors)
    for c in conds:
        _anchor(c, "condition %s" % c.get("id"), lines, errors)
    bugs = inv.get("bugs") or []
    bug_ids = _ids(bugs, "bug", errors)
    scenarios = inv.get("scenarios") or []
    if not scenarios:
        errors.append("at least one scenario is required")
    sc_ids = _ids(scenarios, "scenario", errors)
    used_conds = set()
    for sc in scenarios:
        w = "scenario %s" % sc.get("id")
        if not sc.get("title"):
            errors.append(w + ": title is required")
        if sc.get("status") not in SCENARIO_STATUSES:
            errors.append(w + ": status must be one of %s" % ", ".join(SCENARIO_STATUSES))
        if sc.get("category") and sc["category"] not in CATEGORIES:
            errors.append(w + ": category %r must be one of %s" % (sc["category"], ", ".join(CATEGORIES)))
        if sc.get("evidence", "simulated") not in EVIDENCE:
            errors.append(w + ": evidence must be simulated or executed")
        for p in sc.get("path") or []:
            if p not in node_ids:
                errors.append(w + ": path references unknown node %r" % p)
        for d in sc.get("decisions") or []:
            if d.get("condition") not in cond_ids:
                errors.append(w + ": decision references unknown condition %r" % d.get("condition"))
            used_conds.add(d.get("condition"))
        for b in sc.get("bugs") or []:
            if b not in bug_ids:
                errors.append(w + ": unknown bug %r" % b)
        if sc.get("trace"):
            if not os.path.exists(os.path.join(out_dir, sc["trace"])):
                errors.append(w + ": trace file %s not found in the output folder" % sc["trace"])
        elif sc.get("evidence") == "executed":
            warnings.append(w + " is marked executed but has no `trace` log; link the tracer output or mark it simulated")
        steps = sc.get("steps") or []
        if not steps:
            warnings.append(w + " has no steps: record how the data moves from input to outcome")
        changed = False
        for i, st in enumerate(steps, 1):
            st["n"] = i
            sw = "%s step %d" % (w, i)
            if st.get("kind") not in STEP_KINDS:
                errors.append(sw + ": kind %r must be one of %s" % (st.get("kind"), ", ".join(STEP_KINDS)))
            if not st.get("title"):
                errors.append(sw + ": title is required")
            if st.get("node") and st["node"] not in node_ids:
                errors.append(sw + ": unknown node %r" % st["node"])
            _anchor(st, sw, lines, errors)
            for ch in st.get("changes") or []:
                if not ch.get("path"):
                    errors.append(sw + ": every change needs a path (e.g. params.email, user.status)")
                if ch.get("op", "set") not in CHANGE_OPS:
                    errors.append(sw + ": change op %r must be one of %s" % (ch.get("op"), ", ".join(CHANGE_OPS)))
                changed = True
            if st.get("db") or st.get("external") or st.get("state"):
                changed = True
            fl = st.get("flag")
            if fl and fl.get("bug") and fl["bug"] not in bug_ids:
                errors.append(sw + ": flag references unknown bug %r" % fl["bug"])
        if steps and not changed and sc.get("status") not in ("expected_error", "risk", "inconclusive"):
            warnings.append(w + " records no data changes (changes / db / external / state) in any step")
        if not (sc.get("outcome") or {}).get("result"):
            warnings.append(w + " has no outcome.result")
    if scenarios and len(scenarios) < 3 and conds:
        warnings.append("only %d scenario(s) for a flow with %d condition(s): cover each branch outcome" % (len(scenarios), len(conds)))
    if scenarios and all(sc.get("category") in (None, "happy_path") for sc in scenarios):
        warnings.append("no edge-case / error scenarios: add invalid input, boundaries, missing records, failures")
    for c in conds:
        if c.get("id") not in used_conds:
            warnings.append("condition %s (%s) is not exercised by any scenario's decisions" % (c.get("id"), c.get("expression", "")[:60]))
    for b in bugs:
        w = "bug %s" % b.get("id")
        if b.get("severity") not in SEVERITIES:
            errors.append(w + ": severity must be one of %s" % ", ".join(SEVERITIES))
        if not b.get("title"):
            errors.append(w + ": title is required")
        _anchor(b, w, lines, errors)
        if not b.get("file"):
            warnings.append(w + " has no file/line: pin point where it happens")
        for x in b.get("scenarios") or []:
            if x not in sc_ids:
                errors.append(w + ": unknown scenario %r" % x)
        if not (b.get("fix") or {}).get("description"):
            warnings.append(w + " has no fix.description")
        if b.get("severity") in ("critical", "major") and not b.get("references"):
            warnings.append(w + " (%s) cites no reference: add docs / CWE / issue links that support it" % b.get("severity"))
    rep = inv.get("reproduction")
    if rep:
        if rep.get("scenario") and rep["scenario"] not in sc_ids:
            errors.append("reproduction.scenario %r is not a scenario" % rep["scenario"])
        elif rep.get("scenario") and rep.get("failing_step"):
            sc = next(x for x in scenarios if x.get("id") == rep["scenario"])
            if not 1 <= int(rep["failing_step"]) <= len(sc.get("steps") or []):
                errors.append("reproduction.failing_step %r is not a step of %s" % (rep["failing_step"], rep["scenario"]))
        for f in rep.get("frames") or []:
            if f.get("path"):
                _anchor({"file": f["path"], "line": f.get("line")}, "reproduction frame #%s" % f.get("n"), lines, errors)
    elif os.path.exists(os.path.join(out_dir, "stacktrace.json")):
        warnings.append("a stack trace was supplied but there is no `reproduction` section mapping it to a scenario step")
    if rep and not rep.get("root_cause"):
        warnings.append("reproduction.root_cause is empty")
    return errors, warnings
