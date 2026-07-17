"""Key-contract tests for the in-process MLflow model cache.

Pins cache-key COMPLETENESS: the model's artifact bytes are an input that
affects the cached output (the loaded ``ScoringModel``), so perturbing them
under an unchanged run reference MUST invalidate the in-process cache.
Without this, a re-logged MLflow run or a ``version="latest"`` retrain keeps
serving the previously loaded model on a long-lived server until
``clear_model_cache`` is called by hand.

Each perturbation test rewrites the local artifact bytes in place (the
disk-cache file for the fast path; the resolved local path for the full
path) and asserts the next ``load_mlflow_model`` call returns a model built
from the NEW bytes, not the cached one.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from haute._mlflow_io import (
    ScoringModel,
    _artifact_cache_path,
    _model_cache,
    _model_cache_key,
    clear_model_cache,
    load_mlflow_model,
)

RUN_ID = "run_relog"
ARTIFACT = "model.cbm"


@pytest.fixture(autouse=True)
def _clean_cache():
    _model_cache.clear()
    yield
    _model_cache.clear()


def _fake_load_local(path: str, task: str = "regression") -> ScoringModel:
    """Loader stub whose model identity is the artifact file's bytes."""
    payload = Path(path).read_bytes().decode()
    return ScoringModel(
        model=f"model:{payload}",
        feature_names=["x"],
        flavor="catboost",
    )


def _write_artifact(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def _bump_mtime(path: Path) -> None:
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))


class TestFastPathArtifactPerturbation:
    """source_type="run" fast path (disk-cache-backed CatBoost/RustyStats)."""

    def _load(self):
        with patch("haute._mlflow_io.load_local_model", side_effect=_fake_load_local):
            return load_mlflow_model(
                source_type="run",
                run_id=RUN_ID,
                artifact_path=ARTIFACT,
                task="regression",
            )

    def test_relogged_artifact_bytes_invalidate_in_process_cache(self, tmp_path, monkeypatch):
        """Re-log under the same run ref → the NEW model is served, not stale."""
        monkeypatch.chdir(tmp_path)
        local = _artifact_cache_path(tmp_path / ".cache" / "models", RUN_ID, ARTIFACT)
        _write_artifact(local, b"weights-v1")

        first = self._load()
        assert first.raw_model == "model:weights-v1"

        # Simulate the re-log / retrain-in-place: same run_id, same artifact
        # path, different bytes on disk.
        _write_artifact(local, b"weights-v2-relogged")
        _bump_mtime(local)

        second = self._load()
        assert second.raw_model == "model:weights-v2-relogged", (
            "in-process model cache served the stale pre-relog model: the "
            "cache key does not fold in artifact identity"
        )

    def test_unchanged_artifact_still_hits_cache(self, tmp_path, monkeypatch):
        """No perturbation → second call is an in-process hit (same object)."""
        monkeypatch.chdir(tmp_path)
        local = _artifact_cache_path(tmp_path / ".cache" / "models", RUN_ID, ARTIFACT)
        _write_artifact(local, b"weights-v1")

        first = self._load()
        with patch(
            "haute._mlflow_io.load_local_model",
            side_effect=AssertionError("unchanged artifact must not reload"),
        ):
            second = load_mlflow_model(
                source_type="run",
                run_id=RUN_ID,
                artifact_path=ARTIFACT,
                task="regression",
            )
        assert second is first


class TestFullPathArtifactPerturbation:
    """Resolve-based path (registered model / version="latest")."""

    def _load(self, local_file: Path):
        mock_mlflow = MagicMock()
        with (
            patch(
                "haute._mlflow_io.resolve_mlflow_source",
                return_value=(RUN_ID, "latest", mock_mlflow, MagicMock()),
            ),
            patch(
                "haute._mlflow_io._resolve_artifact_local",
                return_value=str(local_file),
            ),
            patch(
                "haute._mlflow_io._load_catboost_model",
                side_effect=lambda path, task: f"raw:{Path(path).read_bytes().decode()}",
            ),
            patch(
                "haute._mlflow_io._wrap_catboost",
                side_effect=lambda raw: ScoringModel(
                    model=raw, feature_names=["x"], flavor="catboost"
                ),
            ),
        ):
            return load_mlflow_model(
                source_type="registered",
                registered_model="pricing_model",
                version="latest",
                artifact_path=ARTIFACT,
                task="regression",
            )

    def test_latest_retrain_in_place_invalidates_cache(self, tmp_path):
        local = tmp_path / ARTIFACT
        _write_artifact(local, b"weights-v1")

        first = self._load(local)
        assert first.raw_model == "raw:weights-v1"

        _write_artifact(local, b"weights-v2-retrained")
        _bump_mtime(local)

        second = self._load(local)
        assert second.raw_model == "raw:weights-v2-retrained", (
            'version="latest" retrain served the stale in-process model'
        )


class TestKeyContract:
    """Direct pins on the key derivation and eviction compatibility."""

    def test_key_folds_artifact_fingerprint(self):
        base = dict(
            source_type="run",
            run_id=RUN_ID,
            version="",
            artifact_path=ARTIFACT,
            task="regression",
        )
        key_a = _model_cache_key(artifact_fingerprint="fp-a", **base)
        key_b = _model_cache_key(artifact_fingerprint="fp-b", **base)
        assert key_a != key_b
        assert key_a == _model_cache_key(artifact_fingerprint="fp-a", **base)

    def test_targeted_clear_still_evicts_by_run_id(self, tmp_path, monkeypatch):
        """clear_model_cache(run_id=...) matches the run slot in the new key."""
        monkeypatch.chdir(tmp_path)
        key = _model_cache_key(
            source_type="run",
            run_id=RUN_ID,
            version="",
            artifact_path=ARTIFACT,
            task="regression",
            artifact_fingerprint="fp-a",
        )
        _model_cache.put(
            key,
            ScoringModel(model="m", feature_names=["x"], flavor="catboost"),
        )
        clear_model_cache(run_id=RUN_ID)
        assert _model_cache.get(key) is None
