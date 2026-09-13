"""Per-request orchestration: evidence -> ledger -> recurrence -> forecast -> plans -> decision."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Optional

import config
from evidence import messages as msg_rules
from evidence.cache import Cache
from evidence.images import resolve_image
from explain import explain
from forecast import amount_safe_today, build_timeline, earliest_full_payment
from formatting import fmt_plan_amount
from ledger import build_ledger
from loaders import Dataset
from models import Candidate, CashFlow, Decision, EvidenceFact, Request, Trace, numeric_id
from plans import build_candidates, choose
from recurrence import add_months, detect_expense_series, detect_salary_series, project_series


@dataclass
class Services:
    use_llm: bool = False
    refresh_cache: bool = False
    image_agent: object = None
    message_agent: object = None
    explain_agent: object = None
    image_cache: Cache = field(default_factory=lambda: Cache("images"))
    message_cache: Cache = field(default_factory=lambda: Cache("messages"))
    explanation_cache: Cache = field(default_factory=lambda: Cache("explanations"))


def gather_facts(request: Request, ds: Dataset, svc: Services, trace: Trace) -> list[EvidenceFact]:
    facts: list[EvidenceFact] = []
    # messages for this user (all of them predate the request); later messages override earlier ones per kind
    msgs = sorted(ds.messages_by_user.get(request.user_id, []), key=lambda m: (m.sent_at, numeric_id(m.message_id)))
    for m in msgs:
        parsed = msg_rules.parse_message(m)
        if parsed and parsed[0].kind == "unknown" and parsed[0].confidence == 0.0 and svc.use_llm and svc.message_agent is not None and "injection" not in parsed[0].rationale:
            llm_facts = svc.message_agent.extract(m, svc.message_cache, svc.refresh_cache)
            if llm_facts:
                parsed = llm_facts
        facts.extend(parsed)
    # images linked to this user's events
    for img in ds.images_by_user.get(request.user_id, []):
        ev = ds.events_by_id.get(img.related_event_id) if img.related_event_id else None
        facts.append(resolve_image(img, ev, svc.use_llm, svc.image_cache, svc.image_agent, svc.refresh_cache))
    trace.facts = [f.__dict__ | {"amount": str(f.amount) if f.amount is not None else None, "effective_date": f.effective_date.isoformat() if f.effective_date else None, "pct": str(f.pct) if f.pct is not None else None} for f in facts]
    return facts


def decide_request(request: Request, ds: Dataset, svc: Services) -> tuple[Decision, Trace]:
    trace = Trace(request.request_id)
    profile = ds.profiles[request.user_id]
    events = ds.events_by_user.get(request.user_id, [])
    options = ds.options_by_request.get(request.request_id, [])
    facts = gather_facts(request, ds, svc, trace)

    ledger = build_ledger(profile, events, facts, request.request_date, ds.rates)
    trace.notes.extend(ledger.notes)
    if ledger.unknown_amount_ids:
        trace.notes.append(f"unknown amounts (excluded, flagged): {ledger.unknown_amount_ids}")

    series = detect_expense_series(ledger.history, request.request_date, config.ESTIMATOR)
    salary = detect_salary_series(ledger.history, ledger.explicit_flows, ds.events_by_id, facts, request.request_date, trace.notes)
    if salary:
        series.append(salary)
    projected = project_series(series, request.request_date, facts)
    flows: list[CashFlow] = list(ledger.explicit_flows) + projected
    # one-off credits without a date attach to the first salary date on/after the request date
    for f in facts:
        if f.kind == "one_off_credit" and f.amount is not None and f.effective_date is None:
            sal_dates = sorted(c.date for c in flows if c.kind in ("recurring_income", "scheduled") and c.amount > 0)
            if sal_dates:
                flows.append(CashFlow(sal_dates[0], f.amount, "one_off", f.source_id, None, "income", "one-time adjustment"))
                trace.notes.append(f"one-off credit {f.amount} attached to {sal_dates[0]} ({f.source_id})")
    flows.sort(key=lambda c: (c.date, c.kind, c.source_id))
    trace.series = [s.__dict__ | {"anchor": s.anchor.isoformat(), "amount": str(s.amount), "minimum_allowed_amount": str(s.minimum_allowed_amount) if s.minimum_allowed_amount is not None else None} for s in series]
    trace.flows = [{"date": c.date.isoformat(), "amount": str(c.amount), "kind": c.kind, "source": c.source_id, "category": c.category, "description": c.description} for c in flows]

    bal = profile.current_available_balance
    minimum = profile.minimum_balance_to_keep
    tl = build_timeline(bal, flows)
    trace.timeline = [(d.isoformat(), str(b)) for d, b in tl.points]
    safe = amount_safe_today(tl, request.request_date, minimum, request.requested_amount)
    earliest = earliest_full_payment(tl, request.request_date, minimum, request.requested_amount)

    cands = build_candidates(request, profile, options, flows, series, safe, earliest, trace.notes)
    trace.candidates = [{"status": c.status, "method": c.method, "payments": [(d.isoformat(), str(a)) for d, a in c.payments], "changes": [ch.render() for ch in c.changes], "total_paid": str(c.total_paid), "min_balance": str(c.min_projected_balance), "by_deadline": c.completes_by_deadline} for c in cands]
    chosen = choose(cands)
    decision = to_decision(request, profile, chosen, safe, earliest)
    decision.candidate = chosen
    return decision, trace


def to_decision(request: Request, profile, chosen: Optional[Candidate], safe: Decimal, earliest: Optional[date]) -> Decision:
    if chosen is None:
        return Decision(request.request_id, safe, "not_affordable", "not_recommended", "none", None, "none",
                        explain(request, profile, None, safe))
    plan = "|".join(f"{d.isoformat()}:{_plan_amount(chosen, a)}" for d, a in chosen.payments)
    changes = "|".join(c.render() for c in chosen.changes) or "none"
    earliest_out = earliest
    if chosen.status == "affordable_now":
        earliest_out = request.request_date
    return Decision(request.request_id, safe, chosen.status, chosen.method, plan, earliest_out, changes,
                    explain(request, profile, chosen, safe))


def _plan_amount(cand: Candidate, amount: Decimal) -> str:
    if cand.option is not None:
        return cand.option.payment_amount_text
    return fmt_plan_amount(amount)
