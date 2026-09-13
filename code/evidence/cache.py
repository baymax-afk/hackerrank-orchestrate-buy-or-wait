"""JSON evidence cache with provenance. One file per source id."""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import config


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class Cache:
    def __init__(self, kind: str, root: Path = config.CACHE_DIR):
        self.dir = root / kind
        self.dir.mkdir(parents=True, exist_ok=True)

    def path(self, source_id: str) -> Path:
        return self.dir / f"{source_id}.json"

    def get(self, source_id: str, content_hash: str, prompt_version: str = config.PROMPT_VERSION) -> Optional[dict]:
        p = self.path(source_id)
        if not p.exists():
            return None
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
        if data.get("content_sha256") != content_hash or data.get("prompt_version") != prompt_version:
            return None
        return data

    def put(self, source_id: str, record: dict[str, Any]) -> None:
        record = dict(record)
        record.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
        tmp = self.path(source_id).with_suffix(".tmp")
        tmp.write_text(json.dumps(record, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        os.replace(tmp, self.path(source_id))
