"""The csf lane's OCR path: page preparation and geometry without the engine; scans and
photos of synthetic constancias where Tesseract with Spanish data is installed.

The promise measured here: on a clean scan every field is read; on a degraded one a
field that is not read correctly is FLAGGED (low confidence, invalid, blank-but-low or
not found) and never returned wrong with confidence (`csf_accuracy.score`)."""

import io
import logging
import math

import pytest
from PIL import Image, ImageDraw, ImageFont

from tlacuilo.contract import DocumentType, Sensitivity
from tlacuilo.csf_contract import FIELD_NAMES
from tlacuilo.engines import ocr, scan
from tlacuilo.logging import configure_logging
from tlacuilo.pipeline import ExtractionError, run_extraction

from .conftest import needs_ocr_engine
from .csf_accuracy import score
from .synth_csf import KINDS, degrade, make_csf, page_image, render_page, scanned_pdf


def read(data: bytes, mime: str = "application/pdf", reader: str = "auto"):
    return run_extraction(data, mime, Sensitivity.restricted, doc_type_hint=DocumentType.csf, reader=reader)


def text_page(angle: float = 0.0, size=(1700, 2200)) -> Image.Image:
    img = Image.new("L", size, 255)
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default(size=34)
    for row in range(30):
        draw.text((120, 150 + row * 60), "Renglón sintético de prueba número " + str(row), font=font, fill=0)
    return img.rotate(angle, expand=True, fillcolor=255, resample=Image.Resampling.BICUBIC) if angle else img


# --- geometry and preparation, no engine needed ----------------------------------


def test_a_point_survives_a_turn_and_its_inverse():
    img = Image.new("L", (400, 300), 255)
    for angle in (90, 180, 270, 3.5, -4.0):
        turned, back = scan._rotate(img, angle, scan.Affine())
        t = math.radians(angle)
        # The image centre maps to the image centre, and a point keeps its distance to it.
        cx, cy = back(turned.width / 2, turned.height / 2)
        assert abs(cx - 200) < 1 and abs(cy - 150) < 1
        x, y = back(turned.width / 2 + 100 * math.cos(t), turned.height / 2 - 100 * math.sin(t))
        assert abs(x - 300) < 1.5 and abs(y - 150) < 1.5, angle


def test_skew_is_measured_and_a_level_or_empty_page_is_left_alone():
    assert scan.estimate_skew(text_page()) == 0.0
    assert scan.estimate_skew(Image.new("L", (1700, 2200), 255)) == 0.0
    for angle in (2.0, -3.5):
        assert abs(scan.estimate_skew(text_page(angle)) + angle) <= 0.3


def test_a_small_photo_is_enlarged_and_a_huge_one_is_reduced_for_the_engine():
    _, back, adj = scan.prepare(text_page(size=(800, 1000)))
    assert adj.upscale == scan.MAX_UPSCALE and back(100, 100) == pytest.approx((50, 50))
    big, _, adj = scan.prepare(text_page(size=(5000, 6500)))
    assert max(big.size) == scan.WORK_LONG_EDGE and adj.upscale < 1


def test_the_qr_code_is_decoded_from_pixels_and_never_fetched(monkeypatch):
    import socket

    monkeypatch.setattr(socket, "socket", lambda *a, **k: pytest.fail("no connection may be opened"))
    pdf, truth = make_csf()
    found = scan.decode_qr(degrade(render_page(pdf, 1, 200), "photo").convert("L"))
    assert [text for text, _box in found] == [truth.qr_url]
    assert scan.qr_from_pdf(pdf)[1][0].text == truth.qr_url


def test_an_engine_failure_is_an_error_code_and_carries_no_text(monkeypatch):
    pdf, truth = make_csf()
    monkeypatch.setattr(ocr, "tesseract_path", lambda: None)
    uploads = ((scanned_pdf(pdf, "scan", pages=1), "application/pdf"), (page_image(pdf, 1, "scan"), "image/jpeg"))
    for data, mime in uploads:
        with pytest.raises(ExtractionError) as err:
            read(data, mime)
        assert (err.value.code, err.value.status) == ("ocr_unavailable", 503)
        assert err.value.extra == {"reason": "engine_missing"}  # a code and a class, never text


def test_the_budget_and_the_slot_are_the_engines(monkeypatch):
    pdf, _ = make_csf()
    with pytest.raises(ocr.OcrError) as err:
        scan.scan_pdf(scanned_pdf(pdf, "scan", pages=1), budget_seconds=0)
    assert err.value.code in {"ocr_timeout", "ocr_busy"}
    assert ocr._slot.acquire(timeout=1)
    try:
        with pytest.raises(ocr.OcrError) as err:
            scan.scan_image(page_image(pdf, 1, "scan"), "image/jpeg", budget_seconds=0.05)
        assert err.value.code == "ocr_busy"
    finally:
        ocr._slot.release()
    monkeypatch.setattr(ocr, "MAX_IMAGE_PIXELS", 100)
    with pytest.raises(ocr.OcrError) as err:
        scan.scan_image(page_image(pdf, 1, "scan"), "image/jpeg")
    assert err.value.code == "image_too_large"


# --- the engine, end to end --------------------------------------------------------


@needs_ocr_engine
@pytest.mark.parametrize("kind", KINDS)
def test_a_clean_scan_reads_every_field(kind):
    pdf, truth = make_csf(kind)
    result = read(scanned_pdf(pdf, "clean"))
    assert result.csf.quality.source == "ocr" and result.provenance.engines.ocr.startswith("tesseract")
    verdicts = score(result, truth)
    assert verdicts == {name: "correct" for name in FIELD_NAMES}, {k: v for k, v in verdicts.items() if v != "correct"}
    for name in FIELD_NAMES:
        field = getattr(result.csf.fields, name)
        if field.value and field.source == "ocr":
            assert field.bbox and field.page in (1, 2)
    assert result.csf.qr.status == "decoded" and result.csf.seal.timestamp == truth.seal_timestamp
    assert result.csf.regimenesStatus == "read" and len(result.csf.regimenes) == len(truth.regimenes)
    assert "text read by OCR: confirm every value against the document" in result.validation.warnings


@needs_ocr_engine
@pytest.mark.parametrize("level", ["scan", "photo", "poor_photo", "upside_down"])
def test_a_degraded_scan_is_never_wrong_with_confidence(level):
    for kind in ("pf_resico", "pm_general"):
        pdf, truth = make_csf(kind)
        try:
            result = read(scanned_pdf(pdf, level))
        except ExtractionError as err:  # a refusal is an honest answer too, never a guess
            assert err.code in {"ocr_timeout", "unreadable_scan", "not_a_csf"}
            continue
        verdicts = score(result, truth)
        wrong = [name for name, v in verdicts.items() if v == "WRONG"]
        assert wrong == [], (kind, level, wrong)
        # The QR code survives what the text does not: the RFC and idCIF are always there.
        assert result.csf.qr.status == "decoded"
        assert result.csf.fields.rfc.value == truth.fields["rfc"]


def _osd_installed() -> bool:
    import subprocess

    binary = ocr.tesseract_path()
    if binary is None:
        return False
    listed = subprocess.run([binary, "--list-langs"], capture_output=True, timeout=10, check=False)
    return b"osd" in (listed.stdout + listed.stderr).split()


@needs_ocr_engine
@pytest.mark.skipif(not _osd_installed(), reason="tesseract orientation data (osd) is not installed")
def test_an_upside_down_scan_is_turned_and_its_boxes_land_where_the_ink_is():
    pdf, truth = make_csf("pf_resico")
    upright = read(pdf).csf.fields.curp.bbox
    result = read(scanned_pdf(pdf, "upside_down"))
    assert result.csf.quality.pages[0].rotated == 180
    curp = result.csf.fields.curp
    assert curp.value == truth.fields["curp"]
    # Upside down, the CURP sits at (612 − x, 792 − y) of where it is printed upright.
    cx, cy = (curp.bbox[0] + curp.bbox[2]) / 2, (curp.bbox[1] + curp.bbox[3]) / 2
    ux, uy = (upright[0] + upright[2]) / 2, (upright[1] + upright[3]) / 2
    assert abs(cx - (612 - ux)) < 20 and abs(cy - (792 - uy)) < 20


@needs_ocr_engine
def test_a_photo_of_page_one_reads_the_identity_and_says_the_rest_was_not_there():
    pdf, truth = make_csf("pf_resico")
    result = read(page_image(pdf, 1, "photo"), "image/jpeg")
    fields = result.csf.fields
    assert result.document.pages == 1 and result.csf.quality.pagesRead == 1
    assert fields.rfc.value == truth.fields["rfc"] and fields.idCif.value == truth.fields["idCif"]
    assert result.csf.regimenesStatus == "not_found" and "regimenes" in result.csf.quality.unreadFields
    assert "andStreet" in result.csf.quality.unreadFields  # printed on page 2, which was not sent
    assert fields.andStreet.status == "not_found" and fields.andStreet.value is None


@needs_ocr_engine
def test_a_second_reading_of_a_text_pdf_comes_from_ocr():
    pdf, truth = make_csf("pm_general")
    result = read(pdf, reader="ocr")
    assert result.csf.quality.source == "ocr" and result.provenance.engines.ocr
    assert result.csf.fields.rfc.value == truth.fields["rfc"]


@needs_ocr_engine
def test_ocr_logs_and_errors_carry_no_content(caplog):
    pdf, truth = make_csf("pf_ended")
    configure_logging()
    with caplog.at_level(logging.DEBUG):
        read(scanned_pdf(pdf, "photo"))
        with pytest.raises(ExtractionError) as err:
            buf = io.BytesIO()
            Image.new("L", (900, 1200), 255).save(buf, "PNG")
            read(buf.getvalue(), "image/png")
    assert err.value.code in {"unreadable_scan", "not_a_csf"}
    assert not any(v in str(err.value.extra) for v in truth.sensitive)
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "source=ocr" in text
    for content in truth.sensitive:
        assert content not in text, content
