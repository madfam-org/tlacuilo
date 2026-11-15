"""The `csf` block of `document-extraction/v1` (RFC 0040 amendment 2; additive).

What a Constancia de Situación Fiscal says, as a **reading**, never as a fact: every
value carries its status, a confidence, where it came from (text layer, OCR, the QR
code, the seal), its page and box, and the checks it passed or failed. A consumer
shows every value to the person for confirmation; `lowConfidence` tells it which ones
to present as doubtful rather than as read. Nothing here is defaulted: a field the
document did not yield is `not_found` or `blank` with a null value.

Field names are the consumer-neutral names of what the SAT prints (`postalCode` for
«Código Postal», …). The QR code's URL is returned as text so the consumer can show it;
tlacuilo never opens it, and neither should a consumer without the person's action."""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

LOW_CONFIDENCE_BELOW = 0.6

_BBOX = "x0, top, x1, bottom from the top-left corner: PDF points for a PDF, pixels of the image as displayed"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CsfFieldStatus(StrEnum):
    read = "read"  # a value was read (it may still be low confidence)
    invalid = "invalid"  # read, but it fails its format rule: kept, flagged, low confidence
    blank = "blank"  # the label is printed with nothing after it
    not_found = "not_found"  # the label was not found on the pages read
    not_applicable = "not_applicable"  # belongs to the other legal form (a CURP on a persona moral)


class CsfSource(StrEnum):
    text_layer = "text_layer"
    ocr = "ocr"
    qr = "qr"
    seal = "seal"


class CsfCheck(StrEnum):
    format_ok = "format_ok"
    format_failed = "format_failed"
    label_fuzzy = "label_fuzzy"  # the label was recognised with OCR errors in it
    value_wrapped = "value_wrapped"  # the value continued on a line below its label
    low_word_confidence = "low_word_confidence"  # the engine was unsure of a word in it
    qr_agrees = "qr_agrees"
    qr_disagrees = "qr_disagrees"
    seal_agrees = "seal_agrees"
    seal_disagrees = "seal_disagrees"


class CsfField(_Model):
    value: str | None = Field(
        default=None, max_length=300, description="As printed; dates as ISO YYYY-MM-DD; null when blank or not found"
    )
    status: CsfFieldStatus
    confidence: float = Field(ge=0, le=1)
    lowConfidence: bool = Field(description=f"confidence under {LOW_CONFIDENCE_BELOW}: present it as doubtful")
    source: CsfSource | None = None
    page: int | None = Field(default=None, ge=1)
    bbox: list[float] | None = Field(default=None, min_length=4, max_length=4, description=_BBOX)
    checks: list[CsfCheck] = Field(default_factory=list)


FIELD_NAMES = (
    "rfc",
    "curp",
    "legalName",
    "regimenCapital",
    "givenNames",
    "firstSurname",
    "secondSurname",
    "idCif",
    "tradeName",
    "postalCode",
    "streetType",
    "streetName",
    "exteriorNumber",
    "interiorNumber",
    "neighborhood",
    "locality",
    "municipality",
    "state",
    "betweenStreet",
    "andStreet",
    "operationsStartDate",
    "padronStatus",
    "lastStatusChangeDate",
    "emissionPlace",
    "emissionDate",
)


class CsfFields(_Model):
    rfc: CsfField
    curp: CsfField
    legalName: CsfField = Field(description="Denominación/Razón Social (persona moral)")
    regimenCapital: CsfField
    givenNames: CsfField
    firstSurname: CsfField
    secondSurname: CsfField
    idCif: CsfField
    tradeName: CsfField
    postalCode: CsfField
    streetType: CsfField
    streetName: CsfField
    exteriorNumber: CsfField
    interiorNumber: CsfField
    neighborhood: CsfField
    locality: CsfField
    municipality: CsfField
    state: CsfField
    betweenStreet: CsfField
    andStreet: CsfField
    operationsStartDate: CsfField
    padronStatus: CsfField
    lastStatusChangeDate: CsfField
    emissionPlace: CsfField = Field(description="The place half of «Lugar y Fecha de Emisión»")
    emissionDate: CsfField = Field(description="The date half of «Lugar y Fecha de Emisión», ISO")


class CsfLegalForm(_Model):
    value: Literal["persona_fisica", "persona_moral", "unknown"]
    confidence: float = Field(ge=0, le=1)


class _Row(_Model):
    confidence: float = Field(ge=0, le=1)
    lowConfidence: bool
    page: int = Field(ge=1)
    bbox: list[float] | None = Field(default=None, min_length=4, max_length=4, description=_BBOX)
    checks: list[CsfCheck] = Field(default_factory=list)


class CsfRegimen(_Row):
    name: str = Field(min_length=1, max_length=200)
    startDate: date | None = None
    endDate: date | None = Field(default=None, description="null: still in force (or not read; see checks)")


class CsfActivity(_Row):
    order: int | None = Field(default=None, ge=0, le=999)
    name: str = Field(min_length=1, max_length=300)
    percentage: int | None = Field(default=None, ge=0, le=100)
    startDate: date | None = None
    endDate: date | None = None


class CsfObligation(_Row):
    description: str = Field(min_length=1, max_length=400)
    dueRule: str | None = Field(default=None, max_length=400)
    startDate: date | None = None
    endDate: date | None = None


class CsfTableStatus(StrEnum):
    read = "read"
    not_found = "not_found"  # the section title was not found
    no_rows = "no_rows"  # the title was found and no row could be read under it


class CsfQr(_Model):
    status: Literal["decoded", "not_found"]
    url: str | None = Field(default=None, max_length=512, description="The QR's text. Never fetched by tlacuilo")
    satHost: bool | None = Field(default=None, description="The URL's host is sat.gob.mx or one of its subdomains")
    parameters: dict[str, str] = Field(default_factory=dict, description="The URL's query parameters, as encoded")
    idCif: str | None = Field(default=None, max_length=20)
    rfc: str | None = Field(default=None, max_length=13)
    confidence: float = Field(ge=0, le=1)
    page: int | None = Field(default=None, ge=1)
    bbox: list[float] | None = Field(default=None, min_length=4, max_length=4, description=_BBOX)

    @field_validator("parameters")
    @classmethod
    def _bounded(cls, value: dict[str, str]) -> dict[str, str]:
        if len(value) > 10 or any(len(k) > 32 or len(v) > 128 for k, v in value.items()):
            raise ValueError("at most 10 parameters, short keys and values")
        return value


class CsfSeal(_Model):
    """The head of «Cadena Original Sello»: when the SAT generated the document, for whom."""

    status: Literal["read", "invalid", "not_found"]
    timestamp: str | None = Field(
        default=None, pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$", description="Local time as printed"
    )
    rfc: str | None = Field(default=None, max_length=13)
    confidence: float = Field(ge=0, le=1)
    lowConfidence: bool
    source: CsfSource | None = None
    page: int | None = Field(default=None, ge=1)
    bbox: list[float] | None = Field(default=None, min_length=4, max_length=4, description=_BBOX)


class CsfPageQuality(_Model):
    page: int = Field(ge=1)
    meanWordConfidence: float | None = Field(default=None, ge=0, le=1, description="null for a text layer")
    rotated: int = Field(default=0, description="Quarter turns applied, degrees counter-clockwise")
    deskew: float = Field(default=0.0, description="Degrees counter-clockwise")
    upscale: float = 1.0
    contrast: bool = False
    denoised: bool = False
    qrFound: bool = False


class CsfQuality(_Model):
    verdict: Literal["good", "fair", "poor"] = Field(
        description="good: every field the SAT always fills was read confidently; fair: some were not; poor: most"
    )
    source: Literal["text_layer", "ocr"]
    meanWordConfidence: float | None = Field(default=None, ge=0, le=1)
    lowConfidenceBelow: float = LOW_CONFIDENCE_BELOW
    lowConfidenceFields: list[str] = Field(default_factory=list)
    unreadFields: list[str] = Field(default_factory=list, description="Applicable fields not read: capture by hand")
    pagesRead: int = Field(ge=0)
    pages: list[CsfPageQuality] = Field(default_factory=list)


class Csf(_Model):
    legalForm: CsfLegalForm
    fields: CsfFields
    regimenes: list[CsfRegimen] = Field(default_factory=list)
    regimenesStatus: CsfTableStatus
    economicActivities: list[CsfActivity] = Field(default_factory=list)
    economicActivitiesStatus: CsfTableStatus
    obligations: list[CsfObligation] = Field(default_factory=list)
    obligationsStatus: CsfTableStatus
    qr: CsfQr
    seal: CsfSeal
    quality: CsfQuality
