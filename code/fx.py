"""Fixed, dated exchange-rate lookup."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from formatting import q2


class FxMissing(LookupError):
    pass


def convert(amount: Decimal, from_ccy: str, to_ccy: str, on: date, rates: dict) -> Decimal:
    """Convert using the exact (settlement date, from, to) row; raise FxMissing otherwise."""
    if from_ccy == to_ccy:
        return amount
    rate = rates.get((on, from_ccy, to_ccy))
    if rate is None:
        raise FxMissing(f"no rate for {from_ccy}->{to_ccy} on {on.isoformat()}")
    return q2(amount * rate)
