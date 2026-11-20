"""AppDiscovery: map the app so the investigator knows where input shapes and test data come from.

Finds: stack/frameworks (stacks.StackDetector), DB schema + migrations + models, test-data
factories / fixtures / seeds, API contracts (OpenAPI, GraphQL, JSON schema, DTOs), routes,
existing tests, the test runner, whether a runtime tracer applies, and the database
configuration (dbconfig.DbConfigReader, secrets redacted). Nothing is executed.
"""
import os
import re
import shutil

from .dbconfig import DbConfigReader
from .stacks import StackDetector

CATEGORIES = [
    # (key, title, regex on repo-relative path)
    ("schema", "DB schema & migrations", r"(^|/)(db/schema\.rb|db/structure\.sql|schema\.prisma|schema\.sql|.*\.dbml"
     r"|(db|database|alembic|prisma|flyway|liquibase|sql)/(migrate|migrations|versions|changelog)/.+"
     r"|migrations/[^/]+\.(py|sql|ts|js|go|php)|drizzle/.+\.sql|db/migration/.+\.sql|.*\.migration\.[jt]s)$"),
    ("models", "Models / entities", r"(^|/)(app/models/.+\.rb|models?\.py|models/.+\.(py|[jt]s|go|php|cs)"
     r"|entities?/.+\.([jt]s|java|kt|cs)|.*Entity\.(java|kt)|.*\.entity\.ts|.*\.model\.[jt]s|app/Models/.+\.php"
     r"|schema/.+\.[jt]s|db/schema/.+\.[jt]s)$"),
    ("factories", "Test-data factories", r"(^|/)(spec/factories/.+|test/factories/.+|factories\.py|factories/.+\.(py|[jt]s|rb|php)"
     r"|database/factories/.+\.php|.*[Ff]actory\.(py|[jt]s|rb|php|java|kt|cs)|.*\.factory\.[jt]s"
     r"|test/support/factor.+|tests?/fixtures?/factor.+)$"),
    ("fixtures", "Fixtures, seeds & sample data", r"(^|/)(test/fixtures/.+\.(yml|yaml|json)|spec/fixtures/.+\.(yml|yaml|json|xml|csv)"
     r"|fixtures?/.+\.(json|ya?ml|csv|sql)|db/seeds(\.rb|/.+)|seeds?/.+\.([jt]s|py|sql|rb|php|json)|seed\.([jt]s|py|sql)"
     r"|database/seeders/.+\.php|testdata/.+|__fixtures__/.+|__mocks__/.+\.json)$"),
    ("contracts", "API contracts & input shapes", r"(^|/)(.*openapi.*\.(ya?ml|json)|.*swagger.*\.(ya?ml|json)|.*\.graphqls?"
     r"|schema\.graphql|.*\.proto|.*\.schema\.json|json-?schemas?/.+\.json|.*\.avsc"
     r"|app/(serializers|blueprints|contracts|validators|forms|inputs|params)/.+\.rb|serializers?\.py|schemas?\.py"
     r"|.*\.dto\.ts|dto/.+\.([jt]s|java|kt|cs)|.*(Request|Dto|DTO|Form|Input|Params)\.(java|kt|cs|php|ts)"
     r"|app/Http/Requests/.+\.php|validators?/.+\.[jt]s|.*\.(zod|yup|joi|schema)\.[jt]s)$"),
    ("routes", "Routes & entry points", r"(^|/)((api|views?|app|main|server|handlers?)\.(py|[jt]s|go)|config/routes\.rb|config/routes/.+\.rb|urls\.py|routes/.+\.(php|[jt]s|py|rb)"
     r"|routes\.(php|[jt]s|py|go)|router\.[jt]s|app/api/.+/route\.[jt]s|pages/api/.+\.[jt]s"
     r"|app/controllers/.+\.rb|.*[Cc]ontroller\.(java|kt|cs|php|[jt]s)|.*\.controller\.ts|views\.py|handlers?/.+\.go"
     r"|.*_handler\.go|app/graphql/(mutations|types)/.+\.rb|resolvers?/.+\.[jt]s|app/(jobs|workers|sidekiq)/.+\.rb"
     r"|tasks\.py|.*\.rake)$"),
    ("tests", "Existing tests for the flow", r"(^|/)(spec/.+_spec\.rb|test/.+_test\.rb|tests?/.+\.py|.+_test\.py|test_.+\.py"
     r"|.+\.(test|spec)\.[jt]sx?|__tests__/.+|.+_test\.go|.+Test\.(java|kt|php|cs)|tests/.+\.php|e2e/.+|cypress/.+|playwright/.+)$"),
    ("dbconfig", "Database & environment config", r"(^|/)(config/database\.yml|\.env(\.[\w.-]+)?|settings(/[\w]+)?\.py"
     r"|docker-compose[\w.-]*\.ya?ml|compose\.ya?ml|knexfile\.[jt]s|ormconfig\.\w+|data-source\.ts|drizzle\.config\.[jt]s"
     r"|application(-\w+)?\.(properties|ya?ml)|appsettings(\.\w+)?\.json|config/database\.php|alembic\.ini|database\.json"
     r"|prisma/\.env|jest\.config\.\w+|vitest\.config\.\w+|pytest\.ini|conftest\.py|\.rspec|spec/(rails|spec)_helper\.rb"
     r"|test/test_helper\.rb|phpunit\.xml(\.dist)?)$"),
]
SKIP = re.compile(r"(^|/)(node_modules|vendor|dist|build|\.venv|venv|tmp|coverage|\.git|\.investigate|\.code-review|\.branch-review|public/assets|log)/")
CONSOLIDATED_SCHEMA = re.compile(r"(schema\.rb|structure\.sql|schema\.prisma|schema\.sql|\.dbml)$")
JS_RUNNERS = (("Vitest", "npx vitest run <file>"), ("Jest", "npx jest <file> --runTestsByPath"), ("Mocha", "npx mocha <file>"))
OTHER_RUNNERS = {
    "go": {"stack": "go", "runner": "go test", "cmd": "go test ./<pkg> -run <TestName> -v", "env": ""},
    "php": {"stack": "php", "runner": "phpunit", "cmd": "vendor/bin/phpunit <file>", "env": "APP_ENV=testing"},
    "jvm": {"stack": "jvm", "runner": "gradle/maven", "cmd": "./gradlew test --tests <Class>  |  mvn -Dtest=<Class> test", "env": ""},
    "dotnet": {"stack": "dotnet", "runner": "dotnet test", "cmd": "dotnet test --filter <Name>", "env": ""},
}


class FileCategorizer(object):
    """Buckets repo files into schema / models / factories / fixtures / contracts / routes / tests / config."""

    def __init__(self, files, limit=30):
        self.files, self.limit = files, limit

    def categorize(self, focus=None):
        words = [w.lower() for w in re.findall(r"[A-Za-z]{3,}", focus or "")]
        out = {}
        for key, title, rx in CATEGORIES:
            r = re.compile(rx)
            matched = [f for f in self.files if not SKIP.search(f) and r.search(f)]
            hits = self.rank(matched, key)[:self.limit]
            if words:      # float files that mention the focus words (e.g. "user", "session") to the top
                hits = sorted(hits, key=lambda p: -sum(w in p.lower() for w in words))
            out[key] = {"title": title, "count": len(matched), "files": hits}
        return out

    @staticmethod
    def rank(paths, key):
        if key == "schema":     # the consolidated schema first, then the newest migrations
            return sorted(p for p in paths if CONSOLIDATED_SCHEMA.search(p)) + \
                sorted((p for p in paths if not CONSOLIDATED_SCHEMA.search(p)), reverse=True)
        return sorted(paths, key=lambda p: (p.count("/"), p))


class TestRunnerFinder(object):
    """Best guess of how to run one test file under the test environment, and which tracer applies."""

    def __init__(self, repo, files, stack, frameworks):
        self.repo, self.files, self.fs = repo, files, set(files)
        self.stack, self.frameworks = stack, frameworks

    def python(self):
        """The project's interpreter: a venv inside the repo when there is one."""
        venv = next((os.path.join(self.repo, c) for c in (".venv/bin/python", "venv/bin/python", "env/bin/python")
                     if os.path.exists(os.path.join(self.repo, c))), None)
        return venv or shutil.which("python3") or "python3"

    def runners(self):
        cmds = []
        if "RSpec" in self.frameworks or any(f.startswith("spec/") and f.endswith("_spec.rb") for f in self.files):
            cmds.append({"stack": "ruby", "runner": "rspec", "cmd": "bundle exec rspec <file>[:line]", "env": "RAILS_ENV=test"})
        if "test/test_helper.rb" in self.fs:
            cmds.append({"stack": "ruby", "runner": "minitest", "cmd": "bin/rails test <file>[:line]", "env": "RAILS_ENV=test"})
        if "python" in self.stack:
            cmds.append(self._python_runner())
        for fw, cmd in JS_RUNNERS:
            if fw in self.frameworks:
                cmds.append({"stack": "javascript", "runner": fw.lower(), "cmd": cmd, "env": "NODE_ENV=test"})
        cmds.extend(OTHER_RUNNERS[s] for s in ("go", "php", "jvm", "dotnet") if s in self.stack)
        return cmds

    def _python_runner(self):
        if "pytest" in self.frameworks or "pytest.ini" in self.fs or any(f.endswith("conftest.py") for f in self.files):
            return {"stack": "python", "runner": "pytest", "cmd": "<python> -m pytest <file>::<test> -x -q", "env": ""}
        if "Django" in self.frameworks:
            return {"stack": "python", "runner": "django", "cmd": "<python> manage.py test <dotted.path>", "env": ""}
        return {"stack": "python", "runner": "script", "cmd": "<python> <file>", "env": ""}

    def dev_runners(self):
        """How to run a harness against the LOCAL development database (always inside a sandbox)."""
        out = []
        if "Rails" in self.frameworks:
            out.append({"stack": "ruby", "cmd": "RAILS_ENV=development bin/rails runner <harness.rb>",
                        "sandbox": "InvestigateTrace.sandbox(name, watch: {...}) { ... }"})
        elif "ruby" in self.stack:
            out.append({"stack": "ruby", "cmd": "bundle exec ruby <harness.rb>", "sandbox": "InvestigateTrace.sandbox(...)"})
        if "Django" in self.frameworks:
            out.append({"stack": "python", "cmd": "DJANGO_SETTINGS_MODULE=%s <python> trace_py.py --root <REPO> --env development --out <OUT_DIR>/trace/<SC>.json -- <harness.py>"
                        % (self._django_settings() or "<project>.settings"),
                        "sandbox": 'investigate_trace.sandbox(name, rollback="django", watch={...})'})
        elif "python" in self.stack:
            out.append({"stack": "python", "cmd": "<python> trace_py.py --root <REPO> --env development --out <OUT_DIR>/trace/<SC>.json -- <harness.py>",
                        "sandbox": "investigate_trace.sandbox(name, rollback=<SQLAlchemy session or DB-API connection>, watch={...})"})
        if any(s.startswith("javascript") for s in self.stack):
            out.append({"stack": "javascript", "cmd": "NODE_ENV=development node <harness.js>",
                        "sandbox": "wrap the scenario in the ORM's transaction and roll it back (prisma.$transaction + throw, knex trx.rollback)"})
        return out

    def _django_settings(self):
        try:
            with open(os.path.join(self.repo, "manage.py"), errors="replace") as fh:
                m = re.search(r"DJANGO_SETTINGS_MODULE['\"],\s*['\"]([\w.]+)['\"]", fh.read())
                return m.group(1) if m else None
        except OSError:
            return None

    def tracers(self):
        """Which runtime tracers apply, and which interpreters to use."""
        out = []
        if "python" in self.stack:
            out.append({"stack": "python", "tracer": "trace_py.py", "interpreter": self.python(),
                        "note": "run with the project's own interpreter (venv) so its packages import"})
        if "ruby" in self.stack:
            out.append({"stack": "ruby", "tracer": "trace_rb.rb", "interpreter": shutil.which("ruby") or "ruby",
                        "note": "load with RUBYOPT=-r<trace_rb.rb> under `bundle exec`; captures SQL and Net::HTTP too"})
        return out


TRACERS_DIR = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))


class AppDiscovery(object):
    def __init__(self, repo, files):
        self.repo, self.files = repo, files

    def run(self, focus=None):
        stack, frameworks = StackDetector(self.repo, self.files).detect()
        runners = TestRunnerFinder(self.repo, self.files, stack, frameworks)
        py, tracer = runners.python(), os.path.join(TRACERS_DIR, "trace_py.py")

        def concrete(cmds):       # copy-pasteable: the project's interpreter and the tracer's real path
            for c in cmds:
                c["cmd"] = c["cmd"].replace("<python> trace_py.py", "%s %s" % (py, tracer)).replace("<python>", py)
            return cmds
        return {"stack": stack, "frameworks": frameworks, "categories": FileCategorizer(self.files).categorize(focus),
                "test_runners": concrete(runners.runners()), "dev_runners": concrete(runners.dev_runners()),
                "tracers": runners.tracers(),
                "database": DbConfigReader(self.repo, self.files).read()}

    @staticmethod
    def digest(d, path):
        L = ["Stack: %s" % (", ".join(d["stack"]) or "unknown"),
             "Frameworks/libraries: %s" % (", ".join(d["frameworks"]) or "none detected")]
        for c in d["categories"].values():
            if not c["count"]:
                L.append("\n%s: none found" % c["title"])
                continue
            more = ", showing %d" % len(c["files"]) if c["count"] > len(c["files"]) else ""
            L.append("\n%s (%d%s):" % (c["title"], c["count"], more))
            L.extend("  " + f for f in c["files"])
        L.append("\nTest runners:")
        L.extend(["  %-9s %s %s" % (t["runner"], t["env"], t["cmd"]) for t in d["test_runners"]] or ["  none detected"])
        if d.get("dev_runners"):
            L.append("\nDevelopment-environment runs (only with --env dev; always inside a rolled-back sandbox):")
            L.extend("  %-10s %s\n             sandbox: %s" % (r["stack"], r["cmd"], r["sandbox"]) for r in d["dev_runners"])
        L.append("\nRuntime tracers available:")
        L.extend(["  %s via %s (%s)" % (t["tracer"], t["interpreter"], t["note"]) for t in d["tracers"]] or
                 ["  none for this stack: simulate statically, or add temporary log probes inside a throwaway harness"])
        db = d["database"]
        L.append("\nDatabase config (credentials redacted):")
        L.extend(["  %s [%s] %s = %s" % (s["file"], s["env"], s["key"], s["value"]) for s in db["settings"]] or ["  none found"])
        if db["sqlite_files"]:
            L.append("  sqlite files: " + ", ".join(db["sqlite_files"]))
        L.append("  clients on PATH: " + ", ".join(k for k, v in db["clients"].items() if v))
        L.append("\nDISCOVERY_JSON=%s" % path)
        return "\n".join(L)
