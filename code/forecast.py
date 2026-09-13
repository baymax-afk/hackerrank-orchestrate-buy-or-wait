"""End-of-day balance timeline and the closed-form safety checks."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Iterable, Optional

import config
from models import CashFlow


@dataclass(frozen=True)
class Timeline:
    start_balance: Decimal
    points: tuple[tuple[date, Decimal, Decimal], ...]  # (day, intra-day low, end-of-day balance) on flow dates, ascending

    def balance_on(self, d: date) -> Decimal:
        """Balance at the end of day d (carry-forward)."""
        bal = self.start_balance
        for t, _, b in self.points:
            if t <= d:
                bal = b
            else:
                break
        return bal

    def min_from(self, d: date) -> Decimal:
        """Minimum balance over [d, horizon end] — the quantity a payment on d must protect.

        Within a day, debits are applied before credits (INTRADAY_DEBITS_FIRST), so a bill
        due on payday must be covered by the balance carried into that day.
        """
        m = self.balance_on(d)
        for t, low, b in self.points:
            if t > d and min(low, b) < m:
                m = min(low, b)
        return m


def build_timeline(start_balance: Decimal, flows: Iterable[CashFlow], extra: Iterable[tuple[date, Decimal]] = ()) -> Timeline:
    by_day: dict[date, Decimal] = defaultdict(Decimal)
    debits: dict[date, Decimal] = defaultdict(Decimal)
    for f in flows:
        by_day[f.date] += f.amount
        if f.amount < 0:
            debits[f.date] += f.amount
    for d, amt in extra:
        # plan payments are made once the day's credits have landed (end of day)
        by_day[d] += amt
    pts = []
    bal = start_balance
    for d in sorted(by_day):
        low = bal + debits[d] if config.INTRADAY_DEBITS_FIRST else bal + by_day[d]
        bal += by_day[d]
        pts.append((d, low, bal))
    return Timeline(start_balance, tuple(pts))


def amount_safe_today(tl: Timeline, request_date: date, minimum: Decimal, requested: Decimal) -> Decimal:
    room = tl.min_from(request_date) - minimum
    return max(Decimal(0), min(requested, room))


def earliest_full_payment(tl: Timeline, request_date: date, minimum: Decimal, requested: Decimal) -> Optional[date]:
    for k in range(config.HORIZON_DAYS + 1):
        d = request_date + timedelta(days=k)
        if tl.min_from(d) - minimum >= requested:
            return d
    return None


def plan_is_safe(start_balance: Decimal, flows: list[CashFlow], payments: Iterable[tuple[date, Decimal]], request_date: date, minimum: Decimal) -> tuple[bool, Decimal]:
    tl = build_timeline(start_balance, flows, [(d, -a) for d, a in payments])
    m = tl.min_from(request_date)
    return m >= minimum, m
