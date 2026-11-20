"""StackDetector: languages and frameworks of a repo, from its manifests (Gemfile, package.json, ...)."""
import json
import os
import re

RUBY_GEMS = (("rails", "Rails"), ("sinatra", "Sinatra"), ("hanami", "Hanami"), ("grape", "Grape"), ("rspec", "RSpec"),
             ("factory_bot", "FactoryBot"), ("sidekiq", "Sidekiq"), ("devise", "Devise"), ("graphql", "GraphQL-Ruby"),
             ("webmock", "WebMock"), ("vcr", "VCR"), ("dry-validation", "dry-validation"), ("pundit", "Pundit"),
             ("cancancan", "CanCanCan"), ("aasm", "AASM"), ("statesman", "Statesman"))
PYTHON_MANIFESTS = ("pyproject.toml", "requirements.txt", "setup.py", "Pipfile", "setup.cfg")
PYTHON_DEPS = (("django", "Django"), ("flask", "Flask"), ("fastapi", "FastAPI"), ("sqlalchemy", "SQLAlchemy"),
               ("pydantic", "Pydantic"), ("celery", "Celery"), ("pytest", "pytest"), ("factory-boy", "factory_boy"),
               ("factory_boy", "factory_boy"), ("responses", "responses"), ("djangorestframework", "DRF"),
               ("alembic", "Alembic"), ("marshmallow", "marshmallow"))
NODE_DEPS = (("next", "Next.js"), ("express", "Express"), ("@nestjs/core", "NestJS"), ("fastify", "Fastify"), ("koa", "Koa"),
             ("@remix-run/node", "Remix"), ("react", "React"), ("vue", "Vue"), ("@angular/core", "Angular"),
             ("svelte", "Svelte"), ("prisma", "Prisma"), ("@prisma/client", "Prisma"), ("typeorm", "TypeORM"),
             ("sequelize", "Sequelize"), ("mongoose", "Mongoose"), ("knex", "Knex"), ("drizzle-orm", "Drizzle"),
             ("zod", "Zod"), ("joi", "Joi"), ("yup", "Yup"), ("jest", "Jest"), ("vitest", "Vitest"), ("mocha", "Mocha"),
             ("nock", "nock"), ("msw", "MSW"), ("@faker-js/faker", "Faker"), ("fishery", "Fishery"),
             ("cypress", "Cypress"), ("@playwright/test", "Playwright"), ("graphql", "GraphQL"), ("bullmq", "BullMQ"),
             ("xstate", "XState"))
GO_DEPS = (("gin-gonic", "Gin"), ("labstack/echo", "Echo"), ("go-chi", "chi"), ("gorm.io", "GORM"), ("sqlc", "sqlc"),
           ("gofiber", "Fiber"))
JVM_DEPS = (("spring-boot", "Spring Boot"), ("hibernate", "Hibernate"), ("flyway", "Flyway"), ("liquibase", "Liquibase"),
            ("ktor", "Ktor"), ("micronaut", "Micronaut"), ("quarkus", "Quarkus"))


class StackDetector(object):
    def __init__(self, repo, files):
        self.repo, self.files, self.fs = repo, files, set(files)
        self.stack, self.frameworks = [], []

    def detect(self):
        """-> (stack, frameworks), e.g. (["ruby"], ["Rails", "RSpec"])."""
        for ecosystem in (self._ruby, self._python, self._node, self._go, self._jvm, self._php, self._dotnet,
                          self._elixir, self._rust):
            ecosystem()
        return self.stack, self.frameworks

    # ---- helpers ---------------------------------------------------------------------------
    def has(self, name):
        return name in self.fs

    def read(self, name, limit=200000):
        try:
            with open(os.path.join(self.repo, name), errors="replace") as fh:
                return fh.read(limit)
        except OSError:
            return ""

    def add(self, framework):
        if framework not in self.frameworks:
            self.frameworks.append(framework)

    def add_matching(self, table, text):
        for dep, fw in table:
            if dep in text:
                self.add(fw)

    # ---- ecosystems ------------------------------------------------------------------------
    def _ruby(self):
        if not self.has("Gemfile"):
            return
        self.stack.append("ruby")
        gemfile = self.read("Gemfile")
        for gem, fw in RUBY_GEMS:
            if re.search(r"""gem\s+['"]%s(?:-rails)?['"]""" % re.escape(gem), gemfile):
                self.add(fw)

    def _python(self):
        manifests = [f for f in PYTHON_MANIFESTS if self.has(f)]
        if not (manifests or any(f.endswith(".py") for f in self.files[:5000])):
            return
        if manifests or self.has("manage.py"):
            self.stack.append("python")
        self.add_matching(PYTHON_DEPS, " ".join(self.read(f).lower() for f in manifests))
        if self.has("manage.py"):
            self.add("Django")

    def _node(self):
        if not self.has("package.json"):
            return
        ts = any(f.endswith((".ts", ".tsx")) for f in self.files[:20000])
        self.stack.append("javascript" + ("/typescript" if ts else ""))
        try:
            pkg = json.loads(self.read("package.json"))
        except ValueError:
            pkg = {}
        deps = dict(pkg.get("dependencies", {}), **pkg.get("devDependencies", {}))
        for dep, fw in NODE_DEPS:
            if dep in deps:
                self.add(fw)

    def _go(self):
        if self.has("go.mod"):
            self.stack.append("go")
            self.add_matching(GO_DEPS, self.read("go.mod"))

    def _jvm(self):
        if self.has("pom.xml") or any(f.endswith(("build.gradle", "build.gradle.kts")) for f in self.fs):
            self.stack.append("jvm")
            self.add_matching(JVM_DEPS, self.read("pom.xml") + self.read("build.gradle") + self.read("build.gradle.kts"))

    def _php(self):
        if self.has("composer.json"):
            self.stack.append("php")
            if self.has("artisan"):
                self.add("Laravel")
            if self.has("bin/console") or self.has("symfony.lock"):
                self.add("Symfony")

    def _dotnet(self):
        if any(f.endswith((".csproj", ".sln")) for f in self.fs):
            self.stack.append("dotnet")
            if any("Migrations/" in f for f in self.files):
                self.add("EF Core")

    def _elixir(self):
        if self.has("mix.exs"):
            self.stack.append("elixir")
            if "phoenix" in self.read("mix.exs"):
                self.add("Phoenix")

    def _rust(self):
        if self.has("Cargo.toml"):
            self.stack.append("rust")
