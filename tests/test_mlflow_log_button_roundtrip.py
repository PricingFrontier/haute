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

import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import patch

import numpy as np
import polars as pl
import pytest

# Budgeted debt site: needs the real mlflow package (the ``databricks``
# extra).  Main CI installs it via the dev group; core-only installs skip.
mlflow = pytest.importorskip(
    "mlflow",
    reason="mlflow optional dependency (databricks extra) not installed",
)

from haute.modelling._feature_contract import (  # noqa: E402 — after importorskip by design
    build_contract,
    save_contract,
)
from haute.modelling._training_job import model_contract_filename  # noqa: E402
from haute.schemas import TrainResponse  # noqa: E402

FEATURES = ["x", "c"]
CAT_FEATURES = ["c"]
TARGET = "y"


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def local_mlflow(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Path]:
    """Local file-store MLflow rooted in tmp; resets the tracking URI after."""
    monkeypatch.delenv("DATABRICKS_HOST", raising=False)
    monkeypatch.delenv("DATABRICKS_TOKEN", raising=False)
    monkeypatch.chdir(tmp_path)
    yield tmp_path
    # Reset the process-wide tracking URI mlflow picked up during the run so
    # other tests don't inherit our temp file-store.
    mlflow.set_tracking_uri("")


@contextmanager
def _seeded_job(
    job_id: str,
    result: TrainResponse,
    config: dict[str, Any],
    node_label: str = "model",
) -> Iterator[None]:
    """Inject a completed training job into the route's job store."""
    from haute.routes.modelling import _store

    _store.jobs[job_id] = {
        "status": "completed",
        "result": result,
        "config": config,
        "node_label": node_label,
        "created_at": time.time(),
    }
    try:
        yield
    finally:
        _store.jobs.pop(job_id, None)


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
    base: dict[str, Any] = {
        "status": "completed",
        "metrics": {"rmse": 0.1, "gini": 0.5},
        "model_path": model_path,
        "train_rows": 60,
        "test_rows": 20,
        "features": FEATURES,
        "cat_features": CAT_FEATURES,
    }
    base.update(overrides)
    return TrainResponse(**base)


def _signature_inputs(run_id: str) -> list[tuple[str, str]]:
    info = mlflow.models.get_model_info(f"runs:/{run_id}/model")
    assert info.signature is not None, "logged model carries no signature"
    return [(col["name"], col["type"]) for col in info.signature.inputs.to_dict()]


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

        # The signature must describe what the model actually consumes —
        # the categorical feature is a string, not a double.
        assert _signature_inputs(run_id) == [("x", "double"), ("c", "string")]

        # Logged-then-reloaded model must score a native-dtype frame.
        score_frame = pl.DataFrame({"x": [0.1, 0.9], "c": ["red", "blue"]}).to_pandas()
        loaded = mlflow.pyfunc.load_model(f"runs:/{run_id}/model")
        preds = np.asarray(loaded.predict(score_frame)).ravel()
        native = np.asarray(native_model.predict(score_frame)).ravel()
        np.testing.assert_allclose(preds, native, rtol=1e-9)


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

        from mlflow.tracking import MlflowClient

        mlflow_client = MlflowClient()

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

        # Key GLM fit statistics must be logged as top-level metrics.
        run_metrics = mlflow_client.get_run(run_id).data.metrics
        for key in ("aic", "bic", "deviance", "null_deviance"):
            assert key in run_metrics, f"GLM stat {key!r} not logged as metric"

        # Signature mirrors the feature contract (string categorical).
        assert _signature_inputs(run_id) == [("x", "double"), ("c", "string")]

        # The native .rsglm is at the run root, reloads, and scores
        # identically to the in-memory model.
        top_level = [Path(f.path).name for f in mlflow_client.list_artifacts(run_id)]
        assert "sev.rsglm" in top_level

        import rustystats as rs

        downloaded = mlflow.artifacts.download_artifacts(
            run_id=run_id,
            artifact_path="sev.rsglm",
            dst_path=str(local_mlflow / "dl"),
        )
        with open(downloaded, "rb") as f:
            reloaded = rs.GLMModel.from_bytes(f.read())

        score_df = _training_frame(n=10)
        reloaded_preds = np.asarray(reloaded.predict(score_df.select(FEATURES))).flatten()
        original_preds = algo.predict(glm_model, score_df, FEATURES)
        assert np.all(np.isfinite(reloaded_preds))
        np.testing.assert_allclose(reloaded_preds, original_preds, rtol=1e-9)


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

        kwargs = m_log.call_args.kwargs
        meta = kwargs["metadata"]
        assert meta.features == FEATURES
        assert meta.feature_types == {"x": "Float64", "c": "String"}
        assert meta.categorical_features == CAT_FEATURES
        assert meta.target_name == TARGET
        assert meta.target_type == "Float64"

        diag = kwargs["diagnostics"]
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
        config = {"algorithm": "glm", "task": "regression", "target": TARGET}
        fake = MLflowLogResult(
            backend="local",
            experiment_name="e",
            run_id="r",
            tracking_uri="file:///x",
            run_url=None,
        )

        with (
            _seeded_job("btn_permodel", result, config),
            patch("haute.modelling._mlflow_log.log_experiment", return_value=fake) as m_log,
        ):
            resp = client.post("/api/modelling/mlflow/log", json={"job_id": "btn_permodel"})
        assert resp.status_code == 200, resp.text

        meta = m_log.call_args.kwargs["metadata"]
        assert meta.features == FEATURES
        assert meta.feature_types == {"x": "Float64", "c": "String"}
        assert meta.target_name == TARGET

    def test_model_without_contract_fails_loudly(self, client, local_mlflow: Path) -> None:
        """A model file with NO feature contract must not be logged with a
        fabricated signature — the request fails loudly instead, and the
        server-side error names the missing contract."""
        import structlog

        model_path = local_mlflow / "orphan.cbm"
        model_path.write_bytes(b"fake-cbm")  # file exists, contract does not
        result = _completed_result(str(model_path))
        config = {"algorithm": "catboost", "task": "regression", "target": TARGET}

        with (
            _seeded_job("btn_nocontract", result, config),
            structlog.testing.capture_logs() as logs,
        ):
            resp = client.post("/api/modelling/mlflow/log", json={"job_id": "btn_nocontract"})
        assert resp.status_code == 500
        # Sanitized detail — no internal paths leak to the client.
        assert "feature_contract" not in resp.json()["detail"]
        assert str(local_mlflow) not in resp.json()["detail"]
        # The server-side log names the real problem: the missing contract.
        failures = [log for log in logs if log["event"] == "mlflow_log_failed"]
        assert failures, f"expected an mlflow_log_failed log entry, got {logs}"
        assert "feature_contract" in failures[0]["error"]

    def test_metrics_only_job_logs_without_contract(self, client, local_mlflow: Path) -> None:
        """No model artifact → no signature needed → the contract is not
        required and metrics still reach MLflow."""
        result = _completed_result("", metrics={"rmse": 0.25})
        config = {"algorithm": "catboost", "task": "regression", "target": TARGET}

        with _seeded_job("btn_nomodel", result, config):
            resp = client.post(
                "/api/modelling/mlflow/log",
                json={"job_id": "btn_nomodel", "experiment_name": "button_metrics_only"},
            )
        assert resp.status_code == 200, resp.text
        run_id = resp.json()["run_id"]

        from mlflow.tracking import MlflowClient

        run_metrics = MlflowClient().get_run(run_id).data.metrics
        assert run_metrics["rmse"] == pytest.approx(0.25)
