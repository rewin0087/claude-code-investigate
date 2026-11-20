#!/usr/bin/env python3
"""Validate investigation.json and build the interactive HTML report + chat summary.

Usage: render.py --dir <OUT_DIR> [--no-open] [--out report.html]

Embeds the referenced source code, recorded traces, context sources and the parsed stack
trace, writes report.html and summary.md, and prints the summary for the chat. Warnings are
printed as ACTION REQUIRED: fix them in investigation.json and run again.
Logic lives in tracekit.renderer.Renderer; this file is only the CLI.
"""
import argparse
import os
import subprocess
import sys

from tracekit import InvestigateError
from tracekit.renderer import Renderer


def open_in_browser(path):
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", path])
        elif sys.platform.startswith("win"):
            os.startfile(path)  # noqa
        else:
            subprocess.Popen(["xdg-open", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:      # noqa
        print("Could not open browser automatically: %s" % e)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dir", required=True, help="OUT_DIR printed by prepare.py")
    ap.add_argument("--out", help="output HTML path (default <dir>/report.html)")
    ap.add_argument("--no-open", action="store_true")
    a = ap.parse_args(argv)
    try:
        renderer = Renderer(a.dir)
        result = renderer.render(a.out)
    except InvestigateError as e:
        sys.exit("render.py: " + str(e))
    for note in renderer.notes:
        print("WARNING: " + note)
    if result.errors:
        print("investigation.json has %d error(s):" % len(result.errors))
        for e in result.errors:
            print("  - " + e)
        sys.exit(1)
    print("REPORT=%s" % result.report_path)
    print("REPORT_URL=%s" % result.report_url)
    print("----- SUMMARY (relay this to the user) -----")
    print(result.summary)
    if result.warnings:
        print("----- ACTION REQUIRED: %d warning(s) -----" % len(result.warnings))
        for w in result.warnings:
            print("  - " + w)
        print("Fix these in investigation.json and run render.py again before replying. "
              "If one can't be fixed, say why in your reply.")
    if not a.no_open:
        open_in_browser(result.report_path)


if __name__ == "__main__":
    main()
