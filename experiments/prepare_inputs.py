"""Check downloaded ablation inputs without preparing or rewriting data.

The frozen inputs and identities come from ``memharmbench download``. This
historical maintenance entry point now reports read-only integrity checks.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from memharmbench_v84.compiler import load_case_catalog
from memharmbench_v84.identity import (
    canonical_json,
    case_input_files,
    evaluation_contract_identity,
    input_fingerprint,
    require_dataset_identity,
)


def check_inputs(root: Path) -> list[dict[str, object]]:
    summaries: list[dict[str, object]] = []
    for name in ("semantic_alignment", "exposure_distance"):
        data = root / "experiments" / name / "dataset"
        catalog = json.loads((data / "cases/catalog.json").read_text(encoding="utf-8"))
        identity = json.loads((data / "identity.json").read_text(encoding="utf-8"))
        require_dataset_identity(catalog, identity, where=f"{name}/cases/catalog.json")
        cases = load_case_catalog(data)
        for case in cases:
            actual = input_fingerprint(case_input_files(data, case.case_id))
            if actual != case.provenance.get("input_fingerprint"):
                raise ValueError(f"{name}/{case.case_id}: input fingerprint changed")
        visible = {
            path.relative_to(data).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted((data / "artifacts").rglob("*"))
            if path.is_file()
        }
        inputs = hashlib.sha256(canonical_json(visible)).hexdigest()
        if inputs != identity["dataset_input_fingerprint"]:
            raise ValueError(f"{name}: downloaded artifact inventory changed")
        _, _, evaluation = evaluation_contract_identity(root, catalog["cases"])
        if evaluation != identity["evaluation_contract_fingerprint"]:
            raise ValueError(f"{name}: evaluation contract changed")
        summaries.append({"experiment": name, "cases": len(cases), "passed": True})
    return summaries


def main() -> int:
    print(json.dumps(check_inputs(ROOT), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
