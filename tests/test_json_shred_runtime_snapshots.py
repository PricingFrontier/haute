"""Regression coverage for private runtime parquet snapshot ownership."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

import haute._json_shred as shred_mod


@pytest.fixture
def isolated_snapshot_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace process-global snapshot bookkeeping with test-local containers."""
    monkeypatch.setattr(shred_mod, "_RUNTIME_SNAPSHOT_DIRS", set())
    monkeypatch.setattr(shred_mod, "_RUNTIME_SNAPSHOT_REFERENCES", {})
    monkeypatch.setattr(shred_mod, "_RUNTIME_SNAPSHOT_PROCESS_PINS", set())
    monkeypatch.setattr(shred_mod, "_RUNTIME_SNAPSHOT_PROCESS_ID", os.getpid())
    monkeypatch.setattr(shred_mod, "_RUNTIME_SNAPSHOT_PROCESS_TOKEN", "test-owner")
    monkeypatch.setattr(shred_mod, "_RUNTIME_SNAPSHOT_ATEXIT_REGISTERED", True)


def test_cleanup_owned_snapshots_clears_bookkeeping_and_keeps_nonempty_parent(
    tmp_path: Path, isolated_snapshot_state: None
) -> None:
    parent = tmp_path / shred_mod._RUNTIME_SNAPSHOT_DIRNAME
    owned_dir = parent / "test-owner"
    owned_file = owned_dir / "owned.parquet"
    owned_dir.mkdir(parents=True)
    owned_file.write_bytes(b"owned")
    (parent / "other-process").mkdir()

    shred_mod._RUNTIME_SNAPSHOT_DIRS.add(owned_dir)
    shred_mod._RUNTIME_SNAPSHOT_REFERENCES[owned_file] = 2
    shred_mod._RUNTIME_SNAPSHOT_PROCESS_PINS.add(owned_file)

    shred_mod._cleanup_runtime_snapshot_dirs()

    assert not owned_dir.exists()
    assert parent.exists()
    assert shred_mod._RUNTIME_SNAPSHOT_DIRS == set()
    assert shred_mod._RUNTIME_SNAPSHOT_REFERENCES == {}
    assert shred_mod._RUNTIME_SNAPSHOT_PROCESS_PINS == set()


def test_cleanup_in_inherited_pid_leaves_parent_snapshot_state_untouched(
    tmp_path: Path, isolated_snapshot_state: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot_dir = tmp_path / "inherited"
    snapshot_path = snapshot_dir / "snapshot.parquet"
    snapshot_dir.mkdir()
    snapshot_path.write_bytes(b"data")
    shred_mod._RUNTIME_SNAPSHOT_DIRS.add(snapshot_dir)
    shred_mod._RUNTIME_SNAPSHOT_REFERENCES[snapshot_path] = 1
    shred_mod._RUNTIME_SNAPSHOT_PROCESS_PINS.add(snapshot_path)
    monkeypatch.setattr(shred_mod, "_RUNTIME_SNAPSHOT_PROCESS_ID", os.getpid() + 1)

    shred_mod._cleanup_runtime_snapshot_dirs()

    assert snapshot_path.exists()
    assert shred_mod._RUNTIME_SNAPSHOT_DIRS == {snapshot_dir}
    assert shred_mod._RUNTIME_SNAPSHOT_REFERENCES == {snapshot_path: 1}
    assert shred_mod._RUNTIME_SNAPSHOT_PROCESS_PINS == {snapshot_path}


def test_runtime_snapshot_dir_resets_inherited_state_for_new_pid(
    tmp_path: Path, isolated_snapshot_state: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    old_dir = tmp_path / "old"
    old_path = old_dir / "old.parquet"
    shred_mod._RUNTIME_SNAPSHOT_DIRS.add(old_dir)
    shred_mod._RUNTIME_SNAPSHOT_REFERENCES[old_path] = 1
    shred_mod._RUNTIME_SNAPSHOT_PROCESS_PINS.add(old_path)
    monkeypatch.setattr(shred_mod, "_RUNTIME_SNAPSHOT_PROCESS_ID", os.getpid() + 1)

    snapshot_dir = shred_mod._runtime_snapshot_dir(tmp_path / "cache")

    assert snapshot_dir.exists()
    assert snapshot_dir in shred_mod._RUNTIME_SNAPSHOT_DIRS
    assert old_dir not in shred_mod._RUNTIME_SNAPSHOT_DIRS
    assert shred_mod._RUNTIME_SNAPSHOT_REFERENCES == {}
    assert shred_mod._RUNTIME_SNAPSHOT_PROCESS_PINS == set()
    assert shred_mod._RUNTIME_SNAPSHOT_PROCESS_TOKEN.startswith(f"{os.getpid()}-")


def test_stream_copy_removes_partial_target_when_copy_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.parquet"
    target = tmp_path / "target.parquet"
    source.write_bytes(b"payload")
    original_open = Path.open

    class _FailingWriter:
        def __init__(self, file: Any) -> None:
            self.file = file

        def __enter__(self) -> _FailingWriter:
            self.file.__enter__()
            return self

        def __exit__(self, *args: Any) -> Any:
            return self.file.__exit__(*args)

        def write(self, _chunk: bytes) -> int:
            raise OSError("disk full")

    def open_with_failing_target(path: Path, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
        file = original_open(path, mode, *args, **kwargs)
        return _FailingWriter(file) if path == target and mode == "xb" else file

    monkeypatch.setattr(Path, "open", open_with_failing_target)

    with pytest.raises(OSError, match="disk full"):
        shred_mod._stream_copy_with_signature(source, target)

    assert not target.exists()


def test_release_rejects_double_release_and_tolerates_missing_final_file(
    tmp_path: Path, isolated_snapshot_state: None
) -> None:
    snapshot_path = tmp_path / "snapshot.parquet"
    snapshot_path.write_bytes(b"data")
    shred_mod._RUNTIME_SNAPSHOT_REFERENCES[snapshot_path] = 1

    shred_mod._release_runtime_snapshot(snapshot_path)

    assert not snapshot_path.exists()
    with pytest.raises(RuntimeError, match="released twice"):
        shred_mod._release_runtime_snapshot(snapshot_path)

    missing_path = tmp_path / "missing.parquet"
    shred_mod._RUNTIME_SNAPSHOT_REFERENCES[missing_path] = 1
    shred_mod._release_runtime_snapshot(missing_path)
    assert missing_path not in shred_mod._RUNTIME_SNAPSHOT_REFERENCES


def test_release_of_process_pinned_snapshot_keeps_file_but_clears_reference(
    tmp_path: Path, isolated_snapshot_state: None
) -> None:
    snapshot_path = tmp_path / "snapshot.parquet"
    snapshot_path.write_bytes(b"data")
    shred_mod._RUNTIME_SNAPSHOT_REFERENCES[snapshot_path] = 1
    shred_mod._RUNTIME_SNAPSHOT_PROCESS_PINS.add(snapshot_path)

    shred_mod._release_runtime_snapshot(snapshot_path)

    assert snapshot_path.exists()
    assert shred_mod._RUNTIME_SNAPSHOT_REFERENCES == {}
    assert shred_mod._RUNTIME_SNAPSHOT_PROCESS_PINS == {snapshot_path}


def test_unmanaged_retain_requires_owner_and_pins_one_of_many_references(
    tmp_path: Path, isolated_snapshot_state: None
) -> None:
    snapshot_path = tmp_path / "snapshot.parquet"
    with pytest.raises(RuntimeError, match="no transient owner"):
        shred_mod._retain_runtime_snapshot(snapshot_path)

    shred_mod._RUNTIME_SNAPSHOT_REFERENCES[snapshot_path] = 2
    shred_mod._retain_runtime_snapshot(snapshot_path)

    assert shred_mod._RUNTIME_SNAPSHOT_REFERENCES == {snapshot_path: 1}
    assert shred_mod._RUNTIME_SNAPSHOT_PROCESS_PINS == {snapshot_path}


def test_managed_retain_releases_transient_snapshot_when_registration_fails(
    tmp_path: Path, isolated_snapshot_state: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot_path = tmp_path / "snapshot.parquet"
    snapshot_path.write_bytes(b"data")
    shred_mod._RUNTIME_SNAPSHOT_REFERENCES[snapshot_path] = 1

    class _FailingContext:
        def add_cleanup(self, _callback: Any) -> None:
            raise RuntimeError("cleanup registration failed")

    monkeypatch.setattr(shred_mod, "current_execution_context", lambda: _FailingContext())

    with pytest.raises(RuntimeError, match="cleanup registration failed"):
        shred_mod._retain_runtime_snapshot(snapshot_path)

    assert not snapshot_path.exists()
    assert shred_mod._RUNTIME_SNAPSHOT_REFERENCES == {}


def test_hard_link_signature_failure_removes_candidate_and_reraises(
    tmp_path: Path, isolated_snapshot_state: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache_dir = tmp_path / "cache"
    source = tmp_path / "source.parquet"
    source.write_bytes(b"payload")
    monkeypatch.setattr(
        shred_mod,
        "_file_content_signature",
        lambda _path: (_ for _ in ()).throw(RuntimeError("cannot hash candidate")),
    )

    with pytest.raises(RuntimeError, match="cannot hash candidate"):
        shred_mod._snapshot_cache_artifact(
            cache_dir,
            source,
            {"size": len(b"payload"), "sha256": "0" * 64},
        )

    snapshot_root = cache_dir.parent / shred_mod._RUNTIME_SNAPSHOT_DIRNAME / "test-owner"
    assert list(snapshot_root.glob("*.tmp")) == []


def test_hard_link_missing_source_reraises_without_leaving_candidate(
    tmp_path: Path, isolated_snapshot_state: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache_dir = tmp_path / "cache"
    source = tmp_path / "source.parquet"
    candidates: list[Path] = []

    def missing_link(_source: Path, candidate: Path) -> None:
        candidates.append(candidate)
        raise FileNotFoundError("source vanished")

    monkeypatch.setattr(shred_mod.os, "link", missing_link)

    with pytest.raises(FileNotFoundError, match="source vanished"):
        shred_mod._snapshot_cache_artifact(
            cache_dir,
            source,
            {"size": 0, "sha256": "0" * 64},
        )

    assert len(candidates) == 1
    assert not candidates[0].exists()
