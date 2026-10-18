"""The sole V8.4.3 agent adapter: Dockerized Claude Code."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from .base import AgentAdapter
from .claude_code import ClaudeCodeAdapter

ADAPTER_CLASSES: Mapping[str, type[AgentAdapter]] = {
    "claude_code": ClaudeCodeAdapter,
}


def make_adapter(
    name: str, *, root: Path, case_work_root: Path, mode: str | None = None
) -> AgentAdapter:
    cls = ADAPTER_CLASSES.get(name)
    if cls is None:
        raise ValueError(f"unknown adapter {name!r}; use one of {sorted(ADAPTER_CLASSES)}")
    return cls(root=root, case_work_root=case_work_root, mode=mode)


def check_adapters(root: Path, mode: str | None = None) -> dict[str, Any]:
    report: dict[str, Any] = {}
    for name in sorted(ADAPTER_CLASSES):
        probe_dir = root / "outputs" / ".adapter_capabilities" / name
        probe_dir.mkdir(parents=True, exist_ok=True)
        adapter = make_adapter(name, root=root, case_work_root=probe_dir, mode=mode)
        report[name] = adapter.capabilities()
    return report
