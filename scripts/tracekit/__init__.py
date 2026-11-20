"""Business logic for the investigate skill, one concern per module (mostly one class each).

The scripts in the parent directory are thin command-line wrappers around these classes:

  Foundations
    errors.py       InvestigateError: user-facing errors (CLIs print them without a traceback)
    jsonio.py       load_json / write_json
    gitcmd.py       Git: running git for one repository
    sourcefile.py   SourceFile, SourceCache: repo files, snippets, enclosing definitions
    workspace.py    Workspace: the repo + its .investigate/<slug>/ output folder and meta.json

  Prepare (prepare.py)
    preparer.py     Preparer: resolve targets, create the folder, collect context
    target.py       TargetResolver: file:line / Class#method / "VERB /route" / text -> candidates
    context.py      ContextCollector, SourceDocument: bug reports, specs, images, links

  Stack traces (stacktrace.py)
    stacktrace.py   StackTraceParser, FrameMapper, StackTraceDigest

  Discovery (discover.py)
    discover.py     AppDiscovery, FileCategorizer, TestRunnerFinder
    stacks.py       StackDetector: languages + frameworks from manifests
    dbconfig.py     DbConfigReader: database settings, credentials redacted

  Local database samples (dbquery.py)
    dbsafe.py       SqlGuard (read-only single statements + LIMIT), DatabaseUrl (local hosts only)
    dbrunner.py     QueryRunner -> SqliteRunner, PostgresRunner, MysqlRunner (read-only sessions)
    masking.py      RowMasker: secrets redacted, personal data masked

  Runtime traces (trace_digest.py; the tracers themselves are standalone scripts)
    tracelog.py     TraceLog (load, compact for the report), TraceDigest (readable timeline)

  Render (render.py)
    renderer.py     Renderer, RenderResult: validate + build report.html and summary.md
    vocabulary.py   the enums of investigation.json
    validator.py    InvestigationValidator
    snippets.py     SnippetCollector: source code embedded in the report
    summary.py      SummaryWriter: the markdown summary relayed in the chat
    report.py       ReportBuilder: templates/report.html + embedded JSON
"""
from .errors import InvestigateError

__all__ = ["InvestigateError"]
