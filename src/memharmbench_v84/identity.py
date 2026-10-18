"""Frozen input and evaluation identities for MemHarmBench V8.4.3.

``dataset_input_fingerprint`` describes only bytes delivered to the agent.
``evaluation_contract_fingerprint`` independently binds the resolved oracle,
rubrics, evidence boundary, validator and metric policy. Keeping the two
identities separate lets V8.4.3 repair an oracle without pretending the visible
attack inputs changed, while still making every V8.3 result/cache ineligible.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Iterable, Mapping

DATASET_REVISION = "8.4.3"
SCHEMA_VERSION = "8.4.3"
CASE_REVISIONS = frozenset({DATASET_REVISION})
CONDITIONS = ("attack",)
EXPECTED_DATASET_INPUT_FINGERPRINT = (
    "fc129ed3bef9fc040fac73f5f257f5b86447ff2404303d457a4de636d660e462"
)
IDENTITY_FIELDS = (
    "dataset_revision",
    "dataset_input_fingerprint",
    "evaluation_contract_fingerprint",
)
RUNTIME_IDENTITY_FIELDS = (*IDENTITY_FIELDS, "release_lock_sha256")
JUDGE_CONTRACT_REVISION = "v843-judge-contract-r1"
EVIDENCE_SCHEMA_REVISION = "v843-atomic-predicates-1"
VALIDATOR_BOUNDARY_REVISION = "v843-operation-r2"
STATISTICS_POLICY_REVISION = "v843-attack-only-wilson-r1"
PLACEHOLDER_TOKENS = ("{VALUE}", "{{VALUE}}", "<VALUE>")
# These files materially define a label or its denominator. The list is
# intentionally explicit: a new policy surface needs a versioned edit here.
EVALUATION_POLICY_FILES = (
    "data/evaluation_contract.json",
    "src/memharmbench_v84/adapters/native_evidence.py",
    "src/memharmbench_v84/judges/atomic.py",
    "src/memharmbench_v84/judges/__init__.py",
    "src/memharmbench_v84/judges/validation.py",
    "src/memharmbench_v84/metrics.py",
    "src/memharmbench_v84/operation_contract.py",
    "src/memharmbench_v84/pilot_report.py",
    "src/memharmbench_v84/runtime/evidence.py",
    "runtime/protocols.py",
    "runtime/distance_chain.py",
    "runtime/judging.py",
    "workflow_quickrouter/judge_backend.py",
)

RELEASE_CODE_FILES = (
    "pyproject.toml", "scripts/build_release_manifest.py", "scripts/validate_public.py",
    "src/memharmbench_v84/identity.py", "src/memharmbench_v84/cli.py",
)
PUBLIC_RELEASE_SCHEMA = "memharmbench-public-release-1"


def canonical_json(value: Any) -> bytes:
    """Encode a value for stable cross-platform hashing."""
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(_io_path(path).read_bytes())


def _io_path(path: Path) -> Path:
    """Use a Windows extended path for output trees that exceed MAX_PATH."""
    absolute = str(path.resolve())
    if os.name != "nt" or absolute.startswith("\\\\?\\"):
        return Path(absolute)
    if absolute.startswith("\\\\"):
        return Path("\\\\?\\UNC\\" + absolute[2:])
    return Path("\\\\?\\" + absolute)


def case_condition(provenance: Mapping[str, Any]) -> str:
    value = provenance.get("condition")
    if value not in CONDITIONS:
        raise ValueError(
            "case provenance must declare the V8.4.3 attack condition; "
            "clean/control records are outside this release"
        )
    return str(value)


def input_fingerprint(
    files: Mapping[str, bytes], *, payload_value: str | None = None
) -> str:
    """Hash relative input paths and bytes.

    ``payload_value`` remains for historical analysis helpers. Formal V8.4.3
    identity calls this function without normalization.
    """
    import re

    entries: list[list[str]] = []
    for name, content in sorted(files.items()):
        if payload_value is not None:
            text = content.decode("utf-8")
            if payload_value:
                text = text.replace(payload_value, "<ATTACK_VALUE>")
            content = re.sub(r"\s+", " ", text).strip().encode("utf-8")
        entries.append([name, sha256_bytes(content)])
    return sha256_bytes(canonical_json(entries))


def safe_relative_path(root: Path, relative: str) -> Path:
    """Resolve a portable release path without accepting traversal or symlinks."""
    if not relative or "\\" in relative or ":" in relative:
        raise ValueError(f"invalid release path: {relative!r}")
    parts = relative.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError(f"invalid release path: {relative!r}")
    path = root.joinpath(*parts)
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("release path escapes its root")
    if any(parent.is_symlink() for parent in (path, *path.parents) if parent != root.parent):
        raise ValueError("symlinks are not permitted in frozen input paths")
    return path


def case_input_files(root: Path, case_id: str) -> dict[str, bytes]:
    if re.fullmatch(r"[a-z0-9_]+", case_id) is None:
        raise ValueError("invalid case identifier")
    case_root = safe_relative_path(root, f"artifacts/{case_id}")
    return {
        path.relative_to(case_root).as_posix(): safe_relative_path(
            case_root, path.relative_to(case_root).as_posix()
        ).read_bytes()
        for path in sorted(case_root.rglob("*")) if path.is_file()
    }


def compute_dataset_fingerprint(
    case_records: Iterable[Mapping[str, Any]],
    case_fingerprints: Mapping[str, str],
) -> str:
    entries = sorted(
        (str(case["case_id"]), case_fingerprints[str(case["case_id"])])
        for case in case_records
    )
    return sha256_bytes(canonical_json(entries))


def rubric_sha256(root: Path, template_id: str) -> str:
    path = root / "data" / "judge_rubrics" / f"{template_id}.json"
    if not path.is_file():
        raise ValueError(f"missing Judge rubric for {template_id}: {path}")
    return sha256_file(path)


def _binding(case: Mapping[str, Any]) -> Mapping[str, Any]:
    binding = case.get("binding")
    if not isinstance(binding, Mapping):
        raise ValueError(f"{case.get('case_id', '<unknown>')}: binding is missing")
    return binding


def criterion_snapshot(case: Mapping[str, Any]) -> dict[str, Any]:
    """Canonical case-specific oracle before or after placeholder resolution."""
    binding = _binding(case)
    return {
        "target_memory_rule": case.get("target_memory_rule"),
        "trigger_input": case.get("trigger_input"),
        "protected_entity": case.get("protected_entity"),
        "harm_outcome": case.get("harm_outcome"),
        "prohibited_value": case.get("prohibited_value"),
        "binding": {
            key: binding.get(key)
            for key in (
                "trigger_task",
                "target_memory_rule",
                "c_asr_condition",
                "utdr_condition",
                "atar_condition",
            )
        },
    }


def criterion_sha256(case: Mapping[str, Any]) -> str:
    return sha256_bytes(canonical_json(criterion_snapshot(case)))


def compute_case_judge_contract_sha256(
    case: Mapping[str, Any], *, rubric_hash: str
) -> str:
    """Hash every case-specific input that can affect a formal label."""
    binding = _binding(case)
    provenance = case.get("provenance", {})
    condition = provenance.get("condition") if isinstance(provenance, Mapping) else None
    contract = {
        "judge_contract_revision": JUDGE_CONTRACT_REVISION,
        "case_id": case.get("case_id"),
        "condition": condition,
        "template_id": case.get("template_id"),
        "rubric_sha256": rubric_hash,
        "objective_id": case.get("objective_id"),
        "context_id": case.get("context_id"),
        "channel": case.get("channel"),
        "victim": case.get("victim"),
        "permission_condition": case.get("permission_condition"),
        "source_authority": case.get("source_authority"),
        "criterion_before_sha256": (
            provenance.get("criterion_before_sha256")
            if isinstance(provenance, Mapping)
            else None
        ),
        "criterion_after_sha256": (
            provenance.get("criterion_after_sha256")
            if isinstance(provenance, Mapping)
            else None
        ),
        "target_memory_rule": case.get("target_memory_rule"),
        "prohibited_value": case.get("prohibited_value"),
        "trigger_input": case.get("trigger_input"),
        "protected_entity": case.get("protected_entity"),
        "harm_outcome": case.get("harm_outcome"),
        "evidence_dependencies": case.get("evidence_dependencies"),
        "binding": {
            "business_background": binding.get("business_background"),
            "trigger_task": binding.get("trigger_task"),
            "target_memory_rule": binding.get("target_memory_rule"),
            "protected_entity": binding.get("protected_entity"),
            "harm_outcome": binding.get("harm_outcome"),
            "c_asr_condition": binding.get("c_asr_condition"),
            "utdr_condition": binding.get("utdr_condition"),
            "atar_condition": binding.get("atar_condition"),
            "entities": binding.get("entities"),
        },
        "entities": case.get("entities"),
    }
    return sha256_bytes(canonical_json(contract))


def _hash_files(root: Path, relative_paths: Iterable[str], *, strict: bool) -> dict[str, str]:
    result: dict[str, str] = {}
    missing: list[str] = []
    for rel in relative_paths:
        path = root / rel
        if path.is_file():
            result[rel] = sha256_file(path)
        else:
            missing.append(rel)
    operation_root = root / "data" / "operation_contracts"
    if operation_root.exists():
        for path in sorted(operation_root.rglob("*.json")):
            result[path.relative_to(root).as_posix()] = sha256_file(path)
    if strict and missing:
        raise ValueError(f"evaluation policy files are missing: {missing}")
    return dict(sorted(result.items()))


def evaluation_policy_hashes(root: Path, *, strict: bool = True) -> dict[str, str]:
    return _hash_files(root, EVALUATION_POLICY_FILES, strict=strict)


def compute_evaluation_contract_fingerprint(
    case_contracts: Mapping[str, str], policy_hashes: Mapping[str, str]
) -> str:
    payload = {
        "judge_contract_revision": JUDGE_CONTRACT_REVISION,
        "evidence_schema_revision": EVIDENCE_SCHEMA_REVISION,
        "validator_boundary_revision": VALIDATOR_BOUNDARY_REVISION,
        "statistics_policy_revision": STATISTICS_POLICY_REVISION,
        "case_contracts": sorted(case_contracts.items()),
        "policy_files": dict(sorted(policy_hashes.items())),
    }
    return sha256_bytes(canonical_json(payload))


def evaluation_contract_identity(
    root: Path, case_records: Iterable[Mapping[str, Any]]
) -> tuple[dict[str, str], dict[str, str], str]:
    records = list(case_records)
    rubric_hashes = {
        str(case["template_id"]): rubric_sha256(root, str(case["template_id"]))
        for case in records
    }
    case_contracts = {
        str(case["case_id"]): compute_case_judge_contract_sha256(
            case, rubric_hash=rubric_hashes[str(case["template_id"])]
        )
        for case in records
    }
    policy = evaluation_policy_hashes(root)
    return case_contracts, policy, compute_evaluation_contract_fingerprint(case_contracts, policy)


def require_dataset_identity(
    record: Mapping[str, Any], current: Mapping[str, Any], *, where: str = "record"
) -> None:
    """Reject legacy, partial or mismatched dataset/evaluation identities."""
    fields = list(IDENTITY_FIELDS)
    if current.get("release_lock_sha256"):
        fields.append("release_lock_sha256")
    for field in fields:
        expected = current.get(field)
        if not record.get(field) or record.get(field) != expected:
            raise ValueError(
                f"{where}: missing or mismatched {field}; V8.3/V8.3_ds "
                "artifacts and caches cannot be reused in a V8.4.3 campaign"
            )


def require_runtime_artifact_identity(
    record: Mapping[str, Any],
    current: Mapping[str, Any],
    *,
    expected_campaign: str,
    where: str = "runtime artifact",
) -> None:
    """Reject records from another release or campaign in one fail-closed check.

    Runtime records can share the byte-identical input fingerprint across V8.4.0
    and V8.4.3.  Reuse still requires the dataset revision, evaluation contract,
    release lock, and campaign identifier to match simultaneously.
    """
    mismatches = [
        field
        for field in RUNTIME_IDENTITY_FIELDS
        if not record.get(field) or record.get(field) != current.get(field)
    ]
    if record.get("campaign_id") != expected_campaign:
        mismatches.append("campaign_id")
    if mismatches:
        raise ValueError(
            f"{where}: missing or mismatched {', '.join(mismatches)}; "
            "V8.4.0 run/evidence/result/Judge cache reuse is forbidden"
        )


def contains_placeholder(value: Any) -> bool:
    if isinstance(value, str):
        return any(token in value for token in PLACEHOLDER_TOKENS)
    if isinstance(value, Mapping):
        return any(contains_placeholder(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(contains_placeholder(item) for item in value)
    return False


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(_io_path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError(f"cannot read frozen identity file {path}") from error
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return value


def dataset_identity(root: Path) -> dict[str, str]:
    """Recompute both V8.4.3 identities and validate catalog, manifest and lock."""
    catalog = _load_json(root / "cases" / "catalog.json")
    manifest = _load_json(root / "artifacts" / "manifest.json")
    release = _load_json(root / "release_lock.json")
    current = {field: str(catalog.get(field, "")) for field in IDENTITY_FIELDS}
    if current["dataset_revision"] != DATASET_REVISION:
        raise ValueError(f"current dataset must have revision {DATASET_REVISION}")
    count = catalog.get("case_count", catalog.get("counts", {}).get("cases"))
    if catalog.get("condition") != "attack" or count != 288:
        raise ValueError("V8.4.3 catalog must contain exactly 288 attack cases")
    require_dataset_identity(catalog, current, where="cases/catalog.json")
    require_dataset_identity(manifest, current, where="artifacts/manifest.json")
    require_dataset_identity(release, current, where="release_lock.json")

    records = catalog.get("cases")
    if not isinstance(records, list) or len(records) != 288:
        raise ValueError("cases/catalog.json must contain 288 resolved cases")
    fingerprints: dict[str, str] = {}
    seen: set[str] = set()
    for case in records:
        if not isinstance(case, Mapping):
            raise ValueError("catalog contains a non-object case")
        case_id = str(case.get("case_id", ""))
        if not case_id or case_id in seen:
            raise ValueError(f"duplicate or empty case id: {case_id!r}")
        seen.add(case_id)
        provenance = case.get("provenance")
        if not isinstance(provenance, Mapping):
            raise ValueError(f"{case_id}: provenance missing")
        if case.get("dataset_revision") != DATASET_REVISION or provenance.get("dataset_revision") != DATASET_REVISION:
            raise ValueError(f"{case_id}: legacy or missing dataset revision")
        if case.get("evaluation_contract_fingerprint") != current["evaluation_contract_fingerprint"] or provenance.get("evaluation_contract_fingerprint") != current["evaluation_contract_fingerprint"]:
            raise ValueError(f"{case_id}: evaluation identity mismatch")
        prohibited = case.get("prohibited_value")
        binding = _binding(case)
        if prohibited is None or str(prohibited) == "":
            raise ValueError(f"{case_id}: prohibited_value missing")
        resolved_fields = {
            "target_memory_rule": case.get("target_memory_rule"),
            "binding": {
                name: binding.get(name)
                for name in (
                    "target_memory_rule",
                    "c_asr_condition",
                    "utdr_condition",
                    "atar_condition",
                    "trigger_task",
                )
            },
        }
        if contains_placeholder(resolved_fields):
            raise ValueError(f"{case_id}: unresolved placeholder in runtime Judge contract")
        if binding.get("target_memory_rule") != case.get("target_memory_rule"):
            raise ValueError(f"{case_id}: binding and top-level target memory rules differ")
        if binding.get("trigger_task") != case.get("trigger_input"):
            raise ValueError(f"{case_id}: binding trigger_task differs from delivered trigger input")
        if str(prohibited) not in str(binding.get("c_asr_condition", "")):
            raise ValueError(f"{case_id}: C-ASR does not name prohibited_value")
        files = case_input_files(root, case_id)
        actual = input_fingerprint(files)
        if provenance.get("input_fingerprint") != actual:
            raise ValueError(f"{case_id}: input fingerprint mismatch")
        for phase, field in (("exposure", "exposure_input"), ("trigger", "trigger_input")):
            expected_message = (str(case.get(field, "")) + "\n").encode("utf-8")
            if files.get(f"{phase}/message.txt") != expected_message:
                raise ValueError(f"{case_id}: {phase} message differs from catalog")
        fingerprints[case_id] = actual

    actual_dataset = compute_dataset_fingerprint(records, fingerprints)
    if actual_dataset != current["dataset_input_fingerprint"]:
        raise ValueError("dataset input fingerprint does not match visible bytes")
    if actual_dataset != EXPECTED_DATASET_INPUT_FINGERPRINT:
        raise ValueError("V8.4.3 visible inputs differ from the frozen trigger-parity release")

    case_contracts, policy_hashes, evaluation = evaluation_contract_identity(root, records)
    if evaluation != current["evaluation_contract_fingerprint"]:
        raise ValueError("evaluation contract fingerprint does not match current policy files")
    for case in records:
        case_id = str(case["case_id"])
        expected = case_contracts[case_id]
        provenance = case["provenance"]
        if case.get("judge_contract_sha256") != expected or provenance.get("judge_contract_sha256") != expected:
            raise ValueError(f"{case_id}: Judge contract hash mismatch")
    order = [str(case["case_id"]) for case in records]
    if release.get("case_order") != order:
        raise ValueError("release lock case order differs from the catalog")
    if release.get("case_input_fingerprints") != dict(sorted(fingerprints.items())):
        raise ValueError("release lock per-case input fingerprints differ")
    if release.get("case_judge_contracts") != dict(sorted(case_contracts.items())):
        raise ValueError("release lock per-case Judge contracts differ")
    if release.get("evaluation_policy_files") != policy_hashes:
        raise ValueError("release lock evaluation policy file hashes differ")
    verify_release_lock(root, release=release, require_sealed=False, verify_dataset=False)
    current["release_lock_sha256"] = str(release["release_lock_sha256"])
    return current


def release_code_hashes(root: Path) -> dict[str, str]:
    """Bind all shipped executable/configuration surfaces, never generated runs."""
    result = _hash_files(root, RELEASE_CODE_FILES, strict=True)
    for dirname in ("src", "workflow_quickrouter", "runtime", "experiments", "scripts", "configs", "docker"):
        directory = root / dirname
        for path in sorted(directory.rglob("*")):
            if not path.is_file() or any(part in {"__pycache__", ".venv", "outputs", "logs", "state"} for part in path.relative_to(root).parts):
                continue
            if ".local." in path.name:
                continue
            if path.suffix in {".py", ".ps1", ".sh", ".json", ".toml", ".yaml", ".yml"} or path.name.startswith("Dockerfile"):
                rel = path.relative_to(root).as_posix()
                result[rel] = sha256_file(safe_relative_path(root, rel))
    for path in root.glob("*.ps1"):
        result[path.name] = sha256_file(path)
    return dict(sorted(result.items()))


def public_data_hashes(root: Path) -> dict[str, str]:
    result = {"artifacts/manifest.json": sha256_file(root / "artifacts/manifest.json")}
    for dirname in ("data", "cases", "catalogs"):
        for path in sorted((root / dirname).rglob("*")):
            if path.is_file() and path.suffix in {".json", ".py"}:
                rel = path.relative_to(root).as_posix()
                result[rel] = sha256_file(safe_relative_path(root, rel))
    return dict(sorted(result.items()))


def build_release_lock(
    root: Path, dataset: Mapping[str, Any], *, case_ids: Iterable[str],
    case_input_fingerprints: Mapping[str, str], case_contracts: Mapping[str, str],
    policy_hashes: Mapping[str, str],
) -> dict[str, Any]:
    """Build a public source/input lock, deliberately independent of run evidence."""
    from .judges.atomic import response_format_contract
    lock = {
        "schema_version": PUBLIC_RELEASE_SCHEMA,
        "dataset_revision": DATASET_REVISION,
        "dataset_input_fingerprint": dataset["dataset_input_fingerprint"],
        "evaluation_contract_fingerprint": dataset["evaluation_contract_fingerprint"],
        "condition": "attack", "case_count": 288,
        "channel_counts": {"DPI-U": 96, "IPI-UNTRUSTED-ARTIFACT": 192},
        "case_order": list(case_ids),
        "case_input_fingerprints": dict(sorted(case_input_fingerprints.items())),
        "case_judge_contracts": dict(sorted(case_contracts.items())),
        "evaluation_policy_files": dict(sorted(policy_hashes.items())),
        "code_files": release_code_hashes(root),
        "dataset_manifests": public_data_hashes(root),
        "judge": {
            "contract_revision": JUDGE_CONTRACT_REVISION,
            "response_schema_sha256": {
                phase: sha256_bytes(canonical_json(response_format_contract(phase)))
                for phase in ("exposure", "trigger")
            },
            "qualification_lock_state": "campaign_local",
            "actual_model_policy": "Each campaign verifies its configured exact model; no fallback.",
        },
        "publication_boundary": "Source code and input data only; no experiment results or model qualification receipts.",
    }
    lock["release_lock_sha256"] = compute_release_lock_sha256(lock)
    return lock


def compute_release_lock_sha256(release: Mapping[str, Any]) -> str:
    payload = dict(release)
    payload.pop("release_lock_sha256", None)
    return sha256_bytes(canonical_json(payload))


def seal_release_lock(root: Path, resolved_actual_model: str) -> dict[str, Any]:
    raise ValueError("Public release integrity is immutable. Store model qualification in the new campaign directory.")


def verify_release_lock(
    root: Path, *, release: Mapping[str, Any] | None = None,
    require_sealed: bool = False, verify_dataset: bool = True,
) -> dict[str, Any]:
    lock = dict(release or _load_json(root / "release_lock.json"))
    if lock.get("schema_version") != PUBLIC_RELEASE_SCHEMA:
        raise ValueError("expected a public source/input release lock")
    if require_sealed:
        raise ValueError("A public source lock is not model qualification; verify the campaign-local model audit.")
    if lock.get("release_lock_sha256") != compute_release_lock_sha256(lock):
        raise ValueError("release lock self-hash is missing or invalid")
    expected = build_release_lock(
        root, lock, case_ids=lock.get("case_order", []),
        case_input_fingerprints=lock.get("case_input_fingerprints", {}),
        case_contracts=lock.get("case_judge_contracts", {}),
        policy_hashes=lock.get("evaluation_policy_files", {}),
    )
    if lock != expected:
        raise ValueError("public release source, input metadata, or evaluation schema changed")
    if verify_dataset:
        dataset_identity(root)
    return lock
