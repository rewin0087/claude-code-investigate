#!/usr/bin/env python3
"""Start an investigation: resolve the target, create the output folder, collect context.

Usage: prepare.py --repo <dir> [--target <file[:a-b]|Class#method|"POST /path"|text> ...]
                  [--source <file|url|text> ...] [--slug <name>] [--cwd DIR]

Prints OUT_DIR=, the candidate definitions / route lines for each target with a snippet,
and the context digest (bug report, repro steps, specs, images to Read, URLs to fetch).
Logic lives in tracekit.target / .workspace / .context; this file is only the CLI.
"""
import argparse
import os
import sys

from tracekit import InvestigateError
from tracekit.context import format_digest as context_digest, ingest
from tracekit.target import enclosing_definition, find_definitions, find_route, parse_target, slug, snippet
from tracekit.workspace import list_files, out_dir, resolve_repo, write_meta


def describe_target(t, repo, files):
    L = ["\n### Target: %s (%s)" % (t["label"], t["kind"])]
    if t["kind"] in ("range", "file"):
        start = t.get("start") or 1
        enc = enclosing_definition(repo, t["path"], start) if t["kind"] == "range" else None
        if enc:
            L.append("Enclosing definition: %s:%d  %s" % (enc["path"], enc["line"], enc["text"]))
            t["enclosing"] = enc
        L.append(snippet(repo, t["path"], start, t.get("end") or start, pad=2, max_lines=60))
    elif t["kind"] == "symbol":
        hits = find_definitions(t["name"], repo, t.get("owner"))
        t["candidates"] = hits
        if not hits:
            L.append("No definition found for %s: grep for it, or ask the user where it lives." % t["label"])
        for h in hits[:6]:
            L.append("- %s:%d  %s" % (h["path"], h["line"], h["text"]))
        if hits:
            L.append(snippet(repo, hits[0]["path"], hits[0]["line"], pad=1, max_lines=25))
    elif t["kind"] == "route":
        hits = find_route(t, repo, files)
        t["candidates"] = hits
        if not hits:
            L.append("No route/controller line mentions this path: search the router config by hand.")
        for h in hits[:10]:
            L.append("- %s:%d  %s" % (h["path"], h["line"], h["text"]))
    else:
        L.append("Free-text target: find the entry point from the words (git grep), the bug report, or the stack trace.")
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo", default=".", help="repository (or any folder inside it)")
    ap.add_argument("--target", action="append", default=[], help="what to investigate (repeatable)")
    ap.add_argument("--source", action="append", default=[], help="bug report / repro steps / spec: file, URL or text")
    ap.add_argument("--slug", help="name of the output folder (default: from the target)")
    ap.add_argument("--cwd", default=os.getcwd(), help="base directory for relative paths")
    a = ap.parse_args(argv)
    try:
        repo = resolve_repo(a.repo)
        files = list_files(repo)
        targets = [parse_target(t, repo, a.cwd) for t in a.target]
        name = slug(a.slug or (targets[0]["label"] if targets else "") or
                    (a.source[0] if a.source and not os.path.exists(a.source[0]) else os.path.basename(a.source[0]) if a.source else "") or
                    "investigation")
        d = out_dir(repo, name)
        parts = [describe_target(t, repo, files) for t in targets]
        write_meta(d, repo, a.cwd, targets, name)
        ctx = ingest(a.source, d, a.cwd) if a.source else None
    except InvestigateError as e:
        sys.exit("prepare.py: " + str(e))
    print("REPO=%s" % repo)
    print("OUT_DIR=%s" % d)
    print("Files in repo: %d" % len(files))
    for p in parts:
        print(p)
    if not targets:
        print("\nNo explicit target: use the editor selection, the stack trace, or the bug report to find the entry point.")
    if ctx:
        print("\n## Context\n" + context_digest(ctx, d))


if __name__ == "__main__":
    main()
