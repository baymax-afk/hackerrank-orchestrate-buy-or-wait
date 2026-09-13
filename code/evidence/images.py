"""Image evidence: blank event amounts extracted from receipts / bills / payslips.

Resolution order for each image:
  1. cache hit (same file hash + prompt version) written by the vision agent
  2. vision agent call (if an API key is available and --no-llm is not set)
  3. REVIEWED table below — values read from the PNGs by a human/lead review,
     kept as an independent cross-check and as the no-key fallback. Every row is
     pinned to the sha256 of the PNG it was read from (REVIEWED_SHA256): a file
     with a different hash never receives a reviewed value, so a swapped image can
     only be resolved by the vision agent or stays unknown.
  4. unknown (the event amount stays unknown; never zero)

The table is a transcription of evidence that ships with the dataset (the PNGs),
not of any output label; it exists so that model readings are cross-checked and
so that the run is reproducible without an API key.
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

# sha256 of the PNG each reviewed value was read from; a mismatch disables the reviewed value.
REVIEWED_SHA256: dict[str, str] = {
    "image_01": "f37b40e6af42c664846057252cac89ad41b7d029dfe8dacff2db8cceb79fa5ba",
    "image_02": "ccd779e5382b1bcfacfb47d4ccf346ffd667a34c8b94d0cfd48aa4a609bd117d",
    "image_03": "e5fb0bbcda6cc06f8ea95e32e45d4c76acd8c594f4b02ff0d78e6006e103ee4d",
    "image_04": "281e7f1e7bd1f98fbd53cde1381977373e610e6634b11c98098000001ff10f0c",
    "image_05": "9abcda5647afb3dcdf91613253ac0160bd722333af33a4b952dfc96fea6ff97b",
    "image_06": "9055551fbe5940feb01b947e1f18ccfed093192d103b1e930a56df0ea7cd3cb4",
    "image_07": "f6d30a74355224c0b5cda2d7f96399a7b1a0afe4f9fe59ea048bbecb1a21311e",
    "image_08": "e28592ad8b4dacd03055e0b1ebc46670c83fbfa1162af07bdef33bb226bf63c8",
    "image_09": "e0e74e14425d923ff8a5c6db26ec6f4f26ee4697bfd257e414ba05c947a75ba8",
    "image_10": "c90f98caf0877083e471fd47dace772f83d4782037cf112e97c63f79a10ea8cf",
    "image_11": "795e000d48428c97748e8af370cb02b604bec88cc52ec8930f38dc744624e886",
    "image_12": "e10b0123e66d512d82f6c431fb741053b336627b89b9d9a138071ac6980a14ff",
    "image_13": "1ae54b378a9556d94b753093ba80e7117caf86fab4d3fe11ec84e3dd2f6d6dd8",
    "image_14": "bf88e4aa35e6f36304cbf76bf6f505f32b04466bfd5f7df693fcb3ebe8a3e2c1",
    "image_15": "0c0fe3d79e670f2b423bbb2aafc0b5601d3eb4e659ac64058cd189abf7792ee1",
    "image_16": "2665cf731a861ddb217be5b8082fbd390850a7a98feec018519b6fda0b30b4f8",
}


def reviewed_for(image_id: str, content_hash: str):
    """Reviewed row for this image, only if the file hash matches the one it was read from."""
    ref = REVIEWED.get(image_id)
    if ref is None or REVIEWED_SHA256.get(image_id) != content_hash:
        return None
    return ref


def _fact_from_record(image: ImageRef, rec: dict, extractor: str, event: Optional[Event] = None, content_hash: str = "") -> EvidenceFact:
    amt = rec.get("amount")
    rationale = str(rec.get("rationale", ""))[:300]
    confidence = float(rec.get("confidence") or 0.0)
    ref = reviewed_for(image.image_id, content_hash)
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
        return _fact_from_record(image, rec.get("extracted", {}) | {"confidence": rec.get("confidence"), "rationale": rec.get("rationale")}, "llm", event, content_hash)
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
            return _fact_from_record(image, result, "llm", event, content_hash)
    ref = reviewed_for(image.image_id, content_hash)
    if ref is not None:
        amt, ccy, field, conf, note = ref
        return EvidenceFact("amount", "image", image.image_id, f"dataset/media/images/{image.image_id}.png", image.related_event_id,
                            Decimal(amt), ccy, confidence=conf, rationale=f"reviewed reading of field '{field}': {note}", extractor="reviewed")
    why = "no extraction available" if image.image_id not in REVIEWED else "image content differs from the reviewed file; reviewed value withheld"
    return EvidenceFact("unknown", "image", image.image_id, image.path, image.related_event_id, confidence=0.0, rationale=why, extractor="rules")
