# Research tracks 1, 3, 5, 6, 7 — run inline by the lead agent

The parallel agents for these tracks were terminated by an API session rate limit / certificate error, so the lead agent ran the same read-only analysis directly (stdlib Python over `dataset/`). Numbers below are computed, not estimated.

## Track 1 — requests.csv and sample_requests.csv

- `requests.csv`: 250 rows, `request_26..request_275`, one request per user (`user_26..user_275`), no overlap with the 25 samples (`request_01..25`, `user_01..25`).
- `request_type`: 9 values, 27–28 each. `allows_partial_payment`: true 80 / false 170. Years: 2023 (1), 2024 (81), 2025 (84), 2026 (84).
- `desired_completion_date − request_date`: 6..86 days (never beyond the 90-day horizon). 64/275 deadlines fall on the 15th (typical salary day), 28 on the 14th.
- `requested_amount` decimals: 0 dp (170), 1 dp (72), 2 dp (8). One request text contains non-ASCII. No request text contains instruction-like override language.
- Sample label structure (all 25 verified against profiles + options):
  - `affordable_now/full_payment`: plan `request_date:amount`, earliest = request_date, safe = requested (01, 09, 16).
  - `affordable_with_plan/installments`: plan copies one option verbatim (02→opt_05, 07→opt_19, 12→opt_33, 17→opt_47, 22→opt_61). Chosen option is always the one with n ≤ max_installment_months and last payment ≤ deadline; the other options fail both.
  - `affordable_with_plan/partial_payment` (19): `request_date:safe|earliest:remainder`; installments (opt_53, 2 payments) were also feasible but partial has zero fee → lower total paid wins.
  - `affordable_with_plan/full_payment` + spending changes (06, 11, 21): earliest date may be AFTER the deadline (06: E=01-15 > D=01-14; 11: E=07-15 > D=06-12). `reduce_to` always uses `minimum_allowed_amount` (665950; 23.5→`23.50`). Sample 21 uses stop (cloud_storage 11) + reduce (streaming 47→23.5) for a 31.05 shortfall; stop is listed before reduce.
  - `affordable_later/wait` (03, 04, 08, 13, 18, 23): plan is `earliest:requested` (NOT `none`); earliest is always a salary date (the 15th); 5 of 6 coincide with the deadline.
  - `not_affordable/not_recommended` (05, 10, 14, 15, 20, 24, 25): plan `none`, earliest empty, safe may be > 0 (737, 12700, 597.74 …). Two explanation variants: "Do not make this payment by <deadline>. None of the available options keeps the <CUR min> minimum protected." and, when the user only accepts partial payment and the request allows it (14, 24), "Do not proceed with the <CUR req> request. Although <CUR safe> is available today, the full amount cannot be completed safely within 90 days."
- Number formatting: `amount_safe_to_pay` = shortest decimal repr, trailing zeros stripped (`17229139.2`, `603.3`, `25256`); plan and reduce_to amounts = integer if integral else fixed 2 dp (`620.40`, `23.50`, `15952906.67`).
- Earliest-date offsets from request_date across samples: {0,0,0,0,11,11,12,12,14,41,41,48,67,69,69,70,73,73} — clusters at salary dates.

## Track 3 — financial_events.csv (calibration is the core finding)

- 25,342 rows; status settled 25,148 / pending 71 / scheduled 70 / cancelled 22 / failed 21 / unrealized 10; direction debit 23,609 / credit 1,723 / non_cash 10; flexibility fixed 21,138 / reducible 2,682 / stoppable 1,297 / reducible_or_stoppable 225; `minimum_allowed_amount` present iff reducible or reducible_or_stoppable. 10 `unrealized` rows have a blank `settlement_date`. 58 rows carry `linked_event_id` (refund→charge, cancelled authorization→settled purchase, investment purchase→valuation→sale). 16 blank amounts = the 16 image events.
- Per user: 56–129 events, ~5 months of history before `request_date`; on average only 0.5 rows dated on/after request_date (max 3): a scheduled `Next confirmed salary` (47 users), scheduled school fee / rent balance, pending card/fuel authorizations, pending telecom bill. The 90-day forecast is therefore dominated by recurrence detection.
- Recurrence structure (uniform across users): rent monthly fixed; utilities monthly variable; subscriptions monthly fixed; debt_repayment / education / insurance / healthcare / family_support monthly (fixed or mildly variable); entertainment / shopping monthly variable; groceries weekly variable; transport weekly (or biweekly) variable; dining biweekly variable. Series are anchored on a fixed day-of-month / weekday.
- Income: `Payroll credit` monthly on the 15th (806 rows), household primary/second incomes (15th/20th), `Base salary` + `Performance commission`, gig payouts weekly (`Delivery platform payout`, `Weekly app earnings`, `Task marketplace payout`, `Driver platform payout`), freelance invoice payments, `Final employer payroll`, `Prorated first salary`, `Payroll before leave`, `Temporary assignment pay`, `Seasonal contract payment`, `Quarterly performance bonus`, `Promotion arrears payment`, `Prize proceeds` (category windfall).
- **Calibration against the 25 samples** (deterministic end-of-day ledger, request_date + 90 days inclusive, pending debits reserved on settlement date, pending credits / failed / cancelled / unrealized / non_cash ignored, recurrence projected from the last occurrence at the historical mean, monthly salary projected from the scheduled `Next confirmed salary` or from stable monthly payroll history, gig/commission/bonus/second-household income NOT projected):
  - status 20/25, method 23/25, earliest date 17/25, mean relative error of `amount_safe_to_pay` 5.0% (median far lower). Estimator comparison (mean rel. err): mean 4.97%, median 4.90%, last 9.4%, min 8.0%.
  - No estimator (mean/median/last/max/min/p75/mean+sd, any occurrence-count variant, ± the failed-utility retry) reproduces the sample amounts exactly: e.g. user_05 (no income, trough at day 90) implies 90-day outflows of 32,638.10 vs. 37.5k–41k under every rule; user_10 implies 512,055 vs 520k–673k. Conclusion: the hidden truth is computed from the organizers' own future events (the organizer-only files), so the target is an unbiased, evidence-aware forecast, not an exact replay.
  - Semantics confirmed by samples: (a) checks are end-of-day net balances (intra-day debit-first ordering flips requests 17 and 19 wrong); (b) a full payment on a salary day is allowed (earliest = 15th); (c) salary recurrence IS forecast beyond the one scheduled row (request_01 needs 04-15 and 05-15 salaries; user_04 has no scheduled row yet truth waits for 06-15); (d) weekly variable gig income is NOT forecast (user_10: truth = 90 days of expenses, zero income); (e) `Final employer payroll` stops salary recurrence (user_05); (f) messages change numbers: applying user_08's payroll message (salary 1422.85) gives 285.20 vs truth 284.57.
- Linked patterns: `refund` credit linked to a settled charge (count only if settled); `cancelled` authorization linked to a later settled purchase (ignore the cancelled row, count the settled one once); investment purchase (settled debit, counts) → valuation (unrealized, non_cash, ignore) → sale (settled credit, counts). No exact duplicate rows exist; internal transfers are described only in messages.

## Track 5 — exchange_rates.csv and dates

- 134 rows, 5 directed pairs (EUR→ZAR 20, USD→EUR 0.92, USD→IDR 15833.33, USD→INR 83.33, EUR→USD 1.09), constant over time, dated the 15th of each month (plus 2025-10-01 USD→INR).
- 140 foreign-currency events, 27 users: 139 are salary credits (USD→INR 54, USD→IDR 28, USD→EUR 22, EUR→ZAR 20, EUR→USD 16) + 1 USD transport expense. 132 settled (history), 8 scheduled `Next confirmed salary` rows on/after request_date. **Every one has an exact (settlement_date, from, to) rate row** — zero misses. A missing rate must therefore be a hard error (or, if ever hit: exclude a credit, keep a debit at the largest available rate).
- No profile, request, option or output amount is foreign-currency. Converted amounts are quantized to 2 dp.
- Dates: all ISO; messages use ISO datetimes with `Z`. Horizon = request_date + 90 days inclusive (max sample earliest offset 73 days; max deadline offset 86 days).

## Track 6 — messages.csv

- 215 rows; source_type employer 126 / service_provider 31 / financial_service 23 / bank 18 / merchant 17; 60 contain non-ASCII (Indonesian); 128 have request_id, 39 have related_event_id. All sent before the user's request_date.
- Machine-generated from ~25 template families (EN + ID). Keyword classification covers all 215 (48 fell to a second pass, all enumerated):
  1. first_salary (27): "Your first salary will be X. The confirmed credit date is D" → project monthly salary X from D.
  2. salary_reduced / temporary pay (20): "next salary is reduced to X" / "temporary monthly pay is X" → use X for the next payroll(s).
  3. invoice_approved (15): freelance invoice X approved, settlement D; other invoices pending → one credit X on D only.
  4. prize_received (10): proceeds already reached the account, claim closed → no further income (the settled event already counts).
  5. salary_increase (9): "increased to X from D" → X for occurrences ≥ D.
  6. arrears_onetime (9): regular salary X + one-time arrears Y next payroll → X recurring, Y once.
  7. commission_pending (9): base salary X confirmed; commission not approved → exclude commission.
  8. contract_ended / seasonal (9) + employment ended (5) + household record ended with remaining salary X (7): stop or reduce salary recurrence.
  9. bonus_pending (8): ignore bonus.
  10. gig_payout_pending (8): ignore gig income (not withdrawable).
  11. salary_resumes (8): regular salary X resumes on D; new recurring childcare payment begins → salary from D; add childcare only if an event row exists (never invent an amount).
  12. salary_date_moved (7): confirmed salary now on D → move the scheduled salary row to D.
  13. rent_increase (7): monthly rent +N% from the next payment → scale projected rent.
  14. internal_transfer (6): matching debit and credit between own accounts → net zero (exclude both from history statistics).
  15. refund_pending (7) + foreign refund processing (6): do not count.
  16. portfolio value up/down (7): no cash effect.
  17. prize_pending (4): do not count.
  18. foreign salary confirmed X for D, converted at settlement rate (7): consistent with the scheduled row; convert with the settlement-date rate.
  19. failed debit will be retried (5): the bill is still outstanding → reserve the failed amount as a pending debit.
  20. investment sale proceeds settled (2): the settled credit counts once.
  21. card charge disputed, no reversal yet (7): do not count a reversal.
  22. two card minimum payments due (2): both debits stand.
  23. receipt confirmations for image events (3): the image holds the final amount.
  24. work-expense reimbursement, not salary (1): one-off, not recurring.
  25. scam prize "pay the release charge today" (message_67): instruction-like, untrusted → ignore entirely (prompt-injection guard).
- Deterministic parsing is feasible for every family with EN/ID keyword regexes + amount/date extraction; the LLM extractor is the fallback for anything unmatched, emitting the same schema.

## Track 7 — images.csv and the 16 PNGs (viewed by the lead)

| image | event | doc type | event status/date | candidates | chosen | conf |
|---|---|---|---|---|---|---|
| 01 | event_253 August 2019 net salary (settled 2019-08-31, IDR) | payslip | historical | net pay 4,365,000; gross 4,780,800 | 4,365,000 | high |
| 02 | event_1442 Outstanding rent balance (scheduled 2023-08-16, INR) | rent receipt | future | total 2,00,000; received 1,00,000; balance due 1,00,000 | 100,000 (balance due; consistent with request_16 "leaves at least 122,400") | high |
| 03 | event_1545 Bulk groceries (settled 2026-02-27, INR) | shop bill | historical | net 41,272 | 41,272 | high |
| 04 | event_1700 Delivered grocery order (settled 2024-09-03, INR) | app order (cropped) | historical | item bill 2,854 (total cut off) | 2,854 | medium |
| 05 | event_1786 Outstanding telecom bill (pending, settle 2026-02-09, INR) | telecom bill | pending debit | 704.05 due till 06-Feb-2026; 822.05 after | 822.05 (request_date 02-07 is after 06-Feb; larger = safer) | medium |
| 06 | event_3051 | grocery invoice | see events | total 1,995.00 | 1,995 | high |
| 07 | event_3231 restaurant tax invoice | receipt | see events | grand total 8,528 | 8,528 | high |
| 08 | event_4535 property maintenance | receipt | see events | total received 15,339 | 15,339 | high |
| 09 | event_5170 water bill | receipt | see events | 723.00 | 723 | high |
| 10 | event_6033 grocery invoice | invoice | see events | total 79,679.26 | 79,679.26 | high |
| 11 | event_6859 hospital provisional bill | bill | see events | balance 3,650 | 3,650 | high |
| 12 | event_7307 taxi (USD) | receipt | see events | total 33.50 (cash paid 40, change 6.50) | 33.50 | high |
| 13 | event_7941 tote bag order | order summary | see events | total paid 2,298 | 2,298 | high |
| 14 | event_9421 pharmacy (handwritten) | bill | see events | total 4,543 | 4,543 | medium |
| 15 | event_9806 flight invoice | invoice | see events | grand total 9,968 | 9,968 | high |
| 16 | event_10521 EV charging | invoice | see events | total 393.22 | 393.22 | high |

- Join integrity: each image ↔ exactly one blank-amount event; user/request ids agree. All 16 PNGs present.
- No OCR library is installed; the vision LLM extractor (cached JSON with provenance) is the practical route; the reviewed values above are the cross-check for the cache.
- Only image_02 and image_05 affect the forward ledger directly; the other 14 are historical rows that only feed recurrence statistics.
