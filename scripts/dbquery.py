#!/usr/bin/env python3
"""Read sample rows from a LOCAL dev/test database, read-only, with secrets and personal data masked.

Usage: dbquery.py (--url <postgres://..|mysql://..|sqlite:///path> | --url-env DATABASE_URL | --sqlite <file>)
                  --sql "SELECT ..." [--limit 20] [--save <OUT_DIR>/data/name.json] [--keep-pii]

Only single SELECT / WITH / EXPLAIN / SHOW / DESCRIBE / PRAGMA table_info statements run, only
on localhost / sockets / sqlite files, inside a read-only session, with a LIMIT. Use the rows to
shape realistic inputs; never copy real personal data into the report.
Logic lives in tracekit.dbsafe; this file is only the CLI.
"""
import argparse
import os
import sys

from tracekit import InvestigateError
from tracekit.dbsafe import mask, parse_target, run_query, to_json


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--url")
    ap.add_argument("--url-env", help="name of an environment variable holding the URL")
    ap.add_argument("--sqlite")
    ap.add_argument("--sql", required=True)
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--save", help="also write the (masked) rows as JSON here")
    ap.add_argument("--keep-pii", action="store_true", help="don't mask emails/names/phones (secrets stay redacted)")
    a = ap.parse_args(argv)
    url = a.url or (os.environ.get(a.url_env) if a.url_env else None)
    if a.url_env and not url:
        sys.exit("dbquery.py: environment variable %s is not set" % a.url_env)
    try:
        cols, rows = run_query(parse_target(url, a.sqlite), a.sql, min(max(a.limit, 1), 200))
    except InvestigateError as e:
        sys.exit("dbquery.py: " + str(e))
    rows = mask(cols, rows, a.keep_pii)
    out = to_json(cols, rows)
    if a.save:
        os.makedirs(os.path.dirname(os.path.abspath(a.save)), exist_ok=True)
        with open(a.save, "w") as fh:
            fh.write(out)
    print("%d row(s), columns: %s" % (len(rows), ", ".join(cols)))
    print(out)


if __name__ == "__main__":
    main()
