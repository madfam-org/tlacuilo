"""What a Constancia de Situación Fiscal says, field by field (RFC 0040 amendment 2).

Rules only: this module never calls a model, never touches the network and never reads
settings. It takes the Word / Line / Page shapes of a text layer or of OCR, plus the QR
codes found on the pages, and returns the `csf` block of the contract. Values are read
by region, not by line order (`csf_layout`); tables by column (`csf_tables`).

Confidence — a table, not a judgement per call:
  label found and value on its row ............ .95 · continued on the rows below .90
  label recognised with OCR errors ............ × .90
  OCR words ................................... × OCR_SCALE(lowest word confidence in
                                                  the value): 1 at ≥ .96, 0 at ≤ .75,
                                                  linear between (Tesseract rarely gives a
                                                  word more than .97; a misread that is a
                                                  real word — «ingresos» for «Ingresos» —
                                                  still comes in under .90)
  format rule fails (RFC, CURP, CP, idCIF, date)  status `invalid`, at most .30
  agrees with the QR code or the seal ......... at least .97 (`qr_agrees`, `seal_agrees`)
  disagrees with the QR code or the seal ...... at most .30 (`qr_disagrees`, …)
  not read, but the QR code / seal carries it . .90 from the QR (error-corrected), the
                                                  seal's own confidence × .85 for a date
  blank (label printed, nothing after it) ..... .95 from a text layer, .50 from OCR (OCR
                                                  may have missed a faint value)
  not applicable (other legal form) ........... the legal form's confidence
A value under LOW_CONFIDENCE_BELOW is `lowConfidence`: the consumer presents it as
doubtful. Every value — confident or not — is confirmed by the person downstream.

Nothing is corrected: an OCR «O» where the RFC needs a «0» stays, fails the format rule
and comes back `invalid` and low. Nothing is defaulted: a field not found is `not_found`
with a null value, and it is listed in `quality.unreadFields`."""

from __future__ import annotations

import re
import statistics
from datetime import date, datetime
from urllib.parse import parse_qsl, urlsplit

from ..csf_contract import (
    FIELD_NAMES,
    LOW_CONFIDENCE_BELOW,
    Csf,
    CsfCheck,
    CsfField,
    CsfFields,
    CsfFieldStatus,
    CsfLegalForm,
    CsfPageQuality,
    CsfQr,
    CsfQuality,
    CsfSeal,
    CsfSource,
)
from ..engines.text_layer import Page, Word
from . import csf_tables
from .csf_layout import LabelHit, bbox_of, column_below, find_labels, fold, min_conf, value_region, words_after

PARSER_ID = "mx.sat.csf.rules.v1"
PAGES = 4

CONF_ROW, CONF_WRAPPED, FUZZY_FACTOR = 0.95, 0.90, 0.90
CONF_INVALID_MAX, CONF_AGREES, CONF_DISAGREES_MAX = 0.30, 0.97, 0.30
CONF_FROM_QR, SEAL_DATE_FACTOR, CONF_OCR_BLANK = 0.90, 0.85, 0.50
OCR_FULL, OCR_ZERO = 0.96, 0.75

LABELS = {
    "rfc": "RFC:",
    "curp": "CURP:",
    "legalName": "Denominación/Razón Social:",
    "regimenCapital": "Régimen Capital:",
    "givenNames": "Nombre (s):",
    "firstSurname": "Primer Apellido:",
    "secondSurname": "Segundo Apellido:",
    "idCif": "idCIF:",
    "tradeName": "Nombre Comercial:",
    "postalCode": "Código Postal:",
    "streetType": "Tipo de Vialidad:",
    "streetName": "Nombre de Vialidad:",
    "exteriorNumber": "Número Exterior:",
    "interiorNumber": "Número Interior:",
    "neighborhood": "Nombre de la Colonia:",
    "locality": "Nombre de la Localidad:",
    "municipality": "Nombre del Municipio o Demarcación Territorial:",
    "state": "Nombre de la Entidad Federativa:",
    "betweenStreet": "Entre Calle:",
    "andStreet": "Y Calle:",
    "operationsStartDate": "Fecha inicio de operaciones:",
    "padronStatus": "Estatus en el padrón:",
    "lastStatusChangeDate": "Fecha de último cambio de estado:",
}
# Not fields, but they end a value's region when they start in its column.
STOPPERS = {
    "_emission": "Lugar y Fecha de Emisión",
    "_title": "CONSTANCIA DE SITUACIÓN FISCAL",
    "_cedula": "CÉDULA DE IDENTIFICACIÓN FISCAL",
    "_identity": "Datos de Identificación del Contribuyente:",
    "_address": "Datos del domicilio registrado",
    "_address_old": "Datos de Ubicación:",
    "_rfc_caption": "Registro Federal de Contribuyentes",
    "_name_caption": "Nombre, denominación o razón",
    "_validate": "VALIDA TU INFORMACIÓN FISCAL",
    "_email": "Correo Electrónico:",
    "_phone": "Tel. Fijo Lada:",
    "_address_status": "Estado del domicilio:",
    "_taxpayer_status": "Estado del contribuyente en el domicilio:",
    "_page": "Página",
    **csf_tables.TITLES,
}

APPLIES_TO = {
    "curp": "persona_fisica",
    "givenNames": "persona_fisica",
    "firstSurname": "persona_fisica",
    "secondSurname": "persona_fisica",
    "legalName": "persona_moral",
    "regimenCapital": "persona_moral",
}
ALWAYS_FILLED = frozenset(
    {"rfc", "curp", "legalName", "givenNames", "firstSurname", "idCif", "postalCode", "municipality", "state",
     "operationsStartDate", "padronStatus", "emissionDate"}
)  # fmt: skip
DATE_FIELDS = frozenset({"operationsStartDate", "lastStatusChangeDate"})
RULES = {
    "rfc": re.compile(r"^[A-ZÑ&]{3,4}\d{6}[A-Z0-9]{3}$"),
    "curp": re.compile(r"^[A-Z]{4}\d{6}[HMX][A-Z]{2}[B-DF-HJ-NP-TV-Z]{3}[A-Z0-9]\d$"),
    "postalCode": re.compile(r"^\d{5}$"),
    "idCif": re.compile(r"^\d{8,13}$"),
}
COMPACT = frozenset({"rfc", "curp", "postalCode", "idCif"})  # spaces inside are OCR's, not the SAT's
EMPTY_MARKERS = {"", "-", "--", "N/A", "NA", "NO APLICA", "NINGUNO"}
_MONTH_NAMES = "enero febrero marzo abril mayo junio julio agosto septiembre octubre noviembre diciembre"
MONTHS = {m: i for i, m in enumerate(_MONTH_NAMES.split(), start=1)}
MONTHS["setiembre"] = 9
_SPELLED = re.compile(r"(\d{1,2})de([a-z]+)de(\d{4})")
_NUMERIC = re.compile(r"(\d{2})/(\d{2})/(\d{4})")
_EMISSION = re.compile(r"^(.*?)[\s,]*\bA\s+(\d{1,2})\s+DE\s+([A-ZÁÉÍÓÚ]+)\s+DE\s+(\d{4})", re.IGNORECASE)
# ``||2026/11/12 10:15:30|XAXP900101AB1|`` as OCR may render the bars: «11», «l|», «/», «]»,
# or not at all before the date. The date-time-RFC sequence is what identifies it.
_BAR = r"[|lI1!\]\[/()]"
_SEAL = re.compile(_BAR + r"{0,2}(\d{4})/(\d{2})/(\d{2})(\d{2}):(\d{2}):(\d{2})" + _BAR + r"{1,2}([A-ZÑ&0-9]{12,14})")
_D3 = re.compile(r"^(\d{8,13})_([A-ZÑ&0-9]{12,13})$")


def ocr_scale(lowest: float | None) -> float:
    if lowest is None:  # a text layer
        return 1.0
    return max(0.0, min(1.0, (lowest - OCR_ZERO) / (OCR_FULL - OCR_ZERO)))


def iso_date(text: str) -> str | None:
    """``01/01/2022`` or ``01 DE ENERO DE 2022`` → ``2022-01-01``; None otherwise."""
    folded = fold(text)
    for pattern, order in ((_NUMERIC, "dmy"), (_SPELLED, "dMy")):
        m = pattern.fullmatch(folded) if order == "dMy" else pattern.fullmatch(text.strip())
        if not m:
            continue
        day, month, year = m.groups()
        month_number = MONTHS.get(month) if order == "dMy" else int(month)
        try:
            return date(int(year), month_number or 0, int(day)).isoformat()
        except (TypeError, ValueError):
            return None
    return None


def _field(**kwargs) -> CsfField:
    conf = round(kwargs.pop("confidence", 0.0), 3)
    return CsfField(confidence=conf, lowConfidence=conf < LOW_CONFIDENCE_BELOW, **kwargs)


def _missing(status: CsfFieldStatus = CsfFieldStatus.not_found) -> CsfField:
    return _field(status=status, confidence=0.0)


def _read_value(name: str, hit: LabelHit, pages: list[Page], hits: list[LabelHit], source: CsfSource) -> CsfField:
    region = value_region(hit, pages, hits)
    raw = re.sub(r"\s+", " ", region.text).strip()
    if name in COMPACT:
        raw = raw.replace(" ", "")
    if raw.upper() in EMPTY_MARKERS:
        # How sure we are that it IS blank: a text layer says so; OCR may have missed a
        # faint value, so an OCR blank is low and the person confirms there is nothing.
        sure = CONF_ROW if source == CsfSource.text_layer else CONF_OCR_BLANK
        return _field(status=CsfFieldStatus.blank, confidence=sure, source=source, page=hit.page)
    checks: list[CsfCheck] = []
    confidence = CONF_WRAPPED if region.wrapped else CONF_ROW
    if region.wrapped:
        checks.append(CsfCheck.value_wrapped)
    if hit.fuzzy:
        confidence *= FUZZY_FACTOR
        checks.append(CsfCheck.label_fuzzy)
    lowest = min_conf(region.words)
    confidence *= ocr_scale(lowest)
    if lowest is not None and lowest < OCR_FULL:
        checks.append(CsfCheck.low_word_confidence)
    status, value = CsfFieldStatus.read, raw
    if name in DATE_FIELDS:
        value = iso_date(raw) or raw
        ok = value != raw or iso_date(raw) is not None
        status = CsfFieldStatus.read if ok else CsfFieldStatus.invalid
    elif name in RULES:
        ok = bool(RULES[name].match(raw.upper()))
        status = CsfFieldStatus.read if ok else CsfFieldStatus.invalid
    else:
        ok = None
    if ok is not None:
        checks.append(CsfCheck.format_ok if ok else CsfCheck.format_failed)
        if not ok:
            confidence = min(confidence, CONF_INVALID_MAX)
    return _field(
        value=value[:300], status=status, confidence=confidence, source=source, page=hit.page,
        bbox=region.bbox, checks=checks,
    )  # fmt: skip


def _emission(hits: list[LabelHit], pages: list[Page], source: CsfSource) -> tuple[CsfField, CsfField]:
    label = next((h for h in hits if h.name == "_emission"), None)
    if label is None:
        return _missing(), _missing()
    words = column_below(label, pages, 6, lambda text: bool(_EMISSION.search(text)))
    phrase = " ".join(w.text for w in words)
    m = _EMISSION.search(phrase)
    if not m:
        return _missing(), _missing()
    place_raw, day, month, year = m.groups()
    lowest = min_conf(words)
    scale = ocr_scale(lowest)
    checks = [CsfCheck.low_word_confidence] if lowest is not None and lowest < OCR_FULL else []
    month_number = MONTHS.get(fold(month))
    try:
        when = date(int(year), month_number or 0, int(day)).isoformat()
        date_field = _field(value=when, status=CsfFieldStatus.read, confidence=CONF_ROW * scale, source=source,
                            page=label.page, bbox=bbox_of(words), checks=[*checks, CsfCheck.format_ok])  # fmt: skip
    except (TypeError, ValueError):
        date_field = _field(value=f"{day} DE {month} DE {year}", status=CsfFieldStatus.invalid,
                            confidence=min(CONF_INVALID_MAX, CONF_ROW * scale), source=source, page=label.page,
                            bbox=bbox_of(words), checks=[*checks, CsfCheck.format_failed])  # fmt: skip
    place = re.sub(r"\s+,", ",", re.sub(r"\s+", " ", place_raw)).strip(" ,")
    place_field = (
        _field(
            value=place[:300],
            status=CsfFieldStatus.read,
            confidence=0.9 * CONF_ROW * scale,
            source=source,
            page=label.page,
            bbox=bbox_of(words),
            checks=checks,
        )  # fmt: skip
        if place
        else _missing(CsfFieldStatus.blank)
    )
    return date_field, place_field


def read_qr(texts: list[tuple[int, str, list[float] | None]]) -> CsfQr:
    """The first QR code that parses as a URL. Its idCIF and RFC come from `D3`
    (`<idCIF>_<RFC>`) on the SAT's validator URL; any other code is still returned as
    text with `satHost: false`. Never fetched."""
    for page, text, bbox in texts:
        url = text.strip()[:512]
        try:
            parts = urlsplit(url)
        except ValueError:
            continue
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            continue
        host = parts.hostname.lower()
        params = {k[:32]: v[:128] for k, v in parse_qsl(parts.query, keep_blank_values=True)[:10]}
        id_cif = rfc = None
        if m := _D3.match(params.get("D3", "").upper()):
            id_cif, rfc = m.groups()
        return CsfQr(
            status="decoded", url=url, satHost=host == "sat.gob.mx" or host.endswith(".sat.gob.mx"),
            parameters=params, idCif=id_cif, rfc=rfc, confidence=0.99, page=page,
            bbox=[round(v, 2) for v in bbox] if bbox else None,
        )  # fmt: skip
    return CsfQr(status="not_found", confidence=0.0)


def read_seal(hits: list[LabelHit], pages: list[Page], source: CsfSource) -> CsfSeal:
    label = next((h for h in hits if h.name == "_seal"), None)
    if label is None:
        return CsfSeal(status="not_found", confidence=0.0, lowConfidence=True)
    words = words_after(label, pages, 4)
    texts = [w.text.upper().replace(" ", "") for w in words]
    m = _SEAL.search("".join(texts))
    if not m:
        return CsfSeal(status="not_found", confidence=0.0, lowConfidence=True, page=label.page)
    year, month, day, hour, minute, second, run = m.groups()
    # The RFC is 13 or 12 characters and a bar may have been read as «1» or «I» after it:
    # take the length that is an RFC, or none.
    rfc = next((run[:n] for n in (13, 12) if RULES["rfc"].match(run[:n])), "")
    used, position = [], 0  # the words the matched text came from, and only those
    for word, text in zip(words, texts, strict=True):
        if position < m.end() and position + len(text) > m.start():
            used.append(word)
        position += len(text)
    confidence = round(CONF_ROW * ocr_scale(min_conf(used)), 3)
    try:
        moment = datetime(int(year), int(month), int(day), int(hour), int(minute), int(second)).isoformat()
    except ValueError:
        return CsfSeal(status="invalid", confidence=min(confidence, CONF_INVALID_MAX), lowConfidence=True,
                       source=source, page=label.page, bbox=bbox_of(used))  # fmt: skip
    rfc_ok = bool(RULES["rfc"].match(rfc))
    return CsfSeal(
        status="read", timestamp=moment, rfc=rfc if rfc_ok else None, confidence=confidence,
        lowConfidence=confidence < LOW_CONFIDENCE_BELOW, source=source, page=label.page, bbox=bbox_of(used),
    )  # fmt: skip


def _cross_check(field: CsfField, truth: str | None, origin: CsfSource, *, fill_conf: float) -> CsfField:
    """Corroborate (or fill) `field` with an independent machine reading."""
    if not truth:
        return field
    agree_check, disagree_check = (
        (CsfCheck.qr_agrees, CsfCheck.qr_disagrees) if origin == CsfSource.qr else
        (CsfCheck.seal_agrees, CsfCheck.seal_disagrees)
    )  # fmt: skip
    if field.status in (CsfFieldStatus.not_found, CsfFieldStatus.blank) or (
        field.status == CsfFieldStatus.invalid and field.confidence < LOW_CONFIDENCE_BELOW
    ):
        return _field(value=truth, status=CsfFieldStatus.read, confidence=fill_conf, source=origin,
                      checks=[CsfCheck.format_ok, agree_check])  # fmt: skip
    kept = field.model_dump(exclude={"confidence", "lowConfidence", "checks"})
    if field.value == truth:
        return _field(**kept, confidence=max(field.confidence, CONF_AGREES), checks=[*field.checks, agree_check])
    return _field(**kept, confidence=min(field.confidence, CONF_DISAGREES_MAX), checks=[*field.checks, disagree_check])


def _legal_form(found: dict[str, CsfField]) -> CsfLegalForm:
    moral = found.get("legalName")
    physical = [found.get(n) for n in ("givenNames", "firstSurname")]
    if moral and moral.value:
        form, conf = "persona_moral", moral.confidence
    elif any(f and f.value for f in physical):
        form, conf = "persona_fisica", max(f.confidence for f in physical if f and f.value)
    else:
        return CsfLegalForm(value="unknown", confidence=0.0)
    rfc = found.get("rfc")
    if rfc and rfc.value and RULES["rfc"].match(rfc.value):
        implied = "persona_moral" if len(rfc.value) == 12 else "persona_fisica"
        conf = max(conf, 0.95) if implied == form else min(conf, 0.4)
    return CsfLegalForm(value=form, confidence=round(conf, 3))


def is_csf(pages: list[Page]) -> bool:
    text = fold(" ".join(w.text for p in pages for ln in p.lines for w in ln.words))
    return "constanciadesituacionfiscal" in text or "cedulade" in text and "identificacionfiscal" in text


def read_csf(
    pages: list[Page],
    qr_texts: list[tuple[int, str, list[float] | None]],
    *,
    ocr: bool,
    page_quality: list[CsfPageQuality],
) -> Csf:
    source = CsfSource.ocr if ocr else CsfSource.text_layer
    labels = {**LABELS, **STOPPERS, "_seal": "Cadena Original Sello:"}
    hits = find_labels(pages, labels, fuzzy=ocr)
    found: dict[str, CsfField] = {}
    for hit in hits:
        if hit.name in LABELS and hit.name not in found:
            found[hit.name] = _read_value(hit.name, hit, pages, hits, source)
    if "idCif" not in found or found["idCif"].status != CsfFieldStatus.read:
        found.pop("idCif", None)
        found.update(_id_cif_anywhere(pages, source))
    emission_date, emission_place = _emission(hits, pages, source)
    found["emissionDate"], found["emissionPlace"] = emission_date, emission_place
    legal = _legal_form(found)

    fields: dict[str, CsfField] = {}
    for name in FIELD_NAMES:
        applies = APPLIES_TO.get(name)
        if applies and legal.value != "unknown" and applies != legal.value:
            fields[name] = _field(status=CsfFieldStatus.not_applicable, confidence=legal.confidence)
        else:
            fields[name] = found.get(name) or _missing()

    qr = read_qr(qr_texts)
    seal = read_seal(hits, pages, source)
    fields["rfc"] = _cross_check(fields["rfc"], qr.rfc, CsfSource.qr, fill_conf=CONF_FROM_QR)
    fields["idCif"] = _cross_check(fields["idCif"], qr.idCif, CsfSource.qr, fill_conf=CONF_FROM_QR)
    if not qr.rfc and seal.rfc:
        fields["rfc"] = _cross_check(fields["rfc"], seal.rfc, CsfSource.seal, fill_conf=seal.confidence * 0.9)
    if seal.timestamp:
        fields["emissionDate"] = _cross_check(
            fields["emissionDate"], seal.timestamp[:10], CsfSource.seal, fill_conf=seal.confidence * SEAL_DATE_FACTOR
        )

    tables = csf_tables.read_tables(pages, hits, ocr_scale=ocr_scale, fuzzy=ocr)
    applicable = [n for n in FIELD_NAMES if fields[n].status != CsfFieldStatus.not_applicable]
    failed = (CsfFieldStatus.blank, CsfFieldStatus.invalid)
    unread = [
        n for n in applicable
        if fields[n].status == CsfFieldStatus.not_found or (fields[n].status in failed and n in ALWAYS_FILLED)
    ]  # fmt: skip
    if legal.value == "unknown":
        unread.insert(0, "legalForm")
    for key, status in (("regimenes", tables.regimenes_status), ("economicActivities", tables.activities_status),
                        ("obligations", tables.obligations_status)):  # fmt: skip
        if status != "read":
            unread.append(key)
    low = [n for n in applicable if fields[n].value and fields[n].lowConfidence]
    must = [n for n in applicable if n in ALWAYS_FILLED]
    confident = sum(1 for n in must if fields[n].value and not fields[n].lowConfidence)
    verdict = "good" if confident == len(must) else "fair" if confident * 2 >= len(must) else "poor"
    confs = [w.conf for p in pages for ln in p.lines for w in ln.words if w.conf is not None]
    return Csf(
        legalForm=legal,
        fields=CsfFields(**fields),
        regimenes=tables.regimenes,
        regimenesStatus=tables.regimenes_status,
        economicActivities=tables.activities,
        economicActivitiesStatus=tables.activities_status,
        obligations=tables.obligations,
        obligationsStatus=tables.obligations_status,
        qr=qr,
        seal=seal,
        quality=CsfQuality(
            verdict=verdict,
            source="ocr" if ocr else "text_layer",
            meanWordConfidence=round(statistics.fmean(confs), 3) if confs else None,
            lowConfidenceFields=low,
            unreadFields=unread,
            pagesRead=len(pages),
            pages=page_quality,
        ),
    )


def remap_boxes(block: Csf, mappers: dict) -> Csf:
    """Every box of the block (fields, table rows, seal) through its page's mapper. The
    QR box is already in upload units (it is decoded on the upload's pixels)."""

    def moved(item):
        if item.bbox is None or item.page not in mappers:
            return item
        return item.model_copy(update={"bbox": mappers[item.page](item.bbox)})

    fields = {name: moved(getattr(block.fields, name)) for name in FIELD_NAMES}
    return block.model_copy(
        update={
            "fields": CsfFields(**fields),
            "regimenes": [moved(r) for r in block.regimenes],
            "economicActivities": [moved(r) for r in block.economicActivities],
            "obligations": [moved(r) for r in block.obligations],
            "seal": moved(block.seal),
        }
    )


_ID_CIF = re.compile(r"idCIF:?\s*(\d{8,13})(?!\d)", re.IGNORECASE)


def _id_cif_anywhere(pages: list[Page], source: CsfSource) -> dict[str, CsfField]:
    """`idCIF: <digits>` wherever it is printed (glued to a neighbour, say), low."""
    for page in pages:
        for line in page.lines:
            for i in range(len(line.words)):
                window: list[Word] = line.words[i : i + 2]
                if m := _ID_CIF.search(" ".join(w.text for w in window)):
                    conf = 0.85 * ocr_scale(min_conf(window))
                    return {"idCif": _field(value=m.group(1), status=CsfFieldStatus.read, confidence=conf,
                                            source=source, page=page.number, bbox=bbox_of(window),
                                            checks=[CsfCheck.format_ok])}  # fmt: skip
    return {}
