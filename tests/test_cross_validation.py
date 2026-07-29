"""Focused contract tests for pure bounded cross-validation artifacts."""

from __future__ import annotations

import hashlib

import numpy as np
import polars as pl
import pytest

from haute.modelling._cross_validation import (
    CV_SCHEMA_VERSION,
    MAX_CV_FITS,
    MAX_CV_FOLDS,
    CrossValidationConfig,
    CrossValidationFoldResult,
    FoldPlan,
    FoldResultsArtifact,
    aggregate_fold_results,
    canonical_json_bytes,
    file_sha256,
    generate_fold_plan,
    load_fold_plan,
    load_fold_results,
    save_fold_plan,
    save_fold_results,
    validate_group_non_leakage,
)


def config(strategy: str = "random", **extra: object) -> CrossValidationConfig:
    raw: dict[str, object] = {"schema_version": 1, "strategy": strategy, "fold_count": 3, "seed": 7}
    if strategy == "group":
        raw["group_column"] = "group"
    if strategy == "temporal":
        raw["date_column"] = "date"
    raw.update(extra)
    return CrossValidationConfig.from_plain_data(raw)


@pytest.mark.parametrize(
    "raw",
    [
        {},
        {"schema_version": True, "strategy": "random", "fold_count": 2, "seed": 1},
        {"schema_version": 1, "strategy": "random", "fold_count": True, "seed": 1},
        {"schema_version": 1, "strategy": "random", "fold_count": 2, "seed": True},
        {"schema_version": 1, "strategy": "group", "fold_count": 2, "seed": 1},
        {"schema_version": 1, "strategy": "random", "fold_count": 2, "seed": 1, "date_column": "d"},
    ],
)
def test_config_is_exact_and_strict(raw: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        CrossValidationConfig.from_plain_data(raw)


def test_direct_config_construction_cannot_bypass_the_fit_bound_or_strategy_shape() -> None:
    with pytest.raises(ValueError, match="fold_count"):
        CrossValidationConfig(1, "random", MAX_CV_FOLDS + 1, 7)
    with pytest.raises(ValueError, match="group_column"):
        CrossValidationConfig(1, "group", 3, 7)
    with pytest.raises(ValueError, match="date_column"):
        CrossValidationConfig(1, "random", 3, 7, date_column="date")


def test_random_plan_is_deterministic_disjoint_and_bounded() -> None:
    first = generate_fold_plan(config(), "a" * 64, 11)
    assert first == generate_fold_plan(config(), "a" * 64, 11)
    assert MAX_CV_FOLDS == 10 and MAX_CV_FITS == 11 and CV_SCHEMA_VERSION == 1
    for fold in range(3):
        mask = first.partition_mask(fold)
        assert set(mask) == {0, 1}
        assert mask.dtype == np.int8
        assert (mask == 1).sum() == first.fold_counts[fold][1]
    with pytest.raises(ValueError):
        generate_fold_plan(config(), "a" * 64, 2)
    negative = CrossValidationConfig.from_plain_data(
        {**config().to_plain_data(), "seed": -(10**100)}
    )
    assert generate_fold_plan(negative, "a" * 64, 11) == generate_fold_plan(negative, "a" * 64, 11)


def test_group_non_leakage_and_insufficient_groups() -> None:
    keys = [1, 1, "1", "1", 2, 2, 3, 4]
    plan = generate_fold_plan(config("group"), "b" * 64, keys)
    validate_group_non_leakage(plan, keys)
    assert plan.assignments[0] == plan.assignments[1]
    with pytest.raises(ValueError):
        generate_fold_plan(config("group"), "b" * 64, pl.Series(["a", "a"]))


def test_temporal_expands_retains_ties_and_rejects_bad_dates() -> None:
    dates = pl.Series(
        "date", ["2024-01-03", "2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04"]
    )
    plan = generate_fold_plan(config("temporal"), "c" * 64, dates)
    assert plan.assignments[0] == plan.assignments[3]
    for fold in range(3):
        mask = plan.partition_mask(fold)
        assert 3 in mask if fold < 2 else True
        train_dates = [dates[i] for i, label in enumerate(mask) if label == 0]
        valid_dates = [dates[i] for i, label in enumerate(mask) if label == 1]
        assert max(train_dates) < min(valid_dates)
    for bad in (
        pl.Series(["2024-01-01", None, "2024-01-02", "2024-01-03"]),
        pl.Series(["nope", "2024-01-02", "2024-01-03", "2024-01-04"]),
        pl.Series(["2024-01-01", "2024-01-02", "2024-01-03"]),
    ):
        with pytest.raises(ValueError):
            generate_fold_plan(config("temporal"), "c" * 64, bad)
    timestamps = pl.Series(
        ["2024-01-01T08:00:00", "2024-01-01T12:00:00", "2024-01-02T08:00:00", "2024-01-03T08:00:00"]
    )
    timestamp_plan = generate_fold_plan(config("temporal"), "c" * 64, timestamps)
    assert timestamp_plan.assignments[0] != timestamp_plan.assignments[1]
    with pytest.raises(ValueError):
        generate_fold_plan(
            config("temporal"),
            "c" * 64,
            ["2024-01-01", "2024-01-02T00:00:00", "2024-01-03", "2024-01-04"],
        )
    with pytest.raises(ValueError, match="timezone"):
        generate_fold_plan(
            config("temporal"),
            "c" * 64,
            [
                "2024-01-01T00:00:00",
                "2024-01-02T00:00:00+00:00",
                "2024-01-03T00:00:00",
                "2024-01-04T00:00:00+00:00",
            ],
        )


def test_plan_artifact_is_strict_and_round_trips(tmp_path: pytest.TempPathFactory) -> None:
    plan = generate_fold_plan(config(), "d" * 64, 9)
    path = tmp_path / "plan.json"
    save_fold_plan(plan, path)
    assert load_fold_plan(path, source_sha256="d" * 64) == plan
    assert file_sha256(path) == hashlib.sha256(path.read_bytes()).hexdigest()
    raw = plan.to_plain_data()
    raw["unknown"] = 1
    with pytest.raises(ValueError):
        FoldPlan.from_plain_data(raw)
    raw = plan.to_plain_data()
    raw["assignments"] = raw["assignments"][:-1]
    with pytest.raises(ValueError):
        FoldPlan.from_plain_data(raw)
    with pytest.raises(ValueError):
        load_fold_plan(path, source_sha256="e" * 64)


def result(fold: int, rows: int, metric: float) -> CrossValidationFoldResult:
    return CrossValidationFoldResult(1, fold, 10, rows, {"loss": metric})


def test_results_are_strict_ascending_finite_and_aggregate_persisted_artifact(
    tmp_path: pytest.TempPathFactory,
) -> None:
    plan = generate_fold_plan(config(), "f" * 64, 10)
    plan_digest = hashlib.sha256(canonical_json_bytes(plan.to_plain_data())).hexdigest()
    metric_values = (1.0, 3.0, 5.0)
    artifact = FoldResultsArtifact(
        1,
        plan_digest,
        tuple(
            CrossValidationFoldResult(
                1,
                fold,
                plan.fold_counts[fold][0],
                plan.fold_counts[fold][1],
                {"loss": metric_values[fold]},
            )
            for fold in range(3)
        ),
    )
    path = tmp_path / "results.json"
    save_fold_results(artifact, path)
    loaded = load_fold_results(path)
    report = aggregate_fold_results(plan, loaded, ["loss"], results_sha256=file_sha256(path))
    weights = np.asarray([count[1] for count in plan.fold_counts])
    mean = float(np.average(metric_values, weights=weights))
    assert report.plan_sha256 == plan_digest and report.results_sha256 == file_sha256(path)
    assert report.metrics["loss"] == {
        "mean": pytest.approx(mean),
        "population_std": pytest.approx(
            np.sqrt(np.average((np.asarray(metric_values) - mean) ** 2, weights=weights))
        ),
        "min": 1.0,
        "max": 5.0,
        "fold_count": 3,
        "total_validation_rows": 10,
    }
    mismatched = list(artifact.results)
    first = mismatched[0]
    mismatched[0] = CrossValidationFoldResult(
        1,
        first.fold_index,
        first.train_rows,
        first.validation_rows + 1,
        first.metrics,
    )
    with pytest.raises(ValueError, match="row counts"):
        aggregate_fold_results(
            plan,
            FoldResultsArtifact(1, plan_digest, tuple(mismatched)),
            ["loss"],
            results_sha256="f" * 64,
        )
    with pytest.raises(ValueError):
        FoldResultsArtifact(1, plan_digest, (result(1, 1, 1),))
    with pytest.raises(ValueError):
        CrossValidationFoldResult(1, 0, 1, 1, {"loss": float("nan")})
    with pytest.raises(ValueError):
        FoldResultsArtifact(
            1,
            plan_digest,
            (
                CrossValidationFoldResult(1, 0, 1, 1, {"other": 1}),
                result(1, 1, 2),
                result(2, 1, 3),
            ),
        )
    tampered = report.to_plain_data()
    tampered["metrics"]["loss"]["mean"] = 0.0
    from haute.modelling._cross_validation import CrossValidationReport

    with pytest.raises(ValueError):
        CrossValidationReport.from_plain_data(tampered)
