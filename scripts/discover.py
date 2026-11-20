#!/usr/bin/env python3
"""Map the app: stack, DB schema/models, factories/fixtures/seeds, API contracts, routes, tests, DB config.

Usage: discover.py --dir <OUT_DIR> [--focus "user session login"]

Writes <OUT_DIR>/discovery.json and prints a digest. Nothing is executed.
Logic lives in tracekit.discover.AppDiscovery; this file is only the CLI.
"""
import argparse
import sys

from tracekit import InvestigateError
from tracekit.discover import AppDiscovery
from tracekit.jsonio import write_json
from tracekit.workspace import Workspace


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dir", required=True, help="OUT_DIR printed by prepare.py")
    ap.add_argument("--focus", help="words to rank matching files first (e.g. the model / feature names)")
    a = ap.parse_args(argv)
    try:
        ws = Workspace.load(a.dir)
    except InvestigateError as e:
        sys.exit("discover.py: " + str(e))
    result = AppDiscovery(ws.repo, ws.files).run(a.focus)
    print(AppDiscovery.digest(result, write_json(ws.path("discovery.json"), result)))


if __name__ == "__main__":
    main()
