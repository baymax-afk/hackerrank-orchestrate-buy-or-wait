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
python code/tools/confidence.py         # perturbation ensemble -> evaluation/confidence.jsonl (per-row decision stability)
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
8. `critic.py` — independent re-verification of every decision from its rendered row (plan re-simulated with
   the shipped changes, `amount_safe_to_pay` checked to be maximal, earliest date re-simulated); a failure
   becomes a fallback row. `validate.py` — every row checked against the output contract before writing.
9. `usage.py` — `evaluation/usage_report.md` from the per-call `usage.jsonl`.

Agents (`agents/`): image OCR, message fallback, explanation drafting, optional audit. They never write a financial recommendation; all outputs are schema-validated, cached with provenance under `code/cache/`, and reproducible without an API key.

Configuration knobs live in `config.py` (`ESTIMATOR`, `HORIZON_DAYS`, model names, price table).

## Robustness contract

- Every request is decided inside its own fault boundary. An exception (or a request that violates the
  contract preconditions, e.g. a non-positive amount) yields a contract-valid fallback row
  (`not_affordable / not_recommended`, amount 0, explanation naming the reason), the error is logged, and
  the process exits non-zero. One bad row can never lose the run.
- Every row is validated against the output contract before it is written; a row with errors is replaced
  by the fallback row and the original is kept in its trace (`code/traces/<request_id>.json`, written by
  default, gitignored).
- Model calls: schema-constrained JSON, `max_retries=2`, and a circuit breaker (`LLM_CIRCUIT_BREAKER`
  consecutive failures → the client stops calling and the run continues deterministically). Model-supplied
  strings are never interpreted as regex (`re.escape`), and a message-stated salary above
  `SALARY_PLAUSIBILITY_FACTOR` × the settled level is treated as unconfirmed.
- Missing exchange rate: a foreign-currency debit uses the nearest dated rate for the pair (noted); a credit
  is excluded (safer). Image ids must match `image_<n>` (no path traversal); installment options without a
  frequency are ignored.
- Every usage record and trace carries the `run_id` printed at the start of the run.

## Evidence policy (what the model may and may not do)

The model never sets an output column. It is used for three bounded jobs, each cached under `code/cache/`
with the content hash it was computed from:

1. reading one amount from one image (blank event amounts) — two independently framed readings are
   arbitrated with a deterministic line-item arithmetic check and the reviewed table (`evidence/images.py`);
2. classifying a message that no rule matched into the same closed fact schema the rules emit — each fact is
   confidence-gated and must be confirmed by a second call that quotes the exact span stating it;
3. drafting the explanation sentence from the verified numbers (accepted only if every figure matches; one
   feedback-guided retry naming the missing figures).

`--no-llm` never calls the API but still serves every cached model output, so the shipped `output.csv` is
reproduced byte-for-byte offline. `BOW_MAX_USD` caps estimated spend per run.

`evidence/images.py` also carries a small **reviewed table**: the 16 dataset images transcribed by hand,
each pinned to the sha256 of the PNG it was read from. It cross-checks the model reading (a verified
reviewed total wins; otherwise the financially safer value is taken) and makes the run reproducible without
an API key. It is a transcription of evidence that ships with the dataset, not of any output label, and it
is never applied to a file whose hash differs.

## Calibrated constants (read before changing)

Three forecasting choices were calibrated on the 25 solved samples rather than taken literally from the
specification. Each is documented in `research/synthesis.md` with the sample evidence, and each can be
reverted with an environment variable:

| Constant | Shipped | Spec-literal | Override |
|---|---|---|---|
| `HORIZON_DAYS` | 86 (labels ignore day-87+ monthly flows) | 90 | `BOW_HORIZON_DAYS=90` |
| `INTRADAY_DEBITS_FIRST` | on (variable spending precedes payday credit; monthly bills net) | end-of-day netting | `BOW_INTRADAY_DEBITS_FIRST=0` |
| spending-change selection | smallest total cut | (unspecified) | — |

