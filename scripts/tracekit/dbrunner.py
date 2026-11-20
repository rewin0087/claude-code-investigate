"""Run one guarded read query in a read-only session: SqliteRunner, PostgresRunner, MysqlRunner."""
import os
import sqlite3
import subprocess

from .dbsafe import SqlGuard
from .errors import InvestigateError


class QueryRunner(object):
    def __init__(self, target, timeout=20):
        self.target, self.timeout = target, timeout

    @staticmethod
    def for_target(target, timeout=20):
        return {"sqlite": SqliteRunner, "postgres": PostgresRunner, "mysql": MysqlRunner}[target.kind](target, timeout)

    def query(self, sql, limit=20):
        """-> (columns, rows), at most `limit` rows."""
        return self.execute(SqlGuard.prepare(sql, limit), limit)

    def execute(self, stmt, limit):
        raise NotImplementedError

    def _cli(self, cmd, env):
        try:
            r = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=self.timeout + 5)
        except FileNotFoundError:
            raise InvestigateError("%s is not installed." % cmd[0])
        except subprocess.TimeoutExpired:
            raise InvestigateError("query timed out")
        if r.returncode != 0:
            raise InvestigateError((r.stderr or r.stdout).strip()[:800])
        return r.stdout

    @staticmethod
    def _split(out, sep, limit):
        lines = [l for l in out.split("\n") if l != ""]
        if not lines:
            return [], []
        return lines[0].split(sep), [l.split(sep) for l in lines[1:limit + 1]]


class SqliteRunner(QueryRunner):
    def execute(self, stmt, limit):
        path = os.path.abspath(self.target.path)
        if not os.path.isfile(path):
            raise InvestigateError("sqlite file not found: %s" % path)
        con = sqlite3.connect("file:%s?mode=ro" % path, uri=True, timeout=self.timeout)     # read-only open
        try:
            cur = con.execute(stmt)
            return [c[0] for c in cur.description or []], [list(r) for r in cur.fetchmany(limit)]
        finally:
            con.close()


class PostgresRunner(QueryRunner):
    def execute(self, stmt, limit):
        t = self.target
        env = dict(os.environ, PGOPTIONS="-c default_transaction_read_only=on -c statement_timeout=%d" % (self.timeout * 1000),
                   PGCONNECT_TIMEOUT="5")
        if t.password:
            env["PGPASSWORD"] = t.password
        cmd = ["psql", "-X", "-q", "-A", "-F", "\x1f", "-P", "footer=off", "-v", "ON_ERROR_STOP=1", "-h", t.host, "-d", t.db]
        if t.port:
            cmd += ["-p", str(t.port)]
        if t.user:
            cmd += ["-U", t.user]
        return self._split(self._cli(cmd + ["-c", stmt], env), "\x1f", limit)


class MysqlRunner(QueryRunner):
    def execute(self, stmt, limit):
        t = self.target
        env = dict(os.environ)
        if t.password:
            env["MYSQL_PWD"] = t.password
        cmd = ["mysql", "--batch", "--raw", "-h", t.host if t.host != "localhost" else "127.0.0.1"]
        if t.port:
            cmd += ["-P", str(t.port)]
        if t.user:
            cmd += ["-u", t.user]
        cmd += [t.db, "-e", "SET SESSION TRANSACTION READ ONLY; START TRANSACTION READ ONLY; %s; ROLLBACK" % stmt]
        return self._split(self._cli(cmd, env), "\t", limit)
