"""RowMasker: redact secret columns and mask personal data so real people never reach a report."""
import json
import re

SECRET_COL = re.compile(r"(password|passwd|pwd|secret|token|api_?key|private_?key|salt|hash|digest|otp|ssn|cvv|card_?number"
                        r"|iban|encrypted|credential|session_?id|cookie|signature)", re.I)
EMAIL = re.compile(r"([A-Za-z0-9._%+-])[A-Za-z0-9._%+-]*@([A-Za-z0-9.-]+\.[A-Za-z]{2,})")
PHONE = re.compile(r"(?<!\d)(\+?\d[\d\s().-]{7,}\d)(?!\d)")
PERSON_COL = re.compile(r"(^|_)(first_?name|last_?name|full_?name|name|phone|mobile|address|street|postcode|zip|dob|birth"
                        r"|ip_?address|national_id|tax_id)($|_)", re.I)


class RowMasker(object):
    def __init__(self, keep_pii=False):
        self.keep_pii = keep_pii

    def mask(self, cols, rows):
        return [[self.value(c, v) for c, v in zip(cols, r)] for r in rows]

    def value(self, col, v):
        if v is None:
            return None
        if SECRET_COL.search(col):
            return "[redacted]"
        if isinstance(v, bytes):
            return "<%d bytes>" % len(v)
        if self.keep_pii or not isinstance(v, str):
            return v
        s = EMAIL.sub(lambda m: "%s***@%s" % (m.group(1), m.group(2)), v)
        if PERSON_COL.search(col):
            return ((s[:1] + "***") if s else s)[:500]
        return (PHONE.sub("[phone]", s) if PHONE.fullmatch(s.strip() or "x") else s)[:500]

    @staticmethod
    def to_json(cols, rows):
        return json.dumps([dict(zip(cols, r)) for r in rows], indent=1, default=str)
