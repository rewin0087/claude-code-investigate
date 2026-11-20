---
name: investigate
description: Investigate and simulate how data flows through an app, in any tech stack. Given an endpoint, method, class, editor selection, bug report or stack trace, it maps the flow from raw input (API/form request or call) through validation, branches, business logic, database CRUD, external API calls and state changes to the outcome, recording per line / method where the data changes. It generates realistic inputs from the DB schema, factories, fixtures or a local DB, enumerates scenarios (happy path, each failure branch, edge cases), optionally runs them with a runtime tracer in the test environment, reproduces errors, pin-points bugs, and opens an interactive HTML report with a step-by-step data timeline per scenario.
argument-hint: "[target: file[:a-b] | Class#method | \"POST /path\" | text] [--trace <file|text>] [--bug <file|url|text>] [--context <file|url|text>] [--run|--static] [--db [url]] [--scenarios N] [--focus <area>] [--selection|--file] [--no-open] [-- instructions]"
disable-model-invocation: true
allowed-tools: Bash(python3 ${CLAUDE_SKILL_DIR}/scripts/*) Bash(git grep *) Bash(git log *) Bash(git show *) Bash(git blame *) Bash(git ls-files *) Bash(git rev-parse *) Bash(git status *) Bash(git -C *) Bash(ls *) Read Grep Glob WebSearch WebFetch Agent Edit(**/.investigate/**)
---

# Investigate & simulate a flow

Arguments: `$ARGUMENTS`

You are a senior engineer debugging and explaining a system. Follow the data: for each
realistic input, show exactly where it is read, decided on, transformed, stored, sent and
returned, with the value before and after each change. Then find what breaks.

## Arguments

- **target** (bare words, any number):
  - a file, or a file with lines: `app/services/auth.rb`, `auth.rb:10-40`, `auth.rb#L10-L40`, `@auth.rb`
  - a symbol: `AuthService#sign_in`, `Orders::Checkout.call`, `views.login`, `createOrder`
  - a route: `"POST /users/sign_in"`, `/api/orders/:id`
  - a GraphQL mutation, job, event or CLI command name, or free words ("sign in flow")
- **editor context** (VS Code / JetBrains). The IDE shares "The user selected the lines A to B from
  /abs/file" or "The user opened the file /abs/file":
  - `--selection` (or "this", "the selection", "the highlighted method") → target `<file>:A-B`
  - `--file` → target the open file
  - With no explicit target, a **selection** is the target: say so in one line. An open file
    without a selection is not a signal, so ignore it.
- `--trace <file|text>` = a stack trace / error log to reproduce (Python, Ruby, JS/TS, Java/Kotlin,
  Go, PHP, .NET). A trace pasted into the conversation counts too.
- `--bug <file|url|text>`, `--context <file|url|text>` (aliases `--req`, `--spec`, `--steps`): a
  bug report, repro steps, expected behaviour, a spec, screenshots, a ticket URL. Repeatable.
- Everything after a standalone ` -- ` = free-text instructions: how to reproduce, what to
  simulate, which inputs or accounts to use, what "correct" means. Treat attached images and files
  and `@`-mentioned docs the same way.
- `--run` = execute scenarios in the **test** environment with the runtime tracer (Step 5b).
  `--static` = never execute anything. Default (**auto**): simulate statically. Execute only when a
  stack trace or bug must be reproduced **and** a test setup exists, and ask first (Step 5b).
- `--db [url]` = you may read sample rows from the **local** dev/test database, read-only (Step 4).
  Without it, inputs come from the schema, factories, fixtures and seeds only.
- `--scenarios N` = aim for about N scenarios (default: as many as the branches need, usually 6–15).
- `--focus <area>` = e.g. security, validation, payments, concurrency.
- `--no-open` = don't open the browser.

If there's no target, trace, bug or selection, ask the user what to investigate (AskUserQuestion)
and offer the likely entry points you can see (routes, controllers, recently changed files).

## Safety rules (always)

- **Never modify the repo.** Harnesses, scratch scripts, traces and data go in `<OUT_DIR>`
  (`<repo>/.investigate/<slug>/`, kept out of git through `.git/info/exclude`).
- **Execute only in a test environment** (`RAILS_ENV=test`, `NODE_ENV=test`, `APP_ENV=testing`,
  Django test runner / pytest DB fixtures). Never against development, staging or production
  data, and never with production credentials. Prefer the project's test runner: it wraps each
  test in a rolled-back transaction.
- **No real outbound calls.** Stub external APIs with the project's tools (WebMock/VCR, responses,
  nock/MSW, httpmock…). If you can't stub them, simulate that part statically and say so.
- **Database reads** go through `dbquery.py` only (local host, read-only session, LIMIT, masking).
  Never write to a database outside a test transaction.
- **Personal data and secrets** never reach the report. Use synthetic values (`ana@example.com`,
  `[valid password]`) and keep masked values masked.

## Step 1 — Prepare

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/prepare.py --repo <repo or .> --cwd <current dir> \
    [--target <t> ...] [--source <bug/context file|url|"text"> ...] [--slug <short-name>]
```

Pass every `--bug` / `--context` item, the ` -- ` text, and pasted requirement text as `--source`.
Note `REPO=` and `OUT_DIR=`. For each target it prints the candidate definitions or route lines
with a snippet: open the right one and confirm the **entry point**. Then:
- **Read** every file listed under `READ_FILES` (screenshots, PDFs). **WebFetch** every URL under
  `FETCH_URLS`. If a fetch hits a login page, say so in one line and continue.
- Turn the context into a short list of **expectations**: the expected behaviour, repro steps,
  inputs mentioned, and the error seen. Each one becomes a scenario or a check.

**Stack trace** (from `--trace`, or pasted). If it was pasted, first write it to
`<OUT_DIR>/trace.txt` with the Write tool, then:

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/stacktrace.py --dir <OUT_DIR> --source <file or text>
```

It maps the frames to repo lines (innermost first; `*` = our code) and prints snippets.
`START_AT` is the deepest frame in this repo: the last line of our code before the failure.
When there was no target, the trace's app frames and the request in the log are the target.

## Step 2 — Discover the app

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/discover.py --dir <OUT_DIR> --focus "<model / feature words>"
```

It lists the stack, schema and migrations, models, factories, fixtures and seeds, API contracts
(OpenAPI, GraphQL, DTOs, validators, serializers), routes, existing tests, test runners, whether a
runtime tracer applies, and the DB config (credentials redacted). Read what matters for this flow:
the **schema** for the tables it touches (types, NOT NULL, defaults, unique indexes, foreign keys,
enums), the **factories/fixtures** for valid records, and the **contracts/validators** for the input shape.

## Step 3 — Map the flow (static trace)

Starting at the entry point, read the code the way the runtime would execute it, and follow every
call that touches the data. Include the parts frameworks hide:
- routing, middleware, auth filters, `before_action` / guards / interceptors / decorators / dependencies
- param parsing and coercion, strong params / DTO binding, serializers / form objects
- model validations, callbacks / hooks / signals (`before_save`, `pre_save`, `@BeforeInsert`…),
  default values, DB constraints and triggers
- service objects, policies, state machines, transactions, locks, retries
- queries (ORM → SQL: which table, which WHERE, N+1), cache reads and writes
- external API clients, queues, jobs, events and webhooks it emits, emails
- the response / return shape, error handlers and rescue blocks

Use `git grep` / Grep / Glob to find definitions, callbacks and overrides. Use `git log -L` /
`git blame` when *why* matters. For a large flow (more than ~15 files), split it by layer across
parallel Agents. Give each one the entry point, its slice, and the node / condition format, and ask
it for nodes and conditions with exact file:line.

Build:
- **nodes** (`N1…`): each place data is read, decided on or changed, with its kind and file:line.
- **conditions** (`C1…`): every branch that changes the path (`if`, guard clause, validation,
  `case`, rescue, policy check, feature flag, early return), with the exact expression.

## Step 4 — Build realistic inputs

For each scenario, build an input that the real app would accept (or deliberately reject):
- Shape: from the contract / DTO / validator / strong params / form fields (types, required keys,
  nesting, formats, enums, max lengths).
- Records it needs (the user, the order, the plan…): from **factories** (traits), **fixtures** or
  **seeds**. Respect the schema's NOT NULL, unique, FK and enum constraints.
- With `--db`: look at real shapes in the local DB to choose realistic values, read-only:
  ```bash
  python3 ${CLAUDE_SKILL_DIR}/scripts/dbquery.py --url-env DATABASE_URL --sql "SELECT … LIMIT 5" \
      --save <OUT_DIR>/data/<name>.json
  ```
  (or `--url postgres://localhost/app_test`, or `--sqlite db/development.sqlite3`). Take the DB URL
  from the discovery output; ask the user if it isn't there. Prefer the **test** DB. Rows are
  masked; still never copy personal data into scenarios.
- Record where each input came from in `data_sources`.

## Step 5 — Scenarios and simulation

**5a — Enumerate scenarios.** Cover each condition outcome, then the edge cases that apply:

| Area | Examples |
|---|---|
| Happy path | valid input, typical records |
| Validation | each required field missing, wrong type, bad format, too long / too short, blank vs nil |
| Auth / permission | unauthenticated, wrong user, wrong role, expired / locked / unconfirmed account |
| Lookup | record not found, soft-deleted, belongs to another tenant |
| Boundaries | 0, negative, max values, empty lists, exactly-at-limit, time zones, DST, leap days |
| Text | whitespace, case, unicode, emoji, very long strings, injection payloads |
| Conflict / state | duplicates and unique violations, illegal state transitions, stale updates |
| Concurrency | double submit, two workers on the same record, retries / idempotency |
| External | timeout, 4xx / 5xx, malformed response, partial failure mid-transaction |
| Reproduction | exactly the reported input / trace (category `reproduction`) |

Name scenarios the way a person would ("Sign in with invalid password", "Checkout when the card is
declined"). Keep only the ones that exercise a distinct path or a real risk.

**Simulate each scenario** (static): walk the path with the concrete input and write down every
step. Give each one its file:line:method, the decision taken, and each value **before → after**
(`changes`). Add the SQL it would issue (`db`), the external request and response (`external`), and
state transitions (`state`). Keep the same dotted paths for the same value across steps so the
report can show the state at every step. Finish with the outcome (response / return / persisted
state / side effects). Compare it with the expected behaviour from the context, the docs and
common sense. Mark `status` and flag the step where it goes wrong.

**5b — Execute with the runtime tracer** (with `--run`, or to confirm a reproduction in auto mode).
In auto mode, ask the user first (AskUserQuestion), and show the command you will run and that it
uses the test environment. Discovery shows the tracer and the test runner to use.
- Write a throwaway harness in `<OUT_DIR>/harness/` that sets up the scenario's records (factories /
  fixtures) and calls the entry point with the scenario input. It can be a request spec, a test
  client call, or a direct method call. Name harness files `sim_<id>…`, **not** `test_*` / `*.test.*`,
  so normal test runs never pick them up.
- **Python** (use the project's interpreter, e.g. `.venv/bin/python`):
  ```bash
  <python> ${CLAUDE_SKILL_DIR}/scripts/trace_py.py --root <REPO> --out <OUT_DIR>/trace/SC1.json \
      [--entry sign_in] -- -m pytest <OUT_DIR>/harness/sim_sc1.py -q -p no:cacheprovider
  # or: ... -- <OUT_DIR>/harness/sim_sc1.py      (a plain script)
  ```
- **Ruby / Rails**: in the harness, `require "${CLAUDE_SKILL_DIR}/scripts/trace_rb.rb"` (absolute
  path) and wrap only the scenario: `InvestigateTrace.record("SC1") { post "/users/sign_in", params: … }`.
  Run it with `TRACE_ROOT=<REPO> TRACE_DIR=<OUT_DIR>/trace RAILS_ENV=test bundle exec rspec <harness>`.
  To trace an existing spec as-is: `TRACE_AUTOSTART=1 TRACE_OUT=<OUT_DIR>/trace/SC1.json TRACE_ROOT=<REPO>
  RUBYOPT="-r${CLAUDE_SKILL_DIR}/scripts/trace_rb.rb" RAILS_ENV=test bundle exec rspec spec/x_spec.rb:12`.
- **Other stacks** (JS/TS, Go, JVM, PHP, .NET): write the harness as a test in the project's runner
  that logs `JSON.stringify` / `%+v` / `var_export` snapshots of the data after each stage it
  calls (inputs, intermediate values, DB rows read back, responses), and run it in the test env.
  Then mark those scenarios `executed`. If it can't run, stay static.
- Read each log: `python3 ${CLAUDE_SKILL_DIR}/scripts/trace_digest.py <OUT_DIR>/trace/SC1.json`.
  It shows the call tree, every line that changed a variable or attribute (before → after), SQL
  with params, HTTP calls, exceptions, and which lines ran (which branches). Build that scenario's
  steps **from the trace** (`evidence: "executed"`, `trace: "trace/SC1.json"`).
- If a run fails for setup reasons (DB not migrated, missing env), fix it inside the harness. If
  that doesn't work, stop running, keep it static, and say why. Never run migrations or seeds on
  a non-test DB.

## Step 6 — Reproduce (when a stack trace or bug report was given)

Find the input and state that drive execution to the failing line. Start from `START_AT` and walk
the frames outward, asking at each frame which value must hold for the next frame to happen. Turn
that into a `reproduction` scenario. Execute it when allowed (Step 5b) and confirm that the same
error and line appear. Set `reproduction.failing_step` to the step where the bad value is
**created** (the root cause), not only where it explodes. Map each app frame to a step
(`"SC6:5"`). If you can't reproduce it, say what's missing (data, config, timing), give the most
likely cause, and use verdict `not_reproduced` or `inconclusive`.

## Step 7 — Findings

- **Bugs**: wrong results, crashes, data corruption, security holes, race conditions, unhandled
  errors, missing validation, wrong status codes, leaks of internal or personal data. Each bug gives
  the exact file:line, the value that makes it wrong, the scenarios that show it, and a concrete fix
  (code when short). Critical and major bugs need **references** (framework docs, CWE/OWASP, RFCs,
  library issues). Load WebSearch / WebFetch with ToolSearch if they're deferred, and cite only
  pages you opened. Never put repo code or secrets in search queries.
- **Insights**: how the flow really works, surprises (hidden callbacks, implicit coercion, N+1,
  missing transaction, non-idempotent retries), and the overall risk.
- **Coverage**: which branches were traced, which were executed, and what was not covered.

## Step 8 — Write investigation.json and render

Read `${CLAUDE_SKILL_DIR}/references/investigation-schema.md`, write `<OUT_DIR>/investigation.json`,
then:

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/render.py --dir <OUT_DIR> [--no-open]
```

If it prints errors, fix the JSON and run it again. If it prints **ACTION REQUIRED**, address each
warning (add the missing edge cases, fixes, references, or the reproduction mapping) and run again,
at most twice more. If a warning can't be fixed, say why in your reply.

## Step 9 — Reply

Relay the printed SUMMARY in full: the verdict, the scenario table, the bugs, the reproduction,
and the report link (`REPORT_URL`). Add one or two lines on what you would do next (the fix to
apply first, or what data or access would settle an inconclusive result). Mention which scenarios
were executed and which were simulated. Don't apply fixes to the repo unless the user asks.
