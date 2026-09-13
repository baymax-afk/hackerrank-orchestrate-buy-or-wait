"""Deterministic decision explanations in the style of dataset/sample_requests.csv."""
from __future__ import annotations

from decimal import Decimal

from formatting import fmt_long_date, fmt_money
from models import Candidate, Profile, Request


def _describe_changes(cand: Candidate, currency: str) -> str:
    parts = []
    for c in cand.changes:
        name = c.description.lower() if c.description else c.event_id
        if c.action == "stop":
            parts.append(f"stop the {name}")
        else:
            parts.append(f"reduce the {name} to {fmt_money(currency, c.new_amount)}")
    if not parts:
        return ""
    text = parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]
    return text[0].upper() + text[1:]


def explain(request: Request, profile: Profile, cand: Candidate | None, safe_today: Decimal) -> str:
    cur = profile.home_currency
    req = fmt_money(cur, request.requested_amount)
    minimum = fmt_money(cur, profile.minimum_balance_to_keep)
    deadline = fmt_long_date(request.desired_completion_date)
    if cand is None:
        if Decimal(0) < safe_today < request.requested_amount and "partial_payment" in profile.methods and request.allows_partial_payment:
            return (f"Do not proceed with the {req} request. Although {fmt_money(cur, safe_today)} is available today, "
                    f"the full amount cannot be completed safely within the forecast period.")
        return f"Do not make this payment by {deadline}. None of the available options keeps the {minimum} minimum protected."
    if cand.method == "full_payment" and cand.status == "affordable_now":
        return f"Pay {req} today. This leaves at least {minimum} available throughout the forecast period."
    if cand.method == "full_payment":
        return f"{_describe_changes(cand, cur)}, then pay {req} today. This leaves at least {minimum} available."
    if cand.method == "installments":
        n = cand.n_payments
        return (f"Use {n} installments of {fmt_money(cur, cand.payments[0][1])}, starting {fmt_long_date(cand.start_date)}. "
                f"This leaves at least {minimum} available.")
    if cand.method == "partial_payment":
        first, second = cand.payments
        return (f"Pay {fmt_money(cur, first[1])} today and the remaining {fmt_money(cur, second[1])} on {fmt_long_date(second[0])}. "
                f"This completes the full request and keeps the {minimum} minimum protected.")
    if cand.method == "wait":
        return (f"Pay {req} in full on {fmt_long_date(cand.start_date)}. "
                f"Paying earlier would take the balance below the {minimum} minimum.")
    return f"Do not make this payment by {deadline}. None of the available options keeps the {minimum} minimum protected."
