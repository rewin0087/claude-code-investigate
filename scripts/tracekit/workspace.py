"""Workspace: the repository under investigation and its output folder (<repo>/.investigate/<slug>/)."""
import os
import re
import time

from .errors import InvestigateError
from .gitcmd import Git
from .jsonio import load_json, write_json

WALK_SKIP = {".git", "node_modules", "vendor", ".venv", "venv", "dist", "build", "tmp", "coverage", ".investigate",
             ".code-review", "__pycache__", ".tox", ".next", "target"}
SUBDIRS = ("harness", "trace", "data")


def slugify(text, limit=48):
    s = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()
    return (s[:limit].rstrip("-")) or "investigation"


class Workspace(object):
    def __init__(self, repo, out_dir=None, meta=None):
        self.repo = repo
        self.git = Git(repo)
        self.out_dir = out_dir
        self.meta = meta or {}
        self._files = None

    @classmethod
    def for_path(cls, path):
        """The repository containing `path` (or the folder itself when it isn't a git repo)."""
        path = os.path.abspath(path)
        if not os.path.isdir(path):
            raise InvestigateError("not a directory: %s" % path)
        return cls(Git(path).root() or path)

    @classmethod
    def load(cls, out_dir):
        """Re-open an output folder created by prepare.py."""
        p = os.path.join(out_dir, "meta.json")
        if not os.path.exists(p):
            raise InvestigateError("meta.json not found in %s (run prepare.py first)" % out_dir)
        meta = load_json(p, "meta.json")
        return cls(meta["repo"], os.path.abspath(out_dir), meta)

    @property
    def files(self):
        if self._files is None:
            self._files = self.git.ls_files() or self._walk()
        return self._files

    def _walk(self):
        out = []
        for d, dirs, names in os.walk(self.repo):
            dirs[:] = [x for x in dirs if x not in WALK_SKIP and not x.startswith(".")]
            out.extend(os.path.relpath(os.path.join(d, n), self.repo) for n in names)
            if len(out) > 50000:
                break
        return out

    def create(self, slug):
        """Create (or reuse) .investigate/<slug>/ and keep it out of git."""
        self.out_dir = os.path.join(self.repo, ".investigate", slug)
        for sub in ("",) + SUBDIRS:
            os.makedirs(os.path.join(self.out_dir, sub), exist_ok=True)
        self.git.ensure_ignored()
        return self.out_dir

    def write_meta(self, cwd, targets, slug):
        self.meta = {"repo": self.repo, "repo_name": os.path.basename(self.repo), "branch": self.git.branch() or None,
                     "sha": self.git.head_sha() or None, "cwd": cwd, "slug": slug, "out_dir": self.out_dir,
                     "created": time.strftime("%Y-%m-%d %H:%M:%S"), "targets": targets}
        write_json(self.path("meta.json"), self.meta, indent=2)
        return self.meta

    def path(self, *parts):
        return os.path.join(self.out_dir, *parts)

    def optional_json(self, name, on_error=None):
        """Contents of an optional output file, or None (a broken file is reported, not fatal)."""
        p = self.path(name)
        if not os.path.exists(p):
            return None
        try:
            return load_json(p, name)
        except InvestigateError as e:
            if on_error:
                on_error("ignoring " + str(e))
            return None
