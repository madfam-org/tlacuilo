import json
import pathlib

from tlacuilo.cli import SCHEMA_PATH, main
from tlacuilo.contract import ExtractionResult, json_schema, json_schema_text


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
    import pytest

    bad = json.loads(json.dumps({"contract": "document-extraction/v1", "surprise": 1}))
    with pytest.raises(Exception):
        ExtractionResult.model_validate(bad)


def test_internal_devops_copy_is_identical_when_present():
    copy = pathlib.Path("/Users/aldoruizluna/labspace/internal-devops/ecosystem/contracts/document-extraction.v1.json")
    if copy.exists():  # local drift check; CI in internal-devops carries the same rule
        assert copy.read_text() == SCHEMA_PATH.read_text()
