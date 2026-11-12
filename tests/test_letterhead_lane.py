"""The letterhead lane end to end on synthetic letters: selection, the issuer block,
what is never returned, the doctrines, and the OCR path where the engine is installed."""

import ast
import inspect
import json
import pathlib
import socket
from datetime import date

import pytest

import tlacuilo.pipeline as pipeline
from tlacuilo.contract import DocumentType, ExtractionResult, Sensitivity
from tlacuilo.engines import ocr
from tlacuilo.parsers import letterhead
from tlacuilo.pipeline import ExtractionError, run_extraction

from .conftest import needs_ocr_engine
from .synth import make_statement
from .synth_letter import as_scan, as_scanned_pdf, make_letter, make_plain_page

LANE = DocumentType.letterhead_document
ROOT = pathlib.Path(__file__).resolve().parents[1]


def read(data: bytes, mime: str = "application/pdf", sensitivity=Sensitivity.restricted) -> ExtractionResult:
    return run_extraction(data, mime, sensitivity, doc_type_hint=LANE)


def never_returned(truth) -> list[str]:
    """Everything the letter prints that no field of a result may carry."""
    b = truth.born
    return [
        truth.licence,
        b.isoformat(),
        f"{b:%d/%m/%Y}",
        "Cédula",
        truth.address,
        "Inventada 123",
        truth.phone,
        truth.email,
    ]


# --- the result -------------------------------------------------------------------


def test_a_text_letter_yields_institution_professional_and_date_with_provenance():
    pdf, truth = make_letter()
    result = read(pdf)
    assert result.contract == "document-extraction/v1"
    assert result.document.type == LANE and result.document.pages == 1
    assert result.document.classification == Sensitivity.restricted
    issuer = result.issuer
    assert (issuer.institution.name, issuer.institution.confidence) == (truth.institution, 0.9)
    assert issuer.institution.page == issuer.professional.page == issuer.documentDate.page == 1
    assert (issuer.professional.name, issuer.professional.confidence) == (truth.professional, 0.9)
    assert (issuer.documentDate.date, issuer.documentDate.confidence) == (truth.issued, 0.9)
    for found in (issuer.institution, issuer.professional, issuer.documentDate):
        x0, top, x1, bottom = found.bbox
        assert 0 <= x0 < x1 <= 612 and 0 <= top < bottom <= 792  # PDF points, top-left origin
    assert issuer.institution.bbox[1] < 198 < 514 < issuer.professional.bbox[1]  # letterhead above, signature below
    assert result.provenance.parser == "mx.letterhead.rules.v1"
    assert result.provenance.engines.text.startswith("pdfplumber")
    assert result.provenance.engines.ocr is None and result.provenance.engines.vlm is None
    assert result.provenance.fingerprintConfidence == 0.0
    assert result.statement is None and result.fields == {}
    assert result.validation.balanceReconciles is None and result.validation.warnings == []
    ExtractionResult.model_validate(result.model_dump(mode="json"))  # the result is its own contract


def test_letter_variants_numeric_date_second_page_signature_short_letter_and_agreement():
    pdf, truth = make_letter(numeric_date=True)  # «Fecha: 12/11/2026»: day and month could be swapped
    assert (read(pdf).issuer.documentDate.date, read(pdf).issuer.documentDate.confidence) == (truth.issued, 0.8)
    pdf, truth = make_letter(pages=2)
    result = read(pdf)
    assert (result.issuer.professional.name, result.issuer.professional.page) == (truth.professional, 2)
    assert result.issuer.institution.page == 1 and result.document.pages == 2
    pdf, truth = make_letter(short=True)  # the signature sits mid-page
    assert read(pdf).issuer.professional.name == truth.professional
    pdf, truth = make_letter(letterhead_person="Psic. Ana Ejemplo Inventado")
    assert read(pdf).issuer.professional.confidence == 0.95  # letterhead and signature agree
    pdf, truth = make_letter(institution="Jardín de Niños Ejemplo Sintético", professional="Mtra. Luz Muestra Falsa")
    issuer = read(pdf).issuer
    assert (issuer.institution.name, issuer.professional.name) == (truth.institution, truth.professional)


def test_only_two_pages_are_read_whatever_the_length_and_the_result_says_so(monkeypatch):
    pdf, truth = make_letter(pages=12)
    seen = {}
    real = pipeline.text_layer.extract_pages

    def spy(data, limit=None):
        seen["limit"] = limit
        return real(data, limit)

    monkeypatch.setattr(pipeline.text_layer, "extract_pages", spy)
    result = read(pdf)
    assert seen["limit"] == letterhead.PAGES == 2
    assert result.document.pages == 12
    assert result.validation.warnings == ["only the first 2 of 12 pages were read"]
    assert result.issuer.institution.name == truth.institution


def test_a_document_with_no_issuer_is_a_valid_result_of_three_nulls_and_a_warning():
    result = read(make_plain_page())  # running text only: nothing to find, nothing invented
    assert result.document.type == LANE and result.statement is None
    body = result.model_dump(mode="json")
    assert body["issuer"] == {"institution": None, "professional": None, "documentDate": None}
    assert "no issuer field recognised" in result.validation.warnings
    ExtractionResult.model_validate(body)


# --- selection: explicit, and never the other lane's parser ------------------------


def test_the_lane_never_runs_the_statement_parser(monkeypatch):
    for name in ("parse_statement", "validate_statement", "fingerprint"):
        monkeypatch.setattr(pipeline, name, lambda *a, **k: pytest.fail("statement lane reached"))
    pdf, _ = make_statement()
    assert read(pdf).statement is None
    pdf, truth = make_letter()
    assert read(pdf).issuer.institution.name == truth.institution


def test_without_the_document_type_a_letter_takes_the_statement_lane_exactly_as_before(monkeypatch):
    monkeypatch.setattr(letterhead, "extract_issuer", lambda *a, **k: pytest.fail("letterhead lane reached"))
    monkeypatch.setattr(ocr, "_recognise", lambda *a, **k: pytest.fail("OCR reached"))
    pdf, _ = make_letter()
    for hint in (None, DocumentType.bank_statement, DocumentType.receipt, DocumentType.unknown):
        result = run_extraction(pdf, "application/pdf", Sensitivity.restricted, doc_type_hint=hint)
        assert result.issuer is None and "issuer" not in result.model_dump(mode="json")
        assert result.document.type == (hint or DocumentType.unknown) and result.statement is None
        assert result.validation.warnings == ["no statement table recognised"]
    with pytest.raises(ExtractionError) as err:  # an image without the lane is still the M1 answer
        run_extraction(as_scan(pdf), "image/png", Sensitivity.restricted)
    assert err.value.code == "needs_ocr"


def test_a_statement_result_has_exactly_the_keys_it_had_before_the_issuer_block():
    pdf, _ = make_statement()
    body = run_extraction(pdf, "application/pdf", Sensitivity.restricted).model_dump(mode="json")
    assert list(body) == ["contract", "document", "provenance", "statement", "validation", "fields"]
    assert list(body["document"]) == ["type", "mimeType", "pages", "language", "sha256", "sizeBytes", "classification"]
    provenance = ["service", "version", "engines", "parser", "fingerprintConfidence", "durationMs"]
    assert list(body["provenance"]) == provenance
    assert list(body["provenance"]["engines"]) == ["text", "layout", "ocr", "vlm"]
    assert body["document"]["type"] == "bank_statement" and body["validation"]["balanceReconciles"] is True


def test_the_lane_accepts_a_closed_list_of_types():
    assert pipeline.LETTERHEAD_MIMES == ("application/pdf", "image/png", "image/jpeg")
    for mime in ("image/webp", "image/tiff", "application/octet-stream", "text/plain", ""):
        with pytest.raises(ExtractionError) as err:
            read(b"x", mime)
        assert err.value.code == "unsupported_mime" and err.value.extra["allowed"] == list(pipeline.LETTERHEAD_MIMES)
    with pytest.raises(ExtractionError) as err:
        read(b"%PDF-1.4 broken")
    assert err.value.code == "unreadable_pdf"


# --- A1-D5: the licence number is never returned ----------------------------------


@pytest.mark.parametrize(
    "professional",
    [
        "Psic. Ana Ejemplo Inventado",  # the licence on its own line under the name
        "Psic. Ana Ejemplo Inventado, Céd. Prof. 99999999",  # on the name's line
        "Psic. Ana Ejemplo Inventado Cédula 99999999",
        "Psic. Ana Ejemplo Inventado 99999999",  # with no cue at all
    ],
)
def test_no_field_of_a_result_carries_the_licence_number_or_other_printed_data(professional):
    pdf, truth = make_letter(professional=professional, letterhead_person=professional)
    result = read(pdf)
    assert result.issuer.professional.name == "Psic. Ana Ejemplo Inventado"
    text = json.dumps(result.model_dump(mode="json"), ensure_ascii=False)
    for leak in never_returned(truth):
        assert leak not in text, leak
    assert result.issuer.documentDate.date == truth.issued != truth.born
    # The block has no place a raw line could travel in: three fields, four keys each.
    issuer = result.model_dump(mode="json")["issuer"]
    assert set(issuer) == {"institution", "professional", "documentDate"}
    assert set(issuer["institution"]) == set(issuer["professional"]) == {"name", "confidence", "page", "bbox"}
    assert set(issuer["documentDate"]) == {"date", "confidence", "page", "bbox"}


def test_a_licence_in_the_letterhead_does_not_ride_on_the_institution_name():
    pdf, truth = make_letter(institution="Consultorio Ejemplo Inventado Cédula 99999999")
    result = read(pdf)
    assert result.issuer.institution.name == "Consultorio Ejemplo Inventado"
    assert truth.licence not in json.dumps(result.model_dump(mode="json"))


# --- doctrine 2 and A1-D2: rules only, no gateway, no network ----------------------


def test_the_lane_works_under_restricted_with_every_network_path_closed(monkeypatch):
    import httpx

    def closed(*args, **kwargs):
        raise AssertionError("the letterhead lane opened a connection")

    monkeypatch.setenv("SELVA_ENABLED", "true")  # even with the gateway switched on
    monkeypatch.setenv("SELVA_API_KEY", "not-a-real-key")
    monkeypatch.setattr(socket.socket, "connect", closed)
    monkeypatch.setattr(socket, "create_connection", closed)
    monkeypatch.setattr(socket, "getaddrinfo", closed)
    monkeypatch.setattr(httpx.Client, "send", closed)
    monkeypatch.setattr(httpx.AsyncClient, "send", closed)
    monkeypatch.setattr(pipeline, "get_settings", closed)  # nor does it read the inference settings
    pdf, truth = make_letter()
    for sensitivity in Sensitivity:
        result = read(pdf, sensitivity=sensitivity)
        assert result.document.classification == sensitivity
        assert result.issuer.professional.name == truth.professional
        assert result.provenance.engines.vlm is None


def _imports(module) -> set[str]:
    tree = ast.parse(inspect.getsource(module))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.add("." * node.level + (node.module or "").split(".")[0])
    return names


def test_the_lanes_code_cannot_reach_the_gateway_client():
    # The parser is pure: the standard library and its own package, nothing else.
    pure = {"__future__", "re", "statistics", "unicodedata", "dataclasses", "datetime", "..engines", ".common"}
    assert _imports(letterhead) == pure
    # The engine may start one local process and decode pixels; it imports no client.
    allowed = {
        "__future__", "functools", "importlib", "io", "os", "re", "shutil", "subprocess", "threading", "time",
        "collections", "contextlib", "pypdfium2", "PIL", ".text_layer",
    }  # fmt: skip
    assert _imports(ocr) == allowed
    clients = ("httpx", "requests", "urllib", "socket", "aiohttp", "celery", "redis", "get_settings", "selva")
    for source in (inspect.getsource(letterhead), inspect.getsource(ocr), inspect.getsource(pipeline._run_letterhead)):
        for word in clients:
            assert word not in source.lower(), word
    # The only processes the engine starts are the OCR binary: its version and one page.
    assert inspect.getsource(ocr).count("subprocess.run(") == 2


# --- doctrine 4: limits are error codes, never a partial answer --------------------


def test_scans_fail_closed_missing_engine_timeout_too_large_blank(monkeypatch):
    pdf, _ = make_letter()
    scan = as_scan(pdf, dpi=50)
    monkeypatch.setattr(ocr, "tesseract_path", lambda: None)
    for data, mime in ((scan, "image/png"), (as_scanned_pdf(pdf, dpi=50), "application/pdf")):
        with pytest.raises(ExtractionError) as err:
            read(data, mime)
        assert (err.value.code, err.value.status) == ("ocr_unavailable", 503)

    def timeout(image, deadline, dpi=None):
        raise ocr.OcrError("ocr_timeout", limit_seconds=ocr.BUDGET_SECONDS)

    monkeypatch.setattr(ocr, "_recognise", timeout)
    with pytest.raises(ExtractionError) as err:
        read(scan, "image/png")
    assert (err.value.code, err.value.extra) == ("ocr_timeout", {"limit_seconds": ocr.BUDGET_SECONDS})

    monkeypatch.setattr(ocr, "_recognise", lambda *a, **k: "level\tpage_num\n")  # the engine saw no word
    with pytest.raises(ExtractionError) as err:
        read(scan, "image/png")
    assert err.value.code == "unreadable_scan"

    monkeypatch.setattr(ocr, "MAX_IMAGE_PIXELS", 1000)
    with pytest.raises(ExtractionError) as err:
        read(scan, "image/png")
    assert (err.value.code, err.value.status) == ("image_too_large", 413)


# --- the OCR path, where the engine is installed -----------------------------------


@needs_ocr_engine
@pytest.mark.parametrize(("fmt", "mime"), [("PNG", "image/png"), ("JPEG", "image/jpeg")])
def test_a_scanned_image_yields_the_same_issuer_with_reduced_confidence(fmt, mime):
    pdf, truth = make_letter()
    text_result = read(pdf)
    result = read(as_scan(pdf, fmt=fmt), mime)
    issuer = result.issuer
    assert issuer.institution.name.startswith("Colegio Ficticio de Pruebas")
    assert issuer.professional.name == truth.professional
    assert issuer.documentDate.date == truth.issued
    for name in ("institution", "professional", "documentDate"):
        assert getattr(issuer, name).confidence < getattr(text_result.issuer, name).confidence
        x0, top, x1, bottom = getattr(issuer, name).bbox
        assert 0 <= x0 < x1 <= 1700 and 0 <= top < bottom <= 2200  # pixels of the upload
    assert result.provenance.engines.ocr.startswith("tesseract") and result.provenance.engines.text is None
    assert result.document.mimeType == mime and result.document.pages == 1
    assert "text read by OCR: confidences are reduced" in result.validation.warnings
    text = json.dumps(result.model_dump(mode="json"), ensure_ascii=False)
    for leak in never_returned(truth):
        assert leak not in text, leak


@needs_ocr_engine
def test_a_scanned_pdf_is_read_by_ocr_two_pages_at_most(monkeypatch):
    pdf, truth = make_letter(pages=3)
    calls = []
    real = ocr._recognise
    monkeypatch.setattr(ocr, "_recognise", lambda *a, **k: calls.append(1) or real(*a, **k))
    result = read(as_scanned_pdf(pdf))
    assert len(calls) == 2 and result.document.pages == 3
    assert result.issuer.institution.name.startswith("Colegio Ficticio de Pruebas")
    assert (result.issuer.professional.name, result.issuer.professional.page) == (truth.professional, 2)
    assert result.issuer.documentDate.date == truth.issued
    assert result.issuer.institution.bbox[2] <= 612  # PDF points again
    assert "pypdfium2" in result.provenance.engines.ocr
    assert "only the first 2 of 3 pages were read" in result.validation.warnings
    assert truth.licence not in json.dumps(result.model_dump(mode="json"))


# --- through the API ---------------------------------------------------------------


def post(client, token, data: bytes, mime: str = "application/pdf", **form):
    headers = {"Authorization": f"Bearer {token}", "X-Sensitivity": form.pop("sensitivity", "restricted")}
    return client.post("/v1/extractions", headers=headers, files={"file": ("doc", data, mime)}, data=form)


def test_the_api_selects_the_lane_by_document_type_and_keeps_only_metadata(client, token):
    from tlacuilo import db

    pdf, truth = make_letter()
    r = post(client, token, pdf, document_type="letterhead_document")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["document"]["type"] == "letterhead_document" and body["statement"] is None
    assert body["issuer"]["institution"]["name"] == truth.institution
    assert body["issuer"]["professional"]["name"] == truth.professional
    assert body["issuer"]["documentDate"]["date"] == truth.issued.isoformat() == date(2026, 11, 12).isoformat()
    job = client.get(f"/v1/extractions/{r.headers['X-Tlacuilo-Job']}", headers={"Authorization": f"Bearer {token}"})
    meta = job.json()
    assert meta["status"] == "done" and meta["documentType"] == "letterhead_document" and "result" not in meta
    assert set(meta) == {
        "id", "status", "mode", "documentType", "sensitivity", "sha256", "sizeBytes", "pages", "engines",
        "durationMs", "errorCode", "createdAt", "expiresAt", "now",
    }  # fmt: skip
    stored = json.dumps(meta, ensure_ascii=False)
    for content in (truth.institution, truth.professional, "Ejemplo", truth.licence, "issuer"):
        assert content not in stored  # doctrine 1: the job row holds no field of the document
    with db.session() as session:
        row = session.get(db.Job, r.headers["X-Tlacuilo-Job"])
        assert row.engines == {"text": body["provenance"]["engines"]["text"], "layout": None, "ocr": None, "vlm": None}


def test_the_api_keeps_auth_sensitivity_and_the_closed_enum_for_the_lane(client, token):
    pdf, _ = make_letter()
    files = {"file": ("doc", pdf, "application/pdf")}
    form = {"document_type": "letterhead_document"}
    r = client.post("/v1/extractions", headers={"X-Sensitivity": "restricted"}, files=files, data=form)
    assert r.status_code == 401 and r.json()["detail"]["error"] == "missing_bearer"
    r = client.post("/v1/extractions", headers={"Authorization": f"Bearer {token}"}, files=files, data=form)
    assert r.status_code == 400 and r.json()["detail"]["error"] == "missing_sensitivity"
    for unknown in ("letterhead", "letter", "LETTERHEAD_DOCUMENT", "memo"):
        r = post(client, token, pdf, document_type=unknown)
        assert r.status_code == 400 and r.json()["detail"]["error"] == "invalid_document_type"


def test_the_api_reads_a_long_letter_that_the_statement_lane_refuses(client, token):
    pdf, truth = make_letter(pages=12)
    r = post(client, token, pdf)  # no document type: sync mode's page limit, as before
    assert r.status_code == 413 and r.json()["error"] == "too_many_pages"
    r = post(client, token, pdf, document_type="letterhead_document")
    assert r.status_code == 200 and r.json()["issuer"]["institution"]["name"] == truth.institution
    assert r.json()["validation"]["warnings"] == ["only the first 2 of 12 pages were read"]


def test_the_api_reports_a_scan_it_cannot_read_as_an_error_code_and_a_failed_job(client, token, monkeypatch):
    monkeypatch.setattr(ocr, "tesseract_path", lambda: None)
    pdf, _ = make_letter()
    r = post(client, token, as_scan(pdf, dpi=50), "image/png", document_type="letterhead_document")
    assert r.status_code == 503 and r.json()["error"] == "ocr_unavailable"
    job = client.get(f"/v1/extractions/{r.json()['job']}", headers={"Authorization": f"Bearer {token}"}).json()
    assert (job["status"], job["errorCode"]) == ("failed", "ocr_unavailable")
    r = post(client, token, b"GIF89a", "image/gif", document_type="letterhead_document")
    assert r.status_code == 422 and r.json()["error"] == "unsupported_mime"


@needs_ocr_engine
def test_the_api_reads_a_photo_through_ocr(client, token):
    pdf, truth = make_letter()
    r = post(client, token, as_scan(pdf, fmt="JPEG"), "image/jpeg", document_type="letterhead_document")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["issuer"]["professional"]["name"] == truth.professional
    assert body["provenance"]["engines"]["ocr"].startswith("tesseract")
    assert truth.licence not in r.text
