"""Text-layer extraction for born-digital PDFs (pdfplumber, MIT — never PyMuPDF/AGPL).

Words come out with their boxes; we cluster them into lines by their `top` so parsers
see one line of text plus the positions of every word in it. Scanned PDFs have no
words at all: that is the signal the M1 OCR lane switches on."""

from __future__ import annotations

import io
from dataclasses import dataclass, field

import pdfplumber

ENGINE = f"pdfplumber {pdfplumber.__version__}"
_Y_TOL = 3.0


@dataclass
class Word:
    text: str
    x0: float
    x1: float
    top: float
    bottom: float

    @property
    def xc(self) -> float:
        return (self.x0 + self.x1) / 2


@dataclass
class Line:
    page: int
    words: list[Word]

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)

    @property
    def bbox(self) -> list[float]:
        return [
            round(min(w.x0 for w in self.words), 2),
            round(min(w.top for w in self.words), 2),
            round(max(w.x1 for w in self.words), 2),
            round(max(w.bottom for w in self.words), 2),
        ]


@dataclass
class Page:
    number: int
    width: float
    height: float
    lines: list[Line] = field(default_factory=list)


def page_count(pdf_bytes: bytes) -> int:
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        return len(pdf.pages)


def extract_pages(pdf_bytes: bytes) -> list[Page]:
    pages: list[Page] = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for index, p in enumerate(pdf.pages, start=1):
            raw = p.extract_words(x_tolerance=1.5, y_tolerance=2, keep_blank_chars=False)
            words = sorted(
                (Word(w["text"], float(w["x0"]), float(w["x1"]), float(w["top"]), float(w["bottom"])) for w in raw),
                key=lambda w: (w.top, w.x0),
            )
            lines: list[Line] = []
            current: list[Word] = []
            current_top = None
            for w in words:
                if current_top is None or abs(w.top - current_top) <= _Y_TOL:
                    current.append(w)
                    current_top = w.top if current_top is None else current_top
                else:
                    lines.append(Line(index, sorted(current, key=lambda x: x.x0)))
                    current, current_top = [w], w.top
            if current:
                lines.append(Line(index, sorted(current, key=lambda x: x.x0)))
            pages.append(Page(number=index, width=float(p.width), height=float(p.height), lines=lines))
    return pages


def has_text_layer(pages: list[Page]) -> bool:
    return sum(len(line.words) for page in pages for line in page.lines) >= 20
