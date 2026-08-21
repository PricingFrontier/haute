"""Direct mutation witnesses for JSON-source proof and runtime guardrails."""

from __future__ import annotations

from contextlib import contextmanager, nullcontext
from pathlib import Path
from typing import Any

import orjson
import pytest
import structlog

import haute._json_flatten as flatten_mod
import haute._json_shred as shred_mod


@pytest.fixture
def source(tmp_path: Path) -> tuple[Path, shred_mod._StrongFileRevision]:
    path = tmp_path / "data.json"
    path.write_bytes(b"[1]")
    return path, shred_mod._StrongFileRevision((1, 2), 3, 4, 5)


def _proof(revision: shred_mod._StrongFileRevision, digest: str = "a" * 64) -> dict[str, Any]:
    return shred_mod._DataFileSignatureRecord(3, 4, digest, revision).as_dict()


def _meta(proof: dict[str, Any]) -> dict[str, Any]:
    return {"schema_mode": "v2", "data_file": proof}


def _layers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[dict[str, Path], list[str]]:
    dirs = {layer: tmp_path / layer for layer in ("working", "committed")}
    for directory in dirs.values():
        directory.mkdir()
    seen: list[str] = []

    def cache_dir(_path: Path, layer: str) -> Path:
        seen.append(layer)
        return dirs[layer]

    monkeypatch.setattr(flatten_mod, "_json_cache_dir", cache_dir)
    return dirs, seen


@pytest.mark.parametrize("working", [None, b"{", orjson.dumps({"schema_mode": "v1"})])
def test_persisted_proof_falls_through_bad_working_layer_in_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source: tuple[Path, shred_mod._StrongFileRevision],
    working: bytes | None,
) -> None:
    path, revision = source
    dirs, seen = _layers(tmp_path, monkeypatch)
    if working is not None:
        (dirs["working"] / "meta.json").write_bytes(working)
    expected = _proof(revision)
    (dirs["committed"] / "meta.json").write_bytes(orjson.dumps(_meta(expected)))
    monkeypatch.setattr(shred_mod, "_strong_file_revision", lambda _path: revision)

    assert shred_mod._persisted_data_file_signature(
        path, revision
    ) == shred_mod._DataFileSignatureRecord(3, 4, "a" * 64, revision)
    assert seen == ["working", "committed"]


@pytest.mark.parametrize(
    ("working", "reason"),
    [
        (
            lambda rev: {**_proof(rev), "native_revision_proof_sha256": "wrong"},
            "invalid_matching_signature",
        ),
        (lambda rev: _proof(rev, "b" * 64), "conflicting_matching_signatures"),
    ],
)
def test_persisted_matching_proof_rejections_are_fail_closed_and_auditable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source: tuple[Path, shred_mod._StrongFileRevision],
    working: Any,
    reason: str,
) -> None:
    path, revision = source
    dirs, seen = _layers(tmp_path, monkeypatch)
    (dirs["working"] / "meta.json").write_bytes(orjson.dumps(_meta(working(revision))))
    (dirs["committed"] / "meta.json").write_bytes(orjson.dumps(_meta(_proof(revision))))
    monkeypatch.setattr(shred_mod, "_strong_file_revision", lambda _path: revision)

    with structlog.testing.capture_logs() as logs:
        assert shred_mod._persisted_data_file_signature(path, revision) is None

    assert seen == ["working", "committed"]
    assert [
        {
            key: entry[key]
            for key in ("event", "data_path", "matching_meta_paths", "reason", "action")
        }
        for entry in logs
    ] == [
        {
            "event": "json_source_persisted_proof_rejected",
            "data_path": str(path),
            "matching_meta_paths": [
                str(dirs["working"] / "meta.json"),
                str(dirs["committed"] / "meta.json"),
            ],
            "reason": reason,
            "action": "full_source_hash",
        }
    ]


def test_persisted_proof_returns_exact_record_only_if_revision_stays_put(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source: tuple[Path, shred_mod._StrongFileRevision],
) -> None:
    path, revision = source
    dirs, _ = _layers(tmp_path, monkeypatch)
    proof = _proof(revision)
    (dirs["working"] / "meta.json").write_bytes(orjson.dumps(_meta(proof)))
    moved = shred_mod._StrongFileRevision((1, 2), 3, 4, 6)
    monkeypatch.setattr(shred_mod, "_strong_file_revision", lambda _path: moved)
    assert shred_mod._persisted_data_file_signature(path, revision) is None
    monkeypatch.setattr(shred_mod, "_strong_file_revision", lambda _path: revision)
    assert shred_mod._persisted_data_file_signature(
        path, revision
    ) == shred_mod._DataFileSignatureRecord(3, 4, "a" * 64, revision)


@pytest.mark.parametrize("schema_mode", ["v1", "v3"])
def test_persisted_proof_ignores_complete_records_from_other_schema_modes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source: tuple[Path, shred_mod._StrongFileRevision],
    schema_mode: str,
) -> None:
    path, revision = source
    dirs, seen = _layers(tmp_path, monkeypatch)
    expected = _proof(revision)
    (dirs["working"] / "meta.json").write_bytes(
        orjson.dumps({"schema_mode": schema_mode, "data_file": expected})
    )
    (dirs["committed"] / "meta.json").write_bytes(orjson.dumps(_meta(expected)))
    monkeypatch.setattr(shred_mod, "_strong_file_revision", lambda _path: revision)

    assert shred_mod._persisted_data_file_signature(
        path, revision
    ) == shred_mod._DataFileSignatureRecord(3, 4, "a" * 64, revision)
    assert seen == ["working", "committed"]


@pytest.mark.parametrize(
    "working_data_file",
    [
        [],
        pytest.param(
            lambda: _proof(shred_mod._StrongFileRevision((1, 2), 3, 4, 6)),
            id="different-native-revision",
        ),
    ],
)
def test_persisted_proof_skips_independent_nonmatching_records_without_stopping(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source: tuple[Path, shred_mod._StrongFileRevision],
    working_data_file: Any,
) -> None:
    path, revision = source
    dirs, seen = _layers(tmp_path, monkeypatch)
    working_record = working_data_file() if callable(working_data_file) else working_data_file
    (dirs["working"] / "meta.json").write_bytes(orjson.dumps(_meta(working_record)))
    expected = _proof(revision)
    (dirs["committed"] / "meta.json").write_bytes(orjson.dumps(_meta(expected)))
    monkeypatch.setattr(shred_mod, "_strong_file_revision", lambda _path: revision)

    assert shred_mod._persisted_data_file_signature(
        path, revision
    ) == shred_mod._DataFileSignatureRecord(3, 4, "a" * 64, revision)
    assert seen == ["working", "committed"]


@pytest.mark.parametrize(
    "invalid_record",
    [
        pytest.param(
            lambda revision: {
                **_proof(revision),
                "size": True,
            },
            id="invalid-content-parts",
        ),
        pytest.param(
            lambda revision: shred_mod._DataFileSignatureRecord(
                revision.size - 1,
                revision.mtime_ns,
                "a" * 64,
                revision,
            ).as_dict(),
            id="size-below-native-revision",
        ),
        pytest.param(
            lambda revision: shred_mod._DataFileSignatureRecord(
                revision.size + 1,
                revision.mtime_ns,
                "a" * 64,
                revision,
            ).as_dict(),
            id="size-above-native-revision",
        ),
        pytest.param(
            lambda revision: shred_mod._DataFileSignatureRecord(
                revision.size,
                revision.mtime_ns - 1,
                "a" * 64,
                revision,
            ).as_dict(),
            id="mtime-below-native-revision",
        ),
        pytest.param(
            lambda revision: shred_mod._DataFileSignatureRecord(
                revision.size,
                revision.mtime_ns + 1,
                "a" * 64,
                revision,
            ).as_dict(),
            id="mtime-above-native-revision",
        ),
        pytest.param(
            lambda revision: {
                **_proof(revision),
                "native_revision_proof_sha256": None,
            },
            id="non-string-proof",
        ),
    ],
)
def test_persisted_proof_rejects_each_matching_record_defect_in_isolation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source: tuple[Path, shred_mod._StrongFileRevision],
    invalid_record: Any,
) -> None:
    path, revision = source
    dirs, _ = _layers(tmp_path, monkeypatch)
    (dirs["working"] / "meta.json").write_bytes(orjson.dumps(_meta(invalid_record(revision))))
    monkeypatch.setattr(shred_mod, "_strong_file_revision", lambda _path: revision)

    assert shred_mod._persisted_data_file_signature(path, revision) is None


@pytest.mark.parametrize(
    "bad_working",
    [
        None,
        b"{",
        orjson.dumps({"schema_mode": "v1"}),
        orjson.dumps({"schema_mode": "v2", "data_file": {"size": 3, "sha256": "wrong"}}),
    ],
)
def test_legacy_upgrade_skips_independent_bad_layers_and_writes_exact_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source: tuple[Path, shred_mod._StrongFileRevision],
    bad_working: bytes | None,
) -> None:
    path, revision = source
    dirs, seen = _layers(tmp_path, monkeypatch)
    working_meta = dirs["working"] / "meta.json"
    if bad_working is not None:
        working_meta.write_bytes(bad_working)
    legacy = {"schema_mode": "v2", "data_file": {"size": 3, "sha256": "a" * 64}}
    (dirs["committed"] / "meta.json").write_bytes(orjson.dumps(legacy))
    signature = shred_mod._DataFileSignatureRecord(3, 4, "a" * 64, revision)
    monkeypatch.setattr(shred_mod, "_strong_file_revision", lambda _path: revision)

    shred_mod._upgrade_legacy_persisted_source_proofs(path, signature, revision)

    assert seen == ["working", "committed"]
    assert (working_meta.read_bytes() if working_meta.exists() else None) == bad_working
    assert (
        orjson.loads((dirs["committed"] / "meta.json").read_bytes())["data_file"]
        == signature.as_dict()
    )


def test_legacy_upgrade_current_payload_is_untouched_and_movement_stops_later_layers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source: tuple[Path, shred_mod._StrongFileRevision],
) -> None:
    path, revision = source
    dirs, _ = _layers(tmp_path, monkeypatch)
    signature = shred_mod._DataFileSignatureRecord(3, 4, "a" * 64, revision)
    current = _meta(signature.as_dict())
    (dirs["working"] / "meta.json").write_bytes(orjson.dumps(current))
    (dirs["committed"] / "meta.json").write_bytes(orjson.dumps(current))
    real_replace = shred_mod.os.replace
    monkeypatch.setattr(
        shred_mod.os, "replace", lambda *_args: pytest.fail("current proof rewritten")
    )
    with structlog.testing.capture_logs() as current_logs:
        shred_mod._upgrade_legacy_persisted_source_proofs(path, signature, revision)
    assert current_logs == []

    legacy = {"schema_mode": "v2", "data_file": {"size": 3, "sha256": "a" * 64}}
    for directory in dirs.values():
        (directory / "meta.json").write_bytes(orjson.dumps(legacy))
    moved = shred_mod._StrongFileRevision((1, 2), 3, 4, 6)
    observations = iter((revision, moved))
    monkeypatch.setattr(shred_mod.os, "replace", real_replace)
    monkeypatch.setattr(shred_mod, "_strong_file_revision", lambda _path: next(observations))
    shred_mod._upgrade_legacy_persisted_source_proofs(path, signature, revision)
    assert (
        orjson.loads((dirs["working"] / "meta.json").read_bytes())["data_file"]
        == signature.as_dict()
    )
    assert orjson.loads((dirs["committed"] / "meta.json").read_bytes()) == legacy


@pytest.mark.parametrize("schema_mode", ["v1", "v3"])
def test_legacy_upgrade_never_rewrites_other_schema_modes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source: tuple[Path, shred_mod._StrongFileRevision],
    schema_mode: str,
) -> None:
    path, revision = source
    dirs, _ = _layers(tmp_path, monkeypatch)
    legacy = {
        "schema_mode": schema_mode,
        "data_file": {"size": 3, "sha256": "a" * 64},
    }
    meta_path = dirs["working"] / "meta.json"
    meta_path.write_bytes(orjson.dumps(legacy))
    signature = shred_mod._DataFileSignatureRecord(3, 4, "a" * 64, revision)
    monkeypatch.setattr(shred_mod, "_strong_file_revision", lambda _path: revision)

    with structlog.testing.capture_logs() as logs:
        shred_mod._upgrade_legacy_persisted_source_proofs(path, signature, revision)

    assert orjson.loads(meta_path.read_bytes()) == legacy
    assert logs == []


def test_legacy_upgrade_success_does_not_report_missing_temp_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source: tuple[Path, shred_mod._StrongFileRevision],
) -> None:
    path, revision = source
    dirs, _ = _layers(tmp_path, monkeypatch)
    meta_path = dirs["working"] / "meta.json"
    meta_path.write_bytes(
        orjson.dumps({"schema_mode": "v2", "data_file": {"size": 3, "sha256": "a" * 64}})
    )
    signature = shred_mod._DataFileSignatureRecord(3, 4, "a" * 64, revision)
    monkeypatch.setattr(shred_mod, "_strong_file_revision", lambda _path: revision)

    with structlog.testing.capture_logs() as logs:
        shred_mod._upgrade_legacy_persisted_source_proofs(path, signature, revision)

    assert [entry["event"] for entry in logs] == ["json_source_persisted_proof_upgraded"]
    assert orjson.loads(meta_path.read_bytes())["data_file"] == signature.as_dict()


def test_legacy_upgrade_write_and_cleanup_failures_are_visible(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source: tuple[Path, shred_mod._StrongFileRevision],
) -> None:
    path, revision = source
    dirs, _ = _layers(tmp_path, monkeypatch)
    legacy = {"schema_mode": "v2", "data_file": {"size": 3, "sha256": "a" * 64}}
    meta_path = dirs["working"] / "meta.json"
    meta_path.write_bytes(orjson.dumps(legacy))
    signature = shred_mod._DataFileSignatureRecord(3, 4, "a" * 64, revision)
    monkeypatch.setattr(shred_mod, "_strong_file_revision", lambda _path: revision)
    monkeypatch.setattr(
        shred_mod.os, "replace", lambda *_args: (_ for _ in ()).throw(OSError("readonly"))
    )
    real_unlink = Path.unlink
    temp_paths: list[Path] = []

    def deny_cleanup(candidate: Path, *, missing_ok: bool = False) -> None:
        if candidate.name.startswith(".meta.json."):
            temp_paths.append(candidate)
            raise OSError("cleanup denied")
        real_unlink(candidate, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", deny_cleanup)
    with structlog.testing.capture_logs() as logs:
        shred_mod._upgrade_legacy_persisted_source_proofs(path, signature, revision)
    assert orjson.loads(meta_path.read_bytes()) == legacy
    assert [(entry["event"], entry.get("action")) for entry in logs] == [
        ("json_source_persisted_proof_upgrade_failed", "retain_full_hash_result"),
        ("json_source_persisted_proof_temp_cleanup_failed", None),
    ]
    assert logs[-1]["temp_path"] == str(temp_paths[0])
    real_unlink(temp_paths[0], missing_ok=True)


def test_signature_memo_cached_hits_lru_unavailable_and_gate_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = [tmp_path / f"{name}.json" for name in ("a", "b", "c")]
    for path in paths:
        path.write_bytes(b"[1]")
    revisions = {
        path.resolve(): shred_mod._StrongFileRevision((1, index), 3, 4, 5)
        for index, path in enumerate(paths, 1)
    }
    memo = shred_mod._DataFileSignatureMemo(max_entries=2)
    calls: list[Path] = []
    monkeypatch.setattr(shred_mod, "_strong_file_revision", lambda path: revisions[path])
    monkeypatch.setattr(shred_mod, "_persisted_data_file_signature", lambda _p, _r: None)
    monkeypatch.setattr(
        shred_mod,
        "_revision_gated_data_file_signature",
        lambda p, r: calls.append(p) or shred_mod._DataFileSignatureRecord(3, 4, "a" * 64, r),
    )
    memo.get(paths[0], upgrade_legacy_proofs=False)
    memo.get(paths[1])
    memo.get(paths[0])
    memo.get(paths[2])
    assert calls == [path.resolve() for path in (paths[0], paths[1], paths[2])]
    assert list(memo._entries) == [str(paths[0].resolve()).lower(), str(paths[2].resolve()).lower()]
    monkeypatch.setattr(shred_mod, "_strong_file_revision", lambda _p: None)
    monkeypatch.setattr(
        shred_mod,
        "_uncached_data_file_signature",
        lambda _p: shred_mod._DataFileSignatureRecord(3, 4, "u" * 64, None),
    )
    assert memo.get(paths[1], upgrade_legacy_proofs=False)["sha256"] == "u" * 64
    assert len(memo) == 2


def test_signature_memo_default_upgrades_and_equal_fresh_revisions_hit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "data.json"
    path.write_bytes(b"[1]")
    revision = shred_mod._StrongFileRevision((1, 2), 3, 4, 5)
    memo = shred_mod._DataFileSignatureMemo(max_entries=1)
    upgrades: list[tuple[Path, shred_mod._StrongFileRevision]] = []
    hashes = 0

    def fresh_revision(_path: Path) -> shred_mod._StrongFileRevision:
        return shred_mod._StrongFileRevision(
            revision.file_identity,
            revision.size,
            revision.mtime_ns,
            revision.change_token,
        )

    def hash_once(_path: Path, current: shred_mod._StrongFileRevision):
        nonlocal hashes
        hashes += 1
        return shred_mod._DataFileSignatureRecord(3, 4, "a" * 64, current)

    monkeypatch.setattr(shred_mod, "_strong_file_revision", fresh_revision)
    monkeypatch.setattr(shred_mod, "_persisted_data_file_signature", lambda *_args: None)
    monkeypatch.setattr(shred_mod, "_revision_gated_data_file_signature", hash_once)
    monkeypatch.setattr(
        shred_mod,
        "_upgrade_legacy_persisted_source_proofs",
        lambda source_path, _signature, current: upgrades.append((source_path, current)),
    )

    first = memo.get(path)
    second = memo.get(path)
    assert first == second
    assert hashes == 1
    assert upgrades == [(path.resolve(), revision)]


@pytest.mark.parametrize("participants, retained", [(0, False), (1, True), (2, True)])
def test_signature_memo_failed_flight_cleans_only_zero_participant_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, participants: int, retained: bool
) -> None:
    path = tmp_path / "data.json"
    path.write_bytes(b"[1]")
    revision = shred_mod._StrongFileRevision((1, 2), 3, 4, 5)
    memo = shred_mod._DataFileSignatureMemo(max_entries=1)
    key = str(path.resolve()).lower()
    gate = shred_mod._DataFileSignatureLoadGate()
    gate.participants = participants
    memo._load_gates[key] = gate
    monkeypatch.setattr(shred_mod, "_strong_file_revision", lambda _p: revision)
    monkeypatch.setattr(shred_mod, "_persisted_data_file_signature", lambda _p, _r: None)
    monkeypatch.setattr(
        shred_mod,
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
    old, new = tmp_path / "old.json", tmp_path / "new.json"
    old.write_bytes(b"[]")
    new.write_bytes(b"[]")
    old_key, new_key = str(old.resolve()).lower(), str(new.resolve()).lower()
    old_revision, new_revision = (
        shred_mod._StrongFileRevision((1, 1), 2, 3, 4),
        shred_mod._StrongFileRevision((1, 2), 2, 3, 4),
    )
    memo = shred_mod._DataFileSignatureMemo(max_entries=1)
    memo._entries[old_key] = (
        old_revision,
        shred_mod._DataFileSignatureRecord(2, 3, "a" * 64, old_revision),
    )
    gate = shred_mod._DataFileSignatureLoadGate()
    gate.participants = int(active)
    memo._load_gates[old_key] = gate
    monkeypatch.setattr(
        shred_mod,
        "_strong_file_revision",
        lambda p: new_revision if p == new.resolve() else old_revision,
    )
    monkeypatch.setattr(shred_mod, "_persisted_data_file_signature", lambda _p, _r: None)
    monkeypatch.setattr(
        shred_mod,
        "_revision_gated_data_file_signature",
        lambda _p, r: shred_mod._DataFileSignatureRecord(2, 3, "b" * 64, r),
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
    (owner / shred_mod._RUNTIME_OWNER_META_FILENAME).write_bytes(orjson.dumps(payload))
    assert shred_mod._runtime_owner_record(owner) is None


def test_runtime_owner_record_accepts_exact_boundary_values_and_empty_removal_is_safe(
    tmp_path: Path,
) -> None:
    owner = tmp_path / "owner"
    owner.mkdir()
    meta = owner / shred_mod._RUNTIME_OWNER_META_FILENAME
    meta.write_bytes(orjson.dumps({"format_version": 1, "pid": 1, "created_at": 0.0}))
    assert shred_mod._runtime_owner_record(owner) == (1, 0.0)
    shred_mod._remove_empty_runtime_owner_dir(owner)
    assert not owner.exists()
    owner.mkdir()
    (owner / "!payload").write_bytes(b"keep")
    shred_mod._remove_empty_runtime_owner_dir(owner)
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
    monkeypatch.setattr(shred_mod, "_build_lock_for", lock)
    monkeypatch.setattr(
        shred_mod, "_recover_runtime_storage_once", lambda _root: events.append("recover")
    )
    monkeypatch.setattr(
        shred_mod,
        "_runtime_storage_usage_bytes",
        lambda _root: events.append("measure") or next(measurements),
    )
    monkeypatch.setattr(shred_mod, "int_env", lambda *_args: 10)
    manager = shred_mod._runtime_disk_budget_transaction(tmp_path, allow_existing_excess=allow)
    if raises:
        with pytest.raises(shred_mod.JsonRuntimeDiskBudgetExceededError):
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
    monkeypatch.setattr(shred_mod, "_build_lock_for", lambda _path: nullcontext())
    monkeypatch.setattr(shred_mod, "_recover_runtime_storage_once", lambda _root: None)
    monkeypatch.setattr(shred_mod, "_runtime_storage_usage_bytes", lambda _root: 11)
    monkeypatch.setattr(shred_mod, "int_env", lambda *_args: 10)

    with pytest.raises(shred_mod.JsonRuntimeDiskBudgetExceededError):
        with shred_mod._runtime_disk_budget_transaction(tmp_path):
            pytest.fail("an over-budget transaction must not yield")
