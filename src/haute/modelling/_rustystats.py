"""RustyStats GLM algorithm implementation for Haute's training pipeline.

Implements ``BaseAlgorithm`` so that ``TrainingJob(algorithm="glm", ...)``
delegates to RustyStats for fitting, prediction, and serialization.
"""

from __future__ import annotations

import gc
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from haute._logging import get_logger
from haute.modelling._algorithms import (
    BaseAlgorithm,
    FitResult,
    IterationCallback,
    _malloc_trim,
    _mem_checkpoint,
)

logger = get_logger(component="rustystats")


class GLMInferenceUnavailableError(RuntimeError):
    """Real GLM inference statistics (std errors, z-values, p-values) cannot be obtained.

    Raised instead of fabricating placeholder statistics (the old behaviour
    rendered SE=0.0 / p=1.0 as real, inventing significance). The training
    job catches this, records a ``glm_coefficients`` entry in
    ``diagnostics_errors`` for the UI, and omits the coefficient table —
    the frontend payload contract requires every stat field on every row,
    so partial rows are not an option.
    """


def _auto_terms(
    features: list[str],
    cat_features: list[str],
) -> dict[str, dict[str, Any]]:
    """Generate default term specs when none are provided.

    Numeric features → ``linear``, categorical → ``categorical``.
    """
    cat_set = set(cat_features)
    terms: dict[str, dict[str, Any]] = {}
    for f in features:
        if f in cat_set:
            terms[f] = {"type": "categorical"}
        else:
            terms[f] = {"type": "linear"}
    return terms


def _build_interactions(
    interactions_config: list[dict[str, Any]],
    terms: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Convert Haute's interaction format to RustyStats format.

    Haute stores: ``[{"factors": ["a", "b"], "include_main": true}]``
    RustyStats wants: ``[{"a": {"type": "..."}, "b": {"type": "..."}, "include_main": false}]``

    Each factor's term spec is inherited from the main terms dict.

    Note: ``include_main`` is forced to ``False`` when ALL interaction
    factors already appear in the main ``terms`` dict (which is the
    normal case in Haute).  RustyStats' ``include_main: True`` *adds*
    the main effects to the design matrix — duplicating them when
    they're already present as standalone terms causes perfect
    collinearity and a singular matrix error.
    """
    rs_interactions: list[dict[str, Any]] = []
    for interaction in interactions_config:
        factors = interaction.get("factors", [])
        if len(factors) < 2:
            continue
        rs_int: dict[str, Any] = {}
        for factor in factors:
            if factor in terms:
                rs_int[factor] = dict(terms[factor])
            else:
                # Fallback: categorical for unknown factors
                rs_int[factor] = {"type": "categorical"}

        # Only set include_main=True when at least one factor is NOT
        # already in the main terms dict (otherwise it causes singularity).
        all_in_terms = all(f in terms for f in factors)
        if all_in_terms:
            rs_int["include_main"] = False
        else:
            rs_int["include_main"] = interaction.get("include_main", True)

        rs_interactions.append(rs_int)
    return rs_interactions


def _align_coefs_and_names(
    coefs: np.ndarray,
    names: list[str],
) -> tuple[np.ndarray, list[str]]:
    """Align coefficient array and feature names, prepending (Intercept) if needed."""
    if len(coefs) > len(names):
        names = ["(Intercept)"] + names
    min_len = min(len(coefs), len(names))
    return coefs[:min_len], names[:min_len]


def _build_glm_builder_kwargs(
    *,
    target: str,
    terms: dict[str, dict[str, Any]],
    data: pl.DataFrame,
    family: str,
    intercept: bool,
    link: str | None = None,
    var_power: float = 1.5,
    weight: str | None = None,
    offset: str | None = None,
    interactions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build kwargs dict for ``rs.glm_dict()`` used by ``fit()``."""
    kwargs: dict[str, Any] = {
        "response": target,
        "terms": terms,
        "data": data,
        "family": family,
        "intercept": intercept,
    }
    if link:
        kwargs["link"] = link
    if family == "tweedie":
        kwargs["var_power"] = var_power
    if offset:
        kwargs["offset"] = offset
    if weight:
        kwargs["weights"] = weight
    if interactions:
        kwargs["interactions"] = interactions
    return kwargs


class GLMAlgorithm(BaseAlgorithm):
    """RustyStats Generalised Linear Model implementation."""

    def fit(
        self,
        train_df: pl.DataFrame | None,
        features: list[str],
        cat_features: list[str],
        target: str,
        weight: str | None,
        params: dict[str, Any],
        task: str,
        on_iteration: IterationCallback | None = None,
        eval_df: pl.DataFrame | None = None,
        offset: str | None = None,
        monotone_constraints: dict[str, int] | None = None,
        feature_weights: dict[str, float] | None = None,
        **kwargs: Any,
    ) -> FitResult:
        """Fit a GLM using RustyStats dict API.

        GLM-specific config (terms, family, link, regularization, etc.)
        is passed via the ``params`` dict, which is assembled by
        ``TrainingJob._train_model()``.
        """
        import rustystats as rs

        _mem_checkpoint("glm fit() START")

        if train_df is None:
            raise ValueError("GLMAlgorithm.fit() requires train_df (pool bypass)")

        # Extract GLM-specific config from params
        terms = params.get("terms", {})
        family = params.get("family", "gaussian")
        link = params.get("link") or None  # empty string → None (canonical)
        var_power = params.get("var_power", 1.5)
        intercept = params.get("intercept", True)
        interactions_config = params.get("interactions", [])
        regularization = params.get("regularization") or None
        alpha = params.get("alpha", 0.0)
        l1_ratio = params.get("l1_ratio", 0.0)

        # Auto-generate terms if none specified
        if not terms:
            terms = _auto_terms(features, cat_features)

        if feature_weights:
            logger.warning(
                "glm_feature_weights_unsupported",
                msg="feature_weights is not supported by RustyStats GLM and will be ignored",
            )

        # Apply monotone constraints from the top-level config
        if monotone_constraints:
            for feat, direction in monotone_constraints.items():
                if feat in terms:
                    if direction > 0:
                        terms[feat]["monotonicity"] = "increasing"
                    elif direction < 0:
                        terms[feat]["monotonicity"] = "decreasing"

        # Build RustyStats interactions
        rs_interactions = _build_interactions(interactions_config, terms)

        _mem_checkpoint("glm building model")

        # Build the GLM builder
        builder_kwargs = _build_glm_builder_kwargs(
            target=target,
            terms=terms,
            data=train_df,
            family=family,
            intercept=intercept,
            link=link,
            var_power=var_power,
            weight=weight,
            offset=offset,
            interactions=rs_interactions,
        )

        builder = rs.glm_dict(**builder_kwargs)

        # Build fit kwargs
        fit_kwargs: dict[str, Any] = {}
        if regularization:
            fit_kwargs["regularization"] = regularization
            # RustyStats' internal CV sweeps alpha during regularization fits.
            # 5 folds is the standard default used by sklearn's LassoCV /
            # RidgeCV / ElasticNetCV and gives a good bias-variance trade-off.
            fit_kwargs["cv"] = 5
            if alpha > 0:
                fit_kwargs["alpha"] = alpha
            if regularization == "elastic_net":
                fit_kwargs["l1_ratio"] = l1_ratio

        # Signal start
        if on_iteration:
            on_iteration(0, 1, {})

        _mem_checkpoint("glm fitting")
        result = builder.fit(**fit_kwargs)
        _mem_checkpoint("glm fit() DONE")

        # Signal completion
        if on_iteration:
            on_iteration(1, 1, {"deviance": float(result.deviance)})

        # Build loss history (GLM converges in few IRLS steps, not iterative like GBM)
        loss_history: list[dict[str, float]] = [
            {"iteration": 1.0, "train_deviance": float(result.deviance)},
        ]

        del train_df
        gc.collect()
        _malloc_trim()

        return FitResult(
            model=result,
            best_iteration=result.iterations,
            loss_history=loss_history,
        )

    def predict(
        self,
        model: Any,
        df: pl.DataFrame,
        features: list[str],
    ) -> np.ndarray:
        """Generate predictions on the response scale."""
        preds = model.predict(df.select(features))
        return np.asarray(preds).flatten()

    def feature_importance(self, model: Any) -> list[dict[str, Any]]:
        """Return absolute coefficient magnitudes as a proxy for importance.

        This is a rough proxy — for GLMs, the coefficient table (with
        standard errors and p-values) is the real diagnostic. This method
        satisfies the ``BaseAlgorithm`` interface for the shared metrics
        pipeline.
        """
        names = list(model.feature_names)
        coefs = np.abs(np.asarray(model.coefficients))

        coefs, names = _align_coefs_and_names(coefs, names)

        pairs = sorted(zip(names, coefs), key=lambda x: x[1], reverse=True)
        return [{"feature": name, "importance": float(imp)} for name, imp in pairs]

    def save(self, model: Any, path: Path) -> None:
        """Save model using RustyStats native binary serialization."""
        path.parent.mkdir(parents=True, exist_ok=True)
        model_bytes = model.to_bytes()
        with open(path, "wb") as f:
            f.write(model_bytes)

    # ------------------------------------------------------------------
    # GLM-specific diagnostics (called by TrainingJob if hasattr)
    # ------------------------------------------------------------------

    def coefficients_table(self, model: Any) -> list[dict[str, Any]]:
        """Return full coefficient table with SEs, z-stats, p-values.

        Returns a list of dicts with normalized keys:
        ``feature``, ``coefficient``, ``std_error``, ``z_value``,
        ``p_value``, ``significance``.

        Raises :class:`GLMInferenceUnavailableError` when real inference
        statistics cannot be obtained (e.g. a model deserialized without
        covariance data, or a library failure computing ``bse()`` /
        ``pvalues()``). Placeholder statistics are never fabricated.
        """
        # Key mapping from RustyStats column names to our normalized names
        key_map = {
            "Feature": "feature",
            "Estimate": "coefficient",
            "Std. Error": "std_error",
            "Std.Error": "std_error",
            "z": "z_value",
            "z value": "z_value",
            "z.value": "z_value",
            "Pr(>|z|)": "p_value",
            "Signif": "significance",
        }

        try:
            coef_df = model.coef_table()
            raw = coef_df.to_dicts()
            # Normalize keys
            return [
                {key_map.get(k, k.lower().replace(" ", "_")): v for k, v in row.items()}
                for row in raw
            ]
        except Exception as exc:
            logger.warning("coef_table_primary_failed", error=str(exc))
            # Fallback: build the same table from the individual statistic
            # arrays. These are still REAL statistics — only the formatted
            # coef_table() accessor failed.
            names = list(model.feature_names)
            coefs = list(model.coefficients)
            try:
                ses = list(model.bse())
                zvals = list(model.tvalues())
                pvals = list(model.pvalues())
                sigs = list(model.significance_codes())
            except Exception as stats_exc:
                raise GLMInferenceUnavailableError(
                    "GLM inference statistics (std errors, z-values, p-values) "
                    f"are unavailable for this model: {type(stats_exc).__name__}: "
                    f"{stats_exc} (coef_table() failed first: "
                    f"{type(exc).__name__}: {exc}). Refusing to fabricate "
                    "placeholder statistics; the coefficient table is omitted."
                ) from stats_exc

            # Handle intercept
            coefs_arr = np.asarray(coefs)
            coefs_arr, names = _align_coefs_and_names(coefs_arr, names)
            coefs = list(coefs_arr)

            # Every emitted row needs a real value for every statistic —
            # padding short arrays with 0.0/1.0 would be fabrication by index.
            shortest = min(len(ses), len(zvals), len(pvals), len(sigs))
            if shortest < len(coefs):
                raise GLMInferenceUnavailableError(
                    "GLM inference statistic arrays do not cover all "
                    f"{len(coefs)} coefficients (shortest has {shortest} "
                    "entries). Refusing to fabricate placeholder statistics "
                    "for the uncovered coefficients; the coefficient table "
                    "is omitted."
                )

            result = []
            for i, name in enumerate(names):
                if i < len(coefs):
                    result.append(
                        {
                            "feature": name,
                            "coefficient": float(coefs[i]),
                            "std_error": float(ses[i]),
                            "z_value": float(zvals[i]),
                            "p_value": float(pvals[i]),
                            "significance": str(sigs[i]),
                        }
                    )
            return result

    def relativities(self, model: Any) -> list[dict[str, Any]]:
        """Return exp(coef) relativities with confidence intervals.

        Returns dicts with normalized keys: ``feature``, ``relativity``,
        ``ci_lower``, ``ci_upper``.
        """
        key_map = {
            "Feature": "feature",
            "Relativity": "relativity",
            "CI_Lower": "ci_lower",
            "CI_Upper": "ci_upper",
        }

        try:
            rel_df = model.relativities()
            raw = rel_df.to_dicts()
            return [{key_map.get(k, k.lower()): v for k, v in row.items()} for row in raw]
        except Exception as exc:
            logger.warning("relativities_primary_failed", error=str(exc))
            # Fallback: compute from coefficients
            coefs = np.asarray(model.coefficients)
            names = list(model.feature_names)
            coefs, names = _align_coefs_and_names(coefs, names)

            try:
                ci = model.conf_int()  # (n, 2) array
            except Exception as exc:
                logger.warning("relativities_conf_int_failed", error=str(exc))
                ci = None

            result = []
            for i, name in enumerate(names):
                if i < len(coefs):
                    entry: dict[str, Any] = {
                        "feature": name,
                        "relativity": float(np.exp(coefs[i])),
                    }
                    if ci is not None and i < len(ci):
                        entry["ci_lower"] = float(np.exp(ci[i][0]))
                        entry["ci_upper"] = float(np.exp(ci[i][1]))
                    result.append(entry)
            return result

    def fit_statistics(self, model: Any) -> dict[str, float]:
        """Return GLM fit statistics (AIC, BIC, deviance, etc.)."""
        stats: dict[str, float] = {}

        for attr, label in [
            ("deviance", "deviance"),
            ("nobs", "n_obs"),
            ("df_model", "df_model"),
            ("df_resid", "df_residual"),
            ("iterations", "iterations"),
        ]:
            try:
                stats[label] = float(getattr(model, attr))
            except Exception as exc:
                logger.warning("fit_stat_failed", attr=attr, error=str(exc))

        for method, label in [
            ("null_deviance", "null_deviance"),
            ("aic", "aic"),
            ("bic", "bic"),
            ("llf", "log_likelihood"),
        ]:
            try:
                stats[label] = float(getattr(model, method)())
            except Exception as exc:
                logger.warning("fit_stat_failed", attr=method, error=str(exc))

        try:
            stats["converged"] = 1.0 if model.converged else 0.0
        except Exception as exc:
            logger.warning("fit_stat_failed", attr="converged", error=str(exc))

        return stats

    def glm_diagnostics(
        self,
        model: Any,
        data: pl.DataFrame,
        cat_features: list[str],
        features: list[str],
    ) -> dict[str, Any]:
        """Run RustyStats built-in diagnostics (A/E, Hosmer-Lemeshow, etc.).

        Returns the diagnostics dict, or empty dict if diagnostics fail.
        """
        try:
            continuous = [f for f in features if f not in set(cat_features)]
            diag = model.diagnostics(
                data=data,
                categorical_factors=cat_features,
                continuous_factors=continuous,
            )
            return dict(diag.to_dict())
        except Exception as exc:
            logger.warning("glm_diagnostics_failed", error=str(exc))
            return {}
