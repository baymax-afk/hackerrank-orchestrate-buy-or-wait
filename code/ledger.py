"""Turn events + resolved evidence into dated home-currency cash flows.

Only the *explicit* future is produced here (scheduled rows, pending debits,
settled rows dated on/after the request date, evidence-driven one-offs).
Projected recurring flows come from recurrence.py.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

import config
from formatting import q2
from fx import FxMissing, convert, nearest_rate
from models import CashFlow, Event, EvidenceFact, Profile

LIFECYCLE_KINDS = {"amount", "amend_event_date", "amend_event_amount", "retry_failed_debit", "salary_date"}


@dataclass
class LedgerResult:
    explicit_flows: list[CashFlow]
    history: list[Event]  # settled, unlinked, before request date, amounts resolved (home currency)
    excluded_history_ids: set[str]
    unknown_amount_ids: list[str]
    notes: list[str]


def _home(event: Event, profile: Profile, rates: dict, notes: list[str]) -> Optional[Decimal]:
    if event.amount is None:
        return None
    on = event.cash_date
    try:
        return convert(event.amount, event.currency, profile.home_currency, on, rates)
    except FxMissing as exc:
        if event.direction == "credit":
            notes.append(f"{event.event_id}: {exc}; credit excluded from cash (safer)")
            return None
        near = nearest_rate(event.currency, profile.home_currency, on, rates)
        if near is None:
            notes.append(f"{event.event_id}: {exc} and no rate for the pair at all; debit amount unknown")
            return None
        rate, d = near
        notes.append(f"{event.event_id}: {exc}; debit converted with the nearest dated rate ({d.isoformat()})")
        return q2(event.amount * rate)


def apply_event_facts(events: list[Event], facts: list[EvidenceFact], notes: list[str]) -> list[Event]:
    """Resolve blank amounts / amendments on specific event rows. Later facts win (caller orders them)."""
    by_id = {e.event_id: e for e in events}
    for f in facts:
        if f.kind == "salary_date" and f.effective_date:
            # a confirmed payroll date change amends the scheduled salary row (amendment beats forecast)
            for e in list(by_id.values()):
                if e.status == "scheduled" and e.event_type == "income" and e.category == "salary":
                    by_id[e.event_id] = replace(e, settlement_date=f.effective_date, event_date=f.effective_date)
                    notes.append(f"{e.event_id}: salary date moved to {f.effective_date} by {f.source_id}")
            continue
        if f.kind not in LIFECYCLE_KINDS or not f.target_event_id or f.target_event_id not in by_id:
            continue
        e = by_id[f.target_event_id]
        if f.kind == "amount" and f.amount is not None:
            if e.amount is None:
                by_id[e.event_id] = replace(e, amount=f.amount)
                notes.append(f"{e.event_id}: amount {f.amount} from {f.source_kind} {f.source_id} ({f.extractor}, conf {f.confidence:.2f})")
        elif f.kind == "amend_event_date" and f.effective_date and e.status == "scheduled":
            by_id[e.event_id] = replace(e, settlement_date=f.effective_date, event_date=f.effective_date)
            notes.append(f"{e.event_id}: date moved to {f.effective_date} by {f.source_id}")
        elif f.kind == "amend_event_amount" and f.amount is not None and e.status in ("scheduled", "pending"):
            by_id[e.event_id] = replace(e, amount=f.amount)
            notes.append(f"{e.event_id}: amount amended to {f.amount} by {f.source_id}")
        elif f.kind == "retry_failed_debit" and e.status == "failed" and e.direction == "debit":
            by_id[e.event_id] = replace(e, status="pending")
            notes.append(f"{e.event_id}: failed debit will be retried -> reserved as pending ({f.source_id})")
    return [by_id[e.event_id] for e in events]


def build_ledger(profile: Profile, events: list[Event], facts: list[EvidenceFact], request_date: date, rates: dict) -> LedgerResult:
    notes: list[str] = []
    events = apply_event_facts(events, facts, notes)
    horizon_end = request_date + timedelta(days=config.HORIZON_DAYS)
    linked_targets = {e.linked_event_id for e in events if e.linked_event_id}
    transfer_ids: set[str] = set()
    for f in facts:
        if f.kind == "internal_transfer":
            transfer_ids |= _find_transfer_pairs(events)
    flows: list[CashFlow] = []
    history: list[Event] = []
    unknown: list[str] = []
    excluded: set[str] = set(transfer_ids)

    for e in events:
        if e.direction == "non_cash" or e.status in ("failed", "cancelled", "unrealized"):
            continue
        if e.cash_date is None:
            continue
        amt = _home(e, profile, rates, notes)
        if e.status == "pending":
            if e.direction == "debit":
                if amt is None:
                    unknown.append(e.event_id)
                    continue
                flows.append(CashFlow(max(e.cash_date, request_date), -amt, "pending", e.event_id, None, e.category, e.description))
            # pending credits are never counted
            continue
        if e.status == "scheduled":
            if e.cash_date < request_date:
                continue  # stale scheduled row in the past: treat as not happening (no evidence it settled)
            if amt is None:
                unknown.append(e.event_id)
                continue
            if e.cash_date <= horizon_end:
                flows.append(CashFlow(e.cash_date, amt if e.direction == "credit" else -amt, "scheduled", e.event_id, None, e.category, e.description))
            continue
        # settled
        if e.cash_date >= request_date:
            if amt is None:
                unknown.append(e.event_id)
                continue
            if e.event_id in transfer_ids:
                continue
            if e.cash_date <= horizon_end:
                flows.append(CashFlow(e.cash_date, amt if e.direction == "credit" else -amt, "settled", e.event_id, None, e.category, e.description))
            continue
        # settled before the request date -> history (already inside the balance)
        if e.linked_event_id or e.event_id in linked_targets or e.event_id in transfer_ids:
            excluded.add(e.event_id)
            continue
        if amt is None:
            unknown.append(e.event_id)
            excluded.add(e.event_id)
            continue
        history.append(replace(e, amount=amt, currency=profile.home_currency))

    # evidence-driven one-off credits (confirmed invoice / arrears) on their date
    for f in facts:
        if f.kind == "one_off_credit" and f.amount is not None and f.effective_date and request_date <= f.effective_date <= horizon_end:
            flows.append(CashFlow(f.effective_date, f.amount, "one_off", f.source_id, None, "income", f.rationale[:60]))
            notes.append(f"one-off credit {f.amount} on {f.effective_date} from {f.source_id}")

    flows.sort(key=lambda c: (c.date, c.kind, c.source_id))
    return LedgerResult(flows, history, excluded, unknown, notes)


def _find_transfer_pairs(events: list[Event]) -> set[str]:
    """Matching debit/credit with equal amount within 3 days (own-account transfer)."""
    out: set[str] = set()
    settled = [e for e in events if e.status == "settled" and e.amount is not None]
    credits = [e for e in settled if e.direction == "credit"]
    for d in settled:
        if d.direction != "debit":
            continue
        for c in credits:
            if c.amount == d.amount and c.currency == d.currency and abs((c.cash_date - d.cash_date).days) <= 3 and c.event_id not in out:
                out.add(d.event_id)
                out.add(c.event_id)
                break
    return out
