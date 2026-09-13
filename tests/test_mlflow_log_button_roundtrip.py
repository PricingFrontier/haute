"""Round-trip tests for the "Log to MLflow" button (POST /api/modelling/mlflow/log).

CODE_REVIEW MEDIUM "Modelling" / remediation 4b.8: the button route built
``ModelCardMetadata`` without ``feature_types`` / ``categorical_features`` /
``target_name`` / ``target_type``, so ``_build_signature_for_log`` defaulted
EVERY feature to ``Float64`` — a categorical (string) feature was logged with
a ``double`` signature and the logged-then-reloaded model could not score
(mlflow's schema enforcement rejects string-vs-double).  It also built
``ModelDiagnostics`` without the four ``glm_*`` fields, silently dropping GLM
coefficients / relativities / fit statistics / regularization path from the
logged run.

The fix derives the signature metadata from the model's persisted feature
contract (``feature_contract.json`` written by ``TrainingJob._save_artifacts``
next to the model file — the same artifact the deploy bundler and scorer
consume), and passes the GLM diagnostics through, mirroring the in-training
``TrainingJob._log_to_mlflow`` path.

Fixture pattern: real local file-store MLflow (``monkeypatch.chdir`` so
``mlruns`` lands in tmp) — no tracking server; same approach as
``tests/test_mlflow_log.py::test_rustystats_run_yields_native_artifact_discoverable_end_to_end``
and the real-pyfunc module ``tests/test_mlflow_io_real_pyfunc.py``.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import patch

import numpy as np
import polars as pl
import pytest

from tests.training_artifacts_support import publish_trained_job

# This integration test needs the real core MLflow package. Keep the guard for
# deliberately partial test environments.
mlflow = pytest.importorskip(
    "mlflow",
    reason="core mlflow dependency is unavailable",
)

from haute._mlflow_utils import mlflow_fluent_operation  # noqa: E402
from haute.modelling._feature_contract import (  # noqa: E402 — after importorskip by design
    build_contract,
    save_contract,
)
from haute.modelling._training_job import (  # noqa: E402
    evaluation_artifact_filenames,
    model_contract_filename,
)
from haute.schemas import TrainResponse  # noqa: E402

FEATURES = ["x", "c"]
CAT_FEATURES = ["c"]
TARGET = "y"


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def local_mlflow(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Path]:
    """Local file-store MLflow rooted in tmp without ambient credentials."""
    from haute._sandbox import set_project_root

    monkeypatch.delenv("DATABRICKS_HOST", raising=False)
    monkeypatch.delenv("DATABRICKS_TOKEN", raising=False)
    monkeypatch.delenv("DATABRICKS_MLFLOW_HOST", raising=False)
    monkeypatch.delenv("DATABRICKS_MLFLOW_TOKEN", raising=False)
    monkeypatch.delenv("MLFLOW_TRACKING_URI", raising=False)
    monkeypatch.setenv("MLFLOW_ALLOW_FILE_STORE", "true")
    monkeypatch.chdir(tmp_path)
    set_project_root(tmp_path)  # conftest restores the original project root.
    training_root = (tmp_path / "training-artifacts").resolve()
    monkeypatch.setattr(
        "haute.routes._training_artifacts.training_artifact_root", lambda: training_root
    )
    yield tmp_path


@contextmanager
def _seeded_job(
    job_id: str,
    result: TrainResponse,
    config: dict[str, Any],
    node_label: str = "model",
) -> Iterator[None]:
    """Publish a completed training job, with its own artifact set, into the route's store."""
    from haute.routes import _training_artifacts
    from haute.routes.modelling import _store

    publish_trained_job(
        _store,
        job_id,
        root=_training_artifacts.training_artifact_root(),
        model_file=Path(result.model_path),
        result=result,
        config=config,
        node_label=node_label,
    )
    try:
        yield
    finally:
        _store.delete_job(job_id)


def _training_frame(n: int = 80) -> pl.DataFrame:
    rng = np.random.RandomState(7)
    x = rng.rand(n)
    c = rng.choice(["red", "blue", "green"], size=n)
    y = 2.0 * x + np.where(c == "red", 0.5, 0.0) + rng.rand(n) * 0.01
    return pl.DataFrame({"x": x, "c": c, "y": y})


def _write_contract(model_path: Path) -> None:
    """Persist the train-vs-score contract next to the model under the
    per-model name, exactly as ``TrainingJob._save_artifacts`` does on
    every real run (remediation 4b.9 naming)."""
    contract = build_contract(
        features=FEATURES,
        feature_types={"x": "Float64", "c": "String"},
        categorical_features=CAT_FEATURES,
        target_name=TARGET,
        target_type="Float64",
        task="regression",
    )
    save_contract(contract, model_path.parent / model_contract_filename(model_path.stem))


def _train_catboost(model_dir: Path) -> tuple[Path, Any]:
    """Train a tiny real CatBoost model with a string categorical feature."""
    from catboost import CatBoostRegressor

    df = _training_frame()
    train = df.select(FEATURES).to_pandas()
    model = CatBoostRegressor(
        iterations=8,
        depth=2,
        verbose=0,
        allow_writing_files=False,
        cat_features=CAT_FEATURES,
        random_seed=0,
    )
    model.fit(train, df[TARGET].to_numpy())
    model_path = model_dir / "freq.cbm"
    model.save_model(str(model_path))
    _write_contract(model_path)
    return model_path, model


def _train_glm(model_dir: Path) -> tuple[Path, Any, Any]:
    """Fit a tiny real RustyStats GLM (numeric + categorical term)."""
    from haute.modelling._rustystats import GLMAlgorithm

    df = _training_frame()
    algo = GLMAlgorithm()
    fit = algo.fit(df, FEATURES, CAT_FEATURES, TARGET, None, {"family": "gaussian"}, "regression")
    model_path = model_dir / "sev.rsglm"
    algo.save(fit.model, model_path)
    _write_contract(model_path)
    return model_path, fit.model, algo


def _completed_result(model_path: str, **overrides: Any) -> TrainResponse:
    artifact_path = str(Path(__file__).resolve())
    base: dict[str, Any] = {
        "status": "completed",
        "diagnostic_metrics": {"rmse": 0.1, "gini": 0.5},
        "final_test_metrics": {},
        "model_path": model_path,
        "development_rows": 80,
        "final_test_rows": 0,
        "diagnostics_set": "development",
        "features": FEATURES,
        "cat_features": CAT_FEATURES,
        "evaluation": {
            "schema_version": 1,
            "strategy": "random",
            "validation_method": "none",
            "validation_fit_count": 0,
            "fit_count": 1,
            "development_rows": 80,
            "final_test_rows": 0,
            "selection_fits": [],
            "selection_metrics": {},
            "plan_sha256": "a" * 64,
            "results_sha256": "b" * 64,
            "plan_path": artifact_path,
            "results_path": artifact_path,
            "report_path": artifact_path,
            "summary": {
                "development_rows": 80,
                "test_rows": 0,
                "validation_fit_count": 0,
            },
        },
    }
    base.update(overrides)
    return TrainResponse(**base)


def _assert_candidate_run_contract(tracking_uri: str, run_id: str, *, model_file: str) -> None:
    """The candidate-run contract on a real stored run (see mlflow-model-registry)."""
    from mlflow.tracking import MlflowClient

    client = MlflowClient(tracking_uri=tracking_uri)
    run = client.get_run(run_id)
    assert run.data.tags["haute.contract_version"] == "1"
    for tag in (
        "haute.job_id",
        "haute.node_label",
        "haute.trained_at",
        "haute.version",
        "haute.algorithm",
        "haute.task",
        "haute.target",
        "haute.evaluation_plan_sha256",
        "haute.training_identity_sha256",
    ):
        assert run.data.tags.get(tag), f"candidate tag {tag} missing"
    # Outside a git repository the git tags are absent, never guessed.
    assert "haute.git_commit" not in run.data.tags
    assert run.info.run_name.endswith(" UTC") and " · " in run.info.run_name
    # These results have no final test, so every metric is a development diagnostic.
    assert run.data.metrics
    assert all(
        name.startswith(("development_", "selection_", "tuning_", "glm_"))
        for name in run.data.metrics
    ), sorted(run.data.metrics)
    root = {Path(item.path).name for item in client.list_artifacts(run_id)}
    stem = Path(model_file).stem
    assert {model_file, model_contract_filename(stem), "evaluation", "model_card"} <= root, root
    evaluation = {Path(item.path).name for item in client.list_artifacts(run_id, "evaluation")}
    assert evaluation == set(evaluation_artifact_filenames(stem).values()), evaluation


def _signature_inputs(model_metadata: Any) -> list[tuple[str, str]]:
    signature = model_metadata.signature
    assert signature is not None, "logged model carries no signature"
    return [(col["name"], col["type"]) for col in signature.inputs.to_dict()]


# ---------------------------------------------------------------------------
# CatBoost: signature truth + logged-then-reloaded scoring
# ---------------------------------------------------------------------------


class TestCatboostButtonRoundTrip:
    def test_signature_matches_contract_and_reloaded_model_scores(
        self, client, local_mlflow: Path
    ) -> None:
        """RED pre-fix: the button logged ``c`` (a string categorical) as
        ``double``; ``mlflow.pyfunc.load_model(...).predict`` then rejects the
        very frame the model was trained on.  GREEN: the signature mirrors the
        feature contract and the reloaded model scores it, matching the
        native model exactly."""
        model_path, native_model = _train_catboost(local_mlflow)
        result = _completed_result(str(model_path))
        config = {"algorithm": "catboost", "task": "regression", "target": TARGET}

        with _seeded_job("btn_cb", result, config, node_label="freq"):
            resp = client.post(
                "/api/modelling/mlflow/log",
                json={"job_id": "btn_cb", "experiment_name": "button_roundtrip_cb"},
            )
        assert resp.status_code == 200, resp.text
        run_id = resp.json()["run_id"]
        tracking_uri = resp.json()["tracking_uri"]
        _assert_candidate_run_contract(tracking_uri, run_id, model_file="freq.cbm")
        from haute._mlflow_io import _load_pyfunc_model
        from haute._mlflow_utils import resolve_backend

        # The route logged to the local folder (no destination named), so
        # resolving the empty destination again yields the backend the run lives in.
        backend = resolve_backend("")
        assert backend.tracking_uri == tracking_uri
        previous_tracking_uri = mlflow.get_tracking_uri()
        previous_registry_uri = mlflow.get_registry_uri()
        loaded = _load_pyfunc_model(mlflow, run_id, "model", backend=backend)
        assert mlflow.get_tracking_uri() == previous_tracking_uri
        assert mlflow.get_registry_uri() == previous_registry_uri

        # The signature must describe what the model actually consumes —
        # the categorical feature is a string, not a double.
        assert _signature_inputs(loaded.metadata) == [("x", "double"), ("c", "string")]

        # Logged-then-reloaded model must score a native-dtype frame.
        score_frame = pl.DataFrame({"x": [0.1, 0.9], "c": ["red", "blue"]}).to_pandas()
        preds = np.asarray(loaded.predict(score_frame)).ravel()
        native = np.asarray(native_model.predict(score_frame)).ravel()
        np.testing.assert_allclose(preds, native, rtol=1e-9)

    def test_classifier_logs_scores_and_refuses_the_regression_task(
        self, client, local_mlflow: Path
    ) -> None:
        """A logged CatBoost classifier loads through MLflow and through haute's
        loader with probabilities equal to the native model's, and haute refuses
        to score it as a regressor (which would silently return log-odds)."""
        from catboost import CatBoostClassifier

        from haute._mlflow_io import load_mlflow_model
        from haute.errors import ConfigError

        df = _training_frame()
        labels = (df["y"].to_numpy() > float(np.median(df["y"].to_numpy()))).astype(int)
        native_model = CatBoostClassifier(
            iterations=8,
            depth=2,
            verbose=0,
            allow_writing_files=False,
            cat_features=CAT_FEATURES,
            random_seed=0,
        )
        native_model.fit(df.select(FEATURES).to_pandas(), labels)
        model_path = local_mlflow / "conv.cbm"
        native_model.save_model(str(model_path))
        save_contract(
            build_contract(
                features=FEATURES,
                feature_types={"x": "Float64", "c": "String"},
                categorical_features=CAT_FEATURES,
                target_name=TARGET,
                target_type="Int64",
                task="classification",
            ),
            model_path.parent / model_contract_filename(model_path.stem),
        )
        result = _completed_result(str(model_path))
        config = {"algorithm": "catboost", "task": "classification", "target": TARGET}

        with _seeded_job("btn_cls", result, config, node_label="conv"):
            resp = client.post(
                "/api/modelling/mlflow/log",
                json={"job_id": "btn_cls", "experiment_name": "button_roundtrip_cls"},
            )
        assert resp.status_code == 200, resp.text
        run_id = resp.json()["run_id"]
        tracking_uri = resp.json()["tracking_uri"]
        client_api = mlflow.tracking.MlflowClient(tracking_uri=tracking_uri)
        assert client_api.get_run(run_id).data.params["task"] == "classification"

        score_frame = pl.DataFrame({"x": [0.1, 0.9], "c": ["red", "blue"]}).to_pandas()
        native = np.asarray(native_model.predict_proba(score_frame))

        haute_model = load_mlflow_model(source_type="run", run_id=run_id, task="classification")
        np.testing.assert_allclose(haute_model.predict_proba(score_frame), native, rtol=1e-9)

        with mlflow_fluent_operation():
            mlflow.set_tracking_uri(tracking_uri)
            reloaded = mlflow.catboost.load_model(f"runs:/{run_id}/model")
        np.testing.assert_allclose(reloaded.predict_proba(score_frame), native, rtol=1e-9)

        with pytest.raises(ConfigError, match="trained for classification"):
            load_mlflow_model(source_type="run", run_id=run_id, task="regression")


# ---------------------------------------------------------------------------
# GLM: signature truth + GLM artifacts no longer dropped + native scoring
# ---------------------------------------------------------------------------


class TestGlmButtonRoundTrip:
    def test_glm_artifacts_signature_and_reload_scoring(self, client, local_mlflow: Path) -> None:
        """RED pre-fix: the route dropped all four ``glm_*`` diagnostics
        (never logged) and signed the categorical as double.  GREEN: glm/
        artifacts present, fit statistics logged as metrics, the signature
        matches the contract, and the native ``.rsglm`` artifact reloads and
        scores identically to the in-memory model."""
        model_path, glm_model, algo = _train_glm(local_mlflow)
        result = _completed_result(
            str(model_path),
            glm_coefficients=[{"feature": "x", "coefficient": 1.2}],
            glm_relativities=[{"feature": "c", "level": "red", "relativity": 1.5}],
            glm_fit_statistics={
                "aic": 101.5,
                "bic": 110.25,
                "deviance": 50.5,
                "null_deviance": 200.0,
            },
            glm_regularization_path={"selected_alpha": 0.1, "n_nonzero": 3},
        )
        config = {"algorithm": "glm", "task": "regression", "target": TARGET}

        with _seeded_job("btn_glm", result, config, node_label="sev"):
            resp = client.post(
                "/api/modelling/mlflow/log",
                json={"job_id": "btn_glm", "experiment_name": "button_roundtrip_glm"},
            )
        assert resp.status_code == 200, resp.text
        run_id = resp.json()["run_id"]
        tracking_uri = resp.json()["tracking_uri"]
        _assert_candidate_run_contract(tracking_uri, run_id, model_file="sev.rsglm")

        from mlflow.tracking import MlflowClient

        mlflow_client = MlflowClient(tracking_uri=tracking_uri, registry_uri=tracking_uri)

        # GLM diagnostics artifacts must be logged (previously dropped).
        glm_artifact_names = [
            Path(f.path).name for f in mlflow_client.list_artifacts(run_id, "glm")
        ]
        for prefix in (
            "glm_coefficients",
            "glm_relativities",
            "glm_fit_statistics",
            "glm_regularization_path",
        ):
            assert any(name.startswith(prefix) for name in glm_artifact_names), (
                f"GLM artifact {prefix!r} missing from run; got {glm_artifact_names}"
            )

        # Key GLM fit statistics are logged under the candidate-run glm_ namespace.
        run_metrics = mlflow_client.get_run(run_id).data.metrics
        for key in ("aic", "bic", "deviance", "null_deviance"):
            assert f"glm_{key}" in run_metrics, f"GLM stat {key!r} not logged as metric"

        # Inspect signature metadata through the pinned logged-model record;
        # the native GLM reload and scoring contract is checked below.
        logged_models = mlflow_client.search_logged_models(
            experiment_ids=[mlflow_client.get_run(run_id).info.experiment_id]
        )
        assert len(logged_models) == 1
        logged_model = logged_models[0]
        assert logged_model.source_run_id == run_id
        assert logged_model.name == "model"
        local_model_path = mlflow.artifacts.download_artifacts(
            artifact_uri=logged_model.artifact_location,
            tracking_uri=tracking_uri,
            dst_path=str(local_mlflow / "glm_model"),
        )
        assert _signature_inputs(mlflow.models.Model.load(local_model_path)) == [
            ("x", "double"),
            ("c", "string"),
        ]

        # The native .rsglm is at the run root, reloads, and scores
        # identically to the in-memory model.
        top_level = [Path(f.path).name for f in mlflow_client.list_artifacts(run_id)]
        assert "sev.rsglm" in top_level

        import rustystats as rs

        downloaded = mlflow.artifacts.download_artifacts(
            artifact_uri=f"runs:/{run_id}/sev.rsglm",
            tracking_uri=tracking_uri,
            dst_path=str(local_mlflow / "dl"),
        )
        with open(downloaded, "rb") as f:
            reloaded = rs.GLMModel.from_bytes(f.read())

        score_df = _training_frame(n=10)
        reloaded_preds = np.asarray(reloaded.predict(score_df.select(FEATURES))).flatten()
        original_preds = algo.predict(glm_model, score_df, FEATURES)
        assert np.all(np.isfinite(reloaded_preds))
        np.testing.assert_allclose(reloaded_preds, original_preds, rtol=1e-9)

        # Outside haute, MLflow's own pyfunc loader scores the logged GLM through
        # haute's GLM path and predicts exactly what the native model does.
        with mlflow_fluent_operation():
            mlflow.set_tracking_uri(tracking_uri)
            pyfunc_model = mlflow.pyfunc.load_model(f"runs:/{run_id}/model")
        pyfunc_preds = np.asarray(
            pyfunc_model.predict(score_df.select(FEATURES).to_pandas())
        ).flatten()
        np.testing.assert_allclose(pyfunc_preds, original_preds, rtol=1e-9)


# ---------------------------------------------------------------------------
# Route construction unit-level: contract metadata + GLM diagnostics pass-through
# ---------------------------------------------------------------------------


class TestButtonLogConstruction:
    def test_passes_contract_metadata_and_glm_diagnostics_to_log_experiment(
        self, client, local_mlflow: Path
    ) -> None:
        """Pin the exact kwargs the route hands ``log_experiment``: signature
        metadata from the feature contract, GLM diagnostics from the result."""
        from haute.modelling._mlflow_log import MLflowLogResult

        model_path, _, _ = _train_glm(local_mlflow)
        glm_stats = {"aic": 1.0, "bic": 2.0, "deviance": 3.0, "null_deviance": 4.0}
        result = _completed_result(
            str(model_path),
            glm_coefficients=[{"feature": "x", "coefficient": 1.2}],
            glm_relativities=[{"feature": "c", "relativity": 1.5}],
            glm_fit_statistics=glm_stats,
            glm_regularization_path={"selected_alpha": 0.1, "n_nonzero": 3},
        )
        config = {"algorithm": "glm", "task": "regression", "target": TARGET}
        fake = MLflowLogResult(
            backend="local",
            experiment_name="e",
            run_id="r",
            tracking_uri="file:///x",
            run_url=None,
        )

        with (
            _seeded_job("btn_kwargs", result, config),
            patch("haute.modelling._mlflow_log.log_experiment", return_value=fake) as m_log,
        ):
            resp = client.post("/api/modelling/mlflow/log", json={"job_id": "btn_kwargs"})
        assert resp.status_code == 200, resp.text

        candidate = m_log.call_args.kwargs["candidate"]
        meta = candidate.metadata
        assert meta.features == FEATURES
        assert meta.feature_types == {"x": "Float64", "c": "String"}
        assert meta.categorical_features == CAT_FEATURES
        assert meta.target_name == TARGET
        assert meta.target_type == "Float64"

        diag = candidate.diagnostics
        assert diag.glm_coefficients == result.glm_coefficients
        assert diag.glm_relativities == result.glm_relativities
        assert diag.glm_fit_statistics == glm_stats
        assert diag.glm_regularization_path == {"selected_alpha": 0.1, "n_nonzero": 3}

    def test_reads_per_model_contract_not_stale_shared_one(
        self, client, local_mlflow: Path
    ) -> None:
        """4b.8 x 4b.9 interaction: with a leftover SHARED
        ``feature_contract.json`` (pre-4b.9, describing some other model) in
        the same directory, the button must use this model's per-model
        contract — never the stale shared file."""
        from haute.modelling._mlflow_log import MLflowLogResult

        model_path, _, _ = _train_glm(local_mlflow)
        stale = build_contract(
            features=["wrong_feature"],
            feature_types={"wrong_feature": "Int64"},
            categorical_features=[],
            target_name="other_target",
            target_type="Int64",
            task="regression",
        )
        from haute.modelling._feature_contract import CONTRACT_FILENAME

        save_contract(stale, model_path.parent / CONTRACT_FILENAME)

        result = _completed_result(str(model_path))
        from haute.routes.modelling import _store

        original_publish = publish_trained_job

        def publish_with_stale_shared_contract(*args, **kwargs):
            output = original_publish(*args, **kwargs)
            save_contract(stale, output / CONTRACT_FILENAME)
            return output

        del _store
        config = {"algorithm": "glm", "task": "regression", "target": TARGET}
        fake = MLflowLogResult(
            backend="local",
            experiment_name="e",
            run_id="r",
            tracking_uri="file:///x",
            run_url=None,
        )

        with (
            patch(
                f"{__name__}.publish_trained_job", side_effect=publish_with_stale_shared_contract
            ),
            _seeded_job("btn_permodel", result, config),
            patch("haute.modelling._mlflow_log.log_experiment", return_value=fake) as m_log,
        ):
            resp = client.post("/api/modelling/mlflow/log", json={"job_id": "btn_permodel"})
        assert resp.status_code == 200, resp.text

        meta = m_log.call_args.kwargs["candidate"].metadata
        assert meta.features == FEATURES
        assert meta.feature_types == {"x": "Float64", "c": "String"}
        assert meta.target_name == TARGET

    def test_model_without_contract_fails_loudly(self, client, local_mlflow: Path) -> None:
        """A trained model whose feature contract is missing is never logged with a
        fabricated signature: the export reports the artifacts unavailable and no
        run is created."""
        model_path = local_mlflow / "orphan.cbm"
        model_path.write_bytes(b"fake-cbm")  # file exists, contract does not
        result = _completed_result(str(model_path))
        config = {"algorithm": "catboost", "task": "regression", "target": TARGET}

        with (
            _seeded_job("btn_nocontract", result, config),
            patch("haute.modelling._mlflow_log.log_experiment") as m_log,
        ):
            resp = client.post("/api/modelling/mlflow/log", json={"job_id": "btn_nocontract"})
        assert resp.status_code == 410
        assert resp.json()["detail"]["error_code"] == "training_artifacts_unavailable"
        assert str(local_mlflow) not in str(resp.json()["detail"])
        m_log.assert_not_called()


# ---------------------------------------------------------------------------
# Destination-aware logging (Task A4)
# ---------------------------------------------------------------------------


class TestDestinationIsAuthoritative:
    """The request's ``destination`` decides where the run goes; the job's
    training-time ``mlflow_destination`` snapshot is never consulted."""

    def _seed(
        self,
        model_dir: Path,
        job_id: str,
        config_extra: dict[str, Any] | None = None,
    ):
        model_path, _ = _train_catboost(model_dir)
        result = _completed_result(str(model_path))
        config = {
            "algorithm": "catboost",
            "task": "regression",
            "target": TARGET,
            **(config_extra or {}),
        }
        return _seeded_job(job_id, result, config, node_label="freq")

    def test_omitted_and_empty_go_to_the_local_folder_and_key_goes_to_its_own(
        self, client, local_mlflow: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from mlflow.tracking import MlflowClient

        # No destination named -> the local folder.
        # Seed a job whose config snapshot says mlflow_destination="server".
        # 1) omitted destination -> 200, backend == "local", tracking_uri startswith file:
        with self._seed(local_mlflow, "job_dest_omitted", {"mlflow_destination": "server"}):
            resp = client.post(
                "/api/modelling/mlflow/log",
                json={"job_id": "job_dest_omitted", "experiment_name": "exp_dest_test"},
            )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["backend"] == "local"
        assert data["tracking_uri"].startswith("file:")

        # 2) destination "" -> same
        with self._seed(local_mlflow, "job_dest_empty", {"mlflow_destination": "server"}):
            resp = client.post(
                "/api/modelling/mlflow/log",
                json={
                    "job_id": "job_dest_empty",
                    "destination": "",
                    "experiment_name": "exp_dest_test",
                },
            )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["backend"] == "local"
        assert data["tracking_uri"].startswith("file:")

        # 3) destination "local" -> same
        with self._seed(local_mlflow, "job_dest_local", {"mlflow_destination": "server"}):
            resp = client.post(
                "/api/modelling/mlflow/log",
                json={
                    "job_id": "job_dest_local",
                    "destination": "local",
                    "experiment_name": "exp_dest_test",
                },
            )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["backend"] == "local"
        assert data["tracking_uri"].startswith("file:")

        # Count runs before unconfigured server attempt
        client_mlflow = MlflowClient(tracking_uri=(local_mlflow / "mlruns").as_uri())
        exp = client_mlflow.get_experiment_by_name("exp_dest_test")
        assert exp is not None
        runs_before = len(client_mlflow.search_runs([exp.experiment_id]))

        # 4) destination "server" (unconfigured) -> 400, "MLflow server is not
        # configured" in detail, and the local store gained no new run.
        with self._seed(local_mlflow, "job_dest_server", {"mlflow_destination": "server"}):
            resp = client.post(
                "/api/modelling/mlflow/log",
                json={
                    "job_id": "job_dest_server",
                    "destination": "server",
                    "experiment_name": "exp_dest_test",
                },
            )
        assert resp.status_code == 400, resp.text
        assert "MLflow server is not configured" in resp.json()["detail"]
        runs_after = len(client_mlflow.search_runs([exp.experiment_id]))
        assert runs_after == runs_before

    def test_second_destination_is_distinct(
        self, client, local_mlflow: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from mlflow.tracking import MlflowClient

        from haute.modelling._mlflow_settings import TrackingConfig

        server_runs_dir = tmp_path / "server-runs"
        server_config = TrackingConfig(
            "server", server_runs_dir.as_uri(), "http://stub:5000", "toml"
        )
        monkeypatch.setattr(
            "haute.modelling._mlflow_settings._resolve_server",
            lambda stored=None: server_config,
        )

        with self._seed(local_mlflow, "job_dest_server_distinct"):
            resp = client.post(
                "/api/modelling/mlflow/log",
                json={
                    "job_id": "job_dest_server_distinct",
                    "destination": "server",
                    "experiment_name": "srv_exp",
                },
            )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["backend"] == "server"
        assert data["tracking_uri"] == server_runs_dir.as_uri()

        server_client = MlflowClient(tracking_uri=server_runs_dir.as_uri())
        exp = server_client.get_experiment_by_name("srv_exp")
        assert exp is not None
        assert len(server_client.search_runs([exp.experiment_id])) == 1

        with self._seed(local_mlflow, "job_dest_local_distinct"):
            resp = client.post(
                "/api/modelling/mlflow/log",
                json={
                    "job_id": "job_dest_local_distinct",
                    "destination": "local",
                    "experiment_name": "loc_exp",
                },
            )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["backend"] == "local"
        assert data["tracking_uri"] == (local_mlflow / "mlruns").as_uri()

        local_client = MlflowClient(tracking_uri=(local_mlflow / "mlruns").as_uri())
        loc_exp = local_client.get_experiment_by_name("loc_exp")
        assert loc_exp is not None
        assert len(local_client.search_runs([loc_exp.experiment_id])) == 1

    def test_unknown_destination_is_422_before_any_write(self, client, local_mlflow: Path) -> None:
        resp = client.post(
            "/api/modelling/mlflow/log",
            json={"job_id": "x", "destination": "managed"},
        )
        assert resp.status_code == 422

    def test_chosen_databricks_fails_loudly_when_rejected_but_unchosen_logs_locally(
        self, client, local_mlflow: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MLFLOW_TRACKING_URI", "databricks://team")
        monkeypatch.setenv("MLFLOW_ENABLE_DB_SDK", "true")

        with self._seed(local_mlflow, "job_dest_sdk"):
            resp = client.post(
                "/api/modelling/mlflow/log",
                json={"job_id": "job_dest_sdk", "destination": "databricks"},
            )
        assert resp.status_code == 400, resp.text
        assert "MLFLOW_ENABLE_DB_SDK" in resp.json()["detail"]

        with self._seed(local_mlflow, "job_dest_sdk_local"):
            resp = client.post(
                "/api/modelling/mlflow/log",
                json={
                    "job_id": "job_dest_sdk_local",
                    "destination": "",
                    "experiment_name": "sdk_local_exp",
                },
            )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["backend"] == "local"
        from mlflow.tracking import MlflowClient

        client_mlflow = MlflowClient(tracking_uri=(local_mlflow / "mlruns").as_uri())
        exp = client_mlflow.get_experiment_by_name("sdk_local_exp")
        assert exp is not None
        assert len(client_mlflow.search_runs([exp.experiment_id])) == 1
