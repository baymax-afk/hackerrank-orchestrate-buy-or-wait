"""Thin Anthropic Messages API wrapper: structured JSON output, usage logging, no-key safety.

Every call is appended to evaluation/usage.jsonl so the usage report reflects
exactly what the final run consumed. The wrapper never raises into the pipeline;
a failed call returns None and the deterministic fallback takes over.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from decimal import Decimal
from datetime import datetime, timezone
from typing import Any, Optional

import config

log = logging.getLogger("bow.llm")


HARD_FAILURE_MARKERS = ("credit balance", "authentication", "invalid x-api-key", "permission")


class LLMClient:
    def __init__(self, enabled: bool = True) -> None:
        self._client = None
        self.key = config.api_key() if enabled else None
        self.spent_usd = Decimal(0)
        if self.key:
            try:
                import anthropic

                self._client = anthropic.Anthropic(api_key=self.key, max_retries=2, timeout=180.0)
                self._anthropic = anthropic
            except Exception as exc:  # pragma: no cover
                log.warning("anthropic SDK unavailable: %s", exc)
                self._client = None
        self.calls = 0
        self.consecutive_failures = 0
        self.tripped = False
        self._lock = threading.Lock()

    def available(self) -> bool:
        if self._client is None or self.tripped:
            return False
        if config.MAX_USD_PER_RUN is not None and self.spent_usd >= config.MAX_USD_PER_RUN:
            if not self.tripped:
                log.error("model budget of USD %s exhausted (spent %.4f); continuing deterministically", config.MAX_USD_PER_RUN, self.spent_usd)
                self.tripped = True
            return False
        return True

    def _log_usage(self, record: dict[str, Any]) -> None:
        record = {"run_id": config.RUN_ID, **record}
        with self._lock:
            config.EVALUATION_DIR.mkdir(parents=True, exist_ok=True)
            with open(config.USAGE_LOG, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _failure(self, agent: str, source_id: str, why: str, hard: bool = False) -> None:
        with self._lock:
            self.consecutive_failures += 1
            if hard and not self.tripped:
                self.tripped = True
                log.error("unrecoverable API error (%s) on %s %s; circuit open, continuing deterministically", why, agent, source_id)
                return
            if self.consecutive_failures >= config.LLM_CIRCUIT_BREAKER and not self.tripped:
                self.tripped = True
                log.error("%d consecutive API failures (last: %s %s %s); circuit open, continuing deterministically",
                          self.consecutive_failures, agent, source_id, why)

    def _success(self, model: str = "", inp: int = 0, out: int = 0) -> None:
        with self._lock:
            self.consecutive_failures = 0
            self.calls += 1
            price = config.PRICE_TABLE.get(model) or next((v for k, v in config.PRICE_TABLE.items() if model.startswith(k)), None)
            if price:
                self.spent_usd += (Decimal(inp) * price[0] + Decimal(out) * price[1]) / Decimal(1_000_000)

    def json_call(self, *, agent: str, source_id: str, system: str, content: list[dict], schema: dict,
                  model: Optional[str] = None, max_tokens: int = 2048, effort: str = "low") -> Optional[dict]:
        """Return the parsed JSON object (schema-constrained) or None on any failure."""
        if not self.available():
            return None
        model = model or config.MODEL_TEXT
        t0 = time.time()
        try:
            resp = self._client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": content}],
                output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
            )
        except Exception as exc:
            log.warning("%s %s: API call failed: %s", agent, source_id, type(exc).__name__)
            self._log_usage({"ts": datetime.now(timezone.utc).isoformat(), "agent": agent, "source_id": source_id, "provider": config.PROVIDER,
                             "model": model, "ok": False, "error": type(exc).__name__, "input_tokens": 0, "output_tokens": 0})
            text = str(exc).lower()
            self._failure(agent, source_id, type(exc).__name__, hard=any(m in text for m in HARD_FAILURE_MARKERS))
            return None
        usage = getattr(resp, "usage", None)
        rec = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "agent": agent,
            "source_id": source_id,
            "provider": config.PROVIDER,
            "model": getattr(resp, "model", model),
            "ok": True,
            "input_tokens": getattr(usage, "input_tokens", 0) or 0,
            "output_tokens": getattr(usage, "output_tokens", 0) or 0,
            "cache_read_input_tokens": getattr(usage, "cache_read_input_tokens", 0) or 0,
            "latency_s": round(time.time() - t0, 2),
            "stop_reason": getattr(resp, "stop_reason", None),
        }
        self._log_usage(rec)
        self._success(str(rec["model"]), int(rec["input_tokens"]), int(rec["output_tokens"]))
        if getattr(resp, "stop_reason", None) == "refusal":
            return None
        try:
            text = next(b.text for b in resp.content if getattr(b, "type", "") == "text")
            data = json.loads(text)
            if not isinstance(data, dict):
                raise ValueError("top-level JSON is not an object")
        except Exception as exc:
            log.warning("%s %s: unparseable model output: %s", agent, source_id, exc)
            return None
        data["_model"] = rec["model"]
        data["_usage"] = {"input_tokens": rec["input_tokens"], "output_tokens": rec["output_tokens"]}
        return data
