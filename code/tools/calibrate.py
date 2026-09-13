"""Calibration grid over forecasting knobs, scored on dataset/sample_requests.csv.

    python code/tools/calibrate.py

The 25 samples are used only to compare estimator variants; they are never
written into predictions. The hidden truth derives from unseen future events, so
agreement is bounded — prefer the configuration with the best categorical
agreement and the lowest amount error, and keep the conservative reading on ties.
"""
from __future__ import annotations

import itertools
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from decide import Services, decide_request  # noqa: E402
from loaders import load_dataset, load_requests  # noqa: E402
from scoring import score  # noqa: E402


def main() -> int:
    ds = load_dataset(config.DATASET_DIR, "sample_requests.csv")
    _, labeled = load_requests(config.DATASET_DIR / "sample_requests.csv", with_labels=True)
    svc = Services(use_llm=False)
    print("estimator      salary_msg_amounts  status method plan earliest changes  mean_rel_err")
    for est, msg_amounts in itertools.product(["mean", "median", "mean_recent", "last", "min", "max"], [False, True]):
        config.ESTIMATOR = est
        config.SALARY_MESSAGE_AMOUNTS = msg_amounts
        rows = [decide_request(r, ds, svc)[0].to_row() for r in ds.requests]
        rep = score(rows, labeled)
        mre = sum(rep.rel_errors) / len(rep.rel_errors)
        print(f"{est:14s} {str(msg_amounts):18s}  {rep.status_ok:6d} {rep.method_ok:6d} {rep.plan_ok:4d} {rep.earliest_ok:8d} {rep.changes_ok:7d}  {mre:.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
