"""Shared progress, liveness, and campaign-lock helpers for public runners."""

from __future__ import annotations

import json
import os
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _io_path(path: Path) -> Path:
    """Use Windows extended paths only for host filesystem operations."""
    absolute = str(path.resolve())
    if os.name != "nt" or absolute.startswith("\\\\?\\"):
        return Path(absolute)
    if absolute.startswith("\\\\"):
        return Path("\\\\?\\UNC\\" + absolute[2:])
    return Path("\\\\?\\" + absolute)


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path = _io_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def technical_failure(result: Mapping[str, Any]) -> bool:
    exposure = result.get("exposure", {})
    return bool(
        exposure.get("trigger_status") == "technical_failure"
        or any(
            attempt.get("technical_failure")
            for attempt in exposure.get("attempts", [])
        )
        or (result.get("trigger") or {}).get("technical_failure")
        or result.get("runner_technical_failure")
        or result.get("cleanup_error")
        or result.get("runner_cleanup_errors")
    )


class _Heartbeat:
    def __init__(self, path: Path, interval: float):
        self.path = path
        self.interval = interval
        self.state: dict[str, Any] = {}
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._loop, daemon=True)

    def update(self, **fields: Any) -> None:
        with self.lock:
            self.state.update(fields)
            self.state["heartbeat_at"] = _now()
            _write_json(self.path, self.state)

    def _loop(self) -> None:
        while not self.stop_event.wait(self.interval):
            self.update()

    def __enter__(self) -> "_Heartbeat":
        self.thread.start()
        return self

    def __exit__(self, *args: Any) -> None:
        self.stop_event.set()
        self.thread.join(timeout=5)


def _progress(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize provisional agent-stage results without claiming metrics."""
    good = [result for result in results if not technical_failure(result)]
    return {
        "terminal_cases": len(results),
        "completed_cases": len(good),
        "technical_failures": len(results) - len(good),
        "provisional_pmir_positive": sum(
            result["exposure"]["final_pmir"] == 1 for result in good
        ),
        "provisional_pmir_zero": sum(
            result["exposure"]["final_pmir"] == 0 for result in good
        ),
        "trigger_runs": sum(result.get("trigger") is not None for result in results),
        "judge_source": "deferred",
        "final_metrics_available": False,
    }


@contextmanager
def _campaign_lock(root: Path, campaign_id: str):
    """Prevent two resume invocations from spending on one campaign."""
    outputs = root / "outputs"
    outputs.mkdir(parents=True, exist_ok=True)
    with (outputs / f".{campaign_id}.lock").open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if not handle.tell():
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise ValueError("This campaign already has a running supervisor") from None
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


__all__ = ["_Heartbeat", "_campaign_lock", "_progress", "technical_failure"]
