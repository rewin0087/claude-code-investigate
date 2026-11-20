#!/usr/bin/env python3
"""Summarise a runtime trace (from trace_py.py / trace_rb.rb) as a readable per-line timeline.

Usage: trace_digest.py <trace.json> [--max-lines 400]

Prints the call tree with the variables / attributes each line changed, SQL, HTTP calls,
exceptions and return values, plus which lines ran in each file (to tell which branches ran).
Logic lives in tracekit.tracelog (TraceLog, TraceDigest); this file is only the CLI.
"""
import argparse
import sys

from tracekit import InvestigateError
from tracekit.tracelog import TraceDigest, TraceLog


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("trace")
    ap.add_argument("--max-lines", type=int, default=400)
    a = ap.parse_args(argv)
    try:
        print(TraceDigest(TraceLog.load(a.trace), a.max_lines).format())
    except InvestigateError as e:
        sys.exit("trace_digest.py: " + str(e))


if __name__ == "__main__":
    main()
