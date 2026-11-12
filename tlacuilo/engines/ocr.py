"""OCR for the letterhead lane only (RFC 0040 amendment 1): Tesseract on the CPU, Spanish,
at most two pages. Statement scans stay with milestone M1 and do not come through here.

The Tesseract binary the image ships (Apache-2.0) is called directly: the page goes in on
stdin as PNG and the words come back on stdout as TSV, so no pixel and no text touches a
disk and no binding is added. A scanned PDF is rasterised with pypdfium2 (Apache-2.0 or
BSD-3-Clause, already a dependency of pdfplumber) — never PyMuPDF. No hosted OCR, no
model, no network: the subprocess is the whole engine.

Words come back in the shapes `text_layer` returns (Word / Line / Page), in the units a
consumer can draw on: PDF points for a PDF, pixels of the upload as displayed (EXIF
orientation applied) for an image. A lane does not need to know which engine it got.

Hard limits:
- MAX_PAGES: no call rasterises or recognises more pages than that, whatever it is
  asked for. The lane tells the consumer when the document was longer.
- MAX_IMAGE_PIXELS for an uploaded image: over it is `image_too_large`.
- BUDGET_SECONDS of wall clock for the whole call, queueing included: over it is
  `ocr_timeout` or `ocr_busy`. One OCR job runs at a time per process: the pod has
  one CPU.
A limit or a failure is an error code, never a page of half-read words.
"""

from __future__ import annotations

import functools
import importlib.metadata
import io
import os
import re
import shutil
import subprocess
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager

import pypdfium2 as pdfium
from PIL import Image, ImageOps

from .text_layer import Line, Page, Word

LANG = "spa"
MAX_PAGES = 2
MAX_IMAGE_PIXELS = 64_000_000  # an uploaded photo, as declared in its header
WORK_LONG_EDGE = 2600  # pixels Tesseract sees on the long edge, at most
RENDER_DPI = 200  # a letter page becomes 1700 x 2200
BUDGET_SECONDS = 45.0
IMAGE_FORMATS = {"image/png": "PNG", "image/jpeg": "JPEG"}

_SAME_ROW_OVERLAP = 0.6
_slot = threading.Lock()  # pdfium is not thread-safe, and the pod has one CPU
_VERSION = re.compile(r"tesseract\s+v?([0-9][\w.\-]*)")


class OcrError(Exception):
    def __init__(self, code: str, status: int = 422, **extra):
        super().__init__(code)
        self.code, self.status, self.extra = code, status, extra


def tesseract_path() -> str | None:
    return shutil.which("tesseract")


def available() -> bool:
    return tesseract_path() is not None


@functools.lru_cache(maxsize=4)
def _tesseract_version(binary: str) -> str | None:
    try:
        proc = subprocess.run([binary, "--version"], capture_output=True, timeout=5, env=_env(), check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    m = _VERSION.search((proc.stdout + proc.stderr).decode("utf-8", "replace"))
    return m.group(1) if m else None


def engine_label(*, rasterised: bool = False) -> str:
    """What provenance records in `engines.ocr`: names and versions, nothing else."""
    binary = tesseract_path()
    version = _tesseract_version(binary) if binary else None
    label = f"tesseract {version} ({LANG})" if version else f"tesseract ({LANG})"
    if rasterised:
        label += f", raster pypdfium2 {importlib.metadata.version('pypdfium2')}"
    return label


def _env() -> dict[str, str]:
    # The child gets a path and a thread limit; nothing else the service holds.
    env = {"PATH": os.environ.get("PATH", ""), "OMP_THREAD_LIMIT": "1", "LC_ALL": "C"}
    if os.environ.get("TESSDATA_PREFIX"):
        env["TESSDATA_PREFIX"] = os.environ["TESSDATA_PREFIX"]
    return env


@contextmanager
def _one_job(deadline: float) -> Iterator[None]:
    if not _slot.acquire(timeout=max(0.0, deadline - time.monotonic())):
        raise OcrError("ocr_busy", status=503)
    try:
        yield
    finally:
        _slot.release()


def _tsv_words(tsv: str) -> list[tuple[tuple[int, int, int], Word]]:
    """Word rows (level 5) of Tesseract's TSV, in pixels, keyed by block/paragraph/line."""
    out: list[tuple[tuple[int, int, int], Word]] = []
    for row in tsv.splitlines()[1:]:
        cols = row.split("\t")
        if len(cols) < 12 or cols[0] != "5":
            continue
        text = cols[11].strip()
        try:
            conf = float(cols[10])
            left, top, width, height = (int(c) for c in cols[6:10])
            key = (int(cols[2]), int(cols[3]), int(cols[4]))
        except ValueError:
            continue
        if not text or conf < 0 or width <= 0 or height <= 0:
            continue
        out.append((key, Word(text, float(left), float(left + width), float(top), float(top + height), conf / 100)))
    return out


def lines_from_tsv(tsv: str, page: int, kx: float = 1.0, ky: float = 1.0) -> list[Line]:
    """Tesseract's lines, merged into visual rows (two columns at one height are one
    Line, as in a text layer), top to bottom. `kx`, `ky` scale pixels to page units."""
    grouped: dict[tuple[int, int, int], list[Word]] = {}
    for key, w in _tsv_words(tsv):
        grouped.setdefault(key, []).append(Word(w.text, w.x0 * kx, w.x1 * kx, w.top * ky, w.bottom * ky, w.conf))
    rows: list[list[Word]] = []
    for words in sorted(grouped.values(), key=lambda ws: (min(w.top for w in ws), min(w.x0 for w in ws))):
        top, bottom = min(w.top for w in words), max(w.bottom for w in words)
        if rows:
            prev_top, prev_bottom = min(w.top for w in rows[-1]), max(w.bottom for w in rows[-1])
            overlap = min(bottom, prev_bottom) - max(top, prev_top)
            if overlap > _SAME_ROW_OVERLAP * min(bottom - top, prev_bottom - prev_top):
                rows[-1].extend(words)
                continue
        rows.append(list(words))
    return [Line(page, sorted(words, key=lambda w: w.x0)) for words in rows]


def _recognise(image: Image.Image, deadline: float, dpi: int | None = None) -> str:
    binary = tesseract_path()
    if binary is None:
        raise OcrError("ocr_unavailable", status=503, reason="engine_missing")
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise OcrError("ocr_timeout", limit_seconds=BUDGET_SECONDS)
    buf = io.BytesIO()
    image.save(buf, "PNG", compress_level=1)
    cmd = [binary, "stdin", "stdout", "-l", LANG, "--psm", "3"]
    if dpi:
        cmd += ["--dpi", str(dpi)]
    try:
        proc = subprocess.run(
            [*cmd, "tsv"], input=buf.getvalue(), capture_output=True, timeout=remaining, env=_env(), check=False
        )
    except subprocess.TimeoutExpired:  # run() has already killed the child
        raise OcrError("ocr_timeout", limit_seconds=BUDGET_SECONDS) from None
    except OSError as exc:
        raise OcrError("ocr_unavailable", status=503, reason=type(exc).__name__) from None
    if proc.returncode != 0:
        # stderr is the engine's own diagnostics; only its class is reported, never its text.
        if b"traineddata" in proc.stderr or b"load any languages" in proc.stderr:
            raise OcrError("ocr_unavailable", status=503, reason="language_data_missing")
        raise OcrError("ocr_failed", reason=f"exit_{proc.returncode}")
    return proc.stdout.decode("utf-8", "replace")


def _fit(width: float, height: float) -> float:
    """Scale that brings the long edge down to WORK_LONG_EDGE; never up."""
    return min(1.0, WORK_LONG_EDGE / max(width, height))


def image_pages(data: bytes, mime: str = "image/png", *, budget_seconds: float = BUDGET_SECONDS) -> list[Page]:
    """One uploaded image (PNG or JPEG) as one Page, boxes in pixels of the upload."""
    fmt = IMAGE_FORMATS.get(mime)
    if fmt is None:
        raise OcrError("unsupported_mime", mime=mime)
    deadline = time.monotonic() + budget_seconds
    with _one_job(deadline):
        try:
            img = Image.open(io.BytesIO(data), formats=[fmt])
        except Image.DecompressionBombError:
            raise OcrError("image_too_large", status=413, limit_pixels=MAX_IMAGE_PIXELS) from None
        except Exception as exc:  # not the image its content type claims, or truncated
            raise OcrError("unreadable_image", reason=type(exc).__name__) from None
        width, height = img.size
        if width * height > MAX_IMAGE_PIXELS:
            raise OcrError("image_too_large", status=413, pixels=width * height, limit_pixels=MAX_IMAGE_PIXELS)
        if width < 1 or height < 1:
            raise OcrError("unreadable_image", reason="empty")
        try:
            if fmt == "JPEG":  # let the decoder shrink a large photo while it decodes
                img.draft("L", (WORK_LONG_EDGE, WORK_LONG_EDGE))
            sideways = img.getexif().get(0x0112, 1) in (5, 6, 7, 8)
            img = ImageOps.exif_transpose(img)
            if img.mode in ("RGBA", "LA") or "transparency" in img.info:
                rgba = img.convert("RGBA")
                img = Image.alpha_composite(Image.new("RGBA", rgba.size, "white"), rgba)
            img = img.convert("L")
            scale = _fit(*img.size)
            if scale < 1.0:
                img = img.resize((max(1, round(img.width * scale)), max(1, round(img.height * scale))))
        except Image.DecompressionBombError:
            raise OcrError("image_too_large", status=413, limit_pixels=MAX_IMAGE_PIXELS) from None
        except Exception as exc:
            raise OcrError("unreadable_image", reason=type(exc).__name__) from None
        shown_w, shown_h = (height, width) if sideways else (width, height)
        tsv = _recognise(img, deadline)
        lines = lines_from_tsv(tsv, 1, shown_w / img.width, shown_h / img.height)
        return [Page(number=1, width=float(shown_w), height=float(shown_h), lines=lines)]


def pdf_pages(pdf_bytes: bytes, limit: int = MAX_PAGES, *, budget_seconds: float = BUDGET_SECONDS) -> list[Page]:
    """The first `limit` (at most MAX_PAGES) pages of a scanned PDF, boxes in PDF points."""
    deadline = time.monotonic() + budget_seconds
    pages: list[Page] = []
    with _one_job(deadline):
        try:
            pdf = pdfium.PdfDocument(pdf_bytes)
        except Exception as exc:
            raise OcrError("unreadable_pdf", reason=type(exc).__name__) from None
        try:
            for index in range(min(len(pdf), limit, MAX_PAGES)):
                try:
                    page = pdf[index]
                    width, height = page.get_size()
                    if width <= 0 or height <= 0:
                        raise ValueError("empty page")
                    scale = (RENDER_DPI / 72) * _fit(width * RENDER_DPI / 72, height * RENDER_DPI / 72)
                    image = page.render(scale=scale, grayscale=True).to_pil()
                except Exception as exc:
                    raise OcrError("unreadable_pdf", reason=type(exc).__name__) from None
                tsv = _recognise(image, deadline, dpi=round(scale * 72))
                lines = lines_from_tsv(tsv, index + 1, width / image.width, height / image.height)
                pages.append(Page(number=index + 1, width=float(width), height=float(height), lines=lines))
        finally:
            pdf.close()
    return pages
