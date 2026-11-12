import logging

import pytest

from tlacuilo import db
from tlacuilo.contract import DocumentType, Sensitivity
from tlacuilo.engines import ocr
from tlacuilo.logging import NOISY_THIRD_PARTY, RedactionFilter, configure_logging, redact
from tlacuilo.pipeline import ExtractionError, run_extraction

from .conftest import needs_ocr_engine
from .synth import make_statement
from .synth_letter import MONTHS_ES, as_scan, as_scanned_pdf, make_letter


def test_filter_masks_digit_runs():
    record = logging.LogRecord(
        "t", logging.INFO, __file__, 1, "clabe %s amount %s", ("012180001234567891", "15,230.55"), None
    )
    assert RedactionFilter().filter(record)
    assert "0121" not in record.getMessage() and "####" in record.getMessage()
    assert redact("cuenta 4567 ok 123") == "cuenta #### ok 123"


def test_pipeline_logs_carry_no_content(caplog):
    pdf, truth = make_statement()
    configure_logging()  # production configuration: INFO, third-party parsers at WARNING
    with caplog.at_level(logging.DEBUG):
        run_extraction(pdf, "application/pdf", Sensitivity.restricted)
    assert all(logging.getLogger(n).level >= logging.WARNING for n in NOISY_THIRD_PARTY)
    text = "\n".join(r.getMessage() for r in caplog.records)
    for _d, desc, amount, _balance in truth.rows:
        assert desc not in text
        assert f"{abs(amount):,.2f}" not in text
    assert truth.last4 not in text and "1234567891" not in text


def _letter_content(truth) -> list[str]:
    """What a letter prints that no log line may carry: names, dates, the licence."""
    words = [w.strip(",.") for w in (truth.institution + " " + truth.professional).split()]
    dates = [f"{d:%d/%m/%Y}" for d in (truth.issued, truth.born)] + [truth.issued.isoformat(), truth.born.isoformat()]
    return [
        truth.institution,
        truth.professional,
        *[w for w in words if len(w) > 3],
        *dates,
        MONTHS_ES[truth.issued.month - 1],
        truth.licence,
        truth.address,
        truth.phone,
        truth.email,
    ]


def _lane(data: bytes, mime: str = "application/pdf"):
    return run_extraction(data, mime, Sensitivity.restricted, doc_type_hint=DocumentType.letterhead_document)


def test_letterhead_lane_logs_carry_no_content(caplog):
    pdf, truth = make_letter(letterhead_person="Psic. Ana Ejemplo Inventado")
    configure_logging()
    with caplog.at_level(logging.DEBUG):
        result = _lane(pdf)
        with pytest.raises(ExtractionError):
            _lane(b"%PDF-1.4 broken")
    assert result.issuer.institution and result.issuer.professional and result.issuer.documentDate
    assert all(logging.getLogger(n).level >= logging.WARNING for n in NOISY_THIRD_PARTY)
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "type=letterhead_document" in text and "fields=3" in text  # counts and codes are what is logged
    for content in _letter_content(truth):
        assert content not in text, content


def test_letterhead_lane_failures_log_no_content(caplog, monkeypatch):
    pdf, truth = make_letter()
    monkeypatch.setattr(ocr, "tesseract_path", lambda: None)
    configure_logging()
    with caplog.at_level(logging.DEBUG):
        for data, mime in ((as_scan(pdf, dpi=50), "image/png"), (as_scanned_pdf(pdf, dpi=50), "application/pdf")):
            with pytest.raises(ExtractionError) as err:
                _lane(data, mime)
            assert err.value.code == "ocr_unavailable"
            assert not set(err.value.extra) - {"reason"}  # an error carries a code and a class, never text
    text = "\n".join(r.getMessage() for r in caplog.records)
    for content in _letter_content(truth):
        assert content not in text, content


@needs_ocr_engine
def test_letterhead_ocr_logs_carry_no_content(caplog):
    pdf, truth = make_letter()
    configure_logging()
    with caplog.at_level(logging.DEBUG):
        result = _lane(as_scan(pdf), "image/png")
        _lane(as_scanned_pdf(pdf))
    assert result.issuer.professional is not None
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "source=ocr" in text
    for content in _letter_content(truth):
        assert content not in text, content


def test_the_jobs_table_holds_no_content_column():
    # Doctrine 1: ids, hashes, sizes, pages, timings, engine names, status, the label.
    # The letterhead lane added none; a new column here is an RFC amendment, not a diff.
    assert {c.name for c in db.Job.__table__.columns} == {
        "id", "org_id", "status", "mode", "doc_type", "sensitivity", "sha256", "size_bytes", "pages", "engines",
        "duration_ms", "error_code", "created_at", "updated_at", "expires_at",
    }  # fmt: skip
