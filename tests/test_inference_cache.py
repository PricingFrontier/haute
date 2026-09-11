from __future__ import annotations

import copy
import dataclasses
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import orjson
import pytest

from haute._api_input_schema import ApiInputSchemaError
from haute._json_shred import _inference_cache as module
from haute._json_shred._inference_cache import InferenceCache
from haute._json_shred._source_proof import _StrongFileRevision


@pytest.fixture
def base_revision() -> _StrongFileRevision:
    return _StrongFileRevision((1, 2), 10, 20, 30)


@pytest.fixture
def current_revision(
    monkeypatch: pytest.MonkeyPatch,
    base_revision: _StrongFileRevision,
) -> list[_StrongFileRevision | None]:
    current: list[_StrongFileRevision | None] = [base_revision]
    monkeypatch.setattr(
        module._source_proof,
        "_strong_file_revision",
        lambda path: current[0],
    )
    return current


@pytest.fixture
def payload() -> dict[str, Any]:
    return {"tables": [{"columns": [{"name": "value", "type": "int"}]}]}


def test_stable_hit_single_load_and_defensive_copies(
    current_revision: list[_StrongFileRevision | None],
    tmp_path: Path,
    payload: dict[str, Any],
) -> None:
    cache = InferenceCache()
    calls: list[Path] = []

    def loader(path: Path) -> dict[str, Any]:
        assert isinstance(path, Path)
        assert path.is_absolute()
        calls.append(path)
        return copy.deepcopy(payload)

    path = tmp_path / "data.json"
    res1 = cache.get(path, record_limit=10, loader=loader)
    assert len(calls) == 1
    assert res1 == payload

    res1["tables"][0]["columns"][0]["name"] = "mutated"

    # A fresh filesystem observation has equal values, not object identity.
    original_revision = current_revision[0]
    assert original_revision is not None
    current_revision[0] = dataclasses.replace(original_revision)
    assert current_revision[0] is not original_revision
    res2 = cache.get(path, record_limit=10, loader=loader)
    assert len(calls) == 1
    assert res2["tables"][0]["columns"][0]["name"] == "value"
    assert res2 is not res1
    assert res2["tables"][0] is not res1["tables"][0]

    cache.clear()
    res3 = cache.get(path, record_limit=10, loader=loader)
    assert len(calls) == 2
    assert res3["tables"][0]["columns"][0]["name"] == "value"


def test_different_record_limits_require_separate_loads(
    current_revision: list[_StrongFileRevision | None],
    tmp_path: Path,
    payload: dict[str, Any],
) -> None:
    cache = InferenceCache()
    calls = 0

    def loader(path: Path) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return copy.deepcopy(payload)

    path = tmp_path / "data.json"
    cache.get(path, record_limit=10, loader=loader)
    assert calls == 1

    cache.get(path, record_limit=20, loader=loader)
    assert calls == 2

    cache.get(path, record_limit=10, loader=loader)
    assert calls == 2

    cache.get(path, record_limit=20, loader=loader)
    assert calls == 2


@pytest.mark.parametrize(
    "replace_kwargs",
    [
        {"change_token": 999},
        {"file_identity": (99, 99)},
    ],
)
def test_revision_change_invalidates_cache(
    current_revision: list[_StrongFileRevision | None],
    tmp_path: Path,
    payload: dict[str, Any],
    replace_kwargs: dict[str, Any],
) -> None:
    cache = InferenceCache()
    calls = 0

    def loader(path: Path) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return copy.deepcopy(payload)

    path = tmp_path / "data.json"
    cache.get(path, record_limit=10, loader=loader)
    assert calls == 1

    assert current_revision[0] is not None
    current_revision[0] = dataclasses.replace(current_revision[0], **replace_kwargs)

    cache.get(path, record_limit=10, loader=loader)
    assert calls == 2


def test_fake_revision_none_runs_loader_each_call(
    current_revision: list[_StrongFileRevision | None],
    tmp_path: Path,
    payload: dict[str, Any],
) -> None:
    current_revision[0] = None
    cache = InferenceCache()
    calls = 0

    def loader(path: Path) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return copy.deepcopy(payload)

    path = tmp_path / "data.json"
    cache.get(path, record_limit=10, loader=loader)
    cache.get(path, record_limit=10, loader=loader)

    assert calls == 2
    assert len(cache) == 0


def test_loader_failure_propagates_and_cleans_up_flights(
    current_revision: list[_StrongFileRevision | None],
    tmp_path: Path,
    payload: dict[str, Any],
) -> None:
    cache = InferenceCache()
    calls = 0

    def loader(path: Path) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ValueError("loader failure")
        return copy.deepcopy(payload)

    path = tmp_path / "data.json"
    with pytest.raises(ValueError, match="loader failure"):
        cache.get(path, record_limit=10, loader=loader)

    assert len(cache) == 0
    assert len(cache._flights) == 0

    res = cache.get(path, record_limit=10, loader=loader)
    assert res == payload
    assert calls == 2
    assert len(cache) == 1
    assert len(cache._flights) == 0


@pytest.mark.parametrize(
    "new_revision",
    [
        "new_token",
        None,
    ],
)
def test_loader_changes_revision_mid_scan_raises(
    current_revision: list[_StrongFileRevision | None],
    tmp_path: Path,
    payload: dict[str, Any],
    new_revision: str | None,
) -> None:
    cache = InferenceCache()

    def loader(path: Path) -> dict[str, Any]:
        if new_revision == "new_token":
            assert current_revision[0] is not None
            current_revision[0] = dataclasses.replace(current_revision[0], change_token=999)
        else:
            current_revision[0] = None
        return copy.deepcopy(payload)

    path = tmp_path / "data.json"
    with pytest.raises(ApiInputSchemaError) as exc_info:
        cache.get(path, record_limit=10, loader=loader)

    assert "changed while its schema was inferred" in exc_info.value.message
    assert len(cache) == 0
    assert len(cache._flights) == 0


@pytest.mark.parametrize(
    ("clear_during_load", "fail"), [(False, False), (True, False), (False, True)]
)
def test_deterministic_shared_scan(
    current_revision: list[_StrongFileRevision | None],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    payload: dict[str, Any],
    clear_during_load: bool,
    fail: bool,
) -> None:
    cache = InferenceCache()
    path = tmp_path / "data.json"

    started = threading.Event()
    release = threading.Event()
    second_seen = threading.Event()

    class WaitingFuture(module.Future):
        def result(self, *args: Any, **kwargs: Any) -> Any:
            second_seen.set()
            return super().result(*args, **kwargs)

    monkeypatch.setattr(module, "Future", WaitingFuture)

    calls = 0

    def loader(p: Path) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        started.set()
        if not release.wait(timeout=5):
            raise TimeoutError("release event was not set in time")
        if fail:
            raise ValueError("shared scan failed")
        return copy.deepcopy(payload)

    with ThreadPoolExecutor(max_workers=2) as executor:
        try:
            fut1 = executor.submit(cache.get, path, record_limit=10, loader=loader)
            assert started.wait(timeout=5), "first loader did not start"

            if clear_during_load:
                cache.clear()

            fut2 = executor.submit(cache.get, path, record_limit=10, loader=loader)
            assert second_seen.wait(timeout=5), "second query was not observed"
            assert not fut2.done(), "second future completed prematurely"

            release.set()

            if fail:
                for future in (fut1, fut2):
                    with pytest.raises(ValueError, match="shared scan failed"):
                        future.result(timeout=5)
                assert calls == 1
                assert len(cache) == 0
                assert not cache._flights
                fail = False
                assert cache.get(path, record_limit=10, loader=loader) == payload
                assert calls == 2
                return

            res1 = fut1.result(timeout=5)
            res2 = fut2.result(timeout=5)

            assert calls == 1
            assert res1 == payload
            assert res2 == payload
            assert res1 is not res2
        finally:
            release.set()


def test_waiter_rechecks_revision_after_shared_result(
    current_revision: list[_StrongFileRevision | None],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    payload: dict[str, Any],
) -> None:
    second_seen = threading.Event()

    class MutatingFuture(module.Future):
        def result(self, *args: Any, **kwargs: Any) -> Any:
            second_seen.set()
            res = super().result(*args, **kwargs)
            assert current_revision[0] is not None
            current_revision[0] = dataclasses.replace(
                current_revision[0],
                change_token=current_revision[0].change_token + 999,
            )
            return res

    monkeypatch.setattr(module, "Future", MutatingFuture)

    cache = InferenceCache()
    path = tmp_path / "data.json"

    started = threading.Event()
    release = threading.Event()
    calls = 0

    def loader(p: Path) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        started.set()
        if not release.wait(timeout=5):
            raise TimeoutError("release event was not set in time")
        return copy.deepcopy(payload)

    with ThreadPoolExecutor(max_workers=2) as executor:
        try:
            fut1 = executor.submit(cache.get, path, record_limit=10, loader=loader)
            assert started.wait(timeout=5), "first loader did not start"

            fut2 = executor.submit(cache.get, path, record_limit=10, loader=loader)
            assert second_seen.wait(timeout=5), "second query was not observed"
            assert not fut2.done(), "second future completed prematurely"

            release.set()

            res1 = fut1.result(timeout=5)
            assert res1 == payload

            with pytest.raises(ApiInputSchemaError) as exc_info:
                fut2.result(timeout=5)
            assert "changed while its schema was inferred" in exc_info.value.message

            assert calls == 1
        finally:
            release.set()


@pytest.mark.parametrize("bound_kind", ["entries", "bytes"])
def test_lru_eviction(
    current_revision: list[_StrongFileRevision | None],
    tmp_path: Path,
    payload: dict[str, Any],
    bound_kind: str,
) -> None:
    calls: list[str] = []

    def loader(path: Path) -> dict[str, Any]:
        calls.append(path.name)
        return copy.deepcopy(payload)

    path_a = tmp_path / "a.json"
    path_b = tmp_path / "b.json"
    path_c = tmp_path / "c.json"

    if bound_kind == "entries":
        cache = InferenceCache(max_entries=2)
        cache.get(path_a, record_limit=10, loader=loader)
        cache.get(path_b, record_limit=10, loader=loader)
        cache.get(path_a, record_limit=10, loader=loader)
        cache.get(path_c, record_limit=10, loader=loader)
        cache.get(path_a, record_limit=10, loader=loader)
        assert calls == ["a.json", "b.json", "c.json"]

        cache.get(path_b, record_limit=10, loader=loader)
        assert calls == ["a.json", "b.json", "c.json", "b.json"]
    else:
        payload_bytes = len(orjson.dumps(payload))
        cache = InferenceCache(max_entries=10, max_bytes=2 * payload_bytes)
        cache.get(path_a, record_limit=10, loader=loader)
        cache.get(path_b, record_limit=10, loader=loader)
        cache.get(path_c, record_limit=10, loader=loader)

        assert len(cache) == 2
        assert calls == ["a.json", "b.json", "c.json"]

        cache.get(path_a, record_limit=10, loader=loader)
        assert calls == ["a.json", "b.json", "c.json", "a.json"]


def test_oversized_payload_not_cached(
    current_revision: list[_StrongFileRevision | None],
    tmp_path: Path,
    payload: dict[str, Any],
) -> None:
    payload_bytes = len(orjson.dumps(payload))
    cache = InferenceCache(max_bytes=payload_bytes - 1)
    calls = 0

    def loader(path: Path) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return copy.deepcopy(payload)

    path = tmp_path / "data.json"
    res1 = cache.get(path, record_limit=10, loader=loader)
    res2 = cache.get(path, record_limit=10, loader=loader)

    assert calls == 2
    assert len(cache) == 0
    assert res1 == payload
    assert res2 == payload


@pytest.mark.parametrize("pid_delta", [-1, 1])
def test_fork_reset_seam(
    current_revision: list[_StrongFileRevision | None],
    tmp_path: Path,
    payload: dict[str, Any],
    pid_delta: int,
) -> None:
    cache = InferenceCache()
    calls = 0

    def loader(path: Path) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return copy.deepcopy(payload)

    path = tmp_path / "data.json"
    res1 = cache.get(path, record_limit=10, loader=loader)
    assert calls == 1
    assert res1 == payload
    assert len(cache) == 1

    old_lock = cache._lock
    assert old_lock.acquire(timeout=5), "failed to acquire initial lock"
    executor = ThreadPoolExecutor(max_workers=1)
    try:
        cache._process_id = module.os.getpid() + pid_delta
        fut = executor.submit(cache.get, path, record_limit=10, loader=loader)
        res2 = fut.result(timeout=5)

        assert calls == 2
        assert res2 == payload
        assert cache._lock is not old_lock
    finally:
        old_lock.release()
        executor.shutdown(wait=True)
