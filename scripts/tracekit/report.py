"""ReportBuilder: the self-contained interactive HTML report (templates/report.html + embedded JSON)."""
import json
import os

from .errors import InvestigateError

DEFAULT_TEMPLATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "templates", "report.html")
DATA_PLACEHOLDER, TITLE_PLACEHOLDER = "__INVESTIGATION_DATA__", "__TITLE__"


def embed_json(obj):
    """JSON that is safe to place inside a <script> element."""
    return json.dumps(obj, default=str).replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def html_escape(text):
    return text.replace("&", "&amp;").replace("<", "&lt;")


class ReportBuilder(object):
    def __init__(self, template_path=DEFAULT_TEMPLATE):
        self.template_path = template_path

    def build(self, payload):
        with open(self.template_path) as fh:
            html = fh.read()
        if DATA_PLACEHOLDER not in html:
            raise InvestigateError("template is missing the %s placeholder" % DATA_PLACEHOLDER)
        title = "Investigation: %s" % payload["investigation"].get("title", "")
        html = html.replace(DATA_PLACEHOLDER, embed_json(payload), 1)
        return html.replace(TITLE_PLACEHOLDER, html_escape(title), 1)
