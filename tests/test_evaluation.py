"""Focused contracts for the canonical development/validation/final-test plan."""

from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np
import pytest

from haute.modelling._evaluation import (
    EVALUATION_SCHEMA_VERSION,
    MAX_VALIDATION_FITS,
    EvaluationConfig,
    EvaluationFitResult,
    EvaluationPlan,
    EvaluationResultsArtifact,
    _stratified_parts,
    aggregate_evaluation_results,
    canonical_json_bytes,
    file_sha256,
    generate_evaluation_plan,
    load_evaluation_plan,
    save_evaluation_plan,
)


def random_config(
    *,
    validation: dict[str, object] | None = None,
    test: dict[str, object] | None = None,
    seed: int = 17,
) -> EvaluationConfig:
    raw: dict[str, object] = {
        "schema_version": 1,
        "strategy": "random",
        "seed": seed,
        "validation": validation or {"method": "cross_validation", "fold_count": 3},
    }
    if test is not None:
        raw["test"] = test
    return EvaluationConfig.from_plain_data(raw)


@pytest.mark.parametrize(
    "raw",
    [
        {},
        {
            "schema_version": True,
            "strategy": "random",
            "seed": 1,
            "validation": {"method": "none"},
        },
        {
            "schema_version": 1,
            "strategy": "random",
            "seed": True,
            "validation": {"method": "none"},
        },
        {
            "schema_version": 1,
            "strategy": "random",
            "seed": 1,
            "validation": {"method": "single", "size": float("nan")},
        },
        {
            "schema_version": 1,
            "strategy": "random",
            "seed": 1,
            "validation": {"method": "cross_validation", "fold_count": True},
        },
        {
            "schema_version": 1,
            "strategy": "random",
            "seed": 1,
            "validation": {"method": "cross_validation", "fold_count": 11},
        },
        {
            "schema_version": 1,
            "strategy": "group",
            "seed": 1,
            "validation": {"method": "none"},
        },
        {
            "schema_version": 1,
            "strategy": "temporal",
            "date_column": "date",
            "seed": 1,
            "validation": {"method": "none"},
        },
        {
            "schema_version": 1,
            "strategy": "random",
            "seed": 1,
            "validation": {"method": "none"},
            "unknown": 1,
        },
    ],
)
def test_evaluation_config_is_versioned_exact_and_strategy_strict(
    raw: dict[str, object],
) -> None:
    with pytest.raises((TypeError, ValueError)):
        EvaluationConfig.from_plain_data(raw)


def test_evaluation_config_canonicalises_all_validation_modes_and_fit_counts() -> None:
    assert EVALUATION_SCHEMA_VERSION == 1
    assert MAX_VALIDATION_FITS == 10
    assert random_config(validation={"method": "none"}).validation_fit_count == 0
    assert random_config(validation={"method": "single", "size": 0.2}).validation_fit_count == 1
    assert random_config().validation_fit_count == 3
    assert random_config().ordinary_total_fit_count == 4

    temporal = EvaluationConfig.from_plain_data(
        {
            "schema_version": 1,
            "strategy": "temporal",
            "date_column": "date",
            "test": {"start": "2025-01-01"},
            "validation": {
                "method": "cross_validation",
                "fold_count": 5,
                "window": "expanding",
            },
        }
    )
    assert temporal.to_plain_data()["validation"]["window"] == "expanding"


def test_random_regression_plan_assigns_test_first_and_never_leaks_it() -> None:
    config = random_config(test={"size": 0.2})
    first = generate_evaluation_plan(
        config,
        source_sha256="a" * 64,
        row_count=50,
        task="regression",
    )
    assert first == generate_evaluation_plan(
        config,
        source_sha256="a" * 64,
        row_count=50,
        task="regression",
    )
    assert len(first.test_positions) == 10
    assert len(first.development_positions) == 40
    assert first.fit_count == 4
    test = set(first.test_positions)
    for fit in first.validation_fits:
        assert test.isdisjoint(fit.train_positions)
        assert test.isdisjoint(fit.validation_positions)
        assert set(fit.train_positions).isdisjoint(fit.validation_positions)
        assert set(fit.train_positions) | set(fit.validation_positions) == set(
            first.development_positions
        )
    final_mask = first.final_mask()
    assert int((final_mask == 0).sum()) == 40
    assert int((final_mask == 2).sum()) == 10


def test_random_regression_validation_membership_is_seeded_and_reproducible() -> None:
    single = random_config(
        validation={"method": "single", "size": 0.2},
        seed=17,
    )
    first = generate_evaluation_plan(
        single,
        source_sha256="a" * 64,
        row_count=100,
        task="regression",
    )
    repeated = generate_evaluation_plan(
        single,
        source_sha256="a" * 64,
        row_count=100,
        task="regression",
    )
    different_seed = generate_evaluation_plan(
        random_config(
            validation={"method": "single", "size": 0.2},
            seed=18,
        ),
        source_sha256="a" * 64,
        row_count=100,
        task="regression",
    )

    assert first.validation_fits == repeated.validation_fits
    assert (
        first.validation_fits[0].validation_positions
        != different_seed.validation_fits[0].validation_positions
    )
    assert first.validation_fits[0].validation_positions != tuple(range(80, 100))


# Golden vector: the stratified 3-fold assignment of positions 0..11 with two
# alternating classes at seed 42. It pins the ``np.random.default_rng(seed)``
# shuffle stream that every evaluation-plan fold, sample and group bucket in
# _evaluation.py rests on. NumPy freezes ``RandomState`` but may change
# ``Generator`` streams in a feature release; the reproducibility test above
# compares two draws in one process and passes under any stream. Generated on
# numpy 2.4.2 and verified identical on the 2.0.2 floor. If this moves, every
# saved evaluation plan stops matching a regenerated one: decide and record the
# NumPy bump deliberately, do not regenerate the literal.
_GOLDEN_STRATIFIED_3_FOLDS_SEED_42 = [[6, 8, 5, 3], [4, 2, 9, 7], [10, 0, 1, 11]]


def test_stratified_fold_assignment_golden_vector_pins_numpy_generator_stream() -> None:
    folds = _stratified_parts(list(range(12)), ["a", "b"] * 6, 3, 42)

    assert folds == _GOLDEN_STRATIFIED_3_FOLDS_SEED_42, (
        "NumPy Generator stream changed: stratified fold assignment no longer matches "
        "the golden vector, so regenerated evaluation plans differ from saved ones. "
        "Decide and record the NumPy bump deliberately; do not just regenerate."
    )


def test_random_classification_is_stratified_or_rejected_with_counts() -> None:
    target = ["a"] * 20 + ["b"] * 20 + ["c"] * 20
    plan = generate_evaluation_plan(
        random_config(test={"size": 0.2}),
        source_sha256="b" * 64,
        row_count=len(target),
        task="classification",
        target_values=target,
    )
    for positions in (
        plan.test_positions,
        *(fit.validation_positions for fit in plan.validation_fits),
    ):
        assert {target[position] for position in positions} == {"a", "b", "c"}

    with pytest.raises(ValueError, match=r"class counts.*required minimum"):
        generate_evaluation_plan(
            random_config(test={"size": 0.2}),
            source_sha256="b" * 64,
            row_count=7,
            task="classification",
            target_values=["rare", "common", "common", "common", "common", "common", "common"],
        )


def test_group_plan_never_splits_groups_and_balances_by_rows() -> None:
    groups = ["a"] * 13 + ["b"] * 8 + ["c"] * 7 + ["d"] * 6 + ["e"] * 5 + ["f"] * 4
    config = EvaluationConfig.from_plain_data(
        {
            "schema_version": 1,
            "strategy": "group",
            "group_column": "entity",
            "seed": 9,
            "test": {"size": 0.2},
            "validation": {"method": "cross_validation", "fold_count": 3},
        }
    )
    plan = generate_evaluation_plan(
        config,
        source_sha256="c" * 64,
        row_count=len(groups),
        task="regression",
        group_values=groups,
    )
    membership: dict[str, set[str]] = {}
    for position in plan.test_positions:
        membership.setdefault(groups[position], set()).add("test")
    for fold_index, fit in enumerate(plan.validation_fits):
        for position in fit.validation_positions:
            membership.setdefault(groups[position], set()).add(f"fold-{fold_index}")
    assert all(len(destinations) == 1 for destinations in membership.values())
    validation_rows = [fit.validation_rows for fit in plan.validation_fits]
    assert max(validation_rows) - min(validation_rows) <= 13
    assert plan.summary["development_group_count"] + plan.summary["test_group_count"] == 6


def test_group_plan_accepts_native_temporal_keys() -> None:
    groups = [
        date(2024, 1, 1),
        date(2024, 1, 1),
        date(2024, 2, 1),
        date(2024, 2, 1),
        date(2024, 3, 1),
        date(2024, 3, 1),
    ]
    config = EvaluationConfig.from_plain_data(
        {
            "schema_version": 1,
            "strategy": "group",
            "group_column": "renewal_month",
            "seed": 9,
            "validation": {"method": "cross_validation", "fold_count": 2},
        }
    )

    plan = generate_evaluation_plan(
        config,
        source_sha256="c" * 64,
        row_count=len(groups),
        task="regression",
        group_values=groups,
    )

    assert plan.summary["development_group_count"] == 3
    for group in set(groups):
        destinations = {
            index
            for index, fit in enumerate(plan.validation_fits)
            if any(groups[position] == group for position in fit.validation_positions)
        }
        assert len(destinations) == 1


def test_temporal_single_and_cv_keep_ties_and_strict_order() -> None:
    dates = [
        "2024-01-01",
        "2024-01-01",
        "2024-02-01",
        "2024-03-01",
        "2024-04-01",
        "2024-05-01",
        "2024-06-01",
    ]
    single = EvaluationConfig.from_plain_data(
        {
            "schema_version": 1,
            "strategy": "temporal",
            "date_column": "month",
            "test": {"start": "2024-05-01"},
            "validation": {"method": "single", "start": "2024-03-01"},
        }
    )
    single_plan = generate_evaluation_plan(
        single,
        source_sha256="d" * 64,
        row_count=len(dates),
        task="regression",
        date_values=dates,
    )
    assert single_plan.validation_fits[0].train_positions == (0, 1, 2)
    assert single_plan.validation_fits[0].validation_positions == (3, 4)
    assert single_plan.test_positions == (5, 6)

    cv = EvaluationConfig.from_plain_data(
        {
            "schema_version": 1,
            "strategy": "temporal",
            "date_column": "month",
            "test": {"start": "2024-06-01"},
            "validation": {
                "method": "cross_validation",
                "fold_count": 3,
                "window": "expanding",
            },
        }
    )
    cv_plan = generate_evaluation_plan(
        cv,
        source_sha256="d" * 64,
        row_count=len(dates),
        task="regression",
        date_values=dates,
    )
    for fit in cv_plan.validation_fits:
        train_dates = [dates[position] for position in fit.train_positions]
        validation_dates = [dates[position] for position in fit.validation_positions]
        assert max(train_dates) < min(validation_dates)
        assert set(fit.train_positions).isdisjoint(cv_plan.test_positions)
        assert set(fit.validation_positions).isdisjoint(cv_plan.test_positions)

    with pytest.raises(ValueError, match=r"validation\.start must precede test\.start"):
        EvaluationConfig.from_plain_data(
            {
                "schema_version": 1,
                "strategy": "temporal",
                "date_column": "month",
                "test": {"start": "2024-03-01"},
                "validation": {"method": "single", "start": "2024-04-01"},
            }
        )


@pytest.mark.parametrize(
    ("dates", "validation_start", "test_start"),
    [
        (
            [
                date(2024, 1, 1),
                date(2024, 2, 1),
                date(2024, 3, 1),
                date(2024, 4, 1),
            ],
            "2024-03-01",
            "2024-04-01",
        ),
        (
            [
                datetime(2024, 1, 1, tzinfo=UTC),
                datetime(2024, 2, 1, tzinfo=UTC),
                datetime(2024, 3, 1, tzinfo=UTC),
                datetime(2024, 4, 1, tzinfo=UTC),
            ],
            "2024-03-01T00:00:00+00:00",
            "2024-04-01T00:00:00+00:00",
        ),
        (
            [
                datetime(2024, 1, 1, 8, 30),
                datetime(2024, 2, 1, 8, 30),
                datetime(2024, 3, 1, 8, 30),
                datetime(2024, 4, 1, 8, 30),
            ],
            "2024-03-01",
            "2024-04-01",
        ),
    ],
)
def test_temporal_plan_accepts_native_polars_date_values(
    dates: list[date] | list[datetime],
    validation_start: str,
    test_start: str,
) -> None:
    config = EvaluationConfig.from_plain_data(
        {
            "schema_version": 1,
            "strategy": "temporal",
            "date_column": "month",
            "test": {"start": test_start},
            "validation": {"method": "single", "start": validation_start},
        }
    )

    plan = generate_evaluation_plan(
        config,
        source_sha256="d" * 64,
        row_count=len(dates),
        task="regression",
        date_values=dates,
    )

    assert plan.validation_fits[0].train_positions == (0, 1)
    assert plan.validation_fits[0].validation_positions == (2,)
    assert plan.test_positions == (3,)


def test_evaluation_plan_artifact_is_strict_digest_linked_and_byte_stable(
    tmp_path: Path,
) -> None:
    plan = generate_evaluation_plan(
        random_config(test={"size": 0.25}),
        source_sha256="e" * 64,
        row_count=20,
        task="regression",
    )
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    save_evaluation_plan(plan, first)
    save_evaluation_plan(plan, second)
    assert first.read_bytes() == second.read_bytes()
    assert load_evaluation_plan(first, source_sha256="e" * 64) == plan
    assert file_sha256(first) == hashlib.sha256(first.read_bytes()).hexdigest()

    unknown = plan.to_plain_data()
    unknown["unknown"] = 1
    with pytest.raises(ValueError):
        EvaluationPlan.from_plain_data(unknown)
    overlap = plan.to_plain_data()
    overlap["test_positions"][0] = overlap["development_positions"][0]
    with pytest.raises(ValueError):
        EvaluationPlan.from_plain_data(overlap)
    unordered = plan.to_plain_data()
    unordered["development_positions"] = list(reversed(unordered["development_positions"]))
    with pytest.raises(ValueError, match="canonical ascending"):
        EvaluationPlan.from_plain_data(unordered)
    missing_test = plan.to_plain_data()
    missing_test["development_positions"].extend(missing_test["test_positions"])
    missing_test["development_positions"].sort()
    missing_test["test_positions"] = []
    missing_test["summary"]["development_rows"] = 20
    missing_test["summary"]["test_rows"] = 0
    with pytest.raises(ValueError, match="test membership"):
        EvaluationPlan.from_plain_data(missing_test)
    with pytest.raises(ValueError, match="source"):
        load_evaluation_plan(first, source_sha256="f" * 64)


def test_evaluation_artifact_atomic_write_preserves_destination_on_replace_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = generate_evaluation_plan(
        random_config(),
        source_sha256="8" * 64,
        row_count=12,
        task="regression",
    )
    destination = tmp_path / "plan.json"
    destination.write_bytes(b"previous-generation")

    def fail_replace(_source, _destination) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr("haute.modelling._evaluation.os.replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        save_evaluation_plan(plan, destination)

    assert destination.read_bytes() == b"previous-generation"
    assert list(tmp_path.glob(".plan.json.*.tmp")) == []


def test_temporal_cv_plan_artifact_validates_expanding_membership(tmp_path) -> None:
    dates = [f"2024-0{month}-01" for month in range(1, 8)]
    config = EvaluationConfig.from_plain_data(
        {
            "schema_version": 1,
            "strategy": "temporal",
            "date_column": "month",
            "test": {"start": "2024-07-01"},
            "validation": {
                "method": "cross_validation",
                "fold_count": 2,
                "window": "expanding",
            },
        }
    )
    plan = generate_evaluation_plan(
        config,
        source_sha256="9" * 64,
        row_count=len(dates),
        task="regression",
        date_values=dates,
    )
    path = tmp_path / "temporal-plan.json"
    save_evaluation_plan(plan, path)

    assert load_evaluation_plan(path, source_sha256="9" * 64) == plan

    malformed = plan.to_plain_data()
    malformed["validation_fits"][1]["train_positions"] = malformed["validation_fits"][0][
        "train_positions"
    ]
    with pytest.raises(ValueError, match="not expanding"):
        EvaluationPlan.from_plain_data(malformed)


def test_evaluation_results_aggregate_weighted_metrics_from_exact_plan() -> None:
    plan = generate_evaluation_plan(
        random_config(),
        source_sha256="f" * 64,
        row_count=11,
        task="regression",
    )
    plan_sha256 = hashlib.sha256(canonical_json_bytes(plan.to_plain_data())).hexdigest()
    values = (1.0, 3.0, 8.0)
    results = EvaluationResultsArtifact(
        schema_version=1,
        plan_sha256=plan_sha256,
        fits=tuple(
            EvaluationFitResult(
                schema_version=1,
                fit_index=index,
                train_rows=fit.train_rows,
                validation_rows=fit.validation_rows,
                metrics={"rmse": values[index]},
                best_iteration=index,
            )
            for index, fit in enumerate(plan.validation_fits)
        ),
    )
    report = aggregate_evaluation_results(
        plan,
        results,
        ["rmse"],
        results_sha256="1" * 64,
    )
    weights = np.asarray([fit.validation_rows for fit in plan.validation_fits])
    expected = float(np.average(values, weights=weights))
    assert report.metrics["rmse"]["mean"] == pytest.approx(expected)
    assert report.metrics["rmse"]["fit_count"] == 3
    assert report.fit_count == 4
