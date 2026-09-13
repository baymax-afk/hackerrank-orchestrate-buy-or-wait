"""Candidate plan generation, spending-change search and challenge-rule ranking."""
from __future__ import annotations

import itertools
from datetime import date
from decimal import Decimal
from typing import Optional

import config
from forecast import plan_is_safe
from models import Candidate, CashFlow, Change, PaymentOption, Profile, RecurringSeries, Request


def installment_eligible(opt: PaymentOption, profile: Profile, request: Request) -> tuple[bool, str]:
    if opt.payment_method != "installments":
        return False, "not an installment option"
    if "installments" not in profile.methods or profile.max_installment_months is None:
        return False, "user does not consider installments"
    if opt.number_of_payments > profile.max_installment_months:
        return False, f"{opt.number_of_payments} payments exceed max {profile.max_installment_months} months"
    return True, ""


def build_candidates(request: Request, profile: Profile, options: list[PaymentOption], flows: list[CashFlow],
                     series: list[RecurringSeries], safe_today: Decimal, earliest: Optional[date], trace_notes: list[str]) -> list[Candidate]:
    rd = request.request_date
    req = request.requested_amount
    dl = request.desired_completion_date
    bal = profile.current_available_balance
    minimum = profile.minimum_balance_to_keep
    cands: list[Candidate] = []

    def simulate(payments, changes=()):
        mod = flows if not changes else apply_changes(flows, changes)
        return plan_is_safe(bal, mod, payments, rd, minimum)

    # full payment today
    if "full_payment" in profile.methods:
        ok, m = simulate([(rd, req)])
        if ok:
            cands.append(Candidate("affordable_now", "full_payment", ((rd, req),), (), req, None, m, True))
        else:
            trace_notes.append("full payment today breaches the minimum")

    # partial payment: safe amount today + remainder on the earliest full-payment date
    if "partial_payment" in profile.methods and request.allows_partial_payment:
        if Decimal(0) < safe_today < req and earliest and rd < earliest <= dl:
            payments = ((rd, safe_today), (earliest, req - safe_today))
            ok, m = simulate(payments)
            if ok:
                cands.append(Candidate("affordable_with_plan", "partial_payment", payments, (), req, None, m, True))
            else:
                trace_notes.append("partial plan breaches the minimum (remainder too large on earliest date)")
        else:
            trace_notes.append("partial payment not applicable (safe amount / earliest date / deadline)")

    # supplied installment options
    for opt in options:
        ok_elig, why = installment_eligible(opt, profile, request)
        if not ok_elig:
            if opt.payment_method == "installments":
                trace_notes.append(f"{opt.payment_option_id}: {why}")
            continue
        payments = opt.schedule()
        ok, m = simulate(payments)
        if ok:
            cands.append(Candidate("affordable_with_plan", "installments", payments, (), opt.total_payable_amount, opt, m, payments[-1][0] <= dl))
        else:
            trace_notes.append(f"{opt.payment_option_id}: schedule breaches the minimum")

    # full payment today enabled by permitted spending changes
    if "full_payment" in profile.methods and not any(c.method == "full_payment" for c in cands):
        best = search_changes(flows, series, profile, [(rd, req)], rd, minimum, bal)
        if best is not None:
            changes, m = best
            cands.append(Candidate("affordable_with_plan", "full_payment", ((rd, req),), changes, req, None, m, True))

    # wait for a later single full payment
    if "full_payment" in profile.methods and earliest and earliest > rd:
        ok, m = simulate([(earliest, req)])
        if ok:
            cands.append(Candidate("affordable_later", "wait", ((earliest, req),), (), req, None, m, earliest <= dl))
    return cands


def rank_key(c: Candidate):
    return (
        0 if c.completes_by_deadline else 1,
        len(c.changes),
        c.total_paid,
        c.start_date,
        c.n_payments,
        c.option.num if c.option else 0,
    )


def choose(cands: list[Candidate]) -> Optional[Candidate]:
    if not cands:
        return None
    return sorted(cands, key=rank_key)[0]


# --- spending changes ---------------------------------------------------------

def permitted_actions(series: list[RecurringSeries], profile: Profile) -> list[Change]:
    acts: list[Change] = []
    for s in series:
        if s.is_income or s.flexibility == "fixed" or s.category in profile.protect:
            continue
        if s.flexibility in ("stoppable", "reducible_or_stoppable") and s.category in profile.stop:
            acts.append(Change("stop", s.series_id, None, s.description))
        if s.flexibility in ("reducible", "reducible_or_stoppable") and s.category in profile.reduce and s.minimum_allowed_amount is not None and s.minimum_allowed_amount < s.amount:
            acts.append(Change("reduce_to", s.series_id, s.minimum_allowed_amount, s.description))
    return acts


def apply_changes(flows: list[CashFlow], changes: tuple[Change, ...]) -> list[CashFlow]:
    by_event = {c.event_id: c for c in changes}
    out = []
    for f in flows:
        c = by_event.get(f.series_id) if f.series_id else None
        if c is None:
            out.append(f)
        elif c.action == "stop":
            continue
        else:
            out.append(CashFlow(f.date, -c.new_amount, f.kind, f.source_id, f.series_id, f.category, f.description))
    return out


def _saving(change: Change, flows: list[CashFlow]) -> Decimal:
    total = Decimal(0)
    for f in flows:
        if f.series_id == change.event_id:
            total += -f.amount if change.action == "stop" else (-f.amount - change.new_amount)
    return total


def search_changes(flows, series, profile, payments, request_date, minimum, start_balance) -> Optional[tuple[tuple[Change, ...], Decimal]]:
    acts = permitted_actions(series, profile)
    if not acts:
        return None
    # deterministic order: stops before reductions, larger savings first, then event id
    acts.sort(key=lambda a: (0 if a.action == "stop" else 1, -_saving(a, flows), a.event_id))
    tried = 0
    for k in (1, 2, 3):
        for combo in itertools.combinations(acts, k):
            if len({c.event_id for c in combo}) < k:
                continue  # never stop and reduce the same event
            tried += 1
            if tried > config.MAX_CHANGE_COMBINATIONS:
                return None
            ok, m = plan_is_safe(start_balance, apply_changes(flows, combo), payments, request_date, minimum)
            if ok:
                ordered = tuple(sorted(combo, key=lambda c: (0 if c.action == "stop" else 1, c.event_id)))
                return ordered, m
    return None
