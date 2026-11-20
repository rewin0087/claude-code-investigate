"""Git: a thin, testable wrapper around the git CLI for one repository."""
import os
import subprocess

from .errors import InvestigateError


class Git(object):
    def __init__(self, cwd):
        self.cwd = cwd

    def run(self, args, check=True):
        """stdout of `git <args>`. With check=False a failing command returns ""."""
        r = subprocess.run(["git", "-c", "core.quotepath=off"] + list(args), cwd=self.cwd,
                           capture_output=True, text=True, errors="replace")
        if r.returncode != 0:
            if check:
                raise InvestigateError("git %s failed:\n%s" % (" ".join(args), r.stderr.strip()))
            return ""
        return r.stdout

    def root(self):
        return self.run(["rev-parse", "--show-toplevel"], check=False).strip()

    def branch(self):
        return self.run(["rev-parse", "--abbrev-ref", "HEAD"], check=False).strip()

    def head_sha(self):
        return self.run(["rev-parse", "--short", "HEAD"], check=False).strip()

    def ls_files(self):
        """Tracked plus untracked-but-not-ignored files, relative to the repo root."""
        out = self.run(["ls-files", "--cached", "--others", "--exclude-standard"], check=False)
        return [p for p in out.split("\n") if p]

    def grep_word(self, word):
        """`path:line:text` rows for lines containing the literal word."""
        return self.run(["grep", "-n", "-I", "-w", "-F", word], check=False)

    def grep_files(self, pattern):
        return [p for p in self.run(["grep", "-l", "-I", "-E", pattern], check=False).split("\n") if p]

    def ensure_ignored(self, entry=".investigate/"):
        """Keep investigation output out of commits without touching the tracked .gitignore."""
        git_dir = self.run(["rev-parse", "--git-common-dir"], check=False).strip()
        if not git_dir:
            return
        exclude = os.path.join(git_dir if os.path.isabs(git_dir) else os.path.join(self.cwd, git_dir), "info", "exclude")
        try:
            os.makedirs(os.path.dirname(exclude), exist_ok=True)
            existing = open(exclude).read() if os.path.exists(exclude) else ""
            if entry not in existing.split("\n"):
                with open(exclude, "a") as fh:
                    fh.write(("" if existing.endswith("\n") or not existing else "\n") + entry + "\n")
        except OSError:
            pass
