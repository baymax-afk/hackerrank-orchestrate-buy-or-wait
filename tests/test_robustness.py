"""Failure-path and end-to-end tests: the run must survive bad rows, bad model output and bad evidence."""
from __future__ import annotations

import csv
import hashlib
import shutil
from datetime import date
from pathlib import Path

import pytest

import config
from conftest import D, make_event

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "dataset"


# --- helpers ---------------------------------------------------------------------------------

def _mini_dataset(tmp_path: Path, request_rows: list[dict] | None = None) -> Path:
    """A private copy of the dataset restricted to a few requests (all other files untouched)."""
    d = tmp_path / "dataset"
    d.mkdir()
    for name in ("financial_profiles.csv", "financial_events.csv", "exchange_rates.csv", "request_payment_options.csv",
                 "messages.csv", "images.csv", "sample_requests.csv"):
        shutil.copy(DATASET / name, d / name)
    shutil.copytree(DATASET / "media", d / "media")
    with open(DATASET / "requests.csv", encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
        fields = list(rows[0].keys())
    rows = request_rows if request_rows is not None else rows[:3]
    with open(d / "requests.csv", "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    return d


def _run(dataset: Path, out: Path, extra: list[str] | None = None) -> tuple[int, list[dict]]:
    from main import main

    rc = main(["--dataset", str(dataset), "--output", str(out), "--no-llm", "--no-traces", *(extra or [])])
    with open(out, encoding="utf-8", newline="") as fh:
        return rc, list(csv.DictReader(fh))


# --- end to end ------------------------------------------------------------------------------

def test_cli_end_to_end_writes_contract_valid_rows(tmp_path):
    d = _mini_dataset(tmp_path)
    rc, rows = _run(d, tmp_path / "out.csv")
    assert rc == 0 and len(rows) == 3
    assert list(rows[0].keys()) == config.OUTPUT_COLUMNS
    assert all(r["affordability_status"] in config.STATUSES for r in rows)


def test_bad_request_row_gets_fallback_and_run_survives(tmp_path):
    with open(DATASET / "requests.csv", encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))[:3]
    rows[1]["requested_amount"] = "-5"          # invalid: must be positive
    rows[2]["requested_amount"] = "100.005"     # invalid: more than two decimals
    d = _mini_dataset(tmp_path, rows)
    rc, out = _run(d, tmp_path / "out.csv")
    assert rc == 1, "a fallback row must make the run exit non-zero"
    assert out[0]["affordability_status"] != "not_affordable" or out[0]["decision_explanation"]
    for bad in out[1:]:
        assert bad["affordability_status"] == "not_affordable" and bad["recommended_payment_method"] == "not_recommended"
        assert bad["payment_plan"] == "none" and bad["spending_changes_needed"] == "none" and bad["amount_safe_to_pay"] == "0"
        assert "No recommendation could be verified" in bad["decision_explanation"]


def test_exception_inside_decide_is_isolated(tmp_path, monkeypatch):
    import main as main_mod

    calls = {"n": 0}
    real = main_mod.decide_request

    def flaky(req, ds, svc):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("boom")
        return real(req, ds, svc)

    monkeypatch.setattr(main_mod, "decide_request", flaky)
    d = _mini_dataset(tmp_path)
    rc, rows = _run(d, tmp_path / "out.csv")
    assert rc == 1 and len(rows) == 3
    assert rows[1]["affordability_status"] == "not_affordable" and "RuntimeError" in rows[1]["decision_explanation"]
    assert rows[0]["affordability_status"] != "" and rows[2]["affordability_status"] != ""


def test_validation_gate_replaces_invalid_row(tmp_path, monkeypatch):
    import main as main_mod
    from models import Decision

    real = main_mod.decide_request

    def corrupt(req, ds, svc):
        decision, trace = real(req, ds, svc)
        if req.request_id == ds.requests[0].request_id:
            # an internally inconsistent row: affordable_now with an empty plan
            decision = Decision(req.request_id, decision.amount_safe_to_pay, "affordable_now", "full_payment", "none", None, "none", "x")
        return decision, trace

    monkeypatch.setattr(main_mod, "decide_request", corrupt)
    d = _mini_dataset(tmp_path)
    rc, rows = _run(d, tmp_path / "out.csv")
    assert rc == 1
    assert rows[0]["affordability_status"] == "not_affordable" and rows[0]["payment_plan"] == "none"


# --- model output / evidence hardening ------------------------------------------------------

def test_model_supplied_regex_pattern_cannot_crash_salary_detection():
    from models import EvidenceFact
    from recurrence import detect_salary_series

    hist = [make_event(event_id=f"s{i}", event_type="income", direction="credit", category="salary", description="Payroll credit base",
                       event_date=date(2025, i, 15), amount=D("100")) for i in range(3, 8)]
    hostile = EvidenceFact("exclude_income", "message", "m1", "messages.csv", pattern="(", extractor="llm")
    s = detect_salary_series(hist, [], {}, [hostile], date(2025, 8, 5), [])
    assert s is not None and s.amount == D("100")
    keyword = EvidenceFact("exclude_income", "message", "m1", "messages.csv", pattern="base", extractor="llm")
    assert detect_salary_series(hist, [], {}, [keyword], date(2025, 8, 5), []) is None


def test_implausible_salary_claim_is_ignored():
    from models import EvidenceFact
    from recurrence import detect_salary_series

    hist = [make_event(event_id=f"s{i}", event_type="income", direction="credit", category="salary", description="Payroll credit",
                       event_date=date(2025, i, 15), amount=D("1000")) for i in range(3, 8)]
    notes: list[str] = []
    absurd = EvidenceFact("salary_amount", "message", "m1", "messages.csv", amount=D("99999999"), currency="EUR", effective_date=date(2025, 9, 15))
    s = detect_salary_series(hist, [], {}, [absurd], date(2025, 8, 5), notes)
    assert s.amount == D("1000") and s.amount_after is None and any("implausible" in n for n in notes)


def test_reviewed_image_value_is_withheld_when_the_file_changed(tmp_path):
    from evidence.cache import Cache
    from evidence.images import REVIEWED, resolve_image
    from models import ImageRef

    # same id, different bytes -> the reviewed reading for image_14 must not be used
    fake = tmp_path / "image_14.png"
    fake.write_bytes(b"\x89PNG not the reviewed file")
    cache = Cache("images", tmp_path / "cache")
    fact = resolve_image(ImageRef("image_14", "user_x", None, "event_9421", str(fake)), None, use_llm=False, cache=cache)
    assert fact.kind == "unknown" and "differs" in fact.rationale
    real = DATASET / "media" / "images" / "image_14.png"
    fact2 = resolve_image(ImageRef("image_14", "user_x", None, "event_9421", str(real)), None, use_llm=False, cache=cache)
    assert fact2.kind == "amount" and str(fact2.amount) == REVIEWED["image_14"][0]
    assert hashlib.sha256(real.read_bytes()).hexdigest()  # sanity: file readable


def test_image_ids_that_are_not_image_n_are_ignored(tmp_path):
    from loaders import load_images

    p = tmp_path / "images.csv"
    p.write_text("image_id,user_id,request_id,related_event_id\n../secret,user_1,,event_1\nimage_02,user_1,,event_2\n", encoding="utf-8")
    refs = load_images(p, tmp_path / "media")
    assert [r.image_id for r in refs] == ["image_02"]


def test_installment_option_without_frequency_is_dropped(tmp_path):
    from loaders import load_options

    p = tmp_path / "opts.csv"
    p.write_text("payment_option_id,request_id,payment_method,payment_amount,number_of_payments,first_payment_date,payment_frequency_days,financing_fee,total_payable_amount\n"
                 "payment_option_1,request_1,installments,10,3,2026-01-01,,0,30\n"
                 "payment_option_2,request_1,full_payment,30,1,2026-01-01,,0,30\n", encoding="utf-8")
    opts = load_options(p)
    assert [o.payment_option_id for o in opts["request_1"]] == ["payment_option_2"]


def test_fx_missing_debit_uses_nearest_dated_rate():
    from ledger import build_ledger
    from conftest import make_profile

    prof = make_profile(home_currency="EUR")
    e = make_event(event_type="expense", direction="debit", category="travel", description="Hotel", currency="USD",
                   amount=D("100"), event_date=date(2026, 3, 10), status="scheduled")
    rates = {(date(2026, 3, 8), "USD", "EUR"): D("0.9")}
    res = build_ledger(prof, [e], [], date(2026, 3, 1), rates)
    assert [f.amount for f in res.explicit_flows] == [D("-90.00")]
    assert any("nearest dated rate" in n for n in res.notes)


def test_llm_client_circuit_breaker_opens_after_consecutive_failures(monkeypatch):
    from agents.client import LLMClient

    c = LLMClient.__new__(LLMClient)
    c._client = object()
    c.calls = 0
    c.consecutive_failures = 0
    c.tripped = False
    import threading

    c._lock = threading.Lock()
    monkeypatch.setattr(config, "LLM_CIRCUIT_BREAKER", 2)
    monkeypatch.setattr(c, "_log_usage", lambda rec: None)
    assert c.available()
    c._failure("explain_agent", "request_1", "APIError")
    assert c.available()
    c._failure("explain_agent", "request_2", "APIError")
    assert not c.available(), "after N consecutive failures the client must stop calling"
