"""Audit the public source tree without reading or displaying credential values.

This is a publication boundary check, not an experiment or model evaluation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
GENERATED = {'.git', '.venv', '__pycache__', '.pytest_cache', 'build', 'dist'}
FORBIDDEN_DIRS = {
    'outputs', 'results', 'reports', 'logs', 'runs', 'state', 'qualification',
    'judge_cache', 'worklogs', 'recovery', 'supervision', 'private',
}
FORBIDDEN_SUFFIXES = {'.pdf', '.tex', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', '.pem', '.key'}
# Only these author-approved, standalone figures may cross the document boundary.
# Full manuscripts and other research documents remain excluded.
APPROVED_FIGURE_PDFS = {
    'assets/paper-figure-1-en.pdf': 'a05c286bf1a60bec2779d3d6f0b71f278240dab423d5ef13eab63b03cc80c2dc',
    'assets/paper-figure-1-zh.pdf': '3ae8d3cc037d7397e20b92decc17b2f6b06dfc5b0fbbd95e200e8c01ae2bd4f7',
    'assets/paper-figure-2-en.pdf': '02a03b3aa23bbf3641555dc49e232f05fdf800ef928909ca6aeca203b9246373',
    'assets/paper-figure-2-zh.pdf': '24bc0a5e9ade039c81cfdf117d19bee5829e2d29de00155974d520811fe9b6bd',
    'assets/paper-figure-3-en.pdf': '33404a8a07d29c7779d68c517c5052d2e39aa8e031f33573efb03d7be3677871',
    'assets/paper-figure-3-zh.pdf': '55f82316332315e11e7b3ce2d3b2cb6d85329303c3699d86077715c4d806ae66',
}
FORBIDDEN_NAMES = {
    'tokens.json', 'campaign_lock.json', 'gateway_image_lock.json',
    'bridge_config_snapshot.json', 'chain_result.json', 'launch_verification.json',
    'timing_basis.json', 'route_resolution.json', 'offline_check.json',
    'task_plan.md', 'notes.md', 'session.md',
}
SECRET_PATTERNS = {
    'OpenAI/Anthropic key': re.compile(r'\bsk-(?:proj-|ant-)?[A-Za-z0-9_-]{24,}\b'),
    'GitHub token': re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})\b'),
    'Google API key': re.compile(r'\bAIza[0-9A-Za-z_-]{30,}\b'),
    'private key block': re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
}
LOCAL_PATTERNS = {
    'personal Windows home': re.compile(r'(?i)\b[A-Z]:[\\/]Users[\\/](?!Public\b|YOUR_USERNAME\b|<)[^\s"<>]+'),
    'research checkout path': re.compile(r'(?i)\b[A-Z]:[\\/]github[\\/]paper[\\/]'),
    'private provider route': re.compile(r'api\.(?:quickrouter\.ai|tpkcur\.xyz|crazyrouter\.com)'),
}


def tracked_or_source_files(root: Path) -> list[Path]:
    """Check Git's entire index when available, otherwise source candidates."""
    if (root / '.git').exists():
        result = subprocess.run(['git', '-C', str(root), 'ls-files', '-z'],
                                capture_output=True, check=True)
        names = result.stdout.decode('utf-8').split('\0')
        if any(names):
            return [root / name for name in names if name]
    return sorted(p for p in root.rglob('*') if p.is_file()
                  and not any(part in GENERATED or part.endswith('.egg-info')
                              for part in p.relative_to(root).parts))


def inspect_file(path: Path, root: Path) -> list[dict[str, str | int]]:
    relative = path.relative_to(root).as_posix()
    parts = set(path.relative_to(root).parts)
    issues: list[dict[str, str | int]] = []
    def add(reason: str, line: int = 0) -> None:
        issues.append({'file': relative, 'line': line, 'reason': reason})
    if parts & FORBIDDEN_DIRS:
        add('experiment output or internal directory')
    approved_figure = relative in APPROVED_FIGURE_PDFS
    if (path.suffix.lower() in FORBIDDEN_SUFFIXES and not approved_figure) or path.name in FORBIDDEN_NAMES:
        add('excluded document, credential, checkpoint or internal record')
    if path.name.startswith('.env') and path.name != '.env.example':
        add('non-example environment file')
    if path.is_symlink():
        add('symlink requires explicit review'); return issues
    if path.stat().st_size > 10 * 1024 * 1024:
        add('asset exceeds 10 MiB publication budget')
    if approved_figure:
        content = path.read_bytes()
        if not content.startswith(b'%PDF-') or hashlib.sha256(content).hexdigest() != APPROVED_FIGURE_PDFS[relative]:
            add('paper figure differs from the approved public asset')
        return issues
    if path.suffix.lower() in {'.png', '.gif', '.ico', '.webp', '.jpg', '.jpeg'}:
        return issues
    try:
        content = path.read_text('utf-8')
    except UnicodeDecodeError:
        add('unexpected binary file'); return issues
    # Audit signatures themselves contain example private names and regex tokens.
    if relative == 'scripts/audit_release.py':
        return issues
    for index, line in enumerate(content.splitlines(), 1):
        for reason, pattern in {**SECRET_PATTERNS, **LOCAL_PATTERNS}.items():
            if pattern.search(line):
                add(reason, index)
    if path.suffix == '.json' and 'tests' not in parts:
        try:
            value = json.loads(content)
        except json.JSONDecodeError:
            add('invalid JSON'); return issues
        def walk(item: object) -> None:
            if isinstance(item, dict):
                if 'final_pmir' in item and isinstance(item['final_pmir'], (int, float)):
                    add('observed exposure result in JSON')
                if {'finished_at', 'state', 'distances'} <= item.keys():
                    add('completed chain result in JSON')
                if {'requested_model', 'actual_model_verified'} <= item.keys():
                    add('runtime model audit record in JSON')
                if {'trajectory', 'memory_before', 'memory_after', 'session_id'} <= item.keys():
                    add('native execution evidence in JSON')
                for child in item.values(): walk(child)
            elif isinstance(item, list):
                for child in item: walk(child)
        walk(value)
    return issues


def audit(root: Path) -> dict[str, object]:
    paths = tracked_or_source_files(root)
    issues = [issue for path in paths for issue in inspect_file(path, root)]
    return {'passed': not issues, 'files_checked': len(paths), 'issues': issues}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    args = parser.parse_args()
    report = audit(args.root.resolve())
    print(json.dumps(report, indent=2))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
