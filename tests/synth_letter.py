"""Synthetic letters with ground truth for the letterhead lane (RFC 0040 amendment 1).

A text PDF with what a real letter carries: a letterhead (institution, an address and a
contact line), a place-and-date line, a short body that also prints a birth date, and a
signature block (a cue, a ruled line, the professional, a role and a licence number).
Every name, address and number here is invented and says so; no real institution,
person, licence or address belongs in this file. `as_scan` and `as_scanned_pdf` turn the
same letter into pixels, so the OCR path is measured against the same truth."""

from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import date

from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

MONTHS_ES = (
    "enero febrero marzo abril mayo junio julio agosto septiembre octubre noviembre diciembre".split()
)


@dataclass
class LetterTruth:
    institution: str
    professional: str
    issued: date
    # Printed on the letter and never to be returned by the lane, in any field:
    licence: str
    born: date
    address: str
    phone: str
    email: str
    pages: int


def written(d: date) -> str:
    return f"{d.day} de {MONTHS_ES[d.month - 1]} de {d.year}"


BODY = (
    "Por medio de la presente se hace constar que este documento es una muestra sintética",
    "generada para probar un lector de documentos. Ninguna de las personas, instituciones,",
    "direcciones o números que aparecen en él existe: todos fueron inventados para esta",
    "prueba y no describen a nadie. El texto de relleno solo ocupa el lugar del cuerpo.",
    "Se extiende la presente para los fines de prueba que al lector convengan.",
)


def make_letter(
    *,
    institution: str = "Colegio Ficticio de Pruebas, A.C.",
    professional: str = "Psic. Ana Ejemplo Inventado",
    role: str = "Psicóloga escolar",
    licence: str = "99999999",
    issued: date = date(2026, 11, 12),
    born: date = date(2019, 5, 3),
    place: str = "Ciudad de México",
    numeric_date: bool = False,
    pages: int = 1,
    short: bool = False,
    letterhead_person: str | None = None,
) -> tuple[bytes, LetterTruth]:
    """`pages=2` moves the signature block to the second page; `short` signs mid-page;
    `letterhead_person` also prints a professional under the institution."""
    address = "Calle Inventada 123, Col. Ejemplo, C.P. 00000"
    phone = "55 0000 0000"
    email = "contacto@ejemplo.invalid"
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    width, height = letter

    def head() -> None:
        c.setFont("Helvetica-Bold", 16)
        c.drawString(60, height - 56, institution)
        y = height - 72
        c.setFont("Helvetica", 9)
        if letterhead_person:
            c.drawString(60, y, letterhead_person)
            y -= 12
        c.drawString(60, y, address)
        c.drawString(60, y - 12, f"Tel. {phone} · {email}")

    def signature(top: float) -> None:
        c.setFont("Helvetica", 11)
        c.drawString(60, height - top, "Atentamente")
        c.drawString(60, height - top - 44, "______________________________")
        c.drawString(60, height - top - 60, professional)
        c.drawString(60, height - top - 74, role)
        c.drawString(60, height - top - 88, f"Cédula profesional {licence}")

    head()
    c.setFont("Helvetica", 11)
    when = f"Fecha: {issued:%d/%m/%Y}" if numeric_date else f"{place}, a {written(issued)}"
    c.drawRightString(width - 60, height - 140, when)
    y = height - 215
    c.drawString(60, y, "A quien corresponda:")
    y -= 24
    c.drawString(60, y, f"Fecha de nacimiento: {born:%d/%m/%Y}")
    y -= 24
    for line in BODY:
        c.drawString(60, y, line)
        y -= 16
    if pages == 1:
        signature((height - y) + 40 if short else 610)
    else:
        for extra in range(2, pages + 1):
            c.showPage()
            c.setFont("Helvetica", 11)
            y = height - 80
            for line in BODY:
                c.drawString(60, y, line)
                y -= 16
            if extra == 2:
                signature(610)
    c.save()
    truth = LetterTruth(institution, professional, issued, licence, born, address, phone, email, pages)
    return buf.getvalue(), truth


def make_plain_page() -> bytes:
    """A page of running text with no letterhead, no date and no signature block."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    _, height = letter
    c.setFont("Helvetica", 11)
    y = height - 300
    for line in BODY:
        c.drawString(60, y, line)
        y -= 16
    c.save()
    return buf.getvalue()


def as_scan(pdf: bytes, page: int = 1, dpi: int = 200, fmt: str = "PNG") -> bytes:
    """One page of the letter as an image, the way a scanner or a phone would hand it over."""
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(pdf)
    try:
        image = doc[page - 1].render(scale=dpi / 72).to_pil().convert("RGB")
    finally:
        doc.close()
    out = io.BytesIO()
    image.save(out, fmt)
    return out.getvalue()


def as_scanned_pdf(pdf: bytes, dpi: int = 200) -> bytes:
    """The same letter as a PDF of page images: no text layer at all."""
    import pypdfium2 as pdfium
    from PIL import Image

    doc = pdfium.PdfDocument(pdf)
    try:
        count = len(doc)
    finally:
        doc.close()
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    width, height = letter
    for number in range(1, count + 1):
        image = Image.open(io.BytesIO(as_scan(pdf, number, dpi)))
        c.drawImage(ImageReader(image), 0, 0, width=width, height=height)
        c.showPage()
    c.save()
    return buf.getvalue()
