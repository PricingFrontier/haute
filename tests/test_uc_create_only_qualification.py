"""The UC create-only release gate's logic, against an in-memory Files API.

These cases test what the gate accepts; whether a real Unity Catalog volume
admits exactly one of several simultaneous creates is the separate live run
(specs/hosted-project-storage/high-level.md).
"""

from __future__ import annotations

import json
import threading

import pytest
from databricks.sdk.errors import NotFound
from databricks.sdk.errors.platform import PermissionDenied, ResourceConflict

from scripts import uc_create_only_qualification as qualification

URL = "uc://main.pricing.scratch/qualification"
ROOT = "/Volumes/main/pricing/scratch/qualification"


class _Download:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return self._payload


class _Files:
    """Atomic create-only uploads, with scripted failures per writer."""

    def __init__(
        self,
        *,
        failures: dict[int, Exception] | None = None,
        admit_every_create: bool = False,
        stored_bytes: bytes | None = None,
        delete_failure: Exception | None = None,
    ) -> None:
        self.store: dict[str, bytes] = {}
        self.uploads: list[str] = []
        self._lock = threading.Lock()
        self._failures = failures or {}
        self._admit_every_create = admit_every_create
        self._stored_bytes = stored_bytes
        self._delete_failure = delete_failure

    def upload(self, path: str, contents, overwrite: bool | None = None) -> None:
        payload = contents.read()
        writer = json.loads(payload)["writer"]
        with self._lock:
            self.uploads.append(path)
        if writer in self._failures:
            raise self._failures[writer]
        with self._lock:
            if overwrite is False and path in self.store and not self._admit_every_create:
                raise ResourceConflict(f"{path} already exists")
            self.store[path] = payload if self._stored_bytes is None else self._stored_bytes

    def download(self, path: str):
        if path not in self.store:
            raise NotFound(path)

        class _Response:
            contents = _Download(self.store[path])

        return _Response()

    def get_metadata(self, path: str) -> object:
        if path not in self.store:
            raise NotFound(path)
        return object()

    def delete(self, path: str) -> None:
        if self._delete_failure is not None:
            raise self._delete_failure
        if path not in self.store:
            raise NotFound(path)
        del self.store[path]


class _Client:
    def __init__(self, files: _Files) -> None:
        self.files = files


def _round_path(number: int) -> str:
    return f"{ROOT}/round-{number}/pointer.json"


def test_one_winner_and_conflict_refusals_qualify_after_every_round(
    capsys: pytest.CaptureFixture[str],
) -> None:
    files = _Files()

    assert qualification.qualify(URL, writers=4, rounds=3, client=_Client(files)) == 0

    assert len(files.uploads) == 12
    assert files.store == {}, "every round's object is removed"
    assert "VERDICT: QUALIFIED" in capsys.readouterr().out


@pytest.mark.parametrize(
    "error",
    [
        PermissionDenied("caller lacks WRITE VOLUME"),
        TimeoutError("upload timed out"),
        ConnectionError("connection reset"),
    ],
    ids=["permission", "timeout", "transport"],
)
def test_one_winner_and_a_writer_failing_before_contention_does_not_qualify(
    capsys: pytest.CaptureFixture[str], error: Exception
) -> None:
    files = _Files(failures={1: error, 2: error, 3: error})

    assert qualification.qualify(URL, writers=4, rounds=2, client=_Client(files)) == 1

    out = capsys.readouterr().out
    assert "VERDICT: NOT QUALIFIED" in out
    assert f"w1: failed before contention: {type(error).__name__}: {error}" in out
    assert files.store == {}


def test_a_failure_beside_a_real_conflict_still_does_not_qualify() -> None:
    files = _Files(failures={2: PermissionDenied("caller lacks WRITE VOLUME")})

    result = qualification.run_round(_Client(files), _round_path(1), 3)

    assert not result.qualified
    kinds = sorted(writer.kind for writer in result.writers)
    assert kinds == ["conflict", "error", "stored"]


@pytest.mark.parametrize(("writers", "rounds"), [(1, 5), (0, 5), (8, 0), (2, -1)])
def test_too_few_writers_or_rounds_is_refused_before_any_upload(
    capsys: pytest.CaptureFixture[str], writers: int, rounds: int
) -> None:
    files = _Files()

    assert qualification.qualify(URL, writers, rounds, _Client(files)) == 2

    assert files.uploads == []
    assert "Refused" in capsys.readouterr().out


def test_more_than_one_winner_does_not_qualify() -> None:
    files = _Files(admit_every_create=True)

    assert qualification.qualify(URL, writers=3, rounds=1, client=_Client(files)) == 1
    assert files.store == {}


def test_stored_bytes_that_are_not_the_winners_do_not_qualify(
    capsys: pytest.CaptureFixture[str],
) -> None:
    files = _Files(stored_bytes=b'{"writer": 99}')

    assert qualification.qualify(URL, writers=3, rounds=1, client=_Client(files)) == 1
    assert "are not winner" in capsys.readouterr().out


def test_a_round_path_that_already_holds_an_object_is_left_in_place(
    capsys: pytest.CaptureFixture[str],
) -> None:
    files = _Files()
    files.store[_round_path(1)] = b"someone else's"

    assert qualification.qualify(URL, writers=3, rounds=2, client=_Client(files)) == 1

    assert files.store == {_round_path(1): b"someone else's"}
    assert _round_path(1) not in files.uploads
    assert "already held an object this run did not create" in capsys.readouterr().out


def test_a_failed_cleanup_fails_the_run_naming_the_path(
    capsys: pytest.CaptureFixture[str],
) -> None:
    files = _Files(delete_failure=PermissionDenied("caller lacks delete"))

    assert qualification.qualify(URL, writers=3, rounds=1, client=_Client(files)) == 1

    out = capsys.readouterr().out
    assert f"could not delete {_round_path(1)}" in out
    assert "remove it by hand" in out
