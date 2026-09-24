"""Direct mutation witnesses for JSON-source proof and runtime guardrails."""

from __future__ import annotations

from contextlib import contextmanager, nullcontext
from pathlib import Path

import orjson
import pytest

from haute import _file_lock
from haute._json_shred import _runtime_storage


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {"format_version": True, "pid": 1, "created_at": 0.0},
        {"format_version": 1.0, "pid": 1, "created_at": 0.0},
        {"format_version": 0, "pid": 1, "created_at": 0.0},
        {"format_version": 2, "pid": 1, "created_at": 0.0},
        {"format_version": 1, "pid": True, "created_at": 0.0},
        {"format_version": 1, "pid": 1.0, "created_at": 0.0},
        {"format_version": 1, "pid": 0, "created_at": 0.0},
        {"format_version": 1, "pid": -1, "created_at": 0.0},
        {"format_version": 1, "pid": 1, "created_at": True},
        {"format_version": 1, "pid": 1, "created_at": "now"},
        {"format_version": 1, "pid": 1, "created_at": None},
    ],
)
def test_runtime_owner_record_rejects_one_invalid_field_at_a_time(
    tmp_path: Path, payload: object
) -> None:
    owner = tmp_path / "owner"
    owner.mkdir()
    (owner / _runtime_storage._RUNTIME_OWNER_META_FILENAME).write_bytes(orjson.dumps(payload))
    assert _runtime_storage._runtime_owner_record(owner) is None


def test_runtime_owner_record_accepts_exact_boundary_values_and_empty_removal_is_safe(
    tmp_path: Path,
) -> None:
    owner = tmp_path / "owner"
    owner.mkdir()
    meta = owner / _runtime_storage._RUNTIME_OWNER_META_FILENAME
    meta.write_bytes(orjson.dumps({"format_version": 1, "pid": 1, "created_at": 0.0}))
    assert _runtime_storage._runtime_owner_record(owner) == (1, 0.0)
    _runtime_storage._remove_empty_runtime_owner_dir(owner)
    assert not owner.exists()
    owner.mkdir()
    (owner / "!payload").write_bytes(b"keep")
    _runtime_storage._remove_empty_runtime_owner_dir(owner)
    assert (owner / "!payload").read_bytes() == b"keep"


@pytest.mark.parametrize(
    "before, after, allow, raises",
    [(10, 10, False, False), (11, 11, False, True), (11, 11, True, True)],
)
def test_runtime_budget_boundaries_and_transaction_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    before: int,
    after: int,
    allow: bool,
    raises: bool,
) -> None:
    events: list[str] = []

    @contextmanager
    def lock(_path: Path):
        events.append("lock")
        yield

    measurements = iter((before, after))
    monkeypatch.setattr(_file_lock, "file_lock_for", lock)
    monkeypatch.setattr(
        _runtime_storage, "_recover_runtime_storage_once", lambda _root: events.append("recover")
    )
    monkeypatch.setattr(
        _runtime_storage,
        "_runtime_storage_usage_bytes",
        lambda _root: events.append("measure") or next(measurements),
    )
    monkeypatch.setattr(_runtime_storage, "int_env", lambda *_args: 10)
    manager = _runtime_storage._runtime_disk_budget_transaction(
        tmp_path, allow_existing_excess=allow
    )
    if raises:
        with pytest.raises(_runtime_storage.JsonRuntimeDiskBudgetExceededError):
            with manager:
                events.append("yield")
    else:
        with manager:
            events.append("yield")
    assert events == (
        ["lock", "recover", "measure"]
        if before > 10 and not allow
        else ["lock", "recover", "measure", "yield", "measure"]
    )


def test_runtime_budget_default_rejects_preexisting_excess(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_file_lock, "file_lock_for", lambda _path: nullcontext())
    monkeypatch.setattr(_runtime_storage, "_recover_runtime_storage_once", lambda _root: None)
    monkeypatch.setattr(_runtime_storage, "_runtime_storage_usage_bytes", lambda _root: 11)
    monkeypatch.setattr(_runtime_storage, "int_env", lambda *_args: 10)

    with pytest.raises(_runtime_storage.JsonRuntimeDiskBudgetExceededError):
        with _runtime_storage._runtime_disk_budget_transaction(tmp_path):
            pytest.fail("an over-budget transaction must not yield")
