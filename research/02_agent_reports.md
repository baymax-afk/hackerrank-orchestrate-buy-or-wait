# Research agent reports (tracks 2, 4, 8, 9) — returned by parallel read-only Explore agents

## Track 2 — Financial profiles (`dataset/financial_profiles.csv`)

- 275 rows, `user_01..user_275` contiguous/unique; 10 columns; only `expense_categories_user_is_willing_to_reduce` (39 blank), `..._to_stop` (62 blank) and `max_installment_months` (119 blank) are ever blank. Blank list = empty list; blank max ⇔ `installments` not in `payment_methods_user_will_consider` (no exceptions either way).
- Currencies: INR 67, EUR 62, IDR 55, ZAR 51, USD 40. minimum/balance ratio 0.167–0.770 (median 0.476); no user starts below the minimum. Home currency matches the currency named in every request text.
- Vocabularies: priorities {emergency_savings 171, education 94, retirement_investment 61, debt_repayment 56, family_support 49, travel 46, healthcare 44, housing 29}; protect {rent 232, groceries 166, transport 109, utilities 105, education 60, debt_repayment 56, insurance 46, healthcare 44, housing 43, family_support 25}; reduce {dining 153, shopping 72, streaming 66, entertainment 44, gym 14}; stop {cloud_storage 109, streaming 84, music_subscription 58, delivery_membership 41, gym 12}.
- Payment-method combinations: full 60; partial|installments 52; installments 41; full|partial 40; full|installments 35; all three 28; partial 19. 112 users never accept full_payment (so never `affordable_now`/`wait`).
- Consistency: protect ∩ (reduce ∪ stop) = ∅; every reduce/stop category appears in that user's events; 0 of 4,204 non-fixed events are in a category the user did not permit; per-user (category → flexibility) is perfectly determined (streaming/gym `reducible_or_stoppable` ⇔ category in both lists, 45 users). 28 `fixed` events sit in reduce categories (one-offs/lifecycle rows) → the per-event flexibility decides, the profile list is a redundant guard.
- 5 requests have no eligible immediate method at all (profile = partial only, request disallows partial): request_76, 95, 133, 209, 227 → forced `not_recommended` (precedent: sample request_15).
- `max_installment_months` never binds beyond the deadline rule on this dataset (0 options fit the deadline but exceed the cap); 67 requests have installment options that all exceed the cap.
- Priorities vocabulary (`housing`) differs from event vocabulary (`rent`) — priorities are explanation flavour only.

## Track 4 — Payment options (`dataset/request_payment_options.csv`)

- 790 rows: 275 full_payment (exactly one per request; amount == requested, date == request_date, fee 0) + 515 installments (1–3 per request). No `partial_payment` rows. Not always listed full-first.
- Exact Decimal identities hold for all 515: `payment_amount × n == total_payable_amount`, `total − requested == financing_fee`, `payment_amount == round_half_up(total/n, 2)`.
- Schedule = `first_payment_date + k × payment_frequency_days`, k = 0..n−1; reproduces all 5 sample plans byte-for-byte. n ∈ {2:7, 3:80, 4:3, 6:65, 15:89, 18:87, 21:88, 24:96}; frequency ∈ {28,30,31}; first payment 0–14 days after request_date.
- 434/515 options end after `desired_completion_date`; only n ∈ {2,3,4} can ever meet a deadline (max gap 86 days). Sample request_12's last payment equals the deadline → deadline inclusive.
- Month-cap hypotheses (n ≤ mm; n·f ≤ mm·30; (n−1)·f ≤ mm·30; calendar months) agree on all samples; after the deadline filter they differ on only 4 eval options (395, 412, 695, 737: n=3, f=31, mm=3). Recommendation: `n ≤ max_installment_months` AND last payment ≤ deadline.
- Ranking evidence: fee-free full/partial always beat installments (min fee 3.5%); request_19 chose partial over a feasible 2-payment option; request_12 chose installments with safe == requested because the user rejects full_payment. Sort option ids numerically (`payment_option_100` < `payment_option_11` lexicographically).
- Amount rendering: integers as integers, otherwise 2 dp (`3661.6` in the file → recommend copying the option string verbatim to guarantee an exact match).

## Track 8 — Output and validation

- Header exact; 250 rows + header; ids `request_26..275` in requests.csv order (identical to the template order). All dataset files are CRLF, UTF-8, no BOM; `csv.writer` defaults (CRLF, QUOTE_MINIMAL) with `open(..., newline='', encoding='utf-8')` reproduce `sample_requests.csv` byte-for-byte.
- Formatting: `amount_safe_to_pay` shortest repr (strip trailing zeros, never scientific); plan / reduce_to amounts 2 dp if fractional else integer; dates ISO; `none` literal; earliest date empty only when no full payment is safe within the horizon.
- Consistency matrix: affordable_now ⇒ full_payment, plan `R:A`, E=R, S=A, changes none. affordable_with_plan ⇒ partial (`R:S|E:A−S`, R<E≤D, 0<S<A) | installments (exact option) | full_payment with 1–3 changes (E may exceed D). affordable_later ⇒ wait, plan `E:A`, E>R, changes none. not_affordable ⇒ not_recommended, plan none, E empty, changes none.
- Spending-change validator: event exists, same user, debit, settled recurring instance, flexibility permits the action, category in the matching permission list and not protected, `minimum_allowed_amount ≤ reduce_to < amount`, ≤ 3 actions, no duplicate event, never stop+reduce on one event, changes only with affordable_with_plan.
- Rejection list vs warnings and a `validate_row / validate_file / score` API were specified (see research/synthesis.md §10).
- Open questions: not_affordable with a computable E (spec says E is preference-independent — emit computed E? samples always empty); wait with E > D; 1-dp option amounts.

## Track 9 — Architecture and testing

- Environment: Python 3.14.7 at `C:\Python314\python` (`python3` is the Microsoft Store stub — README's `python3` will not work here); pytest 9.1.1, anthropic 1.3.0, httpx, pydantic installed; no pandas/PIL/tesseract; stdout is cp1252 (never print raw multilingual text); OneDrive-synced folder (write temp then `os.replace`).
- Proposed layout: `code/main.py` (CLI), `config.py`, `models.py` (frozen dataclasses, Decimal money), `io_/loaders.py`, `fx.py`, `evidence/{messages,images,cache,llm_client}.py`, `ledger.py`, `recurrence.py`, `forecast.py`, `capacity.py`, `plans.py`, `changes.py`, `explain.py`, `validate.py`, `scoring.py`, `usage.py`, `tests/`.
- Capacity is closed-form: paying x on request_date shifts every later balance by x, so `amount_safe = clamp(min_{t ≥ d0} B(t) − minimum, 0, requested)` and `earliest = first D with min_{t ≥ D} B(t) − minimum ≥ requested` (suffix-min scan). Plans are simulated by inserting payments and re-running the pass.
- Cache design: `code/cache/{images,messages}/<id>.json` with source_file, source_id, content hash, extracted value, confidence, rationale, model, prompt_version, timestamp, usage; no-key runs use the cache; a miss never becomes zero.
- Usage report: `usage.jsonl` appended per call → `evaluation/usage_report.md` aggregating calls, tokens, averages per request and cost from a price table.
- Test plan: 12 test files covering loaders, fx, ledger, evidence (messages/images/injection), recurrence, forecast, capacity (closed-form vs brute force), plans, changes, validate, explain, and a 25-sample regression with a known-deviation allowlist. Single test: `python -m pytest tests/test_capacity.py::test_name -q`.
- Packaging: `code.zip` = code/ (+cache, evaluation), tests/, README, requirements.txt, .env.example; exclude .env, log.txt, dataset/, traces, caches of pyc. Spending-change search bounded (≤ 12 flexible series/user → ≤ ~2k combos).
- Risks: python3 stub; cp1252 console; OneDrive locking; CLAUDE.md's `fixed/flexible` description is wrong (4-value enum) — fix it.
