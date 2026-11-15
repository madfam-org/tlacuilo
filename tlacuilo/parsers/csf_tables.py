"""The three tables of a constancia, read by column: actividades económicas, regímenes
and obligaciones.

A table is found by its title («Regímenes:»); its column header row follows and gives
each column's left edge («Fecha Inicio» …). Every row below is split into columns by
the words' centres. A row **starts** on a line whose «Fecha Inicio» column carries a
date (every row has one); a line without one continues the row above — text columns
wrap independently — and the row says `value_wrapped`. Text before the first dated row
is never dropped: it is prefixed to that row, which drops to low confidence. The table
ends at the next title, the boilerplate, the seal, or the end of the pages read; a
table may continue on the next page.

Confidence: .95 for a row on one line, .85 for a row rejoined from several, times the
OCR scale of its lowest word (`csf.ocr_scale`); a row whose start date is not a date
is at most .30. An absent title is `not_found`; a title with no readable row is
`no_rows`. Neither is ever an empty list that looks like «nothing to declare»."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date

from ..csf_contract import LOW_CONFIDENCE_BELOW, CsfActivity, CsfCheck, CsfObligation, CsfRegimen, CsfTableStatus
from ..engines.text_layer import Page, Word
from .csf_layout import LabelHit, bbox_of, find_labels, min_conf

TITLES = {
    "_activities": "Actividades Económicas:",
    "_regimenes": "Regímenes:",
    "_obligations": "Obligaciones:",
    "_personal": "Sus datos personales",
    "_sello": "Sello Digital:",
}
HEADERS = {
    "_activities": ("Orden", "Actividad Económica", "Porcentaje", "Fecha Inicio", "Fecha Fin"),
    "_regimenes": ("Régimen", "Fecha Inicio", "Fecha Fin"),
    "_obligations": ("Descripción de la Obligación", "Descripción Vencimiento", "Fecha Inicio", "Fecha Fin"),
}
ENDS = frozenset({"_activities", "_regimenes", "_obligations", "_personal", "_sello", "_seal"})
CONF_ONE_LINE, CONF_REJOINED, CONF_BAD_DATE = 0.95, 0.85, 0.30
ROW_GAP = 1.9  # line heights between two lines of one table, at most
_DATE = re.compile(r"^(\d{2})/(\d{2})/(\d{4})$")
_INT = re.compile(r"^\d{1,3}$")


@dataclass
class Tables:
    regimenes: list[CsfRegimen] = field(default_factory=list)
    regimenes_status: CsfTableStatus = CsfTableStatus.not_found
    activities: list[CsfActivity] = field(default_factory=list)
    activities_status: CsfTableStatus = CsfTableStatus.not_found
    obligations: list[CsfObligation] = field(default_factory=list)
    obligations_status: CsfTableStatus = CsfTableStatus.not_found


def _iso(token: str) -> date | None:
    m = _DATE.match(token.strip())
    if not m:
        return None
    day, month, year = (int(g) for g in m.groups())
    try:
        return date(year, month, day)
    except ValueError:
        return None


@dataclass
class _Raw:
    cells: list[list[Word]]
    page: int
    lines: int = 1
    prefixed: bool = False


def _rows(title: LabelHit, pages: list[Page], hits: list[LabelHit], fuzzy: bool) -> list[_Raw] | None:
    """Column-split rows under `title`, or None when its header row is not found."""
    names = HEADERS[title.name]
    page = next(p for p in pages if p.number == title.page)
    header_hits = []
    header_line = None
    for index in range(title.line + 1, min(len(page.lines), title.line + 4)):
        found = find_labels([Page(page.number, page.width, page.height, [page.lines[index]])],
                            {str(i): n for i, n in enumerate(names)}, fuzzy=fuzzy)  # fmt: skip
        if len(found) >= max(2, len(names) - 1):
            header_hits, header_line = found, index
            break
    if header_line is None:
        return None
    lefts = sorted((h.x0, int(h.name)) for h in header_hits)
    start_column = names.index("Fecha Inicio")

    def column(word: Word) -> int:
        chosen = lefts[0][1]
        for x0, number in lefts:
            if word.xc >= x0 - 4:
                chosen = number
        return chosen

    rows: list[_Raw] = []
    pending: list[list[Word]] = [[] for _ in names]
    ends = {(h.page, h.line) for h in hits if h.name in ENDS and (h.page, h.line) != (title.page, title.line)}
    last = page.lines[header_line]
    for p in pages:
        if p.number < title.page:
            continue
        if p.number > title.page:
            # A table runs onto the next page only when it reached the bottom of this one.
            if last is None or max(w.bottom for w in last.words) < 0.85 * next(
                q.height for q in pages if q.number == last.page
            ):
                break
            last = None
        first = header_line + 1 if p.number == title.page else 0
        for index in range(first, len(p.lines)):
            line = p.lines[index]
            if (p.number, index) in ends:
                return _finish(rows, pending)
            cells: list[list[Word]] = [[] for _ in names]
            for w in line.words:
                cells[column(w)].append(w)
            top = min(w.top for w in line.words)
            close = last is not None and top - max(w.bottom for w in last.words) <= ROW_GAP * _line_height(last)
            if any(_iso(w.text) for w in cells[start_column]):
                rows.append(_Raw(cells, p.number))
            elif close and rows:
                for i, words in enumerate(cells):
                    rows[-1].cells[i].extend(words)
                rows[-1].lines += 1
            elif close and not rows:
                for i, words in enumerate(cells):
                    pending[i].extend(words)
            elif rows or last is not None:
                return _finish(rows, pending)  # a gap: the table is over
            else:
                continue  # the top of a continuation page, before its first row
            last = line
    return _finish(rows, pending)


def _line_height(line) -> float:
    heights = sorted(w.bottom - w.top for w in line.words)
    return heights[len(heights) // 2] if heights else 10.0


def _finish(rows: list[_Raw], pending: list[list[Word]]) -> list[_Raw]:
    if rows and any(pending):
        for i, words in enumerate(pending):
            rows[0].cells[i][:0] = words
        rows[0].prefixed = True
    return rows


def _text(words: list[Word]) -> str:
    return re.sub(r"\s+", " ", " ".join(w.text for w in words)).strip()


def _row_meta(raw: _Raw, scale: Callable[[float | None], float]) -> dict:
    words = [w for cell in raw.cells for w in cell]
    lowest = min_conf(words)
    conf = (CONF_ONE_LINE if raw.lines == 1 and not raw.prefixed else CONF_REJOINED) * scale(lowest)
    checks = []
    if raw.lines > 1 or raw.prefixed:
        checks.append(CsfCheck.value_wrapped)
    if raw.prefixed:
        conf = min(conf, 0.5)
    if lowest is not None and scale(lowest) < 1.0:
        checks.append(CsfCheck.low_word_confidence)
    return {"confidence": conf, "page": raw.page, "bbox": bbox_of(words), "checks": checks}


def _dates(raw: _Raw, start_col: int, end_col: int, meta: dict) -> tuple[date | None, date | None]:
    start = next((d for w in raw.cells[start_col] if (d := _iso(w.text))), None)
    end = next((d for w in raw.cells[end_col] if (d := _iso(w.text))), None)
    bad_end = raw.cells[end_col] and end is None
    if start is None or bad_end:
        meta["confidence"] = min(meta["confidence"], CONF_BAD_DATE)
        meta["checks"].append(CsfCheck.format_failed)
    else:
        meta["checks"].append(CsfCheck.format_ok)
    return start, end


def _finalise(meta: dict) -> dict:
    meta["confidence"] = round(meta["confidence"], 3)
    meta["lowConfidence"] = meta["confidence"] < LOW_CONFIDENCE_BELOW
    return meta


def read_tables(pages: list[Page], hits: list[LabelHit], *, ocr_scale, fuzzy: bool) -> Tables:
    out = Tables()
    for key in ("_activities", "_regimenes", "_obligations"):
        title = next((h for h in hits if h.name == key), None)
        if title is None:
            continue
        raws = _rows(title, pages, hits, fuzzy)
        if not raws:
            status = CsfTableStatus.no_rows
            rows: list = []
        elif key == "_regimenes":
            rows = []
            for raw in raws:
                meta = _row_meta(raw, ocr_scale)
                start, end = _dates(raw, 1, 2, meta)
                name = _text(raw.cells[0])
                if name:
                    rows.append(CsfRegimen(name=name[:200], startDate=start, endDate=end, **_finalise(meta)))
        elif key == "_activities":
            rows = []
            for raw in raws:
                meta = _row_meta(raw, ocr_scale)
                start, end = _dates(raw, 3, 4, meta)
                order = next((int(w.text) for w in raw.cells[0] if _INT.match(w.text)), None)
                pct = next((int(w.text) for w in raw.cells[2] if _INT.match(w.text) and int(w.text) <= 100), None)
                name = _text(raw.cells[1])
                if name:
                    rows.append(CsfActivity(order=order, name=name[:300], percentage=pct, startDate=start,
                                            endDate=end, **_finalise(meta)))  # fmt: skip
        else:
            rows = []
            for raw in raws:
                meta = _row_meta(raw, ocr_scale)
                start, end = _dates(raw, 2, 3, meta)
                description = _text(raw.cells[0])
                if description:
                    rows.append(CsfObligation(description=description[:400], dueRule=_text(raw.cells[1])[:400] or None,
                                              startDate=start, endDate=end, **_finalise(meta)))  # fmt: skip
        if raws is not None:
            status = CsfTableStatus.read if rows else CsfTableStatus.no_rows
        else:
            status = CsfTableStatus.no_rows
        if key == "_regimenes":
            out.regimenes, out.regimenes_status = rows, status
        elif key == "_activities":
            out.activities, out.activities_status = rows, status
        else:
            out.obligations, out.obligations_status = rows, status
    return out
