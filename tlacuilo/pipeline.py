"""One job, end to end: classify → text layer → fingerprint → parse → validate → result.

Fails closed: an unreadable document is an error with a code, never a stub result.

Two lanes, chosen by the caller and never by guessing. Without a document type, or with
any type but one, a job takes the statement lane exactly as before. With
`letterhead_document` it takes the letterhead lane (`_run_letterhead`): who issued the
document and when, by rules alone, from the text layer or — for a scan or a photo — from
OCR of at most two pages. The two lanes share no parser: a letter is never read as a
statement, nor a statement as a letter."""

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
    Issuer,
    Period,
    Provenance,
    Sensitivity,
    Statement,
    Validation,
)
from .engines import ocr, text_layer
from .metrics import DURATION, EXTRACTIONS, PAGES, VALIDATION
from .parsers import letterhead
from .parsers.generic_statement import header_columns, parse_statement, to_contract_rows
from .parsers.registry import fingerprint, parser_id
from .settings import get_settings
from .validate import validate_statement

log = logging.getLogger(__name__)
PDF = "application/pdf"
IMAGES = {"image/png", "image/jpeg", "image/webp", "image/tiff"}
LETTERHEAD_MIMES = (PDF, *ocr.IMAGE_FORMATS)  # the closed list the letterhead lane accepts


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
    if doc_type_hint is DocumentType.letterhead_document:
        return _run_letterhead(data, mime, sensitivity, sha=sha, started=started)
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


def _run_letterhead(data: bytes, mime: str, sensitivity: Sensitivity, *, sha: str, started: float) -> ExtractionResult:
    """The letterhead lane: text layer first, OCR when there is none, then rules.

    No model and no gateway: nothing on this path reads the inference settings or opens
    a connection. The page limit of sync mode does not apply — the lane reads at most
    `letterhead.PAGES` pages of any document and says so when there were more."""
    if mime not in LETTERHEAD_MIMES:
        raise ExtractionError("unsupported_mime", mime=mime, allowed=list(LETTERHEAD_MIMES))
    used_ocr = False
    try:
        if mime == PDF:
            try:
                total = text_layer.page_count(data)
                pages = text_layer.extract_pages(data, limit=letterhead.PAGES)
            except Exception as exc:  # corrupt or encrypted PDF
                log.info("pdf unreadable: %s", type(exc).__name__)
                raise ExtractionError("unreadable_pdf", reason=type(exc).__name__) from None
            if total < 1:
                raise ExtractionError("unreadable_pdf", reason="no_pages")
            if not text_layer.has_text_layer(pages):
                pages, used_ocr = ocr.pdf_pages(data, letterhead.PAGES), True
        else:
            total = 1
            pages, used_ocr = ocr.image_pages(data, mime), True
    except ocr.OcrError as exc:
        raise ExtractionError(exc.code, status=exc.status, **exc.extra) from None
    words = sum(len(line.words) for page in pages for line in page.lines)
    if used_ocr and words == 0:  # a blank or illegible scan is an error, not an empty result
        raise ExtractionError("unreadable_scan", pages=len(pages))
    PAGES.labels(engine="ocr" if used_ocr else "text").inc(len(pages))

    draft = letterhead.extract_issuer(pages, ocr=used_ocr)
    warnings = list(draft.warnings)
    if used_ocr:
        warnings.append("text read by OCR: confidences are reduced")
    if total > len(pages):
        warnings.append(f"only the first {len(pages)} of {total} pages were read")
    found = sum(f is not None for f in (draft.institution, draft.professional, draft.document_date))

    duration_ms = int((time.perf_counter() - started) * 1000)
    DURATION.observe(duration_ms / 1000)
    EXTRACTIONS.labels(doc_type=DocumentType.letterhead_document.value, outcome="ok").inc()
    log.info(
        "extraction ok type=%s pages=%d source=%s fields=%d ms=%d",
        DocumentType.letterhead_document.value,
        total,
        "ocr" if used_ocr else "text",
        found,
        duration_ms,
    )
    engines = (
        Engines(ocr=ocr.engine_label(rasterised=mime == PDF)) if used_ocr else Engines(text=text_layer.ENGINE)
    )
    return ExtractionResult(
        document=DocumentInfo(
            type=DocumentType.letterhead_document,
            mimeType=mime,
            pages=total,
            language="es",
            sha256=sha,
            sizeBytes=len(data),
            classification=sensitivity,
        ),
        provenance=Provenance(
            version=__version__,
            engines=engines,
            parser=letterhead.PARSER_ID,
            fingerprintConfidence=0.0,
            durationMs=duration_ms,
        ),
        issuer=Issuer(**letterhead.to_contract(draft)),
        validation=Validation(balanceReconciles=None, unparsedLines=0, warnings=warnings),
    )


def sync_page_limit() -> int:
    return get_settings().sync_max_pages
