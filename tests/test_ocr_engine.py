"""The OCR engine of the letterhead lane: TSV parsing, limits and error codes without
the binary; the real engine where Tesseract with Spanish data is installed."""

import io
import subprocess

import pytest
from PIL import Image

from tlacuilo.engines import ocr

from .conftest import needs_ocr_engine
from .synth_letter import as_scan, as_scanned_pdf, make_letter

HEADER = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"


def tsv(*rows: tuple) -> str:
    """rows: (block, par, line, left, top, width, height, conf, text)"""
    out = [HEADER, "1\t1\t0\t0\t0\t0\t0\t0\t2000\t2600\t-1\t"]
    for n, (block, par, ln, left, top, width, height, conf, text) in enumerate(rows, start=1):
        out.append(f"5\t1\t{block}\t{par}\t{ln}\t{n}\t{left}\t{top}\t{width}\t{height}\t{conf}\t{text}")
    return "\n".join(out) + "\n"


def png(width: int = 40, height: int = 20, mode: str = "L") -> bytes:
    buf = io.BytesIO()
    fill = {"L": 255, "RGB": (255, 255, 255), "RGBA": (0, 0, 0, 0)}[mode]
    Image.new(mode, (width, height), fill).save(buf, "PNG")
    return buf.getvalue()


def test_tsv_words_become_lines_with_boxes_and_confidence():
    lines = ocr.lines_from_tsv(
        tsv(
            (1, 1, 1, 100, 50, 200, 40, 96.5, "Colegio"),
            (1, 1, 1, 320, 52, 220, 38, 91.0, "Ficticio"),
            (2, 1, 1, 100, 400, 90, 30, 88.0, "Atentamente"),
        ),
        page=1,
    )
    assert [ln.text for ln in lines] == ["Colegio Ficticio", "Atentamente"]
    first = lines[0].words[0]
    assert (first.x0, first.x1, first.top, first.bottom, first.conf) == (100.0, 300.0, 50.0, 90.0, 0.965)
    assert lines[0].page == 1 and lines[0].bbox == [100.0, 50.0, 540.0, 90.0]


def test_two_columns_at_one_height_are_one_visual_row_like_a_text_layer():
    lines = ocr.lines_from_tsv(
        tsv(
            (2, 1, 1, 1200, 102, 300, 40, 90.0, "derecha"),  # another block, same height, listed first
            (1, 1, 1, 100, 100, 300, 40, 90.0, "izquierda"),
            (1, 1, 2, 100, 160, 300, 40, 90.0, "abajo"),
        ),
        page=2,
    )
    assert [ln.text for ln in lines] == ["izquierda derecha", "abajo"]
    assert all(ln.page == 2 for ln in lines)


def test_boxes_scale_from_pixels_to_page_units_and_rubbish_rows_are_dropped():
    text = tsv((1, 1, 1, 200, 100, 400, 50, 95.0, "Hospital"), (1, 1, 1, 700, 100, 50, 50, -1, ""))
    text += "5\t1\t1\t1\t1\t9\tx\ty\t1\t1\t90\tmal\n" + "no es una fila\n"
    (only,) = ocr.lines_from_tsv(text, page=1, kx=0.5, ky=0.25)
    (word,) = only.words
    assert (word.text, word.x0, word.x1, word.top, word.bottom) == ("Hospital", 100.0, 300.0, 25.0, 37.5)
    assert ocr.lines_from_tsv(HEADER + "\n", page=1) == []


def test_the_child_process_gets_no_service_configuration(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgres://example.invalid/x")
    monkeypatch.setenv("SELVA_API_KEY", "not-a-real-key")
    env = ocr._env()
    assert set(env) <= {"PATH", "OMP_THREAD_LIMIT", "LC_ALL", "TESSDATA_PREFIX"}
    assert env["OMP_THREAD_LIMIT"] == "1"


def test_only_png_and_jpeg_images_are_accepted():
    for mime in ("image/webp", "image/tiff", "image/gif", "application/octet-stream"):
        with pytest.raises(ocr.OcrError) as err:
            ocr.image_pages(png(), mime)
        assert err.value.code == "unsupported_mime"
    with pytest.raises(ocr.OcrError) as err:
        ocr.image_pages(png(), "image/jpeg")  # a PNG that claims to be a JPEG
    assert err.value.code == "unreadable_image"
    with pytest.raises(ocr.OcrError) as err:
        ocr.image_pages(b"\x89PNG\r\n\x1a\n" + b"0" * 64, "image/png")
    assert err.value.code == "unreadable_image"


def test_an_image_over_the_pixel_cap_is_an_error_before_it_is_decoded(monkeypatch):
    monkeypatch.setattr(ocr, "MAX_IMAGE_PIXELS", 100)
    monkeypatch.setattr(ocr, "_recognise", lambda *a, **k: pytest.fail("the engine must not run"))
    with pytest.raises(ocr.OcrError) as err:
        ocr.image_pages(png(20, 20), "image/png")
    assert (err.value.code, err.value.status) == ("image_too_large", 413)
    assert err.value.extra == {"pixels": 400, "limit_pixels": 100}


def test_a_missing_engine_is_an_error_code_not_an_empty_page(monkeypatch):
    monkeypatch.setattr(ocr, "tesseract_path", lambda: None)
    with pytest.raises(ocr.OcrError) as err:
        ocr.image_pages(png(), "image/png")
    assert (err.value.code, err.value.status) == ("ocr_unavailable", 503)
    assert ocr.available() is False and ocr.engine_label() == "tesseract (spa)"


def test_the_wall_clock_budget_is_hard(monkeypatch):
    monkeypatch.setattr(ocr, "tesseract_path", lambda: "/nonexistent/tesseract")

    def too_slow(cmd, **kwargs):
        assert kwargs["timeout"] <= ocr.BUDGET_SECONDS and kwargs["input"][:4] == b"\x89PNG"
        raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])

    monkeypatch.setattr(ocr.subprocess, "run", too_slow)
    with pytest.raises(ocr.OcrError) as err:
        ocr.image_pages(png(), "image/png")
    assert err.value.code == "ocr_timeout"
    with pytest.raises(ocr.OcrError) as err:  # nothing left of the budget: the engine is not even started
        ocr.image_pages(png(), "image/png", budget_seconds=0)
    assert err.value.code in {"ocr_timeout", "ocr_busy"}


def test_a_second_job_waits_for_the_slot_and_gives_up_inside_the_budget():
    assert ocr._slot.acquire(timeout=1)
    try:
        with pytest.raises(ocr.OcrError) as err:
            ocr.image_pages(png(), "image/png", budget_seconds=0.05)
        assert (err.value.code, err.value.status) == ("ocr_busy", 503)
    finally:
        ocr._slot.release()


def test_an_engine_failure_reports_its_class_and_never_its_output(monkeypatch):
    monkeypatch.setattr(ocr, "tesseract_path", lambda: "/nonexistent/tesseract")

    def fails(stderr: bytes):
        return lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, b"", stderr)

    monkeypatch.setattr(ocr.subprocess, "run", fails(b"Error opening data file /x/spa.traineddata"))
    with pytest.raises(ocr.OcrError) as err:
        ocr.image_pages(png(), "image/png")
    assert (err.value.code, err.value.extra) == ("ocr_unavailable", {"reason": "language_data_missing"})
    monkeypatch.setattr(ocr.subprocess, "run", fails(b"anything the engine printed"))
    with pytest.raises(ocr.OcrError) as err:
        ocr.image_pages(png(), "image/png")
    assert (err.value.code, err.value.extra) == ("ocr_failed", {"reason": "exit_1"})


def test_a_large_image_is_shrunk_for_the_engine_and_boxes_return_in_its_own_pixels(monkeypatch):
    seen = {}

    def fake(image, deadline, dpi=None):
        seen["size"] = image.size
        return tsv((1, 1, 1, 1300, 650, 260, 65, 90.0, "Colegio"))

    monkeypatch.setattr(ocr, "_recognise", fake)
    (page,) = ocr.image_pages(png(5200, 2600), "image/png")
    assert seen["size"] == (2600, 1300)  # the long edge the engine sees is capped
    assert (page.width, page.height) == (5200.0, 2600.0)
    assert page.lines[0].bbox == [2600.0, 1300.0, 3120.0, 1430.0]  # back in pixels of the upload


def test_a_photo_taken_sideways_is_read_upright_and_boxed_as_displayed(monkeypatch):
    seen = {}

    def fake(image, deadline, dpi=None):
        seen["size"] = image.size
        return tsv((1, 1, 1, 10, 300, 100, 20, 90.0, "Colegio"))

    monkeypatch.setattr(ocr, "_recognise", fake)
    exif = Image.Exif()
    exif[0x0112] = 6  # the camera was held sideways: display rotated a quarter turn
    buf = io.BytesIO()
    Image.new("RGB", (400, 200), (255, 255, 255)).save(buf, "JPEG", exif=exif)
    (page,) = ocr.image_pages(buf.getvalue(), "image/jpeg")
    assert seen["size"] == (200, 400) and (page.width, page.height) == (200.0, 400.0)
    assert page.lines[0].bbox == [10.0, 300.0, 110.0, 320.0]


def test_a_transparent_png_is_read_on_white(monkeypatch):
    seen = {}

    def fake(image, deadline, dpi=None):
        seen["mode"], seen["corner"] = image.mode, image.getpixel((0, 0))
        return tsv((1, 1, 1, 1, 1, 5, 5, 90.0, "x"))

    monkeypatch.setattr(ocr, "_recognise", fake)
    ocr.image_pages(png(30, 30, "RGBA"), "image/png")  # fully transparent: black if the alpha were dropped
    assert seen == {"mode": "L", "corner": 255}


def test_a_scanned_pdf_is_rasterised_two_pages_at_most_and_boxes_are_in_points(monkeypatch):
    pdf, _ = make_letter(pages=3)
    calls = []

    def fake(image, deadline, dpi=None):
        calls.append((image.size, image.mode, dpi))
        return tsv((1, 1, 1, 850, 1100, 170, 44, 90.0, "centro"))

    monkeypatch.setattr(ocr, "_recognise", fake)
    pages = ocr.pdf_pages(pdf, limit=5)
    assert [p.number for p in pages] == [1, 2] and len(calls) == ocr.MAX_PAGES == 2
    assert calls[0] == ((1700, 2200), "L", 200)
    assert (pages[0].width, pages[0].height) == (612.0, 792.0)
    assert pages[0].lines[0].bbox == [306.0, 396.0, 367.2, 411.84]
    with pytest.raises(ocr.OcrError) as err:
        ocr.pdf_pages(b"%PDF-1.4 not a pdf")
    assert err.value.code == "unreadable_pdf"


@needs_ocr_engine
def test_the_engine_reads_a_scanned_letter_image():
    pdf, truth = make_letter()
    (page,) = ocr.image_pages(as_scan(pdf), "image/png")
    assert (page.width, page.height) == (1700.0, 2200.0)
    text = "\n".join(ln.text for ln in page.lines)
    assert "Colegio Ficticio de Pruebas" in text and "Ana Ejemplo Inventado" in text
    assert all(w.conf is not None and 0 <= w.conf <= 1 for ln in page.lines for w in ln.words)
    assert ocr.engine_label().startswith("tesseract ") and "(spa)" in ocr.engine_label()


@needs_ocr_engine
def test_the_engine_reads_a_scanned_pdf_in_points():
    pdf, _ = make_letter(pages=2)
    pages = ocr.pdf_pages(as_scanned_pdf(pdf))
    assert [p.number for p in pages] == [1, 2]
    head = next(ln for ln in pages[0].lines if "Colegio" in ln.text)
    assert 40 < head.bbox[1] < 60 and head.bbox[2] < 612  # where the letterhead was drawn, in points
    assert any("Atentamente" in ln.text for ln in pages[1].lines)
    assert "pypdfium2" in ocr.engine_label(rasterised=True)
