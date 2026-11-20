"""Reading and writing the JSON files in the output folder."""
import json
import os

from .errors import InvestigateError


def load_json(path, label=None):
    label = label or os.path.basename(path)
    if not os.path.exists(path):
        raise InvestigateError("%s not found at %s" % (label, path))
    try:
        with open(path) as fh:
            return json.load(fh)
    except ValueError as e:
        raise InvestigateError("%s is not valid JSON: %s" % (label, e))


def write_json(path, data, indent=1):
    with open(path, "w") as fh:
        json.dump(data, fh, indent=indent, default=str)
    return path
