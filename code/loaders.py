"""CSV loading, schema validation and indexing (stdlib only)."""
from __future__ import annotations

import csv
import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable, Optional

import config
from models import Event, ImageRef, Message, PaymentOption, Profile, Request, numeric_id

log = logging.getLogger("bow.load")

SCHEMAS = {
    "financial_profiles.csv": [
        "user_id", "home_currency", "current_available_balance", "minimum_balance_to_keep",
        "financial_priorities", "expense_categories_to_protect",
        "expense_categories_user_is_willing_to_reduce", "expense_categories_user_is_willing_to_stop",
        "payment_methods_user_will_consider", "max_installment_months",
    ],
    "financial_events.csv": [
        "event_id", "user_id", "event_type", "description", "category", "direction", "amount",
        "currency", "event_date", "settlement_date", "status", "linked_event_id", "flexibility",
        "minimum_allowed_amount",
    ],
    "exchange_rates.csv": ["rate_date", "from_currency", "to_currency", "rate"],
    "requests.csv": [
        "request_id", "user_id", "request_date", "request_type", "requested_amount",
        "desired_completion_date", "allows_partial_payment", "request_text",
    ],
    "request_payment_options.csv": [
        "payment_option_id", "request_id", "payment_method", "payment_amount", "number_of_payments",
        "first_payment_date", "payment_frequency_days", "financing_fee", "total_payable_amount",
    ],
    "messages.csv": [
        "message_id", "user_id", "request_id", "related_event_id", "sent_at", "source_type", "message_text",
    ],
    "images.csv": ["image_id", "user_id", "request_id", "related_event_id"],
}


class SchemaError(ValueError):
    pass


def _read(path: Path, expected: list[str]) -> list[dict]:
    with open(path, encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        header = reader.fieldnames or []
        if header[: len(expected)] != expected:
            raise SchemaError(f"{path.name}: header {header} != {expected}")
        return [dict(row) for row in reader]


def _dec(text: str, what: str) -> Decimal:
    try:
        return Decimal(text.strip())
    except (InvalidOperation, AttributeError) as exc:
        raise SchemaError(f"bad decimal for {what}: {text!r}") from exc


def _opt_dec(text: str) -> Optional[Decimal]:
    text = (text or "").strip()
    return Decimal(text) if text else None


def _date(text: str, what: str) -> date:
    try:
        return date.fromisoformat(text.strip())
    except (ValueError, AttributeError) as exc:
        raise SchemaError(f"bad date for {what}: {text!r}") from exc


def _opt_date(text: str) -> Optional[date]:
    text = (text or "").strip()
    return date.fromisoformat(text) if text else None


def _list(text: str) -> tuple[str, ...]:
    text = (text or "").strip()
    return tuple(t.strip() for t in text.split("|") if t.strip()) if text else ()


def _enum(value: str, allowed: Iterable[str], what: str) -> str:
    if value not in allowed:
        raise SchemaError(f"{what}: unexpected value {value!r}")
    return value


@dataclass
class Dataset:
    profiles: dict[str, Profile]
    events: list[Event]
    events_by_user: dict[str, list[Event]]
    events_by_id: dict[str, Event]
    rates: dict[tuple[date, str, str], Decimal]
    requests: list[Request]
    options_by_request: dict[str, list[PaymentOption]]
    messages: list[Message]
    messages_by_user: dict[str, list[Message]]
    images: list[ImageRef]
    images_by_user: dict[str, list[ImageRef]] = field(default_factory=dict)
    dataset_dir: Path = config.DATASET_DIR


def load_profiles(path: Path) -> dict[str, Profile]:
    out: dict[str, Profile] = {}
    for r in _read(path, SCHEMAS["financial_profiles.csv"]):
        uid = r["user_id"].strip()
        if uid in out:
            raise SchemaError(f"duplicate user_id {uid}")
        methods = _list(r["payment_methods_user_will_consider"])
        for m in methods:
            _enum(m, ("full_payment", "partial_payment", "installments"), "payment method")
        mim = r["max_installment_months"].strip()
        out[uid] = Profile(
            user_id=uid,
            home_currency=_enum(r["home_currency"].strip(), config.CURRENCIES, "home_currency"),
            current_available_balance=_dec(r["current_available_balance"], "balance"),
            minimum_balance_to_keep=_dec(r["minimum_balance_to_keep"], "minimum"),
            priorities=_list(r["financial_priorities"]),
            protect=frozenset(_list(r["expense_categories_to_protect"])),
            reduce=frozenset(_list(r["expense_categories_user_is_willing_to_reduce"])),
            stop=frozenset(_list(r["expense_categories_user_is_willing_to_stop"])),
            methods=frozenset(methods),
            max_installment_months=int(mim) if mim else None,
        )
    return out


def load_events(path: Path) -> list[Event]:
    events = []
    for r in _read(path, SCHEMAS["financial_events.csv"]):
        events.append(
            Event(
                event_id=r["event_id"].strip(),
                user_id=r["user_id"].strip(),
                event_type=r["event_type"].strip(),
                description=r["description"].strip(),
                category=r["category"].strip(),
                direction=_enum(r["direction"].strip(), config.DIRECTIONS, "direction"),
                amount=_opt_dec(r["amount"]),
                currency=_enum(r["currency"].strip(), config.CURRENCIES, "currency"),
                event_date=_date(r["event_date"], "event_date"),
                settlement_date=_opt_date(r["settlement_date"]),
                status=_enum(r["status"].strip(), config.EVENT_STATUSES, "status"),
                linked_event_id=(r["linked_event_id"].strip() or None),
                flexibility=_enum(r["flexibility"].strip(), config.FLEXIBILITIES, "flexibility"),
                minimum_allowed_amount=_opt_dec(r["minimum_allowed_amount"]),
            )
        )
    events.sort(key=lambda e: e.num)
    return events


def load_rates(path: Path) -> dict[tuple[date, str, str], Decimal]:
    rates = {}
    for r in _read(path, SCHEMAS["exchange_rates.csv"]):
        key = (_date(r["rate_date"], "rate_date"), r["from_currency"].strip(), r["to_currency"].strip())
        rates[key] = _dec(r["rate"], "rate")
    return rates


def load_requests(path: Path, with_labels: bool = False) -> list[Request] | tuple[list[Request], list[dict]]:
    rows = _read(path, SCHEMAS["requests.csv"])
    reqs = []
    for r in rows:
        flag = r["allows_partial_payment"].strip().lower()
        if flag not in ("true", "false"):
            raise SchemaError(f"allows_partial_payment must be true/false, got {flag!r}")
        reqs.append(
            Request(
                request_id=r["request_id"].strip(),
                user_id=r["user_id"].strip(),
                request_date=_date(r["request_date"], "request_date"),
                request_type=r["request_type"].strip(),
                requested_amount=_dec(r["requested_amount"], "requested_amount"),
                requested_amount_text=r["requested_amount"].strip(),
                desired_completion_date=_date(r["desired_completion_date"], "desired_completion_date"),
                allows_partial_payment=(flag == "true"),
                request_text=r["request_text"],
            )
        )
    ids = [q.request_id for q in reqs]
    if len(set(ids)) != len(ids):
        raise SchemaError("duplicate request_id in requests file")
    if with_labels:
        return reqs, rows
    return reqs


def load_options(path: Path) -> dict[str, list[PaymentOption]]:
    by_req: dict[str, list[PaymentOption]] = defaultdict(list)
    for r in _read(path, SCHEMAS["request_payment_options.csv"]):
        freq = r["payment_frequency_days"].strip()
        n_pay = int(r["number_of_payments"])
        if r["payment_method"].strip() == "installments" and n_pay > 1 and not freq:
            log.warning("%s: installment option without payment_frequency_days; ignored", r["payment_option_id"])
            continue
        opt = PaymentOption(
            payment_option_id=r["payment_option_id"].strip(),
            request_id=r["request_id"].strip(),
            payment_method=_enum(r["payment_method"].strip(), ("full_payment", "installments", "partial_payment"), "option method"),
            payment_amount=_dec(r["payment_amount"], "payment_amount"),
            payment_amount_text=r["payment_amount"].strip(),
            number_of_payments=n_pay,
            first_payment_date=_date(r["first_payment_date"], "first_payment_date"),
            payment_frequency_days=int(freq) if freq else None,
            financing_fee=_dec(r["financing_fee"] or "0", "financing_fee"),
            total_payable_amount=_dec(r["total_payable_amount"], "total_payable_amount"),
        )
        by_req[opt.request_id].append(opt)
    for lst in by_req.values():
        lst.sort(key=lambda o: o.num)
    return dict(by_req)


def load_messages(path: Path) -> list[Message]:
    out = []
    for r in _read(path, SCHEMAS["messages.csv"]):
        out.append(
            Message(
                message_id=r["message_id"].strip(),
                user_id=r["user_id"].strip(),
                request_id=r["request_id"].strip() or None,
                related_event_id=r["related_event_id"].strip() or None,
                sent_at=r["sent_at"].strip(),
                source_type=r["source_type"].strip(),
                text=r["message_text"],
            )
        )
    out.sort(key=lambda m: numeric_id(m.message_id))
    return out


IMAGE_ID = re.compile(r"^image_\d+$")


def load_images(path: Path, media_dir: Path) -> list[ImageRef]:
    out = []
    for r in _read(path, SCHEMAS["images.csv"]):
        iid = r["image_id"].strip()
        if not IMAGE_ID.match(iid):
            # the id becomes a file name under media_dir; anything else is ignored (no path traversal)
            log.warning("images.csv: ignoring image id %r (not of the form image_<n>)", iid)
            continue
        out.append(
            ImageRef(
                image_id=iid,
                user_id=r["user_id"].strip(),
                request_id=r["request_id"].strip() or None,
                related_event_id=r["related_event_id"].strip() or None,
                path=str(media_dir / f"{iid}.png"),
            )
        )
    return out


def load_dataset(dataset_dir: Path = config.DATASET_DIR, requests_file: str = "requests.csv") -> Dataset:
    profiles = load_profiles(dataset_dir / "financial_profiles.csv")
    events = load_events(dataset_dir / "financial_events.csv")
    by_user: dict[str, list[Event]] = defaultdict(list)
    by_id: dict[str, Event] = {}
    for e in events:
        by_user[e.user_id].append(e)
        if e.event_id in by_id:
            raise SchemaError(f"duplicate event_id {e.event_id}")
        by_id[e.event_id] = e
    requests = load_requests(dataset_dir / requests_file)
    for q in requests:
        if q.user_id not in profiles:
            raise SchemaError(f"{q.request_id}: unknown user {q.user_id}")
    messages = load_messages(dataset_dir / "messages.csv")
    msgs_by_user: dict[str, list[Message]] = defaultdict(list)
    for m in messages:
        msgs_by_user[m.user_id].append(m)
    images = load_images(dataset_dir / "images.csv", dataset_dir / "media" / "images")
    imgs_by_user: dict[str, list[ImageRef]] = defaultdict(list)
    for i in images:
        imgs_by_user[i.user_id].append(i)
    return Dataset(
        profiles=profiles,
        events=events,
        events_by_user=dict(by_user),
        events_by_id=by_id,
        rates=load_rates(dataset_dir / "exchange_rates.csv"),
        requests=requests,
        options_by_request=load_options(dataset_dir / "request_payment_options.csv"),
        messages=messages,
        messages_by_user=dict(msgs_by_user),
        images=images,
        images_by_user=dict(imgs_by_user),
        dataset_dir=dataset_dir,
    )
