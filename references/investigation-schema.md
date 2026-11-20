# investigation.json schema

Write `<OUT_DIR>/investigation.json`. `render.py` validates it: **errors** (unknown ids, bad
enums, file/line anchors that don't exist) block rendering. **Warnings** (missing edge cases,
bugs without a fix or a reference, an unmapped stack trace) are printed as ACTION REQUIRED.

All `file` values are **repo-relative** paths, and `line` / `end_line` are 1-based lines in the
current working tree. Text fields accept light markdown: `code`, **bold**, [links](https://…),
and `- ` lists.

```jsonc
{
  "title": "Sign-in: POST /users/sign_in",               // required
  "target": {
    "kind": "endpoint",            // endpoint | method | class | file | range | job | event | stacktrace | bug | feature
    "label": "POST /users/sign_in → SessionsController#create",
    "entry": {"file": "app/controllers/sessions_controller.rb", "line": 8, "symbol": "SessionsController#create"}
  },
  "summary": {
    "verdict": "bug_found",         // bug_found | risks_found | works_as_expected | reproduced | not_reproduced | inconclusive
    "headline": "Locked accounts can still sign in with a valid password",   // required, one sentence
    "overview": "2–5 sentences: what the flow does, what you ran, what you found.",
    "stack": "Rails 7.1 · Devise · PostgreSQL",
    "how_it_works": "- markdown list: the flow narrated from input to outcome",
    "insights": [{"title": "Lockout counter never resets", "body": "…"}, "or a plain string"],
    "recommendations": ["Reset failed_attempts on success", "…"]
  },

  // The canonical map of the flow. Nodes are the places where data is read, decided on or changed.
  "flow": {
    "nodes": [
      {"id": "N1", "kind": "entry", "label": "Route + strong params", "file": "app/controllers/sessions_controller.rb",
       "line": 8, "end_line": 12, "symbol": "SessionsController#create", "description": "optional"}
      // kind: entry | middleware | input | parse | validation | auth | branch | transform | lookup | db_read |
      //       db_write | external_call | state_change | side_effect | cache | event | job | callback |
      //       response | output | error | return
    ],
    "edges": [{"from": "N1", "to": "N2", "label": "params ok"}]
  },

  // Every branch point that changes the path. Scenarios record which way each one went.
  "conditions": [
    {"id": "C1", "file": "app/services/auth.rb", "line": 14, "expression": "user.nil?",
     "description": "unknown email → generic error"}
  ],

  // Where inputs and fixtures came from (schema, factories, fixtures, seeds, local DB, contracts, tests).
  "data_sources": [
    {"kind": "schema", "path": "db/schema.rb", "line": 120, "note": "users: email unique, failed_attempts int default 0"},
    {"kind": "factory", "path": "spec/factories/users.rb", "note": ":user, :locked traits"},
    {"kind": "db", "path": "data/users_sample.json", "note": "3 masked rows from the local dev DB"}
    // kind: schema | model | factory | fixture | seed | db | contract | test | context | trace | doc
  ],

  "scenarios": [{
    "id": "SC1",                                   // required, unique
    "title": "Sign in with valid credentials",      // required
    "category": "happy_path",
    // happy_path | validation | auth | permission | not_found | conflict | boundary | edge_case |
    // error_handling | external_failure | concurrency | security | performance | reproduction | regression
    "description": "Why this scenario matters / what it proves",
    "status": "pass",
    // pass (behaves correctly) | expected_error (correctly rejects) | fail (wrong result) |
    // bug (wrong + root cause found) | risk (works but fragile) | reproduced (the reported error happens) | inconclusive
    "evidence": "simulated",                       // simulated (traced by reading code) | executed (run with a tracer)
    "trace": "trace/SC1.json",                     // relative to OUT_DIR; required when evidence = executed
    "harness": "harness/sim_sc1_spec.rb",           // the throwaway file that ran it, if any

    "input": {
      "kind": "http",                              // http | form | graphql | call | job | event | cli | message
      "summary": "Existing confirmed user, correct password",
      "request": {"method": "POST", "path": "/users/sign_in", "headers": {"Accept": "application/json"},
                  "body": {"user": {"email": "ana@example.com", "password": "[valid password]"}}},
      "data": {"optional": "for direct method calls: the arguments"}
    },
    "fixtures": [{"name": "user", "source": "spec/factories/users.rb :user",
                  "data": {"id": 1, "email": "ana@example.com", "failed_attempts": 0, "locked_at": null}}],

    "decisions": [{"condition": "C1", "value": false, "note": "user exists"}],   // value: true | false | "other label"
    "path": ["N1", "N2", "N3", "N5"],                                             // node ids, in order

    // The heart of it: how the data moves and changes, step by step, from input to outcome.
    "steps": [{
      "node": "N2",                                  // optional link to a flow node
      "kind": "transform",                           // same vocabulary as node kinds (minus entry/middleware/callback/output)
      "title": "Email normalised",                   // required
      "file": "app/models/user.rb", "line": 31, "end_line": 31, "symbol": "User#normalize_email",
      "detail": "`before_validation` strips and downcases.",
      "changes": [                                   // every value this step created / changed / removed / read
        {"path": "params.user.email", "op": "update", "before": " Ana@Example.com ", "after": "ana@example.com",
         "note": "optional"}
        // op: set | update | create | delete | append | remove | read
      ],
      "db": {"op": "SELECT", "table": "users", "sql": "SELECT … WHERE email = $1 LIMIT 1",
             "rows": 1, "result": {"id": 1}},        // for db_read / db_write steps
      "external": {"service": "Stripe", "method": "POST", "url": "https://api.stripe.com/v1/charges",
                   "request": {}, "response": {}, "status": 402},           // for external_call steps
      "state": {"entity": "Order#42.status", "from": "pending", "to": "paid"}, // state-machine transitions
      "flag": {"severity": "bug", "text": "failed_attempts is never reset here", "bug": "B1"},  // bug | risk | note
      "trace_events": [41, 42]                       // optional: event numbers in the recorded trace
    }],

    "outcome": {
      "result": "302 → /dashboard, session set",      // required (warning if missing)
      "status_code": 302,
      "response": {"optional": "body / return value"},
      "side_effects": ["users.sign_in_count 4 → 5", "Login email enqueued"],
      "expected": "What the requirement / user says should happen",
      "matches_expected": true
    },
    "error": {"type": "NoMethodError", "message": "undefined method `email' for nil", "file": "…", "line": 17},
    "bugs": ["B1"]
  }],

  "bugs": [{
    "id": "B1", "severity": "major",                // critical | major | minor
    "title": "Locked users can sign in", "file": "app/services/auth.rb", "line": 22, "end_line": 24,
    "description": "What goes wrong and the user-visible impact.",
    "root_cause": "Pin-point the exact line and the data value that makes it wrong.",
    "scenarios": ["SC4"],
    "fix": {"description": "Check locked_at before verifying the password.", "code": "return lock_error if user.locked?", "language": "ruby"},
    "confidence": "high",                          // high | medium | low
    "references": [{"title": "Devise Lockable", "url": "https://…", "note": "unlock strategy"}]   // required for critical/major
  }],

  // When a stack trace / bug report was given.
  "reproduction": {
    "error": {"type": "NoMethodError", "message": "undefined method `email' for nil:NilClass"},
    "scenario": "SC6", "failing_step": 5,          // the scenario + step where it breaks
    "confirmed": true,                             // true only if you actually ran it and saw the error
    "how": "Ran harness/sim_sc6_spec.rb with trace_rb.rb against the test DB.",
    "root_cause": "…",
    "steps_to_reproduce": ["Create a user without a profile", "POST /users/sign_in with …"],
    "frames": [{"n": 1, "path": "app/services/auth.rb", "line": 17, "fn": "Auth#call", "step": "SC6:5"}]
  },

  "coverage": {"note": "What was traced and how confident you are.", "not_covered": ["OAuth sign-in (separate controller)"]},
  "references": [{"title": "…", "url": "https://…", "note": "…"}]
}
```

## Rules

- **Scenarios**: cover every outcome of every condition on the path, at least one per condition
  value that leads somewhere different. Then add edge cases: boundaries, empty / nil / blank, wrong
  types, unicode and whitespace, duplicates, missing related records, permissions, concurrency,
  external failures and timeouts, and replays. A happy path alone draws a warning.
- **Steps** start at the raw input (the request / form / call arguments) and end at the outcome
  (response, return value, persisted state). Record **every** place the data is parsed,
  validated, defaulted, normalised, looked up, mapped, persisted, sent out, or used to decide.
  Each step names the file, line and method where it happens.
- **changes** use stable dotted paths so the state panel can follow a value across steps:
  `params.user.email`, `user.failed_attempts`, `order.status`, `response.body.error`,
  `db.users[1].locked_at`. Use the same path when the same value changes again.
- `evidence: "executed"` only when the scenario really ran under `trace_py.py` / `trace_rb.rb`
  (or the test suite) and `trace` points at its log. Values in executed steps must come from the trace.
- Never put real secrets or personal data in the file. Use `[valid password]`, `ana@example.com`.
  Values that came from a local DB are already masked; keep them masked.
- Severity: **critical** = data loss/corruption, security hole, or crash on a common path.
  **major** = wrong result for a realistic input. **minor** = edge-case or cosmetic.
