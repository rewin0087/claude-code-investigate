#!/usr/bin/env python3
"""Start an investigation: resolve the target, create the output folder, collect context.

Usage: prepare.py --repo <dir> [--target <file[:a-b]|Class#method|"POST /path"|text> ...]
                  [--source <file|url|text> ...] [--slug <name>] [--cwd DIR]

Prints OUT_DIR=, the candidate definitions / route lines for each target with a snippet,
and the context digest (bug report, repro steps, specs, images to Read, URLs to fetch).
Logic lives in tracekit.preparer.Preparer; this file is only the CLI.
"""
import argparse
import os
import sys

from tracekit import InvestigateError
from tracekit.preparer import Preparer


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo", default=".", help="repository (or any folder inside it)")
    ap.add_argument("--target", action="append", default=[], help="what to investigate (repeatable)")
    ap.add_argument("--source", action="append", default=[], help="bug report / repro steps / spec: file, URL or text")
    ap.add_argument("--slug", help="name of the output folder (default: from the target)")
    ap.add_argument("--cwd", default=os.getcwd(), help="base directory for relative paths")
    a = ap.parse_args(argv)
    try:
        print(Preparer(a.repo, a.cwd).run(a.target, a.source, a.slug))
    except InvestigateError as e:
        sys.exit("prepare.py: " + str(e))


if __name__ == "__main__":
    main()
