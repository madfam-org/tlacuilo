"""The `document-extraction/v1` result contract (RFC 0040 §2).

The committed JSON Schema in contracts/document-extraction.v1.json is generated from
these models (`tlacuilo schema`) and CI fails if the two drift. Consumers may
auto-commit rows only when validation.balanceReconciles is true; everything else is a
result to review, never a stub.

Amendment 1 (additive, still v1): the `letterhead_document` type and the optional
`issuer` block — who issued a document that is not a statement, and its date. The block
is present only on results of that lane; every other result serialises exactly as
before, key for key.

Amendment 2 (additive, still v1): the optional `csf` block — what a Constancia de
Situación Fiscal says, field by field, each with its status, confidence, source, page
and box, plus the document's QR code and seal as cross-checks and a `quality` verdict.
Present only on results of the csf lane (`csf_contract.py`)."""

from __future__ import annotations

import json
from datetime import date
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_serializer

from . import __version__
from .csf_contract import Csf

CONTRACT_ID = "document-extraction/v1"
SCHEMA_ID = "https://github.com/madfam-org/internal-devops/blob/main/ecosystem/contracts/document-extraction.v1.json"


class Sensitivity(StrEnum):
    public = "public"
    internal = "internal"
    confidential = "confidential"
    restricted = "restricted"


class DocumentType(StrEnum):
    bank_statement = "bank_statement"
    receipt = "receipt"
    invoice = "invoice"
    csf = "csf"
    opinion_32d = "opinion_32d"
    id_document = "id_document"
    letterhead_document = "letterhead_document"
    unknown = "unknown"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DocumentInfo(_Model):
    type: DocumentType
    mimeType: str
    pages: int = Field(ge=0)
    language: str | None = None
    sha256: str = Field(min_length=64, max_length=64)
    sizeBytes: int = Field(ge=0)
    classification: Sensitivity


class Engines(_Model):
    text: str | None = None
    layout: str | None = None
    ocr: str | None = None
    vlm: str | None = None


class Provenance(_Model):
    service: Literal["tlacuilo"] = "tlacuilo"
    version: str = __version__
    engines: Engines
    parser: str | None = None
    fingerprintConfidence: float = Field(ge=0, le=1)
    durationMs: int = Field(ge=0)


class Period(_Model):
    start: date
    end: date


class StatementRow(_Model):
    seq: int = Field(ge=1)
    date: date
    valueDate: date | None = None
    description: str
    reference: str | None = None
    amount: float = Field(description="Signed: inflow positive, outflow negative")
    balanceAfter: float | None = None
    confidence: float = Field(ge=0, le=1)
    page: int = Field(ge=1)
    bbox: list[float] | None = Field(default=None, min_length=4, max_length=4)


class Statement(_Model):
    bank: str | None = None
    accountLast4: str | None = Field(default=None, min_length=4, max_length=4)
    clabeLast4: str | None = Field(default=None, min_length=4, max_length=4)
    currency: str = Field(min_length=3, max_length=3)
    period: Period | None = None
    openingBalance: float | None = None
    closingBalance: float | None = None
    transactions: list[StatementRow] = Field(default_factory=list)


class Validation(_Model):
    balanceReconciles: bool | None = Field(default=None, description="null when opening/closing balances are missing")
    sumOfRows: float | None = None
    expectedDelta: float | None = None
    unparsedLines: int = Field(default=0, ge=0)
    warnings: list[str] = Field(default_factory=list)


# A name carries no run of four or more digits: a licence number, a phone number or a
# postal code can never travel in one, whatever the rules upstream let through.
_NO_LONG_DIGIT_RUN = r"^\D*(?:\d{1,3}\D+)*\d{0,3}$"
_BBOX_NOTE = "x0, top, x1, bottom from the top-left corner: PDF points for a PDF, pixels of the image as displayed"


class IssuerParty(_Model):
    name: str = Field(
        min_length=2,
        max_length=160,
        pattern=_NO_LONG_DIGIT_RUN,
        description="As written on the document; never a licence number, an address, a phone or an e-mail",
    )
    confidence: float = Field(ge=0, le=1)
    page: int = Field(ge=1)
    bbox: list[float] | None = Field(default=None, min_length=4, max_length=4, description=_BBOX_NOTE)


class IssuerDate(_Model):
    date: date
    confidence: float = Field(ge=0, le=1)
    page: int = Field(ge=1)
    bbox: list[float] | None = Field(default=None, min_length=4, max_length=4, description=_BBOX_NOTE)


class Issuer(_Model):
    """Who issued a letterhead document and when. A field the rules did not find is
    null: the lane never guesses. No free text, no licence number, no contact data."""

    institution: IssuerParty | None = None
    professional: IssuerParty | None = None
    documentDate: IssuerDate | None = Field(
        default=None, description="The date the document was issued, never a birth date"
    )


class ExtractionResult(_Model):
    contract: Literal["document-extraction/v1"] = CONTRACT_ID
    document: DocumentInfo
    provenance: Provenance
    statement: Statement | None = None
    issuer: Issuer | None = Field(
        default=None, description="Present only on letterhead_document results; the key is absent otherwise"
    )
    csf: Csf | None = Field(default=None, description="Present only on csf results; the key is absent otherwise")
    validation: Validation
    fields: dict[str, Any] = Field(default_factory=dict)

    @model_serializer(mode="wrap")
    def _lane_blocks_only_when_present(self, handler):
        # Results of every other lane keep the exact keys they had before the issuer
        # and csf blocks existed, so a consumer validating against an older copy is
        # unaffected.
        data = handler(self)
        for key in ("issuer", "csf"):
            if getattr(self, key) is None:
                data.pop(key, None)
        return data


def json_schema() -> dict[str, Any]:
    schema = ExtractionResult.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = SCHEMA_ID
    schema["title"] = CONTRACT_ID
    return schema


def json_schema_text() -> str:
    return json.dumps(json_schema(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"
