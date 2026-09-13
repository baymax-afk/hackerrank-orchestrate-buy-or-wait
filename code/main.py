"""Buy or Wait? — entry point.

    python code/main.py                 # full run -> ./output.csv (uses cached evidence; calls the API only for cache misses when a key is set)
    python code/main.py --sample-check  # score the pipeline on dataset/sample_requests.csv
    python code/main.py --no-llm        # never call the API (shipped cache + rule-based messages)
    python code/main.py --explain request_42

Robustness contract of a run:
* every request is decided inside its own fault boundary; an exception yields a contract-valid
  fallback row (not_affordable) and a logged error instead of an aborted run;
* every row is validated before it is written; a row with contract errors is replaced by the
  fallback row (the original is kept in the trace for diagnosis);
* the process exits non-zero when any row fell back, so a silent degradation is impossible.
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import sys
import time
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config  # noqa: E402
from decide import Services, decide_request  # noqa: E402
from loaders import load_dataset, load_requests  # noqa: E402
from models import Decision, Request, Trace  # noqa: E402
from scoring import score  # noqa: E402
from validate import validate_rows  # noqa: E402

log = logging.getLogger("bow")


def build_services(args) -> Services:
    """Agents are always constructed; --no-llm (or a missing key) makes the client unavailable so every
    agent serves cache hits only and never calls the API. Cached explanation drafts therefore survive an
    offline run instead of degrading to the template."""
    svc = Services(use_llm=not args.no_llm, refresh_cache=args.refresh_cache)
    try:
        from agents.client import LLMClient
        from agents.explain_agent import ExplainAgent
        from agents.image_agent import ImageAgent
        from agents.message_agent import MessageAgent

        client = LLMClient(enabled=svc.use_llm)
        svc.image_agent = ImageAgent(client)
        svc.message_agent = MessageAgent(client)
        svc.explain_agent = ExplainAgent(client)
        if client.available():
            log.info("LLM agents enabled (%s)", config.MODEL_TEXT)
        else:
            svc.use_llm = False
            log.info("model calls disabled (%s): shipped caches and rule-based messages only",
                     "--no-llm" if args.no_llm else "no ANTHROPIC_API_KEY")
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("LLM agents unavailable (%s); deterministic fallback", exc)
        svc.use_llm = False
    return svc


def fallback_decision(request: Request, reason: str) -> Decision:
    """Contract-valid row used when a request cannot be decided: nothing is recommended."""
    return Decision(request.request_id, Decimal(0), "not_affordable", "not_recommended", "none", None, "none",
                    f"No recommendation could be verified for this request ({reason}); do not proceed until the "
                    f"forecast can be completed.")


def write_output(rows: list[dict], path: Path) -> None:
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=config.OUTPUT_COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    os.replace(tmp, path)


def _decide_one(req: Request, ds, svc: Services) -> tuple[Decision, Trace, str | None]:
    """Fault boundary for one request: returns (decision, trace, error)."""
    try:
        decision, trace = decide_request(req, ds, svc)
        return decision, trace, None
    except Exception as exc:
        log.error("%s: decision failed (%s: %s); fallback row", req.request_id, type(exc).__name__, exc)
        trace = Trace(req.request_id)
        trace.notes.append(f"decision failed: {type(exc).__name__}: {exc}")
        return fallback_decision(req, f"{type(exc).__name__}"), trace, f"{type(exc).__name__}: {exc}"


def _draft_explanations(decisions: list[Decision], requests: list[Request], ds, svc: Services) -> None:
    """Model-drafted explanations, in parallel (cache-backed, order-independent); failures keep the template."""
    if svc.explain_agent is None:
        return

    def one(i: int) -> None:
        try:
            decisions[i].decision_explanation = svc.explain_agent.draft(
                requests[i], ds.profiles[requests[i].user_id], decisions[i], svc.explanation_cache, svc.refresh_cache)
        except Exception as exc:  # the template explanation stands
            log.warning("%s: explanation draft failed (%s); template kept", requests[i].request_id, type(exc).__name__)

    with ThreadPoolExecutor(max_workers=config.EXPLAIN_WORKERS) as pool:
        list(pool.map(one, range(len(decisions))))


def run(args) -> int:
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
    config.RUN_ID = run_id
    log.info("run %s", run_id)
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
    trace_dir = Path(args.trace_dir) if args.trace_dir else (None if args.no_traces else config.TRACE_DIR)
    if trace_dir:
        trace_dir.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    run_start = datetime.now(timezone.utc).isoformat()
    decisions: list[Decision] = []
    traces: list[Trace] = []
    failures: dict[str, str] = {}
    for i, req in enumerate(requests, 1):
        decision, trace, err = _decide_one(req, ds, svc)
        if err:
            failures[req.request_id] = err
        decisions.append(decision)
        traces.append(trace)
        if i % 50 == 0:
            log.info("%d/%d requests decided", i, len(requests))
    log.info("decided %d requests in %.1fs", len(decisions), time.time() - t0)

    _draft_explanations(decisions, requests, ds, svc)

    rows = [d.to_row() for d in decisions]
    # validation gate: a row with contract errors is replaced by the fallback row
    violations = validate_rows(rows, ds, requests)
    for v in violations[:50]:
        log.log(logging.ERROR if v.severity == "error" else logging.WARNING, "%s", v)
    bad = {v.request_id for v in violations if v.severity == "error" and v.request_id != "*"}
    for i, req in enumerate(requests):
        if req.request_id in bad:
            traces[i].notes.append(f"row replaced by fallback after validation errors: {[str(v) for v in violations if v.request_id == req.request_id]}")
            traces[i].notes.append(f"original row: {rows[i]}")
            decisions[i] = fallback_decision(req, "contract validation failed")
            rows[i] = decisions[i].to_row()
            failures[req.request_id] = "validation"
    if bad:
        remaining = [v for v in validate_rows(rows, ds, requests) if v.severity == "error"]
        if remaining:
            log.error("%d validation errors remain after fallback substitution", len(remaining))

    if trace_dir:
        for req, trace in zip(requests, traces):
            (trace_dir / f"{req.request_id}.json").write_text(json.dumps(trace.__dict__ | {"run_id": run_id}, indent=1, default=str), encoding="utf-8")
    if args.explain:
        for req, trace, decision in zip(requests, traces, decisions):
            if req.request_id in args.explain:
                print_trace(trace, decision)

    if args.sample_check:
        _, labeled = load_requests(Path(args.dataset) / "sample_requests.csv", with_labels=True)
        rep = score(rows, labeled)
        print(rep.summary())
        for d in rep.diffs:
            print("  ", d)
        return 1 if failures else 0

    out = Path(args.output)
    write_output(rows, out)
    stats = Counter((r["affordability_status"], r["recommended_payment_method"]) for r in rows)
    log.info("wrote %s (%d rows): %s", out, len(rows), dict(stats))
    if failures:
        log.error("%d request(s) received the fallback row: %s", len(failures), sorted(failures)[:10])
    if svc.use_llm or config.USAGE_LOG.exists():
        try:
            from usage import write_report

            write_report(len(rows), run_start, request_ids=[r.request_id for r in requests], run_id=run_id)
        except Exception as exc:  # pragma: no cover
            log.warning("usage report not written: %s", exc)
    return 1 if failures else 0


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
    p.add_argument("--trace-dir", default="", help=f"where per-request traces are written (default {config.TRACE_DIR})")
    p.add_argument("--no-traces", action="store_true", help="do not write per-request trace files")
    p.add_argument("--estimator", default="", help="override config.ESTIMATOR (mean|median|last|max|min|mean_recent)")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s %(message)s", stream=sys.stderr)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
