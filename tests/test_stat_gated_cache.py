"""Regression contracts for the bounded stat-gated artifact cache."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event, Lock

import pytest

from haute._json_shred import _source_proof
from haute._json_shred._source_proof import SourceChangedError
from haute._stat_gated_cache import StatGatedCache


def _artifact(tmp_path: Path, name: str) -> Path:
    path = tmp_path / name
    path.write_text(name)
    return path


def test_constructor_requires_a_positive_non_boolean_max_entries() -> None:
    cache = StatGatedCache[str, str](artifact_kind="test", max_entries=1)

    assert len(cache) == 0
    for invalid_max_entries in (0, -1, True):
        with pytest.raises((TypeError, ValueError)):
            StatGatedCache[str, str](artifact_kind="test", max_entries=invalid_max_entries)


def test_lru_eviction_promotes_hits_and_never_exceeds_capacity(tmp_path: Path) -> None:
    cache = StatGatedCache[str, str](artifact_kind="test", max_entries=2)
    paths = {name: _artifact(tmp_path, name) for name in ("a", "b", "c")}
    calls: list[str] = []

    def load(name: str) -> str:
        calls.append(name)
        return f"loaded-{name}-{len(calls)}"

    assert cache.get_or_load("a", str(paths["a"]), lambda: load("a")) == "loaded-a-1"
    assert cache.get_or_load("b", str(paths["b"]), lambda: load("b")) == "loaded-b-2"
    assert len(cache) == 2
    assert cache.get_or_load("a", str(paths["a"]), lambda: load("a")) == "loaded-a-1"
    assert cache.get_or_load("c", str(paths["c"]), lambda: load("c")) == "loaded-c-3"
    assert len(cache) == 2
    assert cache.get_or_load("b", str(paths["b"]), lambda: load("b")) == "loaded-b-4"

    assert calls == ["a", "b", "c", "b"]
    assert len(cache) == 2


def test_idle_per_key_load_gates_are_bounded_with_entries(tmp_path: Path) -> None:
    cache = StatGatedCache[str, str](artifact_kind="test", max_entries=2)

    for name in ("a", "b", "c", "d"):
        path = _artifact(tmp_path, name)
        assert cache.get_or_load(name, str(path), lambda name=name: name) == name
        assert len(cache) <= 2
        assert len(cache._load_locks) <= 2

    assert len(cache._load_locks) <= 2


def test_concurrent_same_key_misses_call_loader_once(tmp_path: Path) -> None:
    path = _artifact(tmp_path, "artifact")
    cache = StatGatedCache[str, object](artifact_kind="test", max_entries=2)
    start = Event()
    loading = Event()
    release = Event()
    counter_lock = Lock()
    calls = 0
    loaded = object()

    def loader() -> object:
        nonlocal calls
        with counter_lock:
            calls += 1
        loading.set()
        assert release.wait(timeout=2)
        return loaded

    def get_value() -> object:
        assert start.wait(timeout=2)
        return cache.get_or_load("key", str(path), loader)

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(get_value) for _ in range(8)]
        start.set()
        assert loading.wait(timeout=2)
        release.set()
        values = [future.result(timeout=2) for future in futures]

    assert calls == 1
    assert all(value is loaded for value in values)


def test_a_caller_queued_across_a_change_reuses_the_next_load(tmp_path: Path) -> None:
    """A waiter observes the file again once it holds the gate.

    The first load reads the old file, which changes under it; the value loaded
    for the new file is then shared, so the change costs one more load, not one
    per queued caller.
    """
    path = _artifact(tmp_path, "artifact")
    cache = StatGatedCache[str, str](artifact_kind="test", max_entries=2)
    loading = Event()
    release = Event()
    counter_lock = Lock()
    loads: list[str] = []

    def loader() -> str:
        with counter_lock:
            loads.append(path.read_text())
            first = len(loads) == 1
        if first:
            loading.set()
            assert release.wait(timeout=5)
        return path.read_text()

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(cache.get_or_load, "key", str(path), loader)
        assert loading.wait(timeout=5)
        queued = executor.submit(cache.get_or_load, "key", str(path), loader)
        deadline = time.monotonic() + 5
        while cache._load_locks["key"].participants < 2:
            assert time.monotonic() < deadline, "the second caller never queued"
            time.sleep(0.005)
        (tmp_path / "artifact").write_text("changed while loading")
        release.set()
        values = [first.result(timeout=5), queued.result(timeout=5)]

    assert values == ["changed while loading", "changed while loading"]
    assert loads == ["artifact", "changed while loading"]


def test_clear_during_active_load_preserves_the_single_flight_gate(tmp_path: Path) -> None:
    path = _artifact(tmp_path, "artifact")
    cache = StatGatedCache[str, object](artifact_kind="test", max_entries=2)
    loading = Event()
    duplicate_load = Event()
    release = Event()
    counter_lock = Lock()
    calls = 0
    loaded = object()

    def loader() -> object:
        nonlocal calls
        with counter_lock:
            calls += 1
            if calls > 1:
                duplicate_load.set()
        loading.set()
        assert release.wait(timeout=2)
        return loaded

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(cache.get_or_load, "key", str(path), loader)
        assert loading.wait(timeout=2)
        cache.clear()
        second = executor.submit(cache.get_or_load, "key", str(path), loader)
        duplicated_while_first_was_active = duplicate_load.wait(timeout=0.5)
        release.set()
        values = [first.result(timeout=2), second.result(timeout=2)]

    assert not duplicated_while_first_was_active
    assert calls == 1
    assert all(value is loaded for value in values)


def test_a_young_file_without_a_native_revision_is_loaded_every_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_source_proof, "_strong_file_revision", lambda _path: None)
    path = _artifact(tmp_path, "artifact")
    cache = StatGatedCache[str, int](artifact_kind="test", max_entries=2)
    calls: list[int] = []

    def loader() -> int:
        calls.append(len(calls))
        return len(calls)

    assert cache.get_or_load("key", str(path), loader) == 1
    assert cache.get_or_load("key", str(path), loader) == 2
    assert len(cache) == 0


def test_a_write_reloads_and_a_finished_load_leaves_no_gate(tmp_path: Path) -> None:
    path = _artifact(tmp_path, "artifact")
    cache = StatGatedCache[str, str](artifact_kind="test", max_entries=2)

    assert cache.get_or_load("key", str(path), lambda: path.read_text()) == "artifact"
    (tmp_path / "artifact").write_text("rewritten")
    assert cache.get_or_load("key", str(path), lambda: path.read_text()) == "rewritten"
    assert cache.get_or_load("key", str(path), lambda: "unused") == "rewritten"
    assert cache._load_locks == {}


def test_a_forked_child_starts_with_an_empty_cache(tmp_path: Path) -> None:
    path = _artifact(tmp_path, "artifact")
    cache = StatGatedCache[str, str](artifact_kind="test", max_entries=2)
    cache.get_or_load("key", str(path), lambda: "parent")
    parent_lock = cache._lock

    cache._process_id = -1  # as a forked child observes it
    assert cache.get_or_load("key", str(path), lambda: "child") == "child"
    assert cache._lock is not parent_lock


def test_a_file_changing_on_every_load_is_refused(tmp_path: Path) -> None:
    path = _artifact(tmp_path, "artifact")
    cache = StatGatedCache[str, int](artifact_kind="Artifact", max_entries=2)
    loads: list[int] = []

    def rewriting_loader() -> int:
        loads.append(len(loads))
        (tmp_path / "artifact").write_text("x" * (len(loads) + 10))
        return len(loads)

    with pytest.raises(SourceChangedError, match="Artifact changed on disk while loading"):
        cache.get_or_load("key", str(path), rewriting_loader)
    assert len(loads) == 2
    assert len(cache) == 0


def test_loader_exception_is_not_cached(tmp_path: Path) -> None:
    path = _artifact(tmp_path, "artifact")
    cache = StatGatedCache[str, str](artifact_kind="test", max_entries=2)
    attempts = 0

    def loader() -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("temporary read failure")
        return "recovered"

    with pytest.raises(OSError, match="temporary read failure"):
        cache.get_or_load("key", str(path), loader)

    assert cache.get_or_load("key", str(path), loader) == "recovered"
    assert attempts == 2
