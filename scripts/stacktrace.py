#!/usr/bin/env python3
"""Parse a stack trace / error log and map its frames to lines in the repo.

Usage: stacktrace.py --dir <OUT_DIR> --source <file|text>

Writes <OUT_DIR>/stacktrace.json (+ a copy of the raw trace) and prints the frames, innermost
first, with snippets of the frames in this repo and the frame to start from.
Logic lives in tracekit.stacktrace; this file is only the CLI.
"""
import argparse
import json
import os
import sys

from tracekit import InvestigateError
from tracekit.stacktrace import format_digest, map_frames, parse
from tracekit.target import snippet
from tracekit.workspace import list_files, load_meta


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dir", required=True, help="OUT_DIR printed by prepare.py")
    ap.add_argument("--source", required=True, help="file with the trace, or the trace text itself")
    a = ap.parse_args(argv)
    try:
        meta = load_meta(a.dir)
    except InvestigateError as e:
        sys.exit("stacktrace.py: " + str(e))
    src = a.source[1:] if a.source.startswith("@") else a.source
    path = src if os.path.isabs(src) else os.path.join(meta.get("cwd") or os.getcwd(), src)
    if "\n" not in a.source and os.path.isfile(path):
        with open(path, errors="replace") as fh:
            text = fh.read()
    else:
        text = a.source
    repo = meta["repo"]
    parsed = map_frames(parse(text), repo, list_files(repo))
    parsed["raw"] = text[:200000]
    with open(os.path.join(a.dir, "stacktrace.json"), "w") as fh:
        json.dump(parsed, fh, indent=1)
    if not parsed["frames"] and not parsed["error"]:
        print("No stack frames or error line recognised. Treat the text as a bug description instead.")
    print(format_digest(parsed, repo, snippet))
    print("STACKTRACE_JSON=%s" % os.path.abspath(os.path.join(a.dir, "stacktrace.json")))


if __name__ == "__main__":
    main()
