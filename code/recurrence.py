"""Detect recurring expense / income series from settled history and project them.

Rules (see research/synthesis.md §5):
* expenses are grouped by (event_type, category); >= MIN_OCCURRENCES occurrences and a
  weekly / biweekly / monthly median interval make a series; one-off descriptions are
  dropped from the estimate; projection starts from the last occurrence.
* salary is projected monthly at the confirmed amount (scheduled row / message /
  last regular payroll); unconfirmed income (bonus, commission, gig payouts, ...) is never projected.
"""
from __future__ import annotations

import calendar
import re
import statistics
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

import config
from formatting import q2
from models import CashFlow, Event, EvidenceFact, Profile, RecurringSeries

NON_RECURRING_INCOME = re.compile(config.NON_RECURRING_INCOME_PATTERN, re.I)
FINAL_INCOME = re.compile(config.FINAL_INCOME_PATTERN, re.I)
EXPENSE_TYPES = ("expense", "subscription", "debt_payment")


def add_months(d: date, k: int) -> date:
    m = d.month - 1 + k
    y = d.year + m // 12
    m = m % 12 + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def _cadence(dates: list[date]) -> tuple[Optional[str], Optional[int]]:
    if len(dates) < 2:
        return None, None
    ivs = [(dates[i + 1] - dates[i]).days for i in range(len(dates) - 1)]
    med = statistics.median(ivs)
    for name, (lo, hi, step) in config.CADENCES.items():
        if lo <= med <= hi:
            return name, step
    return None, None


def _estimate(amounts: list[Decimal], estimator: str) -> Decimal:
    if estimator == "mean":
        return q2(sum(amounts) / len(amounts))
    if estimator == "median":
        return q2(Decimal(str(statistics.median([float(a) for a in amounts]))))
    if estimator == "last":
        return amounts[-1]
    if estimator == "max":
        return max(amounts)
    if estimator == "min":
        return min(amounts)
    if estimator == "mean_recent":
        recent = amounts[-6:]
        return q2(sum(recent) / len(recent))
    raise ValueError(f"unknown estimator {estimator}")


def _mode(values: list[Decimal]) -> Decimal:
    counts: dict[Decimal, int] = {}
    for v in values:
        counts[v] = counts.get(v, 0) + 1
    best = max(counts.values())
    # ties -> the most recent value among the tied ones
    for v in reversed(values):
        if counts[v] == best:
            return v
    return values[-1]


def _project(anchor: date, cadence: str, step: Optional[int], request_date: date, end: date) -> list[date]:
    out = []
    k = 1
    while True:
        nd = anchor + timedelta(days=step * k) if step else add_months(anchor, k)
        if nd > end:
            break
        if nd >= request_date:
            out.append(nd)
        k += 1
        if k > 400:
            break
    return out


def detect_expense_series(history: list[Event], request_date: date, estimator: str = config.ESTIMATOR) -> list[RecurringSeries]:
    groups: dict[tuple[str, str], list[Event]] = defaultdict(list)
    for e in history:
        if e.event_type in EXPENSE_TYPES and e.direction == "debit" and e.amount is not None:
            groups[(e.event_type, e.category)].append(e)
    series: list[RecurringSeries] = []
    for (etype, category), evs in sorted(groups.items()):
        evs.sort(key=lambda e: (e.cash_date, e.num))
        # drop description singletons when the series is long enough to be sure they are one-offs
        if len(evs) >= 5:
            counts = defaultdict(int)
            for e in evs:
                counts[e.description] += 1
            core = [e for e in evs if counts[e.description] > 1]
            if len(core) >= config.MIN_OCCURRENCES:
                evs = core
        if len(evs) < config.MIN_OCCURRENCES:
            continue
        dates = [e.cash_date for e in evs]
        cadence, step = _cadence(dates)
        if cadence is None:
            continue
        amounts = [e.amount for e in evs]
        rep = evs[-1]
        fixed = len(set(amounts)) == 1
        amount = amounts[-1] if fixed else _estimate(amounts, estimator)
        series.append(
            RecurringSeries(
                series_id=rep.event_id,
                category=category,
                description=rep.description,
                cadence=cadence,
                step_days=step,
                anchor=dates[-1],
                amount=amount,
                is_income=False,
                flexibility=rep.flexibility,
                minimum_allowed_amount=rep.minimum_allowed_amount,
                occurrences=len(evs),
            )
        )
    return series


def detect_salary_series(history: list[Event], explicit_flows: list[CashFlow], events_by_id: dict[str, Event],
                         facts: list[EvidenceFact], request_date: date, notes: list[str]) -> Optional[RecurringSeries]:
    """Monthly salary projection with evidence overrides. Returns None when no confirmed salary recurs."""
    regular = [e for e in history if e.event_type == "income" and e.category == "salary" and not NON_RECURRING_INCOME.search(e.description)]
    excl = [f for f in facts if f.kind == "exclude_income" and f.pattern]
    if excl:
        # patterns are keywords (possibly model-supplied): escape them so they can never be regex syntax
        pat = re.compile("|".join(re.escape(f.pattern.strip()) for f in excl if f.pattern.strip()) or r"(?!x)x", re.I)
        regular = [e for e in regular if not pat.search(e.description)]
    regular.sort(key=lambda e: (e.cash_date, e.num))
    scheduled = [c for c in explicit_flows if c.kind == "scheduled" and c.amount > 0 and events_by_id.get(c.source_id) and events_by_id[c.source_id].event_type == "income"]
    scheduled.sort(key=lambda c: c.date)

    kinds = {f.kind: f for f in facts if f.kind.startswith("salary_")}
    if "salary_stop" in kinds:
        notes.append(f"salary recurrence stopped by {kinds['salary_stop'].source_id}")
        return None
    all_salary = sorted([e for e in history if e.event_type == "income" and e.category == "salary"], key=lambda e: (e.cash_date, e.num))
    if all_salary and FINAL_INCOME.search(all_salary[-1].description) and "salary_first" not in kinds and not scheduled:
        notes.append(f"salary series ends with '{all_salary[-1].description}'; not projected")
        return None

    anchor: Optional[date] = None
    amount: Optional[Decimal] = None
    if scheduled:
        anchor = scheduled[-1].date
        amount = scheduled[-1].amount
    elif regular:
        dates = [e.cash_date for e in regular]
        cadence, _ = _cadence(dates)
        if cadence != "monthly" and len(regular) >= 2 and "salary_first" not in kinds:
            # an irregular history (e.g. unpaid leave) is not projected unless the employer
            # confirms the resumed salary and its date (handled by salary_first below)
            notes.append("salary history is not monthly; not projected")
            return None
        anchor = dates[-1]
        amount = regular[-1].amount
    if "salary_first" in kinds and kinds["salary_first"].amount is not None and kinds["salary_first"].effective_date:
        f = kinds["salary_first"]
        if not any(c.date == f.effective_date for c in scheduled):
            anchor = add_months(f.effective_date, -1)  # first occurrence lands on effective_date
        amount = f.amount
    if anchor is None or amount is None:
        return None
    if regular and not scheduled:
        # the most common settled amount is the confirmed regular salary (a single deviating month is not a new level)
        amount = _mode([e.amount for e in regular])
    amount_after = None
    for key in ("salary_remaining", "salary_amount", "salary_next_amount"):
        if key in kinds and kinds[key].amount is not None:
            stated = kinds[key].amount
            eff = kinds[key].effective_date
            if stated <= 0 or (regular and stated > amount * config.SALARY_PLAUSIBILITY_FACTOR):
                # a stated level far outside the settled history is treated as unconfirmed evidence
                notes.append(f"salary amount {stated} stated by {kinds[key].source_id} is implausible vs history {amount}; ignored")
                break
            if key == "salary_remaining" or config.SALARY_MESSAGE_AMOUNTS or stated < amount:
                # an explicitly remaining/reduced salary is adopted; a stated increase is not counted
                # until it settles (financially safer interpretation of an unsettled claim) ...
                amount = stated
                notes.append(f"salary amount {amount} from {kinds[key].source_id} ({key})")
            elif eff is not None and eff > request_date:
                # ... unless the employer confirms the new level with an explicit effective date: the
                # rise is then a confirmed change applied from that payroll date onwards, not before.
                amount_after = (eff, q2(stated))
                notes.append(f"salary rises to {stated} from {eff} per {kinds[key].source_id}; {amount} until then")
            else:
                notes.append(f"salary amount {stated} stated by {kinds[key].source_id} not adopted (history {amount}, not yet settled)")
            break
    if "salary_date" in kinds and kinds["salary_date"].effective_date and not scheduled:
        anchor = add_months(kinds["salary_date"].effective_date, -1)
    rep = regular[-1].event_id if regular else (scheduled[-1].source_id if scheduled else "salary")
    return RecurringSeries(rep, "salary", "salary", "monthly", None, anchor, q2(amount), True, "fixed", None, len(regular), amount_after)


def project_series(series: list[RecurringSeries], request_date: date, facts: list[EvidenceFact]) -> list[CashFlow]:
    end = request_date + timedelta(days=config.HORIZON_DAYS)
    rent_scale = None
    for f in facts:
        if f.kind == "rent_scale" and f.pct is not None:
            rent_scale = (Decimal(1) + f.pct / Decimal(100))
    flows: list[CashFlow] = []
    for s in series:
        amount = s.amount
        if rent_scale and not s.is_income and s.category in ("rent", "housing"):
            amount = q2(amount * rent_scale)
        for d in _project(s.anchor, s.cadence, s.step_days, request_date, end):
            amt = s.amount_after[1] if s.amount_after and d >= s.amount_after[0] else amount
            flows.append(CashFlow(d, amt if s.is_income else -amt, "recurring_income" if s.is_income else "recurring", s.series_id, s.series_id, s.category, s.description,
                                  after_credits=(not s.is_income and s.cadence == "monthly")))
    return flows
