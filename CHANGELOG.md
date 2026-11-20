# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

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

[1.0.0]: https://github.com/rewin0087/claude-code-investigate/releases/tag/v1.0.0
