#!/usr/bin/env python3
"""Parse a stack trace / error log and map its frames to lines in the repo.

Usage: stacktrace.py --dir <OUT_DIR> --source <file|text>

Writes <OUT_DIR>/stacktrace.json and prints the frames, innermost first, with snippets of the
frames in this repo and the frame to start from.
Logic lives in tracekit.stacktrace (StackTraceParser, FrameMapper, StackTraceDigest).
"""
import argparse
import os
import sys

from tracekit import InvestigateError
from tracekit.jsonio import write_json
from tracekit.sourcefile import SourceCache
from tracekit.stacktrace import FrameMapper, StackTraceDigest, StackTraceParser
from tracekit.workspace import Workspace


def read_source(source, cwd):
    """The trace text: from a file (path or @path) or the argument itself."""
    src = source[1:] if source.startswith("@") else source
    path = src if os.path.isabs(src) else os.path.join(cwd or os.getcwd(), src)
    if "\n" not in source and os.path.isfile(path):
        with open(path, errors="replace") as fh:
            return fh.read()
    return source


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dir", required=True, help="OUT_DIR printed by prepare.py")
    ap.add_argument("--source", required=True, help="file with the trace, or the trace text itself")
    a = ap.parse_args(argv)
    try:
        ws = Workspace.load(a.dir)
    except InvestigateError as e:
        sys.exit("stacktrace.py: " + str(e))
    text = read_source(a.source, ws.meta.get("cwd"))
    parsed = FrameMapper(ws.repo, ws.files).map(StackTraceParser().parse(text))
    parsed["raw"] = text[:200000]
    path = write_json(ws.path("stacktrace.json"), parsed)
    if not parsed["frames"] and not parsed["error"]:
        print("No stack frames or error line recognised. Treat the text as a bug description instead.")
    print(StackTraceDigest(SourceCache(ws.repo)).format(parsed))
    print("STACKTRACE_JSON=%s" % path)


if __name__ == "__main__":
    main()
