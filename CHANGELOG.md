# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [1.1.0] - 2026-11-20

### Added
- **Development-environment runs** (`--env dev`): scenarios run on the local development database
  inside a rolled-back sandbox. Ruby uses `InvestigateTrace.sandbox` (an ActiveRecord transaction,
  with ActionMailer / ActiveJob captured). Python uses `investigate_trace.sandbox` (Django atomic,
  a SQLAlchemy session or a DB-API connection, with Django mail captured). Watched records are
  recorded before → after, and COMMITs that escape the rollback are detected and reported.
- **Reference data** (`--record Order#4512`, `table:id`, `"table where …"`): `records.py` fetches
  the records a bug affected and their parents / children, via declared foreign keys or `*_id`
  columns. It is read-only and masked. Scenarios link the records they replay.
- **Database activity** in every trace digest and report: tables read, and every INSERT / UPDATE /
  DELETE with its line and values.
- **Outbound network guard** in both tracers: connections to anything but loopback are blocked
  and recorded (`--allow-host` / `TRACE_ALLOW_HOSTS`, `--allow-network` / `TRACE_ALLOW_NETWORK`).
- Discovery lists how to run a harness against the development environment.
- Report: environment tags, a Reference data card, Run environment (sandbox) and Database
  activity panels.

### Changed
- ActiveRecord objects in Ruby traces are shown attribute by attribute (`@order.status: pending → refunded`).
- Less trace noise: class bodies, transaction plumbing (BEGIN / SAVEPOINT), duplicate SQL from
  nested cursor wrappers, and exceptions handled inside libraries are hidden.
- Local SQLite databases in WAL mode (the Rails default) open read-only through `PRAGMA query_only`.
- Phone masking no longer treats dates as phone numbers.

## [1.0.0] - 2026-11-20

First public release.

### Added
- The `/investigate` Claude Code skill. It maps a flow from raw input (API/form request or call)
  through validation, branches, business logic, DB CRUD, external calls and state changes to the
  outcome, recording the file:line:method of every data change.
- Targets: endpoints (`"POST /path"`), symbols (`Class#method`), files and line ranges, the editor
  selection, stack traces, and bug reports.
- Scenario generation for every branch outcome plus edge cases, with inputs built from the schema,
  factories, fixtures, seeds, API contracts, or (with `--db`) masked read-only samples from a local DB.
- Runtime tracers. `trace_py.py` uses sys.settrace and captures SQL through DB-API, SQLAlchemy,
  Django and sqlite3, plus HTTP through requests, httpx and urllib. `trace_rb.rb` uses
  TracePoint and captures ActiveRecord SQL with binds and Net::HTTP. Both record per-line changes
  to locals, attributes and model fields, and `trace_digest.py` summarises the result.
- Stack-trace parsing for Python, Ruby, JS/TS, Java/Kotlin, Go, PHP and .NET, with frames mapped
  to repo files.
- An interactive HTML report: scenario list, step-by-step data timeline with a state scrubber,
  flow and condition matrices, a code drawer with executed lines, bugs with fixes and references,
  a reproduction view, keyboard navigation, and dark mode.
- Validation of investigation.json, with an ACTION REQUIRED loop for missing edge cases, fixes,
  references and reproduction mapping.
- Safety rails: no repo changes, execution in the test env only, read-only localhost DB access,
  and redaction of secrets and personal data.
- Code organised as classes, one concern per module, in `scripts/tracekit/` (TargetResolver,
  StackTraceParser, AppDiscovery, SqlGuard, QueryRunner, RowMasker, TraceLog, InvestigationValidator,
  Renderer, …). The command-line scripts are thin wrappers, and the tracers are single-file classes.
- README with workflow and sequence diagrams, and a GitHub Pages landing page (`docs/`) with SEO
  metadata, a sitemap and a social preview image.

[1.0.0]: https://github.com/rewin0087/claude-code-investigate/releases/tag/v1.0.0
[1.1.0]: https://github.com/rewin0087/claude-code-investigate/compare/v1.0.0...v1.1.0
