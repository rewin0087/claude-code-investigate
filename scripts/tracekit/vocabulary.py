"""The vocabulary of investigation.json (see references/investigation-schema.md)."""
VERDICTS = ("bug_found", "risks_found", "works_as_expected", "reproduced", "not_reproduced", "inconclusive")
SCENARIO_STATUSES = ("pass", "fail", "bug", "risk", "expected_error", "reproduced", "inconclusive")
# Statuses where a scenario may legitimately change no data (it is rejected before any write).
NO_CHANGE_OK = ("expected_error", "risk", "inconclusive")
CATEGORIES = ("happy_path", "validation", "auth", "permission", "not_found", "conflict", "boundary", "edge_case",
              "error_handling", "external_failure", "concurrency", "security", "performance", "reproduction", "regression")
STEP_KINDS = ("input", "parse", "validation", "branch", "transform", "lookup", "db_read", "db_write", "external_call",
              "state_change", "side_effect", "auth", "cache", "event", "job", "response", "error", "return")
NODE_KINDS = STEP_KINDS + ("entry", "middleware", "callback", "output")
CHANGE_OPS = ("set", "update", "delete", "create", "append", "remove", "read")
SEVERITIES = ("critical", "major", "minor")
EVIDENCE = ("simulated", "executed")
ENVIRONMENTS = ("test", "development")
