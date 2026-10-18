"""Synthetic checks of the publication guard; never uses experiment records."""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

_path = Path(__file__).resolve().parents[1] / 'scripts/audit_release.py'
_spec = spec_from_file_location('release_audit', _path)
audit = module_from_spec(_spec)
_spec.loader.exec_module(audit)


def test_input_log_is_not_an_execution_log(tmp_path):
    path = tmp_path / 'artifacts' / 'sample' / 'imports' / 'workflow.log'
    path.parent.mkdir(parents=True)
    path.write_text('Synthetic business task input', encoding='utf-8')
    assert audit.inspect_file(path, tmp_path) == []


def test_results_directory_is_excluded(tmp_path):
    path = tmp_path / 'outputs' / 'run.json'
    path.parent.mkdir()
    path.write_text('{}', encoding='utf-8')
    assert any('output' in issue['reason'] for issue in audit.inspect_file(path, tmp_path))


def test_secret_reports_location_without_value(tmp_path):
    path = tmp_path / 'config.py'
    simulated = 'ghp_' + ('a' * 36)
    path.write_text('credential = ' + repr(simulated), encoding='utf-8')
    issues = audit.inspect_file(path, tmp_path)
    assert any(issue['reason'] == 'GitHub token' for issue in issues)
    assert simulated not in repr(issues)


def test_runtime_json_is_not_public_input(tmp_path):
    path = tmp_path / 'input.json'
    path.write_text('{"final_pmir": 1}', encoding='utf-8')
    assert any('observed' in issue['reason'] for issue in audit.inspect_file(path, tmp_path))
