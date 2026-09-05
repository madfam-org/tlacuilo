"""Logging that cannot leak document content.

Rule: code never logs descriptions, amounts, names or field values — only ids, hashes,
sizes, counts, timings, engine names and error codes. The RedactionFilter is the belt
to that suspender: any run of four or more digits in a message is masked before it
reaches a handler, so an accidental amount, account number or CLABE never lands in a
log line. tests/test_redaction.py pins both halves."""

from __future__ import annotations

import logging
import re
import sys

_DIGIT_RUN = re.compile(r"\d{4,}")


class RedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003 - logging API
        try:
            message = record.getMessage()
        except Exception:  # pragma: no cover - malformed format args
            message = str(record.msg)
        record.msg = _DIGIT_RUN.sub("####", message)
        record.args = ()
        return True


def redact(text: str) -> str:
    return _DIGIT_RUN.sub("####", text)


NOISY_THIRD_PARTY = ("pdfminer", "pdfplumber", "PIL", "httpx", "httpcore", "celery")


def configure_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    root.setLevel(level)
    for name in NOISY_THIRD_PARTY:  # pdfminer logs page content at DEBUG
        logging.getLogger(name).setLevel(logging.WARNING)
    if not any(getattr(h, "_tlacuilo", False) for h in root.handlers):
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(
            logging.Formatter('{"ts":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}')
        )
        handler.addFilter(RedactionFilter())
        handler._tlacuilo = True  # type: ignore[attr-defined]
        root.addHandler(handler)
    for h in root.handlers:
        if not any(isinstance(f, RedactionFilter) for f in h.filters):
            h.addFilter(RedactionFilter())
