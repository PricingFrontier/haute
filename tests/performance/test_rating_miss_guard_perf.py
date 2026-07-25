from __future__ import annotations

import itertools
import statistics
import time
from collections.abc import Callable
from typing import Any

import polars as pl
import pytest
from polars.testing import assert_frame_equal

import haute._rating as rating
from haute._rating import RatingTableMissError, apply_rating_step_from_config

pytestmark = pytest.mark.perf

_ROWS = 100_000
_REPEATS = 5
_MIN_RELATIVE_OVERHEAD_PERCENT = 20.0
_MIN_ABSOLUTE_OVERHEAD_MS = 10.0
_MIN_TRIGGER_CELLS = 2


def _workload(
    *,
    factor_count: int,
    miss_heavy: bool,
) -> tuple[pl.DataFrame, dict[str, Any], int]:
    factors = [f"factor_{index}" for index in range(factor_count)]
    levels = [f"level_{index}" for index in range(4)]
    columns: dict[str, list[int] | list[str]] = {"row_id": list(range(_ROWS))}
    for factor_index, factor in enumerate(factors):
        columns[factor] = [
            (
                "missing"
                if miss_heavy and factor_index == 0 and row_index % 2
                else levels[(row_index + factor_index) % len(levels)]
            )
            for row_index in range(_ROWS)
        ]

    entries = []
    for entry_index, combination in enumerate(itertools.product(levels, repeat=factor_count)):
        entries.append(
            {
                **dict(zip(factors, combination)),
                "value": 0.75 + entry_index / 100.0,
            }
        )

    return (
        pl.DataFrame(columns),
        {
            "tables": [
                {
                    "name": f"{factor_count}_factor_lookup",
                    "factors": factors,
                    "outputColumn": "rate",
                    "onMissing": "neutral",
                    "entries": entries,
                }
            ]
        },
        _ROWS // 2 if miss_heavy else 0,
    )


def _no_miss_guard(
    _factors: list[str],
    *,
    lookup_value_column: str,
    **_kwargs: Any,
) -> pl.Expr:
    """Control expression that isolates the current guard's collection cost."""
    return pl.col(lookup_value_column)


def _plan(
    frame: pl.DataFrame,
    config: dict[str, Any],
    *,
    entry_point: str,
) -> pl.LazyFrame:
    source: pl.DataFrame | pl.LazyFrame
    source = frame if entry_point == "eager" else frame.lazy()
    plan = apply_rating_step_from_config(source, config)
    assert isinstance(plan, pl.LazyFrame)
    return plan


def _collector(entry_point: str) -> Callable[[pl.LazyFrame], pl.DataFrame]:
    if entry_point == "lazy":
        return lambda plan: plan.collect(engine="streaming")
    return lambda plan: plan.collect()


def _timings(
    guarded: pl.LazyFrame,
    control: pl.LazyFrame,
    collect: Callable[[pl.LazyFrame], pl.DataFrame],
) -> tuple[list[float], list[float]]:
    # Warm both lazy plans before comparing them. Alternate execution order so
    # one path does not consistently benefit from cache/thermal drift.
    collect(guarded)
    collect(control)
    guarded_ms: list[float] = []
    control_ms: list[float] = []
    for repeat in range(_REPEATS):
        ordered = (guarded, control) if repeat % 2 == 0 else (control, guarded)
        elapsed: dict[int, float] = {}
        for plan in ordered:
            start = time.perf_counter()
            collect(plan)
            elapsed[id(plan)] = (time.perf_counter() - start) * 1_000.0
        guarded_ms.append(elapsed[id(guarded)])
        control_ms.append(elapsed[id(control)])
    return guarded_ms, control_ms


def test_rating_miss_guard_records_representative_decision_evidence(
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    cells: list[dict[str, object]] = []

    for factor_count, miss_heavy, entry_point in itertools.product(
        (1, 3),
        (False, True),
        ("eager", "lazy"),
    ):
        frame, config, expected_misses = _workload(
            factor_count=factor_count,
            miss_heavy=miss_heavy,
        )
        guarded = _plan(frame, config, entry_point=entry_point)
        with monkeypatch.context() as patch:
            patch.setattr(rating, "_rating_miss_guard_expr", _no_miss_guard)
            control = _plan(frame, config, entry_point=entry_point)

        collect = _collector(entry_point)
        guarded_result = collect(guarded)
        control_result = collect(control)
        assert_frame_equal(guarded_result, control_result)
        assert guarded_result.height == _ROWS
        assert guarded_result["row_id"].to_list() == list(range(_ROWS))
        assert guarded_result["rate"].null_count() == expected_misses
        for factor_index in range(factor_count):
            assert guarded_result.schema[f"factor_{factor_index}"] == pl.String

        guarded_ms, control_ms = _timings(guarded, control, collect)
        guarded_median_ms = statistics.median(guarded_ms)
        control_median_ms = statistics.median(control_ms)
        overhead_ms = guarded_median_ms - control_median_ms
        overhead_percent = overhead_ms / control_median_ms * 100.0 if control_median_ms else 0.0
        cells.append(
            {
                "factors": factor_count,
                "miss_rate": 0.5 if miss_heavy else 0.0,
                "entry_point": entry_point,
                "materialisation": "streaming" if entry_point == "lazy" else "default",
                "rows": _ROWS,
                "repeats": _REPEATS,
                "guarded_ms": [round(value, 3) for value in guarded_ms],
                "control_ms": [round(value, 3) for value in control_ms],
                "guarded_median_ms": round(guarded_median_ms, 3),
                "control_median_ms": round(control_median_ms, 3),
                "overhead_ms": round(overhead_ms, 3),
                "overhead_percent": round(overhead_percent, 3),
            }
        )

    # Keep the fail-loud semantic boundary in the same evidence test. The
    # control above is deliberately timing-only and is not a proposed rewrite.
    with pytest.raises(
        RatingTableMissError,
        match=r"1 of 2 row\(s\).*Missing key",
    ):
        apply_rating_step_from_config(
            pl.DataFrame({"factor": ["known", "missing"]}),
            {
                "tables": [
                    {
                        "name": "semantic_oracle",
                        "factors": ["factor"],
                        "outputColumn": "rate",
                        "entries": [{"factor": "known", "value": 1.0}],
                    }
                ]
            },
        ).collect()

    trigger_cells = sum(
        float(cell["overhead_percent"]) >= _MIN_RELATIVE_OVERHEAD_PERCENT
        and float(cell["overhead_ms"]) >= _MIN_ABSOLUTE_OVERHEAD_MS
        for cell in cells
    )
    decision = "implement" if trigger_cells >= _MIN_TRIGGER_CELLS else "no_change"
    request.node.user_properties.append(
        (
            "haute_perf_evidence",
            {
                "scenario": "rating_miss_guard",
                "control": "identical lookup plan with only the miss guard removed",
                "cells": cells,
                "gate": {
                    "minimum_relative_overhead_percent": (_MIN_RELATIVE_OVERHEAD_PERCENT),
                    "minimum_absolute_overhead_ms": _MIN_ABSOLUTE_OVERHEAD_MS,
                    "minimum_trigger_cells": _MIN_TRIGGER_CELLS,
                    "trigger_cells": trigger_cells,
                },
                "semantic_oracle": {
                    "row_order": "preserved",
                    "schema": "preserved",
                    "neutral_misses": "null",
                    "default_policy": "RatingTableMissError",
                },
                "decision": decision,
            },
        )
    )
