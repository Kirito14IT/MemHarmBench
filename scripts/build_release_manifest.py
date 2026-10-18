"""Maintainer command: freeze the public inputs and code without execution evidence.

This command updates only identity metadata. It never rewrites an agent-visible
artifact, performs a model call, or imports an earlier research workspace.
Run after deliberate source changes, then run ``memharmbench validate``.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from memharmbench_v84.identity import (
    DATASET_REVISION, EXPECTED_DATASET_INPUT_FINGERPRINT, build_release_lock,
    case_input_files, compute_dataset_fingerprint, evaluation_contract_identity,
    input_fingerprint, sha256_file,
)


def write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")


def main() -> int:
    catalog = json.loads((ROOT / "cases/catalog.json").read_text(encoding="utf-8"))
    records = catalog["cases"]
    if len(records) != 288 or len({case["case_id"] for case in records}) != 288:
        raise ValueError("expected the 288-case source catalog")
    fingerprints = {case["case_id"]: input_fingerprint(case_input_files(ROOT, case["case_id"])) for case in records}
    actual = compute_dataset_fingerprint(records, fingerprints)
    if actual != EXPECTED_DATASET_INPUT_FINGERPRINT:
        raise ValueError("agent-visible inputs changed; this command cannot bless a modified dataset")
    contracts, policies, evaluation = evaluation_contract_identity(ROOT, records)
    identity = {"dataset_revision": DATASET_REVISION, "dataset_input_fingerprint": actual,
                "evaluation_contract_fingerprint": evaluation}
    for case in records:
        provenance = case["provenance"]
        # Public provenance identifies the shipped input, not an inaccessible local build.
        for name in list(provenance):
            if name.startswith("source_") or name in {"template_source", "context_source", "binding_source", "visible_input_source_root", "visible_input_source_revision", "template_source_hash", "context_source_hash"}:
                provenance.pop(name)
        provenance["compiler"] = "memharmbench_v84.public_release"
        provenance["template_path"] = f"data/templates/{case['template_id']}.json"
        provenance["context_path"] = f"data/contexts/{case['context_id']}.json"
        provenance["visible_inputs_frozen"] = True
        provenance["input_fingerprint"] = fingerprints[case["case_id"]]
        case.update(dataset_revision=DATASET_REVISION, evaluation_contract_fingerprint=evaluation,
                    judge_contract_sha256=contracts[case["case_id"]])
        provenance.update(dataset_revision=DATASET_REVISION, evaluation_contract_fingerprint=evaluation,
                          judge_contract_sha256=contracts[case["case_id"]])
        write(ROOT / "cases" / f"{case['case_id']}.json", case)
    for name in list(catalog):
        if name.startswith("source_"):
            catalog.pop(name)
    catalog.update(identity)
    write(ROOT / "cases/catalog.json", catalog)
    manifest = json.loads((ROOT / "artifacts/manifest.json").read_text(encoding="utf-8"))
    manifest.update(identity)
    write(ROOT / "artifacts/manifest.json", manifest)
    full = json.loads((ROOT / "catalogs/full288_attack.json").read_text(encoding="utf-8"))
    full.pop("campaigns", None)
    full.update(identity)
    full["case_input_fingerprints"] = dict(sorted(fingerprints.items()))
    full["case_judge_contracts"] = dict(sorted(contracts.items()))
    write(ROOT / "catalogs/full288_attack.json", full)
    smoke_ids = ["d_w4_standing_instruction__calendar_meetings", "i_w4_embedded_convention__calendar_meetings",
                 "d_u3_standing_instruction__commerce_orders", "i_u3_embedded_convention__commerce_orders"]
    write(ROOT / "catalogs/smoke4.json", {
        "schema_version": "memharmbench-public-smoke-inputs-1", **identity,
        "condition": "attack", "cohort": "smoke4", "repetitions": [1], "case_ids": smoke_ids,
        "case_input_fingerprints": {cid: fingerprints[cid] for cid in smoke_ids},
        "case_judge_contracts": {cid: contracts[cid] for cid in smoke_ids},
        "description": "Optional input subset for a new installation's own integration checks; no execution evidence included.",
    })
    write(ROOT / "catalogs/input_provenance.json", {
        "schema_version": "memharmbench-public-input-provenance-1", **identity,
        "cases": [{"case_id": c["case_id"], "input_fingerprint": fingerprints[c["case_id"]],
                   "template_sha256": sha256_file(ROOT / "data/templates" / f"{c['template_id']}.json"),
                   "context_sha256": sha256_file(ROOT / "data/contexts" / f"{c['context_id']}.json")}
                  for c in records],
    })
    write(ROOT / "data/dataset_metadata.json", {
        "schema_version": "memharmbench-public-metadata-1", **identity,
        "design": "Attack-only persistent-memory injection benchmark; no clean or control campaign.",
        "counts": {"objectives": 12, "templates": 24, "contexts": 12, "attack_cases": 288, "dpi_u": 96, "ipi": 192},
        "conditions": ["attack"], "paired_trusted_trigger_workspaces": 96,
        "distribution": "Frozen input data, evaluation code, and operation contracts; no execution results.",
    })
    lock = build_release_lock(ROOT, identity, case_ids=[c["case_id"] for c in records],
                              case_input_fingerprints=fingerprints, case_contracts=contracts, policy_hashes=policies)
    write(ROOT / "release_lock.json", lock)
    print(json.dumps({**identity, "release_lock_sha256": lock["release_lock_sha256"], "case_count": 288}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
