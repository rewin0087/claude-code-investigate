"""Embed the source code the investigation points at, so the report is self-contained."""
import os

MAX_FULL_LINES = 1500     # embed the whole file up to this size
WINDOW = 40               # otherwise: this many lines around every referenced line
MAX_FILES = 60


def referenced_lines(inv, extra=()):
    """{file: set(lines)} for every anchor in the investigation (+ extra (file, line) pairs)."""
    refs = {}

    def add(f, *ls):
        if f and not os.path.isabs(f):
            s = refs.setdefault(f, set())
            s.update(l for l in ls if isinstance(l, int))
    for nd in (inv.get("flow") or {}).get("nodes") or []:
        add(nd.get("file"), nd.get("line"), nd.get("end_line"))
    for c in inv.get("conditions") or []:
        add(c.get("file"), c.get("line"))
    for sc in inv.get("scenarios") or []:
        for st in sc.get("steps") or []:
            add(st.get("file"), st.get("line"), st.get("end_line"))
        if sc.get("error"):
            add(sc["error"].get("file"), sc["error"].get("line"))
    for b in inv.get("bugs") or []:
        add(b.get("file"), b.get("line"), b.get("end_line"))
    for f in (inv.get("reproduction") or {}).get("frames") or []:
        add(f.get("path"), f.get("line"))
    for f, l in extra:
        add(f, l)
    return refs


def build(repo, refs):
    """{file: {"start": first line no, "lines": [...], "total": n}}"""
    out = {}
    ranked = sorted(refs.items(), key=lambda kv: -len(kv[1]))[:MAX_FILES]
    for rel, lns in ranked:
        try:
            with open(os.path.join(repo, rel), errors="replace") as fh:
                text = fh.read()
        except OSError:
            continue
        lines = text.split("\n")
        if lines and lines[-1] == "":
            lines.pop()
        if len(lines) <= MAX_FULL_LINES or not lns:
            out[rel] = {"start": 1, "lines": lines[:MAX_FULL_LINES], "total": len(lines)}
            continue
        lo = max(1, min(lns) - WINDOW)
        hi = min(len(lines), max(lns) + WINDOW)
        if hi - lo > MAX_FULL_LINES:          # widely spread anchors: keep the first window only
            hi = lo + MAX_FULL_LINES
        out[rel] = {"start": lo, "lines": lines[lo - 1:hi], "total": len(lines)}
    return out
