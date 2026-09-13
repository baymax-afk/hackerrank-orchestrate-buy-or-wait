"""Number and date formatting that mirrors dataset/sample_requests.csv exactly."""
from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal

CENT = Decimal("0.01")


def q2(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_EVEN)


def fmt_safe_amount(value: Decimal) -> str:
    """Shortest representation, ≤ 2 dp, trailing zeros stripped: 603.3, 25256, 87170.56."""
    value = q2(value)
    text = f"{value:f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def fmt_plan_amount(value: Decimal) -> str:
    """Integer when integral, otherwise fixed 2 dp: 25256, 620.40, 15952906.67."""
    value = q2(value)
    if value == value.to_integral_value():
        return f"{value.to_integral_value():f}"
    return f"{value:.2f}"


def fmt_money(currency: str, value: Decimal) -> str:
    """Explanation style: 'ZAR 25,256', 'EUR 620.40', 'IDR 15,952,906.67'."""
    value = q2(value)
    if value == value.to_integral_value():
        return f"{currency} {int(value):,}"
    return f"{currency} {value:,.2f}"


def fmt_long_date(d: date) -> str:
    """'15 November 2019' (no leading zero)."""
    return f"{d.day} {d.strftime('%B %Y')}"
