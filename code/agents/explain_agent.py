"""Explanation agent: drafts decision_explanation from verified numbers only.

The draft is accepted only if it repeats the decision's key figures verbatim
(method, amounts, dates, minimum); otherwise the deterministic template stands.
"""
from __future__ import annotations

import re
from decimal import Decimal

import config
from evidence.cache import Cache, sha256_text
from formatting import fmt_long_date, fmt_money
from models import Decision, Profile, Request

SYSTEM = (
    "You write one concise, plain-English sentence or two (max 45 words, single line) explaining a personal-finance decision "
    "that has already been made by a deterministic engine. Use only the facts provided; never change amounts, dates, "
    "the recommended method or the minimum balance; never add advice or new numbers. Format money as '<CUR> 1,234.56' "
    "(no decimals when the amount is whole) and dates as '15 November 2019'."
)

SCHEMA = {"type": "object", "properties": {"explanation": {"type": "string"}}, "required": ["explanation"], "additionalProperties": False}


class ExplainAgent:
    def __init__(self, client) -> None:
        self.client = client

    def draft(self, request: Request, profile: Profile, decision: Decision, cache: Cache, refresh: bool = False) -> str:
        fallback = decision.decision_explanation
        cur = profile.home_currency
        facts = {
            "request_id": request.request_id,
            "request_type": request.request_type,
            "requested_amount": fmt_money(cur, request.requested_amount),
            "request_date": fmt_long_date(request.request_date),
            "deadline": fmt_long_date(request.desired_completion_date),
            "minimum_balance_to_keep": fmt_money(cur, profile.minimum_balance_to_keep),
            "amount_safe_to_pay_today": fmt_money(cur, decision.amount_safe_to_pay),
            "status": decision.affordability_status,
            "method": decision.recommended_payment_method,
            "payment_plan": decision.payment_plan,
            "earliest_full_payment_date": fmt_long_date(decision.earliest_date_for_full_payment) if decision.earliest_date_for_full_payment else "none within 90 days",
            "spending_changes": decision.spending_changes_needed,
            "template_version": fallback,
        }
        key_text = "|".join(f"{k}={v}" for k, v in facts.items())
        h = sha256_text(key_text)
        rec = None if refresh else cache.get(request.request_id, h)
        if rec is None:
            content = [{"type": "text", "text": "Facts (JSON-like):\n" + "\n".join(f"{k}: {v}" for k, v in facts.items()) + "\nWrite the explanation."}]
            result = self.client.json_call(agent="explain_agent", source_id=request.request_id, system=SYSTEM, content=content, schema=SCHEMA, max_tokens=300, effort="low")
            if result is None:
                return fallback
            rec = {"source_file": "decision", "source_id": request.request_id, "content_sha256": h, "prompt_version": config.PROMPT_VERSION,
                   "provider": config.PROVIDER, "model": result.get("_model"), "extracted": {"explanation": result.get("explanation", "")}, "usage": result.get("_usage")}
            cache.put(request.request_id, rec)
        text = str(rec.get("extracted", {}).get("explanation", "")).replace("\r", " ").replace("\n", " ").strip()
        if self._grounded(text, facts, decision, cur):
            return text
        return fallback

    @staticmethod
    def _grounded(text: str, facts: dict, decision: Decision, cur: str) -> bool:
        if not text or len(text.split()) > 60:
            return False
        must = [facts["minimum_balance_to_keep"]]
        if decision.recommended_payment_method in ("full_payment", "wait"):
            must.append(facts["requested_amount"])
        if decision.recommended_payment_method == "wait":
            must.append(facts["earliest_full_payment_date"])
        if decision.recommended_payment_method == "partial_payment":
            must.append(facts["amount_safe_to_pay_today"])
        if decision.recommended_payment_method == "installments":
            must.append("installment")
        # numbers in the text must all appear in the facts (no invented figures)
        # a number never ends in a separator, so "2025," or "1,302.40." is read as 2025 / 1,302.40
        number = r"\d(?:[\d,]*\d)?(?:\.\d+)?"
        allowed = set(re.findall(number, " ".join(str(v) for v in facts.values())))
        for num in re.findall(number, text):
            if num not in allowed and num.rstrip("0").rstrip(".") not in {a.rstrip("0").rstrip(".") for a in allowed}:
                return False
        return all(m.lower() in text.lower() for m in must)
