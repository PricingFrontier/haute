"""Modelling correctness: the train <-> score contract.

* A reordered input scores as the training order does (features by name)
* MLflow signature is logged and round-trips through the feature contract
* Categorical type mismatch raises FeatureMismatchError (no silent cast)
* GLM column selection preserves categorical metadata across save/load
* Diagnostic exceptions split into mandatory (fail) vs optional (surface flag)
* A classifier's class order, positive class and string labels survive
  training, the exported script, the training identity and MLflow
* A contract describing another loss or algorithm fails at load

Model-score column detection and artifact delete-and-retry live in
``test_modelling_loud_errors.py``.  These pin the wiring between the training
pipeline, the scorer, and MLflow.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import numpy as np
import polars as pl
import pytest

from haute.errors import ConfigError, FeatureMismatchError, HauteValidationError
from haute.modelling._algorithms import CatBoostAlgorithm
from haute.modelling._candidate_run import training_identity_sha256
from haute.modelling._export import generate_training_script
from haute.modelling._feature_contract import (
    CONTRACT_FILENAME,
    ModelIdentity,
    assert_contracts_match,
    build_contract,
    load_contract,
    save_contract,
)
from haute.modelling._train_config import TrainingConfigError, build_training_job_kwargs
from haute.modelling._training_job import TrainingJob
from tests._project import use_project

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mixed_train_df() -> pl.DataFrame:
    """Synthetic dataset with two numeric + one categorical feature.

    Sized generously enough (500 rows) that downstream diagnostics like
    ``LossFunctionChange`` don't hit numerical edge cases on the CatBoost
    side — we care about the signature/ordering contract, not fighting
    the native stats code.
    """
    rng = np.random.RandomState(2026_04_17)
    n = 500
    return pl.DataFrame(
        {
            "age": rng.randint(18, 80, n).astype(np.float64),
            "vehicle_value": rng.uniform(1000, 40_000, n),
            "region": rng.choice(["north", "south", "east", "west"], n),
            "ClaimCount": (rng.poisson(0.5, n)).astype(np.float64),
            "Exposure": np.ones(n),
        }
    )


def _train_tiny_catboost(
    df: pl.DataFrame,
    *,
    features: list[str],
    cat_features: list[str],
    target: str = "ClaimCount",
    weight: str = "Exposure",
    iterations: int = 1,
) -> Any:
    """Train a tiny CatBoost model with an explicit feature order."""
    pytest.importorskip("catboost", reason="catboost not installed")
    from haute.modelling._algorithms import CatBoostAlgorithm

    algo = CatBoostAlgorithm()
    fit_result = algo.fit(
        df,
        features=features,
        cat_features=cat_features,
        target=target,
        weight=weight,
        params={"iterations": iterations, "depth": 1, "verbose": 0},
        task="regression",
    )
    return fit_result.model


# ===========================================================================
# Item #1 — Feature / categorical order between training and scoring
# ===========================================================================


class TestFeatureOrderAtScore:
    """Training features ordered one way, score input ordered another way.

    Every scoring path selects the model's features by name, in the model's
    own order, before predicting, so a reordered input scores exactly as the
    training order does: CatBoost's positional categorical indices still
    describe the same columns.
    """

    def test_reordered_features_score_as_in_training_order(
        self, mixed_train_df: pl.DataFrame
    ) -> None:
        pytest.importorskip("catboost", reason="catboost optional dependency not installed")
        from haute._mlflow_io import ScoringModel, _wrap_catboost

        # Train with a deliberate, non-alphabetic feature order.
        train_features = ["region", "age", "vehicle_value"]
        model = _train_tiny_catboost(
            mixed_train_df,
            features=train_features,
            cat_features=["region"],
        )
        scoring_model: ScoringModel = _wrap_catboost(model, source="test model")
        assert list(scoring_model.feature_names) == train_features, (
            "Pre-check: CatBoost must remember training feature order"
        )

        rows = mixed_train_df.head(5)
        assert _predictions(scoring_model, rows.select(["age", "vehicle_value", "region"])) == (
            _predictions(scoring_model, rows.select(train_features))
        )

    def test_a_moved_categorical_column_scores_as_in_training_order(
        self, mixed_train_df: pl.DataFrame
    ) -> None:
        """``region`` arriving last instead of first keeps its categorical index."""
        pytest.importorskip("catboost", reason="catboost optional dependency not installed")
        from haute._mlflow_io import _wrap_catboost

        train_features = ["region", "age", "vehicle_value"]
        model = _train_tiny_catboost(
            mixed_train_df,
            features=train_features,
            cat_features=["region"],
        )
        scoring_model = _wrap_catboost(model, source="test model")

        rows = mixed_train_df.head(4)
        assert _predictions(scoring_model, rows.select(["vehicle_value", "age", "region"])) == (
            _predictions(scoring_model, rows.select(train_features))
        )


def _predictions(scoring_model: Any, rows: pl.DataFrame) -> list[float]:
    """Score *rows* through the shared scoring pipeline, as every path does."""
    from haute._model_scorer import _run_score_pipeline

    scored = _run_score_pipeline(
        scoring_model, rows.lazy(), task="regression", output_col="pred", source="live"
    )
    frame = scored.collect() if isinstance(scored, pl.LazyFrame) else scored
    return frame["pred"].to_list()


# ===========================================================================
# Item #2 — MLflow signature is logged with the training feature contract
# ===========================================================================


class TestMLflowSignatureLogged:
    """``log_experiment`` / training must attach a ModelSignature so that
    loading the logged model via ``mlflow.models.get_model_info`` yields
    a signature that agrees with the training feature contract.
    """

    def test_training_attaches_signature_matching_contract(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """End-to-end: ``TrainingJob.run()`` with mlflow_experiment set must
        produce an MLflow run whose attached signature's input columns are
        the training features, in training order, with matching dtypes
        (i.e. the signature is consistent with the feature contract).

        Uses a numeric-only synth dataset to avoid tangling with
        CatBoost's native LossFunctionChange on categorical pools — the
        property being tested is the signature→feature-order contract,
        not the diagnostic stack.
        """
        pytest.importorskip("catboost", reason="catboost optional dependency not installed")
        pytest.importorskip("mlflow", reason="core mlflow dependency is unavailable")
        monkeypatch.delenv("DATABRICKS_HOST", raising=False)
        monkeypatch.delenv("DATABRICKS_TOKEN", raising=False)
        monkeypatch.delenv("DATABRICKS_MLFLOW_HOST", raising=False)
        monkeypatch.delenv("DATABRICKS_MLFLOW_TOKEN", raising=False)
        monkeypatch.delattr("haute.modelling._algorithms.CatBoostAlgorithm.shap_values")
        monkeypatch.setattr(
            "haute.modelling._algorithms.CatBoostAlgorithm.feature_importance_typed",
            lambda *a, **kw: [],
        )
        monkeypatch.setattr("haute.modelling._metrics.compute_pdp", lambda *a, **kw: [])

        from haute.modelling._signature import build_signature
        from haute.modelling._training_job import TrainingJob

        rng = np.random.RandomState(2026)
        n = 400
        synth = pl.DataFrame(
            {
                "age": rng.randn(n),
                "vehicle_value": rng.randn(n) * 10,
                "ClaimCount": (rng.randn(n) + 1).clip(0),
                "Exposure": np.ones(n),
            }
        )

        captured: dict[str, Any] = {}

        def _capture(*args: Any, **kwargs: Any) -> None:
            captured.update(kwargs)

        mock_run = MagicMock()
        mock_run.info.run_id = "run_sig_2"

        with (
            patch("mlflow.set_tracking_uri"),
            patch("mlflow.set_registry_uri"),
            patch("mlflow.set_experiment"),
            patch("mlflow.start_run") as m_run,
            patch("mlflow.log_params"),
            patch("mlflow.log_metrics"),
            patch("mlflow.log_artifact"),
            patch("mlflow.catboost.log_model", side_effect=_capture),
            patch("mlflow.pyfunc.log_model", side_effect=_capture),
        ):
            m_run.return_value.__enter__ = MagicMock(return_value=mock_run)
            m_run.return_value.__exit__ = MagicMock(return_value=False)

            job = TrainingJob(
                name="sig_model",
                data=synth,
                target="ClaimCount",
                weight="Exposure",
                params={"iterations": 1, "depth": 1, "verbose": 0},
                mlflow_experiment="/Shared/haute/sig_model",
                evaluation={
                    "schema_version": 1,
                    "strategy": "random",
                    "seed": 7,
                    "validation": {"method": "single", "size": 0.2},
                },
                output_dir=str(tmp_path),
            )
            result = job.run()

        # Build the expected signature from the training contract.
        feature_types = {f: str(synth[f].dtype) for f in result.features}
        expected_sig = build_signature(
            features=result.features,
            feature_types=feature_types,
            categorical_features=result.cat_features,
            target_name="ClaimCount",
            target_type="Float64",
            task="regression",
        )
        expected_input_names = [c.name for c in expected_sig.inputs.inputs]

        # The training job must have passed a signature to log_model.
        # If not, training silently logs a model without its feature contract —
        # deploy-time mismatches become invisible.
        sig = captured.get("signature")
        assert sig is not None, (
            "TrainingJob.run() with mlflow_experiment set must attach a "
            "ModelSignature to the logged model"
        )
        actual_input_names = [c.name for c in sig.inputs.inputs]
        assert actual_input_names == expected_input_names, (
            f"Signature input order {actual_input_names} does not match "
            f"training feature order {expected_input_names}"
        )


# ===========================================================================
# Item #13 — Categorical type mismatch: warning-only is not enough
# ===========================================================================


class TestCategoricalTypeMismatchRaises:
    """The current ``_validate_features`` logs a warning when a categorical
    is typed differently at score time.  Fail-loud policy: this must raise.
    """

    def test_int_for_string_cat_raises(self) -> None:
        """Training used String; scoring passes Int64 — must raise."""
        from haute._mlflow_io import ScoringModel
        from haute._model_scorer import _validate_features

        model = MagicMock()
        sm = ScoringModel(
            model=model,
            feature_names=["age", "region"],
            cat_feature_names=frozenset({"region"}),
            flavor="catboost",
        )
        # region trained as String, now passed as Int64 — encoding differs,
        # predictions will be garbage.
        schema = pl.Schema({"age": pl.Float64, "region": pl.Int64})

        with pytest.raises(FeatureMismatchError) as exc_info:
            _validate_features(sm, schema)

        err_msg = str(exc_info.value)
        ctx = exc_info.value.context
        assert "region" in err_msg, (
            "The error must name 'region' so the operator sees the offending column"
        )
        assert ctx.get("type_mismatches"), (
            "context['type_mismatches'] must be set so log consumers can act"
        )

    def test_float_for_string_cat_raises(self) -> None:
        """Float64 for a categorical is also a type mismatch."""
        from haute._mlflow_io import ScoringModel
        from haute._model_scorer import _validate_features

        model = MagicMock()
        sm = ScoringModel(
            model=model,
            feature_names=["channel"],
            cat_feature_names=frozenset({"channel"}),
            flavor="catboost",
        )
        schema = pl.Schema({"channel": pl.Float64})

        with pytest.raises(FeatureMismatchError):
            _validate_features(sm, schema)

    def test_string_for_string_cat_does_not_raise(self) -> None:
        """Matching dtypes must proceed silently — no false positive."""
        from haute._mlflow_io import ScoringModel
        from haute._model_scorer import _validate_features

        model = MagicMock()
        sm = ScoringModel(
            model=model,
            feature_names=["region"],
            cat_feature_names=frozenset({"region"}),
            flavor="catboost",
        )
        schema = pl.Schema({"region": pl.Utf8})

        usable, missing = _validate_features(sm, schema)
        assert usable == ["region"]
        assert missing == []


# ===========================================================================
# Item #15 — GLM column narrowing preserves categorical metadata
# ===========================================================================


class TestGLMCategoricalSurvival:
    """After the GLM narrowing at lines 262-264 of ``_training_job.py``
    the ``cat_features`` list must still contain the categorical terms
    chosen by the user; a round-trip through training and result should
    preserve them so deploy/scoring can infer the right dtype.
    """

    def test_glm_result_includes_categorical_term(self, tmp_path: Path) -> None:
        pytest.importorskip("rustystats", reason="rustystats optional dependency not installed")
        from haute.modelling._training_job import TrainingJob

        rng = np.random.RandomState(17)
        n = 200
        df = pl.DataFrame(
            {
                "region": rng.choice(["A", "B", "C"], n),
                "age": rng.randint(18, 65, n).astype(np.float64),
                "y": (rng.randn(n) * 0.3 + 1.0).clip(0.05),
            }
        )

        job = TrainingJob(
            name="glm_cat_roundtrip",
            data=df,
            target="y",
            algorithm="glm",
            params={
                "family": "gaussian",
                "terms": {
                    "region": {"type": "categorical"},
                    "age": {"type": "linear"},
                },
            },
            output_dir=str(tmp_path),
        )
        result = job.run()

        # After narrowing, region must still be flagged as categorical.
        assert "region" in result.features
        assert "region" in result.cat_features, (
            "After GLM narrowing (_training_job.py:262-264) the categorical "
            "flag for the term must be preserved — otherwise deploy-time "
            "scoring won't know to String-cast the column."
        )

    def test_save_load_preserves_cat_features_via_contract(self, tmp_path: Path) -> None:
        """Contract round-trip keeps the categorical set intact."""
        contract = build_contract(
            features=["region", "age"],
            feature_types={"region": "String", "age": "Float64"},
            categorical_features=["region"],
            target_name="y",
            target_type="Float64",
            task="regression",
        )
        path = tmp_path / CONTRACT_FILENAME
        save_contract(contract, path)
        loaded = load_contract(path)

        assert loaded.categorical_features == ["region"]
        assert loaded.feature_types["region"] == "String"
        assert_contracts_match(contract, loaded)

    def test_glm_narrowing_drops_non_term_cat_feature(self, tmp_path: Path) -> None:
        """A categorical column NOT referenced by a term is dropped — but the
        narrowing must not accidentally drop *all* categoricals because of a
        wrong set operation.
        """
        pytest.importorskip("rustystats", reason="rustystats optional dependency not installed")
        from haute.modelling._training_job import TrainingJob

        rng = np.random.RandomState(5)
        n = 150
        df = pl.DataFrame(
            {
                "region": rng.choice(["X", "Y", "Z"], n),
                "brand": rng.choice(["ford", "bmw"], n),  # categorical, NOT in terms
                "age": rng.randint(18, 70, n).astype(np.float64),
                "y": rng.randn(n).astype(np.float64).clip(-2, 2),
            }
        )

        job = TrainingJob(
            name="glm_narrowing",
            data=df,
            target="y",
            algorithm="glm",
            params={
                "family": "gaussian",
                "terms": {
                    "region": {"type": "categorical"},
                    "age": {"type": "linear"},
                },
            },
            output_dir=str(tmp_path),
        )
        result = job.run()

        assert "region" in result.cat_features
        # brand was not in terms — it must be dropped from features *and* cat_features.
        assert "brand" not in result.features
        assert "brand" not in result.cat_features


# ===========================================================================
# Item #26 — Diagnostic except-all silently skips mandatory work
# ===========================================================================


@pytest.mark.parametrize("categorical,with_offset", [(True, False), (False, False), (True, True)])
def test_pdp_ranking_preserves_native_prediction_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, categorical: bool, with_offset: bool
) -> None:
    """Chart ranking cannot swap model inputs, even when swapped types still fit."""
    import catboost

    from haute.modelling._algorithms import CatBoostAlgorithm
    from haute.modelling._training_job import TrainingJob

    use_project(tmp_path)
    features = ["first", "amount"]
    display_order = ["amount", "first"]
    n = 80
    first = np.arange(n) % 2
    amount = np.arange(n, dtype=float) % 13 + 10
    baseline = np.log1p(np.arange(n) % 5)
    df = pl.DataFrame(
        {
            "first": ["a" if value == 0 else "b" for value in first]
            if categorical
            else first.astype(float),
            "amount": amount,
            "target": 20 * amount + 10 * first + (baseline if with_offset else 0),
        }
    )
    if with_offset:
        df = df.with_columns(pl.Series("offset", baseline))

    # Force a different presentation order independently of CatBoost's learned
    # importance, while retaining real fitting, PDP computation and prediction.
    monkeypatch.setattr(
        CatBoostAlgorithm,
        "feature_importance",
        lambda _self, _model: [
            {"feature": feature, "importance": float(100 - index)}
            for index, feature in enumerate(display_order)
        ],
    )
    monkeypatch.delattr(CatBoostAlgorithm, "shap_values")
    monkeypatch.setattr(CatBoostAlgorithm, "feature_importance_typed", lambda *args, **kwargs: [])
    monkeypatch.setenv("HAUTE_TRAINING_THREADS", "1")
    result = TrainingJob(
        name="pdp_order",
        data=df,
        target="target",
        feature_columns=features,
        offset="offset" if with_offset else None,
        params={"iterations": 8, "depth": 2, "verbose": 0},
        split={"validation_size": 0, "holdout_size": 0},
        output_dir=str(tmp_path),
    ).run()

    assert not [error for error in result.diagnostics_errors if error["diagnostic"] == "pdp"]
    assert [entry["feature"] for entry in result.pdp_data] == display_order
    native_model = catboost.CatBoostRegressor()
    native_model.load_model(result.model_path)
    assert native_model.feature_names_ == features
    for entry in result.pdp_data:
        assert entry["grid"] and "error" not in entry
        for point in entry["grid"]:
            modified = df.with_columns(pl.lit(point["value"]).alias(entry["feature"]))
            # Independent native oracle: use a named Pool in training order,
            # never Haute's prediction adapter or PDP implementation.
            pool = catboost.Pool(
                modified.select(features).to_numpy(),
                feature_names=features,
                cat_features=[0] if categorical else [],
                baseline=baseline if with_offset else None,
            )
            expected = float(np.mean(native_model.predict(pool)))
            assert point["avg_prediction"] == pytest.approx(expected, abs=1e-5, rel=0)


class TestDiagnosticsFailLoudlySplit:
    """7 sites in ``_training_job.py`` swallow every exception and log a
    warning.  Policy: split into mandatory vs optional.

    * Mandatory — metrics/feature-importance — must raise.
    * Optional — SHAP / PDP / regularization_path — may skip but must
      surface an entry in ``result.diagnostics_errors`` so callers aren't
      blind to silent degradation.
    """

    @pytest.fixture()
    def synth(self) -> pl.DataFrame:
        """Numeric-only synth dataset large enough to avoid native CatBoost
        edge cases in LossFunctionChange / SHAP — we care about the
        diagnostics-error surfacing, not the stats' numerical stability.
        """
        rng = np.random.RandomState(11)
        n = 400
        return pl.DataFrame(
            {
                "x1": rng.randn(n),
                "x2": rng.randn(n),
                "ClaimCount": (rng.randn(n) * 0.3 + 1).clip(0),
                "Exposure": np.ones(n),
            }
        )

    def test_shap_failure_surfaces_in_result(self, synth: pl.DataFrame, tmp_path: Path) -> None:
        """SHAP is optional — if it raises, the run must still succeed
        BUT ``result.diagnostics_errors`` must carry the entry so the UI
        can show a degraded-diagnostics badge.
        """
        pytest.importorskip("catboost", reason="catboost optional dependency not installed")
        from haute.modelling._training_job import TrainingJob

        def _exploding_shap(*args: Any, **kwargs: Any) -> Any:
            raise ArithmeticError("simulated SHAP explosion")

        # Patch the *method* used by _compute_metrics.
        with (
            patch(
                "haute.modelling._algorithms.CatBoostAlgorithm.shap_values",
                side_effect=_exploding_shap,
            ),
            patch(
                "haute.modelling._algorithms.CatBoostAlgorithm.feature_importance_typed",
                return_value=[],
            ),
            patch("haute.modelling._metrics.compute_pdp", return_value=[]),
        ):
            job = TrainingJob(
                name="shap_fail_model",
                data=synth,
                target="ClaimCount",
                weight="Exposure",
                params={"iterations": 1, "depth": 1, "verbose": 0},
                output_dir=str(tmp_path),
            )
            result = job.run()

        # Mandatory metrics still populated — training succeeded.
        assert result.metrics

        # Optional: surface the degraded diagnostic so callers see it.
        diag_errors = getattr(result, "diagnostics_errors", None)
        assert diag_errors is not None, (
            "TrainResult must expose `diagnostics_errors` so optional "
            "diagnostic failures (SHAP, PDP, etc.) are visible to callers."
        )
        assert any("shap" in str(entry).lower() for entry in diag_errors), (
            "SHAP failure must be recorded in diagnostics_errors with the "
            "offending diagnostic named"
        )

    def test_pdp_failure_surfaces_in_result(self, synth: pl.DataFrame, tmp_path: Path) -> None:
        """PDP is optional — its failure is recorded, not silently swallowed."""
        pytest.importorskip("catboost", reason="catboost optional dependency not installed")
        from haute.modelling._training_job import TrainingJob

        # Force compute_pdp to blow up.  It is imported inside
        # ``_compute_metrics`` so we patch the source module.
        with (
            patch(
                "haute.modelling._algorithms.CatBoostAlgorithm.feature_importance_typed",
                return_value=[],
            ),
            patch(
                "haute.modelling._metrics.compute_pdp",
                side_effect=ArithmeticError("simulated PDP failure"),
            ),
        ):
            job = TrainingJob(
                name="pdp_fail_model",
                data=synth,
                target="ClaimCount",
                weight="Exposure",
                params={"iterations": 1, "depth": 1, "verbose": 0},
                output_dir=str(tmp_path),
            )
            result = job.run()

        assert result.metrics
        diag_errors = getattr(result, "diagnostics_errors", None)
        assert diag_errors is not None
        assert any("pdp" in str(entry).lower() for entry in diag_errors)

    def test_mandatory_metric_failure_raises(self, synth: pl.DataFrame, tmp_path: Path) -> None:
        """Core metrics (``compute_metrics``) are mandatory — a failure must
        propagate as an ExecutionError / ValueError, not be swallowed.
        """
        pytest.importorskip("catboost", reason="catboost optional dependency not installed")
        from haute.modelling._training_job import TrainingJob

        with patch(
            "haute.modelling._training_job.compute_metrics",
            side_effect=ArithmeticError("simulated metrics failure"),
        ):
            job = TrainingJob(
                name="metric_fail_model",
                data=synth,
                target="ClaimCount",
                weight="Exposure",
                params={"iterations": 1, "depth": 1, "verbose": 0},
                output_dir=str(tmp_path),
            )
            with pytest.raises((ArithmeticError, Exception)) as exc_info:
                job.run()
            # The original cause must be preserved so the operator sees
            # the real failure mode.
            assert "metrics" in str(exc_info.value).lower() or (
                exc_info.value.__cause__ is not None
            )

    def test_feature_importance_failure_raises(self, synth: pl.DataFrame, tmp_path: Path) -> None:
        """CatBoost feature-importance is mandatory — zero-diagnostics runs
        are useless to actuaries.  Must propagate on failure.
        """
        pytest.importorskip("catboost", reason="catboost optional dependency not installed")
        from haute.modelling._training_job import TrainingJob

        with patch(
            "haute.modelling._algorithms.CatBoostAlgorithm.feature_importance",
            side_effect=ArithmeticError("simulated FI failure"),
        ):
            job = TrainingJob(
                name="fi_fail_model",
                data=synth,
                target="ClaimCount",
                weight="Exposure",
                params={"iterations": 1, "depth": 1, "verbose": 0},
                output_dir=str(tmp_path),
            )
            with pytest.raises(Exception):  # noqa: PT011 - intentionally broad: testing error propagation from patched failure
                job.run()


EVALUATION = {
    "schema_version": 1,
    "strategy": "random",
    "seed": 1,
    "validation": {"method": "single", "size": 0.25},
}


def classification_config(**overrides: object) -> dict[str, object]:
    config: dict[str, object] = {
        "target": "y",
        "feature_columns": ["x"],
        "task": "classification",
        "loss_function": "Logloss",
        "params": {"iterations": 10},
        "evaluation": EVALUATION,
        "positive_class": "claim",
    }
    config.update(overrides)
    return config


def labelled_frame(n: int = 120) -> pl.DataFrame:
    rng = np.random.default_rng(5)
    x = rng.random(n)
    return pl.DataFrame({"x": x, "y": np.where(x > 0.6, "claim", "none").tolist()})


def test_catboost_class_names_cannot_reorder_the_probability_columns() -> None:
    with pytest.raises(TrainingConfigError, match="cannot set 'class_names'"):
        build_training_job_kwargs(
            classification_config(params={"iterations": 5, "class_names": [1, 0]}),
            data="d.parquet",
        )


def test_fit_refuses_a_class_order_other_than_the_encoding() -> None:
    frame = pl.DataFrame({"x": [0.1, 0.2, 0.8, 0.9] * 10, "y": [0.0, 0.0, 1.0, 1.0] * 10})
    with pytest.raises(HauteValidationError, match="did not fit the encoded classes in order"):
        CatBoostAlgorithm().fit(
            frame,
            ["x"],
            [],
            "y",
            None,
            {"iterations": 3, "loss_function": "Logloss", "class_names": [1.0, 0.0]},
            "classification",
            class_labels=("none", "claim"),
        )


def test_exported_script_constructs_the_job_with_positive_class() -> None:
    from tests.test_glm_integration import _captured_export_kwargs

    config = classification_config()
    script = generate_training_script(config, "data.parquet")
    captured = _captured_export_kwargs(script)
    assert captured["positive_class"] == "claim"
    assert captured == {
        key: value
        for key, value in build_training_job_kwargs(config, data="data.parquet").items()
        if key in captured
    }


def test_positive_class_changes_the_canvas_and_direct_training_identity() -> None:
    frame = labelled_frame()
    identities = []
    for positive in ("claim", "none"):
        kwargs = build_training_job_kwargs(
            classification_config(positive_class=positive), data="d.parquet"
        )
        job = TrainingJob(**{**kwargs, "data": frame})
        assert job.training_identity_sha256 == training_identity_sha256(kwargs)
        identities.append(job.training_identity_sha256)
    assert identities[0] != identities[1]


def test_rounds_configured_honours_every_round_count_spelling() -> None:
    frame = pl.DataFrame({"x": np.linspace(0, 1, 60), "y": np.linspace(0, 2, 60)})
    result = CatBoostAlgorithm().fit(
        frame, ["x"], [], "y", None, {"n_estimators": 3, "loss_function": "RMSE"}, "regression"
    )
    assert (result.rounds_configured, result.rounds_fitted, result.stopping_reason) == (
        3,
        3,
        "none",
    )


def test_scripted_mlflow_logging_carries_fit_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HAUTE_TRAINING_THREADS", "2")
    captured: dict[str, object] = {}

    def fake_log(*, experiment_name, candidate, destination, check_cancelled):
        captured["params"] = dict(candidate.params)

    rng = np.random.default_rng(2)
    x = rng.random(80)
    job = TrainingJob(
        name="scripted",
        data=pl.DataFrame({"x": x, "y": 2 * x + rng.normal(scale=0.1, size=80)}),
        target="y",
        loss_function="RMSE",
        params={"iterations": 6},
        metrics=["rmse"],
        evaluation=EVALUATION,
        mlflow_experiment="/scripted",
        output_dir=str(tmp_path),
    )
    with patch("haute.modelling._mlflow_log.log_experiment", side_effect=fake_log):
        job.run()
    params = captured["params"]
    assert params["fit_threads"] == 2
    assert params["fit_stopping_reason"] in {"none", "validation"}
    assert "fit_rounds_fitted" in params


def test_haute_trained_string_labels_round_trip_through_mlflow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Log and reload through real MLflow; compare with the native model independently."""
    import mlflow
    from catboost import CatBoostClassifier

    from haute._mlflow_utils import mlflow_fluent_operation
    from haute.modelling._feature_contract import load_contract
    from haute.modelling._native_pyfunc import package_native_model
    from haute.modelling._training_job import model_contract_filename

    monkeypatch.setenv("MLFLOW_ALLOW_FILE_STORE", "true")
    monkeypatch.delenv("MLFLOW_TRACKING_URI", raising=False)
    frame = labelled_frame()
    result = TrainingJob(
        name="labels",
        data=frame,
        target="y",
        task="classification",
        loss_function="Logloss",
        params={"iterations": 15},
        metrics=["auc"],
        positive_class="claim",
        output_dir=str(tmp_path / "train"),
    ).run()
    model_path = Path(result.model_path)
    contract_path = model_path.parent / model_contract_filename(model_path.stem)
    assert load_contract(contract_path).model.class_labels == ("none", "claim")
    package = package_native_model(model_path, contract_path, tmp_path / "package")

    with mlflow_fluent_operation():
        mlflow.set_tracking_uri((tmp_path / "mlruns").as_uri())
        with mlflow.start_run() as run:
            mlflow.pyfunc.log_model(
                name="model",
                loader_module="haute.modelling._native_pyfunc",
                data_path=str(package),
            )
        served = mlflow.pyfunc.load_model(f"runs:/{run.info.run_id}/model").predict(
            frame.select("x").to_pandas()
        )

    native = CatBoostClassifier()
    native.load_model(str(model_path))
    positive = native.predict_proba(frame.select("x").to_pandas())[:, 1]
    np.testing.assert_allclose(served["pred_proba"].to_numpy(), positive, rtol=1e-9)
    assert served["pred_label"].tolist() == np.where(positive > 0.5, "claim", "none").tolist()
    assert {"claim", "none"} == set(served["pred_label"])


def test_a_contract_describing_another_loss_fails_at_load(tmp_path: Path) -> None:
    import dataclasses

    from haute.modelling._feature_contract import build_contract, load_contract, save_contract
    from haute.modelling._native_pyfunc import NativePyfuncModel, package_native_model
    from haute.modelling._training_job import model_contract_filename

    frame = pl.DataFrame({"x": np.linspace(0, 1, 50), "y": np.linspace(0, 3, 50)})
    result = TrainingJob(
        name="reg",
        data=frame,
        target="y",
        loss_function="RMSE",
        params={"iterations": 4},
        metrics=["rmse"],
        output_dir=str(tmp_path / "train"),
    ).run()
    model_path = Path(result.model_path)
    contract = load_contract(model_path.parent / model_contract_filename(model_path.stem))
    assert isinstance(contract.model, ModelIdentity)
    tampered = build_contract(
        features=contract.features,
        feature_types=contract.feature_types,
        categorical_features=contract.categorical_features,
        target_name=contract.target_name,
        target_type=contract.target_type,
        task=contract.task,
        offset_column=contract.offset_column,
        model=dataclasses.replace(contract.model, loss="Poisson", link="log"),
    )
    save_contract(tampered, tmp_path / "tampered.json")
    package = package_native_model(model_path, tmp_path / "tampered.json", tmp_path / "pkg")
    with pytest.raises(ConfigError, match="describes a Poisson model"):
        NativePyfuncModel(str(package))


def test_a_contract_describing_another_algorithm_fails_the_identity_check(tmp_path: Path) -> None:
    from haute._mlflow_io import load_local_model, verify_contract_identity

    frame = pl.DataFrame({"x": np.linspace(0, 1, 50), "y": np.linspace(0, 3, 50)})
    result = TrainingJob(
        name="reg",
        data=frame,
        target="y",
        loss_function="RMSE",
        params={"iterations": 4},
        metrics=["rmse"],
        output_dir=str(tmp_path / "train"),
    ).run()
    scoring = load_local_model(result.model_path)
    glm_identity = ModelIdentity(
        algorithm="glm",
        link="identity",
        engine_name="rustystats",
        engine_version="0",
        haute_version="0",
        glm_family="gaussian",
    )
    with pytest.raises(ConfigError, match="describes a glm model"):
        verify_contract_identity(glm_identity, scoring)
