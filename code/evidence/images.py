"""Image evidence: blank event amounts extracted from receipts / bills / payslips.

Resolution order for each image:
  1. cache hit (same file hash + prompt version) written by the vision agent — the cache ships
     with the code, so a run without an API key reproduces the same readings;
  2. vision agent call (if an API key is available and --no-llm is not set): two independently
     framed readings — reader A answers the ledger row's question, reader B enumerates every
     labelled amount — arbitrated below with a line-item arithmetic check;
  3. unknown (the event amount stays unknown; never zero; the event is excluded and flagged).

No hand-transcribed values are used at runtime. The human readings of the dataset images live in
tests/golden_image_readings.py and are used only to *evaluate* the reader (an evaluation workflow),
never to answer a row.
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Optional

import config
from evidence.cache import Cache, sha256_file
from models import Event, EvidenceFact, ImageRef

TOTAL_WORDS = ("total", "net", "due", "balance", "payable", "paid", "received", "amount", "grand")
CHARGE_WORDS = ("tax", "gst", "vat", "cgst", "sgst", "igst", "service", "charge", "fee", "tip", "delivery", "surcharge", "cess", "handling", "convenience", "round")
SUBTOTAL_WORDS = ("subtotal", "sub total", "sub-total")
FINAL_TOTAL_WORDS = ("grand total", "net pay", "net amount", "net salary", "take home", "incl tax", "incl. tax", "including tax", "total(incl", "balance due", "amount due", "amount payable", "total paid", "total amount")
# figures that are never the answer to "what does this document establish for the row"
NON_ANSWER_WORDS = ("cash paid", "paid by", "tendered", "tender", "change", "previous balance", "previous", "opening", "brought forward", "carried forward", "payments", "discount")
DIGIT_MISREAD_TOLERANCE = Decimal("0.03")  # arithmetic may correct an agreed total only within this relative distance
MATCH_TOLERANCE = Decimal("1")  # OCR readings of the same printed figure may differ by paise/cents (8528 vs 8528.10)


def _d(x) -> Optional[Decimal]:
    try:
        return Decimal(str(x)) if x is not None else None
    except (ArithmeticError, ValueError):
        return None


def _same(a: Optional[Decimal], b: Optional[Decimal]) -> bool:
    return a is not None and b is not None and abs(a - b) <= MATCH_TOLERANCE


def arbitrate(model_amt: Optional[Decimal], enumeration: Optional[dict], is_credit: bool) -> tuple[Optional[Decimal], str, float]:
    """Combine reader A (the targeted answer) with reader B (every labelled amount) and arithmetic.

    Reader A answers the ledger row's question; reader B only sees figures. So:
      1. A matches a printed total that B also read            -> A, corroborated (two readers, one figure)
      2. A matches what the line items (+ taxes/fees) add up to -> A, corroborated by arithmetic
      3. A matches nothing printed, but B lists a total that the line items add up to -> that total
         (A is most likely a misread), flagged
      4. A matches nothing and nothing is arithmetic-backed      -> A, flagged low confidence
      5. no A: an arithmetic-backed printed total (safer of several) or unknown
    Arithmetic can therefore confirm or replace a misread, but never overrule a figure both readers
    printed (a payslip's "Total earnings" also adds up, yet it is not the net pay).
    Returns (amount, rationale, confidence).
    """
    totals: list[Decimal] = []
    subtotals: list[Decimal] = []
    items: list[Decimal] = []
    charges: list[Decimal] = []
    for a in (enumeration or {}).get("amounts", []) or []:
        v = _d(a.get("amount"))
        if v is None:
            continue
        label = str(a.get("label", "")).lower()
        if any(w in label for w in NON_ANSWER_WORDS):
            continue
        if any(w in label for w in SUBTOTAL_WORDS):
            subtotals.append(v)
        elif any(w in label for w in FINAL_TOTAL_WORDS):
            totals.append(v)  # "grand total", "net pay", "total (incl taxes)" are totals even when they mention tax
        elif any(w in label for w in CHARGE_WORDS):
            charges.append(v)  # taxes and fees sit between the subtotal and the amount charged
        elif a.get("kind") == "total" or any(w in label for w in TOTAL_WORDS):
            totals.append(v)
        else:
            items.append(v)
    arith: list[Decimal] = []
    charge_sum = sum(charges, Decimal(0))
    if len(items) >= 2:
        arith.append(sum(items, Decimal(0)) + charge_sum)
    for sub in subtotals:
        arith.append(sub + charge_sum)

    def in_totals(v: Decimal) -> bool:
        return any(_same(v, t) for t in totals)

    def arith_backed(v: Decimal) -> bool:
        return any(_same(v, s) for s in arith)

    if model_amt is not None:
        if in_totals(model_amt):
            if arith_backed(model_amt):
                return model_amt, f"{model_amt} corroborated by reader A, reader B total, line items add up", 0.95
            # both readers agree on the printed digits, but the line items add up to a figure that is
            # nearly the same and is not printed anywhere else: a single misread digit (handwritten
            # totals). Arithmetic wins by a narrow margin; anything further apart is a different figure.
            near = [s_ for s_ in arith if s_ != model_amt and abs(s_ - model_amt) <= model_amt * DIGIT_MISREAD_TOLERANCE and not in_totals(s_)]
            if near:
                fixed = min(near, key=lambda s_: abs(s_ - model_amt))
                return fixed, f"{fixed} by arithmetic: both readers printed {model_amt} but the line items add up to {fixed} (digit misread)", 0.85
            return model_amt, f"{model_amt} corroborated by reader A, reader B total", 0.95
        if arith_backed(model_amt):
            return model_amt, f"{model_amt} corroborated by reader A, line items add up", 0.9
        backed = [t for t in totals if arith_backed(t)]
        if backed:
            chosen = min(backed) if is_credit else max(backed)
            return chosen, f"reader A {model_amt} matches nothing printed; arithmetic-backed printed total {chosen} adopted (flag for review)", 0.75
        return model_amt, f"reader A {model_amt} uncorroborated (reader B totals {sorted(set(totals))}, arithmetic {arith}); kept, flag for review", 0.6
    backed = [t for t in totals if arith_backed(t)]
    if backed:
        chosen = min(backed) if is_credit else max(backed)
        return chosen, f"no targeted reading; arithmetic-backed printed total {chosen} adopted (safer of {sorted(backed)})", 0.6
    return None, "no reading available", 0.0


def _fact_from_record(image: ImageRef, rec: dict, extractor: str, event: Optional[Event] = None, enumeration: Optional[dict] = None) -> EvidenceFact:
    model_amt = _d(rec.get("amount"))
    is_credit = event is not None and event.direction == "credit"
    if enumeration is None:
        amt, why, conf = model_amt, str(rec.get("rationale", ""))[:300], min(float(rec.get("confidence") or 0.0), 0.8)
        why = "single reading (second reader unavailable). " + why
    else:
        amt, why, conf = arbitrate(model_amt, enumeration, is_credit)
        why = f"{why}. reader A: {model_amt} ({rec.get('field_used')}). " + str(rec.get("rationale", ""))[:200]
    return EvidenceFact(
        kind="amount" if amt is not None else "unknown",
        source_kind="image",
        source_id=image.image_id,
        source_file=f"dataset/media/images/{image.image_id}.png",
        target_event_id=image.related_event_id,
        amount=amt,
        currency=rec.get("currency"),
        confidence=conf,
        rationale=why[:400],
        extractor=extractor,
    )


def resolve_image(image: ImageRef, event: Optional[Event], use_llm: bool, cache: Cache, agent=None, refresh: bool = False) -> EvidenceFact:
    path = Path(image.path)
    if not path.exists():
        return EvidenceFact("unknown", "image", image.image_id, image.path, image.related_event_id, confidence=0.0, rationale="image file missing", extractor="rules")
    content_hash = sha256_file(path)
    rec = None if refresh else cache.get(image.image_id, content_hash)
    if rec is None and use_llm and agent is not None:
        result = agent.extract(image, event)
        if result is not None:
            rec = {
                "source_file": f"dataset/media/images/{image.image_id}.png",
                "source_id": image.image_id,
                "target_event_id": image.related_event_id,
                "content_sha256": content_hash,
                "prompt_version": config.PROMPT_VERSION,
                "provider": config.PROVIDER,
                "model": result.get("model"),
                "extracted": {"amount": result.get("amount"), "currency": result.get("currency"), "field_used": result.get("field_used"), "document_type": result.get("document_type")},
                "confidence": result.get("confidence"),
                "rationale": result.get("rationale"),
                "usage": result.get("usage"),
            }
            cache.put(image.image_id, rec)
    if rec is not None:
        # second reader (enumeration) is added to the same record once; cache hits never call again
        if "enumeration" not in rec and use_llm and agent is not None and hasattr(agent, "enumerate"):
            enum = agent.enumerate(image)
            if enum is not None:
                rec = dict(rec, enumeration=enum)
                cache.put(image.image_id, rec)
        return _fact_from_record(image, rec.get("extracted", {}) | {"confidence": rec.get("confidence"), "rationale": rec.get("rationale")},
                                 "llm", event, rec.get("enumeration"))
    return EvidenceFact("unknown", "image", image.image_id, image.path, image.related_event_id, confidence=0.0,
                        rationale="no reading available (no cache for this file hash and no model access)", extractor="rules")
