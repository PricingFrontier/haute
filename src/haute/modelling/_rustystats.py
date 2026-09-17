"""RustyStats GLM algorithm implementation for Haute's training pipeline.

Implements ``BaseAlgorithm`` so that ``TrainingJob(algorithm="glm", ...)``
delegates to RustyStats for fitting, prediction, and serialization. The term
contract and interaction resolution live in :mod:`haute.modelling._glm_terms`;
config validation lives in :mod:`haute.modelling._train_config`.
"""

from __future__ import annotations

import gc
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from haute._logging import get_logger
from haute.errors import HauteValidationError
from haute.modelling._algorithms import (
    BaseAlgorithm,
    FitResult,
    IterationCallback,
    _malloc_trim,
    _mem_checkpoint,
)
from haute.modelling._glm_terms import (
    bounded_names,
    glm_dtype_class,
    resolve_categorical_levels,
    resolve_glm_design,
    validate_glm_model_columns,
)
from haute.modelling._train_config import (
    glm_cross_validates,
    glm_effective_link,
    glm_params_issue,
    validate_glm_params,
)

logger = get_logger(component="rustystats")


# ── Design preparation ───────────────────────────────────────────────────


def _observed_category_labels(series: pl.Series) -> list[str]:
    """Non-null labels exactly as RustyStats compares categorical levels.

    RustyStats matches ``levels`` against ``column.to_numpy().astype(str)``
    over the whole column, so an integer column with nulls is labelled
    ``2.0`` rather than ``2``. Null rows are excluded from the labels.
    """
    strings = np.asarray(series.to_numpy()).astype(str)
    present = ~series.is_null().to_numpy()
    return sorted(set(strings[present].tolist()))


def prepare_glm_design(
    params: Mapping[str, Any],
    frame: pl.DataFrame,
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """Resolve stored GLM params into the terms and interactions RustyStats fits.

    Validates every model column against the frame's dtypes, translates
    categorical reference levels using the frame's observed labels, and
    resolves interactions independently of card order.
    """
    terms = params.get("terms") or {}
    if not terms:
        raise HauteValidationError("GLM config has no terms. Add a term to at least one feature.")
    interactions = params.get("interactions") or []
    schema = {name: str(dtype) for name, dtype in frame.schema.items()}
    validate_glm_model_columns(terms, interactions, schema)
    observed = {
        name: _observed_category_labels(frame[name])
        for name, spec in terms.items()
        if spec["type"] == "categorical" and ("levels" in spec or "reference" in spec)
    }
    resolved_terms = resolve_categorical_levels(terms, observed)
    dtype_classes = {name: glm_dtype_class(dtype) for name, dtype in schema.items()}
    rs_interactions, effective_terms = resolve_glm_design(
        resolved_terms,
        interactions,
        dtype_classes,
        reserved_names=frame.columns,
    )
    return effective_terms, rs_interactions


def _build_glm_builder_kwargs(
    *,
    target: str,
    terms: dict[str, dict[str, Any]],
    data: pl.DataFrame,
    params: Mapping[str, Any],
    weight: str | None = None,
    offset: str | None = None,
    interactions: list[dict[str, Any]] | None = None,
    var_power: float | None = None,
    theta: float | None = None,
) -> dict[str, Any]:
    """Build kwargs for ``rs.glm_dict()``.

    ``var_power`` and ``theta`` override the params values for profile
    likelihood candidates.
    """
    family = str(params["family"])
    kwargs: dict[str, Any] = {
        "response": target,
        "terms": terms,
        "data": data,
        "family": family,
        "intercept": params.get("intercept", True),
    }
    link = params.get("link") or None
    if link:
        kwargs["link"] = link
    if family == "tweedie":
        power = float(params["var_power"] if var_power is None else var_power)
        kwargs["var_power"] = power
        if power in (1.0, 2.0):
            # RustyStats 0.9 restricts Tweedie to 1 < p < 2 unless extended
            # support is enabled; the Poisson and Gamma endpoints need it.
            kwargs["allow_extended_tweedie"] = True
    if family == "negbinomial":
        kwargs["theta"] = float(params["theta"] if theta is None else theta)
    if offset:
        # Haute's offset column is a positive multiplier under a log link and
        # additive otherwise. RustyStats 0.9 takes ``offset`` verbatim on the
        # link scale and reserves ``exposure`` for the raw, log-transformed rate
        # denominator, so route by the effective link. RustyStats stores the
        # spec on the fitted model and re-reads the column by name at
        # prediction time.
        if glm_effective_link(params) == "log":
            kwargs["exposure"] = offset
        else:
            kwargs["offset"] = offset
    if weight:
        kwargs["weights"] = weight
    if interactions:
        kwargs["interactions"] = interactions
    return kwargs


def glm_fit_kwargs(params: Mapping[str, Any]) -> dict[str, Any]:
    """Build kwargs for ``FormulaGLMDict.fit()`` from validated GLM params.

    A positive ``alpha`` is a fixed penalty and never passes ``regularization``,
    because RustyStats ignores ``alpha`` whenever ``regularization`` is set.
    """
    kwargs: dict[str, Any] = {}
    regularization = params.get("regularization") or None
    if regularization:
        l1_ratio = {"ridge": 0.0, "lasso": 1.0}.get(regularization)
        if l1_ratio is None:
            l1_ratio = float(params["l1_ratio"])
        if glm_cross_validates(params):
            kwargs["regularization"] = regularization
            kwargs["cv"] = int(params["cv_folds"])
            kwargs["selection"] = str(params["cv_selection"])
            kwargs["cv_seed"] = int(params["cv_seed"])
            if regularization == "elastic_net":
                kwargs["l1_ratio"] = l1_ratio
        else:
            kwargs["alpha"] = float(params["alpha"])
            kwargs["l1_ratio"] = l1_ratio
    if params.get("max_iter") is not None:
        kwargs["max_iter"] = int(params["max_iter"])
    if params.get("tol") is not None:
        kwargs["tol"] = float(params["tol"])
    if params.get("robust_standard_errors"):
        kwargs["store_design_matrix"] = True
    return kwargs


# ── Dispersion estimation ────────────────────────────────────────────────

# Search bounds for profile-likelihood dispersion estimation. Theta is a
# scale-like parameter, so it is profiled in log-space; practical Negative
# Binomial dispersions live well inside [0.01, 1000]. Tweedie variance power
# is profiled on the open interval (1, 2) the compound Poisson-gamma family
# is defined on.
_DISPERSION_BOUNDS: dict[str, tuple[float, float]] = {
    "theta": (0.01, 1000.0),
    "var_power": (1.01, 1.99),
}
_DISPERSION_FAMILIES: dict[str, str] = {"theta": "negbinomial", "var_power": "tweedie"}


@dataclass(frozen=True)
class DispersionEstimate:
    """Result of a profile-likelihood dispersion estimation."""

    param: str
    value: float
    llf: float
    n_fits: int


def estimate_glm_dispersion(
    *,
    data: pl.DataFrame,
    params: Mapping[str, Any],
    target: str,
    param: str,
    weight: str | None = None,
    offset: str | None = None,
    on_fit: Callable[[int], None] | None = None,
) -> DispersionEstimate:
    """Estimate a GLM dispersion parameter by profile likelihood.

    RustyStats refuses a Negative Binomial fit without ``theta`` and has no
    estimator for the Tweedie variance power, so the config panel offers this
    estimate as an explicit user action; the resolved value lands in the node
    config where the objective gate requires it, never as a hidden default.

    Maximises the fitted model's log-likelihood over the single dispersion
    parameter with a bounded 1-D search on the training design (deterministic;
    about 20 to 30 fits). ``theta`` is profiled in log-space.
    """
    import rustystats as rs
    from scipy.optimize import minimize_scalar

    if param not in _DISPERSION_FAMILIES:
        raise HauteValidationError(
            f"Unknown dispersion parameter {param!r}. "
            f"Estimable parameters: {', '.join(_DISPERSION_FAMILIES)}."
        )
    family = params.get("family")
    if family != _DISPERSION_FAMILIES[param]:
        raise HauteValidationError(
            f"Dispersion parameter {param!r} belongs to the "
            f"{_DISPERSION_FAMILIES[param]} family, not {family!r}."
        )
    terms, interactions = prepare_glm_design(params, data)

    n_fits = 0
    last_error: Exception | None = None

    def _llf_at(value: float) -> float:
        nonlocal n_fits, last_error
        if on_fit is not None:
            # Cancellation hook: called before each candidate fit; the
            # caller raises to abort the whole search.
            on_fit(n_fits)
        builder_kwargs = _build_glm_builder_kwargs(
            target=target,
            terms=terms,
            data=data,
            params=params,
            weight=weight,
            offset=offset,
            interactions=interactions,
            var_power=value if param == "var_power" else None,
            theta=value if param == "theta" else None,
        )
        n_fits += 1
        try:
            return float(rs.glm_dict(**builder_kwargs).fit().llf())
        except Exception as exc:  # non-convergence at this candidate
            last_error = exc
            return -math.inf

    lo, hi = _DISPERSION_BOUNDS[param]
    if param == "theta":
        result = minimize_scalar(
            lambda log_value: -_llf_at(math.exp(log_value)),
            bounds=(math.log(lo), math.log(hi)),
            method="bounded",
            options={"xatol": 1e-3},
        )
        value = float(math.exp(result.x))
    else:
        result = minimize_scalar(
            lambda candidate: -_llf_at(candidate),
            bounds=(lo, hi),
            method="bounded",
            options={"xatol": 1e-3},
        )
        value = float(result.x)

    llf = float(-result.fun)
    if not math.isfinite(llf):
        raise HauteValidationError(
            f"Profile-likelihood estimation of {param!r} failed: no candidate "
            f"value produced a converged fit"
            + (f" (last error: {last_error})" if last_error else ".")
        )
    return DispersionEstimate(param=param, value=value, llf=llf, n_fits=n_fits)


# ── Result extraction ────────────────────────────────────────────────────

_INFERENCE_REASONS: dict[str, str] = {
    "naive_after_regularization": (
        "The ridge penalty shrinks the coefficients, so standard errors and p-values are not valid."
    ),
    "naive_after_selection": (
        "Lasso or elastic net selects variables, so standard errors and p-values are not valid."
    ),
    "naive_after_cv_selection": (
        "The penalty was chosen by cross-validation, so standard errors and p-values are not valid."
    ),
    "constrained_boundary": (
        "Monotonicity constraints restrict the coefficients, so standard errors and p-values "
        "are not valid."
    ),
    "unavailable": (
        "Automatically smoothed splines are penalised, so standard errors and p-values are "
        "not valid."
    ),
    "covariance_skipped": (
        "Covariance was not computed, so standard errors and p-values are unavailable."
    ),
    "singular_design": (
        "Standard errors are not finite because the design is close to singular; check for "
        "collinear or unscaled terms."
    ),
}


def _significance_code(p_value: float) -> str:
    """R-style significance code, matching RustyStats' ``significance_codes``."""
    if p_value < 0.001:
        return "***"
    if p_value < 0.01:
        return "**"
    if p_value < 0.05:
        return "*"
    if p_value < 0.1:
        return "."
    return ""


@dataclass(frozen=True)
class GLMInference:
    """Whether a fitted GLM's standard errors are valid, with the statistics."""

    status: str
    valid: bool
    standard_errors: str | None
    reason: str | None
    std_error: np.ndarray | None = None
    z_value: np.ndarray | None = None
    p_value: np.ndarray | None = None
    significance: list[str] | None = None
    confidence_intervals: np.ndarray | None = None

    def to_plain_data(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "valid": self.valid,
            "standard_errors": self.standard_errors,
            "reason": self.reason,
        }


def glm_inference(model: Any, robust_standard_errors: str | None) -> GLMInference:
    """Read RustyStats' inference status and the statistics it allows.

    RustyStats 0.9 marks inference invalid after penalties, selection,
    constraints, and smoothing; only ``valid_standard`` fits report standard
    errors. A valid status whose standard errors are not finite is reported as
    ``singular_design`` rather than published as numbers.
    """
    status = getattr(model, "inference_status", None)
    if not isinstance(status, str):
        raise ValueError("RustyStats did not report an inference status for this model")
    if status != "valid_standard":
        reason = _INFERENCE_REASONS.get(status)
        if reason is None:
            raise ValueError(f"RustyStats reported an unknown inference status {status!r}")
        return GLMInference(status=status, valid=False, standard_errors=None, reason=reason)
    if robust_standard_errors:
        std_error = np.asarray(model.bse_robust(robust_standard_errors), dtype=np.float64)
        z_value = np.asarray(model.tvalues_robust(robust_standard_errors), dtype=np.float64)
        p_value = np.asarray(model.pvalues_robust(robust_standard_errors), dtype=np.float64)
        intervals = np.asarray(
            model.conf_int_robust(alpha=0.05, cov_type=robust_standard_errors),
            dtype=np.float64,
        )
    else:
        std_error = np.asarray(model.bse(), dtype=np.float64)
        z_value = np.asarray(model.tvalues(), dtype=np.float64)
        p_value = np.asarray(model.pvalues(), dtype=np.float64)
        intervals = np.asarray(model.conf_int(0.05), dtype=np.float64)
    arrays = (std_error, z_value, p_value, intervals)
    if not all(bool(np.all(np.isfinite(array))) for array in arrays):
        return GLMInference(
            status="singular_design",
            valid=False,
            standard_errors=None,
            reason=_INFERENCE_REASONS["singular_design"],
        )
    return GLMInference(
        status=status,
        valid=True,
        standard_errors=robust_standard_errors or "model",
        reason=None,
        std_error=std_error,
        z_value=z_value,
        p_value=p_value,
        significance=[_significance_code(float(value)) for value in p_value],
        confidence_intervals=intervals,
    )


def _coefficients(model: Any) -> tuple[list[str], np.ndarray]:
    names = [str(name) for name in model.feature_names]
    coefficients = np.asarray(model.params, dtype=np.float64)
    if len(names) != len(coefficients):
        raise ValueError(
            f"RustyStats reported {len(coefficients)} coefficients for {len(names)} design columns"
        )
    return names, coefficients


def glm_coefficient_rows(model: Any, inference: GLMInference) -> list[dict[str, Any]]:
    """Coefficient table rows; inference fields are null unless inference is valid."""
    names, coefficients = _coefficients(model)
    non_finite = [names[index] for index in np.flatnonzero(~np.isfinite(coefficients))]
    if non_finite:
        raise ValueError(
            f"RustyStats reported non-finite coefficients for {bounded_names(non_finite)}"
        )
    rows: list[dict[str, Any]] = []
    for index, name in enumerate(names):
        row: dict[str, Any] = {"feature": name, "coefficient": float(coefficients[index])}
        if inference.valid:
            assert inference.std_error is not None
            assert inference.z_value is not None
            assert inference.p_value is not None
            assert inference.significance is not None
            row.update(
                std_error=float(inference.std_error[index]),
                z_value=float(inference.z_value[index]),
                p_value=float(inference.p_value[index]),
                significance=inference.significance[index],
            )
        else:
            row.update(std_error=None, z_value=None, p_value=None, significance=None)
        rows.append(row)
    return rows


def glm_relativity_rows(model: Any, inference: GLMInference) -> list[dict[str, Any]]:
    """Exponentiated coefficients for log-link models; empty for any other link."""
    if str(model.link) != "log":
        return []
    names, coefficients = _coefficients(model)
    with np.errstate(over="ignore", invalid="ignore"):
        relativities = np.exp(coefficients)
        bounds = (
            np.exp(inference.confidence_intervals)
            if inference.valid and inference.confidence_intervals is not None
            else None
        )
    overflow = [names[index] for index in np.flatnonzero(~np.isfinite(relativities))]
    if bounds is not None:
        overflow.extend(
            names[index]
            for index in np.flatnonzero(~np.all(np.isfinite(bounds), axis=1))
            if names[index] not in overflow
        )
    if overflow:
        raise ValueError(
            f"Relativities are not finite for {bounded_names(overflow)}: the coefficients or "
            "their intervals are too large to exponentiate. Check for collinear or unscaled "
            "terms, and for categorical levels whose response is always zero."
        )
    rows: list[dict[str, Any]] = []
    for index, name in enumerate(names):
        rows.append(
            {
                "feature": name,
                "relativity": float(relativities[index]),
                "ci_lower": float(bounds[index][0]) if bounds is not None else None,
                "ci_upper": float(bounds[index][1]) if bounds is not None else None,
            }
        )
    return rows


def glm_fit_statistics(model: Any) -> dict[str, float]:
    """Finite fit statistics RustyStats defines for this model."""
    smooth = bool(model.has_smooth_terms())
    raw: dict[str, Any] = {
        "deviance": model.deviance,
        "null_deviance": model.null_deviance(),
        "n_obs": model.nobs,
        "df_model": model.df_model,
        "df_residual": model.df_resid,
        "iterations": model.iterations,
        "converged": 1.0 if model.converged else 0.0,
        "scale": model.scale(),
    }
    if not model.is_quasi_likelihood:
        raw["log_likelihood"] = model.llf()
    for name, method in (("aic", model.aic), ("bic", model.bic)):
        value = method()
        if value is not None:
            raw[name] = value
    if smooth:
        raw["total_edf"] = model.total_edf
        raw["gcv"] = model.gcv
    statistics = {name: float(value) for name, value in raw.items()}
    non_finite = sorted(name for name, value in statistics.items() if not math.isfinite(value))
    if non_finite:
        raise ValueError(f"RustyStats reported non-finite fit statistics: {non_finite}")
    return statistics


def glm_smooth_term_rows(model: Any) -> list[dict[str, Any]]:
    """Effective degrees of freedom and smoothing parameter per penalised spline."""
    if not model.has_smooth_terms():
        return []
    rows: list[dict[str, Any]] = []
    for term in model.smooth_terms:
        edf = float(term.edf)
        smoothing = float(term.lambda_)
        if not (math.isfinite(edf) and math.isfinite(smoothing)):
            raise ValueError(
                f"RustyStats reported non-finite smoothing results for {term.variable!r}"
            )
        rows.append({"term": str(term.variable), "k": int(term.k), "edf": edf, "lambda": smoothing})
    return rows


def glm_regularization_summary(model: Any, params: Mapping[str, Any]) -> dict[str, Any] | None:
    """The penalty RustyStats applied, or ``None`` for an unpenalised fit."""
    regularization = params.get("regularization") or None
    if not regularization:
        return None
    cross_validated = glm_cross_validates(params)
    alpha = float(model.alpha)
    l1_ratio = model.l1_ratio
    summary: dict[str, Any] = {
        "penalty": regularization,
        "mode": "cross_validation" if cross_validated else "fixed",
        "alpha": alpha,
        "l1_ratio": None if l1_ratio is None else float(l1_ratio),
        "n_nonzero": int(model.n_nonzero()),
        "cv_folds": int(model.n_cv_folds) if cross_validated else None,
        "cv_selection": str(model.cv_selection_method) if cross_validated else None,
        "cv_seed": int(params["cv_seed"]) if cross_validated else None,
    }
    if not math.isfinite(alpha) or (
        summary["l1_ratio"] is not None and not math.isfinite(summary["l1_ratio"])
    ):
        raise ValueError("RustyStats reported a non-finite penalty")
    return summary


@dataclass
class GLMResultReport:
    """GLM-specific diagnostics for one fitted model.

    ``errors`` names each optional diagnostic that failed; the training job
    records them in ``diagnostics_errors`` without failing the run.
    """

    inference: dict[str, Any] | None = None
    coefficients: list[dict[str, Any]] = field(default_factory=list)
    relativities: list[dict[str, Any]] = field(default_factory=list)
    fit_statistics: dict[str, float] = field(default_factory=dict)
    smooth_terms: list[dict[str, Any]] = field(default_factory=list)
    regularization: dict[str, Any] | None = None
    errors: list[tuple[str, Exception]] = field(default_factory=list)


# ── Algorithm ────────────────────────────────────────────────────────────


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
        """Fit a GLM using the RustyStats dict API.

        GLM configuration (terms, family, link, regularization, and so on)
        arrives in ``params``. The design is resolved from ``train_df``'s
        dtypes; ``features`` and ``cat_features`` only bound the columns read.
        """
        import rustystats as rs

        if train_df is None:
            raise HauteValidationError("GLMAlgorithm.fit() requires train_df (pool bypass)")
        if monotone_constraints:
            raise HauteValidationError(
                "monotone_constraints is a CatBoost lever; GLM monotonicity lives on each "
                "term's 'monotonicity' key"
            )
        if feature_weights:
            raise HauteValidationError(
                "feature_weights is a CatBoost lever; RustyStats GLMs do not support it"
            )
        issue = glm_params_issue(params)
        if issue is not None:
            raise HauteValidationError(issue)
        validate_glm_params(params)

        _mem_checkpoint("glm fit() START")
        terms, rs_interactions = prepare_glm_design(params, train_df)
        builder = rs.glm_dict(
            **_build_glm_builder_kwargs(
                target=target,
                terms=terms,
                data=train_df,
                params=params,
                weight=weight,
                offset=offset,
                interactions=rs_interactions,
            )
        )

        if on_iteration:
            on_iteration(0, 1, {})
        _mem_checkpoint("glm fitting")
        result = builder.fit(**glm_fit_kwargs(params))
        _mem_checkpoint("glm fit() DONE")
        if on_iteration:
            on_iteration(1, 1, {"deviance": float(result.deviance)})

        # A GLM converges in a few IRLS steps, not iteratively like a GBM.
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
        offset: str | None = None,
    ) -> np.ndarray:
        """Generate predictions on the response scale.

        When *offset* is set, the offset column is kept in the frame handed
        to RustyStats, which re-reads its fit-time exposure (log link) or
        offset (other links) column by name and re-applies the exact
        fit-time transform. A frame without the column raises.
        """
        columns = list(features)
        if offset:
            if offset not in df.columns:
                raise HauteValidationError(
                    f"GLM predict: offset column {offset!r} is missing from "
                    f"the input data. The model was trained with this offset "
                    f"and predictions without it would be mis-scaled. "
                    f"Available columns: {bounded_names(df.columns)}"
                )
            if offset not in columns:
                columns.append(offset)
        preds = model.predict(df.select(columns))
        return np.asarray(preds).flatten()

    def feature_importance(self, model: Any) -> list[dict[str, Any]]:
        """Return absolute coefficient magnitudes as a proxy for importance.

        This is a rough proxy — for GLMs the coefficient table is the real
        diagnostic. It satisfies the ``BaseAlgorithm`` interface for the shared
        metrics pipeline.
        """
        names, coefficients = _coefficients(model)
        pairs = sorted(zip(names, np.abs(coefficients)), key=lambda pair: pair[1], reverse=True)
        return [{"feature": name, "importance": float(value)} for name, value in pairs]

    def save(self, model: Any, path: Path) -> None:
        """Save model using RustyStats native binary serialization."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            f.write(model.to_bytes())

    def glm_result(self, model: Any, params: Mapping[str, Any]) -> GLMResultReport:
        """Collect every GLM result diagnostic, recording failures by name."""
        report = GLMResultReport()
        try:
            inference = glm_inference(model, params.get("robust_standard_errors") or None)
            report.inference = inference.to_plain_data()
        except Exception as exc:
            report.errors.append(("glm_inference", exc))
            inference = None
        if inference is not None:
            for name, collect in (
                ("glm_coefficients", glm_coefficient_rows),
                ("glm_relativities", glm_relativity_rows),
            ):
                try:
                    rows = collect(model, inference)
                except Exception as exc:
                    report.errors.append((name, exc))
                    continue
                if name == "glm_coefficients":
                    report.coefficients = rows
                else:
                    report.relativities = rows
        try:
            report.fit_statistics = glm_fit_statistics(model)
        except Exception as exc:
            report.errors.append(("glm_fit_statistics", exc))
        try:
            report.smooth_terms = glm_smooth_term_rows(model)
        except Exception as exc:
            report.errors.append(("glm_smooth_terms", exc))
        try:
            report.regularization = glm_regularization_summary(model, params)
        except Exception as exc:
            report.errors.append(("glm_regularization", exc))
        return report
