"""Mutation witnesses for JSON-shred runtime control boundaries."""

from __future__ import annotations

import threading
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import orjson
import pytest

import haute._json_shred as shred_mod


class _CheckpointContext:
    def __init__(self) -> None:
        self.labels: list[str] = []

    def checkpoint(self, *, label: str) -> None:
        self.labels.append(label)


def test_execution_progress_checkpoints_at_the_exact_unit_threshold() -> None:
    assert shred_mod._SHRED_EXECUTION_CHECKPOINT_ROWS == 1_024

    no_context = shred_mod._ShredExecutionProgress(None, work_since_checkpoint=17)
    no_context.advance("ignored")
    assert no_context.work_since_checkpoint == 17

    context = _CheckpointContext()
    progress = shred_mod._ShredExecutionProgress(context)
    for _ in range(1_023):
        progress.advance("json_shred_record")
    assert context.labels == []
    assert progress.work_since_checkpoint == 1_023

    progress.advance("json_shred_record")
    assert context.labels == ["json_shred_record"]
    assert progress.work_since_checkpoint == 0

    for starting_units in (1_024, 1_025):
        progress.work_since_checkpoint = starting_units
        progress.advance("already_due")
        assert context.labels[-1] == "already_due"
        assert progress.work_since_checkpoint == 0


def test_runtime_storage_recovery_is_once_per_pid_and_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    current_pid = 100
    recovered: list[Path] = []
    root = tmp_path / "cache-root"
    monkeypatch.setattr(shred_mod.os, "getpid", lambda: current_pid)
    monkeypatch.setattr(shred_mod, "_RUNTIME_STORAGE_RECOVERY_PROCESS_ID", 100)
    monkeypatch.setattr(shred_mod, "_RUNTIME_STORAGE_RECOVERED_ROOTS", set())
    monkeypatch.setattr(
        shred_mod,
        "recover_json_runtime_storage",
        lambda cache_root: recovered.append(cache_root),
    )

    shred_mod._recover_runtime_storage_once(root)
    shred_mod._recover_runtime_storage_once(root)
    current_pid = 99
    shred_mod._recover_runtime_storage_once(root)
    current_pid = 101
    shred_mod._recover_runtime_storage_once(root)

    assert recovered == [root, root, root]
    assert shred_mod._RUNTIME_STORAGE_RECOVERY_PROCESS_ID == 101
    assert shred_mod._RUNTIME_STORAGE_RECOVERED_ROOTS == {root}


@pytest.fixture
def isolated_direct_spill_state(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    registrations: list[Any] = []
    monkeypatch.setattr(shred_mod, "_DIRECT_SPILL_PROCESS_ID", 100)
    monkeypatch.setattr(shred_mod, "_DIRECT_SPILL_PROCESS_TOKEN", "owner-100")
    monkeypatch.setattr(shred_mod, "_DIRECT_SPILL_DIRS", set())
    monkeypatch.setattr(shred_mod, "_DIRECT_SPILL_LOCK", threading.Lock())
    monkeypatch.setattr(shred_mod, "_DIRECT_SPILL_ATEXIT_REGISTERED", False)
    monkeypatch.setattr(shred_mod.os, "getpid", lambda: 100)
    monkeypatch.setattr(
        shred_mod,
        "_runtime_disk_budget_transaction",
        lambda *_args, **_kwargs: nullcontext(),
    )
    monkeypatch.setattr(shred_mod, "_ensure_runtime_owner_metadata", lambda _owner: None)
    monkeypatch.setattr(shred_mod.atexit, "register", registrations.append)
    return registrations


def test_new_direct_spill_dir_tracks_owner_dirs_and_registers_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    isolated_direct_spill_state: list[Any],
) -> None:
    uuids = iter(("first", "second"))
    monkeypatch.setattr(shred_mod.uuid, "uuid4", lambda: SimpleNamespace(hex=next(uuids)))
    cache_dir = tmp_path / ".haute_cache" / "committed" / "artifact"

    first = shred_mod._new_direct_spill_dir(cache_dir)
    second = shred_mod._new_direct_spill_dir(cache_dir)

    owner = tmp_path / ".haute_cache" / shred_mod._DIRECT_SPILL_DIRNAME / "owner-100"
    assert first == owner / "first"
    assert second == owner / "second"
    assert first.is_dir() and second.is_dir()
    assert shred_mod._DIRECT_SPILL_DIRS == {first, second}
    assert shred_mod._DIRECT_SPILL_PROCESS_TOKEN == "owner-100"
    assert isolated_direct_spill_state == [shred_mod._cleanup_direct_spill_dirs]


def test_new_direct_spill_dir_rejects_an_exact_child_collision(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    isolated_direct_spill_state: list[Any],
) -> None:
    monkeypatch.setattr(
        shred_mod.uuid,
        "uuid4",
        lambda: SimpleNamespace(hex="collision"),
    )
    cache_dir = tmp_path / ".haute_cache" / "working" / "artifact"
    collision = (
        tmp_path / ".haute_cache" / shred_mod._DIRECT_SPILL_DIRNAME / "owner-100" / "collision"
    )
    collision.mkdir(parents=True)
    marker = collision / "pre-existing"
    marker.write_bytes(b"not owned by this allocation")

    with pytest.raises(FileExistsError):
        shred_mod._new_direct_spill_dir(cache_dir)

    assert marker.read_bytes() == b"not owned by this allocation"
    assert shred_mod._DIRECT_SPILL_DIRS == set()
    assert isolated_direct_spill_state == []


@pytest.mark.parametrize("changed_pid", [99, 101])
def test_new_direct_spill_dir_resets_inherited_ownership_for_any_pid_change(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    isolated_direct_spill_state: list[Any],
    changed_pid: int,
) -> None:
    inherited = tmp_path / "inherited"
    shred_mod._DIRECT_SPILL_DIRS.add(inherited)
    monkeypatch.setattr(shred_mod.os, "getpid", lambda: changed_pid)
    monkeypatch.setattr(
        shred_mod.uuid,
        "uuid4",
        lambda: SimpleNamespace(hex="new-owner"),
    )

    created = shred_mod._new_direct_spill_dir(tmp_path / ".haute_cache" / "cache")

    expected_owner = (
        tmp_path / ".haute_cache" / shred_mod._DIRECT_SPILL_DIRNAME / f"{changed_pid}-new-owner"
    )
    assert created == expected_owner / "new-owner"
    assert shred_mod._DIRECT_SPILL_PROCESS_ID == changed_pid
    assert shred_mod._DIRECT_SPILL_PROCESS_TOKEN == f"{changed_pid}-new-owner"
    assert shred_mod._DIRECT_SPILL_DIRS == {created}
    assert isolated_direct_spill_state == [shred_mod._cleanup_direct_spill_dirs]


def _bare_writer(*, max_rows: int, max_bytes: int) -> Any:
    writer = object.__new__(shred_mod._BoundedParquetRowGroupWriter)
    writer.buffers = {"table": []}
    writer.row_counts = {"table": 0}
    writer.buffered_rows = 0
    writer.buffered_bytes = 0
    writer.max_rows = max_rows
    writer.max_bytes = max_bytes
    return writer


def test_bounded_writer_emit_flushes_at_exact_row_and_byte_boundaries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = {"value": "x"}
    row_bytes = len(orjson.dumps(row))

    writer = _bare_writer(max_rows=2, max_bytes=10_000)
    flushed: list[tuple[int, int]] = []
    monkeypatch.setattr(
        writer,
        "flush",
        lambda: flushed.append((writer.buffered_rows, writer.buffered_bytes)),
    )
    writer.emit("table", row)
    assert flushed == []
    writer.emit("table", row)
    assert flushed == [(2, row_bytes * 2)]
    assert writer.row_counts == {"table": 2}
    assert writer.buffers == {"table": [row, row]}

    writer = _bare_writer(max_rows=10, max_bytes=row_bytes)
    byte_flushed: list[tuple[int, int]] = []
    monkeypatch.setattr(
        writer,
        "flush",
        lambda: byte_flushed.append((writer.buffered_rows, writer.buffered_bytes)),
    )
    writer.emit("table", row)
    assert byte_flushed == [(1, row_bytes)]
    assert writer.row_counts == {"table": 1}
    assert writer.buffered_rows == 1
    assert writer.buffered_bytes == row_bytes

    writer = _bare_writer(max_rows=2, max_bytes=row_bytes + 1)
    monkeypatch.setattr(writer, "flush", lambda: pytest.fail("must not flush below either limit"))
    writer.emit("table", row)
    assert writer.row_counts == {"table": 1}
    assert writer.buffered_rows == 1
    assert writer.buffered_bytes == row_bytes

    with pytest.raises(RuntimeError, match="unknown table 'missing'"):
        writer.emit("missing", row)
    assert writer.row_counts == {"table": 1}
    assert writer.buffered_rows == 1
    assert writer.buffered_bytes == row_bytes


def test_shred_chunk_returns_a_zeroed_failure_envelope(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        shred_mod,
        "_emitting_table_specs",
        lambda _config: (_ for _ in ()).throw(ValueError("bad table config")),
    )

    result = shred_mod._shred_chunk((str(tmp_path / "input.jsonl"), 4, 9, 7, {}, str(tmp_path)))

    assert result.index == 7
    assert result.record_count == 0
    assert result.skipped_records == 0
    assert result.skipped_rows_by_table == {}
    assert result.row_counts == {}
    assert result.part_paths == {}
    assert result.failure is not None
    assert result.failure.type_name == "ValueError"
    assert result.failure.message == "bad table config"


def test_runtime_storage_errors_expose_exact_public_fields_and_messages() -> None:
    budget_error = shred_mod.JsonRuntimeDiskBudgetExceededError(used_bytes=0, budget_bytes=1)
    assert budget_error.used_bytes == 0
    assert budget_error.budget_bytes == 1
    assert (
        str(budget_error)
        == "JSON runtime storage requires 0 bytes, exceeding its 1 byte disk budget"
    )

    path = Path("runtime-entry")
    integrity_error = shred_mod.JsonRuntimeStorageIntegrityError(path=path, reason="unreadable")
    assert integrity_error.path == path
    assert integrity_error.reason == "unreadable"
    assert (
        str(integrity_error)
        == "JSON runtime storage cannot be measured safely because runtime-entry is unreadable"
    )
