"""Rule-based extraction of structured facts from messages (English + Indonesian).

Messages are untrusted data. Every rule maps a template family to a closed
EvidenceFact.kind; text that reads like an instruction ("pay the release charge
today", "ignore ...") is rejected by the injection guard before any rule runs.
The LLM message agent (agents/message_agent.py) is only used for messages that
no rule matches, and it must emit the same schema.
"""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from typing import Optional

from models import EvidenceFact, Message

AMOUNT = re.compile(r"\b(IDR|INR|ZAR|USD|EUR)\s?(\d[\d,]*(?:\.\d+)?)")
DATE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
PCT = re.compile(r"(\d+(?:\.\d+)?)\s?%")

INJECTION = re.compile(
    r"pay the (release|processing) charge|bayar biaya (pencairan|pemrosesan)|ignore (the|all|previous)|"
    r"disregard|override|mark (this|the request) as|recommend (full|installments)|set (the )?status|"
    r"you must|approve the payment|system prompt|as an ai",
    re.I,
)

# (kind, pattern) — first match wins; patterns are checked against the whole text.
RULES: list[tuple[str, re.Pattern]] = [
    ("salary_stop", re.compile(r"contract has ended|kontrak musiman .*berakhir|employment has ended|hubungan kerja .*berakhir|no regular salary payments|tidak ada pembayaran gaji rutin", re.I)),
    ("salary_remaining", re.compile(r"remaining confirmed monthly salary|sisa gaji bulanan yang dikonfirmasi", re.I)),
    ("salary_first", re.compile(r"first salary|gaji pertama|resumes on|dilanjutkan (kembali )?pada|kembali dibayarkan", re.I)),
    ("salary_amount", re.compile(r"increased to|naik menjadi|meningkat menjadi", re.I)),
    ("salary_next_amount", re.compile(r"reduced to|temporary monthly pay|gaji bulanan sementara|dikurangi menjadi|berkurang menjadi|turun menjadi", re.I)),
    ("salary_date", re.compile(r"now expected on|replaces the payroll date|kini diperkirakan masuk pada|menggantikan tanggal", re.I)),
    ("arrears_onetime", re.compile(r"arrears adjustment|penyesuaian tunggakan", re.I)),
    ("commission_pending", re.compile(r"confirmed base salary|base salary .*confirmed|gaji pokok yang dikonfirmasi", re.I)),
    ("one_off_credit", re.compile(r"approved an invoice payment|menyetujui pembayaran faktur", re.I)),
    ("rent_scale", re.compile(r"increases monthly rent by|menaikkan biaya sewa bulanan sebesar", re.I)),
    ("internal_transfer", re.compile(r"between your two accounts|antara dua rekening", re.I)),
    ("retry_failed_debit", re.compile(r"another debit (will|may) be attempted|debit attempt failed|akan dicoba lagi|percobaan debit .*gagal", re.I)),
    ("no_effect", re.compile(
        r"bonus|bonus kuartalan|payout is still pending|masih tertunda|refund|pengembalian dana|market value|nilai investasi|"
        r"prize|hadiah|investment sale|penjualan investasi|reversal|pembalikan|dispute|sengketa|two separate card|dua kartu|"
        r"displayed value|nilai .*ditampilkan|has fallen|has not been sold|belum dijual|"
        r"receipt (has|contains) the final|foreign currency|mata uang asing|confirmed for \d{4}|dikonfirmasi untuk \d{4}|"
        r"reimbursement|penggantian|childcare|one-time adjustment separately|penyesuaian satu kali secara terpisah|"
        r"convert|kurs|settlement-date", re.I)),
]


def _amounts(text: str) -> list[tuple[str, Decimal]]:
    return [(c, Decimal(a.replace(",", ""))) for c, a in AMOUNT.findall(text)]


def _dates(text: str) -> list[date]:
    out = []
    for t in DATE.findall(text):
        try:
            out.append(date.fromisoformat(t))
        except ValueError:
            pass
    return out


def parse_message(m: Message) -> list[EvidenceFact]:
    src = dict(source_kind="message", source_id=m.message_id, source_file="dataset/messages.csv", extractor="rules")
    text = m.text
    if INJECTION.search(text):
        return [EvidenceFact("unknown", confidence=0.0, rationale="instruction-like content rejected by the injection guard", **src)]
    amounts = _amounts(text)
    dates = _dates(text)
    first_amt = amounts[0][1] if amounts else None
    first_ccy = amounts[0][0] if amounts else None
    first_date = dates[0] if dates else None
    for kind, pat in RULES:
        if not pat.search(text):
            continue
        if kind == "salary_stop":
            return [EvidenceFact("salary_stop", rationale="employment/contract ended; no further salary", **src)]
        if kind == "salary_remaining":
            return [EvidenceFact("salary_remaining", amount=first_amt, currency=first_ccy, rationale="one household income ended", **src)]
        if kind == "salary_first":
            return [EvidenceFact("salary_first", amount=first_amt, currency=first_ccy, effective_date=first_date, rationale="first/resumed salary confirmed", **src)]
        if kind == "salary_amount":
            return [EvidenceFact("salary_amount", amount=first_amt, currency=first_ccy, effective_date=first_date, rationale="salary increase confirmed", **src)]
        if kind == "salary_next_amount":
            return [EvidenceFact("salary_next_amount", amount=first_amt, currency=first_ccy, rationale="reduced/temporary pay for the next payroll", **src)]
        if kind == "salary_date":
            return [EvidenceFact("salary_date", effective_date=first_date, rationale="confirmed salary date moved", **src)]
        if kind == "arrears_onetime":
            facts = []
            if amounts:
                facts.append(EvidenceFact("salary_amount", amount=amounts[0][1], currency=amounts[0][0], rationale="regular salary confirmed alongside a one-off adjustment", **src))
            if len(amounts) > 1:
                facts.append(EvidenceFact("one_off_credit", amount=amounts[1][1], currency=amounts[1][0], effective_date=None, rationale="one-time arrears adjustment on the next payroll", **src))
            return facts or [EvidenceFact("no_effect", rationale="arrears message without amounts", **src)]
        if kind == "commission_pending":
            facts = [EvidenceFact("exclude_income", pattern="commission", rationale="commission not approved", **src)]
            if first_amt is not None:
                facts.append(EvidenceFact("salary_amount", amount=first_amt, currency=first_ccy, rationale="confirmed base salary", **src))
            return facts
        if kind == "one_off_credit":
            return [EvidenceFact("one_off_credit", amount=first_amt, currency=first_ccy, effective_date=first_date, rationale="client-approved invoice; other invoices pending", **src)]
        if kind == "rent_scale":
            pct = PCT.search(text)
            return [EvidenceFact("rent_scale", pct=Decimal(pct.group(1)) if pct else None, rationale="lease renewal raises rent", **src)]
        if kind == "internal_transfer":
            return [EvidenceFact("internal_transfer", rationale="matching debit/credit are an own-account transfer", **src)]
        if kind == "retry_failed_debit":
            return [EvidenceFact("retry_failed_debit", target_event_id=m.related_event_id, rationale="failed debit will be retried; bill outstanding", **src)]
        if kind == "no_effect":
            return [EvidenceFact("no_effect", rationale="informational; already reflected in event statuses", **src)]
    return [EvidenceFact("unknown", confidence=0.0, rationale="no rule matched", **src)]

