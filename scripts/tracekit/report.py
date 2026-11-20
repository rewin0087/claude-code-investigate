"""Build the self-contained interactive HTML report."""
import json
import os

from .errors import InvestigateError

DEFAULT_TEMPLATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "templates", "report.html")
DATA_PLACEHOLDER, TITLE_PLACEHOLDER = "__INVESTIGATION_DATA__", "__TITLE__"


def embed_json(obj):
    """JSON that is safe to place inside a <script> element."""
    return json.dumps(obj, default=str).replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def build_html(payload, template_path=DEFAULT_TEMPLATE):
    with open(template_path) as fh:
        html = fh.read()
    if DATA_PLACEHOLDER not in html:
        raise InvestigateError("template is missing the %s placeholder" % DATA_PLACEHOLDER)
    title = "Investigation: %s" % payload["investigation"].get("title", "")
    html = html.replace(DATA_PLACEHOLDER, embed_json(payload), 1)
    return html.replace(TITLE_PLACEHOLDER, title.replace("&", "&amp;").replace("<", "&lt;"), 1)
