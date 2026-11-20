"""Turn a raw tracer log (trace_py.py / trace_rb.rb) into something a person can read.

* digest(): an indented call tree with the data changes per line, SQL, HTTP, exceptions and
  return values; loops are collapsed. This is what the investigator reads to write the steps.
* compact(): a trimmed event list embedded in the HTML report as the "recorded trace".
"""
import json

from .errors import InvestigateError

KEEP = ("call", "return", "sql", "http", "exception")


def load(path):
    try:
        with open(path) as fh:
            data = json.load(fh)
    except (OSError, ValueError) as e:
        raise InvestigateError("Could not read trace %s: %s" % (path, e))
    if not isinstance(data, dict) or "events" not in data:
        raise InvestigateError("%s is not a trace_py / trace_rb log." % path)
    return data


def _short(v, n=120):
    v = "∅" if v is None else str(v)
    return v if len(v) <= n else v[:n - 1] + "…"


def digest(data, max_lines=400):
    ev = data["events"]
    L = ["Trace: %s, status=%s, %d events, %.0f ms%s" % (
        data.get("tool"), data.get("status"), len(ev), data.get("duration_ms") or 0,
        " (TRUNCATED: raise --max-events / TRACE_MAX_EVENTS or narrow with --entry/--focus)" if data.get("truncated") else "")]
    if data.get("error"):
        L.append("Run ended with %s: %s" % (data["error"].get("type"), data["error"].get("message")))
    out, last_key, repeat = [], None, 0
    for e in ev:
        kind = e["ev"]
        if kind == "line" and not e.get("changes"):
            continue      # executed but changed nothing: kept in the raw log, skipped here
        ind = "  " * max(0, (e.get("depth") or 1) - 1)
        loc = "%s:%s" % (e.get("file"), e.get("line"))
        if kind == "call":
            args = ", ".join("%s=%s" % (k, _short(v, 60)) for k, v in (e.get("args") or {}).items())
            text = "%s→ %s(%s)  [%s]" % (ind, e.get("fn"), args, loc)
        elif kind == "return":
            text = "%s← %s returned %s  [%s]" % (ind, e.get("fn"), _short(e.get("value"), 100), loc)
        elif kind == "line":
            ch = "; ".join("%s: %s → %s" % (k, _short(v[0], 50), _short(v[1], 70)) for k, v in e["changes"].items())
            text = "%s  %s  %s" % (ind, loc, ch)
        elif kind == "sql":
            text = "%s  %s  SQL %s%s" % (ind, loc, _short(e.get("sql"), 160),
                                       ("  params=" + _short(e.get("params"), 80)) if e.get("params") else "")
        elif kind == "http":
            text = "%s  %s  HTTP %s %s%s%s" % (ind, loc, e.get("method"), e.get("url"),
                                             (" → %s" % e["status"]) if e.get("status") else "",
                                             (" ✗ %s" % e["error"]) if e.get("error") else "")
        elif kind == "exception":
            text = "%s  %s  ✗ RAISE %s: %s%s" % (ind, loc, e.get("type"), _short(e.get("message"), 160),
                                                " (from library code)" if e.get("origin") == "library" else "")
        else:
            continue
        key = (kind, e.get("file"), e.get("line"), e.get("fn"), tuple(sorted(e.get("changes") or ())))
        if key == last_key and kind in ("line", "sql", "call", "return"):
            repeat += 1
            continue
        if repeat:
            out.append("%s  … repeated %d more time(s) (loop)" % (ind, repeat))
            repeat = 0
        last_key = key
        out.append("#%-5s %s" % (e.get("i"), text))
    if repeat:
        out.append("  … repeated %d more time(s) (loop)" % repeat)
    if len(out) > max_lines:
        half = max_lines // 2
        out = out[:half] + ["… %d lines omitted (see the JSON) …" % (len(out) - max_lines)] + out[-half:]
    L.extend(out)
    files = sorted({e["file"] for e in ev if e.get("file")})
    L.append("\nFiles touched: " + ", ".join(files[:40]))
    executed = {}
    for e in ev:
        if e["ev"] == "line":
            executed.setdefault(e["file"], set()).add(e["line"])
    L.append("Executed lines per file (use them to tell which branches ran):")
    for f in files[:25]:
        if f in executed:
            L.append("  %s: %s" % (f, _ranges(sorted(executed[f]))))
    return "\n".join(L)


def _ranges(nums):
    out, start, prev = [], None, None
    for n in nums:
        if start is None:
            start = prev = n
        elif n == prev + 1:
            prev = n
        else:
            out.append(str(start) if start == prev else "%d-%d" % (start, prev))
            start = prev = n
    if start is not None:
        out.append(str(start) if start == prev else "%d-%d" % (start, prev))
    return ",".join(out)


def compact(data, limit=1500):
    """Events worth showing in the report: calls/returns, lines that changed data, SQL, HTTP, raises."""
    keep = [e for e in data.get("events", []) if e["ev"] in KEEP or e.get("changes")]
    trimmed = len(keep) > limit
    if trimmed:
        keep = keep[:limit // 2] + keep[-limit // 2:]
    return {"tool": data.get("tool"), "status": data.get("status"), "error": data.get("error"),
            "truncated": bool(data.get("truncated")) or trimmed, "total_events": len(data.get("events", [])),
            "duration_ms": data.get("duration_ms"), "events": keep}
