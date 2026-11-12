"""Fetch and verify the fixed HF input snapshot without executing its contents.

Only the Python standard library is needed. Dataset bytes come exclusively
from the pinned Hugging Face release, never from Git or a code repository.
"""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import stat
import tempfile
from typing import Any, Mapping
import urllib.error
import urllib.request
import zipfile

HF_REPO = "kirco567/MemHarmBench"
HF_REVISION = "2b4c2b736fdbabf7e56182870d71095ac38afdb0"
MANIFEST_SHA256 = "8d563ed1ca476d5a5236948387525731ee89207bcf9d8032f05dbb7d60d56c17"
ARCHIVE_SHA256 = "fa2c2e7cfab33ac2f0aac0f056aa8a7cfc0e3e9c003db71053037b4998b7b560"
STATE_DIRECTORY = ".memharmbench"
DATA_PREFIXES = (
    "cases/", "artifacts/", "data/", "catalogs/",
    "experiments/semantic_alignment/dataset/",
    "experiments/exposure_distance/dataset/",
)
SOURCE_ONLY_FILES = {"LICENSE", "THIRD_PARTY_NOTICES.md"}
MAX_DOWNLOAD_BYTES = 16 * 1024 * 1024


def _sha256(data: bytes) -> str:
    import hashlib
    return hashlib.sha256(data).hexdigest()


def installable_path(path: str) -> bool:
    return path == "release_lock.json" or path.startswith(DATA_PREFIXES)


def _portable_path(path: str) -> str:
    if (not path or "\\" in path or ":" in path
            or any(ord(char) < 32 for char in path)
            or any(part in {"", ".", ".."} or part.endswith((" ", "."))
                   for part in path.split("/"))):
        raise ValueError("dataset archive contains an unsafe relative path")
    return path


def _destination(root: Path, relative: str) -> Path:
    from .identity import safe_relative_path
    return safe_relative_path(root, _portable_path(relative))


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".dataset-", delete=False) as stream:
            temporary = stream.name
            stream.write(data)
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def verified_manifest(data: bytes) -> dict[str, Any]:
    if _sha256(data) != MANIFEST_SHA256:
        raise ValueError("HF dataset manifest SHA256 mismatch")
    manifest = json.loads(data.decode("utf-8"))
    if not isinstance(manifest, dict) or not isinstance(manifest.get("source_files"), dict):
        raise ValueError("invalid HF dataset source manifest")
    sources = manifest["source_files"]
    if manifest.get("source_file_count") != len(sources):
        raise ValueError("HF source manifest file count mismatch")
    if manifest.get("source_size_bytes") != sum(entry["size_bytes"] for entry in sources.values()):
        raise ValueError("HF source manifest byte count mismatch")
    archive = manifest["source_archive"]
    if archive["path"] != "source_snapshot.zip" or archive["sha256"] != ARCHIVE_SHA256:
        raise ValueError("HF source manifest archive identity mismatch")
    folded: set[str] = set()
    for path in sources:
        _portable_path(path)
        if not installable_path(path) and path not in SOURCE_ONLY_FILES:
            raise ValueError("HF source manifest contains a non-data path")
        if path.casefold() in folded:
            raise ValueError("HF source manifest contains colliding paths")
        folded.add(path.casefold())
    if "release_lock.json" not in sources:
        raise ValueError("HF source manifest is missing its original release lock")
    return manifest


def verified_source_files(manifest_bytes: bytes, archive_bytes: bytes) -> tuple[dict[str, Any], dict[str, bytes]]:
    """Verify the whole archive before considering any filesystem writes."""
    manifest = verified_manifest(manifest_bytes)
    if (_sha256(archive_bytes) != ARCHIVE_SHA256
            or len(archive_bytes) != manifest["source_archive"]["size_bytes"]):
        raise ValueError("HF dataset ZIP SHA256 or size mismatch")
    files: dict[str, bytes] = {}
    try:
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
            for member in archive.infolist():
                path = _portable_path(member.filename)
                mode = member.external_attr >> 16
                if member.is_dir() or stat.S_IFMT(mode) not in {0, stat.S_IFREG}:
                    raise ValueError("dataset archive contains a symlink or non-regular entry")
                if path in files or path not in manifest["source_files"]:
                    raise ValueError("dataset archive contains a duplicate or unexpected path")
                expected = manifest["source_files"][path]
                if member.file_size != expected["size_bytes"]:
                    raise ValueError("dataset archive member size mismatch")
                content = archive.read(member)
                if _sha256(content) != expected["sha256"]:
                    raise ValueError("dataset archive member SHA256 mismatch")
                files[path] = content
    except zipfile.BadZipFile as error:
        raise ValueError("invalid HF dataset ZIP") from error
    if set(files) != set(manifest["source_files"]):
        raise ValueError("dataset archive is missing source files")
    return manifest, files


def _register_local_identity(root: Path, manifest_bytes: bytes) -> dict[str, Any]:
    from .identity import (
        build_release_lock, compute_release_lock_sha256, evaluation_policy_hashes,
        canonical_json, sha256_file,
    )
    source_lock = json.loads(_destination(root, "release_lock.json").read_bytes())
    if source_lock.get("release_lock_sha256") != compute_release_lock_sha256(source_lock):
        raise ValueError("downloaded source release lock self-hash mismatch")
    if evaluation_policy_hashes(root) != source_lock["evaluation_policy_files"]:
        raise ValueError("current evaluation policy differs from the pinned dataset; use compatible code")
    local_lock = build_release_lock(
        root, source_lock, case_ids=source_lock["case_order"],
        case_input_fingerprints=source_lock["case_input_fingerprints"],
        case_contracts=source_lock["case_judge_contracts"],
        policy_hashes=source_lock["evaluation_policy_files"],
    )
    # This lock binds the downloaded immutable inputs to this installed code.
    # The original source lock and all case/input bytes remain untouched.
    _atomic_write(_destination(root, STATE_DIRECTORY + "/release_lock.json"),
                  json.dumps(local_lock, ensure_ascii=False, indent=2).encode("utf-8") + b"\n")
    manifest = verified_manifest(manifest_bytes)
    metadata = {
        "schema_version": "memharmbench-hf-download-1",
        "hf_repo": HF_REPO, "hf_revision": HF_REVISION,
        "manifest_sha256": MANIFEST_SHA256, "archive_sha256": ARCHIVE_SHA256,
        "source_commit": manifest["source_commit"],
        "source_release_lock_file_sha256": sha256_file(root / "release_lock.json"),
        "source_release_lock_sha256": source_lock["release_lock_sha256"],
        "local_release_lock_sha256": local_lock["release_lock_sha256"],
        "code_identity_sha256": _sha256(canonical_json(local_lock["code_files"])),
        "identity_policy": "original source lock and inputs preserved; local lock binds current code",
    }
    _atomic_write(_destination(root, STATE_DIRECTORY + "/source_manifest.json"), manifest_bytes)
    _atomic_write(_destination(root, STATE_DIRECTORY + "/dataset_source.json"),
                  json.dumps(metadata, ensure_ascii=False, indent=2).encode("utf-8") + b"\n")
    return metadata


def install_snapshot(root: Path, manifest_bytes: bytes, archive_bytes: bytes, *, force: bool = False) -> dict[str, Any]:
    manifest, files = verified_source_files(manifest_bytes, archive_bytes)
    root = root.expanduser().resolve()
    selected = {path: data for path, data in files.items() if installable_path(path)}
    plan: list[tuple[Path, bytes]] = []
    # Preflight every destination, including identity/cache paths, before writes.
    for relative in (STATE_DIRECTORY + "/source_manifest.json", STATE_DIRECTORY + "/dataset_source.json",
                     STATE_DIRECTORY + "/release_lock.json"):
        _destination(root, relative)
    for relative, data in sorted(selected.items()):
        destination = _destination(root, relative)
        for parent in destination.parents:
            if parent == root.parent:
                break
            if parent.exists() and not parent.is_dir():
                raise ValueError("dataset destination is blocked by an existing file")
        if destination.exists():
            if not destination.is_file():
                raise ValueError("dataset destination is not a regular file")
            if destination.read_bytes() == data:
                continue
            if not force:
                raise ValueError(f"existing dataset file differs: {relative}; use --force to replace dataset files")
        plan.append((destination, data))
    for destination, data in plan:
        _atomic_write(destination, data)
    metadata = _register_local_identity(root, manifest_bytes)
    return {"downloaded": True, "hf_repo": HF_REPO, "hf_revision": HF_REVISION,
            "installed_files": len(selected), "source_files": manifest["source_file_count"],
            "dataset_input_fingerprint": json.loads(files["release_lock.json"])["dataset_input_fingerprint"],
            "local_release_lock_sha256": metadata["local_release_lock_sha256"]}


def _fetch(filename: str) -> bytes:
    url = f"https://huggingface.co/datasets/{HF_REPO}/resolve/{HF_REVISION}/{filename}"
    request = urllib.request.Request(url, headers={"User-Agent": "MemHarmBench/8.4.3"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            content = response.read(MAX_DOWNLOAD_BYTES + 1)
    except (OSError, urllib.error.URLError) as error:
        raise ValueError("could not fetch the fixed Hugging Face dataset; check the connection or use a verified --offline cache") from error
    if len(content) > MAX_DOWNLOAD_BYTES:
        raise ValueError("HF dataset download exceeds the allowed size")
    return content


def download_dataset(root: Path, *, force: bool = False, offline: bool = False) -> dict[str, Any]:
    root = root.expanduser().resolve()
    manifest_path = _destination(root, STATE_DIRECTORY + "/cache/manifest.json")
    archive_path = _destination(root, STATE_DIRECTORY + "/cache/source_snapshot.zip")
    if offline:
        if not manifest_path.is_file() or not archive_path.is_file():
            raise ValueError("verified dataset cache is missing; run memharmbench download once with network access")
        manifest_bytes, archive_bytes = manifest_path.read_bytes(), archive_path.read_bytes()
    else:
        manifest_bytes, archive_bytes = _fetch("manifest.json"), _fetch("source_snapshot.zip")
    report = install_snapshot(root, manifest_bytes, archive_bytes, force=force)
    if not offline:
        _atomic_write(manifest_path, manifest_bytes)
        _atomic_write(archive_path, archive_bytes)
    return report


def verify_download_source(root: Path, local_lock: Mapping[str, Any]) -> None:
    """Anchor local identity to the pinned HF source without a network call."""
    metadata = json.loads(_destination(root, STATE_DIRECTORY + "/dataset_source.json").read_bytes())
    if (metadata.get("schema_version") != "memharmbench-hf-download-1"
            or metadata.get("hf_repo") != HF_REPO or metadata.get("hf_revision") != HF_REVISION
            or metadata.get("manifest_sha256") != MANIFEST_SHA256
            or metadata.get("archive_sha256") != ARCHIVE_SHA256
            or metadata.get("local_release_lock_sha256") != local_lock.get("release_lock_sha256")):
        raise ValueError("local HF dataset provenance or code identity changed; rerun memharmbench download")
    manifest = verified_manifest(_destination(root, STATE_DIRECTORY + "/source_manifest.json").read_bytes())
    from .identity import canonical_json
    if (metadata.get("source_commit") != manifest["source_commit"]
            or metadata.get("source_release_lock_file_sha256") != manifest["source_files"]["release_lock.json"]["sha256"]
            or metadata.get("code_identity_sha256") != _sha256(canonical_json(local_lock["code_files"]))):
        raise ValueError("local HF source/code identity metadata changed; rerun memharmbench download")
    for relative, expected in manifest["source_files"].items():
        if installable_path(relative):
            path = _destination(root, relative)
            if not path.is_file() or _sha256(path.read_bytes()) != expected["sha256"]:
                raise ValueError(f"downloaded input differs from the pinned HF release: {relative}")
    source_lock = json.loads(_destination(root, "release_lock.json").read_bytes())
    if metadata.get("source_release_lock_sha256") != source_lock["release_lock_sha256"]:
        raise ValueError("local HF source release identity metadata changed")
    ignored = {"code_files", "release_lock_sha256"}
    if ({k: v for k, v in source_lock.items() if k not in ignored}
            != {k: v for k, v in local_lock.items() if k not in ignored}):
        raise ValueError("local release lock changed the original dataset/evaluation identity")
