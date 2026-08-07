"""Strict, algorithm-neutral contracts for bounded hyperparameter tuning.

Optuna owns only parameter suggestion. Haute continues to own evaluation
partitions, fitting, metrics, cancellation, artifacts, and winner selection.
The helpers in this module deliberately contain no fitting or job-lifecycle
logic so live training and exported scripts can share the same contract.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from haute.errors import HauteValidationError
from haute.modelling._evaluation import (
    EvaluationConfig,
    EvaluationFitResult,
    canonical_json_bytes,
    save_canonical_json,
)

TUNING_SCHEMA_VERSION = 1
MIN_TRIAL_COUNT = 5
MAX_TRIAL_COUNT = 50
MAX_TRIAL_FITS = 200
MIN_SEARCH_ENTRIES = 1
MAX_SEARCH_ENTRIES = 32
MAX_PARAMETER_CHOICES = 50

MetricDirection = Literal["maximize", "minimize"]

# These values are owned by orchestration or by the evaluation contract. A
# trial must never be able to replace them behind Haute's back.
_ORCHESTRATION_OWNED_KEYS = frozenset(
    {
        "allow_writing_files",
        "callbacks",
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
)

_MAXIMIZE_METRICS = frozenset({"gini", "auc", "r2"})
_MINIMIZE_METRICS = frozenset(
    {
        "rmse",
        "mae",
        "mse",
        "logloss",
        "poisson_deviance",
        "tweedie_deviance",
    }
)


def _exact_keys(raw: Mapping[str, Any], expected: set[str], name: str) -> None:
    actual = set(raw)
    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        raise HauteValidationError(
            f"{name} fields must be exact; missing={missing}, unknown={unknown}"
        )


def _exact_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise HauteValidationError(f"{name} must be an exact integer")
    return int(value)


def _finite_number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise HauteValidationError(f"{name} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise HauteValidationError(f"{name} must be a finite number")
    return result


def _canonical_key(value: Any) -> bytes:
    def normalise(item: Any) -> Any:
        if isinstance(item, float) and item.is_integer():
            return int(item)
        if isinstance(item, Mapping):
            return {key: normalise(child) for key, child in item.items()}
        if isinstance(item, (list, tuple)):
            return [normalise(child) for child in item]
        return item

    return canonical_json_bytes(normalise(value))


def _assert_finite_json(value: Any, name: str) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise HauteValidationError(f"{name} contains a non-finite number")
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise HauteValidationError(f"{name} object keys must be strings")
        for key, item in value.items():
            _assert_finite_json(item, f"{name}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _assert_finite_json(item, f"{name}[{index}]")
    elif not isinstance(value, (str, int, float, bool, type(None))):
        raise HauteValidationError(f"{name} contains unsupported JSON value {type(value).__name__}")


def _canonical_metric_name(value: str) -> str:
    return value.strip().lower().replace(" ", "_").replace("-", "_").replace("²", "2")


def metric_direction(metric: str) -> MetricDirection:
    """Return the server-owned optimisation direction for a supported metric."""
    if not isinstance(metric, str) or not metric.strip():
        raise HauteValidationError("tuning metric must be a non-empty string")
    canonical = _canonical_metric_name(metric)
    if canonical in _MAXIMIZE_METRICS:
        return "maximize"
    if canonical in _MINIMIZE_METRICS:
        return "minimize"
    raise HauteValidationError(
        f"metric {metric!r} is not a supported tuning objective; "
        f"supported={sorted(_MAXIMIZE_METRICS | _MINIMIZE_METRICS)}"
    )


def _parse_when(
    raw: Any,
    *,
    entry_name: str,
) -> dict[str, tuple[Any, ...]]:
    if not isinstance(raw, Mapping) or not raw:
        raise HauteValidationError(f"search_space.{entry_name}.when must be a non-empty object")
    parsed: dict[str, tuple[Any, ...]] = {}
    for parent, choices in raw.items():
        if not isinstance(parent, str) or not parent:
            raise HauteValidationError(
                f"search_space.{entry_name}.when parent names must be non-empty strings"
            )
        if not isinstance(choices, list) or not choices:
            raise HauteValidationError(
                f"search_space.{entry_name}.when.{parent} must be a non-empty list"
            )
        for index, choice in enumerate(choices):
            _assert_finite_json(
                choice,
                f"search_space.{entry_name}.when.{parent}[{index}]",
            )
        canonical = [_canonical_key(choice) for choice in choices]
        if len(set(canonical)) != len(canonical):
            raise HauteValidationError(
                f"search_space.{entry_name}.when.{parent} choices must be canonically distinct"
            )
        parsed[parent] = tuple(copy.deepcopy(choices))
    return parsed


def _parse_search_entry(name: str, raw: Any) -> dict[str, Any]:
    if isinstance(raw, list):
        choices = raw
        when = {}
    elif isinstance(raw, Mapping):
        _exact_keys(raw, {"choices", "when"}, f"search_space.{name}")
        choices = raw["choices"]
        when = _parse_when(raw["when"], entry_name=name)
    else:
        raise HauteValidationError(
            f"search_space.{name} must be a choices list or an object with choices and when"
        )

    if not isinstance(choices, list) or not 2 <= len(choices) <= MAX_PARAMETER_CHOICES:
        raise HauteValidationError(
            f"search_space.{name}.choices must contain 2 through {MAX_PARAMETER_CHOICES} values"
        )
    for index, choice in enumerate(choices):
        _assert_finite_json(choice, f"search_space.{name}.choices[{index}]")
    canonical = [_canonical_key(choice) for choice in choices]
    if len(set(canonical)) != len(canonical):
        raise HauteValidationError(f"search_space.{name}.choices must be canonically distinct")
    return {
        "choices": tuple(copy.deepcopy(choices)),
        "when": when,
    }


def _validate_conditions(
    search_space: Mapping[str, Mapping[str, Any]],
    base_params: Mapping[str, Any],
) -> tuple[str, ...]:
    dependencies: dict[str, set[str]] = {name: set() for name in search_space}
    for name, entry in search_space.items():
        for parent, allowed in entry["when"].items():
            if parent in search_space:
                parent_entry = search_space[parent]
                parent_choices = {_canonical_key(value) for value in parent_entry["choices"]}
                allowed_choices = {_canonical_key(value) for value in allowed}
                if not parent_choices.intersection(allowed_choices):
                    raise HauteValidationError(
                        f"search_space.{name}.when condition on {parent!r} is impossible"
                    )
                if not allowed_choices <= parent_choices:
                    raise HauteValidationError(
                        f"search_space.{name}.when condition on {parent!r} "
                        "must use only the parent's declared choices"
                    )
                dependencies[name].add(parent)
            elif parent in base_params:
                fixed = _canonical_key(base_params[parent])
                if fixed not in {_canonical_key(value) for value in allowed}:
                    raise HauteValidationError(
                        f"search_space.{name}.when fixed condition on {parent!r} is impossible"
                    )
            else:
                raise HauteValidationError(
                    f"search_space.{name}.when references unknown parent {parent!r}"
                )

    ordered: list[str] = []
    pending = {name: set(parents) for name, parents in dependencies.items()}
    while pending:
        ready = sorted(name for name, parents in pending.items() if not parents)
        if not ready:
            cycle = sorted(pending)
            raise HauteValidationError(f"search_space conditions contain a cycle: {cycle}")
        for name in ready:
            ordered.append(name)
            pending.pop(name)
        for parents in pending.values():
            parents.difference_update(ready)
    return tuple(ordered)


@dataclass(frozen=True)
class TuningConfig:
    schema_version: int
    trial_count: int
    seed: int
    metric: str
    search_space: Mapping[str, Mapping[str, Any]]
    suggestion_order: tuple[str, ...]
    direction: MetricDirection
    validation_fit_count: int
    trial_fit_count: int
    total_fit_count: int

    @classmethod
    def from_plain_data(
        cls,
        raw: Any,
        *,
        algorithm: str,
        base_params: Mapping[str, Any],
        evaluation: EvaluationConfig,
        configured_metrics: Sequence[str],
    ) -> TuningConfig:
        if not isinstance(raw, Mapping):
            raise HauteValidationError("tuning config must be an object")
        expected = {"schema_version", "seed", "metric", "search_space"} | (
            {"trial_count"} if "trial_count" in raw else set()
        )
        _exact_keys(raw, expected, "tuning")
        schema_version = _exact_int(raw.get("schema_version"), "tuning.schema_version")
        if schema_version != TUNING_SCHEMA_VERSION:
            raise HauteValidationError(f"tuning.schema_version must be {TUNING_SCHEMA_VERSION}")
        if str(algorithm).lower() != "catboost":
            raise HauteValidationError("tuning version 1 supports CatBoost only")
        if evaluation.validation_fit_count == 0:
            raise HauteValidationError("tuning requires single or cross-validation")

        trial_count = _exact_int(raw.get("trial_count", 20), "tuning.trial_count")
        if not MIN_TRIAL_COUNT <= trial_count <= MAX_TRIAL_COUNT:
            raise HauteValidationError(
                f"tuning.trial_count must be from {MIN_TRIAL_COUNT} through {MAX_TRIAL_COUNT}"
            )
        seed = _exact_int(raw.get("seed"), "tuning.seed")
        metric_raw = raw.get("metric")
        if not isinstance(metric_raw, str) or not metric_raw.strip():
            raise HauteValidationError("tuning.metric must be a non-empty string")
        configured_by_canonical = {
            _canonical_metric_name(name): name for name in configured_metrics
        }
        metric_key = _canonical_metric_name(metric_raw)
        if metric_key not in configured_by_canonical:
            raise HauteValidationError(
                f"tuning.metric {metric_raw!r} must be one of configured metrics "
                f"{list(configured_metrics)!r}"
            )
        metric = configured_by_canonical[metric_key]
        direction = metric_direction(metric)

        raw_space = raw.get("search_space")
        if not isinstance(raw_space, Mapping):
            raise HauteValidationError("tuning.search_space must be an object")
        if not MIN_SEARCH_ENTRIES <= len(raw_space) <= MAX_SEARCH_ENTRIES:
            raise HauteValidationError(
                f"tuning.search_space must contain {MIN_SEARCH_ENTRIES} through "
                f"{MAX_SEARCH_ENTRIES} entries"
            )
        parsed_space: dict[str, Mapping[str, Any]] = {}
        for name, entry in raw_space.items():
            if not isinstance(name, str) or not name:
                raise HauteValidationError("tuning.search_space names must be non-empty strings")
            if name in _ORCHESTRATION_OWNED_KEYS:
                raise HauteValidationError(
                    f"tuning.search_space cannot search orchestration-owned key {name!r}"
                )
            parsed_space[name] = _parse_search_entry(name, entry)
        suggestion_order = _validate_conditions(parsed_space, base_params)

        validation_fit_count = evaluation.validation_fit_count
        trial_fit_count = trial_count * validation_fit_count
        if trial_fit_count > MAX_TRIAL_FITS:
            raise HauteValidationError(
                f"tuning requires {trial_fit_count} trial fits; the maximum is {MAX_TRIAL_FITS}"
            )
        return cls(
            schema_version=schema_version,
            trial_count=trial_count,
            seed=seed,
            metric=metric,
            search_space=parsed_space,
            suggestion_order=suggestion_order,
            direction=direction,
            validation_fit_count=validation_fit_count,
            trial_fit_count=trial_fit_count,
            total_fit_count=trial_fit_count + 1,
        )

    def to_plain_data(self) -> dict[str, Any]:
        space: dict[str, Any] = {}
        for name in sorted(self.search_space):
            entry = self.search_space[name]
            if entry["when"]:
                space[name] = {
                    "choices": copy.deepcopy(list(entry["choices"])),
                    "when": {
                        parent: copy.deepcopy(list(values))
                        for parent, values in sorted(entry["when"].items())
                    },
                }
            else:
                space[name] = copy.deepcopy(list(entry["choices"]))
        return {
            "schema_version": self.schema_version,
            "trial_count": self.trial_count,
            "seed": self.seed,
            "metric": self.metric,
            "search_space": space,
        }


def _condition_matches(
    allowed: Sequence[Any],
    actual: Any,
) -> bool:
    actual_key = _canonical_key(actual)
    return actual_key in {_canonical_key(value) for value in allowed}


def suggest_parameters(
    trial: Any,
    config: TuningConfig,
    base_params: Mapping[str, Any],
) -> dict[str, Any]:
    """Resolve one conditional suggestion through an Optuna Trial-like object."""
    sampled: dict[str, Any] = {}
    for name in config.suggestion_order:
        entry = config.search_space[name]
        active = True
        for parent, allowed in entry["when"].items():
            parent_value = sampled[parent] if parent in sampled else base_params.get(parent)
            if not _condition_matches(allowed, parent_value):
                active = False
                break
        if not active:
            continue
        encoded = [canonical_json_bytes(choice).decode("utf-8") for choice in entry["choices"]]
        selected = trial.suggest_categorical(name, encoded)
        sampled[name] = json.loads(selected)
    return sampled


def resolve_trial_parameters(
    base_params: Mapping[str, Any],
    sampled_params: Mapping[str, Any],
) -> dict[str, Any]:
    """Return a non-mutating fixed-plus-sampled parameter projection."""
    resolved = copy.deepcopy(dict(base_params))
    for name, value in sampled_params.items():
        resolved[name] = copy.deepcopy(value)
    return resolved


@dataclass(frozen=True)
class TuningTrialResult:
    schema_version: int
    trial_index: int
    label: Literal["baseline", "sampled"]
    sampled_params: Mapping[str, Any]
    resolved_params: Mapping[str, Any]
    fits: tuple[Any, ...]
    aggregate_metrics: Mapping[str, float]
    objective: float
    elapsed_seconds: float

    def __post_init__(self) -> None:
        if _exact_int(self.schema_version, "trial schema_version") != TUNING_SCHEMA_VERSION:
            raise HauteValidationError(f"trial schema_version must be {TUNING_SCHEMA_VERSION}")
        trial_index = _exact_int(self.trial_index, "trial_index")
        if trial_index < 0:
            raise HauteValidationError("trial_index must be a non-negative integer")
        if self.label not in {"baseline", "sampled"}:
            raise HauteValidationError("trial label must be baseline or sampled")
        if self.trial_index == 0 and self.label != "baseline":
            raise HauteValidationError("trial zero must be labelled baseline")
        if self.trial_index > 0 and self.label != "sampled":
            raise HauteValidationError("non-zero trials must be labelled sampled")
        _assert_finite_json(self.sampled_params, "sampled_params")
        _assert_finite_json(self.resolved_params, "resolved_params")
        if not self.aggregate_metrics:
            raise HauteValidationError("aggregate_metrics must not be empty")
        for name, value in self.aggregate_metrics.items():
            if not isinstance(name, str) or not name:
                raise HauteValidationError("aggregate metric names must be non-empty strings")
            _finite_number(value, f"aggregate_metrics.{name}")
        _finite_number(self.objective, "objective")
        elapsed = _finite_number(self.elapsed_seconds, "elapsed_seconds")
        if elapsed < 0:
            raise HauteValidationError("elapsed_seconds must be non-negative")
        if not isinstance(self.fits, tuple) or any(
            not isinstance(fit, EvaluationFitResult) for fit in self.fits
        ):
            raise HauteValidationError("trial fits must be evaluation fit results")

    def to_plain_data(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "trial_index": self.trial_index,
            "label": self.label,
            "sampled_params": copy.deepcopy(dict(self.sampled_params)),
            "resolved_params": copy.deepcopy(dict(self.resolved_params)),
            "fits": [fit.to_plain_data() for fit in self.fits],
            "aggregate_metrics": dict(self.aggregate_metrics),
            "objective": self.objective,
            "elapsed_seconds": self.elapsed_seconds,
        }

    @classmethod
    def from_plain_data(cls, raw: Any) -> TuningTrialResult:
        if not isinstance(raw, Mapping):
            raise HauteValidationError("tuning trial must be an object")
        _exact_keys(
            raw,
            {
                "schema_version",
                "trial_index",
                "label",
                "sampled_params",
                "resolved_params",
                "fits",
                "aggregate_metrics",
                "objective",
                "elapsed_seconds",
            },
            "tuning trial",
        )
        if not isinstance(raw["fits"], list):
            raise HauteValidationError("tuning trial fits must be a list")
        return cls(
            schema_version=raw["schema_version"],
            trial_index=raw["trial_index"],
            label=raw["label"],
            sampled_params=raw["sampled_params"],
            resolved_params=raw["resolved_params"],
            fits=tuple(EvaluationFitResult.from_plain_data(fit) for fit in raw["fits"]),
            aggregate_metrics=raw["aggregate_metrics"],
            objective=raw["objective"],
            elapsed_seconds=raw["elapsed_seconds"],
        )


def choose_winner(
    trials: Sequence[TuningTrialResult],
    *,
    direction: MetricDirection,
) -> TuningTrialResult:
    """Choose by objective, with stable lower-index tie breaking."""
    if not trials:
        raise HauteValidationError("at least one tuning trial is required")
    if direction not in {"maximize", "minimize"}:
        raise HauteValidationError("direction must be maximize or minimize")
    ordered = sorted(trials, key=lambda item: item.trial_index)
    if ordered[0].trial_index != 0 or ordered[0].label != "baseline":
        raise HauteValidationError("ordered tuning trials must start with baseline trial zero")
    if [item.trial_index for item in ordered] != list(range(len(ordered))):
        raise HauteValidationError("tuning trial indices must be contiguous")
    if direction == "maximize":
        return max(ordered, key=lambda item: (item.objective, -item.trial_index))
    return min(ordered, key=lambda item: (item.objective, item.trial_index))


def validation_weighted_tree_count(
    *,
    best_iterations: Sequence[int],
    validation_rows: Sequence[int],
    iteration_ceiling: int,
) -> int:
    """Return weighted median(best_iteration + 1), capped by the fixed ceiling."""
    if (
        not best_iterations
        or len(best_iterations) != len(validation_rows)
        or isinstance(iteration_ceiling, bool)
        or not isinstance(iteration_ceiling, int)
        or iteration_ceiling <= 0
    ):
        raise HauteValidationError(
            "best iterations, validation rows, and a positive ceiling are required"
        )
    weighted: list[tuple[int, int]] = []
    for best_iteration, rows in zip(best_iterations, validation_rows, strict=True):
        if (
            isinstance(best_iteration, bool)
            or not isinstance(best_iteration, int)
            or best_iteration < 0
        ):
            raise HauteValidationError("best_iterations must be non-negative integers")
        if isinstance(rows, bool) or not isinstance(rows, int) or rows <= 0:
            raise HauteValidationError("validation_rows must be positive integers")
        weighted.append((best_iteration + 1, rows))
    weighted.sort(key=lambda item: item[0])
    threshold = sum(rows for _, rows in weighted) / 2
    cumulative = 0
    selected = weighted[-1][0]
    for trees, rows in weighted:
        cumulative += rows
        if cumulative >= threshold:
            selected = trees
            break
    return min(selected, iteration_ceiling)


@dataclass(frozen=True)
class TuningPlanArtifact:
    schema_version: int
    config: Mapping[str, Any]
    base_params_sha256: str
    evaluation_plan_sha256: str
    metric: str
    direction: MetricDirection
    sampler: str
    sampler_version: str
    seed: int
    validation_fit_count: int
    trial_count: int
    trial_fit_count: int
    total_fit_count: int

    def __post_init__(self) -> None:
        if _exact_int(self.schema_version, "tuning plan schema_version") != TUNING_SCHEMA_VERSION:
            raise HauteValidationError(
                f"tuning plan schema_version must be {TUNING_SCHEMA_VERSION}"
            )
        if not isinstance(self.config, Mapping):
            raise HauteValidationError("tuning plan config must be an object")
        _assert_finite_json(self.config, "tuning plan config")
        _exact_keys(
            self.config,
            {
                "schema_version",
                "trial_count",
                "seed",
                "metric",
                "search_space",
            },
            "tuning plan config",
        )
        _require_sha256(self.base_params_sha256, "base_params_sha256")
        _require_sha256(self.evaluation_plan_sha256, "evaluation_plan_sha256")
        if not isinstance(self.metric, str) or not self.metric:
            raise HauteValidationError("tuning plan metric must be a non-empty string")
        if self.direction != metric_direction(self.metric):
            raise HauteValidationError("tuning plan metric direction is inconsistent")
        if self.sampler != "TPESampler":
            raise HauteValidationError("tuning plan sampler must be TPESampler")
        if not isinstance(self.sampler_version, str) or not self.sampler_version.startswith("4."):
            raise HauteValidationError("tuning plan sampler_version must identify Optuna 4.x")
        _exact_int(self.seed, "tuning plan seed")
        validation_fit_count = _exact_int(
            self.validation_fit_count,
            "tuning plan validation_fit_count",
        )
        trial_count = _exact_int(self.trial_count, "tuning plan trial_count")
        trial_fit_count = _exact_int(
            self.trial_fit_count,
            "tuning plan trial_fit_count",
        )
        total_fit_count = _exact_int(
            self.total_fit_count,
            "tuning plan total_fit_count",
        )
        if not 1 <= validation_fit_count <= 10:
            raise HauteValidationError("tuning plan validation_fit_count is outside bounds")
        if not MIN_TRIAL_COUNT <= trial_count <= MAX_TRIAL_COUNT:
            raise HauteValidationError("tuning plan trial_count is outside bounds")
        if (
            trial_fit_count != trial_count * validation_fit_count
            or trial_fit_count > MAX_TRIAL_FITS
            or total_fit_count != trial_fit_count + 1
        ):
            raise HauteValidationError("tuning plan fit counts are inconsistent")
        config_schema_version = _exact_int(
            self.config["schema_version"],
            "tuning plan config schema_version",
        )
        config_trial_count = _exact_int(
            self.config["trial_count"],
            "tuning plan config trial_count",
        )
        config_seed = _exact_int(
            self.config["seed"],
            "tuning plan config seed",
        )
        config_metric = self.config["metric"]
        config_search_space = self.config["search_space"]
        if (
            config_schema_version != self.schema_version
            or config_trial_count != trial_count
            or config_seed != self.seed
            or not isinstance(config_metric, str)
            or config_metric != self.metric
            or not isinstance(config_search_space, Mapping)
            or not config_search_space
        ):
            raise HauteValidationError("tuning plan config disagrees with its derived fields")

    @classmethod
    def create(
        cls,
        *,
        config: TuningConfig,
        base_params: Mapping[str, Any],
        evaluation_plan_sha256: str,
        sampler: str,
        sampler_version: str,
    ) -> TuningPlanArtifact:
        _assert_finite_json(base_params, "base_params")
        return cls(
            schema_version=TUNING_SCHEMA_VERSION,
            config=config.to_plain_data(),
            base_params_sha256=hashlib.sha256(canonical_json_bytes(base_params)).hexdigest(),
            evaluation_plan_sha256=evaluation_plan_sha256,
            metric=config.metric,
            direction=config.direction,
            sampler=sampler,
            sampler_version=sampler_version,
            seed=config.seed,
            validation_fit_count=config.validation_fit_count,
            trial_count=config.trial_count,
            trial_fit_count=config.trial_fit_count,
            total_fit_count=config.total_fit_count,
        )

    def to_plain_data(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "config": copy.deepcopy(dict(self.config)),
            "base_params_sha256": self.base_params_sha256,
            "evaluation_plan_sha256": self.evaluation_plan_sha256,
            "metric": self.metric,
            "direction": self.direction,
            "sampler": self.sampler,
            "sampler_version": self.sampler_version,
            "seed": self.seed,
            "validation_fit_count": self.validation_fit_count,
            "trial_count": self.trial_count,
            "trial_fit_count": self.trial_fit_count,
            "total_fit_count": self.total_fit_count,
        }

    @classmethod
    def from_plain_data(cls, raw: Any) -> TuningPlanArtifact:
        if not isinstance(raw, Mapping):
            raise HauteValidationError("tuning plan must be an object")
        fields = {
            "schema_version",
            "config",
            "base_params_sha256",
            "evaluation_plan_sha256",
            "metric",
            "direction",
            "sampler",
            "sampler_version",
            "seed",
            "validation_fit_count",
            "trial_count",
            "trial_fit_count",
            "total_fit_count",
        }
        _exact_keys(raw, fields, "tuning plan")
        return cls(**{field: raw[field] for field in fields})


@dataclass(frozen=True)
class TuningTrialsArtifact:
    schema_version: int
    plan_sha256: str
    evaluation_plan_sha256: str
    trials: tuple[TuningTrialResult, ...]

    def __post_init__(self) -> None:
        if _exact_int(self.schema_version, "tuning trials schema_version") != TUNING_SCHEMA_VERSION:
            raise HauteValidationError(
                f"tuning trials schema_version must be {TUNING_SCHEMA_VERSION}"
            )
        _require_sha256(self.plan_sha256, "plan_sha256")
        _require_sha256(self.evaluation_plan_sha256, "evaluation_plan_sha256")
        if (
            not isinstance(self.trials, tuple)
            or not MIN_TRIAL_COUNT <= len(self.trials) <= MAX_TRIAL_COUNT
            or any(not isinstance(trial, TuningTrialResult) for trial in self.trials)
        ):
            raise HauteValidationError(
                f"tuning trials must contain {MIN_TRIAL_COUNT} through {MAX_TRIAL_COUNT} trials"
            )
        indices = [trial.trial_index for trial in self.trials]
        if indices != list(range(len(self.trials))):
            raise HauteValidationError("tuning trial indices must be contiguous")
        if self.trials[0].label != "baseline" or self.trials[0].sampled_params:
            raise HauteValidationError(
                "tuning trials must start with an unsampled baseline trial zero"
            )
        if any(trial.label != "sampled" or not trial.sampled_params for trial in self.trials[1:]):
            raise HauteValidationError("tuning trials after baseline must be sampled")
        metric_names = set(self.trials[0].aggregate_metrics)
        fit_count = len(self.trials[0].fits)
        if not 1 <= fit_count <= 10 or len(self.trials) * fit_count > MAX_TRIAL_FITS:
            raise HauteValidationError("tuning trials must contain a bounded validation fit set")
        for trial in self.trials:
            if set(trial.aggregate_metrics) != metric_names or len(trial.fits) != fit_count:
                raise HauteValidationError(
                    "tuning trials must retain identical metric and fit contracts"
                )
            if [fit.fit_index for fit in trial.fits] != list(range(fit_count)):
                raise HauteValidationError("tuning trial fit indices must be contiguous")
            if any(set(fit.metrics) != metric_names for fit in trial.fits):
                raise HauteValidationError(
                    "tuning trial fit metric names must match aggregate metrics"
                )
            total_validation_rows = sum(fit.validation_rows for fit in trial.fits)
            for metric, aggregate in trial.aggregate_metrics.items():
                weighted_mean = (
                    sum(fit.metrics[metric] * fit.validation_rows for fit in trial.fits)
                    / total_validation_rows
                )
                if not math.isclose(
                    aggregate,
                    weighted_mean,
                    rel_tol=1e-12,
                    abs_tol=1e-12,
                ):
                    raise HauteValidationError(
                        f"tuning trial aggregate metric {metric!r} "
                        "does not match its validation fits"
                    )

    def to_plain_data(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "plan_sha256": self.plan_sha256,
            "evaluation_plan_sha256": self.evaluation_plan_sha256,
            "trials": [trial.to_plain_data() for trial in self.trials],
        }

    @classmethod
    def from_plain_data(cls, raw: Any) -> TuningTrialsArtifact:
        if not isinstance(raw, Mapping):
            raise HauteValidationError("tuning trials must be an object")
        _exact_keys(
            raw,
            {
                "schema_version",
                "plan_sha256",
                "evaluation_plan_sha256",
                "trials",
            },
            "tuning trials",
        )
        if not isinstance(raw["trials"], list):
            raise HauteValidationError("tuning trials list is required")
        return cls(
            schema_version=raw["schema_version"],
            plan_sha256=raw["plan_sha256"],
            evaluation_plan_sha256=raw["evaluation_plan_sha256"],
            trials=tuple(TuningTrialResult.from_plain_data(trial) for trial in raw["trials"]),
        )


@dataclass(frozen=True)
class TuningReportArtifact:
    schema_version: int
    plan_sha256: str
    trials_sha256: str
    evaluation_plan_sha256: str
    metric: str
    direction: MetricDirection
    baseline_objective: float
    winner_trial_index: int
    winner_objective: float
    improvement: float
    best_sampled_params: Mapping[str, Any]
    final_params: Mapping[str, Any]
    final_tree_count: int
    trial_count: int
    trial_fit_count: int
    total_fit_count: int

    def __post_init__(self) -> None:
        if _exact_int(self.schema_version, "tuning report schema_version") != TUNING_SCHEMA_VERSION:
            raise HauteValidationError(
                f"tuning report schema_version must be {TUNING_SCHEMA_VERSION}"
            )
        _require_sha256(self.plan_sha256, "plan_sha256")
        _require_sha256(self.trials_sha256, "trials_sha256")
        _require_sha256(self.evaluation_plan_sha256, "evaluation_plan_sha256")
        if self.direction != metric_direction(self.metric):
            raise HauteValidationError("tuning report metric direction is inconsistent")
        baseline = _finite_number(
            self.baseline_objective,
            "tuning report baseline_objective",
        )
        winner = _finite_number(
            self.winner_objective,
            "tuning report winner_objective",
        )
        improvement = _finite_number(
            self.improvement,
            "tuning report improvement",
        )
        if improvement < 0:
            raise HauteValidationError("tuning report improvement must be non-negative")
        winner_index = _exact_int(
            self.winner_trial_index,
            "tuning report winner_trial_index",
        )
        if winner_index < 0:
            raise HauteValidationError("tuning report winner_trial_index must be non-negative")
        _assert_finite_json(self.best_sampled_params, "best_sampled_params")
        _assert_finite_json(self.final_params, "final_params")
        final_tree_count = _exact_int(
            self.final_tree_count,
            "tuning report final_tree_count",
        )
        trial_count = _exact_int(
            self.trial_count,
            "tuning report trial_count",
        )
        trial_fit_count = _exact_int(
            self.trial_fit_count,
            "tuning report trial_fit_count",
        )
        total_fit_count = _exact_int(
            self.total_fit_count,
            "tuning report total_fit_count",
        )
        if (
            final_tree_count <= 0
            or not MIN_TRIAL_COUNT <= trial_count <= MAX_TRIAL_COUNT
            or not 0 <= winner_index < trial_count
            or trial_fit_count <= 0
            or trial_fit_count > MAX_TRIAL_FITS
            or total_fit_count != trial_fit_count + 1
        ):
            raise HauteValidationError("tuning report counts are inconsistent")
        if self.final_params.get("iterations") != final_tree_count or any(
            key in self.final_params
            for key in (
                "early_stopping_rounds",
                "od_pval",
                "od_type",
                "od_wait",
                "use_best_model",
            )
        ):
            raise HauteValidationError("tuning report final parameter projection is inconsistent")
        if bool(winner_index) != bool(self.best_sampled_params):
            raise HauteValidationError(
                "tuning report best sampled parameters disagree with the winner"
            )
        expected_improvement = (
            winner - baseline if self.direction == "maximize" else baseline - winner
        )
        if not math.isclose(
            improvement,
            expected_improvement,
            rel_tol=0.0,
            abs_tol=1e-15,
        ):
            raise HauteValidationError("tuning report improvement is inconsistent")

    def to_plain_data(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "plan_sha256": self.plan_sha256,
            "trials_sha256": self.trials_sha256,
            "evaluation_plan_sha256": self.evaluation_plan_sha256,
            "metric": self.metric,
            "direction": self.direction,
            "baseline_objective": self.baseline_objective,
            "winner_trial_index": self.winner_trial_index,
            "winner_objective": self.winner_objective,
            "improvement": self.improvement,
            "best_sampled_params": copy.deepcopy(dict(self.best_sampled_params)),
            "final_params": copy.deepcopy(dict(self.final_params)),
            "final_tree_count": self.final_tree_count,
            "trial_count": self.trial_count,
            "trial_fit_count": self.trial_fit_count,
            "total_fit_count": self.total_fit_count,
        }

    @classmethod
    def from_plain_data(cls, raw: Any) -> TuningReportArtifact:
        if not isinstance(raw, Mapping):
            raise HauteValidationError("tuning report must be an object")
        fields = {
            "schema_version",
            "plan_sha256",
            "trials_sha256",
            "evaluation_plan_sha256",
            "metric",
            "direction",
            "baseline_objective",
            "winner_trial_index",
            "winner_objective",
            "improvement",
            "best_sampled_params",
            "final_params",
            "final_tree_count",
            "trial_count",
            "trial_fit_count",
            "total_fit_count",
        }
        _exact_keys(raw, fields, "tuning report")
        return cls(**{field: raw[field] for field in fields})


def _require_sha256(value: Any, name: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise HauteValidationError(f"{name} must be a lowercase SHA-256 digest")
    return value


def save_tuning_plan(plan: TuningPlanArtifact, path: str | Path) -> None:
    save_canonical_json(plan.to_plain_data(), path)


def load_tuning_plan(path: str | Path) -> TuningPlanArtifact:
    return TuningPlanArtifact.from_plain_data(json.loads(Path(path).read_bytes()))


def save_tuning_trials(
    trials: TuningTrialsArtifact,
    path: str | Path,
) -> None:
    save_canonical_json(trials.to_plain_data(), path)


def load_tuning_trials(
    path: str | Path,
    *,
    plan_sha256: str | None = None,
) -> TuningTrialsArtifact:
    trials = TuningTrialsArtifact.from_plain_data(json.loads(Path(path).read_bytes()))
    if plan_sha256 is not None and trials.plan_sha256 != _require_sha256(
        plan_sha256,
        "plan_sha256",
    ):
        raise HauteValidationError("tuning trials plan digest does not match")
    return trials


def save_tuning_report(
    report: TuningReportArtifact,
    path: str | Path,
) -> None:
    save_canonical_json(report.to_plain_data(), path)


def load_tuning_report(path: str | Path) -> TuningReportArtifact:
    return TuningReportArtifact.from_plain_data(json.loads(Path(path).read_bytes()))


def build_tuning_report(
    plan: TuningPlanArtifact,
    trials: TuningTrialsArtifact,
    *,
    trials_sha256: str,
    final_params: Mapping[str, Any],
    final_tree_count: int,
) -> TuningReportArtifact:
    plan_sha256 = hashlib.sha256(canonical_json_bytes(plan.to_plain_data())).hexdigest()
    if (
        trials.plan_sha256 != plan_sha256
        or trials.evaluation_plan_sha256 != plan.evaluation_plan_sha256
        or len(trials.trials) != plan.trial_count
    ):
        raise HauteValidationError("tuning trials do not match the tuning plan")
    baseline = trials.trials[0]
    baseline_digest = hashlib.sha256(canonical_json_bytes(baseline.resolved_params)).hexdigest()
    if baseline_digest != plan.base_params_sha256:
        raise HauteValidationError("tuning baseline parameters do not match the tuning plan")
    for trial in trials.trials:
        expected_resolved = resolve_trial_parameters(
            baseline.resolved_params,
            trial.sampled_params,
        )
        if (
            len(trial.fits) != plan.validation_fit_count
            or plan.metric not in trial.aggregate_metrics
            or canonical_json_bytes(expected_resolved)
            != canonical_json_bytes(trial.resolved_params)
            or not math.isclose(
                trial.objective,
                trial.aggregate_metrics[plan.metric],
                rel_tol=0.0,
                abs_tol=0.0,
            )
        ):
            raise HauteValidationError("tuning trial does not match plan fit/metric contract")
    winner = choose_winner(trials.trials, direction=plan.direction)
    iteration_ceiling = winner.resolved_params.get("iterations", 1000)
    if (
        isinstance(iteration_ceiling, bool)
        or not isinstance(iteration_ceiling, int)
        or iteration_ceiling <= 0
        or any(fit.best_iteration is None for fit in winner.fits)
    ):
        raise HauteValidationError(
            "winning tuning trial must retain a positive iteration ceiling "
            "and best_iteration for every validation fit"
        )
    expected_tree_count = validation_weighted_tree_count(
        best_iterations=[
            fit.best_iteration for fit in winner.fits if fit.best_iteration is not None
        ],
        validation_rows=[fit.validation_rows for fit in winner.fits],
        iteration_ceiling=iteration_ceiling,
    )
    expected_final_params = copy.deepcopy(dict(winner.resolved_params))
    for key in (
        "early_stopping_rounds",
        "od_pval",
        "od_type",
        "od_wait",
        "use_best_model",
    ):
        expected_final_params.pop(key, None)
    expected_final_params["iterations"] = expected_tree_count
    if _exact_int(
        final_tree_count, "final_tree_count"
    ) != expected_tree_count or canonical_json_bytes(final_params) != canonical_json_bytes(
        expected_final_params
    ):
        raise HauteValidationError(
            "tuning report final parameter projection must be derived from "
            "the winning validation fits"
        )
    improvement = (
        winner.objective - baseline.objective
        if plan.direction == "maximize"
        else baseline.objective - winner.objective
    )
    return TuningReportArtifact(
        schema_version=TUNING_SCHEMA_VERSION,
        plan_sha256=plan_sha256,
        trials_sha256=_require_sha256(trials_sha256, "trials_sha256"),
        evaluation_plan_sha256=plan.evaluation_plan_sha256,
        metric=plan.metric,
        direction=plan.direction,
        baseline_objective=baseline.objective,
        winner_trial_index=winner.trial_index,
        winner_objective=winner.objective,
        improvement=improvement,
        best_sampled_params=winner.sampled_params,
        final_params=final_params,
        final_tree_count=final_tree_count,
        trial_count=plan.trial_count,
        trial_fit_count=plan.trial_fit_count,
        total_fit_count=plan.total_fit_count,
    )
