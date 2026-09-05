import logging

from tlacuilo.contract import Sensitivity
from tlacuilo.logging import NOISY_THIRD_PARTY, RedactionFilter, configure_logging, redact
from tlacuilo.pipeline import run_extraction

from .synth import make_statement


def test_filter_masks_digit_runs():
    record = logging.LogRecord(
        "t", logging.INFO, __file__, 1, "clabe %s amount %s", ("012180001234567891", "15,230.55"), None
    )
    assert RedactionFilter().filter(record)
    assert "0121" not in record.getMessage() and "####" in record.getMessage()
    assert redact("cuenta 4567 ok 123") == "cuenta #### ok 123"


def test_pipeline_logs_carry_no_content(caplog):
    pdf, truth = make_statement()
    configure_logging()  # production configuration: INFO, third-party parsers at WARNING
    with caplog.at_level(logging.DEBUG):
        run_extraction(pdf, "application/pdf", Sensitivity.restricted)
    assert all(logging.getLogger(n).level >= logging.WARNING for n in NOISY_THIRD_PARTY)
    text = "\n".join(r.getMessage() for r in caplog.records)
    for _d, desc, amount, _balance in truth.rows:
        assert desc not in text
        assert f"{abs(amount):,.2f}" not in text
    assert truth.last4 not in text and "1234567891" not in text
