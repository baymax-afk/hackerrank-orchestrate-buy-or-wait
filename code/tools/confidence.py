"""Perturbation ensemble: how stable is each decision under small, defensible modelling variations?

    python code/tools/confidence.py                 # -> code/evaluation/confidence.jsonl + summary on stdout
    python code/tools/confidence.py --sample-check  # same on the 25 solved samples, with agreement vs labels

For every request the pipeline is re-run under a grid of variants (estimator, horizon, intra-day rule) and
the fraction of variants that reproduce the shipped status / method / earliest date is recorded. Rows with
low agreement are threshold cases: the forecast is within noise of a decision boundary. This never changes
output.csv; it tells a reviewer where to look and quantifies the calibration risk noted in
research/code_review.md.
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from decide import Services, decide_request  # noqa: E402
from loaders import load_dataset, load_requests  # noqa: E402

GRID = {
    "ESTIMATOR": ["median", "mean"],
    "HORIZON_DAYS": [84, 86, 90],
    "INTRADAY_DEBITS_FIRST": [True, False],
}
KEYS = ("affordability_status", "recommended_payment_method", "earliest_date_for_full_payment")


def _decide_all(ds, svc):
    out = {}
    for r in ds.requests:
        try:
            out[r.request_id] = decide_request(r, ds, svc)[0].to_row()
        except Exception as exc:  # a variant that cannot decide a row counts as disagreement
            out[r.request_id] = {"affordability_status": f"error:{type(exc).__name__}", "recommended_payment_method": "", "earliest_date_for_full_payment": ""}
    return out


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--sample-check", action="store_true")
    args = p.parse_args(argv)
    ds = load_dataset(config.DATASET_DIR, "sample_requests.csv" if args.sample_check else "requests.csv")
    svc = Services(use_llm=False)
    base = {k: getattr(config, k) for k in GRID}
    shipped = _decide_all(ds, svc)
    variants = []
    for combo in itertools.product(*GRID.values()):
        setting = dict(zip(GRID.keys(), combo))
        for k, v in setting.items():
            setattr(config, k, v)
        variants.append((setting, _decide_all(ds, svc)))
    for k, v in base.items():
        setattr(config, k, v)

    labels = {}
    if args.sample_check:
        _, rows = load_requests(config.DATASET_DIR / "sample_requests.csv", with_labels=True)
        labels = {r["request_id"]: r for r in rows}

    config.EVALUATION_DIR.mkdir(parents=True, exist_ok=True)
    out_path = config.EVALUATION_DIR / ("confidence_samples.jsonl" if args.sample_check else "confidence.jsonl")
    buckets = Counter()
    with open(out_path, "w", encoding="utf-8") as fh:
        for rid, row in shipped.items():
            agree = {k: sum(1 for _, res in variants if res[rid][k] == row[k]) / len(variants) for k in KEYS}
            statuses = Counter(res[rid]["affordability_status"] for _, res in variants)
            rec = {"request_id": rid, "shipped": {k: row[k] for k in KEYS}, "agreement": agree, "status_votes": dict(statuses)}
            if labels:
                rec["label_status"] = labels[rid]["affordability_status"]
                rec["label_matches_shipped"] = labels[rid]["affordability_status"] == row["affordability_status"]
            fh.write(json.dumps(rec) + "\n")
            level = "stable" if agree["affordability_status"] == 1.0 else ("threshold" if agree["affordability_status"] >= 0.5 else "unstable")
            buckets[level] += 1
    print(f"variants={len(variants)} rows={len(shipped)} {dict(buckets)} -> {out_path}")
    if labels:
        for level in ("stable", "threshold", "unstable"):
            rows_ = [json.loads(l) for l in open(out_path, encoding="utf-8")]
            sel = [r for r in rows_ if ("stable" if r["agreement"]["affordability_status"] == 1.0 else ("threshold" if r["agreement"]["affordability_status"] >= 0.5 else "unstable")) == level]
            if sel:
                print(f"  {level:9s}: {len(sel):2d} rows, label status agreement {sum(r['label_matches_shipped'] for r in sel)}/{len(sel)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
