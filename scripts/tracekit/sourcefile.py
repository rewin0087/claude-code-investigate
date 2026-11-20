"""Source files of the repo: their lines, snippets and enclosing definitions (cached)."""
import os
import re

DEFINITION_LINE = re.compile(r"^\s*(?:export\s+)?(?:public|private|protected|static|async|def|function|func|fun|fn|class|module)\b")


class SourceFile(object):
    def __init__(self, repo, rel):
        self.repo, self.rel = repo, rel
        self._lines = None

    @property
    def exists(self):
        return self.lines is not None

    @property
    def lines(self):
        if self._lines is None:
            try:
                with open(os.path.join(self.repo, self.rel), errors="replace") as fh:
                    text = fh.read()
            except OSError:
                return None
            lines = text.split("\n")
            if lines and lines[-1] == "":
                lines.pop()           # a trailing newline is not an extra line
            self._lines = lines
        return self._lines

    def __len__(self):
        return len(self.lines or [])

    def snippet(self, start, end=None, pad=2, max_lines=40):
        """Numbered lines around start..end, the range itself marked with '>'."""
        lines = self.lines
        if not lines:
            return ""
        end = end or start
        a, b = max(1, start - pad), min(len(lines), end + pad, start + max_lines)
        width = len(str(b))
        return "\n".join("%s%*d | %s" % (">" if start <= i <= end else " ", width, i, lines[i - 1]) for i in range(a, b + 1))

    def enclosing_definition(self, line):
        """Nearest definition at or above `line` (best effort, indentation-agnostic)."""
        lines = self.lines or []
        for i in range(min(line, len(lines)) - 1, -1, -1):
            if DEFINITION_LINE.match(lines[i]):
                return {"path": self.rel, "line": i + 1, "text": lines[i].strip()[:160]}
        return None


class SourceCache(object):
    """One SourceFile per path, so each file is read at most once."""

    def __init__(self, repo):
        self.repo, self._files = repo, {}

    def get(self, rel):
        f = self._files.get(rel)
        if f is None:
            f = self._files[rel] = SourceFile(self.repo, rel)
        return f
