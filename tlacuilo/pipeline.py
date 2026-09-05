"""One job, end to end: classify → text layer → fingerprint → parse → validate → result.

Fails closed: an unreadable document is an error with a code, never a stub result."""

from __future__ import annotations

import hashlib
import logging
import shutil
import time

from . import __version__
from .contract import (
    DocumentInfo,
    DocumentType,
    Engines,
    ExtractionResult,
    Period,
    Provenance,
    Sensitivity,
    Statement,
    Validation,
)
from .engines import text_layer
from .metrics import DURATION, EXTRACTIONS, PAGES, VALIDATION
from .parsers.generic_statement import header_columns, parse_statement, to_contract_rows
from .parsers.registry import fingerprint, parser_id
from .settings import get_settings
from .validate import validate_statement

log = logging.getLogger(__name__)
PDF = "application/pdf"
IMAGES = {"image/png", "image/jpeg", "image/webp", "image/tiff"}


class ExtractionError(Exception):
    def __init__(self, code: str, status: int = 422, **extra):
        super().__init__(code)
        self.code, self.status, self.extra = code, status, extra


def engines_available() -> dict[str, str | None]:
    tesseract = shutil.which("tesseract")
    return {
        "text": text_layer.ENGINE,
        "layout": None,
        "ocr": f"tesseract ({tesseract})" if tesseract else None,
        "vlm": None,
    }


def run_extraction(
    data: bytes,
    mime_type: str,
    sensitivity: Sensitivity,
    *,
    max_pages: int | None = None,
    doc_type_hint: DocumentType | None = None,
) -> ExtractionResult:
    started = time.perf_counter()
    sha = hashlib.sha256(data).hexdigest()
    mime = (mime_type or "").split(";")[0].strip().lower()
    if mime in IMAGES:
        raise ExtractionError("needs_ocr", engines=engines_available(), note="image inputs arrive with the M1 OCR lane")
    if mime != PDF:
        raise ExtractionError("unsupported_mime", mime=mime)

    try:
        pages = text_layer.extract_pages(data)
    except Exception as exc:  # corrupt or encrypted PDF
        log.info("pdf unreadable: %s", type(exc).__name__)
        raise ExtractionError("unreadable_pdf", reason=type(exc).__name__) from None
    if max_pages is not None and len(pages) > max_pages:
        raise ExtractionError("too_many_pages", status=413, pages=len(pages), limit=max_pages)
    if not text_layer.has_text_layer(pages):
        raise ExtractionError("needs_ocr", pages=len(pages), engines=engines_available())
    PAGES.labels(engine="text").inc(len(pages))

    # Fingerprint on page 1 ABOVE the table header only: a transaction description may
    # name another bank ("PAGO TARJETA BBVA" on a Santander statement).
    head: list = []
    for line in pages[0].lines if pages else []:
        if header_columns(line):
            break
        head.append(line)
    first_text = "\n".join(line.text for line in (head or pages[0].lines)).upper() if pages else ""
    bank, fp_conf = fingerprint(first_text)
    parsed = parse_statement(pages, bank)
    validation: Validation = validate_statement(parsed)
    is_statement = bool(parsed.rows) and (
        parsed.opening is not None or parsed.closing is not None or len(parsed.rows) >= 3
    )
    doc_type = DocumentType.bank_statement if is_statement else (doc_type_hint or DocumentType.unknown)

    statement = None
    if is_statement:
        statement = Statement(
            bank=parsed.bank,
            accountLast4=parsed.account_last4,
            clabeLast4=parsed.clabe_last4,
            currency=parsed.currency,
            period=Period(start=parsed.period[0], end=parsed.period[1]) if parsed.period else None,
            openingBalance=float(parsed.opening) if parsed.opening is not None else None,
            closingBalance=float(parsed.closing) if parsed.closing is not None else None,
            transactions=to_contract_rows(parsed.rows),
        )
        VALIDATION.labels(
            result={True: "reconciles", False: "mismatch", None: "unknown"}[validation.balanceReconciles]
        ).inc()
    else:
        validation = Validation(
            balanceReconciles=None, unparsedLines=parsed.unparsed_lines, warnings=["no statement table recognised"]
        )

    duration_ms = int((time.perf_counter() - started) * 1000)
    DURATION.observe(duration_ms / 1000)
    EXTRACTIONS.labels(doc_type=doc_type.value, outcome="ok").inc()
    log.info("extraction ok type=%s pages=%d rows=%d ms=%d", doc_type.value, len(pages), len(parsed.rows), duration_ms)
    return ExtractionResult(
        document=DocumentInfo(
            type=doc_type,
            mimeType=mime,
            pages=len(pages),
            language="es",
            sha256=sha,
            sizeBytes=len(data),
            classification=sensitivity,
        ),
        provenance=Provenance(
            version=__version__,
            engines=Engines(text=text_layer.ENGINE),
            parser=parser_id(bank) if is_statement else None,
            fingerprintConfidence=fp_conf if is_statement else 0.0,
            durationMs=duration_ms,
        ),
        statement=statement,
        validation=validation,
    )


def sync_page_limit() -> int:
    return get_settings().sync_max_pages
