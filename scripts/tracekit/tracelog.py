"""Raw runtime-tracer logs (trace_py.py / trace_rb.rb).

TraceLog     loads a log and trims it for embedding in the HTML report ("recorded trace").
TraceDigest  formats it for the investigator: an indented call tree with the data changes per
             line, SQL, HTTP, exceptions and return values, loops collapsed, plus which lines ran.
"""
import json

from .errors import InvestigateError

KEEP = ("call", "return", "sql", "http", "exception")


def short(v, n=120):
    v = "∅" if v is None else str(v)
    return v if len(v) <= n else v[:n - 1] + "…"


def line_ranges(nums):
    """[1, 2, 3, 7, 9, 10] -> '1-3,7,9-10'"""
    out, start, prev = [], None, None
    for n in nums:
        if start is not None and n == prev + 1:
            prev = n
            continue
        if start is not None:
            out.append(str(start) if start == prev else "%d-%d" % (start, prev))
        start = prev = n
    if start is not None:
        out.append(str(start) if start == prev else "%d-%d" % (start, prev))
    return ",".join(out)


class TraceLog(object):
    def __init__(self, data):
        self.data = data
        self.events = data.get("events", [])

    @classmethod
    def load(cls, path):
        try:
            with open(path) as fh:
                data = json.load(fh)
        except (OSError, ValueError) as e:
            raise InvestigateError("Could not read trace %s: %s" % (path, e))
        if not isinstance(data, dict) or "events" not in data:
            raise InvestigateError("%s is not a trace_py / trace_rb log." % path)
        return cls(data)

    def compact(self, limit=1500):
        """Events worth showing in the report: calls/returns, lines that changed data, SQL, HTTP, raises."""
        keep = [e for e in self.events if e["ev"] in KEEP or e.get("changes")]
        trimmed = len(keep) > limit
        if trimmed:
            keep = keep[:limit // 2] + keep[-limit // 2:]
        d = self.data
        return {"tool": d.get("tool"), "status": d.get("status"), "error": d.get("error"),
                "truncated": bool(d.get("truncated")) or trimmed, "total_events": len(self.events),
                "duration_ms": d.get("duration_ms"), "events": keep}

    def files(self):
        return sorted({e["file"] for e in self.events if e.get("file")})

    def executed_lines(self):
        out = {}
        for e in self.events:
            if e["ev"] == "line":
                out.setdefault(e["file"], set()).add(e["line"])
        return out


class TraceDigest(object):
    def __init__(self, log, max_lines=400):
        self.log, self.max_lines = log, max_lines

    def format(self):
        d, ev = self.log.data, self.log.events
        L = ["Trace: %s, status=%s, %d events, %.0f ms%s" % (
            d.get("tool"), d.get("status"), len(ev), d.get("duration_ms") or 0,
            " (TRUNCATED: raise --max-events / TRACE_MAX_EVENTS or narrow with --entry/--focus)" if d.get("truncated") else "")]
        if d.get("error"):
            L.append("Run ended with %s: %s" % (d["error"].get("type"), d["error"].get("message")))
        L.extend(self._timeline())
        files = self.log.files()
        L.append("\nFiles touched: " + ", ".join(files[:40]))
        executed = self.log.executed_lines()
        L.append("Executed lines per file (use them to tell which branches ran):")
        L.extend("  %s: %s" % (f, line_ranges(sorted(executed[f]))) for f in files[:25] if f in executed)
        return "\n".join(L)

    def _timeline(self):
        out, last_key, repeat, ind = [], None, 0, ""
        for e in self.log.events:
            text = self.event_text(e)
            if text is None:
                continue
            ind = "  " * max(0, (e.get("depth") or 1) - 1)
            key = (e["ev"], e.get("file"), e.get("line"), e.get("fn"), tuple(sorted(e.get("changes") or ())))
            if key == last_key and e["ev"] in ("line", "sql", "call", "return"):
                repeat += 1
                continue
            if repeat:
                out.append("%s  … repeated %d more time(s) (loop)" % (ind, repeat))
                repeat = 0
            last_key = key
            out.append("#%-5s %s%s" % (e.get("i"), ind, text))
        if repeat:
            out.append("  … repeated %d more time(s) (loop)" % repeat)
        if len(out) > self.max_lines:
            half = self.max_lines // 2
            out = out[:half] + ["… %d lines omitted (see the JSON) …" % (len(out) - self.max_lines)] + out[-half:]
        return out

    @staticmethod
    def event_text(e):
        """One event as text (without indentation), or None for lines that changed nothing."""
        kind, loc = e["ev"], "%s:%s" % (e.get("file"), e.get("line"))
        if kind == "call":
            args = ", ".join("%s=%s" % (k, short(v, 60)) for k, v in (e.get("args") or {}).items())
            return "→ %s(%s)  [%s]" % (e.get("fn"), args, loc)
        if kind == "return":
            return "← %s returned %s  [%s]" % (e.get("fn"), short(e.get("value"), 100), loc)
        if kind == "line":
            if not e.get("changes"):
                return None     # executed but changed nothing: kept in the raw log, skipped here
            return "  %s  %s" % (loc, "; ".join("%s: %s → %s" % (k, short(v[0], 50), short(v[1], 70)) for k, v in e["changes"].items()))
        if kind == "sql":
            return "  %s  SQL %s%s" % (loc, short(e.get("sql"), 160), ("  params=" + short(e.get("params"), 80)) if e.get("params") else "")
        if kind == "http":
            return "  %s  HTTP %s %s%s%s" % (loc, e.get("method"), e.get("url"), (" → %s" % e["status"]) if e.get("status") else "",
                                            (" ✗ %s" % e["error"]) if e.get("error") else "")
        if kind == "exception":
            return "  %s  ✗ RAISE %s: %s%s" % (loc, e.get("type"), short(e.get("message"), 160),
                                              " (from library code)" if e.get("origin") == "library" else "")
        return None
