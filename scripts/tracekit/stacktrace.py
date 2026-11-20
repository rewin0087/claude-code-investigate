"""Parse stack traces / error logs and map their frames to files in the repo.

Supports Python, Ruby (incl. Rails backtraces), JavaScript/TypeScript (Node, browsers),
Java/Kotlin/Scala, Go, PHP and .NET. Frames are tagged `app` (a file in this repo) or
`lib` (framework / gems / node_modules / stdlib), so the investigator can start at the
deepest app frame: the last line of *our* code before the failure.
"""
import os
import re

FRAME_PATTERNS = [
    ("python", re.compile(r'^\s*File "(?P<file>[^"]+)", line (?P<line>\d+)(?:, in (?P<fn>.+))?$')),
    ("dotnet", re.compile(r"^\s*at (?P<fn>.+?) in (?P<file>.+?):line (?P<line>\d+)\s*$")),
    ("java", re.compile(r"^\s*at (?P<fn>[\w$.<>/]+)\((?P<file>[\w$.\-]+\.(?:java|kt|scala|groovy)):(?P<line>\d+)\)")),
    ("js", re.compile(r"^\s*at (?:(?:async )?(?P<fn>[^\s(][^(]*?) \()?(?P<file>(?:file://)?[^\s()]+?):(?P<line>\d+):(?P<col>\d+)\)?\s*$")),
    ("js", re.compile(r"^\s*(?P<fn>[^@\s]*)@(?P<file>[^\s@]+?):(?P<line>\d+):(?P<col>\d+)\s*$")),   # firefox / safari
    ("ruby", re.compile(r"^\s*(?:from\s+|\d+:\s*from\s+)?(?P<file>[^\s:][^:]*\.(?:rb|rake|erb|haml|slim|ru)):(?P<line>\d+)(?::in [`'](?P<fn>[^']+)')?")),
    ("php", re.compile(r"^\s*#\d+\s+(?P<file>[^\s(]+\.php)\((?P<line>\d+)\):\s*(?P<fn>.+)$")),
    ("php", re.compile(r"\bin (?P<file>/\S+\.php)(?: on line |:)(?P<line>\d+)")),
    ("go", re.compile(r"^\s+(?P<file>\S+\.go):(?P<line>\d+)(?:\s+\+0x[0-9a-f]+)?\s*$")),
    ("generic", re.compile(r"(?P<file>[\w./\\-]+\.(?:py|rb|js|jsx|ts|tsx|mjs|cjs|java|kt|go|php|cs|rs|ex|exs|swift|scala)):(?P<line>\d+)")),
]
ERROR_PATTERNS = [
    re.compile(r"^(?P<type>(?:[\w.]+\.)?\w*(?:Error|Exception|Exit|Interrupt|Fault|Failure|Invalid|NotFound|Denied|Timeout)\w*)(?::\s*(?P<msg>.*))?$"),
    re.compile(r"^\S+\.rb:\d+:in [`'][^']+': (?P<msg>.*) \((?P<type>[\w:]+)\)$"),     # ruby top frame
    re.compile(r"^(?:Uncaught |Unhandled )?(?P<type>\w*Error)(?: \[\w+\])?:\s*(?P<msg>.*)$"),
    re.compile(r"^panic: (?P<msg>.*)$"),
    re.compile(r"^(?:PHP )?(?:Fatal error|Warning|Notice):\s*(?:Uncaught )?(?P<type>[\w\\]+)?:?\s*(?P<msg>.*)$"),
    re.compile(r"^(?P<type>[\w.]+(?:Exception|Error))(?::\s*(?P<msg>.*))?$"),
]
PHP_ERROR = re.compile(r"^\s*(?:PHP )?(?:Fatal error|Warning|Notice|Parse error|Deprecated):")
LIB_MARKERS = ("site-packages/", "dist-packages/", "/gems/", "/ruby/", "node_modules/", "/vendor/", "<internal",
               "node:internal", "/usr/lib/", "/usr/local/lib/", "/rubygems/", "/bundler/", "go/src/runtime/",
               "/pkg/mod/", "webpack-internal", "/.rbenv/", "/.pyenv/", "/.nvm/", "/Library/Frameworks/", "<frozen")


def parse(text):
    """-> {language, error: {type, message}, frames: [{n, file, line, fn, raw, language}]}"""
    frames, langs, error = [], {}, None
    seen_generic = set()
    lines = text.replace("\r\n", "\n").split("\n")
    prev = None
    for raw in lines:
        line = raw.rstrip()
        if not line.strip():
            continue
        matched = False
        if PHP_ERROR.match(line):          # "PHP Fatal error: Uncaught X: msg in /f.php:31" is the error, not a frame
            if error is None:
                m = ERROR_PATTERNS[4].match(line.strip())
                error = {"type": (m.group("type") or "").strip(), "message": re.sub(r"\s+in \S+\.php(:| on line )\d+.*$", "", m.group("msg") or "")[:500]}
            continue
        for lang, rx in FRAME_PATTERNS:
            m = rx.search(line) if lang in ("php", "generic") else rx.match(line)
            if not m:
                continue
            f = m.group("file")
            if f.startswith("file://"):
                f = f[7:]
            if lang == "generic":
                key = (f, m.group("line"))
                if key in seen_generic or frames and frames[-1]["raw"] == line.strip():
                    break
                seen_generic.add(key)
            fn = (m.groupdict().get("fn") or "").strip()
            if lang == "go" and prev:      # Go prints the function on the line before its file:line
                fn = _strip_args(prev.strip())
            frames.append({"n": len(frames) + 1, "file": f, "line": int(m.group("line")), "fn": fn,
                           "raw": line.strip()[:300], "language": lang})
            langs[lang] = langs.get(lang, 0) + 1
            matched = True
            break
        if matched:
            prev = None
            continue
        prev = line
        if error is None or (error.get("message") == "" and line.strip()):
            for rx in ERROR_PATTERNS:
                m = rx.match(line.strip())
                if m:
                    error = {"type": (m.groupdict().get("type") or "").strip(),
                             "message": (m.groupdict().get("msg") or "").strip()[:500]}
                    break
    if not error:
        # Ruby's first backtrace line carries the message: "app/x.rb:12:in `m': boom (RuntimeError)"
        for raw in lines:
            m = re.search(r":in [`'][^']+': (.*) \(([\w:]+)\)\s*$", raw)
            if m:
                error = {"type": m.group(2), "message": m.group(1)[:500]}
                break
    language = max(langs, key=langs.get) if langs else None
    if language == "python":
        frames.reverse()          # Python prints the innermost frame last; normalise to innermost first
        for i, f in enumerate(frames, 1):
            f["n"] = i
    return {"language": language, "error": error or {}, "frames": frames}


def _strip_args(call):
    """'main.(*Server).handle(0xc0, {0x1})' -> 'main.(*Server).handle' (drop the final argument list)."""
    if not call.endswith(")"):
        return call
    depth = 0
    for i in range(len(call) - 1, -1, -1):
        depth += {")": 1, "(": -1}.get(call[i], 0)
        if depth == 0:
            return call[:i]
    return call


def map_frames(parsed, repo, files):
    """Resolve each frame to a repo-relative path when it's our code; tag app/lib."""
    root = os.path.realpath(repo)
    by_suffix = {}
    for rel in files:
        parts = rel.split("/")
        for i in range(len(parts)):
            by_suffix.setdefault("/".join(parts[i:]), []).append(rel)
    for f in parsed["frames"]:
        path = f["file"].replace("\\", "/")
        rel = None
        if os.path.isabs(path) and os.path.realpath(path).startswith(root + os.sep):
            rel = os.path.relpath(os.path.realpath(path), root)
        if rel is None and not any(mk in path for mk in LIB_MARKERS):
            clean = re.sub(r"^(\./|webpack:///|/app/|/usr/src/app/|/var/www/(html/)?|/srv/|/code/|/workspace/)", "", path)
            parts = clean.lstrip("/").split("/")
            for i in range(len(parts)):     # longest suffix that matches a tracked file
                cand = by_suffix.get("/".join(parts[i:]))
                if cand:
                    rel = cand[0] if len(cand) == 1 else _best(cand, parts)
                    break
            if rel and "/" not in rel and len(parts) == 1 and len(by_suffix.get(rel, [])) > 1:
                rel = None
        f["path"] = rel
        f["app"] = bool(rel) and not any(mk in rel for mk in ("node_modules/", "vendor/"))
    app = [f for f in parsed["frames"] if f["app"]]
    parsed["deepest_app_frame"] = app[0]["n"] if app else None
    parsed["app_frames"] = len(app)
    return parsed


def _best(cands, parts):
    """Prefer the candidate sharing the most leading directories with the frame path."""
    joined = "/".join(parts)
    return max(cands, key=lambda c: (sum(1 for seg in c.split("/") if seg in joined.split("/")), -len(c)))


def format_digest(parsed, repo, snippet_fn, max_frames=25):
    err = parsed.get("error") or {}
    L = ["Stack trace: language=%s, %d frame(s), %d in this repo" % (
        parsed.get("language") or "unknown", len(parsed["frames"]), parsed.get("app_frames", 0))]
    if err:
        L.append("Error: %s%s" % (err.get("type") or "?", (": " + err["message"]) if err.get("message") else ""))
    L.append("Frames (innermost first; * = our code):")
    for f in parsed["frames"][:max_frames]:
        L.append("  %s#%-2d %s:%d %s" % ("*" if f["app"] else " ", f["n"], f["path"] or f["file"], f["line"],
                                         ("in " + f["fn"]) if f["fn"] else ""))
    if len(parsed["frames"]) > max_frames:
        L.append("  … %d more frame(s)" % (len(parsed["frames"]) - max_frames))
    shown = 0
    for f in parsed["frames"]:
        if f["app"] and shown < 6:
            L.append("\n--- #%d %s:%d %s" % (f["n"], f["path"], f["line"], f["fn"]))
            L.append(snippet_fn(repo, f["path"], f["line"], pad=3))
            shown += 1
    if parsed.get("deepest_app_frame"):
        L.append("\nSTART_AT=frame #%d (deepest frame in this repo)" % parsed["deepest_app_frame"])
    else:
        L.append("\nNo frame maps to this repo: search for the error message and the function names instead.")
    return "\n".join(L)
