"""Destination-aware model cache: identical run IDs/artifact paths on different
backends never alias, and unresolved configuration fails before a cached
artifact can be served (specs/roadmap/mlflow-destinations.md MLF-D03).

Each backend's tracking store is a fake client holding the same run ID and
artifact path, with the feature contract training logs beside the model; the
model resolution lists it through the client pinned to the backend, and the
download is served by the backend's tracking URI.
"""

from __future__ import annotations

import hashlib
import os
import threading
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from haute._mlflow_io import (
    ScoringModel,
    _artifact_cache_path,
    _artifact_io_lock,
    _evict_disk_cache,
    _model_cache,
    _model_cache_key,
    clear_model_cache,
    load_mlflow_model,
)
from haute._mlflow_model import ResolvedModel, StoredArtifact
from haute._mlflow_utils import ResolvedBackend, resolve_backend
from haute._model_flavors import model_family
from haute._model_source import RunModelSource
from haute._project_context import context_thread
from haute.errors import MlflowConfigError
from haute.modelling._mlflow_settings import TrackingConfig
from tests._mlflow_fakes import FakeArtifactClient
from tests._project import use_project

RUN_ID = "run-1"
ARTIFACT = "model.cbm"
CONTRACT = "model.feature_contract.json"
WAIT_MUST_HAPPEN_S = 10.0

_BASE_TOML = '[project]\nname = "main"\npipeline = "rating/main.py"\n'


@pytest.fixture(autouse=True)
def _env_free(monkeypatch: pytest.MonkeyPatch):
    """No ambient credentials: every destination comes from the fixture."""
    for var in (
        "MLFLOW_TRACKING_URI",
        "DATABRICKS_HOST",
        "DATABRICKS_TOKEN",
        "DATABRICKS_MLFLOW_HOST",
        "DATABRICKS_MLFLOW_TOKEN",
    ):
        monkeypatch.delenv(var, raising=False)
    _model_cache.clear()
    yield
    _model_cache.clear()


@pytest.fixture
def workspace(tmp_path: Path):
    """Project root with three local runs folders and an isolated disk cache."""
    (tmp_path / "haute.toml").write_text(_BASE_TOML, encoding="utf-8")
    use_project(tmp_path)
    folders = {}
    for name in ("a", "b", "c"):
        folder = tmp_path / f"runs-{name}"
        folder.mkdir()
        folders[name] = folder
    cache_root = tmp_path / "diskcache"
    with patch("haute._mlflow_io._disk_cache_root", return_value=cache_root):
        yield SimpleNamespace(root=tmp_path, cache_root=cache_root, **folders)


def _configure_local(root: Path, folder: Path) -> None:
    """Write ``[mlflow] folder`` into the workspace ``haute.toml``."""
    (root / "haute.toml").write_text(
        f'{_BASE_TOML}\n[mlflow]\nfolder = "{folder.as_posix()}"\n',
        encoding="utf-8",
    )


def _server_config(host: str) -> TrackingConfig:
    return TrackingConfig("server", host, host, "toml")


def _fake_backend(tracking_uri: str, identity: str, mode: str = "databricks") -> ResolvedBackend:
    """A backend whose store is a local folder standing in for a remote one."""
    return ResolvedBackend(
        mode=mode,
        tracking_uri=tracking_uri,
        registry_uri=tracking_uri,
        identity=identity,
        digest=hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16],
    )


class _PayloadDownloader:
    """The tracking stores of several backends, keyed on their tracking URIs.

    Each store holds ``RUN_ID`` with ``ARTIFACT`` (its own payload) and the
    contract beside it. :meth:`client` is the client pinned to a backend, which
    lists that store; calling the downloader is ``mlflow.artifacts.download_artifacts``.
    A listing or a download aimed at a backend that holds no store fails, and
    the contract is recorded apart from ``calls``, which count model downloads.
    """

    def __init__(
        self,
        payloads: dict[str, bytes],
        barrier: threading.Barrier | None = None,
    ) -> None:
        self.payloads = payloads
        self.barrier = barrier
        self.calls: list[tuple[str, str]] = []
        self.contract_calls: list[tuple[str, str]] = []
        self.clients: dict[str, FakeArtifactClient] = {}
        self._mutex = threading.Lock()

    def client(self, backend: ResolvedBackend) -> FakeArtifactClient:
        if backend.tracking_uri not in self.payloads:
            raise AssertionError(f"listing aimed at an unexpected backend: {backend.identity!r}")
        with self._mutex:
            return self.clients.setdefault(
                backend.tracking_uri,
                FakeArtifactClient(
                    {RUN_ID: {ARTIFACT: len(self.payloads[backend.tracking_uri]), CONTRACT: 2}}
                ),
            )

    def __call__(
        self,
        artifact_uri: str,
        dst_path: str | None = None,
        *,
        tracking_uri: str | None = None,
        **_kwargs: Any,
    ) -> str:
        if artifact_uri.endswith(".feature_contract.json"):
            if tracking_uri not in self.payloads:
                raise AssertionError(f"contract read from an unexpected backend: {tracking_uri!r}")
            with self._mutex:
                self.contract_calls.append((artifact_uri, tracking_uri or ""))
            assert dst_path is not None
            out = Path(dst_path) / Path(artifact_uri).name
            out.write_bytes(b"{}")
            return str(out)
        with self._mutex:
            self.calls.append((artifact_uri, tracking_uri or ""))
        if self.barrier is not None:
            self.barrier.wait()
        if tracking_uri not in self.payloads:
            raise AssertionError(f"download aimed at an unexpected backend: {tracking_uri!r}")
        assert dst_path is not None
        out = Path(dst_path) / Path(artifact_uri).name
        out.write_bytes(self.payloads[tracking_uri])
        return str(out)

    def tracking_uris(self) -> list[str]:
        return [tracking_uri for _uri, tracking_uri in self.calls]


def _fake_load_file(
    path: str, task: str = "regression", *, contract_path: str | None, source: str | None
) -> ScoringModel:
    """Loader stub whose model identity is the artifact file's bytes."""
    payload = Path(path).read_bytes().decode()
    return ScoringModel(model=f"model:{payload}", feature_names=["x"], flavor="catboost")


@contextmanager
def _patched_loaders(downloader: _PayloadDownloader):
    """Patch the backends' clients, the download transport and the native loader."""
    with (
        patch("haute._mlflow_utils.pinned_client", side_effect=downloader.client),
        patch("mlflow.artifacts.download_artifacts", new=downloader),
        patch("haute._mlflow_io._load_catboost_file", side_effect=_fake_load_file),
    ):
        yield


def _load(destination: str = "") -> ScoringModel:
    return load_mlflow_model(
        RunModelSource(run_id=RUN_ID, artifact_path=ARTIFACT, mlflow_destination=destination),
        task="regression",
    )


def _cached_artifact(cache_root: Path, backend: ResolvedBackend, payload: bytes) -> Path:
    """The disk copy of *payload*, the bytes the backend's store lists for the model."""
    token = StoredArtifact(ARTIFACT, f"size:{len(payload)}").token
    return _artifact_cache_path(cache_root, backend.digest, RUN_ID, ARTIFACT, token)


def _resolved(backend_identity: str, *, version: str = "") -> ResolvedModel:
    backend = _fake_backend("file:///runs", backend_identity, mode="local")
    return ResolvedModel(
        backend=backend,
        run_id=RUN_ID,
        artifact=StoredArtifact(ARTIFACT, "size:8"),
        family=model_family("catboost"),
        contract=StoredArtifact(CONTRACT, "size:2"),
        version=version,
        reference=f"MLflow run {RUN_ID!r}",
    )


class TestTwoLocalFolders:
    """The same run ID on two local folders is two backends, never one entry."""

    def test_same_run_id_on_two_local_folders_returns_each_folders_bytes(self, workspace):
        _configure_local(workspace.root, workspace.a)
        backend_a = resolve_backend("local")
        _configure_local(workspace.root, workspace.b)
        backend_b = resolve_backend("local")
        downloader = _PayloadDownloader(
            {backend_a.tracking_uri: b"folder-a", backend_b.tracking_uri: b"folder-b"}
        )

        with _patched_loaders(downloader):
            _configure_local(workspace.root, workspace.a)
            first_a = _load()
            _configure_local(workspace.root, workspace.b)
            from_b = _load()
            _configure_local(workspace.root, workspace.a)
            second_a = _load()

        assert first_a.raw_model == "model:folder-a"
        assert from_b.raw_model == "model:folder-b"
        assert second_a.raw_model == "model:folder-a"
        assert downloader.tracking_uris() == [backend_a.tracking_uri, backend_b.tracking_uri]

    def test_disk_cache_paths_are_partitioned_by_backend_digest(self, workspace):
        _configure_local(workspace.root, workspace.a)
        backend_a = resolve_backend("local")
        _configure_local(workspace.root, workspace.b)
        backend_b = resolve_backend("local")
        downloader = _PayloadDownloader(
            {backend_a.tracking_uri: b"folder-a", backend_b.tracking_uri: b"folder-b"}
        )

        with _patched_loaders(downloader):
            _configure_local(workspace.root, workspace.a)
            _load()
            _configure_local(workspace.root, workspace.b)
            _load()

        path_a = _cached_artifact(workspace.cache_root, backend_a, b"folder-a")
        path_b = _cached_artifact(workspace.cache_root, backend_b, b"folder-b")
        assert path_a != path_b
        assert path_a.read_bytes() == b"folder-a"
        assert path_b.read_bytes() == b"folder-b"
        cache_root = workspace.cache_root.resolve()
        assert path_a.relative_to(cache_root).parts[:2] == (backend_a.digest, RUN_ID)
        assert path_b.relative_to(cache_root).parts[:2] == (backend_b.digest, RUN_ID)
        # Each backend's contract came from that backend, beside its own model.
        assert sorted(uri for _artifact, uri in downloader.contract_calls) == sorted(
            [backend_a.tracking_uri, backend_b.tracking_uri]
        )

    def test_warm_cache_follows_folder_change_in_toml(self, workspace):
        _configure_local(workspace.root, workspace.a)
        backend_a = resolve_backend("local")
        _configure_local(workspace.root, workspace.b)
        backend_b = resolve_backend("local")
        downloader = _PayloadDownloader(
            {backend_a.tracking_uri: b"folder-a", backend_b.tracking_uri: b"folder-b"}
        )

        with _patched_loaders(downloader):
            _configure_local(workspace.root, workspace.a)
            warm = _load()
            _configure_local(workspace.root, workspace.b)
            after_change = _load()

        assert warm.raw_model == "model:folder-a"
        assert after_change.raw_model == "model:folder-b", (
            "the warm cache served folder A's model after the toml pointed at folder B"
        )

    def test_warm_cache_follows_a_node_choosing_databricks(self, workspace, monkeypatch):
        _configure_local(workspace.root, workspace.a)
        backend_a = resolve_backend("")
        # The remote workspace's store is a local folder so the test stays offline.
        backend_c = _fake_backend(
            workspace.c.as_uri(),
            "databricks:https://adb-1.example.net|profile=|registry=databricks-uc",
        )
        downloader = _PayloadDownloader(
            {backend_a.tracking_uri: b"folder-a", backend_c.tracking_uri: b"workspace-c"}
        )
        real_resolve_backend = resolve_backend

        def resolve_with_databricks(destination: str = "", project_root: Path | None = None):
            if destination == "databricks":
                return backend_c
            return real_resolve_backend(destination, project_root)

        with _patched_loaders(downloader):
            unchosen = _load()
            monkeypatch.setenv("DATABRICKS_MLFLOW_HOST", "https://adb-1.example.net")
            monkeypatch.setenv("DATABRICKS_MLFLOW_TOKEN", "synthetic-token")
            with patch(
                "haute._mlflow_utils.resolve_backend",
                side_effect=resolve_with_databricks,
            ):
                still_local = _load()
                chosen = _load("databricks")

        assert unchosen.raw_model == "model:folder-a"
        assert still_local.raw_model == "model:folder-a", (
            "configuring Databricks retargeted a node that never chose it"
        )
        assert chosen.raw_model == "model:workspace-c", (
            "the warm local entry was served after the node chose Databricks"
        )
        assert downloader.tracking_uris() == [backend_a.tracking_uri, backend_c.tracking_uri]


class TestUnresolvableDestination:
    """A destination whose prerequisites are gone fails before the cache."""

    def test_removing_explicit_prerequisites_after_warm_raises_config_error(self, workspace):
        host = "https://mlflow-a.example.test"
        downloader = _PayloadDownloader({host: b"server-a"})

        with (
            _patched_loaders(downloader),
            patch(
                "haute.modelling._mlflow_settings._resolve_server",
                return_value=_server_config(host),
            ),
        ):
            warm = _load(destination="server")

        assert warm.raw_model == "model:server-a"

        with (
            patch(
                "haute._mlflow_io._load_catboost_file",
                side_effect=AssertionError("the cached artifact must not be served"),
            ),
            patch(
                "haute._mlflow_utils.pinned_client",
                side_effect=AssertionError("the store was asked for an unresolved destination"),
            ),
            patch("mlflow.artifacts.download_artifacts", new=downloader),
            pytest.raises(MlflowConfigError, match="MLflow server is not configured"),
        ):
            _load(destination="server")

        assert downloader.calls == [(f"runs:/{RUN_ID}/{ARTIFACT}", host)]


class TestClearAndEviction:
    """Clearing and eviction see every backend partition."""

    def test_clear_model_cache_by_run_id_clears_every_backend(self, workspace):
        digest_a, digest_b = "a" * 16, "b" * 16
        token = StoredArtifact(ARTIFACT, "size:6").token
        for digest in (digest_a, digest_b):
            run_dir = workspace.cache_root / digest / RUN_ID
            assert (
                run_dir
                == _artifact_cache_path(
                    workspace.cache_root, digest, RUN_ID, ARTIFACT, token
                ).parents[2]
            )
            run_dir.mkdir(parents=True)
            (run_dir / "artifact.cbm").write_bytes(b"cached")
        other_run = workspace.cache_root / digest_a / "run-2"
        other_run.mkdir(parents=True)
        (other_run / "artifact.cbm").write_bytes(b"other")
        keys = [
            _model_cache_key(_resolved(identity), "regression")
            for identity in ("local:/a|registry=file:///a", "local:/b|registry=file:///b")
        ]
        for key in keys:
            _model_cache.put(key, ScoringModel(model="m", feature_names=["x"], flavor="catboost"))

        removed = clear_model_cache(run_id=RUN_ID)

        assert removed == 2
        assert not (workspace.cache_root / digest_a / RUN_ID).exists()
        assert not (workspace.cache_root / digest_b / RUN_ID).exists()
        assert (other_run / "artifact.cbm").is_file()
        assert all(_model_cache.get(key) is None for key in keys)

    def test_eviction_counts_run_dirs_across_backends(self, workspace):
        digest_a, digest_b = "a" * 16, "b" * 16
        layout = {
            "run-old": (digest_a, 1_700_000_000),
            "run-mid": (digest_a, 1_700_000_100),
            "run-new": (digest_b, 1_700_000_200),
        }
        token = StoredArtifact(ARTIFACT, "size:6").token
        for run_id, (digest, mtime) in layout.items():
            run_dir = workspace.cache_root / digest / run_id
            assert (
                run_dir
                == _artifact_cache_path(
                    workspace.cache_root, digest, run_id, ARTIFACT, token
                ).parents[2]
            )
            run_dir.mkdir(parents=True)
            (run_dir / "artifact.cbm").write_bytes(b"cached")
            os.utime(run_dir, (mtime, mtime))

        with patch("haute._mlflow_io._DISK_CACHE_MAX_DIRS", 2):
            _evict_disk_cache(workspace.cache_root)

        assert not (workspace.cache_root / digest_a / "run-old").exists(), (
            "eviction counted run directories per backend instead of across them"
        )
        assert (workspace.cache_root / digest_a / "run-mid").is_dir()
        assert (workspace.cache_root / digest_b / "run-new").is_dir()


class TestKeyAndLockIdentity:
    """The in-memory key and the I/O lock both carry the backend."""

    def test_model_cache_key_contains_backend_identity_last(self):
        identity = "local:/a|registry=file:///a"
        key = _model_cache_key(_resolved(identity), "regression")
        versioned = _model_cache_key(_resolved(identity, version="7"), "regression")
        other_backend = _model_cache_key(_resolved("local:/b|registry=file:///b"), "regression")

        assert key[-1] == identity
        assert key[1] == RUN_ID
        # A version registered from the same file is the same entry, on its backend only.
        assert versioned == key
        assert key != other_backend

    def test_artifact_io_lock_is_per_backend(self):
        lock_a = _artifact_io_lock("a" * 16, RUN_ID, ARTIFACT)
        lock_b = _artifact_io_lock("b" * 16, RUN_ID, ARTIFACT)

        assert lock_a is not lock_b
        assert _artifact_io_lock("a" * 16, RUN_ID, ARTIFACT) is lock_a


class TestTwoServerEndpoints:
    """Two endpoints of one category are two backends."""

    def test_two_server_endpoints_same_category_do_not_alias(self, workspace):
        host_a = "https://mlflow-a.example.test"
        host_b = "https://mlflow-b.example.test"
        downloader = _PayloadDownloader({host_a: b"server-a", host_b: b"server-b"})

        with _patched_loaders(downloader):
            with patch(
                "haute.modelling._mlflow_settings._resolve_server",
                return_value=_server_config(host_a),
            ):
                from_a = _load(destination="server")
                backend_a = resolve_backend("server")
            with patch(
                "haute.modelling._mlflow_settings._resolve_server",
                return_value=_server_config(host_b),
            ):
                from_b = _load(destination="server")
                backend_b = resolve_backend("server")

        assert from_a.raw_model == "model:server-a"
        assert from_b.raw_model == "model:server-b"
        assert backend_a.digest != backend_b.digest
        assert (
            _cached_artifact(workspace.cache_root, backend_a, b"server-a").read_bytes()
            == b"server-a"
        )
        assert (
            _cached_artifact(workspace.cache_root, backend_b, b"server-b").read_bytes()
            == b"server-b"
        )


class TestConcurrentBackends:
    """Per-backend locks let two backends load the same run concurrently."""

    def test_concurrent_loads_on_different_backends_stay_isolated(self, workspace):
        _configure_local(workspace.root, workspace.a)
        backend_local = resolve_backend("local")
        host = "https://mlflow-a.example.test"
        barrier = threading.Barrier(2, timeout=WAIT_MUST_HAPPEN_S)
        downloader = _PayloadDownloader(
            {backend_local.tracking_uri: b"folder-a", host: b"server-a"},
            barrier=barrier,
        )
        results: dict[str, ScoringModel] = {}
        errors: dict[str, BaseException] = {}

        def run(name: str, destination: str) -> None:
            try:
                results[name] = _load(destination=destination)
            except BaseException as exc:  # surfaced via assertions
                errors[name] = exc

        threads = [
            context_thread(run, args=("local", "local"), daemon=True),
            context_thread(run, args=("server", "server"), daemon=True),
        ]

        with (
            _patched_loaders(downloader),
            patch(
                "haute.modelling._mlflow_settings._resolve_server",
                return_value=_server_config(host),
            ),
        ):
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(WAIT_MUST_HAPPEN_S)

        assert not any(thread.is_alive() for thread in threads), "a loader deadlocked"
        assert errors == {}, f"concurrent backends serialized or failed: {errors}"
        assert results["local"].raw_model == "model:folder-a"
        assert results["server"].raw_model == "model:server-a"
        assert sorted(downloader.tracking_uris()) == sorted([backend_local.tracking_uri, host])


class TestOneResolutionPerLoad:
    """Exactly one backend resolution per load, and it is the one used."""

    @pytest.mark.parametrize("path", ["memory-hit", "download"])
    def test_load_resolves_backend_exactly_once(self, workspace, path: str):
        _configure_local(workspace.root, workspace.a)
        backend_a = resolve_backend("local")
        downloader = _PayloadDownloader({backend_a.tracking_uri: b"folder-a"})
        real_resolve_backend = resolve_backend
        calls: list[str] = []

        def counting_resolve(destination: str = "", project_root: Path | None = None):
            calls.append(destination)
            return real_resolve_backend(destination, project_root)

        with _patched_loaders(downloader):
            warm = _load()
            assert warm.raw_model == "model:folder-a"
            if path == "download":
                _model_cache.clear()
                _cached_artifact(workspace.cache_root, backend_a, b"folder-a").unlink()
            with patch(
                "haute._mlflow_utils.resolve_backend",
                side_effect=counting_resolve,
            ):
                again = _load()

        assert again.raw_model == "model:folder-a"
        assert calls == [""], f"{path} path resolved the backend {len(calls)} time(s)"
        assert len(downloader.calls) == (2 if path == "download" else 1)

    def test_settings_change_during_load_does_not_split_backends(self, workspace):
        _configure_local(workspace.root, workspace.a)
        backend_a = resolve_backend("local")
        _configure_local(workspace.root, workspace.b)
        backend_b = resolve_backend("local")
        _configure_local(workspace.root, workspace.a)
        downloader = _PayloadDownloader(
            {backend_a.tracking_uri: b"folder-a", backend_b.tracking_uri: b"folder-b"}
        )

        with (
            _patched_loaders(downloader),
            patch(
                "haute._mlflow_utils.resolve_backend",
                side_effect=[backend_a, backend_b],
            ),
        ):
            loaded = _load()

        assert loaded.raw_model == "model:folder-a"
        # The listing, the model and its contract all came from the one backend.
        assert list(downloader.clients) == [backend_a.tracking_uri]
        assert downloader.tracking_uris() == [backend_a.tracking_uri]
        assert [uri for _artifact, uri in downloader.contract_calls] == [backend_a.tracking_uri]
        assert (
            _cached_artifact(workspace.cache_root, backend_a, b"folder-a").read_bytes()
            == b"folder-a"
        )
        assert not _cached_artifact(workspace.cache_root, backend_b, b"folder-b").exists()
