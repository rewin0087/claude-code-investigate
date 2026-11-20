"""Resolve what to investigate: a file / line range, a symbol (Class#method), a route, or free text.

Resolution is a best-effort *index* for the investigator: it lists candidate definitions with
a short snippet so Claude can open the right one, it never decides on its own.
"""
import os
import re

from .gitcmd import git

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
ROUTE_FILES = re.compile(r"(routes?(\.rb|\.php|\.py|\.[jt]sx?)$|urls\.py$|(^|/)(api|views?|app|main|server|handlers?)\.(py|[jt]s|go)$|/routes/|/controllers?/|Controller\.|/api/|/handlers?/|/pages/|/app/.*route\.[jt]s$)", re.I)


def parse_target(spec, repo, cwd):
    """-> dict(kind=range|file|symbol|route|text, label, path?, start?, end?, name?, verb?)"""
    s = spec.strip()
    if s.startswith("@"):
        s = s[1:]
    m = RANGE_RE.match(s)
    if m:
        rel = _repo_rel(m.group("path"), repo, cwd)
        if rel:
            a = int(m.group("a") or m.group("la"))
            b = int(m.group("b") or m.group("lb") or a)
            return {"kind": "range", "label": "%s:%d-%d" % (rel, a, b) if b != a else "%s:%d" % (rel, a),
                    "path": rel, "start": min(a, b), "end": max(a, b)}
    rel = _repo_rel(s, repo, cwd)
    if rel and os.path.isfile(os.path.join(repo, rel)):
        return {"kind": "file", "label": rel, "path": rel}
    m = ROUTE_RE.match(s)
    if m and "/" in s and not os.path.exists(os.path.join(repo, s.lstrip("/"))):
        verb = (m.group("verb") or "").upper()
        return {"kind": "route", "label": ("%s %s" % (verb, m.group("path"))).strip(), "verb": verb,
                "path_pattern": m.group("path")}
    if SYMBOL_RE.match(s):
        parts = re.split(r"::|#|\.", s)
        return {"kind": "symbol", "label": s, "name": parts[-1], "owner": parts[-2] if len(parts) > 1 else None}
    return {"kind": "text", "label": s}


def _repo_rel(p, repo, cwd):
    for base in (cwd, repo):
        full = p if os.path.isabs(p) else os.path.join(base, p)
        if os.path.exists(full):
            full = os.path.realpath(full)
            root = os.path.realpath(repo)
            if full == root or full.startswith(root + os.sep):
                return os.path.relpath(full, root)
    return None


def _code_file(path):
    return os.path.splitext(path)[1].lower() in CODE_EXT and not any(d in path for d in SKIP_DIRS)


def find_definitions(name, repo, owner=None, limit=12):
    """Candidate definition sites for an identifier, best first: [{path, line, text, score}]."""
    esc = re.escape(name)
    defn = re.compile("|".join("(?:%s)" % p.format(name=esc) for p in DEF_PATTERNS))
    # git grep narrows to lines containing the word (fast, portable); Python regex picks definitions.
    out = git(["grep", "-n", "-I", "-w", "-F", name.rstrip("?!=")], repo, check=False)
    hits = []
    owner_files = set()
    if owner:
        oesc = re.escape(owner)
        for line in git(["grep", "-l", "-I", "-E", r"(class|module|struct|interface|object)\s+%s\b" % oesc], repo,
                        check=False).split("\n"):
            if line:
                owner_files.add(line)
        snake = re.sub(r"(?<!^)(?=[A-Z])", "_", owner).lower()
        owner_files |= {p for p in git(["ls-files"], repo, check=False).split("\n")
                        if os.path.basename(p).split(".")[0] in (snake, owner, owner.lower())}
    for row in out.split("\n"):
        m = re.match(r"^(.+?):(\d+):(.*)$", row)
        if not m or not _code_file(m.group(1)):
            continue
        path, line, text = m.group(1), int(m.group(2)), m.group(3).strip()
        if not defn.search(m.group(3)):
            continue
        score = 1
        if owner and path in owner_files:
            score += 5
        if re.search(r"(^|/)(spec|test|tests|__tests__)/|_spec\.|_test\.|\.test\.|\.spec\.", path):
            score -= 2
        if re.search(r"\b(class|module|struct|interface)\s+%s\b" % esc, text):
            score += 1
        hits.append({"path": path, "line": line, "text": text[:160], "score": score})
    hits.sort(key=lambda h: (-h["score"], h["path"], h["line"]))
    return hits[:limit]


def find_route(target, repo, files, limit=15):
    """Lines in routing/controller files mentioning the route's path segments."""
    segs = [s for s in re.split(r"[/{}<>:*]+", target["path_pattern"]) if s and not s.isdigit()]
    if not segs:
        return []
    hits = _scan_route(target, repo, [f for f in files if ROUTE_FILES.search(f) and _code_file(f)], segs)
    if not hits:      # unconventional layout: scan every code file
        hits = _scan_route(target, repo, [f for f in files if _code_file(f)], segs)
    hits.sort(key=lambda h: (-h["score"], h["path"], h["line"]))
    return hits[:limit]


def _scan_route(target, repo, candidates, segs):
    hits = []
    for rel in candidates[:4000]:
        try:
            with open(os.path.join(repo, rel), errors="replace") as fh:
                for i, line in enumerate(fh, 1):
                    found = sum(1 for s in segs if s in line)
                    if found:
                        verb_ok = not target.get("verb") or target["verb"].lower() in line.lower()
                        hits.append({"path": rel, "line": i, "text": line.strip()[:160],
                                     "score": found * 2 + (1 if verb_ok else 0) + (2 if found == len(segs) else 0)})
        except OSError:
            continue
    return hits


def enclosing_definition(repo, path, line):
    """Nearest definition line at or above `line` (best effort, indentation-agnostic)."""
    try:
        with open(os.path.join(repo, path), errors="replace") as fh:
            lines = fh.read().split("\n")
    except OSError:
        return None
    rx = re.compile(r"^\s*(?:export\s+)?(?:public|private|protected|static|async|def|function|func|fun|fn|class|module)\b")
    for i in range(min(line, len(lines)) - 1, -1, -1):
        if rx.match(lines[i]):
            return {"path": path, "line": i + 1, "text": lines[i].strip()[:160]}
    return None


def snippet(repo, path, start, end=None, pad=2, max_lines=40):
    try:
        with open(os.path.join(repo, path), errors="replace") as fh:
            lines = fh.read().split("\n")
    except OSError:
        return ""
    end = end or start
    a, b = max(1, start - pad), min(len(lines), end + pad, start + max_lines)
    width = len(str(b))
    return "\n".join("%s%*d | %s" % (">" if start <= i <= end else " ", width, i, lines[i - 1]) for i in range(a, b + 1))


def slug(text, limit=48):
    s = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()
    return (s[:limit].rstrip("-")) or "investigation"
