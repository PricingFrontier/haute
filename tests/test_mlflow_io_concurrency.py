"""Concurrency contract for MLflow model download/load (remediation 4a.7).

CODE_REVIEW MEDIUM "Scoring/integrations": concurrent ``load_mlflow_model``
calls for the SAME artifact were completely unserialized — every caller
that missed the in-memory cache downloaded the artifact again (thundering
herd) and ``shutil.move``'d over the cache file another thread might be
reading (on Windows the rename falls back to an in-place copy over the
open file).  The corrupt-retry path could likewise ``unlink`` a file
mid-read.

Contract under test (per-key lock, W2.10 concurrency-test doctrine —
deterministic Events/Barriers, bounded waits, no sleeps-as-sync):

* same artifact: exactly one thread downloads / loads; waiters block on
  the per-key lock and then reuse the winner's result (in-memory cache)
  or the on-disk file — never a second transport call;
* different artifacts proceed fully concurrently (per-key, not global);
* a failed download releases the lock so the next caller can retry;
* the artifact file's bytes are never rewritten while a load is in
  flight.

All transports are deterministic fakes (plain objects, gated by
``threading.Event``) — no MagicMock magic on the I/O surface.
"""

from __future__ import annotations

import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import polars as pl  # noqa: F401 — keeps fixture parity with sibling modules
import pytest

from haute._mlflow_io import (
    ScoringModel,
    _model_cache,
    _resolve_artifact_local,
    load_mlflow_model,
)

# Generous upper bounds for "this MUST happen" waits — they only matter on
# a wedged run.  The single short wait used to assert "this must NOT
# happen" is held by the blocked thread for its full duration only on the
# fixed (green) path.
WAIT_MUST_HAPPEN_S = 10.0
WAIT_MUST_NOT_HAPPEN_S = 1.0


@pytest.fixture(autouse=True)
def _clear_cache():
    _model_cache.clear()
    yield
    _model_cache.clear()


class _FakeTransport:
    """Deterministic slow fake of the ``mlflow.artifacts`` surface.

    Each download: records entry (per-call ``Event``), optionally blocks
    on a shared gate or barrier, then writes ``payload`` to the requested
    destination — exactly what ``download_artifacts`` does for a single
    file artifact.
    """

    def __init__(
        self,
        payload: bytes = b"model-bytes-v1",
        artifact_name: str = "model.cbm",
        gate: threading.Event | None = None,
        barrier: threading.Barrier | None = None,
        fail_calls: frozenset[int] = frozenset(),
    ) -> None:
        self.payload = payload
        self.artifact_name = artifact_name
        self.gate = gate
        self.barrier = barrier
        self.fail_calls = fail_calls
        self.calls = 0
        self.entered: list[threading.Event] = [threading.Event() for _ in range(4)]
        self._mutex = threading.Lock()
        self.artifacts = SimpleNamespace(download_artifacts=self._download)

    def _download(self, artifact_uri: str, dst_path: str) -> str:
        with self._mutex:
            call_index = self.calls
            self.calls += 1
        self.entered[call_index].set()
        if self.barrier is not None:
            self.barrier.wait()
        if self.gate is not None and not self.gate.wait(timeout=WAIT_MUST_HAPPEN_S):
            raise AssertionError("transport gate was never released")
        if call_index in self.fail_calls:
            raise RuntimeError("transport exploded")
        out = Path(dst_path) / self.artifact_name
        out.write_bytes(self.payload)
        return str(out)


def _run_threads(workers: dict[str, Any], join_timeout: float = WAIT_MUST_HAPPEN_S):
    """Start one named daemon thread per worker; return (results, errors, threads)."""
    results: dict[str, Any] = {}
    errors: dict[str, BaseException] = {}

    def _wrap(name: str, fn: Any):
        def runner() -> None:
            try:
                results[name] = fn()
            except BaseException as exc:  # noqa: BLE001 — surfaced via assertions
                errors[name] = exc

        return runner

    threads = {
        name: threading.Thread(target=_wrap(name, fn), name=name, daemon=True)
        for name, fn in workers.items()
    }
    return results, errors, threads


class TestDownloadSingleFlight:
    """One downloader per artifact; waiters reuse its file."""

    def test_second_caller_waits_and_never_downloads(self, tmp_path, monkeypatch):
        """RED pre-fix: both threads miss the disk check and both download
        (transport entered twice, second move overwrites the first file).
        Post-fix: the second caller blocks on the per-key lock and returns
        the already-downloaded file without touching the transport.
        """
        monkeypatch.chdir(tmp_path)
        gate = threading.Event()
        transport = _FakeTransport(gate=gate)

        results, errors, threads = _run_threads(
            {
                "t1": lambda: _resolve_artifact_local(transport, "run-x", "model.cbm"),
                "t2": lambda: _resolve_artifact_local(transport, "run-x", "model.cbm"),
            }
        )
        try:
            threads["t1"].start()
            assert transport.entered[0].wait(WAIT_MUST_HAPPEN_S), "first download never began"
            threads["t2"].start()
            assert not transport.entered[1].wait(WAIT_MUST_NOT_HAPPEN_S), (
                "second caller entered the transport while the first download "
                "was still in flight — thundering herd / overwrite hazard"
            )
        finally:
            gate.set()
            for t in threads.values():
                t.join(WAIT_MUST_HAPPEN_S)

        assert not any(t.is_alive() for t in threads.values()), "a caller deadlocked"
        assert errors == {}
        assert transport.calls == 1, "the same artifact was downloaded more than once"
        assert results["t1"] == results["t2"]
        assert Path(results["t1"]).read_bytes() == b"model-bytes-v1"

    def test_distinct_artifacts_download_concurrently(self, tmp_path, monkeypatch):
        """The lock is per-(run, artifact), NOT global: two different
        artifacts must be in flight simultaneously.  A global lock would
        strand one thread at the barrier → BrokenBarrierError → loud fail.
        """
        monkeypatch.chdir(tmp_path)
        barrier = threading.Barrier(2, timeout=WAIT_MUST_HAPPEN_S)
        transport_a = _FakeTransport(payload=b"aa", barrier=barrier)
        transport_b = _FakeTransport(payload=b"bb", barrier=barrier)

        results, errors, threads = _run_threads(
            {
                "a": lambda: _resolve_artifact_local(transport_a, "run-a", "model.cbm"),
                "b": lambda: _resolve_artifact_local(transport_b, "run-b", "model.cbm"),
            }
        )
        for t in threads.values():
            t.start()
        for t in threads.values():
            t.join(WAIT_MUST_HAPPEN_S)

        assert not any(t.is_alive() for t in threads.values())
        assert errors == {}, f"distinct artifacts serialized or failed: {errors}"
        assert Path(results["a"]).read_bytes() == b"aa"
        assert Path(results["b"]).read_bytes() == b"bb"

    def test_failed_download_releases_lock_for_next_caller(self, tmp_path, monkeypatch):
        """A download failure must release the per-key lock: the waiting
        caller retries the download itself and succeeds (no deadlock, no
        poisoned lock).
        """
        monkeypatch.chdir(tmp_path)
        release_first = threading.Event()
        transport = _FakeTransport(gate=release_first, fail_calls=frozenset({0}))

        results, errors, threads = _run_threads(
            {
                "t1": lambda: _resolve_artifact_local(transport, "run-f", "model.cbm"),
                "t2": lambda: _resolve_artifact_local(transport, "run-f", "model.cbm"),
            }
        )
        try:
            threads["t1"].start()
            assert transport.entered[0].wait(WAIT_MUST_HAPPEN_S)
            threads["t2"].start()
        finally:
            release_first.set()
            for t in threads.values():
                t.join(WAIT_MUST_HAPPEN_S)

        assert not any(t.is_alive() for t in threads.values()), "lock leaked after failure"
        assert isinstance(errors.get("t1"), RuntimeError)
        assert "t2" in results, f"second caller failed: {errors.get('t2')!r}"
        assert Path(results["t2"]).read_bytes() == b"model-bytes-v1"
        assert transport.calls == 2


class _StubCatBoost:
    """Plain stub satisfying the surface ``_wrap_catboost`` reads."""

    feature_names_ = ["a"]

    def get_cat_feature_indices(self) -> list[int]:
        return []


class TestLoadModelSingleFlight:
    """``load_mlflow_model`` itself is single-flight per artifact: while one
    thread downloads + loads, same-artifact callers wait and then reuse the
    cached ``ScoringModel`` instance."""

    def test_concurrent_same_model_loads_once_and_shares_instance(self, tmp_path, monkeypatch):
        """RED pre-fix: the second thread misses the (not-yet-populated)
        cache, downloads the artifact AGAIN, overwrites the file the first
        thread is actively loading, and builds a duplicate model.
        """
        monkeypatch.chdir(tmp_path)
        transport = _FakeTransport()

        load_gate = threading.Event()
        load_entered = [threading.Event(), threading.Event()]
        load_calls = []
        load_mutex = threading.Lock()

        def gated_load_catboost(path: str, task: str) -> _StubCatBoost:
            with load_mutex:
                index = len(load_calls)
                load_calls.append(path)
            load_entered[index].set()
            if not load_gate.wait(timeout=WAIT_MUST_HAPPEN_S):
                raise AssertionError("load gate was never released")
            return _StubCatBoost()

        def call() -> ScoringModel:
            return load_mlflow_model(
                source_type="run",
                run_id="run-sf",
                artifact_path="model.cbm",
                task="regression",
            )

        results, errors, threads = _run_threads({"t1": call, "t2": call})
        with (
            patch(
                "haute._mlflow_io.resolve_mlflow_source",
                return_value=("run-sf", "", transport, MagicMock()),
            ),
            patch("haute._mlflow_io._load_catboost_model", side_effect=gated_load_catboost),
        ):
            try:
                threads["t1"].start()
                assert load_entered[0].wait(WAIT_MUST_HAPPEN_S), "first load never began"
                threads["t2"].start()
                assert not load_entered[1].wait(WAIT_MUST_NOT_HAPPEN_S), (
                    "a second model load began while the first was in flight — "
                    "the cached file could be overwritten/unlinked under a reader"
                )
                # The artifact bytes must be untouched while the load is open.
                cached_file = tmp_path / ".cache" / "models" / "run-sf" / "model.cbm"
                assert cached_file.read_bytes() == b"model-bytes-v1"
            finally:
                load_gate.set()
                for t in threads.values():
                    t.join(WAIT_MUST_HAPPEN_S)

        assert not any(t.is_alive() for t in threads.values()), "a caller deadlocked"
        assert errors == {}
        assert transport.calls == 1, "thundering herd: artifact downloaded twice"
        assert len(load_calls) == 1, "model loaded twice for one artifact"
        assert results["t1"] is results["t2"], (
            "waiter built a duplicate ScoringModel instead of reusing the winner's cached instance"
        )

    def test_caller_after_completion_hits_memory_cache(self, tmp_path, monkeypatch):
        """Sequential follow-up callers never re-download or re-load."""
        monkeypatch.chdir(tmp_path)
        transport = _FakeTransport()
        load_calls: list[str] = []

        def counting_load(path: str, task: str) -> _StubCatBoost:
            load_calls.append(path)
            return _StubCatBoost()

        with (
            patch(
                "haute._mlflow_io.resolve_mlflow_source",
                return_value=("run-seq", "", transport, MagicMock()),
            ),
            patch("haute._mlflow_io._load_catboost_model", side_effect=counting_load),
        ):
            first = load_mlflow_model(
                source_type="run",
                run_id="run-seq",
                artifact_path="model.cbm",
                task="regression",
            )
            second = load_mlflow_model(
                source_type="run",
                run_id="run-seq",
                artifact_path="model.cbm",
                task="regression",
            )

        assert first is second
        assert transport.calls == 1
        assert len(load_calls) == 1


class TestLockAcquisitionRaces:
    """Deterministic, single-threaded simulations of the exact interleavings
    the thread tests can only hit probabilistically.

    Each test hooks ``_artifact_io_lock`` so "what a concurrent winner did
    while this caller waited for the lock" happens at the acquisition
    point itself — same code paths, zero timing dependence.
    """

    @staticmethod
    def _hooked_lock(hook):
        """Return a drop-in for ``_artifact_io_lock`` running *hook* once."""
        from haute import _mlflow_io

        real_lock = _mlflow_io._artifact_io_lock
        fired = []

        def locked(run_id: str, artifact_path: str):
            lock = real_lock(run_id, artifact_path)
            if not fired:
                fired.append(True)
                hook()
            return lock

        return locked

    def test_fast_path_waiter_reuses_model_cached_while_waiting(self, tmp_path, monkeypatch):
        """Disk file present, cache empty at entry; the 'winner' populates
        the cache while this caller waits — the waiter must return that
        instance without loading."""
        monkeypatch.chdir(tmp_path)
        cached_file = tmp_path / ".cache" / "models" / "run-h" / "model.cbm"
        cached_file.parent.mkdir(parents=True)
        cached_file.write_bytes(b"bytes")
        winner_model = ScoringModel(_StubCatBoost(), ["a"], frozenset(), "catboost")
        fast_key = ("run", "run-h", "model.cbm", "regression")

        def winner_populates_cache() -> None:
            _model_cache.put(fast_key, winner_model)

        with (
            patch(
                "haute._mlflow_io._artifact_io_lock",
                side_effect=self._hooked_lock(winner_populates_cache),
            ),
            patch("haute._mlflow_io.load_local_model") as load_local,
            patch("haute._mlflow_io.resolve_mlflow_source") as resolve_source,
        ):
            result = load_mlflow_model(
                source_type="run",
                run_id="run-h",
                artifact_path="model.cbm",
                task="regression",
            )

        assert result is winner_model
        load_local.assert_not_called()
        resolve_source.assert_not_called()

    def test_fast_path_falls_through_when_file_vanishes_while_waiting(self, tmp_path, monkeypatch):
        """Disk file present at entry but deleted while waiting (a
        concurrent corrupt-retry): the caller must fall through to the
        full resolve path and re-download instead of failing."""
        monkeypatch.chdir(tmp_path)
        cached_file = tmp_path / ".cache" / "models" / "run-v" / "model.cbm"
        cached_file.parent.mkdir(parents=True)
        cached_file.write_bytes(b"stale")
        transport = _FakeTransport()

        def retry_deleted_the_file() -> None:
            cached_file.unlink(missing_ok=True)

        with (
            patch(
                "haute._mlflow_io._artifact_io_lock",
                side_effect=self._hooked_lock(retry_deleted_the_file),
            ),
            patch(
                "haute._mlflow_io.resolve_mlflow_source",
                return_value=("run-v", "", transport, MagicMock()),
            ),
            patch(
                "haute._mlflow_io._load_catboost_model",
                side_effect=lambda path, task: _StubCatBoost(),
            ),
        ):
            result = load_mlflow_model(
                source_type="run",
                run_id="run-v",
                artifact_path="model.cbm",
                task="regression",
            )

        assert isinstance(result, ScoringModel)
        assert transport.calls == 1, "vanished file must trigger exactly one re-download"
        assert cached_file.read_bytes() == b"model-bytes-v1"

    def test_resolve_path_waiter_reuses_model_cached_while_waiting(self, tmp_path, monkeypatch):
        """No disk file (resolve path); the 'winner' populates the cache
        while this caller waits — no download, no load."""
        monkeypatch.chdir(tmp_path)
        winner_model = ScoringModel(_StubCatBoost(), ["a"], frozenset(), "catboost")
        cache_key = ("run", "run-r", "model.cbm", "regression")
        transport = _FakeTransport()

        def winner_populates_cache() -> None:
            _model_cache.put(cache_key, winner_model)

        with (
            patch(
                "haute._mlflow_io._artifact_io_lock",
                side_effect=self._hooked_lock(winner_populates_cache),
            ),
            patch(
                "haute._mlflow_io.resolve_mlflow_source",
                return_value=("run-r", "", transport, MagicMock()),
            ),
            patch("haute._mlflow_io._load_catboost_model") as load_catboost,
        ):
            result = load_mlflow_model(
                source_type="run",
                run_id="run-r",
                artifact_path="model.cbm",
                task="regression",
            )

        assert result is winner_model
        assert transport.calls == 0, "cache hit under the lock must skip the download"
        load_catboost.assert_not_called()
