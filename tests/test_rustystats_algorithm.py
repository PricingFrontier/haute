"""Tests for GLMAlgorithm — RustyStats integration."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
import pytest

# ---------------------------------------------------------------------------
# Skip if RustyStats is not installed
# ---------------------------------------------------------------------------
rs = pytest.importorskip("rustystats", reason="rustystats not installed")


from haute.errors import HauteValidationError  # noqa: E402 - import after importorskip guard
from haute.modelling._rustystats import (  # noqa: E402 - import after importorskip guard
    GLMAlgorithm,
    _build_glm_builder_kwargs,
    estimate_glm_dispersion,
    glm_fit_kwargs,
    prepare_glm_design,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def sample_df() -> pl.DataFrame:
    """Small DataFrame for testing GLM fits."""
    rng = np.random.default_rng(42)
    n = 500
    return pl.DataFrame(
        {
            "driver_age": rng.integers(18, 70, n),
            "vehicle_age": rng.integers(0, 20, n),
            "area": rng.choice(["A", "B", "C", "D"], n),
            "exposure": rng.uniform(0.5, 1.0, n),
            "claim_count": rng.poisson(0.1, n).astype(float),
        }
    )


_SAMPLE_TERMS = {
    "driver_age": {"type": "linear"},
    "vehicle_age": {"type": "linear"},
    "area": {"type": "categorical"},
}


@pytest.fixture()
def algo() -> GLMAlgorithm:
    return GLMAlgorithm()


@pytest.fixture()
def interaction_df() -> pl.DataFrame:
    rng = np.random.default_rng(0)
    n = 400
    return pl.DataFrame(
        {
            "x": rng.normal(size=n),
            "z": rng.normal(size=n),
            "w": rng.normal(size=n),
            "c": rng.choice(["a", "b", "c"], size=n),
            "d": rng.choice(["p", "q"], size=n),
            "flag": rng.choice([True, False], size=n),
            "y": rng.poisson(2.0, size=n).astype(float),
        }
    )


def _fit(
    algo: GLMAlgorithm,
    frame: pl.DataFrame,
    params: dict[str, Any],
    *,
    target: str = "y",
    weight: str | None = None,
    offset: str | None = None,
    **kwargs: Any,
) -> Any:
    features = [name for name in frame.columns if name not in {target, weight, offset}]
    return algo.fit(
        train_df=frame,
        features=features,
        cat_features=[],
        target=target,
        weight=weight,
        params=params,
        task="regression",
        offset=offset,
        **kwargs,
    )


def _design_columns(frame: pl.DataFrame, params: dict[str, Any]) -> list[str]:
    """Column names RustyStats builds for the design Haute resolves from ``params``."""
    from rustystats.formula import dict_to_parsed_formula
    from rustystats.interactions import InteractionBuilder

    terms, interactions = prepare_glm_design(params, frame)
    parsed = dict_to_parsed_formula("y", terms, interactions, intercept=True)
    _y, _design, names = InteractionBuilder(frame).build_design_matrix_from_parsed(parsed)
    return list(names)


# ---------------------------------------------------------------------------
# The resolved design RustyStats fits
# ---------------------------------------------------------------------------


class TestResolvedDesign:
    def test_a_glm_without_terms_is_refused(self, algo, interaction_df):
        with pytest.raises(HauteValidationError, match="GLM config has no terms"):
            _fit(algo, interaction_df, {"family": "poisson"})

    def test_materialised_main_effects_appear_once(self, interaction_df):
        params = {
            "family": "poisson",
            "terms": {"x": {"type": "linear"}},
            "interactions": [
                {"factors": ["x", "z"], "include_main": True},
                {"factors": ["z", "w"], "include_main": True},
            ],
        }
        assert _design_columns(interaction_df, params) == [
            "Intercept",
            "x",
            "z",
            "w",
            "x:z",
            "z:w",
        ]

    def test_include_main_false_leaves_a_factor_without_main_effect(self, interaction_df):
        params = {
            "family": "poisson",
            "terms": {"x": {"type": "linear"}},
            "interactions": [{"factors": ["x", "z"], "include_main": False}],
        }
        assert _design_columns(interaction_df, params) == ["Intercept", "x", "x:z"]

    def test_boolean_factor_defaults_to_categorical(self, interaction_df):
        params = {
            "family": "poisson",
            "terms": {"x": {"type": "linear"}},
            "interactions": [{"factors": ["x", "flag"], "include_main": True}],
        }
        names = _design_columns(interaction_df, params)
        assert "flag" not in names
        assert any(name.startswith("flag[T.") for name in names), names

    def test_local_spline_override_produces_an_interaction_local_basis(self, interaction_df):
        params = {
            "family": "poisson",
            "terms": {"x": {"type": "bs", "df": 4}, "c": {"type": "categorical"}},
            "interactions": [
                {
                    "factors": ["x", "c"],
                    "specs": {"x": {"type": "bs", "df": 6}},
                    "include_main": False,
                }
            ],
        }
        names = _design_columns(interaction_df, params)
        assert "bs(x, 2/4)" in names and "bs(x, 4/4)" in names
        assert "c[T.b]:bs(x, 2/6)" in names and "c[T.b]:bs(x, 6/6)" in names

    def test_linear_override_on_a_spline_main_forces_a_linear_column(self, interaction_df):
        params = {
            "family": "poisson",
            "terms": {"x": {"type": "bs", "df": 4}, "c": {"type": "categorical"}},
            "interactions": [
                {"factors": ["x", "c"], "specs": {"x": {"type": "linear"}}, "include_main": False}
            ],
        }
        names = _design_columns(interaction_df, params)
        assert "c[T.b]:x" in names and "c[T.b]:bs(x, 2/4)" not in names

    def test_categorical_slot_over_a_materialised_categorical_main_builds(self, interaction_df):
        params = {
            "family": "poisson",
            "terms": {"w": {"type": "linear"}},
            "interactions": [
                {"factors": ["c", "z"], "include_main": True},
                {
                    "factors": ["c", "w"],
                    "specs": {"c": {"type": "categorical"}},
                    "include_main": False,
                },
            ],
        }
        names = _design_columns(interaction_df, params)
        assert names.count("c[T.b]") == 1
        assert "c[T.b]:z" in names and "c[T.b]:w" in names

    def test_reference_level_shares_the_intercept(self, algo, interaction_df):
        params = {"family": "poisson", "terms": {"c": {"type": "categorical", "reference": "b"}}}
        model = _fit(algo, interaction_df.select("c", "y"), params).model
        # Level-restricted categoricals are named without the treatment prefix.
        assert model.feature_names == ["Intercept", "c[a]", "c[c]"]

    def test_unobserved_reference_level_is_refused_at_fit(self, algo, interaction_df):
        params = {"family": "poisson", "terms": {"c": {"type": "categorical", "reference": "z"}}}
        with pytest.raises(HauteValidationError, match="reference level 'z' is not in the"):
            _fit(algo, interaction_df.select("c", "y"), params)

    @pytest.mark.parametrize(
        ("primary", "additional"),
        [("target_encoding", "frequency_encoding"), ("frequency_encoding", "target_encoding")],
    )
    def test_multiple_encodings_fit_save_and_score_raw_source(
        self, algo, tmp_path, primary, additional
    ):
        from haute._mlflow_io import load_local_model
        from haute.modelling._glm_pyfunc import GLMPyfuncModel

        rng = np.random.default_rng(72)
        region = rng.choice(["a", "b", "c", "d", "e"], size=500, p=[0.4, 0.25, 0.2, 0.1, 0.05])
        means = {"a": 1.0, "b": 3.0, "c": 2.0, "d": 5.0, "e": 4.0}
        df = pl.DataFrame(
            {"region": region, "y": [means[r] for r in region] + rng.normal(size=500)}
        )
        terms = {
            "region": {"type": primary},
            "region_extra": {"type": additional, "variable": "region"},
        }
        model = _fit(algo, df, {"family": "gaussian", "terms": terms}).model
        assert set(model.feature_names) == {"Intercept", "TE(region)", "FE(region)"}
        holdout = pl.DataFrame({"region": ["a", "b", "unseen"]})
        expected = model.predict(holdout)
        path = tmp_path / "encodings.rsglm"
        algo.save(model, path)
        loaded = load_local_model(str(path))
        assert loaded.feature_names == ["region"]
        np.testing.assert_allclose(loaded.predict(holdout), expected)
        # This uses the same projection/scoring path as Model Score and deployment.
        np.testing.assert_allclose(GLMPyfuncModel(str(path)).predict(holdout), expected)


class TestProductTargetEncoding:
    def test_slot_encoding_registers_its_main_effect_with_its_parameters(self, interaction_df):
        from rustystats.formula import dict_to_parsed_formula
        from rustystats.interactions import InteractionBuilder

        frame = interaction_df
        params = {
            "family": "poisson",
            "terms": {"x": {"type": "bs", "df": 4}},
            "interactions": [
                {
                    "factors": ["c", "z"],
                    "include_main": False,
                    "specs": {
                        "c": {"type": "target_encoding", "prior_weight": 0, "n_permutations": 2},
                    },
                }
            ],
        }
        terms, interactions = prepare_glm_design(params, frame)
        assert terms == {
            "x": {"type": "bs", "df": 4},
            "c": {"type": "target_encoding", "prior_weight": 0, "n_permutations": 2},
        }
        parsed = dict_to_parsed_formula("y", terms, interactions)
        assert len(parsed.target_encoding_terms) == 1
        assert parsed.target_encoding_terms[0].prior_weight == 0
        assert parsed.target_encoding_terms[0].n_permutations == 2
        _, matrix, names = InteractionBuilder(frame).build_design_matrix_from_parsed(parsed)
        assert "bs(x, 2/4)" in names
        assert names.count("TE(c)") == 1
        np.testing.assert_allclose(
            matrix[:, names.index("z:TE(c)")],
            frame["z"].to_numpy() * matrix[:, names.index("TE(c)")],
        )

    def test_inherited_encoding_with_multiple_linear_partners(self, interaction_df):
        params = {
            "family": "poisson",
            "terms": {"c": {"type": "target_encoding", "prior_weight": 2, "n_permutations": 3}},
            "interactions": [{"factors": ["c", "x", "z"], "include_main": False}],
        }
        assert set(_design_columns(interaction_df, params)) == {"Intercept", "TE(c)", "x:z:TE(c)"}

    def test_fit_predict_and_save_load_raw_columns(self, algo, interaction_df, tmp_path):
        from haute._mlflow_io import load_local_model
        from haute.modelling._glm_pyfunc import GLMPyfuncModel

        frame = interaction_df.select("c", "x", "y")
        params = {
            "family": "poisson",
            "terms": {"x": {"type": "linear"}},
            "interactions": [
                {
                    "factors": ["c", "x"],
                    "include_main": False,
                    "specs": {
                        "c": {"type": "target_encoding", "prior_weight": 3, "n_permutations": 2}
                    },
                }
            ],
        }
        model = _fit(algo, frame, params).model
        assert set(model.feature_names) == {"Intercept", "x", "TE(c)", "x:TE(c)"}
        holdout = pl.DataFrame({"c": ["a", "unseen", "b"], "x": [0.1, -0.2, 0.7]})
        predictions = algo.predict(model, holdout, ["c", "x"])
        assert np.isfinite(predictions).all()
        path = tmp_path / "product_te.rsglm"
        algo.save(model, path)
        loaded = load_local_model(str(path))
        assert set(loaded.feature_names) == {"c", "x"}
        np.testing.assert_allclose(loaded.predict(holdout), predictions)
        np.testing.assert_allclose(GLMPyfuncModel(str(path)).predict(holdout), predictions)


class TestEncodedInteractions:
    @pytest.mark.parametrize("encoding", ["target_encoding", "frequency_encoding"])
    def test_fit_predict_and_save_load_joint_encoding(
        self, encoding, algo, interaction_df, tmp_path
    ):
        from haute._mlflow_io import load_local_model

        settings = (
            {"prior_weight": 1.0, "n_permutations": 2} if encoding == "target_encoding" else {}
        )
        params = {
            "family": "poisson",
            "terms": {
                "x": {"type": "linear"},
                "c": {"type": "target_encoding", "prior_weight": 1.0},
                "d": {"type": "frequency_encoding"},
            },
            "interactions": [
                {"factors": ["c", "d"], "encoding": encoding, "include_main": False, **settings},
            ],
        }
        model = _fit(algo, interaction_df.select("x", "c", "d", "y"), params).model
        prefix = "TE" if encoding == "target_encoding" else "FE"
        assert set(model.feature_names) == {"Intercept", "x", "TE(c)", "FE(d)", f"{prefix}(c:d)"}
        holdout = pl.DataFrame(
            {"x": [0.1, -0.2, 0.7], "c": ["a", "new", "b"], "d": ["p", "q", "new"]}
        )
        predictions = algo.predict(model, holdout, ["x", "c", "d"])
        assert np.isfinite(predictions).all()
        path = tmp_path / "joint.rsglm"
        algo.save(model, path)
        np.testing.assert_allclose(load_local_model(str(path)).predict(holdout), predictions)

    @pytest.mark.parametrize("encoding", ["target_encoding", "frequency_encoding"])
    def test_joint_encoding_adds_one_column_and_no_factor_main_effects(
        self, encoding, interaction_df
    ):
        card = {"factors": ["flag", "c"], "encoding": encoding, "include_main": False}
        params = {"family": "poisson", "terms": {"x": {"type": "linear"}}, "interactions": [card]}
        names = _design_columns(interaction_df, params)
        assert len(names) == 3
        assert names[:2] == ["Intercept", "x"]


# ---------------------------------------------------------------------------
# GLMAlgorithm.fit()
# ---------------------------------------------------------------------------


class TestGLMFit:
    @pytest.mark.parametrize(
        ("kind", "interaction"),
        [("bs", False), ("ns", False), ("ms", False), ("bs", True), ("ns", True)],
    )
    def test_omitted_df_selects_smoothing_and_numeric_df_selects_fixed(
        self, kind, interaction, algo, interaction_df
    ):
        frame = interaction_df.select("x", "c", "y")
        for extra in ({}, {"df": 5}):
            spec = {"type": kind, **extra}
            terms = {"c": {"type": "categorical"}} if interaction else {"x": spec}
            interactions = (
                [{"factors": ["x", "c"], "specs": {"x": spec}, "include_main": False}]
                if interaction
                else []
            )
            params = {"family": "gaussian", "terms": terms, "interactions": interactions}
            model = _fit(algo, frame, params).model
            assert model.has_smooth_terms() is (not extra)
            assert np.isfinite(algo.predict(model, frame, ["x", "c"])).all()

    def test_frequency_encoding_native_fit(self, algo, interaction_df):
        frame = interaction_df.select("c", "y")
        params = {"family": "poisson", "terms": {"c": {"type": "frequency_encoding"}}}
        model = _fit(algo, frame, params).model
        assert model.feature_names == ["Intercept", "FE(c)"]
        assert np.isfinite(algo.predict(model, frame, ["c"])).all()

    def test_fit_poisson_with_a_weight(self, algo, sample_df):
        result = _fit(
            algo,
            sample_df,
            {"family": "poisson", "terms": _SAMPLE_TERMS},
            target="claim_count",
            weight="exposure",
        )
        assert result.best_iteration is not None
        assert "train_deviance" in result.loss_history[0]
        assert result.model.feature_names == [
            "Intercept",
            "driver_age",
            "vehicle_age",
            "area[T.B]",
            "area[T.C]",
            "area[T.D]",
        ]

    def test_fit_with_interactions(self, algo):
        rng = np.random.default_rng(123)
        n = 5000
        driver_age = rng.integers(20, 65, n).astype(float)
        vehicle_age = rng.integers(0, 15, n).astype(float)
        rate = np.exp(-2.0 + 0.01 * driver_age - 0.02 * vehicle_age)
        df = pl.DataFrame(
            {
                "driver_age": driver_age,
                "vehicle_age": vehicle_age,
                "claim_count": rng.poisson(rate).astype(float),
            }
        )
        params = {
            "family": "poisson",
            "terms": {"driver_age": {"type": "linear"}, "vehicle_age": {"type": "linear"}},
            "interactions": [{"factors": ["driver_age", "vehicle_age"], "include_main": True}],
        }
        model = _fit(algo, df, params, target="claim_count").model
        assert model.feature_names == [
            "Intercept",
            "driver_age",
            "vehicle_age",
            "driver_age:vehicle_age",
        ]

    @pytest.mark.parametrize(
        ("kwargs", "message"),
        [
            ({"monotone_constraints": {"x": -1}}, "monotone_constraints is a CatBoost lever"),
            ({"feature_weights": {"x": 2.0}}, "feature_weights is a CatBoost lever"),
        ],
    )
    def test_catboost_levers_are_refused(self, algo, interaction_df, kwargs, message):
        params = {"family": "poisson", "terms": {"x": {"type": "linear"}}}
        with pytest.raises(HauteValidationError, match=message):
            _fit(algo, interaction_df.select("x", "y"), params, **kwargs)

    def test_monotonicity_lives_on_the_term(self, algo, interaction_df):
        params = {
            "family": "poisson",
            "terms": {"x": {"type": "linear", "monotonicity": "decreasing"}},
        }
        model = _fit(algo, interaction_df.select("x", "y"), params).model
        assert model.inference_status == "constrained_boundary"
        assert float(model.params[1]) <= 0.0

    def test_fit_requires_dataframe(self, algo):
        with pytest.raises(ValueError, match="requires train_df"):
            algo.fit(None, [], [], "target", None, {}, "regression")

    def test_fit_calls_on_iteration(self, algo, interaction_df):
        calls = []

        def callback(iteration, total, metrics):
            calls.append((iteration, total))

        _fit(
            algo,
            interaction_df.select("x", "y"),
            {"family": "poisson", "terms": {"x": {"type": "linear"}}},
            on_iteration=callback,
        )
        assert calls == [(0, 1), (1, 1)]


# ---------------------------------------------------------------------------
# Regularisation, solver, and family controls
# ---------------------------------------------------------------------------


@pytest.fixture()
def penalty_df() -> pl.DataFrame:
    rng = np.random.default_rng(5)
    n = 800
    x = rng.uniform(0, 1, n)
    z = rng.normal(size=n)
    return pl.DataFrame({"x": x, "z": z, "y": rng.poisson(np.exp(0.2 + x)).astype(float)})


_PENALTY_TERMS = {"x": {"type": "linear"}, "z": {"type": "linear"}}
_CV = {"cv_folds": 4, "cv_selection": "1se", "cv_seed": 7}


class TestRegularizationAndSolverControls:
    @pytest.mark.parametrize(
        ("penalty", "l1_ratio"),
        [
            ({"regularization": "ridge"}, 0.0),
            ({"regularization": "lasso"}, 1.0),
            ({"regularization": "elastic_net", "l1_ratio": 0.4}, 0.4),
        ],
    )
    def test_fixed_alpha_reaches_rustystats_without_cross_validation(
        self, algo, penalty_df, penalty, l1_ratio
    ):
        params = {"family": "poisson", "terms": _PENALTY_TERMS, "alpha": 0.5, **penalty}
        assert glm_fit_kwargs(params) == {"alpha": 0.5, "l1_ratio": l1_ratio}

        model = _fit(algo, penalty_df, params).model
        assert float(model.alpha) == 0.5
        assert model.n_cv_folds is None
        summary = algo.glm_result(model, params).regularization
        assert summary is not None
        assert summary["mode"] == "fixed"
        assert summary["alpha"] == 0.5
        assert summary["cv_folds"] is None

    def test_cross_validation_is_seeded_and_reproducible(self, algo, penalty_df):
        params = {"family": "poisson", "terms": _PENALTY_TERMS, "regularization": "ridge", **_CV}
        assert glm_fit_kwargs(params) == {
            "regularization": "ridge",
            "cv": 4,
            "selection": "1se",
            "cv_seed": 7,
        }
        assert glm_fit_kwargs(
            {**params, "regularization": "elastic_net", "l1_ratio": 0.3, "alpha": 0}
        ) == {
            "regularization": "elastic_net",
            "cv": 4,
            "selection": "1se",
            "cv_seed": 7,
            "l1_ratio": 0.3,
        }

        first = _fit(algo, penalty_df, params).model
        second = _fit(algo, penalty_df, params).model
        assert float(first.alpha) == float(second.alpha)
        np.testing.assert_array_equal(np.asarray(first.params), np.asarray(second.params))

    def test_solver_controls_reach_the_fit_only_when_set(self, algo, penalty_df):
        params = {"family": "poisson", "terms": _PENALTY_TERMS}
        assert glm_fit_kwargs(params) == {}
        controlled = {**params, "max_iter": 50, "tol": 1e-6, "robust_standard_errors": "HC1"}
        assert glm_fit_kwargs(controlled) == {
            "max_iter": 50,
            "tol": 1e-6,
            "store_design_matrix": True,
        }
        assert bool(_fit(algo, penalty_df, controlled).model.converged)

    def test_robust_standard_errors_use_hc_statistics(self, algo, penalty_df):
        params = {"family": "poisson", "terms": _PENALTY_TERMS, "robust_standard_errors": "HC1"}
        model = _fit(algo, penalty_df, params).model
        report = algo.glm_result(model, params)

        assert report.errors == []
        assert report.inference == {
            "status": "valid_standard",
            "valid": True,
            "standard_errors": "HC1",
            "reason": None,
        }
        robust = np.asarray(model.bse_robust("HC1"))
        np.testing.assert_allclose([row["std_error"] for row in report.coefficients], robust)
        assert not np.allclose(robust, np.asarray(model.bse()))
        lower = np.exp(np.asarray(model.conf_int_robust(alpha=0.05, cov_type="HC1"))[:, 0])
        np.testing.assert_allclose([row["ci_lower"] for row in report.relativities], lower)

    def test_quasibinomial_fits_with_logit_link(self, algo):
        rng = np.random.default_rng(9)
        n = 800
        x = rng.uniform(0, 1, n)
        frame = pl.DataFrame(
            {"x": x, "y": rng.binomial(1, 1 / (1 + np.exp(0.5 - x))).astype(float)}
        )
        params = {"family": "quasibinomial", "terms": {"x": {"type": "linear"}}}
        model = _fit(algo, frame, params).model
        report = algo.glm_result(model, params)

        assert model.link == "logit"
        assert report.errors == []
        assert report.relativities == []
        assert "log_likelihood" not in report.fit_statistics
        assert "aic" not in report.fit_statistics

    def test_tweedie_endpoints_fit_with_extended_support(self, algo):
        rng = np.random.default_rng(4)
        n = 600
        x = rng.normal(size=n)
        frame = pl.DataFrame({"x": x, "y": rng.gamma(2.0, np.exp(0.3 + 0.2 * x) / 2.0)})
        for power in (1.0, 2.0):
            params = {"family": "tweedie", "var_power": power, "terms": {"x": {"type": "linear"}}}
            assert _build_glm_builder_kwargs(
                target="y", terms=params["terms"], data=frame, params=params
            )["allow_extended_tweedie"]
            assert bool(_fit(algo, frame, params).model.converged)
        interior = {"family": "tweedie", "var_power": 1.5, "terms": {"x": {"type": "linear"}}}
        assert "allow_extended_tweedie" not in _build_glm_builder_kwargs(
            target="y", terms=interior["terms"], data=frame, params=interior
        )


# ---------------------------------------------------------------------------
# GLMAlgorithm.predict(), feature_importance(), and save()
# ---------------------------------------------------------------------------


class TestGLMPredictAndSave:
    @pytest.fixture()
    def fitted(self, algo, sample_df):
        return _fit(
            algo,
            sample_df.drop("exposure"),
            {"family": "poisson", "terms": _SAMPLE_TERMS},
            target="claim_count",
        ).model

    def test_predict_returns_positive_finite_rates(self, algo, fitted, sample_df):
        predictions = algo.predict(fitted, sample_df, list(_SAMPLE_TERMS))
        assert isinstance(predictions, np.ndarray)
        assert predictions.shape == (len(sample_df),)
        assert np.all(np.isfinite(predictions)) and np.all(predictions > 0)

    def test_feature_importance_is_sorted_by_coefficient_magnitude(self, algo, fitted):
        importance = algo.feature_importance(fitted)
        assert [row["feature"] for row in importance] == sorted(
            fitted.feature_names,
            key=lambda name: abs(float(fitted.params[fitted.feature_names.index(name)])),
            reverse=True,
        )

    def test_save_and_load_roundtrip(self, algo, fitted, sample_df, tmp_path: Path):
        from haute._mlflow_io import load_local_model

        path = tmp_path / "model.rsglm"
        algo.save(fitted, path)
        scoring_model = load_local_model(str(path))
        assert scoring_model.flavor == "rustystats"
        np.testing.assert_allclose(
            algo.predict(fitted, sample_df, list(_SAMPLE_TERMS)),
            scoring_model.predict(sample_df),
            rtol=1e-6,
        )


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
_NB_PARAMS = {"family": "negbinomial", "terms": _NB_TERMS}


class TestNegBinomialThetaThreading:
    def test_unset_negbinomial_theta_raises_on_rustystats(self, nb_df):
        """RustyStats 0.9 refuses a Negative Binomial fit without theta — there
        is no silent theta=1.0 any more. Haute's gate still requires an explicit
        value; this pins that the library backs the gate up."""
        from rustystats.exceptions import ValidationError

        with pytest.raises(ValidationError, match="requires an explicit theta"):
            rs.glm_dict(response="y", terms=_NB_TERMS, data=nb_df, family="negbinomial").fit()

    def test_theta_param_reaches_the_fit(self, algo, nb_df):
        def fit_deviance(theta: float) -> float:
            return float(_fit(algo, nb_df, {**_NB_PARAMS, "theta": theta}).model.deviance)

        assert fit_deviance(1.0) != fit_deviance(5.0)


class TestEstimateGlmDispersion:
    def test_nb_theta_profile_mle_matches_statsmodels_reference(self, nb_df):
        """Golden value: statsmodels NB2 MLE on this exact draw gives
        1/alpha = 2.4487 (betas match rustystats to 4 d.p.). Pinned as a
        literal so statsmodels is not a test dependency."""
        est = estimate_glm_dispersion(data=nb_df, params=_NB_PARAMS, target="y", param="theta")
        assert est.param == "theta"
        assert est.value == pytest.approx(2.4487, abs=0.01)
        assert est.llf == pytest.approx(-693.038, abs=0.05)
        assert est.n_fits > 0

    def test_estimate_is_deterministic(self, nb_df):
        kwargs = {"data": nb_df, "params": _NB_PARAMS, "target": "y", "param": "theta"}
        first = estimate_glm_dispersion(**kwargs)
        second = estimate_glm_dispersion(**kwargs)
        assert first.value == second.value
        assert first.n_fits == second.n_fits

    def test_tweedie_var_power_finds_interior_maximum(self):
        rng = np.random.default_rng(7)
        n = 400
        x1 = rng.normal(0, 1, n)
        mu = np.exp(0.3 + 0.5 * x1)
        y = np.where(rng.random(n) < 0.3, 0.0, rng.gamma(2.0, mu / 2.0))
        est = estimate_glm_dispersion(
            data=pl.DataFrame({"x1": x1, "y": y}),
            params={"family": "tweedie", "terms": {"x1": {"type": "linear"}}},
            target="y",
            param="var_power",
        )
        assert est.param == "var_power"
        assert 1.01 < est.value < 1.99

    def test_the_profile_fits_the_resolved_design(self, nb_df):
        """An unobserved reference level is refused before any candidate fit."""
        frame = nb_df.with_columns(pl.Series("g", ["a", "b"] * 200))
        params = {
            "family": "negbinomial",
            "terms": {**_NB_TERMS, "g": {"type": "categorical", "reference": "z"}},
        }
        with pytest.raises(HauteValidationError, match="reference level 'z'"):
            estimate_glm_dispersion(data=frame, params=params, target="y", param="theta")

    def test_on_fit_callback_can_abort(self, nb_df):
        class _StopError(RuntimeError):
            pass

        def abort_immediately(fit_index: int) -> None:
            raise _StopError()

        with pytest.raises(_StopError):
            estimate_glm_dispersion(
                data=nb_df,
                params=_NB_PARAMS,
                target="y",
                param="theta",
                on_fit=abort_immediately,
            )

    def test_unknown_param_rejected(self, nb_df):
        with pytest.raises(ValueError, match="Unknown dispersion parameter"):
            estimate_glm_dispersion(data=nb_df, params=_NB_PARAMS, target="y", param="alpha")

    def test_family_param_mismatch_rejected(self, nb_df):
        with pytest.raises(ValueError, match="belongs to the negbinomial family"):
            estimate_glm_dispersion(
                data=nb_df,
                params={"family": "poisson", "terms": _NB_TERMS},
                target="y",
                param="theta",
            )


# ---------------------------------------------------------------------------
# Offset → exposure semantics on RustyStats 0.9
# ---------------------------------------------------------------------------


@pytest.fixture()
def exposure_df() -> pl.DataFrame:
    """Poisson counts whose rate scales with an exposure column ``e``."""
    rng = np.random.default_rng(7)
    n = 2000
    x = rng.normal(size=n)
    e = rng.uniform(0.2, 2.0, size=n)
    y = rng.poisson(e * np.exp(0.3 + 0.5 * x)).astype(float)
    return pl.DataFrame({"x": x, "e": e, "y": y})


_EXPOSURE_TERMS = {"x": {"type": "linear"}}


class TestOffsetExposureSemantics:
    def _fit(self, algo, df, params):
        return _fit(algo, df, {"terms": _EXPOSURE_TERMS, **params}, offset="e")

    @pytest.mark.parametrize(
        ("params", "key"),
        [
            ({"family": "poisson"}, "exposure"),
            ({"family": "gaussian", "link": "log"}, "exposure"),
            ({"family": "gaussian"}, "offset"),
            ({"family": "binomial"}, "offset"),
        ],
    )
    def test_builder_routes_the_offset_by_the_effective_link(self, params, key):
        frame = pl.DataFrame({"y": [1.0], "x": [0.0], "e": [1.0]})
        kwargs = _build_glm_builder_kwargs(
            target="y", terms=_EXPOSURE_TERMS, data=frame, params=params, offset="e"
        )
        assert kwargs[key] == "e"
        assert {"exposure", "offset"} - {key} <= {"exposure", "offset"} - set(kwargs)
        without = _build_glm_builder_kwargs(
            target="y", terms=_EXPOSURE_TERMS, data=frame, params=params
        )
        assert "exposure" not in without and "offset" not in without

    def test_log_link_offset_predictions_equal_exp_of_log_exposure_plus_linear_predictor(
        self, algo, exposure_df
    ):
        """Canonical-link path (no explicit link): the offset column is a multiplier."""
        model = self._fit(algo, exposure_df, {"family": "poisson"}).model
        coef = dict(zip(model.feature_names, np.asarray(model.params)))
        head = exposure_df.head(50)
        preds = algo.predict(model, head, ["x"], offset="e")
        expected = np.exp(
            np.log(head["e"].to_numpy()) + coef["Intercept"] + coef["x"] * head["x"].to_numpy()
        )
        np.testing.assert_allclose(preds, expected, rtol=1e-9)

        doubled = algo.predict(model, head.with_columns(pl.col("e") * 2.0), ["x"], offset="e")
        np.testing.assert_allclose(doubled, 2.0 * preds, rtol=1e-9)

    def test_explicit_log_link_on_gaussian_maps_offset_to_exposure(self, algo, exposure_df):
        """Explicit-link path: gaussian is identity by default, log when asked."""
        model = self._fit(algo, exposure_df, {"family": "gaussian", "link": "log"}).model
        head = exposure_df.head(50)
        preds = algo.predict(model, head, ["x"], offset="e")
        doubled = algo.predict(model, head.with_columns(pl.col("e") * 2.0), ["x"], offset="e")
        np.testing.assert_allclose(doubled, 2.0 * preds, rtol=1e-9)

    def test_identity_link_offset_is_additive(self, algo, exposure_df):
        model = self._fit(algo, exposure_df, {"family": "gaussian"}).model
        head = exposure_df.head(50)
        preds = algo.predict(model, head, ["x"], offset="e")
        shifted = algo.predict(model, head.with_columns(pl.col("e") + 1.0), ["x"], offset="e")
        np.testing.assert_allclose(shifted, preds + 1.0, rtol=1e-9, atol=1e-9)

    @pytest.mark.parametrize(
        ("params", "link"),
        [({"family": "poisson"}, "log"), ({"family": "gaussian"}, "identity")],
    )
    def test_loaded_log_link_glm_reports_its_exposure_column(
        self, algo, exposure_df, tmp_path: Path, params, link
    ):
        """A log-link GLM records its offset as RustyStats' exposure spec; the
        loader must still report the column and how it enters the prediction."""
        from haute._mlflow_io import load_local_model

        model = self._fit(algo, exposure_df, params).model
        path = tmp_path / "offset.rsglm"
        algo.save(model, path)
        scoring_model = load_local_model(str(path))
        assert scoring_model.offset_column == "e"
        assert scoring_model.offset_link == link


# ---------------------------------------------------------------------------
# Term key subsets
# ---------------------------------------------------------------------------


class TestTermKeySubsets:
    """Haute's stored term keys are RustyStats keys, plus ``reference``.

    Pinning them against the installed wheel catches a RustyStats release that
    renames or drops a key Haute writes.
    """

    def test_stored_term_keys_are_rustystats_valid_keys(self):
        import inspect

        from rustystats import formula

        from haute.modelling._glm_terms import TERM_KEYS

        # ``VALID_KEYS`` is a local literal inside a function in the installed
        # wheel, so it has to be read out of the module source.
        source = inspect.getsource(formula)
        start = source.index("VALID_KEYS = {")
        last_entry = source.index("}", source.index('"expression"', start))
        end = source.index("}", last_entry + 1) + 1
        namespace: dict[str, object] = {}
        exec(source[start:end], namespace)  # noqa: S102 - reading a literal from the installed wheel
        valid_keys = namespace["VALID_KEYS"]
        assert set(TERM_KEYS) <= set(valid_keys)
        for term_type, keys in TERM_KEYS.items():
            assert keys - {"reference"} <= valid_keys[term_type], term_type
