"""Canonical, deterministic evaluation planning artifacts."""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

import numpy as np

EVALUATION_SCHEMA_VERSION = 1
MAX_VALIDATION_FITS = 10


def _exact_mapping(value: object, keys: set[str], name: str) -> Mapping[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError(f"{name} must contain exactly {sorted(keys)}")
    return value


def _integer(value: object, name: str, *, low: int | None = None, high: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if (low is not None and value < low) or (high is not None and value > high):
        raise ValueError(f"{name} is outside its permitted range")
    return value


def _fraction(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise TypeError(f"{name} must be a finite number")
    value = float(value)
    if not 0 <= value < 1:
        raise ValueError(f"{name} must be in [0, 1)")
    return value


def _boundary(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be an ISO date or datetime")
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{name} must be an ISO date or datetime") from error
    return value


def _sha256(value: object, name: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(c not in "0123456789abcdef" for c in value)
    ):
        raise ValueError(f"invalid {name}")
    return value


def _json_key(value: object) -> str:
    """Canonicalise a finite JSON value so unhashable group values remain valid keys."""
    if isinstance(value, datetime):
        return f"datetime:{value.isoformat()}"
    if isinstance(value, date):
        return f"date:{value.isoformat()}"
    if isinstance(value, time):
        return f"time:{value.isoformat()}"
    return canonical_json_bytes(value).decode("utf-8")


def _parsed_dates(values: Sequence[object]) -> list[datetime]:
    if not values:
        return []
    parsed: list[datetime] = []
    aware: bool | None = None
    for value in values:
        if isinstance(value, datetime):
            parsed_value = value
        elif isinstance(value, date):
            parsed_value = datetime.combine(value, time.min)
        elif isinstance(value, str):
            parsed_value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        else:
            raise TypeError("date values must be ISO strings or native date/datetime values")
        this_aware = parsed_value.tzinfo is not None
        if aware is None:
            aware = this_aware
        elif aware != this_aware:
            raise ValueError("date values must not mix timezone awareness")
        parsed.append(parsed_value)
    return parsed


@dataclass(frozen=True)
class EvaluationConfig:
    schema_version: int
    strategy: str
    seed: int | None
    validation: Mapping[str, Any]
    test: Mapping[str, Any] | None = None
    group_column: str | None = None
    date_column: str | None = None

    def __post_init__(self) -> None:
        if self.schema_version != EVALUATION_SCHEMA_VERSION or self.strategy not in {
            "random",
            "group",
            "temporal",
        }:
            raise ValueError("invalid evaluation configuration")
        if self.strategy == "temporal":
            if (
                self.seed is not None
                or not isinstance(self.date_column, str)
                or not self.date_column
            ):
                raise ValueError("temporal evaluation requires date_column and no seed")
        else:
            _integer(self.seed, "seed")
        if self.strategy == "group" and (
            not isinstance(self.group_column, str) or not self.group_column
        ):
            raise ValueError("group evaluation requires group_column")
        if not isinstance(self.validation, Mapping) or self.validation.get("method") not in {
            "none",
            "single",
            "cross_validation",
        }:
            raise ValueError("invalid validation configuration")
        method = self.validation["method"]
        expected_validation = (
            {"method"}
            if method == "none"
            else (
                {"method", "start"}
                if self.strategy == "temporal" and method == "single"
                else (
                    {"method", "size"}
                    if method == "single"
                    else (
                        {"method", "fold_count", "window"}
                        if self.strategy == "temporal"
                        else {"method", "fold_count"}
                    )
                )
            )
        )
        if set(self.validation) != expected_validation:
            raise ValueError("invalid validation configuration")
        if method == "single":
            if self.strategy == "temporal":
                _boundary(self.validation["start"], "validation.start")
            else:
                _fraction(self.validation["size"], "validation.size")
        elif method == "cross_validation":
            _integer(self.validation["fold_count"], "fold_count", low=2, high=MAX_VALIDATION_FITS)
            if self.strategy == "temporal" and self.validation["window"] != "expanding":
                raise ValueError("invalid temporal validation window")
        if self.test is not None:
            if not isinstance(self.test, Mapping) or set(self.test) != (
                {"start"} if self.strategy == "temporal" else {"size"}
            ):
                raise ValueError("invalid test configuration")
            if self.strategy == "temporal":
                _boundary(self.test["start"], "test.start")
            else:
                _fraction(self.test["size"], "test.size")
        if self.strategy == "temporal" and method == "single" and self.test is not None:
            validation_start, test_start = _parsed_dates(
                [self.validation["start"], self.test["start"]]
            )
            if validation_start >= test_start:
                raise ValueError("validation.start must precede test.start")

    @classmethod
    def from_plain_data(cls, raw: object) -> EvaluationConfig:
        if not isinstance(raw, dict):
            raise TypeError("evaluation must be an object")
        strategy = raw.get("strategy")
        if strategy not in {"random", "group", "temporal"}:
            raise ValueError("evaluation strategy must be random, group, or temporal")
        required = {"schema_version", "strategy", "validation"}
        if strategy != "temporal":
            required.add("seed")
        if strategy == "group":
            required.add("group_column")
        if strategy == "temporal":
            required.add("date_column")
        allowed = required | {"test"}
        if set(raw) - allowed or not required <= set(raw):
            raise ValueError("evaluation has unknown or missing fields")
        if _integer(raw["schema_version"], "schema_version") != EVALUATION_SCHEMA_VERSION:
            raise ValueError("unsupported evaluation schema_version")
        seed = None if strategy == "temporal" else _integer(raw["seed"], "seed")
        group_column = raw.get("group_column")
        date_column = raw.get("date_column")
        if group_column is not None and (not isinstance(group_column, str) or not group_column):
            raise ValueError("group_column must be a non-empty string")
        if date_column is not None and (not isinstance(date_column, str) or not date_column):
            raise ValueError("date_column must be a non-empty string")
        test = raw.get("test")
        if test is not None:
            if strategy == "temporal":
                test = _exact_mapping(test, {"start"}, "test")
                test = {"start": _boundary(test["start"], "test.start")}
            else:
                test = _exact_mapping(test, {"size"}, "test")
                test = {"size": _fraction(test["size"], "test.size")}
        validation_raw = raw["validation"]
        if not isinstance(validation_raw, dict) or not isinstance(
            validation_raw.get("method"), str
        ):
            raise ValueError("validation must be an object with method")
        method = validation_raw["method"]
        if method == "none":
            validation = _exact_mapping(validation_raw, {"method"}, "validation")
        elif method == "single":
            key = "start" if strategy == "temporal" else "size"
            validation = _exact_mapping(validation_raw, {"method", key}, "validation")
            value = (
                _boundary(validation[key], "validation.start")
                if key == "start"
                else _fraction(validation[key], "validation.size")
            )
            validation = {"method": method, key: value}
        elif method == "cross_validation":
            keys = {"method", "fold_count"} | ({"window"} if strategy == "temporal" else set())
            validation = _exact_mapping(validation_raw, keys, "validation")
            validation = {
                "method": method,
                "fold_count": _integer(
                    validation["fold_count"], "fold_count", low=2, high=MAX_VALIDATION_FITS
                ),
            }
            if strategy == "temporal":
                if validation_raw["window"] != "expanding":
                    raise ValueError("temporal cross_validation window must be expanding")
                validation["window"] = "expanding"
        else:
            raise ValueError("validation method must be none, single, or cross_validation")
        if strategy == "temporal" and method == "single" and test is not None:
            validation_boundary, test_boundary = _parsed_dates([validation["start"], test["start"]])
            if validation_boundary >= test_boundary:
                raise ValueError("validation.start must precede test.start")
        return cls(1, strategy, seed, validation, test, group_column, date_column)

    @property
    def validation_fit_count(self) -> int:
        return (
            0
            if self.validation["method"] == "none"
            else (1 if self.validation["method"] == "single" else self.validation["fold_count"])
        )

    @property
    def ordinary_total_fit_count(self) -> int:
        return self.validation_fit_count + 1

    def to_plain_data(self) -> dict[str, Any]:
        result: dict[str, Any] = {"schema_version": 1, "strategy": self.strategy}
        if self.strategy == "group":
            result["group_column"] = self.group_column
        if self.strategy == "temporal":
            result["date_column"] = self.date_column
        else:
            result["seed"] = self.seed
        if self.test is not None:
            result["test"] = dict(self.test)
        result["validation"] = dict(self.validation)
        return result


@dataclass(frozen=True)
class EvaluationValidationFit:
    train_positions: tuple[int, ...]
    validation_positions: tuple[int, ...]

    @property
    def train_rows(self) -> int:
        return len(self.train_positions)

    @property
    def validation_rows(self) -> int:
        return len(self.validation_positions)

    def to_plain_data(self) -> dict[str, Any]:
        return {
            "train_positions": [int(x) for x in self.train_positions],
            "validation_positions": [int(x) for x in self.validation_positions],
        }


@dataclass(frozen=True)
class EvaluationPlan:
    schema_version: int
    source_sha256: str
    row_count: int
    config: EvaluationConfig
    development_positions: tuple[int, ...]
    test_positions: tuple[int, ...]
    validation_fits: tuple[EvaluationValidationFit, ...]
    summary: Mapping[str, Any]

    @property
    def fit_count(self) -> int:
        return len(self.validation_fits) + 1

    def final_mask(self) -> np.ndarray:
        mask = np.full(self.row_count, 3, dtype=np.int8)
        mask[list(self.development_positions)] = 0
        mask[list(self.test_positions)] = 2
        return mask

    def selection_mask(self, fit_index: int) -> np.ndarray:
        fit_index = _integer(fit_index, "fit_index", low=0, high=len(self.validation_fits) - 1)
        fit = self.validation_fits[fit_index]
        mask = np.full(self.row_count, 3, dtype=np.int8)
        mask[list(fit.train_positions)] = 0
        mask[list(fit.validation_positions)] = 1
        return mask

    def to_plain_data(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source_sha256": self.source_sha256,
            "row_count": self.row_count,
            "config": self.config.to_plain_data(),
            "development_positions": [int(x) for x in self.development_positions],
            "test_positions": [int(x) for x in self.test_positions],
            "validation_fits": [x.to_plain_data() for x in self.validation_fits],
            "summary": dict(self.summary),
        }

    @classmethod
    def from_plain_data(cls, raw: object) -> EvaluationPlan:
        required = {
            "schema_version",
            "source_sha256",
            "row_count",
            "config",
            "development_positions",
            "test_positions",
            "validation_fits",
            "summary",
        }
        data = _exact_mapping(raw, required, "evaluation plan")
        if _integer(data["schema_version"], "schema_version") != 1:
            raise ValueError("unsupported plan schema_version")
        source = _sha256(data["source_sha256"], "source_sha256")
        count = _integer(data["row_count"], "row_count", low=1)
        config = EvaluationConfig.from_plain_data(data["config"])

        def positions(value: object, name: str) -> tuple[int, ...]:
            if not isinstance(value, list):
                raise TypeError(f"{name} must be a list")
            values = tuple(_integer(x, name, low=0, high=count - 1) for x in value)
            if len(set(values)) != len(values):
                raise ValueError(f"duplicate {name}")
            if values != tuple(sorted(values)):
                raise ValueError(f"{name} must be in canonical ascending order")
            return values

        dev, test = (
            positions(data["development_positions"], "development_positions"),
            positions(data["test_positions"], "test_positions"),
        )
        if not dev or set(dev) & set(test) or set(dev) | set(test) != set(range(count)):
            raise ValueError("development/test positions overlap or are empty")
        if bool(test) != (config.test is not None):
            raise ValueError("test membership disagrees with evaluation config")
        if (
            not isinstance(data["validation_fits"], list)
            or len(data["validation_fits"]) != config.validation_fit_count
        ):
            raise ValueError("validation fit count disagrees")
        fits = []
        for item in data["validation_fits"]:
            item = _exact_mapping(
                item, {"train_positions", "validation_positions"}, "validation fit"
            )
            fit = EvaluationValidationFit(
                positions(item["train_positions"], "train_positions"),
                positions(item["validation_positions"], "validation_positions"),
            )
            if (
                not fit.train_positions
                or not fit.validation_positions
                or set(fit.train_positions) & set(fit.validation_positions)
                or not (set(fit.train_positions) | set(fit.validation_positions)).issubset(dev)
            ):
                raise ValueError("invalid validation fit membership")
            fits.append(fit)
        if config.validation["method"] == "cross_validation":
            validation_sets = [set(fit.validation_positions) for fit in fits]
            if any(
                left & right
                for index, left in enumerate(validation_sets)
                for right in validation_sets[index + 1 :]
            ):
                raise ValueError("cross-validation validation memberships overlap")
            if config.strategy == "temporal":
                for previous, current in zip(fits, fits[1:], strict=False):
                    if set(current.train_positions) != (
                        set(previous.train_positions) | set(previous.validation_positions)
                    ):
                        raise ValueError(
                            "temporal cross-validation training membership is not expanding"
                        )
                if set(fits[-1].train_positions) | set(fits[-1].validation_positions) != set(dev):
                    raise ValueError(
                        "temporal cross-validation does not end with all development rows"
                    )
            elif set().union(*validation_sets) != set(dev):
                raise ValueError(
                    "cross-validation validation memberships do not partition development"
                )
            if config.strategy != "temporal" and any(
                set(fit.train_positions) | set(fit.validation_positions) != set(dev) for fit in fits
            ):
                raise ValueError("cross-validation fit does not cover development")
        elif fits and (
            set(fits[0].train_positions) | set(fits[0].validation_positions) != set(dev)
        ):
            raise ValueError("single validation fit does not cover development")
        if not isinstance(data["summary"], dict):
            raise TypeError("summary must be an object")
        summary = data["summary"]
        expected_summary = {
            "development_rows": len(dev),
            "test_rows": len(test),
            "validation_fit_count": len(fits),
        }
        if any(isinstance(v, bool) or not isinstance(v, int) or v < 0 for v in summary.values()):
            raise ValueError("summary values must be non-negative integers")
        if any(summary.get(key) != value for key, value in expected_summary.items()):
            raise ValueError("summary disagrees with plan membership")
        required_summary = set(expected_summary)
        if config.strategy == "group":
            required_summary |= {"development_group_count", "test_group_count"}
        if config.strategy == "temporal":
            required_summary |= {"development_date_count", "test_date_count"}
        if set(summary) != required_summary:
            raise ValueError("summary fields do not match the evaluation strategy")
        if config.strategy == "group":
            if summary["development_group_count"] < 1:
                raise ValueError("summary must contain a development group")
            if bool(test) != bool(summary["test_group_count"]):
                raise ValueError("summary test group count disagrees with membership")
            if summary["development_group_count"] + summary["test_group_count"] > count:
                raise ValueError("summary group counts exceed source rows")
        if config.strategy == "temporal":
            if summary["development_date_count"] < 1:
                raise ValueError("summary must contain a development date")
            if bool(test) != bool(summary["test_date_count"]):
                raise ValueError("summary test date count disagrees with membership")
            if summary["development_date_count"] + summary["test_date_count"] > count:
                raise ValueError("summary date counts exceed source rows")
        return cls(1, source, count, config, dev, test, tuple(fits), summary)


def _count(size: float, total: int) -> int:
    value = int(round(size * total))
    if value < 1 or value >= total:
        raise ValueError("requested partition must leave non-empty partitions")
    return value


def _stratified_parts(
    positions: Sequence[int], values: Sequence[object], parts: int, seed: int
) -> list[list[int]]:
    classes: dict[object, list[int]] = {}
    for p in positions:
        classes.setdefault(values[p], []).append(p)
    counts = {str(k): len(v) for k, v in classes.items()}
    if min(map(len, classes.values())) < parts:
        raise ValueError(f"class counts {counts}; required minimum {parts}")
    rng = np.random.default_rng(seed)
    output: list[list[int]] = [[] for _ in range(parts)]
    for rows in classes.values():
        rows = list(rows)
        rng.shuffle(rows)
        for index, row in enumerate(rows):
            output[index % parts].append(row)
    return output


def _stratified_sample(
    positions: Sequence[int],
    values: Sequence[object],
    size: int,
    seed: int,
    required_remaining: int,
) -> list[int]:
    classes: dict[object, list[int]] = {}
    for position in positions:
        classes.setdefault(values[position], []).append(position)
    counts = {str(key): len(rows) for key, rows in classes.items()}
    required = required_remaining + 1
    if size < len(classes) or any(len(rows) < required for rows in classes.values()):
        raise ValueError(f"class counts {counts}; required minimum {required}")
    raw = {key: size * len(rows) / len(positions) for key, rows in classes.items()}
    allocations = {key: max(1, int(math.floor(value))) for key, value in raw.items()}
    while sum(allocations.values()) < size:
        eligible = [
            key
            for key, rows in classes.items()
            if allocations[key] < len(rows) - required_remaining
        ]
        if not eligible:
            raise ValueError(f"class counts {counts}; required minimum {required}")
        key = max(eligible, key=lambda item: (raw[item] - allocations[item], str(item)))
        allocations[key] += 1
    rng = np.random.default_rng(seed)
    selected: list[int] = []
    for key in sorted(classes, key=str):
        rows = list(classes[key])
        rng.shuffle(rows)
        selected.extend(rows[: allocations[key]])
    return selected


def _balanced_groups(groups: Mapping[str, list[int]], buckets: int, seed: int) -> list[list[int]]:
    rng = np.random.default_rng(seed)
    keys = list(groups)
    rng.shuffle(keys)
    rank = {x: i for i, x in enumerate(keys)}
    keys.sort(key=lambda x: (-len(groups[x]), rank[x]))
    result: list[list[int]] = [[] for _ in range(buckets)]
    sizes = [0] * buckets
    for key in keys:
        index = min(range(buckets), key=lambda i: (sizes[i], i))
        result[index].extend(groups[key])
        sizes[index] += len(groups[key])
    if any(not item for item in result):
        raise ValueError("requested group partition would be empty")
    return result


def _groups_near_target(groups: Mapping[str, list[int]], target: int, seed: int) -> list[int]:
    """Seeded greedy assignment that minimises distance from one requested row target."""
    rng = np.random.default_rng(seed)
    keys = list(groups)
    rng.shuffle(keys)
    rank = {key: index for index, key in enumerate(keys)}
    keys.sort(key=lambda key: (-len(groups[key]), rank[key]))
    # Begin with the closest viable whole group. Starting from the largest
    # group can strand a much closer smaller group when the requested
    # fraction is small.
    first = min(
        keys,
        key=lambda key: (abs(len(groups[key]) - target), rank[key]),
    )
    selected_keys = {first}
    selected_rows = len(groups[first])
    for key in keys:
        if key in selected_keys:
            continue
        candidate = selected_rows + len(groups[key])
        if abs(candidate - target) < abs(selected_rows - target):
            selected_keys.add(key)
            selected_rows = candidate
    selected = [position for key in keys if key in selected_keys for position in groups[key]]
    if not selected or len(selected) == sum(map(len, groups.values())):
        raise ValueError("requested group partition would be empty")
    return selected


def generate_evaluation_plan(
    config: EvaluationConfig,
    *,
    source_sha256: str,
    row_count: int,
    task: str,
    target_values: Sequence[object] | None = None,
    group_values: Sequence[object] | None = None,
    date_values: Sequence[object] | None = None,
) -> EvaluationPlan:
    if row_count < 1:
        raise ValueError("invalid source or row count")
    _sha256(source_sha256, "source_sha256")
    all_positions = tuple(range(row_count))
    method = config.validation["method"]
    summary: dict[str, Any] = {}
    if task not in {"regression", "classification"}:
        raise ValueError("evaluation task must be regression or classification")
    if config.strategy == "temporal":
        if date_values is None or len(date_values) != row_count:
            raise ValueError("temporal evaluation requires date values")
        boundary_values = list(date_values)
        if config.test:
            boundary_values.append(config.test["start"])
        if method == "single":
            boundary_values.append(config.validation["start"])
        parsed = _parsed_dates(boundary_values)
        dates = parsed[:row_count]
        cursor = row_count
        test_start = parsed[cursor] if config.test else None
        cursor += bool(config.test)
        validation_start = parsed[cursor] if method == "single" else None
        test = tuple(p for p in all_positions if test_start is not None and dates[p] >= test_start)
        test_membership = set(test)
        dev = tuple(p for p in all_positions if p not in test_membership)
        if config.test and (not test or not dev):
            raise ValueError("temporal test partition is empty")
        ordered = sorted(set(dates[p] for p in dev))
        summary.update(
            {
                "development_date_count": len(ordered),
                "test_date_count": len(set(dates[p] for p in test)),
            }
        )
        if method == "none":
            fits = []
        elif method == "single":
            assert validation_start is not None
            boundary = validation_start
            train = tuple(p for p in dev if dates[p] < boundary)
            valid = tuple(p for p in dev if dates[p] >= boundary)
            if not train or not valid:
                raise ValueError("temporal validation partition is empty")
            fits = [EvaluationValidationFit(train, valid)]
        else:
            k = config.validation["fold_count"]
            if len(ordered) < k + 1:
                raise ValueError("temporal cross_validation needs enough distinct dates")
            blocks = np.array_split(np.asarray(ordered, dtype=object), k + 1)
            fits = []
            for i in range(k):
                train_dates = set(x for block in blocks[: i + 1] for x in block.tolist())
                valid_dates = set(blocks[i + 1].tolist())
                fits.append(
                    EvaluationValidationFit(
                        tuple(p for p in dev if dates[p] in train_dates),
                        tuple(p for p in dev if dates[p] in valid_dates),
                    )
                )
    else:
        if config.strategy == "group":
            if group_values is None or len(group_values) != row_count:
                raise ValueError("group evaluation requires group values")
            groups: dict[str, list[int]] = {}
            for p, value in enumerate(group_values):
                groups.setdefault(_json_key(value), []).append(p)
            if config.test:
                target = _count(config.test["size"], row_count)
                test = tuple(_groups_near_target(groups, target, config.seed or 0))
                test_membership = set(test)
                dev = tuple(p for p in all_positions if p not in test_membership)
            else:
                dev, test = all_positions, ()
            dev_membership = set(dev)
            dev_groups = {
                key: [p for p in rows if p in dev_membership]
                for key, rows in groups.items()
                if any(p in dev_membership for p in rows)
            }
            summary.update(
                {
                    "development_group_count": len(dev_groups),
                    "test_group_count": len(groups) - len(dev_groups),
                }
            )
            if method == "cross_validation":
                parts = _balanced_groups(
                    dev_groups, config.validation["fold_count"], config.seed or 0
                )
            elif method == "single":
                target = _count(config.validation["size"], row_count)
                parts = [_groups_near_target(dev_groups, target, (config.seed or 0) + 1)]
            else:
                parts = []
        else:
            rng = np.random.default_rng(config.seed)
            positions = list(all_positions)
            rng.shuffle(positions)
            if task == "classification":
                if target_values is None or len(target_values) != row_count:
                    raise ValueError("classification evaluation requires target values")
                classes: dict[str, int] = {}
                for value in target_values:
                    key = _json_key(value)
                    classes[key] = classes.get(key, 0) + 1
                required_minimum = (1 if config.test else 0) + (
                    config.validation["fold_count"]
                    if method == "cross_validation"
                    else (2 if method == "single" else 1)
                )
                if min(classes.values()) < required_minimum:
                    raise ValueError(f"class counts {classes}; required minimum {required_minimum}")
            if config.test:
                n = _count(config.test["size"], row_count)
                if task == "classification":
                    assert target_values is not None
                    remaining = (
                        config.validation["fold_count"]
                        if method == "cross_validation"
                        else (1 if method == "single" else 0)
                    )
                    test = tuple(
                        _stratified_sample(
                            all_positions, target_values, n, config.seed or 0, remaining
                        )
                    )
                else:
                    test = tuple(positions[:n])
                test_membership = set(test)
                dev = tuple(p for p in all_positions if p not in test_membership)
            else:
                dev, test = all_positions, ()
            development_membership = set(dev)
            development_order = [
                position for position in positions if position in development_membership
            ]
            if method == "cross_validation":
                if task == "classification":
                    assert target_values is not None
                    parts = _stratified_parts(
                        dev,
                        target_values,
                        config.validation["fold_count"],
                        config.seed or 0,
                    )
                else:
                    parts = [
                        list(x)
                        for x in np.array_split(
                            np.asarray(development_order),
                            config.validation["fold_count"],
                        )
                    ]
            elif method == "single":
                n = _count(config.validation["size"], row_count)
                if task == "classification":
                    assert target_values is not None
                    parts = [_stratified_sample(dev, target_values, n, (config.seed or 0) + 1, 1)]
                else:
                    parts = [development_order[:n]]
            else:
                parts = []
        fits = []
        for part in parts:
            part_membership = set(part)
            fits.append(
                EvaluationValidationFit(
                    tuple(p for p in dev if p not in part_membership),
                    tuple(sorted(part)),
                )
            )
        if any(not x.train_positions or not x.validation_positions for x in fits):
            raise ValueError("requested validation partition is empty")
    summary.update(
        {"development_rows": len(dev), "test_rows": len(test), "validation_fit_count": len(fits)}
    )
    return EvaluationPlan(
        1,
        source_sha256,
        row_count,
        config,
        tuple(sorted(dev)),
        tuple(sorted(test)),
        tuple(fits),
        summary,
    )


def canonical_json_bytes(value: object) -> bytes:
    def reject(item: object) -> None:
        if isinstance(item, float) and not math.isfinite(item):
            raise ValueError("canonical JSON cannot contain non-finite numbers")
        if isinstance(item, Mapping):
            for k, v in item.items():
                if not isinstance(k, str):
                    raise TypeError("JSON object keys must be strings")
                reject(v)
        elif isinstance(item, (list, tuple)):
            for v in item:
                reject(v)

    reject(value)
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def file_sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save_canonical_json(value: object, path: str | Path) -> None:
    """Atomically replace *path* with canonical finite JSON."""
    target = Path(path)
    payload = canonical_json_bytes(value)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def save_evaluation_plan(plan: EvaluationPlan, path: str | Path) -> None:
    save_canonical_json(plan.to_plain_data(), path)


def load_evaluation_plan(path: str | Path, *, source_sha256: str) -> EvaluationPlan:
    plan = EvaluationPlan.from_plain_data(json.loads(Path(path).read_bytes()))
    if plan.source_sha256 != source_sha256:
        raise ValueError("evaluation plan source digest does not match")
    return plan


def save_evaluation_results(results: EvaluationResultsArtifact, path: str | Path) -> None:
    save_canonical_json(results.to_plain_data(), path)


def load_evaluation_results(path: str | Path, *, plan_sha256: str) -> EvaluationResultsArtifact:
    results = EvaluationResultsArtifact.from_plain_data(json.loads(Path(path).read_bytes()))
    if results.plan_sha256 != _sha256(plan_sha256, "plan_sha256"):
        raise ValueError("evaluation results plan digest does not match")
    return results


def save_evaluation_report(report: EvaluationAggregateReport, path: str | Path) -> None:
    save_canonical_json(report.to_plain_data(), path)


def load_evaluation_report(path: str | Path) -> EvaluationAggregateReport:
    return EvaluationAggregateReport.from_plain_data(json.loads(Path(path).read_bytes()))


@dataclass(frozen=True)
class EvaluationFitResult:
    schema_version: int
    fit_index: int
    train_rows: int
    validation_rows: int
    metrics: Mapping[str, float]
    best_iteration: int | None

    def __post_init__(self) -> None:
        _integer(self.schema_version, "schema_version", low=1, high=1)
        _integer(self.fit_index, "fit_index", low=0)
        _integer(self.train_rows, "train_rows", low=1)
        _integer(self.validation_rows, "validation_rows", low=1)
        if self.best_iteration is not None:
            _integer(self.best_iteration, "best_iteration", low=0)
        _metric_mapping(self.metrics, "metrics")

    def to_plain_data(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "fit_index": self.fit_index,
            "train_rows": self.train_rows,
            "validation_rows": self.validation_rows,
            "metrics": dict(self.metrics),
            "best_iteration": self.best_iteration,
        }

    @classmethod
    def from_plain_data(cls, raw: object) -> EvaluationFitResult:
        data = _exact_mapping(
            raw,
            {
                "schema_version",
                "fit_index",
                "train_rows",
                "validation_rows",
                "metrics",
                "best_iteration",
            },
            "evaluation fit result",
        )
        return cls(
            data["schema_version"],
            data["fit_index"],
            data["train_rows"],
            data["validation_rows"],
            data["metrics"],
            data["best_iteration"],
        )


@dataclass(frozen=True)
class EvaluationResultsArtifact:
    schema_version: int
    plan_sha256: str
    fits: tuple[EvaluationFitResult, ...]

    def __post_init__(self) -> None:
        _integer(self.schema_version, "schema_version", low=1, high=1)
        _sha256(self.plan_sha256, "plan_sha256")
        if not isinstance(self.fits, tuple) or any(
            not isinstance(fit, EvaluationFitResult) for fit in self.fits
        ):
            raise TypeError("fits must be evaluation fit results")

    def to_plain_data(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "plan_sha256": self.plan_sha256,
            "fits": [fit.to_plain_data() for fit in self.fits],
        }

    @classmethod
    def from_plain_data(cls, raw: object) -> EvaluationResultsArtifact:
        data = _exact_mapping(raw, {"schema_version", "plan_sha256", "fits"}, "evaluation results")
        if not isinstance(data["fits"], list):
            raise TypeError("fits must be a list")
        return cls(
            data["schema_version"],
            data["plan_sha256"],
            tuple(EvaluationFitResult.from_plain_data(item) for item in data["fits"]),
        )


@dataclass(frozen=True)
class EvaluationAggregateReport:
    plan_sha256: str
    results_sha256: str
    metrics: Mapping[str, Mapping[str, float | int]]
    fit_count: int
    total_validation_rows: int

    def __post_init__(self) -> None:
        _sha256(self.plan_sha256, "plan_sha256")
        _sha256(self.results_sha256, "results_sha256")
        _integer(self.fit_count, "fit_count", low=1)
        _integer(self.total_validation_rows, "total_validation_rows", low=0)
        if not isinstance(self.metrics, Mapping):
            raise TypeError("metrics must be an object")

    def to_plain_data(self) -> dict[str, Any]:
        return {
            "plan_sha256": self.plan_sha256,
            "results_sha256": self.results_sha256,
            "metrics": {key: dict(value) for key, value in self.metrics.items()},
            "fit_count": self.fit_count,
            "total_validation_rows": self.total_validation_rows,
        }

    @classmethod
    def from_plain_data(cls, raw: object) -> EvaluationAggregateReport:
        data = _exact_mapping(
            raw,
            {"plan_sha256", "results_sha256", "metrics", "fit_count", "total_validation_rows"},
            "evaluation aggregate report",
        )
        if not isinstance(data["metrics"], dict):
            raise TypeError("metrics must be an object")
        return cls(
            data["plan_sha256"],
            data["results_sha256"],
            data["metrics"],
            data["fit_count"],
            data["total_validation_rows"],
        )


def _metric_mapping(value: object, name: str) -> Mapping[str, float]:
    if not isinstance(value, Mapping) or not value:
        raise ValueError(f"{name} must be a non-empty object")
    for key, metric in value.items():
        if (
            not isinstance(key, str)
            or not key
            or isinstance(metric, bool)
            or not isinstance(metric, (int, float))
            or not math.isfinite(metric)
        ):
            raise ValueError(f"invalid {name}")
    return value


def aggregate_evaluation_results(
    plan: EvaluationPlan,
    results: EvaluationResultsArtifact,
    metrics: Sequence[str],
    *,
    results_sha256: str,
) -> EvaluationAggregateReport:
    expected = hashlib.sha256(canonical_json_bytes(plan.to_plain_data())).hexdigest()
    if (
        results.schema_version != 1
        or results.plan_sha256 != expected
        or len(results.fits) != len(plan.validation_fits)
    ):
        raise ValueError("evaluation results do not match plan")
    configured_metrics = tuple(metrics)
    if len(set(configured_metrics)) != len(configured_metrics) or any(
        not isinstance(name, str) or not name for name in configured_metrics
    ):
        raise ValueError("metrics must be distinct non-empty names")
    report = {}
    total = 0
    for metric in configured_metrics:
        values = []
        weights = []
        for i, (result, fit) in enumerate(zip(results.fits, plan.validation_fits, strict=True)):
            if (
                result.schema_version != 1
                or result.fit_index != i
                or result.train_rows != fit.train_rows
                or result.validation_rows != fit.validation_rows
            ):
                raise ValueError("fit result does not match plan")
            if set(result.metrics) != set(configured_metrics):
                raise ValueError("fit result metric names do not match configured metrics")
            value = result.metrics.get(metric)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
            ):
                raise ValueError(f"invalid metric {metric}")
            values.append(float(value))
            weights.append(fit.validation_rows)
        if values:
            mean = float(np.average(values, weights=weights))
            weighted_variance = float(np.average((np.asarray(values) - mean) ** 2, weights=weights))
            report[metric] = {
                "mean": mean,
                "stddev": math.sqrt(weighted_variance),
                "min": min(values),
                "max": max(values),
                "fit_count": len(values),
                "validation_rows": sum(weights),
            }
            total = sum(weights)
    return EvaluationAggregateReport(expected, results_sha256, report, plan.fit_count, total)
