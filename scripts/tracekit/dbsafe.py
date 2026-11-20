"""Read-only sample queries against a LOCAL development / test database.

Guard rails, all enforced here rather than trusted to the caller:
  * one statement, and it must be SELECT / WITH / EXPLAIN / SHOW / DESCRIBE / PRAGMA table_info
  * no write or DDL keywords anywhere in the statement
  * the host must be local (localhost, 127.0.0.1, ::1, a unix socket, or a sqlite file)
  * the session itself is read-only (sqlite mode=ro, postgres default_transaction_read_only,
    mysql SET SESSION TRANSACTION READ ONLY)
  * a row LIMIT is always applied
  * secret-like columns are redacted and personal data is masked before printing
"""
import json
import os
import re
import sqlite3
import subprocess
from urllib.parse import unquote, urlparse

from .errors import InvestigateError

READ_START = re.compile(r"^\s*(select|with|explain|show|describe|desc|pragma\s+(table_info|index_list|foreign_key_list))\b", re.I)
WRITE_WORDS = re.compile(r"\b(insert|update|delete|merge|upsert|drop|create|alter|truncate|grant|revoke|attach|detach"
                         r"|vacuum|reindex|copy|call|exec|execute|do|lock|set|reset|load_file|into\s+outfile|pg_sleep"
                         r"|pg_read_file|pg_write_file|lo_import|lo_export|dblink|nextval|setval)\b", re.I)
LOCAL_HOSTS = {"", "localhost", "127.0.0.1", "::1", "0.0.0.0", "host.docker.internal"}
SECRET_COL = re.compile(r"(password|passwd|pwd|secret|token|api_?key|private_?key|salt|hash|digest|otp|ssn|cvv|card_?number"
                        r"|iban|encrypted|credential|session_?id|cookie|signature)", re.I)
EMAIL = re.compile(r"([A-Za-z0-9._%+-])[A-Za-z0-9._%+-]*@([A-Za-z0-9.-]+\.[A-Za-z]{2,})")
PHONE = re.compile(r"(?<!\d)(\+?\d[\d\s().-]{7,}\d)(?!\d)")
PERSON_COL = re.compile(r"(^|_)(first_?name|last_?name|full_?name|name|phone|mobile|address|street|postcode|zip|dob|birth"
                        r"|ip_?address|national_id|tax_id)($|_)", re.I)


def check_sql(sql):
    stmt = sql.strip().rstrip(";").strip()
    no_strings = re.sub(r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"", "''", stmt)
    if ";" in no_strings:
        raise InvestigateError("Only one statement is allowed.")
    if not READ_START.match(stmt):
        raise InvestigateError("Only read queries are allowed (SELECT, WITH, EXPLAIN, SHOW, DESCRIBE, PRAGMA table_info).")
    if WRITE_WORDS.search(re.sub(r"(?i)^\s*(show|describe|desc|pragma)\b", "", no_strings)):
        bad = WRITE_WORDS.search(no_strings).group(0)
        raise InvestigateError("Refusing a query containing '%s': this tool is read-only." % bad)
    return stmt


def with_limit(stmt, limit):
    if re.match(r"^\s*(select|with)\b", stmt, re.I) and not re.search(r"\blimit\s+\d+\s*(offset\s+\d+\s*)?$", stmt, re.I):
        return "SELECT * FROM (%s) AS _q LIMIT %d" % (stmt, limit)
    return stmt


def parse_target(url=None, sqlite_path=None):
    if sqlite_path:
        return {"kind": "sqlite", "path": sqlite_path}
    if not url:
        raise InvestigateError("Give --url (postgres://, mysql://, sqlite:///path) or --sqlite <file>.")
    u = urlparse(url)
    scheme = u.scheme.split("+")[0].lower()
    if scheme in ("sqlite", "sqlite3"):
        return {"kind": "sqlite", "path": unquote(u.path if not u.netloc else "/" + u.netloc + u.path)}
    if scheme in ("postgres", "postgresql", "mysql", "mariadb"):
        host = (u.hostname or "").lower()
        if host not in LOCAL_HOSTS and not host.startswith("/"):
            raise InvestigateError("Refusing non-local database host '%s'. Only local dev/test databases are queried." % host)
        if re.search(r"prod|production|live", (u.path or "") + host, re.I):
            raise InvestigateError("Refusing a database whose name looks like production.")
        return {"kind": "postgres" if scheme.startswith("postgres") else "mysql", "url": url,
                "host": host or "localhost", "port": u.port, "user": unquote(u.username or ""),
                "password": unquote(u.password or ""), "db": (u.path or "/").lstrip("/")}
    raise InvestigateError("Unsupported database URL scheme '%s'." % u.scheme)


def run_query(target, sql, limit=20, timeout=20):
    stmt = with_limit(check_sql(sql), limit)
    if target["kind"] == "sqlite":
        path = os.path.abspath(target["path"])
        if not os.path.isfile(path):
            raise InvestigateError("sqlite file not found: %s" % path)
        con = sqlite3.connect("file:%s?mode=ro" % path, uri=True, timeout=timeout)
        try:
            cur = con.execute(stmt)
            cols = [c[0] for c in cur.description or []]
            rows = [list(r) for r in cur.fetchmany(limit)]
        finally:
            con.close()
        return cols, rows
    if target["kind"] == "postgres":
        env = dict(os.environ, PGOPTIONS="-c default_transaction_read_only=on -c statement_timeout=%d" % (timeout * 1000),
                   PGCONNECT_TIMEOUT="5")
        if target["password"]:
            env["PGPASSWORD"] = target["password"]
        cmd = ["psql", "-X", "-q", "-A", "-F", "\x1f", "-P", "footer=off", "-v", "ON_ERROR_STOP=1",
               "-h", target["host"], "-d", target["db"]]
        if target["port"]:
            cmd += ["-p", str(target["port"])]
        if target["user"]:
            cmd += ["-U", target["user"]]
        out = _run(cmd + ["-c", stmt], env, timeout)
        return _split(out, "\x1f", limit)
    env = dict(os.environ)
    if target["password"]:
        env["MYSQL_PWD"] = target["password"]
    cmd = ["mysql", "--batch", "--raw", "-h", target["host"] if target["host"] != "localhost" else "127.0.0.1"]
    if target["port"]:
        cmd += ["-P", str(target["port"])]
    if target["user"]:
        cmd += ["-u", target["user"]]
    cmd += [target["db"], "-e", "SET SESSION TRANSACTION READ ONLY; START TRANSACTION READ ONLY; %s; ROLLBACK" % stmt]
    return _split(_run(cmd, env, timeout), "\t", limit)


def _run(cmd, env, timeout):
    try:
        r = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=timeout + 5)
    except FileNotFoundError:
        raise InvestigateError("%s is not installed." % cmd[0])
    except subprocess.TimeoutExpired:
        raise InvestigateError("query timed out")
    if r.returncode != 0:
        raise InvestigateError((r.stderr or r.stdout).strip()[:800])
    return r.stdout


def _split(out, sep, limit):
    lines = [l for l in out.split("\n") if l != ""]
    if not lines:
        return [], []
    cols = lines[0].split(sep)
    return cols, [l.split(sep) for l in lines[1:limit + 1]]


def mask(cols, rows, keep_pii=False):
    """Redact secret columns; mask emails/phones/names so real people never reach the report."""
    out = []
    for r in rows:
        row = []
        for c, v in zip(cols, r):
            if v is None:
                row.append(None)
            elif SECRET_COL.search(c):
                row.append("[redacted]")
            elif keep_pii:
                row.append(v)
            elif isinstance(v, str):
                s = EMAIL.sub(lambda m: "%s***@%s" % (m.group(1), m.group(2)), v)
                if PERSON_COL.search(c):
                    s = (s[:1] + "***") if s else s
                else:
                    s = PHONE.sub("[phone]", s) if PHONE.fullmatch(s.strip() or "x") else s
                row.append(s[:500])
            elif isinstance(v, bytes):
                row.append("<%d bytes>" % len(v))
            else:
                row.append(v)
        out.append(row)
    return out


def to_json(cols, rows):
    return json.dumps([dict(zip(cols, r)) for r in rows], indent=1, default=str)
