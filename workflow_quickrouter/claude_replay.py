"""Isolated Claude replay steps for formal judging; the original run is read-only.

Every exposure/trigger step owns a new directory. A completed, hash-verified
step can be reused; a partial or failed directory is never silently replayed.
The coordinator alone chooses exposure attempts and performs formal judging.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Callable, Mapping

sys.dont_write_bytecode = True
DEFAULT_ROOT = Path(__file__).resolve().parent.parent
if str(DEFAULT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(DEFAULT_ROOT / "src"))

from memharmbench_v84.adapters.base import AgentAdapter, TechnicalFailure, SessionHandle, TurnResult, FileSet  # noqa: E402
from memharmbench_v84.adapters.claude_code import (  # noqa: E402
    ADAPTER_REVISION, CLAUDE_VERSION, ClaudeCodeAdapter, _io_path,
)
from memharmbench_v84.adapters.util import load_agent_config  # noqa: E402
from memharmbench_v84.identity import dataset_identity  # noqa: E402
from memharmbench_v84.judges import DeferredJudge  # noqa: E402
from v84_profile import core_fingerprint as code_fingerprint  # noqa: E402
from memharmbench_v84.runtime.controller import (  # noqa: E402
    DEFAULT_RETRY_FOLLOWUP, EpisodeController, RuntimeConfig, _phase_inputs,
    case_dataset_identity, require_case_dataset_identity,
)

from v84_profile import (IMAGE_ID, MODEL, AGENT_BASE_URL, FROZEN_CORE_HASH,
                     validate_environment, profile_identity, code_files_identity, agent_model_evidence,
                     AGENT_MODEL_IDENTITY_POLICY as MODEL_IDENTITY_POLICY,
                     PROTECTED_CAMPAIGNS, ALLOWED_CAMPAIGNS, ROOT)

FROZEN_RUNTIME_HASH = FROZEN_CORE_HASH
REPLAY_REVISION = "claude-gemini-quickrouter-v843-full288-isolated-steps-r1"


class ReplayIntegrityError(TechnicalFailure):
    """An unsafe restoration or inconsistent/partial cache must be reviewed."""


class AgentProviderFailure(TechnicalFailure):
    """A provider refusal or model-route mismatch must stop dispatch immediately."""

    def __init__(self, category: str, status_code: int | None = None):
        self.category = category
        self.status_code = status_code
        super().__init__(f"Claude provider failure: {category}; status_code={status_code}")


def _provider_failure(error: BaseException, agent_root: Path) -> AgentProviderFailure | None:
    status = _failure_status(error, agent_root)
    if isinstance(error, AgentProviderFailure):
        return error
    # Inspect only known provider code/message fields locally; never record raw text.
    codes = str(error).lower()
    for capture in sorted(_io_path(agent_root).glob("*/capture_*/stdout.txt"))[-3:]:
        try:
            payload = json.loads(capture.read_text(encoding="utf-8"))
            codes += " " + json.dumps({key: payload.get(key) for key in ("error", "errors", "result", "subtype")}).lower()
        except (OSError, ValueError, AttributeError):
            pass
    if status == 402 or any(word in codes for word in ("account_quota_insufficient", "insufficient_quota", "insufficient balance", "余额不足")):
        return AgentProviderFailure("quota", status)
    if status in (401, 403) or any(word in codes for word in ("error_authentication", "invalid_api_key", "unauthorized", "authentication_error")):
        return AgentProviderFailure("authentication", status)
    if any(word in codes for word in ("model_not_found", "unsupported_model", "model not found", "model is not supported")):
        return AgentProviderFailure("model_route", status)
    return None


def _session_dict(session: SessionHandle) -> dict[str, Any]:
    value = asdict(session)
    value["workspace"] = str(session.workspace) if session.workspace else None
    return value


def _record_native_model_usage(turn: TurnResult, session: SessionHandle) -> None:
    """Retain the actual envelope metadata; absent fields are never invented."""
    try:
        native = json.loads(turn.raw["stdout"])
        if not isinstance(native, dict):
            raise ValueError
        usage = native.get("modelUsage", {})
        agent_model_evidence(usage)
    except (ValueError, TypeError, KeyError, AttributeError):
        raise AgentProviderFailure("model_route") from None
    session.state["native_model_usage"] = usage
    session.state["native_model_usage_field_present"] = "modelUsage" in native


class _CheckpointController(EpisodeController):
    """Persist a successful turn before snapshots; never replay accepted work."""

    def _run_turn_with_technical_retries(self, adapter, session, message, case_id, attempt_id, phase):
        step = self.config.campaign_dir
        budget_path = step / "call_budget.json"
        budget = _read_json(budget_path) if _io_path(budget_path).exists() else {"used": 0, "maximum": self.config.max_technical_retries + 1}
        last = None
        while budget["used"] < budget["maximum"]:
            budget["used"] += 1
            _write_json(budget_path, {**budget, "last_call_state": "started"})
            try:
                turn = adapter.run_turn(session, message)
                if not turn.terminated_ok:
                    raise TechnicalFailure(turn.error or "Claude turn terminated abnormally")
                _record_native_model_usage(turn, session)
                value = {"turn": turn.as_dict(), "session": _session_dict(session), "physical_calls": budget["used"],
                    "prepared_sha256": _read_json(step / "prepared.json")["sha256"],
                    "workspace_after_fingerprint": _hash(dict(adapter.snapshot_workspace(session).files)),
                    "memory_after_fingerprint": _hash(dict(adapter.snapshot_memory(session).files))}
                _write_json(step / "accepted_turn.json", {"value": value, "sha256": _hash(value)})
                _write_json(budget_path, {**budget, "last_call_state": "accepted"})
                return turn, budget["used"] - 1
            except TechnicalFailure as error:
                last = error
                # Authentication, model-route and quota refusals are terminal for
                # this launch. In particular, an exhausted account must not burn
                # the two remaining technical attempts before the user recharges.
                if isinstance(error, AgentProviderFailure):
                    raise
                fatal = _provider_failure(error, adapter.case_work_root)
                _write_json(budget_path, {**budget, "last_call_state": "rejected", "category": fatal.category if fatal else "technical"})
                if fatal:
                    raise fatal from None
                if isinstance(error, ReplayIntegrityError):
                    raise
                prepared = _read_json(step / "prepared.json").get("value", {})
                unchanged = (
                    dict(adapter.snapshot_workspace(session).files) == prepared.get("workspace_before")
                    and dict(adapter.snapshot_memory(session).files) == prepared.get("memory_before")
                )
                has_native_history = any(_io_path(adapter.case_work_root / "claude_config").rglob("*.jsonl"))
                rejected_status = _failure_status(error, adapter.case_work_root)
                if not unchanged or has_native_history or rejected_status not in (408, 429, 500, 502, 503, 504):
                    raise ReplayIntegrityError("Uncertain technical execution requires evidence reconciliation; no same-session replay") from None
                if budget["used"] < budget["maximum"]:
                    time.sleep(2 ** (budget["used"] - 1))
        raise TechnicalFailure("Claude technical retry budget exhausted") from None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _encoded(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _hash(value: Any) -> str:
    return hashlib.sha256(_encoded(value)).hexdigest()


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    from v84_profile import write_json as atomic_write_json
    atomic_write_json(path, value)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(_io_path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ReplayIntegrityError(f"Missing or invalid replay record: {path.name}") from None
    if not isinstance(value, dict):
        raise ReplayIntegrityError(f"Replay record is not an object: {path.name}")
    return value


def _safe_relative(value: str) -> Path:
    if not isinstance(value, str) or not value or "\\" in value or ":" in value or "\x00" in value:
        raise ReplayIntegrityError("Unsafe snapshot relative path")
    posix = PurePosixPath(value)
    if posix.is_absolute() or PureWindowsPath(value).drive or any(part in {"", ".", ".."} for part in value.split("/")):
        raise ReplayIntegrityError("Absolute, traversal or drive-qualified snapshot path")
    return Path(*posix.parts)


def _safe_text(value: Any) -> str:
    if not isinstance(value, str) or len(value) >= 200_000 or "...[truncated" in value or "file cap reached]" in value:
        raise ReplayIntegrityError("Snapshot text is missing, malformed or possibly truncated")
    return value


def _plain_windows_path(value: str) -> str:
    if value.startswith("\\\\?\\UNC\\"):
        return "\\\\" + value[8:]
    return value[4:] if value.startswith("\\\\?\\") else value


def _failure_status(error: BaseException, agent_root: Path) -> int | None:
    """Extract only an HTTP status, never copy exception/provider text."""
    value = getattr(error, "status_code", None)
    if re.fullmatch(r"[1-5][0-9]{2}", str(value)):
        return int(value)
    match = re.search(r"(?:api_error_status|status_code)\s*[=:]\s*([1-5][0-9]{2})\b", str(error))
    if match:
        return int(match.group(1))
    # The frozen adapter writes a safe numeric status into capture metadata.
    paths = sorted(_io_path(agent_root).glob("*/capture_*/native_metadata.json"))
    for path in reversed(paths):
        try:
            value = json.loads(path.read_text(encoding="utf-8")).get("api_error_status")
        except (OSError, ValueError, AttributeError):
            continue
        if re.fullmatch(r"[1-5][0-9]{2}", str(value)):
            return int(value)
    return None


class _LongPathClaudeAdapter(ClaudeCodeAdapter):
    """Keep frozen execution behavior while extending local Windows I/O paths."""

    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)
        self.case_work_root = _io_path(self.case_work_root)

    def _run_container(self, arguments: list[str], **kwargs: Any) -> dict[str, Any]:
        arguments = list(arguments)
        for index, value in enumerate(arguments[:-1]):
            if value == "-v":
                host, container = arguments[index + 1].rsplit(":", 1)
                arguments[index + 1] = _plain_windows_path(host) + ":" + container
        return super()._run_container(arguments, **kwargs)

    def run_turn(self, session: SessionHandle, message: str) -> TurnResult:
        turn = super().run_turn(session, message)
        if turn.terminated_ok:
            _record_native_model_usage(turn, session)
        return turn


def _validate_environment() -> None:
    validate_environment()


class ClaudeReplayer:
    def __init__(self, root: Path, output_dir: Path, *, adapter_factory: Callable[[Path], AgentAdapter] | None = None):
        self.root = root.resolve()
        self.output_dir = output_dir.resolve()
        protected = [self.root / "outputs" / name for name in PROTECTED_CAMPAIGNS]
        if any(self.output_dir == original or original in self.output_dir.parents for original in protected):
            raise ReplayIntegrityError("Replay output must not modify any protected historical campaign")
        production_outputs = ROOT / "outputs"
        if adapter_factory is None and (self.output_dir.parent != production_outputs or self.output_dir.name not in ALLOWED_CAMPAIGNS):
            raise ReplayIntegrityError("Replay output is not an allow-listed controlled campaign")
        forbidden = [self.root / name for name in ("src", "scripts", "configs", "tests", "data", "cases", "artifacts")]
        if self.output_dir == self.root or any(self.output_dir == path or path in self.output_dir.parents for path in forbidden):
            raise ReplayIntegrityError("Replay output must not modify frozen runtime or dataset directories")
        self.last_cache_hit = False
        self._quota_recovery_authorized = False
        self.dataset_identity = dataset_identity(self.root)
        actual_code = code_fingerprint(self.root)
        if actual_code != FROZEN_RUNTIME_HASH or ADAPTER_REVISION != "claude-code-2.1.228-r4-v843" or CLAUDE_VERSION != "2.1.228":
            raise ReplayIntegrityError("Frozen runtime or Claude adapter identity changed")
        if adapter_factory is None:
            _validate_environment()
        self.identity = {
            **self.dataset_identity, "replay_revision": REPLAY_REVISION,
            "replay_code_fingerprint": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "frozen_runtime_code_fingerprint": actual_code, "image_id": IMAGE_ID,
            "claude_code_version": CLAUDE_VERSION, "adapter_revision": ADAPTER_REVISION,
            "model": MODEL, "base_url": AGENT_BASE_URL,
            "timeout_seconds": 900, "max_technical_retries": 2,
            "profile": profile_identity(), "execution_files": code_files_identity(),
            "model_identity_policy": MODEL_IDENTITY_POLICY,
        }
        config = load_agent_config(ROOT)["claude_code"]
        pinned = {**config, "docker": {**config["docker"], "image": IMAGE_ID}}
        self._factory = adapter_factory or (lambda path: _LongPathClaudeAdapter(
            root=self.root, case_work_root=path, config=pinned, mode="docker"
        ))

    def authorize_quota_recovery(self) -> None:
        """Explicit launch flag permits one preserved quota-step recovery only."""
        self._quota_recovery_authorized = True

    def _recover_quota(self, step: Path, specification: Mapping[str, Any]) -> bool:
        path = _io_path(step)
        if not (path / "failure.json").is_file():
            return False
        failure = _read_json(path / "failure.json")
        if failure.get("category") != "quota":
            return False
        if _read_json(path / "input.json") != specification or failure.get("identity") != self.identity:
            raise ReplayIntegrityError("Quota recovery identity mismatch")
        if not self._quota_recovery_authorized:
            raise AgentProviderFailure("quota", failure.get("status_code"))
        if getattr(self, '_retry_authorized_at', '') <= failure.get('at', ''):
            raise AgentProviderFailure("quota", failure.get("status_code"))
        if (path / "accepted_turn.json").exists() or (path / "evidence.json").exists():
            raise ReplayIntegrityError("Refusing quota restart after an accepted turn")
        budget_path = path / "call_budget.json"
        if not budget_path.is_file():
            raise ReplayIntegrityError("Quota failure has no persisted physical-call budget")
        budget = _read_json(budget_path)
        used, maximum = budget.get("used"), budget.get("maximum")
        if type(used) is not int or type(maximum) is not int or maximum != 3 or not (1 <= used <= maximum):
            raise ReplayIntegrityError("Quota failure has an invalid physical-call budget")
        if used >= maximum:
            raise TechnicalFailure("Quota failure exhausted the frozen three-call phase budget")
        history = _io_path(self.output_dir / "recovery_history" / step.parent.name / step.name)
        history.mkdir(parents=True, exist_ok=True)
        generation = history / f"quota_{len(list(history.glob('quota_*'))) + 1:04d}"
        # Both absolute resolved paths remain inside this campaign; rename only.
        if self.output_dir not in step.resolve().parents or self.output_dir not in Path(_plain_windows_path(str(generation))).resolve().parents:
            raise ReplayIntegrityError("Quota archive escaped output directory")
        path.rename(generation)
        path.mkdir()
        _write_json(path / "call_budget.json", {
            "used": used,
            "maximum": maximum,
            "last_call_state": "quota_recovered",
        })
        _write_json(path / "quota_recovery.json", {"at": _now(), "reason": "explicit resume_after_recharge",
            "archived_step": _plain_windows_path(str(generation)), "failure_sha256": hashlib.sha256((generation / "failure.json").read_bytes()).hexdigest(),
            "previous_physical_calls": used,
            "maximum_physical_calls": maximum,
            "remaining_physical_calls": maximum - used})
        self._quota_recovery_authorized = False
        return True

    def _validate_selected(self, case: Any, selected: Mapping[str, Any]) -> str:
        require_case_dataset_identity(selected, case, self.dataset_identity, where="selected replay evidence")
        if selected.get("runtime_model_check", {}).get("model_identity_policy") != MODEL_IDENTITY_POLICY:
            raise ReplayIntegrityError("Selected exposure belongs to another model-evidence policy")
        if selected.get("phase") != "exposure" or selected.get("termination", {}).get("status") != "ok":
            raise ReplayIntegrityError("Replay requires a successful exposure evidence snapshot")
        if not isinstance(selected.get("final_workspace_state"), dict) or not isinstance(selected.get("memory_after"), dict):
            raise ReplayIntegrityError("Replay requires workspace and memory snapshots")
        for name, content in selected["final_workspace_state"].items():
            _safe_relative(name)
            _safe_text(content)
        for name, content in selected["memory_after"].items():
            if not isinstance(name, str) or ":" not in name:
                raise ReplayIntegrityError("Invalid native memory namespace")
            namespace, relative = name.split(":", 1)
            path = _safe_relative(relative)
            if namespace not in {"workspace", "claude_config"}:
                raise ReplayIntegrityError("Unknown native memory namespace")
            if namespace == "workspace" and (path.name != "CLAUDE.md" or path.parts[0] in {"imports", "records"}):
                raise ReplayIntegrityError("Business/carrier file is not retained native memory")
            if namespace == "workspace" and selected["final_workspace_state"].get(relative) != content:
                raise ReplayIntegrityError("Workspace memory conflicts with the workspace snapshot")
            if namespace == "claude_config" and not (path.name == "CLAUDE.md" or (
                len(path.parts) >= 4 and path.parts[0] == "projects" and path.parts[2] == "memory" and path.suffix == ".md"
            )):
                raise ReplayIntegrityError("Unexpected native auto-memory path")
            _safe_text(content)
        entries = selected.get("trajectory")
        if not isinstance(entries, list):
            raise ReplayIntegrityError("Exposure native trajectory is missing")
        main = [entry for entry in entries if entry.get("scope") == "session"]
        if len(main) != 1:
            raise ReplayIntegrityError("Exactly one exposure native main transcript is required")
        native_id = main[0].get("native_session_id")
        try:
            if str(uuid.UUID(native_id)) != native_id:
                raise ValueError
        except (ValueError, TypeError, AttributeError):
            raise ReplayIntegrityError("Exposure native UUID is invalid") from None
        for entry in entries:
            path = _safe_relative(entry.get("source", ""))
            if entry.get("native_session_id") != native_id or path.parts[0] != "projects":
                raise ReplayIntegrityError("Mixed native sessions in exposure trajectory")
            if entry.get("scope") == "session":
                valid = len(path.parts) == 3 and path.name == native_id + ".jsonl"
            else:
                valid = entry.get("scope") == "subagent" and len(path.parts) >= 5 and path.parts[2:4] == (native_id, "subagents")
            if not valid or not isinstance(entry.get("lines"), list) or not entry["lines"]:
                raise ReplayIntegrityError("Invalid scoped native transcript")
            for line in entry["lines"]:
                if not isinstance(line, str) or "\n" in line or "\r" in line:
                    raise ReplayIntegrityError("Invalid native JSONL line")
                try:
                    event = json.loads(line)
                except ValueError:
                    raise ReplayIntegrityError("Malformed native transcript JSON") from None
                if entry.get("scope") == "session" and isinstance(event, dict) and event.get("sessionId") not in (None, native_id):
                    raise ReplayIntegrityError("Main native transcript contains another session")
        return native_id

    def _restore(self, agent_root: Path, selected: Mapping[str, Any], *, restore_history: bool) -> None:
        for name, content in selected["final_workspace_state"].items():
            path = _io_path(agent_root / "workspace" / _safe_relative(name))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8", newline="\n")
        for name, content in selected["memory_after"].items():
            namespace, relative = name.split(":", 1)
            path = _io_path(agent_root / namespace / _safe_relative(relative))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8", newline="\n")
        if restore_history:
            for entry in selected["trajectory"]:
                path = _io_path(agent_root / "claude_config" / _safe_relative(entry["source"]))
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("\n".join(entry["lines"]) + "\n", encoding="utf-8", newline="\n")

    def _recover_interrupted(self, step: Path, specification: Mapping[str, Any]) -> bool:
        path=_io_path(step)
        if not path.exists() or any((path/name).exists() for name in ('result.json','accepted_turn.json','failure.json','cleanup_failure.json')):
            return False
        if not (path/'input.json').exists() or _read_json(path/'input.json')!=specification:
            raise ReplayIntegrityError('Interrupted stage has no matching input identity')
        # Unknown executions may already have changed state. Never blindly replay.
        raise ReplayIntegrityError('Interrupted request needs evidence reconciliation before replay')

    def _cache(self, step: Path, specification: Mapping[str, Any]) -> dict[str, Any] | None:
        path = _io_path(step)
        if not path.exists():
            self.last_cache_hit = False
            return None
        stored_input = _read_json(path / "input.json")
        if not (path / "result.json").exists():
            if stored_input != specification or (path / "cleanup_failure.json").exists():
                raise ReplayIntegrityError("Partial replay step identity or cleanup requires review")
            if (path / "failure.json").exists() and _read_json(path / "failure.json").get("category") == "quota":
                raise AgentProviderFailure("quota", _read_json(path / "failure.json").get("status_code"))
            if (path / "accepted_turn.json").is_file() and (path / "prepared.json").is_file():
                self.last_cache_hit = False
                return None  # Finish snapshots from the accepted turn, without another physical call.
            raise ReplayIntegrityError("Partial replay step exists; preserve it and use a reviewed recovery path")
        result = _read_json(path / "result.json")
        if result.get("status") != "complete" or stored_input != specification or result.get("input_fingerprint") != _hash(specification):
            raise ReplayIntegrityError("Replay cache input or completion identity mismatch")
        if result.get("identity") != self.identity:
            raise ReplayIntegrityError("Replay cache runtime identity mismatch")
        expected_marker = {**self.identity, **{key: specification[key] for key in (
            *self.dataset_identity, "input_fingerprint", "judge_contract_sha256", "case_id"
        )}}
        if _read_json(path / "identity.json") != expected_marker:
            raise ReplayIntegrityError("Replay identity marker mismatch")
        evidence_path = path / "evidence.json"
        if not evidence_path.is_file() or hashlib.sha256(evidence_path.read_bytes()).hexdigest() != result.get("evidence_sha256"):
            raise ReplayIntegrityError("Replay cached evidence was modified")
        evidence = _read_json(evidence_path)
        self.last_cache_hit = True
        return evidence

    def exposure(self, case: Any, attempt_no: int, previous_evidence: Mapping[str, Any] | None = None) -> dict[str, Any]:
        if attempt_no not in (1, 2) or (attempt_no == 1) != (previous_evidence is None):
            raise ReplayIntegrityError("Exposure a1 is clean; a2 requires the selected a1 evidence")
        if previous_evidence is not None and not str(previous_evidence.get("attempt_id", "")).endswith("exposure_attempt_1"):
            raise ReplayIntegrityError("Exposure a2 can only resume exposure a1")
        return self._execute(case, "exposure", attempt_no, previous_evidence)

    def trigger(self, case: Any, selected_evidence: Mapping[str, Any]) -> dict[str, Any]:
        return self._execute(case, "trigger", None, selected_evidence)

    def _execute(self, case: Any, phase: str, attempt_no: int | None, selected: Mapping[str, Any] | None) -> dict[str, Any]:
        case_identity = case_dataset_identity(case, self.dataset_identity)
        _safe_relative(case.case_id)
        native_before = self._validate_selected(case, selected) if selected is not None else None
        selected_hash = _hash({key: value for key, value in selected.items() if key != "replay"}) if selected is not None else None
        step_id = f"exposure_a{attempt_no}" if phase == "exposure" else "trigger"
        step = self.output_dir / "replay" / case.case_id / step_id
        attempt_id = f"{case.case_id}_exposure_attempt_{attempt_no}" if phase == "exposure" else f"{case.case_id}_trigger"
        inputs = _phase_inputs(case, phase, self.root)
        if phase == "exposure" and attempt_no == 2:
            inputs = {"message": DEFAULT_RETRY_FOLLOWUP, "workspace_files": {}}
        specification = {
            "identity": self.identity, **case_identity, "case_id": case.case_id,
            "step_id": step_id, "step_dir": str(step), "phase": phase, "attempt_no": attempt_no,
            "selected_evidence_fingerprint": selected_hash, "inputs_fingerprint": _hash(inputs),
        }
        quota_recovered = self._recover_quota(step, specification) if _io_path(step).exists() else False
        recovered = quota_recovered
        if not recovered:
            recovered = self._recover_interrupted(step, specification)
        cached = None if recovered else self._cache(step, specification)
        if cached is not None:
            return cached
        # Atomic mkdir is the per-step exclusion guard for independent callers.
        _io_path(step).parent.mkdir(parents=True, exist_ok=True)
        resuming_accepted = _io_path(step / "accepted_turn.json").is_file()
        if not recovered and not resuming_accepted:
            try:
                _io_path(step).mkdir(exist_ok=False)
            except FileExistsError:
                raise ReplayIntegrityError("Another caller created this replay step") from None
        _write_json(step / "input.json", specification)
        _write_json(step / "identity.json", {**self.identity, **case_identity, "case_id": case.case_id})
        if selected is not None:
            _write_json(step / "selected_evidence.json", dict(selected))
        agent_root = step / "agent"
        adapter = None
        session = None
        evidence = None
        primary_error = None
        physical_calls = 0
        try:
            if selected is not None and not resuming_accepted:
                self._restore(agent_root, selected, restore_history=phase == "exposure")
            adapter = self._factory(agent_root)
            if hasattr(adapter, "timeout"):
                adapter.timeout = 900
            controller = _CheckpointController(RuntimeConfig(
                judge=DeferredJudge(), adapter_factory=lambda _path: adapter,
                campaign_dir=_io_path(step), root=self.root, max_technical_retries=2,
                keep_workspaces=True, dataset_metadata=self.dataset_identity,
            ))
            lineage = {}
            if selected is not None:
                lineage = ({"previous_attempt_id": selected["attempt_id"]} if phase == "exposure" else {
                    "exposure_session_id": selected["session_id"], "exposure_attempt_id": selected["attempt_id"],
                })
            if resuming_accepted:
                accepted = _read_json(step / "accepted_turn.json")
                prepared = _read_json(step / "prepared.json")
                value = accepted.get("value", {})
                prior = prepared.get("value", {})
                if accepted.get("sha256") != _hash(value) or prepared.get("sha256") != _hash(prior) or value.get("prepared_sha256") != prepared.get("sha256"):
                    raise ReplayIntegrityError("Accepted turn checkpoint hash mismatch")
                if prior.get("input_fingerprint") != _hash(specification):
                    raise ReplayIntegrityError("Accepted turn checkpoint input mismatch")
                restored = dict(value["session"])
                restored["workspace"] = _io_path(Path(restored["workspace"]))
                session = SessionHandle(**restored)
                if session.phase != phase or session.attempt_id != attempt_id:
                    raise ReplayIntegrityError("Accepted turn session identity mismatch")
                expected_workspace = Path(_plain_windows_path(str(agent_root / "workspace"))).resolve()
                actual_workspace = Path(_plain_windows_path(str(session.workspace))).resolve()
                expected_config = Path(_plain_windows_path(str(agent_root / "claude_config"))).resolve()
                actual_config = Path(_plain_windows_path(str(session.state.get("config_dir", "")))).resolve()
                if actual_workspace != expected_workspace or actual_config != expected_config:
                    raise ReplayIntegrityError("Accepted turn session escaped its isolated step")
                if _hash(dict(adapter.snapshot_workspace(session).files)) != value["workspace_after_fingerprint"] or _hash(dict(adapter.snapshot_memory(session).files)) != value["memory_after_fingerprint"]:
                    raise ReplayIntegrityError("Accepted turn retained state changed after interruption")
                memory_before = FileSet(prior["memory_before"])
                workspace_before = FileSet(prior["workspace_before"])
                expected_memory = dict(selected["memory_after"]) if selected is not None else {}
                turn = TurnResult(**value["turn"])
                retries = value["physical_calls"] - 1
            else:
                session = adapter.start_session(case, phase, attempt_id, lineage=lineage, inputs=inputs)
            session.state["keep_workspace"] = True
            if not resuming_accepted and phase == "exposure" and attempt_no == 2:
                session.session_id = selected["session_id"]
                session.state["requested_native_session_id"] = native_before
                session.state["native_session_id"] = native_before
                session.state["continue_session"] = True
                session.state["previous_native_session_ids"] = [
                    sid for sid in session.state.get("previous_native_session_ids", []) if sid != native_before
                ]
            elif not resuming_accepted and phase == "trigger":
                session.state["previous_native_session_ids"] = list(set(session.state.get("previous_native_session_ids", [])) | {native_before})
                if session.state.get("requested_native_session_id") == native_before:
                    raise ReplayIntegrityError("Trigger attempted to reuse the exposure native UUID")
            if not resuming_accepted:
                memory_before = adapter.snapshot_memory(session)
            expected_memory = dict(selected["memory_after"]) if selected is not None else {}
            if dict(memory_before.files) != expected_memory:
                raise ReplayIntegrityError("Restored native memory differs from the selected exposure snapshot")
            if not resuming_accepted:
                workspace_before = adapter.snapshot_workspace(session)
            _write_json(step / "restored_state.json", {
                "memory_fingerprint": _hash(dict(memory_before.files)),
                "workspace_fingerprint": _hash(dict(workspace_before.files)),
                "selected_native_session_id": native_before,
                "requested_native_session_id": session.state.get("requested_native_session_id"),
            })
            if not resuming_accepted:
                prepared = {"input_fingerprint": _hash(specification), "session": _session_dict(session),
                            "memory_before": dict(memory_before.files), "workspace_before": dict(workspace_before.files)}
                _write_json(step / "prepared.json", {"value": prepared, "sha256": _hash(prepared)})
                turn, retries = controller._run_turn_with_technical_retries(
                    adapter, session, inputs["message"], case.case_id, attempt_id, phase,
                )
            physical_calls = retries + 1
            evidence = controller._snapshot_evidence(
                adapter, session, case, phase, attempt_id, inputs["message"], turn,
                memory_before, workspace_before, lineage,
            )
            try:
                evidence["runtime_model_check"] = agent_model_evidence(
                    session.state["native_model_usage"], evidence["trajectory"])
            except (ValueError, TypeError, KeyError, AttributeError):
                raise AgentProviderFailure("model_route")
            if evidence["memory_before"] != expected_memory:
                raise ReplayIntegrityError("Collected evidence changed the restored memory boundary")
            native_after = session.state.get("native_session_id")
            if phase == "trigger" and native_after == native_before:
                raise ReplayIntegrityError("Trigger native session was not fresh")
            if phase == "exposure" and attempt_no == 2 and native_after != native_before:
                raise ReplayIntegrityError("Exposure a2 did not continue the selected native session")
            evidence["replay"] = {
                "step_id": step_id, "step_dir": str(step), "physical_calls": physical_calls,
                "reused": False, "input_fingerprint": _hash(specification),
                "selected_evidence_fingerprint": selected_hash, "native_session_id": native_after,
            }
            _write_json(step / "evidence.json", evidence)
        except BaseException as error:
            primary_error = error
            physical_calls = max(physical_calls, int(session.state.get("call_count", 0)) if session else 0)
            status_code = _failure_status(error, agent_root)
            category = getattr(error, "category", "integrity" if isinstance(error, ReplayIntegrityError) else "technical")
            _write_json(step / "failure.json", {"at": _now(), "case_id": case.case_id, "step_id": step_id,
                "identity": self.identity, "physical_calls": physical_calls, "error_type": type(error).__name__,
                "status_code": status_code, "category": category})
            if isinstance(error, (KeyboardInterrupt, SystemExit, ReplayIntegrityError, AgentProviderFailure)):
                raise
            if _io_path(step / "accepted_turn.json").is_file():
                raise ReplayIntegrityError("Accepted turn checkpoint retained; resume evidence collection without another model call") from None
            failure = TechnicalFailure(f"Claude replay {step_id} failed ({type(error).__name__}); preserved step evidence")
            failure.status_code = status_code
            failure.category = category
            raise failure from None
        finally:
            if adapter is not None:
                try:
                    cleanup = getattr(adapter, "cleanup_resources", None)
                    cleanup() if cleanup else adapter.cleanup(session)
                except Exception as error:
                    _write_json(step / "cleanup_failure.json", {"at": _now(), "error_type": type(error).__name__, "physical_calls": physical_calls})
                    if primary_error is None:
                        raise TechnicalFailure("Claude replay cleanup failed; partial step retained") from None
        _write_json(step / "result.json", {
            "status": "complete", "at": _now(), "identity": self.identity,
            "case_id": case.case_id, "step_id": step_id, "physical_calls": physical_calls,
            "input_fingerprint": _hash(specification),
            "evidence_sha256": hashlib.sha256(_io_path(step / "evidence.json").read_bytes()).hexdigest(),
        })
        return evidence

    def physical_calls_total(self) -> int:
        total = 0
        # Active replay call budgets are cumulative across preserved quota
        # recoveries, so archived generations must not be counted a second time.
        steps = list(_io_path(self.output_dir / "replay").glob("*/*"))
        for step in steps:
            if not step.is_dir():
                continue
            recorded = []
            for name in ("result.json", "failure.json", "cleanup_failure.json"):
                if (step / name).is_file():
                    recorded.append(int(_read_json(step / name).get("physical_calls", 0)))
            if (step / "call_budget.json").is_file():
                recorded.append(int(_read_json(step / "call_budget.json").get("used", 0)))
            captures = sum(1 for path in (step / "agent").glob("*/capture_*") if path.is_dir())
            total += max([captures, *recorded])
        return total
