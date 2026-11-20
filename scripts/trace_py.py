#!/usr/bin/env python3
"""Record how data changes, line by line, while Python code runs.

Runs a script, a module (-m pytest ...), or a callable, under sys.settrace and writes a JSON
event log: calls with arguments, every executed line of *app* code (files under --root, not
site-packages) with the local variables / self attributes that line changed, return values,
exceptions, SQL statements (DB-API / SQLAlchemy / Django cursors, sqlite3) and outgoing HTTP
requests (requests, httpx, urllib, http.client).

Standalone on purpose: run it with the PROJECT's interpreter (its venv), e.g.
  .venv/bin/python trace_py.py --root . --out trace/SC1.json -- -m pytest path/sim_sc1.py -q
  .venv/bin/python trace_py.py --root . --out trace/SC2.json -- harness/sim_sc2.py
Use it only against a test / local environment. Values of password/secret/token-like names
are redacted unless --no-mask is given.
"""
import argparse
import json
import os
import re
import runpy
import sys
import threading
import time

MAX_REPR = 240
SECRET_NAME = re.compile(r"(pass(wd|word)?|secret|token|api_?key|private_?key|authorization|cookie|session_?key|otp|cvv)", re.I)
SECRET_IN_REPR = re.compile(
    r"""(['"]?\b\w*(?:pass(?:wd|word)?|secret|token|api_?key|authorization)\w*['"]?\s*(?:[:=]|=>)\s*)(b?['"])((?:\\.|(?!\2).)*)\2""", re.I)
SQL_FUNCS = {"execute", "executemany", "do_execute", "do_executemany", "_execute", "exec_driver_sql", "raw"}
SQL_ARGS = ("sql", "statement", "operation", "query", "stmt", "raw_query")
HTTP_FUNCS = {("requests.sessions", "request"), ("httpx._client", "request"), ("httpx._client", "send"),
              ("http.client", "request"), ("urllib.request", "urlopen"), ("aiohttp.client", "_request")}
SKIP_ATTR = re.compile(r"^(_sa_instance_state|_state|_prefetched_objects_cache|__\w+__|_django_version)$")


class Recorder(object):
    def __init__(self, root, out, max_events, mask, include_tests, focus, entry=None):
        self.root = os.path.realpath(root) + os.sep
        self.out = out
        self.max_events = max_events
        self.mask = mask
        self.include_tests = include_tests
        self.focus = focus
        self.entry = entry            # record only while inside this function (name or Class.name)
        self.active = not entry
        self.entry_frame = None
        self.events = []
        self.frames = {}          # id(frame) -> {"line": last line, "snap": {...}}
        self.app_cache = {}
        self.cls_cache = {}
        self.truncated = False
        self.started = time.time()
        self.depth = 0
        self.lock = threading.Lock()

    # ---- helpers -------------------------------------------------------------------------
    def is_app(self, filename):
        r = self.app_cache.get(filename)
        if r is None:
            real = os.path.realpath(filename) if filename and not filename.startswith("<") else ""
            inside = "/" + real[len(self.root):] if real.startswith(self.root) else None    # "/app/models.py"
            r = inside is not None and not re.search(
                r"(/site-packages/|/dist-packages/|/\.venv/|/venv/|/env/|/node_modules/|/\.tox/|/\.nox/)", inside)
            if r and not self.include_tests and re.search(r"(/tests?/|/spec/|/test_[^/]*\.py$|_test\.py$|conftest\.py$)", inside) \
                    and "/.investigate/" not in inside:
                r = False
            if r and self.focus and not any(f in inside for f in self.focus) and "/.investigate/" not in inside:
                r = False
            self.app_cache[filename] = r
        return r

    def rel(self, filename):
        real = os.path.realpath(filename)
        return real[len(self.root):] if real.startswith(self.root) else filename

    def safe_repr(self, name, value):
        if self.mask and SECRET_NAME.search(name or "") and isinstance(value, (str, bytes)) and value:
            return "'[redacted]'"
        try:
            r = self.describe(value)
        except Exception as e:          # noqa - a broken __repr__ must not stop the run
            r = "<%s (repr failed: %s)>" % (type(value).__name__, type(e).__name__)
        if self.mask:
            r = SECRET_IN_REPR.sub(lambda m: m.group(1) + m.group(2) + "[redacted]" + m.group(2), r)
        return r if len(r) <= MAX_REPR else r[:MAX_REPR - 1] + "…"

    def describe(self, value):
        tname = type(value).__name__
        # Never let repr run a query: an unevaluated Django QuerySet executes on repr().
        if tname == "QuerySet" and getattr(value, "_result_cache", 1) is None:
            try:
                return "<QuerySet %s not evaluated: %s>" % (value.model.__name__, value.query)
            except Exception:       # noqa
                return "<QuerySet not evaluated>"
        r = repr(value)
        d = getattr(value, "__dict__", None)
        # Default object reprs ("<x.User object at 0x..>", Django's "User object (1)") hide the state.
        if isinstance(d, dict) and (" object at 0x" in r or re.search(r" object \(\w*\)>?$", r)):
            fields = ", ".join("%s=%s" % (k, "'[redacted]'" if self.mask and SECRET_NAME.search(k) and v else repr(v))
                               for k, v in list(d.items())[:25] if not SKIP_ATTR.match(k) and not callable(v))
            r = "%s(%s)" % (tname, fields)
        return r

    def snapshot(self, frame):
        snap = {}
        for k, v in list(frame.f_locals.items()):
            if k.startswith("__") or k.startswith("@py_assert"):
                continue
            if k == "self":
                d = getattr(v, "__dict__", None)
                if isinstance(d, dict):
                    for a, av in list(d.items())[:40]:
                        if not SKIP_ATTR.match(a) and not callable(av):
                            snap["self." + a] = self.safe_repr(a, av)
                continue
            if callable(v) and not hasattr(v, "__dict__") or type(v).__name__ in ("module", "function", "type", "builtin_function_or_method"):
                continue
            d = getattr(v, "__dict__", None)
            if isinstance(d, dict) and self.app_class(type(v)):
                # Objects of app classes are shown attribute by attribute, so "user.locked_at: None → '…'"
                # appears on the line that changed it instead of a whole new repr.
                snap[k] = "<%s>" % type(v).__name__
                for a, av in list(d.items())[:40]:
                    if not SKIP_ATTR.match(a) and not callable(av):
                        snap[k + "." + a] = self.safe_repr(a, av)
                continue
            snap[k] = self.safe_repr(k, v)
        return snap

    def app_class(self, cls):
        r = self.cls_cache.get(cls)
        if r is None:
            mod = sys.modules.get(getattr(cls, "__module__", ""), None)
            r = bool(getattr(mod, "__file__", None)) and self.is_app(mod.__file__)
            self.cls_cache[cls] = r
        return r

    @staticmethod
    def unquote(r):
        return r[1:-1] if len(r) >= 2 and r[0] == r[-1] and r[0] in "'\"" else r

    def emit(self, ev):
        if not self.active:
            return
        if len(self.events) >= self.max_events:
            self.truncated = True
            return
        ev["i"] = len(self.events) + 1
        ev["t"] = round((time.time() - self.started) * 1000, 2)
        self.events.append(ev)

    def app_caller(self, frame):
        f = frame
        while f is not None:
            if self.is_app(f.f_code.co_filename):
                return self.rel(f.f_code.co_filename), f.f_lineno, f.f_code.co_name
            f = f.f_back
        return None, None, None

    # ---- trace functions -----------------------------------------------------------------
    def global_trace(self, frame, event, arg):
        if event != "call":
            return None
        code = frame.f_code
        if self.is_app(code.co_filename):
            if code.co_name == "<module>" and "/.investigate/" not in code.co_filename:
                return None        # importing app modules: definitions, not data flow
            self.depth += 1
            snap = self.snapshot(frame)
            args = {k: snap[k] for k in code.co_varnames[:code.co_argcount + code.co_kwonlyargcount] if k in snap and k != "self"}
            owner = type(frame.f_locals["self"]).__name__ + "." if "self" in frame.f_locals else ""
            if not self.active and (code.co_name == self.entry or (owner + code.co_name).endswith(self.entry)):
                self.active, self.entry_frame = True, id(frame)
            self.frames[id(frame)] = {"line": None, "snap": snap}
            self.emit({"ev": "call", "file": self.rel(code.co_filename), "line": frame.f_lineno,
                       "fn": owner + code.co_name, "depth": self.depth, "args": args})
            return self.local_trace
        self.library_call(frame, code)
        return None

    def library_call(self, frame, code):
        name = code.co_name
        mod = frame.f_globals.get("__name__", "")
        if name in SQL_FUNCS:
            loc = frame.f_locals
            sql = next((loc[a] for a in SQL_ARGS if isinstance(loc.get(a), str)), None)
            if sql is None:
                s = next((loc[a] for a in SQL_ARGS if a in loc), None)
                sql = str(s) if s is not None and type(s).__name__ in ("TextClause", "Select", "Insert", "Update", "Delete") else None
            if sql:
                params = next((loc[a] for a in ("params", "parameters", "args", "parameters_list") if a in loc), None)
                f, l, fn = self.app_caller(frame.f_back)
                if f:
                    self.emit({"ev": "sql", "file": f, "line": l, "fn": fn, "depth": self.depth + 1, "sql": sql.strip()[:2000],
                               "params": self.safe_repr("params", params) if params is not None else None,
                               "op": (sql.strip().split() or ["?"])[0].upper(), "via": mod})
        elif (mod, name) in HTTP_FUNCS or (name == "request" and mod.startswith(("requests", "httpx"))):
            loc = frame.f_locals
            method = loc.get("method")
            url = loc.get("url") or loc.get("fullurl") or loc.get("str_or_url")
            if mod == "http.client" and "self" in loc:
                url = "%s://%s%s" % ("https" if type(loc["self"]).__name__.startswith("HTTPS") else "http",
                                     getattr(loc["self"], "host", "?"), loc.get("url", ""))
            if hasattr(url, "full_url"):
                url = url.full_url
            if hasattr(method, "method") and hasattr(method, "url"):        # httpx.send(request)
                method, url = method.method, method.url
            if url is not None:
                f, l, fn = self.app_caller(frame.f_back)
                if f:
                    self.emit({"ev": "http", "file": f, "line": l, "fn": fn, "depth": self.depth + 1, "method": str(method or "GET").upper(),
                               "url": self.unquote(self.safe_repr("url", str(url))), "via": mod})

    def local_trace(self, frame, event, arg):
        st = self.frames.get(id(frame))
        if st is None:
            return None
        if event in ("line", "return"):
            new = self.snapshot(frame)
            changes = {}
            for k, v in new.items():
                old = st["snap"].get(k)
                if old != v:
                    changes[k] = [old, v]
            if st["line"] is not None:
                ev = {"ev": "line", "file": self.rel(frame.f_code.co_filename), "line": st["line"], "fn": frame.f_code.co_name,
                      "depth": self.depth}
                if changes:
                    ev["changes"] = changes
                self.emit(ev)
            st["snap"] = new
            st["line"] = frame.f_lineno
            if event == "return":
                self.emit({"ev": "return", "file": self.rel(frame.f_code.co_filename), "line": frame.f_lineno,
                           "fn": frame.f_code.co_name, "depth": self.depth, "value": self.safe_repr("return", arg)})
                self.frames.pop(id(frame), None)
                self.depth -= 1
                if self.entry and id(frame) == self.entry_frame:
                    self.active, self.entry_frame = False, None
        elif event == "exception":
            exc_type, exc, _tb = arg
            self.emit({"ev": "exception", "file": self.rel(frame.f_code.co_filename), "line": frame.f_lineno,
                       "fn": frame.f_code.co_name, "depth": self.depth, "type": exc_type.__name__,
                       "message": self.unquote(self.safe_repr("message", str(exc)))})
        return self.local_trace

    # ---- sqlite3 (C module: not visible to settrace) -------------------------------------
    def hook_sqlite(self):
        try:
            import sqlite3
        except ImportError:
            return
        real_connect = sqlite3.connect
        rec = self

        def connect(*a, **kw):
            con = real_connect(*a, **kw)
            try:
                def cb(stmt):
                    last = rec.events[-1] if rec.events else {}
                    if last.get("ev") == "sql" and last.get("via") != "sqlite3":
                        return          # already recorded at the DB-API / ORM level, with params
                    f, l, fn = rec.app_caller(sys._getframe(1))
                    if f and not re.match(r"^\s*(BEGIN|COMMIT|ROLLBACK|SAVEPOINT|RELEASE|PRAGMA)\b", stmt, re.I):
                        rec.emit({"ev": "sql", "file": f, "line": l, "fn": fn, "depth": rec.depth + 1, "sql": stmt.strip()[:2000],
                                  "op": (stmt.strip().split() or ["?"])[0].upper(), "via": "sqlite3"})
                con.set_trace_callback(cb)
            except Exception:       # noqa
                pass
            return con
        sqlite3.connect = connect

    def write(self, status, error=None):
        data = {"tool": "trace_py", "version": 1, "root": self.root.rstrip(os.sep), "status": status,
                "argv": sys.argv[1:], "duration_ms": round((time.time() - self.started) * 1000, 1),
                "truncated": self.truncated, "events": self.events}
        if error:
            data["error"] = error
        d = os.path.dirname(os.path.abspath(self.out))
        if not os.path.isdir(d):
            os.makedirs(d)
        with open(self.out, "w") as fh:
            json.dump(data, fh, indent=0, default=str)
        calls = sum(1 for e in self.events if e["ev"] == "call")
        changed = sum(1 for e in self.events if e.get("changes"))
        sys.stderr.write("\n[trace_py] %s: %d events (%d calls, %d lines with data changes, %d sql, %d http)%s -> %s\n" % (
            status, len(self.events), calls, changed, sum(1 for e in self.events if e["ev"] == "sql"),
            sum(1 for e in self.events if e["ev"] == "http"), " TRUNCATED" if self.truncated else "", self.out))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", default=".", help="project root: only files under it are traced")
    ap.add_argument("--out", required=True, help="where to write the JSON event log")
    ap.add_argument("--max-events", type=int, default=20000)
    ap.add_argument("--include-tests", action="store_true", help="also trace test files (harnesses in .investigate are always traced)")
    ap.add_argument("--focus", action="append", default=[], help="only trace files whose path contains this (repeatable)")
    ap.add_argument("--entry", help="record only while inside this function (e.g. sign_in or AuthService.sign_in)")
    ap.add_argument("--no-mask", action="store_true", help="don't redact secret-looking values")
    ap.add_argument("target", nargs=argparse.REMAINDER, help="-- script.py [args] | -- -m module [args]")
    a = ap.parse_args()
    target = a.target[1:] if a.target[:1] == ["--"] else a.target
    if not target:
        ap.error("nothing to run: pass `-- script.py` or `-- -m pytest file.py`")
    rec = Recorder(a.root, a.out, a.max_events, not a.no_mask, a.include_tests, a.focus, a.entry)
    rec.hook_sqlite()
    sys.path.insert(0, os.path.realpath(a.root))
    status, error, code = "ok", None, 0
    if target[0] == "-m":
        sys.argv = [target[1]] + target[2:]
        runner = lambda: runpy.run_module(target[1], run_name="__main__", alter_sys=True)
    else:
        sys.argv = list(target)
        sys.path.insert(0, os.path.dirname(os.path.abspath(target[0])))
        runner = lambda: runpy.run_path(target[0], run_name="__main__")
    threading.settrace(rec.global_trace)
    sys.settrace(rec.global_trace)
    try:
        runner()
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
        status = "ok" if not code else "exit %s" % code
    except BaseException as e:      # noqa - record, then re-raise for the caller's exit code
        status = "exception"
        error = {"type": type(e).__name__, "message": str(e)[:1000]}
        code = 1
    finally:
        sys.settrace(None)
        threading.settrace(None)
        rec.write(status, error)
    sys.exit(code)


if __name__ == "__main__":
    main()
