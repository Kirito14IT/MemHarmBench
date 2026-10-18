"""Claude Code thin adapter (host mode + Docker container mode).

Docker mode runs the pinned `memharm-v8-deepseek-claude-code:2.1.228` image
one-shot per turn with an isolated CLAUDE_CONFIG_DIR and workspace bind
mounted; credentials are forwarded only from environment variables
(ANTHROPIC_API_KEY / CLAUDE_API_KEY), never copied. Session continuity uses
`--continue` for the same session (behavioral retry) and a fresh process for
the trigger session. Both CLAUDE.md and project auto-memory are captured.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from pathlib import Path
from typing import Any, Mapping

from .base import (
    AgentAdapter,
    FileSet,
    SessionHandle,
    TechnicalFailure,
    TraceData,
    TurnResult,
)
from .native_evidence import normalize_native_trace
from .util import (
    best_effort_rmtree,
    load_agent_config,
    render_argv,
    run_argv,
    wait_paths_stable,
    walk_files,
)

ADAPTER_REVISION = "claude-code-2.1.228-r4-v843"
CLAUDE_VERSION = "2.1.228"
PROTECTED_OPERATION_INPUTS = ("tools/workflow.py", "records/operation.json")


def operation_readonly_mounts(session: SessionHandle, container_workspace: str) -> list[str]:
    """Overlay trusted trigger inputs as read-only files inside the writable workspace.

    The agent may write an operation state log alongside these files, but cannot
    edit and restore either trusted input between endpoint snapshots.
    """
    if session.phase != "trigger" or session.workspace is None:
        return []
    workspace = Path(session.workspace)
    protected = [workspace / Path(rel) for rel in PROTECTED_OPERATION_INPUTS]
    present = [path.exists() for path in protected]
    if not any(present):
        return []
    if not all(present) or any(not path.is_file() or path.is_symlink() for path in protected):
        raise TechnicalFailure("trusted operation input pair is missing or unsafe")
    return [
        f"{path.resolve()}:{container_workspace.rstrip('/')}/{rel}:ro"
        for path, rel in zip(protected, PROTECTED_OPERATION_INPUTS)
    ]


def _io_path(path: Path) -> Path:
    """Use Windows long paths for local I/O, never for Docker mount syntax."""
    if os.name != "nt":
        return path
    absolute = str(path.absolute())
    if absolute.startswith("\\\\?\\"):
        return path
    if absolute.startswith("\\\\"):
        return Path("\\\\?\\UNC\\" + absolute[2:])
    return Path("\\\\?\\" + absolute)


def _native_session_id(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        return str(uuid.UUID(value)) if str(uuid.UUID(value)) == value else None
    except ValueError:
        return None


def _pinned_version(text: str) -> bool:
    return re.search(r"(?<![\d.])2\.1\.228(?![\d.])", text) is not None


def _extract_json_result(text: str) -> dict[str, Any]:
    try:
        payload = json.loads(text)
        if isinstance(payload, dict):
            return payload
    except json.JSONDecodeError:
        pass
    return {}


def _extract_tool_blocks(payload: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    calls: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    messages = payload.get("messages", payload) if isinstance(payload, dict) else []
    for message in messages if isinstance(messages, list) else []:
        content = message.get("content") if isinstance(message, dict) else None
        for block in content if isinstance(content, list) else []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                calls.append(block)
            elif block.get("type") == "tool_result":
                results.append(block)
    return calls, results


class ClaudeCodeAdapter(AgentAdapter):
    name = "claude_code"

    def __init__(
        self,
        *,
        root: Path,
        case_work_root: Path,
        config: Mapping[str, Any] | None = None,
        mode: str | None = None,
    ):
        self.root = root.resolve()
        self.case_work_root = case_work_root.resolve()
        self.config = config or load_agent_config(root)["claude_code"]
        self.mode = mode or os.environ.get("CLAUDE_MODE", "host")
        if self.mode not in ("host", "docker"):
            raise ValueError(f"CLAUDE_MODE must be host|docker, got {self.mode!r}")
        self.command = os.environ.get("CLAUDE_COMMAND", self.config.get("command", "claude"))
        self.timeout = int(
            os.environ.get(self.config.get("timeout_env", "CLAUDE_TIMEOUT"), 900)
        )
        self._owner_id = uuid.uuid4().hex
        self._owned_containers: dict[str, Path] = {}
        self._native_sessions: dict[str, str] = {}

    def _docker_cfg(self) -> Mapping[str, Any]:
        return self.config.get("docker", {})

    def _config_dir(self) -> Path:
        return self.case_work_root / "claude_config"

    def _workspace(self) -> Path:
        return self.case_work_root / self.config.get("workspace_subpath", "workspace")

    def _reset_business_files(self, workspace: Path) -> None:
        # Only product-native memory may cross the phase boundary. Keeping
        # arbitrary exposure outputs or runtime state would be another carrier.
        root = _io_path(workspace)
        if not root.exists():
            return
        for path in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
            relative = path.relative_to(root)
            if path.is_symlink():
                path.unlink()
            elif path.is_file():
                if path.name != "CLAUDE.md" or relative.parts[0] in ("imports", "records"):
                    path.unlink()
            elif path.is_dir() and not any(path.iterdir()):
                path.rmdir()

    def _forwarded_env(self) -> list[str]:
        out: list[str] = []
        for name in self._docker_cfg().get("env_forward", []):
            value = os.environ.get(name)
            if value:
                out += ["-e", name]
        return out

    def _remove_container(self, name: str, capture: Path) -> None:
        if name not in self._owned_containers:
            raise TechnicalFailure("refusing to clean a container not owned by this adapter")
        result = run_argv(
            ["docker", "rm", "-f", name], cwd=self.case_work_root,
            timeout=60, capture_to=capture / "cleanup",
        )
        missing = "no such container" in (result["stderr"] + result["stdout"]).lower()
        if result["returncode"] != 0 and not missing:
            raise TechnicalFailure(f"failed to remove owned Claude container {name}")
        self._owned_containers.pop(name, None)

    def cleanup_resources(self) -> None:
        """Remove only containers created by this adapter, retaining evidence."""
        errors = []
        for name, capture in list(self._owned_containers.items()):
            try:
                self._remove_container(name, capture)
            except TechnicalFailure as exc:
                errors.append(str(exc))
        if errors:
            raise TechnicalFailure("; ".join(errors))

    def cleanup_orphan_containers(self) -> None:
        """Recover only this exact case directory's revision-matched containers."""
        if self.mode != "docker":
            return
        case_hash = hashlib.sha256(str(self.case_work_root.resolve()).encode()).hexdigest()
        capture = self.case_work_root / ".orphan_cleanup" / uuid.uuid4().hex
        result = run_argv(["docker", "ps", "-aq", "--filter",
                           f"label=memharmbench.case_root_sha256={case_hash}", "--filter",
                           f"label=memharmbench.adapter_revision={ADAPTER_REVISION}"],
                          cwd=self.case_work_root, timeout=60, capture_to=capture / "inventory")
        if result["returncode"] != 0:
            raise TechnicalFailure("cannot inspect exact-case orphan containers")
        identifiers = result["stdout"].split()
        if any(not re.fullmatch(r"[0-9a-f]{12,64}", value) for value in identifiers):
            raise TechnicalFailure("invalid exact-case Docker container inventory")
        for identifier in identifiers:
            self._owned_containers[identifier] = capture / identifier
            self._remove_container(identifier, capture / identifier)

    def _run_container(self, arguments: list[str], *, capture: Path, timeout: int, phase: str) -> dict[str, Any]:
        # A failed cleanup must be retried before another container can touch
        # this case's shared state.
        self.cleanup_resources()
        suffix = uuid.uuid4().hex
        name = f"memharm-v843-gemini-claude-code-{suffix}"
        self._owned_containers[name] = capture
        argv = [
            "docker", "run", "--rm", "--name", name,
            "--label", f"memharmbench.owner={self._owner_id}",
            "--label", f"memharmbench.adapter_revision={ADAPTER_REVISION}",
            "--label", f"memharmbench.case={self.case_work_root.name}",
            "--label", "memharmbench.case_root_sha256=" + hashlib.sha256(str(self.case_work_root.resolve()).encode()).hexdigest(),
            "--label", f"memharmbench.phase={phase}",
            *arguments,
        ]
        try:
            return run_argv(argv, cwd=self.case_work_root, timeout=timeout, capture_to=capture)
        finally:
            self._remove_container(name, capture)

    def capabilities(self) -> Mapping[str, Any]:
        capture = self.case_work_root / ".capabilities" / uuid.uuid4().hex
        if self.mode == "docker":
            docker_cfg = self._docker_cfg()
            image = docker_cfg["image"]
            try:
                inspect = run_argv(
                    ["docker", "image", "inspect", image, "--format", "{{.Id}}"],
                    cwd=self.case_work_root,
                    timeout=60,
                    capture_to=capture / "image",
                )
                image_id = inspect["stdout"].strip()
                if inspect["returncode"] != 0 or not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
                    raise TechnicalFailure(f"pinned Claude image is unavailable: {image}")
                version = self._run_container(
                    [image, "--version"], capture=capture / "version", timeout=180, phase="capability",
                )
                version_text = (version["stdout"] or "").strip()
                installed = version["returncode"] == 0 and _pinned_version(version_text)
                return {
                    "adapter": self.name,
                    "mode": "docker",
                    "installed": installed,
                    "image": image,
                    "image_id": image_id,
                    "version": version_text,
                    "adapter_revision": ADAPTER_REVISION,
                    "error": None if installed else f"Claude Code must be {CLAUDE_VERSION}",
                }
            except TechnicalFailure as exc:
                return {"adapter": self.name, "mode": "docker", "installed": False,
                        "adapter_revision": ADAPTER_REVISION, "error": str(exc)}
        try:
            result = run_argv(
                render_argv(self.config["capability_argv"], {"command": self.command}),
                cwd=self.root,
                timeout=30,
                capture_to=capture,
            )
            return {
                "adapter": self.name,
                "mode": "host",
                "installed": result["returncode"] == 0 and _pinned_version(result["stdout"]),
                "version": (result["stdout"] or result["stderr"]).strip(),
                "adapter_revision": ADAPTER_REVISION,
            }
        except TechnicalFailure as exc:
            return {"adapter": self.name, "mode": "host", "installed": False, "error": str(exc)}

    def start_session(
        self,
        case: Any,
        phase: str,
        attempt_id: str,
        lineage: Mapping[str, str] | None = None,
        inputs: Mapping[str, Any] | None = None,
    ) -> SessionHandle:
        inputs = inputs or {}
        workspace = self._workspace()
        workspace.mkdir(parents=True, exist_ok=True)
        if phase == "trigger":
            self._reset_business_files(workspace)
        for rel, content in (inputs.get("workspace_files") or {}).items():
            path = workspace / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8", newline="\n")
        message_file = self.case_work_root / phase / "message.txt"
        message_file.parent.mkdir(parents=True, exist_ok=True)
        message_file.write_text(inputs.get("message", ""), encoding="utf-8", newline="\n")
        config_dir = self._config_dir()
        config_dir.mkdir(parents=True, exist_ok=True)
        native_id = str(uuid.uuid4())
        session_id = f"mhb843-{case.case_id[:40]}-{phase}-{attempt_id[-12:]}"
        previous_ids = set(self._native_sessions.values())
        previous_ids.update(
            path.stem for path in (_io_path(config_dir) / "projects").glob("*/*.jsonl")
            if _native_session_id(path.stem)
        )
        return SessionHandle(
            adapter=self.name,
            session_id=session_id,
            phase=phase,
            attempt_id=attempt_id,
            lineage=dict(lineage or {}),
            workspace=workspace,
            state={
                "mode": self.mode,
                "config_dir": str(config_dir),
                "message_file": str(message_file),
                "continue_session": False,
                "requested_native_session_id": native_id,
                "native_session_id": None,
                "previous_native_session_ids": sorted(previous_ids),
                "call_count": 0,
                "turn": 0,
            },
        )

    def run_turn(self, session: SessionHandle, message: str) -> TurnResult:
        message_file = Path(session.state["message_file"])
        message_file.parent.mkdir(parents=True, exist_ok=True)
        message_file.write_text(message, encoding="utf-8", newline="\n")
        session.state["call_count"] = session.state.get("call_count", 0) + 1
        capture_dir = self.case_work_root / session.phase / (
            "capture_" + hashlib.sha256(session.attempt_id.encode("utf-8")).hexdigest()[:12]
            + f"_{session.state['call_count']:04d}_{uuid.uuid4().hex[:12]}"
        )
        capture_dir.mkdir(parents=True, exist_ok=False)
        (capture_dir / "message.txt").write_text(message, encoding="utf-8", newline="\n")
        session.state["last_capture_dir"] = str(capture_dir)
        requested_id = session.state["requested_native_session_id"]
        history_exists = bool(self._session_trace_paths(session, requested_id))
        continue_session = session.state.get("continue_session") or history_exists
        # --continue selects the latest conversation. A precise UUID also
        # remains safe after shutdown or unrelated product bookkeeping writes.
        session_args = ["--resume", requested_id] if continue_session else ["--session-id", requested_id]
        docker_cfg = self._docker_cfg() if self.mode == "docker" else None
        protected_mounts = operation_readonly_mounts(
            session, str(docker_cfg["workspace_container_path"])
        ) if docker_cfg is not None else []
        mount_provenance = {
            "policy": "trigger_pair_readonly_nested_bind_v1",
            "mode": "ro",
            "inputs": [
                {
                    "path": relative,
                    "container_path": str(docker_cfg["workspace_container_path"]).rstrip("/") + "/" + relative,
                    "sha256": hashlib.sha256((Path(session.workspace) / relative).read_bytes()).hexdigest(),
                }
                for relative in PROTECTED_OPERATION_INPUTS
            ] if protected_mounts else [],
        }
        metadata = {
            "adapter_revision": ADAPTER_REVISION,
            "harness_session_id": session.session_id,
            "phase": session.phase,
            "call_count": session.state["call_count"],
            "requested_native_session_id": requested_id,
            "native_session_id": session.state.get("native_session_id"),
            "continued": bool(continue_session),
            "termination": "started",
            "protected_operation_mounts": mount_provenance,
        }
        metadata_path = capture_dir / "native_metadata.json"

        def save_metadata() -> None:
            metadata_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")

        save_metadata()
        try:
            if self.mode == "docker":
                arguments = [
                    "-e", f"CLAUDE_CONFIG_DIR={docker_cfg['config_dir_container_path']}",
                    "-v", f"{Path(session.state['config_dir']).resolve()}:{docker_cfg['config_dir_container_path']}",
                    "-v", f"{Path(session.workspace).resolve()}:{docker_cfg['workspace_container_path']}",
                    *(item for mount in protected_mounts for item in ("-v", mount)),
                    "-w", docker_cfg["workspace_container_path"],
                    *self._forwarded_env(),
                    docker_cfg["image"], "-p", message, "--output-format", "json",
                    "--dangerously-skip-permissions", *session_args,
                ]
                result = self._run_container(
                    arguments, capture=capture_dir, timeout=self.timeout, phase=session.phase,
                )
            else:
                argv = render_argv(
                    self.config["turn_argv"],
                    {"command": self.command, "message": message, "workspace": str(session.workspace)},
                ) + session_args
                result = run_argv(
                    argv, cwd=session.workspace,
                    env={self.config.get("config_dir_env", "CLAUDE_CONFIG_DIR"): session.state["config_dir"]},
                    timeout=self.timeout, capture_to=capture_dir,
                )
            result["protected_operation_mounts"] = mount_provenance
        except BaseException as exc:
            metadata["termination"] = "technical_failure"
            metadata["error_type"] = type(exc).__name__
            save_metadata()
            # A timeout may still have created durable session history. The
            # next physical call resumes that same UUID, never another case.
            if self._session_trace_paths(session, requested_id):
                session.state["continue_session"] = True
            raise

        payload = _extract_json_result(result["stdout"])
        native_id = _native_session_id(payload.get("session_id"))
        metadata["native_session_id"] = native_id
        status = payload.get("api_error_status")
        api_error_status = int(status) if re.fullmatch(r"[1-5][0-9]{2}", str(status)) else None
        subtype = payload.get("subtype")
        safe_subtypes = {
            "success", "error_during_execution", "error_max_turns", "error_max_budget_usd",
            "error_max_structured_output_retries", "error_api", "error_authentication",
        }
        safe_subtype = subtype if isinstance(subtype, str) and subtype in safe_subtypes else "unrecognized"
        metadata["api_error_status"] = api_error_status
        metadata["subtype"] = safe_subtype
        error = None
        if native_id is None:
            error = "Claude result is missing a valid native session_id"
        elif native_id != requested_id or native_id in session.state["previous_native_session_ids"]:
            error = "Claude native session_id violates the requested session boundary"
        else:
            session.state["native_session_id"] = native_id
            self._native_sessions[session.session_id] = native_id
            session.state["continue_session"] = True
        if result["returncode"] != 0:
            error = error or f"Claude exited with code {result['returncode']}"
        if payload.get("is_error") is not False:
            error = error or "Claude result is_error is true or missing"
        if payload.get("type") != "result" or payload.get("subtype") != "success":
            error = error or "Claude did not return a successful result envelope"
        if error:
            error += f" (api_error_status={api_error_status}, subtype={safe_subtype})"
            metadata["termination"] = "technical_failure"
            metadata["error"] = error
            save_metadata()
            return TurnResult(
                answer=result["stdout"] or result["stderr"],
                terminated_ok=False,
                error=error,
                raw=result,
            )
        answer = payload.get("result") or payload.get("text") or result["stdout"].strip()
        if isinstance(answer, (dict, list)):
            answer = json.dumps(answer, ensure_ascii=False)
        try:
            native = normalize_native_trace(self.collect_trace(session).entries)
            calls, tool_results = native["tool_calls"], native["tool_results"]
        except TechnicalFailure as exc:
            metadata["termination"] = "technical_failure"
            metadata["error"] = str(exc)
            save_metadata()
            return TurnResult(answer=str(answer), terminated_ok=False, error=str(exc), raw=result)
        session.state["turn"] = session.state.get("turn", 0) + 1
        metadata["native_tool_call_count"] = len(calls)
        metadata["termination"] = "ok"
        save_metadata()
        return TurnResult(
            answer=str(answer),
            tool_calls=calls,
            tool_results=tool_results,
            terminated_ok=True,
            raw=result,
        )

    def _memory_files(self, session: SessionHandle) -> dict[str, Path]:
        config_root = _io_path(Path(session.state["config_dir"]))
        workspace_root = _io_path(Path(session.workspace))
        roots = [config_root, workspace_root]
        files: dict[str, Path] = {}
        for root in roots:
            for path in sorted(root.rglob("CLAUDE.md")):
                relative = path.relative_to(root)
                # These business directories are deliberately replaced at the
                # phase boundary; their files cannot count as durable memory.
                if root == workspace_root and relative.parts[0] in ("imports", "records"):
                    continue
                if path.is_file():
                    files[f"{root.name}:{relative.as_posix()}"] = path
        for path in sorted(config_root.glob("projects/*/memory/**/*.md")):
            if path.is_file():
                files[f"{config_root.name}:{path.relative_to(config_root).as_posix()}"] = path
        return files

    def snapshot_memory(self, session: SessionHandle) -> FileSet:
        try:
            files = {
                name: path.read_text(encoding="utf-8", errors="strict")
                for name, path in self._memory_files(session).items()
            }
        except (OSError, UnicodeError) as exc:
            raise TechnicalFailure("Claude native memory snapshot is unreadable") from exc
        return FileSet(files=files, note="Claude Code CLAUDE.md and project auto-memory files")

    def wait_memory_settled(self, session: SessionHandle, timeout_ms: int = 5000) -> None:
        config_root = _io_path(Path(session.state["config_dir"]))
        workspace_root = _io_path(Path(session.workspace))
        projects = config_root / "projects"
        # Directory signatures detect new memory entries while existing files
        # use the exact same discovery rules as snapshot_memory.
        paths = [config_root, config_root / "CLAUDE.md", workspace_root,
                 workspace_root / "CLAUDE.md", projects]
        paths += list(projects.glob("*")) + list(projects.glob("*/memory"))
        paths += list(projects.glob("*/memory/**/")) + list(self._memory_files(session).values())
        if not wait_paths_stable(list(dict.fromkeys(paths)), timeout_ms):
            raise TechnicalFailure("Claude memory files did not settle before the snapshot")

    def snapshot_workspace(self, session: SessionHandle) -> FileSet:
        return FileSet(files=walk_files(session.workspace), note=f"claude workspace {session.workspace}")

    def _session_trace_paths(self, session: SessionHandle, native_id: str) -> list[Path]:
        projects = _io_path(Path(session.state["config_dir"])) / "projects"
        return sorted(path for path in projects.glob(f"*/{native_id}.jsonl") if path.is_file())

    def collect_trace(self, session: SessionHandle) -> TraceData:
        native_id = session.state.get("native_session_id")
        if not _native_session_id(native_id):
            raise TechnicalFailure("cannot collect Claude trajectory without a verified native session_id")
        config_root = _io_path(Path(session.state["config_dir"]))
        entries: list[Mapping[str, Any]] = []
        main_paths = self._session_trace_paths(session, native_id)
        if not main_paths:
            raise TechnicalFailure("Claude native session completed without its main transcript")
        for main in main_paths:
            scoped_paths = [main] + sorted((main.parent / native_id / "subagents").rglob("*.jsonl"))
            for path in scoped_paths:
                if not path.is_file():
                    continue
                try:
                    lines = path.read_text(encoding="utf-8", errors="strict").splitlines()
                except UnicodeError as exc:
                    raise TechnicalFailure("Claude native transcript has invalid text encoding") from exc
                if path == main:
                    if not any(line.strip() for line in lines):
                        raise TechnicalFailure("Claude native session has an empty main transcript")
                    for line in lines:
                        try:
                            entry = json.loads(line)
                        except json.JSONDecodeError as exc:
                            if line.strip():
                                raise TechnicalFailure("Claude main transcript contains corrupt JSON") from exc
                            continue
                        if isinstance(entry, dict) and entry.get("sessionId") not in (None, native_id):
                            raise TechnicalFailure("Claude main transcript contains another native session")
                entries.append({
                    "source": path.relative_to(config_root).as_posix(), "lines": lines,
                    "native_session_id": native_id, "scope": "session" if path == main else "subagent",
                })
        normalized = normalize_native_trace(entries)
        if not normalized["integrity"]["native_capture_complete"]:
            raise TechnicalFailure("Claude native evidence is incomplete: " + "; ".join(normalized["integrity"]["errors"]))
        return TraceData(entries=entries)

    def cleanup(self, session: SessionHandle | None = None) -> None:
        # Evidence and product state remain available for rejudgment and audit.
        self.cleanup_resources()
