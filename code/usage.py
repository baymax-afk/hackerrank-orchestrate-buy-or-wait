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


def write_report(n_requests: int, run_start: str | None = None, usage_log: Path = config.USAGE_LOG, out: Path = config.USAGE_REPORT) -> Path:
    records = _load(usage_log)
    run_records = [r for r in records if run_start is None or r.get("ts", "") >= run_start]
    run = summarise(run_records)
    cumulative = summarise(records)
    n = max(n_requests, 1)
    lines = [
        "# Token usage and cost report",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        f"Dataset run: {n_requests} requests (dataset/requests.csv). Provider: {config.PROVIDER}. Prompt version: {config.PROMPT_VERSION}.",
        "",
        "Model calls are made only by the bounded evidence/explanation agents (image OCR, message extraction fallback, explanation drafting, optional audit). "
        "All forecasting, plan generation, ranking and validation are deterministic Python. Cache hits make no API call and consume no tokens.",
        "",
        "## Final full-dataset run (calls made during this run)",
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
        "## Cumulative calls that built the shipped evidence caches (all runs)",
        "",
        "| Model | Calls | Failed | Input tokens | Output tokens | Total tokens | Est. cost (USD) |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for m, d in sorted(cumulative["per_model"].items()):
        lines.append(f"| {m} | {d['calls']} | {d['failed']} | {d['input_tokens']} | {d['output_tokens']} | {d['input_tokens'] + d['output_tokens']} | {d['cost']:.4f} |")
    c = cumulative["total"]
    ct = c["input_tokens"] + c["output_tokens"]
    lines += [
        f"| **All models** | {c['calls']} | | {c['input_tokens']} | {c['output_tokens']} | {ct} | {c['cost']:.4f} |",
        "",
        f"- Cumulative tokens: {ct}; per request: {ct / n:.1f}; cumulative cost: USD {c['cost']:.4f} (USD {c['cost'] / n:.6f} per request)",
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
