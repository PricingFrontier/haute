"""Single source of truth for modelling-node config → TrainingJob kwargs.

Both consumers of a modelling node's config MUST build their TrainingJob
arguments here so they can never drift:

- live training (``haute.routes._train_service.TrainService``), and
- standalone script export (``haute.modelling._export.generate_training_script``).

Historically each path assembled kwargs independently, which produced two
silent-wrongness bugs: GLM keys (incl. ``offset``) were merged into CatBoost
params (CatBoost's constructors have no ``**kwargs`` → fit crash), and the
exported GLM script dropped top-level terms/family/link/regularization and
"successfully" trained a Gaussian all-features model.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

from haute.errors import HauteValidationError
from haute.modelling._evaluation import EvaluationConfig
from haute.modelling._glm_terms import (
    glm_model_columns,
    monotone_constraint_terms,
    penalised_smooth_terms,
)
from haute.modelling._tuning import TuningConfig

# GLM config keys live at the top level of the modelling-node config (not
# inside ``config["params"]``) and are consumed by ``GLMAlgorithm.fit`` via
# the params dict. They must be merged into train params for GLM only:
# CatBoost receives ``params`` verbatim as constructor kwargs.
GLM_CONFIG_KEYS: tuple[str, ...] = (
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
)

#: Supported GLM families and their links on RustyStats 0.9.0. The first link
#: is canonical (``rustystats.formula.get_default_link``). The Target pane
#: reads an identical TypeScript table, pinned by a contract test.
GLM_FAMILY_LINKS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "gaussian": ("identity", "log"),
        "poisson": ("log", "identity"),
        "quasipoisson": ("log", "identity"),
        "binomial": ("logit", "log", "identity"),
        "quasibinomial": ("logit", "log", "identity"),
        "gamma": ("log", "identity"),
        "tweedie": ("log", "identity"),
        "negbinomial": ("log", "identity"),
    }
)

#: Config levers only CatBoost honours. A GLM's features are its terms and
#: interaction factors, and its monotonicity lives on each term.
CATBOOST_ONLY_LEVERS: tuple[str, ...] = (
    "exclude",
    "feature_columns",
    "monotone_constraints",
    "feature_weights",
)

GLM_REGULARIZATIONS: frozenset[str] = frozenset({"ridge", "lasso", "elastic_net"})
GLM_CV_SELECTIONS: frozenset[str] = frozenset({"min", "1se"})
GLM_ROBUST_STANDARD_ERRORS: frozenset[str] = frozenset({"HC0", "HC1", "HC2", "HC3"})
GLM_CV_FOLDS_RANGE = (2, 20)
GLM_CV_DEFAULT_SEED = 42
GLM_MAX_ITER_RANGE = (1, 10_000)


def is_glm_config(config: Mapping[str, Any]) -> bool:
    """Whether a modelling-node config trains a GLM."""
    return str(config.get("algorithm", "catboost")).lower() == "glm"


def glm_effective_link(params: Mapping[str, Any]) -> str:
    """The link a GLM fits: the explicit link, else the family's canonical link."""
    link = params.get("link") or None
    if link:
        return str(link)
    return GLM_FAMILY_LINKS[str(params["family"])][0]


def _configured(params: Mapping[str, Any], key: str) -> bool:
    value = params.get(key)
    return value is not None and value != ""


def glm_cross_validates(params: Mapping[str, Any]) -> bool:
    """Whether a regularised GLM selects its penalty by cross-validation."""
    if not _configured(params, "regularization"):
        return False
    alpha = params.get("alpha")
    return alpha is None or alpha == 0


def glm_params_issue(params: Mapping[str, Any]) -> str | None:
    """Return an actionable message when GLM params are incomplete.

    An unset objective parameter must gate, never fall through to a RustyStats
    or literal failover. ``params`` holds the GLM config keys.
    """
    family = params.get("family")
    if not family:
        return (
            "GLM config has no family. Open the config panel and choose a "
            "distribution family explicitly (e.g. poisson for claim counts, "
            "gamma for severity) — an unset family would silently train a "
            "gaussian model."
        )
    if str(family).lower() == "tweedie" and not _configured(params, "var_power"):
        return (
            "Tweedie GLM has no variance power. Set it explicitly "
            "(1=Poisson, 2=Gamma) — an unset value would silently fit "
            "at power 1.5."
        )
    if str(family).lower() == "negbinomial" and not _configured(params, "theta"):
        return (
            "Negative Binomial GLM has no dispersion (theta). Set it "
            "explicitly or estimate it from the data — RustyStats refuses "
            "to fit without it."
        )
    if not params.get("terms"):
        return "GLM config has no terms. Add a term to at least one feature."
    regularization = str(params.get("regularization") or "").lower()
    if regularization == "elastic_net" and not _configured(params, "l1_ratio"):
        return (
            "Elastic-net regularisation has no L1 ratio. Set it explicitly "
            "(0 fits Ridge, 1 fits LASSO) — an unset value would silently "
            "fit pure Ridge."
        )
    if glm_cross_validates(params):
        missing = [
            label
            for key, label in (
                ("cv_folds", "folds"),
                ("cv_selection", "selection rule"),
                ("cv_seed", "seed"),
            )
            if not _configured(params, key)
        ]
        if missing:
            return (
                "Cross-validated regularisation needs its "
                f"{', '.join(missing)}. Set them in the Regularization section so the "
                "selected penalty is reproducible."
            )
    return None


def _require_number(
    value: Any,
    message: str,
    *,
    integer: bool = False,
) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TrainingConfigError(message)
    if integer and not isinstance(value, int):
        raise TrainingConfigError(message)
    if not math.isfinite(value):
        raise TrainingConfigError(message)


def validate_glm_params(params: Mapping[str, Any]) -> None:
    """Raise ``TrainingConfigError`` when configured GLM params are invalid.

    Validates configured values only; absent values are the objective gate's
    concern (:func:`glm_params_issue`).
    """
    family = params.get("family")
    if family:
        if family not in GLM_FAMILY_LINKS:
            raise TrainingConfigError(
                f"Unknown GLM family '{family}'. Available: {', '.join(GLM_FAMILY_LINKS)}."
            )
        link = params.get("link") or None
        if link and link not in GLM_FAMILY_LINKS[family]:
            raise TrainingConfigError(
                f"Link '{link}' is not valid for the {family} family. "
                f"Valid links: {', '.join(GLM_FAMILY_LINKS[family])}."
            )
        if family == "tweedie" and _configured(params, "var_power"):
            var_power = params["var_power"]
            _require_number(var_power, "Tweedie variance power must be a number from 1 to 2.")
            if not 1 <= var_power <= 2:
                raise TrainingConfigError(
                    "Tweedie variance power must be from 1 (Poisson) to 2 (Gamma)."
                )
        if family == "negbinomial" and _configured(params, "theta"):
            theta = params["theta"]
            _require_number(theta, "Negative Binomial theta must be a finite positive number.")
            if theta <= 0:
                raise TrainingConfigError(
                    "Negative Binomial theta must be a finite positive number."
                )
    if "intercept" in params and not isinstance(params["intercept"], bool):
        raise TrainingConfigError("GLM intercept must be true or false.")

    terms = params.get("terms") or {}
    interactions = params.get("interactions") or []
    try:
        glm_model_columns(terms, interactions)
    except TrainingConfigError:
        raise
    except HauteValidationError as exc:
        raise TrainingConfigError(str(exc)) from exc

    regularization = params.get("regularization") or None
    if regularization is not None:
        if regularization not in GLM_REGULARIZATIONS:
            raise TrainingConfigError(
                f"Unknown GLM regularization '{regularization}'. "
                f"Available: {', '.join(sorted(GLM_REGULARIZATIONS))}."
            )
        if _configured(params, "alpha"):
            alpha = params["alpha"]
            _require_number(alpha, "Regularization alpha must be a finite non-negative number.")
            if alpha < 0:
                raise TrainingConfigError(
                    "Regularization alpha must be a finite non-negative number."
                )
        if regularization == "elastic_net" and _configured(params, "l1_ratio"):
            l1_ratio = params["l1_ratio"]
            _require_number(l1_ratio, "Elastic-net L1 ratio must be a number from 0 to 1.")
            if not 0 <= l1_ratio <= 1:
                raise TrainingConfigError("Elastic-net L1 ratio must be a number from 0 to 1.")
        if glm_cross_validates(params):
            if _configured(params, "cv_folds"):
                folds = params["cv_folds"]
                low, high = GLM_CV_FOLDS_RANGE
                _require_number(
                    folds,
                    f"Cross-validation folds must be an integer from {low} to {high}.",
                    integer=True,
                )
                if not low <= folds <= high:
                    raise TrainingConfigError(
                        f"Cross-validation folds must be an integer from {low} to {high}."
                    )
            if _configured(params, "cv_selection") and (
                params["cv_selection"] not in GLM_CV_SELECTIONS
            ):
                raise TrainingConfigError(
                    "Cross-validation selection must be 'min' (lowest deviance) or '1se' "
                    "(one standard error)."
                )
            if _configured(params, "cv_seed"):
                seed = params["cv_seed"]
                _require_number(
                    seed, "Cross-validation seed must be a non-negative integer.", integer=True
                )
                if seed < 0:
                    raise TrainingConfigError(
                        "Cross-validation seed must be a non-negative integer."
                    )
        smooth = penalised_smooth_terms(terms, interactions)
        if smooth:
            raise TrainingConfigError(
                "Regularization cannot be combined with automatically smoothed splines "
                f"({', '.join(smooth)}): RustyStats already penalises them. Set Fixed df on "
                "those splines or turn regularization off."
            )

    if _configured(params, "max_iter"):
        low, high = GLM_MAX_ITER_RANGE
        max_iter = params["max_iter"]
        _require_number(
            max_iter, f"Maximum iterations must be an integer from {low} to {high}.", integer=True
        )
        if not low <= max_iter <= high:
            raise TrainingConfigError(
                f"Maximum iterations must be an integer from {low} to {high}."
            )
    if _configured(params, "tol"):
        tol = params["tol"]
        _require_number(tol, "Tolerance must be a number greater than 0 and less than 1.")
        if not 0 < tol < 1:
            raise TrainingConfigError("Tolerance must be a number greater than 0 and less than 1.")

    robust = params.get("robust_standard_errors") or None
    if robust is not None:
        if robust not in GLM_ROBUST_STANDARD_ERRORS:
            raise TrainingConfigError(
                "Robust standard errors must be one of "
                f"{', '.join(sorted(GLM_ROBUST_STANDARD_ERRORS))}."
            )
        if regularization is not None:
            raise TrainingConfigError(
                "Robust standard errors cannot be combined with regularization: RustyStats "
                "marks inference after a penalty as not valid."
            )
        monotone = monotone_constraint_terms(terms)
        if monotone:
            raise TrainingConfigError(
                "Robust standard errors cannot be combined with monotonicity constraints "
                f"({', '.join(repr(name) for name in monotone)}): RustyStats marks constrained "
                "inference as not valid."
            )
        smooth = penalised_smooth_terms(terms, interactions)
        if smooth:
            raise TrainingConfigError(
                "Robust standard errors cannot be combined with automatically smoothed splines "
                f"({', '.join(smooth)}): RustyStats marks their inference as not valid."
            )


class TrainingConfigError(HauteValidationError):
    """Raised when a modelling node config cannot produce a training job.

    Extends :class:`HauteValidationError`: every message raised here is
    haute-authored validation wording, safe to surface verbatim.
    """


def parse_evaluation_config(raw: Any) -> dict[str, Any]:
    """Validate and canonicalise the required public evaluation object."""
    if raw is None:
        raise TrainingConfigError(
            "Modelling config has no evaluation object. Open the Split pane and "
            "choose how the data is structured, how candidates are validated, "
            "and whether a final test is reserved."
        )
    try:
        return EvaluationConfig.from_plain_data(raw).to_plain_data()
    except (TypeError, ValueError) as exc:
        raise TrainingConfigError(f"Invalid evaluation config: {exc}") from exc


def parse_tuning_config(
    raw: Any,
    *,
    algorithm: str,
    base_params: Mapping[str, Any],
    evaluation: Mapping[str, Any],
    configured_metrics: list[str],
) -> dict[str, Any] | None:
    """Validate and canonicalise the optional public tuning object."""
    if raw is None:
        return None
    try:
        evaluation_config = EvaluationConfig.from_plain_data(evaluation)
        return TuningConfig.from_plain_data(
            raw,
            algorithm=algorithm,
            base_params=base_params,
            evaluation=evaluation_config,
            configured_metrics=configured_metrics,
        ).to_plain_data()
    except (TypeError, ValueError) as exc:
        raise TrainingConfigError(f"Invalid tuning config: {exc}") from exc


def default_metrics(
    task: str,
    *,
    loss_function: str | None = None,
    family: str | None = None,
) -> list[str]:
    """Default reported-metric list matched to the training objective.

    The headline metrics must follow the loss family: a Poisson or Tweedie
    frequency model reported with squared-error metrics produces plausible
    numbers that say nothing about the fit under the actual objective.
    """
    objective = str(family or loss_function or "").lower()
    if task == "classification" or objective in {
        "binomial",
        "quasibinomial",
        "logloss",
        "crossentropy",
    }:
        return ["auc", "logloss"]
    if objective in {"poisson", "quasipoisson", "negbinomial"}:
        return ["gini", "poisson_deviance"]
    if objective == "tweedie":
        return ["gini", "tweedie_deviance"]
    return ["gini", "rmse"]


def effective_metrics(config: Mapping[str, Any]) -> list[str]:
    """The reported-metric list training will actually use for this config.

    Explicit ``config["metrics"]`` wins; otherwise the objective-implied
    defaults from :func:`default_metrics` apply — notably, a
    classification-flavoured objective (binomial family, Logloss/CrossEntropy
    loss) implies AUC/log loss even under ``task="regression"``. Shared by
    ``build_training_job_kwargs`` and the train route's pre-dispatch
    target/task/metric gate so the two derivations can never drift.
    """
    algorithm = str(config.get("algorithm", "catboost")).lower()
    task = str(config.get("task", "regression"))
    if algorithm == "glm":
        family = config.get("family")
        loss_function = None
    else:
        family = None
        loss_function = config.get("loss_function")
    metrics = config.get("metrics") or default_metrics(
        task,
        loss_function=loss_function,
        family=family,
    )
    if not isinstance(metrics, list) or not all(
        isinstance(metric, str) and metric for metric in metrics
    ):
        raise TrainingConfigError("Configured metrics must be a non-empty string list.")
    return list(metrics)


def _excluded_feature_names(config: Mapping[str, Any]) -> set[str]:
    """Return exclusions that make feature settings dormant for this fit.

    The established explicit ``feature_columns`` contract wins over a stale entry
    in ``exclude``; keep that same precedence when projecting dependent settings.
    """
    raw = config.get("exclude")
    if not isinstance(raw, list):
        return set()
    excluded = {name for name in raw if isinstance(name, str) and name}
    explicit = config.get("feature_columns")
    if isinstance(explicit, list):
        excluded.difference_update(name for name in explicit if isinstance(name, str) and name)
    return excluded


def _effective_glm_params(config: Mapping[str, Any]) -> dict[str, Any]:
    """Project stored GLM config into the settings active for this fit.

    ``exclude`` is a CatBoost lever; a GLM feature is in the model exactly
    when it has a term or is an interaction factor, so nothing is narrowed.
    """
    params = {key: config[key] for key in GLM_CONFIG_KEYS if key in config}
    if glm_cross_validates(params) and not _configured(params, "cv_seed"):
        params["cv_seed"] = GLM_CV_DEFAULT_SEED
    return params


def _effective_monotone_constraints(config: Mapping[str, Any]) -> Any:
    """Omit dormant excluded constraints without mutating stored node config."""
    constraints = config.get("monotone_constraints")
    if not isinstance(constraints, Mapping):
        return constraints or None
    excluded = _excluded_feature_names(config)
    effective = {name: direction for name, direction in constraints.items() if name not in excluded}
    return effective or None


def training_objective_issue(config: Mapping[str, Any]) -> str | None:
    """Return an actionable message when the training objective is incomplete.

    An unset objective parameter must gate, never fall through to a library
    or literal failover (CatBoost RMSE, GLM gaussian, Tweedie power 1.5,
    Negative Binomial theta, elastic-net collapsing to ridge at l1_ratio=0,
    unseeded cross-validation). Shared by ``build_training_job_kwargs``
    (build/export time) and the train route's fast upfront validation so the
    two can never drift. Returns ``None`` when the objective is fully specified.
    """
    if is_glm_config(config):
        return glm_params_issue(_effective_glm_params(config))
    loss_function = config.get("loss_function")
    if not loss_function:
        return (
            "Modelling config has no loss function. Open the config panel and "
            "choose a training loss explicitly (e.g. Poisson for claim counts, "
            "RMSE for a squared-error regression) — an unset loss would "
            "silently train under the library default."
        )
    variance_power = config.get("variance_power")
    if str(loss_function) == "Tweedie" and (
        isinstance(variance_power, bool)
        or not isinstance(variance_power, (int, float))
        or not math.isfinite(variance_power)
        or not 1 < variance_power < 2
    ):
        return "Tweedie variance power must be a finite number greater than 1 and less than 2."
    return None


def build_train_params(config: Mapping[str, Any]) -> dict[str, Any]:
    """Build the algorithm ``params`` dict from a modelling-node config.

    GLM receives only its canonical top-level config keys. Any other algorithm
    receives a copy of ``config["params"]`` — CatBoost in particular has no
    ``**kwargs``, so a leaked GLM key (e.g. ``offset`` in the standard
    log-exposure frequency workflow) crashes the fit.
    """
    if is_glm_config(config):
        return _effective_glm_params(config)
    return {**(config.get("params") or {})}


def build_training_job_kwargs(
    config: Mapping[str, Any],
    *,
    data: str,
    default_name: str = "model",
) -> dict[str, Any]:
    """Build the canonical ``TrainingJob(**kwargs)`` mapping from a node config.

    Parameters
    ----------
    config:
        The modelling-node configuration.
    data:
        Path to the training data file the job will read.
    default_name:
        Name used when the config has no ``name`` (live training passes the
        node id; export defaults to ``"model"``).

    Raises
    ------
    ValueError
        If the config has no target column, an incomplete training objective
        (an unset loss/family, or an unset objective parameter that would fall
        through to a library/literal failover: Tweedie variance power, Negative
        Binomial theta, elastic-net L1 ratio, cross-validation settings, empty
        GLM term set), or invalid GLM params. Such a job/script trains a
        plausible-looking wrong model, so it must fail at build time, not at
        training time.
    """
    target = config.get("target")
    if not isinstance(target, str) or not target:
        raise TrainingConfigError(
            "Modelling config has no target column. "
            "Open the config panel and choose a target column."
        )

    params = build_train_params(config)
    algorithm = str(config.get("algorithm", "catboost")).lower()
    glm = is_glm_config(config)
    # A wrong value beats an incomplete one, matching the train route.
    if glm:
        validate_glm_params(params)
    objective_issue = training_objective_issue(config)
    if objective_issue is not None:
        raise TrainingConfigError(objective_issue)
    task = str(config.get("task", "regression"))
    variance_power = config.get("var_power") if glm else config.get("variance_power")
    legacy_fields = [key for key in ("split", "cross_validation") if key in config]
    if legacy_fields:
        raise TrainingConfigError(
            "Invalid legacy modelling config: public split/cross_validation fields "
            "were replaced by the canonical versioned evaluation object."
        )
    evaluation = parse_evaluation_config(config.get("evaluation"))
    metrics = effective_metrics(config)
    tuning = parse_tuning_config(
        config.get("tuning"),
        algorithm=algorithm,
        base_params=params,
        evaluation=evaluation,
        configured_metrics=metrics,
    )
    refit_on_development = config.get("refit_on_development", True)
    if not isinstance(refit_on_development, bool):
        raise TrainingConfigError("refit_on_development must be a boolean")
    if not refit_on_development and evaluation["validation"]["method"] != "single":
        raise TrainingConfigError("Skipping the final refit requires holdout validation")
    if not refit_on_development and tuning is not None:
        raise TrainingConfigError("Parameter tuning requires a final refit")

    destination = config.get("mlflow_destination") or ""
    if destination not in ("", "databricks", "server"):
        raise TrainingConfigError(
            "Modelling config has an invalid mlflow_destination; expected databricks or "
            "server. Remove the field to use the local MLflow folder."
        )

    return {
        "name": config.get("name", default_name),
        "data": data,
        "target": target,
        "weight": config.get("weight") or None,
        # CatBoost-only levers never reach a GLM job (CATBOOST_ONLY_LEVERS).
        "exclude": [] if glm else config.get("exclude", []),
        "feature_columns": None if glm else config.get("feature_columns") or None,
        "fold_column": config.get("fold_column") or None,
        "id_columns": config.get("id_columns") or None,
        "algorithm": config.get("algorithm", "catboost"),
        "task": task,
        "params": params,
        "evaluation": evaluation,
        "tuning": tuning,
        "refit_on_development": refit_on_development,
        "metrics": metrics,
        "mlflow_experiment": config.get("mlflow_experiment") or None,
        "output_dir": config.get("output_dir", "outputs"),
        "loss_function": config.get("loss_function") or None,
        "variance_power": variance_power,
        "offset": config.get("offset") or None,
        "monotone_constraints": None if glm else _effective_monotone_constraints(config),
        "feature_weights": None if glm else config.get("feature_weights") or None,
        "categorical_levels": config.get("categorical_levels") or None,
        "mlflow_destination": destination,
    }
