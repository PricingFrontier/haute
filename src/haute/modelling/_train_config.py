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

from collections.abc import Mapping
from typing import Any

from haute.modelling._split import DEFAULT_SPLIT_DICT

# GLM config keys live at the top level of the modelling-node config (not
# inside ``config["params"]``) and are consumed by ``GLMAlgorithm.fit`` via
# the params dict. They must be merged into train params for GLM only:
# CatBoost receives ``params`` verbatim as constructor kwargs.
GLM_CONFIG_KEYS: tuple[str, ...] = (
    "terms",
    "all_factors",
    "family",
    "link",
    "interactions",
    "regularization",
    "alpha",
    "l1_ratio",
    "intercept",
    "var_power",
    "theta",
    "offset",
)


class TrainingConfigError(ValueError):
    """Raised when a modelling node config cannot produce a training job."""


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
    if task == "classification" or objective in {"binomial", "logloss", "crossentropy"}:
        return ["auc", "logloss"]
    if objective in {"poisson", "quasipoisson", "negbinomial"}:
        return ["gini", "poisson_deviance"]
    if objective == "tweedie":
        return ["gini", "tweedie_deviance"]
    return ["gini", "rmse"]


def _power_values_equal(left: Any, right: Any) -> bool:
    try:
        return float(left) == float(right)
    except (TypeError, ValueError):
        return bool(left == right)


def _first_set(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def training_objective_issue(config: Mapping[str, Any]) -> str | None:
    """Return an actionable message when the training objective is incomplete.

    An unset objective parameter must gate, never fall through to a library
    or literal failover (CatBoost RMSE, GLM gaussian, Tweedie power 1.5,
    Negative Binomial theta 1.0, elastic-net collapsing to ridge at
    l1_ratio=0, auto-terms over every column). Shared by
    ``build_training_job_kwargs`` (build/export time) and
    the train route's fast upfront validation so the two can never drift.
    Returns ``None`` when the objective is fully specified.
    """
    params = config.get("params")
    if not isinstance(params, Mapping):
        params = {}
    algorithm = str(config.get("algorithm", "catboost")).lower()
    if algorithm == "glm":
        family = params.get("family") or config.get("family")
        if not family:
            return (
                "GLM config has no family. Open the config panel and choose a "
                "distribution family explicitly (e.g. poisson for claim counts, "
                "gamma for severity) — an unset family would silently train a "
                "gaussian model."
            )
        var_power = _first_set(
            params.get("var_power"),
            config.get("var_power"),
            config.get("variance_power"),
        )
        if str(family).lower() == "tweedie" and var_power is None:
            return (
                "Tweedie GLM has no variance power. Set it explicitly "
                "(1=Poisson, 2=Gamma) — an unset value would silently fit "
                "at power 1.5."
            )
        theta = _first_set(params.get("theta"), config.get("theta"))
        if str(family).lower() == "negbinomial" and theta is None:
            return (
                "Negative Binomial GLM has no dispersion (theta). Set it "
                "explicitly or estimate it from the data — RustyStats does "
                "not estimate theta, so an unset value would silently fit "
                "at theta=1.0."
            )
        terms = _first_set(params.get("terms"), config.get("terms"))
        all_factors = _first_set(params.get("all_factors"), config.get("all_factors"))
        if not terms and not all_factors:
            return (
                "GLM config has no factors. Add factors or tick 'All features' "
                "— an empty factor set would silently auto-build a term for "
                "every column."
            )
        regularization = _first_set(params.get("regularization"), config.get("regularization"))
        l1_ratio = _first_set(params.get("l1_ratio"), config.get("l1_ratio"))
        if str(regularization or "").lower() == "elastic_net" and l1_ratio is None:
            return (
                "Elastic-net regularisation has no L1 ratio. Set it explicitly "
                "(0 fits Ridge, 1 fits LASSO) — an unset value would silently "
                "fit pure Ridge."
            )
    else:
        loss_function = config.get("loss_function")
        if not loss_function:
            return (
                "Modelling config has no loss function. Open the config panel and "
                "choose a training loss explicitly (e.g. Poisson for claim counts, "
                "RMSE for a squared-error regression) — an unset loss would "
                "silently train under the library default."
            )
        variance_power = _first_set(config.get("variance_power"), config.get("var_power"))
        if str(loss_function) == "Tweedie" and variance_power is None:
            return (
                "Tweedie loss has no variance power. Set it explicitly "
                "(1=Poisson, 2=Gamma) — an unset value would silently train "
                "at power 1.5."
            )
    return None


def build_train_params(config: Mapping[str, Any]) -> dict[str, Any]:
    """Build the algorithm ``params`` dict from a modelling-node config.

    Starts from a copy of ``config["params"]``. For GLM only, top-level GLM
    config keys are merged in (without overriding explicit ``params`` entries).
    Any other algorithm receives ``config["params"]`` untouched — CatBoost in
    particular has no ``**kwargs``, so a leaked GLM key (e.g. ``offset`` in the
    standard log-exposure frequency workflow) crashes the fit.
    """
    params: dict[str, Any] = {**(config.get("params") or {})}
    algorithm = str(config.get("algorithm", "catboost")).lower()
    if algorithm == "glm":
        for key in GLM_CONFIG_KEYS:
            if key in config and key not in params:
                params[key] = config[key]
        if config.get("variance_power") is not None and "var_power" not in params:
            params["var_power"] = config["variance_power"]
    return params


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
        If the config has no target column, or an incomplete training
        objective — an unset loss/family, or an unset objective parameter
        that would fall through to a library/literal failover (Tweedie
        variance power, Negative Binomial theta, elastic-net L1 ratio,
        empty GLM factor set). Such a
        job/script trains a plausible-looking wrong model, so it must fail at
        build time, not at training time.
    """
    target = config.get("target")
    if not isinstance(target, str) or not target:
        raise TrainingConfigError(
            "Modelling config has no target column. "
            "Open the config panel and choose a target column."
        )

    objective_issue = training_objective_issue(config)
    if objective_issue is not None:
        raise TrainingConfigError(objective_issue)

    params = build_train_params(config)
    algorithm = str(config.get("algorithm", "catboost")).lower()
    task = str(config.get("task", "regression"))
    family = params.get("family") if algorithm == "glm" else None
    loss_function = None if algorithm == "glm" else config.get("loss_function")
    raw_params = config.get("params") or {}
    params_has_explicit_var_power = (
        isinstance(raw_params, Mapping) and raw_params.get("var_power") is not None
    )
    variance_power = (
        config.get("variance_power")
        if config.get("variance_power") is not None
        else config.get("var_power")
    )
    if algorithm == "glm" and params.get("var_power") is not None:
        if (
            config.get("variance_power") is not None
            and params_has_explicit_var_power
            and not _power_values_equal(config.get("variance_power"), params["var_power"])
        ):
            raise TrainingConfigError(
                "GLM config has conflicting variance_power and params['var_power'] "
                "settings. Keep one Tweedie variance-power source."
            )
        variance_power = params["var_power"]

    return {
        "name": config.get("name", default_name),
        "data": data,
        "target": target,
        "weight": config.get("weight") or None,
        "exclude": config.get("exclude", []),
        "feature_columns": config.get("feature_columns") or None,
        "fold_column": config.get("fold_column") or None,
        "id_columns": config.get("id_columns") or None,
        "algorithm": config.get("algorithm", "catboost"),
        "task": task,
        "params": params,
        "split": config.get("split", DEFAULT_SPLIT_DICT),
        "metrics": config.get("metrics")
        or default_metrics(task, loss_function=loss_function, family=family),
        "mlflow_experiment": config.get("mlflow_experiment") or None,
        "model_name": config.get("model_name") or None,
        "output_dir": config.get("output_dir", "outputs"),
        "loss_function": config.get("loss_function") or None,
        "variance_power": variance_power,
        "offset": config.get("offset") or None,
        "monotone_constraints": config.get("monotone_constraints") or None,
        "feature_weights": config.get("feature_weights") or None,
        "categorical_levels": config.get("categorical_levels") or None,
    }
