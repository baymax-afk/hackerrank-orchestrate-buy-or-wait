# Agentic orchestration — how to make it better and more robust

Context: the shipped system is a deterministic pipeline with three bounded, cached, LLM-backed helpers
(image reading, message classification fallback, explanation drafting). The review concluded that is the
right shape: the model never sets an output column. This note brainstorms how the *agentic* part could add
more value and become more robust without giving the model authority over decisions. Each idea is tagged
with an honest value/cost estimate and, where it was tried, what happened.

Guiding principle: **the LLM proposes, deterministic code disposes.** Every idea below keeps that line.

---

## 1. Patterns that were adopted in this pass

### 1.1 Validator-feedback loop (adopted)
The explanation agent's draft is checked by a deterministic grounding function; on failure the agent gets
*one* more attempt with the exact list of missing figures. Result: 116/116 previously rejected drafts were
recovered, 250/250 grounded. Cost: one extra call per rejected draft. This is the smallest useful form of
"reflection": the critic is code, the loop is bounded, and the failure mode is the deterministic template.

### 1.2 Circuit breaker + fault boundary (adopted)
After N consecutive API failures the client opens and the run continues deterministically; every request is
decided inside its own try/except with a contract-valid fallback row. Agents can now fail loudly without
taking the run down.

### 1.3 Hash-keyed evidence (adopted)
Every model reading is cached under the sha256 of the file it was computed from; a changed file never
receives a stale reading. Human transcriptions are kept out of the runtime and used only as a golden
evaluation set (`tests/golden_image_readings.py`), pinned to the same hashes.

---

## 2. Evidence extraction: make the model outputs *verifiable*, not just *validated*

### 2.1 Two-reader arbitration for images (high value, low cost) — ADOPTED
Today: one vision read, arbitrated by the reviewed table. Better: two independent reads with different
framings (e.g. "what is the total?" vs "list every labelled amount on the document"), plus a *deterministic
arithmetic check* (line items sum to the total). Accept when both readings agree or the arithmetic confirms one
of them; otherwise take the financially safer value and flag. This replaces the need for the human table on
unseen images and would have caught image_14 (4593 vs 4543) automatically because the items sum to 4543.
Cost: one extra vision call per image (16 here).

*Implemented: `ImageAgent.enumerate` (second framing: every labelled amount, item vs total) and
`evidence.images.arbitrate`, which scores each candidate by corroboration — reader A, reader B total,
reviewed table (1 point each) and line items adding up (2 points, deterministic) — takes a candidate that
leads with ≥2 points, else a verified reviewed value, else the financially safer value flagged for review.
Reader B only *corroborates*: its figures include previous balances, subtotals and payments that are not the
answer, so they can never become the adopted value, and line-item arithmetic breaks ties among targeted
readings but cannot crown a new one (items usually sum to a pre-tax subtotal). Both rules were learned from
the real readings: the first draft adopted a previous balance for image_05 and a subtotal for image_07.
The reviewed table was subsequently removed from the runtime (the challenge forbids file-specific answers)
and kept only as an evaluation fixture; with subtotal/tender exclusion and a narrow digit-misread
correction the two readers plus arithmetic score 16/16 against it. output.csv is unchanged.*

### 2.2 Self-consistency by sampling (medium value, low cost)
For any extraction with confidence < 0.9, sample 3 readings at low effort and take the majority; disagreement
→ safer value + flag. Cheap insurance against single-shot misreads; cached like everything else.

### 2.3 Extractor → verifier split for messages (high value, medium cost) — ADOPTED
A second, differently-prompted call that receives the message and the *extracted fact* and answers only
"is this fact stated explicitly in the text? quote the span". Facts without a quoted span are downgraded to
unconfirmed. This is the cheapest way to make hallucinated amounts impossible: the verifier must point at
the text. Combine with the plausibility bound (adopted) for numeric sanity.

*Implemented: `MessageAgent.verify` — a second call with a different prompt that must return the exact
substring stating the fact; the span is checked against the message text in code (`span_supported`), so a
verifier that "agrees" without a real quote is rejected. Facts that cannot be verified (or when the client is
unavailable) are dropped. Only rule-miss messages reach this path.*

### 2.4 Confidence-gated adoption (low cost) — ADOPTED
Model facts carry a confidence but the pipeline ignores it. Gate: confidence ≥ 0.8 → adopt; 0.5–0.8 → adopt
only if it makes the forecast *safer*; < 0.5 → ignore and note. Deterministic, one function.

*Implemented as `message_agent.gate`: optimistic kinds (salary rise, first salary, one-off credit, amendments)
need ≥ 0.8; conservative kinds ≥ 0.5.*

### 2.5 Rule mining offline, rules online (medium value, medium cost)
The regex families were hand-written for this dataset. A better workflow: run the message agent over *all*
messages offline, cluster its outputs by (kind, template), have the human review the clusters once, and
freeze them as rules. The online path then has near-zero LLM dependence, and the LLM's role becomes "propose
new rule families for review" — a development-time agent, not a runtime one.

---

## 3. Decision robustness: use agents to *stress* the deterministic core, never to override it

### 3.1 Perturbation ensemble → confidence band (high value, low cost, no LLM) — ADOPTED as a report
Re-run the forecast under a small grid (estimator median/mean, horizon 84/86/90, intraday on/off). Report
for each row: how many variants agree with the shipped status/method. Rows where the decision flips under
tiny perturbations are *threshold cases*; for those, prefer the financially safer status when the spec allows
(e.g. `affordable_now` vs `affordable_with_plan` → the one that still completes the request) and say so in the
explanation. This converts the calibration risk identified in the review into a per-row risk score.

### 3.2 Deterministic critic — ADOPTED (`code/critic.py`)
Beyond the contract validator, add invariant checks that would catch *plausible but wrong* decisions:
- the chosen plan's simulated minimum ≥ the profile minimum (re-simulate from scratch, independent code path);
- `amount_safe_to_pay` recomputed by brute force (day-by-day) equals the closed form;
- every spending change references a series that actually has an occurrence before the crunch date;
- installments: schedule dates strictly increasing, last ≤ horizon.
Any failure → fallback row + trace. This is "reflection" implemented as code.

*Implemented: `critic.verify` re-simulates the shipped plan with the shipped changes from the rendered
strings, checks `amount_safe_to_pay` is safe and maximal (safe + 0.01 must fail), and re-simulates the
earliest date. Runs on every request; 0 failures on the 250 + 25 samples.*

### 3.3 LLM critic with a *narrow* job (low value today, keep as optional tooling)
The deleted audit agent asked the model to review whole traces — too broad, unactionable. A useful variant
asks one question per row: "Given these facts, is there any *stated* evidence the pipeline ignored?" and
returns quoted spans. Output goes to a review file, never to the decision. Run it only on threshold rows from
3.1 to keep cost near zero.

### 3.4 Counterfactual explanations (medium value, low cost)
Give the explanation agent the *runner-up* candidate and the reason it lost (deadline / changes / cost /
start / payments). Drafts then say why installments beat waiting, etc. Grounding check extends naturally
(the runner-up's figures join the allowed set). Improves the "usefulness" criterion without touching decisions.

---

## 4. Orchestration mechanics

### 4.1 Stage graph with explicit contracts (medium value, medium cost)
Formalise the pipeline as stages with typed inputs/outputs (already true in code) and make each stage
idempotent and separately replayable from its cached inputs: `facts.json → ledger.json → series.json →
timeline.json → candidates.json → decision.json`. Failures then resume from the last good stage, and the
trace *is* the state. Cheap to add given `Trace` already holds every intermediate.

### 4.2 Budget-aware scheduling (low cost) — ADOPTED
A token/cost budget per run (`BOW_MAX_USD`): the client refuses new calls once the budget is spent and the
run completes deterministically. Combine with the circuit breaker. Prevents runaway cost when a cache is
invalidated by a prompt-version bump.

*Implemented: `BOW_MAX_USD` → `config.MAX_USD_PER_RUN`; the client tracks estimated spend from the price table.*

### 4.3 Concurrency with determinism (adopted for explanations)
Extraction calls are independent per source; the explanation calls are independent per request. Parallelise
both; keep ordering by index and cache writes atomic (already true). The decision loop stays sequential — it
is 0.7 s for 250 rows and its ordering must be reproducible.

### 4.4 Prompt/version contracts (medium value, low cost) — PARTIALLY ADOPTED
Every prompt has `PROMPT_VERSION`; add a golden set: 10 messages, 5 images, 10 decisions with expected
extractions/explanations, run in CI when the prompt changes. A prompt edit that shifts extraction becomes a
failing test rather than a silent output change (the "never state a number of days" edit this session broke
grounding for 116 rows — a golden set would have caught it before a full run).

*Implemented for the deterministic layer: `tests/test_agents.py::test_message_rules_golden_set` (15 messages).
A model-side golden set needs API access and is left for when credit is available.*

### 4.5 Model routing by task (low cost)
Vision reads: keep the stronger model. Message classification and explanation drafts: a smaller model is
adequate (closed schema, short text); route by agent with the same client. Keep pricing per model in the
usage report (already supported).

---

## 5. Human-in-the-loop, done properly

- A review queue file (`evaluation/review_queue.jsonl`) fed by: image disagreements, message facts without a
  verifier span, threshold rows from 3.1, fallback rows. Each entry carries the request id, the evidence,
  the pipeline's provisional decision and the safer alternative.
- Human resolutions are written back as hash-pinned evidence records (like the reviewed image table) so a
  re-run is reproducible and the decision path stays deterministic.

---

## 6. What not to do

- Do not let an LLM choose the payment plan, the status, or the spending changes. The ranking is fully
  specified; an LLM adds variance and nothing else.
- Do not add a "planner" agent to sequence the stages. The stage order is fixed by data dependencies.
- Do not add agent-to-agent conversation. Every handoff here is a typed record; keep it that way.
- Do not use the model to "estimate" variable spending. The labels are unbiased realisations of the same
  generator; a statistical estimator beats a language model at this by construction.

---

## 7. Priority list

| # | Idea | Value | Cost | Risk |
|---|---|---|---|---|
| 1 | Perturbation ensemble → per-row confidence, safer-status on flips (3.1) | high | low | none (no LLM) |
| 2 | Deterministic critic invariants + brute-force recompute (3.2) | high | low | none |
| 3 | Two-reader + arithmetic arbitration for images (2.1) | high | low | 16 extra calls |
| 4 | Extractor→verifier span check for message facts (2.3) | high | medium | more calls on unknown messages |
| 5 | Golden set for prompts in CI (4.4) | medium | low | none |
| 6 | Confidence-gated adoption (2.4) | medium | low | none |
| 7 | Counterfactual explanations (3.4) | medium | low | re-draft cost |
| 8 | Stage replay from trace (4.1) | medium | medium | none |
| 9 | Budget guard (4.2) | low | low | none |
| 10 | Rule mining offline (2.5) | medium | medium | needs review time |

Items 1, 2, 5 and 9 need no model calls and could be added in an hour each; 3 and 4 are the ones that would
change how much the evidence layer can be trusted on *unseen* data, which is where the current design's
reliance on a human table is weakest.
