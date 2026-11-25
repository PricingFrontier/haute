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

Every load resolves its reference through the store first: here a fake
tracking client (``tests._mlflow_fakes.FakeArtifactClient``) lists each run's
files with their sizes, so each copy is kept under the identity of the bytes
its transport serves. All transports are deterministic fakes (plain objects,
gated by ``threading.Event``) — no MagicMock magic on the I/O surface.
"""

from __future__ import annotations

import os
import shutil
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
import structlog

from haute._mlflow_io import (
    ScoringModel,
    _active_disk_cache_runs,
    _artifact_cache_path,
    _evict_disk_cache,
    _model_cache,
    _model_cache_key,
    _resolution_record_path,
    _resolve_artifact_local,
    disk_cache_only_model_loads,
    load_mlflow_model,
)
from haute._mlflow_model import StoredArtifact, resolve_model
from haute._mlflow_utils import ResolvedBackend, resolve_backend
from haute._model_source import RegisteredModelSource, RunModelSource
from haute._project_context import context_thread
from tests._mlflow_fakes import FakeArtifactClient
from tests._project import use_project

# Generous upper bounds for "this MUST happen" waits — they only matter on
# a wedged run.  The single short wait used to assert "this must NOT
# happen" is held by the blocked thread for its full duration only on the
# fixed (green) path.
WAIT_MUST_HAPPEN_S = 10.0
WAIT_MUST_NOT_HAPPEN_S = 1.0


def _local_backend() -> ResolvedBackend:
    """The local-folder backend every load in this module shares.

    Loads here exercise concurrency, not destination choice, so they all run
    without a named destination; the digest and identity are what partition the
    disk cache, the memory cache, and the artifact I/O locks.
    """
    return resolve_backend("")


@pytest.fixture(autouse=True)
def _clear_cache():
    _model_cache.clear()
    yield
    _model_cache.clear()


@pytest.fixture()
def store(monkeypatch: pytest.MonkeyPatch) -> FakeArtifactClient:
    """The tracking store every load lists: the runs a test puts in ``store.runs``."""
    client = FakeArtifactClient({})
    monkeypatch.setattr("haute._mlflow_utils.pinned_client", lambda backend: client)
    return client


def _stored(path: str, payload: bytes) -> StoredArtifact:
    """*path* as a store that lists *payload* there describes it."""
    return StoredArtifact(path, f"size:{len(payload)}")


def _copy(cache_root: Path, run_id: str, path: str, payload: bytes) -> Path:
    """Where the disk cache keeps *payload*, the bytes the store lists at *path*."""
    return _artifact_cache_path(
        cache_root, _local_backend().digest, run_id, path, _stored(path, payload).token
    )


def _run(run_id: str, artifact_path: str) -> RunModelSource:
    return RunModelSource(run_id=run_id, artifact_path=artifact_path, mlflow_destination="")


@contextmanager
def _serving(transport: Any) -> Iterator[None]:
    """Make *transport* the download every load in the block makes."""
    with patch("mlflow.artifacts.download_artifacts", new=transport.artifacts.download_artifacts):
        yield


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

    def _download(
        self,
        artifact_uri: str,
        dst_path: str,
        *,
        tracking_uri: str | None = None,
    ) -> str:
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


class _MappingTransport:
    """Transport fake that returns bytes based on the requested artifact URI."""

    def __init__(self, payloads: dict[str, bytes]) -> None:
        self.payloads = payloads
        self.calls: list[str] = []
        self.artifacts = SimpleNamespace(download_artifacts=self._download)

    def _download(
        self,
        artifact_uri: str,
        dst_path: str,
        *,
        tracking_uri: str | None = None,
    ) -> str:
        self.calls.append(artifact_uri)
        artifact_path = artifact_uri.rsplit("/", maxsplit=1)[-1]
        if artifact_uri not in self.payloads:
            # runs:/<run>/<dir>/<file> loses the directory in rsplit; use
            # the suffix match for same-basename fixture paths.
            matches = [payload for key, payload in self.payloads.items() if key in artifact_uri]
            if len(matches) != 1:
                raise AssertionError(f"unexpected artifact URI: {artifact_uri}")
            payload = matches[0]
        else:
            payload = self.payloads[artifact_uri]
        out = Path(dst_path) / Path(artifact_path).name
        out.write_bytes(payload)
        return str(out)


def _no_download(artifact_uri: str, *_args: Any, **_kwargs: Any) -> str:
    raise AssertionError(f"downloaded {artifact_uri} although its copy was on disk")


def _run_threads(workers: dict[str, Any], join_timeout: float = WAIT_MUST_HAPPEN_S):
    """Start one named daemon thread per worker; return (results, errors, threads)."""
    results: dict[str, Any] = {}
    errors: dict[str, BaseException] = {}

    def _wrap(name: str, fn: Any):
        def runner() -> None:
            try:
                results[name] = fn()
            except BaseException as exc:  # surfaced via assertions
                errors[name] = exc

        return runner

    threads = {
        name: context_thread(_wrap(name, fn), name=name, daemon=True)
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
        use_project(tmp_path)
        gate = threading.Event()
        transport = _FakeTransport(gate=gate)
        stored = _stored("model.cbm", transport.payload)

        results, errors, threads = _run_threads(
            {
                "t1": lambda: _resolve_artifact_local(transport, _local_backend(), "run-x", stored),
                "t2": lambda: _resolve_artifact_local(transport, _local_backend(), "run-x", stored),
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
        use_project(tmp_path)
        barrier = threading.Barrier(2, timeout=WAIT_MUST_HAPPEN_S)
        transport_a = _FakeTransport(payload=b"aa", barrier=barrier)
        transport_b = _FakeTransport(payload=b"bb", barrier=barrier)

        results, errors, threads = _run_threads(
            {
                "a": lambda: _resolve_artifact_local(
                    transport_a, _local_backend(), "run-a", _stored("model.cbm", b"aa")
                ),
                "b": lambda: _resolve_artifact_local(
                    transport_b, _local_backend(), "run-b", _stored("model.cbm", b"bb")
                ),
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
        use_project(tmp_path)
        release_first = threading.Event()
        transport = _FakeTransport(gate=release_first, fail_calls=frozenset({0}))
        stored = _stored("model.cbm", transport.payload)

        results, errors, threads = _run_threads(
            {
                "t1": lambda: _resolve_artifact_local(transport, _local_backend(), "run-f", stored),
                "t2": lambda: _resolve_artifact_local(transport, _local_backend(), "run-f", stored),
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

    def get_metadata(self) -> dict[str, str]:
        return {}


class TestLoadModelSingleFlight:
    """``load_mlflow_model`` itself is single-flight per artifact: while one
    thread downloads + loads, same-artifact callers wait and then reuse the
    cached ``ScoringModel`` instance."""

    def test_concurrent_same_model_loads_once_and_shares_instance(
        self, tmp_path, monkeypatch, store
    ):
        """RED pre-fix: the second thread misses the (not-yet-populated)
        cache, downloads the artifact AGAIN, overwrites the file the first
        thread is actively loading, and builds a duplicate model.
        """
        use_project(tmp_path)
        transport = _FakeTransport()
        store.runs["run-sf"] = {"model.cbm": len(transport.payload)}

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
            return load_mlflow_model(_run("run-sf", "model.cbm"), task="regression")

        results, errors, threads = _run_threads({"t1": call, "t2": call})
        with (
            _serving(transport),
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
                cached_file = _copy(
                    tmp_path / ".cache" / "models", "run-sf", "model.cbm", transport.payload
                )
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

    def test_caller_after_completion_hits_memory_cache(self, tmp_path, monkeypatch, store):
        """Sequential follow-up callers never re-download or re-load."""
        use_project(tmp_path)
        transport = _FakeTransport()
        store.runs["run-seq"] = {"model.cbm": len(transport.payload)}
        load_calls: list[str] = []

        def counting_load(path: str, task: str) -> _StubCatBoost:
            load_calls.append(path)
            return _StubCatBoost()

        with (
            _serving(transport),
            patch("haute._mlflow_io._load_catboost_model", side_effect=counting_load),
        ):
            first = load_mlflow_model(_run("run-seq", "model.cbm"), task="regression")
            second = load_mlflow_model(_run("run-seq", "model.cbm"), task="regression")

        assert first is second
        assert transport.calls == 1
        assert len(load_calls) == 1


def _seed_artifact(path: Path, payload: bytes) -> None:
    """Write one cached artifact at a path the caller derived from its sandbox."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


class _FakeRegistry:
    """The store's client with registered models, each a version registered from a run file."""

    def __init__(self, client: FakeArtifactClient, models: dict[str, tuple[str, str]]) -> None:
        self.client = client
        self.models = models

    def __getattr__(self, name: str) -> Any:
        return getattr(self.client, name)

    def get_model_version(self, name: str, version: str) -> Any:
        run_id, source = self.models[name]
        return SimpleNamespace(run_id=run_id, source=source)


class TestArtifactDiskIdentity:
    """Disk cache identity is the full MLflow artifact path, not basename."""

    @staticmethod
    def _bytes_backed_catboost(path: str, task: str) -> _StubCatBoost:
        model = _StubCatBoost()
        model.feature_names_ = [Path(path).read_bytes().decode("utf-8")]
        return model

    def test_same_run_same_basename_artifacts_store_distinct_bytes_and_models(
        self,
        tmp_path,
        monkeypatch,
        store,
    ):
        """``freq/model.cbm`` and ``sev/model.cbm`` must not share disk bytes.

        RED pre-fix: the first load caches ``.cache/models/run/model.cbm``;
        the second artifact sees that basename hit and loads the wrong
        model without ever downloading its own bytes.
        """
        use_project(tmp_path)
        payloads = {"freq/model.cbm": b"freq-bytes", "sev/model.cbm": b"sev-bytes"}
        store.runs["run-shared"] = {path: len(payload) for path, payload in payloads.items()}
        transport = _MappingTransport(payloads)

        with (
            _serving(transport),
            patch(
                "haute._mlflow_io._load_catboost_model",
                side_effect=self._bytes_backed_catboost,
            ),
        ):
            freq = load_mlflow_model(_run("run-shared", "freq/model.cbm"), task="regression")
            sev = load_mlflow_model(_run("run-shared", "sev/model.cbm"), task="regression")

        cache_root = tmp_path / ".cache" / "models"
        freq_path = _copy(cache_root, "run-shared", "freq/model.cbm", b"freq-bytes")
        sev_path = _copy(cache_root, "run-shared", "sev/model.cbm", b"sev-bytes")
        assert freq_path != sev_path
        assert freq_path.read_bytes() == b"freq-bytes"
        assert sev_path.read_bytes() == b"sev-bytes"
        assert freq.feature_names == ["freq-bytes"]
        assert sev.feature_names == ["sev-bytes"]
        assert transport.calls == [
            "runs:/run-shared/freq/model.cbm",
            "runs:/run-shared/sev/model.cbm",
        ]

    def test_artifact_cache_path_uses_full_identity_and_stays_under_cache_root(
        self,
        tmp_path,
    ):
        cache_root = tmp_path / ".cache" / "models"
        digest = _local_backend().digest
        token = _stored("model.cbm", b"bytes").token
        freq_path = _artifact_cache_path(cache_root, digest, "run-x", "freq/model.cbm", token)
        sev_path = _artifact_cache_path(cache_root, digest, "run-x", "sev/model.cbm", token)

        assert freq_path != sev_path
        assert freq_path.name.endswith(".cbm")
        assert sev_path.name.endswith(".cbm")
        assert freq_path.is_relative_to(cache_root)
        assert sev_path.is_relative_to(cache_root)
        # Two identities of one path are two copies, side by side.
        relogged = _artifact_cache_path(
            cache_root,
            digest,
            "run-x",
            "freq/model.cbm",
            _stored("model.cbm", b"relogged-bytes").token,
        )
        assert relogged != freq_path
        assert relogged.parent.parent == freq_path.parent.parent

        for bad_artifact in ("../model.cbm", "freq/../../model.cbm", "/tmp/model.cbm"):
            with pytest.raises(ValueError, match="Invalid artifact_path"):
                _artifact_cache_path(cache_root, digest, "run-x", bad_artifact, token)
        for alias_artifact in (
            "./model.cbm",
            "nested/./model.cbm",
            "nested//model.cbm",
        ):
            with pytest.raises(ValueError, match="Invalid artifact_path"):
                _artifact_cache_path(cache_root, digest, "run-x", alias_artifact, token)

        with pytest.raises(ValueError, match="Invalid run_id"):
            _artifact_cache_path(cache_root, digest, "../outside", "model.cbm", token)
        for bad_token in ("..", "", token.upper(), token + "0"):
            with pytest.raises(ValueError, match="Invalid artifact identity token"):
                _artifact_cache_path(cache_root, digest, "run-x", "model.cbm", bad_token)

    def test_registered_same_version_same_basename_artifacts_store_distinct_models(
        self,
        tmp_path,
        monkeypatch,
        store,
    ):
        """Registered-model cache keys must include full artifact identity.

        Version 7 of two registered models, registered from same-basename files
        of one run, are two models.
        """
        use_project(tmp_path)
        payloads = {"freq/model.cbm": b"registered-freq", "sev/model.cbm": b"registered-sev"}
        store.runs["run-registered"] = {path: len(payload) for path, payload in payloads.items()}
        registry = _FakeRegistry(
            store,
            {
                "frequency": ("run-registered", "runs:/run-registered/freq/model.cbm"),
                "severity": ("run-registered", "runs:/run-registered/sev/model.cbm"),
            },
        )
        monkeypatch.setattr("haute._mlflow_utils.pinned_client", lambda backend: registry)
        transport = _MappingTransport(payloads)

        def registered(name: str) -> RegisteredModelSource:
            return RegisteredModelSource(
                registered_model=name, version="7", alias="", mlflow_destination=""
            )

        with (
            _serving(transport),
            patch(
                "haute._mlflow_io._load_catboost_model",
                side_effect=self._bytes_backed_catboost,
            ),
        ):
            freq = load_mlflow_model(registered("frequency"), task="regression")
            sev = load_mlflow_model(registered("severity"), task="regression")

        assert freq.feature_names == ["registered-freq"]
        assert sev.feature_names == ["registered-sev"]
        assert transport.calls == [
            "runs:/run-registered/freq/model.cbm",
            "runs:/run-registered/sev/model.cbm",
        ]

    def test_restart_load_ignores_poisoned_same_basename_cache_entry(
        self,
        tmp_path,
        monkeypatch,
        store,
    ):
        """A cold process must not serve ``freq/model.cbm`` for ``sev/model.cbm``.

        The two files have the same size, so their copies share an identity
        token and are told apart only by their full paths.
        """
        use_project(tmp_path)
        cache_root = tmp_path / ".cache" / "models"
        poisoned = _copy(cache_root, "run-restart", "freq/model.cbm", b"freq-poison")
        poisoned.parent.mkdir(parents=True)
        poisoned.write_bytes(b"freq-poison")
        store.runs["run-restart"] = {"freq/model.cbm": 11, "sev/model.cbm": len(b"sev-freshed")}
        transport = _MappingTransport({"sev/model.cbm": b"sev-freshed"})

        with (
            _serving(transport),
            patch(
                "haute._mlflow_io._load_catboost_model",
                side_effect=self._bytes_backed_catboost,
            ),
        ):
            result = load_mlflow_model(_run("run-restart", "sev/model.cbm"), task="regression")

        sev_path = _copy(cache_root, "run-restart", "sev/model.cbm", b"sev-freshed")
        assert sev_path.parent.name == poisoned.parent.name, "the test needs one identity token"
        assert result.feature_names == ["sev-freshed"]
        assert poisoned.read_bytes() == b"freq-poison"
        assert sev_path.read_bytes() == b"sev-freshed"
        assert transport.calls == ["runs:/run-restart/sev/model.cbm"]

    def test_eviction_skips_run_directory_with_model_load_in_flight(
        self,
        tmp_path,
        monkeypatch,
        store,
    ):
        """Eviction must not delete files another thread is actively loading."""
        use_project(tmp_path)
        cache_root = tmp_path / ".cache" / "models"
        active_path = _copy(cache_root, "run-active", "freq/model.cbm", b"active")
        active_path.parent.mkdir(parents=True)
        active_path.write_bytes(b"active")
        inactive_path = _copy(cache_root, "run-inactive", "model.cbm", b"inactive")
        inactive_path.parent.mkdir(parents=True)
        inactive_path.write_bytes(b"inactive")
        store.runs["run-active"] = {"freq/model.cbm": len(b"active")}

        active_dir = cache_root / _local_backend().digest / "run-active"
        inactive_dir = cache_root / _local_backend().digest / "run-inactive"
        old = 1_700_000_000
        fresh = old + 100
        active_dir.touch()
        inactive_dir.touch()

        os.utime(active_dir, (old, old))
        os.utime(inactive_dir, (fresh, fresh))

        entered_load = threading.Event()
        release_load = threading.Event()

        def gated_load(path: str, task: str) -> _StubCatBoost:
            assert Path(path) == active_path
            entered_load.set()
            if not release_load.wait(timeout=WAIT_MUST_HAPPEN_S):
                raise AssertionError("load gate was never released")
            return _StubCatBoost()

        results, errors, threads = _run_threads(
            {
                "loader": lambda: load_mlflow_model(
                    _run("run-active", "freq/model.cbm"), task="regression"
                )
            }
        )
        transport = _FakeTransport(payload=b"new", artifact_name="model.cbm")

        with (
            patch("haute._mlflow_io._DISK_CACHE_MAX_DIRS", 1),
            patch("haute._mlflow_io._load_catboost_model", side_effect=gated_load),
            patch("mlflow.artifacts.download_artifacts", side_effect=_no_download),
        ):
            threads["loader"].start()
            assert entered_load.wait(WAIT_MUST_HAPPEN_S), "disk-cache load never began"
            resolved = _resolve_artifact_local(
                transport, _local_backend(), "run-new", _stored("model.cbm", b"new")
            )
            assert Path(resolved).read_bytes() == b"new"
            assert active_path.is_file(), "eviction deleted a file being loaded"
            release_load.set()
            threads["loader"].join(WAIT_MUST_HAPPEN_S)

        assert errors == {}
        assert "loader" in results
        assert active_path.is_file()
        # The loader asked the store once, then loaded the copy already on disk.
        assert store.listings == [("run-active", "freq")]

    def test_direct_resolve_marks_run_active_during_cache_hit_and_eviction(
        self,
        tmp_path,
        monkeypatch,
    ):
        """Direct callers of _resolve_artifact_local must be eviction-safe."""
        from haute import _mlflow_io

        use_project(tmp_path)
        cache_root = tmp_path / ".cache" / "models"
        active_path = _copy(cache_root, "run-active", "model.cbm", b"active")
        active_path.parent.mkdir(parents=True)
        active_path.write_bytes(b"active")
        inactive_path = _copy(cache_root, "run-inactive", "model.cbm", b"inactive")
        inactive_path.parent.mkdir(parents=True)
        inactive_path.write_bytes(b"inactive")

        active_seen: list[frozenset[str]] = []
        real_evict = _mlflow_io._evict_disk_cache

        def observing_evict(cache_root_arg: Path) -> None:
            active_seen.append(_mlflow_io._active_disk_cache_runs())
            real_evict(cache_root_arg)

        with (
            patch("haute._mlflow_io._DISK_CACHE_MAX_DIRS", 1),
            patch("haute._mlflow_io._evict_disk_cache", side_effect=observing_evict),
        ):
            resolved = _resolve_artifact_local(
                _FakeTransport(payload=b"new", artifact_name="model.cbm"),
                _local_backend(),
                "run-new",
                _stored("model.cbm", b"new"),
            )

        assert Path(resolved).read_bytes() == b"new"
        assert active_seen == [frozenset({"run-new"})]
        assert (cache_root / _local_backend().digest / "run-new").is_dir()

    def test_eviction_rechecks_active_runs_before_deleting_candidate(
        self,
        tmp_path,
        monkeypatch,
    ):
        """A run becoming active after the eviction snapshot must survive."""
        from haute import _mlflow_io

        use_project(tmp_path)
        cache_root = tmp_path / ".cache" / "models"
        race_path = _copy(cache_root, "run-race", "model.cbm", b"race")
        race_path.parent.mkdir(parents=True)
        race_path.write_bytes(b"race")
        keep_path = _copy(cache_root, "run-keep", "model.cbm", b"keep")
        keep_path.parent.mkdir(parents=True)
        keep_path.write_bytes(b"keep")

        old = 1_700_000_000
        fresh = old + 100
        os.utime(cache_root / _local_backend().digest / "run-race", (old, old))
        os.utime(cache_root / _local_backend().digest / "run-keep", (fresh, fresh))
        entered = threading.Event()
        release = threading.Event()
        thread: threading.Thread | None = None

        def active_runs() -> frozenset[str]:
            nonlocal thread
            if thread is None:

                def hold_run() -> None:
                    with _mlflow_io._disk_cache_run_in_use("run-race"):
                        entered.set()
                        if not release.wait(timeout=WAIT_MUST_HAPPEN_S):
                            raise AssertionError("active run holder was never released")

                thread = context_thread(hold_run, daemon=True)
                thread.start()
                assert entered.wait(WAIT_MUST_HAPPEN_S), "active run was never marked"
                return frozenset()
            return _mlflow_io._active_disk_cache_runs()

        try:
            with (
                patch("haute._mlflow_io._DISK_CACHE_MAX_DIRS", 1),
                patch("haute._mlflow_io._active_disk_cache_runs", side_effect=active_runs),
            ):
                _evict_disk_cache(cache_root)
        finally:
            release.set()
            if thread is not None:
                thread.join(WAIT_MUST_HAPPEN_S)

        assert race_path.is_file(), "eviction deleted a run that became active"

    def test_eviction_tombstones_before_delete_without_blocking_new_run_users(
        self,
        tmp_path,
        monkeypatch,
    ):
        """Rename is atomic with the active check; slow deletion is lock-free."""
        from haute import _mlflow_io

        use_project(tmp_path)
        cache_root = tmp_path / ".cache" / "models"
        race_path = _copy(cache_root, "run-race", "model.cbm", b"race")
        race_path.parent.mkdir(parents=True)
        race_path.write_bytes(b"race")
        keep_path = _copy(cache_root, "run-keep", "model.cbm", b"keep")
        keep_path.parent.mkdir(parents=True)
        keep_path.write_bytes(b"keep")
        old = 1_700_000_000
        fresh = old + 100
        os.utime(cache_root / _local_backend().digest / "run-race", (old, old))
        os.utime(cache_root / _local_backend().digest / "run-keep", (fresh, fresh))
        entered = threading.Event()
        observed_paths: list[Path] = []
        removal_errors: list[OSError] = []
        real_rmtree = shutil.rmtree

        def user_enters_run() -> None:
            with _mlflow_io._disk_cache_run_in_use("run-race"):
                assert not (cache_root / _local_backend().digest / "run-race").exists()
                entered.set()

        def observing_rmtree(path: Path, ignore_errors: bool = False) -> None:
            tombstone = Path(path)
            observed_paths.append(tombstone)
            # Short name: a tombstone must never lengthen the path of the
            # artifact underneath it (Windows cannot open past 260 characters).
            assert tombstone.name.startswith(".evicting-")
            assert len(tombstone.name) <= len(".evicting-") + 8
            thread = context_thread(user_enters_run, daemon=True)
            thread.start()
            assert entered.wait(WAIT_MUST_HAPPEN_S), (
                "slow tombstone deletion still held the global active-runs guard"
            )
            try:
                real_rmtree(tombstone, ignore_errors=ignore_errors)
            except OSError as exc:
                # Recorded so a tombstone that survives says why, instead of
                # failing with a bare "it still exists".
                removal_errors.append(exc)
                raise
            finally:
                thread.join(WAIT_MUST_HAPPEN_S)

        with (
            patch("haute._mlflow_io._DISK_CACHE_MAX_DIRS", 1),
            patch("shutil.rmtree", side_effect=observing_rmtree),
        ):
            _evict_disk_cache(cache_root)

        assert entered.is_set()
        # One tombstone, removed — possibly after a retry, which the shared
        # removal makes when Windows refuses a delete while a handle is held.
        assert len(set(observed_paths)) == 1
        assert not observed_paths[0].exists(), (
            f"tombstone survived its deletion: {[repr(exc) for exc in removal_errors]}"
        )

    def test_a_tombstone_that_cannot_be_deleted_is_reported_not_forgotten(
        self,
        tmp_path,
        monkeypatch,
    ):
        """Eviction is housekeeping, but a survivor holds disk until it is seen.

        Windows reports a path it cannot open — one past its 260-character
        limit, which is how long-named tombstones used to fail — as a missing
        path from inside the walk, so the removal must not read that as success.
        """
        use_project(tmp_path)
        cache_root = tmp_path / ".cache" / "models"
        for run in ("run-old", "run-new"):
            _seed_artifact(_copy(cache_root, run, "model.cbm", run.encode()), run.encode())
        os.utime(cache_root / _local_backend().digest / "run-old", (1_700_000_000, 1_700_000_000))

        def rmtree_descendant_missing(_path, *_args, **_kwargs):
            raise FileNotFoundError(2, "The system cannot find the path specified")

        with (
            patch("haute._mlflow_io._DISK_CACHE_MAX_DIRS", 1),
            patch("shutil.rmtree", side_effect=rmtree_descendant_missing),
            structlog.testing.capture_logs() as logs,
        ):
            _evict_disk_cache(cache_root)

        tombstones = list((cache_root / _local_backend().digest).glob(".evicting-*"))
        assert len(tombstones) == 1
        assert any(
            record.get("event") == "mlflow_disk_cache_tombstone_delete_failed"
            and record.get("tombstone") == str(tombstones[0])
            for record in logs
        ), logs

    def test_a_load_marks_its_run_active_before_probing_its_disk_copy(
        self,
        tmp_path,
        monkeypatch,
        store,
    ):
        """The disk-cache hit inside a load must be eviction-safe before is_file()."""
        use_project(tmp_path)
        cache_root = tmp_path / ".cache" / "models"
        cached_file = _copy(cache_root, "run-fast", "model.cbm", b"model")
        cached_file.parent.mkdir(parents=True)
        cached_file.write_bytes(b"model")
        store.runs["run-fast"] = {"model.cbm": len(b"model")}
        active_seen: list[frozenset[str]] = []
        real_is_file = Path.is_file
        loaded_model = ScoringModel(_StubCatBoost(), ["a"], frozenset(), "catboost")

        def observing_is_file(path: Path) -> bool:
            if path == cached_file:
                active_seen.append(_active_disk_cache_runs())
            return real_is_file(path)

        with (
            patch.object(Path, "is_file", observing_is_file),
            patch("haute._mlflow_io._load_catboost_file", return_value=loaded_model),
            patch("mlflow.artifacts.download_artifacts", side_effect=_no_download),
        ):
            result = load_mlflow_model(_run("run-fast", "model.cbm"), task="regression")

        assert result is loaded_model
        assert active_seen
        assert active_seen[0] == frozenset({"run-fast"})

    def test_a_disk_cache_only_load_marks_its_run_active_before_probing_the_record(
        self,
        tmp_path,
        monkeypatch,
        store,
    ):
        """A load confined to the disk cache reads the record with its run guarded."""
        use_project(tmp_path)
        transport = _FakeTransport(payload=b"model")
        store.runs["run-disk"] = {"model.cbm": len(b"model")}
        loaded_model = ScoringModel(_StubCatBoost(), ["a"], frozenset(), "catboost")
        with (
            _serving(transport),
            patch("haute._mlflow_io._load_catboost_file", return_value=loaded_model),
        ):
            load_mlflow_model(_run("run-disk", "model.cbm"), task="regression")
        _model_cache.clear()  # what a new process starts with

        record = _resolution_record_path(
            tmp_path / ".cache" / "models", _local_backend(), "run-disk", "model.cbm"
        )
        active_seen: list[frozenset[str]] = []
        real_is_file = Path.is_file

        def observing_is_file(path: Path) -> bool:
            if path == record:
                active_seen.append(_active_disk_cache_runs())
            return real_is_file(path)

        monkeypatch.setattr(
            "haute._mlflow_utils.pinned_client",
            lambda backend: pytest.fail("a disk-cache-only load asked the store"),
        )
        with (
            disk_cache_only_model_loads(),
            patch.object(Path, "is_file", observing_is_file),
            patch("haute._mlflow_io._load_catboost_file", return_value=loaded_model),
            patch("mlflow.artifacts.download_artifacts", side_effect=_no_download),
        ):
            result = load_mlflow_model(_run("run-disk", "model.cbm"), task="regression")

        assert result is loaded_model
        assert active_seen
        assert active_seen[0] == frozenset({"run-disk"})
        assert transport.calls == 1


class TestLockAcquisitionRaces:
    """Deterministic, single-threaded simulations of the exact interleavings
    the thread tests can only hit probabilistically.

    Each test hooks ``_artifact_io_lock`` so "what a concurrent winner did
    while this caller waited for the lock" happens at the acquisition
    point itself — same code paths, zero timing dependence. The load lock
    (``load_resolved_model``) is the first acquisition; the download lock
    inside ``_resolve_artifact_local`` follows only when no copy is on disk.
    """

    @staticmethod
    def _hooked_lock(hook, fire_on: int = 1):
        """Return a drop-in for ``_artifact_io_lock`` running *hook* once,
        at the *fire_on*-th acquisition."""
        from haute import _mlflow_io

        real_lock = _mlflow_io._artifact_io_lock
        calls = [0]

        def locked(backend_digest: str, run_id: str, artifact_path: str):
            lock = real_lock(backend_digest, run_id, artifact_path)
            calls[0] += 1
            if calls[0] == fire_on:
                hook()
            return lock

        return locked

    @pytest.mark.parametrize("copy_on_disk", [True, False], ids=["copy-on-disk", "no-copy"])
    def test_a_waiter_reuses_the_model_cached_while_it_waited(
        self, tmp_path, monkeypatch, store, copy_on_disk
    ):
        """Cache empty at entry; the 'winner' populates it while this caller
        waits for the load lock — the waiter returns that instance without
        loading, with or without a copy on disk.

        The key is the store's answer, read before the lock, so the waiter
        needs no download to know the winner loaded the same bytes; it fetches
        only the copy a load of that answer leaves, when none is on disk.
        """
        use_project(tmp_path)
        store.runs["run-h"] = {"model.cbm": len(b"bytes")}
        if copy_on_disk:
            cached_file = _copy(tmp_path / ".cache" / "models", "run-h", "model.cbm", b"bytes")
            cached_file.parent.mkdir(parents=True)
            cached_file.write_bytes(b"bytes")
        winner_model = ScoringModel(_StubCatBoost(), ["a"], frozenset(), "catboost")
        key = _model_cache_key(
            resolve_model(_run("run-h", "model.cbm"), backend=_local_backend()), "regression"
        )
        transport = _FakeTransport(payload=b"bytes")

        def winner_populates_cache() -> None:
            _model_cache.put(key, winner_model)

        with (
            patch(
                "haute._mlflow_io._artifact_io_lock",
                side_effect=self._hooked_lock(winner_populates_cache),
            ),
            _serving(transport),
            patch("haute._mlflow_io._load_catboost_model") as load_catboost,
        ):
            result = load_mlflow_model(_run("run-h", "model.cbm"), task="regression")

        assert result is winner_model
        assert transport.calls == (0 if copy_on_disk else 1)
        load_catboost.assert_not_called()

    def test_a_copy_that_vanishes_while_waiting_is_downloaded_again(
        self, tmp_path, monkeypatch, store
    ):
        """Copy present at entry but deleted while this caller waited for the
        load lock (a concurrent corrupt-retry): the caller must re-download
        it instead of failing."""
        use_project(tmp_path)
        cached_file = _copy(tmp_path / ".cache" / "models", "run-v", "model.cbm", b"model-bytes-v1")
        cached_file.parent.mkdir(parents=True)
        cached_file.write_bytes(b"model-bytes-v0")
        store.runs["run-v"] = {"model.cbm": len(b"model-bytes-v1")}
        transport = _FakeTransport()

        def retry_deleted_the_file() -> None:
            cached_file.unlink(missing_ok=True)

        with (
            patch(
                "haute._mlflow_io._artifact_io_lock",
                side_effect=self._hooked_lock(retry_deleted_the_file),
            ),
            _serving(transport),
            patch(
                "haute._mlflow_io._load_catboost_model",
                side_effect=lambda path, task: _StubCatBoost(),
            ),
        ):
            result = load_mlflow_model(_run("run-v", "model.cbm"), task="regression")

        assert isinstance(result, ScoringModel)
        assert transport.calls == 1, "vanished file must trigger exactly one re-download"
        assert cached_file.read_bytes() == b"model-bytes-v1"
