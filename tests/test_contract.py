import json
import pathlib
from datetime import date

import pytest

from tlacuilo.cli import SCHEMA_PATH, main
from tlacuilo.contract import (
    DocumentInfo,
    DocumentType,
    Engines,
    ExtractionResult,
    Issuer,
    IssuerDate,
    IssuerParty,
    Period,
    Provenance,
    Sensitivity,
    Statement,
    StatementRow,
    Validation,
    json_schema,
    json_schema_text,
)


def test_committed_schema_matches_models():
    assert SCHEMA_PATH.exists(), "run `python -m tlacuilo.cli schema > contracts/document-extraction.v1.json`"
    assert SCHEMA_PATH.read_text() == json_schema_text()
    assert main(["check-schema"]) == 0


def test_schema_shape():
    schema = json_schema()
    assert schema["title"] == "document-extraction/v1"
    assert schema["$schema"].startswith("https://json-schema.org/")
    for key in ("document", "provenance", "validation"):
        assert key in schema["required"]
    assert schema["properties"]["contract"]["const"] == "document-extraction/v1"
    defs = schema["$defs"]
    assert set(defs["Sensitivity"]["enum"]) == {"public", "internal", "confidential", "restricted"}
    assert "balanceReconciles" in defs["Validation"]["properties"]


def test_result_rejects_unknown_keys():
    bad = json.loads(json.dumps({"contract": "document-extraction/v1", "surprise": 1}))
    with pytest.raises(Exception):
        ExtractionResult.model_validate(bad)


def test_internal_devops_copy_is_identical_when_present():
    copy = pathlib.Path("/Users/aldoruizluna/labspace/internal-devops/ecosystem/contracts/document-extraction.v1.json")
    if copy.exists():  # local drift check; CI in internal-devops carries the same rule
        assert copy.read_text() == SCHEMA_PATH.read_text()


# Serialised by the models as they were before the issuer block existed (amendment 1).
STATEMENT_RESULT_V1 = (
    '{"contract":"document-extraction/v1","document":{"type":"bank_statement","mimeType":"application/pdf",'
    '"pages":1,"language":"es","sha256":"' + "0" * 64 + '","sizeBytes":10,"classification":"restricted"},'
    '"provenance":{"service":"tlacuilo","version":"0.1.0","engines":{"text":"pdfplumber 0.11","layout":null,'
    '"ocr":null,"vlm":null},"parser":"mx.generic.table.v1","fingerprintConfidence":0.9,"durationMs":5},'
    '"statement":{"bank":"BBVA","accountLast4":"1234","clabeLast4":"7891","currency":"MXN",'
    '"period":{"start":"2026-07-01","end":"2026-07-31"},"openingBalance":100.0,"closingBalance":110.0,'
    '"transactions":[{"seq":1,"date":"2026-07-02","valueDate":null,"description":"X","reference":null,'
    '"amount":10.0,"balanceAfter":110.0,"confidence":0.98,"page":1,"bbox":[1.0,2.0,3.0,4.0]}]},'
    '"validation":{"balanceReconciles":true,"sumOfRows":10.0,"expectedDelta":10.0,"unparsedLines":0,'
    '"warnings":[]},"fields":{}}'
)


def _statement_result(**extra) -> ExtractionResult:
    row = StatementRow(
        seq=1, date=date(2026, 7, 2), description="X", amount=10.0, balanceAfter=110.0, confidence=0.98, page=1,
        bbox=[1.0, 2.0, 3.0, 4.0],
    )  # fmt: skip
    return ExtractionResult(
        document=DocumentInfo(
            type=DocumentType.bank_statement, mimeType="application/pdf", pages=1, language="es", sha256="0" * 64,
            sizeBytes=10, classification=Sensitivity.restricted,
        ),  # fmt: skip
        provenance=Provenance(
            version="0.1.0", engines=Engines(text="pdfplumber 0.11"), parser="mx.generic.table.v1",
            fingerprintConfidence=0.9, durationMs=5,
        ),  # fmt: skip
        statement=Statement(
            bank="BBVA", accountLast4="1234", clabeLast4="7891", currency="MXN",
            period=Period(start=date(2026, 7, 1), end=date(2026, 7, 31)), openingBalance=100.0, closingBalance=110.0,
            transactions=[row],
        ),  # fmt: skip
        validation=Validation(balanceReconciles=True, sumOfRows=10.0, expectedDelta=10.0),
        **extra,
    )


def test_a_statement_result_is_byte_for_byte_what_it_was_before_the_issuer_block():
    result = _statement_result()
    assert result.model_dump_json() == STATEMENT_RESULT_V1
    assert json.dumps(result.model_dump(mode="json"), separators=(",", ":")) == STATEMENT_RESULT_V1  # the API's path
    assert ExtractionResult.model_validate_json(STATEMENT_RESULT_V1) == result  # and an old result still validates


def test_the_issuer_block_is_additive_and_holds_only_its_three_fields():
    schema = json_schema()
    assert schema["title"] == "document-extraction/v1"  # additive: the contract id does not move
    assert "issuer" in schema["properties"] and "issuer" not in schema["required"]
    defs = schema["$defs"]
    assert "letterhead_document" in defs["DocumentType"]["enum"]
    assert set(defs["Issuer"]["properties"]) == {"institution", "professional", "documentDate"}
    assert set(defs["IssuerParty"]["properties"]) == {"name", "confidence", "page", "bbox"}
    assert set(defs["IssuerDate"]["properties"]) == {"date", "confidence", "page", "bbox"}
    for model in ("Issuer", "IssuerParty", "IssuerDate"):
        assert defs[model]["additionalProperties"] is False  # no snippet, no raw line, no licence field
    issuer = Issuer(
        institution=IssuerParty(name="Colegio Ficticio de Pruebas", confidence=0.9, page=1, bbox=[1, 2, 3, 4]),
        documentDate=IssuerDate(date=date(2026, 11, 12), confidence=0.9, page=1),
    )
    body = _statement_result(issuer=issuer).model_dump(mode="json")
    assert list(body) == ["contract", "document", "provenance", "statement", "issuer", "validation", "fields"]
    assert body["issuer"]["professional"] is None and body["issuer"]["documentDate"]["date"] == "2026-11-12"
    with pytest.raises(Exception):
        Issuer(institution=None, snippet="una línea del documento")
    with pytest.raises(Exception):
        IssuerParty(name="Psic. Ana Ejemplo Inventado", confidence=0.9, page=1, licence="99999999")


@pytest.mark.parametrize(
    "name",
    ["Dra. Ana Ejemplo Inventado 99999999", "Cédula 99999999", "Clínica Inventada tel 5500000000", "CP 00000", "1234"],
)
def test_a_name_with_four_digits_in_a_row_is_refused_by_the_contract_itself(name):
    with pytest.raises(Exception):
        IssuerParty(name=name, confidence=0.9, page=1)
    assert IssuerParty(name="Secundaria Ficticia 120", confidence=0.9, page=1).name.endswith("120")
