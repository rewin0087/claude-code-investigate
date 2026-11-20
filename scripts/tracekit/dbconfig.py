"""DbConfigReader: database settings for dev/test (config/database.yml, .env, docker compose), redacted."""
import os
import re
import shutil

SKIP = re.compile(r"(^|/)(node_modules|vendor|dist|build|\.venv|venv|tmp|coverage|\.git|\.investigate|\.code-review|\.branch-review|public/assets|log)/")
SECRET_KEY = re.compile(r"(pass(word)?|secret|token|key|credential|auth|private|dsn|url)", re.I)
URL_CRED = re.compile(r"(\w+://[^:/@\s]+:)([^@\s]+)(@)")
ENV_DB_KEY = re.compile(r"^\s*(?:export\s+)?([A-Z0-9_]*(?:DATABASE|DB_|POSTGRES|MYSQL|MONGO|REDIS|SQLITE)[A-Z0-9_]*)\s*=\s*(.*)$")
COMPOSE_DB_IMAGE = re.compile(r"image:\s*['\"]?((?:postgres|postgis|mysql|mariadb|mongo|redis|mcr\.microsoft\.com/mssql)[^\s'\"]*)")
CLIENTS = ("sqlite3", "psql", "mysql", "mongosh", "redis-cli")


def redact(key, value):
    """Hide passwords in URLs and the values of secret-looking keys."""
    v = URL_CRED.sub(r"\1***\3", value)
    if SECRET_KEY.search(key) and not re.search(r"url|database|host|port|adapter|username", key, re.I) and v and "<%" not in v:
        return "***"
    return v[:200]


class DbConfigReader(object):
    def __init__(self, repo, files):
        self.repo, self.files = repo, files
        self.found = []

    def read(self):
        """-> {settings: [{file, env, key, value}], clients: {name: on PATH?}, sqlite_files: [...]}"""
        if "config/database.yml" in set(self.files):
            self._database_yml()
        for f in self.files:
            base = os.path.basename(f)
            if re.match(r"^\.env(\.[\w.-]+)?$", base) and not SKIP.search(f):
                self._dotenv(f, base)
            elif re.match(r"^(docker-)?compose[\w.-]*\.ya?ml$", base):
                self._compose(f)
        clients = {c: bool(shutil.which(c)) for c in CLIENTS}
        sqlite = [f for f in self.files if re.search(r"\.(sqlite3?|db)$", f) and not SKIP.search(f)]
        return {"settings": self.found[:60], "clients": clients, "sqlite_files": sqlite[:10]}

    def _add(self, file, env, key, value):
        self.found.append({"file": file, "env": env, "key": key, "value": value})

    def _database_yml(self):
        section = None
        for line in self._text("config/database.yml").split("\n"):
            m = re.match(r"^(\w+):\s*(?:&\w+)?\s*$", line)
            if m:
                section = m.group(1)
                continue
            m = re.match(r"^\s+(adapter|database|host|port|url|username)\s*:\s*(.+)$", line)
            if m and section:
                self._add("config/database.yml", section, m.group(1), redact(m.group(1), m.group(2)))

    def _dotenv(self, f, base):
        for line in self._text(f).split("\n"):
            m = ENV_DB_KEY.match(line)
            if m:
                self._add(f, base, m.group(1), redact(m.group(1), m.group(2).strip().strip("'\"")))

    def _compose(self, f):
        for m in COMPOSE_DB_IMAGE.finditer(self._text(f)):
            self._add(f, "docker", "image", m.group(1))

    def _text(self, rel, limit=200000):
        try:
            with open(os.path.join(self.repo, rel), errors="replace") as fh:
                return fh.read(limit)
        except OSError:
            return ""
