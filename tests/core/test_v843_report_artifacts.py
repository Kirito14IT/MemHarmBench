"""Regression tests for V8.4.3 formal quality accounting and report artifacts."""
from __future__ import annotations

import csv
import json
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from memharmbench_v84.metrics import compute_metrics, require_common_trigger_episode_ids
from memharmbench_v84.pilot_report import write_full_report


FINGERPRINT = "a" * 64


def metric(value: int | None = 0, review: bool = False, reason: str = "derived") -> dict:
    return {"label": value, "need_human_review": review, "reason": reason}


def row(case_id: str, pmir: int = 1) -> dict:
    return {
        "case_id": case_id, "replicate_id": 1, "condition": "attack",
        "channel": "direct_instruction", "objective_id": "u1", "state": "complete",
        "dataset_revision": "8.4.3", "evaluation_contract_fingerprint": FINGERPRINT,
        "judge_source": "gpt-5-quickrouter-http", "selected_attempt": 1,
        "exposure": {
            "final_pmir": pmir, "trigger_status": "run" if pmir else "skipped",
            "attempts": [{"attempt_no": 1, "pmir": metric(pmir)}],
        },
        "trigger": ({
            "technical_failure": None, "c_asr": metric(1),
            "utdr": metric(0), "atar": metric(0),
        } if pmir else None),
    }


def report_rows() -> list[dict]:
    good, skipped, unknown, review, failed = [
        row(name) for name in ("good", "skipped", "unknown", "review", "failed")
    ]
    skipped = row("skipped", 0)
    unknown["state"] = "judge_unresolved"
    unknown["failure"] = {"stage": "trigger", "kind": "judge", "technical": False}
    unknown["trigger"]["utdr"] = metric(None, True, "semantic predicate unknown")
    review["state"] = "judge_unresolved"
    review["failure"] = {"stage": "trigger", "kind": "judge", "technical": False}
    review["trigger"]["atar"] = metric(0, True, "validator downgraded evidence")
    failed["state"] = "technical_unresolved"
    failed["failure"] = {"stage": "trigger", "kind": "technical", "technical": True}
    failed["trigger"]["technical_failure"] = "timeout"
    return [good, skipped, unknown, review, failed]


def test_quality_categories_are_separate_and_do_not_change_denominators():
    result = compute_metrics(report_rows())
    assert result["metrics"]["PMIR"]["denominator"] == 5
    assert all(result["metrics"][name]["denominator"] == 1
               for name in ("C_ASR", "UTDR", "ATAR"))
    assert result["counts"]["per_metric_null"] == {
        "PMIR": 0, "C_ASR": 0, "UTDR": 1, "ATAR": 1,
    }
    assert result["counts"]["technical_failures"] == 1
    assert result["quality"]["technical_failure"]["per_metric"]["C_ASR"]["count"] == 1
    assert result["counts"]["judge_insufficient"] == 2
    assert result["quality"]["judge_insufficient"]["per_metric"] == {
        "PMIR": 0, "C_ASR": 0, "UTDR": 1, "ATAR": 1,
    }
    assert result["counts"]["human_review"] == 2
    assert result["need_human_review"] == result["human_review"]


def test_unselected_exposure_chain_is_a_final_pmir_null():
    pending = row("pending", 0)
    pending["selected_attempt"] = None
    pending["exposure"]["final_pmir"] = None
    pending["exposure"]["trigger_status"] = "pending"
    pending["state"] = "judge_unresolved"
    pending["failure"] = {"stage": "exposure_a2", "kind": "judge", "technical": False}
    result = compute_metrics([pending])
    assert result["metrics"]["PMIR"]["denominator"] == 0
    assert result["quality"]["per_metric_null"]["PMIR"]["episode_ids"] == ["pending#r1"]
    assert result["quality"]["judge_insufficient"]["per_metric"]["PMIR"] == 1


def test_common_trigger_episode_id_assertion_is_explicit():
    metrics = compute_metrics([row("shared")])["metrics"]
    require_common_trigger_episode_ids(metrics)
    metrics["UTDR"]["eligible_episode_ids"] = ["different#r1"]
    with pytest.raises(ValueError, match="identical eligible episode IDs"):
        require_common_trigger_episode_ids(metrics)


def test_write_full_report_emits_only_formal_full288_artifacts(tmp_path):
    rows = report_rows()
    expected = [item["case_id"] for item in rows]
    out = tmp_path / "formal"
    summary = write_full_report(tmp_path, rows, expected, report_dir=out)
    names = {
        "full288_case_status.csv", "full288_case_status.json",
        "full288_metrics.md", "full288_metrics.csv", "full288_unresolved.json",
    }
    assert {path.name for path in out.iterdir()} == names
    assert summary["quality_counts"] == {
        "per_metric_null": {"PMIR": 0, "C_ASR": 0, "UTDR": 1, "ATAR": 1},
        "technical_failure": 1, "judge_insufficient": 2, "human_review": 2,
    }
    status = json.loads((out / "full288_case_status.json").read_text(encoding="utf-8"))
    assert status["expected_cases"] == 5
    assert [item["case_id"] for item in status["rows"]] == expected
    unresolved = json.loads((out / "full288_unresolved.json").read_text(encoding="utf-8"))
    assert unresolved["judge_insufficient"]["count"] == 2
    assert unresolved["technical_failure"]["count"] == 1
    with (out / "full288_metrics.csv").open(encoding="utf-8-sig", newline="") as handle:
        metrics = list(csv.DictReader(handle))
    overall_utdr = next(item for item in metrics
                        if item["scope"] == "overall" and item["metric"] == "UTDR")
    assert overall_utdr["denominator"] == "1"
    assert overall_utdr["null"] == "1"
    markdown = (out / "full288_metrics.md").read_text(encoding="utf-8")
    assert "# V8.4.3 full-288 formal metrics" in markdown
    assert "Judge insufficient" in markdown
    assert not any("pilot" in name.lower() for name in names)
