from datetime import date
from decimal import Decimal

from tlacuilo.parsers.common import find_dates, parse_amount, parse_date_prefix
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


def test_dates_in_running_text():
    def dates(text):
        return [(h.date, text[h.start : h.end], h.ambiguous) for h in find_dates(text)]

    nov12 = date(2026, 11, 12)
    assert dates("12 de noviembre de 2026") == [(nov12, "12 de noviembre de 2026", False)]
    assert dates("Ciudad de México, a 12 de noviembre del 2026.") == [(nov12, "12 de noviembre del 2026", False)]
    assert dates("a los 12 días del mes de noviembre de 2026")[0][0] == nov12
    assert dates("a los 12 dias del mes de noviembre del año 2026")[0][0] == nov12
    assert dates("1º de marzo de 2026")[0][0] == dates("1o. de marzo de 2026")[0][0] == date(2026, 3, 1)
    assert dates("12 de nov. de 2026")[0][0] == dates("12 noviembre 2026")[0][0] == nov12
    assert dates("Fecha: 12/11/2026") == [(nov12, "12/11/2026", True)]  # day first; 12 and 11 could be swapped
    assert dates("25/11/2026") == [(date(2026, 11, 25), "25/11/2026", False)]
    assert dates("12-11-26")[0][0] == dates("12/nov/2026")[0][0] == dates("12-NOV-26")[0][0] == nov12
    assert dates("2026-11-12") == [(nov12, "2026-11-12", False)]
    assert [d for d, _, _ in dates("del 15/11/2026 al 03/05/2019")] == [date(2026, 11, 15), date(2019, 5, 3)]
    # Never repaired, never completed: impossible dates, a month without a day, numbers that are not dates.
    not_dates = ("31/02/2026", "13/13/2026", "noviembre de 2026", "Tel. 55-12-34-56", "F-01-02-24", "112 de mayo 2026")
    for text in not_dates:
        assert dates(text) == [], text
