"""Phase 1 Package 1E — modelling correctness tests (train <-> score contract).

TDD suite covering items:

* #1  — Feature / categorical order mismatch raises at score time
* #2  — MLflow signature is logged and round-trips through the feature contract
* #13 — Categorical type mismatch raises FeatureMismatchError (no silent cast)
* #15 — GLM column selection preserves categorical metadata across save/load
* #26 — Diagnostic exceptions split into mandatory (fail) vs optional (surface flag)

Item #25 (model-score column detection) and item #27 (artifact delete-and-retry)
live in ``test_modelling_loud_errors.py``.

Written before implementation lands — tests *must* fail loudly until the
production code matches the contract.  These are not unit tests of the
foundation packages (F4 / F5); they pin the wiring between the training
pipeline, the scorer, and MLflow.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import numpy as np
import polars as pl
import pytest

from haute.errors import FeatureMismatchError
from haute.modelling._feature_contract import (
    CONTRACT_FILENAME,
    assert_contracts_match,
    build_contract,
    load_contract,
    save_contract,
)

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
# Item #1 — Feature / categorical order mismatch between training and scoring
# ===========================================================================


class TestFeatureOrderMismatchAtScore:
    """Training features ordered one way, score input ordered another way.

    The scorer must raise :class:`FeatureMismatchError` rather than silently
    reorder columns or mis-index the categorical positions fed into the
    CatBoost Pool.
    """

    def test_score_with_reordered_features_raises(
        self, mixed_train_df: pl.DataFrame, tmp_path: Path
    ) -> None:
        pytest.importorskip("catboost", reason="catboost optional dependency not installed")
        from haute._mlflow_io import ScoringModel, _wrap_catboost
        from haute._model_scorer import _run_score_pipeline

        # Train with a deliberate, non-alphabetic feature order.
        train_features = ["region", "age", "vehicle_value"]
        cat_features = ["region"]
        model = _train_tiny_catboost(
            mixed_train_df,
            features=train_features,
            cat_features=cat_features,
        )

        scoring_model: ScoringModel = _wrap_catboost(model)
        assert list(scoring_model.feature_names) == train_features, (
            "Pre-check: CatBoost must remember training feature order"
        )

        # Score with a different column order.  The scorer is expected to
        # treat the training order as load-bearing for CatBoost (categorical
        # indices are positional), so a reorder must be detected and surfaced.
        reordered = mixed_train_df.select(["age", "vehicle_value", "region"])
        scoring_lf = reordered.head(5).lazy()

        with pytest.raises(FeatureMismatchError) as exc_info:
            _run_score_pipeline(
                scoring_model,
                scoring_lf,
                task="regression",
                output_col="pred",
                source="live",
            )

        msg = str(exc_info.value).lower()
        assert "order" in msg or "feature" in msg

    def test_score_with_cat_at_wrong_position_raises(self, mixed_train_df: pl.DataFrame) -> None:
        """Moving the categorical column to a different index is a mismatch.

        In CatBoost the categorical set is stored as *indices* into the
        feature vector.  If the incoming column order shifts ``region`` from
        position 0 to position 2 the indices no longer describe the same
        columns — silent reordering here produces wrong predictions.
        """
        pytest.importorskip("catboost", reason="catboost optional dependency not installed")
        from haute._mlflow_io import _wrap_catboost
        from haute._model_scorer import _run_score_pipeline

        train_features = ["region", "age", "vehicle_value"]
        model = _train_tiny_catboost(
            mixed_train_df,
            features=train_features,
            cat_features=["region"],
        )
        scoring_model = _wrap_catboost(model)

        # Build scoring data with all the right columns but wrong order.
        bad_order = mixed_train_df.select(["vehicle_value", "age", "region"]).head(4)

        with pytest.raises(FeatureMismatchError):
            _run_score_pipeline(
                scoring_model,
                bad_order.lazy(),
                task="regression",
                output_col="pred",
                source="live",
            )


# ===========================================================================
# Item #2 — MLflow signature is logged with the training feature contract
# ===========================================================================


class TestMLflowSignatureLogged:
    """``log_experiment`` / training must attach a ModelSignature so that
    loading the logged model via ``mlflow.models.get_model_info`` yields
    a signature that agrees with the training feature contract.
    """

    def test_log_experiment_includes_signature_artifact(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """After ``log_experiment`` completes, the run has a non-None
        ``ModelSignature`` attached that matches the training schema.

        We assert via the mocked mlflow.pyfunc.log_model / mlflow.sklearn.log_model
        / mlflow.catboost.log_model calls that a *signature* argument was
        passed, with inputs matching the training feature order.
        """
        pytest.importorskip("mlflow", reason="mlflow optional dependency not installed")
        monkeypatch.delenv("DATABRICKS_HOST", raising=False)
        monkeypatch.delenv("DATABRICKS_TOKEN", raising=False)

        mock_run = MagicMock()
        mock_run.info.run_id = "run_sig_1"

        model_file = tmp_path / "model.cbm"
        model_file.write_bytes(b"fake cbm bytes")

        signature_calls: list[Any] = []

        def _capture_signature(*args: Any, **kwargs: Any) -> None:
            if "signature" in kwargs:
                signature_calls.append(kwargs["signature"])

        with (
            patch("mlflow.set_tracking_uri"),
            patch("mlflow.set_experiment"),
            patch("mlflow.start_run") as m_run,
            patch("mlflow.log_params"),
            patch("mlflow.log_metrics"),
            patch("mlflow.log_artifact"),
            patch("mlflow.register_model"),
            patch("mlflow.catboost.log_model", side_effect=_capture_signature),
            patch("mlflow.pyfunc.log_model", side_effect=_capture_signature),
        ):
            m_run.return_value.__enter__ = MagicMock(return_value=mock_run)
            m_run.return_value.__exit__ = MagicMock(return_value=False)

            from haute.modelling._mlflow_log import log_experiment

            log_experiment(
                experiment_name="/test/sig",
                run_name="sig-run",
                metrics={"rmse": 0.5},
                params={"algorithm": "catboost", "task": "regression"},
                model_path=str(model_file),
            )

        # At least one of the model-logging functions must have been called
        # with a signature kwarg; otherwise scoring callers cannot detect
        # feature-order drift via the logged metadata.
        assert signature_calls, (
            "log_experiment did not pass a `signature` to mlflow.*.log_model — "
            "deploy-time scorers cannot verify the training feature contract."
        )

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
        pytest.importorskip("mlflow", reason="mlflow optional dependency not installed")
        monkeypatch.delenv("DATABRICKS_HOST", raising=False)
        monkeypatch.delenv("DATABRICKS_TOKEN", raising=False)
        monkeypatch.setattr(
            "haute.modelling._algorithms.CatBoostAlgorithm.shap_summary",
            lambda *a, **kw: [],
        )
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
            patch("mlflow.set_experiment"),
            patch("mlflow.start_run") as m_run,
            patch("mlflow.log_params"),
            patch("mlflow.log_metrics"),
            patch("mlflow.log_artifact"),
            patch("mlflow.register_model"),
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
                "haute.modelling._algorithms.CatBoostAlgorithm.shap_summary",
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
            patch("haute.modelling._algorithms.CatBoostAlgorithm.shap_summary", return_value=[]),
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
            with pytest.raises(Exception):
                job.run()
