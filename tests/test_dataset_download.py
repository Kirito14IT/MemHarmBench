"""Synthetic, offline checks for the HF-only dataset downloader."""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import warnings
import zipfile

from memharmbench_v84 import dataset_download as download
from memharmbench_v84.cli import main


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def bundle(files: dict[str, bytes] | None = None, *, members: list[tuple[str, bytes, int]] | None = None,
           expected: dict[str, bytes] | None = None) -> tuple[bytes, bytes]:
    files = files or {
        "cases/fixture.json": b'{"case_id":"fixture"}\n',
        "data/reference.txt": b"synthetic reference fixture\n",
        "release_lock.json": b'{"dataset_input_fingerprint":"fixture"}\n',
        "LICENSE": b"archive license fixture\n",
        "THIRD_PARTY_NOTICES.md": b"archive notices fixture\n",
    }
    out = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(out, "w") as archive:
            for path, content, mode in members or [(p, b, 0o100644) for p, b in files.items()]:
                info = zipfile.ZipInfo(path)
                info.create_system = 3
                info.external_attr = mode << 16
                archive.writestr(info, content)
    archive_bytes = out.getvalue()
    source = expected or files
    manifest = {
        "source_commit": "synthetic-source", "source_file_count": len(source),
        "source_size_bytes": sum(map(len, source.values())),
        "source_files": {p: {"sha256": sha(b), "size_bytes": len(b)} for p, b in source.items()},
        "source_archive": {"path": "source_snapshot.zip", "sha256": sha(archive_bytes),
                           "size_bytes": len(archive_bytes)},
    }
    return json.dumps(manifest).encode(), archive_bytes


@contextlib.contextmanager
def pinned(manifest: bytes, archive: bytes):
    with patch.multiple(download, MANIFEST_SHA256=sha(manifest), ARCHIVE_SHA256=sha(archive)):
        yield


class DatasetDownloadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="memharmbench-download-test-")
        self.root = Path(self.temporary.name) / "clone"
        self.root.mkdir()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def register(self):
        return patch.object(download, "_register_local_identity",
                            return_value={"local_release_lock_sha256": "synthetic-code-lock"})

    def test_byte_exact_data_install_never_overwrites_code_license_or_readme(self) -> None:
        manifest, archive = bundle()
        for name in ("README.md", "LICENSE", "THIRD_PARTY_NOTICES.md", "src/module.py"):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"existing code-repository file\n")
        with pinned(manifest, archive), self.register():
            report = download.install_snapshot(self.root, manifest, archive)
            download.install_snapshot(self.root, manifest, archive)  # idempotent
        self.assertEqual(report["installed_files"], 3)
        self.assertEqual((self.root / "cases/fixture.json").read_bytes(), b'{"case_id":"fixture"}\n')
        for name in ("README.md", "LICENSE", "THIRD_PARTY_NOTICES.md", "src/module.py"):
            self.assertEqual((self.root / name).read_bytes(), b"existing code-repository file\n")

    def test_hash_failures_are_rejected_before_writes(self) -> None:
        manifest, archive = bundle()
        with pinned(manifest, archive), self.register():
            for bad_manifest, bad_archive in ((manifest + b" ", archive), (manifest, archive + b"x")):
                with self.assertRaisesRegex(ValueError, "SHA256"):
                    download.install_snapshot(self.root, bad_manifest, bad_archive)
                self.assertEqual(list(self.root.iterdir()), [])
        expected = {"release_lock.json": b"old bytes"}
        manifest, archive = bundle({"release_lock.json": b"new bytes"}, expected=expected)
        with pinned(manifest, archive), self.register():
            with self.assertRaisesRegex(ValueError, "SHA256"):
                download.install_snapshot(self.root, manifest, archive)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_unsafe_or_nondata_paths_rejected_even_with_valid_bundle_hash(self) -> None:
        for path in ("../escape", "/absolute", "data/../escape", "C:/escape", "data\\escape",
                     "src/module.py", "README.md", "runtime/file.json", ".git/config"):
            with self.subTest(path=path):
                manifest, archive = bundle({path: b"fixture", "release_lock.json": b"{}"})
                with pinned(manifest, archive), self.register(), self.assertRaises(ValueError):
                    download.install_snapshot(self.root, manifest, archive, force=True)
                self.assertEqual(list(self.root.iterdir()), [])

    def test_symlink_duplicate_missing_or_colliding_members_rejected(self) -> None:
        files = {"data/link": b"target", "release_lock.json": b"{}"}
        variants = [
            [("data/link", b"target", 0o120777), ("release_lock.json", b"{}", 0o100644)],
            [("data/link", b"target", 0o100644), ("data/link", b"target", 0o100644),
             ("release_lock.json", b"{}", 0o100644)],
            [("release_lock.json", b"{}", 0o100644)],
        ]
        for members in variants:
            manifest, archive = bundle(files, members=members)
            with pinned(manifest, archive), self.register(), self.assertRaises(ValueError):
                download.install_snapshot(self.root, manifest, archive)
        manifest, archive = bundle({"data/A.json": b"a", "data/a.json": b"a", "release_lock.json": b"{}"})
        with pinned(manifest, archive), self.assertRaisesRegex(ValueError, "colliding"):
            download.verified_source_files(manifest, archive)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_existing_different_data_requires_explicit_force(self) -> None:
        manifest, archive = bundle()
        target = self.root / "cases/fixture.json"
        target.parent.mkdir()
        target.write_bytes(b"local edit")
        with pinned(manifest, archive), self.register():
            with self.assertRaisesRegex(ValueError, "--force"):
                download.install_snapshot(self.root, manifest, archive)
            self.assertFalse((self.root / "data").exists(), "preflight must finish before writing")
            self.assertEqual(target.read_bytes(), b"local edit")
            download.install_snapshot(self.root, manifest, archive, force=True)
        self.assertEqual(target.read_bytes(), b'{"case_id":"fixture"}\n')

    def test_existing_destination_symlink_cannot_redirect_writes(self) -> None:
        outside = Path(self.temporary.name) / "outside"
        outside.mkdir()
        try:
            (self.root / "data").symlink_to(outside, target_is_directory=True)
        except OSError:
            # Windows commonly denies symlink creation to CI accounts. Exercise
            # the same fail-closed parent check without requiring that privilege.
            (self.root / "data").mkdir()
            original = Path.is_symlink
            with patch.object(Path, "is_symlink", lambda path: path == self.root / "data" or original(path)):
                manifest, archive = bundle()
                with pinned(manifest, archive), self.register(), self.assertRaises(ValueError):
                    download.install_snapshot(self.root, manifest, archive, force=True)
            self.assertEqual(list(outside.iterdir()), [])
            return
        manifest, archive = bundle()
        with pinned(manifest, archive), self.register(), self.assertRaises(ValueError):
            download.install_snapshot(self.root, manifest, archive, force=True)
        self.assertEqual(list(outside.iterdir()), [])

    def test_network_reads_only_the_two_pinned_hf_files_and_offline_uses_verified_cache(self) -> None:
        manifest, archive = bundle()
        with pinned(manifest, archive), self.register():
            with patch.object(download, "_fetch", side_effect=[manifest, archive]) as fetch:
                download.download_dataset(self.root)
            self.assertEqual([call.args[0] for call in fetch.call_args_list], ["manifest.json", "source_snapshot.zip"])
            with patch.object(download, "_fetch", side_effect=AssertionError("network forbidden")):
                download.download_dataset(self.root, offline=True)
                (self.root / ".memharmbench/cache/source_snapshot.zip").write_bytes(b"corrupted cache")
                with self.assertRaisesRegex(ValueError, "SHA256"):
                    download.download_dataset(self.root, offline=True)

    def test_fetch_url_is_fixed_hf_revision_and_has_no_github_fallback(self) -> None:
        response = unittest.mock.MagicMock()
        response.__enter__.return_value.read.return_value = b"fixture"
        with patch.object(download.urllib.request, "urlopen", return_value=response) as request:
            self.assertEqual(download._fetch("manifest.json"), b"fixture")
        url = request.call_args.args[0].full_url
        self.assertEqual(url, f"https://huggingface.co/datasets/{download.HF_REPO}/resolve/{download.HF_REVISION}/manifest.json")

    def test_missing_data_message_and_download_cli_work_before_catalog_exists(self) -> None:
        error = io.StringIO()
        with contextlib.redirect_stderr(error), self.assertRaises(SystemExit) as stopped:
            main(["--root", str(self.root), "catalog"])
        self.assertEqual(stopped.exception.code, 2)
        self.assertIn("memharmbench download", error.getvalue())
        with patch.object(download, "download_dataset", return_value={"downloaded": True}) as fetch:
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(["--root", str(self.root), "download", "--offline"]), 0)
            fetch.assert_called_once_with(self.root, force=False, offline=True)
        with self.assertRaisesRegex(ValueError, "cache is missing"):
            download.download_dataset(self.root, offline=True)

    def test_validation_output_cannot_overwrite_download_provenance(self) -> None:
        (self.root / "cases").mkdir()
        (self.root / "cases/catalog.json").write_text('{"cases": []}', encoding="utf-8")
        with patch("memharmbench_v84.public_validation.validate_public", return_value={"passed": True}):
            with patch("memharmbench_v84.identity.verify_release_lock", return_value={"code_files": {}, "dataset_manifests": {}}):
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    main(["--root", str(self.root), "validate", "--output",
                          str(self.root / ".memharmbench/release_lock.json")])
        self.assertFalse((self.root / ".memharmbench/release_lock.json").exists())


if __name__ == "__main__":
    unittest.main()
