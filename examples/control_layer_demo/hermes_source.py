"""Verify and stage the pinned, patched Hermes source for an isolated run.

This helper performs no imports from Hermes, network access, credential lookup,
or provider calls. Its source and receipt paths are intentionally fixed.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
from pathlib import Path, PurePosixPath

_WORKSPACE = Path(__file__).resolve().parents[3]
_AUDIT = _WORKSPACE / "workspace-support" / "audit" / "2026-10-18-cm104-hermes-dependency-setup"
_MANIFEST_PATH = _AUDIT / "hermes-cm104-source-manifest-original.json"
_SOURCE_RECEIPT_PATH = _AUDIT / "hermes-cm104-source-auth-original-receipt.json"
_DEPENDENCY_RECEIPT_PATH = _AUDIT / "hermes-cm104-setup-receipt.json"
_PATCH_PATH = _WORKSPACE / "workspace-support" / "audit" / "2026-10-18-cm095-resume" / "hermes-cm095-single-dispatch.patch"
_SOURCE_ROOT = Path("/tmp/nakul-cm104-hermes-auth-source-ufj4hnpn/source")  # noqa: S108 - fixed path bound to the pinned source manifest
_EXPECTED_COMMIT = "51abb25ce07f384bd60f1eba151623c769db6b3e"
_EXPECTED_ARCHIVE_SHA256 = "20415b5b5ee77fa79de2c36bcd6636a480fa95e8c02cc0c64acadd2327088f39"
_EXPECTED_MANIFEST_SHA256 = "c8590281b6059ec20ab8a749fdd7cff35b1dd36b005645120ebe60754a441cb0"
_EXPECTED_SOURCE_RECEIPT_SHA256 = "1cc5de143a7c731bc38b449dbb0df61f7d03e84e19a546f864d5e95a868d20e1"
_EXPECTED_DEPENDENCY_RECEIPT_SHA256 = "1c58da5485065eccce724011a8bafc0007956c874ce9c65575970d5bb226f0a5"
_EXPECTED_PATCH_SHA256 = "dac520caa486f8c567cfa3b6bd25fef746ca8c5b18b85322b095caaeb42817a7"
_PATCHED_HANDLER_SHA256 = "203d9416b18ab298f101877e7b4cddf3e9e2120dd26bf54c58cf760cd115e041"
_HANDLER_PATH = "tools/mcp_tool_handlers.py"
_EXPECTED_FILES = 19742
_EXPECTED_BYTES = 222030481
_DEPENDENCY_COUNT = 35
_DEPENDENCY_BYTES = 9947331
_UV_LOCK_SHA256 = "16300cf807347b7fcfa3c19963f5fe950913fdb3e41e3c649ea724bc7dc997da"
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _finite(status: str, reason: str, **values: object) -> dict:
    proof = {
        "schema": "cloudmorph.hermes_source_proof.v1",
        "status": status,
        "reason_code": reason,
        "commit": _EXPECTED_COMMIT,
        "archive_sha256": _EXPECTED_ARCHIVE_SHA256,
        "source_root": str(_SOURCE_ROOT),
        "hermes_imported": False,
        "network_accessed": False,
        "credentials_read": False,
        "provider_calls": 0,
        "model_calls": 0,
    }
    proof.update(values)
    return proof


def _safe_manifest_path(value: object) -> str | None:
    if not isinstance(value, str) or not value or "\\" in value:
        return None
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        return None
    if path.as_posix() != value:
        return None
    return value


def _check_receipts_and_manifest() -> tuple[dict | None, dict | None, dict | None]:
    expected_hashes = (
        (_MANIFEST_PATH, _EXPECTED_MANIFEST_SHA256),
        (_SOURCE_RECEIPT_PATH, _EXPECTED_SOURCE_RECEIPT_SHA256),
        (_DEPENDENCY_RECEIPT_PATH, _EXPECTED_DEPENDENCY_RECEIPT_SHA256),
        (_PATCH_PATH, _EXPECTED_PATCH_SHA256),
    )
    for path, expected in expected_hashes:
        try:
            if _sha256(path) != expected:
                return None, None, None
        except OSError:
            return None, None, None
    try:
        manifest = json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))
        source_receipt = json.loads(_SOURCE_RECEIPT_PATH.read_text(encoding="utf-8"))
        dependency_receipt = json.loads(_DEPENDENCY_RECEIPT_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, None, None
    if (manifest.get("schema") != "cloudmorph.hermes_cm104_full_source_manifest.v1"
            or manifest.get("commit") != _EXPECTED_COMMIT
            or manifest.get("archive_sha256") != _EXPECTED_ARCHIVE_SHA256
            or manifest.get("source_root") != str(_SOURCE_ROOT)
            or manifest.get("file_count") != _EXPECTED_FILES
            or manifest.get("regular_file_bytes") != _EXPECTED_BYTES
            or manifest.get("symlinks_included") is not False):
        return None, None, None
    if (source_receipt.get("status") != "PASS"
            or source_receipt.get("reason_code") != "PINNED_ARCHIVE_AND_TREE_VERIFIED"
            or source_receipt.get("archive_sha256") != _EXPECTED_ARCHIVE_SHA256
            or source_receipt.get("manifest_sha256") != _EXPECTED_MANIFEST_SHA256
            or source_receipt.get("source_root") != str(_SOURCE_ROOT)
            or source_receipt.get("file_count") != _EXPECTED_FILES
            or source_receipt.get("regular_file_bytes") != _EXPECTED_BYTES):
        return None, None, None
    if (dependency_receipt.get("status") != "PASS"
            or dependency_receipt.get("reason_code") != "LOCKED_WHEEL_INSTALL_VERIFIED"
            or dependency_receipt.get("source_commit") != _EXPECTED_COMMIT
            or dependency_receipt.get("uv_lock_sha256") != _UV_LOCK_SHA256
            or dependency_receipt.get("package_count") != _DEPENDENCY_COUNT
            or dependency_receipt.get("wheel_bytes") != _DEPENDENCY_BYTES
            or dependency_receipt.get("hermes_imported") is not False
            or dependency_receipt.get("provider_or_cloud_calls") != 0
            or dependency_receipt.get("model_calls") != 0):
        return None, None, None
    return manifest, source_receipt, dependency_receipt


def _entries(manifest: dict) -> dict[str, tuple[int, str]] | None:
    values = manifest.get("files")
    if not isinstance(values, list) or len(values) != _EXPECTED_FILES:
        return None
    expected: dict[str, tuple[int, str]] = {}
    for item in values:
        if not isinstance(item, dict):
            return None
        path = _safe_manifest_path(item.get("path"))
        size = item.get("bytes")
        digest = item.get("sha256")
        if (path is None or not isinstance(size, int) or size < 0
                or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)
                or path in expected):
            return None
        expected[path] = (size, digest)
    if sum(size for size, _ in expected.values()) != _EXPECTED_BYTES:
        return None
    return expected


def _walk_regular_files(root: Path) -> tuple[set[str] | None, str | None]:
    seen: set[str] = set()
    try:
        root_stat = root.lstat()
        if not stat.S_ISDIR(root_stat.st_mode) or stat.S_ISLNK(root_stat.st_mode):
            return None, "SOURCE_ROOT_TYPE_INVALID"
        for base, directories, filenames in os.walk(root, topdown=True, followlinks=False):
            base_path = Path(base)
            for name in directories:
                child = base_path / name
                mode = child.lstat().st_mode
                if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
                    return None, "SYMLINK_OR_NON_DIRECTORY_REJECTED"
            for name in filenames:
                child = base_path / name
                mode = child.lstat().st_mode
                if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
                    return None, "SYMLINK_OR_NONREGULAR_FILE_REJECTED"
                relative = child.relative_to(root).as_posix()
                if _safe_manifest_path(relative) is None or relative in seen:
                    return None, "UNSAFE_OR_DUPLICATE_SOURCE_PATH"
                seen.add(relative)
    except OSError:
        return None, "SOURCE_TREE_READ_FAILURE"
    return seen, None


def _verify_tree(root: Path, manifest: dict, patched: bool = False) -> tuple[dict | None, str | None]:
    expected = _entries(manifest)
    if expected is None:
        return None, "MANIFEST_CONTENT_INVALID"
    observed, walk_error = _walk_regular_files(root)
    if walk_error:
        return None, walk_error
    if observed != set(expected):
        return None, "SOURCE_TREE_MEMBERSHIP_MISMATCH"
    total_bytes = 0
    try:
        for relative, (size, expected_digest) in expected.items():
            path = root.joinpath(*PurePosixPath(relative).parts)
            mode = path.lstat().st_mode
            if not stat.S_ISREG(mode) or stat.S_ISLNK(mode):
                return None, "SOURCE_FILE_TYPE_INVALID"
            actual_size = path.stat(follow_symlinks=False).st_size
            actual_digest = _sha256(path)
            if patched and relative == _HANDLER_PATH:
                if actual_digest != _PATCHED_HANDLER_SHA256:
                    return None, "PATCHED_HANDLER_HASH_MISMATCH"
                total_bytes += actual_size
            else:
                if actual_size != size or actual_digest != expected_digest:
                    return None, "SOURCE_FILE_HASH_MISMATCH"
                total_bytes += size
    except OSError:
        return None, "SOURCE_FILE_READ_FAILURE"
    if not patched and total_bytes != _EXPECTED_BYTES:
        return None, "SOURCE_TOTAL_BYTES_MISMATCH"
    return {"file_count": len(observed), "regular_file_bytes": total_bytes}, None


def check_source() -> dict:
    """Verify the pinned source tree, source receipt, dependency receipt and patch."""
    manifest, source_receipt, dependency_receipt = _check_receipts_and_manifest()
    if manifest is None:
        return _finite("FAIL", "PINNED_RECEIPT_OR_MANIFEST_INVALID")
    if not _owned_private_dir(_SOURCE_ROOT.parent) or not _owned_private_dir(_SOURCE_ROOT):
        return _finite("FAIL", "SOURCE_ROOT_PRIVATE_MODE_INVALID")
    summary, error = _verify_tree(_SOURCE_ROOT, manifest)
    if error:
        return _finite("FAIL", error)
    return _finite(
        "PASS", "PINNED_SOURCE_AND_DEPENDENCIES_VERIFIED",
        manifest_sha256=_EXPECTED_MANIFEST_SHA256,
        source_receipt_sha256=_EXPECTED_SOURCE_RECEIPT_SHA256,
        dependency_receipt_sha256=_EXPECTED_DEPENDENCY_RECEIPT_SHA256,
        patch_sha256=_EXPECTED_PATCH_SHA256,
        file_count=summary["file_count"],
        regular_file_bytes=summary["regular_file_bytes"],
        dependency_package_count=dependency_receipt["package_count"],
        dependency_wheel_bytes=dependency_receipt["wheel_bytes"],
    )


def _tmpfs_mount() -> bool:
    try:
        if not os.path.ismount("/dev/shm"):  # noqa: S108 - required mountpoint for private tmpfs staging
            return False
        with open("/proc/self/mountinfo", encoding="utf-8") as stream:
            for line in stream:
                fields = line.split()
                if len(fields) > 6 and fields[4] == "/dev/shm":  # noqa: S108 - verify the required tmpfs mount identity
                    separator = fields.index("-")
                    return len(fields) > separator + 1 and fields[separator + 1] == "tmpfs"
    except (OSError, ValueError):
        return False
    return False


def _owned_private_dir(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return False
    return (stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode)
            and info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == 0o700)


def _copy_tree(root: Path, destination: Path, manifest: dict) -> tuple[dict | None, str | None]:
    expected = _entries(manifest)
    observed, walk_error = _walk_regular_files(root)
    if walk_error:
        return None, walk_error
    if expected is None or observed != set(expected):
        return None, "SOURCE_TREE_MEMBERSHIP_MISMATCH"
    created = False
    try:
        destination.mkdir(mode=0o700)
        created = True
        for relative, (expected_size, expected_digest) in expected.items():
            source = root.joinpath(*PurePosixPath(relative).parts)
            target = destination.joinpath(*PurePosixPath(relative).parts)
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            source_fd = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            target_fd = -1
            copied = hashlib.sha256()
            copied_size = 0
            try:
                if not stat.S_ISREG(os.fstat(source_fd).st_mode):
                    return {"created": created}, "SOURCE_FILE_TYPE_INVALID"
                target_fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(source_fd, "rb", closefd=False) as input_stream, os.fdopen(target_fd, "wb", closefd=False) as output_stream:
                    while True:
                        chunk = input_stream.read(1024 * 1024)
                        if not chunk:
                            break
                        copied_size += len(chunk)
                        copied.update(chunk)
                        output_stream.write(chunk)
                    output_stream.flush()
                    os.fsync(target_fd)
                os.chmod(target, 0o600)
            finally:
                os.close(source_fd)
                if target_fd >= 0:
                    os.close(target_fd)
            if copied_size != expected_size or copied.hexdigest() != expected_digest:
                return {"created": created}, "COPY_HASH_MISMATCH"
        return {"created": created}, None
    except OSError:
        return {"created": created}, "PRIVATE_COPY_FAILURE"


def _remove_created_destination(destination: Path) -> None:
    try:
        info = destination.lstat()
        if stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode) and info.st_uid == os.geteuid():
            shutil.rmtree(destination)
    except OSError:
        return


def prepare_source(destination: str | os.PathLike[str], expected_run_uuid: str) -> dict:
    """Copy the verified source into the run's private tmpfs directory and apply one exact patch."""
    if not isinstance(expected_run_uuid, str) or not _UUID_RE.fullmatch(expected_run_uuid):
        return _finite("FAIL", "RUN_UUID_INVALID")
    expected_parent = Path("/dev/shm") / f"cm095-workload1-{expected_run_uuid}"  # noqa: S108 - destination is owner/mode/tmpfs checked below
    expected_destination = expected_parent / "hermes-source"
    try:
        requested = Path(destination)
        if (not requested.is_absolute() or os.path.normpath(str(requested)) != str(expected_destination)
                or requested != expected_destination):
            return _finite("FAIL", "DESTINATION_NOT_RUN_BOUND", run_uuid=expected_run_uuid)
    except (TypeError, ValueError):
        return _finite("FAIL", "DESTINATION_INVALID", run_uuid=expected_run_uuid)
    if not _tmpfs_mount() or not _owned_private_dir(expected_parent):
        return _finite("FAIL", "RUN_TMPFS_PARENT_INVALID", run_uuid=expected_run_uuid)
    if os.path.lexists(expected_destination):
        return _finite("FAIL", "DESTINATION_ALREADY_EXISTS", run_uuid=expected_run_uuid)
    preflight = check_source()
    if preflight.get("status") != "PASS":
        return _finite("FAIL", "SOURCE_PREFLIGHT_FAILED", run_uuid=expected_run_uuid,
                       preflight_reason=preflight.get("reason_code"))
    manifest, _, _ = _check_receipts_and_manifest()
    if manifest is None:
        return _finite("FAIL", "PINNED_RECEIPT_OR_MANIFEST_INVALID", run_uuid=expected_run_uuid)
    copied, copy_error = _copy_tree(_SOURCE_ROOT, expected_destination, manifest)
    created = bool(copied and copied.get("created"))
    if copy_error:
        if created:
            _remove_created_destination(expected_destination)
        return _finite("FAIL", copy_error, run_uuid=expected_run_uuid)
    try:
        patch_executable = "/usr/bin/patch"
        if not os.path.isfile(patch_executable) or not os.access(patch_executable, os.X_OK):
            raise OSError("patch tool unavailable")
        result = subprocess.run(  # noqa: S603 - fixed reviewed absolute executable and pinned argv, no shell
            [patch_executable, "--batch", "--forward", "--fuzz=0", "-p1", "-i", str(_PATCH_PATH)],
            cwd=expected_destination,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
            timeout=15,
            check=False,
        )
        if result.returncode != 0:
            _remove_created_destination(expected_destination)
            return _finite("FAIL", "PATCH_APPLY_FAILED", run_uuid=expected_run_uuid)
    except (OSError, subprocess.TimeoutExpired):
        _remove_created_destination(expected_destination)
        return _finite("FAIL", "PATCH_APPLY_FAILED", run_uuid=expected_run_uuid)
    patched_summary, post_error = _verify_tree(expected_destination, manifest, patched=True)
    if post_error:
        _remove_created_destination(expected_destination)
        return _finite("FAIL", post_error, run_uuid=expected_run_uuid)
    return _finite(
        "PASS", "PRIVATE_PATCHED_SOURCE_READY",
        run_uuid=expected_run_uuid,
        source_path=str(expected_destination),
        manifest_sha256=_EXPECTED_MANIFEST_SHA256,
        source_receipt_sha256=_EXPECTED_SOURCE_RECEIPT_SHA256,
        dependency_receipt_sha256=_EXPECTED_DEPENDENCY_RECEIPT_SHA256,
        patch_sha256=_EXPECTED_PATCH_SHA256,
        patched_handler_sha256=_PATCHED_HANDLER_SHA256,
        file_count=patched_summary["file_count"],
        regular_file_bytes=patched_summary["regular_file_bytes"],
        patch_scope=[_HANDLER_PATH],
    )


def main() -> int:
    proof = check_source()
    audit_path = _AUDIT / "hermes-source-helper-verification.json"
    audit_data = dict(proof)
    audit_data["helper_sha256"] = _sha256(Path(__file__).resolve())
    temp = audit_path.with_suffix(".json.tmp")
    temp.write_text(json.dumps(audit_data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(temp, 0o600)
    temp.replace(audit_path)
    os.chmod(audit_path, 0o600)
    print(json.dumps(proof, sort_keys=True))
    return 0 if proof.get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
