"""Read-only checks for the self-contained public input release."""
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
from typing import Any

from .compiler import load_case_catalog
from .identity import dataset_identity, safe_relative_path, sha256_file


def validate_public(root: Path) -> dict[str, Any]:
    identity = dataset_identity(root)
    raw = json.loads((root / "cases/catalog.json").read_text(encoding="utf-8"))
    cases = raw["cases"]
    loaded = load_case_catalog(root)
    if len(loaded) != 288 or len({case.case_id for case in loaded}) != 288:
        raise ValueError("expected 288 unique resolved cases")
    counts = {
        "cases": len(cases), "templates": len({case["template_id"] for case in cases}),
        "contexts": len({case["context_id"] for case in cases}),
        "objectives": len({case["objective_id"] for case in cases}),
    }
    if counts != {"cases": 288, "templates": 24, "contexts": 12, "objectives": 12}:
        raise ValueError("case/template/context/objective coverage changed")
    if any(value != 12 for value in Counter(case["template_id"] for case in cases).values()):
        raise ValueError("each template must cover all 12 contexts")
    channels = dict(Counter(case["channel"] for case in cases))
    if channels != {"direct_instruction": 96, "indirect_content": 192}:
        raise ValueError("channel coverage changed")
    manifest = json.loads((root / "artifacts/manifest.json").read_text(encoding="utf-8"))
    recorded = {}
    for row in manifest["files"]:
        rel = row["path"]
        if rel in recorded:
            raise ValueError("duplicate input manifest path")
        actual = sha256_file(safe_relative_path(root / "artifacts", rel))
        if actual != row["sha256"]:
            raise ValueError(f"input bytes changed: {rel}")
        recorded[rel] = actual
    actual_paths = {
        path.relative_to(root / "artifacts").as_posix() for case in cases
        for path in (root / "artifacts" / case["case_id"]).rglob("*") if path.is_file()
    }
    if actual_paths != set(recorded) or manifest["file_count"] != len(recorded):
        raise ValueError("input manifest inventory changed")
    by_id = {case["case_id"]: case for case in cases}
    for case in cases:
        saved = json.loads(safe_relative_path(root, f"cases/{case['case_id']}.json").read_text(encoding="utf-8"))
        if saved != case:
            raise ValueError(f"case file differs from catalog: {case['case_id']}")
        if case["channel"] == "direct_instruction":
            paired = case["case_id"].replace("d_", "i_", 1).replace("standing_instruction", "embedded_convention")
            if paired not in by_id:
                raise ValueError("missing DPI/IPI pair")
            def workspace(cid: str) -> dict[str, bytes]:
                base = root / "artifacts" / cid / "trigger/workspace"
                return {p.relative_to(base).as_posix(): p.read_bytes() for p in base.rglob("*") if p.is_file()}
            if workspace(case["case_id"]) != workspace(paired):
                raise ValueError("paired trusted trigger workspaces differ")
    return {
        "passed": True, "check": "public input and source integrity", **counts,
        "channels": channels, "agent_visible_files": len(recorded),
        "paired_trusted_workspaces": 96, **identity, "model_requests": 0,
    }
