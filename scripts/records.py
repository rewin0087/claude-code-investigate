#!/usr/bin/env python3
"""Fetch the existing records a bug affected (and the rows around them) from a LOCAL dev/test DB.

Usage: records.py (--url <db url> | --url-env DATABASE_URL | --sqlite <file>)
                  --record <Model#id | table:id | "table where <condition>"> [--record ...]
                  [--depth 1] [--children 5] [--save <OUT_DIR>/data/records.json] [--keep-pii]

Follows declared foreign keys and Rails-style *_id columns to parents and children. Read-only:
single guarded SELECTs, local hosts only, read-only session, LIMIT. Secrets are redacted and
personal data masked (use --keep-pii only for synthetic dev data).
Logic lives in tracekit.records (RecordRef, SchemaIntrospector, RecordGraph); this file is only the CLI.
"""
import argparse
import json
import os
import sys

from tracekit import InvestigateError
from tracekit.dbrunner import QueryRunner
from tracekit.dbsafe import DatabaseUrl
from tracekit.masking import RowMasker
from tracekit.records import RecordGraph, RecordRef


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--url")
    ap.add_argument("--url-env", help="name of an environment variable holding the URL")
    ap.add_argument("--sqlite")
    ap.add_argument("--record", action="append", required=True, help="Model#id, table:id, or \"table where <condition>\"")
    ap.add_argument("--depth", type=int, default=1, help="how many levels of children to follow (parents: always 1)")
    ap.add_argument("--children", type=int, default=5, help="max child rows per relation")
    ap.add_argument("--save", help="write the record graph as JSON here (e.g. <OUT_DIR>/data/records.json)")
    ap.add_argument("--keep-pii", action="store_true", help="don't mask emails/names/phones (secrets stay redacted)")
    a = ap.parse_args(argv)
    url = a.url or (os.environ.get(a.url_env) if a.url_env else None)
    if a.url_env and not url:
        sys.exit("records.py: environment variable %s is not set" % a.url_env)
    try:
        runner = QueryRunner.for_target(DatabaseUrl.parse(url, a.sqlite))
        graph = RecordGraph(runner, RowMasker(a.keep_pii), a.children, a.depth)
        result = [graph.fetch(RecordRef.parse(r)) for r in a.record]
    except InvestigateError as e:
        sys.exit("records.py: " + str(e))
    for node in result:
        print(RecordGraph.digest(node))
        print()
    if a.save:
        os.makedirs(os.path.dirname(os.path.abspath(a.save)), exist_ok=True)
        with open(a.save, "w") as fh:
            json.dump({"records": result}, fh, indent=1, default=str)
        print("RECORDS_JSON=%s" % os.path.abspath(a.save))


if __name__ == "__main__":
    main()
