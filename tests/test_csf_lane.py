"""The csf lane on born-digital synthetic constancias: every field, the tables, the QR
code and the seal, the interleaved header, the contract, the API, and what is never
kept or logged. The OCR path is `test_csf_ocr.py`."""

import ast
import io
import json
import logging
import pathlib

import pytest
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

import tlacuilo.pipeline as pipeline
from tlacuilo import db
from tlacuilo.contract import DocumentType, ExtractionResult, Sensitivity, json_schema
from tlacuilo.csf_contract import FIELD_NAMES, LOW_CONFIDENCE_BELOW, CsfQr
from tlacuilo.engines import ocr, scan, text_layer
from tlacuilo.logging import configure_logging
from tlacuilo.parsers import csf
from tlacuilo.pipeline import ExtractionError, run_extraction

from .csf_accuracy import score
from .synth import make_statement
from .synth_csf import KINDS, make_csf, truth_for
from .synth_letter import make_letter

ROOT = pathlib.Path(__file__).resolve().parents[1]


def read(data: bytes, mime: str = "application/pdf", reader: str = "auto") -> ExtractionResult:
    return run_extraction(data, mime, Sensitivity.restricted, doc_type_hint=DocumentType.csf, reader=reader)


@pytest.fixture(scope="module")
def results():
    out = {}
    for kind in KINDS:
        pdf, truth = make_csf(kind)
        out[kind] = (read(pdf), truth, pdf)
    return out


# --- every field, by region -----------------------------------------------------


@pytest.mark.parametrize("kind", KINDS)
def test_every_field_of_a_text_constancia_is_read_exactly(results, kind):
    result, truth, _ = results[kind]
    assert result.document.type == DocumentType.csf and result.document.pages == 3
    assert result.provenance.parser == csf.PARSER_ID and result.provenance.engines.text
    assert score(result, truth) == {name: "correct" for name in FIELD_NAMES}
    block = result.csf
    assert block.legalForm.value == truth.legal_form
    assert block.quality.source == "text_layer" and block.quality.verdict == "good"
    assert block.quality.unreadFields == [] and block.quality.lowConfidenceFields == []
    for name in FIELD_NAMES:
        field = getattr(block.fields, name)
        if field.value:
            assert field.source in ("text_layer", "qr", "seal") and field.page in (1, 2) and field.bbox
            assert field.confidence >= 0.85 and not field.lowConfidence


@pytest.mark.parametrize("kind", KINDS)
def test_the_three_tables_are_read_by_column(results, kind):
    block, truth = results[kind][0].csf, results[kind][1]
    assert block.regimenesStatus == block.economicActivitiesStatus == block.obligationsStatus == "read"
    assert [(r.name, r.startDate.isoformat(), r.endDate.isoformat() if r.endDate else "") for r in block.regimenes] == [
        tuple(row) for row in truth.regimenes
    ]
    assert [(a.order, a.name, a.percentage) for a in block.economicActivities] == [
        (o, n, p) for o, n, p, _s, _e in truth.activities
    ]
    assert [(o.description, o.dueRule) for o in block.obligations] == [(d, v) for d, v, _s, _e in truth.obligations]
    wrapped = [o for o in block.obligations if "value_wrapped" in o.checks]
    assert wrapped and all(0.8 <= o.confidence < 0.9 for o in wrapped)  # rejoined rows are said to be


def test_an_ended_regimen_keeps_its_end_date(results):
    block = results["pf_ended"][0].csf
    ended = [r for r in block.regimenes if r.endDate]
    assert [(r.name, r.endDate.isoformat()) for r in ended] == [
        ("Régimen de Sueldos y Salarios e Ingresos Asimilados a Salarios", "2021-12-31")
    ]


def test_the_interleaved_emission_phrase_is_read_from_its_column(results):
    # The text layer puts the phrase's halves on lines with left-block words between
    # them; a line reader sees «… A 12 DE» and «social NOVIEMBRE DE 2026».
    _, truth, pdf = results["pf_resico"]
    lines = [line.text for line in text_layer.extract_pages(pdf)[0].lines]
    assert any(line.startswith("social NOVIEMBRE DE 2026") for line in lines)
    fields = results["pf_resico"][0].csf.fields
    assert fields.emissionDate.value == truth.fields["emissionDate"] == "2026-11-12"
    assert fields.emissionPlace.value == "MUNICIPIO EJEMPLO, ESTADO DE PRUEBA"
    assert "seal_agrees" in fields.emissionDate.checks


@pytest.mark.parametrize("kind", KINDS)
def test_the_qr_code_and_the_seal_are_decoded_and_cross_check(results, kind):
    block, truth = results[kind][0].csf, results[kind][1]
    assert block.qr.status == "decoded" and block.qr.url == truth.qr_url and block.qr.satHost is True
    assert (block.qr.idCif, block.qr.rfc) == (truth.fields["idCif"], truth.fields["rfc"])
    assert block.qr.parameters == {"D1": "10", "D2": "1", "D3": f"{truth.fields['idCif']}_{truth.fields['rfc']}"}
    assert block.qr.page == 1 and block.qr.bbox[0] < 150  # the code sits left of the cédula block
    seal = (block.seal.status, block.seal.timestamp, block.seal.rfc)
    assert seal == ("read", truth.seal_timestamp, truth.fields["rfc"])
    assert block.seal.page == 2
    assert "qr_agrees" in block.fields.rfc.checks and "qr_agrees" in block.fields.idCif.checks


def test_a_field_that_disagrees_with_the_qr_code_drops_below_the_line():
    pdf, truth = make_csf("pf_resico")
    pages = text_layer.extract_pages(pdf)
    other = f"{csf.read_qr([(1, truth.qr_url, None)]).url[:-13]}XAXP900101AB9"  # a QR for another RFC
    block = csf.read_csf(pages, [(1, other, None)], ocr=False, page_quality=[])
    assert block.fields.rfc.value == truth.fields["rfc"]
    assert "qr_disagrees" in block.fields.rfc.checks and block.fields.rfc.lowConfidence
    assert block.fields.rfc.confidence <= 0.3 < LOW_CONFIDENCE_BELOW


def test_a_value_only_the_qr_carries_fills_the_field_from_the_qr():
    pdf, truth = make_csf("pm_general")
    pages = text_layer.extract_pages(pdf)
    for page in pages:  # the idCIF line is gone (printed inside an image, say)
        page.lines = [ln for ln in page.lines if "idCIF" not in ln.text]
    block = csf.read_csf(pages, [(1, truth.qr_url, None)], ocr=False, page_quality=[])
    assert (block.fields.idCif.value, block.fields.idCif.source) == (truth.fields["idCif"], "qr")
    assert block.fields.idCif.confidence == 0.9 and "qr_agrees" in block.fields.idCif.checks


def test_a_qr_that_is_not_the_sats_is_returned_as_text_and_never_parsed_for_identity():
    qr = csf.read_qr([(1, "https://example.invalid/x?D3=19999999901_XAXP900101AB1", [1, 2, 3, 4])])
    assert qr.status == "decoded" and qr.satHost is False and qr.rfc == "XAXP900101AB1"
    assert csf.read_qr([(1, "not a url", None)]).status == "not_found"
    with pytest.raises(Exception):
        CsfQr(status="decoded", parameters={f"k{i}": "v" for i in range(11)}, confidence=1)


def test_nothing_in_the_lane_opens_a_connection_or_reads_settings():
    for path in ("tlacuilo/parsers/csf.py", "tlacuilo/parsers/csf_layout.py", "tlacuilo/parsers/csf_tables.py",
                 "tlacuilo/engines/scan.py", "tlacuilo/csf_contract.py"):  # fmt: skip
        tree = ast.parse((ROOT / path).read_text())
        imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        imported |= {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        assert not imported & {"httpx", "socket", "urllib.request", "requests", "settings", "..settings"}, path
        assert "selva" not in (ROOT / path).read_text().lower(), path


# --- refusals ---------------------------------------------------------------------


def test_a_document_that_is_not_a_constancia_is_refused_with_a_code():
    pdf, _ = make_letter()
    with pytest.raises(ExtractionError) as err:
        read(pdf)
    assert err.value.code == "not_a_csf" and set(err.value.extra) == {"pages"}
    statement, _ = make_statement()
    with pytest.raises(ExtractionError) as err:
        read(statement)
    assert err.value.code == "not_a_csf"


def test_closed_mimes_and_readers():
    pdf, _ = make_csf()
    with pytest.raises(ExtractionError) as err:
        read(pdf, "image/gif")
    assert err.value.code == "unsupported_mime" and err.value.extra["allowed"] == list(pipeline.CSF_MIMES)
    assert pipeline.CSF_MIMES[:3] == ("application/pdf", "image/png", "image/jpeg")
    with pytest.raises(ExtractionError) as err:
        read(pdf, reader="vlm")
    assert (err.value.code, err.value.status) == ("invalid_reader", 400)
    with pytest.raises(ExtractionError) as err:
        read(b"%PDF-1.4 broken")
    assert err.value.code == "unreadable_pdf"


def test_heic_photos_are_accepted_when_the_decoder_is_installed():
    if not scan.HEIF:
        pytest.skip("pi-heif is not installed")
    assert {"image/heic", "image/heif"} <= set(pipeline.CSF_MIMES)
    with pytest.raises(ExtractionError) as err:
        read(b"\x00\x00\x00\x18ftypheic" + b"0" * 64, "image/heic")
    assert err.value.code == "unreadable_image"


def test_a_scan_with_a_scanner_apps_invisible_text_is_read_by_ocr_not_trusted(monkeypatch):
    # A full-page picture with invisible text over it (iOS / Adobe scanning apps):
    # that text is a reading of a reading, so the lane OCRs the picture instead.
    pdf, _ = make_csf()
    from .synth_csf import render_page

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    from reportlab.lib.utils import ImageReader

    c.drawImage(ImageReader(render_page(pdf, 1, 100)), 0, 0, width=612, height=792)
    text = c.beginText(40, 700)
    text.setTextRenderMode(3)  # invisible
    for line in text_layer.extract_pages(pdf)[0].lines:
        text.textLine(line.text)
    c.drawText(text)
    c.save()
    assert text_layer.has_text_layer(text_layer.extract_pages(buf.getvalue()))
    assert text_layer.full_page_images(buf.getvalue(), 4)
    called = {}

    def fake_scan(data, limit):
        called["ocr"] = True
        raise ocr.OcrError("ocr_unavailable", status=503, reason="engine_missing")

    monkeypatch.setattr(scan, "scan_pdf", fake_scan)
    with pytest.raises(ExtractionError) as err:
        read(buf.getvalue())
    assert called == {"ocr": True} and err.value.code == "ocr_unavailable"
    assert not text_layer.full_page_images(pdf, 4)  # a born-digital constancia's images are small


# --- the contract -------------------------------------------------------------------


def test_the_csf_block_is_additive_and_closed():
    schema = json_schema()
    assert schema["title"] == "document-extraction/v1" and "csf" not in schema["required"]
    defs = schema["$defs"]
    assert "csf" in defs["DocumentType"]["enum"]
    assert set(defs["CsfFields"]["properties"]) == set(FIELD_NAMES)
    assert set(defs["CsfField"]["properties"]) == {
        "value", "status", "confidence", "lowConfidence", "source", "page", "bbox", "checks"
    }  # fmt: skip
    for model in ("Csf", "CsfField", "CsfFields", "CsfQr", "CsfSeal", "CsfQuality", "CsfRegimen"):
        assert defs[model]["additionalProperties"] is False
    statement = run_extraction(make_statement()[0], "application/pdf", Sensitivity.restricted)
    assert "csf" not in statement.model_dump(mode="json")  # every other lane keeps its keys


def test_a_result_round_trips_through_its_json(results):
    result = results["pm_general"][0]
    body = json.loads(json.dumps(result.model_dump(mode="json")))
    assert list(body) == ["contract", "document", "provenance", "statement", "csf", "validation", "fields"]
    assert ExtractionResult.model_validate(body) == result


# --- custody: nothing durable, nothing logged ---------------------------------------


def test_the_lane_logs_counts_and_codes_never_a_value(caplog, results):
    pdf, truth = make_csf("pf_ended")
    configure_logging()
    with caplog.at_level(logging.DEBUG):
        read(pdf)
        with pytest.raises(ExtractionError):
            read(make_letter()[0])
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "type=csf" in text and "source=text" in text and "verdict=good" in text
    for content in truth.sensitive:
        assert content not in text, content


def test_the_api_reads_a_constancia_and_keeps_no_content(client, token):
    pdf, truth = make_csf("pm_general")
    headers = {"Authorization": f"Bearer {token}", "X-Sensitivity": "restricted"}
    files = {"file": ("constancia.pdf", pdf, "application/pdf")}
    r = client.post("/v1/extractions", headers=headers, files=files, data={"document_type": "csf"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["csf"]["fields"]["rfc"]["value"] == truth.fields["rfc"] and "issuer" not in body
    job = r.headers["X-Tlacuilo-Job"]
    with db.session() as session:
        row = session.get(db.Job, job).public()
    assert row["documentType"] == "csf" and row["status"] == "done"
    assert not any(value in json.dumps(row) for value in truth.sensitive)
    r = client.post("/v1/extractions", headers=headers, files=files, data={"reader": "ocr"})
    assert r.status_code == 400 and r.json()["detail"]["error"] == "invalid_reader"  # csf lane only
    r = client.post("/v1/extractions", headers=headers, files=files, data={"document_type": "csf", "reader": "x"})
    assert r.status_code == 400
    letter_pdf, _ = make_letter()
    r = client.post("/v1/extractions", headers=headers, files={"file": ("x.pdf", letter_pdf, "application/pdf")},
                    data={"document_type": "csf"})  # fmt: skip
    assert r.status_code == 422 and r.json()["error"] == "not_a_csf"


def test_truth_is_synthetic_and_says_so():
    for kind in KINDS:
        truth = truth_for(kind)
        assert truth.fields["rfc"][:2] in {"XA", "XB", "XC"} and set(truth.fields["idCif"][1:-2]) == {"9"}
        assert truth.fields["postalCode"] == "99999"
