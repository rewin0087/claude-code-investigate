"""SnippetCollector: embed the source code the investigation points at, so the report is self-contained."""
import os

from .sourcefile import SourceCache

MAX_FULL_LINES = 1500     # embed the whole file up to this size
WINDOW = 40               # otherwise: this many lines around every referenced line
MAX_FILES = 60


class SnippetCollector(object):
    def __init__(self, repo):
        self.sources = SourceCache(repo)
        self.refs = {}

    def add(self, f, *lines):
        if f and not os.path.isabs(f):
            self.refs.setdefault(f, set()).update(l for l in lines if isinstance(l, int))

    def add_investigation(self, inv):
        """Every anchor: flow nodes, conditions, steps, scenario errors, bugs, reproduction frames."""
        for nd in (inv.get("flow") or {}).get("nodes") or []:
            self.add(nd.get("file"), nd.get("line"), nd.get("end_line"))
        for c in inv.get("conditions") or []:
            self.add(c.get("file"), c.get("line"))
        for sc in inv.get("scenarios") or []:
            for st in sc.get("steps") or []:
                self.add(st.get("file"), st.get("line"), st.get("end_line"))
            if sc.get("error"):
                self.add(sc["error"].get("file"), sc["error"].get("line"))
        for b in inv.get("bugs") or []:
            self.add(b.get("file"), b.get("line"), b.get("end_line"))
        for f in (inv.get("reproduction") or {}).get("frames") or []:
            self.add(f.get("path"), f.get("line"))
        return self

    def build(self):
        """{file: {"start": first line no, "lines": [...], "total": n}}"""
        out = {}
        for rel, lns in sorted(self.refs.items(), key=lambda kv: -len(kv[1]))[:MAX_FILES]:
            lines = self.sources.get(rel).lines
            if lines is None:
                continue
            if len(lines) <= MAX_FULL_LINES or not lns:
                out[rel] = {"start": 1, "lines": lines[:MAX_FULL_LINES], "total": len(lines)}
                continue
            lo = max(1, min(lns) - WINDOW)
            hi = min(len(lines), max(lns) + WINDOW, lo + MAX_FULL_LINES)   # widely spread anchors: first window only
            out[rel] = {"start": lo, "lines": lines[lo - 1:hi], "total": len(lines)}
        return out
