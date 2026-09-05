"""Bank fingerprints. One parser per (bank, layout version) arrives in M1; in M0 every
bank routes to the generic table parser and the fingerprint only names the bank."""

from __future__ import annotations

import re

FINGERPRINTS: list[tuple[str, re.Pattern[str]]] = [
    ("BBVA", re.compile(r"\bBBVA\b|BANCOMER")),
    ("Citibanamex", re.compile(r"CITIBANAMEX|\bBANAMEX\b|BANCO NACIONAL DE M[EÉ]XICO")),
    ("Santander", re.compile(r"\bSANTANDER\b")),
    ("Banorte", re.compile(r"\bBANORTE\b")),
    ("HSBC", re.compile(r"\bHSBC\b")),
    ("Scotiabank", re.compile(r"SCOTIABANK|SCOTIA\b")),
    ("Inbursa", re.compile(r"\bINBURSA\b")),
    ("Banco Azteca", re.compile(r"BANCO AZTECA")),
    ("BanBajío", re.compile(r"BANBAJ[IÍ]O|BANCO DEL BAJ[IÍ]O")),
    ("Afirme", re.compile(r"\bAFIRME\b")),
    ("Nu", re.compile(r"\bNU M[EÉ]XICO\b|\bNU BN\b|\bNU\b.{0,20}INSTITUCI[OÓ]N")),
    ("Mercado Pago", re.compile(r"MERCADO PAGO")),
    ("Hey Banco", re.compile(r"HEY BANCO|\bHEY,?\s+BANCO")),
    ("Klar", re.compile(r"\bKLAR\b")),
    ("Ualá", re.compile(r"UAL[AÁ]\b")),
]


def fingerprint(text_upper: str) -> tuple[str | None, float]:
    for bank, pattern in FINGERPRINTS:
        if pattern.search(text_upper):
            return bank, 0.9
    return None, 0.3


def parser_id(bank: str | None) -> str:
    # M1 registers "mx.<bank>.<product>.v<n>" parsers; M0 is the generic table parser.
    return "mx.generic.table.v1"
