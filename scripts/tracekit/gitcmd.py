"""Thin wrappers around the git CLI."""
import os
import subprocess

from .errors import InvestigateError


def git(args, cwd, check=True):
    """Run git and return stdout. With check=False a failing command returns ""."""
    r = subprocess.run(["git", "-c", "core.quotepath=off"] + list(args), cwd=cwd,
                       capture_output=True, text=True, errors="replace")
    if r.returncode != 0:
        if check:
            raise InvestigateError("git %s failed:\n%s" % (" ".join(args), r.stderr.strip()))
        return ""
    return r.stdout


def repo_root(path):
    return git(["rev-parse", "--show-toplevel"], path, check=False).strip()


def current_branch(cwd):
    return git(["rev-parse", "--abbrev-ref", "HEAD"], cwd, check=False).strip()


def head_sha(cwd):
    return git(["rev-parse", "--short", "HEAD"], cwd, check=False).strip()


def ls_files(cwd):
    """Tracked plus untracked-but-not-ignored files, relative to the repo root."""
    out = git(["ls-files", "--cached", "--others", "--exclude-standard"], cwd, check=False)
    return [p for p in out.split("\n") if p]


def ensure_ignored(repo, entry=".investigate/"):
    """Keep investigation output out of commits without touching the tracked .gitignore."""
    git_dir = git(["rev-parse", "--git-common-dir"], repo, check=False).strip()
    if not git_dir:
        return
    exclude = os.path.join(git_dir if os.path.isabs(git_dir) else os.path.join(repo, git_dir), "info", "exclude")
    try:
        os.makedirs(os.path.dirname(exclude), exist_ok=True)
        existing = open(exclude).read() if os.path.exists(exclude) else ""
        if entry not in existing.split("\n"):
            with open(exclude, "a") as fh:
                fh.write(("" if existing.endswith("\n") or not existing else "\n") + entry + "\n")
    except OSError:
        pass
