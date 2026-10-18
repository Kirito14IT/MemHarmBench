"""Content-addressed V8.4.3 QuickRouter GPT-5 atomic predicate judging."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import time
from typing import Any, Callable, Mapping
import urllib.error
import urllib.request

from memharmbench_v84.identity import case_condition, contains_placeholder
from memharmbench_v84.judges import (
    JudgeClient, JudgeError, JudgeFormatError, build_judge_messages,
    load_rubric_text, parse_judge_output,
)
from memharmbench_v84.schema import JudgeOutput
from memharmbench_v84.judges.validation import prepare_judge_evidence, validate_judge_output, V84_JUDGE_BOUNDARY
from v84_profile import load_credentials as profile_credentials, is_gpt5_model

REVISION = "claude-gemini-v843-gpt5-quickrouter-atomic-r1"
MODEL = "gpt-5"
from v84_profile import JUDGE_BASE_URL
EXPECTED_ENDPOINT = JUDGE_BASE_URL
MEMORY_BOUNDARY = ""  # Scope is composed once by build_judge_messages.

def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def io_path(path: Path) -> Path:
    value = str(path.resolve())
    if os.name == "nt" and not value.startswith("\\\\?\\"):
        return Path("\\\\?\\UNC\\" + value[2:] if value.startswith("\\\\") else "\\\\?\\" + value)
    return Path(value)


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path = io_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def load_credentials(path: Path) -> tuple[str, str]:
    """Read only the pinned QuickRouter judge provider."""
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.removeprefix("export ").split("=", 1)
        values[name.strip()] = value.strip().strip('"').strip("'")
    endpoint = values.get("QUICKROUTER_BASE_URL", "").rstrip("/")
    key = values.get("QUICKROUTER_API_KEY", "")
    if endpoint != EXPECTED_ENDPOINT or not key:
        raise ValueError("Existing GPT-5 judge provider configuration is missing or changed")
    if values.get("QUICKROUTER_MODEL") != MODEL:
        raise ValueError("Existing judge configuration must explicitly select gpt-5")
    return endpoint, key


class FormalJudgeError(JudgeError):
    def __init__(self, category: str, status_code: int | None = None):
        self.category = category
        self.status_code = status_code
        super().__init__(f"judge {category}" + (f" status={status_code}" if status_code is not None else ""))


class OfflineCallRequired(RuntimeError):
    """Raised before transport when migration preparation reaches new paid work."""


def is_quota_failure(record: Mapping[str, Any]) -> bool:
    """An explicit provider quota signal is recoverable; generic 403 is not."""
    return record.get("status_code") == 402 or (
        record.get("status_code") == 403
        and "account_quota_insufficient" in str(record.get("detail", "")))


def recovered_quota_attempts(output_dir: Path, folder: Path) -> set[str]:
    recovered = set()
    for ledger_path in io_path(output_dir / "quota_recovery").glob(folder.name + "_attempt_*/ledger.json"):
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        name = ledger.get("attempt_file", "")
        if not re.fullmatch(r"attempt_\d+\.json", name):
            raise FormalJudgeError("quota_ledger_path_mismatch")
        active = io_path(folder / name).read_bytes()
        archive = (ledger_path.parent / "attempt.json").read_bytes()
        request = json.loads(io_path(folder / "request.json").read_text(encoding="utf-8"))
        record = json.loads(active)
        if (active != archive or hashlib.sha256(active).hexdigest() != ledger.get("attempt_sha256")
                or ledger.get("cache_key") != folder.name or ledger.get("request_identity_sha256") != digest(request["identity"])
                or ledger.get("acknowledgment") != "explicit_resume_after_recharge"
                or record.get("kind") != "fatal" or not is_quota_failure(record)):
            raise FormalJudgeError("quota_recovery_identity_mismatch")
        recovered.add(name)
    return recovered


def unlock_quota_errors(output_dir: Path) -> list[dict[str, Any]]:
    """Explicit user recharge acknowledgment; preserve each failed attempt verbatim.

    Acknowledgments exempt only the named quota-fatal record. Transport/format
    counters and all other fatal records remain in place, as do valid decisions.
    A later quota error requires a new explicit invocation.
    """
    unlocked = []
    for folder in sorted(io_path(output_dir / "judge_cache").glob("*")):
        if not folder.is_dir() or (folder / "decision.json").exists():
            continue
        if not re.fullmatch(r"[0-9a-f]{64}", folder.name):
            raise FormalJudgeError("quota_cache_path_mismatch")
        request = json.loads((folder / "request.json").read_text(encoding="utf-8"))
        if digest(request["identity"]) != folder.name:
            raise FormalJudgeError("quota_request_identity_mismatch")
        already = recovered_quota_attempts(output_dir, folder)
        for attempt in sorted(folder.glob("attempt_*.json")):
            raw = attempt.read_bytes()
            record = json.loads(raw)
            if attempt.name in already or record.get("kind") != "fatal" or not is_quota_failure(record):
                continue
            archive_dir = io_path(output_dir / "quota_recovery" / (folder.name + "_" + attempt.stem))
            archive_dir.mkdir(parents=True, exist_ok=True)
            archive = archive_dir / "attempt.json"
            if archive.exists() and archive.read_bytes() != raw:
                raise FormalJudgeError("quota_archive_mismatch")
            if not archive.exists():
                archive.write_bytes(raw)
            ledger = {"at": now(), "acknowledgment": "explicit_resume_after_recharge",
                      "cache_key": folder.name, "attempt_file": attempt.name,
                      "attempt_sha256": hashlib.sha256(raw).hexdigest(),
                      "request_identity_sha256": digest(request["identity"]),
                      "case_id": request["identity"]["case_id"], "phase": request["identity"]["phase"],
                      "budgets_reset": False, "valid_labels_overridden": False}
            write_json(archive_dir / "ledger.json", ledger)
            unlocked.append(ledger)
        recovered_quota_attempts(output_dir, folder)
    return unlocked


class FormalJudgeClient(JudgeClient):
    source = "gpt-5-compatible-http"

    def __init__(self, *, output_dir: Path, data_root: Path,
                 credential_path: Path | None = None,
                 transport: Callable[[dict[str, Any]], Mapping[str, Any]] | None = None,
                 timeout: float = 180, max_transport_retries: int = 3,
                 max_format_retries: int = 2, migration=None, allow_network: bool = True,
                 evaluation_contract_fingerprint: str | None = None,
                 qualification: bool = False, expected_actual_model: str | None = None):
        from memharmbench_v84.judges.atomic import require_fingerprint, PREDICATE_CONTRACT_REVISION
        contract = require_fingerprint(evaluation_contract_fingerprint)
        if not qualification and not is_gpt5_model(expected_actual_model):
            raise ValueError("full evaluation requires the locked judge resolved_actual_model")
        if expected_actual_model is not None and not is_gpt5_model(expected_actual_model):
            raise ValueError("invalid locked GPT-5 model identity")
        self.qualification = qualification
        self.expected_actual_model = expected_actual_model
        self.observed_actual_models: set[str] = set()
        if (timeout, max_transport_retries, max_format_retries) != (180, 3, 2):
            raise ValueError("V8.4.3 pins judge timeout/retries to 180/3/2")
        if migration is not None:
            raise ValueError("V8.4.3 does not migrate old judge decisions")
        self.output_dir = output_dir.resolve()
        self.data_root = data_root.resolve()
        self.timeout = timeout
        self.max_transport_retries = max_transport_retries
        self.max_format_retries = max_format_retries
        self._transport = transport
        self.migration = migration
        self.allow_network = allow_network
        self.api_key = ""
        self.base_url = EXPECTED_ENDPOINT
        if transport is None and allow_network:
            self.base_url, self.api_key = (load_credentials(credential_path) if credential_path else profile_credentials())
        self.identity = {
            "revision": REVISION, "source": self.source, "model": MODEL,
            "evaluation_contract_fingerprint": contract,
            "predicate_contract_revision": PREDICATE_CONTRACT_REVISION,
            "qualification": qualification, "expected_actual_model": expected_actual_model,
            "base_url": self.base_url, "timeout_seconds": timeout,
            "max_transport_retries": max_transport_retries,
            "max_format_retries": max_format_retries,
            "temperature": 0, "response_format": "phase-specific strict json_schema",
            "accepted_response_models": "gpt-5 or gpt-5-YYYY-MM-DD",
            "provider": "configured_compatible_endpoint",
            "memory_boundary_sha256": digest(MEMORY_BOUNDARY + V84_JUDGE_BOUNDARY),
        }
        self.calls = 0
        self.cache_hits = 0
        self.last_call: dict[str, Any] = {}
        self._observe_persisted_models()

    def _observe_persisted_models(self) -> None:
        """Rebuild qualification model identity from every persisted response.

        Responses rejected for schema/format still identify the provider model
        that served a paid call, so a restart must not forget them.
        """
        cache_root = io_path(self.output_dir / "judge_cache")
        if not cache_root.exists():
            return
        for folder in sorted(cache_root.iterdir()):
            if not folder.is_dir() or not re.fullmatch(r"[0-9a-f]{64}", folder.name):
                raise FormalJudgeError("judge_cache_path_mismatch")
            for response_path in sorted(folder.glob("response_*.json")):
                if not re.fullmatch(r"response_\d+\.json", response_path.name):
                    raise FormalJudgeError("cache_response_path_mismatch")
                try:
                    response = json.loads(response_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    raise FormalJudgeError("persisted_response_malformed") from None
                if not isinstance(response, Mapping):
                    raise FormalJudgeError("persisted_response_malformed")
                self.observe_actual_model(response.get("model"))

    def _send(self, body: dict[str, Any]) -> Mapping[str, Any]:
        if self._transport is not None:
            return self._transport(body)
        request = urllib.request.Request(
            self.base_url + "/chat/completions",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": "Bearer " + self.api_key},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def observe_actual_model(self, model: object) -> None:
        if not is_gpt5_model(model) or (self.expected_actual_model is not None and model != self.expected_actual_model):
            raise FormalJudgeError("unexpected_actual_model")
        if self.observed_actual_models and model not in self.observed_actual_models:
            raise FormalJudgeError("judge_model_changed_within_campaign")
        self.observed_actual_models.add(model)

    def _validate_response(self, response: Mapping[str, Any], phase: str, evidence: Mapping[str, Any]) -> JudgeOutput:
        if not isinstance(response, Mapping):
            raise JudgeFormatError("judge response must be an object")
        model = response.get("model")
        # Bind the provider identity as soon as a response envelope is received.
        # Malformed content still came from that model and must participate in
        # the qualification-wide uniqueness check before any format retry.
        self.observe_actual_model(model)
        try:
            choice = response["choices"][0]
            if not isinstance(choice, Mapping) or not isinstance(choice.get("message"), Mapping):
                raise JudgeFormatError("judge choice and message must be objects")
            if choice.get("finish_reason") not in (None, "stop"):
                raise JudgeFormatError("incomplete judge response")
            content = choice["message"]["content"]
        except (KeyError, IndexError, TypeError) as error:
            raise JudgeFormatError("missing response content") from error
        if not isinstance(content, str):
            raise JudgeFormatError("response content must be text")
        output = parse_judge_output(content, phase, evidence)
        return output

    def judge(self, case: Any, evidence: Mapping[str, Any], phase: str) -> JudgeOutput:
        if phase not in ("exposure", "trigger"):
            raise ValueError("Unsupported judge phase")
        from memharmbench_v84.judges.atomic import (
            case_contract_fingerprint,
            response_format_contract,
        )
        contract = case_contract_fingerprint(case)
        if contract != self.identity["evaluation_contract_fingerprint"] or evidence.get("evaluation_contract_fingerprint") != contract:
            raise FormalJudgeError("evaluation_contract_fingerprint_mismatch")
        if evidence.get("evidence_dependencies") != getattr(case, "evidence_dependencies", None):
            raise FormalJudgeError("evidence_dependencies_contract_mismatch")
        if case_condition(case.provenance) != "attack":
            raise FormalJudgeError("controls_not_in_v84_release")
        raw_evidence = dict(evidence)
        if contains_placeholder(raw_evidence):
            raise FormalJudgeError("runtime_placeholder_detected")
        # Never default a missing provenance condition to attack. V8.4.3 resolved
        # records must carry that identity explicitly.
        evidence = prepare_judge_evidence({**evidence, "condition": case_condition(case.provenance)}, phase)
        rubric = load_rubric_text(self.data_root, case.template_id)
        messages = build_judge_messages(rubric, evidence, phase)
        body = {"model": MODEL, "messages": messages, "temperature": 0,
                "response_format": response_format_contract(phase)}
        request_record = {
            "rubric": rubric,
            "messages": messages,
            "body": body,
        }
        if contains_placeholder(request_record):
            raise FormalJudgeError("runtime_placeholder_detected")
        request_identity = {"judge": self.identity, "case_id": case.case_id,
                            "phase": phase, "evaluation_contract_fingerprint": contract,
                            "evidence_sha256": digest(evidence),
                            "rubric_sha256": digest(rubric), "messages_sha256": digest(messages),
                            "request_sha256": digest(body)}
        cache_key = digest(request_identity)
        folder = self.output_dir / "judge_cache" / cache_key
        cached = io_path(folder / "decision.json")
        self.last_call = {**request_identity, "cache_key": cache_key, "directory": str(folder)}
        if cached.exists():
            record = json.loads(cached.read_text(encoding="utf-8"))
            if record.get("identity") != request_identity:
                raise FormalJudgeError("cache_identity_mismatch")
            request = json.loads(io_path(folder / "request.json").read_text(encoding="utf-8"))
            if (request.get("identity") != request_identity
                    or any(request.get(key) != value for key, value in request_record.items())):
                raise FormalJudgeError("cache_request_mismatch")
            response_name = record["response_file"]
            if not re.fullmatch(r"response_\d+\.json", response_name):
                raise FormalJudgeError("cache_response_path_mismatch")
            response_path = io_path(folder / response_name)
            response = json.loads(response_path.read_text(encoding="utf-8"))
            if digest(response) != record["response_sha256"]:
                raise FormalJudgeError("cache_response_mismatch")
            output = validate_judge_output(self._validate_response(response, phase, evidence), evidence, phase)
            derived = {
                "predicates": json.loads(response["choices"][0]["message"]["content"])["predicates"],
                "metrics": {k: v.as_dict() for k, v in output.results.items()},
            }
            if (derived["metrics"] != record["metrics"]
                    or record.get("derived_decision_sha256") != digest(derived)):
                raise FormalJudgeError("cache_decision_mismatch")
            self.cache_hits += 1
            provenance = record.get("response_origin", {})
            self.last_call.update(cache_hit=True, actual_model=response["model"],
                                  response_origin=provenance.get("kind", "unknown"),
                                  response_origin_provider=provenance.get("provider", "unknown"))
            return output
        request_path = io_path(folder / "request.json")
        if request_path.exists():
            saved = json.loads(request_path.read_text(encoding="utf-8"))
            if (saved.get("identity") != request_identity
                    or any(saved.get(key) != value for key, value in request_record.items())):
                raise FormalJudgeError("unfinished_request_identity_mismatch")
        else:
            write_json(request_path, {
                "at": now(),
                "identity": request_identity,
                **request_record,
            })
        if self.migration is not None:
            response = self.migration.reuse_response(case, raw_evidence, evidence, phase, request_identity, body, folder)
            if response is not None:
                output = validate_judge_output(self._validate_response(response, phase, evidence), evidence, phase)
                self._commit(folder, request_identity, response, "response_000.json", output)
                self.cache_hits += 1
                migration_record = json.loads(io_path(Path(folder) / "migration.json").read_text(encoding="utf-8"))
                self.last_call.update(cache_hit=True, migrated=True, actual_model=response["model"],
                                      response_origin="migrated",
                                      response_origin_provider=migration_record["source_provider"])
                return output
        # Budgets are persisted per judge item and do not reset on a process restart.
        prior = []
        unlocked = recovered_quota_attempts(self.output_dir, folder)
        for path in sorted(io_path(folder).glob("attempt_*.json")):
            record = json.loads(path.read_text(encoding="utf-8"))
            if path.name not in unlocked:
                prior.append(record)
        # A persisted ``started`` record means an interrupted request consumed
        # one transport attempt.  It may resume only within the original
        # frozen retry budget; the budget is never reset on restart.
        transport_errors = sum(r.get("kind") in ("transport", "started") for r in prior)
        format_errors = sum(r.get("kind") == "format" for r in prior)
        terminal = [r for r in prior if r.get("kind") == "fatal"]
        # Recover a persisted valid raw response before the offline/network gate.
        # A crash between response.json and decision.json must never spend another call.
        for response_path in sorted(io_path(folder).glob("response_*.json")):
            try:
                response = json.loads(response_path.read_text(encoding="utf-8"))
                output = validate_judge_output(self._validate_response(response, phase, evidence), evidence, phase)
            except (JudgeFormatError, json.JSONDecodeError):
                continue
            self._commit(folder, request_identity, response, response_path.name, output)
            self.cache_hits += 1
            self.last_call.update(cache_hit=True, recovered=True, actual_model=response["model"],
                                  response_origin="new", response_origin_provider="configured_compatible_endpoint")
            return output
        if terminal:
            raise FormalJudgeError(terminal[-1]["category"], terminal[-1].get("status_code"))
        if not self.allow_network:
            raise OfflineCallRequired("a new QuickRouter request is required")
        sequence = max([int(p.stem.rsplit("_", 1)[1]) for p in io_path(folder).glob("attempt_*.json")] + [0])
        while True:
            if transport_errors > self.max_transport_retries:
                raise FormalJudgeError("transport_retry_exhausted")
            if format_errors > self.max_format_retries:
                raise FormalJudgeError("format_retry_exhausted")
            sequence += 1
            attempt_path = folder / f"attempt_{sequence:03d}.json"
            write_json(attempt_path, {"at": now(), "kind": "started", "sequence": sequence})
            try:
                self.calls += 1
                response = self._send(body)
            except urllib.error.HTTPError as error:
                code = error.code
                kind = "fatal" if code in (400, 401, 402, 403, 404) else "transport"
                # Preserve diagnostic body with the active credential removed, never headers.
                detail = error.read().decode("utf-8", errors="replace")
                if self.api_key:
                    detail = detail.replace(self.api_key, "[REDACTED]")
                detail = detail[:12000]
                category = "provider_quota" if is_quota_failure({"status_code": code, "detail": detail}) else "http_error"
                write_json(attempt_path, {"at": now(), "kind": kind, "category": category,
                                          "status_code": code, "detail": detail})
                if kind == "fatal":
                    raise FormalJudgeError(category, code) from None
                transport_errors += 1
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as error:
                transport_errors += 1
                write_json(attempt_path, {"at": now(), "kind": "transport",
                                          "error_type": type(error).__name__})
            else:
                response_name = f"response_{sequence:03d}.json"
                try:
                    write_json(folder / response_name, response)
                except OSError as error:
                    try:
                        write_json(attempt_path, {
                            "at": now(), "kind": "fatal",
                            "category": "response_persistence_failed",
                            "error_type": type(error).__name__,
                        })
                    except OSError:
                        pass
                    raise FormalJudgeError("response_persistence_failed") from error
                try:
                    output = validate_judge_output(
                        self._validate_response(response, phase, evidence), evidence, phase
                    )
                except (JudgeFormatError, json.JSONDecodeError, TypeError) as error:
                    format_errors += 1
                    write_json(attempt_path, {"at": now(), "kind": "format",
                                              "error_type": type(error).__name__})
                except FormalJudgeError as error:
                    write_json(attempt_path, {"at": now(), "kind": "fatal", "category": error.category,
                                              "status_code": error.status_code})
                    raise
                else:
                    self._commit(folder, request_identity, response, response_name, output)
                    write_json(attempt_path, {"at": now(), "kind": "accepted", "sequence": sequence})
                    self.last_call.update(cache_hit=False, actual_model=response["model"],
                                          response_origin="new", response_origin_provider="configured_compatible_endpoint")
                    return output
            if transport_errors <= self.max_transport_retries and format_errors <= self.max_format_retries:
                time.sleep(min(2 ** min(transport_errors + format_errors, 5), 30))

    def _commit(self, folder: Path, identity: dict, response: Mapping[str, Any],
                response_name: str, output: JudgeOutput) -> None:
        migration_path = io_path(folder / "migration.json")
        origin = {"kind": "new", "provider": "configured_compatible_endpoint", "base_url": EXPECTED_ENDPOINT}
        if migration_path.exists():
            migration = json.loads(migration_path.read_text(encoding="utf-8"))
            origin = {"kind": "migrated", "provider": migration["source_provider"],
                      "base_url": migration["source_base_url"],
                      "source_campaign": migration["previous_formal_campaign"]}
        predicates = json.loads(response["choices"][0]["message"]["content"])["predicates"]
        metrics = {key: metric.as_dict() for key, metric in output.results.items()}
        derived = {"predicates": predicates, "metrics": metrics}
        write_json(folder / "decision.json", {
            "at": now(), "identity": identity, "actual_model": response["model"],
            "response_file": response_name, "response_sha256": digest(response),
            "usage": response.get("usage"),
            "response_origin": origin,
            "predicates": predicates,
            "metrics": metrics,
            "derived_decision_sha256": digest(derived),
        })
