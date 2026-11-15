"""A synthetic job posting with ground truth for the text lane (RFC 0040 amendment 3).

A text PDF in Spanish and English, the way a posting is often written. The company, the
address and every other detail are invented (`*.example`) and describe no one. The scan
and scanned-PDF forms come from `synth_letter.as_scan` / `as_scanned_pdf`, so the OCR
path is measured against the same lines."""

from __future__ import annotations

import io

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

POSTING_LINES = (
    "Maison Ejemplo Ficticia",
    "Asesora de ventas de lujo",
    "Ubicación: Ciudad de México",
    "Empresa inventada para una prueba; vacantes en careers.maison-ejemplo.example",
    "Responsabilidades",
    "Atender a cada cliente con un servicio personalizado y dar seguimiento a su cartera.",
    "Alcanzar los objetivos de venta de la boutique y cuidar la imagen del piso de venta.",
    "Requirements",
    "Two years of experience in luxury retail and a strong record against sales targets.",
    "Fluent Spanish and conversational English; availability to work on weekends.",
    "We offer a competitive salary, commission and continuous product training.",
)
FILLER = "Este renglón de relleno solo ocupa lugar en la página de una vacante inventada para la prueba."


def make_posting(*, pages: int = 1, filler_lines: int = 0) -> tuple[bytes, tuple[str, ...]]:
    """`pages` repeats the posting on further pages; `filler_lines` adds running text."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    _, height = letter
    for _ in range(pages):
        y = height - 72
        for index, line in enumerate(POSTING_LINES):
            c.setFont("Helvetica-Bold" if index in (0, 1) else "Helvetica", 14 if index in (0, 1) else 11)
            c.drawString(60, y, line)
            y -= 22
        c.setFont("Helvetica", 11)
        for _ in range(filler_lines):
            c.drawString(60, y, FILLER)
            y -= 16
        c.showPage()
    c.save()
    return buf.getvalue(), POSTING_LINES


def make_blank_page() -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    c.showPage()
    c.save()
    return buf.getvalue()
