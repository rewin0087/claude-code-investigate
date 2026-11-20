"""Business logic for the investigate skill.

The scripts in the parent directory are thin command-line wrappers around this package:

  gitcmd       running git, repo root, keeping output out of commits
  target       resolving what to investigate (file:line, symbol, route, selection)
  context      bug reports, repro steps, specs, images and links supplied by the user
  stacktrace   parsing stack traces (7 languages) and mapping frames to repo lines
  discover     stack, schema, factories/fixtures, routes, tests and DB config of the app
  dbsafe       read-only, masked sample queries against a local/test database
  tracedigest  turning raw runtime-tracer events into a compact per-line digest
  model        investigation.json loading and validation
  snippets     embedding the referenced source code in the report
  summary      the markdown summary relayed in the chat
  report       the self-contained interactive HTML report
"""
from .errors import InvestigateError

__all__ = ["InvestigateError"]
