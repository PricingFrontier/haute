"""Canonical, deterministic evaluation planning artifacts."""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

import numpy as np

from haute.errors import HauteValidationError

EVALUATION_SCHEMA_VERSION = 1
MAX_VALIDATION_FITS = 10


def _exact_mapping(value: object, keys: set[str], name: str) -> Mapping[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise HauteValidationError(f"{name} must contain exactly {sorted(keys)}")
    return value


def _integer(value: object, name: str, *, low: int | None = None, high: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if (low is not None and value < low) or (high is not None and value > high):
        raise HauteValidationError(f"{name} is outside its permitted range")
    return value


def _fraction(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise TypeError(f"{name} must be a finite number")
    value = float(value)
    if not 0 <= value < 1:
        raise HauteValidationError(f"{name} must be in [0, 1)")
    return value


def _boundary(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be an ISO date or datetime")
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise HauteValidationError(f"{name} must be an ISO date or datetime") from error
    return value


def _sha256(value: object, name: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(c not in "0123456789abcdef" for c in value)
    ):
        raise HauteValidationError(f"invalid {name}")
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
            raise HauteValidationError("date values must not mix timezone awareness")
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
            raise HauteValidationError("invalid evaluation configuration")
        if self.strategy == "temporal":
            if (
                self.seed is not None
                or not isinstance(self.date_column, str)
                or not self.date_column
            ):
                raise HauteValidationError("temporal evaluation requires date_column and no seed")
        else:
            _integer(self.seed, "seed")
        if self.strategy == "group" and (
            not isinstance(self.group_column, str) or not self.group_column
        ):
            raise HauteValidationError("group evaluation requires group_column")
        if not isinstance(self.validation, Mapping) or self.validation.get("method") not in {
            "none",
            "single",
            "cross_validation",
        }:
            raise HauteValidationError("invalid validation configuration")
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
            raise HauteValidationError("invalid validation configuration")
        if method == "single":
            if self.strategy == "temporal":
                _boundary(self.validation["start"], "validation.start")
            else:
                _fraction(self.validation["size"], "validation.size")
        elif method == "cross_validation":
            _integer(self.validation["fold_count"], "fold_count", low=2, high=MAX_VALIDATION_FITS)
            if self.strategy == "temporal" and self.validation["window"] != "expanding":
                raise HauteValidationError("invalid temporal validation window")
        if self.test is not None:
            if not isinstance(self.test, Mapping) or set(self.test) != (
                {"start"} if self.strategy == "temporal" else {"size"}
            ):
                raise HauteValidationError("invalid test configuration")
            if self.strategy == "temporal":
                _boundary(self.test["start"], "test.start")
            else:
                _fraction(self.test["size"], "test.size")
        if (
            self.strategy != "temporal"
            and method == "single"
            and self.test is not None
            and self.validation["size"] + self.test["size"] >= 1
        ):
            raise HauteValidationError("validation.size and test.size must total below 1")
        if self.strategy == "temporal" and method == "single" and self.test is not None:
            validation_start, test_start = _parsed_dates(
                [self.validation["start"], self.test["start"]]
            )
            if validation_start >= test_start:
                raise HauteValidationError("validation.start must precede test.start")

    @classmethod
    def from_plain_data(cls, raw: object) -> EvaluationConfig:
        if not isinstance(raw, dict):
            raise TypeError("evaluation must be an object")
        strategy = raw.get("strategy")
        if strategy not in {"random", "group", "temporal"}:
            raise HauteValidationError("evaluation strategy must be random, group, or temporal")
        required = {"schema_version", "strategy", "validation"}
        if strategy != "temporal":
            required.add("seed")
        if strategy == "group":
            required.add("group_column")
        if strategy == "temporal":
            required.add("date_column")
        allowed = required | {"test"}
        if set(raw) - allowed or not required <= set(raw):
            raise HauteValidationError("evaluation has unknown or missing fields")
        if _integer(raw["schema_version"], "schema_version") != EVALUATION_SCHEMA_VERSION:
            raise HauteValidationError("unsupported evaluation schema_version")
        seed = None if strategy == "temporal" else _integer(raw["seed"], "seed")
        group_column = raw.get("group_column")
        date_column = raw.get("date_column")
        if group_column is not None and (not isinstance(group_column, str) or not group_column):
            raise HauteValidationError("group_column must be a non-empty string")
        if date_column is not None and (not isinstance(date_column, str) or not date_column):
            raise HauteValidationError("date_column must be a non-empty string")
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
            raise HauteValidationError("validation must be an object with method")
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
                    raise HauteValidationError("temporal cross_validation window must be expanding")
                validation["window"] = "expanding"
        else:
            raise HauteValidationError(
                "validation method must be none, single, or cross_validation"
            )
        if strategy == "temporal" and method == "single" and test is not None:
            validation_boundary, test_boundary = _parsed_dates([validation["start"], test["start"]])
            if validation_boundary >= test_boundary:
                raise HauteValidationError("validation.start must precede test.start")
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


#: The evaluation plan file's format: one membership label per source row.
PLAN_SCHEMA_VERSION = 2
#: A final-test row's membership label.
_TEST_LABEL = "T"
_TEST_GROUP = -1
#: Development rows carry the label of their group: the rows every validation fit
#: treats alike (a fold, a temporal block, a holdout's validation or training rows).
_GROUP_LABELS = "0123456789abcdefghijklmnopqrstuvwxyz"
_LABEL_CODES = np.frombuffer(_GROUP_LABELS.encode("ascii"), dtype=np.uint8)
_GROUP_OF_CODE = np.full(256, -2, dtype=np.int16)
_GROUP_OF_CODE[ord(_TEST_LABEL)] = _TEST_GROUP
_GROUP_OF_CODE[_LABEL_CODES] = np.arange(len(_GROUP_LABELS), dtype=np.int16)
#: Each group's label code; the last entry, which group -1 indexes, is the final test's.
_CODE_OF_GROUP = np.frombuffer((_GROUP_LABELS + _TEST_LABEL).encode("ascii"), dtype=np.uint8)
#: A development row's role in one validation fit.
_ROLE_OUTSIDE, _ROLE_TRAIN, _ROLE_VALIDATION = 0, 1, 2
#: The codes of a partition mask.
_MASK_TRAIN, _MASK_VALIDATION, _MASK_TEST, _MASK_UNUSED = 0, 1, 2, 3


def _decode_memberships(value: object, row_count: int) -> np.ndarray:
    """Each row's development group (``-1`` for the final test) from its label."""
    if not isinstance(value, str) or len(value) != row_count or not value.isascii():
        raise HauteValidationError("memberships must hold one label per source row")
    groups = _GROUP_OF_CODE[np.frombuffer(value.encode("ascii"), dtype=np.uint8)]
    if (groups == -2).any():
        raise HauteValidationError("memberships hold an unknown label")
    return groups.astype(np.int8)


def _fit_groups(value: object, group_count: int, name: str) -> frozenset[int]:
    if not isinstance(value, str):
        raise TypeError(f"validation fit {name} must be a string of group labels")
    groups = [_GROUP_LABELS.find(label) for label in value]
    if any(group < 0 or group >= group_count for group in groups) or len(set(groups)) != len(
        groups
    ):
        raise HauteValidationError(f"validation fit {name} names an unknown or repeated group")
    return frozenset(groups)


def _group_labels(groups: frozenset[int]) -> str:
    return "".join(_GROUP_LABELS[group] for group in sorted(groups))


def _row_positions(row_groups: np.ndarray, groups: frozenset[int]) -> tuple[int, ...]:
    """The source positions of *groups*' rows, as Python integers."""
    return tuple(np.flatnonzero(np.isin(row_groups, sorted(groups))).tolist())


@dataclass(frozen=True, eq=False)
class EvaluationValidationFit:
    """One validation fit's memberships: the development groups it trains and validates on.

    ``row_groups`` is the plan's own array of labels, shared by every fit.
    """

    row_groups: np.ndarray = field(repr=False)
    train_groups: frozenset[int]
    validation_groups: frozenset[int]
    train_rows: int
    validation_rows: int

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, EvaluationValidationFit):
            return NotImplemented
        return (
            self.train_groups == other.train_groups
            and self.validation_groups == other.validation_groups
            and np.array_equal(self.row_groups, other.row_groups)
        )

    @property
    def train_positions(self) -> tuple[int, ...]:
        return _row_positions(self.row_groups, self.train_groups)

    @property
    def validation_positions(self) -> tuple[int, ...]:
        return _row_positions(self.row_groups, self.validation_groups)


def _validation_fit(
    row_groups: np.ndarray,
    group_rows: Sequence[int],
    train: frozenset[int],
    validation: frozenset[int],
) -> EvaluationValidationFit:
    return EvaluationValidationFit(
        row_groups,
        train,
        validation,
        sum(group_rows[group] for group in train),
        sum(group_rows[group] for group in validation),
    )


@dataclass(frozen=True, eq=False)
class EvaluationPlan:
    """A source's memberships: one development group per row, ``-1`` for the final test.

    The labels are the plan file's encoding. Development rows that every validation
    fit treats alike form one group; groups are numbered in the order of their roles,
    so one plan has one encoding.
    """

    schema_version: int
    source_sha256: str
    row_count: int
    config: EvaluationConfig
    row_groups: np.ndarray = field(repr=False)
    validation_fits: tuple[EvaluationValidationFit, ...]
    summary: Mapping[str, Any]

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, EvaluationPlan):
            return NotImplemented
        return self.to_plain_data() == other.to_plain_data()

    @property
    def fit_count(self) -> int:
        return len(self.validation_fits) + 1

    @property
    def test_rows(self) -> int:
        return int(np.count_nonzero(self.row_groups == _TEST_GROUP))

    @property
    def development_rows(self) -> int:
        return self.row_count - self.test_rows

    @property
    def development_positions(self) -> tuple[int, ...]:
        return tuple(np.flatnonzero(self.row_groups != _TEST_GROUP).tolist())

    @property
    def test_positions(self) -> tuple[int, ...]:
        return tuple(np.flatnonzero(self.row_groups == _TEST_GROUP).tolist())

    def final_mask(self) -> np.ndarray:
        return np.where(self.row_groups == _TEST_GROUP, _MASK_TEST, _MASK_TRAIN).astype(np.int8)

    def _fit_mask(self, fit_index: int, test_code: int) -> np.ndarray:
        fit_index = _integer(fit_index, "fit_index", low=0, high=len(self.validation_fits) - 1)
        fit = self.validation_fits[fit_index]
        code_of_group = np.full(len(_GROUP_LABELS) + 1, _MASK_UNUSED, dtype=np.int8)
        code_of_group[sorted(fit.train_groups)] = _MASK_TRAIN
        code_of_group[sorted(fit.validation_groups)] = _MASK_VALIDATION
        # The final test's group, -1, indexes the last entry.
        code_of_group[-1] = test_code
        mask: np.ndarray = code_of_group[self.row_groups]
        return mask

    def selection_mask(self, fit_index: int) -> np.ndarray:
        return self._fit_mask(fit_index, _MASK_UNUSED)

    def saved_selection_mask(self, fit_index: int) -> np.ndarray:
        """Keep final-test rows held out when a validation fit is the saved model."""
        return self._fit_mask(fit_index, _MASK_TEST)

    def to_plain_data(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source_sha256": self.source_sha256,
            "row_count": self.row_count,
            "config": self.config.to_plain_data(),
            "memberships": _CODE_OF_GROUP[self.row_groups].tobytes().decode("ascii"),
            "validation_fits": [
                {
                    "train": _group_labels(fit.train_groups),
                    "validation": _group_labels(fit.validation_groups),
                }
                for fit in self.validation_fits
            ],
            "summary": dict(self.summary),
        }

    @classmethod
    def from_plain_data(cls, raw: object) -> EvaluationPlan:
        required = {
            "schema_version",
            "source_sha256",
            "row_count",
            "config",
            "memberships",
            "validation_fits",
            "summary",
        }
        data = _exact_mapping(raw, required, "evaluation plan")
        if _integer(data["schema_version"], "schema_version") != PLAN_SCHEMA_VERSION:
            raise HauteValidationError("unsupported plan schema_version")
        source = _sha256(data["source_sha256"], "source_sha256")
        count = _integer(data["row_count"], "row_count", low=1)
        config = EvaluationConfig.from_plain_data(data["config"])
        groups = _decode_memberships(data["memberships"], count)
        development_groups = groups[groups >= 0]
        group_count = int(development_groups.max()) + 1 if len(development_groups) else 0
        if group_count == 0:
            raise HauteValidationError("development/test positions overlap or are empty")
        group_rows = np.bincount(development_groups, minlength=group_count).tolist()
        if 0 in group_rows:
            raise HauteValidationError("development group labels are not contiguous")
        development_rows = len(development_groups)
        test_rows = count - development_rows
        if bool(test_rows) != (config.test is not None):
            raise HauteValidationError("test membership disagrees with evaluation config")
        if (
            not isinstance(data["validation_fits"], list)
            or len(data["validation_fits"]) != config.validation_fit_count
        ):
            raise HauteValidationError("validation fit count disagrees")
        every_group = frozenset(range(group_count))
        fit_groups: list[tuple[frozenset[int], frozenset[int]]] = []
        for item in data["validation_fits"]:
            item = _exact_mapping(item, {"train", "validation"}, "validation fit")
            train = _fit_groups(item["train"], group_count, "train")
            valid = _fit_groups(item["validation"], group_count, "validation")
            if not train or not valid or train & valid:
                raise HauteValidationError("invalid validation fit membership")
            fit_groups.append((train, valid))
        if config.validation["method"] == "cross_validation":
            validation_sets = [valid for _, valid in fit_groups]
            if any(
                left & right
                for index, left in enumerate(validation_sets)
                for right in validation_sets[index + 1 :]
            ):
                raise HauteValidationError("cross-validation validation memberships overlap")
            if config.strategy == "temporal":
                for (previous_train, previous_valid), (current_train, _) in zip(
                    fit_groups, fit_groups[1:], strict=False
                ):
                    if current_train != previous_train | previous_valid:
                        raise HauteValidationError(
                            "temporal cross-validation training membership is not expanding"
                        )
                if fit_groups[-1][0] | fit_groups[-1][1] != every_group:
                    raise HauteValidationError(
                        "temporal cross-validation does not end with all development rows"
                    )
            elif frozenset().union(*validation_sets) != every_group:
                raise HauteValidationError(
                    "cross-validation validation memberships do not partition development"
                )
            if config.strategy != "temporal" and any(
                train | valid != every_group for train, valid in fit_groups
            ):
                raise HauteValidationError("cross-validation fit does not cover development")
        elif fit_groups and fit_groups[0][0] | fit_groups[0][1] != every_group:
            raise HauteValidationError("single validation fit does not cover development")
        if not isinstance(data["summary"], dict):
            raise TypeError("summary must be an object")
        summary = data["summary"]
        expected_summary = {
            "development_rows": development_rows,
            "test_rows": test_rows,
            "validation_fit_count": len(fit_groups),
        }
        if any(isinstance(v, bool) or not isinstance(v, int) or v < 0 for v in summary.values()):
            raise HauteValidationError("summary values must be non-negative integers")
        if any(summary.get(key) != value for key, value in expected_summary.items()):
            raise HauteValidationError("summary disagrees with plan membership")
        required_summary = set(expected_summary)
        if config.strategy == "group":
            required_summary |= {"development_group_count", "test_group_count"}
        if config.strategy == "temporal":
            required_summary |= {"development_date_count", "test_date_count"}
        if set(summary) != required_summary:
            raise HauteValidationError("summary fields do not match the evaluation strategy")
        if config.strategy == "group":
            if summary["development_group_count"] < 1:
                raise HauteValidationError("summary must contain a development group")
            if bool(test_rows) != bool(summary["test_group_count"]):
                raise HauteValidationError("summary test group count disagrees with membership")
            if summary["development_group_count"] + summary["test_group_count"] > count:
                raise HauteValidationError("summary group counts exceed source rows")
        if config.strategy == "temporal":
            if summary["development_date_count"] < 1:
                raise HauteValidationError("summary must contain a development date")
            if bool(test_rows) != bool(summary["test_date_count"]):
                raise HauteValidationError("summary test date count disagrees with membership")
            if summary["development_date_count"] + summary["test_date_count"] > count:
                raise HauteValidationError("summary date counts exceed source rows")
        # One plan has one encoding: groups numbered by their roles, no two alike, and
        # each fit's labels in order.
        signatures = [
            tuple(
                _ROLE_TRAIN
                if group in train
                else (_ROLE_VALIDATION if group in valid else _ROLE_OUTSIDE)
                for train, valid in fit_groups
            )
            for group in range(group_count)
        ]
        canonical_fits = [
            {"train": _group_labels(train), "validation": _group_labels(valid)}
            for train, valid in fit_groups
        ]
        if signatures != sorted(set(signatures)) or canonical_fits != data["validation_fits"]:
            raise HauteValidationError("evaluation plan memberships are not in canonical form")
        groups.setflags(write=False)
        fits = tuple(
            _validation_fit(groups, group_rows, train, valid) for train, valid in fit_groups
        )
        return cls(PLAN_SCHEMA_VERSION, source, count, config, groups, fits, summary)


def _plan_from_roles(
    config: EvaluationConfig,
    source_sha256: str,
    is_test: np.ndarray,
    fit_roles: Sequence[np.ndarray],
    summary: dict[str, Any],
) -> EvaluationPlan:
    """The plan for each row's final-test flag and its role in every validation fit.

    Each development row's roles, read as a base-3 number with the first fit most
    significant, order the groups lexicographically by their roles.
    """
    row_count = len(is_test)
    development = np.flatnonzero(~is_test)
    key = np.zeros(len(development), dtype=np.int32)
    for roles in fit_roles:
        key = key * 3 + roles[development]
    keys, groups = np.unique(key, return_inverse=True)
    if len(keys) > len(_GROUP_LABELS):
        raise HauteValidationError("evaluation plan has more development groups than labels")
    row_groups = np.full(row_count, _TEST_GROUP, dtype=np.int8)
    row_groups[development] = groups.reshape(-1)
    row_groups.setflags(write=False)
    group_rows = np.bincount(groups.reshape(-1), minlength=len(keys)).tolist()
    fits = []
    for index in range(len(fit_roles)):
        role = keys // 3 ** (len(fit_roles) - 1 - index) % 3
        fits.append(
            _validation_fit(
                row_groups,
                group_rows,
                frozenset(np.flatnonzero(role == _ROLE_TRAIN).tolist()),
                frozenset(np.flatnonzero(role == _ROLE_VALIDATION).tolist()),
            )
        )
    summary.update(
        {
            "development_rows": len(development),
            "test_rows": row_count - len(development),
            "validation_fit_count": len(fits),
        }
    )
    return EvaluationPlan(
        PLAN_SCHEMA_VERSION, source_sha256, row_count, config, row_groups, tuple(fits), summary
    )


def _count(size: float, total: int) -> int:
    value = int(round(size * total))
    if value < 1 or value >= total:
        raise HauteValidationError("requested partition must leave non-empty partitions")
    return value


def _class_rows(positions: np.ndarray, values: Sequence[object]) -> dict[object, np.ndarray]:
    """*positions* by class, classes as first seen, each keeping the order given."""
    classes: dict[object, int] = {}
    codes = np.fromiter(
        (classes.setdefault(values[position], len(classes)) for position in positions),
        dtype=np.int64,
        count=len(positions),
    )
    return {key: positions[codes == code] for key, code in classes.items()}


def _stratified_parts(
    positions: Sequence[int] | np.ndarray, values: Sequence[object], parts: int, seed: int
) -> list[np.ndarray]:
    classes = _class_rows(np.asarray(positions, dtype=np.int64), values)
    counts = {str(k): len(v) for k, v in classes.items()}
    if min(map(len, classes.values())) < parts:
        raise HauteValidationError(f"class counts {counts}; required minimum {parts}")
    rng = np.random.default_rng(seed)
    output: list[list[np.ndarray]] = [[] for _ in range(parts)]
    # Classes in label order, so the seeded stream does not follow which appeared first.
    for key in sorted(classes, key=str):
        rows = classes[key]
        rng.shuffle(rows)
        for index in range(parts):
            output[index].append(rows[index::parts])
    return [np.concatenate(part) for part in output]


def _development_reserve(validation: Mapping[str, Any]) -> tuple[int, list[str]]:
    """Rows of every class the development set keeps for the partitions after the test.

    Each validation fold needs one row of every class and trains on the other folds; a
    holdout needs one row to validate and one to train on; no validation needs one row to
    train on. Returns the count and the partitions it serves, for refusals.
    """
    method = validation["method"]
    if method == "cross_validation":
        folds = validation["fold_count"]
        return folds, [f"{folds} for the {folds} validation folds"]
    if method == "single":
        return 2, ["1 for the validation holdout", "1 to train on"]
    return 1, ["1 to train on"]


def _stratified_sample(
    positions: np.ndarray,
    values: Sequence[object],
    size: int,
    seed: int,
    reserve: int,
    *,
    partition: str,
) -> np.ndarray:
    """Exactly *size* of *positions*, at least one of every class, *reserve* of each left.

    Each class starts at its proportional share (at least one, at most its rows less the
    reserve); the classes furthest below their share take the rows still missing, and
    those furthest above it give up rows past the total, ties going by label. The rows
    then come from each class's seeded shuffle, classes in label order.
    """
    classes = _class_rows(positions, values)
    counts = {str(key): len(rows) for key, rows in classes.items()}
    ceiling = {key: len(rows) - reserve for key, rows in classes.items()}
    capacity = sum(ceiling.values())
    if min(ceiling.values()) < 1 or not len(classes) <= size <= capacity:
        raise HauteValidationError(
            f"class counts {counts}; a {partition} of {size} rows must hold one row of every "
            f"class and leave {reserve} of each, so it can hold {len(classes)} to "
            f"{capacity} rows"
        )
    raw = {key: size * len(rows) / len(positions) for key, rows in classes.items()}
    allocations = {key: min(ceiling[key], max(1, math.floor(raw[key]))) for key in classes}
    while sum(allocations.values()) < size:
        short = [key for key in classes if allocations[key] < ceiling[key]]
        key = max(short, key=lambda item: (raw[item] - allocations[item], str(item)))
        allocations[key] += 1
    while sum(allocations.values()) > size:
        over = [key for key in classes if allocations[key] > 1]
        key = max(over, key=lambda item: (allocations[item] - raw[item], str(item)))
        allocations[key] -= 1
    rng = np.random.default_rng(seed)
    selected: list[np.ndarray] = []
    for key in sorted(classes, key=str):
        rows = classes[key]
        rng.shuffle(rows)
        selected.append(rows[: allocations[key]])
    return np.concatenate(selected)


def _balanced_groups(sizes: Mapping[str, int], buckets: int, seed: int) -> list[list[str]]:
    """The groups in each of *buckets* buckets, largest first into the emptiest bucket."""
    rng = np.random.default_rng(seed)
    # Shuffled from key order, not the order the groups appeared in.
    keys = sorted(sizes)
    rng.shuffle(keys)
    rank = {x: i for i, x in enumerate(keys)}
    keys.sort(key=lambda x: (-sizes[x], rank[x]))
    result: list[list[str]] = [[] for _ in range(buckets)]
    totals = [0] * buckets
    for key in keys:
        index = min(range(buckets), key=lambda i: (totals[i], i))
        result[index].append(key)
        totals[index] += sizes[key]
    if any(not item for item in result):
        raise HauteValidationError("requested group partition would be empty")
    return result


def _groups_near_target(sizes: Mapping[str, int], target: int, seed: int) -> list[str]:
    """Seeded greedy assignment that minimises distance from one requested row target."""
    rng = np.random.default_rng(seed)
    # Shuffled from key order, not the order the groups appeared in.
    keys = sorted(sizes)
    rng.shuffle(keys)
    rank = {key: index for index, key in enumerate(keys)}
    keys.sort(key=lambda key: (-sizes[key], rank[key]))
    # Begin with the closest viable whole group. Starting from the largest
    # group can strand a much closer smaller group when the requested
    # fraction is small.
    first = min(
        keys,
        key=lambda key: (abs(sizes[key] - target), rank[key]),
    )
    selected_keys = {first}
    selected_rows = sizes[first]
    for key in keys:
        if key in selected_keys:
            continue
        candidate = selected_rows + sizes[key]
        if abs(candidate - target) < abs(selected_rows - target):
            selected_keys.add(key)
            selected_rows = candidate
    if selected_rows == sum(sizes.values()):
        raise HauteValidationError("requested group partition would be empty")
    return [key for key in keys if key in selected_keys]


def _canonical_positions(
    row_order: Sequence[int] | np.ndarray | None, row_count: int
) -> np.ndarray:
    """A random plan's source positions in canonical order, checked to be every position once."""
    if row_order is None:
        raise HauteValidationError(
            "A random plan selects rows by position and needs the canonical row order"
        )
    order = np.asarray(row_order)
    if (
        order.ndim != 1
        or len(order) != row_count
        or order.dtype.kind not in "iu"
        or int(order.min()) < 0
        or int(order.max()) >= row_count
        or int(np.bincount(order, minlength=row_count).max()) != 1
    ):
        raise HauteValidationError(
            f"The canonical row order is not a permutation of the source's {row_count} positions"
        )
    return order.astype(np.int64)


def _date_codes(
    date_values: Sequence[object], boundaries: Sequence[object]
) -> tuple[np.ndarray, list[datetime], list[datetime]]:
    """Each row's index among the distinct dates, those dates parsed, and *boundaries* parsed."""
    distinct: dict[object, int] = {}
    codes = np.empty(len(date_values), dtype=np.int64)
    for position, value in enumerate(date_values):
        if not isinstance(value, (str, date)):
            raise TypeError("date values must be ISO strings or native date/datetime values")
        codes[position] = distinct.setdefault(value, len(distinct))
    parsed = _parsed_dates([*distinct, *boundaries])
    return codes, parsed[: len(distinct)], parsed[len(distinct) :]


def _roles_for_parts(is_test: np.ndarray, parts: Sequence[np.ndarray]) -> list[np.ndarray]:
    """Each fit's roles: its part validates and the rest of development trains on."""
    fit_roles = []
    for part in parts:
        roles = np.where(is_test, _ROLE_OUTSIDE, _ROLE_TRAIN).astype(np.int8)
        roles[part] = _ROLE_VALIDATION
        if not (roles == _ROLE_TRAIN).any() or not (roles == _ROLE_VALIDATION).any():
            raise HauteValidationError("requested validation partition is empty")
        fit_roles.append(roles)
    return fit_roles


def generate_evaluation_plan(
    config: EvaluationConfig,
    *,
    source_sha256: str,
    row_count: int,
    task: str,
    target_values: Sequence[object] | None = None,
    group_values: Sequence[object] | None = None,
    date_values: Sequence[object] | None = None,
    row_order: Sequence[int] | np.ndarray | None = None,
) -> EvaluationPlan:
    """Plan a source's development, final-test and validation-fit memberships.

    A random plan selects positions, so it takes *row_order*, the source positions in
    canonical order (``haute.modelling._row_order``), and draws over that order: the
    same rows are selected whatever order the source holds them in. Group and temporal
    plans decide by group and date values and take no order.
    """
    if row_count < 1:
        raise HauteValidationError("invalid source or row count")
    _sha256(source_sha256, "source_sha256")
    if config.strategy != "random" and row_order is not None:
        raise HauteValidationError(
            f"Only a random plan takes a row order; a {config.strategy} plan decides by values"
        )
    method = config.validation["method"]
    summary: dict[str, Any] = {}
    if task not in {"regression", "classification"}:
        raise HauteValidationError("evaluation task must be regression or classification")
    is_test = np.zeros(row_count, dtype=bool)
    fit_roles: list[np.ndarray]
    if config.strategy == "temporal":
        if date_values is None or len(date_values) != row_count:
            raise HauteValidationError("temporal evaluation requires date values")
        boundaries = [config.test["start"]] if config.test else []
        if method == "single":
            boundaries.append(config.validation["start"])
        codes, dates, parsed_boundaries = _date_codes(date_values, boundaries)
        if config.test:
            test_start = parsed_boundaries[0]
            is_test_date = np.array([value >= test_start for value in dates], dtype=bool)
            is_test = is_test_date[codes]
            if is_test.all() or not is_test.any():
                raise HauteValidationError("temporal test partition is empty")
        else:
            is_test_date = np.zeros(len(dates), dtype=bool)
        ordered = sorted({v for v, test_date in zip(dates, is_test_date.tolist()) if not test_date})
        summary.update(
            {
                "development_date_count": len(ordered),
                "test_date_count": len(
                    {v for v, test_date in zip(dates, is_test_date.tolist()) if test_date}
                ),
            }
        )
        if method == "none":
            fit_roles = []
        elif method == "single":
            boundary = parsed_boundaries[-1]
            before = np.array([value < boundary for value in dates], dtype=bool)[codes]
            roles = np.full(row_count, _ROLE_OUTSIDE, dtype=np.int8)
            roles[~is_test & before] = _ROLE_TRAIN
            roles[~is_test & ~before] = _ROLE_VALIDATION
            if not (roles == _ROLE_TRAIN).any() or not (roles == _ROLE_VALIDATION).any():
                raise HauteValidationError("temporal validation partition is empty")
            fit_roles = [roles]
        else:
            k = config.validation["fold_count"]
            if len(ordered) < k + 1:
                raise HauteValidationError("temporal cross_validation needs enough distinct dates")
            blocks = np.array_split(np.asarray(ordered, dtype=object), k + 1)
            block_of_date = {
                value: index for index, block in enumerate(blocks) for value in block.tolist()
            }
            # A final-test date belongs to no block.
            row_block = np.array([block_of_date.get(v, -1) for v in dates], dtype=np.int64)[codes]
            fit_roles = []
            for index in range(k):
                roles = np.full(row_count, _ROLE_OUTSIDE, dtype=np.int8)
                roles[(row_block >= 0) & (row_block <= index)] = _ROLE_TRAIN
                roles[row_block == index + 1] = _ROLE_VALIDATION
                fit_roles.append(roles)
    else:
        parts: list[np.ndarray]
        if config.strategy == "group":
            if group_values is None or len(group_values) != row_count:
                raise HauteValidationError("group evaluation requires group values")
            group_index: dict[str, int] = {}
            group_codes = np.fromiter(
                (
                    group_index.setdefault(_json_key(value), len(group_index))
                    for value in group_values
                ),
                dtype=np.int64,
                count=row_count,
            )
            sizes = dict(
                zip(group_index, np.bincount(group_codes, minlength=len(group_index)).tolist())
            )

            def rows_of(keys: Sequence[str]) -> np.ndarray:
                selected = np.zeros(len(group_index), dtype=bool)
                selected[[group_index[key] for key in keys]] = True
                return selected[group_codes]

            development_sizes = sizes
            if config.test:
                target = _count(config.test["size"], row_count)
                test_groups = _groups_near_target(sizes, target, config.seed or 0)
                is_test = rows_of(test_groups)
                held_out = set(test_groups)
                development_sizes = {k: v for k, v in sizes.items() if k not in held_out}
            summary.update(
                {
                    "development_group_count": len(development_sizes),
                    "test_group_count": len(sizes) - len(development_sizes),
                }
            )
            if method == "cross_validation":
                parts = [
                    rows_of(keys)
                    for keys in _balanced_groups(
                        development_sizes, config.validation["fold_count"], config.seed or 0
                    )
                ]
            elif method == "single":
                target = _count(config.validation["size"], row_count)
                parts = [
                    rows_of(_groups_near_target(development_sizes, target, (config.seed or 0) + 1))
                ]
            else:
                parts = []
        else:
            canonical = _canonical_positions(row_order, row_count)
            rng = np.random.default_rng(config.seed)
            positions = canonical.copy()
            rng.shuffle(positions)
            if task == "classification":
                if target_values is None or len(target_values) != row_count:
                    raise HauteValidationError("classification evaluation requires target values")
                classes: dict[str, int] = {}
                for value in target_values:
                    key = _json_key(value)
                    classes[key] = classes.get(key, 0) + 1
                reserve, later_partitions = _development_reserve(config.validation)
                needs = (["1 for the final test"] if config.test else []) + later_partitions
                required_minimum = (1 if config.test else 0) + reserve
                if min(classes.values()) < required_minimum:
                    named = needs[0] if len(needs) == 1 else ", ".join(needs[:-1])
                    if len(needs) > 1:
                        named += f" and {needs[-1]}"
                    raise HauteValidationError(
                        f"class counts {classes}; required minimum {required_minimum} rows of "
                        f"every class: {named}"
                    )
            if config.test:
                n = _count(config.test["size"], row_count)
                if task == "classification":
                    assert target_values is not None
                    is_test[
                        _stratified_sample(
                            canonical,
                            target_values,
                            n,
                            config.seed or 0,
                            reserve,
                            partition="final test",
                        )
                    ] = True
                else:
                    is_test[positions[:n]] = True
            in_development = ~is_test
            development_order = positions[in_development[positions]]
            canonical_development = canonical[in_development[canonical]]
            if method == "cross_validation":
                if task == "classification":
                    assert target_values is not None
                    parts = _stratified_parts(
                        canonical_development,
                        target_values,
                        config.validation["fold_count"],
                        config.seed or 0,
                    )
                else:
                    parts = np.array_split(development_order, config.validation["fold_count"])
            elif method == "single":
                n = _count(config.validation["size"], row_count)
                if task == "classification":
                    assert target_values is not None
                    parts = [
                        _stratified_sample(
                            canonical_development,
                            target_values,
                            n,
                            (config.seed or 0) + 1,
                            1,
                            partition="validation holdout",
                        )
                    ]
                else:
                    parts = [development_order[:n]]
            else:
                parts = []
        fit_roles = _roles_for_parts(is_test, parts)
    return _plan_from_roles(config, source_sha256, is_test, fit_roles, summary)


def canonical_json_bytes(value: object) -> bytes:
    def reject(item: object) -> None:
        if isinstance(item, float) and not math.isfinite(item):
            raise HauteValidationError("canonical JSON cannot contain non-finite numbers")
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
    """The file's SHA-256, without holding it: a training source can be many gigabytes."""
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


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
        raise HauteValidationError("evaluation plan source digest does not match")
    return plan


def save_evaluation_results(results: EvaluationResultsArtifact, path: str | Path) -> None:
    save_canonical_json(results.to_plain_data(), path)


def load_evaluation_results(path: str | Path, *, plan_sha256: str) -> EvaluationResultsArtifact:
    results = EvaluationResultsArtifact.from_plain_data(json.loads(Path(path).read_bytes()))
    if results.plan_sha256 != _sha256(plan_sha256, "plan_sha256"):
        raise HauteValidationError("evaluation results plan digest does not match")
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
        raise HauteValidationError(f"{name} must be a non-empty object")
    for key, metric in value.items():
        if (
            not isinstance(key, str)
            or not key
            or isinstance(metric, bool)
            or not isinstance(metric, (int, float))
            or not math.isfinite(metric)
        ):
            raise HauteValidationError(f"invalid {name}")
    return value


def aggregate_evaluation_results(
    plan: EvaluationPlan,
    results: EvaluationResultsArtifact,
    metrics: Sequence[str],
    *,
    results_sha256: str,
    refit_on_development: bool = True,
) -> EvaluationAggregateReport:
    """Aggregate the selection fits; the fit count includes the final refit when one runs.

    Skipping the refit keeps the single holdout-validation fit as the saved model, so it is
    only meaningful for ``single`` validation.
    """
    if not refit_on_development and plan.config.validation["method"] != "single":
        raise HauteValidationError("skipping the final refit requires holdout validation")
    expected = hashlib.sha256(canonical_json_bytes(plan.to_plain_data())).hexdigest()
    if (
        results.schema_version != 1
        or results.plan_sha256 != expected
        or len(results.fits) != len(plan.validation_fits)
    ):
        raise HauteValidationError("evaluation results do not match plan")
    configured_metrics = tuple(metrics)
    if len(set(configured_metrics)) != len(configured_metrics) or any(
        not isinstance(name, str) or not name for name in configured_metrics
    ):
        raise HauteValidationError("metrics must be distinct non-empty names")
    # Every fit is checked whatever the metric list holds: an empty list would
    # otherwise skip them and aggregate to an empty report.
    for i, (result, fit) in enumerate(zip(results.fits, plan.validation_fits, strict=True)):
        if (
            result.schema_version != 1
            or result.fit_index != i
            or result.train_rows != fit.train_rows
            or result.validation_rows != fit.validation_rows
        ):
            raise HauteValidationError("fit result does not match plan")
        if set(result.metrics) != set(configured_metrics):
            raise HauteValidationError("fit result metric names do not match configured metrics")
    report = {}
    total = 0
    for metric in configured_metrics:
        values = []
        weights = []
        for result, fit in zip(results.fits, plan.validation_fits, strict=True):
            value = result.metrics.get(metric)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
            ):
                raise HauteValidationError(f"invalid metric {metric}")
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
    fit_count = len(plan.validation_fits) + int(refit_on_development)
    return EvaluationAggregateReport(expected, results_sha256, report, fit_count, total)
