"""A seed selects the same training rows whatever order the rows arrive in.

Every selection training takes by position (the random and temporal-holdout masks,
random evaluation plans, the SHAP, partial-dependence and actual-against-predicted
samples) is taken over the canonical row order of ``_row_order``. Each test runs a
selection over a frame and over a permutation of it and compares the selected rows by
value, never by position: the frames hold duplicate rows and ties on every column a
selection reads, which is where an order-dependent selection shows.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
import pytest
from polars.testing import assert_frame_equal

from haute.modelling._evaluation import (
    EvaluationConfig,
    _balanced_groups,
    _groups_near_target,
    generate_evaluation_plan,
)
from haute.modelling._metrics import (
    compute_actual_vs_predicted,
    compute_ave_per_feature,
    compute_double_lift,
    compute_pdp,
)
from haute.modelling._row_order import (
    canonical_row_order,
    frame_row_order,
    row_order_columns,
    scan_row_order,
)
from haute.modelling._shap import shap_sample
from haute.modelling._split import (
    PARTITION_HOLDOUT,
    PARTITION_TRAIN,
    PARTITION_VALIDATION,
    SplitConfig,
    needs_row_order,
    split_mask,
)
from haute.modelling._training_job import TrainingJob, _PreparedData


def _rows(rows: int = 300, seed: int = 3) -> pl.DataFrame:
    """Training-shaped rows with ties on every column and 30 exact duplicates."""
    rng = np.random.default_rng(seed)
    claims = rng.integers(0, 3, rows).astype(float)
    claims[rng.integers(0, rows, 12)] = np.nan
    frame = pl.DataFrame(
        {
            "policy": rng.integers(0, 120, rows),
            "region": rng.choice(["north", "south", "east"], rows),
            # Encountered "c" first: a categorical sorted by encounter would differ.
            "band": pl.Series(["c", *rng.choice(["a", "b", "c"], rows - 1)]).cast(pl.Categorical),
            "level": pl.Series(rng.choice(["low", "high"], rows)).cast(pl.Enum(["low", "high"])),
            "start": [date(2024, 1, 1) + timedelta(days=int(d)) for d in rng.integers(0, 12, rows)],
            "claims": claims,
            "exposure": rng.choice([0.5, 1.0], rows),
            "converted": rng.integers(0, 2, rows),
        }
    )
    return pl.concat([frame, frame.head(30)])


def _permuted(frame: pl.DataFrame, seed: int = 11) -> pl.DataFrame:
    return frame[np.random.default_rng(seed).permutation(frame.height)]


def _by_value(frame: pl.DataFrame) -> pl.DataFrame:
    return frame.sort(frame.columns, maintain_order=True, nulls_last=True)


def _selected(frame: pl.DataFrame, positions: Sequence[int]) -> pl.DataFrame:
    """The rows at *positions*, compared by value."""
    return _by_value(frame[list(positions)])


def _labelled(frame: pl.DataFrame, mask: pl.Series) -> pl.DataFrame:
    """Each row with the partition it was given, compared by value."""
    return _by_value(frame.with_columns(mask.alias("_partition")))


# ---------------------------------------------------------------------------
# The canonical order
# ---------------------------------------------------------------------------


def test_the_canonical_order_is_one_stable_sort_over_every_column() -> None:
    frame = pl.DataFrame(
        {
            "region": ["b", "a", "b", None, "a", "b", "a"],
            "policy": [3, 1, 3, 2, 1, 3, 1],
            "band": pl.Series(["z", "y", "z", "x", "y", None, "y"]).cast(pl.Categorical),
            "level": pl.Series(["low", "high", "low", "low", "high", "low", "high"]).cast(
                pl.Enum(["low", "high"])
            ),
            "nothing": pl.Series([None] * 7, dtype=pl.Null),
            "claims": [1.0, float("nan"), 1.0, 0.0, float("nan"), -0.0, 2.0],
        }
    )
    columns = row_order_columns(frame.columns, ["policy"])
    assert columns == ["policy", "region", "band", "level", "nothing", "claims"]
    expected = frame.select(
        pl.arg_sort_by(columns, maintain_order=True, nulls_last=True)
    ).to_series()

    assert frame_row_order(frame, ["policy"]).tolist() == expected.to_list()


def test_the_order_stops_reading_columns_once_every_row_is_distinct() -> None:
    frame = pl.DataFrame(
        {"policy": [4, 2, 9, 1], "region": ["a", "b", "a", "b"], "x": [1, 2, 3, 4]}
    )
    read: list[str] = []

    def read_column(name: str) -> pl.Series:
        read.append(name)
        return frame.get_column(name)

    order = canonical_row_order(["policy", "region", "x"], read_column, frame.height)

    assert order.tolist() == [3, 1, 0, 2]
    assert read == ["policy"]


def test_an_empty_or_one_row_frame_has_its_own_order() -> None:
    assert frame_row_order(pl.DataFrame({"x": []}, schema={"x": pl.Int64}), []).tolist() == []
    assert frame_row_order(pl.DataFrame({"x": [7]}), []).tolist() == [0]


def test_the_canonical_rows_are_the_same_whatever_their_arrival_order() -> None:
    frame = _rows()
    permuted = _permuted(frame)

    assert_frame_equal(
        frame[frame_row_order(frame, ["policy"])],
        permuted[frame_row_order(permuted, ["policy"])],
    )


def test_a_scan_is_ordered_one_column_per_collect(tmp_path: Path) -> None:
    frame = _rows()
    path = tmp_path / "rows.parquet"
    frame.write_parquet(path)
    collected: list[list[str]] = []

    def collect(lf: pl.LazyFrame) -> pl.DataFrame:
        result = lf.collect(engine="streaming")
        collected.append(result.columns)
        return result

    order = scan_row_order(pl.scan_parquet(path), frame.height, ["policy"], collect=collect)

    assert order.tolist() == frame_row_order(frame, ["policy"]).tolist()
    assert collected and all(len(columns) == 1 for columns in collected)


# ---------------------------------------------------------------------------
# Split masks
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "config",
    [
        SplitConfig(validation_size=0.2, holdout_size=0.1, seed=42),
        SplitConfig(
            strategy="temporal",
            date_column="start",
            cutoff_date="2024-01-05",
            validation_size=0.2,
            holdout_size=0.2,
        ),
        SplitConfig(strategy="group", group_column="policy", validation_size=0.2, holdout_size=0.1),
    ],
    ids=["random", "temporal_holdout", "group"],
)
def test_a_split_gives_the_same_rows_the_same_partition_in_any_order(config: SplitConfig) -> None:
    def labelled(frame: pl.DataFrame) -> pl.DataFrame:
        order = frame_row_order(frame, ["policy"]) if needs_row_order(config) else None
        mask_df = frame.select(config.date_column or config.group_column or "policy")
        return _labelled(frame, split_mask(frame.height, config, df=mask_df, row_order=order))

    frame = _rows()
    first = labelled(frame)

    assert_frame_equal(first, labelled(_permuted(frame)))
    assert set(first["_partition"].unique().to_list()) == {
        PARTITION_TRAIN,
        PARTITION_VALIDATION,
        PARTITION_HOLDOUT,
    }


def test_a_temporal_holdout_takes_a_tied_boundary_date_by_canonical_order() -> None:
    frame = _rows()
    config = SplitConfig(
        strategy="temporal",
        date_column="start",
        cutoff_date="2024-01-02",
        validation_size=0.3,
        holdout_size=0.2,
    )
    holdout_rows = []
    for rows in (frame, _permuted(frame, 5), _permuted(frame, 6)):
        mask = split_mask(
            rows.height,
            config,
            df=rows.select("start"),
            row_order=frame_row_order(rows, ["policy"]),
        )
        holdout_rows.append(_by_value(rows.filter(mask == PARTITION_HOLDOUT)))
    boundary = holdout_rows[0]["start"].min()

    # The holdout ends inside a date: some rows of the boundary date are held out, some not.
    assert 0 < holdout_rows[0].filter(pl.col("start") == boundary).height
    assert (
        frame.filter(pl.col("start") == boundary).height
        > holdout_rows[0].filter(pl.col("start") == boundary).height
    )
    assert_frame_equal(holdout_rows[0], holdout_rows[1])
    assert_frame_equal(holdout_rows[0], holdout_rows[2])


@pytest.mark.parametrize(
    "config",
    [
        SplitConfig(validation_size=0.2, seed=42),
        SplitConfig(
            strategy="temporal",
            date_column="start",
            cutoff_date="2024-01-05",
            validation_size=0.2,
            holdout_size=0.2,
        ),
    ],
    ids=["random", "temporal_holdout"],
)
def test_a_split_that_selects_by_position_refuses_to_run_without_the_order(
    config: SplitConfig,
) -> None:
    frame = _rows()
    with pytest.raises(ValueError, match="canonical row order"):
        split_mask(frame.height, config, df=frame.select("start"))


def test_a_split_that_needs_no_order_refuses_one() -> None:
    frame = _rows()
    config = SplitConfig(strategy="group", group_column="policy")
    assert not needs_row_order(config)
    assert not needs_row_order(
        SplitConfig(strategy="temporal", date_column="start", cutoff_date="2024-01-05")
    )
    with pytest.raises(ValueError, match="needs no row order"):
        split_mask(
            frame.height,
            config,
            df=frame.select("policy"),
            row_order=frame_row_order(frame, []),
        )


# ---------------------------------------------------------------------------
# Evaluation plans
# ---------------------------------------------------------------------------


def _config(strategy: str, **fields: Any) -> EvaluationConfig:
    seed = {} if strategy == "temporal" else {"seed": 7}
    return EvaluationConfig.from_plain_data(
        {"schema_version": 1, "strategy": strategy, **seed, **fields}
    )


_PLANS = {
    "random_regression": (
        _config("random", validation={"method": "single", "size": 0.2}, test={"size": 0.2}),
        "regression",
    ),
    "random_regression_cv": (
        _config("random", validation={"method": "cross_validation", "fold_count": 3}),
        "regression",
    ),
    "random_classification": (
        _config("random", validation={"method": "single", "size": 0.2}, test={"size": 0.2}),
        "classification",
    ),
    "random_classification_cv": (
        _config("random", validation={"method": "cross_validation", "fold_count": 3}),
        "classification",
    ),
    "group_single": (
        _config(
            "group",
            group_column="policy",
            validation={"method": "single", "size": 0.2},
            test={"size": 0.2},
        ),
        "regression",
    ),
    "group_cv": (
        _config(
            "group",
            group_column="policy",
            validation={"method": "cross_validation", "fold_count": 3},
        ),
        "regression",
    ),
    "temporal_cv": (
        _config(
            "temporal",
            date_column="start",
            validation={"method": "cross_validation", "fold_count": 2, "window": "expanding"},
            test={"start": "2024-01-10"},
        ),
        "regression",
    ),
}


def _plan_rows(frame: pl.DataFrame, config: EvaluationConfig, task: str) -> list[pl.DataFrame]:
    plan = generate_evaluation_plan(
        config,
        source_sha256="a" * 64,
        row_count=frame.height,
        task=task,
        target_values=frame["converted"].to_list() if task == "classification" else None,
        group_values=frame["policy"].to_list() if config.strategy == "group" else None,
        date_values=(
            [value.isoformat() for value in frame["start"].to_list()]
            if config.strategy == "temporal"
            else None
        ),
        row_order=frame_row_order(frame, ["policy"]) if config.strategy == "random" else None,
    )
    memberships = [plan.development_positions, plan.test_positions]
    for fit in plan.validation_fits:
        memberships.extend([fit.train_positions, fit.validation_positions])
    return [_selected(frame, positions) for positions in memberships]


@pytest.mark.parametrize("name", list(_PLANS))
def test_a_plan_selects_the_same_rows_in_any_order(name: str) -> None:
    config, task = _PLANS[name]
    frame = _rows()

    first = _plan_rows(frame, config, task)
    for seed in (5, 6):
        again = _plan_rows(_permuted(frame, seed), config, task)
        for expected, actual in zip(first, again, strict=True):
            assert_frame_equal(expected, actual)


def test_a_random_plan_refuses_to_run_without_the_order() -> None:
    config, _task = _PLANS["random_regression"]
    with pytest.raises(ValueError, match="canonical row order"):
        generate_evaluation_plan(config, source_sha256="a" * 64, row_count=10, task="regression")


@pytest.mark.parametrize(
    "row_order",
    [list(range(9)), [0, 1, 2, 3, 4, 5, 6, 7, 8, 8], [*range(1, 10), 10]],
    ids=["short", "repeated", "out_of_range"],
)
def test_a_random_plan_refuses_an_order_that_is_not_a_permutation(row_order: list[int]) -> None:
    config, _task = _PLANS["random_regression"]
    with pytest.raises(ValueError, match="permutation"):
        generate_evaluation_plan(
            config,
            source_sha256="a" * 64,
            row_count=10,
            task="regression",
            row_order=np.asarray(row_order),
        )


def test_a_plan_that_needs_no_order_refuses_one() -> None:
    config, _task = _PLANS["group_cv"]
    with pytest.raises(ValueError, match="random plan"):
        generate_evaluation_plan(
            config,
            source_sha256="a" * 64,
            row_count=4,
            task="regression",
            group_values=["a", "a", "b", "b"],
            row_order=np.arange(4),
        )


def _prepared(path: Path, rows: int) -> _PreparedData:
    return _PreparedData(
        data_path=str(path),
        owns_tmp=False,
        features=["region", "band", "age"],
        cat_features=["region", "band"],
        total_rows=rows,
    )


@pytest.mark.parametrize(
    "split",
    [
        {"strategy": "random", "validation_size": 0.25, "holdout_size": 0.15, "seed": 17},
        {
            "strategy": "temporal",
            "date_column": "start",
            "cutoff_date": "2024-01-04",
            "validation_size": 0.25,
            "holdout_size": 0.25,
        },
    ],
    ids=["random", "temporal_holdout"],
)
def test_a_training_run_splits_the_same_rows_from_a_permuted_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, split: dict[str, Any]
) -> None:
    monkeypatch.setenv("HAUTE_MEM_LOG", str(tmp_path / "training_mem.log"))

    def split_rows(frame: pl.DataFrame, name: str) -> pl.DataFrame:
        path = tmp_path / f"{name}.parquet"
        frame.write_parquet(path)
        job = TrainingJob(
            name=name, data=str(path), target="exposure", split=split, id_columns=["policy"]
        )
        result = job._split_data(_prepared(path, frame.height), lambda _message, _fraction: None)
        try:
            return _by_value(pl.read_parquet(result.split_path))
        finally:
            Path(result.split_path).unlink()

    frame = _rows().with_columns(pl.col("policy").alias("age"))

    assert_frame_equal(split_rows(frame, "first"), split_rows(_permuted(frame), "permuted"))


@pytest.mark.parametrize("task", ["regression", "classification"])
def test_a_training_run_plans_the_same_rows_from_a_permuted_source(
    tmp_path: Path, task: str
) -> None:
    config = _config(
        "random", validation={"method": "cross_validation", "fold_count": 3}, test={"size": 0.2}
    )

    def plan_rows(frame: pl.DataFrame, name: str) -> list[pl.DataFrame]:
        path = tmp_path / f"{name}.parquet"
        frame.write_parquet(path)
        job = TrainingJob(
            name=name,
            data=str(path),
            target="converted",
            task=task,
            evaluation=config,
            id_columns=["policy"],
        )
        plan = job._build_evaluation_plan(_prepared(path, frame.height))
        memberships = [plan.development_positions, plan.test_positions]
        for fit in plan.validation_fits:
            memberships.extend([fit.train_positions, fit.validation_positions])
        return [_selected(frame, positions) for positions in memberships]

    frame = _rows().with_columns(pl.col("policy").alias("age"))
    for expected, actual in zip(
        plan_rows(frame, "first"), plan_rows(_permuted(frame), "permuted"), strict=True
    ):
        assert_frame_equal(expected, actual)


def test_groups_get_the_same_partition_whatever_order_they_appear_in() -> None:
    # Equal-sized groups: the seeded shuffle alone decides their place.
    groups = {f"g{index:02d}": [index * 3, index * 3 + 1, index * 3 + 2] for index in range(12)}
    reversed_groups = dict(reversed(list(groups.items())))

    def assignment(parts: list[list[int]]) -> list[frozenset[int]]:
        return sorted((frozenset(part) for part in parts), key=min)

    assert assignment(_balanced_groups(groups, 3, 7)) == assignment(
        _balanced_groups(reversed_groups, 3, 7)
    )
    assert set(_groups_near_target(groups, 9, 7)) == set(_groups_near_target(reversed_groups, 9, 7))


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------


def test_the_shap_sample_is_the_same_rows_in_the_same_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("haute.modelling._shap.SHAP_SAMPLE_ROWS", 100)
    frame = _rows()
    permuted = _permuted(frame)

    sample = shap_sample(frame, frame_row_order(frame, ["policy"]))

    assert sample.height == 100
    assert_frame_equal(sample, shap_sample(permuted, frame_row_order(permuted, ["policy"])))
    # Without an order the sample orders the frame itself, the same way for any arrival order.
    assert_frame_equal(shap_sample(frame), shap_sample(permuted))


class _SumAlgo:
    """Predicts each row's sum of its numeric features, so the sample shows in the mean."""

    def predict(self, model: Any, df: pl.DataFrame, features: list[str], **_: Any) -> np.ndarray:
        numeric = [f for f in features if df.schema[f].is_numeric()]
        return df.select(pl.sum_horizontal(numeric).fill_nan(0.0)).to_series().to_numpy()


def test_the_partial_dependence_sample_is_the_same_rows_in_any_order() -> None:
    frame = _rows().with_columns(pl.col("region").alias("area"))
    permuted = _permuted(frame)
    features = ["policy", "claims", "exposure", "area"]

    def pdp(rows: pl.DataFrame, **order: Any) -> list[dict[str, Any]]:
        return compute_pdp(
            None, _SumAlgo(), rows, features, ["area"], n_grid=5, max_sample=50, **order
        )

    assert pdp(frame) == pdp(permuted)
    assert pdp(frame, row_order=frame_row_order(frame, ["policy"])) == pdp(
        permuted, row_order=frame_row_order(permuted, ["policy"])
    )


def test_a_categorical_grid_keeps_its_most_frequent_levels_ties_by_level() -> None:
    # 40 levels of 5 rows each: which 30 make the grid is decided by the level alone.
    levels = [f"level_{index:02d}" for index in range(40)]
    frame = pl.DataFrame({"area": [level for level in levels for _ in range(5)], "x": 1.0})
    permuted = _permuted(frame)

    def grid(rows: pl.DataFrame) -> list[Any]:
        result = compute_pdp(None, _SumAlgo(), rows, ["area", "x"], ["area"], n_grid=5)
        return [point["value"] for point in result[0]["grid"]]

    assert grid(frame) == levels[:30]
    assert grid(permuted) == levels[:30]


def _scored(rows: int = 400, seed: int = 4) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Actual, predicted and weight with many tied predictions."""
    rng = np.random.default_rng(seed)
    predicted = rng.choice([0.1, 0.2, 0.3, 0.4], rows)
    actual = rng.choice([0.0, 1.0, 2.0], rows)
    weight = rng.choice([0.5, 1.0], rows)
    return actual, predicted, weight


def test_double_lift_puts_tied_predictions_in_the_same_deciles_in_any_order() -> None:
    actual, predicted, weight = _scored()
    permutation = np.random.default_rng(9).permutation(len(actual))

    assert compute_double_lift(actual, predicted, weight) == compute_double_lift(
        actual[permutation], predicted[permutation], weight[permutation]
    )


@pytest.mark.parametrize("rows", [40, 400], ids=["every_point", "sampled"])
def test_the_actual_against_predicted_points_are_the_same_in_any_order(rows: int) -> None:
    actual, predicted, weight = _scored(rows)
    permutation = np.random.default_rng(9).permutation(rows)

    assert compute_actual_vs_predicted(
        actual, predicted, weight, max_points=50
    ) == compute_actual_vs_predicted(
        actual[permutation], predicted[permutation], weight[permutation], max_points=50
    )


def test_ave_keeps_its_largest_levels_ties_by_level_in_any_order() -> None:
    # 20 levels of equal exposure: the 15 kept are the first 15 by level.
    levels = [f"level_{index:02d}" for index in range(20)]
    frame = pl.DataFrame({"area": [level for level in levels for _ in range(4)]})
    actual = np.arange(frame.height, dtype=float) % 3
    predicted = np.full(frame.height, 1.0)
    permutation = np.random.default_rng(9).permutation(frame.height)

    def bins(rows: pl.DataFrame, y: np.ndarray, p: np.ndarray) -> list[dict[str, Any]]:
        return compute_ave_per_feature(rows, ["area"], ["area"], y, p)[0]["bins"]

    first = bins(frame, actual, predicted)
    assert [entry["label"] for entry in first] == [*levels[:15], "Other"]
    assert first == bins(frame[permutation], actual[permutation], predicted[permutation])
