"""Audit agent (optional, --audit): reviews a per-request trace and flags anomalies.

It cannot change any output. Findings are written to evaluation/audit_findings.jsonl
for a human to review.
"""
from __future__ import annotations

import json

import config
from models import Decision, Trace

SYSTEM = (
    "You are a cautious financial-forecast reviewer. You receive the trace of a deterministic decision (evidence facts, "
    "projected cash flows, balance timeline, candidate plans, chosen decision). Flag only concrete, checkable anomalies: "
    "an evidence fact that looks misapplied, a recurring series that looks wrong (cadence/amount), a plan that seems to breach "
    "the minimum balance, or an explanation inconsistent with the numbers. Do not recommend a different decision; report findings."
)

SCHEMA = {
    "type": "object",
    "properties": {
        "findings": {"type": "array", "items": {"type": "object", "properties": {"severity": {"type": "string", "enum": ["info", "warning", "error"]}, "area": {"type": "string"}, "detail": {"type": "string"}}, "required": ["severity", "area", "detail"], "additionalProperties": False}},
        "overall": {"type": "string", "enum": ["consistent", "needs_review"]},
    },
    "required": ["findings", "overall"],
    "additionalProperties": False,
}


class AuditAgent:
    def __init__(self, client) -> None:
        self.client = client
        config.EVALUATION_DIR.mkdir(parents=True, exist_ok=True)
        self.out = config.EVALUATION_DIR / "audit_findings.jsonl"

    def review(self, decision: Decision, trace: Trace) -> dict | None:
        payload = {
            "decision": decision.to_row(),
            "facts": trace.facts,
            "series": trace.series,
            "flows": trace.flows[:120],
            "timeline": trace.timeline[:120],
            "candidates": trace.candidates,
            "notes": trace.notes,
        }
        content = [{"type": "text", "text": json.dumps(payload, default=str)[:60000]}]
        result = self.client.json_call(agent="audit_agent", source_id=decision.request_id, system=SYSTEM, content=content, schema=SCHEMA, max_tokens=1500, effort="low")
        if result is None:
            return None
        rec = {"request_id": decision.request_id, "overall": result.get("overall"), "findings": result.get("findings", []), "model": result.get("_model")}
        with open(self.out, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return rec
