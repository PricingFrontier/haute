"""Key-contract tests for the in-process MLflow model cache.

Pins cache-key COMPLETENESS: the model's stored bytes are an input that
affects the cached output (the loaded ``ScoringModel``), so changing them
under an unchanged reference MUST miss the in-process cache. Without this, a
re-logged MLflow run or a ``version="latest"`` retrain keeps serving the
previously loaded model on a long-lived server until ``clear_model_cache`` is
called by hand.

The key is the model resolution's answer (``_model_cache_key(resolved, task)``):
the run, the artifact's path and the identity the store gives its bytes, the
contract's identity, the task and the backend. So a file logged again (a new
identity) or an alias moved to another version (another file) misses, and
every reference that resolves to one file shares one entry.

The perturbation tests log files to a real local MLflow store, whose identities
are the stored files' content hashes. The loader stub builds each model from
the bytes it was handed, so a stale entry shows as the old bytes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from haute._mlflow_io import (
    ScoringModel,
    _model_cache,
    _model_cache_key,
    clear_model_cache,
    load_mlflow_model,
)
from haute._mlflow_model import ResolvedModel, StoredArtifact
from haute._mlflow_utils import ResolvedBackend
from haute._model_flavors import model_family
from haute._model_source import RegisteredModelSource, RunModelSource
from tests._project import use_project

RUN_ID = "run_relog"
ARTIFACT = "model.cbm"
MODEL = "pricing_model"


@pytest.fixture(autouse=True)
def _clean_cache():
    _model_cache.clear()
    yield
    _model_cache.clear()


@pytest.fixture()
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """A client of the project's local MLflow store, the one ``""`` resolves to."""
    from mlflow.tracking import MlflowClient

    monkeypatch.setenv("MLFLOW_ALLOW_FILE_STORE", "true")
    for name in ("MLFLOW_TRACKING_URI", "DATABRICKS_MLFLOW_HOST", "DATABRICKS_MLFLOW_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    use_project(tmp_path)
    uri = (tmp_path / "mlruns").as_uri()
    return MlflowClient(tracking_uri=uri, registry_uri=uri)


def _new_run(store: Any) -> str:
    experiment = store.get_experiment_by_name("cache-key") or store.get_experiment(
        store.create_experiment("cache-key")
    )
    return str(store.create_run(experiment.experiment_id).info.run_id)


def _log(store: Any, run_id: str, folder: Path, payload: bytes, name: str = ARTIFACT) -> None:
    """Log *payload* as *name* at the top of the run, from a fresh *folder*."""
    folder.mkdir(parents=True)
    (folder / name).write_bytes(payload)
    store.log_artifact(run_id, str(folder / name))


def _register(store: Any, run_id: str) -> str:
    """Register ``model.cbm`` of the run as a new version of ``pricing_model``."""
    if not store.search_registered_models(f"name = '{MODEL}'"):
        store.create_registered_model(MODEL)
    location = store.get_run(run_id).info.artifact_uri
    return str(store.create_model_version(MODEL, f"{location}/{ARTIFACT}", run_id).version)


def _fake_load_file(
    path: str,
    task: str = "regression",
    *,
    contract_path: str | None = None,
    source: str | None = None,
) -> ScoringModel:
    """Loader stub whose model identity is the artifact file's bytes."""
    payload = Path(path).read_bytes().decode()
    return ScoringModel(model=f"model:{payload}", feature_names=["x"], flavor="catboost")


def _load(source: RunModelSource | RegisteredModelSource) -> ScoringModel:
    with patch("haute._mlflow_io._load_catboost_file", side_effect=_fake_load_file):
        return load_mlflow_model(source, task="regression")


def _run_source(run_id: str, artifact_path: str = ARTIFACT) -> RunModelSource:
    return RunModelSource(run_id=run_id, artifact_path=artifact_path, mlflow_destination="")


def _registered(*, version: str = "", alias: str = "") -> RegisteredModelSource:
    return RegisteredModelSource(
        registered_model=MODEL, version=version, alias=alias, mlflow_destination=""
    )


class TestFastPathArtifactPerturbation:
    """A run reference naming its artifact path.

    Once a disk-cache fast path that never asked MLflow; every load now
    resolves through the store, so the run's current bytes are always seen.
    """

    def test_relogged_artifact_bytes_invalidate_in_process_cache(self, store, tmp_path):
        """Re-log under the same run ref → the NEW model is served, not stale."""
        run_id = _new_run(store)
        _log(store, run_id, tmp_path / "v1", b"weights-v1")

        first = _load(_run_source(run_id))
        assert first.raw_model == "model:weights-v1"

        # The re-log / retrain-in-place: same run, same artifact path, different
        # bytes of the same length, so a key that read only the size would miss it.
        _log(store, run_id, tmp_path / "v2", b"weights-v2")

        second = _load(_run_source(run_id))
        assert second.raw_model == "model:weights-v2", (
            "in-process model cache served the stale pre-relog model: the "
            "cache key does not fold in artifact identity"
        )

    def test_unchanged_artifact_still_hits_cache(self, store, tmp_path):
        """No perturbation → second call is an in-process hit (same object)."""
        run_id = _new_run(store)
        _log(store, run_id, tmp_path / "v1", b"weights-v1")

        first = _load(_run_source(run_id))
        with patch(
            "haute._mlflow_io._load_catboost_file",
            side_effect=AssertionError("unchanged artifact must not reload"),
        ):
            second = load_mlflow_model(_run_source(run_id), task="regression")
        assert second is first

    def test_a_contract_logged_later_is_a_new_entry(self, store, tmp_path):
        """The contract beside the model is part of the key: logging one misses."""
        run_id = _new_run(store)
        _log(store, run_id, tmp_path / "v1", b"weights-v1")

        bare = _load(_run_source(run_id))
        assert bare.run_contract_path is None

        _log(store, run_id, tmp_path / "contract", b"{}", name="model.feature_contract.json")

        contracted = _load(_run_source(run_id))
        assert contracted is not bare
        assert contracted.run_contract_path is not None
        assert Path(contracted.run_contract_path).read_bytes() == b"{}"


class TestFullPathArtifactPerturbation:
    """A registered reference (``version="latest"``, an alias)."""

    def test_latest_retrain_in_place_invalidates_cache(self, store, tmp_path):
        run_id = _new_run(store)
        _log(store, run_id, tmp_path / "v1", b"weights-v1")
        _register(store, run_id)

        first = _load(_registered(version="latest"))
        assert first.raw_model == "model:weights-v1"

        # Retrained in place: the version's file logged again, same length.
        _log(store, run_id, tmp_path / "v2", b"weights-v2")

        second = _load(_registered(version="latest"))
        assert second.raw_model == "model:weights-v2", (
            'version="latest" retrain served the stale in-process model'
        )

    def test_latest_follows_a_new_version(self, store, tmp_path):
        first_run = _new_run(store)
        _log(store, first_run, tmp_path / "v1", b"weights-v1")
        _register(store, first_run)
        assert _load(_registered(version="latest")).raw_model == "model:weights-v1"

        second_run = _new_run(store)
        _log(store, second_run, tmp_path / "v2", b"weights-v2")
        _register(store, second_run)

        assert _load(_registered(version="latest")).raw_model == "model:weights-v2"

    def test_a_moved_alias_loads_the_file_it_now_names(self, store, tmp_path):
        first_run, second_run = _new_run(store), _new_run(store)
        _log(store, first_run, tmp_path / "v1", b"weights-v1")
        _log(store, second_run, tmp_path / "v2", b"weights-v2")
        first_version, second_version = _register(store, first_run), _register(store, second_run)

        store.set_registered_model_alias(MODEL, "champion", first_version)
        assert _load(_registered(alias="champion")).raw_model == "model:weights-v1"

        store.set_registered_model_alias(MODEL, "champion", second_version)
        assert _load(_registered(alias="champion")).raw_model == "model:weights-v2", (
            "the alias moved to another file but the cache served the one it left"
        )


class TestTwoReferencesToOneFile:
    def test_every_reference_to_one_file_shares_one_entry(self, store, tmp_path):
        """A run with its path, the run by discovery and a version registered from
        the file resolve to one file and its contract, so they load one model."""
        run_id = _new_run(store)
        _log(store, run_id, tmp_path / "v1", b"weights-v1")
        version = _register(store, run_id)
        loads: list[str] = []

        def counting_load(path: str, task: str, **kwargs: Any) -> ScoringModel:
            loads.append(path)
            return _fake_load_file(path, task, **kwargs)

        with patch("haute._mlflow_io._load_catboost_file", side_effect=counting_load):
            by_path = load_mlflow_model(_run_source(run_id), task="regression")
            by_discovery = load_mlflow_model(_run_source(run_id, ""), task="regression")
            by_version = load_mlflow_model(_registered(version=version), task="regression")

        assert by_path is by_discovery is by_version
        assert len(loads) == 1


def _backend() -> ResolvedBackend:
    return ResolvedBackend(
        mode="local",
        tracking_uri="file:///runs",
        registry_uri="file:///runs",
        identity="local:/runs|registry=file:///runs",
        digest="0123456789abcdef",
    )


def _resolved(
    *,
    identity: str = "size:10",
    contract: StoredArtifact | None = None,
    version: str = "",
    reference: str = f"MLflow run {RUN_ID!r}",
) -> ResolvedModel:
    return ResolvedModel(
        backend=_backend(),
        run_id=RUN_ID,
        artifact=StoredArtifact(ARTIFACT, identity),
        family=model_family("catboost"),
        contract=contract,
        version=version,
        reference=reference,
    )


class TestKeyContract:
    """Direct pins on the key derivation and eviction compatibility."""

    def test_key_folds_artifact_fingerprint(self):
        """The identity the store gives the artifact's bytes is in the key."""
        key_a = _model_cache_key(_resolved(identity="content:xxh64:aaaa"), "regression")
        key_b = _model_cache_key(_resolved(identity="content:xxh64:bbbb"), "regression")
        assert key_a != key_b
        assert key_a == _model_cache_key(_resolved(identity="content:xxh64:aaaa"), "regression")

    def test_key_folds_the_contract_identity(self):
        contract = StoredArtifact("model.feature_contract.json", "size:2")
        bare = _model_cache_key(_resolved(), "regression")
        contracted = _model_cache_key(_resolved(contract=contract), "regression")
        relogged = _model_cache_key(
            _resolved(contract=StoredArtifact(contract.path, "size:3")), "regression"
        )
        assert len({bare, contracted, relogged}) == 3

    def test_how_the_file_was_reached_is_not_in_the_key(self):
        """A run reference and a registered version of one file share an entry."""
        by_run = _model_cache_key(_resolved(), "regression")
        by_version = _model_cache_key(
            _resolved(version="7", reference="version 7 of registered model 'm'"), "regression"
        )
        assert by_run == by_version
        assert by_run != _model_cache_key(_resolved(), "classification")

    def test_targeted_clear_still_evicts_by_run_id(self, tmp_path, monkeypatch):
        """clear_model_cache(run_id=...) matches the run slot in the key."""
        use_project(tmp_path)
        key = _model_cache_key(_resolved(), "regression")
        assert key[1] == RUN_ID
        _model_cache.put(
            key,
            ScoringModel(model="m", feature_names=["x"], flavor="catboost"),
        )
        clear_model_cache(run_id=RUN_ID)
        assert _model_cache.get(key) is None
