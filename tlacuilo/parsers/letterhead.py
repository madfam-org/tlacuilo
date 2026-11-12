"""Who issued a document that is not a statement, and when (RFC 0040 amendment 1).

Rules only: this module never calls a model, never touches the network and never reads
settings. It takes the Word / Line / Page shapes an engine returns (a text layer or OCR,
it does not care which) and returns up to three fields, each with a confidence, a page
and a box. A field the rules do not find is None: nothing here guesses, completes or
corrects a name or a date.

What is read — the first PAGES pages, and on each only:
- the TOP band (the letterhead): lines starting in the top TOP_FRACTION of the page, at
  most TOP_MAX_LINES;
- the BOTTOM band (the signature block): lines starting below BOTTOM_FRACTION of the
  page, plus the last BOTTOM_LAST_LINES lines of the page (a short letter signs mid-page);
- for the date only, the first BODY_HEAD_LINES lines under the top band of page 1.
The body is not read.

Institution — a top-band segment that looks like an organisation: one of ORG_HEADS among
its first three words («Jardín de Niños» as a phrase, CAM / USAER as written in capitals)
or one of ORG_SUFFIXES at its end. Never an address or contact line (ADDRESS_CUES).
The largest type wins when it is clearly larger; otherwise the first match.

Professional — a segment that starts with a courtesy title (TITLES), followed by at
least two name words. Looked for next to a signature cue (SIGNATURE_CUES) in a bottom
band first, then in a top band. The name is returned as written, title included, and
ends at the first word that is not part of a name (a digit, a role, a licence cue).

Date — the forms `common.find_dates` knows. Top band of page 1, then the first body
lines, then a bottom band (the closing formula). A line that carries a birth, validity,
revision or period cue (NOT_ISSUE_DATE) is skipped, and so is the line under a birth
label; so is an address line.

Never returned: a professional licence number (cédula), an address, a phone, an e-mail.
A name ends before a licence cue and before any long or repeated number (`_clean`), a
name that still carried a run of four digits would be dropped whole, and the contract
refuses one again (`IssuerParty.name`).

Confidence — a table, not a judgement per call:
  institution   top band of page 1 .90 · of page 2 .75
  professional  signature block .90 · letterhead .75 (page 2: .60) · both agree .95
  date          top band .90 · first body lines .80 · closing formula .70
  minus .15 when the winning place holds more than one distinct candidate
  minus .10 for a numeric date whose day and month could be swapped
  times .80 when the words came from OCR; times .60 when their mean OCR confidence
  is under .60
"""

from __future__ import annotations

import re
import statistics
import unicodedata
from dataclasses import dataclass, field
from datetime import date

from ..engines.text_layer import Line, Page, Word
from .common import find_dates

PARSER_ID = "mx.letterhead.rules.v1"
PAGES = 2
TOP_FRACTION = 0.25
TOP_MAX_LINES = 12
BOTTOM_FRACTION = 0.65
BOTTOM_LAST_LINES = 12
BODY_HEAD_LINES = 8
GAP_FACTOR = 2.5  # a horizontal gap wider than this many line heights starts a new segment
SIGNATURE_REACH = 3  # lines between a signature cue and the name it belongs to
MAX_INSTITUTION_WORDS = 14
MAX_NAME_CHARS = 160  # the contract's bound on a name; a longer line is a sentence, not a name
MAX_NAME_WORDS = 5  # given names and surnames after the title; particles do not count

CONF_INSTITUTION = {1: 0.90, 2: 0.75}
CONF_SIGNATURE = 0.90
CONF_LETTERHEAD_PERSON = {1: 0.75, 2: 0.60}
CONF_PERSON_BOTH = 0.95
CONF_DATE_TOP, CONF_DATE_BODY_HEAD, CONF_DATE_CLOSING = 0.90, 0.80, 0.70
PENALTY_SEVERAL = 0.15
PENALTY_DAY_MONTH = 0.10
OCR_FACTOR, OCR_WEAK_FACTOR, OCR_WEAK_BELOW = 0.80, 0.60, 0.60

# Closed lists. Compared accent-folded and lower-cased, so OCR that drops an accent
# still matches. Extending a list is a parser change with a test, not configuration.
ORG_HEADS = frozenset(
    "escuela colegio instituto centro clinica hospital consultorio universidad preescolar primaria "
    "secundaria preparatoria bachillerato guarderia liceo kinder academia fundacion asociacion "
    "sanatorio laboratorio".split()
)
ORG_HEAD_PHRASES = ("jardin de ninos",)
ORG_ACRONYMS = frozenset({"CAM", "USAER"})  # matched as written, capitals only
ORG_SUFFIXES = re.compile(r"(?:^|[\s,])(?:S\.\s?C|A\.\s?C|I\.\s?A\.\s?P)\.?$")
ADDRESS_CUES = frozenset(
    "calle av avenida blvd boulevard bulevar calzada calz col colonia fracc fraccionamiento carretera km "
    "esq esquina piso int ext mz manzana lote lt cp tel tels telefono telefonos cel celular whatsapp "
    "correo email fax rfc www http https".split()
)
_CONTACT = re.compile(r"@|www\.|https?:|\.com\b|\.mx\b|\.org\b|\.edu\b|\bs/n\b", re.I)
TITLES = frozenset(
    "dr dra lic licda mtra mtro psic psicol tf tl to ltf lto profr profra prof ing enf "
    "doctor doctora licenciado licenciada maestro maestra psicologo psicologa neuropsicologo "
    "neuropsicologa profesor profesora pedagogo pedagoga terapeuta psicoterapeuta neurologo neurologa "
    "pediatra psiquiatra logopeda".split()
)
_SHORT_TITLES = frozenset({"tf", "tl", "to"})  # only as written with points or in capitals
PARTICLES = frozenset("de del la las los y e san santa da das do dos di van von mc mac".split())
SIGNATURE_CUES = frozenset("atentamente atte firma cedula ced elaboro evaluo responsable".split())
LICENCE_CUES = frozenset("cedula ced cedulas dgp".split())
REGISTRY_CUES = frozenset("cct clave rvoe registro reg".split())  # a code follows; the name ends before it
# Words that end a name: roles, disciplines, labels. Exact words, then stems.
_NAME_STOP_WORDS = frozenset(
    "prof profr profra profesional jefe jefa firma sello nombre atentamente fecha grupo grado turno "
    "rfc tel cel correo centro colegio escuela hospital instituto universidad general".split()
)
_NAME_STOP_STEMS = (
    "cedul direct subdirect coordin respons titular psicolog neuropsicolog psicoterap medic especiali "
    "terapeut terapia docente maestr supervis encargad pediatr neurolog psiquiatr clinic infantil educa "
    "lenguaje aprendizaje ocupacional comunicacion audicion escolar licenciad doctor profesor"
).split()
NOT_ISSUE_DATE = re.compile(
    r"nacimiento|\bnacio\b|\bnacid[oa]\b|natalicio|(?<![a-z])f\.\s?n\.?(?![a-z])|(?<![a-z])fn\s*:"
    r"|vigen|\bvence\b|vencimiento|caducidad|valid[oa] hasta|proxima cita|\brev\b|revision|version|periodo"
)
_BIRTH_LABEL = re.compile(r"nacimiento|(?<![a-z])f\.\s?n\.?(?![a-z])|(?<![a-z])fn\s*:")
_ISSUE_CUE = re.compile(
    r"\bfecha\s*:|\bfecha de (?:expedicion|emision|elaboracion|informe|reporte|entrega)"
    r"|,\s*a\s+\d|\ba los \d{1,2} dias\b|\ba \d{1,2} de "
)
_SEPARATORS = frozenset({"|", "·", "•", "-", "–", "—", "/", "*"})
_NAME_TOKEN = re.compile(r"^[^\W\d_](?:[^\W\d_]|['’-])*\.?$")
_INITIAL = re.compile(r"^[^\W\d_]\.?$")
_LONG_DIGITS = re.compile(r"\d{4}")
_NUMBER = re.compile(r"^\d+[.,]?$")
_TRAILING = " ,;:-–—"


def fold(text: str) -> str:
    """Lower-case and strip accents: «Clínica» and «CLINICA» compare equal."""
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)).lower()


def _bare(token: str) -> str:
    return re.sub(r"[^a-z0-9]", "", fold(token))


@dataclass(frozen=True)
class Segment:
    """A run of words on one line: a line is cut at separator marks and at wide gaps,
    so a letterhead's two columns or two signatures side by side do not read as one."""

    page: int
    row: int  # the line's index on its page, top to bottom
    words: tuple[Word, ...]

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)

    @property
    def height(self) -> float:
        return statistics.median(w.bottom - w.top for w in self.words)


@dataclass(frozen=True)
class Found:
    value: str | date
    confidence: float
    page: int
    bbox: list[float]


@dataclass
class IssuerDraft:
    institution: Found | None = None
    professional: Found | None = None
    document_date: Found | None = None
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Bands:
    lines: list[Line]  # the page's lines, top to bottom
    top: list[int]
    body_head: list[int]
    bottom: list[int]


def _bbox(words) -> list[float]:
    return [
        round(min(w.x0 for w in words), 2),
        round(min(w.top for w in words), 2),
        round(max(w.x1 for w in words), 2),
        round(max(w.bottom for w in words), 2),
    ]


def bands(page: Page) -> Bands:
    lines = sorted((ln for ln in page.lines if ln.words), key=lambda ln: (min(w.top for w in ln.words), ln.words[0].x0))
    height = page.height or max((w.bottom for ln in lines for w in ln.words), default=0.0)
    tops = [min(w.top for w in ln.words) for ln in lines]
    top = [i for i, t in enumerate(tops) if t < TOP_FRACTION * height][:TOP_MAX_LINES]
    rest = list(range(len(top), len(lines)))  # lines are sorted by their top, so `top` is a prefix
    tail = set(rest[-BOTTOM_LAST_LINES:])
    bottom = [i for i in rest if tops[i] >= BOTTOM_FRACTION * height or i in tail]
    return Bands(lines=lines, top=top, body_head=rest[:BODY_HEAD_LINES], bottom=bottom)


def segments(line: Line, row: int) -> list[Segment]:
    height = statistics.median(w.bottom - w.top for w in line.words)
    groups: list[list[Word]] = []
    current: list[Word] = []
    previous: Word | None = None
    for w in sorted(line.words, key=lambda w: w.x0):
        if w.text in _SEPARATORS:
            if current:
                groups.append(current)
            current, previous = [], None
            continue
        if previous is not None and current and w.x0 - previous.x1 > GAP_FACTOR * height:
            groups.append(current)
            current = []
        current.append(w)
        previous = w
    if current:
        groups.append(current)
    return [Segment(line.page, row, tuple(g)) for g in groups]


def _is_address(text: str) -> bool:
    return bool(_CONTACT.search(text)) or any(_bare(t) in ADDRESS_CUES for t in text.split())


def _is_org(tokens: list[str]) -> bool:
    # «Centro, Villa Ejemplo» is a place: a keyword followed by a comma opens no name.
    head = [t for t in tokens[:3] if not t.endswith(",")]
    if any(_bare(t) in ORG_HEADS for t in head):
        return True
    if any(t.strip(".,:;()\"'«»“”") in ORG_ACRONYMS for t in head):
        return True
    folded = fold(" ".join(tokens[:5]))
    if any(phrase in folded for phrase in ORG_HEAD_PHRASES):
        return True
    return bool(ORG_SUFFIXES.search(" ".join(tokens).rstrip(_TRAILING)))


def _clean(tokens: list[str]) -> list[str]:
    """Cut a name before anything that could be a licence or contact number: a licence
    cue, a token with four digits in a row, or two numbers in a row. One short number
    stays («Secundaria Ficticia 120», «CAM Ejemplo 15»)."""
    out: list[str] = []
    for i, t in enumerate(tokens):
        if _bare(t) in LICENCE_CUES or _bare(t) in REGISTRY_CUES or _LONG_DIGITS.search(t):
            break
        if _NUMBER.match(t) and i + 1 < len(tokens) and _NUMBER.match(tokens[i + 1]):
            break
        out.append(t)
    return out


def institution_name(seg: Segment) -> tuple[str, int] | None:
    """The organisation named by a segment and how many of its words the name spans,
    or None when the segment is not an organisation line."""
    if title_at(seg.words, 0):  # a person's line, not an organisation's
        return None
    tokens = _clean([w.text for w in seg.words])
    if len(tokens) < 2 or len(tokens) > MAX_INSTITUTION_WORDS:
        return None
    text = " ".join(tokens)
    if _is_address(text) or not _is_org(tokens):
        return None
    name = text.strip(_TRAILING)
    if not 2 <= len(name) <= MAX_NAME_CHARS or _LONG_DIGITS.search(name):
        return None
    return name, len(tokens)


def title_at(words, start: int) -> int:
    """How many words from `start` form a courtesy title (0 when none do)."""
    for count in (3, 2, 1):
        chunk = [w.text for w in words[start : start + count]]
        if len(chunk) < count:
            continue
        if count > 1 and not all(_INITIAL.match(t) for t in chunk):
            continue  # only spelled-out initials join: «T. F.», «L. T. F.»
        key = "".join(_bare(t) for t in chunk)
        if key not in TITLES:
            continue
        written = "".join(chunk)
        if key in _SHORT_TITLES and "." not in written and not written.isupper():
            continue
        return count
    return 0


def _ends_name(token: str) -> bool:
    bare = _bare(token)
    if bare in _NAME_STOP_WORDS or bare in TITLES or bare in LICENCE_CUES:
        return True
    return any(bare.startswith(stem) for stem in _NAME_STOP_STEMS)


def person_name(seg: Segment) -> tuple[str, tuple[Word, ...]] | None:
    """A title and the name that follows it, as written, with the words they span. The
    title opens the segment or follows a label («Elaboró: Psic. …»)."""
    words = seg.words
    for start in range(len(words)):
        if start and not words[start - 1].text.endswith(":"):
            continue
        n_title = title_at(words, start)
        if not n_title:
            continue
        taken: list[str] = []
        counted = 0
        for w in words[start + n_title :]:
            token = w.text
            closes = token.endswith(",") or (token.endswith(".") and len(token) > 4)
            core = token.rstrip(",") if token.endswith(",") else (token[:-1] if closes else token)
            if not _NAME_TOKEN.match(core) or _ends_name(core):
                break
            particle = fold(core) in PARTICLES
            if not particle and not core[0].isupper():
                break
            if not particle:
                counted += 1
                if counted > MAX_NAME_WORDS:
                    break
            taken.append(core)
            if closes:
                break
        while taken and fold(taken[-1]) in PARTICLES:
            taken.pop()
        if sum(1 for t in taken if fold(t) not in PARTICLES) < 2:
            continue
        title = [w.text for w in words[start : start + n_title]]
        name = " ".join(title + taken)
        if len(name) > MAX_NAME_CHARS or any(c.isdigit() for c in name):
            return None
        return name, words[start : start + n_title + len(taken)]
    return None


def _score(base: float, used, *, ocr: bool, penalty: float = 0.0) -> float:
    value = base - penalty
    if ocr:
        confs = [w.conf for w in used if w.conf is not None]
        mean = sum(confs) / len(confs) if confs else 0.0
        value *= OCR_FACTOR if mean >= OCR_WEAK_BELOW else OCR_WEAK_FACTOR
    return round(min(0.99, max(0.05, value)), 2)


def find_institution(pages: list[Page], *, ocr: bool = False) -> Found | None:
    for page in pages[:PAGES]:
        b = bands(page)
        best: tuple[Segment, str, int] | None = None
        names: set[str] = set()
        for row in b.top:
            for seg in segments(b.lines[row], row):
                hit = institution_name(seg)
                if hit is None:
                    continue
                names.add(fold(hit[0]))
                if best is None or seg.height > best[0].height * 1.2:
                    best = (seg, *hit)
        if best:
            seg, name, n_words = best
            used = seg.words[:n_words]
            penalty = PENALTY_SEVERAL if len(names) > 1 else 0.0
            base = CONF_INSTITUTION.get(page.number, min(CONF_INSTITUTION.values()))
            return Found(name, _score(base, used, ocr=ocr, penalty=penalty), page.number, _bbox(used))
    return None


def _is_cue_row(line: Line) -> bool:
    if any(_bare(w.text) in SIGNATURE_CUES for w in line.words):
        return True
    return "atentamente" in fold(line.text).replace(" ", "")  # «A T E N T A M E N T E»


def _has_text(line: Line) -> bool:
    return any(c.isalnum() for w in line.words for c in w.text)


def _signature_people(page: Page) -> list[tuple[int, int, str, tuple[Word, ...]]]:
    """(distance to the nearest cue, row, name, words) for every titled name within
    SIGNATURE_REACH text lines of a signature cue in the page's bottom band."""
    b = bands(page)
    rows = [r for r in b.bottom if _has_text(b.lines[r])]  # ruled lines do not count
    cue_at = [i for i, r in enumerate(rows) if _is_cue_row(b.lines[r])]
    out = []
    for i, r in enumerate(rows):
        if not cue_at:
            break
        distance = min(abs(i - c) for c in cue_at)
        if distance > SIGNATURE_REACH:
            continue
        for seg in segments(b.lines[r], r):
            hit = person_name(seg)
            if hit:
                out.append((distance, r, *hit))
    return sorted(out, key=lambda t: (t[0], t[1]))


def _letterhead_people(page: Page) -> list[tuple[str, tuple[Word, ...]]]:
    b = bands(page)
    out = []
    for row in b.top:
        for seg in segments(b.lines[row], row):
            hit = person_name(seg)
            if hit:
                out.append(hit)
    return out


def find_professional(pages: list[Page], *, ocr: bool = False) -> Found | None:
    signed: tuple[Page, list] | None = None
    for page in pages[:PAGES]:
        people = _signature_people(page)
        if people:
            signed = (page, people)
            break
    headed: tuple[Page, list] | None = None
    for page in pages[:PAGES]:
        people = _letterhead_people(page)
        if people:
            headed = (page, people)
            break
    if signed:
        page, people = signed
        _, _, name, used = people[0]
        several = len({fold(p[2]) for p in people}) > 1
        agrees = headed is not None and any(fold(h[0]) == fold(name) for h in headed[1])
        base = CONF_PERSON_BOTH if agrees and not several else CONF_SIGNATURE
        penalty = PENALTY_SEVERAL if several else 0.0
        return Found(name, _score(base, used, ocr=ocr, penalty=penalty), page.number, _bbox(used))
    if headed:
        page, people = headed
        name, used = people[0]
        penalty = PENALTY_SEVERAL if len({fold(p[0]) for p in people}) > 1 else 0.0
        base = CONF_LETTERHEAD_PERSON.get(page.number, min(CONF_LETTERHEAD_PERSON.values()))
        return Found(name, _score(base, used, ocr=ocr, penalty=penalty), page.number, _bbox(used))
    return None


def _span_words(line: Line, start: int, end: int) -> list[Word]:
    used, offset = [], 0
    for w in line.words:
        if offset < end and offset + len(w.text) > start:
            used.append(w)
        offset += len(w.text) + 1
    return used


def _dates_in(b: Bands, rows: list[int]) -> list[tuple[bool, date, bool, list[Word]]]:
    """(issue-cued, date, day/month ambiguous, words) for each date on the given rows,
    in reading order, leaving out every line that is not about the issue date."""
    out = []
    for row in rows:
        line = b.lines[row]
        folded = fold(line.text)
        if NOT_ISSUE_DATE.search(folded) or _is_address(line.text):
            continue
        if row > 0:  # a value under its label: «Fecha de nacimiento» / «03/05/2019»
            above = b.lines[row - 1].text
            if _BIRTH_LABEL.search(fold(above)) and not find_dates(above):
                continue
        cued = bool(_ISSUE_CUE.search(folded))
        for hit in find_dates(line.text):
            used = _span_words(line, hit.start, hit.end)
            if used:
                out.append((cued, hit.date, hit.ambiguous, used))
    return out


def find_document_date(pages: list[Page], *, ocr: bool = False) -> Found | None:
    if not pages:
        return None
    first = bands(pages[0])
    places = [
        (CONF_DATE_TOP, pages[0], first, first.top),
        (CONF_DATE_BODY_HEAD, pages[0], first, first.body_head),
        (CONF_DATE_CLOSING, pages[0], first, first.bottom),
    ]
    for page in pages[1:PAGES]:
        b = bands(page)
        places.append((CONF_DATE_CLOSING, page, b, b.bottom))
    for base, page, b, rows in places:
        found = _dates_in(b, rows)
        if not found:
            continue
        cued, value, ambiguous, used = next((f for f in found if f[0]), found[0])
        penalty = PENALTY_SEVERAL if len({f[1] for f in found}) > 1 else 0.0
        penalty += PENALTY_DAY_MONTH if ambiguous else 0.0
        return Found(value, _score(base, used, ocr=ocr, penalty=penalty), page.number, _bbox(used))
    return None


def extract_issuer(pages: list[Page], *, ocr: bool = False) -> IssuerDraft:
    """The three fields, or None for each the rules did not find, plus warnings that
    name what is missing and never quote the document."""
    pages = pages[:PAGES]
    draft = IssuerDraft(
        institution=find_institution(pages, ocr=ocr),
        professional=find_professional(pages, ocr=ocr),
        document_date=find_document_date(pages, ocr=ocr),
    )
    if draft.institution is None:
        draft.warnings.append("institution not found")
    if draft.professional is None:
        draft.warnings.append("professional not found")
    if draft.document_date is None:
        draft.warnings.append("document date not found")
    if len(draft.warnings) == 3:
        draft.warnings.append("no issuer field recognised")
    return draft


def to_contract(draft: IssuerDraft) -> dict:
    def party(f: Found | None) -> dict | None:
        return None if f is None else {"name": f.value, "confidence": f.confidence, "page": f.page, "bbox": f.bbox}

    d = draft.document_date
    return {
        "institution": party(draft.institution),
        "professional": party(draft.professional),
        "documentDate": None
        if d is None
        else {"date": d.value, "confidence": d.confidence, "page": d.page, "bbox": d.bbox},
    }
