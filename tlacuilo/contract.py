"""The `document-extraction/v1` result contract (RFC 0040 §2).

The committed JSON Schema in contracts/document-extraction.v1.json is generated from
these models (`tlacuilo schema`) and CI fails if the two drift. Consumers may
auto-commit rows only when validation.balanceReconciles is true; everything else is a
result to review, never a stub."""

from __future__ import annotations

import json
from datetime import date
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from . import __version__

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


class ExtractionResult(_Model):
    contract: Literal["document-extraction/v1"] = CONTRACT_ID
    document: DocumentInfo
    provenance: Provenance
    statement: Statement | None = None
    validation: Validation
    fields: dict[str, Any] = Field(default_factory=dict)


def json_schema() -> dict[str, Any]:
    schema = ExtractionResult.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = SCHEMA_ID
    schema["title"] = CONTRACT_ID
    return schema


def json_schema_text() -> str:
    return json.dumps(json_schema(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"
