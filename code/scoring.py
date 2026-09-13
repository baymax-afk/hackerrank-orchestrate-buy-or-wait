"""Regression scoring against dataset/sample_requests.csv (calibration only, never labels)."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from validate import parse_plan


@dataclass
class ScoreReport:
    n: int = 0
    status_ok: int = 0
    method_ok: int = 0
    plan_ok: int = 0
    earliest_ok: int = 0
    changes_ok: int = 0
    amount_exact: int = 0
    rel_errors: list[Decimal] = field(default_factory=list)
    diffs: list[str] = field(default_factory=list)

    def summary(self) -> str:
        mre = (sum(self.rel_errors) / len(self.rel_errors)) if self.rel_errors else Decimal(0)
        return (f"samples={self.n} status={self.status_ok}/{self.n} method={self.method_ok}/{self.n} plan={self.plan_ok}/{self.n} "
                f"earliest={self.earliest_ok}/{self.n} changes={self.changes_ok}/{self.n} amount_exact={self.amount_exact}/{self.n} "
                f"mean_rel_err={mre:.4f}")


def score(predicted: list[dict], labeled: list[dict]) -> ScoreReport:
    rep = ScoreReport()
    truth = {r["request_id"]: r for r in labeled}
    for p in predicted:
        t = truth.get(p["request_id"])
        if t is None:
            continue
        rep.n += 1
        req = Decimal(t["requested_amount"])
        ps, ts = Decimal(p["amount_safe_to_pay"]), Decimal(t["amount_safe_to_pay"])
        rel = abs(ps - ts) / req if req else Decimal(0)
        rep.rel_errors.append(rel)
        if abs(ps - ts) <= Decimal("0.01"):
            rep.amount_exact += 1
        ok_status = p["affordability_status"] == t["affordability_status"]
        ok_method = p["recommended_payment_method"] == t["recommended_payment_method"]
        ok_plan = parse_plan(p["payment_plan"]) == parse_plan(t["payment_plan"])
        ok_earliest = p["earliest_date_for_full_payment"] == t["earliest_date_for_full_payment"]
        ok_changes = set(p["spending_changes_needed"].split("|")) == set(t["spending_changes_needed"].split("|"))
        rep.status_ok += ok_status
        rep.method_ok += ok_method
        rep.plan_ok += ok_plan
        rep.earliest_ok += ok_earliest
        rep.changes_ok += ok_changes
        if not (ok_status and ok_method and ok_earliest and ok_changes):
            rep.diffs.append(
                f"{p['request_id']}: pred {p['affordability_status']}/{p['recommended_payment_method']} safe={p['amount_safe_to_pay']} "
                f"E={p['earliest_date_for_full_payment'] or '-'} ch={p['spending_changes_needed']} | truth {t['affordability_status']}/"
                f"{t['recommended_payment_method']} safe={t['amount_safe_to_pay']} E={t['earliest_date_for_full_payment'] or '-'} ch={t['spending_changes_needed']}"
            )
        elif rel > Decimal("0.05"):
            rep.diffs.append(f"{p['request_id']}: amount {p['amount_safe_to_pay']} vs {t['amount_safe_to_pay']} (rel {rel:.3f})")
    return rep
