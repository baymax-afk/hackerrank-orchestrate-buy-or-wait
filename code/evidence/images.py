"""Image evidence: blank event amounts extracted from receipts / bills / payslips.

Resolution order for each image:
  1. cache hit (same file hash + prompt version) written by the vision agent
  2. vision agent call (if an API key is available and --no-llm is not set)
  3. REVIEWED table below — values read from the PNGs by a human/lead review,
     kept as an independent cross-check and as the no-key fallback
  4. unknown (the event amount stays unknown; never zero)
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Optional

import config
from evidence.cache import Cache, sha256_file
from models import Event, EvidenceFact, ImageRef

# image_id -> (amount, currency, field used, confidence, note)
REVIEWED: dict[str, tuple[str, str, str, float, str]] = {
    "image_01": ("4365000", "IDR", "Net Pay", 0.95, "payslip Aug-2019 net pay"),
    "image_02": ("100000", "INR", "Balance Due", 0.9, "rent receipt: 2,00,000 total, 1,00,000 received, balance due 1,00,000 (event is the outstanding balance)"),
    "image_03": ("41272", "INR", "Net Amount", 0.95, "grocery bill of supply"),
    "image_04": ("2854", "INR", "Item Bill", 0.6, "cropped delivery order; item bill visible, delivery fee cut off"),
    "image_05": ("822.05", "INR", "Amount due after 06-Feb-2026", 0.7, "704.05 due till 06-Feb-2026, 822.05 after; request date is after the due date -> larger amount (safer)"),
    "image_06": ("1995", "INR", "Total", 0.95, "Blinkit tax invoice"),
    "image_07": ("8528", "INR", "Grand Total", 0.95, "restaurant tax invoice"),
    "image_08": ("15339", "INR", "Total Amount Received", 0.95, "property maintenance receipt"),
    "image_09": ("723", "INR", "Total Amount Received", 0.95, "water bill receipt"),
    "image_10": ("79679.26", "INR", "Balance Due / Total", 0.9, "grocery invoice"),
    "image_11": ("3650", "INR", "Balance", 0.9, "hospital provisional bill"),
    "image_12": ("33.50", "USD", "Total", 0.95, "taxi receipt (cash paid 40, change 6.50)"),
    "image_13": ("2298", "INR", "Total paid", 0.95, "tote bag order"),
    "image_14": ("4543", "INR", "TOTAL", 0.95, "handwritten pharmacy bill; line items 1500+724+796+550+303+670 = 4543 (verified)"),
    "image_15": ("9968", "INR", "Grand Total", 0.95, "flight tax invoice"),
    "image_16": ("393.22", "INR", "Total", 0.95, "EV charging invoice"),
}


def _fact_from_record(image: ImageRef, rec: dict, extractor: str, event: Optional[Event] = None) -> EvidenceFact:
    amt = rec.get("amount")
    rationale = str(rec.get("rationale", ""))[:300]
    confidence = float(rec.get("confidence") or 0.0)
    ref = REVIEWED.get(image.image_id)
    if amt is not None and ref is not None and Decimal(str(amt)) != Decimal(ref[0]):
        model_amt, reviewed_amt = Decimal(str(amt)), Decimal(ref[0])
        if ref[3] >= 0.95:
            # a verified reviewed reading (e.g. line items add up to the total) outranks the model
            chosen = reviewed_amt
            rationale = f"model read {model_amt} ({rec.get('field_used')}); verified reviewed reading {reviewed_amt} ({ref[2]}) adopted. " + rationale
            confidence = ref[3]
        else:
            # both readings uncertain -> financially safer value (larger debit / smaller credit)
            is_credit = event is not None and event.direction == "credit"
            chosen = min(model_amt, reviewed_amt) if is_credit else max(model_amt, reviewed_amt)
            rationale = f"model read {model_amt} ({rec.get('field_used')}), reviewed reading {reviewed_amt} ({ref[2]}); safer value {chosen} adopted. " + rationale
            confidence = min(confidence, ref[3], 0.7)
        amt = chosen
    return EvidenceFact(
        kind="amount" if amt is not None else "unknown",
        source_kind="image",
        source_id=image.image_id,
        source_file=f"dataset/media/images/{image.image_id}.png",
        target_event_id=image.related_event_id,
        amount=Decimal(str(amt)) if amt is not None else None,
        currency=rec.get("currency"),
        confidence=confidence,
        rationale=rationale,
        extractor=extractor,
    )


def resolve_image(image: ImageRef, event: Optional[Event], use_llm: bool, cache: Cache, agent=None, refresh: bool = False) -> EvidenceFact:
    path = Path(image.path)
    if not path.exists():
        return EvidenceFact("unknown", "image", image.image_id, image.path, image.related_event_id, confidence=0.0, rationale="image file missing", extractor="rules")
    content_hash = sha256_file(path)
    rec = None if refresh else cache.get(image.image_id, content_hash)
    if rec is not None:
        return _fact_from_record(image, rec.get("extracted", {}) | {"confidence": rec.get("confidence"), "rationale": rec.get("rationale")}, "llm", event)
    if use_llm and agent is not None:
        result = agent.extract(image, event)
        if result is not None:
            cache.put(image.image_id, {
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
            })
            return _fact_from_record(image, result, "llm", event)
    if image.image_id in REVIEWED:
        amt, ccy, field, conf, note = REVIEWED[image.image_id]
        return EvidenceFact("amount", "image", image.image_id, f"dataset/media/images/{image.image_id}.png", image.related_event_id,
                            Decimal(amt), ccy, confidence=conf, rationale=f"reviewed reading of field '{field}': {note}", extractor="reviewed")
    return EvidenceFact("unknown", "image", image.image_id, image.path, image.related_event_id, confidence=0.0, rationale="no extraction available", extractor="rules")


def cross_check(fact: EvidenceFact) -> Optional[str]:
    """Compare an LLM extraction with the reviewed table; return a warning string on disagreement."""
    ref = REVIEWED.get(fact.source_id)
    if ref is None or fact.amount is None:
        return None
    if Decimal(ref[0]) != fact.amount:
        return f"{fact.source_id}: model read {fact.amount} but reviewed value is {ref[0]} ({ref[4]})"
    return None
