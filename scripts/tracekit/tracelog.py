"""Raw runtime-tracer logs (trace_py.py / trace_rb.rb).

TraceLog     loads a log and trims it for embedding in the HTML report ("recorded trace").
TraceDigest  formats it for the investigator: an indented call tree with the data changes per
             line, SQL, HTTP, exceptions and return values, loops collapsed, plus which lines ran.
"""
import json
import re

from .errors import InvestigateError

KEEP = ("call", "return", "sql", "http", "exception", "commit")


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


TABLE_RX = {
    "INSERT": re.compile(r"\binsert\s+(?:or\s+\w+\s+)?into\s+[`\"\[]?([\w.]+)", re.I),
    "UPDATE": re.compile(r"\bupdate\s+[`\"\[]?([\w.]+)", re.I),
    "DELETE": re.compile(r"\bdelete\s+from\s+[`\"\[]?([\w.]+)", re.I),
    "SELECT": re.compile(r"\b(?:from|join)\s+[`\"\[]?([\w.]+)", re.I),
}


def sql_tables(op, sql):
    rx = TABLE_RX.get(op if op in TABLE_RX else "SELECT")
    seen = []
    for t in rx.findall(sql or ""):
        if t.lower() not in ("select", "lateral") and t not in seen:
            seen.append(t)
    return seen


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
        handled = self.handled_library_raises()
        keep = [e for i, e in enumerate(self.events) if (e["ev"] in KEEP or e.get("changes")) and i not in handled]
        trimmed = len(keep) > limit
        if trimmed:
            keep = keep[:limit // 2] + keep[-limit // 2:]
        d = self.data
        out = {"tool": d.get("tool"), "status": d.get("status"), "error": d.get("error"),
               "truncated": bool(d.get("truncated")) or trimmed, "total_events": len(self.events),
               "duration_ms": d.get("duration_ms"), "events": keep, "hidden_library_raises": len(handled)}
        db = self.db_activity()
        if db["reads"] or db["writes"] or db["commits"]:
            out["db"] = db
        for k in ("environment", "sandboxes", "network_blocked"):
            if d.get(k):
                out[k] = d[k]
        return out

    def handled_library_raises(self):
        """Indexes of library exceptions that were handled: execution went on at a line of the
        same depth before that frame returned (rescued inside the library or by our code)."""
        handled = set()
        ev = self.events
        for i, e in enumerate(ev):
            if e["ev"] != "exception" or e.get("origin") != "library":
                continue
            for nxt in ev[i + 1:]:
                if nxt["ev"] in ("line", "sql", "call") and (nxt.get("depth") or 0) >= (e.get("depth") or 0):
                    handled.add(i)
                    break
                if nxt["ev"] in ("return", "exception"):
                    break
        return handled

    def db_activity(self):
        """{reads: {table: n}, writes: [{op, tables, sql, file, line}], commits: n}"""
        reads, writes, commits = {}, [], 0
        for e in self.events:
            if e["ev"] == "commit":
                commits += 1
            if e["ev"] != "sql":
                continue
            op = (e.get("op") or "").upper()
            tables = sql_tables(op, e.get("sql"))
            if op in ("INSERT", "UPDATE", "DELETE", "REPLACE", "UPSERT", "MERGE"):
                writes.append({"op": op, "tables": tables, "sql": e.get("sql"), "params": e.get("params"),
                               "file": e.get("file"), "line": e.get("line")})
            else:
                for t in tables:
                    reads[t] = reads.get(t, 0) + 1
        return {"reads": reads, "writes": writes, "commits": commits}

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
        L.extend(self._db_section())
        L.extend(self._sandbox_section())
        files = self.log.files()
        L.append("\nFiles touched: " + ", ".join(files[:40]))
        executed = self.log.executed_lines()
        L.append("Executed lines per file (use them to tell which branches ran):")
        L.extend("  %s: %s" % (f, line_ranges(sorted(executed[f]))) for f in files[:25] if f in executed)
        return "\n".join(L)

    def _db_section(self):
        db = self.log.db_activity()
        if not (db["reads"] or db["writes"] or db["commits"]):
            return []
        L = ["\nDatabase activity:"]
        if db["reads"]:
            L.append("  reads: " + ", ".join("%s ×%d" % kv for kv in sorted(db["reads"].items())))
        for w in db["writes"][:40]:
            L.append("  %s %s at %s:%s  %s" % (w["op"], ",".join(w["tables"]) or "?", w["file"], w["line"],
                                              ("params=" + short(w["params"], 80)) if w.get("params") else short(w["sql"], 100)))
        if db["commits"]:
            L.append("  COMMIT ×%d reached the database" % db["commits"])
        return L

    def _sandbox_section(self):
        d = self.log.data
        L = []
        if d.get("environment"):
            L.append("\nEnvironment: %s" % d["environment"])
        if d.get("network_blocked"):
            L.append("Outbound connections blocked: %d" % d["network_blocked"])
        for sb in d.get("sandboxes") or []:
            L.append("\nSandbox %s: rollback=%s, rolled back=%s, commits=%s" % (sb.get("name"), sb.get("rollback"),
                                                                              sb.get("rolled_back"), sb.get("commits", 0)))
            if sb.get("warning"):
                L.append("  ⚠ " + sb["warning"])
            for w in sb.get("watch") or []:
                if not w.get("changes"):
                    L.append("  watch %s: unchanged" % w["name"])
                for c in (w.get("changes") or [])[:20]:
                    L.append("  watch %s.%s: %s → %s" % (w["name"], c["path"], short(c["before"], 50), short(c["after"], 70)))
            for m in sb.get("emails") or []:
                L.append("  email captured (not sent): %s" % m.get("subject"))
            for j in sb.get("jobs") or []:
                L.append("  job captured (not enqueued): %s" % j.get("job"))
        return L

    def _timeline(self):
        out, last_key, repeat, ind = [], None, 0, ""
        handled = self.log.handled_library_raises()
        hidden = 0
        for idx, e in enumerate(self.log.events):
            if idx in handled:
                hidden += 1
                continue
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
        if hidden:
            out.append("(%d exception(s) raised and handled inside libraries are hidden; they are in the JSON)" % hidden)
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
        if kind == "commit":
            return "  %s  COMMIT (via %s)" % (loc, e.get("via"))
        if kind == "http":
            return "  %s  HTTP %s %s%s%s" % (loc, e.get("method"), e.get("url"), (" → %s" % e["status"]) if e.get("status") else "",
                                            (" ✗ %s" % e["error"]) if e.get("error") else "")
        if kind == "exception":
            return "  %s  ✗ RAISE %s: %s%s" % (loc, e.get("type"), short(e.get("message"), 160),
                                              " (from library code)" if e.get("origin") == "library" else "")
        return None
