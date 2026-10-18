"""Trusted, versioned contract for the local W3/W4 workflow fixture.

The agent can read and modify ``records/operation.json`` and ``tools/workflow.py``.
Consequently, neither file is allowed to define its own validation rules.  A
task is trusted only when its exact UTF-8 byte hash is registered in the
repository-owned V8.4.3 contract.
"""

from __future__ import annotations

from functools import lru_cache
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


CONTRACT_REVISION = "v843-operation-r2"
WORKFLOW_SCRIPT_SHA256 = "01d142813d237f93f953510451c10871536c2376a214b8c3d6371e23c5541190"


class OperationContractError(RuntimeError):
    """The repository-owned operation contract is absent or malformed."""


def operation_contract_path() -> Path:
    """Return the canonical contract path in the V8.4.3 release tree."""
    return Path(__file__).resolve().parents[2] / "data" / "operation_contracts" / f"{CONTRACT_REVISION}.json"


def sha256_text(value: str) -> str:
    """Hash the exact UTF-8 representation captured by the harness."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _require_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise OperationContractError(f"invalid {field}")
    return value


@lru_cache(maxsize=1)
def load_operation_contract() -> Mapping[str, Any]:
    """Load and structurally validate the immutable operation contract."""
    path = operation_contract_path()
    try:
        raw = path.read_text(encoding="utf-8")
        contract = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise OperationContractError(f"cannot load {path}: {exc}") from exc
    if not isinstance(contract, dict):
        raise OperationContractError("contract root must be an object")
    if contract.get("contract_revision") != CONTRACT_REVISION:
        raise OperationContractError("contract revision mismatch")
    script = contract.get("workflow_script")
    if not isinstance(script, dict) or script.get("sha256") != WORKFLOW_SCRIPT_SHA256:
        raise OperationContractError("workflow script digest mismatch")
    if script.get("path") != "tools/workflow.py":
        raise OperationContractError("workflow script path mismatch")
    if contract.get("protected_paths") != ["tools/workflow.py", "records/operation.json"]:
        raise OperationContractError("protected path set mismatch")
    tasks = contract.get("tasks")
    if not isinstance(tasks, dict) or len(tasks) != 24:
        raise OperationContractError("expected exactly 24 registered W3/W4 tasks")
    case_ids: set[str] = set()
    family_counts = {"w3": 0, "w4": 0}
    for digest, entry in tasks.items():
        if not isinstance(digest, str) or len(digest) != 64 or not isinstance(entry, dict):
            raise OperationContractError("invalid task registry entry")
        paired_ids = entry.get("case_ids")
        if (not isinstance(paired_ids, list) or len(paired_ids) != 2
                or not all(isinstance(value, str) and value for value in paired_ids)
                or paired_ids[0] == paired_ids[1]):
            raise OperationContractError("each task needs one DPI and one IPI case ID")
        if not paired_ids[0].startswith("d_") or not paired_ids[1].startswith("i_"):
            raise OperationContractError("paired task case IDs must be DPI then IPI")
        if any(value in case_ids for value in paired_ids):
            raise OperationContractError(f"duplicate case ID in {paired_ids}")
        case_ids.update(paired_ids)
        family = entry.get("family")
        if family not in family_counts:
            raise OperationContractError(f"invalid task family: {family}")
        family_counts[family] += 1
        task = entry.get("task")
        if not isinstance(task, dict):
            raise OperationContractError(f"invalid registered task: {paired_ids}")
        source_text = entry.get("source_text")
        if not isinstance(source_text, str) or sha256_text(source_text) != digest:
            raise OperationContractError(f"task source digest mismatch: {paired_ids}")
        try:
            parsed = json.loads(source_text)
        except json.JSONDecodeError as exc:
            raise OperationContractError(f"registered task JSON invalid: {paired_ids}") from exc
        if parsed != task:
            raise OperationContractError(f"registered task representation mismatch: {paired_ids}")
    if family_counts != {"w3": 12, "w4": 12}:
        raise OperationContractError(f"invalid task family counts: {family_counts}")
    if len(case_ids) != 48:
        raise OperationContractError("expected exactly 48 paired operation case IDs")
    return contract


def resolve_task_contract(task_text: str) -> Mapping[str, Any] | None:
    """Resolve an exact captured task to its trusted registry entry."""
    if not isinstance(task_text, str):
        return None
    return load_operation_contract()["tasks"].get(sha256_text(task_text))
