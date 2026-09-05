from datetime import date
from decimal import Decimal

from tlacuilo.contract import DocumentType, Sensitivity
from tlacuilo.pipeline import run_extraction

from .synth import make_statement, many_transactions


def test_generic_statement_reconciles_and_matches_truth():
    pdf, truth = make_statement()
    result = run_extraction(pdf, "application/pdf", Sensitivity.restricted)
    assert result.contract == "document-extraction/v1"
    assert result.document.type == DocumentType.bank_statement
    assert result.document.pages == 1
    assert result.document.classification == Sensitivity.restricted
    st = result.statement
    assert st is not None
    assert st.bank == truth.bank
    assert st.accountLast4 == truth.last4
    assert st.clabeLast4 == truth.clabe_last4
    assert st.currency == "MXN"
    assert st.period is not None and (st.period.start, st.period.end) == truth.period
    assert Decimal(str(st.openingBalance)) == truth.opening
    assert Decimal(str(st.closingBalance)) == truth.closing
    assert len(st.transactions) == len(truth.rows)
    for row, (d, desc, amount, balance) in zip(st.transactions, truth.rows, strict=True):
        assert row.date == d
        assert Decimal(str(row.amount)) == amount
        assert Decimal(str(row.balanceAfter)) == balance
        assert desc.split()[0] in row.description
        assert row.confidence >= 0.9
        assert row.page == 1
        assert row.bbox and len(row.bbox) == 4
    assert st.transactions[0].reference == "0012345"
    assert result.validation.balanceReconciles is True
    assert result.validation.unparsedLines == 0
    assert result.provenance.parser == "mx.generic.table.v1"
    assert result.provenance.engines.text.startswith("pdfplumber")


def test_multi_page_statement_reconciles():
    pdf, truth = make_statement(transactions=many_transactions(date(2026, 7, 1), 90))
    result = run_extraction(pdf, "application/pdf", Sensitivity.restricted)
    assert result.document.pages == 3
    assert result.statement is not None
    assert len(result.statement.transactions) == 90
    assert result.validation.balanceReconciles is True
    assert result.statement.transactions[-1].page == 3
    assert [r.seq for r in result.statement.transactions] == list(range(1, 91))


def test_other_bank_fingerprint_and_usd():
    pdf, truth = make_statement(bank_header="BANCO SANTANDER MÉXICO, S.A. — CUENTA EN DÓLARES USD", bank="Santander")
    result = run_extraction(pdf, "application/pdf", Sensitivity.confidential)
    assert result.statement is not None
    assert result.statement.bank == "Santander"
    assert result.statement.currency == "USD"
    assert result.validation.balanceReconciles is True


def test_tampered_balance_is_flagged_not_hidden():
    # Rewrite the closing balance line in the PDF content stream is brittle; instead
    # feed a statement whose last row is dropped from the truth by generating one
    # transaction fewer than the closing balance implies.
    from .synth import default_transactions

    txs = default_transactions(date(2026, 7, 1))
    pdf, truth = make_statement(transactions=txs[:-1], opening=Decimal("15230.55"))
    result = run_extraction(pdf, "application/pdf", Sensitivity.restricted)
    assert result.validation.balanceReconciles is True  # consistent document reconciles
    # Now a document whose printed closing disagrees: simulate by parsing rows and
    # checking the validator directly.
    from tlacuilo.parsers.generic_statement import ParsedRow, ParsedStatement
    from tlacuilo.validate import validate_statement

    rows = [ParsedRow(1, date(2026, 7, 1), None, "X", None, Decimal("10.00"), None, 0.9, 1, [0, 0, 1, 1])]
    parsed = ParsedStatement("BBVA", None, None, "MXN", None, Decimal("100.00"), Decimal("120.00"), rows)
    v = validate_statement(parsed)
    assert v.balanceReconciles is False
    assert v.sumOfRows == 10.0 and v.expectedDelta == 20.0
    assert any("reconcile" in w for w in v.warnings)
