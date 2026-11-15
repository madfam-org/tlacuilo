"""OCR for scans and photos of whole documents (the `csf` lane; RFC 0040 amendment 2).

The letterhead lane reads two pages as they come (`engines/ocr.py`). A constancia
arrives as a phone photo or a flatbed scan, so this module first **prepares** each page
and then reads it with the same Tesseract binary, over the same stdin/stdout pipe, in
the same one-job-at-a-time slot (`ocr._one_job`):

1. contrast: the grey levels are stretched (1 % clipped at each end).
2. skew: the angle at which the rows of text line up best (horizontal projection
   profile of a small binarised copy, ±MAX_SKEW degrees) is undone; a page with too few
   rows to tell is left alone.
3. resolution: a page whose long edge is under MIN_LONG_EDGE pixels is enlarged (at most
   MAX_UPSCALE times) — a low-resolution photo has letters too small for the engine.
4. noise: a median filter, only when the page measures noisy (NOISE_FLOOR).
5. a page that still reads weakly (WEAK_PAGE_CONF, WEAK_PAGE_WORDS) is read again with
   its uneven light flattened, then — if still weak — turned the way Tesseract's
   orientation detector (OSD, on a small copy) says is up; the better reading wins.
   An image's EXIF rotation is applied before all of this. Only a weak page pays for
   the extra readings.

Every step is recorded (`Adjustments`, numbers only). Words come back in the **reading
frame** (upright, level, in the upload's units), where a parser can trust columns and
rows; `ScannedPage.to_upload` maps a box from that frame back through the inverse
transform, so the boxes a result returns are in the units of the upload — PDF points for
a PDF, pixels of the image as displayed for an image — whatever was turned or enlarged.

The QR code a document carries is decoded here too (zxing-cpp, Apache-2.0), from the
same prepared pixels: its text is returned, **never fetched**.

Images: PNG, JPEG, and HEIC/HEIF when `pi-heif` is installed (a decode-only build of
libheif; LGPL-3.0, compatible with this repository's AGPL-3.0 and reported by
`scripts/check-licenses.sh`). Limits are the engine's: MAX_PAGES pages at most,
`ocr.MAX_IMAGE_PIXELS` per image, BUDGET_SECONDS of wall clock for the whole call
including the queue for the slot. A limit or a failure is an `OcrError` code, never a
page of half-read words. Nothing touches a disk; pixels live in this process only for
the call."""

from __future__ import annotations

import io
import math
import re
import statistics
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field

import pypdfium2 as pdfium
from PIL import Image, ImageChops, ImageFilter, ImageOps, ImageStat

from . import ocr
from .text_layer import Line, Page, Word

try:  # optional: HEIC/HEIF photos (iPhone default). Absent, those types are refused.
    import pi_heif

    pi_heif.register_heif_opener()
    HEIF = True
except ImportError:  # pragma: no cover - the image and CI install it
    HEIF = False

MAX_PAGES = 4
RENDER_DPI = 250
BUDGET_SECONDS = 60.0
MIN_LONG_EDGE = 2000  # a page smaller than this (≈ 180 dpi on letter) is enlarged
MAX_UPSCALE = 2.0
WORK_LONG_EDGE = 3300  # and never more than this reaches the engine (≈ 300 dpi on letter)
MAX_SKEW = 6.0  # degrees either way; more than that is a photo the person should retake
SKEW_MIN_GAIN = 1.15  # the turned profile must be this much sharper than upright
NOISE_FLOOR = 2.5  # mean grey-level change a median filter makes on a noisy page
WEAK_PAGE_CONF, WEAK_PAGE_WORDS = 0.75, 25  # a page this weak is tried again, flattened or turned
FLATTEN_RADIUS = 30  # pixels: the background estimate for uneven light
OSD_MIN_CONFIDENCE = 0.5  # low on purpose: a turn is kept only if the page then reads better
PSM = 3
CONFIG = ("tessedit_do_invert=0",)  # no inverted text on a constancia; halves the time

IMAGE_FORMATS = {"image/png": "PNG", "image/jpeg": "JPEG"}
if HEIF:
    IMAGE_FORMATS |= {"image/heic": "HEIF", "image/heif": "HEIF"}

_ROTATE = re.compile(r"Rotate:\s*(\d+)")
_ROTATE_CONF = re.compile(r"Orientation confidence:\s*([0-9.]+)")


# ── geometry ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Affine:
    """Maps a point of the prepared image back to the upload: (x, y) → (a·x + b·y + c,
    d·x + e·y + f)."""

    a: float = 1.0
    b: float = 0.0
    c: float = 0.0
    d: float = 0.0
    e: float = 1.0
    f: float = 0.0

    def __call__(self, x: float, y: float) -> tuple[float, float]:
        return self.a * x + self.b * y + self.c, self.d * x + self.e * y + self.f

    def then(self, inner: Affine) -> Affine:
        """self ∘ inner: apply `inner` first (a later preparation step), then self."""
        return Affine(
            self.a * inner.a + self.b * inner.d,
            self.a * inner.b + self.b * inner.e,
            self.a * inner.c + self.b * inner.f + self.c,
            self.d * inner.a + self.e * inner.d,
            self.d * inner.b + self.e * inner.e,
            self.d * inner.c + self.e * inner.f + self.f,
        )

    def box(self, x0: float, top: float, x1: float, bottom: float) -> tuple[float, float, float, float]:
        corners = [self(x, y) for x in (x0, x1) for y in (top, bottom)]
        xs, ys = [p[0] for p in corners], [p[1] for p in corners]
        return min(xs), min(ys), max(xs), max(ys)


def _scale(k: float) -> Affine:
    return Affine(a=k, e=k)


def _rotation_back(old: tuple[int, int], new: tuple[int, int], degrees_ccw: float) -> Affine:
    """The inverse of Pillow's `rotate(degrees_ccw, expand=True)`: new pixels → old."""
    t = math.radians(degrees_ccw)
    cos, sin = math.cos(t), math.sin(t)
    (w, h), (nw, nh) = old, new
    # x = cos·(x' − W/2) − sin·(y' − H/2) + w/2 ;  y = sin·(x' − W/2) + cos·(y' − H/2) + h/2
    return Affine(cos, -sin, -cos * nw / 2 + sin * nh / 2 + w / 2, sin, cos, -sin * nw / 2 - cos * nh / 2 + h / 2)


def _rotate(img: Image.Image, degrees_ccw: float, back: Affine) -> tuple[Image.Image, Affine]:
    if not degrees_ccw:
        return img, back
    turned = img.rotate(degrees_ccw, resample=Image.Resampling.BICUBIC, expand=True, fillcolor=255)
    return turned, back.then(_rotation_back(img.size, turned.size, degrees_ccw))


# ── preparation ─────────────────────────────────────────────────────────────


@dataclass
class Adjustments:
    """What was done to a page before it was read. Numbers only, safe to return."""

    rotated: int = 0  # quarter turns applied, in degrees counter-clockwise
    deskew: float = 0.0  # degrees counter-clockwise
    upscale: float = 1.0
    contrast: bool = False
    denoised: bool = False


def estimate_skew(img: Image.Image) -> float:
    """Degrees (counter-clockwise) that make the rows of text horizontal; 0.0 when
    nothing beats upright clearly. Rows of text give the sharpest horizontal
    projection profile when they are level."""
    thumb = img.copy()
    thumb.thumbnail((900, 900))
    ink = thumb.point(lambda v: 255 if v < 150 else 0)  # text white on black, for rotate's fill

    def sharpness(angle: float) -> float:
        rows = ink.rotate(angle, resample=Image.Resampling.BILINEAR, fillcolor=0).resize((1, ink.height), Image.BOX)
        return ImageStat.Stat(rows).var[0]

    upright = sharpness(0.0)
    best = max((a / 2 for a in range(int(-MAX_SKEW * 2), int(MAX_SKEW * 2) + 1)), key=sharpness)
    best = max((best + d / 10 for d in range(-5, 6) if abs(best + d / 10) <= MAX_SKEW), key=sharpness)
    # A page with a line or two of text (or none) has no rows to line up: a small gain
    # there is noise, and turning the page by it would only blur it.
    if upright <= 0 or abs(best) < 0.2 or sharpness(best) < upright * SKEW_MIN_GAIN:
        return 0.0
    return round(best, 1)


def _noisy(img: Image.Image) -> bool:
    w, h = img.size
    side = min(600, w, h)
    patch = img.crop(((w - side) // 2, (h - side) // 2, (w + side) // 2, (h + side) // 2))
    change = ImageChops.difference(patch, patch.filter(ImageFilter.MedianFilter(3)))
    return ImageStat.Stat(change).mean[0] > NOISE_FLOOR


def flatten(img: Image.Image) -> Image.Image:
    """Even out uneven light (a phone's shadow, a scanner's gradient): the page minus
    its own blurred background, stretched again. Applied only when a page reads weakly."""
    background = img.filter(ImageFilter.MaxFilter(5)).filter(ImageFilter.BoxBlur(FLATTEN_RADIUS))
    return ImageOps.autocontrast(ImageChops.invert(ImageChops.subtract(background, img)), cutoff=1)


def prepare(img: Image.Image) -> tuple[Image.Image, Affine, Adjustments]:
    """Grey, contrast, skew, size and noise: the page as the engine should see it, the
    transform back to the input's pixels, and what was done."""
    adj = Adjustments()
    back = Affine()
    img = img.convert("L")
    stretched = ImageOps.autocontrast(img, cutoff=1)
    adj.contrast = stretched.getextrema() != img.getextrema()
    img = stretched
    adj.deskew = estimate_skew(img)
    img, back = _rotate(img, adj.deskew, back)
    long_edge = max(img.size)
    k = 1.0
    if long_edge < MIN_LONG_EDGE:
        k = min(MAX_UPSCALE, MIN_LONG_EDGE / long_edge)
    elif long_edge > WORK_LONG_EDGE:
        k = WORK_LONG_EDGE / long_edge
    if k != 1.0:
        img = img.resize((max(1, round(img.width * k)), max(1, round(img.height * k))), Image.Resampling.LANCZOS)
        back = back.then(_scale(1 / k))
        adj.upscale = round(k, 2)
    if _noisy(img):
        img = img.filter(ImageFilter.MedianFilter(3))
        adj.denoised = True
    return img, back, adj


# ── reading ─────────────────────────────────────────────────────────────────


@dataclass
class QrHit:
    text: str
    bbox: tuple[float, float, float, float]  # in the page's units


@dataclass
class ScannedPage:
    """One page as read. `page` is in the **reading frame**: upright and level, in the
    upload's units (a parser reads columns and rows there). `to_upload` maps a box of
    that frame back to the upload as it was sent, which is what a result returns."""

    page: Page
    adjustments: Adjustments
    to_upload: Callable[[list[float]], list[float]]
    qr: list[QrHit] = field(default_factory=list)
    mean_conf: float | None = None


def _words(tsv: str, page: int, unit: float) -> list[Line]:
    """Tesseract's lines in the reading frame: prepared pixels times `unit`."""
    lines = []
    for line in ocr.lines_from_tsv(tsv, page):
        words = [Word(w.text, w.x0 * unit, w.x1 * unit, w.top * unit, w.bottom * unit, w.conf) for w in line.words]
        lines.append(Line(page, words))
    return lines


def _mapper(back: Affine, unit: float, kx: float, ky: float) -> Callable[[list[float]], list[float]]:
    def to_upload(bbox: list[float]) -> list[float]:
        x0, top, x1, bottom = back.box(*(v / unit for v in bbox))
        return [round(x0 * kx, 2), round(top * ky, 2), round(x1 * kx, 2), round(bottom * ky, 2)]

    return to_upload


def _quality(lines: list[Line]) -> tuple[float, int]:
    confs = [w.conf for ln in lines for w in ln.words if w.conf is not None]
    return (statistics.fmean(confs) if confs else 0.0), len(confs)


def _osd_rotation(img: Image.Image, deadline: float) -> int:
    """Tesseract's verdict on which way up the page is, in degrees counter-clockwise
    to apply; 0 when it is not confident or the detector is not installed."""
    binary = ocr.tesseract_path()
    remaining = deadline - time.monotonic()
    if binary is None or remaining <= 0:
        return 0
    small = img.copy()
    small.thumbnail((1600, 1600))
    buf = io.BytesIO()
    small.save(buf, "PNG", compress_level=1)
    try:
        proc = subprocess.run(
            [binary, "stdin", "stdout", "-l", "osd", "--psm", "0"],
            input=buf.getvalue(),
            capture_output=True,
            timeout=remaining,
            env=ocr._env(),
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return 0
    out = proc.stdout.decode("utf-8", "replace")
    rotate, conf = _ROTATE.search(out), _ROTATE_CONF.search(out)
    if not rotate or not conf or float(conf.group(1)) < OSD_MIN_CONFIDENCE:
        return 0
    return (-int(rotate.group(1))) % 360  # «Rotate: 90» = turn it 90° clockwise


def decode_qr(img: Image.Image) -> list[tuple[str, tuple[float, float, float, float]]]:
    """QR codes in `img`: (text, pixel box). Empty when none or the decoder is absent."""
    try:
        import zxingcpp
    except ImportError:  # pragma: no cover - a dependency; kept defensive
        return []
    out = []
    for code in zxingcpp.read_barcodes(img, formats=zxingcpp.BarcodeFormat.QRCode):
        if not code.valid or not code.text:
            continue
        p = code.position
        xs = [p.top_left.x, p.top_right.x, p.bottom_left.x, p.bottom_right.x]
        ys = [p.top_left.y, p.top_right.y, p.bottom_left.y, p.bottom_right.y]
        out.append((code.text, (min(xs), min(ys), max(xs), max(ys))))
    return out


def _score(lines: list[Line]) -> float:
    """How much was read with some certainty: the sum of confident words' confidences."""
    return sum(w.conf for ln in lines for w in ln.words if w.conf is not None and w.conf >= 0.6)


def _read(img: Image.Image, number: int, deadline: float, kx: float, ky: float) -> ScannedPage:
    """Prepare and read. A page that reads weakly is tried again flattened (uneven
    light) and, if still weak, the way OSD says is up; the best reading wins."""
    prepared, back, adj = prepare(img)
    unit = kx / adj.upscale  # page units per prepared pixel (kx ≈ ky: pages are not stretched)
    lines = _words(ocr._recognise(prepared, deadline, psm=PSM, config=CONFIG), number, unit)
    mean, count = _quality(lines)
    if mean < WEAK_PAGE_CONF or count < WEAK_PAGE_WORDS:
        flat = flatten(prepared)
        again = _words(ocr._recognise(flat, deadline, psm=PSM, config=CONFIG), number, unit)
        if _score(again) > _score(lines):
            lines, prepared, adj.contrast = again, flat, True
            mean, count = _quality(lines)
    if mean < WEAK_PAGE_CONF or count < WEAK_PAGE_WORDS:
        turn = _osd_rotation(prepared, deadline)
        if turn:
            # A quarter turn is exact: no resampling blur, and the boxes map back.
            turned, turned_back = _rotate(prepared, turn, back)
            again = _words(ocr._recognise(turned, deadline, psm=PSM, config=CONFIG), number, unit)
            if _score(again) > _score(lines):
                lines, prepared, back = again, turned, turned_back
                mean, count = _quality(lines)
                adj.rotated = turn
    hits = [(text, back.box(*box)) for text, box in decode_qr(prepared)] or decode_qr(img)
    qr = [QrHit(text, (b[0] * kx, b[1] * ky, b[2] * kx, b[3] * ky)) for text, b in hits]
    page = Page(number=number, width=prepared.width * unit, height=prepared.height * unit, lines=lines)
    return ScannedPage(page, adj, _mapper(back, unit, kx, ky), qr=qr, mean_conf=mean if lines else None)


def scan_image(data: bytes, mime: str, *, budget_seconds: float = BUDGET_SECONDS) -> list[ScannedPage]:
    """One uploaded photo or scan as one page; boxes in pixels of the image as displayed."""
    fmt = IMAGE_FORMATS.get(mime)
    if fmt is None:
        raise ocr.OcrError("unsupported_mime", mime=mime)
    deadline = time.monotonic() + budget_seconds
    with ocr._one_job(deadline):
        try:
            img = Image.open(io.BytesIO(data), formats=[fmt])
        except Image.DecompressionBombError:
            raise ocr.OcrError("image_too_large", status=413, limit_pixels=ocr.MAX_IMAGE_PIXELS) from None
        except Exception as exc:
            raise ocr.OcrError("unreadable_image", reason=type(exc).__name__) from None
        width, height = img.size
        if width * height > ocr.MAX_IMAGE_PIXELS:
            raise ocr.OcrError("image_too_large", status=413, pixels=width * height, limit_pixels=ocr.MAX_IMAGE_PIXELS)
        try:
            img = ImageOps.exif_transpose(img)
            if img.mode in ("RGBA", "LA") or "transparency" in img.info:
                rgba = img.convert("RGBA")
                img = Image.alpha_composite(Image.new("RGBA", rgba.size, "white"), rgba)
            img = img.convert("L")
        except Exception as exc:
            raise ocr.OcrError("unreadable_image", reason=type(exc).__name__) from None
        if min(img.size) < 1:
            raise ocr.OcrError("unreadable_image", reason="empty")
        return [_read(img, 1, deadline, 1.0, 1.0)]


def scan_pdf(data: bytes, limit: int = MAX_PAGES, *, budget_seconds: float = BUDGET_SECONDS) -> list[ScannedPage]:
    """The first `limit` (at most MAX_PAGES) pages of a PDF, rasterised and read;
    boxes in PDF points."""
    deadline = time.monotonic() + budget_seconds
    out: list[ScannedPage] = []
    with ocr._one_job(deadline):
        for number, image, kx, ky in _render(data, limit):
            out.append(_read(image, number, deadline, kx, ky))
    return out


def qr_from_pdf(data: bytes, limit: int = MAX_PAGES) -> dict[int, list[QrHit]]:
    """QR codes of a PDF that has a text layer: pages are rasterised only to find them
    (no OCR), and the search stops at the first page that has one. {page: hits}."""
    found: dict[int, list[QrHit]] = {}
    for number, image, kx, ky in _render(data, limit):
        hits = [QrHit(text, (b[0] * kx, b[1] * ky, b[2] * kx, b[3] * ky)) for text, b in decode_qr(image)]
        if hits:
            found[number] = hits
            break
    return found


def _render(data: bytes, limit: int):
    try:
        pdf = pdfium.PdfDocument(data)
    except Exception as exc:
        raise ocr.OcrError("unreadable_pdf", reason=type(exc).__name__) from None
    try:
        for index in range(min(len(pdf), limit, MAX_PAGES)):
            try:
                page = pdf[index]
                width, height = page.get_size()
                if width <= 0 or height <= 0:
                    raise ValueError("empty page")
                scale = (RENDER_DPI / 72) * min(1.0, WORK_LONG_EDGE / (max(width, height) * RENDER_DPI / 72))
                image = page.render(scale=scale, grayscale=True).to_pil()
            except Exception as exc:
                raise ocr.OcrError("unreadable_pdf", reason=type(exc).__name__) from None
            yield index + 1, image, width / image.width, height / image.height
    finally:
        pdf.close()


def page_count(data: bytes) -> int:
    try:
        pdf = pdfium.PdfDocument(data)
    except Exception as exc:
        raise ocr.OcrError("unreadable_pdf", reason=type(exc).__name__) from None
    try:
        return len(pdf)
    finally:
        pdf.close()


def engine_label(*, rasterised: bool = False) -> str:
    label = ocr.engine_label(rasterised=rasterised)
    return label + ", prepared (contrast, skew, size, noise), qr zxing-cpp"
