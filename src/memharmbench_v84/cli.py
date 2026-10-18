"""Offline, read-only exploration of the public benchmark inputs."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

DEFAULT_CASE = "i_u3_embedded_convention__commerce_orders"


def _root(value: str | None) -> Path:
    if value:
        root = Path(value).expanduser().resolve()
    elif os.environ.get("MEMHARMBENCH_DATA_ROOT"):
        root = Path(os.environ["MEMHARMBENCH_DATA_ROOT"]).expanduser().resolve()
    else:
        source_root = Path(__file__).resolve().parents[2]
        root = source_root if (source_root / "cases/catalog.json").is_file() else Path.cwd()
    if not (root / "cases/catalog.json").is_file():
        raise ValueError("Run inside the cloned repository, install it with pip install -e ., or provide --root.")
    return root


def _catalog(root: Path) -> list[dict[str, Any]]:
    return json.loads((root / "cases/catalog.json").read_text(encoding="utf-8"))["cases"]


def _inspect(root: Path, case_id: str) -> dict[str, Any]:
    from .identity import case_input_files
    matches = [case for case in _catalog(root) if case["case_id"] == case_id]
    if len(matches) != 1:
        raise ValueError(f"Unknown case identifier: {case_id}")
    case = matches[0]
    files = case_input_files(root, case_id)
    return {
        key: case[key] for key in (
            "case_id", "template_id", "objective_id", "context_id", "channel", "victim",
            "exposure_input", "trigger_input", "target_memory_rule", "prohibited_value",
        )
    } | {"input_files": sorted(files), "note": "Input specification only; no model was called and no outcome is shown."}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="memharmbench", description="Explore and validate frozen benchmark inputs without model calls.")
    parser.add_argument("--root", help="Public repository/data root")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("identity", help="Verify and show input/evaluation/source identities")
    validate = sub.add_parser("validate", help="Validate all published inputs and source hashes")
    validate.add_argument("--output", type=Path, help="Explicitly write the validation report to this path")
    catalog = sub.add_parser("catalog", help="List input cases")
    catalog.add_argument("--limit", type=int, default=12)
    catalog.add_argument("--json", action="store_true")
    inspect = sub.add_parser("inspect", help="Show one case's inputs and file inventory")
    inspect.add_argument("case_id")
    sub.add_parser("demo", help="Show a short offline walkthrough of the input release")
    args = parser.parse_args(argv)
    try:
        root = _root(args.root)
        if args.command == "identity":
            from .identity import dataset_identity
            print(json.dumps(dataset_identity(root), indent=2))
        elif args.command == "validate":
            from .public_validation import validate_public
            report = validate_public(root)
            payload = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
            if args.output:
                destination = args.output.resolve()
                from .identity import verify_release_lock
                lock = verify_release_lock(root, verify_dataset=False)
                protected = set(lock["code_files"]) | set(lock["dataset_manifests"]) | {"release_lock.json"}
                if destination.is_relative_to(root.resolve()):
                    rel = destination.relative_to(root.resolve()).as_posix()
                    if rel in protected or rel.startswith("artifacts/"):
                        raise ValueError("validation output would overwrite a frozen release file")
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(payload, encoding="utf-8", newline="\n")
            print(payload, end="")
        elif args.command == "catalog":
            if args.limit < 0:
                raise ValueError("--limit must be non-negative")
            cases = _catalog(root)
            rows = [{key: c[key] for key in ("case_id", "channel", "objective_id", "context_id")} for c in cases[:args.limit]]
            if args.json:
                print(json.dumps({"total": len(cases), "cases": rows}, indent=2))
            else:
                print(f"MemHarmBench | {len(cases)} cases | 24 templates | 12 contexts")
                for row in rows:
                    print(f"{row['case_id']}  [{row['channel']}]")
        elif args.command == "inspect":
            print(json.dumps(_inspect(root, args.case_id), indent=2, ensure_ascii=False))
        else:
            case = _inspect(root, DEFAULT_CASE)
            print("MEMHARMBENCH  |  OFFLINE INPUT DEMO")
            print("288 cases / 24 templates / 12 contexts / 12 objectives")
            print("\n01  SELECT A CASE\n    " + case["case_id"])
            print("\n02  EXPOSURE\n    Untrusted task material carries a persistent preference.")
            print("\n03  MEMORY BOUNDARY\n    Native persistent memory crosses into a fresh session.")
            print("\n04  TRIGGER INPUT\n    " + case["trigger_input"])
            print("\n05  AUDIT\n    Separate memory retention, target adoption, task disruption, and trajectory anomalies.")
            print(f"\n{len(case['input_files'])} input files available. No model calls; no experiment outcomes.")
        return 0
    except (ValueError, OSError, KeyError) as error:
        parser.exit(2, f"memharmbench: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
