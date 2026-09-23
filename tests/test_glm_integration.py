"""Regression tests for GLM integration gaps.

Covers four critical areas:
1. Config key merge — GLM-specific keys flow from top-level config into train_params.
2. Terms referencing non-existent columns — clear error when terms don't match data.
3. Offset/weight/target survival through feature narrowing via _glm_select_columns.
4. Result extraction — inference status, coefficient and relativity rows.
"""

from __future__ import annotations

import re
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import numpy as np
import polars as pl
import pytest

from haute.errors import HauteValidationError

# ---------------------------------------------------------------------------
# Gap 1: Config key merge
# ---------------------------------------------------------------------------


class TestGLMConfigKeyMerge:
    """Verify GLM keys are merged from top-level config into train_params via
    the shared builder (``haute.modelling._train_config.build_train_params``)
    — the exact function live training and script export both use."""

    def test_glm_config_keys_merged_into_train_params(self):
        """GLM-specific keys at top level of config are merged into train_params."""
        from haute.modelling._train_config import build_train_params

        config = {
            "target": "y",
            "algorithm": "glm",
            "terms": {"age": {"type": "linear"}},
            "family": "poisson",
            "link": "log",
            "interactions": [],
            "regularization": "ridge",
            "alpha": 0.5,
            "l1_ratio": 0.1,
            "intercept": True,
            "var_power": 1.5,
        }

        train_params = build_train_params(config)

        # All GLM keys should be present
        assert train_params["terms"] == {"age": {"type": "linear"}}
        assert train_params["family"] == "poisson"
        assert train_params["link"] == "log"
        assert train_params["regularization"] == "ridge"
        assert train_params["alpha"] == 0.5
        assert train_params["l1_ratio"] == 0.1
        assert train_params["intercept"] is True
        assert train_params["var_power"] == 1.5
        assert train_params["interactions"] == []

    def test_missing_glm_keys_are_skipped(self):
        """Only keys actually present in config are merged; no KeyError."""
        from haute.modelling._train_config import build_train_params

        config: dict = {"algorithm": "glm", "family": "tweedie"}
        train_params = build_train_params(config)

        assert train_params["family"] == "tweedie"
        # Other GLM keys should be absent, not defaulted
        assert "terms" not in train_params
        assert "link" not in train_params
        assert "regularization" not in train_params

    def test_catboost_never_receives_glm_keys(self):
        """4b.1 — the merge is gated on algorithm: CatBoost has no **kwargs, so
        leaked GLM keys (offset, terms, …) crash the fit."""
        from haute.modelling._train_config import build_train_params

        config = {
            "target": "y",
            "algorithm": "catboost",
            "params": {"iterations": 100},
            "offset": "log_exposure",
            "terms": {"age": {"type": "linear"}},
            "family": "poisson",
        }
        assert build_train_params(config) == {"iterations": 100}

    def test_glm_config_keys_tuple_is_complete(self):
        """Ensure GLM_CONFIG_KEYS contains all expected entries."""
        from haute.modelling._train_config import GLM_CONFIG_KEYS

        expected = {
            "terms",
            "family",
            "link",
            "interactions",
            "regularization",
            "alpha",
            "l1_ratio",
            "cv_folds",
            "cv_selection",
            "cv_seed",
            "max_iter",
            "tol",
            "robust_standard_errors",
            "intercept",
            "var_power",
            "theta",
            "offset",
        }
        assert set(GLM_CONFIG_KEYS) == expected


# ---------------------------------------------------------------------------
# Gap 2: Terms referencing non-existent columns
# ---------------------------------------------------------------------------


class TestGLMTermsValidation:
    """TrainingJob.run() must raise ValueError when GLM terms reference
    columns that do not exist in the data."""

    def test_glm_terms_referencing_missing_columns(self, tmp_path):
        """GLM raises clear error when terms reference non-existent columns."""
        from haute.modelling._training_job import TrainingJob

        df = pl.DataFrame(
            {
                "age": [25, 30, 35, 40, 45],
                "target": [1.0, 2.0, 3.0, 4.0, 5.0],
            }
        )

        job = TrainingJob(
            name="test_missing_cols",
            data=df,
            target="target",
            algorithm="glm",
            params={
                "family": "gaussian",
                "terms": {"nonexistent_col": {"type": "linear"}},
            },
            output_dir=str(tmp_path),
        )

        with pytest.raises(ValueError, match="not found in training data"):
            job.run()

    def test_glm_terms_partially_missing(self, tmp_path):
        """One valid, one invalid term — error lists only the missing one."""
        from haute.modelling._training_job import TrainingJob

        df = pl.DataFrame(
            {
                "age": [25, 30, 35, 40, 45],
                "target": [1.0, 2.0, 3.0, 4.0, 5.0],
            }
        )

        job = TrainingJob(
            name="test_partial_missing",
            data=df,
            target="target",
            algorithm="glm",
            params={
                "family": "gaussian",
                "terms": {
                    "age": {"type": "linear"},
                    "ghost": {"type": "categorical"},
                },
            },
            output_dir=str(tmp_path),
        )

        with pytest.raises(ValueError, match="ghost"):
            job.run()

    @pytest.mark.parametrize(
        ("kwargs", "message"),
        [
            ({"exclude": ["age"]}, "exclude only apply to CatBoost"),
            ({"feature_columns": ["age"]}, "feature_columns only apply to CatBoost"),
            ({"monotone_constraints": {"age": 1}}, "monotone_constraints only apply to CatBoost"),
            ({"feature_weights": {"age": 2.0}}, "feature_weights only apply to CatBoost"),
            ({"params": {"family": "gaussian"}}, "GLM config has no terms"),
            ({"params": {"family": "gaussian", "terms": {}}}, "GLM config has no terms"),
        ],
    )
    def test_glm_refuses_catboost_only_levers_and_empty_terms(self, tmp_path, kwargs, message):
        from haute.modelling._training_job import TrainingJob

        df = pl.DataFrame({"age": [25, 30, 35, 40, 45], "target": [1.0, 2.0, 3.0, 4.0, 5.0]})
        job_kwargs = {
            "name": "glm_levers",
            "data": df,
            "target": "target",
            "algorithm": "glm",
            "params": {"family": "gaussian", "terms": {"age": {"type": "linear"}}},
            "output_dir": str(tmp_path),
            **kwargs,
        }
        with pytest.raises(HauteValidationError, match=message):
            TrainingJob(**job_kwargs)

    def test_glm_evaluation_key_cannot_be_read_by_an_expression_or_interaction(self, tmp_path):
        """The evaluation key is refused when an interaction factor reads it."""
        from haute.modelling._training_job import TrainingJob

        df = pl.DataFrame(
            {"age": [25, 30, 35, 40], "grp": ["a", "b", "a", "b"], "target": [1.0, 2.0, 3.0, 4.0]}
        )
        evaluation = {
            "schema_version": 1,
            "strategy": "group",
            "seed": 0,
            "group_column": "grp",
            "validation": {"method": "none"},
        }
        with pytest.raises(HauteValidationError, match="evaluation key cannot be a GLM term"):
            TrainingJob(
                name="t",
                data=df,
                target="target",
                algorithm="glm",
                params={
                    "family": "gaussian",
                    "terms": {"age": {"type": "linear"}},
                    "interactions": [{"factors": ["age", "grp"], "include_main": True}],
                },
                evaluation=evaluation,
                output_dir=str(tmp_path),
            )

    def test_glm_contract_keeps_expression_and_interaction_only_columns(self, tmp_path):
        """Expression identifiers and interaction-only factors survive the
        final feature contract; the expression key itself is not a column."""
        from haute.modelling._training_job import TrainingJob

        rng = np.random.default_rng(3)
        n = 300
        df = pl.DataFrame(
            {
                "age": rng.normal(size=n),
                "income": rng.normal(size=n),
                "region": rng.choice(["n", "s"], size=n),
                "target": rng.normal(size=n),
            }
        )
        job = TrainingJob(
            name="t",
            data=df,
            target="target",
            algorithm="glm",
            params={
                "family": "gaussian",
                "terms": {
                    "age": {"type": "linear"},
                    "age_sq": {"type": "expression", "expr": "age ** 2"},
                },
                "interactions": [{"factors": ["income", "region"], "include_main": True}],
            },
            output_dir=str(tmp_path),
        )
        result = job.run()
        assert set(result.features) >= {"age", "income", "region"}


# ---------------------------------------------------------------------------
# Gap 3: Offset/weight/target survival through _glm_select_columns
# ---------------------------------------------------------------------------


class TestGLMSelectColumns:
    """_glm_select_columns must include features, target, weight, and offset."""

    def test_glm_select_columns_includes_offset_weight_target(self):
        """_glm_select_columns returns terms + target + weight + offset."""
        from haute.modelling._training_job import TrainingJob

        job = TrainingJob(
            name="test_select",
            data="dummy.parquet",  # won't be read
            target="loss_cost",
            weight="exposure",
            offset="policy_years",
            algorithm="glm",
            params={
                "family": "poisson",
                "terms": {
                    "age": {"type": "linear"},
                    "region": {"type": "categorical"},
                },
            },
        )

        columns = job._glm_select_columns(["age", "region"])
        assert columns is not None
        assert "age" in columns
        assert "region" in columns
        assert "loss_cost" in columns  # target
        assert "exposure" in columns  # weight
        assert "policy_years" in columns  # offset

    def test_glm_select_columns_without_weight_or_offset(self):
        """When weight and offset are None, only features + target are returned."""
        from haute.modelling._training_job import TrainingJob

        job = TrainingJob(
            name="test_no_extras",
            data="dummy.parquet",
            target="y",
            algorithm="glm",
            params={"family": "gaussian", "terms": {"x1": {"type": "linear"}}},
        )

        columns = job._glm_select_columns(["x1", "x2"])
        assert columns is not None
        assert set(columns) == {"x1", "x2", "y"}

    def test_glm_select_columns_returns_sorted(self):
        """Returned list should be sorted for deterministic parquet reads."""
        from haute.modelling._training_job import TrainingJob

        job = TrainingJob(
            name="test_sorted",
            data="dummy.parquet",
            target="z_target",
            weight="a_weight",
            algorithm="glm",
            params={"family": "gaussian", "terms": {"m_feature": {"type": "linear"}}},
        )

        columns = job._glm_select_columns(["m_feature", "b_feature"])
        assert columns == sorted(columns)

    def test_glm_select_columns_returns_none_for_catboost(self):
        """CatBoost path returns None (no column pruning)."""
        from haute.modelling._training_job import TrainingJob

        job = TrainingJob(
            name="test_catboost",
            data="dummy.parquet",
            target="y",
            algorithm="catboost",
        )

        assert job._glm_select_columns(["a", "b", "c"]) is None


# ---------------------------------------------------------------------------
# Gap 4: Result extraction never publishes invalid or non-finite statistics
# ---------------------------------------------------------------------------


class _FakeGLM:
    """The RustyStats result surface the extraction functions read."""

    def __init__(
        self,
        *,
        status: object = "valid_standard",
        names: tuple[str, ...] = ("Intercept", "age"),
        params: tuple[float, ...] = (0.5, -0.25),
        bse: tuple[float, ...] = (0.1, 0.05),
        link: str = "log",
    ) -> None:
        self.inference_status = status
        self.feature_names = list(names)
        self.params = np.asarray(params, dtype=np.float64)
        self.link = link
        self._bse = np.asarray(bse, dtype=np.float64)

    def bse(self) -> np.ndarray:
        return self._bse

    def tvalues(self) -> np.ndarray:
        return self.params / self._bse

    def pvalues(self) -> np.ndarray:
        return np.asarray([0.0004, 0.2])

    def conf_int(self, alpha: float) -> np.ndarray:
        assert alpha == 0.05
        return np.column_stack([self.params - 2 * self._bse, self.params + 2 * self._bse])

    def bse_robust(self, cov_type: str) -> np.ndarray:
        assert cov_type == "HC1"
        return self._bse * 2

    def tvalues_robust(self, cov_type: str) -> np.ndarray:
        return self.params / self.bse_robust(cov_type)

    def pvalues_robust(self, cov_type: str) -> np.ndarray:
        assert cov_type == "HC1"
        return np.asarray([0.03, 0.4])

    def conf_int_robust(self, *, alpha: float, cov_type: str) -> np.ndarray:
        assert alpha == 0.05
        spread = 2 * self.bse_robust(cov_type)
        return np.column_stack([self.params - spread, self.params + spread])


class TestGLMResultExtraction:
    def test_valid_inference_publishes_model_statistics(self):
        from haute.modelling._rustystats import glm_coefficient_rows, glm_inference

        model = _FakeGLM()
        inference = glm_inference(model, None)

        assert inference.to_plain_data() == {
            "status": "valid_standard",
            "valid": True,
            "standard_errors": "model",
            "reason": None,
        }
        assert glm_coefficient_rows(model, inference) == [
            {
                "feature": "Intercept",
                "coefficient": 0.5,
                "std_error": pytest.approx(0.1),
                "z_value": pytest.approx(5.0),
                "p_value": pytest.approx(0.0004),
                "significance": "***",
            },
            {
                "feature": "age",
                "coefficient": -0.25,
                "std_error": pytest.approx(0.05),
                "z_value": pytest.approx(-5.0),
                "p_value": pytest.approx(0.2),
                "significance": "",
            },
        ]

    def test_robust_standard_errors_come_from_the_heteroskedasticity_consistent_statistics(self):
        from haute.modelling._rustystats import glm_coefficient_rows, glm_inference

        model = _FakeGLM()
        inference = glm_inference(model, "HC1")

        assert inference.standard_errors == "HC1"
        rows = glm_coefficient_rows(model, inference)
        assert [row["std_error"] for row in rows] == pytest.approx([0.2, 0.1])
        assert [row["significance"] for row in rows] == ["*", ""]

    @pytest.mark.parametrize(
        ("status", "reason"),
        [
            ("naive_after_regularization", "ridge penalty shrinks the coefficients"),
            ("naive_after_selection", "Lasso or elastic net selects variables"),
            ("naive_after_cv_selection", "chosen by cross-validation"),
            ("constrained_boundary", "Monotonicity constraints restrict the coefficients"),
            ("unavailable", "Automatically smoothed splines are penalised"),
            ("covariance_skipped", "Covariance was not computed"),
        ],
    )
    def test_invalid_inference_keeps_coefficients_and_nulls_statistics(self, status, reason):
        from haute.modelling._rustystats import glm_coefficient_rows, glm_inference

        model = _FakeGLM(status=status)
        inference = glm_inference(model, None)

        assert inference.valid is False
        assert inference.standard_errors is None
        assert reason in (inference.reason or "")
        assert glm_coefficient_rows(model, inference) == [
            {
                "feature": name,
                "coefficient": coefficient,
                "std_error": None,
                "z_value": None,
                "p_value": None,
                "significance": None,
            }
            for name, coefficient in (("Intercept", 0.5), ("age", -0.25))
        ]

    def test_singular_design_reports_invalid_inference(self):
        from haute.modelling._rustystats import glm_coefficient_rows, glm_inference

        model = _FakeGLM(bse=(0.1, float("nan")))
        inference = glm_inference(model, None)

        assert inference.status == "singular_design"
        assert inference.valid is False
        assert "close to singular" in (inference.reason or "")
        assert all(row["std_error"] is None for row in glm_coefficient_rows(model, inference))

    @pytest.mark.parametrize(
        ("status", "message"),
        [(None, "did not report an inference status"), ("bootstrap", "unknown inference status")],
    )
    def test_missing_or_unknown_inference_status_is_an_error(self, status, message):
        from haute.modelling._rustystats import glm_inference

        with pytest.raises(ValueError, match=message):
            glm_inference(_FakeGLM(status=status), None)

    def test_coefficients_that_do_not_match_the_design_columns_are_an_error(self):
        from haute.modelling._rustystats import glm_coefficient_rows, glm_inference

        model = _FakeGLM(names=("Intercept",))
        with pytest.raises(ValueError, match="2 coefficients for 1 design columns"):
            glm_coefficient_rows(model, glm_inference(_FakeGLM(), None))

    def test_non_finite_coefficients_are_an_error(self):
        from haute.modelling._rustystats import glm_coefficient_rows, glm_inference

        model = _FakeGLM(status="naive_after_regularization", params=(0.5, float("inf")))
        with pytest.raises(ValueError, match=re.escape("non-finite coefficients for ['age']")):
            glm_coefficient_rows(model, glm_inference(model, None))

    def test_relativities_exponentiate_coefficients_and_interval_bounds(self):
        from haute.modelling._rustystats import glm_inference, glm_relativity_rows

        model = _FakeGLM()
        rows = glm_relativity_rows(model, glm_inference(model, None))

        assert rows == [
            {
                "feature": "Intercept",
                "relativity": pytest.approx(np.exp(0.5)),
                "ci_lower": pytest.approx(np.exp(0.3)),
                "ci_upper": pytest.approx(np.exp(0.7)),
            },
            {
                "feature": "age",
                "relativity": pytest.approx(np.exp(-0.25)),
                "ci_lower": pytest.approx(np.exp(-0.35)),
                "ci_upper": pytest.approx(np.exp(-0.15)),
            },
        ]

    def test_relativities_have_no_bounds_when_inference_is_invalid(self):
        from haute.modelling._rustystats import glm_inference, glm_relativity_rows

        model = _FakeGLM(status="constrained_boundary")
        rows = glm_relativity_rows(model, glm_inference(model, None))

        assert [row["relativity"] for row in rows] == pytest.approx([np.exp(0.5), np.exp(-0.25)])
        assert all(row["ci_lower"] is None and row["ci_upper"] is None for row in rows)

    @pytest.mark.parametrize("link", ["identity", "logit"])
    def test_only_log_link_models_have_relativities(self, link):
        from haute.modelling._rustystats import glm_inference, glm_relativity_rows

        model = _FakeGLM(link=link)
        assert glm_relativity_rows(model, glm_inference(model, None)) == []

    def test_relativity_overflow_is_an_error_naming_the_term(self):
        from haute.modelling._rustystats import glm_inference, glm_relativity_rows

        model = _FakeGLM(status="naive_after_regularization", params=(0.5, 900.0))
        with pytest.raises(ValueError, match=re.escape("Relativities are not finite for ['age']")):
            glm_relativity_rows(model, glm_inference(model, None))

    def test_glm_result_records_each_failed_diagnostic_and_keeps_the_rest(self):
        from haute.modelling._rustystats import GLMAlgorithm

        model = MagicMock()
        model.inference_status = "naive_after_regularization"
        model.feature_names = ["Intercept", "age"]
        model.params = np.asarray([0.5, 900.0])
        model.link = "log"
        model.has_smooth_terms.return_value = False
        model.deviance = float("nan")
        model.alpha = 0.5
        model.l1_ratio = 0.0
        model.n_nonzero.return_value = 2

        report = GLMAlgorithm().glm_result(model, {"regularization": "ridge", "alpha": 0.5})

        assert report.inference == {
            "status": "naive_after_regularization",
            "valid": False,
            "standard_errors": None,
            "reason": pytest.approx(report.inference["reason"]),
        }
        assert [row["coefficient"] for row in report.coefficients] == [0.5, 900.0]
        assert report.relativities == []
        assert report.smooth_terms == []
        assert report.regularization == {
            "penalty": "ridge",
            "mode": "fixed",
            "alpha": 0.5,
            "l1_ratio": 0.0,
            "n_nonzero": 2,
            "cv_folds": None,
            "cv_selection": None,
            "cv_seed": None,
        }
        assert [name for name, _error in report.errors] == [
            "glm_relativities",
            "glm_fit_statistics",
        ]

    def test_glm_result_without_an_inference_status_skips_the_tables(self):
        from haute.modelling._rustystats import GLMAlgorithm

        model = SimpleNamespace(inference_status=None)
        report = GLMAlgorithm().glm_result(model, {})

        assert report.inference is None
        assert report.coefficients == []
        assert report.relativities == []
        assert [name for name, _error in report.errors] == [
            "glm_inference",
            "glm_fit_statistics",
            "glm_smooth_terms",
        ]


def _run_glm(tmp_path, frame: pl.DataFrame, params: dict[str, Any]) -> Any:
    from haute.modelling._training_job import TrainingJob

    return TrainingJob(
        name="glm_result",
        data=frame,
        target="y",
        algorithm="glm",
        params=params,
        evaluation=_random_evaluation(),
        output_dir=str(tmp_path),
    ).run()


def _worker_response(result: Any) -> dict[str, Any]:
    """The training worker's response, which refuses any non-finite number."""
    from haute.routes._training_worker import _training_response_payload

    return _training_response_payload(
        result,
        job_id="job",
        model_path="model.rsglm",
        evaluation=result.evaluation,
        tuning=None,
    )


class TestGLMResultIntegrity:
    """Real fits whose inference RustyStats marks invalid still deliver a result."""

    def test_monotone_glm_result_passes_the_finite_response_guard_with_null_inference(
        self, tmp_path
    ) -> None:
        rng = np.random.default_rng(42)
        n = 400
        x = rng.uniform(0.0, 2.0, size=n)
        frame = pl.DataFrame({"x": x, "y": rng.poisson(np.exp(0.2 + 0.4 * x)).astype(float)})
        params = {
            "family": "poisson",
            "terms": {"x": {"type": "linear", "monotonicity": "increasing"}},
        }

        result = _run_glm(tmp_path, frame, params)
        response = _worker_response(result)

        assert result.diagnostics_errors == [], result.diagnostics_errors
        assert response["glm_inference"] == {
            "status": "constrained_boundary",
            "valid": False,
            "standard_errors": None,
            "reason": (
                "Monotonicity constraints restrict the coefficients, so standard errors and "
                "p-values are not valid."
            ),
        }
        assert [row["feature"] for row in result.glm_coefficients] == ["Intercept", "pos(x)"]
        assert all(row["std_error"] is None for row in result.glm_coefficients)
        assert all(row["ci_lower"] is None for row in result.glm_relativities)
        assert "aic" not in result.glm_fit_statistics

    def test_auto_spline_glm_reports_smooth_terms_and_unavailable_inference(self, tmp_path) -> None:
        rng = np.random.default_rng(8)
        n = 600
        x = rng.uniform(0.0, 1.0, size=n)
        frame = pl.DataFrame({"x": x, "y": rng.poisson(np.exp(0.2 + np.sin(3 * x))).astype(float)})

        result = _run_glm(tmp_path, frame, {"family": "poisson", "terms": {"x": {"type": "bs"}}})
        response = _worker_response(result)

        assert result.diagnostics_errors == [], result.diagnostics_errors
        assert result.glm_inference["status"] == "unavailable"
        assert response["glm_smooth_terms"] == [
            {
                "term": "x",
                "k": 10,
                "edf": pytest.approx(result.glm_smooth_terms[0]["edf"]),
                "lambda": pytest.approx(result.glm_smooth_terms[0]["lambda"]),
            }
        ]
        assert 1.0 < result.glm_smooth_terms[0]["edf"] < 10.0
        assert result.glm_smooth_terms[0]["lambda"] > 0.0
        assert {"total_edf", "gcv"} <= set(result.glm_fit_statistics)

    def test_cv_ridge_glm_reports_the_selected_alpha_and_folds(self, tmp_path) -> None:
        rng = np.random.default_rng(5)
        n = 800
        x = rng.uniform(0.0, 1.0, size=n)
        frame = pl.DataFrame(
            {
                "x": x,
                "z": rng.normal(size=n),
                "y": rng.poisson(np.exp(0.2 + x)).astype(float),
            }
        )
        params = {
            "family": "poisson",
            "terms": {"x": {"type": "linear"}, "z": {"type": "linear"}},
            "regularization": "ridge",
            "cv_folds": 4,
            "cv_selection": "1se",
            "cv_seed": 7,
        }

        result = _run_glm(tmp_path, frame, params)
        _worker_response(result)

        assert result.diagnostics_errors == [], result.diagnostics_errors
        assert result.glm_inference["status"] == "naive_after_cv_selection"
        regularization = result.glm_regularization
        assert regularization == {
            "penalty": "ridge",
            "mode": "cross_validation",
            "alpha": regularization["alpha"],
            "l1_ratio": 0.0,
            "n_nonzero": 2,
            "cv_folds": 4,
            "cv_selection": "1se",
            "cv_seed": 7,
        }
        # The penalty cross-validation selected, not the old always-zero read.
        assert regularization["alpha"] > 0.0

    def test_relativity_overflow_is_a_diagnostic_error_not_a_job_failure(self, tmp_path) -> None:
        """An unscaled term's coefficient is finite but too large to exponentiate."""
        rng = np.random.default_rng(5)
        n = 800
        x = rng.uniform(0.0, 0.002, size=n)
        frame = pl.DataFrame({"x": x, "y": rng.poisson(np.exp(0.5 + 800 * x)).astype(float)})

        result = _run_glm(
            tmp_path, frame, {"family": "poisson", "terms": {"x": {"type": "linear"}}}
        )
        response = _worker_response(result)

        assert [entry["diagnostic"] for entry in result.diagnostics_errors] == ["glm_relativities"]
        assert "Relativities are not finite for ['x']" in result.diagnostics_errors[0]["error"]
        assert result.glm_relativities == []
        assert [row["feature"] for row in response["glm_coefficients"]] == ["Intercept", "x"]
        assert result.glm_inference["valid"] is True

    def test_robust_standard_errors_are_computed_on_the_fitted_model_before_it_is_saved(
        self, tmp_path
    ) -> None:
        """RustyStats keeps the design matrix only in memory, so the robust table
        is extracted during training and delivered in the result; the saved model
        scores exactly like the same fit without robust standard errors."""
        from haute._mlflow_io import load_local_model

        rng = np.random.default_rng(5)
        n = 800
        x = rng.uniform(0.0, 1.0, size=n)
        frame = pl.DataFrame(
            {
                "x": x,
                "z": rng.normal(size=n),
                "y": rng.poisson(np.exp(0.2 + x)).astype(float),
            }
        )
        params = {"family": "poisson", "terms": {"x": {"type": "linear"}, "z": {"type": "linear"}}}

        robust = _run_glm(tmp_path / "robust", frame, {**params, "robust_standard_errors": "HC1"})
        plain = _run_glm(tmp_path / "plain", frame, params)
        response = _worker_response(robust)

        assert robust.diagnostics_errors == [], robust.diagnostics_errors
        assert response["glm_inference"]["standard_errors"] == "HC1"
        robust_errors = [row["std_error"] for row in response["glm_coefficients"]]
        plain_errors = [row["std_error"] for row in plain.glm_coefficients]
        assert all(error is not None for error in robust_errors)
        assert not np.allclose(robust_errors, plain_errors)
        np.testing.assert_allclose(
            [row["coefficient"] for row in robust.glm_coefficients],
            [row["coefficient"] for row in plain.glm_coefficients],
        )
        np.testing.assert_allclose(
            load_local_model(robust.model_path).predict(frame),
            load_local_model(plain.model_path).predict(frame),
        )

    def test_identity_link_has_no_relativities(self, tmp_path) -> None:
        rng = np.random.default_rng(6)
        n = 400
        x = rng.uniform(0.0, 1.0, size=n)
        frame = pl.DataFrame({"x": x, "y": 1.0 + 2.0 * x + rng.normal(size=n)})

        result = _run_glm(
            tmp_path, frame, {"family": "gaussian", "terms": {"x": {"type": "linear"}}}
        )

        assert result.diagnostics_errors == [], result.diagnostics_errors
        assert result.glm_relativities == []
        assert result.glm_inference["valid"] is True
        assert [row["feature"] for row in result.glm_coefficients] == ["Intercept", "x"]


# ---------------------------------------------------------------------------
# Gap 13: Null-target cleaning in _prepare_data() tested
# ---------------------------------------------------------------------------


class TestNullTargetCleaning:
    """Verify null targets are dropped during data preparation."""

    def test_null_targets_are_dropped_before_training(self, tmp_path):
        from haute.modelling._training_job import TrainingJob

        df = pl.DataFrame(
            {
                "feature": [1, 2, 3, 4, 5],
                "target": [1.0, None, 3.0, None, 5.0],
            }
        )

        job = TrainingJob(
            name="test_null_clean",
            data=df,
            target="target",
            algorithm="glm",
            params={"family": "gaussian", "terms": {"feature": {"type": "linear"}}},
            output_dir=str(tmp_path),
        )

        def _report(msg, frac):
            pass

        prepared = job._prepare_data(_report)
        try:
            result_df = pl.read_parquet(prepared.data_path)
            assert result_df.height == 3  # 2 nulls dropped
            assert result_df["target"].null_count() == 0
        finally:
            if prepared.owns_tmp:
                import os

                os.unlink(prepared.data_path)


# ---------------------------------------------------------------------------
# End-to-end: terms, expressions, and interaction specs through one real fit
# ---------------------------------------------------------------------------


def _write(df: pl.DataFrame, tmp_path) -> str:
    """Write ``df`` where a TrainingJob can read it, returning the path."""
    path = tmp_path / "training.parquet"
    df.write_parquet(path)
    return str(path)


def _random_evaluation() -> dict[str, object]:
    return {
        "schema_version": 1,
        "strategy": "random",
        "seed": 42,
        "validation": {"method": "single", "size": 0.2},
    }


# Kwargs ``generate_training_script`` deliberately leaves off the call because
# ``TrainingJob``'s own default builds the identical job (``exclude or []``
# makes ``None`` and ``[]`` the same). Anything else the template drops would
# change the model the script trains.
_EXPORT_DEFAULTED_KWARGS: dict[str, object] = {
    "weight": None,
    "exclude": [],
    "feature_columns": None,
    "fold_column": None,
    "id_columns": None,
    "tuning": None,
    "refit_on_development": True,
    "loss_function": None,
    "variance_power": None,
    "offset": None,
    "monotone_constraints": None,
    "feature_weights": None,
    "categorical_levels": None,
    "positive_class": None,
    "mlflow_experiment": None,
    "mlflow_destination": "",
}


def _captured_export_kwargs(script: str) -> dict[str, Any]:
    """Run a generated script and return the kwargs it hands ``TrainingJob``.

    Searching the script text only proves a token appears somewhere in it.
    Executing the script against a recording stub proves the exported job is
    *constructed* with the values live training passes.
    """
    captured: dict[str, Any] = {}

    class _RecordingTrainingJob:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

        def run(self) -> None:
            return None

    # Not "__main__", so the script's run-it guard stays shut.
    namespace: dict[str, Any] = {"__name__": "haute_export_parity_harness"}
    with patch("haute.modelling.TrainingJob", _RecordingTrainingJob):
        exec(compile(script, "<export>", "exec"), namespace)
    assert captured, "the generated script never constructed a TrainingJob"
    return captured


def _assert_export_parity(script: str, expected: dict[str, Any]) -> dict[str, Any]:
    """Assert the script builds exactly the job ``build_training_job_kwargs`` describes."""
    captured = _captured_export_kwargs(script)
    invented = sorted(set(captured) - set(expected))
    assert invented == [], invented
    mismatched = {
        key: (expected[key], captured[key]) for key in captured if captured[key] != expected[key]
    }
    assert mismatched == {}, mismatched
    omitted = {key: expected[key] for key in expected if key not in captured}
    assert omitted == {
        key: _EXPORT_DEFAULTED_KWARGS.get(key, "<not a TrainingJob default>") for key in omitted
    }, omitted
    return captured


class TestGLMTermsEndToEnd:
    """One real fit proves the shipped GLM term contract end to end."""

    def test_native_expression_and_interaction_with_materialised_main_and_local_spline(
        self, tmp_path
    ):
        """A native spline, an expression term, and an interaction whose factor
        carries its own spec all reach the design matrix; each materialised
        main effect appears exactly once (RustyStats' ``include_main`` would
        duplicate it), and the exported script trains a model whose predictions
        match the live one to floating-point noise."""
        from haute._mlflow_io import load_local_model
        from haute.modelling._export import generate_training_script
        from haute.modelling._train_config import build_training_job_kwargs
        from haute.modelling._training_job import TrainingJob

        rng = np.random.default_rng(11)
        n = 600
        age = rng.uniform(18, 80, size=n)
        density = rng.uniform(0.0, 2.0, size=n)
        income = rng.normal(size=n)
        region = rng.choice(["n", "s", "e"], size=n)
        y = rng.poisson(np.exp(-1.5 + 0.01 * age + 0.3 * density + 0.2 * income)).astype(float)
        df = pl.DataFrame(
            {"age": age, "density": density, "income": income, "region": region, "y": y}
        )
        data_path = _write(df, tmp_path)
        config = {
            "algorithm": "glm",
            "target": "y",
            "family": "poisson",
            "terms": {
                "age": {"type": "bs", "df": 4},
                "density": {"type": "linear"},
                "density_sq": {"type": "expression", "expr": "density ** 2"},
            },
            "interactions": [
                {
                    "factors": ["income", "region"],
                    "specs": {"income": {"type": "bs", "df": 4}},
                    "include_main": True,
                }
            ],
            "evaluation": _random_evaluation(),
        }
        job = TrainingJob(
            **build_training_job_kwargs(
                {**config, "name": "e2e", "output_dir": str(tmp_path)}, data=data_path
            )
        )
        result = job.run()

        # The coefficient table names the fitted design columns; an empty table
        # means a diagnostic failed rather than the fit succeeding quietly.
        assert result.diagnostics_errors == [], result.diagnostics_errors
        assert result.glm_coefficients
        names = [row["feature"] for row in result.glm_coefficients]
        assert result.glm_inference["valid"] is True
        assert any(name.startswith("bs(age") for name in names), names
        assert "I(density ** 2)" in names, names
        assert any(":bs(income" in name for name in names), names
        assert sum(name == "region[T.s]" for name in names) == 1, names  # materialised once
        income_mains = [name for name in names if name.startswith("bs(income") and ":" not in name]
        # The slot's own df=4 basis (one column dropped for the intercept), once.
        assert len(income_mains) == 3, names

        output_dir = tmp_path / "exported"
        output_dir.mkdir()
        export_config = {**config, "name": "e2e_export", "output_dir": str(output_dir)}
        script = generate_training_script(export_config, data_path)
        captured = _assert_export_parity(
            script, build_training_job_kwargs(export_config, data=data_path)
        )
        assert captured["params"]["terms"] == config["terms"]
        assert captured["params"]["interactions"] == config["interactions"]

        exported = TrainingJob(**captured).run()
        assert exported.diagnostics_errors == [], exported.diagnostics_errors
        assert [row["feature"] for row in exported.glm_coefficients] == names
        np.testing.assert_allclose(
            load_local_model(exported.model_path).predict(df),
            load_local_model(result.model_path).predict(df),
            rtol=1e-9,
        )

    def test_exported_script_carries_the_offset_and_its_exposure_scaling(self, tmp_path):
        """Under the canonical log link an offset is a multiplier, so an export
        that drops the column silently trains an unexposed model."""
        from haute._mlflow_io import load_local_model
        from haute.modelling._export import generate_training_script
        from haute.modelling._train_config import build_training_job_kwargs
        from haute.modelling._training_job import TrainingJob

        rng = np.random.default_rng(7)
        n = 600
        x = rng.uniform(1.0, 5.0, size=n)
        exposure = rng.uniform(0.5, 2.0, size=n)
        y = rng.poisson(exposure * np.exp(-1.0 + 0.3 * x)).astype(float)
        df = pl.DataFrame({"x": x, "exposure": exposure, "y": y})
        data_path = _write(df, tmp_path)
        output_dir = tmp_path / "exported"
        output_dir.mkdir()
        config = {
            "algorithm": "glm",
            "target": "y",
            "family": "poisson",
            "offset": "exposure",
            "terms": {"x": {"type": "linear"}},
            "evaluation": _random_evaluation(),
            "name": "offset_export",
            "output_dir": str(output_dir),
        }

        script = generate_training_script(config, data_path)
        captured = _assert_export_parity(script, build_training_job_kwargs(config, data=data_path))
        assert captured["offset"] == "exposure"

        exported = TrainingJob(**captured).run()
        assert exported.diagnostics_errors == [], exported.diagnostics_errors
        model = load_local_model(exported.model_path)
        head = df.head(50)
        predictions = model.predict(head)
        doubled = model.predict(head.with_columns(pl.col("exposure") * 2.0))
        np.testing.assert_allclose(doubled, 2.0 * predictions, rtol=1e-9)


def test_all_factors_is_gone():
    from pathlib import Path

    this_file = Path(__file__).resolve()
    root = this_file.parents[1]
    offenders: list[str] = []
    for folder, pattern in (
        ("src", '"all_factors"'),
        ("tests", '"all_factors"'),
        ("specs", "all_factors"),
        ("docs", "all_factors"),
    ):
        for path in (root / folder).rglob("*"):
            if (
                path.suffix not in {".py", ".md", ".ts", ".tsx"}
                or "node_modules" in path.parts
                or "static" in path.parts
                or path.resolve() == this_file  # this test names the token it forbids
            ):
                continue
            if pattern in path.read_text(encoding="utf-8", errors="ignore"):
                offenders.append(path.relative_to(root).as_posix())
    # The roadmap documents the removal by name; everything else must be clean.
    assert offenders == ["specs/roadmap/modelling.md"], offenders
