"""Typed records shared by every stage of the pipeline."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Optional


def numeric_id(identifier: str) -> int:
    """'event_12' -> 12, 'payment_option_100' -> 100 (for deterministic ordering)."""
    try:
        return int(identifier.rsplit("_", 1)[1])
    except (IndexError, ValueError):
        return 0


@dataclass(frozen=True)
class Profile:
    user_id: str
    home_currency: str
    current_available_balance: Decimal
    minimum_balance_to_keep: Decimal
    priorities: tuple[str, ...]
    protect: frozenset[str]
    reduce: frozenset[str]
    stop: frozenset[str]
    methods: frozenset[str]
    max_installment_months: Optional[int]


@dataclass(frozen=True)
class Event:
    event_id: str
    user_id: str
    event_type: str
    description: str
    category: str
    direction: str
    amount: Optional[Decimal]
    currency: str
    event_date: date
    settlement_date: Optional[date]
    status: str
    linked_event_id: Optional[str]
    flexibility: str
    minimum_allowed_amount: Optional[Decimal]

    @property
    def num(self) -> int:
        return numeric_id(self.event_id)

    @property
    def cash_date(self) -> Optional[date]:
        return self.settlement_date or self.event_date


@dataclass(frozen=True)
class PaymentOption:
    payment_option_id: str
    request_id: str
    payment_method: str
    payment_amount: Decimal
    payment_amount_text: str
    number_of_payments: int
    first_payment_date: date
    payment_frequency_days: Optional[int]
    financing_fee: Decimal
    total_payable_amount: Decimal

    @property
    def num(self) -> int:
        return numeric_id(self.payment_option_id)

    def schedule(self) -> tuple[tuple[date, Decimal], ...]:
        from datetime import timedelta

        step = self.payment_frequency_days or 0
        return tuple(
            (self.first_payment_date + timedelta(days=step * k), self.payment_amount)
            for k in range(self.number_of_payments)
        )


@dataclass(frozen=True)
class Request:
    request_id: str
    user_id: str
    request_date: date
    request_type: str
    requested_amount: Decimal
    requested_amount_text: str
    desired_completion_date: date
    allows_partial_payment: bool
    request_text: str

    @property
    def num(self) -> int:
        return numeric_id(self.request_id)


@dataclass(frozen=True)
class Message:
    message_id: str
    user_id: str
    request_id: Optional[str]
    related_event_id: Optional[str]
    sent_at: str
    source_type: str
    text: str


@dataclass(frozen=True)
class ImageRef:
    image_id: str
    user_id: str
    request_id: Optional[str]
    related_event_id: Optional[str]
    path: str


@dataclass(frozen=True)
class EvidenceFact:
    """A structured, provenance-tagged hypothesis extracted from a message or image.

    kind values (closed set):
      amount            -> amount for target_event_id (image or receipt confirmation)
      amend_event_date  -> move target scheduled event to effective_date
      amend_event_amount-> change target scheduled event amount
      retry_failed_debit-> reserve target failed debit again
      salary_amount     -> ongoing salary amount (from effective_date if given)
      salary_next_amount-> amount for the next payroll only
      salary_date       -> next salary date moves to effective_date
      salary_first      -> first salary amount on effective_date (new job)
      salary_stop       -> no further salary after the last settled/scheduled one
      salary_remaining  -> one household income ended; remaining monthly salary = amount
      one_off_credit    -> single confirmed credit (invoice, arrears) on effective_date
      rent_scale        -> multiply the rent series by (1 + pct/100)
      exclude_income    -> exclude a description pattern from projection (gig/commission/bonus)
      internal_transfer -> matching debit/credit are transfers; exclude from statistics
      no_effect         -> informational only
      unknown           -> could not interpret (ignored)
    """

    kind: str
    source_kind: str  # message | image
    source_id: str
    source_file: str
    target_event_id: Optional[str] = None
    amount: Optional[Decimal] = None
    currency: Optional[str] = None
    effective_date: Optional[date] = None
    pct: Optional[Decimal] = None
    pattern: Optional[str] = None
    confidence: float = 1.0
    rationale: str = ""
    extractor: str = "rules"  # rules | llm | reviewed


@dataclass(frozen=True)
class CashFlow:
    date: date
    amount: Decimal  # signed, home currency
    kind: str  # scheduled | settled | pending | recurring | recurring_income | plan | one_off
    source_id: str
    series_id: Optional[str] = None
    category: str = ""
    description: str = ""
    # True for debits that are paid out of the same day's credits (monthly bills co-dated with
    # payday); False for variable spending that must be covered by the balance carried into the day
    after_credits: bool = False


@dataclass(frozen=True)
class RecurringSeries:
    series_id: str  # representative (latest) event id
    category: str
    description: str
    cadence: str
    step_days: Optional[int]
    anchor: date
    amount: Decimal
    is_income: bool
    flexibility: str
    minimum_allowed_amount: Optional[Decimal]
    occurrences: int
    # optional step change: occurrences on/after this date use amount_after (e.g. a confirmed salary rise)
    amount_after: Optional[tuple[date, Decimal]] = None


@dataclass(frozen=True)
class Change:
    action: str  # stop | reduce_to
    event_id: str
    new_amount: Optional[Decimal] = None
    description: str = ""

    def render(self) -> str:
        from formatting import fmt_plan_amount

        if self.action == "stop":
            return f"stop:{self.event_id}"
        return f"reduce_to:{self.event_id}:{fmt_plan_amount(self.new_amount)}"


@dataclass(frozen=True)
class Candidate:
    status: str
    method: str
    payments: tuple[tuple[date, Decimal], ...]
    changes: tuple[Change, ...]
    total_paid: Decimal
    option: Optional[PaymentOption]
    min_projected_balance: Decimal
    completes_by_deadline: bool

    @property
    def start_date(self) -> date:
        return self.payments[0][0]

    @property
    def n_payments(self) -> int:
        return len(self.payments)


@dataclass
class Decision:
    request_id: str
    amount_safe_to_pay: Decimal
    affordability_status: str
    recommended_payment_method: str
    payment_plan: str
    earliest_date_for_full_payment: Optional[date]
    spending_changes_needed: str
    decision_explanation: str
    candidate: Optional[Candidate] = None

    def to_row(self) -> dict:
        from formatting import fmt_safe_amount

        return {
            "request_id": self.request_id,
            "amount_safe_to_pay": fmt_safe_amount(self.amount_safe_to_pay),
            "affordability_status": self.affordability_status,
            "recommended_payment_method": self.recommended_payment_method,
            "payment_plan": self.payment_plan,
            "earliest_date_for_full_payment": (
                self.earliest_date_for_full_payment.isoformat()
                if self.earliest_date_for_full_payment
                else ""
            ),
            "spending_changes_needed": self.spending_changes_needed,
            "decision_explanation": self.decision_explanation,
        }


@dataclass
class Trace:
    request_id: str
    facts: list = field(default_factory=list)
    flows: list = field(default_factory=list)
    series: list = field(default_factory=list)
    timeline: list = field(default_factory=list)
    candidates: list = field(default_factory=list)
    notes: list = field(default_factory=list)
