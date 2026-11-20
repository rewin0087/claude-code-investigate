"""Guard rails for reading sample rows from a LOCAL development / test database.

SqlGuard   one statement; SELECT / WITH / EXPLAIN / SHOW / DESCRIBE / PRAGMA table_info only;
           no write or DDL keywords anywhere; a row LIMIT is always applied.
DatabaseUrl  parses postgres:// mysql:// sqlite:/// URLs and refuses non-local hosts and
           production-looking names.
The queries themselves run in dbrunner (read-only sessions); rows are masked by masking.RowMasker.
"""
import re
from urllib.parse import unquote, urlparse

from .errors import InvestigateError

READ_START = re.compile(r"^\s*(select|with|explain|show|describe|desc|pragma\s+(table_info|index_list|foreign_key_list))\b", re.I)
WRITE_WORDS = re.compile(r"\b(insert|update|delete|merge|upsert|drop|create|alter|truncate|grant|revoke|attach|detach"
                         r"|vacuum|reindex|copy|call|exec|execute|do|lock|set|reset|load_file|into\s+outfile|pg_sleep"
                         r"|pg_read_file|pg_write_file|lo_import|lo_export|dblink|nextval|setval)\b", re.I)
LOCAL_HOSTS = {"", "localhost", "127.0.0.1", "::1", "0.0.0.0", "host.docker.internal"}


class SqlGuard(object):
    @staticmethod
    def check(sql):
        """The statement without a trailing ';', or InvestigateError if it isn't a single read."""
        stmt = sql.strip().rstrip(";").strip()
        no_strings = re.sub(r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"", "''", stmt)
        if ";" in no_strings:
            raise InvestigateError("Only one statement is allowed.")
        if not READ_START.match(stmt):
            raise InvestigateError("Only read queries are allowed (SELECT, WITH, EXPLAIN, SHOW, DESCRIBE, PRAGMA table_info).")
        bad = WRITE_WORDS.search(re.sub(r"(?i)^\s*(show|describe|desc|pragma)\b", "", no_strings))
        if bad:
            raise InvestigateError("Refusing a query containing '%s': this tool is read-only." % bad.group(0))
        return stmt

    @staticmethod
    def with_limit(stmt, limit):
        if re.match(r"^\s*(select|with)\b", stmt, re.I) and not re.search(r"\blimit\s+\d+\s*(offset\s+\d+\s*)?$", stmt, re.I):
            return "SELECT * FROM (%s) AS _q LIMIT %d" % (stmt, limit)
        return stmt

    @classmethod
    def prepare(cls, sql, limit):
        return cls.with_limit(cls.check(sql), limit)


class DatabaseUrl(object):
    """A parsed connection target: kind sqlite | postgres | mysql, plus its connection fields."""

    def __init__(self, kind, path=None, host=None, port=None, user="", password="", db=""):
        self.kind, self.path, self.host, self.port = kind, path, host, port
        self.user, self.password, self.db = user, password, db

    @classmethod
    def parse(cls, url=None, sqlite_path=None):
        if sqlite_path:
            return cls("sqlite", path=sqlite_path)
        if not url:
            raise InvestigateError("Give --url (postgres://, mysql://, sqlite:///path) or --sqlite <file>.")
        u = urlparse(url)
        scheme = u.scheme.split("+")[0].lower()
        if scheme in ("sqlite", "sqlite3"):
            return cls("sqlite", path=unquote(u.path if not u.netloc else "/" + u.netloc + u.path))
        if scheme not in ("postgres", "postgresql", "mysql", "mariadb"):
            raise InvestigateError("Unsupported database URL scheme '%s'." % u.scheme)
        host = (u.hostname or "").lower()
        if host not in LOCAL_HOSTS and not host.startswith("/"):
            raise InvestigateError("Refusing non-local database host '%s'. Only local dev/test databases are queried." % host)
        if re.search(r"prod|production|live", (u.path or "") + host, re.I):
            raise InvestigateError("Refusing a database whose name looks like production.")
        return cls("postgres" if scheme.startswith("postgres") else "mysql", host=host or "localhost", port=u.port,
                   user=unquote(u.username or ""), password=unquote(u.password or ""), db=(u.path or "/").lstrip("/"))
