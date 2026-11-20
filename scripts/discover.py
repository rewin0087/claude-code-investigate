#!/usr/bin/env python3
"""Map the app: stack, DB schema/models, factories/fixtures/seeds, API contracts, routes, tests, DB config.

Usage: discover.py --dir <OUT_DIR> [--focus "user session login"]

Writes <OUT_DIR>/discovery.json and prints a digest. Nothing is executed.
Logic lives in tracekit.discover; this file is only the CLI.
"""
import argparse
import json
import os
import sys

from tracekit import InvestigateError
from tracekit.discover import discover, format_digest
from tracekit.workspace import list_files, load_meta


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dir", required=True, help="OUT_DIR printed by prepare.py")
    ap.add_argument("--focus", help="words to rank matching files first (e.g. the model / feature names)")
    a = ap.parse_args(argv)
    try:
        meta = load_meta(a.dir)
    except InvestigateError as e:
        sys.exit("discover.py: " + str(e))
    repo = meta["repo"]
    d = discover(repo, list_files(repo), a.focus)
    path = os.path.abspath(os.path.join(a.dir, "discovery.json"))
    with open(path, "w") as fh:
        json.dump(d, fh, indent=1)
    print(format_digest(d, path))


if __name__ == "__main__":
    main()
