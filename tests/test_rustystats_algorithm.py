"""Tests for GLMAlgorithm — RustyStats integration."""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import polars as pl
import pytest

# ---------------------------------------------------------------------------
# Skip if RustyStats is not installed
# ---------------------------------------------------------------------------
rs = pytest.importorskip("rustystats", reason="rustystats not installed")


from haute.modelling._rustystats import (  # noqa: E402 - import after importorskip guard
    GLMAlgorithm,
    _auto_terms,
    _build_interactions,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def sample_df() -> pl.DataFrame:
    """Small DataFrame for testing GLM fits."""
    np.random.seed(42)
    n = 500
    return pl.DataFrame(
        {
            "driver_age": np.random.randint(18, 70, n),
            "vehicle_age": np.random.randint(0, 20, n),
            "area": np.random.choice(["A", "B", "C", "D"], n),
            "exposure": np.random.uniform(0.5, 1.0, n),
            "claim_count": np.random.poisson(0.1, n),
        }
    )


@pytest.fixture()
def algo() -> GLMAlgorithm:
    return GLMAlgorithm()


# ---------------------------------------------------------------------------
# _auto_terms
# ---------------------------------------------------------------------------


class TestAutoTerms:
    def test_numeric_features_become_linear(self):
        terms = _auto_terms(["age", "income"], [])
        assert terms == {
            "age": {"type": "linear"},
            "income": {"type": "linear"},
        }

    def test_cat_features_become_categorical(self):
        terms = _auto_terms(["age", "region"], ["region"])
        assert terms["age"] == {"type": "linear"}
        assert terms["region"] == {"type": "categorical"}

    def test_empty_features(self):
        assert _auto_terms([], []) == {}


# ---------------------------------------------------------------------------
# _build_interactions
# ---------------------------------------------------------------------------


class TestBuildInteractions:
    def test_basic_interaction(self):
        terms = {
            "age": {"type": "linear"},
            "region": {"type": "categorical"},
        }
        config = [{"factors": ["age", "region"], "include_main": True}]
        result = _build_interactions(config, terms)
        assert len(result) == 1
        assert result[0]["age"] == {"type": "linear"}
        assert result[0]["region"] == {"type": "categorical"}
        # include_main is forced to False when all factors are already in terms
        # (avoids duplicate main effects causing singularity)
        assert result[0]["include_main"] is False

    def test_include_main_when_factor_not_in_terms(self):
        terms = {"age": {"type": "linear"}}
        config = [{"factors": ["age", "region"], "include_main": True}]
        result = _build_interactions(config, terms)
        assert len(result) == 1
        # region is NOT in terms, so include_main stays True
        assert result[0]["include_main"] is True

    def test_interaction_skips_single_factor(self):
        terms = {"age": {"type": "linear"}}
        config = [{"factors": ["age"], "include_main": True}]
        result = _build_interactions(config, terms)
        assert result == []

    def test_unknown_factor_gets_categorical_fallback(self):
        terms = {"age": {"type": "linear"}}
        config = [{"factors": ["age", "unknown"], "include_main": False}]
        result = _build_interactions(config, terms)
        assert result[0]["unknown"] == {"type": "categorical"}
        assert result[0]["include_main"] is False

    def test_empty_interactions(self):
        assert _build_interactions([], {"a": {"type": "linear"}}) == []


# ---------------------------------------------------------------------------
# GLMAlgorithm.fit()
# ---------------------------------------------------------------------------


class TestGLMFit:
    def test_fit_poisson_auto_terms(self, algo, sample_df):
        """Fit a Poisson GLM with auto-generated terms."""
        features = ["driver_age", "vehicle_age", "area"]
        cat_features = ["area"]

        result = algo.fit(
            train_df=sample_df,
            features=features,
            cat_features=cat_features,
            target="claim_count",
            weight="exposure",
            params={"family": "poisson"},
            task="regression",
        )

        assert result.model is not None
        assert result.best_iteration is not None
        assert len(result.loss_history) > 0
        assert "train_deviance" in result.loss_history[0]

    def test_fit_with_explicit_terms(self, algo, sample_df):
        """Fit with user-specified term types."""
        result = algo.fit(
            train_df=sample_df,
            features=["driver_age", "vehicle_age", "area"],
            cat_features=["area"],
            target="claim_count",
            weight="exposure",
            params={
                "family": "poisson",
                "terms": {
                    "driver_age": {"type": "linear"},
                    "vehicle_age": {"type": "linear"},
                    "area": {"type": "categorical"},
                },
            },
            task="regression",
        )
        assert result.model is not None

    def test_fit_gaussian(self, algo, sample_df):
        """Fit a Gaussian (OLS) GLM."""
        result = algo.fit(
            train_df=sample_df,
            features=["driver_age", "vehicle_age"],
            cat_features=[],
            target="claim_count",
            weight=None,
            params={"family": "gaussian"},
            task="regression",
        )
        assert result.model is not None

    def test_fit_with_offset(self, algo, sample_df):
        """Fit with exposure as offset (RustyStats requires positive offset for Poisson/log)."""
        result = algo.fit(
            train_df=sample_df,
            features=["driver_age", "vehicle_age"],
            cat_features=[],
            target="claim_count",
            weight=None,
            params={"family": "poisson"},
            task="regression",
            offset="exposure",
        )
        assert result.model is not None

    def test_fit_with_interactions(self, algo):
        """Fit with interaction terms (two linear features)."""
        np.random.seed(123)
        n = 5000
        driver_age = np.random.randint(20, 65, n).astype(float)
        vehicle_age = np.random.randint(0, 15, n).astype(float)
        rate = np.exp(-2.0 + 0.01 * driver_age - 0.02 * vehicle_age)
        claim_count = np.random.poisson(rate)
        df = pl.DataFrame(
            {
                "driver_age": driver_age,
                "vehicle_age": vehicle_age,
                "exposure": np.ones(n),
                "claim_count": claim_count,
            }
        )
        result = algo.fit(
            train_df=df,
            features=["driver_age", "vehicle_age"],
            cat_features=[],
            target="claim_count",
            weight="exposure",
            params={
                "family": "poisson",
                "terms": {
                    "driver_age": {"type": "linear"},
                    "vehicle_age": {"type": "linear"},
                },
                "interactions": [
                    {"factors": ["driver_age", "vehicle_age"], "include_main": True},
                ],
            },
            task="regression",
        )
        assert result.model is not None

    def test_fit_with_monotone_constraints(self, algo, sample_df):
        """Monotone constraints from top-level config are applied to terms."""
        result = algo.fit(
            train_df=sample_df,
            features=["driver_age", "vehicle_age"],
            cat_features=[],
            target="claim_count",
            weight=None,
            params={"family": "poisson"},
            task="regression",
            monotone_constraints={"driver_age": -1},
        )
        assert result.model is not None

    def test_fit_requires_dataframe(self, algo):
        """fit() should raise if train_df is None."""
        with pytest.raises(ValueError, match="requires train_df"):
            algo.fit(
                None,
                [],
                [],
                "target",
                None,
                {},
                "regression",
            )

    def test_fit_calls_on_iteration(self, algo, sample_df):
        """Verify iteration callback is called at start and end."""
        calls = []

        def callback(it, total, metrics):
            calls.append((it, total))

        algo.fit(
            train_df=sample_df,
            features=["driver_age"],
            cat_features=[],
            target="claim_count",
            weight=None,
            params={"family": "poisson"},
            task="regression",
            on_iteration=callback,
        )
        assert (0, 1) in calls  # start signal
        assert (1, 1) in calls  # completion signal

    def test_fit_with_regularization(self, algo, sample_df):
        """Fit with lasso regularization."""
        result = algo.fit(
            train_df=sample_df,
            features=["driver_age", "vehicle_age"],
            cat_features=[],
            target="claim_count",
            weight=None,
            params={
                "family": "poisson",
                "regularization": "lasso",
            },
            task="regression",
        )
        assert result.model is not None


# ---------------------------------------------------------------------------
# GLMAlgorithm.predict()
# ---------------------------------------------------------------------------


class TestGLMPredict:
    def test_predict_returns_ndarray(self, algo, sample_df):
        fit_result = algo.fit(
            train_df=sample_df,
            features=["driver_age", "vehicle_age"],
            cat_features=[],
            target="claim_count",
            weight=None,
            params={"family": "poisson"},
            task="regression",
        )
        preds = algo.predict(fit_result.model, sample_df, ["driver_age", "vehicle_age"])
        assert isinstance(preds, np.ndarray)
        assert preds.shape == (len(sample_df),)
        assert np.all(np.isfinite(preds))

    def test_predict_poisson_positive(self, algo, sample_df):
        """Poisson predictions should be non-negative."""
        fit_result = algo.fit(
            train_df=sample_df,
            features=["driver_age"],
            cat_features=[],
            target="claim_count",
            weight=None,
            params={"family": "poisson"},
            task="regression",
        )
        preds = algo.predict(fit_result.model, sample_df, ["driver_age"])
        assert np.all(preds >= 0)


# ---------------------------------------------------------------------------
# GLMAlgorithm.feature_importance()
# ---------------------------------------------------------------------------


class TestGLMFeatureImportance:
    def test_returns_sorted_list(self, algo, sample_df):
        fit_result = algo.fit(
            train_df=sample_df,
            features=["driver_age", "vehicle_age"],
            cat_features=[],
            target="claim_count",
            weight=None,
            params={"family": "poisson"},
            task="regression",
        )
        importance = algo.feature_importance(fit_result.model)
        assert len(importance) > 0
        assert all("feature" in item and "importance" in item for item in importance)
        # Should be sorted descending by importance
        imps = [item["importance"] for item in importance]
        assert imps == sorted(imps, reverse=True)


# ---------------------------------------------------------------------------
# GLMAlgorithm.save() and model loading
# ---------------------------------------------------------------------------


class TestGLMSaveLoad:
    def test_save_and_load_roundtrip(self, algo, sample_df):
        """Save model, load it back, and verify predictions match."""
        fit_result = algo.fit(
            train_df=sample_df,
            features=["driver_age", "vehicle_age"],
            cat_features=[],
            target="claim_count",
            weight=None,
            params={"family": "poisson"},
            task="regression",
        )
        preds_original = algo.predict(
            fit_result.model,
            sample_df,
            ["driver_age", "vehicle_age"],
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "model.rsglm"
            algo.save(fit_result.model, path)
            assert path.exists()
            assert path.stat().st_size > 0

            # Load via _mlflow_io
            from haute._mlflow_io import load_local_model

            scoring_model = load_local_model(str(path))
            assert scoring_model.flavor == "rustystats"

            preds_loaded = scoring_model.predict(sample_df)
            np.testing.assert_allclose(preds_original, preds_loaded, rtol=1e-6)


# ---------------------------------------------------------------------------
# GLM-specific diagnostics
# ---------------------------------------------------------------------------


class TestGLMDiagnostics:
    @pytest.fixture()
    def fitted_model(self, algo, sample_df):
        fit_result = algo.fit(
            train_df=sample_df,
            features=["driver_age", "vehicle_age", "area"],
            cat_features=["area"],
            target="claim_count",
            weight="exposure",
            params={"family": "poisson"},
            task="regression",
        )
        return fit_result.model

    def test_coefficients_table(self, algo, fitted_model):
        table = algo.coefficients_table(fitted_model)
        assert len(table) > 0
        first = table[0]
        # Check normalized keys
        assert "feature" in first
        assert "coefficient" in first
        assert "p_value" in first

    def test_relativities(self, algo, fitted_model):
        rels = algo.relativities(fitted_model)
        assert len(rels) > 0
        first = rels[0]
        # Check normalized keys
        assert "feature" in first
        assert "relativity" in first
        assert first["relativity"] > 0  # exp(coef) is always positive

    def test_fit_statistics(self, algo, fitted_model):
        stats = algo.fit_statistics(fitted_model)
        assert "deviance" in stats
        assert "aic" in stats
        assert "bic" in stats
        assert stats["deviance"] > 0

    def test_fit_statistics_convergence(self, algo, fitted_model):
        stats = algo.fit_statistics(fitted_model)
        assert "converged" in stats
        assert stats["converged"] == 1.0

    def test_coefficients_table_deserialized_model_fails_loud(self, algo, fitted_model):
        """A round-tripped (to_bytes/from_bytes) model has no covariance data,
        so real inference statistics cannot be computed.

        Characterises the real trigger for the old fabrication path: the
        deserialized RustyStats result lacks bse()/tvalues()/pvalues().
        The contract is to raise — never to return SE=0.0 / p=1.0 rows.
        Today the deserialized result also lacks ``coefficients`` (it only
        stores ``params``), so the fallback fails on attribute access; if a
        future RustyStats exposes coefficients there, the stats block raises
        GLMInferenceUnavailableError instead. Either way: loud, no table.
        """
        from haute.modelling._rustystats import GLMInferenceUnavailableError

        loaded = rs.GLMModel.from_bytes(fitted_model.to_bytes())

        with pytest.raises((AttributeError, GLMInferenceUnavailableError)):
            algo.coefficients_table(loaded)


# ---------------------------------------------------------------------------
# GLMAlgorithm.cross_validate() — removed in Phase 2 Package 2C-5.
# The orchestrator (``TrainingJob``) no longer calls CV, so the method
# and its tests were deleted. AIC/BIC remain on the GLM fit statistics.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Integration: TrainingJob with GLM
# ---------------------------------------------------------------------------


class TestTrainingJobGLM:
    def test_training_job_glm_basic(self, sample_df):
        """Full TrainingJob pipeline with GLM."""
        from haute.modelling import TrainingJob

        with tempfile.TemporaryDirectory() as tmpdir:
            job = TrainingJob(
                name="test_glm",
                data=sample_df,
                target="claim_count",
                weight="exposure",
                algorithm="glm",
                task="regression",
                params={
                    "family": "poisson",
                    "terms": {
                        "driver_age": {"type": "linear"},
                        "vehicle_age": {"type": "linear"},
                        "area": {"type": "categorical"},
                    },
                },
                split={"strategy": "random", "validation_size": 0.2, "seed": 42},
                metrics=["gini", "poisson_deviance"],
                output_dir=tmpdir,
            )
            result = job.run()

            assert result.model_path.endswith(".rsglm")
            assert Path(result.model_path).exists()
            assert result.train_rows > 0
            assert result.validation_rows > 0
            assert "gini" in result.metrics
            assert len(result.feature_importance) > 0

            # GLM-specific fields populated
            assert len(result.glm_coefficients) > 0
            assert len(result.glm_relativities) > 0
            assert "deviance" in result.glm_fit_statistics
            assert "aic" in result.glm_fit_statistics

    def test_training_job_glm_auto_terms(self, sample_df):
        """TrainingJob with GLM auto-generates terms when not specified."""
        from haute.modelling import TrainingJob

        with tempfile.TemporaryDirectory() as tmpdir:
            job = TrainingJob(
                name="test_glm_auto",
                data=sample_df,
                target="claim_count",
                algorithm="glm",
                params={"family": "gaussian"},
                output_dir=tmpdir,
            )
            result = job.run()
            assert result.model_path.endswith(".rsglm")
            assert len(result.features) > 0


# ---------------------------------------------------------------------------
# Negative Binomial theta threading + dispersion estimation
# ---------------------------------------------------------------------------


@pytest.fixture()
def nb_df() -> pl.DataFrame:
    """Overdispersed count frame with a known dispersion.

    Gamma-Poisson mixture with gamma shape 2.0 — the true NB theta is 2.0.
    The profile-likelihood MLE on this exact draw is 2.4487, cross-validated
    against statsmodels NegativeBinomial(loglike_method="nb2"): 1/alpha_hat =
    2.4487, with coefficient parity to 4 d.p. (theta = 1/alpha under NB2).
    """
    rng = np.random.default_rng(42)
    n = 400
    x1 = rng.normal(0, 1, n)
    x2 = rng.normal(0, 1, n)
    mu = np.exp(0.5 + 0.4 * x1 - 0.3 * x2)
    lam = rng.gamma(2.0, mu / 2.0)
    return pl.DataFrame({"x1": x1, "x2": x2, "y": rng.poisson(lam).astype(float)})


_NB_TERMS = {"x1": {"type": "linear"}, "x2": {"type": "linear"}}


class TestNegBinomialThetaThreading:
    def test_unset_theta_is_the_silent_default(self, nb_df):
        """Pin the failover the gate exists to close: RustyStats does not
        estimate theta — an unset theta fits bit-identically to theta=1.0."""
        unset = rs.glm_dict(response="y", terms=_NB_TERMS, data=nb_df, family="negbinomial").fit()
        explicit = rs.glm_dict(
            response="y", terms=_NB_TERMS, data=nb_df, family="negbinomial", theta=1.0
        ).fit()
        assert list(unset.coefficients) == list(explicit.coefficients)
        assert float(unset.deviance) == float(explicit.deviance)

    def test_theta_param_reaches_the_fit(self, algo, nb_df):
        """params["theta"] must change the fitted model — the whole gate is
        pointless if the threaded value never reaches RustyStats."""

        def fit_deviance(params):
            result = algo.fit(
                train_df=nb_df,
                features=["x1", "x2"],
                cat_features=[],
                target="y",
                weight=None,
                params=params,
                task="regression",
            )
            return float(result.model.deviance)

        base = {"family": "negbinomial", "terms": _NB_TERMS}
        dev_theta_1 = fit_deviance({**base, "theta": 1.0})
        dev_theta_5 = fit_deviance({**base, "theta": 5.0})
        assert dev_theta_1 != dev_theta_5


class TestEstimateGlmDispersion:
    def test_nb_theta_profile_mle_matches_statsmodels_reference(self, nb_df):
        """Golden value: statsmodels NB2 MLE on this exact draw gives
        1/alpha = 2.4487 (betas match rustystats to 4 d.p.). Pinned as a
        literal so statsmodels is not a test dependency."""
        from haute.modelling._rustystats import estimate_glm_dispersion

        est = estimate_glm_dispersion(
            data=nb_df,
            terms=_NB_TERMS,
            target="y",
            family="negbinomial",
            param="theta",
        )
        assert est.param == "theta"
        assert est.value == pytest.approx(2.4487, abs=0.01)
        assert est.llf == pytest.approx(-693.038, abs=0.05)
        assert est.n_fits > 0

    def test_estimate_is_deterministic(self, nb_df):
        from haute.modelling._rustystats import estimate_glm_dispersion

        kwargs = dict(data=nb_df, terms=_NB_TERMS, target="y", family="negbinomial", param="theta")
        first = estimate_glm_dispersion(**kwargs)
        second = estimate_glm_dispersion(**kwargs)
        assert first.value == second.value
        assert first.n_fits == second.n_fits

    def test_tweedie_var_power_finds_interior_maximum(self, nb_df):
        from haute.modelling._rustystats import estimate_glm_dispersion

        rng = np.random.default_rng(7)
        n = 400
        x1 = rng.normal(0, 1, n)
        mu = np.exp(0.3 + 0.5 * x1)
        y = np.where(rng.random(n) < 0.3, 0.0, rng.gamma(2.0, mu / 2.0))
        frame = pl.DataFrame({"x1": x1, "y": y})

        est = estimate_glm_dispersion(
            data=frame,
            terms={"x1": {"type": "linear"}},
            target="y",
            family="tweedie",
            param="var_power",
        )
        assert est.param == "var_power"
        assert 1.01 < est.value < 1.99

    def test_on_fit_callback_can_abort(self, nb_df):
        from haute.modelling._rustystats import estimate_glm_dispersion

        class _StopError(RuntimeError):
            pass

        def abort_immediately(fit_index: int) -> None:
            raise _StopError()

        with pytest.raises(_StopError):
            estimate_glm_dispersion(
                data=nb_df,
                terms=_NB_TERMS,
                target="y",
                family="negbinomial",
                param="theta",
                on_fit=abort_immediately,
            )

    def test_unknown_param_rejected(self, nb_df):
        from haute.modelling._rustystats import estimate_glm_dispersion

        with pytest.raises(ValueError, match="Unknown dispersion parameter"):
            estimate_glm_dispersion(
                data=nb_df, terms=_NB_TERMS, target="y", family="negbinomial", param="alpha"
            )

    def test_family_param_mismatch_rejected(self, nb_df):
        from haute.modelling._rustystats import estimate_glm_dispersion

        with pytest.raises(ValueError, match="belongs to the negbinomial family"):
            estimate_glm_dispersion(
                data=nb_df, terms=_NB_TERMS, target="y", family="poisson", param="theta"
            )


class TestBuildInteractionsEmptySlots:
    def test_unfilled_interaction_row_is_skipped(self):
        """The config panel's "+ Add" persists {"factors": ["", ""]} until both
        columns are picked; such rows must not reach RustyStats (they crash the
        fit — and the dispersion estimate — with "Interaction must have at
        least 2 variables")."""
        terms = {"a": {"type": "linear"}, "b": {"type": "categorical"}}
        assert _build_interactions([{"factors": ["", ""], "include_main": True}], terms) == []
        assert _build_interactions([{"factors": ["a", ""], "include_main": True}], terms) == []
        assert len(_build_interactions([{"factors": ["a", "b"]}], terms)) == 1
