"""The output folder (<repo>/.investigate/<slug>/) and its meta.json."""
import json
import os
import time

from .errors import InvestigateError
from .gitcmd import current_branch, ensure_ignored, head_sha, ls_files, repo_root

WALK_SKIP = {".git", "node_modules", "vendor", ".venv", "venv", "dist", "build", "tmp", "coverage", ".investigate",
             ".code-review", "__pycache__", ".tox", ".next", "target"}


def resolve_repo(path):
    path = os.path.abspath(path)
    if not os.path.isdir(path):
        raise InvestigateError("not a directory: %s" % path)
    return repo_root(path) or path


def list_files(repo):
    files = ls_files(repo)
    if files:
        return files
    out = []
    for d, dirs, names in os.walk(repo):
        dirs[:] = [x for x in dirs if x not in WALK_SKIP and not x.startswith(".")]
        out.extend(os.path.relpath(os.path.join(d, n), repo) for n in names)
        if len(out) > 50000:
            break
    return out


def out_dir(repo, slug):
    d = os.path.join(repo, ".investigate", slug)
    for sub in ("", "harness", "trace", "data"):
        os.makedirs(os.path.join(d, sub), exist_ok=True)
    ensure_ignored(repo)
    return d


def write_meta(d, repo, cwd, targets, slug):
    meta = {"repo": repo, "repo_name": os.path.basename(repo), "branch": current_branch(repo) or None,
            "sha": head_sha(repo) or None, "cwd": cwd, "slug": slug, "out_dir": d,
            "created": time.strftime("%Y-%m-%d %H:%M:%S"), "targets": targets}
    with open(os.path.join(d, "meta.json"), "w") as fh:
        json.dump(meta, fh, indent=2)
    return meta


def load_meta(d):
    p = os.path.join(d, "meta.json")
    if not os.path.exists(p):
        raise InvestigateError("meta.json not found in %s (run prepare.py first)" % d)
    with open(p) as fh:
        return json.load(fh)
