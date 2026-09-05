from datetime import date
from decimal import Decimal

from tlacuilo.parsers.common import parse_amount, parse_date_prefix
from tlacuilo.parsers.registry import fingerprint


def test_amounts():
    assert parse_amount("$15,230.55") == Decimal("15230.55")
    assert parse_amount("-1,200.00") == Decimal("-1200.00")
    assert parse_amount("(1,200.00)") == Decimal("-1200.00")
    assert parse_amount("1,200.00-") == Decimal("-1200.00")
    assert parse_amount("99.00") == Decimal("99.00")
    assert parse_amount("0012345") is None
    assert parse_amount("12/07/2026") is None
    assert parse_amount("1.234,56") is None


def test_dates():
    assert parse_date_prefix(["02/07/2026", "x"], None) == (date(2026, 7, 2), 1)
    assert parse_date_prefix(["02/07", "x"], 2026) == (date(2026, 7, 2), 1)
    assert parse_date_prefix(["02-JUL-26"], None) == (date(2026, 7, 2), 1)
    assert parse_date_prefix(["02", "JUL", "2026", "SPEI"], None) == (date(2026, 7, 2), 3)
    assert parse_date_prefix(["02", "JUL", "SPEI"], 2026) == (date(2026, 7, 2), 2)
    assert parse_date_prefix(["02JUL"], 2026) == (date(2026, 7, 2), 1)
    assert parse_date_prefix(["SPEI", "02/07/2026"], None) == (None, 0)
    assert parse_date_prefix(["31/02/2026"], None) == (None, 1)


def test_fingerprints():
    assert fingerprint("BBVA MEXICO SA") == ("BBVA", 0.9)
    assert fingerprint("BANCO NACIONAL DE MÉXICO CITIBANAMEX") == ("Citibanamex", 0.9)
    assert fingerprint("MERCADO PAGO W SA") == ("Mercado Pago", 0.9)
    assert fingerprint("SOME FINTECH") == (None, 0.3)
