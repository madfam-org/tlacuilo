"""Tokens shared by every statement parser: Mexican amounts and dates."""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation

_AMOUNT = re.compile(r"^\(?(-)?\$?\s?(-)?(\d{1,3}(?:,\d{3})+|\d+)\.(\d{2})\)?(-|\+|CR|DR)?$", re.I)

MONTHS = {
    "ENE": 1,
    "ENERO": 1,
    "FEB": 2,
    "FEBRERO": 2,
    "MAR": 3,
    "MARZO": 3,
    "ABR": 4,
    "ABRIL": 4,
    "MAY": 5,
    "MAYO": 5,
    "JUN": 6,
    "JUNIO": 6,
    "JUL": 7,
    "JULIO": 7,
    "AGO": 8,
    "AGOSTO": 8,
    "SEP": 9,
    "SEPT": 9,
    "SEPTIEMBRE": 9,
    "OCT": 10,
    "OCTUBRE": 10,
    "NOV": 11,
    "NOVIEMBRE": 11,
    "DIC": 12,
    "DICIEMBRE": 12,
}
_NUMERIC_DATE = re.compile(r"^(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?$")
_DAY_MONTH_ABBR = re.compile(r"^(\d{1,2})[-/]?([A-ZÁÉÍÓÚ]{3,10})\.?(?:[-/](\d{2,4}))?$", re.I)
_DAY_ONLY = re.compile(r"^\d{1,2}$")
_MONTH_WORD = re.compile(r"^([A-ZÁÉÍÓÚ]{3,10})\.?$", re.I)
_YEAR = re.compile(r"^(\d{4}|\d{2})$")


def parse_amount(token: str) -> Decimal | None:
    m = _AMOUNT.match(token.strip())
    if not m:
        return None
    lead1, lead2, whole, cents, trail = m.groups()
    try:
        value = Decimal(f"{whole.replace(',', '')}.{cents}")
    except InvalidOperation:  # pragma: no cover
        return None
    negative = bool(lead1 or lead2) or token.strip().startswith("(") or (trail or "").upper() in {"-", "DR"}
    return -value if negative else value


def _year(text: str | None, default_year: int | None) -> int | None:
    if text is None:
        return default_year
    y = int(text)
    return 2000 + y if y < 100 else y


def parse_date_prefix(tokens: list[str], default_year: int | None) -> tuple[date | None, int]:
    """Read a date from the start of a token list. Returns (date, tokens consumed)."""
    if not tokens:
        return None, 0
    t0 = tokens[0].upper().rstrip(".")
    m = _NUMERIC_DATE.match(t0)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), _year(m.group(3), default_year)
        return _safe(y, mo, d), 1
    m = _DAY_MONTH_ABBR.match(t0)
    if m and m.group(2).upper() in MONTHS:
        d, mo, y = int(m.group(1)), MONTHS[m.group(2).upper()], _year(m.group(3), default_year)
        consumed = 1
        if m.group(3) is None and len(tokens) > 1 and _YEAR.match(tokens[1]):
            y, consumed = _year(tokens[1], default_year), 2
        return _safe(y, mo, d), consumed
    if _DAY_ONLY.match(t0) and len(tokens) > 1:
        m2 = _MONTH_WORD.match(tokens[1].upper().rstrip("."))
        if m2 and m2.group(1).upper() in MONTHS:
            d, mo, consumed, y = int(t0), MONTHS[m2.group(1).upper()], 2, default_year
            if len(tokens) > 2 and _YEAR.match(tokens[2]):
                y, consumed = _year(tokens[2], default_year), 3
            return _safe(y, mo, d), consumed
    return None, 0


def _safe(y: int | None, mo: int, d: int) -> date | None:
    if y is None:
        return None
    try:
        return date(y, mo, d)
    except ValueError:
        return None


def money(value: Decimal | None) -> float | None:
    return None if value is None else float(value.quantize(Decimal("0.01")))
