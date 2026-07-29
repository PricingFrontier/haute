"""Strict, portable artifacts and split plans for bounded cross-validation.

This module deliberately contains no training code.  It is the small pure seam used
by the training supervisor: build a plan from the prepared source, persist it, then
validate and aggregate the independently produced fold metrics.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import json
import math
import os
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Literal

import numpy as np
import polars as pl

CV_SCHEMA_VERSION = 1
MAX_CV_FOLDS = 10
MAX_CV_FITS = MAX_CV_FOLDS + 1
_SHA256_RE = set("0123456789abcdef")
_STRATEGIES = frozenset(("random", "group", "temporal"))


def _exact_keys(value: Mapping[str, Any], expected: set[str], name: str) -> None:
    actual = set(value)
    if actual != expected:
        raise ValueError(f"{name} must have exactly {sorted(expected)}; got {sorted(actual)}")


def _int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer (not bool)")
    return int(value)


def _sha256(value: Any, name: str = "sha256") -> str:
    if not isinstance(value, str) or len(value) != 64 or set(value) - _SHA256_RE:
        raise ValueError(f"{name} must be a lowercase SHA-256 hex digest")
    return value


def _finite(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    return float(value)


def canonical_json_bytes(value: Any) -> bytes:
    """Return canonical, finite JSON bytes suitable for stable hashing."""
    _assert_finite_json(value)
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def _assert_finite_json(value: Any) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("JSON artifact contains a non-finite number")
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("JSON artifact object keys must be strings")
            _assert_finite_json(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _assert_finite_json(item)


def file_sha256(path: Path | str) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def save_canonical_json(value: Any, path: Path | str) -> None:
    """Atomically replace *path* with canonical finite JSON in its directory."""
    target = Path(path)
    data = canonical_json_bytes(value)
    fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, target)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def load_canonical_json(path: Path | str) -> Any:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON artifact: {exc}") from exc
    _assert_finite_json(value)
    return value


@dataclasses.dataclass(frozen=True)
class CrossValidationConfig:
    schema_version: int
    strategy: Literal["random", "group", "temporal"]
    fold_count: int
    seed: int
    group_column: str | None = None
    date_column: str | None = None

    def __post_init__(self) -> None:
        if _int(self.schema_version, "schema_version") != CV_SCHEMA_VERSION:
            raise ValueError(f"unsupported cross_validation schema_version {self.schema_version}")
        if self.strategy not in _STRATEGIES:
            raise ValueError("cross_validation strategy must be random, group, or temporal")
        folds = _int(self.fold_count, "fold_count")
        if not 2 <= folds <= MAX_CV_FOLDS:
            raise ValueError(f"fold_count must be between 2 and {MAX_CV_FOLDS}")
        _int(self.seed, "seed")

        if self.strategy == "group":
            if not isinstance(self.group_column, str) or not self.group_column.strip():
                raise ValueError("group_column must be a non-empty string")
            if self.date_column is not None:
                raise ValueError("date_column is not valid for group cross-validation")
        elif self.strategy == "temporal":
            if not isinstance(self.date_column, str) or not self.date_column.strip():
                raise ValueError("date_column must be a non-empty string")
            if self.group_column is not None:
                raise ValueError("group_column is not valid for temporal cross-validation")
        elif self.group_column is not None or self.date_column is not None:
            name = "group_column" if self.group_column is not None else "date_column"
            raise ValueError(f"{name} is not valid for random cross-validation")

    @classmethod
    def from_plain_data(cls, raw: Mapping[str, Any]) -> CrossValidationConfig:
        if not isinstance(raw, Mapping):
            raise ValueError("cross_validation must be an object")
        strategy = raw.get("strategy")
        if strategy not in _STRATEGIES:
            raise ValueError("cross_validation strategy must be random, group, or temporal")
        expected = {"schema_version", "strategy", "fold_count", "seed"}
        if strategy == "group":
            expected.add("group_column")
        if strategy == "temporal":
            expected.add("date_column")
        _exact_keys(raw, expected, "cross_validation")
        version = _int(raw["schema_version"], "schema_version")
        if version != CV_SCHEMA_VERSION:
            raise ValueError(f"unsupported cross_validation schema_version {version}")
        folds = _int(raw["fold_count"], "fold_count")
        if not 2 <= folds <= MAX_CV_FOLDS:
            raise ValueError(f"fold_count must be between 2 and {MAX_CV_FOLDS}")
        seed = _int(raw["seed"], "seed")
        group = raw.get("group_column")
        date = raw.get("date_column")
        for name, value in (("group_column", group), ("date_column", date)):
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"{name} must be a non-empty string")
        return cls(version, strategy, folds, seed, group, date)

    # Short aliases make the artifact boundary convenient to callers.
    from_dict = from_plain_data

    def to_plain_data(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "schema_version": self.schema_version,
            "strategy": self.strategy,
            "fold_count": self.fold_count,
            "seed": self.seed,
        }
        if self.strategy == "group":
            data["group_column"] = self.group_column
        if self.strategy == "temporal":
            data["date_column"] = self.date_column
        return data

    to_dict = to_plain_data


@dataclasses.dataclass(frozen=True)
class FoldPlan:
    schema_version: int
    config: CrossValidationConfig
    row_count: int
    source_sha256: str
    assignments: tuple[int, ...]
    fold_counts: tuple[tuple[int, int], ...]  # (train, validation), ordered by fold

    def __post_init__(self) -> None:
        if _int(self.schema_version, "fold plan schema_version") != CV_SCHEMA_VERSION:
            raise ValueError("unsupported fold plan schema_version")
        if _int(self.row_count, "row_count") < 1 or len(self.assignments) != self.row_count:
            raise ValueError("fold plan assignment count must equal positive row_count")
        _sha256(self.source_sha256, "source_sha256")
        if len(self.fold_counts) != self.config.fold_count:
            raise ValueError("fold plan must have one count summary per fold")
        self._validate()

    def _validate(self) -> None:
        k = self.config.fold_count
        allowed = set(range(k)) if self.config.strategy != "temporal" else {-1, *range(k)}
        if any(
            isinstance(item, bool) or not isinstance(item, int) or item not in allowed
            for item in self.assignments
        ):
            raise ValueError("fold plan assignments are outside the strategy vocabulary")
        expected = _fold_count_summaries(self.config.strategy, self.assignments, k)
        for index, summary in enumerate(self.fold_counts):
            if len(summary) != 2 or any(
                isinstance(x, bool) or not isinstance(x, int) or x < 1 for x in summary
            ):
                raise ValueError(f"invalid non-empty counts for fold {index}")
            if summary != expected[index]:
                raise ValueError(f"fold {index} count summary disagrees with assignments")
        if self.config.strategy == "temporal":
            _validate_temporal_assignments(self.assignments, k)

    def partition_mask(self, fold_index: int) -> np.ndarray:
        if (
            isinstance(fold_index, bool)
            or not isinstance(fold_index, int)
            or not 0 <= fold_index < self.config.fold_count
        ):
            raise ValueError("fold_index is outside the plan")
        assignments = np.asarray(self.assignments, dtype=np.int64)
        labels = np.zeros(self.row_count, dtype=np.int8)
        labels[assignments == fold_index] = 1
        if self.config.strategy == "temporal":
            labels[assignments > fold_index] = 3
        return labels

    def assert_source_digest(self, actual_sha256: str) -> None:
        if _sha256(actual_sha256, "source_sha256") != self.source_sha256:
            raise ValueError("fold plan source SHA-256 does not match prepared source")

    def to_plain_data(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "config": self.config.to_plain_data(),
            "row_count": self.row_count,
            "source_sha256": self.source_sha256,
            "assignments": list(self.assignments),
            "fold_counts": [
                {"train_rows": train, "validation_rows": valid} for train, valid in self.fold_counts
            ],
        }

    to_dict = to_plain_data

    @classmethod
    def from_plain_data(cls, raw: Mapping[str, Any]) -> FoldPlan:
        if not isinstance(raw, Mapping):
            raise ValueError("fold plan must be an object")
        _exact_keys(
            raw,
            {
                "schema_version",
                "config",
                "row_count",
                "source_sha256",
                "assignments",
                "fold_counts",
            },
            "fold plan",
        )
        version = _int(raw["schema_version"], "fold plan schema_version")
        row_count = _int(raw["row_count"], "row_count")
        if (
            row_count < 1
            or not isinstance(raw["assignments"], list)
            or not isinstance(raw["fold_counts"], list)
        ):
            raise ValueError("invalid fold plan array fields")
        counts: list[tuple[int, int]] = []
        for value in raw["fold_counts"]:
            if not isinstance(value, Mapping):
                raise ValueError("fold count summary must be an object")
            _exact_keys(value, {"train_rows", "validation_rows"}, "fold count summary")
            counts.append(
                (
                    _int(value["train_rows"], "train_rows"),
                    _int(value["validation_rows"], "validation_rows"),
                )
            )
        return cls(
            version,
            CrossValidationConfig.from_plain_data(raw["config"]),
            row_count,
            _sha256(raw["source_sha256"], "source_sha256"),
            tuple(raw["assignments"]),
            tuple(counts),
        )

    from_dict = from_plain_data


def _canonical_group(value: Any) -> str:
    if value is None:
        raise ValueError("group key contains null")
    # type tagging prevents 1, "1", and True becoming the same group.
    if isinstance(value, bool):
        return f"bool:{value}"
    if isinstance(value, (int, float, str)):
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("group key contains a non-finite value")
        return f"{type(value).__name__}:{value!r}"
    if isinstance(value, (dt.date, dt.datetime)):
        return f"{type(value).__name__}:{value.isoformat()}"
    return f"{type(value).__name__}:{str(value)!r}"


def _as_values(keys: pl.Series | pl.DataFrame | Sequence[Any]) -> list[Any]:
    if isinstance(keys, pl.DataFrame):
        if keys.width != 1:
            raise ValueError("cross-validation key DataFrame must contain exactly one column")
        return keys.to_series(0).to_list()
    if isinstance(keys, pl.Series):
        return keys.to_list()
    if isinstance(keys, Sequence) and not isinstance(keys, (str, bytes)):
        return list(keys)
    raise ValueError("cross-validation strategy requires one Polars key Series or column DataFrame")


def _parse_date(value: Any) -> dt.date | dt.datetime:
    if value is None:
        raise ValueError("temporal date key contains null")
    if isinstance(value, dt.datetime):
        return value
    if isinstance(value, dt.date):
        return value
    if isinstance(value, str):
        try:
            normalized = value.removesuffix("Z") + ("+00:00" if value.endswith("Z") else "")
            return (
                dt.datetime.fromisoformat(normalized)
                if "T" in value or " " in value
                else dt.date.fromisoformat(value)
            )
        except ValueError as exc:
            raise ValueError(f"temporal date key is unparseable: {value!r}") from exc
    raise ValueError("temporal key must have Date, Datetime, or Utf8 dtype")


def _temporal_values(values: Sequence[Any]) -> list[dt.date | dt.datetime]:
    parsed = [_parse_date(value) for value in values]
    if len({type(value) for value in parsed}) != 1:
        raise ValueError("temporal keys must not mix date and datetime semantics")
    datetimes = [value for value in parsed if isinstance(value, dt.datetime)]
    if datetimes:
        timezone_modes = {value.utcoffset() is not None for value in datetimes}
        if len(timezone_modes) != 1:
            raise ValueError("temporal datetime keys must not mix timezone-aware and naive values")
    return parsed


def _numpy_entropy(seed: int) -> int:
    """Map all signed/arbitrary-size Python integers to NumPy-compatible entropy."""
    return int.from_bytes(hashlib.sha256(str(seed).encode("ascii")).digest()[:16], "big")


def generate_fold_plan(
    config: CrossValidationConfig | Mapping[str, Any],
    source_sha256: str,
    row_count_or_keys: int | pl.Series | pl.DataFrame | Sequence[Any],
) -> FoldPlan:
    """Build a deterministic plan; *row_count_or_keys* is an int for random plans."""
    if not isinstance(config, CrossValidationConfig):
        config = CrossValidationConfig.from_plain_data(config)
    digest = _sha256(source_sha256, "source_sha256")
    if config.strategy == "random":
        count = _int(row_count_or_keys, "row_count")
        if count < config.fold_count:
            raise ValueError("random cross-validation needs at least one row per fold")
        assignments = np.empty(count, dtype=np.int64)
        for fold, positions in enumerate(
            np.array_split(
                np.random.default_rng(_numpy_entropy(config.seed)).permutation(count),
                config.fold_count,
            )
        ):
            assignments[positions] = fold
    elif config.strategy == "group":
        if isinstance(row_count_or_keys, int):
            raise ValueError("group cross-validation requires key values")
        values = _as_values(row_count_or_keys)
        groups: dict[str, list[int]] = {}
        for index, value in enumerate(values):
            groups.setdefault(_canonical_group(value), []).append(index)
        if len(groups) < config.fold_count:
            raise ValueError("group cross-validation has fewer groups than folds")
        assignments = np.empty(len(values), dtype=np.int64)
        ordered = sorted(
            groups, key=lambda item: hashlib.sha256(f"{config.seed}:{item}".encode()).hexdigest()
        )
        for fold, group in enumerate(ordered):
            assignments[groups[group]] = fold % config.fold_count
        _assert_group_non_leakage(tuple(int(item) for item in assignments), values)
    else:
        if isinstance(row_count_or_keys, int):
            raise ValueError("temporal cross-validation requires key values")
        values = _as_values(row_count_or_keys)
        dates = _temporal_values(values)
        ordered_dates = sorted(set(dates))
        if len(ordered_dates) < config.fold_count + 1:
            raise ValueError("temporal cross-validation needs fold_count + 1 distinct dates")
        # np.array_split gives contiguous non-empty date blocks.  One initial block
        # means every fold has a genuinely earlier expanding training window.
        blocks = np.array_split(np.asarray(ordered_dates, dtype=object), config.fold_count + 1)
        by_date: dict[dt.date, int] = {}
        for block_index, block in enumerate(blocks):
            for date in block:
                by_date[date] = block_index - 1
        assignments = np.asarray([by_date[date] for date in dates], dtype=np.int64)
        _validate_temporal_dates(tuple(int(item) for item in assignments), dates, config.fold_count)
    assignment_tuple = tuple(int(item) for item in assignments)
    counts = _fold_count_summaries(config.strategy, assignment_tuple, config.fold_count)
    return FoldPlan(
        CV_SCHEMA_VERSION, config, len(assignment_tuple), digest, assignment_tuple, counts
    )


def _fold_count_summaries(
    strategy: str, assignments: Sequence[int], fold_count: int
) -> tuple[tuple[int, int], ...]:
    """Per-fold (train, validation) counts for vocabulary-validated assignments."""
    values = np.asarray(assignments, dtype=np.int64)
    validation = np.bincount(values[values >= 0], minlength=fold_count)
    if strategy == "temporal":
        train = int((values == -1).sum()) + np.concatenate(([0], np.cumsum(validation[:-1])))
    else:
        train = values.size - validation
    return tuple(
        (int(train_rows), int(validation_rows))
        for train_rows, validation_rows in zip(train, validation, strict=True)
    )


def _assert_group_non_leakage(assignments: Sequence[int], values: Sequence[Any]) -> None:
    seen: dict[str, int] = {}
    for assignment, value in zip(assignments, values, strict=True):
        key = _canonical_group(value)
        if key in seen and seen[key] != assignment:
            raise ValueError("group leakage: a group appears in multiple validation folds")
        seen[key] = assignment


def validate_group_non_leakage(
    plan: FoldPlan, keys: pl.Series | pl.DataFrame | Sequence[Any]
) -> None:
    if plan.config.strategy != "group":
        raise ValueError("group leakage validation requires a group fold plan")
    values = _as_values(keys)
    if len(values) != plan.row_count:
        raise ValueError("group key count does not match fold plan")
    _assert_group_non_leakage(plan.assignments, values)


def validate_temporal_expanding_window(
    plan: FoldPlan, keys: pl.Series | pl.DataFrame | Sequence[Any]
) -> None:
    """Prove a temporal plan retains date ties and never trains on the future."""
    if plan.config.strategy != "temporal":
        raise ValueError("temporal validation requires a temporal fold plan")
    values = _as_values(keys)
    if len(values) != plan.row_count:
        raise ValueError("temporal key count does not match fold plan")
    _validate_temporal_dates(plan.assignments, _temporal_values(values), plan.config.fold_count)


def _validate_temporal_assignments(assignments: Sequence[int], fold_count: int) -> None:
    # Source rows need not be ordered by date, so an assignment vector itself cannot
    # be monotonic.  Date identity/ties and expanding ordering are checked against the
    # supplied key values while generating a plan.
    present = set(np.unique(np.asarray(assignments, dtype=np.int64)).tolist())
    if -1 not in present:
        raise ValueError("temporal plan needs a non-empty initial training block")
    for fold in range(fold_count):
        if fold not in present:
            raise ValueError(f"temporal fold {fold} is empty")


def _validate_temporal_dates(
    assignments: Sequence[int], dates: Sequence[dt.date | dt.datetime], fold_count: int
) -> None:
    _validate_temporal_assignments(assignments, fold_count)
    per_date: dict[dt.date | dt.datetime, int] = {}
    for assignment, date in zip(assignments, dates, strict=True):
        if date in per_date and per_date[date] != assignment:
            raise ValueError("equal temporal dates must stay in the same block")
        per_date[date] = assignment
    for fold in range(fold_count):
        train_dates = [date for date, block in per_date.items() if block == -1 or 0 <= block < fold]
        valid_dates = [date for date, block in per_date.items() if block == fold]
        if not train_dates or not valid_dates or max(train_dates) >= min(valid_dates):
            raise ValueError("temporal fold does not have a strict expanding window")


@dataclasses.dataclass(frozen=True)
class CrossValidationFoldResult:
    schema_version: int
    fold_index: int
    train_rows: int
    validation_rows: int
    metrics: dict[str, float]

    def __post_init__(self) -> None:
        if (
            _int(self.schema_version, "fold result schema_version") != CV_SCHEMA_VERSION
            or _int(self.fold_index, "fold_index") < 0
            or _int(self.train_rows, "train_rows") < 1
            or _int(self.validation_rows, "validation_rows") < 1
        ):
            raise ValueError("invalid cross-validation fold result")
        if not self.metrics or any(not isinstance(key, str) or not key for key in self.metrics):
            raise ValueError("fold result metrics must be a non-empty named mapping")
        for name, value in self.metrics.items():
            _finite(value, f"metric {name}")

    def to_plain_data(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "fold_index": self.fold_index,
            "train_rows": self.train_rows,
            "validation_rows": self.validation_rows,
            "metrics": dict(self.metrics),
        }

    @classmethod
    def from_plain_data(cls, raw: Mapping[str, Any]) -> CrossValidationFoldResult:
        if not isinstance(raw, Mapping):
            raise ValueError("fold result must be an object")
        _exact_keys(
            raw,
            {"schema_version", "fold_index", "train_rows", "validation_rows", "metrics"},
            "fold result",
        )
        if not isinstance(raw["metrics"], Mapping):
            raise ValueError("fold result metrics must be an object")
        if any(not isinstance(key, str) or not key for key in raw["metrics"]):
            raise ValueError("fold result metrics must have non-empty string names")
        return cls(
            _int(raw["schema_version"], "schema_version"),
            _int(raw["fold_index"], "fold_index"),
            _int(raw["train_rows"], "train_rows"),
            _int(raw["validation_rows"], "validation_rows"),
            {key: _finite(value, f"metric {key}") for key, value in raw["metrics"].items()},
        )

    from_dict = from_plain_data
    to_dict = to_plain_data


@dataclasses.dataclass(frozen=True)
class FoldResultsArtifact:
    schema_version: int
    plan_sha256: str
    results: tuple[CrossValidationFoldResult, ...]

    def __post_init__(self) -> None:
        if _int(self.schema_version, "fold results schema_version") != CV_SCHEMA_VERSION:
            raise ValueError("unsupported fold results schema_version")
        _sha256(self.plan_sha256, "plan_sha256")
        if not 2 <= len(self.results) <= MAX_CV_FOLDS or [
            item.fold_index for item in self.results
        ] != list(range(len(self.results))):
            raise ValueError("fold results must be complete and in ascending fold order")
        if len({frozenset(item.metrics) for item in self.results}) != 1:
            raise ValueError("fold result metric names must agree exactly")

    def to_plain_data(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "plan_sha256": self.plan_sha256,
            "results": [item.to_plain_data() for item in self.results],
        }

    @classmethod
    def from_plain_data(cls, raw: Mapping[str, Any]) -> FoldResultsArtifact:
        if not isinstance(raw, Mapping):
            raise ValueError("fold results artifact must be an object")
        _exact_keys(raw, {"schema_version", "plan_sha256", "results"}, "fold results artifact")
        if not isinstance(raw["results"], list):
            raise ValueError("fold results must be an array")
        return cls(
            _int(raw["schema_version"], "schema_version"),
            _sha256(raw["plan_sha256"], "plan_sha256"),
            tuple(CrossValidationFoldResult.from_plain_data(item) for item in raw["results"]),
        )

    from_dict = from_plain_data
    to_dict = to_plain_data


@dataclasses.dataclass(frozen=True)
class CrossValidationReport:
    schema_version: int
    plan_sha256: str
    results_sha256: str
    folds: tuple[CrossValidationFoldResult, ...]
    metrics: dict[str, dict[str, float | int]]

    def __post_init__(self) -> None:
        if _int(self.schema_version, "report schema_version") != CV_SCHEMA_VERSION:
            raise ValueError("unsupported cross-validation report schema_version")
        _sha256(self.plan_sha256, "plan_sha256")
        _sha256(self.results_sha256, "results_sha256")
        if not 2 <= len(self.folds) <= MAX_CV_FOLDS or [
            item.fold_index for item in self.folds
        ] != list(range(len(self.folds))):
            raise ValueError("report folds must be complete and ascending")
        if not isinstance(self.metrics, dict) or not self.metrics:
            raise ValueError("report metrics must be a non-empty object")
        required = {"mean", "population_std", "min", "max", "fold_count", "total_validation_rows"}
        metric_names = set(self.folds[0].metrics)
        if (
            any(set(fold.metrics) != metric_names for fold in self.folds)
            or set(self.metrics) != metric_names
        ):
            raise ValueError("report metric names must exactly match every fold")
        for name, summary in self.metrics.items():
            if not isinstance(name, str) or not name or not isinstance(summary, Mapping):
                raise ValueError("invalid report metric summary")
            _exact_keys(summary, required, "report metric summary")
            for field in ("mean", "population_std", "min", "max"):
                _finite(summary[field], f"report metric {field}")
            if _int(summary["fold_count"], "report fold_count") != len(self.folds) or _int(
                summary["total_validation_rows"], "total_validation_rows"
            ) != sum(fold.validation_rows for fold in self.folds):
                raise ValueError("invalid report metric counts")
            values = np.asarray([fold.metrics[name] for fold in self.folds], dtype=float)
            weights = np.asarray([fold.validation_rows for fold in self.folds], dtype=float)
            mean = float(np.average(values, weights=weights))
            expected_summary = (
                mean,
                float(np.sqrt(np.average((values - mean) ** 2, weights=weights))),
                float(values.min()),
                float(values.max()),
            )
            if not all(
                math.isclose(float(summary[key]), expected, rel_tol=1e-12, abs_tol=1e-12)
                for key, expected in zip(
                    ("mean", "population_std", "min", "max"), expected_summary, strict=True
                )
            ):
                raise ValueError("report metric summary disagrees with folds")

    def to_plain_data(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "plan_sha256": self.plan_sha256,
            "results_sha256": self.results_sha256,
            "folds": [fold.to_plain_data() for fold in self.folds],
            "metrics": self.metrics,
        }

    @classmethod
    def from_plain_data(cls, raw: Mapping[str, Any]) -> CrossValidationReport:
        if not isinstance(raw, Mapping):
            raise ValueError("cross-validation report must be an object")
        _exact_keys(
            raw,
            {"schema_version", "plan_sha256", "results_sha256", "folds", "metrics"},
            "cross-validation report",
        )
        if not isinstance(raw["folds"], list) or not isinstance(raw["metrics"], Mapping):
            raise ValueError("invalid cross-validation report arrays")
        metrics: dict[str, dict[str, float | int]] = {}
        for name, value in raw["metrics"].items():
            if not isinstance(name, str) or not isinstance(value, Mapping):
                raise ValueError("invalid report metric summary")
            metrics[name] = dict(value)
        return cls(
            _int(raw["schema_version"], "schema_version"),
            _sha256(raw["plan_sha256"], "plan_sha256"),
            _sha256(raw["results_sha256"], "results_sha256"),
            tuple(CrossValidationFoldResult.from_plain_data(item) for item in raw["folds"]),
            metrics,
        )

    from_dict = from_plain_data
    to_dict = to_plain_data


def aggregate_fold_results(
    plan: FoldPlan,
    artifact: FoldResultsArtifact,
    metric_names: Sequence[str],
    *,
    results_sha256: str,
) -> CrossValidationReport:
    """Create weighted aggregates only after callers have reloaded *artifact*."""
    if (
        artifact.plan_sha256
        != hashlib.sha256(canonical_json_bytes(plan.to_plain_data())).hexdigest()
    ):
        raise ValueError("fold results artifact does not link to this fold plan")
    if len(artifact.results) != plan.config.fold_count:
        raise ValueError("fold results are incomplete for the fold plan")
    for result in artifact.results:
        if (
            result.train_rows,
            result.validation_rows,
        ) != plan.fold_counts[result.fold_index]:
            raise ValueError("fold result row counts disagree with the fold plan")
    expected = set(metric_names)
    if not expected or any(not isinstance(name, str) or not name for name in expected):
        raise ValueError("configured metrics must be non-empty names")
    if any(set(result.metrics) != expected for result in artifact.results):
        raise ValueError("fold result metric names do not exactly match configured metrics")
    aggregate: dict[str, dict[str, float | int]] = {}
    for name in sorted(expected):
        values = np.asarray([result.metrics[name] for result in artifact.results], dtype=float)
        weights = np.asarray([result.validation_rows for result in artifact.results], dtype=float)
        mean = float(np.average(values, weights=weights))
        aggregate[name] = {
            "mean": mean,
            "population_std": float(np.sqrt(np.average((values - mean) ** 2, weights=weights))),
            "min": float(values.min()),
            "max": float(values.max()),
            "fold_count": len(values),
            "total_validation_rows": int(weights.sum()),
        }
    return CrossValidationReport(
        CV_SCHEMA_VERSION,
        hashlib.sha256(canonical_json_bytes(plan.to_plain_data())).hexdigest(),
        _sha256(results_sha256, "results_sha256"),
        artifact.results,
        aggregate,
    )


def save_fold_plan(plan: FoldPlan, path: Path | str) -> None:
    save_canonical_json(plan.to_plain_data(), path)


def load_fold_plan(path: Path | str, *, source_sha256: str | None = None) -> FoldPlan:
    plan = FoldPlan.from_plain_data(load_canonical_json(path))
    if source_sha256 is not None:
        plan.assert_source_digest(source_sha256)
    return plan


def save_fold_results(artifact: FoldResultsArtifact, path: Path | str) -> None:
    save_canonical_json(artifact.to_plain_data(), path)


def load_fold_results(path: Path | str) -> FoldResultsArtifact:
    return FoldResultsArtifact.from_plain_data(load_canonical_json(path))


def save_cross_validation_report(report: CrossValidationReport, path: Path | str) -> None:
    save_canonical_json(report.to_plain_data(), path)


def load_cross_validation_report(path: Path | str) -> CrossValidationReport:
    return CrossValidationReport.from_plain_data(load_canonical_json(path))
