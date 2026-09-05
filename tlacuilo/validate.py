"""The arithmetic that lets a consumer trust a parse without a human: opening balance
plus every signed row must equal the closing balance. It is the ONLY field a consumer
may auto-commit on (RFC 0040 §2)."""

from __future__ import annotations

from decimal import Decimal

from .contract import Validation
from .parsers.common import money
from .parsers.generic_statement import ParsedStatement

_TOLERANCE = Decimal("0.01")


def validate_statement(parsed: ParsedStatement) -> Validation:
    warnings = list(parsed.warnings)
    total = sum((r.amount for r in parsed.rows), Decimal("0"))
    if parsed.opening is None or parsed.closing is None:
        warnings.append("opening or closing balance not found: reconciliation impossible")
        return Validation(
            balanceReconciles=None,
            sumOfRows=money(total),
            expectedDelta=None,
            unparsedLines=parsed.unparsed_lines,
            warnings=warnings,
        )
    expected = parsed.closing - parsed.opening
    reconciles = abs(total - expected) <= _TOLERANCE
    if not reconciles:
        warnings.append("rows do not reconcile the opening and closing balances")
    return Validation(
        balanceReconciles=reconciles,
        sumOfRows=money(total),
        expectedDelta=money(expected),
        unparsedLines=parsed.unparsed_lines,
        warnings=warnings,
    )
