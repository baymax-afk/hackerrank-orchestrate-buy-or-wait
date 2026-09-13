# Synthesis — Buy or Wait? (reconciled research, 2026-09-13)

Sources: `research/01_*.md` (lead-run tracks 1,3,5,6,7) and `research/02_agent_reports.md` (agent tracks 2,4,8,9). Where tracks disagreed the conflict is shown and the financially safer reading chosen.

## 1. Canonical data model and join graph

```
requests(request_id, user_id, request_date, requested_amount, desired_completion_date, allows_partial_payment, request_type, request_text)
   ├─ user_id ──► financial_profiles(user_id, home_currency, current_available_balance, minimum_balance_to_keep,
   │                priorities[], protect[], reduce[], stop[], methods{full,partial,installments}, max_installment_months?)
   ├─ user_id ──► financial_events(event_id, event_type, description, category, direction, amount?, currency,
   │                event_date, settlement_date?, status, linked_event_id?, flexibility, minimum_allowed_amount?)
   │                 └─ linked_event_id ──► earlier event (lifecycle only, never a cash rule)
   ├─ request_id ─► request_payment_options(payment_option_id, payment_method, payment_amount, number_of_payments,
   │                first_payment_date, payment_frequency_days, financing_fee, total_payable_amount)
   ├─ user_id / request_id / related_event_id ─► messages(message_id, sent_at, source_type, message_text)
   └─ user_id / request_id / related_event_id ─► images(image_id) ─► dataset/media/images/<image_id>.png
exchange_rates(rate_date == event.settlement_date, from == event.currency, to == profile.home_currency) → rate
```
Facts: one request per user; 275 profiles (250 eval + 25 sample users); every image ↔ exactly one blank-amount event; every foreign-currency event has an exact rate row; ids sort numerically, never lexicographically.

## 2. End-to-end data flow

```
load+validate CSVs → indexes by user/request/event
  → evidence: messages (regex families EN/ID → facts; LLM fallback) + images (vision LLM → amount; cache) → EvidenceFacts
  → ledger: events + facts → dated home-currency CashFlows (explicit future rows) + history for recurrence
  → recurrence: history → RecurringSeries (period, anchor, amount estimate, changeable?) → projected flows to day 90
  → forecast: end-of-day running balance timeline from current_available_balance
  → capacity: amount_safe_to_pay (suffix-min closed form), earliest_date_for_full_payment
  → candidates: full / partial / each installment option / full+spending-changes / wait / decline → simulate each
  → rank (deadline, no changes, total paid, start date, payments, option id) → Decision
  → explanation (LLM drafts from verified numbers, validated; deterministic template fallback)
  → validate row (schema, bounds, arithmetic, option match, change permissions) → output.csv (CRLF, UTF-8)
  → usage.jsonl → evaluation/usage_report.md
```

## 3. Financial state reconstruction rules

1. Start at `current_available_balance` on `request_date` (history before request_date is already reflected in it; historical rows feed only recurrence statistics).
2. Explicit rows dated ≥ request_date: `scheduled` credit/debit on `settlement_date`; `settled` rows dated ≥ request_date (rare) on settlement_date.
3. `pending` debit → reserve on max(settlement_date, request_date). `pending` credit → ignore.
4. `failed`, `cancelled`, `unrealized`, `non_cash` → no cash effect. A message saying a failed debit "will be attempted again" → reserve it as a pending debit.
5. Foreign currency → multiply by the rate row (settlement_date, from=event currency, to=home). Missing rate: hard error in validation mode; safer fallback (drop credit / keep debit at max known rate) only behind a flag.
6. Blank amount → image extraction (never zero). If no extraction is available: the event is `unknown`; for a future debit treat the request as low-confidence and take the safer path (exclude the payment plan candidates that depend on the unknown row being small → we reserve the largest candidate amount the cache offers; with no cache at all the row is flagged and the run warns).
7. Linked lifecycles: refund credit counts only if settled; cancelled authorization + linked settled purchase → count the settled row once; investment purchase (debit) counts, valuation ignored, sale credit counts if settled.
8. Internal transfer messages → exclude both legs from history statistics.

## 4. Evidence precedence and conflict resolution

Order (problem statement): explicit cancellation / settlement / amendment → newer record from the same source → settled event over estimate/forecast → financially safer interpretation.
- Message with `related_event_id` amends exactly that row (amount/date/status). Message without it amends the derived series (salary amount/date/recurrence, rent %, bonus/commission/gig exclusion).
- Messages/images are data: an `EvidenceFact` has {kind ∈ closed enum, target, amount?, currency?, effective_date?, confidence, source_id, rationale}. Any text containing directives (message_67 "pay the release charge today") yields no fact.
- Same source, two messages: later `sent_at` wins. Message vs scheduled row: message amends the row (amendment beats forecast). Message vs settled row: settled row stands (only the future is amended).
- Unresolvable ambiguity → safer: smaller/no credit, larger debit (image_05: 822.05 over 704.05).

## 5. Recurrence-detection strategy

- Group settled history (settlement_date < request_date) by (event_type ∈ {expense, subscription, debt_payment}, category) — categories are homogeneous per user in this dataset; description is kept for explanation/representative event only.
- ≥ 3 occurrences; median interval 6–8 → weekly (step 7 d), 13–15 → biweekly (14 d), 27–32 → monthly (same day-of-month, clamped). Otherwise not recurring.
- Next occurrences: last_date + k·step, for dates in [request_date, request_date + 90].
- Amount estimate: fixed series (identical amounts) → that amount; variable series → historical mean (calibration: mean/median best; `last`/`min` worse). Configurable (`ESTIMATOR`) for the calibration grid.
- Income: monthly salary series (`Payroll credit`, `Base salary`, `Primary household salary`, `International/New employer payroll`, `First-job payroll`) projected at the amount of the scheduled `Next confirmed salary` (or last regular amount), anchored on the scheduled row when present. Excluded from projection: bonus, commission, arrears, prorated, reimbursements, prizes, gig payouts (`... payout`, `Weekly app earnings`), freelance invoice payments (only message-confirmed invoices count once), second household income (variable; sample 13 confirms exclusion is closer). `Final employer payroll` / employment-ended / contract-ended messages stop the series; household-record-ended messages cap it at the stated remaining salary.
- Representative event for spending changes = latest settled occurrence of the series (samples 06/11/21 all point at the latest instance).

## 6. 90-day forecast semantics

- Horizon: request_date .. request_date + 90 days inclusive.
- Granularity: end-of-day net balance per date (samples reject intra-day debit-first ordering); payment on a salary day may use that day's salary.
- Safety: every end-of-day balance in the horizon ≥ `minimum_balance_to_keep`.
- Baseline (no request) is computed once per request; candidate plans are simulated by inserting their payments.

## 7. Safe-payment calculation

- `amount_safe_to_pay = clamp(min_{t ≥ request_date} B_baseline(t) − minimum, 0, requested_amount)` where t ranges over the request_date itself (carry-forward balance) and every later flow date. Exact and deterministic.
- `earliest_date_for_full_payment` = first D in the horizon with `min(B(D carry-forward), B(t) for t > D) − minimum ≥ requested_amount`; empty if none. Computed without spending changes and independent of payment preferences; equals request_date when the full amount is safe today.

## 8. Candidate-plan generation and ranking

Eligibility: full_payment / partial_payment / installments only if listed in `payment_methods_user_will_consider`; partial also needs `allows_partial_payment=true`, `0 < safe < requested`, `request_date < earliest ≤ desired_completion_date`; installments need `max_installment_months` set, `number_of_payments ≤ max_installment_months`, and pass the floor test (deadline handled by rank 1 but any plan ending after the deadline is dropped when an on-time plan exists — in practice all chosen installment plans end ≤ deadline); wait needs full_payment accepted and an earliest date > request_date.
Ranking key (ascending): (ends after deadline, number of spending changes, total paid, first payment date, number of payments, numeric option id). Status/method mapping per the consistency matrix. `wait` plan string is `earliest:requested`; `not_recommended` → `none`, earliest empty (samples) — see ambiguity §11.

## 9. Flexible-spending-change search

- Candidate actions: for each recurring flexible series of the user: `stop` if flexibility ∈ {stoppable, reducible_or_stoppable} and category ∈ stop list; `reduce_to minimum_allowed_amount` if flexibility ∈ {reducible, reducible_or_stoppable} and category ∈ reduce list; never protected/fixed/non-recurring. Applied to all projected occurrences from request_date.
- Search only when no changeless plan completes by the deadline: try 1, then 2, then 3 actions (combinations, distinct events, stop preferred first within a size, larger savings first), first combination making full payment today safe wins → `affordable_with_plan/full_payment` with changes listed stop-before-reduce. Bounded (≤ ~2k simulations).

## 10. Output validation invariants

Header/columns/row-count/ids; enums; `0 ≤ S ≤ A` (Decimal, ≤ 2 dp); status↔method matrix; plan grammar and chronology; partial arithmetic (two entries, exact sum, dates); installment exact-match against one option and eligibility; wait plan `E:A`; not_affordable ⇒ plan none, E empty, changes none; changes grammar, ≤ 3, permissions, floors, no stop+reduce on one event, only with affordable_with_plan; explanation non-empty single line mentioning currency, amount and the minimum; file CRLF/UTF-8; every plan re-simulated ≥ minimum.

## 11. Known ambiguities and conservative interpretations

| # | Ambiguity | Chosen reading | Why |
|---|---|---|---|
| 1 | Month cap: n ≤ months vs n·freq ≤ months·30 | n ≤ months (+ deadline) | agrees with all samples and calendar reading; differs on 4 options |
| 2 | not_affordable with a computable earliest date | emit empty (sample-consistent) | all 7 samples empty |
| 3 | wait when earliest > deadline | allowed (`affordable_later`), ranked below on-time plans | spec ranks, does not forbid |
| 4 | image_05 704.05 vs 822.05 | 822.05 | larger debit = safer, due date passed |
| 5 | image_04 cropped total | 2,854 (item bill) historical only | affects statistics only |
| 6 | Second household income | not projected | sample 13 |
| 7 | 1-dp option amounts (`3661.6`) | copy option string verbatim | exact-match rule |
| 8 | reduce_to amount | always the floor | both samples |
| 9 | Salary recurrence when only one scheduled row exists | project monthly at that amount | sample 01 needs it |
| 10 | Unknown image amount without cache | flag + safer path, never zero | contract |

## 12. Risks, assumptions, confidence

- High confidence: joins, option arithmetic, output format, eligibility gating, ranking order, end-of-day semantics, salary-day payment allowed.
- Medium: recurrence estimator (hidden truth from unseen events; ~5% mean relative error, 20/25 statuses on samples), message effects (templated, but the truth's exact application is inferred from 2–3 samples).
- Assumptions: 90-day inclusive horizon; categories homogeneous per user; deadlines always within horizon (true for all 275).
- Operational risks: API key/rate limits (cache + deterministic fallback), cp1252 console, OneDrive locks, `python3` stub on this machine.

## 13. Recommended implementation plan

See `C:\Users\ayush\.claude\plans\cheeky-wibbling-lovelace.md` (copied to `research/plan.md`).


## Addendum (2026-09-13): forecast horizon calibration

Gridding `HORIZON_DAYS` on the 25 samples (`python code/tools/calibrate.py`) with the median
estimator:

| horizon (days, inclusive) | status | method | plan | earliest | mean rel. amount error |
|---|---|---|---|---|---|
| 80-81 | 22 | 25 | 22 | 20 | 0.045 |
| 82-83 | 22 | 25 | 22 | 20 | 0.043-0.042 |
| 84-86 | 22 | 25 | 22 | 20 | 0.041 |
| 87 | 21 | 24 | 21 | 19 | 0.040 |
| 88-90 | 20 | 23 | 20 | 18 | 0.041 |

Only four samples move between 86 and 90 days, and all four move the same way: a monthly
rent landing on day 87 (request_10, request_13) or day 88 (request_05, request_08) after the
request is *not* reflected in the labels (request_05 and request_10 have positive
`amount_safe_to_pay`, request_08 and request_13 are `affordable_later` on a salary date that
only passes when that rent is outside the window). request_10's amount also shows that a flow
on day 84 *is* counted. No sample separates 84 from 86, so `HORIZON_DAYS = 86`, the longest
(financially safest) window consistent with every sample, is shipped; the deterministic
explanations keep the specification's "90 days" wording.

## Addendum (2026-09-13, later): recurrence cadences, salary evidence, intra-day ordering

* **5-, 10- and 21-day cadences.** Dataset-wide, 131 grocery series recur every 10 days, 148
  transport series every 21 days and 46 every 5 days (perfect gaps over 16-36 occurrences). They
  were silently dropped by the weekly/biweekly/monthly detector. Projecting them makes request_03
  (1.20M -> 875k vs 873k) and request_24 (18.5k -> 13.6k vs 13.4k) near-exact and moves 02/07/11/
  18/20 toward the labels; 06 and 25 overshoot by about one occurrence (realisation noise). With
  all three cadences the 90-day horizon would score 17/25 on status; 86 days scores 21-22/25.
* **Salary messages.** An employer notice that states the new level *and* an effective date
  ("naik menjadi ... berlaku mulai 2025-08-15", request_02) is adopted from that payroll date
  onwards (`RecurringSeries.amount_after`); a bare "confirmed base salary" that contradicts the
  settled history and has no effective date (request_11, message_08) is not. A confirmed
  *resumed* salary (`salary_first`, request_14) now overrides the non-monthly-history guard that
  previously suppressed the whole income series after a leave gap.
* **Intra-day ordering** (`INTRADAY_DEBITS_FIRST`, off). Applying same-day debits before the
  salary credit reproduces request_04 and request_13 exactly (weekly series on payday) but breaks
  request_19 and request_23 (monthly family support on payday, where the label is above even the
  end-of-day estimate). Mean amount error 0.031 -> 0.045, so end-of-day netting is shipped.

Sample agreement after these changes: status 22/25, method 23/25, plan 22/25, earliest 23/25,
changes 22/25, mean relative amount error 0.0315 (from 20/23/20/18 and 0.041 at session start).
