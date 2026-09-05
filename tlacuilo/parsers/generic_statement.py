"""Generic Mexican bank-statement parser over a text layer (M0).

Strategy: find the table header (FECHA … CARGOS/ABONOS … SALDO) to learn column
positions; every line that starts with a date is a transaction; amounts are assigned
to columns by horizontal proximity; the running balance, when the statement prints
one, checks every row's sign and value. Opening/closing balances, period, account
digits, CLABE and currency come from labelled lines anywhere in the document."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from ..engines.text_layer import Line, Page, Word
from .common import MONTHS, money, parse_amount, parse_date_prefix

_HEADER_DEBIT = re.compile(r"CARGO|RETIRO|D[EÉ]BITO|EGRESO", re.I)
_HEADER_CREDIT = re.compile(r"ABONO|DEP[OÓ]SITO|CR[EÉ]DITO|INGRESO", re.I)
_HEADER_BALANCE = re.compile(r"^SALDO", re.I)
_HEADER_AMOUNT = re.compile(r"IMPORTE|MONTO|CANTIDAD", re.I)
_OPENING = re.compile(r"SALDO\s+(?:ANTERIOR|INICIAL|AL\s+INICIO|DE\s+APERTURA)", re.I)
_CLOSING = re.compile(r"SALDO\s+(?:FINAL|ACTUAL|AL\s+CORTE|NUEVO|AL\s+CIERRE|DE\s+CIERRE)", re.I)
_PERIOD_NUM = re.compile(
    r"(?:PER[IÍ]ODO|DEL)\s*:?\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\s*(?:AL|A|-|HASTA)\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})",
    re.I,
)
_PERIOD_WORDS = re.compile(
    r"(\d{1,2})\s+DE\s+([A-ZÁÉÍÓÚ]+)\s+(?:DE\s+)?(\d{4})\s+AL\s+(\d{1,2})\s+DE\s+([A-ZÁÉÍÓÚ]+)\s+(?:DE\s+)?(\d{4})",
    re.I,
)
_CLABE = re.compile(r"CLABE[^0-9]{0,20}((?:\d[\s-]?){18})")
_ACCOUNT_LINE = re.compile(r"\b(?:CUENTA|CTA\.?|CONTRATO|TARJETA)\b", re.I)
_MASKED_NUMBER = re.compile(r"^[\d*Xx•·-]{6,}$")
_REFERENCE = re.compile(r"^\d{6,}$")
_COLUMN_TOLERANCE = 70.0


@dataclass
class ParsedRow:
    seq: int
    date: date
    value_date: date | None
    description: str
    reference: str | None
    amount: Decimal
    balance_after: Decimal | None
    confidence: float
    page: int
    bbox: list[float]
    sign_known: bool = True


@dataclass
class ParsedStatement:
    bank: str | None
    account_last4: str | None
    clabe_last4: str | None
    currency: str
    period: tuple[date, date] | None
    opening: Decimal | None
    closing: Decimal | None
    rows: list[ParsedRow] = field(default_factory=list)
    unparsed_lines: int = 0
    warnings: list[str] = field(default_factory=list)


@dataclass
class _Columns:
    debit: float | None = None
    credit: float | None = None
    balance: float | None = None
    amount: float | None = None

    def known(self) -> dict[str, float]:
        return {k: v for k, v in self.__dict__.items() if v is not None}


def header_columns(line: Line) -> _Columns | None:
    text = line.text.upper()
    if "FECHA" not in text or "SALDO" not in text:
        return None
    if not (_HEADER_DEBIT.search(text) or _HEADER_CREDIT.search(text) or _HEADER_AMOUNT.search(text)):
        return None
    cols = _Columns()
    for w in line.words:
        t = w.text.upper()
        if _HEADER_BALANCE.match(t) and cols.balance is None:
            cols.balance = w.xc
        elif _HEADER_DEBIT.search(t) and cols.debit is None:
            cols.debit = w.xc
        elif _HEADER_CREDIT.search(t) and cols.credit is None:
            cols.credit = w.xc
        elif _HEADER_AMOUNT.search(t) and cols.amount is None:
            cols.amount = w.xc
    return cols if cols.known() else None


def _assign(cols: _Columns, w: Word) -> str | None:
    best, best_d = None, _COLUMN_TOLERANCE
    for name, x in cols.known().items():
        d = abs(w.xc - x)
        if d < best_d:
            best, best_d = name, d
    return best


def _year_for(period: tuple[date, date] | None) -> int | None:
    return period[1].year if period else None


def _fix_year(d: date | None, period: tuple[date, date] | None) -> date | None:
    if d is None or period is None:
        return d
    start, end = period
    if start.year != end.year and d.month > end.month:
        return date(start.year, d.month, d.day)
    return d


def _period(text: str) -> tuple[date, date] | None:
    m = _PERIOD_NUM.search(text)
    if m:
        a, _ = parse_date_prefix([m.group(1)], None)
        b, _ = parse_date_prefix([m.group(2)], None)
        if a and b:
            return a, b
    m = _PERIOD_WORDS.search(text)
    if m:
        try:
            a = date(int(m.group(3)), MONTHS[m.group(2).upper()], int(m.group(1)))
            b = date(int(m.group(6)), MONTHS[m.group(5).upper()], int(m.group(4)))
            return a, b
        except (KeyError, ValueError):
            return None
    return None


def _balances(lines: list[Line]) -> tuple[Decimal | None, Decimal | None]:
    opening = closing = None
    for line in lines:
        text = line.text
        amounts = [parse_amount(w.text) for w in line.words]
        amounts = [a for a in amounts if a is not None]
        if not amounts:
            continue
        if opening is None and _OPENING.search(text):
            opening = amounts[-1]
        elif closing is None and _CLOSING.search(text):
            closing = amounts[-1]
    return opening, closing


def _account(lines: list[Line]) -> str | None:
    for line in lines:
        if not _ACCOUNT_LINE.search(line.text):
            continue
        for w in line.words:
            if _MASKED_NUMBER.match(w.text):
                digits = re.sub(r"\D", "", w.text)
                if len(digits) >= 4:
                    return digits[-4:]
    return None


def _clabe(text: str) -> str | None:
    m = _CLABE.search(text)
    if not m:
        return None
    digits = re.sub(r"\D", "", m.group(1))
    return digits[-4:] if len(digits) == 18 else None


def _currency(text_upper: str) -> str:
    if re.search(r"\bUSD\b|D[OÓ]LARES|US\$", text_upper):
        return "USD"
    if re.search(r"\bEUR\b|EUROS", text_upper):
        return "EUR"
    return "MXN"


def parse_statement(pages: list[Page], bank: str | None) -> ParsedStatement:
    lines = [line for page in pages for line in page.lines]
    full_text = "\n".join(line.text for line in lines)
    upper = full_text.upper()
    period = _period(full_text)
    opening, closing = _balances(lines)
    result = ParsedStatement(
        bank=bank,
        account_last4=_account(lines),
        clabe_last4=_clabe(full_text),
        currency=_currency(upper),
        period=period,
        opening=opening,
        closing=closing,
    )
    default_year = _year_for(period)
    cols: _Columns | None = None
    prev_balance = opening
    rows: list[ParsedRow] = []

    for line in lines:
        header = header_columns(line)
        if header:
            cols = header
            continue
        tokens = [w.text for w in line.words]
        d, consumed = parse_date_prefix(tokens, default_year)
        if d is None:
            # Continuation of the previous description (no date, no amounts, same page).
            if rows and rows[-1].page == line.page and not any(parse_amount(t) for t in tokens):
                if not (_OPENING.search(line.text) or _CLOSING.search(line.text)) and len(tokens) <= 12:
                    rows[-1].description = (rows[-1].description + " " + line.text).strip()
            continue
        d = _fix_year(d, period)
        value_date, consumed2 = parse_date_prefix(tokens[consumed:], default_year)
        consumed += consumed2 if value_date else 0
        value_date = _fix_year(value_date, period)

        amount_words: list[tuple[Word, Decimal]] = []
        for w in line.words[consumed:]:
            a = parse_amount(w.text)
            if a is not None:
                amount_words.append((w, a))
        if not amount_words:
            result.unparsed_lines += 1
            continue

        assigned: dict[str, Decimal] = {}
        used: set[int] = set()
        if cols:
            for w, a in amount_words:
                name = _assign(cols, w)
                if name and name not in assigned:
                    assigned[name] = a
                    used.add(id(w))
        if not assigned:
            if len(amount_words) >= 2:
                assigned["amount"], assigned["balance"] = amount_words[-2][1], amount_words[-1][1]
                used.update(id(amount_words[-2][0]), id(amount_words[-1][0]))
            else:
                assigned["amount"] = amount_words[-1][1]
                used.add(id(amount_words[-1][0]))

        sign_known = True
        if "debit" in assigned:
            amount = -abs(assigned["debit"])
        elif "credit" in assigned:
            amount = abs(assigned["credit"])
        else:
            amount = assigned.get("amount", Decimal("0"))
            sign_known = amount < 0  # an explicit minus is the only sure sign
        balance_after = assigned.get("balance")

        desc_words = [w for w in line.words[consumed:] if id(w) not in used and w.text != "$"]
        description = " ".join(w.text for w in desc_words).strip()
        reference = next((w.text for w in desc_words if _REFERENCE.match(w.text)), None)

        confidence = 0.9 if cols else 0.75
        if balance_after is not None and prev_balance is not None:
            if abs(prev_balance + amount - balance_after) <= Decimal("0.01"):
                confidence = min(0.99, confidence + 0.08)
                sign_known = True
            elif not sign_known and abs(prev_balance - amount - balance_after) <= Decimal("0.01"):
                amount, confidence, sign_known = -amount, min(0.99, confidence + 0.08), True
            else:
                confidence = max(0.2, confidence - 0.3)
                result.warnings.append(f"row {len(rows) + 1}: running balance does not follow from the previous row")
        elif not sign_known:
            confidence = min(confidence, 0.5)
        if balance_after is not None:
            prev_balance = balance_after

        rows.append(
            ParsedRow(
                seq=len(rows) + 1,
                date=d,
                value_date=value_date,
                description=description,
                reference=reference,
                amount=amount,
                balance_after=balance_after,
                confidence=round(confidence, 2),
                page=line.page,
                bbox=line.bbox,
                sign_known=sign_known,
            )
        )

    if any(not r.sign_known for r in rows):
        result.warnings.append("some rows have an inferred sign: review before committing")
    result.rows = rows
    return result


def to_contract_rows(rows: list[ParsedRow]) -> list[dict]:
    return [
        {
            "seq": r.seq,
            "date": r.date,
            "valueDate": r.value_date,
            "description": r.description,
            "reference": r.reference,
            "amount": money(r.amount),
            "balanceAfter": money(r.balance_after),
            "confidence": r.confidence,
            "page": r.page,
            "bbox": r.bbox,
        }
        for r in rows
    ]
