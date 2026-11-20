"""Reference data: the existing records a bug affected, and the records around them.

RecordRef          parses what the user names: "Order#4512", "orders:4512", "users where email = 'a@b.c'"
SchemaIntrospector columns + foreign keys of a table (sqlite / postgres / mysql), read-only
RecordGraph        fetches the record(s), then follows foreign keys (or Rails-style *_id columns)
                   to parents and children, so the investigation can replay the bug on that data

Every query goes through QueryRunner -> SqlGuard (single read statement, LIMIT, read-only
session, local hosts only). Rows are masked by RowMasker before they leave this module.
"""
import re

from .errors import InvestigateError
from .masking import RowMasker

IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)?$")


def pluralize(word):
    w = word.lower()
    if re.search(r"[^aeiou]y$", w):
        return w[:-1] + "ies"
    if re.search(r"(s|x|z|ch|sh)$", w):
        return w + "es"
    return w + "s"


def snake(name):
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name.split("::")[-1]).lower()


def quote(value):
    """A SQL literal for an id / value the user typed."""
    if re.match(r"^-?\d+$", str(value)):
        return str(value)
    return "'%s'" % str(value).replace("'", "''")


class RecordRef(object):
    """One record (or set of records) the user pointed at."""

    def __init__(self, table, where, label):
        if not IDENT.match(table):
            raise InvestigateError("Not a table name: %r" % table)
        self.table, self.where, self.label = table, where, label

    @classmethod
    def parse(cls, spec):
        s = spec.strip()
        m = re.match(r"^([A-Z][\w:]*)#(\S+)$", s)                     # Order#4512, Billing::Invoice#7
        if m:
            return cls(pluralize(snake(m.group(1))), "id = %s" % quote(m.group(2)), s)
        m = re.match(r"^([A-Za-z_][\w.]*)[:#](\S+)$", s)                # orders:4512
        if m:
            return cls(m.group(1), "id = %s" % quote(m.group(2)), s)
        m = re.match(r"^([A-Za-z_][\w.]*)\s+where\s+(.+)$", s, re.I)    # users where email = 'x'
        if m:
            return cls(m.group(1), m.group(2), s)
        raise InvestigateError("Can't read record reference %r. Use Model#id, table:id or \"table where <condition>\"." % spec)

    def sql(self):
        return "SELECT * FROM %s WHERE %s" % (self.table, self.where)


class SchemaIntrospector(object):
    """Columns and foreign keys, through the same guarded read-only runner."""

    def __init__(self, runner):
        self.runner = runner
        self.kind = runner.target.kind
        self._cache = {}

    def columns(self, table):
        if self.kind != "sqlite":
            return self._all_columns().get(table.split(".")[-1], [])
        if ("cols", table) not in self._cache:
            cols, rows = self.runner.query("PRAGMA table_info(%s)" % table, 500)
            self._cache[("cols", table)] = [r[cols.index("name")] for r in rows]
        return self._cache[("cols", table)]

    def _all_columns(self):
        """postgres / mysql: every table's columns in one query (each query is a CLI round trip)."""
        if "all_cols" not in self._cache:
            schema = "current_schema()" if self.kind == "postgres" else "database()"
            rows = self.runner.query("SELECT table_name, column_name FROM information_schema.columns "
                                     "WHERE table_schema = %s ORDER BY table_name, ordinal_position" % schema, 20000)[1]
            out = {}
            for t, c in rows:
                out.setdefault(t, []).append(c)
            self._cache["all_cols"] = out
        return self._cache["all_cols"]

    def tables(self):
        if "tables" not in self._cache:
            if self.kind == "sqlite":
                sql = "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            elif self.kind == "postgres":
                sql = "SELECT table_name FROM information_schema.tables WHERE table_schema = current_schema()"
            else:
                sql = "SELECT table_name FROM information_schema.tables WHERE table_schema = database()"
            self._cache["tables"] = [r[0] for r in self.runner.query(sql, 2000)[1]]
        return self._cache["tables"]

    def foreign_keys(self):
        """[(table, column, ref_table, ref_column)] declared in the database."""
        if "fks" in self._cache:
            return self._cache["fks"]
        fks = []
        if self.kind == "sqlite":
            for t in self.tables():
                cols, rows = self.runner.query("PRAGMA foreign_key_list(%s)" % t, 200)
                for r in rows:
                    d = dict(zip(cols, r))
                    fks.append((t, d["from"], d["table"], d.get("to") or "id"))
        else:
            schema = "current_schema()" if self.kind == "postgres" else "database()"
            sql = ("SELECT kcu.table_name, kcu.column_name, ccu.table_name, ccu.column_name "
                   "FROM information_schema.key_column_usage kcu JOIN information_schema.constraint_column_usage ccu "
                   "ON kcu.constraint_name = ccu.constraint_name WHERE kcu.table_schema = %s AND kcu.table_name <> ccu.table_name") % schema \
                if self.kind == "postgres" else \
                ("SELECT table_name, column_name, referenced_table_name, referenced_column_name FROM information_schema.key_column_usage "
                 "WHERE table_schema = %s AND referenced_table_name IS NOT NULL") % schema
            fks = [tuple(r) for r in self.runner.query(sql, 2000)[1]]
        self._cache["fks"] = fks
        return fks

    def links(self):
        """Declared foreign keys plus Rails-style conventions (`user_id` -> users.id) when undeclared."""
        links = set(self.foreign_keys())
        tables = set(self.tables())
        for t in tables:
            for c in self.columns(t):
                m = re.match(r"^(\w+)_id$", c)
                if m and not any(l[0] == t and l[1] == c for l in links):
                    for cand in (pluralize(m.group(1)), m.group(1)):
                        if cand in tables:
                            links.add((t, c, cand, "id"))
                            break
        return sorted(links)


class RecordGraph(object):
    """The referenced rows, their parents (what they point at) and children (what points at them)."""

    def __init__(self, runner, masker=None, children_limit=5, depth=1):
        self.runner, self.schema = runner, SchemaIntrospector(runner)
        self.masker = masker or RowMasker()
        self.children_limit, self.depth = children_limit, depth

    def fetch(self, ref, limit=5):
        root = self._rows(ref.sql(), limit)
        if not root:
            raise InvestigateError("No %s row matches %s." % (ref.table, ref.where))
        links = self.schema.links()
        node = {"ref": ref.label, "table": ref.table, "where": ref.where, "rows": root}
        self._expand(node, links, self.depth)
        return node

    def _expand(self, node, links, depth):
        if depth <= 0:
            return
        table, rows = node["table"], node["rows"]
        node["parents"], node["children"] = [], []
        for (t, col, ref_table, ref_col) in links:
            if t == table:                                   # this row points at a parent
                ids = sorted({r.get(col) for r in rows if r.get(col) is not None}, key=str)
                if ids:
                    sql = "SELECT * FROM %s WHERE %s IN (%s)" % (ref_table, ref_col, ", ".join(quote(i) for i in ids))
                    node["parents"].append({"via": "%s.%s -> %s.%s" % (t, col, ref_table, ref_col), "table": ref_table,
                                            "rows": self._rows(sql, len(ids))})
            if ref_table == table:                           # children point at this row
                ids = sorted({r.get(ref_col) for r in rows if r.get(ref_col) is not None}, key=str)
                if ids:
                    sql = "SELECT * FROM %s WHERE %s IN (%s)" % (t, col, ", ".join(quote(i) for i in ids))
                    child = {"via": "%s.%s -> %s.%s" % (t, col, ref_table, ref_col), "table": t,
                             "rows": self._rows(sql, self.children_limit)}
                    if child["rows"]:
                        node["children"].append(child)
                        self._expand(child, links, depth - 1)

    def _rows(self, sql, limit):
        cols, rows = self.runner.query(sql, limit)
        return [dict(zip(cols, r)) for r in self.masker.mask(cols, rows)]

    @staticmethod
    def digest(node, indent=0):
        pad = "  " * indent
        L = ["%s%s (%d row%s)%s" % (pad, node.get("ref") or node["table"], len(node["rows"]), "" if len(node["rows"]) == 1 else "s",
                                    ("  via " + node["via"]) if node.get("via") else "")]
        for r in node["rows"][:5]:
            L.append(pad + "  " + ", ".join("%s=%s" % (k, _short(v)) for k, v in r.items()))
        for p in node.get("parents", []):
            L.append("%s  ↑ parent %s via %s: %s" % (pad, p["table"], p["via"],
                                                      "; ".join(", ".join("%s=%s" % (k, _short(v)) for k, v in r.items()) for r in p["rows"][:3])))
        for c in node.get("children", []):
            L.append("%s  ↓ children:" % pad)
            L.extend(RecordGraph.digest(c, indent + 2).split("\n"))
        return "\n".join(L)


def _short(v, n=40):
    s = "NULL" if v is None else str(v)
    return s if len(s) <= n else s[:n - 1] + "…"
