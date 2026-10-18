"""Public-input, CLI, and integrity regressions; no recorded model runs."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import socket

import pytest

from memharmbench_v84.cli import main
from memharmbench_v84.identity import (
    EXPECTED_DATASET_INPUT_FINGERPRINT, case_input_files, dataset_identity,
    release_code_hashes, require_runtime_artifact_identity, safe_relative_path,
    verify_release_lock,
)
from memharmbench_v84.public_validation import validate_public

ROOT = Path(__file__).resolve().parents[2]


def test_public_inputs_and_paired_tasks_validate():
    report = validate_public(ROOT)
    assert report["passed"] and report["model_requests"] == 0
    assert report["agent_visible_files"] == 2416
    assert report["dataset_input_fingerprint"] == EXPECTED_DATASET_INPUT_FINGERPRINT


@pytest.mark.parametrize("relative", ["../outside", "/absolute", "a/../b", "a\\b", "C:/outside", "a//b", "./a", ""])
def test_input_paths_reject_escape_and_nonportable_names(tmp_path, relative):
    with pytest.raises(ValueError):
        safe_relative_path(tmp_path, relative)


@pytest.mark.parametrize("case_id", ["../cases", "x/y", "x\\y", "C:escape", ""])
def test_case_reader_rejects_non_catalog_path(case_id):
    with pytest.raises(ValueError):
        case_input_files(ROOT, case_id)


def test_manifest_rejects_tampering_and_is_not_model_qualification():
    lock = json.loads((ROOT / "release_lock.json").read_text(encoding="utf-8"))
    changed = deepcopy(lock)
    changed["case_count"] = 1
    with pytest.raises(ValueError, match="self-hash"):
        verify_release_lock(ROOT, release=changed)
    with pytest.raises(ValueError, match="not model qualification"):
        verify_release_lock(ROOT, require_sealed=True)


def test_local_runtime_config_is_not_a_release_source_file(tmp_path):
    # The source inventory accepts user-specific ignored configuration while
    # the campaign separately freezes whichever file it actually uses.
    from memharmbench_v84 import identity
    from unittest.mock import patch
    config = tmp_path / "configs"
    config.mkdir()
    with patch.object(identity, "RELEASE_CODE_FILES", ()):
        before = release_code_hashes(tmp_path)
        (config / "experiment.local.json").write_text('{"model": "test"}')
        (config / "experiment.local.yaml").write_text('model: test\n')
        assert release_code_hashes(tmp_path) == before
        (config / "example.json").write_text('{"model": "example"}')
        assert "configs/example.json" in release_code_hashes(tmp_path)


def test_cli_commands_are_offline_and_do_not_emit_outcomes(monkeypatch, capsys):
    def forbidden(*args, **kwargs):
        raise AssertionError("offline commands must not use network sockets")
    monkeypatch.setattr(socket, "create_connection", forbidden)
    for command in [["catalog", "--limit", "2"], ["inspect", "i_u3_embedded_convention__commerce_orders"], ["demo"]]:
        assert main(["--root", str(ROOT), *command]) == 0
        text = capsys.readouterr().out
        assert str(ROOT) not in text
        assert '"final_pmir"' not in text and '"judge_source"' not in text


def test_validate_only_writes_when_explicitly_requested(tmp_path, capsys):
    assert main(["--root", str(ROOT), "validate"]) == 0
    assert not (ROOT / "reports").exists()
    output = tmp_path / "check.json"
    assert main(["--root", str(ROOT), "validate", "--output", str(output)]) == 0
    assert json.loads(output.read_text())["passed"] is True
    original = (ROOT / "cases/catalog.json").read_bytes()
    with pytest.raises(SystemExit):
        main(["--root", str(ROOT), "validate", "--output", str(ROOT / "cases/catalog.json")])
    assert (ROOT / "cases/catalog.json").read_bytes() == original


def test_runtime_evidence_requires_all_current_identity_axes():
    current = {"dataset_revision": "8.4.3", "dataset_input_fingerprint": "a" * 64,
               "evaluation_contract_fingerprint": "b" * 64, "release_lock_sha256": "c" * 64}
    record = {**current, "campaign_id": "synthetic-campaign"}
    require_runtime_artifact_identity(record, current, expected_campaign="synthetic-campaign")
    for field in record:
        changed = dict(record)
        changed[field] = "wrong"
        with pytest.raises(ValueError, match=field):
            require_runtime_artifact_identity(changed, current, expected_campaign="synthetic-campaign")
