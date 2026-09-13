import os
import sys
from datetime import date, timedelta
from decimal import Decimal

import pytest

CODE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "code")
if CODE_DIR not in sys.path:
    sys.path.insert(0, CODE_DIR)

from models import Event, PaymentOption, Profile, Request  # noqa: E402


def D(x):
    return Decimal(str(x))


def make_profile(**kw) -> Profile:
    base = dict(
        user_id="user_t",
        home_currency="EUR",
        current_available_balance=D("3000"),
        minimum_balance_to_keep=D("1000"),
        priorities=("emergency_savings",),
        protect=frozenset({"rent", "groceries"}),
        reduce=frozenset({"dining", "streaming"}),
        stop=frozenset({"streaming", "cloud_storage"}),
        methods=frozenset({"full_payment", "partial_payment", "installments"}),
        max_installment_months=6,
    )
    base.update(kw)
    return Profile(**base)


_counter = {"n": 0}


def make_event(**kw) -> Event:
    _counter["n"] += 1
    base = dict(
        event_id=f"event_{_counter['n']}",
        user_id="user_t",
        event_type="expense",
        description="Rent",
        category="rent",
        direction="debit",
        amount=D("500"),
        currency="EUR",
        event_date=date(2026, 1, 1),
        settlement_date=date(2026, 1, 1),
        status="settled",
        linked_event_id=None,
        flexibility="fixed",
        minimum_allowed_amount=None,
    )
    base.update(kw)
    if "settlement_date" not in kw and "event_date" in kw:
        base["settlement_date"] = kw["event_date"]
    return Event(**base)


def monthly(start: date, n: int, **kw) -> list[Event]:
    from recurrence import add_months

    return [make_event(event_date=add_months(start, k), **kw) for k in range(n)]


def weekly(start: date, n: int, **kw) -> list[Event]:
    return [make_event(event_date=start + timedelta(days=7 * k), **kw) for k in range(n)]


def make_request(**kw) -> Request:
    base = dict(
        request_id="request_t",
        user_id="user_t",
        request_date=date(2026, 3, 3),
        request_type="purchase",
        requested_amount=D("1000"),
        requested_amount_text="1000",
        desired_completion_date=date(2026, 4, 30),
        allows_partial_payment=True,
        request_text="Can I buy this?",
    )
    base.update(kw)
    return Request(**base)


def make_option(**kw) -> PaymentOption:
    base = dict(
        payment_option_id="payment_option_1",
        request_id="request_t",
        payment_method="installments",
        payment_amount=D("350"),
        payment_amount_text="350",
        number_of_payments=3,
        first_payment_date=date(2026, 3, 10),
        payment_frequency_days=30,
        financing_fee=D("50"),
        total_payable_amount=D("1050"),
    )
    base.update(kw)
    return PaymentOption(**base)


@pytest.fixture
def helpers():
    return dict(D=D, make_profile=make_profile, make_event=make_event, monthly=monthly, weekly=weekly, make_request=make_request, make_option=make_option)
