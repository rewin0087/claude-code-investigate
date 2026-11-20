#!/usr/bin/env python3
"""Validate investigation.json and build the interactive HTML report + chat summary.

Usage: render.py --dir <OUT_DIR> [--no-open] [--out report.html]

Embeds the referenced source code, recorded traces, context sources and the parsed stack
trace, writes report.html and summary.md, and prints the summary for the chat. Warnings are
printed as ACTION REQUIRED: fix them in investigation.json and run again.
Logic lives in tracekit.model / .snippets / .report / .summary; this file is only the CLI.
"""
import argparse
import os
import subprocess
import sys

from tracekit import InvestigateError
from tracekit.model import load_json, validate
from tracekit.report import build_html
from tracekit.snippets import build as build_snippets, referenced_lines
from tracekit.summary import file_url, terminal_summary
from tracekit.tracedigest import compact, load as load_trace
from tracekit.workspace import load_meta


def open_file(path):
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", path])
        elif sys.platform.startswith("win"):
            os.startfile(path)  # noqa
        else:
            subprocess.Popen(["xdg-open", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:      # noqa
        print("Could not open browser automatically: %s" % e)


def optional(d, name):
    p = os.path.join(d, name)
    if not os.path.exists(p):
        return None
    try:
        return load_json(p, name)
    except InvestigateError as e:
        print("WARNING: ignoring " + str(e))
        return None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dir", required=True, help="OUT_DIR printed by prepare.py")
    ap.add_argument("--out", help="output HTML path (default <dir>/report.html)")
    ap.add_argument("--no-open", action="store_true")
    a = ap.parse_args(argv)
    d = os.path.abspath(a.dir)
    try:
        meta = load_meta(d)
        inv = load_json(os.path.join(d, "investigation.json"), "investigation.json")
    except InvestigateError as e:
        sys.exit("render.py: " + str(e))
    repo = meta["repo"]
    errors, warnings = validate(inv, repo, d)
    if errors:
        print("investigation.json has %d error(s):" % len(errors))
        for e in errors:
            print("  - " + e)
        sys.exit(1)

    traces = {}
    extra = []
    for sc in inv.get("scenarios") or []:
        if sc.get("trace"):
            try:
                t = compact(load_trace(os.path.join(d, sc["trace"])))
                traces[sc["id"]] = t
                extra += [(e.get("file"), e.get("line")) for e in t["events"] if e.get("file")][:400]
            except InvestigateError as e:
                warnings.append(str(e))
    st = optional(d, "stacktrace.json")
    if st:
        extra += [(f.get("path"), f.get("line")) for f in st.get("frames", []) if f.get("app")]
    discovery = optional(d, "discovery.json")
    payload = {
        "meta": meta, "investigation": inv, "traces": traces,
        "sources": build_snippets(repo, referenced_lines(inv, extra)),
        "context": optional(d, "context.json"), "stacktrace": st,
        "discovery": {"stack": discovery.get("stack"), "frameworks": discovery.get("frameworks")} if discovery else None,
    }
    out = os.path.abspath(a.out or os.path.join(d, "report.html"))
    md = terminal_summary(inv, meta, out)
    payload["summary_md"] = md
    try:
        html = build_html(payload)
    except InvestigateError as e:
        sys.exit("render.py: " + str(e))
    with open(out, "w") as fh:
        fh.write(html)
    with open(os.path.join(d, "summary.md"), "w") as fh:
        fh.write(md)
    print("REPORT=%s" % out)
    print("REPORT_URL=%s" % file_url(out))
    print("----- SUMMARY (relay this to the user) -----")
    print(md)
    if warnings:
        print("----- ACTION REQUIRED: %d warning(s) -----" % len(warnings))
        for w in warnings:
            print("  - " + w)
        print("Fix these in investigation.json and run render.py again before replying. "
              "If one can't be fixed, say why in your reply.")
    if not a.no_open:
        open_file(out)


if __name__ == "__main__":
    main()
