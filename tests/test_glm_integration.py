"""Regression tests for GLM integration gaps.

Covers four critical areas:
1. Config key merge — GLM-specific keys flow from top-level config into train_params.
2. Terms referencing non-existent columns — clear error when terms don't match data.
3. Offset/weight/target survival through feature narrowing via _glm_select_columns.
4. Diagnostic fallback — coefficients_table builds result when coef_table() raises.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import numpy as np
import polars as pl
import pytest

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
            "params": {"iterations": 100},  # CatBoost-style param (should survive)
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
        # Pre-existing param should also survive
        assert train_params["iterations"] == 100

    def test_glm_config_keys_do_not_overwrite_params(self):
        """Keys already in params are NOT overwritten by top-level config."""
        from haute.modelling._train_config import build_train_params

        config = {
            "algorithm": "glm",
            "params": {"family": "gaussian"},  # already in params
            "family": "poisson",  # top-level — should NOT overwrite
        }
        train_params = build_train_params(config)

        assert train_params["family"] == "gaussian"  # params version wins

    def test_missing_glm_keys_are_skipped(self):
        """Only keys actually present in config are merged; no KeyError."""
        from haute.modelling._train_config import build_train_params

        config: dict = {"algorithm": "glm", "params": {}, "family": "tweedie"}
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
            "intercept",
            "var_power",
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
            offset="log_exposure",
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
        assert "log_exposure" in columns  # offset

    def test_glm_select_columns_without_weight_or_offset(self):
        """When weight and offset are None, only features + target are returned."""
        from haute.modelling._training_job import TrainingJob

        job = TrainingJob(
            name="test_no_extras",
            data="dummy.parquet",
            target="y",
            algorithm="glm",
            params={"family": "gaussian"},
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
            params={"family": "gaussian"},
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
# Gap 4: Diagnostic fallback — coefficients_table
# ---------------------------------------------------------------------------


class TestCoefficientsTableFallback:
    """When model.coef_table() raises, the fallback path must build the
    table from individual arrays (feature_names, coefficients, bse, etc.)."""

    def test_coefficients_table_primary_normalises_plain_z_column(self):
        """RustyStats coef_table() may emit a plain `z` column; API rows use z_value."""
        from haute.modelling._rustystats import GLMAlgorithm

        algo = GLMAlgorithm()

        class CoefTable:
            def to_dicts(self):
                return [
                    {
                        "Feature": "Intercept",
                        "Estimate": 0.25,
                        "Std. Error": 0.1,
                        "z": 2.5,
                        "Pr(>|z|)": 0.012,
                        "Signif": "*",
                    }
                ]

        model = MagicMock()
        model.coef_table.return_value = CoefTable()

        result = algo.coefficients_table(model)

        assert result == [
            {
                "feature": "Intercept",
                "coefficient": 0.25,
                "std_error": 0.1,
                "z_value": 2.5,
                "p_value": 0.012,
                "significance": "*",
            }
        ]
        assert "z" not in result[0]

    def test_coefficients_table_fallback_with_intercept(self):
        """Fallback builds correct table when coef_table() raises and there's an intercept."""
        from haute.modelling._rustystats import GLMAlgorithm

        algo = GLMAlgorithm()

        model = MagicMock()
        model.coef_table.side_effect = AttributeError("no coef_table")
        model.feature_names = ["age", "region"]
        # 3 coefs for 2 features → intercept is prepended
        model.coefficients = [0.5, -0.3, 1.2]
        model.bse.return_value = [0.1, 0.05, 0.08]
        model.tvalues.return_value = [5.0, -6.0, 15.0]
        model.pvalues.return_value = [0.001, 0.0001, 1e-10]
        model.significance_codes.return_value = ["**", "***", "***"]

        result = algo.coefficients_table(model)

        assert len(result) == 3
        # _align_coefs_and_names prepends "(Intercept)" to names when
        # len(coefs) > len(names), so result order is:
        # 0: (Intercept) paired with coefs[0]=0.5
        # 1: age         paired with coefs[1]=-0.3
        # 2: region      paired with coefs[2]=1.2
        assert result[0]["feature"] == "(Intercept)"
        assert result[0]["coefficient"] == pytest.approx(0.5)
        assert result[0]["std_error"] == pytest.approx(0.1)
        assert result[0]["z_value"] == pytest.approx(5.0)
        assert result[0]["p_value"] == pytest.approx(0.001)
        assert result[0]["significance"] == "**"

        assert result[1]["feature"] == "age"
        assert result[1]["coefficient"] == pytest.approx(-0.3)

        assert result[2]["feature"] == "region"
        assert result[2]["coefficient"] == pytest.approx(1.2)

    def test_coefficients_table_fallback_no_intercept(self):
        """Fallback works when coef count == feature count (no intercept)."""
        from haute.modelling._rustystats import GLMAlgorithm

        algo = GLMAlgorithm()

        model = MagicMock()
        model.coef_table.side_effect = RuntimeError("broken")
        model.feature_names = ["x1", "x2"]
        model.coefficients = [0.7, -0.4]  # same length → no intercept prepend
        model.bse.return_value = [0.02, 0.03]
        model.tvalues.return_value = [35.0, -13.3]
        model.pvalues.return_value = [0.0, 0.0]
        model.significance_codes.return_value = ["***", "***"]

        result = algo.coefficients_table(model)

        assert len(result) == 2
        assert result[0]["feature"] == "x1"
        assert result[0]["coefficient"] == pytest.approx(0.7)
        assert result[1]["feature"] == "x2"
        assert result[1]["coefficient"] == pytest.approx(-0.4)

    def test_coefficients_table_inference_unavailable_raises(self):
        """When coef_table() AND bse() both fail, refuse to fabricate SE=0/p=1.

        The old behaviour invented std_error=0.0 (infinitely precise!) and
        p_value=1.0 and rendered them as real statistics. The contract is now
        fail-loud: raise GLMInferenceUnavailableError so the training job
        records a diagnostics error and omits the table entirely.
        """
        from haute.modelling._rustystats import (
            GLMAlgorithm,
            GLMInferenceUnavailableError,
        )

        algo = GLMAlgorithm()

        model = MagicMock()
        model.coef_table.side_effect = AttributeError("no coef_table")
        model.feature_names = ["a"]
        model.coefficients = [2.0, 3.0]  # 2 coefs, 1 name → intercept
        bse_error = RuntimeError("no bse")
        model.bse.side_effect = bse_error

        with pytest.raises(GLMInferenceUnavailableError) as exc_info:
            algo.coefficients_table(model)

        message = str(exc_info.value)
        # Names what is missing and why — the UI shows this verbatim.
        assert "inference statistics" in message
        assert "no bse" in message
        assert "no coef_table" in message
        # Root cause is chained for debugging.
        assert exc_info.value.__cause__ is bse_error

    def test_coefficients_table_partial_inference_failure_raises(self):
        """If ANY of bse/tvalues/pvalues/significance_codes fails, raise.

        A table with real SEs but fabricated p-values would be just as
        misleading as the all-fabricated one, so partial availability is
        treated as unavailable.
        """
        from haute.modelling._rustystats import (
            GLMAlgorithm,
            GLMInferenceUnavailableError,
        )

        algo = GLMAlgorithm()

        model = MagicMock()
        model.coef_table.side_effect = AttributeError("no coef_table")
        model.feature_names = ["x1"]
        model.coefficients = [0.5]
        model.bse.return_value = [0.1]
        model.tvalues.return_value = [5.0]
        model.pvalues.side_effect = RuntimeError("pvalues exploded")

        with pytest.raises(GLMInferenceUnavailableError, match="pvalues exploded"):
            algo.coefficients_table(model)

    def test_coefficients_table_short_inference_arrays_raise(self):
        """Stats arrays shorter than the coefficient vector must not be padded.

        The old code padded missing tail entries with std_error=0.0 /
        p_value=1.0 — fabrication by index. A library returning misaligned
        arrays is a contract violation and must fail loud.
        """
        from haute.modelling._rustystats import (
            GLMAlgorithm,
            GLMInferenceUnavailableError,
        )

        algo = GLMAlgorithm()

        model = MagicMock()
        model.coef_table.side_effect = AttributeError("no coef_table")
        model.feature_names = ["a", "b"]
        model.coefficients = [2.0, 3.0, 4.0]  # 3 coefs (intercept + 2)
        model.bse.return_value = [0.1]  # too short — covers 1 of 3
        model.tvalues.return_value = [1.0]
        model.pvalues.return_value = [0.5]
        model.significance_codes.return_value = [""]

        with pytest.raises(GLMInferenceUnavailableError, match="cover"):
            algo.coefficients_table(model)


class TestInferenceUnavailableDiagnostics:
    """End-to-end: inference-unavailable GLM run omits the table and records
    a diagnostics error the UI can show, instead of fabricating stats."""

    def test_training_run_omits_table_and_records_diagnostics_error(
        self,
        tmp_path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A run whose model cannot produce inference stats must surface
        diagnostic='glm_coefficients' in diagnostics_errors and ship an
        empty glm_coefficients list (frontend contract: rows require all
        stat fields, so the table is omitted at table level)."""
        rustystats_formula = pytest.importorskip(
            "rustystats.formula", reason="rustystats optional dependency not installed"
        )
        from haute.modelling._training_job import TrainingJob

        def _raise_coef_table(self):  # noqa: ANN001 - patched method
            raise AttributeError("no coef_table")

        def _raise_bse(self):  # noqa: ANN001 - patched method
            raise RuntimeError("covariance unavailable")

        monkeypatch.setattr(rustystats_formula.GLMModel, "coef_table", _raise_coef_table)
        # bse is normally delegated via __getattr__, so set it directly.
        monkeypatch.setattr(rustystats_formula.GLMModel, "bse", _raise_bse, raising=False)

        rng = np.random.RandomState(42)
        n = 60
        df = pl.DataFrame({"x1": rng.randn(n), "y": rng.randn(n)})
        job = TrainingJob(
            name="glm_no_inference",
            data=df,
            target="y",
            algorithm="glm",
            params={"family": "gaussian", "terms": {"x1": {"type": "linear"}}},
            output_dir=str(tmp_path),
        )

        result = job.run()

        # No fabricated rows — the table is omitted entirely.
        assert result.glm_coefficients == []
        # The skip is surfaced as a structured diagnostics error.
        coef_errors = [
            entry
            for entry in result.diagnostics_errors
            if entry["diagnostic"] == "glm_coefficients"
        ]
        assert len(coef_errors) == 1
        assert coef_errors[0]["error_type"] == "GLMInferenceUnavailableError"
        assert "covariance unavailable" in coef_errors[0]["error"]


# ---------------------------------------------------------------------------
# Gap 11: glm_diagnostics() is never tested
# ---------------------------------------------------------------------------


class TestGLMDiagnostics:
    """Verify glm_diagnostics() success and failure paths."""

    def test_glm_diagnostics_returns_dict_on_success(self):
        from haute.modelling._rustystats import GLMAlgorithm

        algo = GLMAlgorithm()
        model = MagicMock()
        diag_mock = MagicMock()
        diag_mock.to_dict.return_value = {"ae_by_feature": {"age": [1.0, 1.1]}}
        model.diagnostics.return_value = diag_mock

        data = pl.DataFrame({"age": [25, 30], "region": ["A", "B"], "y": [1.0, 2.0]})
        result = algo.glm_diagnostics(
            model, data, cat_features=["region"], features=["age", "region"]
        )

        assert result == {"ae_by_feature": {"age": [1.0, 1.1]}}
        model.diagnostics.assert_called_once()

    def test_glm_diagnostics_returns_empty_on_failure(self):
        from haute.modelling._rustystats import GLMAlgorithm

        algo = GLMAlgorithm()
        model = MagicMock()
        model.diagnostics.side_effect = RuntimeError("diagnostics unavailable")

        data = pl.DataFrame({"x": [1, 2], "y": [1.0, 2.0]})
        result = algo.glm_diagnostics(model, data, cat_features=[], features=["x"])

        assert result == {}


# ---------------------------------------------------------------------------
# Gap 12: relativities() double-fallback path untested
# ---------------------------------------------------------------------------


class TestRelativitiesFallback:
    """Verify relativities() fallback paths."""

    def test_relativities_fallback_without_conf_int(self):
        from haute.modelling._rustystats import GLMAlgorithm

        algo = GLMAlgorithm()
        model = MagicMock()
        model.relativities.side_effect = AttributeError("no relativities")
        model.coefficients = [0.5, -0.3]
        model.feature_names = ["a", "b"]
        model.conf_int.side_effect = RuntimeError("no conf_int")

        result = algo.relativities(model)

        assert len(result) == 2
        assert result[0]["feature"] == "a"
        assert result[0]["relativity"] == pytest.approx(np.exp(0.5))
        assert "ci_lower" not in result[0]
        assert result[1]["feature"] == "b"
        assert result[1]["relativity"] == pytest.approx(np.exp(-0.3))

    def test_relativities_fallback_with_conf_int(self):
        from haute.modelling._rustystats import GLMAlgorithm

        algo = GLMAlgorithm()
        model = MagicMock()
        model.relativities.side_effect = AttributeError("no relativities")
        model.coefficients = [0.5, -0.3]
        model.feature_names = ["a", "b"]
        model.conf_int.return_value = [[0.1, 0.9], [-0.8, 0.2]]

        result = algo.relativities(model)

        assert len(result) == 2
        assert result[0]["relativity"] == pytest.approx(np.exp(0.5))
        assert result[0]["ci_lower"] == pytest.approx(np.exp(0.1))
        assert result[0]["ci_upper"] == pytest.approx(np.exp(0.9))
        assert result[1]["ci_lower"] == pytest.approx(np.exp(-0.8))
        assert result[1]["ci_upper"] == pytest.approx(np.exp(0.2))

    def test_relativities_with_intercept(self):
        from haute.modelling._rustystats import GLMAlgorithm

        algo = GLMAlgorithm()
        model = MagicMock()
        model.relativities.side_effect = AttributeError("no relativities")
        model.coefficients = [0.1, 0.5, -0.3]  # 3 coefs, 2 names → intercept
        model.feature_names = ["a", "b"]
        model.conf_int.side_effect = RuntimeError("no conf_int")

        result = algo.relativities(model)

        assert len(result) == 3
        assert result[0]["feature"] == "(Intercept)"
        assert result[0]["relativity"] == pytest.approx(np.exp(0.1))


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
