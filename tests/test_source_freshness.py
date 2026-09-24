"""One source-freshness proof for every local file.

Covers the freshness observation (native revision, or a settled stat where the
platform has none), the shared content signature and its reuse across every
consumer, and a file that keeps changing while it is proved.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from haute._hashing import content_hash
from haute._json_shred import _source_proof
from haute._json_shred._source_proof import (
    SETTLE_SECONDS,
    FileSignature,
    Freshness,
    SourceChangedError,
    clear_file_signatures,
    file_signature,
    observe_freshness,
)


def _write(path: Path, text: str = "id,value\n1,a\n") -> Path:
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture()
def hashes(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    """Every complete-content hash the proof computes, in order."""
    calls: list[Path] = []
    real = _source_proof._hash_file

    def counting(path: Path) -> str:
        calls.append(path)
        return real(path)

    monkeypatch.setattr(_source_proof, "_hash_file", counting)
    return calls


@pytest.fixture()
def without_native_revision(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    """A platform with no native revision; returns the warnings it logs."""
    warnings: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(_source_proof, "_strong_file_revision", lambda _path: None)
    monkeypatch.setattr(
        _source_proof.logger,
        "warning",
        lambda event, **fields: warnings.append((event, fields)),
    )
    return warnings


def _age(path: Path, seconds: float) -> None:
    observed = path.stat()
    moved = observed.st_mtime_ns - int(seconds * 1_000_000_000)
    os.utime(path, ns=(moved, moved))


# ------------------------------------------------------------- observation


def test_a_native_revision_is_the_reusable_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _write(tmp_path / "a.csv")
    revision = object()
    monkeypatch.setattr(_source_proof, "_strong_file_revision", lambda _path: revision)

    assert observe_freshness(path) == Freshness(revision, reusable=True)


def test_without_a_native_revision_only_a_settled_stat_is_reusable(
    tmp_path: Path, without_native_revision: list[tuple[str, dict[str, Any]]]
) -> None:
    young = _write(tmp_path / "young.csv")
    settled = _write(tmp_path / "settled.csv")
    _age(settled, SETTLE_SECONDS + 1)

    young_freshness = observe_freshness(young)
    settled_freshness = observe_freshness(settled)

    assert young_freshness.reusable is False
    assert settled_freshness.reusable is True
    observed = settled.stat()
    assert settled_freshness.token == (
        "stat",
        observed.st_dev,
        observed.st_ino,
        observed.st_size,
        observed.st_mtime_ns,
        observed.st_ctime_ns,
    )


def test_a_missing_revision_is_logged_once_per_path(
    tmp_path: Path, without_native_revision: list[tuple[str, dict[str, Any]]]
) -> None:
    first = _write(tmp_path / "first.csv")
    second = _write(tmp_path / "second.csv")

    observe_freshness(first)
    observe_freshness(first)
    observe_freshness(second)

    assert [event for event, _fields in without_native_revision] == [
        "source_revision_unavailable",
        "source_revision_unavailable",
    ]
    assert {fields["action"] for _event, fields in without_native_revision} == {
        "stat_gate_after_settle"
    }
    clear_file_signatures()
    observe_freshness(first)
    assert len(without_native_revision) == 3


def test_a_missing_file_cannot_be_observed(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        observe_freshness(tmp_path / "absent.csv")


# --------------------------------------------------------------- signature


def test_the_signature_is_the_complete_content_hash(tmp_path: Path) -> None:
    path = _write(tmp_path / "a.csv")

    signature = file_signature(path)

    observed = path.stat()
    assert signature == FileSignature(observed.st_size, observed.st_mtime_ns, content_hash(path))
    assert signature.source_signature == f"xxh64:{content_hash(path)}:{observed.st_size}"


def test_an_unchanged_file_is_hashed_once_and_a_write_hashes_again(
    tmp_path: Path, hashes: list[Path]
) -> None:
    path = _write(tmp_path / "a.csv")

    first = file_signature(path)
    assert file_signature(tmp_path / "." / "a.csv") == first
    assert len(hashes) == 1

    _write(path, "id,value\n2,b\n")
    second = file_signature(path)

    assert len(hashes) == 2
    assert second.digest == content_hash(path) != first.digest


def test_a_young_file_without_a_native_revision_is_hashed_every_time(
    tmp_path: Path,
    hashes: list[Path],
    without_native_revision: list[tuple[str, dict[str, Any]]],
) -> None:
    path = _write(tmp_path / "a.csv")

    file_signature(path)
    file_signature(path)

    assert len(hashes) == 2


def test_a_settled_file_without_a_native_revision_is_hashed_once(
    tmp_path: Path,
    hashes: list[Path],
    without_native_revision: list[tuple[str, dict[str, Any]]],
) -> None:
    path = _write(tmp_path / "a.csv")
    _age(path, SETTLE_SECONDS + 1)

    file_signature(path)
    file_signature(path)

    assert len(hashes) == 1


def test_clearing_forgets_every_proof(tmp_path: Path, hashes: list[Path]) -> None:
    path = _write(tmp_path / "a.csv")
    file_signature(path)

    clear_file_signatures()
    file_signature(path)

    assert len(hashes) == 2


def test_a_file_that_changes_once_while_hashed_is_signed_in_its_settled_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _write(tmp_path / "a.csv")
    real = _source_proof._hash_file
    calls: list[Path] = []

    def racing(target: Path) -> str:
        calls.append(target)
        if len(calls) == 1:
            _write(target, "id,value\n1,a\n2,b\n")
        return real(target)

    monkeypatch.setattr(_source_proof, "_hash_file", racing)

    signature = file_signature(path)

    assert len(calls) == 2
    assert signature.digest == content_hash(path)
    assert signature.size == path.stat().st_size


def test_a_file_that_keeps_changing_while_hashed_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _write(tmp_path / "a.csv")
    real = _source_proof._hash_file
    rows = ["id,value"]

    def perpetually_racing(target: Path) -> str:
        rows.append(f"{len(rows)},x")
        _write(target, "\n".join(rows))
        return real(target)

    monkeypatch.setattr(_source_proof, "_hash_file", perpetually_racing)

    with pytest.raises(SourceChangedError, match="changed on disk while loading") as raised:
        file_signature(path)
    assert isinstance(raised.value, OSError)
    assert isinstance(raised.value, RuntimeError)


def test_every_consumer_shares_one_proof(tmp_path: Path, hashes: list[Path]) -> None:
    """A Data Input's freshness, runtime identity and a utility hash hash once."""
    from haute._cache import _utility_file_hash
    from haute._input_providers import source_signature
    from haute.execution import _stat_gated_runtime_path_fingerprint

    path = _write(tmp_path / "quotes.csv")
    config = {"inputType": "file", "format": "csv", "mode": "scan", "path": str(path)}

    signature = source_signature(config, base_dir=tmp_path)
    fingerprint = _stat_gated_runtime_path_fingerprint(path)
    utility = _utility_file_hash(path, None)

    digest = content_hash(path)
    assert signature == f"xxh64:{digest}:{path.stat().st_size}"
    assert fingerprint["content_hash"] == utility == digest
    assert len(hashes) == 1


def test_an_api_input_source_shares_the_same_proof(tmp_path: Path, hashes: list[Path]) -> None:
    from haute._json_shred._snapshots import api_input_source_signature
    from haute.execution import _stat_gated_runtime_path_fingerprint

    path = _write(tmp_path / "quotes.jsonl", '{"id": 1}\n')

    signature = api_input_source_signature(path)
    fingerprint = _stat_gated_runtime_path_fingerprint(path)

    assert signature == f"xxh64:{content_hash(path)}:{path.stat().st_size}"
    assert fingerprint["content_hash"] == content_hash(path)
    assert api_input_source_signature(tmp_path / "absent.jsonl") == "missing"
    assert len(hashes) == 1


def test_a_missing_path_or_a_directory_is_signed_by_its_stat(
    tmp_path: Path, hashes: list[Path]
) -> None:
    from haute.execution import _stat_gated_runtime_path_fingerprint

    missing = tmp_path / "absent.csv"
    directory = tmp_path / "parts"
    directory.mkdir()
    observed = directory.stat()

    assert _stat_gated_runtime_path_fingerprint(missing) == {
        "path": str(missing.resolve()),
        "exists": False,
    }
    assert _stat_gated_runtime_path_fingerprint(directory) == {
        "path": str(directory.resolve()),
        "exists": True,
        "is_file": False,
        "size": observed.st_size,
        "mtime_ns": observed.st_mtime_ns,
    }
    assert hashes == []


def test_a_file_exactly_the_settle_age_old_is_reusable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    without_native_revision: list[tuple[str, dict[str, Any]]],
) -> None:
    path = _write(tmp_path / "a.csv")
    mtime = path.stat().st_mtime
    monkeypatch.setattr(_source_proof.time, "time", lambda: mtime + SETTLE_SECONDS)

    assert observe_freshness(path).reusable is True


@pytest.mark.parametrize(
    ("os_name", "reader"),
    [("".join(["n", "t"]), "_windows_strong_file_revision"), ("ce", "_posix_strong_file_revision")],
)
def test_the_native_revision_reader_follows_the_os(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, os_name: str, reader: str
) -> None:
    calls: list[str] = []
    for name in ("_windows_strong_file_revision", "_posix_strong_file_revision"):
        monkeypatch.setattr(_source_proof, name, lambda _path, name=name: calls.append(name))
    monkeypatch.setattr(_source_proof.os, "name", os_name)

    _source_proof._strong_file_revision(tmp_path / "a.csv")

    assert calls == [reader]
