"""Thin agent-adapter contract for the frozen V8.4.3 agent runtime.

All episode logic (exposure/trigger state machine, PMIR retry, trigger skip,
judging, metrics) lives outside this module. Adapters only know how to talk
to one concrete agent product:

    start_session / run_turn / snapshot_memory / wait_memory_settled /
    snapshot_workspace / collect_trace / cleanup / capabilities
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping


class AdapterError(RuntimeError):
    """Base error raised by adapters."""


class TechnicalFailure(AdapterError):
    """Infrastructure failure (timeout, container, transport).

    Retrying this does NOT consume the behavioral retry budget.
    """


class AgentUnavailable(AdapterError):
    """The agent executable/config is not installed or not configured."""


@dataclass
class SessionHandle:
    adapter: str
    session_id: str
    phase: str
    attempt_id: str
    lineage: Mapping[str, str] = field(default_factory=dict)
    workspace: Path | None = None
    state: Mapping[str, Any] = field(default_factory=dict)


@dataclass
class TurnResult:
    answer: str
    tool_calls: list[Mapping[str, Any]] = field(default_factory=list)
    tool_results: list[Mapping[str, Any]] = field(default_factory=list)
    terminated_ok: bool = True
    error: str | None = None
    raw: Any = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "answer": self.answer,
            "tool_calls": list(self.tool_calls),
            "tool_results": list(self.tool_results),
            "terminated_ok": self.terminated_ok,
            "error": self.error,
        }


@dataclass
class FileSet:
    """A snapshot of memory or workspace files."""

    files: Mapping[str, str]
    note: str = ""

    def digest(self) -> str:
        import hashlib
        import json

        payload = json.dumps(self.files, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass
class TraceData:
    entries: list[Mapping[str, Any]]


class AgentAdapter(ABC):
    """Thin adapter contract. Implementations contain only agent-specific code."""

    name: str

    @abstractmethod
    def start_session(
        self,
        case: Any,
        phase: str,
        attempt_id: str,
        lineage: Mapping[str, str] | None = None,
        inputs: Mapping[str, Any] | None = None,
    ) -> SessionHandle:
        """Create an isolated session for one exposure/trigger attempt.

        `inputs` carries the prepared user message and workspace files for the
        phase (message, workspace_files mapping), produced by the shared
        controller so adapters never generate prompts themselves.
        """

    @abstractmethod
    def run_turn(self, session: SessionHandle, message: str) -> TurnResult:
        """Run one agent turn. Raise TechnicalFailure for infra errors."""

    @abstractmethod
    def snapshot_memory(self, session: SessionHandle) -> FileSet:
        """Capture the agent's native durable memory."""

    @abstractmethod
    def wait_memory_settled(self, session: SessionHandle, timeout_ms: int = 5000) -> None:
        """Block until the agent's memory writes are settled."""

    @abstractmethod
    def snapshot_workspace(self, session: SessionHandle) -> FileSet:
        """Capture the final workspace files for the session."""

    @abstractmethod
    def collect_trace(self, session: SessionHandle) -> TraceData:
        """Collect the turn trajectory (assistant messages + tool events)."""

    @abstractmethod
    def cleanup(self, session: SessionHandle) -> None:
        """Remove temporary session resources after evidence is collected."""

    @abstractmethod
    def capabilities(self) -> Mapping[str, Any]:
        """Report installed/configured capability status (never secrets)."""
