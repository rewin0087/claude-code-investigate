"""Preparer: start an investigation (resolve targets, create the output folder, collect context)."""
import os

from .context import ContextCollector
from .target import TargetResolver
from .workspace import Workspace, slugify


class Preparer(object):
    def __init__(self, repo_path, cwd):
        self.ws = Workspace.for_path(repo_path)
        self.cwd = cwd
        self.resolver = TargetResolver(self.ws, cwd)

    def slug_for(self, explicit, targets, sources):
        if explicit:
            return slugify(explicit)
        if targets:
            return slugify(targets[0]["label"])
        if sources:
            first = sources[0]
            return slugify(os.path.basename(first) if os.path.exists(first) else first)
        return "investigation"

    def run(self, target_specs, sources, slug=None):
        """-> printable report (REPO=, OUT_DIR=, target digests, context digest)."""
        targets = [self.resolver.parse(t) for t in target_specs]
        name = self.slug_for(slug, targets, sources)
        out_dir = self.ws.create(name)
        parts = [self.resolver.describe(t) for t in targets]
        self.ws.write_meta(self.cwd, targets, name)
        collector = ContextCollector(out_dir, self.cwd)
        ctx = collector.ingest(sources) if sources else None
        L = ["REPO=%s" % self.ws.repo, "OUT_DIR=%s" % out_dir, "Files in repo: %d" % len(self.ws.files)]
        L.extend(parts)
        if not targets:
            L.append("\nNo explicit target: use the editor selection, the stack trace, or the bug report to find the entry point.")
        if ctx:
            L.append("\n## Context\n" + collector.digest(ctx))
        return "\n".join(L)
