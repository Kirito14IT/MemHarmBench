"""Strict loader for the frozen MemHarmBench V8.4.3 attack release.

The public release distributes all frozen V8.4.3 inputs directly. The package-level legacy compiler is deliberately
disabled so it cannot create a clean/control tree or overwrite the release.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

from ..identity import DATASET_REVISION
from ..schema import ResolvedCase


def build_all(*args: Any, **kwargs: Any) -> dict[str, Any]:
    raise RuntimeError(
        "V8.4.3 is immutable and attack-only; create an explicitly versioned dataset to modify frozen inputs"
    )


def build_paired(*args: Any, **kwargs: Any) -> dict[str, Any]:
    raise RuntimeError("V8.4.3 does not define a clean/control campaign")


def load_case_catalog(root: Path) -> list[ResolvedCase]:
    """Load cases/catalog.json and reconstruct ResolvedCase objects.

    V8.4.3 is attack-only. Every resolved oracle and both identity hashes must be
    present in the catalog; legacy V8.3/V8.3_ds objects are rejected rather than
    completed with inferred defaults. Loading is non-destructive so callers may
    retain the parsed catalog for an independent identity audit.
    """
    raw = json.loads((root / "cases" / "catalog.json").read_text(encoding="utf-8"))
    declared_condition = raw.get("condition")
    if declared_condition != "attack":
        raise ValueError(f"{root}: V8.4.3 cases/catalog.json must declare attack")
    if raw.get("dataset_revision") != DATASET_REVISION:
        raise ValueError(f"{root}: cases/catalog.json must declare {DATASET_REVISION}")
    evaluation = raw.get("evaluation_contract_fingerprint")
    if not isinstance(evaluation, str) or re.fullmatch(r"[0-9a-f]{64}", evaluation) is None:
        raise ValueError(f"{root}: evaluation_contract_fingerprint must be a 64-hex digest")
    cases = []
    for item in raw["cases"]:
        binding_raw = item["binding"]
        from ..schema import ObjectiveContextBinding as B

        provenance = item.get("provenance", {})
        required = {
            "dataset_revision": item.get("dataset_revision"),
            "prohibited_value": item.get("prohibited_value"),
            "permission_condition": item.get("permission_condition"),
            "source_authority": item.get("source_authority"),
            "judge_contract_sha256": item.get("judge_contract_sha256"),
            "evaluation_contract_fingerprint": item.get(
                "evaluation_contract_fingerprint"
            ),
            "evidence_dependencies": item.get("evidence_dependencies"),
        }
        if required["dataset_revision"] != DATASET_REVISION:
            raise ValueError(f"{item.get('case_id')}: legacy dataset revision")
        if not isinstance(required["prohibited_value"], str) or not required["prohibited_value"]:
            raise ValueError(f"{item.get('case_id')}: prohibited_value is required")
        for field in (
            "permission_condition",
            "source_authority",
            "judge_contract_sha256",
            "evaluation_contract_fingerprint",
        ):
            if not isinstance(required[field], str) or not required[field]:
                raise ValueError(f"{item.get('case_id')}: {field} is required")
        if re.fullmatch(r"[0-9a-f]{64}", required["judge_contract_sha256"]) is None:
            raise ValueError(f"{item.get('case_id')}: invalid judge_contract_sha256")
        if required["evaluation_contract_fingerprint"] != evaluation:
            raise ValueError(f"{item.get('case_id')}: evaluation fingerprint mismatch")
        if not isinstance(required["evidence_dependencies"], dict):
            raise ValueError(f"{item.get('case_id')}: evidence_dependencies is required")
        if provenance.get("condition") != declared_condition:
            raise ValueError(f"{item.get('case_id')}: provenance condition mismatch")

        binding = B(
            binding_id=binding_raw["binding_id"],
            objective_id=binding_raw["objective_id"],
            context_id=binding_raw["context_id"],
            exposure_reference=binding_raw["exposure_reference"],
            attacker_role=binding_raw["attacker_role"],
            business_background=binding_raw["business_background"],
            trigger_task=binding_raw["trigger_task"],
            target_memory_rule=binding_raw["target_memory_rule"],
            protected_entity=binding_raw["protected_entity"],
            harm_outcome=binding_raw["harm_outcome"],
            c_asr_condition=binding_raw["c_asr_condition"],
            utdr_condition=binding_raw["utdr_condition"],
            atar_condition=binding_raw["atar_condition"],
            entities=dict(binding_raw["entities"]),
        )
        cases.append(
            ResolvedCase(
                case_id=item["case_id"],
                template_id=item["template_id"],
                objective_id=item["objective_id"],
                context_id=item["context_id"],
                channel=item["channel"],
                victim=item["victim"],
                seed=item["seed"],
                provenance=dict(provenance),
                binding=binding,
                exposure_input=item["exposure_input"],
                carrier_artifact=item["carrier_artifact"],
                trigger_input=item["trigger_input"],
                trigger_artifact=item["trigger_artifact"],
                payload_text=item["payload_text"],
                payload_value=item["payload_value"],
                target_memory_rule=item["target_memory_rule"],
                protected_entity=item["protected_entity"],
                harm_outcome=item["harm_outcome"],
                entities=dict(item["entities"]),
                dataset_revision=required["dataset_revision"],
                prohibited_value=required["prohibited_value"],
                permission_condition=required["permission_condition"],
                source_authority=required["source_authority"],
                judge_contract_sha256=required["judge_contract_sha256"],
                evaluation_contract_fingerprint=required[
                    "evaluation_contract_fingerprint"
                ],
                evidence_dependencies=dict(required["evidence_dependencies"]),
            )
        )
    return cases
