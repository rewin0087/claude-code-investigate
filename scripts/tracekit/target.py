"""What to investigate: a file / line range, a symbol (Class#method), a route, or free text.

TargetResolver turns each user-supplied spec into a target dict and lists candidate
definitions / route lines with a snippet. It is a best-effort *index* for the investigator;
it never decides on its own.
"""
import os
import re

from .sourcefile import SourceCache

RANGE_RE = re.compile(r"^(?P<path>.+?)(?::(?P<a>\d+)(?:-(?P<b>\d+))?|#L(?P<la>\d+)(?:-L?(?P<lb>\d+))?)$")
ROUTE_RE = re.compile(r"^(?:(?P<verb>GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+)?(?P<path>/[\w\-./:{}<>*]*)$", re.I)
SYMBOL_RE = re.compile(r"^[A-Za-z_$][\w$]*(?:(?:::|#|\.)[A-Za-z_$][\w$]*[?!=]?)*$")

# Lines that *define* something, per language family. {name} is the escaped identifier.
DEF_PATTERNS = [
    r"\bdef\s+(?:self\.)?{name}\b",                                   # ruby / python
    r"\b(?:async\s+)?function\s*\*?\s*{name}\b",                      # js / php
    r"\b{name}\s*[:=]\s*(?:async\s+)?(?:function\b|\([^)]*\)\s*=>|[\w$]+\s*=>)",  # js arrow / assigned
    r"^\s*(?:public|private|protected|static|async|override|readonly|\s)*{name}\s*\([^;]*\)\s*(?::\s*[\w<>\[\], |]+)?\s*\{{?\s*$",  # js/ts class method
    r"\bfunc\s+(?:\([^)]*\)\s*)?{name}\s*\(",                          # go
    r"\b(?:public|private|protected|internal)\b[^=;(]*\b{name}\s*\(",   # java / c# / kotlin / php
    r"\bfun\s+(?:[\w.<>]+\.)?{name}\s*\(",                             # kotlin
    r"\bfn\s+{name}\b",                                                # rust
    r"\b(?:class|module|interface|struct|trait|object|enum|record|type)\s+{name}\b",
]
CODE_EXT = {".rb", ".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".go", ".java", ".kt", ".kts", ".php",
            ".cs", ".rs", ".scala", ".ex", ".exs", ".swift", ".vue", ".svelte", ".erb", ".rake"}
SKIP_DIRS = ("node_modules/", "vendor/", "dist/", "build/", ".venv/", "venv/", "tmp/", "coverage/", ".investigate/")
TEST_PATH = re.compile(r"(^|/)(spec|test|tests|__tests__)/|_spec\.|_test\.|\.test\.|\.spec\.")
ROUTE_FILES = re.compile(r"(routes?(\.rb|\.php|\.py|\.[jt]sx?)$|urls\.py$|(^|/)(api|views?|app|main|server|handlers?)\.(py|[jt]s|go)$|"
                         r"/routes/|/controllers?/|Controller\.|/api/|/handlers?/|/pages/|/app/.*route\.[jt]s$)", re.I)


def is_code_file(path):
    return os.path.splitext(path)[1].lower() in CODE_EXT and not any(d in path for d in SKIP_DIRS)


class TargetResolver(object):
    def __init__(self, workspace, cwd):
        self.ws = workspace
        self.repo = workspace.repo
        self.cwd = cwd
        self.sources = SourceCache(self.repo)

    # ---- parsing ---------------------------------------------------------------------------
    def parse(self, spec):
        """-> dict(kind=range|file|symbol|route|text, label, path?, start?, end?, name?, verb?)"""
        s = spec.strip()
        if s.startswith("@"):
            s = s[1:]
        m = RANGE_RE.match(s)
        if m:
            rel = self.repo_relative(m.group("path"))
            if rel:
                a = int(m.group("a") or m.group("la"))
                b = int(m.group("b") or m.group("lb") or a)
                return {"kind": "range", "label": "%s:%d-%d" % (rel, a, b) if b != a else "%s:%d" % (rel, a),
                        "path": rel, "start": min(a, b), "end": max(a, b)}
        rel = self.repo_relative(s)
        if rel and os.path.isfile(os.path.join(self.repo, rel)):
            return {"kind": "file", "label": rel, "path": rel}
        m = ROUTE_RE.match(s)
        if m and "/" in s and not os.path.exists(os.path.join(self.repo, s.lstrip("/"))):
            verb = (m.group("verb") or "").upper()
            return {"kind": "route", "label": ("%s %s" % (verb, m.group("path"))).strip(), "verb": verb,
                    "path_pattern": m.group("path")}
        if SYMBOL_RE.match(s):
            parts = re.split(r"::|#|\.", s)
            return {"kind": "symbol", "label": s, "name": parts[-1], "owner": parts[-2] if len(parts) > 1 else None}
        return {"kind": "text", "label": s}

    def repo_relative(self, p):
        for base in (self.cwd, self.repo):
            full = p if os.path.isabs(p) else os.path.join(base, p)
            if os.path.exists(full):
                full = os.path.realpath(full)
                root = os.path.realpath(self.repo)
                if full == root or full.startswith(root + os.sep):
                    return os.path.relpath(full, root)
        return None

    # ---- lookups ---------------------------------------------------------------------------
    def find_definitions(self, name, owner=None, limit=12):
        """Candidate definition sites for an identifier, best first: [{path, line, text, score}]."""
        esc = re.escape(name)
        defn = re.compile("|".join("(?:%s)" % p.format(name=esc) for p in DEF_PATTERNS))
        # git grep narrows to lines containing the word (fast, portable); Python regex picks definitions.
        out = self.ws.git.grep_word(name.rstrip("?!="))
        owner_files = self._owner_files(owner) if owner else set()
        hits = []
        for row in out.split("\n"):
            m = re.match(r"^(.+?):(\d+):(.*)$", row)
            if not m or not is_code_file(m.group(1)) or not defn.search(m.group(3)):
                continue
            path, line, text = m.group(1), int(m.group(2)), m.group(3).strip()
            score = 1 + (5 if path in owner_files else 0) - (2 if TEST_PATH.search(path) else 0)
            if re.search(r"\b(class|module|struct|interface)\s+%s\b" % esc, text):
                score += 1
            hits.append({"path": path, "line": line, "text": text[:160], "score": score})
        hits.sort(key=lambda h: (-h["score"], h["path"], h["line"]))
        return hits[:limit]

    def _owner_files(self, owner):
        files = set(self.ws.git.grep_files(r"(class|module|struct|interface|object)\s+%s\b" % re.escape(owner)))
        snake = re.sub(r"(?<!^)(?=[A-Z])", "_", owner).lower()
        return files | {p for p in self.ws.files if os.path.basename(p).split(".")[0] in (snake, owner, owner.lower())}

    def find_route(self, target, limit=15):
        """Lines in routing/controller files (else any code file) mentioning the route's path segments."""
        segs = [s for s in re.split(r"[/{}<>:*]+", target["path_pattern"]) if s and not s.isdigit()]
        if not segs:
            return []
        code = [f for f in self.ws.files if is_code_file(f)]
        hits = self._scan_route(target, [f for f in code if ROUTE_FILES.search(f)], segs) or self._scan_route(target, code, segs)
        hits.sort(key=lambda h: (-h["score"], h["path"], h["line"]))
        return hits[:limit]

    def _scan_route(self, target, candidates, segs):
        hits = []
        for rel in candidates[:4000]:
            for i, line in enumerate(self.sources.get(rel).lines or [], 1):
                found = sum(1 for s in segs if s in line)
                if found:
                    verb_ok = not target.get("verb") or target["verb"].lower() in line.lower()
                    hits.append({"path": rel, "line": i, "text": line.strip()[:160],
                                 "score": found * 2 + (1 if verb_ok else 0) + (2 if found == len(segs) else 0)})
        return hits

    # ---- digest ----------------------------------------------------------------------------
    def describe(self, t):
        """Printable description of a target; adds `candidates` / `enclosing` to it."""
        L = ["\n### Target: %s (%s)" % (t["label"], t["kind"])]
        if t["kind"] in ("range", "file"):
            src = self.sources.get(t["path"])
            start = t.get("start") or 1
            enc = src.enclosing_definition(start) if t["kind"] == "range" else None
            if enc:
                L.append("Enclosing definition: %s:%d  %s" % (enc["path"], enc["line"], enc["text"]))
                t["enclosing"] = enc
            L.append(src.snippet(start, t.get("end") or start, pad=2, max_lines=60))
        elif t["kind"] == "symbol":
            hits = t["candidates"] = self.find_definitions(t["name"], t.get("owner"))
            if not hits:
                L.append("No definition found for %s: grep for it, or ask the user where it lives." % t["label"])
            L.extend("- %s:%d  %s" % (h["path"], h["line"], h["text"]) for h in hits[:6])
            if hits:
                L.append(self.sources.get(hits[0]["path"]).snippet(hits[0]["line"], pad=1, max_lines=25))
        elif t["kind"] == "route":
            hits = t["candidates"] = self.find_route(t)
            if not hits:
                L.append("No route/controller line mentions this path: search the router config by hand.")
            L.extend("- %s:%d  %s" % (h["path"], h["line"], h["text"]) for h in hits[:10])
        else:
            L.append("Free-text target: find the entry point from the words (git grep), the bug report, or the stack trace.")
        return "\n".join(L)
