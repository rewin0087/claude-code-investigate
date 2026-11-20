"""Context supplied with an investigation: bug reports, repro steps, specs, images, links.

ContextCollector classifies each source, copies local files and free text into
<OUT_DIR>/context/, records everything in <OUT_DIR>/context.json, and formats a digest for
the investigator. Nothing is fetched from the network here: URLs are recorded and Claude
fetches them.
"""
import hashlib
import os
import re
import shutil
import zipfile

from .errors import InvestigateError
from .jsonio import write_json

TEXT_EXT = {".txt", ".md", ".markdown", ".rst", ".adoc", ".csv", ".tsv", ".json", ".yaml", ".yml",
            ".xml", ".html", ".htm", ".feature", ".gherkin", ".org", ".log", ".ini", ".toml", ".sql"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp"}
MAX_INLINE_CHARS = 20000          # per source, in the digest printed for the investigator
MAX_FILE_BYTES = 20 * 1024 * 1024
URL_RE = re.compile(r"^https?://", re.I)
TICKET_HOSTS = ("atlassian.net", "jira", "linear.app", "github.com", "gitlab", "azure.com", "visualstudio.com",
                "shortcut.com", "clickup.com", "asana.com", "notion.so", "trello.com", "monday.com")


class SourceDocument(object):
    """Helpers for one local context file."""

    def __init__(self, path):
        self.path = path

    @property
    def kind(self):
        ext = os.path.splitext(self.path)[1].lower()
        if ext in IMAGE_EXT:
            return "image"
        if ext == ".pdf":
            return "pdf"
        if ext == ".docx":
            return "docx"
        if ext in TEXT_EXT:
            return "text"
        with open(self.path, "rb") as fh:
            return "binary" if b"\0" in fh.read(8000) else "text"

    def docx_text(self):
        """Plain text of a .docx (paragraphs only) without third-party libraries."""
        try:
            with zipfile.ZipFile(self.path) as z:
                xml = z.read("word/document.xml").decode("utf-8", errors="replace")
        except (KeyError, zipfile.BadZipFile) as e:
            raise InvestigateError("Could not read %s as .docx: %s" % (self.path, e))
        xml = re.sub(r"</w:p>", "\n", xml)
        xml = re.sub(r"<w:tab/>", "\t", xml)
        return re.sub(r"\n{3,}", "\n\n", re.sub(r"<[^>]+>", "", xml)).strip()

    def sha256(self):
        with open(self.path, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()[:16]


class ContextCollector(object):
    def __init__(self, out_dir, cwd):
        self.out_dir = out_dir
        self.cwd = cwd
        self.ctx_dir = os.path.join(out_dir, "context")

    def classify(self, source):
        """-> (kind, value): kind is file | url | text; value is an absolute path, URL or text."""
        s = source.strip()
        if URL_RE.match(s):
            return "url", s
        candidate = s[1:] if s.startswith("@") else s
        path = candidate if os.path.isabs(candidate) else os.path.join(self.cwd, candidate)
        if os.path.isfile(path):
            return "file", os.path.realpath(path)
        if os.path.isdir(path):
            raise InvestigateError("Context source '%s' is a folder; pass the individual files." % source)
        return "text", s

    def ingest(self, sources):
        """Copy/record every source. -> manifest dict (also written to context.json)."""
        if os.path.isdir(self.ctx_dir):
            shutil.rmtree(self.ctx_dir)           # each run reflects exactly the sources given
        os.makedirs(self.ctx_dir, exist_ok=True)
        items, seen = [], set()
        for src in sources:
            kind, value = self.classify(src)
            if (kind, value) in seen:
                continue
            seen.add((kind, value))
            sid = "S%d" % (len(items) + 1)
            item = {"id": sid, "kind": kind, "source": src}
            item.update(getattr(self, "_ingest_" + kind)(sid, value))
            items.append(item)
        manifest = {"sources": items}
        write_json(os.path.join(self.out_dir, "context.json"), manifest, indent=2)
        return manifest

    def _ingest_url(self, sid, url):
        return {"type": "ticket" if any(h in url.lower() for h in TICKET_HOSTS) else "link", "url": url, "title": url}

    def _ingest_text(self, sid, text):
        name = "%s-note.md" % sid
        self._write(name, text + "\n")
        return {"type": "text", "path": "context/" + name, "title": first_line(text), "chars": len(text)}

    def _ingest_file(self, sid, path):
        size = os.path.getsize(path)
        if size > MAX_FILE_BYTES:
            raise InvestigateError("Context file %s is larger than %d MB." % (path, MAX_FILE_BYTES // 2**20))
        doc = SourceDocument(path)
        ftype = doc.kind
        if ftype == "binary":
            raise InvestigateError("Context file %s is binary and not a supported type "
                                   "(text, markdown, pdf, docx, or an image)." % path)
        name = "%s-%s" % (sid, _slug(os.path.basename(path), 80))
        shutil.copyfile(path, os.path.join(self.ctx_dir, name))
        item = {"type": ftype, "path": "context/" + name, "title": os.path.basename(path), "bytes": size,
                "sha256": doc.sha256()}
        if ftype == "docx":
            self._write(name + ".txt", doc.docx_text())
            item["text_path"] = "context/" + name + ".txt"
        return item

    def _write(self, name, text):
        with open(os.path.join(self.ctx_dir, name), "w") as fh:
            fh.write(text)

    def digest(self, manifest):
        """What the investigator reads: inline text, plus what to Read / WebFetch next."""
        out_dir = os.path.abspath(self.out_dir)
        L = ["Context sources: %d" % len(manifest["sources"])]
        to_read, to_fetch = [], []
        for it in manifest["sources"]:
            L.append("\n### %s · %s · %s" % (it["id"], it["type"], it.get("title") or it["source"]))
            if it["type"] in ("text", "docx"):
                path = os.path.join(out_dir, it.get("text_path") or it["path"])
                with open(path, "rb") as fh:
                    body = fh.read().decode("utf-8", errors="replace")
                if len(body) > MAX_INLINE_CHARS:
                    L.append(body[:MAX_INLINE_CHARS] + "\n… [truncated: Read %s for the rest]" % path)
                else:
                    L.append(body.rstrip())
            elif it["type"] in ("image", "pdf"):
                p = os.path.join(out_dir, it["path"])
                to_read.append("%s (%s)" % (p, it["id"]))
                L.append("(%s: Read %s)" % (it["type"], p))
            else:
                to_fetch.append("%s (%s)" % (it["url"], it["id"]))
                L.append("(%s: WebFetch %s)" % (it["type"], it["url"]))
        if to_read:
            L.append("\nREAD_FILES:\n  " + "\n  ".join(to_read))
        if to_fetch:
            L.append("\nFETCH_URLS:\n  " + "\n  ".join(to_fetch))
        L.append("\nCONTEXT_JSON=%s" % os.path.join(out_dir, "context.json"))
        return "\n".join(L)


def _slug(text, limit=40):
    return re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip("-")[:limit] or "source"


def first_line(text, limit=80):
    line = next((l.strip() for l in text.splitlines() if l.strip()), "Note")
    line = re.sub(r"^[#>*\-\s]+", "", line)
    return line if len(line) <= limit else line[:limit - 1] + "…"
