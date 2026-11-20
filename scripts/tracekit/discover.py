"""Map the app so the investigator knows where input shapes and test data come from.

Finds: stack/frameworks, DB schema + migrations + models, test-data factories / fixtures /
seeds, API contracts (OpenAPI, GraphQL, JSON schema), routes, the test runner, whether the
runtime tracer can be used, and the database configuration (secrets redacted). Everything is
derived from file names and small config reads; nothing is executed.
"""
import json
import os
import re
import shutil

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
SKIP = re.compile(r"(^|/)(node_modules|vendor|dist|build|\.venv|venv|tmp|coverage|\.git|\.investigate|\.code-review|public/assets|log)/")
SECRET_KEY = re.compile(r"(pass(word)?|secret|token|key|credential|auth|private|dsn|url)", re.I)
URL_CRED = re.compile(r"(\w+://[^:/@\s]+:)([^@\s]+)(@)")


def detect_stack(repo, files):
    fs = set(files)
    stack, frameworks = [], []

    def has(name):
        return name in fs

    def read(name, limit=200000):
        try:
            with open(os.path.join(repo, name), errors="replace") as fh:
                return fh.read(limit)
        except OSError:
            return ""
    if has("Gemfile"):
        stack.append("ruby")
        g = read("Gemfile")
        for gem, fw in (("rails", "Rails"), ("sinatra", "Sinatra"), ("hanami", "Hanami"), ("grape", "Grape"),
                        ("rspec", "RSpec"), ("factory_bot", "FactoryBot"), ("sidekiq", "Sidekiq"),
                        ("devise", "Devise"), ("graphql", "GraphQL-Ruby"), ("webmock", "WebMock"), ("vcr", "VCR"),
                        ("dry-validation", "dry-validation"), ("pundit", "Pundit"), ("cancancan", "CanCanCan"),
                        ("aasm", "AASM"), ("statesman", "Statesman")):
            if re.search(r"""gem\s+['"]%s(?:-rails)?['"]""" % re.escape(gem), g):
                frameworks.append(fw)
    py = [f for f in ("pyproject.toml", "requirements.txt", "setup.py", "Pipfile", "setup.cfg") if has(f)]
    if py or any(f.endswith(".py") for f in files[:5000]):
        if py or has("manage.py"):
            stack.append("python")
        txt = " ".join(read(f).lower() for f in py)
        for dep, fw in (("django", "Django"), ("flask", "Flask"), ("fastapi", "FastAPI"), ("sqlalchemy", "SQLAlchemy"),
                        ("pydantic", "Pydantic"), ("celery", "Celery"), ("pytest", "pytest"),
                        ("factory-boy", "factory_boy"), ("factory_boy", "factory_boy"), ("responses", "responses"),
                        ("djangorestframework", "DRF"), ("alembic", "Alembic"), ("marshmallow", "marshmallow")):
            if dep in txt and fw not in frameworks:
                frameworks.append(fw)
        if has("manage.py") and "Django" not in frameworks:
            frameworks.append("Django")
    if has("package.json"):
        stack.append("javascript" + ("/typescript" if any(f.endswith((".ts", ".tsx")) for f in files[:20000]) else ""))
        try:
            pkg = json.loads(read("package.json"))
        except ValueError:
            pkg = {}
        deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
        for dep, fw in (("next", "Next.js"), ("express", "Express"), ("@nestjs/core", "NestJS"), ("fastify", "Fastify"),
                        ("koa", "Koa"), ("@remix-run/node", "Remix"), ("react", "React"), ("vue", "Vue"),
                        ("@angular/core", "Angular"), ("svelte", "Svelte"), ("prisma", "Prisma"),
                        ("@prisma/client", "Prisma"), ("typeorm", "TypeORM"), ("sequelize", "Sequelize"),
                        ("mongoose", "Mongoose"), ("knex", "Knex"), ("drizzle-orm", "Drizzle"), ("zod", "Zod"),
                        ("joi", "Joi"), ("yup", "Yup"), ("jest", "Jest"), ("vitest", "Vitest"), ("mocha", "Mocha"),
                        ("nock", "nock"), ("msw", "MSW"), ("@faker-js/faker", "Faker"), ("fishery", "Fishery"),
                        ("cypress", "Cypress"), ("@playwright/test", "Playwright"), ("graphql", "GraphQL"),
                        ("bullmq", "BullMQ"), ("xstate", "XState")):
            if dep in deps and fw not in frameworks:
                frameworks.append(fw)
    if has("go.mod"):
        stack.append("go")
        g = read("go.mod")
        for dep, fw in (("gin-gonic", "Gin"), ("labstack/echo", "Echo"), ("go-chi", "chi"), ("gorm.io", "GORM"),
                        ("sqlc", "sqlc"), ("gofiber", "Fiber")):
            if dep in g:
                frameworks.append(fw)
    if has("pom.xml") or any(f.endswith(("build.gradle", "build.gradle.kts")) for f in fs):
        stack.append("jvm")
        txt = read("pom.xml") + read("build.gradle") + read("build.gradle.kts")
        for dep, fw in (("spring-boot", "Spring Boot"), ("hibernate", "Hibernate"), ("flyway", "Flyway"),
                        ("liquibase", "Liquibase"), ("ktor", "Ktor"), ("micronaut", "Micronaut"), ("quarkus", "Quarkus")):
            if dep in txt:
                frameworks.append(fw)
    if has("composer.json"):
        stack.append("php")
        if has("artisan"):
            frameworks.append("Laravel")
        if has("bin/console") or has("symfony.lock"):
            frameworks.append("Symfony")
    if any(f.endswith((".csproj", ".sln")) for f in fs):
        stack.append("dotnet")
        if any("Migrations/" in f for f in files):
            frameworks.append("EF Core")
    if has("mix.exs"):
        stack.append("elixir")
        if "phoenix" in read("mix.exs"):
            frameworks.append("Phoenix")
    if has("Cargo.toml"):
        stack.append("rust")
    return stack, frameworks


def categorize(files, limit=30):
    out = {}
    for key, title, rx in CATEGORIES:
        r = re.compile(rx)
        hits = [f for f in files if not SKIP.search(f) and r.search(f)]
        out[key] = {"title": title, "count": len(hits), "files": _rank(hits, key)[:limit]}
    return out


def _rank(paths, key):
    if key == "schema":     # the consolidated schema first, then the newest migrations
        main = re.compile(r"(schema\.rb|structure\.sql|schema\.prisma|schema\.sql|\.dbml)$")
        return sorted(p for p in paths if main.search(p)) + sorted((p for p in paths if not main.search(p)), reverse=True)
    return sorted(paths, key=lambda p: (p.count("/"), p))


def test_runner(repo, files, stack, frameworks):
    """Best guess of how to run one test file under the test environment."""
    fs = set(files)
    cmds = []
    if "RSpec" in frameworks or any(f.startswith("spec/") and f.endswith("_spec.rb") for f in files):
        cmds.append({"stack": "ruby", "runner": "rspec", "cmd": "bundle exec rspec <file>[:line]", "env": "RAILS_ENV=test"})
    if "test/test_helper.rb" in fs:
        cmds.append({"stack": "ruby", "runner": "minitest", "cmd": "bin/rails test <file>[:line]", "env": "RAILS_ENV=test"})
    if "python" in stack:
        if "pytest" in frameworks or "pytest.ini" in fs or "conftest.py" in fs or any(f.endswith("conftest.py") for f in files):
            cmds.append({"stack": "python", "runner": "pytest", "cmd": "<python> -m pytest <file>::<test> -x -q", "env": ""})
        elif "Django" in frameworks:
            cmds.append({"stack": "python", "runner": "django", "cmd": "<python> manage.py test <dotted.path>", "env": ""})
        else:
            cmds.append({"stack": "python", "runner": "script", "cmd": "<python> <file>", "env": ""})
    for fw, cmd in (("Vitest", "npx vitest run <file>"), ("Jest", "npx jest <file> --runTestsByPath"),
                    ("Mocha", "npx mocha <file>")):
        if fw in frameworks:
            cmds.append({"stack": "javascript", "runner": fw.lower(), "cmd": cmd, "env": "NODE_ENV=test"})
    if "go" in stack:
        cmds.append({"stack": "go", "runner": "go test", "cmd": "go test ./<pkg> -run <TestName> -v", "env": ""})
    if "php" in stack:
        cmds.append({"stack": "php", "runner": "phpunit", "cmd": "vendor/bin/phpunit <file>", "env": "APP_ENV=testing"})
    if "jvm" in stack:
        cmds.append({"stack": "jvm", "runner": "gradle/maven", "cmd": "./gradlew test --tests <Class>  |  mvn -Dtest=<Class> test", "env": ""})
    if "dotnet" in stack:
        cmds.append({"stack": "dotnet", "runner": "dotnet test", "cmd": "dotnet test --filter <Name>", "env": ""})
    return cmds


def tracer_support(stack, repo):
    """Which runtime tracers apply, and which interpreters are on PATH."""
    out = []
    if "python" in stack:
        py = None
        for cand in (".venv/bin/python", "venv/bin/python", "env/bin/python"):
            if os.path.exists(os.path.join(repo, cand)):
                py = os.path.join(repo, cand)
                break
        out.append({"stack": "python", "tracer": "trace_py.py", "interpreter": py or shutil.which("python3") or "python3",
                    "note": "run with the project's own interpreter (venv) so its packages import"})
    if "ruby" in stack:
        out.append({"stack": "ruby", "tracer": "trace_rb.rb", "interpreter": shutil.which("ruby") or "ruby",
                    "note": "load with RUBYOPT=-r<trace_rb.rb> under `bundle exec`; captures SQL and Net::HTTP too"})
    return out


def db_config(repo, files):
    """Database settings for the dev/test environments, with credentials redacted."""
    found = []
    fs = set(files)
    if "config/database.yml" in fs:
        txt = _read(repo, "config/database.yml")
        section = None
        for line in txt.split("\n"):
            m = re.match(r"^(\w+):\s*(?:&\w+)?\s*$", line)
            if m:
                section = m.group(1)
                continue
            m = re.match(r"^\s+(adapter|database|host|port|url|username)\s*:\s*(.+)$", line)
            if m and section:
                found.append({"file": "config/database.yml", "env": section, "key": m.group(1), "value": _redact(m.group(1), m.group(2))})
    for f in files:
        base = os.path.basename(f)
        if re.match(r"^\.env(\.[\w.-]+)?$", base) and not SKIP.search(f):
            for line in _read(repo, f).split("\n"):
                m = re.match(r"^\s*(?:export\s+)?([A-Z0-9_]*(?:DATABASE|DB_|POSTGRES|MYSQL|MONGO|REDIS|SQLITE)[A-Z0-9_]*)\s*=\s*(.*)$", line)
                if m:
                    found.append({"file": f, "env": base, "key": m.group(1), "value": _redact(m.group(1), m.group(2).strip().strip("'\""))})
        elif re.match(r"^(docker-)?compose[\w.-]*\.ya?ml$", base):
            for m in re.finditer(r"image:\s*['\"]?((?:postgres|postgis|mysql|mariadb|mongo|redis|mcr\.microsoft\.com/mssql)[^\s'\"]*)", _read(repo, f)):
                found.append({"file": f, "env": "docker", "key": "image", "value": m.group(1)})
    clients = {c: bool(shutil.which(c)) for c in ("sqlite3", "psql", "mysql", "mongosh", "redis-cli")}
    sqlite = [f for f in files if re.search(r"\.(sqlite3?|db)$", f) and not SKIP.search(f)]
    return {"settings": found[:60], "clients": clients, "sqlite_files": sqlite[:10]}


def _redact(key, value):
    v = URL_CRED.sub(r"\1***\3", value)
    if SECRET_KEY.search(key) and not re.search(r"url|database|host|port|adapter|username", key, re.I) and v and "<%" not in v:
        return "***"
    return v[:200]


def _read(repo, rel, limit=200000):
    try:
        with open(os.path.join(repo, rel), errors="replace") as fh:
            return fh.read(limit)
    except OSError:
        return ""


def discover(repo, files, focus=None):
    stack, frameworks = detect_stack(repo, files)
    cats = categorize(files)
    if focus:      # float files that mention the focus words (e.g. "user", "session") to the top
        words = [w.lower() for w in re.findall(r"[A-Za-z]{3,}", focus)]
        for c in cats.values():
            c["files"] = sorted(c["files"], key=lambda p: -sum(w in p.lower() for w in words))
    return {"stack": stack, "frameworks": frameworks, "categories": cats,
            "test_runners": test_runner(repo, files, stack, frameworks),
            "tracers": tracer_support(stack, repo), "database": db_config(repo, files)}


def format_digest(d, path):
    L = ["Stack: %s" % (", ".join(d["stack"]) or "unknown"),
         "Frameworks/libraries: %s" % (", ".join(d["frameworks"]) or "none detected")]
    for key, c in d["categories"].items():
        if not c["count"]:
            L.append("\n%s: none found" % c["title"])
            continue
        L.append("\n%s (%d%s):" % (c["title"], c["count"], ", showing %d" % len(c["files"]) if c["count"] > len(c["files"]) else ""))
        L.extend("  " + f for f in c["files"])
    L.append("\nTest runners:")
    L.extend("  %-9s %s %s" % (t["runner"], t["env"], t["cmd"]) for t in d["test_runners"]) if d["test_runners"] else L.append("  none detected")
    L.append("\nRuntime tracers available:")
    L.extend("  %s via %s (%s)" % (t["tracer"], t["interpreter"], t["note"]) for t in d["tracers"]) if d["tracers"] else \
        L.append("  none for this stack: simulate statically, or add temporary log probes inside a throwaway harness")
    db = d["database"]
    L.append("\nDatabase config (credentials redacted):")
    L.extend("  %s [%s] %s = %s" % (s["file"], s["env"], s["key"], s["value"]) for s in db["settings"]) if db["settings"] else L.append("  none found")
    if db["sqlite_files"]:
        L.append("  sqlite files: " + ", ".join(db["sqlite_files"]))
    L.append("  clients on PATH: " + ", ".join(k for k, v in db["clients"].items() if v))
    L.append("\nDISCOVERY_JSON=%s" % path)
    return "\n".join(L)
