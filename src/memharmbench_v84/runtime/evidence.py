"""Runtime evidence collection shared by all adapters and judges."""

from __future__ import annotations

import difflib
import hashlib
from typing import Any, Mapping

from ..adapters.base import FileSet, TraceData, TurnResult
from ..adapters.native_evidence import is_native_memory_path, memory_diagnostics, normalize_native_trace, operation_diagnostics
from ..identity import case_condition

EVIDENCE_SCHEMA_VERSION = "8.4.3"


def diff_files(before: Mapping[str, str], after: Mapping[str, str]) -> list[dict[str, Any]]:
    """Unified diffs between two file snapshots."""
    changes: list[dict[str, Any]] = []
    keys = sorted(set(before) | set(after))
    for key in keys:
        old = before.get(key)
        new = after.get(key)
        if old == new:
            continue
        if old is None:
            changes.append({"path": key, "change": "added", "diff": new})
        elif new is None:
            changes.append({"path": key, "change": "removed", "diff": old})
        else:
            diff = "\n".join(
                difflib.unified_diff(
                    old.splitlines(),
                    new.splitlines(),
                    fromfile=f"before/{key}",
                    tofile=f"after/{key}",
                    lineterm="",
                )
            )
            changes.append({"path": key, "change": "modified", "diff": diff})
    return changes


def build_evidence(
    *,
    case: Any,
    phase: str,
    attempt_id: str,
    session_id: str,
    memory_before: FileSet,
    memory_after: FileSet,
    workspace_before: FileSet,
    workspace_after: FileSet,
    trace: TraceData,
    turn: TurnResult,
    message: str,
    attachments: Mapping[str, str],
    lineage: Mapping[str, str],
) -> dict[str, Any]:
    """Assemble the complete evidence bundle a Judge must see."""
    from ..judges.atomic import case_contract_fingerprint
    evaluation_contract = case_contract_fingerprint(case)
    dependencies = getattr(case, "evidence_dependencies", None)
    if not isinstance(dependencies, Mapping) or any(name not in dependencies for name in ("C_ASR", "UTDR", "ATAR")):
        raise ValueError("case evidence_dependencies are missing")
    template = {
        "template_id": case.template_id,
        "objective_id": case.objective_id,
        "objective_name": case.binding.objective_name if hasattr(case.binding, "objective_name") else "",
        "victim": case.victim,
        "channel": case.channel,
        "context_id": case.context_id,
    }
    normalized = normalize_native_trace(trace.entries)
    provenance = getattr(case, "provenance", {})
    # The V8.4.3 catalog explicitly declares the attack condition; missing identity is invalid.
    condition = case_condition(provenance)
    memory_errors = [f"non_native_memory_path:{name}"
                     for snapshot in (memory_before.files, memory_after.files)
                     for name in snapshot if not is_native_memory_path(name)]
    integrity = {**normalized["integrity"], "memory_snapshot_complete": not memory_errors,
                 "capture_complete": turn.terminated_ok and normalized["integrity"]["native_capture_complete"] and not memory_errors,
                 "errors": normalized["integrity"]["errors"] + memory_errors}
    protected_paths = ("tools/workflow.py", "records/operation.json")
    initial_operation_files = {
        path: workspace_before.files[path]
        for path in protected_paths if path in workspace_before.files
    }
    mount = (turn.raw.get("protected_operation_mounts")
             if isinstance(turn.raw, Mapping) else None)
    mount_ok = True
    if phase == "trigger" and initial_operation_files:
        observed = mount.get("inputs") if isinstance(mount, Mapping) else None
        expected = [
            {"path": path, "container_path": "/workspace/" + path,
             "sha256": hashlib.sha256(initial_operation_files[path].encode("utf-8")).hexdigest()}
            for path in protected_paths if path in initial_operation_files
        ]
        mount_ok = (
            len(initial_operation_files) == 2
            and isinstance(mount, Mapping)
            and mount.get("policy") == "trigger_pair_readonly_nested_bind_v1"
            and mount.get("mode") == "ro"
            and observed == expected
        )
        if not mount_ok:
            integrity["errors"].append("trusted_operation_readonly_mount_missing_or_mismatched")
            integrity["capture_complete"] = False
    integrity["trusted_operation_mount_complete"] = mount_ok
    return {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "evaluation_contract_fingerprint": evaluation_contract,
        "evidence_dependencies": dict(dependencies),
        "case_id": case.case_id,
        "dataset_revision": getattr(case, "provenance", {}).get("dataset_revision", "8.4.3"),
        "condition": condition,
        "replicate_id": provenance.get("replicate_id"),
        "case_provenance": dict(provenance),
        "phase": phase,
        "attempt_id": attempt_id,
        "session_id": session_id,
        "lineage": dict(lineage),
        "template_and_target": {
            **template,
            "mechanism": case.binding.exposure_reference,
            "target_memory_rule": case.target_memory_rule,
            "payload_text": case.payload_text,
            "payload_value": case.payload_value,
            "prohibited_value": case.prohibited_value,
            "judge_contract_sha256": case.judge_contract_sha256,
            "permission_condition": case.permission_condition,
            "source_authority": case.source_authority,
            "protected_entity": case.protected_entity,
            "harm_outcome": case.harm_outcome,
            "c_asr_condition": case.binding.c_asr_condition,
            "utdr_condition": case.binding.utdr_condition,
            "atar_condition": case.binding.atar_condition,
            "business_background": case.binding.business_background,
            "entities": dict(case.binding.entities),
        },
        "benign_task": case.binding.trigger_task,
        "user_message": message,
        "trajectory": list(trace.entries),
        "tool_calls": normalized["tool_calls"],
        "tool_results": normalized["tool_results"],
        "native_events": normalized["native_events"],
        "native_sources": normalized["native_sources"],
        "initial_task_files": {path: workspace_before.files[path] for path in ("tools/workflow.py", "records/operation.json")
                               if path in workspace_before.files},
        "protected_operation_mounts": mount if isinstance(mount, Mapping) else None,
        "operation_diagnostics": operation_diagnostics(workspace_before.files, workspace_after.files,
                                                       normalized["tool_calls"], normalized["tool_results"],
                                                       expected_case_id=case.case_id, dataset_revision="8.4.3"),
        "evidence_integrity": integrity,
        "result_envelope_tool_calls": list(turn.tool_calls),
        "result_envelope_tool_results": list(turn.tool_results),
        "attachments": dict(attachments),
        "final_answer": turn.answer,
        "final_workspace_state": dict(workspace_after.files),
        "workspace_diff": diff_files(workspace_before.files, workspace_after.files),
        "memory_before": dict(memory_before.files),
        "memory_after": dict(memory_after.files),
        "memory_diff": diff_files(memory_before.files, memory_after.files),
        "memory_diagnostics": memory_diagnostics(memory_before.files if phase == "trigger" else memory_after.files,
                                                 normalized["native_events"]),
        "termination": {
            "status": "ok" if turn.terminated_ok else "error",
            "message": turn.error or "",
        },
    }


def attachments_from_workspace(workspace: FileSet, max_files: int = 64) -> dict[str, str]:
    return {name: content for name, content in list(workspace.files.items())[:max_files]}
