"""Unit tests for the deterministic core: loaders, fx, ledger, recurrence, forecast, capacity."""
import io
from datetime import date
from decimal import Decimal

import pytest

import config
from conftest import D, make_event, make_profile, make_request, monthly, weekly
from forecast import amount_safe_today, build_timeline, earliest_full_payment, plan_is_safe
from fx import FxMissing, convert
from ledger import build_ledger
from loaders import SchemaError, load_events, load_profiles
from models import EvidenceFact
from recurrence import add_months, detect_expense_series, detect_salary_series, project_series

RATES = {(date(2026, 3, 15), "USD", "EUR"): D("0.92")}


# --- loaders ----------------------------------------------------------------

def test_loader_blank_amount_is_none_not_zero(tmp_path):
    p = tmp_path / "financial_events.csv"
    p.write_text(
        "event_id,user_id,event_type,description,category,direction,amount,currency,event_date,settlement_date,status,linked_event_id,flexibility,minimum_allowed_amount\r\n"
        "event_1,user_1,expense,Bill,utilities,debit,,INR,2026-01-01,2026-01-03,pending,,fixed,\r\n"
        "event_2,user_1,subscription,Stream,streaming,debit,47,USD,2026-01-09,2026-01-09,settled,,reducible_or_stoppable,23.5\r\n",
        encoding="utf-8",
    )
    events = load_events(p)
    assert events[0].amount is None
    assert events[1].flexibility == "reducible_or_stoppable" and events[1].minimum_allowed_amount == D("23.5")


def test_loader_rejects_bad_header(tmp_path):
    p = tmp_path / "financial_profiles.csv"
    p.write_text("user_id,home_currency\r\nuser_1,EUR\r\n", encoding="utf-8")
    with pytest.raises(SchemaError):
        load_profiles(p)


def test_loader_profile_blank_lists(tmp_path):
    p = tmp_path / "financial_profiles.csv"
    p.write_text(
        "user_id,home_currency,current_available_balance,minimum_balance_to_keep,financial_priorities,expense_categories_to_protect,expense_categories_user_is_willing_to_reduce,expense_categories_user_is_willing_to_stop,payment_methods_user_will_consider,max_installment_months\r\n"
        "user_1,ZAR,100.5,50,education|travel,rent,,,full_payment,\r\n",
        encoding="utf-8",
    )
    prof = load_profiles(p)["user_1"]
    assert prof.reduce == frozenset() and prof.stop == frozenset() and prof.max_installment_months is None
    assert prof.current_available_balance == D("100.5")


# --- fx -----------------------------------------------------------------------

def test_fx_exact_date_and_direction():
    assert convert(D("100"), "USD", "EUR", date(2026, 3, 15), RATES) == D("92.00")
    with pytest.raises(FxMissing):
        convert(D("100"), "EUR", "USD", date(2026, 3, 15), RATES)  # reverse direction is not implied
    with pytest.raises(FxMissing):
        convert(D("100"), "USD", "EUR", date(2026, 3, 16), RATES)  # other dates are not interpolated
    assert convert(D("5"), "EUR", "EUR", date(2026, 1, 1), {}) == D("5")


# --- ledger -------------------------------------------------------------------

def test_ledger_statuses():
    prof = make_profile()
    rd = date(2026, 3, 3)
    events = [
        make_event(description="pending card", status="pending", event_date=date(2026, 3, 2), settlement_date=date(2026, 3, 5), amount=D("40"), category="shopping"),
        make_event(description="pending refund", event_type="refund", direction="credit", status="pending", event_date=date(2026, 3, 4), amount=D("999")),
        make_event(description="failed", status="failed", event_date=date(2026, 3, 4), amount=D("70")),
        make_event(description="cancelled", status="cancelled", event_date=date(2026, 3, 4), amount=D("80")),
        make_event(description="valuation", event_type="investment_valuation", direction="non_cash", status="unrealized", event_date=date(2026, 3, 4), settlement_date=None, amount=D("5000")),
        make_event(description="Next confirmed salary", event_type="income", category="salary", direction="credit", status="scheduled", event_date=date(2026, 3, 15), amount=D("1500")),
        make_event(description="school fee", status="scheduled", event_date=date(2026, 3, 20), amount=D("200"), category="education"),
    ]
    res = build_ledger(prof, events, [], rd, {})
    kinds = {(f.kind, f.amount) for f in res.explicit_flows}
    assert ("pending", D("-40")) in kinds
    assert ("scheduled", D("1500")) in kinds and ("scheduled", D("-200")) in kinds
    assert not any(f.amount == D("999") for f in res.explicit_flows)
    assert not any(f.amount in (D("-70"), D("-80"), D("-5000")) for f in res.explicit_flows)


def test_ledger_pending_debit_before_request_date_is_reserved_today():
    prof = make_profile()
    ev = make_event(status="pending", event_date=date(2026, 2, 20), settlement_date=date(2026, 2, 22), amount=D("40"))
    res = build_ledger(prof, [ev], [], date(2026, 3, 3), {})
    assert res.explicit_flows[0].date == date(2026, 3, 3)


def test_ledger_linked_lifecycle_rows_excluded_from_history():
    prof = make_profile()
    charge = make_event(event_id="event_900", description="Card charge later reversed", category="shopping", event_date=date(2026, 1, 25), amount=D("58"))
    refund = make_event(event_id="event_901", event_type="refund", direction="credit", description="reversal", category="shopping", event_date=date(2026, 1, 28), amount=D("58"), linked_event_id="event_900")
    normal = make_event(event_id="event_902", event_date=date(2026, 2, 1))
    res = build_ledger(prof, [charge, refund, normal], [], date(2026, 3, 3), {})
    assert {e.event_id for e in res.history} == {"event_902"}
    assert {"event_900", "event_901"} <= res.excluded_history_ids


def test_ledger_foreign_currency_uses_settlement_date_rate():
    prof = make_profile()
    ev = make_event(event_type="income", category="salary", direction="credit", status="scheduled", currency="USD", event_date=date(2026, 3, 15), amount=D("1000"))
    res = build_ledger(prof, [ev], [], date(2026, 3, 3), RATES)
    assert res.explicit_flows[0].amount == D("920.00")


def test_ledger_image_amount_fact_fills_blank_never_zero():
    prof = make_profile()
    ev = make_event(event_id="event_77", status="scheduled", event_date=date(2026, 3, 16), amount=None, category="rent")
    res = build_ledger(prof, [ev], [], date(2026, 3, 3), {})
    assert res.unknown_amount_ids == ["event_77"] and not res.explicit_flows
    fact = EvidenceFact("amount", "image", "image_x", "x.png", target_event_id="event_77", amount=D("100000"), confidence=0.9)
    res2 = build_ledger(prof, [ev], [fact], date(2026, 3, 3), {})
    assert res2.explicit_flows[0].amount == D("-100000")


def test_ledger_retry_failed_debit_fact():
    prof = make_profile()
    ev = make_event(event_id="event_5", status="failed", event_date=date(2026, 3, 1), amount=D("60"))
    fact = EvidenceFact("retry_failed_debit", "message", "message_1", "messages.csv", target_event_id="event_5")
    res = build_ledger(prof, [ev], [fact], date(2026, 3, 3), {})
    assert res.explicit_flows and res.explicit_flows[0].amount == D("-60")


# --- recurrence -----------------------------------------------------------------

def test_recurrence_monthly_fixed_and_weekly_variable():
    rd = date(2026, 3, 3)
    hist = monthly(date(2025, 10, 2), 5, description="Rent", category="rent", amount=D("500"))
    hist += [make_event(event_date=date(2026, 1, 1) + __import__("datetime").timedelta(days=7 * k), description="Groceries", category="groceries", amount=D(80 + 10 * (k % 3))) for k in range(8)]
    hist += [make_event(event_date=date(2026, 2, 10), description="One-off", category="healthcare", amount=D("300"))]
    series = detect_expense_series(hist, rd, "median")
    by_cat = {s.category: s for s in series}
    assert by_cat["rent"].cadence == "monthly" and by_cat["rent"].amount == D("500")
    assert by_cat["groceries"].cadence == "weekly"
    assert "healthcare" not in by_cat  # single occurrence is not recurring
    flows = project_series(series, rd, [])
    rent_dates = sorted(f.date for f in flows if f.category == "rent")
    assert rent_dates[0] == date(2026, 3, 2) if date(2026, 3, 2) >= rd else rent_dates[0] == date(2026, 4, 2)
    assert all(rd <= f.date <= rd + __import__("datetime").timedelta(days=config.HORIZON_DAYS) for f in flows)


def test_recurrence_singleton_description_dropped_from_estimate():
    rd = date(2026, 3, 3)
    hist = weekly(date(2026, 1, 1), 8, description="Groceries", category="groceries", amount=D("80"))
    hist.append(make_event(event_date=date(2026, 2, 20), description="Bulk one-off", category="groceries", amount=D("4000")))
    s = [x for x in detect_expense_series(hist, rd, "mean") if x.category == "groceries"][0]
    assert s.amount == D("80")


def test_salary_projection_rules():
    rd = date(2026, 3, 3)
    hist = monthly(date(2025, 10, 15), 5, event_type="income", category="salary", direction="credit", description="Payroll credit", amount=D("1500"))
    s = detect_salary_series(hist, [], {}, [], rd, [])
    assert s and s.amount == D("1500") and s.anchor == date(2026, 2, 15)
    # gig payouts are never projected
    gig = weekly(date(2026, 1, 1), 8, event_type="income", category="salary", direction="credit", description="Delivery platform payout", amount=D("100"))
    assert detect_salary_series(gig, [], {}, [], rd, []) is None
    # final payroll stops the series
    hist_final = hist[:-1] + [make_event(event_date=date(2026, 2, 15), event_type="income", category="salary", direction="credit", description="Final employer payroll", amount=D("1500"))]
    assert detect_salary_series(hist_final, [], {}, [], rd, []) is None
    # a message that ends employment stops it too
    stop = EvidenceFact("salary_stop", "message", "message_9", "messages.csv")
    assert detect_salary_series(hist, [], {}, [stop], rd, []) is None
    # a stated lower amount is adopted, a stated increase is not (until it settles)
    lower = EvidenceFact("salary_next_amount", "message", "message_4", "messages.csv", amount=D("1000"))
    assert detect_salary_series(hist, [], {}, [lower], rd, []).amount == D("1000")
    higher = EvidenceFact("salary_amount", "message", "message_1", "messages.csv", amount=D("9000"))
    assert detect_salary_series(hist, [], {}, [higher], rd, []).amount == D("1500")
    # one deviating month does not change the regular level (mode wins)
    hist_dev = hist[:-1] + [make_event(event_date=date(2026, 2, 15), event_type="income", category="salary", direction="credit", description="Payroll credit", amount=D("700"))]
    assert detect_salary_series(hist_dev, [], {}, [], rd, []).amount == D("1500")


def test_rent_scale_fact_applies_to_projection():
    rd = date(2026, 3, 3)
    hist = monthly(date(2025, 10, 2), 5, description="Rent", category="rent", amount=D("500"))
    series = detect_expense_series(hist, rd, "median")
    flows = project_series(series, rd, [EvidenceFact("rent_scale", "message", "message_12", "messages.csv", pct=D("12"))])
    assert all(f.amount == D("-560.00") for f in flows if f.category == "rent")


# --- forecast / capacity -------------------------------------------------------------

def test_capacity_closed_form_matches_brute_force():
    from models import CashFlow

    rd = date(2026, 3, 3)
    flows = [
        CashFlow(date(2026, 3, 5), D("-400"), "recurring", "e1"),
        CashFlow(date(2026, 3, 15), D("1500"), "recurring_income", "e2"),
        CashFlow(date(2026, 4, 2), D("-900"), "recurring", "e3"),
        CashFlow(date(2026, 4, 15), D("1500"), "recurring_income", "e4"),
        CashFlow(date(2026, 5, 2), D("-900"), "recurring", "e5"),
    ]
    bal, minimum = D("3000"), D("1000")
    tl = build_timeline(bal, flows)
    safe = amount_safe_today(tl, rd, minimum, D("100000"))
    # brute force: largest x such that every end-of-day balance stays >= minimum
    best = D(0)
    x = D(0)
    while x <= D("3000"):
        ok, _ = plan_is_safe(bal, flows, [(rd, x)], rd, minimum)
        if ok:
            best = x
        x += D("1")
    assert safe == best == D("1600")


def test_earliest_date_allows_payment_on_the_salary_day():
    from models import CashFlow

    rd = date(2026, 3, 3)
    flows = [CashFlow(date(2026, 3, 15), D("1500"), "recurring_income", "e2"), CashFlow(date(2026, 4, 2), D("-900"), "recurring", "e3")]
    tl = build_timeline(D("1500"), flows)
    assert earliest_full_payment(tl, rd, D("1000"), D("1000")) == date(2026, 3, 15)
    assert earliest_full_payment(tl, rd, D("1000"), D("5000")) is None


def test_minimum_balance_protected_end_of_day():
    from models import CashFlow

    rd = date(2026, 3, 3)
    # a monthly bill co-dated with payday is paid out of that salary (nets end-of-day) ...
    flows = [CashFlow(date(2026, 3, 10), D("-2500"), "recurring", "e1", after_credits=True), CashFlow(date(2026, 3, 10), D("2500"), "recurring_income", "e2")]
    ok, m = plan_is_safe(D("3000"), flows, [(rd, D("2000"))], rd, D("1000"))
    assert ok and m == D("1000")
    ok2, _ = plan_is_safe(D("3000"), flows, [(rd, D("2001"))], rd, D("1000"))
    assert not ok2
    # ... while variable spending on payday must be covered by the balance carried into the day
    variable = [CashFlow(date(2026, 3, 10), D("-2500"), "recurring", "e1"), flows[1]]
    assert not plan_is_safe(D("3000"), variable, [(rd, D("2000"))], rd, D("1000"))[0]


def test_add_months_clamps_day():
    assert add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert add_months(date(2025, 12, 15), 1) == date(2026, 1, 15)


def test_resumed_salary_message_overrides_irregular_history():
    """An employer message confirming the resumed salary and its date projects income even when
    the settled history has a leave gap (otherwise the non-monthly guard drops the series)."""
    from datetime import date as _d

    from models import EvidenceFact
    from recurrence import detect_salary_series

    hist = [
        make_event(event_id="s1", event_type="income", direction="credit", category="salary", description="Payroll before leave", event_date=_d(2025, 3, 15), amount=D("2717")),
        make_event(event_id="s2", event_type="income", direction="credit", category="salary", description="Payroll before leave", event_date=_d(2025, 4, 15), amount=D("2717")),
        make_event(event_id="s3", event_type="income", direction="credit", category="salary", description="Payroll after returning from leave", event_date=_d(2025, 7, 15), amount=D("2717")),
    ]
    notes: list[str] = []
    assert detect_salary_series(hist, [], {}, [], _d(2025, 8, 4), notes) is None
    fact = EvidenceFact("salary_first", "message", "m1", "messages.csv", amount=D("2717"), currency="EUR", effective_date=_d(2025, 8, 15))
    s = detect_salary_series(hist, [], {}, [fact], _d(2025, 8, 4), notes)
    assert s is not None and s.amount == D("2717") and s.anchor == _d(2025, 7, 15)


def test_salary_rise_with_effective_date_applies_from_that_payroll():
    from datetime import date as _d

    from models import EvidenceFact
    from recurrence import detect_salary_series, project_series

    hist = [make_event(event_id=f"s{i}", event_type="income", direction="credit", category="salary", description="Payroll credit", event_date=_d(2025, i, 15), amount=D("100")) for i in range(3, 8)]
    notes: list[str] = []
    dated = EvidenceFact("salary_amount", "message", "m1", "messages.csv", amount=D("150"), currency="EUR", effective_date=_d(2025, 9, 15))
    s = detect_salary_series(hist, [], {}, [dated], _d(2025, 8, 5), notes)
    flows = {f.date: f.amount for f in project_series([s], _d(2025, 8, 5), [])}
    assert flows[_d(2025, 8, 15)] == D("100") and flows[_d(2025, 9, 15)] == D("150")
    undated = EvidenceFact("salary_amount", "message", "m2", "messages.csv", amount=D("150"), currency="EUR")
    s2 = detect_salary_series(hist, [], {}, [undated], _d(2025, 8, 5), notes)
    assert s2.amount == D("100") and s2.amount_after is None


def test_intraday_variable_spending_precedes_payday_credit_but_monthly_bills_do_not():
    from datetime import date as _d

    from forecast import build_timeline
    from models import CashFlow

    config.INTRADAY_DEBITS_FIRST = True
    payday = _d(2026, 3, 15)
    salary = CashFlow(payday, D("1000"), "recurring_income", "s")
    groceries = CashFlow(payday, D("-100"), "recurring", "g", after_credits=False)
    childcare = CashFlow(payday, D("-200"), "recurring", "c", after_credits=True)
    tl = build_timeline(D("500"), [salary, groceries, childcare])
    # groceries must be covered by the 500 carried in; childcare is paid from the salary
    assert tl.min_from(_d(2026, 3, 1)) == D("400") and tl.balance_on(payday) == D("1200")
    config.INTRADAY_DEBITS_FIRST = False
    assert build_timeline(D("500"), [salary, groceries, childcare]).min_from(_d(2026, 3, 1)) == D("500")
    config.INTRADAY_DEBITS_FIRST = True
