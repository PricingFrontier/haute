from __future__ import annotations

import os
import stat
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import orjson
import pytest

import haute._json_shred as shred_mod
from haute._json_flatten import _json_cache_dir


@pytest.fixture(autouse=True)
def _isolated_runtime_storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    shred_mod._cleanup_direct_spill_dirs()
    shred_mod._cleanup_runtime_snapshot_dirs()
    shred_mod._RUNTIME_STORAGE_RECOVERED_ROOTS.clear()
    yield
    shred_mod._cleanup_direct_spill_dirs()
    shred_mod._cleanup_runtime_snapshot_dirs()
    shred_mod._RUNTIME_STORAGE_RECOVERED_ROOTS.clear()


def _owner(parent: Path, name: str, *, pid: int, created_at: float) -> Path:
    owner = parent / name
    owner.mkdir(parents=True)
    (owner / shred_mod._RUNTIME_OWNER_META_FILENAME).write_bytes(
        orjson.dumps(
            {
                "format_version": shred_mod._RUNTIME_OWNER_FORMAT_VERSION,
                "pid": pid,
                "created_at": created_at,
            }
        )
    )
    return owner


def _write_bytes(path: Path, payload: bytes) -> None:
    path.write_bytes(payload)


def _directory_symlink_for_test(
    target: Path,
    link: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Create a POSIX directory symlink or classify a Windows stand-in as one."""
    if os.name != "nt":
        os.symlink(target, link, target_is_directory=True)
        return

    link.mkdir()
    original_lstat = Path.lstat

    def classify_as_symlink(
        path: Path, *args: object, **kwargs: object
    ) -> os.stat_result | SimpleNamespace:
        metadata = original_lstat(path, *args, **kwargs)
        if path != link:
            return metadata
        return SimpleNamespace(
            st_mode=stat.S_IFLNK | stat.S_IMODE(metadata.st_mode),
            st_file_attributes=(
                getattr(metadata, "st_file_attributes", 0)
                | getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x0400)
            ),
        )

    monkeypatch.setattr(Path, "lstat", classify_as_symlink)


def _col(name: str, path: str) -> dict[str, Any]:
    return {
        "name": name,
        "path": path,
        "type": "int",
        "status": "Confirmed",
        "selected": True,
        "levels": None,
    }


def _config() -> dict[str, Any]:
    return {
        "tables": [
            {
                "path": "$[:]",
                "label": "root",
                "emit": True,
                "row_id_column": None,
                "columns": [_col("id", "$[:].id")],
            }
        ]
    }


def test_runtime_owner_metadata_publication_failure_removes_temporary_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = tmp_path / "owner"
    owner.mkdir()
    monkeypatch.setattr(
        shred_mod.os,
        "replace",
        lambda *_args: (_ for _ in ()).throw(OSError("publication failed")),
    )

    with pytest.raises(OSError, match="publication failed"):
        shred_mod._ensure_runtime_owner_metadata(owner)

    assert tuple(owner.iterdir()) == ()


def test_runtime_owner_record_rejects_unknown_format_version(tmp_path: Path) -> None:
    owner = tmp_path / "owner"
    owner.mkdir()
    (owner / shred_mod._RUNTIME_OWNER_META_FILENAME).write_bytes(
        orjson.dumps({"format_version": 999, "pid": 1, "created_at": 1.0})
    )

    assert shred_mod._runtime_owner_record(owner) is None


def test_recovery_removes_only_old_dead_owned_directories(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / ".haute_cache"
    parent = root / "working" / shred_mod._RUNTIME_SNAPSHOT_DIRNAME
    dead = _owner(parent, "dead", pid=10, created_at=1.0)
    active = _owner(parent, "active", pid=20, created_at=1.0)
    young = _owner(parent, "young", pid=30, created_at=99.5)
    malformed = parent / "malformed"
    malformed.mkdir()
    (malformed / "payload.parquet").write_bytes(b"preserve")
    monkeypatch.setenv("HAUTE_JSON_RUNTIME_ORPHAN_GRACE_SECONDS", "10")
    monkeypatch.setattr(shred_mod, "process_is_alive", lambda pid: pid == 20)

    report = shred_mod.recover_json_runtime_storage(root, now=100.0)

    assert report == {"inspected": 4, "removed": 1, "preserved": 3}
    assert not dead.exists()
    assert active.is_dir()
    assert young.is_dir()
    assert (malformed / "payload.parquet").read_bytes() == b"preserve"


def test_recovery_preserves_non_plain_owner_entry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / ".haute_cache"
    parent = root / shred_mod._DIRECT_SPILL_DIRNAME
    parent.mkdir(parents=True)
    hostile = parent / "owner-file"
    hostile.write_text("not an owned directory", encoding="utf-8")
    monkeypatch.setenv("HAUTE_JSON_RUNTIME_ORPHAN_GRACE_SECONDS", "1")

    report = shred_mod.recover_json_runtime_storage(root, now=100.0)

    assert report == {"inspected": 1, "removed": 0, "preserved": 1}
    assert hostile.read_text(encoding="utf-8") == "not an owned directory"


def test_recovery_does_not_traverse_symlinked_runtime_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / ".haute_cache"
    external = tmp_path / "external"
    external.mkdir()
    marker = external / "must-survive"
    marker.write_text("owned elsewhere", encoding="utf-8")
    link = root / shred_mod._DIRECT_SPILL_DIRNAME
    link.parent.mkdir(parents=True)
    _directory_symlink_for_test(external, link, monkeypatch)

    report = shred_mod.recover_json_runtime_storage(root, now=100.0)

    assert report["removed"] == 0
    assert report["preserved"] == 1
    assert marker.read_text(encoding="utf-8") == "owned elsewhere"


def test_runtime_usage_counts_hard_link_identity_once(tmp_path: Path) -> None:
    root = tmp_path / ".haute_cache"
    owner = _owner(root / shred_mod._DIRECT_SPILL_DIRNAME, "owner", pid=1, created_at=1.0)
    before = shred_mod._runtime_storage_usage_bytes(root)
    source = tmp_path / "source.parquet"
    source.write_bytes(b"x" * 64)
    (owner / "first").hardlink_to(source)
    (owner / "second").hardlink_to(source)

    assert shred_mod._runtime_storage_usage_bytes(root) - before == 64


def test_runtime_usage_rejects_non_plain_child(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / ".haute_cache"
    runtime_parent = root / shred_mod._DIRECT_SPILL_DIRNAME
    external = tmp_path / "external"
    external.mkdir()
    hostile = runtime_parent / "hostile"
    hostile.parent.mkdir(parents=True)
    _directory_symlink_for_test(external, hostile, monkeypatch)

    with pytest.raises(
        shred_mod.JsonRuntimeStorageIntegrityError,
        match="not a plain file or directory",
    ):
        shred_mod._runtime_storage_usage_bytes(root)


def test_runtime_budget_blocks_allocation_for_non_plain_preserved_entry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / ".haute_cache"
    runtime_parent = root / shred_mod._DIRECT_SPILL_DIRNAME
    runtime_parent.mkdir(parents=True)
    marker = runtime_parent / "must-survive"
    marker.write_text("owned elsewhere", encoding="utf-8")
    original_plain_directory_stat = shred_mod._plain_directory_stat

    def reject_runtime_parent(path: Path) -> Any:
        if path == runtime_parent:
            raise shred_mod.JsonCacheRecoveryError("simulated non-plain runtime entry")
        return original_plain_directory_stat(path)

    monkeypatch.setattr(shred_mod, "_plain_directory_stat", reject_runtime_parent)

    with pytest.raises(
        shred_mod.JsonRuntimeStorageIntegrityError,
        match="cannot be measured safely",
    ):
        with shred_mod._runtime_disk_budget_transaction(root):
            pytest.fail("allocation must not start with unaccounted runtime storage")

    assert marker.read_text(encoding="utf-8") == "owned elsewhere"


def test_runtime_usage_ignores_only_a_concurrent_disappearance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / ".haute_cache"
    _owner(root / shred_mod._DIRECT_SPILL_DIRNAME, "owner", pid=1, created_at=1.0)
    vanishing = (
        tmp_path / ".haute_cache" / shred_mod._DIRECT_SPILL_DIRNAME / "owner" / "vanishing.parquet"
    )
    vanishing.write_bytes(b"payload")
    original_lstat = Path.lstat

    def race_lstat(path: Path) -> Any:
        if path == vanishing:
            raise FileNotFoundError(path)
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", race_lstat)

    assert shred_mod._runtime_storage_usage_bytes(root) > 0


def test_direct_spill_budget_exhaustion_fails_and_cleans_partial_bundle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = tmp_path / "records.json"
    data.write_text('[{"id": 1}]', encoding="utf-8")
    monkeypatch.setenv("HAUTE_JSON_RUNTIME_DISK_BUDGET_BYTES", "1")

    with pytest.raises(shred_mod.JsonRuntimeDiskBudgetExceededError):
        shred_mod.load_v2_api_source(str(data), _config())

    runtime_root = tmp_path / ".haute_cache" / shred_mod._DIRECT_SPILL_DIRNAME
    assert not runtime_root.exists() or not list(runtime_root.rglob("*.parquet"))


def test_runtime_snapshot_budget_exhaustion_fails_before_returning_lazyframe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = tmp_path / "records.json"
    data.write_text('[{"id": 1}]', encoding="utf-8")
    cache_dir = _json_cache_dir(data, "working")
    shred_mod.build_per_port_cache(data, _config(), cache_dir)
    monkeypatch.setenv("HAUTE_JSON_RUNTIME_DISK_BUDGET_BYTES", "1")

    with pytest.raises(shred_mod.JsonRuntimeDiskBudgetExceededError):
        shred_mod.load_per_port_cache(cache_dir, _config())

    runtime_parent = cache_dir.parent / shred_mod._RUNTIME_SNAPSHOT_DIRNAME
    assert not runtime_parent.exists() or not list(runtime_parent.rglob("*.parquet"))


@pytest.mark.parametrize("invalid_now", [True, float("nan"), float("inf"), "later"])
def test_runtime_recovery_rejects_nonfinite_or_non_numeric_clock(invalid_now: object) -> None:
    with pytest.raises(ValueError, match="now must be finite"):
        shred_mod.recover_json_runtime_storage(now=invalid_now)  # type: ignore[arg-type]


def test_runtime_recovery_fails_closed_for_unreadable_parent_and_bad_owner_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / ".haute_cache"
    parent = root / shred_mod._DIRECT_SPILL_DIRNAME
    owner = _owner(parent, "bad", pid=1, created_at=1.0)
    _write_bytes(
        owner / shred_mod._RUNTIME_OWNER_META_FILENAME,
        orjson.dumps({"format_version": 1, "pid": True, "created_at": 1.0}),
    )
    report = shred_mod.recover_json_runtime_storage(root, now=100.0)
    assert report["preserved"] == 1 and owner.exists()

    original = shred_mod._plain_directory_stat
    monkeypatch.setattr(
        shred_mod,
        "_plain_directory_stat",
        lambda path: (_ for _ in ()).throw(OSError("denied")) if path == parent else original(path),
    )
    report = shred_mod.recover_json_runtime_storage(root, now=100.0)
    assert report == {"inspected": 0, "removed": 0, "preserved": 1}


def test_runtime_recovery_preserves_non_plain_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / ".haute_cache"
    root.mkdir()
    original = shred_mod._plain_directory_stat
    monkeypatch.setattr(
        shred_mod,
        "_plain_directory_stat",
        lambda path: (
            (_ for _ in ()).throw(shred_mod.JsonCacheRecoveryError("hostile"))
            if path == root
            else original(path)
        ),
    )

    assert shred_mod.recover_json_runtime_storage(root, now=1.0) == {
        "inspected": 0,
        "removed": 0,
        "preserved": 1,
    }


def test_runtime_usage_reports_unreadable_child_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / ".haute_cache"
    owner = _owner(root / shred_mod._DIRECT_SPILL_DIRNAME, "owner", pid=1, created_at=1.0)
    blocked = owner / "blocked.parquet"
    _write_bytes(blocked, b"x")
    original_lstat = Path.lstat

    def unreadable(path: Path, *args: object, **kwargs: object) -> Any:
        if path == blocked:
            raise OSError("denied")
        return original_lstat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", unreadable)
    with pytest.raises(shred_mod.JsonRuntimeStorageIntegrityError, match="unreadable"):
        shred_mod._runtime_storage_usage_bytes(root)


def test_runtime_usage_wraps_identity_resolution_and_recovery_resets_after_fork(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / ".haute_cache"
    owner = _owner(root / shred_mod._DIRECT_SPILL_DIRNAME, "owner", pid=1, created_at=1.0)
    _write_bytes(owner / "payload", b"x")
    monkeypatch.setattr(
        shred_mod, "_runtime_file_identity", lambda *_args: (_ for _ in ()).throw(OSError("race"))
    )
    with pytest.raises(shred_mod.JsonRuntimeStorageIntegrityError, match="resolving"):
        shred_mod._runtime_storage_usage_bytes(root)

    calls: list[Path] = []
    monkeypatch.setattr(shred_mod, "recover_json_runtime_storage", lambda path: calls.append(path))
    monkeypatch.setattr(shred_mod, "_RUNTIME_STORAGE_RECOVERY_PROCESS_ID", os.getpid() + 1)
    shred_mod._recover_runtime_storage_once(root)
    assert calls == [root]


def test_runtime_storage_defensive_root_identity_and_budget_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    missing = tmp_path / "missing-root"
    assert shred_mod.recover_json_runtime_storage(missing, now=1.0) == {
        "inspected": 0,
        "removed": 0,
        "preserved": 0,
    }
    assert shred_mod._runtime_file_identity(tmp_path / "file", SimpleNamespace(st_ino=0)) == (
        "path",
        os.path.normcase(str((tmp_path / "file").resolve())),
    )

    root = tmp_path / ".haute_cache"
    owner = _owner(root / shred_mod._DIRECT_SPILL_DIRNAME, "owner", pid=1, created_at=1.0)
    _write_bytes(owner / "payload", b"x")
    monkeypatch.setenv("HAUTE_JSON_RUNTIME_DISK_BUDGET_BYTES", "1")
    monkeypatch.setattr(shred_mod, "process_is_alive", lambda _pid: True)
    with pytest.raises(shred_mod.JsonRuntimeDiskBudgetExceededError):
        with shred_mod._runtime_disk_budget_transaction(root):
            pass
