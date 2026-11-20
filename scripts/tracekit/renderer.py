"""Renderer: validate investigation.json and produce report.html + summary.md for one output folder."""
import os

from .errors import InvestigateError
from .jsonio import load_json
from .report import ReportBuilder
from .snippets import SnippetCollector
from .summary import SummaryWriter, file_url
from .tracelog import TraceLog
from .validator import InvestigationValidator
from .workspace import Workspace


class RenderResult(object):
    def __init__(self, errors=(), warnings=(), report_path=None, summary=None):
        self.errors, self.warnings = list(errors), list(warnings)
        self.report_path, self.summary = report_path, summary

    @property
    def report_url(self):
        return file_url(self.report_path) if self.report_path else None


class Renderer(object):
    def __init__(self, out_dir):
        self.ws = Workspace.load(out_dir)
        self.inv = load_json(self.ws.path("investigation.json"), "investigation.json")
        self.notes = []          # non-fatal problems with optional files, printed as WARNING:

    def render(self, out_path=None):
        errors, warnings = InvestigationValidator(self.inv, self.ws.repo, self.ws.out_dir).validate()
        if errors:
            return RenderResult(errors, warnings)
        snippets = SnippetCollector(self.ws.repo).add_investigation(self.inv)
        traces = self._traces(snippets, warnings)
        stacktrace = self.ws.optional_json("stacktrace.json", self.notes.append)
        for f in (stacktrace or {}).get("frames", []):
            if f.get("app"):
                snippets.add(f.get("path"), f.get("line"))
        discovery = self.ws.optional_json("discovery.json", self.notes.append)
        out = os.path.abspath(out_path or self.ws.path("report.html"))
        summary = SummaryWriter(self.inv, out).write()
        payload = {
            "meta": self.ws.meta, "investigation": self.inv, "traces": traces, "sources": snippets.build(),
            "context": self.ws.optional_json("context.json", self.notes.append), "stacktrace": stacktrace,
            "discovery": {"stack": discovery.get("stack"), "frameworks": discovery.get("frameworks")} if discovery else None,
            "summary_md": summary,
        }
        with open(out, "w") as fh:
            fh.write(ReportBuilder().build(payload))
        with open(self.ws.path("summary.md"), "w") as fh:
            fh.write(summary)
        return RenderResult([], warnings, out, summary)

    def _traces(self, snippets, warnings):
        """Compact recorded traces per scenario; their lines are embedded as code too."""
        traces = {}
        for sc in self.inv.get("scenarios") or []:
            if not sc.get("trace"):
                continue
            try:
                t = traces[sc["id"]] = TraceLog.load(self.ws.path(sc["trace"])).compact()
            except InvestigateError as e:
                warnings.append(str(e))
                continue
            for e in [e for e in t["events"] if e.get("file")][:400]:
                snippets.add(e.get("file"), e.get("line"))
        return traces
