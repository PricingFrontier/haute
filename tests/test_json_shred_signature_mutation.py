"""Direct mutation witnesses for JSON-source proof and runtime guardrails."""

from __future__ import annotations

from contextlib import contextmanager, nullcontext
from pathlib import Path

import orjson
import pytest

from haute._json_shred import _publication, _runtime_storage, _source_proof


@pytest.fixture
def source(tmp_path: Path) -> tuple[Path, _source_proof._StrongFileRevision]:
    path = tmp_path / "data.json"
    path.write_bytes(b"[1]")
    return path, _source_proof._StrongFileRevision((1, 2), 3, 4, 5)


def test_signature_memo_cached_hits_lru_unavailable_and_gate_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths: list[Path] = []
    for name in ("a", "b", "c"):
        path = tmp_path / f"{name}.json"
        path.write_bytes(b"[1]")
        paths.append(path)
    revisions = {
        path.resolve(): _source_proof._StrongFileRevision((1, index), 3, 4, 5)
        for index, path in enumerate(paths, 1)
    }
    memo = _source_proof._DataFileSignatureMemo(max_entries=2)
    calls: list[Path] = []
    monkeypatch.setattr(_source_proof, "_strong_file_revision", lambda path: revisions[path])
    monkeypatch.setattr(
        _source_proof,
        "_revision_gated_data_file_signature",
        lambda p, r: calls.append(p) or _source_proof._DataFileSignatureRecord(3, 4, "a" * 64, r),
    )
    memo.get(paths[0])
    memo.get(paths[1])
    memo.get(paths[0])
    memo.get(paths[2])
    assert calls == [path.resolve() for path in (paths[0], paths[1], paths[2])]
    assert list(memo._entries) == [str(paths[0].resolve()).lower(), str(paths[2].resolve()).lower()]
    monkeypatch.setattr(_source_proof, "_strong_file_revision", lambda _p: None)
    monkeypatch.setattr(
        _source_proof,
        "_uncached_data_file_signature",
        lambda _p: _source_proof._DataFileSignatureRecord(3, 4, "u" * 64, None),
    )
    assert memo.get(paths[1])["sha256"] == "u" * 64
    assert len(memo) == 2


def test_signature_memo_equal_fresh_revisions_hit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "data.json"
    path.write_bytes(b"[1]")
    revision = _source_proof._StrongFileRevision((1, 2), 3, 4, 5)
    memo = _source_proof._DataFileSignatureMemo(max_entries=1)
    hashes = 0

    def fresh_revision(_path: Path) -> _source_proof._StrongFileRevision:
        return _source_proof._StrongFileRevision(
            revision.file_identity,
            revision.size,
            revision.mtime_ns,
            revision.change_token,
        )

    def hash_once(_path: Path, current: _source_proof._StrongFileRevision):
        nonlocal hashes
        hashes += 1
        return _source_proof._DataFileSignatureRecord(3, 4, "a" * 64, current)

    monkeypatch.setattr(_source_proof, "_strong_file_revision", fresh_revision)
    monkeypatch.setattr(_source_proof, "_revision_gated_data_file_signature", hash_once)

    first = memo.get(path)
    second = memo.get(path)
    assert first == second == {"size": 3, "mtime_ns": 4, "sha256": "a" * 64}
    assert hashes == 1


@pytest.mark.parametrize("participants, retained", [(0, False), (1, True), (2, True)])
def test_signature_memo_failed_flight_cleans_only_zero_participant_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, participants: int, retained: bool
) -> None:
    path = tmp_path / "data.json"
    path.write_bytes(b"[1]")
    revision = _source_proof._StrongFileRevision((1, 2), 3, 4, 5)
    memo = _source_proof._DataFileSignatureMemo(max_entries=1)
    key = str(path.resolve()).lower()
    gate = _source_proof._DataFileSignatureLoadGate()
    gate.participants = participants
    memo._load_gates[key] = gate
    monkeypatch.setattr(_source_proof, "_strong_file_revision", lambda _p: revision)
    monkeypatch.setattr(
        _source_proof,
        "_revision_gated_data_file_signature",
        lambda *_args: (_ for _ in ()).throw(OSError("hash failed")),
    )
    with pytest.raises(OSError, match="hash failed"):
        memo.get(path)
    assert (key in memo._load_gates) is retained
    assert gate.participants == participants


@pytest.mark.parametrize("active, retained", [(False, False), (True, True)])
def test_signature_memo_eviction_keeps_only_active_old_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, active: bool, retained: bool
) -> None:
    old = tmp_path / "old.json"
    new = tmp_path / "new.json"
    old.write_bytes(b"[]")
    new.write_bytes(b"[]")
    old_key, new_key = str(old.resolve()).lower(), str(new.resolve()).lower()
    old_revision, new_revision = (
        _source_proof._StrongFileRevision((1, 1), 2, 3, 4),
        _source_proof._StrongFileRevision((1, 2), 2, 3, 4),
    )
    memo = _source_proof._DataFileSignatureMemo(max_entries=1)
    memo._entries[old_key] = (
        old_revision,
        _source_proof._DataFileSignatureRecord(2, 3, "a" * 64, old_revision),
    )
    gate = _source_proof._DataFileSignatureLoadGate()
    gate.participants = int(active)
    memo._load_gates[old_key] = gate
    monkeypatch.setattr(
        _source_proof,
        "_strong_file_revision",
        lambda p: new_revision if p == new.resolve() else old_revision,
    )
    monkeypatch.setattr(
        _source_proof,
        "_revision_gated_data_file_signature",
        lambda _p, r: _source_proof._DataFileSignatureRecord(2, 3, "b" * 64, r),
    )
    memo.get(new)
    assert (old_key in memo._load_gates) is retained and new_key in memo._entries


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
    monkeypatch.setattr(_publication, "_build_lock_for", lock)
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
    monkeypatch.setattr(_publication, "_build_lock_for", lambda _path: nullcontext())
    monkeypatch.setattr(_runtime_storage, "_recover_runtime_storage_once", lambda _root: None)
    monkeypatch.setattr(_runtime_storage, "_runtime_storage_usage_bytes", lambda _root: 11)
    monkeypatch.setattr(_runtime_storage, "int_env", lambda *_args: 10)

    with pytest.raises(_runtime_storage.JsonRuntimeDiskBudgetExceededError):
        with _runtime_storage._runtime_disk_budget_transaction(tmp_path):
            pytest.fail("an over-budget transaction must not yield")
