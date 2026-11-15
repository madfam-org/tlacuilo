"""The text lane end to end on a synthetic posting: selection, the text block, the page
limits, what every other lane keeps, the doctrines, and the OCR path (Spanish and
English) where the engine is installed."""

import json
import logging
import re
import socket
import unicodedata

import pytest

import tlacuilo.pipeline as pipeline
from tlacuilo.contract import TEXT_LINE_MAX, DocumentType, ExtractionResult, Sensitivity, Text, json_schema
from tlacuilo.engines import ocr, scan
from tlacuilo.pipeline import ExtractionError, run_extraction

from .conftest import needs_text_ocr_engine
from .synth import make_statement
from .synth_letter import as_scan, as_scanned_pdf, make_letter
from .synth_posting import make_blank_page, make_posting

LANE = DocumentType.text_document


def read(data: bytes, mime: str = "application/pdf", sensitivity=Sensitivity.restricted) -> ExtractionResult:
    return run_extraction(data, mime, sensitivity, doc_type_hint=LANE)


def tokens(text: str) -> list[str]:
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return re.findall(r"[a-z]{3,}", plain)


# --- the result -------------------------------------------------------------------


def test_a_text_pdf_yields_its_lines_in_order_with_boxes_and_no_confidence():
    pdf, truth = make_posting()
    result = read(pdf)
    assert result.contract == "document-extraction/v1"
    assert result.document.type == LANE and result.document.pages == 1 and result.document.language is None
    text = result.text
    assert (text.source, text.pagesRead, text.quality.verdict) == ("text_layer", 1, "good")
    (page,) = text.pages
    assert page.page == 1 and page.meanWordConfidence is None
    assert [line.text for line in page.lines] == list(truth)
    assert text.characters == sum(len(line) for line in truth)
    tops = []
    for line in page.lines:
        assert line.confidence is None
        x0, top, x1, bottom = line.bbox
        assert 0 <= x0 < x1 <= 612 and 0 <= top < bottom <= 792  # PDF points, top-left origin
        tops.append(top)
    assert tops == sorted(tops)
    assert result.provenance.parser == "text.lines.v1"
    assert result.provenance.engines.text.startswith("pdfplumber") and result.provenance.engines.ocr is None
    assert result.provenance.engines.vlm is None and result.provenance.fingerprintConfidence == 0.0
    assert result.statement is None and result.issuer is None and result.csf is None and result.fields == {}
    assert result.validation.balanceReconciles is None and result.validation.unparsedLines == 0
    assert result.validation.warnings == []
    body = result.model_dump(mode="json")
    assert list(body) == ["contract", "document", "provenance", "statement", "text", "validation", "fields"]
    ExtractionResult.model_validate(body)  # the result is its own contract


def test_a_long_text_pdf_is_read_ten_pages_and_says_so():
    pdf, truth = make_posting(pages=12)
    result = read(pdf)
    assert result.document.pages == 12 and result.text.pagesRead == pipeline.TEXT_LAYER_PAGES == 10
    assert [p.page for p in result.text.pages] == list(range(1, 11))
    assert result.validation.warnings == ["only the first 10 of 12 pages were read"]


def test_a_result_is_cut_at_the_size_limit_and_says_so(monkeypatch):
    pdf, truth = make_posting(filler_lines=20)
    limit = sum(len(line) for line in truth[:3]) + 5
    monkeypatch.setattr(pipeline, "TEXT_TOTAL_MAX", limit)
    result = read(pdf)
    assert [line.text for line in result.text.pages[0].lines] == list(truth[:3])
    assert result.text.characters <= limit
    assert "the text was cut at the size limit of a result" in result.validation.warnings
    assert Text.model_fields["characters"].metadata  # the contract bounds it too
    assert TEXT_LINE_MAX == 2000


def test_the_lane_is_chosen_by_the_caller_and_never_by_guessing():
    pdf, _ = make_posting()
    with_hint = read(pdf)
    without = run_extraction(pdf, "application/pdf", Sensitivity.restricted)
    assert with_hint.text is not None and without.text is None
    assert "text" not in without.model_dump(mode="json")
    statement = run_extraction(make_statement()[0], "application/pdf", Sensitivity.restricted, doc_type_hint=LANE)
    assert statement.statement is None and statement.text.source == "text_layer"  # read, never parsed


def test_other_lanes_serialise_without_the_text_key():
    letter, _ = make_letter()
    letterhead = run_extraction(
        letter, "application/pdf", Sensitivity.restricted, doc_type_hint=DocumentType.letterhead_document
    )
    assert list(letterhead.model_dump(mode="json")) == [
        "contract", "document", "provenance", "statement", "issuer", "validation", "fields",
    ]  # fmt: skip
    statement = run_extraction(make_statement()[0], "application/pdf", Sensitivity.restricted)
    assert list(statement.model_dump(mode="json")) == [
        "contract", "document", "provenance", "statement", "validation", "fields",
    ]  # fmt: skip


def test_the_text_block_is_additive_in_the_schema():
    schema = json_schema()
    defs = schema["$defs"]
    assert "text_document" in defs["DocumentType"]["enum"]
    assert "text" in schema["properties"] and "text" not in schema["required"]
    assert set(defs["Text"]["properties"]) == {"source", "pagesRead", "characters", "pages", "quality"}
    assert set(defs["TextPage"]["properties"]) == {"page", "meanWordConfidence", "lines"}
    assert set(defs["TextLine"]["properties"]) == {"text", "confidence", "bbox"}
    assert defs["TextQuality"]["properties"]["verdict"]["enum"] == ["good", "fair", "poor"]
    assert defs["Text"]["properties"]["source"]["enum"] == ["text_layer", "ocr"]


# --- refusals ---------------------------------------------------------------------


def test_unsupported_types_and_unreadable_pdfs_are_error_codes():
    with pytest.raises(ExtractionError) as err:
        read(b"hello", "text/plain")
    assert err.value.code == "unsupported_mime" and "application/pdf" in err.value.extra["allowed"]
    with pytest.raises(ExtractionError) as err:
        read(b"%PDF-1.4 not really a pdf")
    assert err.value.code == "unreadable_pdf"


def test_scans_fail_closed_missing_engine_blank_and_too_large(monkeypatch):
    pdf, _ = make_posting()
    image = as_scan(pdf, dpi=50)
    monkeypatch.setattr(ocr, "tesseract_path", lambda: None)
    for data, mime in ((image, "image/png"), (as_scanned_pdf(pdf, dpi=50), "application/pdf")):
        with pytest.raises(ExtractionError) as err:
            read(data, mime)
        assert (err.value.code, err.value.status) == ("ocr_unavailable", 503)

    monkeypatch.setattr(ocr, "_recognise", lambda *a, **k: "level\tpage_num\n")  # the engine saw no word
    with pytest.raises(ExtractionError) as err:
        read(image, "image/png")
    assert err.value.code == "unreadable_scan"
    with pytest.raises(ExtractionError) as err:
        read(make_blank_page())  # no text layer, and OCR finds nothing
    assert err.value.code == "unreadable_scan"

    monkeypatch.setattr(ocr, "MAX_IMAGE_PIXELS", 1000)
    with pytest.raises(ExtractionError) as err:
        read(image, "image/png")
    assert (err.value.code, err.value.status) == ("image_too_large", 413)


def test_the_lane_reads_spanish_and_english_and_other_lanes_keep_spanish(monkeypatch):
    asked: list[str] = []

    def fake(image, deadline, dpi=None, *, psm=3, config=(), lang=ocr.LANG):
        asked.append(lang)
        return "level\tpage_num\n"

    monkeypatch.setattr(ocr, "_recognise", fake)
    pdf, _ = make_posting()
    image = as_scan(pdf, dpi=50)
    with pytest.raises(ExtractionError):
        read(image, "image/png")
    assert asked and set(asked) == {"spa+eng"} == {ocr.TEXT_LANG}
    asked.clear()
    with pytest.raises(ExtractionError):
        run_extraction(image, "image/png", Sensitivity.restricted, doc_type_hint=DocumentType.letterhead_document)
    with pytest.raises(ExtractionError):
        run_extraction(image, "image/png", Sensitivity.restricted, doc_type_hint=DocumentType.csf)
    assert asked and set(asked) == {"spa"}
    assert ocr.LANGS == ("spa", "spa+eng")  # closed: a language never comes from a request


def test_a_language_outside_the_closed_list_is_refused_before_the_engine_runs():
    from PIL import Image

    with pytest.raises(ocr.OcrError) as err:
        ocr._recognise(Image.new("L", (10, 10), 255), deadline=1e18, lang="spa+eng+osd")
    assert (err.value.code, err.value.status) == ("ocr_unavailable", 503)


# --- doctrines --------------------------------------------------------------------


def test_no_network_and_no_content_in_logs(monkeypatch, caplog):
    def no_network(*a, **k):
        raise AssertionError("the text lane opened a connection")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    pdf, truth = make_posting()
    with caplog.at_level(logging.INFO, logger="tlacuilo"):
        read(pdf)
    logged = " ".join(r.getMessage() for r in caplog.records if r.name.startswith("tlacuilo"))
    assert "extraction ok type=text_document" in logged
    for line in truth:
        assert line not in logged
    for word in ("Maison", "Ejemplo", "Asesora", "Requirements", "luxury"):
        assert word not in logged


# --- OCR, where the engine is installed --------------------------------------------


@needs_text_ocr_engine
@pytest.mark.parametrize("form", ["png", "scanned_pdf"])
def test_a_scan_is_read_by_ocr_in_both_languages(form):
    pdf, truth = make_posting()
    if form == "png":
        result = read(as_scan(pdf), "image/png")
        width, height = 1700, 2200  # pixels of the upload
    else:
        result = read(as_scanned_pdf(pdf))
        width, height = 612, 792  # PDF points
    text = result.text
    assert (text.source, text.pagesRead) == ("ocr", 1) and text.quality.verdict == "good"
    (page,) = text.pages
    assert page.meanWordConfidence is not None and page.meanWordConfidence >= 0.8
    wanted = [t for line in truth for t in tokens(line)]
    got = set(tokens(" ".join(line.text for line in page.lines)))
    found = sum(1 for t in wanted if t in got)
    assert found >= 0.9 * len(wanted), f"read {found} of {len(wanted)} words"
    for word in ("responsabilidades", "requirements", "luxury", "ventas", "weekends"):
        assert word in got  # both languages
    for line in page.lines:
        assert line.confidence is not None and 0 <= line.confidence <= 1
        x0, top, x1, bottom = line.bbox
        assert -1 <= x0 < x1 <= width + 1 and -1 <= top < bottom <= height + 1
    assert text.characters == sum(len(line.text) for line in page.lines)
    assert "spa+eng" in result.provenance.engines.ocr and result.provenance.engines.text is None
    assert result.validation.warnings == ["text read by OCR: confirm it against the document"]
    ExtractionResult.model_validate(json.loads(result.model_dump_json()))


@needs_text_ocr_engine
def test_a_long_scan_is_read_four_pages_and_says_so():
    pdf, _ = make_posting(pages=5)
    result = read(as_scanned_pdf(pdf, dpi=100))
    assert result.document.pages == 5 and result.text.pagesRead == scan.MAX_PAGES == 4
    assert "only the first 4 of 5 pages were read" in result.validation.warnings


# --- the API ----------------------------------------------------------------------


def test_the_api_accepts_the_document_type_and_refuses_a_reader(client, token):
    pdf, truth = make_posting()
    headers = {"Authorization": f"Bearer {token}", "X-Sensitivity": "restricted"}
    r = client.post(
        "/v1/extractions",
        headers=headers,
        files={"file": ("posting.pdf", pdf, "application/pdf")},
        data={"document_type": "text_document"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["document"]["type"] == "text_document" and body["document"]["language"] is None
    assert [line["text"] for line in body["text"]["pages"][0]["lines"]] == list(truth)
    assert "issuer" not in body and "csf" not in body
    r = client.post(
        "/v1/extractions",
        headers=headers,
        files={"file": ("posting.pdf", pdf, "application/pdf")},
        data={"document_type": "text_document", "reader": "ocr"},
    )
    assert r.status_code == 400 and r.json()["detail"]["error"] == "invalid_reader"
    r = client.post(
        "/v1/extractions",
        headers=headers,
        files={"file": ("x.bin", b"hello", "application/octet-stream")},
        data={"document_type": "text_document"},
    )
    assert r.status_code == 422 and r.json()["error"] == "unsupported_mime"
