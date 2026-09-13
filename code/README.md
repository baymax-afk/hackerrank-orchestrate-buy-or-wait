# Buy or Wait? — solution package

Deterministic financial-decision engine with bounded LLM agents for evidence interpretation.
Produces the root-level `output.csv` (one row per request in `dataset/requests.csv`).

## Setup

```bash
python --version            # 3.10+ (developed on 3.14); stdlib only for the core
pip install -r requirements.txt   # anthropic (optional, for the agents) + pytest (dev)
cp .env.example .env        # optional: ANTHROPIC_API_KEY=... enables the agents
```

Run from the repository root (the folder that contains `dataset/`):

```bash
python code/main.py                     # full run -> ./output.csv + code/evaluation/usage_report.md
python code/main.py --no-llm            # fully deterministic run (caches + reviewed image table + rule-based messages)
python code/main.py --sample-check      # score against dataset/sample_requests.csv (calibration only)
python code/main.py --explain request_42 --no-llm   # print the ledger, timeline and candidates for one request
python code/main.py --audit             # additionally run the read-only audit agent (evaluation/audit_findings.jsonl)
python -m pytest tests -q               # tests (single test: python -m pytest tests/test_core.py::test_fx_exact_date_and_direction -q)
python code/tools/build_zip.py          # package code.zip
```

On Windows use `python` (or `py`), not `python3`.

## How it works

1. `loaders.py` — schema-validated CSV loading, indexes by user / request / event, Decimal money.
2. `evidence/` — messages parsed by EN/ID template rules into closed-schema facts (`EvidenceFact`); images resolved through a cached vision agent, cross-checked against a reviewed reading table (disagreement → financially safer value). An injection guard rejects instruction-like text.
3. `ledger.py` — events + facts → dated home-currency cash flows: pending debits reserved, pending credits / failed / cancelled / unrealized ignored, FX at the settlement-date rate, blank amounts filled from images (never zero).
4. `recurrence.py` — recurring expense series (weekly / biweekly / monthly) from settled history; monthly salary projected at the confirmed level (scheduled row, message, or settled history mode); unconfirmed income (gig payouts, commissions, bonuses, prizes) never projected.
5. `forecast.py` — end-of-day balance timeline over 90 days; `amount_safe_to_pay` and `earliest_date_for_full_payment` in closed form.
6. `plans.py` — candidates (full, partial, each supplied installment option, full + permitted spending changes, wait), simulation against the minimum balance, ranking: deadline → no changes → total paid → earlier start → fewer payments → lowest option id.
7. `explain.py` / `agents/explain_agent.py` — explanation from verified numbers (LLM draft accepted only if every figure matches; deterministic template otherwise).
8. `validate.py` — every row and the file checked against the output contract before writing.
9. `usage.py` — `evaluation/usage_report.md` from the per-call `usage.jsonl`.

Agents (`agents/`): image OCR, message fallback, explanation drafting, optional audit. They never write a financial recommendation; all outputs are schema-validated, cached with provenance under `code/cache/`, and reproducible without an API key.

Configuration knobs live in `config.py` (`ESTIMATOR`, `HORIZON_DAYS`, model names, price table).
