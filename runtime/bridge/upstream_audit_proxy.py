"""Metadata-only audit hop inside the Docker LiteLLM gateway.

The hop accepts OpenAI Chat Completions from LiteLLM on loopback,
adapts the unsupported propertyNames schema keyword for Gemini and records hashes and
response model/tool-call metadata. It does not convert protocols. Claude Code
still talks to LiteLLM's separate Anthropic Messages listener.

Configuration (environment, never command-line secrets)::

    PILOT_QR_AUDIT_CLIENT_TOKEN   token configured as the LiteLLM provider key
    PILOT_QR_AUDIT_UPSTREAM_KEY   QuickRouter API key
    PILOT_QR_AUDIT_LOG_PATH       append-only JSONL path
    PILOT_QR_AUDIT_PORT           optional loopback port, default 17843
    PILOT_QR_AUDIT_TIMEOUT        optional upstream timeout, default 900 seconds
    PILOT_QR_AUDIT_EXPECTED_MARKER optional isolated probe marker (ASCII token)

Set the LiteLLM custom_openai base URL to ``http://127.0.0.1:17843/v1``.
This module is deliberately a separate, optional route component. A campaign
must hash and freeze its exact file bytes and inspect the resulting audit rows
before treating any local Claude model alias as an upstream model identity.
"""
from __future__ import annotations

import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import sys
import threading
from typing import Any, BinaryIO
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import uuid
from datetime import datetime, timezone


EXPECTED_MODEL = os.environ.get("MHB_MODEL", "gemini-2.5-flash")
UPSTREAM_URL = os.environ.get("MHB_UPSTREAM_URL", "")
MAX_REQUEST_BYTES = 32 * 1024 * 1024
MAX_RESPONSE_BYTES = 64 * 1024 * 1024
MAX_SSE_LINE_BYTES = 4 * 1024 * 1024
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
MARKER = re.compile(r"[A-Za-z0-9_-]{8,128}\Z")
AUDIT_WRITE_LOCK = threading.Lock()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def redact_error_body(content: bytes, key: str) -> bytes:
    """Provider diagnostics must never echo the upstream credential to the agent."""
    return content.replace(key.encode(), b"[REDACTED]") if key else content


def parse_request(body: bytes) -> tuple[str, bool]:
    """Reject unexpected models before the paid upstream request."""
    value = json.loads(body)
    if not isinstance(value, dict) or value.get("model") != EXPECTED_MODEL:
        raise ValueError("request.model must be the frozen Gemini model")
    if not isinstance(value.get("messages"), list):
        raise ValueError("OpenAI Chat Completions messages must be a list")
    stream = value.get("stream", False)
    if not isinstance(stream, bool):
        raise ValueError("request.stream must be boolean")
    return value["model"], stream


def adapt_gemini_tool_schemas(body: bytes) -> tuple[bytes, list[str]]:
    """Remove only unsupported schema keywords, never message text or tool values.

    Gemini Vertex rejects JSON Schema propertyNames. Removing it weakens that
    wire-level schema constraint; the original Claude tool definition and local
    tool execution remain unchanged. Record every removed path for provenance.
    """
    value = json.loads(body)
    removed: list[str] = []

    def visit(schema: Any, path: str) -> None:
        if not isinstance(schema, dict):
            return
        if "propertyNames" in schema:
            del schema["propertyNames"]
            removed.append(path + "/propertyNames")
        for name in ("properties", "patternProperties", "$defs", "definitions", "dependentSchemas"):
            mapping = schema.get(name)
            if isinstance(mapping, dict):
                for key, child in mapping.items():
                    visit(child, path + "/" + name + "/" + key)
        for name in ("items", "additionalProperties", "contains", "if", "then", "else", "not", "unevaluatedProperties"):
            child = schema.get(name)
            if isinstance(child, list):
                for index, item in enumerate(child):
                    visit(item, path + "/" + name + "/" + str(index))
            else:
                visit(child, path + "/" + name)
        for name in ("allOf", "anyOf", "oneOf", "prefixItems"):
            if isinstance(schema.get(name), list):
                for index, child in enumerate(schema[name]):
                    visit(child, path + "/" + name + "/" + str(index))

    for index, tool in enumerate(value.get("tools", [])):
        if isinstance(tool, dict) and isinstance(tool.get("function"), dict):
            visit(tool["function"].get("parameters"), f"tools/{index}/function/parameters")
    return (json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8") if removed else body), removed


def marker_in_tool_result(body: bytes, marker: str) -> bool:
    """Recognize a marker only in a subsequent OpenAI tool/function result."""
    if not marker:
        return False
    value = json.loads(body)
    messages = value.get("messages", []) if isinstance(value, dict) else []
    if not isinstance(messages, list):
        return False
    for message in messages:
        if not isinstance(message, dict) or message.get("role") not in ("tool", "function"):
            continue
        content = message.get("content")
        if isinstance(content, str) and marker in content:
            return True
        if isinstance(content, list) and marker in json.dumps(content, ensure_ascii=False):
            return True
    return False


class ResponseMetadata:
    """Extract model and tool-call identity without storing message content."""

    def __init__(self) -> None:
        self.models: set[str] = set()
        self.completion_ids: set[str] = set()
        self.calls: dict[tuple[int, int], dict[str, Any]] = {}
        self._sse_data: list[str] = []
        self.parse_errors = 0

    def _tool(self, choice_index: int, tool_index: int, item: dict[str, Any],
              *, delta: bool) -> None:
        call = self.calls.setdefault((choice_index, tool_index), {
            "id": "", "name": "", "_arguments_hash": hashlib.sha256(),
            "arguments_bytes": 0, "arguments_seen": False,
        })
        tool_id = item.get("id")
        if isinstance(tool_id, str) and tool_id:
            if delta and call["id"] and not tool_id.startswith(call["id"]):
                call["id"] += tool_id
            else:
                call["id"] = tool_id
        function = item.get("function")
        if isinstance(function, dict):
            name = function.get("name")
            if isinstance(name, str) and name:
                if delta and call["name"] and not name.startswith(call["name"]):
                    call["name"] += name
                else:
                    call["name"] = name
            arguments = function.get("arguments")
            if isinstance(arguments, str):
                encoded = arguments.encode("utf-8")
                call["_arguments_hash"].update(encoded)
                call["arguments_bytes"] += len(encoded)
                call["arguments_seen"] = True
            elif arguments is not None:
                self.parse_errors += 1

    def observe(self, value: Any, *, delta: bool = False) -> None:
        if not isinstance(value, dict):
            self.parse_errors += 1
            return
        model = value.get("model")
        if isinstance(model, str) and model:
            self.models.add(model)
        completion_id = value.get("id")
        if isinstance(completion_id, str) and completion_id:
            self.completion_ids.add(completion_id)
        choices = value.get("choices", [])
        if not isinstance(choices, list):
            self.parse_errors += 1
            return
        for ordinal, choice in enumerate(choices):
            if not isinstance(choice, dict):
                self.parse_errors += 1
                continue
            choice_index = choice.get("index", ordinal)
            if not isinstance(choice_index, int) or choice_index < 0:
                self.parse_errors += 1
                continue
            message = choice.get("delta" if delta else "message", {})
            if not isinstance(message, dict):
                continue
            tool_calls = message.get("tool_calls", [])
            if not isinstance(tool_calls, list):
                continue
            for tool_ordinal, item in enumerate(tool_calls):
                if not isinstance(item, dict):
                    self.parse_errors += 1
                    continue
                tool_index = item.get("index", tool_ordinal)
                if not isinstance(tool_index, int) or tool_index < 0:
                    self.parse_errors += 1
                    continue
                self._tool(choice_index, tool_index, item, delta=delta)

    def observe_sse_line(self, line: bytes) -> None:
        stripped = line.strip()
        if not stripped:
            self._dispatch_sse()
            return
        if stripped.startswith(b"data:"):
            try:
                self._sse_data.append(stripped[5:].decode("utf-8").lstrip())
            except UnicodeDecodeError:
                self.parse_errors += 1

    def _dispatch_sse(self) -> None:
        if not self._sse_data:
            return
        data = "\n".join(self._sse_data)
        self._sse_data.clear()
        if data == "[DONE]":
            return
        try:
            self.observe(json.loads(data), delta=True)
        except json.JSONDecodeError:
            self.parse_errors += 1

    def finish_sse(self) -> None:
        self._dispatch_sse()

    def to_record(self) -> dict[str, Any]:
        return {
            "response_models": sorted(self.models),
            "upstream_model_exact": self.models == {EXPECTED_MODEL},
            "upstream_completion_ids": sorted(self.completion_ids),
            "response_tool_calls": [
                {"choice_index": choice, "tool_index": index,
                 "id": call["id"], "name": call["name"],
                 "arguments_sha256": (call["_arguments_hash"].hexdigest()
                                      if call["arguments_seen"] else None),
                 "arguments_bytes": call["arguments_bytes"]}
                for (choice, index), call in sorted(self.calls.items())
            ],
            "metadata_parse_errors": self.parse_errors,
        }


def append_audit(path: Path, record: dict[str, Any]) -> None:
    """Serialize append-only rows across keep-alive request threads."""
    path.parent.mkdir(parents=True, exist_ok=True)
    line = (json.dumps(record, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":")) + "\n").encode("utf-8")
    with AUDIT_WRITE_LOCK:
        descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            if os.write(descriptor, line) != len(line):
                raise OSError("partial audit write")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def build_probe_summary(log_path: Path) -> dict[str, Any]:
    """Summarize one isolated probe's ordered, metadata-only audit log.

    A tool-call response must precede a later request carrying the native
    tool-result marker. The caller must independently verify that marker in
    Claude's native tool_result and that this log covers only the probe window.
    """
    raw = log_path.read_bytes()
    rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
    if len(rows) < 2 or not all(isinstance(row, dict) for row in rows):
        raise ValueError("isolated probe needs at least two audit records")
    ids = [row.get("audit_request_id") for row in rows]
    if any(not isinstance(item, str) or not item for item in ids) or len(set(ids)) != len(ids):
        raise ValueError("audit request IDs are missing or repeated")
    if any(row.get("request_model") != EXPECTED_MODEL
           or row.get("http_status") != 200
           or row.get("outcome") != "completed"
           or row.get("upstream_model_exact") is not True
           or row.get("metadata_parse_errors") != 0
           for row in rows):
        raise ValueError("probe has non-Gemini or incomplete upstream response")
    marker_hashes = {row.get("marker_sha256") for row in rows}
    if len(marker_hashes) != 1 or next(iter(marker_hashes)) is None:
        raise ValueError("probe marker is absent or changed between requests")
    marker_hash = next(iter(marker_hashes))
    if not isinstance(marker_hash, str) or SHA256.fullmatch(marker_hash) is None:
        raise ValueError("probe marker hash is malformed")
    tool_index = next((index for index, row in enumerate(rows)
                       if any(isinstance(call, dict)
                              and call.get("id") and call.get("name")
                              and isinstance(call.get("arguments_sha256"), str)
                              and SHA256.fullmatch(call["arguments_sha256"])
                              for call in row.get("response_tool_calls", []))), None)
    if tool_index is None:
        raise ValueError("upstream tool call with hashed arguments is missing")
    marker_index = next((index for index in range(tool_index + 1, len(rows))
                         if rows[index].get("request_marker_present") is True), None)
    if marker_index is None:
        raise ValueError("later request carrying native tool-result marker is missing")
    tool_row = rows[tool_index]
    return {
        "schema_version": "full288-upstream-probe-summary-r1",
        "bridge_request_id": tool_row["audit_request_id"],
        "request_model": tool_row["request_model"],
        "response": {"model": EXPECTED_MODEL},
        "response_tool_calls": tool_row["response_tool_calls"],
        "upstream_completion_ids": tool_row.get("upstream_completion_ids", []),
        "marker_request_id": rows[marker_index]["audit_request_id"],
        "request_marker_present": True,
        "marker_sha256": marker_hash,
        "audit_request_ids": ids,
        "audit_order": {"tool_call_index": tool_index,
                        "marker_request_index": marker_index},
        "raw_audit_log_sha256": sha256(raw),
        "audit_record_count": len(rows),
        "linkage_scope": "isolated_probe_time_order_and_unique_marker",
    }


class AuditHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True

    def __init__(self, port: int, *, client_token: str, upstream_key: str,
                 log_path: Path, timeout: int = 900,
                 expected_marker: str = "") -> None:
        if not (1 <= port <= 65535):
            raise ValueError("invalid audit port")
        if not client_token or not upstream_key:
            raise ValueError("both audit route credentials are required")
        if not (1 <= timeout <= 3600):
            raise ValueError("invalid upstream timeout")
        if not isinstance(log_path, Path):
            raise TypeError("log_path must be a Path")
        if expected_marker and MARKER.fullmatch(expected_marker) is None:
            raise ValueError("probe marker must be a single ASCII token")
        self.client_token = client_token
        self.upstream_key = upstream_key
        self.log_path = log_path
        self.upstream_timeout = timeout
        self.expected_marker = expected_marker
        super().__init__(("127.0.0.1", port), AuditHandler)


class AuditHandler(BaseHTTPRequestHandler):
    server: AuditHTTPServer
    protocol_version = "HTTP/1.1"

    def log_message(self, _format: str, *_args: object) -> None:
        # Never echo headers, request bodies, keys, or upstream errors.
        return

    def _json_error(self, status: int, message: str) -> None:
        body = json.dumps({"error": {"message": message, "type": "audit_proxy_error"}}).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path != "/health":
            self._json_error(404, "not found")
            return
        self._json_error(200, "ready")

    def do_POST(self) -> None:
        if self.path not in ("/v1/chat/completions", "/chat/completions"):
            self._json_error(404, "unsupported endpoint")
            return
        expected = "Bearer " + self.server.client_token
        if not secrets.compare_digest(self.headers.get("Authorization", ""), expected):
            self._json_error(401, "unauthorized")
            return
        raw_length = self.headers.get("Content-Length", "")
        try:
            length = int(raw_length)
        except ValueError:
            self._json_error(411, "Content-Length required")
            return
        if not (0 < length <= MAX_REQUEST_BYTES):
            self._json_error(413, "invalid request size")
            return
        body = self.rfile.read(length)
        if len(body) != length:
            self._json_error(400, "incomplete request")
            return
        try:
            model, stream = parse_request(body)
        except (ValueError, json.JSONDecodeError):
            self._json_error(400, "invalid or unfrozen OpenAI request")
            return

        original_body_sha256 = sha256(body)
        body, removed_schema_paths = adapt_gemini_tool_schemas(body) if EXPECTED_MODEL == "gemini-2.5-flash" else (body, [])

        audit_id = uuid.uuid4().hex
        record: dict[str, Any] = {
            "schema_version": "full288-upstream-audit-r1",
            "audit_request_id": audit_id,
            "started_at_utc": utc_now(),
            "request_model": model,
            "request_sha256": sha256(body),
            "pre_schema_adaptation_sha256": original_body_sha256,
            "schema_adaptation": "gemini-propertyNames-r1",
            "removed_schema_paths": removed_schema_paths,
            "request_stream": stream,
            "upstream_endpoint": UPSTREAM_URL,
            "request_marker_present": marker_in_tool_result(
                body, self.server.expected_marker),
            "marker_sha256": (sha256(self.server.expected_marker.encode("ascii"))
                              if self.server.expected_marker else None),
        }
        metadata = ResponseMetadata()
        response_hash = hashlib.sha256()
        status = 0
        outcome = "transport_error"
        headers_sent = False
        upstream: BinaryIO | None = None
        try:
            # Fail before the paid call if an audit row could never be written.
            self.server.log_path.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(self.server.log_path,
                                 os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            os.close(descriptor)
            request = Request(UPSTREAM_URL, data=body, headers={
                "Authorization": "Bearer " + self.server.upstream_key,
                "Content-Type": "application/json",
                "Accept": "text/event-stream" if stream else "application/json",
                "Accept-Encoding": "identity",
                "X-Pilot-Audit-Id": audit_id,
            }, method="POST")
            if os.environ.get("MHB_UPSTREAM_HOST"):
                request.add_header("Host", os.environ["MHB_UPSTREAM_HOST"])
            try:
                upstream = urlopen(request, timeout=self.server.upstream_timeout)
            except HTTPError as error:
                upstream = error
            status = upstream.status
            record["upstream_request_id"] = upstream.headers.get("x-request-id")
            content_type = upstream.headers.get("Content-Type", "application/json")
            content_encoding = upstream.headers.get("Content-Encoding", "identity").lower()
            if content_encoding != "identity":
                raise ValueError("compressed upstream response cannot be audited")
            is_sse = status == 200 and stream and "text/event-stream" in content_type.lower()
            record["response_stream"] = is_sse
            if not is_sse:
                content = upstream.read(MAX_RESPONSE_BYTES + 1)
                if len(content) > MAX_RESPONSE_BYTES:
                    raise ValueError("upstream response exceeds audit limit")
                response_hash.update(content)
                if status >= 400:
                    content = redact_error_body(content, self.server.upstream_key)
                try:
                    metadata.observe(json.loads(content))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    metadata.parse_errors += 1
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("X-Pilot-Audit-Id", audit_id)
            self.send_header("Cache-Control", "no-store")
            if is_sse:
                self.send_header("Transfer-Encoding", "chunked")
            else:
                self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            headers_sent = True
            if is_sse:
                total_bytes = 0
                while True:
                    line = upstream.readline(MAX_SSE_LINE_BYTES + 1)
                    if not line:
                        break
                    total_bytes += len(line)
                    if (len(line) > MAX_SSE_LINE_BYTES
                            or (len(line) == MAX_SSE_LINE_BYTES and not line.endswith(b"\n"))
                            or total_bytes > MAX_RESPONSE_BYTES):
                        raise ValueError("SSE line exceeds audit limit")
                    response_hash.update(line)
                    metadata.observe_sse_line(line)
                    self.wfile.write(f"{len(line):X}\r\n".encode("ascii") + line + b"\r\n")
                    self.wfile.flush()
                metadata.finish_sse()
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
            else:
                self.wfile.write(content)
            outcome = "upstream_http" if status >= 400 else "completed"
        except (URLError, TimeoutError):
            if not headers_sent:
                self._json_error(502, "upstream transport failed")
            else:
                self.close_connection = True
            outcome = "transport_error"
        except (BrokenPipeError, ConnectionResetError):
            outcome = "downstream_disconnected"
        except (OSError, ValueError):
            if not headers_sent:
                self._json_error(502, "upstream audit failed")
            else:
                self.close_connection = True
            outcome = "audit_error"
        finally:
            if upstream is not None:
                upstream.close()
            record.update({
                "finished_at_utc": utc_now(), "http_status": status,
                "outcome": outcome,
                "response_sha256": response_hash.hexdigest(),
                **metadata.to_record(),
            })
            append_audit(self.server.log_path, record)


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if arguments:
        if len(arguments) != 3 or arguments[0] != "summary":
            raise ValueError("usage: upstream_audit_proxy.py summary AUDIT.jsonl SUMMARY.json")
        summary = build_probe_summary(Path(arguments[1]))
        output = Path(arguments[2])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
                          encoding="utf-8")
        return 0
    token = os.environ.get("PILOT_QR_AUDIT_CLIENT_TOKEN", "")
    key = os.environ.get("PILOT_QR_AUDIT_UPSTREAM_KEY", "")
    path_text = os.environ.get("PILOT_QR_AUDIT_LOG_PATH", "")
    if not path_text:
        raise ValueError("PILOT_QR_AUDIT_LOG_PATH is required")
    port = int(os.environ.get("PILOT_QR_AUDIT_PORT", "17843"))
    timeout = int(os.environ.get("PILOT_QR_AUDIT_TIMEOUT", "900"))
    server = AuditHTTPServer(port, client_token=token, upstream_key=key,
                             log_path=Path(path_text), timeout=timeout,
                             expected_marker=os.environ.get(
                                 "PILOT_QR_AUDIT_EXPECTED_MARKER", ""))
    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
