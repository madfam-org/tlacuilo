"""Where things are on a constancia: labels found by position, values read by region.

The csf reader never trusts the order of lines. On a real constancia the page-1 header
is two blocks side by side and the text layer (and OCR) interleaves them, and the
emission phrase wraps with a word of the other block between its halves
(`<MUNICIPIO> , <ENTIDAD> A 12 DE` … `social NOVIEMBRE DE 2026`). So a value is read
from the **region** that belongs to its label:

- on the label's own visual row, from the label's right edge to the next label on that
  row (the column boundary), and
- on the rows below, inside the same column, while no other label or section title
  starts in that column and the rows stay close (`CONTINUATION_GAP` line heights,
  at most `CONTINUATION_LINES` rows, left-aligned with the label or the value).

Labels are matched on folded text (no accents, no spaces, no case, colon optional) and,
for OCR, within a small edit distance (`_close`): «Vialidad» read as «Viaiidad» is still
the label, and the field records `label_fuzzy`. The longest label wins, so «Nombre de
la Colonia» is never taken for «Nombre (s)». Rules only; no model, no network."""

from __future__ import annotations

import re
import statistics
import unicodedata
from dataclasses import dataclass, field

from ..engines.text_layer import Line, Page, Word

CONTINUATION_GAP = 1.9  # line heights between a value row and its continuation, at most
CONTINUATION_LINES = 2
ALIGN_TOLERANCE = 14.0  # points (or pixels) a continuation may sit off its column's edge
MAX_LABEL_WORDS = 9
FUZZY_MIN_LENGTH = 8  # shorter labels («Y Calle:», «RFC:») must be read exactly


def fold(text: str) -> str:
    """No accents, no spaces, lower case: «Código Postal:» → «codigopostal:»."""
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"\s+", "", stripped).casefold()


def _bare(text: str) -> str:
    return fold(text).replace(":", "").replace(";", "")


def _distance(a: str, b: str, cap: int) -> int:
    """Levenshtein distance, giving up (cap + 1) once it cannot be within `cap`."""
    if abs(len(a) - len(b)) > cap:
        return cap + 1
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current = [i]
        for j, cb in enumerate(b, start=1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        if min(current) > cap:
            return cap + 1
        previous = current
    return previous[-1]


def _close(seen: str, label: str, fuzzy: bool) -> bool:
    if seen == label:
        return True
    if not fuzzy or len(label) < FUZZY_MIN_LENGTH:
        return False
    cap = max(1, len(label) // 9)
    return _distance(seen, label, cap) <= cap


@dataclass
class LabelHit:
    name: str
    page: int
    line: int  # index of the visual row on its page
    x0: float
    x1: float
    top: float
    bottom: float
    fuzzy: bool = False
    glued: str = ""  # value text glued to the label's last word («Postal:99999»)
    words: list[Word] = field(default_factory=list)


def find_labels(pages: list[Page], labels: dict[str, str], *, fuzzy: bool) -> list[LabelHit]:
    """Every occurrence of every label, left to right on each visual row; the longest
    label that fits wins at a position, and its words are not matched again."""
    targets = sorted(((name, _bare(text)) for name, text in labels.items()), key=lambda t: -len(t[1]))
    hits: list[LabelHit] = []
    for page in pages:
        for index, line in enumerate(page.lines):
            words = line.words
            i = 0
            while i < len(words):
                best: tuple[str, int, bool, str] | None = None
                for name, target in targets:
                    joined = ""
                    for j in range(i, min(len(words), i + MAX_LABEL_WORDS)):
                        joined += _bare(words[j].text)
                        if len(joined) > len(target) + 2 and not joined.startswith(target):
                            break
                        # A label read with errors still ends with its colon; a value
                        # («CALLE …») never does, so it cannot pass for «Y Calle:».
                        colon = ":" in words[j].text or not labels[name].endswith(":")
                        if joined == target or (colon and _close(joined, target, fuzzy)):
                            best = (name, j, joined != target, "")
                            break
                        if joined.startswith(target) and len(joined) > len(target):
                            # The value is glued to the label: keep the raw tail.
                            raw = words[j].text
                            cut = raw.find(":")
                            best = (name, j, False, raw[cut + 1 :] if cut >= 0 else "")
                            break
                    if best:
                        break
                if best is None:
                    i += 1
                    continue
                name, j, was_fuzzy, glued = best
                span = words[i : j + 1]
                hits.append(
                    LabelHit(
                        name=name,
                        page=page.number,
                        line=index,
                        x0=min(w.x0 for w in span),
                        x1=max(w.x1 for w in span),
                        top=min(w.top for w in span),
                        bottom=max(w.bottom for w in span),
                        fuzzy=was_fuzzy,
                        glued=glued,
                        words=span,
                    )
                )
                i = j + 1
    return hits


def _height(line: Line) -> float:
    return statistics.median(w.bottom - w.top for w in line.words) if line.words else 0.0


@dataclass
class Region:
    """The words a label owns, row by row, and how they were reached."""

    rows: list[list[Word]]
    wrapped: bool = False

    @property
    def words(self) -> list[Word]:
        return [w for row in self.rows for w in row]

    @property
    def text(self) -> str:
        return " ".join(" ".join(w.text for w in row) for row in self.rows if row).strip()

    @property
    def bbox(self) -> list[float] | None:
        ws = self.words
        if not ws:
            return None
        return [round(min(w.x0 for w in ws), 2), round(min(w.top for w in ws), 2),
                round(max(w.x1 for w in ws), 2), round(max(w.bottom for w in ws), 2)]  # fmt: skip


def value_region(hit: LabelHit, pages: list[Page], hits: list[LabelHit]) -> Region:
    """The value a label owns, read by region (module docstring)."""
    page = next(p for p in pages if p.number == hit.page)
    same_page = [h for h in hits if h.page == hit.page]
    line = page.lines[hit.line]
    right = min((h.x0 for h in same_page if h.line == hit.line and h.x0 > hit.x1), default=page.width + 1)
    own = {id(w) for w in hit.words}
    first = [w for w in line.words if id(w) not in own and w.x0 >= hit.x1 - 1 and w.xc < right]
    if hit.glued:
        last = hit.words[-1]
        first.insert(0, Word(hit.glued, last.x0, last.x1, last.top, last.bottom, last.conf))
    rows = [first]
    anchor = first[0].x0 if first else hit.x0
    previous_bottom, height = line.words and max(w.bottom for w in line.words), _height(line) or 10.0
    wrapped = False
    for index in range(hit.line + 1, min(len(page.lines), hit.line + 1 + CONTINUATION_LINES + 1)):
        below = page.lines[index]
        top = min(w.top for w in below.words)
        if top - previous_bottom > CONTINUATION_GAP * height:
            break
        starts = [h for h in same_page if h.line == index and h.x0 < right - 1 and h.x1 > hit.x0 - ALIGN_TOLERANCE]
        if starts:
            break  # another label owns this part of the column
        right_here = min((h.x0 for h in same_page if h.line == index and h.x0 > hit.x0), default=right)
        words = [w for w in below.words if w.x0 >= hit.x0 - ALIGN_TOLERANCE and w.xc < min(right, right_here)]
        if not words:
            if len(rows) > 1 or first:
                break
            continue
        lead = min(w.x0 for w in words)
        if abs(lead - anchor) > ALIGN_TOLERANCE and abs(lead - hit.x0) > ALIGN_TOLERANCE:
            break
        rows.append(sorted(words, key=lambda w: w.x0))
        wrapped = True
        previous_bottom = max(w.bottom for w in words)
        if len(rows) > CONTINUATION_LINES:
            break
    return Region(rows=[r for r in rows if r], wrapped=wrapped)


def column_below(hit: LabelHit, pages: list[Page], lookahead: int, stop) -> list[Word]:
    """Words of the label's column from the label down, in reading order, until `stop`
    (a predicate on the joined text so far) says the phrase is complete. The column
    starts half a label-width left of the label: a value centred under a narrower label
    still starts there, and the other block sits much further left."""
    page = next(p for p in pages if p.number == hit.page)
    boundary = hit.x0 - (hit.x1 - hit.x0) / 2
    own = {id(w) for w in hit.words}
    taken = [w for w in page.lines[hit.line].words if id(w) not in own and w.x0 >= hit.x1 - 1]
    for line in page.lines[hit.line + 1 : hit.line + 1 + lookahead]:
        if stop(" ".join(w.text for w in taken)):
            break
        taken += [w for w in line.words if w.xc >= boundary]
    return taken


def words_after(hit: LabelHit, pages: list[Page], lines: int) -> list[Word]:
    """Every word from the label to `lines` rows below it, across the page width."""
    page = next(p for p in pages if p.number == hit.page)
    own = {id(w) for w in hit.words}
    out = [w for w in page.lines[hit.line].words if id(w) not in own and w.x0 >= hit.x1 - 1]
    for line in page.lines[hit.line + 1 : hit.line + 1 + lines]:
        out += line.words
    return out


def bbox_of(words: list[Word]) -> list[float] | None:
    if not words:
        return None
    return [round(min(w.x0 for w in words), 2), round(min(w.top for w in words), 2),
            round(max(w.x1 for w in words), 2), round(max(w.bottom for w in words), 2)]  # fmt: skip


def min_conf(words: list[Word]) -> float | None:
    confs = [w.conf for w in words if w.conf is not None]
    return min(confs) if confs else None
