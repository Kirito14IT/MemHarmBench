"""JSONL event recorder for V7 campaigns."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


class EventRecorder:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, event_type: str, **data: Any) -> None:
        payload = {
            "ts": time.time(),
            "event": event_type,
            **data,
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
