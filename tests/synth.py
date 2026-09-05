"""Synthetic Mexican bank statements with ground truth (RFC 0040 §5, corpus source 1).

Renders a text PDF in a generic table layout (FECHA / DESCRIPCIÓN / CARGOS / ABONOS /
SALDO) with the labelled lines real statements carry. The M1 generator grows one
layout per bank; the contract and the harness are what this file establishes."""

from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas


@dataclass
class Truth:
    bank: str
    last4: str
    clabe_last4: str
    period: tuple[date, date]
    opening: Decimal
    closing: Decimal
    rows: list[tuple[date, str, Decimal, Decimal]]  # date, description, signed amount, balance after


def _fmt(v: Decimal) -> str:
    return f"{abs(v):,.2f}"


def make_statement(
    *,
    bank_header: str = "BBVA MÉXICO, S.A., INSTITUCIÓN DE BANCA MÚLTIPLE",
    bank: str = "BBVA",
    opening: Decimal = Decimal("15230.55"),
    period: tuple[date, date] = (date(2026, 7, 1), date(2026, 7, 31)),
    last4: str = "1234",
    clabe: str = "012180001234567891",
    transactions: list[tuple[date, str, Decimal]] | None = None,
    rows_per_page: int = 32,
) -> tuple[bytes, Truth]:
    if transactions is None:
        transactions = default_transactions(period[0])
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    width, height = letter
    balance = opening
    truth_rows: list[tuple[date, str, Decimal, Decimal]] = []

    def header(page_no: int) -> float:
        c.setFont("Helvetica-Bold", 12)
        c.drawString(40, height - 50, bank_header)
        c.setFont("Helvetica", 9)
        c.drawString(40, height - 66, "ESTADO DE CUENTA")
        c.drawString(40, height - 80, f"CUENTA: ****{last4}")
        c.drawString(300, height - 80, f"CLABE: {clabe}")
        c.drawString(40, height - 94, f"PERIODO DEL {period[0]:%d/%m/%Y} AL {period[1]:%d/%m/%Y}")
        c.drawString(300, height - 94, f"PÁGINA {page_no}")
        if page_no == 1:
            c.drawString(40, height - 112, f"SALDO ANTERIOR ${_fmt(opening)}")
        y = height - 140
        c.setFont("Helvetica-Bold", 9)
        c.drawString(40, y, "FECHA")
        c.drawString(110, y, "DESCRIPCIÓN")
        c.drawRightString(430, y, "CARGOS")
        c.drawRightString(510, y, "ABONOS")
        c.drawRightString(590, y, "SALDO")
        c.setFont("Helvetica", 9)
        return y - 16

    page_no = 1
    y = header(page_no)
    on_page = 0
    for d, desc, amount in transactions:
        if on_page >= rows_per_page:
            c.showPage()
            page_no += 1
            y = header(page_no)
            on_page = 0
        balance += amount
        c.drawString(40, y, f"{d:%d/%m/%Y}")
        c.drawString(110, y, desc[:48])
        if amount < 0:
            c.drawRightString(430, y, _fmt(amount))
        else:
            c.drawRightString(510, y, _fmt(amount))
        c.drawRightString(590, y, _fmt(balance))
        truth_rows.append((d, desc[:48], amount, balance))
        y -= 16
        on_page += 1
    c.setFont("Helvetica-Bold", 9)
    c.drawString(40, y - 10, f"SALDO FINAL ${_fmt(balance)}")
    c.save()
    return buf.getvalue(), Truth(bank, last4, clabe[-4:], period, opening, balance, truth_rows)


def default_transactions(start: date) -> list[tuple[date, str, Decimal]]:
    items = [
        (1, "SPEI RECIBIDO ACME SA DE CV REF 0012345", Decimal("2500.00")),
        (2, "COMPRA OXXO EXPRESS CDMX", Decimal("-215.50")),
        (3, "PAGO TARJETA DE CREDITO BBVA", Decimal("-4300.00")),
        (5, "DEPOSITO EN EFECTIVO SUCURSAL 0184", Decimal("1200.00")),
        (8, "SPEI ENVIADO RENTA JULIO CLABE 7891", Decimal("-6500.00")),
        (9, "COMISION MEMBRESIA", Decimal("-99.00")),
        (9, "IVA COMISION", Decimal("-15.84")),
        (12, "NOMINA INNOVACIONES MADFAM", Decimal("18750.00")),
        (15, "CARGO DOMICILIADO CFE", Decimal("-843.00")),
        (18, "RETIRO CAJERO AUTOMATICO", Decimal("-2000.00")),
        (22, "SPEI RECIBIDO CLIENTE FINAL", Decimal("980.25")),
        (29, "INTERESES GANADOS", Decimal("3.19")),
    ]
    return [(start + timedelta(days=offset - 1), desc, amt) for offset, desc, amt in items]


def many_transactions(start: date, n: int) -> list[tuple[date, str, Decimal]]:
    out = []
    for i in range(n):
        d = start + timedelta(days=i % 28)
        if i % 3 == 0:
            out.append((d, f"SPEI RECIBIDO CLIENTE {i:04d}", Decimal(1000 + (i * 37) % 900) + Decimal("0.25")))
        else:
            out.append((d, f"COMPRA TIENDA {i:04d}", -(Decimal(100 + (i * 53) % 700) + Decimal("0.50"))))
    return out
