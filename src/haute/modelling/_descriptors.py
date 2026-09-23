"""Capability descriptors for the modelling node's algorithms.

One frozen :class:`AlgorithmDescriptor` per registry key states what a family
supports: tasks and Haute losses (with the native objective each translates
to), the raw-``params`` policy, the refit round key, feature controls, and the
artifact suffix. Validation and dispatch read these descriptors instead of
branching on ``algorithm == "catboost"``. Importing this module never imports
an engine.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal

from haute.errors import HauteValidationError

Link = Literal["identity", "log", "logit"]
RefitPolicy = Literal["validation_weighted_rounds", "fixed_budget", "none"]
FeatureControl = Literal["monotone_constraints", "feature_weights", "interactions"]

#: The Haute loss vocabulary shared by every tree and EBM family. A family's
#: descriptor lists the subset it supports and the native objective each
#: translates to; the GLM configures ``family``/``link`` instead.
HAUTE_LOSSES: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "regression": ("RMSE", "MAE", "Poisson", "Gamma", "Tweedie"),
        "classification": ("Logloss", "CrossEntropy"),
    }
)

TRAINING_THREADS_ENV = "HAUTE_TRAINING_THREADS"


@dataclass(frozen=True)
class NativeLoss:
    """The native objective a Haute loss translates to, and its prediction link."""

    objective: str
    link: Link


@dataclass(frozen=True)
class AlgorithmDescriptor:
    """What one modelling family supports; the single source for validation."""

    key: str
    label: str
    tasks: frozenset[str]
    losses: Mapping[str, Mapping[str, NativeLoss]]
    #: ``None`` keeps the family's existing parameter contract (CatBoost
    #: forwards raw params; the GLM validates its own config keys).
    allowed_params: frozenset[str] | None
    reserved_params: frozenset[str]
    #: Keys a tuning search space may never search (orchestration-owned).
    tuning_reserved_params: frozenset[str]
    param_aliases: Mapping[str, str]
    #: The native parameter the refit round count is written to.
    round_key: str | None
    #: Every raw-param spelling of the round count, ``round_key`` included.
    round_key_aliases: tuple[str, ...]
    #: Params that only make sense with a validation set (early stopping).
    validation_only_params: tuple[str, ...]
    refit_policy: RefitPolicy
    feature_controls: frozenset[FeatureControl]
    suffix: str
    engine_module: str

    def __post_init__(self) -> None:
        if not self.suffix.startswith(".") or len(self.suffix) < 2:
            raise ValueError(f"descriptor {self.key!r} needs an artifact suffix like '.cbm'")
        if self.refit_policy == "validation_weighted_rounds" and not self.round_key:
            raise ValueError(f"descriptor {self.key!r} refits rounds but names no round key")
        if self.round_key and self.round_key not in self.round_key_aliases:
            raise ValueError(f"descriptor {self.key!r} round key must be among its aliases")
        for task, losses in self.losses.items():
            if task not in self.tasks:
                raise ValueError(f"descriptor {self.key!r} lists losses for unsupported {task}")
            unknown = set(losses) - set(HAUTE_LOSSES[task])
            if unknown:
                raise ValueError(f"descriptor {self.key!r} lists unknown losses {sorted(unknown)}")

    @property
    def supports_tuning(self) -> bool:
        return self.refit_policy != "none"

    @property
    def refit_round_key(self) -> str:
        """The round key a round-refitting family writes its final count to."""
        if self.round_key is None:
            raise HauteValidationError(f"{self.label} has no refit round key")
        return self.round_key

    def native_loss(self, task: str, loss: str) -> NativeLoss:
        """The native objective for a Haute loss, or a validation error naming why not."""
        if task not in self.tasks:
            raise HauteValidationError(f"{self.label} does not support the {task} task.")
        vocabulary = HAUTE_LOSSES.get(task, ())
        if loss not in vocabulary:
            raise HauteValidationError(
                f"Loss '{loss}' is not valid for task '{task}'. Choose from: {sorted(vocabulary)}"
            )
        supported = self.losses.get(task, {})
        if loss not in supported:
            raise HauteValidationError(
                f"{self.label} does not support the {loss} loss. "
                f"Supported {task} losses: {sorted(supported)}."
            )
        return supported[loss]

    def validate_params(self, params: Mapping[str, Any], *, context: str = "params") -> None:
        """Reject reserved, aliased, duplicate, or (when allowlisted) unknown params."""
        from haute.modelling._train_config import TrainingConfigError

        canonical_seen: dict[str, str] = {}
        for key in params:
            if key in self.reserved_params:
                raise TrainingConfigError(
                    f"{self.label} {context} cannot set '{key}': Haute owns it."
                )
            canonical = self.param_aliases.get(key, key)
            if canonical != key:
                raise TrainingConfigError(
                    f"{self.label} {context} key '{key}' is an alias; use '{canonical}'."
                )
            if canonical in canonical_seen:
                raise TrainingConfigError(
                    f"{self.label} {context} set '{canonical}' twice "
                    f"('{canonical_seen[canonical]}' and '{key}')."
                )
            canonical_seen[canonical] = key
            if self.allowed_params is not None and key not in self.allowed_params:
                raise TrainingConfigError(
                    f"{self.label} {context} key '{key}' is not supported. "
                    f"Supported keys: {', '.join(sorted(self.allowed_params))}."
                )


def _losses(**by_task: Mapping[str, NativeLoss]) -> Mapping[str, Mapping[str, NativeLoss]]:
    return MappingProxyType(
        {task: MappingProxyType(dict(losses)) for task, losses in by_task.items()}
    )


CATBOOST = AlgorithmDescriptor(
    key="catboost",
    label="CatBoost",
    tasks=frozenset({"regression", "classification"}),
    losses=_losses(
        regression={
            "RMSE": NativeLoss("RMSE", "identity"),
            "MAE": NativeLoss("MAE", "identity"),
            "Poisson": NativeLoss("Poisson", "log"),
            "Tweedie": NativeLoss("Tweedie", "log"),
        },
        classification={
            "Logloss": NativeLoss("Logloss", "logit"),
            "CrossEntropy": NativeLoss("CrossEntropy", "logit"),
        },
    ),
    allowed_params=None,
    # ``thread_count`` belongs to the allotment; ``class_names`` would reorder
    # the probability columns away from the job's positive = 1 encoding.
    reserved_params=frozenset({"thread_count", "class_names"}),
    tuning_reserved_params=frozenset(
        {
            "allow_writing_files",
            "callbacks",
            "class_names",
            "data_partition",
            "device",
            "devices",
            "dev_score_calc_obj_block_size",
            "eval_metric",
            "gpu_cat_features_storage",
            "gpu_ram_part",
            "iterations",
            "loss_function",
            "objective",
            "od_pval",
            "od_type",
            "od_wait",
            "pinned_memory_size",
            "random_seed",
            "random_state",
            "save_snapshot",
            "snapshot_file",
            "task_type",
            "thread_count",
            "train_dir",
            "used_ram_limit",
            "use_best_model",
        }
    ),
    param_aliases=MappingProxyType({}),
    round_key="iterations",
    round_key_aliases=("iterations", "n_estimators", "num_boost_round", "num_trees"),
    validation_only_params=(
        "early_stopping_rounds",
        "od_pval",
        "od_type",
        "od_wait",
        "use_best_model",
    ),
    refit_policy="validation_weighted_rounds",
    feature_controls=frozenset({"monotone_constraints", "feature_weights"}),
    suffix=".cbm",
    engine_module="catboost",
)

GLM = AlgorithmDescriptor(
    key="glm",
    label="GLM",
    # A binomial GLM predicts a probability as a regression; it has no class labels.
    tasks=frozenset({"regression"}),
    losses=_losses(),
    allowed_params=None,
    reserved_params=frozenset(),
    tuning_reserved_params=frozenset(),
    param_aliases=MappingProxyType({}),
    round_key=None,
    round_key_aliases=(),
    validation_only_params=(),
    refit_policy="none",
    feature_controls=frozenset(),
    suffix=".rsglm",
    engine_module="rustystats",
)

XGBOOST = AlgorithmDescriptor(
    key="xgboost",
    label="XGBoost",
    tasks=frozenset({"regression", "classification"}),
    losses=_losses(
        regression={
            "RMSE": NativeLoss("reg:squarederror", "identity"),
            "MAE": NativeLoss("reg:absoluteerror", "identity"),
            "Poisson": NativeLoss("count:poisson", "log"),
            "Gamma": NativeLoss("reg:gamma", "log"),
            "Tweedie": NativeLoss("reg:tweedie", "log"),
        },
        classification={"Logloss": NativeLoss("binary:logistic", "logit")},
    ),
    allowed_params=frozenset(
        {
            "num_boost_round",
            "early_stopping_rounds",
            "eta",
            "max_depth",
            "max_leaves",
            "grow_policy",
            "min_child_weight",
            "gamma",
            "max_delta_step",
            "subsample",
            "colsample_bytree",
            "colsample_bylevel",
            "colsample_bynode",
            "lambda",
            "alpha",
            "max_bin",
            "max_cat_to_onehot",
            "max_cat_threshold",
        }
    ),
    reserved_params=frozenset(
        {
            "objective",
            "tweedie_variance_power",
            "eval_metric",
            "base_score",
            "tree_method",
            "booster",
            "device",
            "nthread",
            "n_jobs",
            "seed",
            "random_state",
            "enable_categorical",
            "feature_names",
            "feature_types",
            "monotone_constraints",
            "interaction_constraints",
            "callbacks",
            "base_margin",
        }
    ),
    tuning_reserved_params=frozenset({"num_boost_round"}),
    param_aliases=MappingProxyType(
        {
            "learning_rate": "eta",
            "min_split_loss": "gamma",
            "reg_lambda": "lambda",
            "reg_alpha": "alpha",
            "n_estimators": "num_boost_round",
        }
    ),
    round_key="num_boost_round",
    round_key_aliases=("num_boost_round",),
    validation_only_params=("early_stopping_rounds",),
    refit_policy="validation_weighted_rounds",
    feature_controls=frozenset({"monotone_constraints"}),
    suffix=".ubj",
    engine_module="xgboost",
)

DESCRIPTORS: Mapping[str, AlgorithmDescriptor] = MappingProxyType(
    {descriptor.key: descriptor for descriptor in (CATBOOST, GLM, XGBOOST)}
)


def algorithm_descriptor(algorithm: str) -> AlgorithmDescriptor:
    """The descriptor for *algorithm*, or a validation error listing the choices."""
    from haute.modelling._train_config import TrainingConfigError

    key = str(algorithm).lower()
    descriptor = DESCRIPTORS.get(key)
    if descriptor is None:
        raise TrainingConfigError(
            f"Unknown algorithm: {algorithm}. Available: {', '.join(DESCRIPTORS)}."
        )
    return descriptor


def project_refit_params(
    descriptor: AlgorithmDescriptor,
    params: Mapping[str, Any],
    round_count: int,
) -> dict[str, Any]:
    """Final-fit params: validation-only keys dropped, the round count under the round key."""
    round_key = descriptor.refit_round_key
    projected = {
        key: value
        for key, value in params.items()
        if key not in descriptor.round_key_aliases and key not in descriptor.validation_only_params
    }
    projected[round_key] = round_count
    return projected


def refit_descriptor(final_params: Mapping[str, Any]) -> AlgorithmDescriptor:
    """The round-refitting family whose round key a final-fit param set carries.

    :func:`project_refit_params` writes exactly one family's round key and drops
    every other spelling, so the key identifies the family unambiguously.
    """
    matches = [
        descriptor
        for descriptor in DESCRIPTORS.values()
        if descriptor.refit_policy == "validation_weighted_rounds"
        and descriptor.round_key in final_params
    ]
    if len(matches) != 1:
        raise HauteValidationError(
            "final-fit parameters must carry exactly one family's refit round key"
        )
    return matches[0]


def round_ceiling(descriptor: AlgorithmDescriptor, params: Mapping[str, Any], default: int) -> Any:
    """The configured round ceiling under any of the family's round-count spellings."""
    return next(
        (params[key] for key in descriptor.round_key_aliases if key in params),
        default,
    )


def training_threads() -> int:
    """The per-job thread allotment every engine receives.

    ``HAUTE_TRAINING_THREADS`` overrides the default of every logical CPU,
    which matches CatBoost's own ``thread_count=-1`` default.
    """
    raw = os.environ.get(TRAINING_THREADS_ENV)
    if raw is None or raw.strip() == "":
        return os.cpu_count() or 1
    try:
        threads = int(raw)
    except ValueError:
        threads = 0
    if threads <= 0:
        raise HauteValidationError(
            f"{TRAINING_THREADS_ENV} must be a positive integer, got {raw!r}."
        )
    return threads


def capability_fixture() -> dict[str, Any]:
    """The serialisable capability table the frontend's checked-in fixture mirrors."""
    return {
        key: {
            "label": descriptor.label,
            "tasks": sorted(descriptor.tasks),
            "losses": {
                task: [loss for loss in HAUTE_LOSSES[task] if loss in losses]
                for task, losses in sorted(descriptor.losses.items())
            },
            "feature_controls": sorted(descriptor.feature_controls),
            "refit_policy": descriptor.refit_policy,
            "allowed_params": (
                sorted(descriptor.allowed_params) if descriptor.allowed_params is not None else None
            ),
            "reserved_params": sorted(descriptor.reserved_params),
            "round_key": descriptor.round_key,
            "round_key_aliases": list(descriptor.round_key_aliases),
            "validation_only_params": list(descriptor.validation_only_params),
            "suffix": descriptor.suffix,
            "supports_tuning": descriptor.supports_tuning,
        }
        for key, descriptor in DESCRIPTORS.items()
    }
