"""Buy or Wait? — entry point.

    python code/main.py                 # full run -> ./output.csv (uses cached evidence; calls the API only for cache misses when a key is set)
    python code/main.py --sample-check  # score the pipeline on dataset/sample_requests.csv
    python code/main.py --no-llm        # never call the API (cache + reviewed image table + rule-based messages)
    python code/main.py --explain request_42
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config  # noqa: E402
from decide import Services, decide_request  # noqa: E402
from loaders import load_dataset, load_requests  # noqa: E402
from scoring import score  # noqa: E402
from validate import validate_rows  # noqa: E402

log = logging.getLogger("bow")


def build_services(args) -> Services:
    svc = Services(use_llm=not args.no_llm, refresh_cache=args.refresh_cache)
    svc.audit_agent = None
    if svc.use_llm:
        try:
            from agents.client import LLMClient
            from agents.explain_agent import ExplainAgent
            from agents.image_agent import ImageAgent
            from agents.message_agent import MessageAgent

            client = LLMClient()
            if client.available():
                svc.image_agent = ImageAgent(client)
                svc.message_agent = MessageAgent(client)
                svc.explain_agent = ExplainAgent(client)
                if getattr(args, "audit", False):
                    from agents.audit_agent import AuditAgent

                    svc.audit_agent = AuditAgent(client)
                log.info("LLM agents enabled (%s)", config.MODEL_TEXT)
            else:
                log.warning("no ANTHROPIC_API_KEY: running with caches, reviewed image table and rule-based messages only")
                svc.use_llm = False
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("LLM agents unavailable (%s); deterministic fallback", exc)
            svc.use_llm = False
    return svc


def write_output(rows: list[dict], path: Path) -> None:
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=config.OUTPUT_COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    os.replace(tmp, path)


def run(args) -> int:
    config.load_dotenv()
    ds = load_dataset(Path(args.dataset), "sample_requests.csv" if args.sample_check else "requests.csv")
    requests = ds.requests
    if args.request_id:
        requests = [r for r in requests if r.request_id in set(args.request_id)]
    if args.limit:
        requests = requests[: args.limit]
    svc = build_services(args)
    if args.estimator:
        config.ESTIMATOR = args.estimator
    trace_dir = Path(args.trace_dir) if args.trace_dir else None
    if trace_dir:
        trace_dir.mkdir(parents=True, exist_ok=True)

    rows, decisions = [], []
    t0 = time.time()
    from datetime import datetime, timezone

    run_start = datetime.now(timezone.utc).isoformat()
    for i, req in enumerate(requests, 1):
        decision, trace = decide_request(req, ds, svc)
        if svc.explain_agent is not None:
            decision.decision_explanation = svc.explain_agent.draft(req, ds.profiles[req.user_id], decision, svc.explanation_cache, svc.refresh_cache)
        rows.append(decision.to_row())
        decisions.append(decision)
        if getattr(svc, "audit_agent", None) is not None:
            svc.audit_agent.review(decision, trace)
        if trace_dir:
            (trace_dir / f"{req.request_id}.json").write_text(json.dumps(trace.__dict__, indent=1, default=str), encoding="utf-8")
        if args.explain and req.request_id in args.explain:
            print_trace(trace, decision)
        if i % 50 == 0:
            log.info("%d/%d requests decided", i, len(requests))
    log.info("decided %d requests in %.1fs", len(rows), time.time() - t0)

    violations = validate_rows(rows, ds, requests)
    errors = [v for v in violations if v.severity == "error"]
    for v in violations[:50]:
        log.log(logging.ERROR if v.severity == "error" else logging.WARNING, "%s", v)
    if errors:
        log.error("%d validation errors", len(errors))

    if args.sample_check:
        _, labeled = load_requests(Path(args.dataset) / "sample_requests.csv", with_labels=True)
        rep = score(rows, labeled)
        print(rep.summary())
        for d in rep.diffs:
            print("  ", d)
        return 1 if errors else 0

    out = Path(args.output)
    write_output(rows, out)
    stats = Counter((r["affordability_status"], r["recommended_payment_method"]) for r in rows)
    log.info("wrote %s (%d rows): %s", out, len(rows), dict(stats))
    if svc.use_llm or (config.USAGE_LOG.exists()):
        try:
            from usage import write_report

            write_report(len(rows), run_start)
        except Exception as exc:  # pragma: no cover
            log.warning("usage report not written: %s", exc)
    return 1 if errors else 0


def print_trace(trace, decision) -> None:
    print(f"=== {trace.request_id} -> {decision.affordability_status}/{decision.recommended_payment_method} safe={decision.amount_safe_to_pay} earliest={decision.earliest_date_for_full_payment}")
    for n in trace.notes:
        print("  note:", n)
    for s in trace.series:
        print(f"  series {s['series_id']:12s} {s['category']:18s} {s['cadence']:9s} anchor={s['anchor']} amount={s['amount']} n={s['occurrences']} flex={s['flexibility']}")
    for f in trace.flows:
        print(f"  flow {f['date']} {f['amount']:>16s} {f['kind']:16s} {f['source']:12s} {f['description'][:40]}")
    for d, b in trace.timeline:
        print(f"  bal  {d} {b}")
    for c in trace.candidates:
        print("  cand", c)
    print("  plan:", decision.payment_plan, "| changes:", decision.spending_changes_needed)
    print("  ", decision.decision_explanation)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Buy or Wait? financial decision agent")
    p.add_argument("--dataset", default=str(config.DATASET_DIR))
    p.add_argument("--output", default=str(config.OUTPUT_PATH))
    p.add_argument("--sample-check", action="store_true", help="run on sample_requests.csv and score against its labels")
    p.add_argument("--no-llm", action="store_true", help="never call the model API")
    p.add_argument("--refresh-cache", action="store_true", help="ignore cached model outputs")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--request-id", nargs="*", help="only these request ids")
    p.add_argument("--explain", nargs="*", help="print the ledger/trace for these request ids")
    p.add_argument("--trace-dir", default="")
    p.add_argument("--audit", action="store_true", help="also run the read-only audit agent on every request (writes evaluation/audit_findings.jsonl)")
    p.add_argument("--estimator", default="", help="override config.ESTIMATOR (mean|median|last|max|min|mean_recent)")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s %(message)s", stream=sys.stderr)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
