"""Deterministic output validation (row + file level). Every rule cites the contract."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Optional

import config
from loaders import Dataset
from models import Event, PaymentOption, Profile, Request

PLAN_ENTRY = re.compile(r"^(\d{4}-\d{2}-\d{2}):(\d+(?:\.\d{1,2})?)$")
CHANGE = re.compile(r"^(stop:event_\d+|reduce_to:event_\d+:\d+(?:\.\d{1,2})?)$")


@dataclass
class Violation:
    request_id: str
    column: str
    code: str
    severity: str  # error | warning
    message: str

    def __str__(self) -> str:
        return f"[{self.severity}] {self.request_id} {self.column} {self.code}: {self.message}"


def _dec(text: str) -> Optional[Decimal]:
    try:
        return Decimal(text)
    except (InvalidOperation, TypeError):
        return None


def parse_plan(text: str) -> Optional[list[tuple[date, Decimal]]]:
    if text == "none":
        return []
    out = []
    for part in text.split("|"):
        m = PLAN_ENTRY.match(part)
        if not m:
            return None
        out.append((date.fromisoformat(m.group(1)), Decimal(m.group(2))))
    return out


def validate_row(row: dict, request: Request, profile: Profile, options: list[PaymentOption], events_by_id: dict[str, Event]) -> list[Violation]:
    v: list[Violation] = []
    rid = request.request_id

    def err(col, code, msg):
        v.append(Violation(rid, col, code, "error", msg))

    def warn(col, code, msg):
        v.append(Violation(rid, col, code, "warning", msg))

    status = row.get("affordability_status", "")
    method = row.get("recommended_payment_method", "")
    if status not in config.STATUSES:
        err("affordability_status", "ENUM", status)
    if method not in config.METHODS:
        err("recommended_payment_method", "ENUM", method)
    safe = _dec(row.get("amount_safe_to_pay", ""))
    if safe is None:
        err("amount_safe_to_pay", "NUMERIC", row.get("amount_safe_to_pay"))
        safe = Decimal(0)
    if safe < 0 or safe > request.requested_amount:
        err("amount_safe_to_pay", "BOUNDS", f"{safe} not in [0, {request.requested_amount}]")
    if safe.as_tuple().exponent < -2:
        err("amount_safe_to_pay", "PRECISION", str(safe))

    plan = parse_plan(row.get("payment_plan", ""))
    if plan is None:
        err("payment_plan", "GRAMMAR", row.get("payment_plan"))
        plan = []
    rd = request.request_date
    horizon = rd + timedelta(days=config.HORIZON_DAYS)
    for i, (d, a) in enumerate(plan):
        if d < rd or d > horizon:
            err("payment_plan", "DATE_RANGE", f"{d} outside [{rd}, {horizon}]")
        if i and d <= plan[i - 1][0]:
            err("payment_plan", "CHRONOLOGY", "dates not strictly increasing")
    earliest_txt = row.get("earliest_date_for_full_payment", "")
    earliest = None
    if earliest_txt:
        try:
            earliest = date.fromisoformat(earliest_txt)
            if earliest < rd or earliest > horizon:
                err("earliest_date_for_full_payment", "DATE_RANGE", earliest_txt)
        except ValueError:
            err("earliest_date_for_full_payment", "DATE_FORMAT", earliest_txt)
    changes_txt = row.get("spending_changes_needed", "")
    changes = [] if changes_txt == "none" else changes_txt.split("|")
    if changes_txt != "none":
        if not changes or len(changes) > 3:
            err("spending_changes_needed", "COUNT", changes_txt)
        for c in changes:
            if not CHANGE.match(c):
                err("spending_changes_needed", "GRAMMAR", c)

    pairs = {"affordable_now": {"full_payment"}, "affordable_with_plan": {"full_payment", "partial_payment", "installments"},
             "affordable_later": {"wait"}, "not_affordable": {"not_recommended"}}
    if status in pairs and method not in pairs[status]:
        err("recommended_payment_method", "STATUS_METHOD", f"{status}/{method}")

    req = request.requested_amount
    if status == "affordable_now":
        if plan != [(rd, req)]:
            err("payment_plan", "FULL_PLAN", "must be request_date:requested_amount")
        if earliest != rd:
            err("earliest_date_for_full_payment", "EARLIEST_NOW", "must equal request_date")
        if safe != req:
            err("amount_safe_to_pay", "SAFE_EQ_REQ", f"{safe} != {req}")
        if changes:
            err("spending_changes_needed", "NO_CHANGES", "affordable_now cannot need changes")
        if "full_payment" not in profile.methods:
            err("recommended_payment_method", "NOT_ACCEPTED", "user does not consider full_payment")
    elif status == "affordable_later":
        if not earliest or earliest <= rd:
            err("earliest_date_for_full_payment", "EARLIEST_LATER", earliest_txt)
        if earliest and plan != [(earliest, req)]:
            err("payment_plan", "WAIT_PLAN", "must be earliest_date:requested_amount")
        if changes:
            err("spending_changes_needed", "NO_CHANGES", "wait cannot need changes")
        if "full_payment" not in profile.methods:
            err("recommended_payment_method", "NOT_ACCEPTED", "wait requires full_payment acceptance")
    elif status == "not_affordable":
        if plan:
            err("payment_plan", "NONE_EXPECTED", row.get("payment_plan"))
        if earliest_txt:
            warn("earliest_date_for_full_payment", "EMPTY_EXPECTED", earliest_txt)
        if changes:
            err("spending_changes_needed", "NO_CHANGES", changes_txt)
    elif status == "affordable_with_plan":
        if method == "partial_payment":
            if not request.allows_partial_payment:
                err("recommended_payment_method", "PARTIAL_NOT_ALLOWED", "request disallows partial payment")
            if "partial_payment" not in profile.methods:
                err("recommended_payment_method", "NOT_ACCEPTED", "user does not consider partial_payment")
            if not (Decimal(0) < safe < req):
                err("amount_safe_to_pay", "PARTIAL_RANGE", str(safe))
            if len(plan) != 2:
                err("payment_plan", "PARTIAL_TWO", "exactly two payments required")
            else:
                (d1, a1), (d2, a2) = plan
                if d1 != rd or a1 != safe:
                    err("payment_plan", "PARTIAL_FIRST", "first payment must be request_date:amount_safe_to_pay")
                if earliest is None or d2 != earliest:
                    err("payment_plan", "PARTIAL_SECOND_DATE", "second payment must be on earliest_date_for_full_payment")
                if d2 > request.desired_completion_date:
                    err("payment_plan", "PARTIAL_DEADLINE", f"{d2} after {request.desired_completion_date}")
                if a1 + a2 != req:
                    err("payment_plan", "PARTIAL_SUM", f"{a1}+{a2} != {req}")
        elif method == "installments":
            if "installments" not in profile.methods or profile.max_installment_months is None:
                err("recommended_payment_method", "NOT_ACCEPTED", "user does not consider installments")
            match = None
            for opt in options:
                if opt.payment_method == "installments" and list(opt.schedule()) == plan:
                    match = opt
            if match is None:
                err("payment_plan", "OPTION_MATCH", "plan does not match any supplied installment option")
            else:
                if profile.max_installment_months is not None and match.number_of_payments > profile.max_installment_months:
                    err("payment_plan", "MONTH_LIMIT", f"{match.number_of_payments} > {profile.max_installment_months}")
                if plan[-1][0] > request.desired_completion_date:
                    warn("payment_plan", "AFTER_DEADLINE", f"last payment {plan[-1][0]} after {request.desired_completion_date}")
        elif method == "full_payment":
            if plan != [(rd, req)]:
                err("payment_plan", "FULL_PLAN", "must be request_date:requested_amount")
            if not changes:
                err("spending_changes_needed", "CHANGES_REQUIRED", "full payment under affordable_with_plan needs spending changes")
            if "full_payment" not in profile.methods:
                err("recommended_payment_method", "NOT_ACCEPTED", "user does not consider full_payment")
    # spending-change permissions
    seen = set()
    for c in changes:
        if not CHANGE.match(c):
            continue
        parts = c.split(":")
        action, eid = parts[0], parts[1]
        ev = events_by_id.get(eid)
        if ev is None or ev.user_id != request.user_id:
            err("spending_changes_needed", "EVENT", f"{eid} unknown or not the user's")
            continue
        if eid in seen:
            err("spending_changes_needed", "DUPLICATE_EVENT", eid)
        seen.add(eid)
        if ev.direction != "debit" or ev.flexibility == "fixed" or ev.category in profile.protect:
            err("spending_changes_needed", "NOT_FLEXIBLE", f"{eid} {ev.flexibility} {ev.category}")
        if action == "stop":
            if ev.flexibility not in ("stoppable", "reducible_or_stoppable") or ev.category not in profile.stop:
                err("spending_changes_needed", "STOP_NOT_PERMITTED", f"{eid} {ev.category} {ev.flexibility}")
        else:
            new_amt = Decimal(parts[2])
            if ev.flexibility not in ("reducible", "reducible_or_stoppable") or ev.category not in profile.reduce:
                err("spending_changes_needed", "REDUCE_NOT_PERMITTED", f"{eid} {ev.category} {ev.flexibility}")
            if ev.minimum_allowed_amount is not None and new_amt < ev.minimum_allowed_amount:
                err("spending_changes_needed", "BELOW_FLOOR", f"{new_amt} < {ev.minimum_allowed_amount}")
            if ev.amount is not None and new_amt >= ev.amount:
                err("spending_changes_needed", "NOT_A_REDUCTION", f"{new_amt} >= {ev.amount}")
    if changes and status != "affordable_with_plan":
        err("spending_changes_needed", "STATUS", "changes only allowed with affordable_with_plan")
    expl = row.get("decision_explanation", "")
    if not expl.strip() or "\n" in expl or "\r" in expl:
        err("decision_explanation", "TEXT", "must be a non-empty single line")
    elif profile.home_currency not in expl:
        warn("decision_explanation", "CURRENCY", "explanation does not mention the currency")
    return v


def validate_rows(rows: list[dict], ds: Dataset, requests: Optional[list[Request]] = None) -> list[Violation]:
    requests = requests or ds.requests
    by_id = {r.request_id: r for r in requests}
    out: list[Violation] = []
    ids = [r.get("request_id") for r in rows]
    if len(rows) != len(requests):
        out.append(Violation("*", "request_id", "ROW_COUNT", "error", f"{len(rows)} rows, expected {len(requests)}"))
    if len(set(ids)) != len(ids):
        out.append(Violation("*", "request_id", "DUPLICATE", "error", "duplicate request ids"))
    missing = set(by_id) - set(ids)
    if missing:
        out.append(Violation("*", "request_id", "MISSING", "error", f"missing {sorted(missing)[:5]}..."))
    for row in rows:
        if list(row.keys()) != config.OUTPUT_COLUMNS:
            out.append(Violation(row.get("request_id", "?"), "*", "COLUMNS", "error", str(list(row.keys()))))
        req = by_id.get(row.get("request_id"))
        if req is None:
            out.append(Violation(row.get("request_id", "?"), "request_id", "UNKNOWN", "error", "not in requests"))
            continue
        out.extend(validate_row(row, req, ds.profiles[req.user_id], ds.options_by_request.get(req.request_id, []), ds.events_by_id))
    return out

