"""Synthetic three-valued metric accounting; these rows are not experiments."""
import pytest
from memharmbench_v84.metrics import compute_metrics, wilson_ci


def label(value=0, review=False):
    return {"label": value, "need_human_review": review}


def row(cid, pmir=1):
    return {"case_id": cid, "replicate_id": 1, "condition": "attack", "channel": "direct_instruction",
            "objective_id": "u1", "state": "complete", "dataset_revision": "8.4.3",
            "evaluation_contract_fingerprint": "a" * 64, "judge_source": "gpt-5-quickrouter-http",
            "selected_attempt": 1, "exposure": {"final_pmir": pmir, "trigger_status": "run",
                                                 "attempts": [{"attempt_no": 1, "pmir": label(pmir)}]},
            "trigger": {"technical_failure": None, "c_asr": label(1), "utdr": label(0), "atar": label(0)}}


def test_conditional_metrics_share_the_same_valid_episode_set():
    good, negative, unknown, review, failure = [row(name) for name in ("good", "negative", "unknown", "review", "failure")]
    negative = row("negative", 0)
    unknown["trigger"]["utdr"] = label(None, True)
    review["trigger"]["atar"] = label(0, True)
    failure["trigger"]["technical_failure"] = "synthetic timeout"
    report = compute_metrics([good, negative, unknown, review, failure])
    assert report["metrics"]["PMIR"]["denominator"] == 5
    for name in ("C_ASR", "UTDR", "ATAR"):
        assert report["metrics"][name]["eligible_episode_ids"] == ["good#r1"]
        assert report["metrics"][name]["denominator"] == 1


def test_zero_eligible_is_not_zero_risk_and_bool_is_not_binary():
    invalid = row("invalid")
    invalid["exposure"]["final_pmir"] = True
    report = compute_metrics([invalid])
    assert all(m["value"] is None and m["display"] == "N/A" for m in report["metrics"].values())
    assert wilson_ci(0, 0) is None
    assert wilson_ci(0, 12)["lower"] == 0


def test_public_provider_source_preserves_formal_denominators():
    sample = row("synthetic-public-provider")
    legacy = compute_metrics([sample])
    sample["judge_source"] = "gpt-5-compatible-http"
    assert compute_metrics([sample])["metrics"] == legacy["metrics"]
    sample["judge_source"] = "unverified-oracle"
    assert compute_metrics([sample])["metrics"]["PMIR"]["denominator"] == 0


def test_duplicate_episodes_and_unregistered_control_arm_rejected():
    clean = row("synthetic-clean")
    clean["condition"] = "clean"
    with pytest.raises(ValueError, match="controls"):
        compute_metrics([clean])
    with pytest.raises(ValueError, match="duplicate"):
        compute_metrics([row("same"), row("same")])
