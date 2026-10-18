"""Attach only the isolated Claude test containers to the frozen gateway network."""
from pathlib import Path
import hashlib
import re
import sys
import uuid

from runtime.settings import ROOT as SOURCE,load,owner
HERE=Path(__file__).resolve().parent
from claude_replay import _LongPathClaudeAdapter, _plain_windows_path
from memharmbench_v84.adapters.claude_code import ADAPTER_REVISION, _io_path
from memharmbench_v84.adapters.base import TechnicalFailure
from memharmbench_v84.adapters.util import run_argv
from runtime.bridge.gateway_setup import NETWORK


class GatewayClaudeAdapter(_LongPathClaudeAdapter):
    """Use a short host cwd for Docker CLI; keep all case mounts/captures unchanged."""

    def _remove_container(self, name: str, capture: Path) -> None:
        if name not in self._owned_containers:
            raise TechnicalFailure("refusing to clean an unowned container")
        result = run_argv(["docker", "rm", "-f", name], cwd=HERE, timeout=60,
                          capture_to=_io_path(capture / "cleanup"))
        missing = "no such container" in (result["stderr"] + result["stdout"]).lower()
        if result["returncode"] != 0 and not missing:
            raise TechnicalFailure("failed to clean owned gateway Claude container")
        self._owned_containers.pop(name, None)

    def cleanup_orphan_containers(self) -> None:
        case_hash = hashlib.sha256(str(self.case_work_root.resolve()).encode()).hexdigest()
        capture = self.case_work_root / ".orphan_cleanup" / uuid.uuid4().hex
        result = run_argv(["docker", "ps", "-aq", "--filter",
            f"label=memharmbench.case_root_sha256={case_hash}", "--filter",
            f"label=memharmbench.adapter_revision={ADAPTER_REVISION}"],
            cwd=HERE, timeout=60, capture_to=_io_path(capture / "inventory"))
        if result["returncode"]:
            raise TechnicalFailure("cannot inspect exact-case orphan containers")
        identifiers = result["stdout"].split()
        if any(not re.fullmatch(r"[0-9a-f]{12,64}", item) for item in identifiers):
            raise TechnicalFailure("invalid orphan container inventory")
        for identifier in identifiers:
            self._owned_containers[identifier] = capture / identifier
            self._remove_container(identifier, capture / identifier)

    def _run_container(self, arguments: list[str], *, capture: Path, timeout: int, phase: str) -> dict:
        self.cleanup_resources()
        arguments = list(arguments)
        for index, item in enumerate(arguments[:-1]):
            if item == "-v":
                arguments[index + 1] = _plain_windows_path(arguments[index + 1])
        name = owner(load()) + "-" + uuid.uuid4().hex
        self._owned_containers[name] = capture
        argv = ["docker", "run", "--rm", "--name", name,
                "--label", f"memharmbench.owner={self._owner_id}",
                "--label", f"memharmbench.adapter_revision={ADAPTER_REVISION}",
                "--label", f"memharmbench.case={self.case_work_root.name}",
                "--label", "memharmbench.case_root_sha256=" + hashlib.sha256(str(self.case_work_root.resolve()).encode()).hexdigest(),
                "--label", f"memharmbench.phase={phase}",
                "--network", NETWORK, *arguments]
        try:
            return run_argv(argv, cwd=HERE, timeout=timeout, capture_to=_io_path(capture))
        finally:
            self._remove_container(name, capture)
