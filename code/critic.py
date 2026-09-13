"""Deterministic critic: re-verifies a decision from the rendered row, independently of how it was built.

The pipeline simulates every candidate before ranking; this module repeats the safety checks from the
*output strings* (the plan and the changes as they will be written) so that a formatting slip, a stale
candidate, or a bug in candidate construction cannot ship. A failed check raises CriticError, which the
per-request fault boundary in main.py turns into a fallback row.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from forecast import plan_is_safe
from models import CashFlow, Change, Decision, Profile, RecurringSeries, Request
from plans import apply_changes
from validate import parse_plan

CENT = Decimal("0.01")


class CriticError(AssertionError):
    pass


def _changes_from_text(text: str, series: list[RecurringSeries]) -> tuple[Change, ...]:
    if text == "none":
        return ()
    by_id = {s.series_id: s for s in series}
    out = []
    for part in text.split("|"):
        bits = part.split(":")
        if bits[0] == "stop":
            out.append(Change("stop", bits[1]))
        else:
            out.append(Change("reduce_to", bits[1], Decimal(bits[2])))
        if bits[1] not in by_id:
            raise CriticError(f"change {part} references a series that was not projected")
    return tuple(out)


def verify(decision: Decision, request: Request, profile: Profile, flows: list[CashFlow], series: list[RecurringSeries]) -> None:
    bal, minimum, rd = profile.current_available_balance, profile.minimum_balance_to_keep, request.request_date
    safe = decision.amount_safe_to_pay

    # 1. amount_safe_to_pay is the largest amount that is safe today (before any spending change)
    if safe < 0 or safe > request.requested_amount:
        raise CriticError(f"amount_safe_to_pay {safe} outside [0, {request.requested_amount}]")
    if safe > 0 and not plan_is_safe(bal, flows, [(rd, safe)], rd, minimum)[0]:
        raise CriticError(f"paying amount_safe_to_pay {safe} today breaches the minimum on re-simulation")
    if safe < request.requested_amount and plan_is_safe(bal, flows, [(rd, safe + CENT)], rd, minimum)[0]:
        raise CriticError(f"amount_safe_to_pay {safe} is not maximal: {safe + CENT} is also safe")

    # 2. the shipped plan (with the shipped changes) keeps the minimum across the window
    plan = parse_plan(decision.payment_plan)
    if plan is None:
        raise CriticError(f"unparseable plan {decision.payment_plan!r}")
    if decision.recommended_payment_method == "not_recommended":
        if plan:
            raise CriticError("not_recommended must carry no plan")
        return
    if not plan:
        raise CriticError(f"{decision.recommended_payment_method} without a plan")
    changes = _changes_from_text(decision.spending_changes_needed, series)
    mod = apply_changes(flows, changes) if changes else flows
    ok, m = plan_is_safe(bal, mod, plan, rd, minimum)
    if not ok:
        raise CriticError(f"shipped plan breaches the minimum on re-simulation (min {m} < {minimum})")
    if sum(a for _, a in plan) != request.requested_amount and decision.recommended_payment_method != "installments":
        raise CriticError("plan payments do not sum to the requested amount")
    if any(d < rd for d, _ in plan):
        raise CriticError("plan starts before the request date")
    if any(plan[i][0] >= plan[i + 1][0] for i in range(len(plan) - 1)):
        raise CriticError("plan dates are not strictly increasing")
    if any(a <= 0 for _, a in plan):
        raise CriticError("plan contains a non-positive payment")

    # 3. earliest date is consistent with the status
    e = decision.earliest_date_for_full_payment
    if decision.affordability_status == "affordable_now" and e != rd:
        raise CriticError("affordable_now must have earliest = request_date")
    if decision.recommended_payment_method == "wait" and (e is None or plan[0][0] != e):
        raise CriticError("wait plan must be dated on the earliest full-payment date")
    if e is not None and e > rd and not plan_is_safe(bal, flows, [(e, request.requested_amount)], rd, minimum)[0]:
        raise CriticError(f"earliest date {e} is not actually safe for a full payment on re-simulation")
