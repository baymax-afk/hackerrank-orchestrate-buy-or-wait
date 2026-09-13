# Plan — Buy or Wait? implementation

## Context
`code/main.py` is empty. The submission needs a runnable, deterministic solution that writes root `output.csv` (250 rows), `code.zip` with `evaluation/usage_report.md`, and `log.txt`. Research (see `research/synthesis.md`) established the data contracts, the sample-verified decision rules, and that the hidden truth comes from unseen future events — so the forecast must be unbiased and evidence-aware, and categorical fields (status/method/plan/earliest) are prioritised. User decisions: Anthropic API (claude-sonnet-5) behind a shipped cache; multi-agent split = LLM agents for message extraction, image OCR, explanation drafting, optional auditor; forecast/plan/rank/validate stay deterministic; pip allowed; research kept under `research/`; balanced time split.

## Layout (all new files)
```
code/
  main.py            CLI: --dataset --output --sample-check --no-llm --refresh-cache --limit --request-id --explain --audit --trace-dir
  config.py          constants (HORIZON_DAYS=90, ESTIMATOR, enums, PRICE_TABLE, model names, paths), .env loader
  models.py          frozen dataclasses (Profile, Event, PaymentOption, Request, Message, ImageRef, EvidenceFact, CashFlow, RecurringSeries, Candidate, Change, Decision, Trace)
  loaders.py         csv → dataclasses, schema validation, indexes, Decimal parsing
  fx.py              exact (date, from, to) lookup
  evidence/messages.py   regex families EN/ID → EvidenceFact; LLM fallback via agents
  evidence/images.py     image → amount via vision agent; cache; reviewed cross-check table
  evidence/cache.py      JSON cache with provenance (source_file, source_id, sha256, value, confidence, rationale, model, prompt_version, ts, usage)
  agents/client.py       anthropic wrapper (temperature 0, JSON schema validation, usage.jsonl logging, no-key → None)
  agents/{message_agent,image_agent,explain_agent,audit_agent}.py  prompts + schemas
  ledger.py          events + facts → CashFlows (§3 rules)
  recurrence.py      history → RecurringSeries (§5)
  forecast.py        end-of-day timeline, suffix mins
  capacity.py        amount_safe, earliest date
  plans.py           candidates + ranking (§8)
  changes.py         spending-change search (§9)
  explain.py         deterministic templates (sample style) + LLM draft validation
  validate.py        row/file invariants (§10)
  scoring.py         sample regression scorer
  usage.py           usage.jsonl → evaluation/usage_report.md
  tools/build_zip.py, tools/calibrate.py
  evaluation/usage_report.md (generated)
  cache/images/*.json, cache/messages/*.json, cache/explanations/*.json
tests/  (pytest; conftest adds code/ to sys.path; fixtures = tiny in-memory CSVs)
README.md (submission section), requirements.txt, .env.example
```

## Steps
1. Skeleton + models + loaders + fx + validate/format helpers; test_loaders, test_fx, test_validate.
2. Ledger + recurrence + forecast + capacity (port of the calibrated simulator); tests: pending/failed/unrealized/linked/fx/recurrence/min-balance/closed-form.
3. Plans + changes + explain (templates) + main pipeline writing output.csv; tests: partial arithmetic, installment exact schedule, month limit, accepted methods, change permissions/floors; run `--sample-check` (target ≥ 20/25 status).
4. Evidence: deterministic message parser (families §6 of research/01) + image cache; tests: multilingual, conflicts, injection; calibrate on samples.
5. Agents (anthropic): image OCR (16 calls, cross-checked against the reviewed table), message fallback, explanation drafting (validated against the numbers, template fallback), optional auditor; usage.jsonl + usage_report.md.
6. Full run → root output.csv; validate; calibration grid (`tools/calibrate.py`) on ESTIMATOR/income rules; fix CLAUDE.md enum note; README run instructions; build code.zip; final logs.

## Verification
- `python -m pytest tests -q` green; `python code/main.py --sample-check` prints per-column agreement (status/method/plan/earliest/amount rel-err).
- `python code/main.py` → `output.csv` 251 lines, `validate.py` reports 0 errors; every plan re-simulated ≥ minimum.
- `python code/tools/build_zip.py` → code.zip contains code/, tests/, README, requirements, evaluation/usage_report.md, cache; no .env/log.txt/dataset.
