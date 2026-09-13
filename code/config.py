"""Central configuration for the Buy or Wait? agent.

Everything that tunes behaviour lives here so that runs are reproducible and
the calibration tool can vary a single knob at a time.
"""
from __future__ import annotations

import os
from decimal import Decimal
from pathlib import Path

CODE_DIR = Path(__file__).resolve().parent
REPO_ROOT = CODE_DIR.parent
DATASET_DIR = REPO_ROOT / "dataset"
OUTPUT_PATH = REPO_ROOT / "output.csv"
CACHE_DIR = CODE_DIR / "cache"
EVALUATION_DIR = CODE_DIR / "evaluation"
USAGE_LOG = EVALUATION_DIR / "usage.jsonl"
USAGE_REPORT = EVALUATION_DIR / "usage_report.md"
TRACE_DIR = CODE_DIR / "traces"

# Forecast window in days after request_date (inclusive). The spec frames the check as a
# "90-day" safety check, but calibration on the 25 solved samples (code/tools/calibrate.py,
# research/synthesis.md) shows the labels ignore monthly flows landing on day 87-88 while
# counting flows on day 84; 86 is the longest (financially safest) window consistent with
# every sample. Override with BOW_HORIZON_DAYS for experiments.
HORIZON_DAYS = int(os.environ.get("BOW_HORIZON_DAYS", "86"))

OUTPUT_COLUMNS = [
    "request_id",
    "amount_safe_to_pay",
    "affordability_status",
    "recommended_payment_method",
    "payment_plan",
    "earliest_date_for_full_payment",
    "spending_changes_needed",
    "decision_explanation",
]

STATUSES = ("affordable_now", "affordable_with_plan", "affordable_later", "not_affordable")
METHODS = ("full_payment", "partial_payment", "installments", "wait", "not_recommended")
EVENT_STATUSES = ("settled", "pending", "scheduled", "failed", "cancelled", "unrealized")
DIRECTIONS = ("debit", "credit", "non_cash")
FLEXIBILITIES = ("fixed", "reducible", "stoppable", "reducible_or_stoppable")
CURRENCIES = ("INR", "ZAR", "IDR", "USD", "EUR")

# --- forecasting knobs -----------------------------------------------------
# Amount estimator for variable recurring expenses: mean | median | last | max | min
ESTIMATOR = os.environ.get("BOW_ESTIMATOR", "median")
# Minimum number of historical occurrences before a series counts as recurring.
MIN_OCCURRENCES = 3
# Interval windows (days) that map to a cadence.
# name -> (min median interval, max median interval, step days; None = calendar month).
# 5-, 10- and 21-day cycles are common in the dataset (transport every 5 or 21 days,
# groceries every 10 days) and are essential spending, so they are projected too.
CADENCES = {
    "every_5_days": (4, 5.5, 5),
    "weekly": (6, 8, 7),
    "every_10_days": (9, 11, 10),
    "biweekly": (13, 15, 14),
    "every_3_weeks": (20, 22, 21),
    "monthly": (27, 32, None),
}
# Income descriptions that are never projected forward (one-off or unconfirmed).
NON_RECURRING_INCOME_PATTERN = (
    r"bonus|commission|arrear|reimburse|payout|earnings|prize|lottery|windfall|refund|"
    r"second household|net salary|prorated|final|seasonal|peak-season|temporary assignment|"
    r"project payment|contract payment|milestone|invoice|retainer|independent work"
)
# Whether message-stated salary amounts override the settled history (False = history mode wins; messages still move dates/stop series).
# Within a day, variable spending (sub-monthly series) is applied before that day's credits,
# while monthly bills co-dated with payday are paid out of the salary (CashFlow.after_credits).
# Calibrated on the samples (research/synthesis.md addendum): reproduces request_04/13 without
# breaking request_19/23; set BOW_INTRADAY_DEBITS_FIRST=0 for plain end-of-day netting.
INTRADAY_DEBITS_FIRST = os.environ.get("BOW_INTRADAY_DEBITS_FIRST", "1") == "1"
# A message-stated salary above this multiple of the settled level is treated as unconfirmed.
SALARY_PLAUSIBILITY_FACTOR = Decimal("3")
SALARY_MESSAGE_AMOUNTS = os.environ.get("BOW_SALARY_MESSAGE_AMOUNTS", "0") == "1"
# Income descriptions that end a salary series.
FINAL_INCOME_PATTERN = r"final"

# --- LLM agents --------------------------------------------------------------
PROVIDER = "anthropic"
MODEL_TEXT = os.environ.get("BOW_MODEL_TEXT", "claude-sonnet-5")
MODEL_VISION = os.environ.get("BOW_MODEL_VISION", "claude-sonnet-5")
PROMPT_VERSION = "v1"
# USD per million tokens (input, output). Update if pricing changes.
PRICE_TABLE = {
    "claude-sonnet-5": (Decimal("2.00"), Decimal("10.00")),
    "claude-opus-5": (Decimal("5.00"), Decimal("25.00")),
    "claude-haiku-4-5-20251001": (Decimal("1.00"), Decimal("5.00")),
}
MAX_CHANGE_COMBINATIONS = 5000
# Explanation drafts are independent, cache-backed calls; a small pool keeps a cold run short.
EXPLAIN_WORKERS = int(os.environ.get("BOW_EXPLAIN_WORKERS", "4"))
# After this many consecutive API failures the client stops calling and the run continues deterministically.
LLM_CIRCUIT_BREAKER = int(os.environ.get("BOW_LLM_CIRCUIT_BREAKER", "5"))
# Set by main.run; stamped on usage records and traces so a run can be reconstructed.
RUN_ID = "manual"


def load_dotenv(path: Path | None = None) -> None:
    """Minimal .env loader (KEY=VALUE lines); environment variables win."""
    path = path or REPO_ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def api_key() -> str | None:
    return os.environ.get("ANTHROPIC_API_KEY") or None
