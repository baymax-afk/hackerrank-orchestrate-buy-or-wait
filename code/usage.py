"""Aggregate evaluation/usage.jsonl into evaluation/usage_report.md.

Every model call made by any agent during a run is appended to usage.jsonl by
agents/client.py. This module summarises the calls that belong to the current
run (run_start timestamp) and, separately, the cumulative calls that built the
shipped caches, so the report reflects real usage for the final full-dataset run.
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import config


def _load(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def _cost(model: str, inp: int, out: int) -> Decimal:
    price = config.PRICE_TABLE.get(model)
    if price is None:
        # fall back to the family price if the returned id carries a suffix
        for k, v in config.PRICE_TABLE.items():
            if model.startswith(k):
                price = v
                break
    if price is None:
        return Decimal(0)
    return (Decimal(inp) * price[0] + Decimal(out) * price[1]) / Decimal(1_000_000)


def summarise(records: list[dict]) -> dict:
    per_model: dict[str, dict] = defaultdict(lambda: {"calls": 0, "failed": 0, "input_tokens": 0, "output_tokens": 0, "cost": Decimal(0)})
    per_agent: dict[str, int] = defaultdict(int)
    for r in records:
        m = r.get("model") or "unknown"
        d = per_model[m]
        d["calls"] += 1
        if not r.get("ok", True):
            d["failed"] += 1
        d["input_tokens"] += int(r.get("input_tokens", 0) or 0)
        d["output_tokens"] += int(r.get("output_tokens", 0) or 0)
        d["cost"] += _cost(m, int(r.get("input_tokens", 0) or 0), int(r.get("output_tokens", 0) or 0))
        per_agent[r.get("agent", "?")] += 1
    total = {"calls": sum(d["calls"] for d in per_model.values()), "input_tokens": sum(d["input_tokens"] for d in per_model.values()),
             "output_tokens": sum(d["output_tokens"] for d in per_model.values()), "cost": sum((d["cost"] for d in per_model.values()), Decimal(0))}
    return {"per_model": per_model, "per_agent": per_agent, "total": total}


def embodied_records(request_ids: list[str] | None, cache_dir: Path = config.CACHE_DIR) -> list[dict]:
    """Synthesise one usage record per cached model output that the shipped rows rely on.

    Cache hits make no API call, so the per-run table alone would hide the model work behind
    output.csv. Every cache record carries the usage of the call that produced it; this returns
    those as records (explanations only for the requests in the run, all evidence caches).
    """
    wanted = set(request_ids or [])
    out = []
    for kind, agent in (("images", "image_agent"), ("messages", "message_agent"), ("explanations", "explain_agent")):
        d = cache_dir / kind
        if not d.exists():
            continue
        for f in sorted(d.glob("*.json")):
            if kind == "explanations" and wanted and f.stem not in wanted:
                continue
            try:
                rec = json.loads(f.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            u = rec.get("usage") or {}
            out.append({"agent": agent, "source_id": rec.get("source_id", f.stem), "model": rec.get("model") or "unknown", "ok": True,
                        "input_tokens": int(u.get("input_tokens", 0) or 0), "output_tokens": int(u.get("output_tokens", 0) or 0)})
            enum = rec.get("enumeration") or {}
            if enum.get("usage"):
                eu = enum["usage"]
                out.append({"agent": "image_enumerate", "source_id": rec.get("source_id", f.stem), "model": enum.get("model") or rec.get("model") or "unknown", "ok": True,
                            "input_tokens": int(eu.get("input_tokens", 0) or 0), "output_tokens": int(eu.get("output_tokens", 0) or 0)})
    return out


def write_report(n_requests: int, run_start: str | None = None, usage_log: Path = config.USAGE_LOG, out: Path = config.USAGE_REPORT,
                 request_ids: list[str] | None = None, run_id: str | None = None) -> Path:
    records = _load(usage_log)
    if run_id:
        run_records = [r for r in records if r.get("run_id") == run_id]
    else:
        run_records = [r for r in records if run_start is None or r.get("ts", "") >= run_start]
    run = summarise(run_records)
    n = max(n_requests, 1)
    lines = [
        "# Token usage and cost report",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}" + (f" (run id {run_id})" if run_id else ""),
        f"Dataset run: {n_requests} requests (dataset/requests.csv). Provider: {config.PROVIDER}. Prompt version: {config.PROMPT_VERSION}.",
        "",
        "Model calls are made only by the bounded evidence/explanation agents (image OCR, message extraction fallback, explanation drafting). "
        "All forecasting, plan generation, ranking and validation are deterministic Python. Cache hits make no API call and consume no tokens.",
        "",
        "## Final full-dataset run: model work behind the shipped output.csv",
        "",
        "Each shipped row is built from cached, provenance-tracked model outputs (image OCR, message extraction, explanation draft). "
        "A cached output is reused without a new API call, so this table sums the usage recorded when each output used by this run was originally produced. "
        "This is the per-request model cost of reproducing output.csv from scratch.",
        "",
        "| Model | Calls | Input tokens | Output tokens | Total tokens | Est. cost (USD) |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    emb = summarise(embodied_records(request_ids))
    for m, d in sorted(emb["per_model"].items()):
        lines.append(f"| {m} | {d['calls']} | {d['input_tokens']} | {d['output_tokens']} | {d['input_tokens'] + d['output_tokens']} | {d['cost']:.4f} |")
    e = emb["total"]
    et = e["input_tokens"] + e["output_tokens"]
    lines += [
        f"| **All models** | {e['calls']} | {e['input_tokens']} | {e['output_tokens']} | {et} | {e['cost']:.4f} |",
        "",
        f"- Total tokens: {et}; average per request: {et / n:.1f}",
        f"- Estimated total cost: USD {e['cost']:.4f}; average per request: USD {e['cost'] / n:.6f}",
        f"- Calls per agent: {dict(sorted(emb['per_agent'].items()))}",
        "",
        "## API calls made during this run (cache misses only)",
        "",
        "| Model | Calls | Failed | Input tokens | Output tokens | Total tokens | Est. cost (USD) |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for m, d in sorted(run["per_model"].items()):
        lines.append(f"| {m} | {d['calls']} | {d['failed']} | {d['input_tokens']} | {d['output_tokens']} | {d['input_tokens'] + d['output_tokens']} | {d['cost']:.4f} |")
    t = run["total"]
    tt = t["input_tokens"] + t["output_tokens"]
    lines += [
        f"| **All models** | {t['calls']} | | {t['input_tokens']} | {t['output_tokens']} | {tt} | {t['cost']:.4f} |",
        "",
        f"- Total tokens: {tt}; average per request: {tt / n:.1f}",
        f"- Estimated total cost: USD {t['cost']:.4f}; average per request: USD {t['cost'] / n:.6f}",
        f"- Calls per agent: {dict(sorted(run['per_agent'].items()))}",
        "",
        "## Pricing assumptions (USD per million tokens)",
        "",
    ]
    for m, (i, o) in config.PRICE_TABLE.items():
        lines.append(f"- {m}: input {i}, output {o}")
    lines += ["", "No API keys or credentials are included in this package."]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out
