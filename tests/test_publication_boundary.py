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


def test_approved_standalone_figures_are_public():
    root = _path.parents[1]
    for relative in audit.APPROVED_FIGURE_PDFS:
        assert audit.inspect_file(root / relative, root) == []


def test_figure_exception_does_not_allow_other_pdfs(tmp_path):
    path = tmp_path / 'assets' / 'full-paper.pdf'
    path.parent.mkdir()
    path.write_bytes(b'%PDF-1.7\nSynthetic document')
    assert any('excluded document' in issue['reason'] for issue in audit.inspect_file(path, tmp_path))


def test_approved_filename_does_not_allow_replaced_content(tmp_path):
    path = tmp_path / next(iter(audit.APPROVED_FIGURE_PDFS))
    path.parent.mkdir()
    path.write_bytes(b'%PDF-1.7\nUnapproved replacement')
    assert any('differs from the approved' in issue['reason'] for issue in audit.inspect_file(path, tmp_path))
