"""Shared adapter helpers (subprocess execution, config, file walk)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Mapping

from .base import TechnicalFailure

MAX_FILE_CHARS = 200_000
MAX_FILES = 256


def load_agent_config(root: Path) -> Mapping[str, Any]:
    raw = json.loads((root / "configs" / "agents.json").read_text(encoding="utf-8"))
    return raw["agents"]


def render_argv(template: list[str], values: Mapping[str, str]) -> list[str]:
    return [item.format(**values) for item in template]


def run_argv(
    argv: list[str],
    *,
    cwd: Path,
    env: Mapping[str, str] | None = None,
    timeout: int,
    capture_to: Path,
) -> dict[str, Any]:
    """Run a subprocess writing output to files (no piped stdio)."""
    resolved = list(argv)
    if os.path.sep not in resolved[0]:
        found = shutil.which(resolved[0])
        if found:
            resolved[0] = found
    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    stdout_path = capture_to / "stdout.txt"
    stderr_path = capture_to / "stderr.txt"
    capture_to.mkdir(parents=True, exist_ok=True)
    with stdout_path.open("w", encoding="utf-8", errors="replace") as out, stderr_path.open(
        "w", encoding="utf-8", errors="replace"
    ) as err:
        try:
            completed = subprocess.run(
                resolved,
                cwd=str(cwd),
                env=full_env,
                stdout=out,
                stderr=err,
                timeout=timeout,
                check=False,
                shell=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise TechnicalFailure(f"timeout after {timeout}s: {' '.join(argv[:4])}") from exc
        except OSError as exc:
            raise TechnicalFailure(f"cannot execute {' '.join(argv[:2])}: {exc}") from exc
    return {
        "argv": resolved,
        "returncode": completed.returncode,
        "stdout": stdout_path.read_text(encoding="utf-8", errors="replace"),
        "stderr": stderr_path.read_text(encoding="utf-8", errors="replace"),
    }


def walk_files(root: Path, max_files: int = MAX_FILES, max_chars: int = MAX_FILE_CHARS) -> dict[str, str]:
    files: dict[str, str] = {}
    if not root.exists():
        return files
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if ".git" in path.parts:
            continue
        rel = path.relative_to(root).as_posix()
        if len(files) >= max_files:
            files[rel] = f"...[{max_files} file cap reached]"
            break
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        files[rel] = content[:max_chars] + ("\n...[truncated]" if len(content) > max_chars else "")
    return files


def wait_paths_stable(paths: list[Path], timeout_ms: int, poll_ms: int = 100) -> bool:
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        signatures = []
        for path in paths:
            if path.exists():
                stat = path.stat()
                signatures.append((path, stat.st_mtime_ns, stat.st_size))
            else:
                signatures.append((path, None, None))
        time.sleep(poll_ms / 1000)
        current = []
        for path in paths:
            if path.exists():
                stat = path.stat()
                current.append((path, stat.st_mtime_ns, stat.st_size))
            else:
                current.append((path, None, None))
        if current == signatures:
            return True
    return False


def best_effort_rmtree(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
